"""工单 41：真实模型同任务四条件画像配对评测。

同一模型、同一任务提示下分别在有画像（正确）、无画像、错误画像、过时画像
四种条件下生成回答，按具体内容（举例先后、时间约束、任务主题、过期事实）
检查改善，记录延迟、token 与成本，并产出盲评材料。模型调用由注入的
``PairingSender`` 执行：脚本使用真实 Qwen 适配器，测试使用确定性替身。
"""

from __future__ import annotations

import json
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

from bridges.evaluation.profile_quality import Checkpoint

#: 个性化套话：出现即不计具体改善。
PERSONALIZATION_CLICHES = (
    "根据你的画像",
    "作为你的专属",
    "为你量身定制",
    "贴合你的个人特点",
    "基于对你的了解",
)

_EXAMPLE_MARKERS = ("例如", "比如", "例子", "举例", "打个比方", "想象")
_FORMULA_MARKERS = ("公式", "$$", "\\frac", "数学表达", "数学形式")


class PairingCondition(StrEnum):
    """配对条件：无画像、正确画像、错误画像、过时画像。"""

    NONE = "no_profile"
    CORRECT = "correct_profile"
    WRONG = "wrong_profile"
    OUTDATED = "outdated_profile"


@dataclass(frozen=True)
class PairingTask:
    """一个配对任务及其四条件的画像事实与内容要求。"""

    task_id: str
    prompt: str
    topic_terms: tuple[str, ...]
    correct_facts: tuple[str, ...]
    wrong_facts: tuple[str, ...]
    outdated_facts: tuple[str, ...]
    correct_example_first: bool = False
    correct_required_any: tuple[str, ...] = ()
    outdated_forbidden: tuple[str, ...] = ()

    def facts_for(self, condition: PairingCondition) -> tuple[str, ...]:
        return {
            PairingCondition.NONE: (),
            PairingCondition.CORRECT: self.correct_facts,
            PairingCondition.WRONG: self.wrong_facts,
            PairingCondition.OUTDATED: self.outdated_facts,
        }[condition]


@dataclass(frozen=True)
class PairingResponse:
    """一次配对生成的结果与测量值。"""

    status: str
    answer: str
    latency_ms: int
    model_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_estimate: dict[str, Any] | None = None
    profile_item_count: int | None = None
    error_code: str | None = None


class PairingSender(Protocol):
    """执行单次配对生成的调用方（真实模型或替身）。"""

    def __call__(
        self, task: PairingTask, condition: PairingCondition
    ) -> PairingResponse: ...


@dataclass(frozen=True)
class PairingRun:
    """一个任务在一个条件下的结果、检查点与测量。"""

    task_id: str
    condition: PairingCondition
    response: PairingResponse
    checkpoints: tuple[Checkpoint, ...]
    measurements: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.response.status == "done" and all(
            checkpoint.passed for checkpoint in self.checkpoints
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "condition": self.condition.value,
            "status": self.response.status,
            "error_code": self.response.error_code,
            "passed": self.passed,
            "answer": self.response.answer,
            "answer_length": len(self.response.answer),
            "latency_ms": self.response.latency_ms,
            "model_id": self.response.model_id,
            "input_tokens": self.response.input_tokens,
            "output_tokens": self.response.output_tokens,
            "cost_estimate": self.response.cost_estimate,
            "profile_item_count": self.response.profile_item_count,
            "checkpoints": [
                {
                    "id": checkpoint.checkpoint_id,
                    "title": checkpoint.title,
                    "passed": checkpoint.passed,
                    "hard_gate": checkpoint.hard_gate,
                    "detail": checkpoint.detail,
                }
                for checkpoint in self.checkpoints
            ],
            "measurements": self.measurements,
        }


@dataclass
class PairingReport:
    """四条件配对汇总：检查点通过率、成本与盲评素材。"""

    tasks: tuple[PairingTask, ...]
    runs: list[PairingRun]

    @property
    def checkpoints(self) -> list[Checkpoint]:
        return [c for run in self.runs for c in run.checkpoints]

    @property
    def failed_runs(self) -> list[PairingRun]:
        return [run for run in self.runs if not run.passed]

    @property
    def passed(self) -> bool:
        return not self.failed_runs

    def condition_summary(self, condition: PairingCondition) -> dict[str, Any]:
        runs = [run for run in self.runs if run.condition == condition]
        return {
            "runs": len(runs),
            "passed": sum(1 for run in runs if run.passed),
            "latency_ms_avg": (
                round(sum(r.response.latency_ms for r in runs) / len(runs))
                if runs
                else None
            ),
            "input_tokens": sum(
                r.response.input_tokens or 0 for r in runs
            ),
            "output_tokens": sum(
                r.response.output_tokens or 0 for r in runs
            ),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "task_count": len(self.tasks),
            "run_count": len(self.runs),
            "failed_runs": [
                f"{run.task_id}/{run.condition.value}"
                for run in self.failed_runs
            ],
            "conditions": {
                condition.value: self.condition_summary(condition)
                for condition in PairingCondition
            },
            "runs": [run.to_dict() for run in self.runs],
        }

    def render_markdown(self) -> str:
        lines = [
            "# 真实模型画像配对评测（四条件同模型同任务）",
            "",
            f"- 任务数：{len(self.tasks)}；调用数：{len(self.runs)}",
            f"- 总体结论：{'通过' if self.passed else '不通过（失败不放行）'}",
            "",
            "## 条件汇总",
            "",
            "| 条件 | 次数 | 通过 | 平均延迟(ms) | 输入 token | 输出 token |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for condition in PairingCondition:
            summary = self.condition_summary(condition)
            lines.append(
                f"| {condition.value} | {summary['runs']} | {summary['passed']} | "
                f"{summary['latency_ms_avg']} | {summary['input_tokens']} | "
                f"{summary['output_tokens']} |"
            )
        lines.append("")
        for run in self.runs:
            status = "通过" if run.passed else "不通过"
            lines.append(
                f"### {run.task_id} / {run.condition.value}（{status}，"
                f"{run.response.latency_ms}ms，"
                f"{len(run.response.answer)} 字）"
            )
            lines.append("")
            for checkpoint in run.checkpoints:
                mark = "x" if checkpoint.passed else " "
                gate = "（硬门）" if checkpoint.hard_gate else ""
                lines.append(
                    f"- [{mark}] {checkpoint.title}{gate}：{checkpoint.detail}"
                )
            lines.append("")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 内容检查：只看具体内容，不用条数或套话代替
# ---------------------------------------------------------------------------


def _first_position(text: str, markers: tuple[str, ...]) -> int | None:
    positions = [text.find(marker) for marker in markers if marker in text]
    return min(positions) if positions else None


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def evaluate_answer(
    task: PairingTask, condition: PairingCondition, answer: str
) -> tuple[list[Checkpoint], dict[str, Any]]:
    """按条件检查回答的具体内容，返回检查点与可比测量值。"""

    checks: list[Checkpoint] = []
    measurements: dict[str, Any] = {}

    topic_kept = _contains_any(answer, task.topic_terms)
    checks.append(
        Checkpoint(
            checkpoint_id="topic_kept",
            title="回答仍然回答原任务（旧兴趣/画像不替换任务）",
            passed=topic_kept,
            detail=f"主题词：{task.topic_terms}",
            hard_gate=True,
        )
    )
    cliche_hits = [c for c in PERSONALIZATION_CLICHES if c in answer]
    checks.append(
        Checkpoint(
            checkpoint_id="no_cliche",
            title="不使用个性化套话",
            passed=not cliche_hits,
            detail=f"命中：{cliche_hits}",
        )
    )

    example_pos = _first_position(answer, _EXAMPLE_MARKERS)
    formula_pos = _first_position(answer, _FORMULA_MARKERS)
    measurements["example_present"] = example_pos is not None
    measurements["example_before_formula"] = bool(
        example_pos is not None and (formula_pos is None or example_pos < formula_pos)
    )
    measurements["answer_length"] = len(answer)

    if condition == PairingCondition.CORRECT:
        if task.correct_example_first:
            checks.append(
                Checkpoint(
                    checkpoint_id="correct_example_first",
                    title="正确画像让回答先给例子再讲公式",
                    passed=measurements["example_before_formula"],
                    detail=(
                        f"example_pos={example_pos} formula_pos={formula_pos}"
                    ),
                    hard_gate=True,
                )
            )
        if task.correct_required_any:
            reflected = _contains_any(answer, task.correct_required_any)
            measurements["correct_constraint_reflected"] = reflected
            checks.append(
                Checkpoint(
                    checkpoint_id="correct_constraint_reflected",
                    title="正确画像的事实/约束进入具体内容",
                    passed=reflected,
                    detail=f"应有其一：{task.correct_required_any}",
                    hard_gate=True,
                )
            )

    if condition == PairingCondition.OUTDATED:
        stale_hits = [
            term for term in task.outdated_forbidden if term in answer
        ]
        measurements["outdated_fact_echoed"] = stale_hits
        checks.append(
            Checkpoint(
                checkpoint_id="outdated_fact_absent",
                title="过时/无关事实不进入回答",
                passed=not stale_hits,
                detail=f"命中过时词：{stale_hits}",
                hard_gate=True,
            )
        )
    return checks, measurements


# ---------------------------------------------------------------------------
# 配对编排
# ---------------------------------------------------------------------------

#: 两个配对任务：概念解释（举例顺序）与计划制定（时间约束）。
PAIRING_TASKS: tuple[PairingTask, ...] = (
    PairingTask(
        task_id="bayes-explain",
        prompt="请给我讲讲贝叶斯定理，帮我理解它的含义和用法。",
        topic_terms=("贝叶斯", "概率"),
        correct_facts=("我正在学习概率统计，希望先举例再讲公式",),
        wrong_facts=("我已经学完概率论，希望只讲严格证明，不要举例",),
        outdated_facts=("我计划下周通过英语六级考试", "我最近在学 Python"),
        correct_example_first=True,
        outdated_forbidden=("六级", "Python", "python"),
    ),
    PairingTask(
        task_id="study-plan",
        prompt="帮我制定一个两周的线性代数复习计划。",
        topic_terms=("线性代数", "线代", "复习", "矩阵", "行列式", "向量", "特征值"),
        correct_facts=(
            "我每天大概只能安排 30 分钟学习数学",
            "我的目标是通过线性代数期末考试",
        ),
        wrong_facts=("我每天可以学习 8 小时",),
        outdated_facts=("我下周有英语六级考试",),
        correct_required_any=("30", "半小时", "三十分钟"),
        outdated_forbidden=("六级", "英语"),
    ),
)


def run_pairing(
    sender: PairingSender,
    *,
    tasks: tuple[PairingTask, ...] = PAIRING_TASKS,
    conditions: tuple[PairingCondition, ...] = tuple(PairingCondition),
) -> PairingReport:
    """按任务 × 条件执行配对生成并检查具体内容。"""

    runs: list[PairingRun] = []
    for task in tasks:
        for condition in conditions:
            started = time.monotonic()
            try:
                response = sender(task, condition)
            except Exception as exc:  # noqa: BLE001 - 失败收敛为不通过结果
                response = PairingResponse(
                    status="error",
                    answer="",
                    latency_ms=int((time.monotonic() - started) * 1000),
                    error_code=exc.__class__.__name__.lower(),
                )
            checks, measurements = evaluate_answer(task, condition, response.answer)
            runs.append(
                PairingRun(
                    task_id=task.task_id,
                    condition=condition,
                    response=response,
                    checkpoints=tuple(checks),
                    measurements=measurements,
                )
            )
    return PairingReport(tasks=tasks, runs=runs)


def blind_review_bundle(
    report: PairingReport, *, seed: int = 41
) -> tuple[str, dict[str, str]]:
    """产出匿名盲评材料与「匿名编号 → 条件」映射。"""

    shuffled = list(report.runs)
    random.Random(seed).shuffle(shuffled)
    mapping: dict[str, str] = {}
    lines = [
        "# 画像配对盲评材料",
        "",
        "以下回答来自同一模型同一任务的不同画像条件（编号已打乱，",
        "评审人未见条件标签）。请分别判断：是否回答了任务、是否按",
        "个人情况具体适配、是否出现无关事实或套话。",
        "",
    ]
    for index, run in enumerate(shuffled, start=1):
        label = f"case-{index:02d}"
        mapping[label] = f"{run.task_id}/{run.condition.value}"
        lines.extend(
            [
                f"## {label}（任务提示：{_task_prompt(report, run.task_id)}）",
                "",
                run.response.answer.strip() or "（无回答，状态："
                f"{run.response.status}）",
                "",
            ]
        )
    return "\n".join(lines), mapping


def _task_prompt(report: PairingReport, task_id: str) -> str:
    for task in report.tasks:
        if task.task_id == task_id:
            return task.prompt
    return task_id


# ---------------------------------------------------------------------------
# 真实模型发送器（脚本使用；测试注入替身）
# ---------------------------------------------------------------------------


def make_chat_sender(
    gateway: Any, *, account_id: str = "eval41-account"
) -> Callable[[PairingTask, PairingCondition], PairingResponse]:
    """构造真实聊天发送器：每次调用独立账户环境并播种条件事实。"""

    def send(task: PairingTask, condition: PairingCondition) -> PairingResponse:
        from bridges.chat.repository import ConversationRepository
        from bridges.chat.service import ChatService
        from bridges.contracts.chat import ChatMode
        from bridges.contracts.projects import ObjectDomain
        from bridges.contracts.workflows import RunContextEnvelope
        from bridges.profiles.adapters import InMemoryProfileRepository
        from bridges.profiles.atomic import (
            AtomicProfileService,
            InMemoryAtomicProfileRepository,
        )
        from bridges.profiles.automatic import (
            AutomaticProfileService,
            InMemoryAutomaticProfileRepository,
        )
        from bridges.profiles.four_dimensions import (
            FourDimensionProfileService,
            InMemoryFourDimensionProfileRepository,
        )
        from bridges.storage.database import BridgesDatabase

        database = BridgesDatabase(":memory:")
        database.initialize()
        conversations = ConversationRepository(database)
        four = FourDimensionProfileService(
            InMemoryProfileRepository(), InMemoryFourDimensionProfileRepository()
        )
        atomic = AtomicProfileService(four, InMemoryAtomicProfileRepository())
        automatic = AutomaticProfileService(
            four_dimension_service=four,
            repository=InMemoryAutomaticProfileRepository(),
            atomic_profile_service=atomic,
        )
        now = datetime.now(UTC)
        for index, fact in enumerate(task.facts_for(condition)):
            source_at = (
                now - timedelta(days=14)
                if condition == PairingCondition.OUTDATED
                else now
            )
            atomic.remember(
                account_id,
                fact,
                source_message_id=f"m-{task.task_id}-{index}",
                source_at=source_at,
            )
        service = ChatService(
            repository=conversations,
            gateway=gateway,
            four_dimension_profile_service=four,
            atomic_profile_service=atomic,
            automatic_profile_service=automatic,
        )
        conversation = service.create_conversation(
            account_id, mode=ChatMode.COMPANION
        )
        started = time.monotonic()
        user, assistant, _ = service.start_generation(
            account_id, conversation.conversation_id, task.prompt
        )
        context = RunContextEnvelope(
            run_id=f"pair-{task.task_id}-{condition.value}",
            account_id=account_id,
            project_id=conversation.conversation_id,
            workflow_name="chat",
            workflow_version="1",
            object_domain=ObjectDomain.PERSONAL_VAULT,
            submitted_at=datetime.now(UTC),
        )
        lock = None
        error_code: str | None = None
        for event in service.stream_generation(
            account_id,
            conversation.conversation_id,
            assistant.message_id,
            context,
            until_user_message_id=user.message_id,
        ):
            if getattr(event, "kind", None) == "error":
                error_code = getattr(event, "error_code", None)
            if getattr(event, "lock", None) is not None:
                lock = event.lock
        latency_ms = int((time.monotonic() - started) * 1000)
        final = service.message_projection(account_id, assistant.message_id)
        answer = final.content if final is not None else ""
        status = final.status.value if final is not None else "error"
        usage = (lock.usage or {}) if lock is not None else {}
        return PairingResponse(
            status="done" if status == "done" else "error",
            answer=answer,
            latency_ms=latency_ms,
            model_id=getattr(lock, "actual_model_id", None),
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            cost_estimate=getattr(lock, "cost_estimate", None),
            profile_item_count=(
                final.context_note.profile_item_count
                if final is not None and final.context_note is not None
                else None
            ),
            error_code=error_code,
        )

    return send


def write_reports(report: PairingReport, output_dir: Any) -> dict[str, Any]:
    """把配对报告、盲评材料与映射写入目录，返回落盘清单。"""

    from pathlib import Path

    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    blind_markdown, mapping = blind_review_bundle(report)
    payload = report.to_dict()
    payload["blind_review_map"] = mapping
    (target / "pairing-report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (target / "pairing-report.md").write_text(
        report.render_markdown(), encoding="utf-8"
    )
    (target / "blind-review.md").write_text(blind_markdown, encoding="utf-8")
    return {
        "pairing-report.json": True,
        "pairing-report.md": True,
        "blind-review.md": True,
    }


__all__ = [
    "PAIRING_TASKS",
    "PERSONALIZATION_CLICHES",
    "PairingCondition",
    "PairingReport",
    "PairingResponse",
    "PairingRun",
    "PairingSender",
    "PairingTask",
    "blind_review_bundle",
    "evaluate_answer",
    "make_chat_sender",
    "run_pairing",
    "write_reports",
]
