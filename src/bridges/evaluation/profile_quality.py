"""画像质量纵向评测（改进工单 41，确定性机制层）。

本模块复用既有评测底座（``contracts.evaluation_suite`` 的维度与量表语义、
``report`` 的置信区间统计），在真实画像服务（16–22）之上执行纵向场景，
产出可复现的检查点与测量值：

- **十类纵向场景**：否定偏好、多事实并存、用户+第三方/引用/情绪混合、
  回指、时间锚、并行目标与精准替代、编辑/删除/近义抑制与明确恢复、
  LOW 镜像、停止记录后忘掉、自述与答题证据分离。
- **控制/账户隔离硬门**：忘掉在停止记录后仍生效、账户互不可见、
  迟到后台任务不复活已撤回事实。
- **异步时序与事务边界**：登记→领取→提交时间戳、尝试次数、模型调用
  次数、模型调用在写事务外。
- **来源支持与完整事实**：每条活动事实都有可定位来源；并行事实同时
  存在而非互相覆盖。

确定性替身只证明机制（任务 1/2/3/5），真实模型抽取与回答改善由
``profile_pairing`` 的真实配对与 41 报告验证。
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import Any

from bridges.contracts.atomic_profile import (
    AtomicProfileFeedbackEffect,
    AtomicProfileFeedbackKind,
    AtomicProfileItemFeedbackRequest,
    AtomicProfileItemModifyRequest,
    AtomicProfileItemStatus,
    AtomicProfileValidityStatus,
)
from bridges.contracts.profile_extraction import ProfileExtractionOutput
from bridges.contracts.profiles import (
    FourDimension,
    FourDimensionConfidence,
)
from bridges.profiles.adapters import InMemoryProfileRepository
from bridges.profiles.atomic import (
    AtomicProfileService,
    InMemoryAtomicProfileRepository,
    ProfileSourceMessage,
)
from bridges.profiles.automatic import (
    AutomaticProfileService,
    InMemoryAutomaticProfileRepository,
)
from bridges.profiles.four_dimensions import (
    FourDimensionProfileService,
    InMemoryFourDimensionProfileRepository,
)
from bridges.profiles.purpose import build_purpose

#: 评测账户（合成，不来自真实用户）。
ACCOUNT_A = "eval41-account-a"
ACCOUNT_B = "eval41-account-b"

#: 场景时间锚（确定性；相对时间以来源消息时间为锚）。
ANCHOR = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


@dataclass(frozen=True)
class Checkpoint:
    """一条通过/失败检查点；``hard_gate`` 不通过时整体失败不放行。"""

    checkpoint_id: str
    title: str
    passed: bool
    detail: str
    hard_gate: bool = False


@dataclass
class ScenarioResult:
    """一个纵向场景的检查点与测量值。"""

    scenario_id: str
    title: str
    checkpoints: list[Checkpoint]
    measurements: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return all(checkpoint.passed for checkpoint in self.checkpoints)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "title": self.title,
            "passed": self.passed,
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
class ProfileQualityReport:
    """确定性纵向评测的汇总报告（样本量、通过率、硬门与测量）。"""

    scenarios: list[ScenarioResult]
    environment: dict[str, Any] = field(default_factory=dict)

    @property
    def checkpoints(self) -> list[Checkpoint]:
        return [c for s in self.scenarios for c in s.checkpoints]

    @property
    def hard_gates(self) -> list[Checkpoint]:
        return [c for c in self.checkpoints if c.hard_gate]

    @property
    def passed(self) -> bool:
        return all(checkpoint.passed for checkpoint in self.checkpoints)

    @property
    def pass_rate(self) -> float:
        checks = self.checkpoints
        return sum(1 for c in checks if c.passed) / len(checks) if checks else 1.0

    @property
    def scenario_pass_rate(self) -> float:
        return (
            sum(1 for s in self.scenarios if s.passed) / len(self.scenarios)
            if self.scenarios
            else 1.0
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "pass_rate": self.pass_rate,
            "scenario_pass_rate": self.scenario_pass_rate,
            "scenario_count": len(self.scenarios),
            "checkpoint_count": len(self.checkpoints),
            "hard_gate_failures": [
                checkpoint.checkpoint_id for checkpoint in self.hard_gates if not checkpoint.passed
            ],
            "environment": dict(self.environment),
            "scenarios": [scenario.to_dict() for scenario in self.scenarios],
        }

    def render_markdown(self) -> str:
        lines = [
            "# 画像质量纵向评测报告（确定性机制层）",
            "",
            f"- 场景数：{len(self.scenarios)}；检查点：{len(self.checkpoints)}"
            f"（通过率 {self.pass_rate:.0%}）",
            f"- 硬门不通过：{len([c for c in self.hard_gates if not c.passed])}",
            f"- 总体结论：{'通过' if self.passed else '不通过（失败不放行）'}",
            "",
        ]
        for scenario in self.scenarios:
            status = "通过" if scenario.passed else "不通过"
            lines.append(f"## {scenario.scenario_id} — {scenario.title}（{status}）")
            lines.append("")
            for checkpoint in scenario.checkpoints:
                mark = "x" if checkpoint.passed else " "
                gate = "（硬门）" if checkpoint.hard_gate else ""
                lines.append(f"- [{mark}] {checkpoint.title}{gate}：{checkpoint.detail}")
            if scenario.measurements:
                lines.append("")
                lines.append(f"- 测量：`{scenario.measurements}`")
            lines.append("")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 固定候选抽取器（确定性替身；证据区间必须精确支持取值）
# ---------------------------------------------------------------------------


class FixedCandidateExtractor:
    """按消息返回固定候选；只用于机制评测，不模拟真实模型质量。"""

    version = "issue41-fixed-candidates-v1"

    def __init__(self, candidates: dict[str, list[dict[str, Any]]]) -> None:
        self._candidates = candidates
        self.calls: list[str] = []

    def extract(self, **kwargs: Any) -> ProfileExtractionOutput:
        message_id = str(kwargs.get("message_id", ""))
        content = str(kwargs.get("content", ""))
        self.calls.append(message_id)
        items: list[dict[str, Any]] = []
        for raw in self._candidates.get(message_id, []):
            candidate = dict(raw)
            value = str(candidate.get("normalized_value", ""))
            candidate["evidence_ref"] = message_id
            start = candidate.pop("evidence_start", None)
            end = candidate.pop("evidence_end", None)
            if start is None:
                index = content.find(value)
                start = index if index >= 0 else 0
                end = start + len(value)
            candidate["evidence_start"] = int(start)
            candidate["evidence_end"] = int(end)
            items.append(candidate)
        return ProfileExtractionOutput.model_validate({"items": items})


def _candidate(
    value: str,
    *,
    fact_text: str | None = None,
    dimension: FourDimension = FourDimension.KNOWLEDGE_INTEREST,
    action: str = "create",
    reliability: float = 0.99,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "dimension": dimension.value,
        "normalized_value": value,
        "action": action,
        "reliability": reliability,
    }
    if fact_text is not None:
        payload["fact_text"] = fact_text
    return payload


# ---------------------------------------------------------------------------
# 场景执行环境
# ---------------------------------------------------------------------------


class ProfileLab:
    """一个账户的确定性画像实验台（真实服务 + 内存仓库）。"""

    def __init__(
        self,
        candidates: dict[str, list[dict[str, Any]]] | None = None,
        *,
        message_clock: dict[str, datetime] | None = None,
    ) -> None:
        self.four = FourDimensionProfileService(
            InMemoryProfileRepository(), InMemoryFourDimensionProfileRepository()
        )
        self.atomic_repository = InMemoryAtomicProfileRepository()
        self.atomic = AtomicProfileService(
            self.four,
            self.atomic_repository,
            message_reader=self._read_source_message,
        )
        self.extractor = FixedCandidateExtractor(candidates or {})
        self.automatic = AutomaticProfileService(
            four_dimension_service=self.four,
            repository=InMemoryAutomaticProfileRepository(),
            extractor=self.extractor,
            atomic_profile_service=self.atomic,
        )
        self._messages: dict[str, ProfileSourceMessage] = {}
        self._message_clock = message_clock or {}
        self.run_records: list[dict[str, Any]] = []

    # -- 消息与来源 --------------------------------------------------

    def register_message(
        self,
        account_id: str,
        message_id: str,
        content: str,
        *,
        conversation_id: str = "eval41-conversation",
        created_at: datetime | None = None,
    ) -> ProfileSourceMessage:
        message = ProfileSourceMessage(
            message_id=message_id,
            conversation_id=conversation_id,
            role="user",
            status="done",
            content=content,
            created_at=created_at or self._message_clock.get(message_id, ANCHOR),
        )
        self._messages[message_id] = message
        return message

    def _read_source_message(self, account_id: str, message_id: str) -> ProfileSourceMessage | None:
        return self._messages.get(message_id)

    def schedule(
        self,
        account_id: str,
        message_id: str,
        content: str,
        *,
        created_at: datetime | None = None,
    ):
        self.register_message(account_id, message_id, content, created_at=created_at)
        return self.automatic.schedule_message_extraction(
            account_id,
            conversation_id="eval41-conversation",
            message_id=message_id,
            content=content,
            run_id=f"run-{message_id}",
        )

    def drain(self, *, max_ticks: int = 8) -> list[str]:
        """运行后台 worker 直到没有待处理任务（有界）。"""

        messages: list[str] = []
        for _ in range(max_ticks):
            outcome = self.automatic.run_retry_tick()
            messages.append(outcome)
            if "无待处理任务" in outcome:
                break
        self._capture_run_records()
        return messages

    def _capture_run_records(self) -> None:
        for run in self.automatic._repository.list_runs():  # noqa: SLF001 - 评测只读审计
            self.run_records.append(
                {
                    "extraction_id": run.extraction_id,
                    "message_id": run.message_id,
                    "status": run.status.value,
                    "attempts": run.attempts,
                    "outcome": run.outcome.value,
                    "created_at": run.created_at.isoformat(),
                    "updated_at": run.updated_at.isoformat(),
                    "source": run.source.value if run.source else None,
                }
            )

    # -- 读取视图 ----------------------------------------------------

    def active_texts(self, account_id: str) -> list[str]:
        return [
            item.text
            for item in self.atomic.list_items(account_id)
            if item.status == AtomicProfileItemStatus.ACTIVE
        ]

    def all_items(self, account_id: str):
        return self.atomic.list_items(account_id)

    def released_texts(self, account_id: str) -> list[str]:
        return [
            item.text
            for item in self.atomic.list_items(account_id)
            if item.status != AtomicProfileItemStatus.ACTIVE
        ]

    def recall(self, account_id: str, question: str, *, now: datetime | None = None):
        purpose = build_purpose(mode="daily", query=question)
        return self.atomic.compile_adopted_slice(
            account_id,
            run_id=f"run-eval-{question}",
            purpose=purpose,
            now=now,
        )

    def recalled_texts(
        self,
        account_id: str,
        question: str,
        *,
        now: datetime | None = None,
    ) -> list[str]:
        return [item.fact_text for item in self.recall(account_id, question, now=now).adopted_items]


def _check(
    checkpoint_id: str,
    title: str,
    passed: bool,
    detail: str,
    *,
    hard_gate: bool = False,
) -> Checkpoint:
    return Checkpoint(
        checkpoint_id=checkpoint_id,
        title=title,
        passed=bool(passed),
        detail=detail,
        hard_gate=hard_gate,
    )


def _contains_negated(text: str) -> bool:
    return any(marker in text for marker in ("不", "没", "别", "无需", "不要"))


# ---------------------------------------------------------------------------
# 场景 1：否定偏好保留完整分句，绝不写成肯定值
# ---------------------------------------------------------------------------


def scenario_negated_preference() -> ScenarioResult:
    lab = ProfileLab(
        {
            "m-neg-1": [
                _candidate(
                    "不喜欢长篇回答",
                    fact_text="我不喜欢长篇回答",
                    dimension=FourDimension.KNOWLEDGE_INTEREST,
                )
            ]
        }
    )
    lab.schedule(ACCOUNT_A, "m-neg-1", "我不喜欢长篇回答")
    lab.drain()
    active = lab.active_texts(ACCOUNT_A)
    negation_kept = any("不喜欢长篇回答" in text for text in active)
    no_inversion = not any(
        "喜欢长篇回答" in text and not _contains_negated(text) for text in active
    )
    return ScenarioResult(
        scenario_id="negated_preference",
        title="否定偏好保留完整分句且不反转为肯定",
        checkpoints=[
            _check(
                "negation_kept",
                "否定分句完整保存",
                negation_kept,
                f"活动条目：{active}",
            ),
            _check(
                "no_positive_inversion",
                "不写入肯定版本",
                no_inversion,
                f"活动条目：{active}",
                hard_gate=True,
            ),
        ],
        measurements={"active_count": len(active)},
    )


# ---------------------------------------------------------------------------
# 场景 2：一条消息多个事实并存
# ---------------------------------------------------------------------------


def scenario_multi_fact_coexistence() -> ScenarioResult:
    lab = ProfileLab(
        {
            "m-multi-1": [
                _candidate("喜欢跑步", fact_text="我喜欢跑步"),
                _candidate("喜欢游泳", fact_text="我喜欢游泳"),
            ]
        }
    )
    lab.schedule(ACCOUNT_A, "m-multi-1", "我喜欢跑步，也喜欢游泳")
    lab.drain()
    active = lab.active_texts(ACCOUNT_A)
    both = any("跑步" in text for text in active) and any("游泳" in text for text in active)
    return ScenarioResult(
        scenario_id="multi_fact_coexistence",
        title="一条消息的多条事实并存而不是只保留一条",
        checkpoints=[
            _check(
                "two_facts_coexist",
                "跑步与游泳同时存在",
                both,
                f"活动条目：{active}",
                hard_gate=True,
            ),
        ],
        measurements={"active_count": len(active)},
    )


# ---------------------------------------------------------------------------
# 场景 3：用户 + 第三方/引用/情绪混合，只写用户合规片段
# ---------------------------------------------------------------------------


def scenario_mixed_subject_quote_emotion() -> ScenarioResult:
    content = "我喜欢跑步，我朋友喜欢摄影，他还说“跑步伤膝盖”，最近我有点焦虑。"
    lab = ProfileLab(
        {
            "m-mixed-1": [
                _candidate("喜欢跑步", fact_text="我喜欢跑步"),
                _candidate("喜欢摄影", fact_text="我朋友喜欢摄影"),
                _candidate("有点焦虑", fact_text="我有点焦虑"),
            ]
        }
    )
    lab.schedule(ACCOUNT_A, "m-mixed-1", content)
    lab.drain()
    active = lab.active_texts(ACCOUNT_A)
    self_fact_written = any("跑步" in text for text in active)
    third_party_absent = not any("摄影" in text for text in active)
    emotion_absent = not any("焦虑" in text for text in active)
    return ScenarioResult(
        scenario_id="mixed_subject_quote_emotion",
        title="混合消息只写用户合规片段，第三方/引用/情绪零写入",
        checkpoints=[
            _check(
                "self_fact_written",
                "合规自述写入",
                self_fact_written,
                f"活动条目：{active}",
            ),
            _check(
                "third_party_absent",
                "第三方内容零写入",
                third_party_absent,
                f"活动条目：{active}",
                hard_gate=True,
            ),
            _check(
                "emotion_absent",
                "情绪线索不进入稳定画像",
                emotion_absent,
                f"活动条目：{active}",
                hard_gate=True,
            ),
        ],
        measurements={"active_count": len(active)},
    )


# ---------------------------------------------------------------------------
# 场景 4：回指不产生新事实，既有背景可跨轮使用
# ---------------------------------------------------------------------------


def scenario_anaphora() -> ScenarioResult:
    lab = ProfileLab(
        {
            "m-ana-1": [
                _candidate(
                    "学习概率统计",
                    fact_text="我正在学习概率统计",
                    dimension=FourDimension.ACADEMIC_STATUS,
                )
            ]
        }
    )
    lab.schedule(ACCOUNT_A, "m-ana-1", "我正在学习概率统计")
    lab.drain()
    # 后续回指消息没有独立证据，不应产生新事实。
    lab.schedule(ACCOUNT_A, "m-ana-2", "我想继续学这个，先看例子")
    lab.drain()
    active = lab.active_texts(ACCOUNT_A)
    no_duplicate = len([t for t in active if "概率" in t]) == 1
    no_dangling = not any("这个" in text for text in active)
    recalled = lab.recalled_texts(ACCOUNT_A, "解释概率分布")
    background_used = any("概率" in text for text in recalled)
    return ScenarioResult(
        scenario_id="anaphora",
        title="回指不写入新事实，既有背景可跨轮召回",
        checkpoints=[
            _check(
                "no_duplicate_fact",
                "回指消息不重复写入",
                no_duplicate,
                f"活动条目：{active}",
            ),
            _check(
                "no_dangling_reference",
                "回指词不成为事实对象",
                no_dangling,
                f"活动条目：{active}",
                hard_gate=True,
            ),
            _check(
                "background_recalled",
                "既有背景跨轮按用途召回",
                background_used,
                f"采用条目：{recalled}",
            ),
        ],
        measurements={"active_count": len(active)},
    )


# ---------------------------------------------------------------------------
# 场景 5：时间锚与有效期
# ---------------------------------------------------------------------------


def scenario_time_anchor() -> ScenarioResult:
    lab = ProfileLab()
    item = lab.atomic.remember(
        ACCOUNT_A,
        "我计划下周通过英语六级考试",
        source_message_id="m-time-1",
        source_at=ANCHOR,
    )
    preference = lab.atomic.remember(
        ACCOUNT_A,
        "我平时喜欢跑步",
        source_message_id="m-time-2",
        source_at=ANCHOR,
    )
    anchored = (
        item.valid_from is not None
        and item.valid_until is not None
        and item.validity_anchor_at == ANCHOR
    )
    expired_at = (
        item.valid_until + timedelta(seconds=1)
        if item.valid_until is not None
        else ANCHOR + timedelta(days=14)
    )
    in_window = any(
        "英语六级" in text for text in lab.recalled_texts(ACCOUNT_A, "英语六级考试", now=ANCHOR)
    )
    after_window = not any(
        "英语六级" in text for text in lab.recalled_texts(ACCOUNT_A, "英语六级考试", now=expired_at)
    )
    long_term_kept = any(
        "跑步" in text
        for text in lab.recalled_texts(
            ACCOUNT_A, "我平时喜欢跑步，推荐运动", now=ANCHOR + timedelta(days=365)
        )
    )
    return ScenarioResult(
        scenario_id="time_anchor",
        title="相对时间以来源消息为锚，过期不注入，长期偏好无统一 TTL",
        checkpoints=[
            _check(
                "validity_anchored",
                "相对时间解析为绝对区间并保留锚点",
                anchored,
                f"valid_from={item.valid_from} valid_until={item.valid_until} "
                f"anchor={item.validity_anchor_at}",
            ),
            _check("in_window_recall", "有效期内可召回", in_window, "下周考试"),
            _check(
                "expired_not_recalled",
                "过期后停止注入",
                after_window,
                "过期时间点切片不含该事实",
                hard_gate=True,
            ),
            _check(
                "long_term_no_ttl",
                "长期偏好不因统一 TTL 丢失",
                long_term_kept,
                f"偏好条目：{preference.text}",
            ),
        ],
        measurements={
            "valid_from": item.valid_from.isoformat() if item.valid_from else None,
            "valid_until": item.valid_until.isoformat() if item.valid_until else None,
        },
    )


# ---------------------------------------------------------------------------
# 场景 6：并行目标并存与精准替代
# ---------------------------------------------------------------------------


def scenario_parallel_goals_precise_change() -> ScenarioResult:
    lab = ProfileLab()
    kaoyan = lab.atomic.remember(
        ACCOUNT_A, "我计划考研", source_message_id="m-goal-1", source_at=ANCHOR
    )
    cet6 = lab.atomic.remember(
        ACCOUNT_A, "我计划通过英语六级", source_message_id="m-goal-2", source_at=ANCHOR
    )
    both_active = {"我计划考研", "我计划通过英语六级"} <= set(lab.active_texts(ACCOUNT_A))
    before_change = list(lab.active_texts(ACCOUNT_A))
    # 变更前冻结的画像切片：变更后必须按版本失效，不能在下一轮继续注入。
    adopted_before_change = lab.recall(ACCOUNT_A, "我计划考研还是就业")
    slice_current_before = lab.atomic.is_adopted_slice_current(ACCOUNT_A, adopted_before_change)
    # 用户行内编辑：把考研目标替换成就业目标；六级不受影响。
    lab.atomic.modify_item(
        ACCOUNT_A,
        kaoyan.profile_item_id,
        AtomicProfileItemModifyRequest(text="我计划毕业后直接就业", version=kaoyan.version),
    )
    slice_invalidated = not lab.atomic.is_adopted_slice_current(ACCOUNT_A, adopted_before_change)
    active_after = lab.active_texts(ACCOUNT_A)
    old_released = not any(text == "我计划考研" for text in active_after)
    replacement_active = any("直接就业" in text for text in active_after)
    cet6_untouched = any("英语六级" in text for text in active_after)
    return ScenarioResult(
        scenario_id="parallel_goals_precise_change",
        title="并行目标并存，明确替代只影响对应事实",
        checkpoints=[
            _check(
                "parallel_coexist",
                "考研与六级同时存在",
                both_active,
                f"活动条目：{before_change}",
            ),
            _check(
                "old_goal_released",
                "被替代旧目标不再活动",
                old_released,
                f"活动条目：{active_after}",
            ),
            _check(
                "replacement_active",
                "新目标成为活动事实",
                replacement_active,
                f"活动条目：{active_after}",
            ),
            _check(
                "unrelated_untouched",
                "无关目标不被连带覆盖",
                cet6_untouched,
                f"活动条目：{active_after}",
                hard_gate=True,
            ),
            _check(
                "slice_invalidated_after_change",
                "已变更事实使旧画像切片按版本失效",
                slice_current_before and slice_invalidated,
                f"变更前有效={slice_current_before} 变更后失效={slice_invalidated}",
                hard_gate=True,
            ),
        ],
        measurements={
            "kaoyan_id": kaoyan.profile_item_id,
            "cet6_id": cet6.profile_item_id,
        },
    )


# ---------------------------------------------------------------------------
# 场景 7：删除后近义抑制与明确重新记住
# ---------------------------------------------------------------------------


def scenario_delete_synonym_and_recovery() -> ScenarioResult:
    lab = ProfileLab(
        {
            "m-del-2": [_candidate("喜欢慢跑", fact_text="我喜欢慢跑")],
        }
    )
    lab.atomic.remember(ACCOUNT_A, "我喜欢跑步", source_message_id="m-del-1", source_at=ANCHOR)
    item = next(entry for entry in lab.all_items(ACCOUNT_A) if "跑步" in entry.text)
    lab.atomic.delete_item(ACCOUNT_A, item.profile_item_id, item.version)
    lab.schedule(ACCOUNT_A, "m-del-2", "我喜欢慢跑")
    lab.drain()
    after_synonym = lab.active_texts(ACCOUNT_A)
    suppressed = not any("慢跑" in text for text in after_synonym)
    recovered_item = lab.atomic.remember(
        ACCOUNT_A, "我喜欢跑步", source_message_id="m-del-3", source_at=ANCHOR
    )
    recovered = any("跑步" in text for text in lab.active_texts(ACCOUNT_A))
    new_source_recorded = "m-del-3" in recovered_item.source_message_ids
    return ScenarioResult(
        scenario_id="delete_synonym_and_recovery",
        title="删除后普通近义不恢复，明确重新记住只以新来源恢复",
        checkpoints=[
            _check(
                "synonym_suppressed",
                "普通近义提及不恢复",
                suppressed,
                f"活动条目：{after_synonym}",
                hard_gate=True,
            ),
            _check(
                "explicit_recovery",
                "明确重新记住可恢复",
                recovered,
                f"恢复条目：{recovered_item.text}",
            ),
            _check(
                "recovery_new_source_recorded",
                "恢复记录新授权来源，不靠旧消息自动复活",
                new_source_recorded,
                f"来源：{recovered_item.source_message_ids}",
            ),
        ],
        measurements={"deleted_id": item.profile_item_id},
    )


# ---------------------------------------------------------------------------
# 场景 8：LOW 把握度镜像不进入回答切片
# ---------------------------------------------------------------------------


def scenario_low_confidence_mirror() -> ScenarioResult:
    lab = ProfileLab()
    record = lab.four.upsert_automatic_record(
        ACCOUNT_A,
        dimension=FourDimension.KNOWLEDGE_INTEREST,
        content="概率统计",
        action="create",
        confidence=FourDimensionConfidence.LOW,
        evidence_message_id="low-evidence",
    )
    mirrored = lab.atomic.mirror_record(ACCOUNT_A, record)
    recalled = lab.recalled_texts(ACCOUNT_A, "概率统计")
    excluded_reasons = {
        item.fact_text: item.exclusion_reason
        for item in lab.recall(ACCOUNT_A, "概率统计").excluded_items
    }
    not_recalled = not any("概率统计" in text for text in recalled)
    has_reason = any("概率统计" in text and reason for text, reason in excluded_reasons.items())
    return ScenarioResult(
        scenario_id="low_confidence_mirror",
        title="LOW 把握度镜像条目不作为确定事实召回",
        checkpoints=[
            _check(
                "low_not_recalled",
                "低把握度条目不进入采用切片",
                not_recalled,
                f"采用条目：{recalled}",
                hard_gate=True,
            ),
            _check(
                "exclusion_reason_recorded",
                "排除原因可审计",
                has_reason,
                f"排除：{excluded_reasons}",
            ),
        ],
        measurements={"mirrored_id": mirrored.profile_item_id if mirrored else None},
    )


# ---------------------------------------------------------------------------
# 场景 9：停止记录后仍可忘掉，迟到任务不复活
# ---------------------------------------------------------------------------


def scenario_stop_recording_forget_and_late_task() -> ScenarioResult:
    lab = ProfileLab({"m-stop-late": [_candidate("喜欢跑步", fact_text="我喜欢跑步")]})
    lab.atomic.remember(ACCOUNT_A, "我喜欢跑步", source_message_id="m-stop-1", source_at=ANCHOR)
    # 先登记非空候选，再撤回：验证已存在的旧任务，而非关闭后才登记空任务。
    lab.schedule(ACCOUNT_A, "m-stop-late", "我喜欢跑步")
    lab.automatic.set_account_controls(ACCOUNT_A, recording_enabled=False)
    result = lab.automatic.process_synchronous_controls(
        ACCOUNT_A,
        conversation_id="eval41-conversation",
        message_id="m-stop-2",
        content="忘掉跑步",
        run_id="run-stop-2",
        mode="daily",
    )
    memory = result.memory if result is not None else None
    forgotten = bool(memory and memory.status.value == "forgotten" and memory.matched_count >= 1)
    active_after = lab.active_texts(ACCOUNT_A)
    gone = not any("跑步" in text for text in active_after)

    # 停止记录期间普通消息不产生新事实；迟到后台任务不复活已忘事实。
    lab.schedule(ACCOUNT_A, "m-stop-3", "我喜欢摄影")
    lab.drain()
    lab.schedule(ACCOUNT_A, "m-stop-1", "我喜欢跑步")
    lab.drain(max_ticks=2)
    final_active = lab.active_texts(ACCOUNT_A)
    no_revive = not any("跑步" in text for text in final_active)
    no_new = not any("摄影" in text for text in final_active)

    return ScenarioResult(
        scenario_id="stop_recording_forget_and_late_task",
        title="停止记录不挡主动忘掉，迟到任务不复活已撤回事实",
        checkpoints=[
            _check(
                "forget_still_works",
                "停止记录后忘掉仍即时生效",
                forgotten and gone,
                f"memory={memory.model_dump(mode='json') if memory else None} 活跃={active_after}",
                hard_gate=True,
            ),
            _check(
                "no_new_fact_while_blocked",
                "停止记录期间不自动新增",
                no_new,
                f"活动条目：{final_active}",
            ),
            _check(
                "late_task_no_revive",
                "消息重放/迟到任务不复活已撤回事实",
                no_revive,
                f"活动条目：{final_active}",
                hard_gate=True,
            ),
        ],
        measurements={"run_records": lab.run_records[-2:]},
    )


def scenario_sqlite_async_transactions() -> ScenarioResult:
    """实际 SQLite 仓库跨会话提交、重放、迟到竞争及事务占用测量。"""
    from bridges.profiles import (
        SqliteAtomicProfileRepository,
        SqliteAutomaticProfileRepository,
        SqliteFourDimensionProfileRepository,
    )
    from bridges.storage import BridgesDatabase

    class MeasuredDatabase(BridgesDatabase):
        def __init__(self):
            self.transaction_ms: list[float] = []
            super().__init__(":memory:")

        @contextmanager
        def transaction(self):
            with super().transaction():
                started = perf_counter()
                try:
                    yield
                finally:
                    self.transaction_ms.append((perf_counter() - started) * 1000)

    database = MeasuredDatabase()
    database.initialize()
    database.transaction_ms.clear()
    model_in_transaction: list[bool] = []
    calls: list[str] = []
    four = FourDimensionProfileService(
        InMemoryProfileRepository(), SqliteFourDimensionProfileRepository(database)
    )
    atomic = AtomicProfileService(four, SqliteAtomicProfileRepository(database))
    repository = SqliteAutomaticProfileRepository(database)
    candidate = FixedCandidateExtractor(
        {
            "m-sqlite": [_candidate("学习概率统计", fact_text="我正在学习概率统计")],
            "m-race": [_candidate("喜欢跑步", fact_text="我喜欢跑步")],
        }
    )

    class ProbeExtractor:
        version = "issue41-sqlite-probe-v1"

        def extract(self, **kwargs):
            model_in_transaction.append(database.connection.in_transaction)
            calls.append(kwargs["message_id"])
            if kwargs["message_id"] == "m-race":
                # worker 已领取并进入模型阶段时撤回，返回的旧候选必须被拒绝。
                item = next(item for item in atomic.list_items(ACCOUNT_A) if "跑步" in item.text)
                atomic.delete_item(ACCOUNT_A, item.profile_item_id, item.version)
            return candidate.extract(**kwargs)

    automatic = AutomaticProfileService(
        four_dimension_service=four,
        repository=repository,
        extractor=ProbeExtractor(),
        atomic_profile_service=atomic,
    )
    try:
        started = perf_counter()
        automatic.schedule_message_extraction(
            ACCOUNT_A,
            conversation_id="session-a",
            message_id="m-sqlite",
            content="我正在学习概率统计",
            run_id="run-sqlite",
        )
        before = atomic.compile_adopted_slice(
            ACCOUNT_A,
            run_id="session-b-before",
            purpose=build_purpose(mode="daily", query="解释概率统计"),
        )
        pending_hidden = not before.adopted_items and not calls
        automatic.run_retry_tick()
        committed = perf_counter()
        after = atomic.compile_adopted_slice(
            ACCOUNT_A,
            run_id="session-b-after",
            purpose=build_purpose(mode="daily", query="解释概率统计"),
        )
        automatic.schedule_message_extraction(
            ACCOUNT_A,
            conversation_id="session-a",
            message_id="m-sqlite",
            content="我正在学习概率统计",
            run_id="run-sqlite",
        )
        automatic.run_retry_tick()
        idempotent = calls == ["m-sqlite"]
        atomic.remember(ACCOUNT_A, "我喜欢跑步", source_message_id="m-race")
        old_slice = atomic.compile_adopted_slice(
            ACCOUNT_A, run_id="old-run", purpose=build_purpose(mode="daily", query="跑步")
        )
        automatic.schedule_message_extraction(
            ACCOUNT_A,
            conversation_id="session-a",
            message_id="m-race",
            content="我喜欢跑步",
            run_id="race-run",
        )
        automatic.run_retry_tick()
        active = [
            item.text
            for item in atomic.list_items(ACCOUNT_A)
            if item.status == AtomicProfileItemStatus.ACTIVE
        ]
        return ScenarioResult(
            "sqlite_async_transactions",
            "SQLite 跨会话提交与撤回竞争",
            [
                _check(
                    "pending_cross_session_hidden",
                    "后台未提交不在另一会话召回",
                    pending_hidden,
                    f"待提交采用={len(before.adopted_items)}",
                    hard_gate=True,
                ),
                _check(
                    "committed_cross_session_visible",
                    "提交后另一会话可用",
                    any("学习概率统计" in item.fact_text for item in after.adopted_items),
                    f"提交后采用={len(after.adopted_items)}",
                    hard_gate=True,
                ),
                _check(
                    "same_message_replay_once",
                    "同消息重放不重复模型调用",
                    idempotent,
                    f"模型调用={calls}",
                    hard_gate=True,
                ),
                _check(
                    "claimed_worker_revoked",
                    "领取后撤回拒绝迟到事实并使旧切片失效",
                    "我喜欢跑步" not in active
                    and not atomic.is_slice_current(ACCOUNT_A, old_slice.revocation_version),
                    f"活动事实={active}",
                    hard_gate=True,
                ),
                _check(
                    "model_outside_transaction",
                    "模型阶段不占 SQLite 写事务",
                    model_in_transaction == [False, False],
                    f"模型阶段事务状态={model_in_transaction}",
                    hard_gate=True,
                ),
            ],
            {
                "commit_latency_ms": (committed - started) * 1000,
                "model_calls": len(calls),
                "transaction_count": len(database.transaction_ms),
                "transaction_ms_total": sum(database.transaction_ms),
                "transaction_ms_max": max(database.transaction_ms, default=0),
                "transaction_measurement": (
                    "BEGIN 成功后至提交前业务持有时间；不含锁等待/COMMIT；"
                    "合成内存 SQLite，不代表磁盘 P95"
                ),
                "runs": [
                    {
                        "message_id": run.message_id,
                        "status": run.status.value,
                        "created_at": run.created_at.isoformat(),
                        "updated_at": run.updated_at.isoformat(),
                    }
                    for run in repository.list_runs()
                ],
            },
        )
    finally:
        database.close()


# ---------------------------------------------------------------------------
# 场景 10：自述与答题证据分开、行为线索只观察
# ---------------------------------------------------------------------------


def scenario_self_report_vs_answer_evidence() -> ScenarioResult:
    lab = ProfileLab(
        {
            "m-self-1": [
                _candidate(
                    "学习概率统计",
                    fact_text="我正在学习概率统计",
                    dimension=FourDimension.ACADEMIC_STATUS,
                )
            ],
            "m-self-2": [
                _candidate(
                    "数学差",
                    fact_text="我的数学很差",
                    dimension=FourDimension.ACADEMIC_STATUS,
                )
            ],
        }
    )
    lab.schedule(ACCOUNT_A, "m-self-1", "我正在学习概率统计")
    lab.drain()
    # 一次答错后没有可复用自述：不得形成全局能力标签。
    lab.schedule(ACCOUNT_A, "m-self-2", "这题我不会，昨天看了两小时数学")
    lab.drain()
    active = lab.active_texts(ACCOUNT_A)
    self_report_stored = any("我正在学习概率统计" in text for text in active)
    no_capability_label = not any(("数学差" in text) or ("数学很差" in text) for text in active)
    # 行为线索（看了两小时）不是稳定事实。
    no_behavior_label = not any("两小时" in text for text in active)
    return ScenarioResult(
        scenario_id="self_report_vs_answer_evidence",
        title="自述基础与答题证据分开，不以一次不会答形成长期标签",
        checkpoints=[
            _check(
                "self_report_stored",
                "明确自述作为起点事实保存",
                self_report_stored,
                f"活动条目：{active}",
            ),
            _check(
                "no_capability_label",
                "一次不会答不形成全局能力标签",
                no_capability_label,
                f"活动条目：{active}",
                hard_gate=True,
            ),
            _check(
                "no_behavior_label",
                "行为线索只观察不晋升为长期事实",
                no_behavior_label,
                f"活动条目：{active}",
            ),
        ],
        measurements={"active_count": len(active)},
    )


# ---------------------------------------------------------------------------
# 场景 11：使用/记录开关与账户隔离、跨会话时序
# ---------------------------------------------------------------------------


def scenario_switches_and_isolation() -> ScenarioResult:
    lab = ProfileLab()
    lab.atomic.remember(ACCOUNT_A, "我喜欢跑步", source_message_id="m-iso-1", source_at=ANCHOR)
    lab.atomic.remember(ACCOUNT_B, "我喜欢摄影", source_message_id="m-iso-2", source_at=ANCHOR)
    lab.automatic.set_account_controls(ACCOUNT_A, usage_enabled=False)
    usage_flag_off = not lab.automatic.is_profile_usage_enabled(ACCOUNT_A)
    kept_after_off = any("跑步" in text for text in lab.active_texts(ACCOUNT_A))
    isolation_a = lab.recalled_texts(ACCOUNT_A, "我平时喜欢摄影")
    isolation_b = lab.recalled_texts(ACCOUNT_B, "我平时喜欢跑步")
    account_isolated = not any("摄影" in text for text in isolation_a) and not any(
        "跑步" in text for text in isolation_b
    )
    lab.automatic.set_account_controls(ACCOUNT_A, usage_enabled=True)
    usage_back = any(
        "跑步" in text for text in lab.recalled_texts(ACCOUNT_A, "我平时喜欢跑步，推荐运动")
    )
    return ScenarioResult(
        scenario_id="switches_and_isolation",
        title="使用开关停止取用且不删数据，账户隔离互不可见",
        checkpoints=[
            _check(
                "usage_off_flag",
                "关闭使用后开关为关（调用方据此不注入）",
                usage_flag_off,
                f"usage_flag_off={usage_flag_off}",
            ),
            _check(
                "usage_off_keeps_data",
                "关闭使用不删除已记录信息",
                kept_after_off,
                f"活动条目：{lab.active_texts(ACCOUNT_A)}",
                hard_gate=True,
            ),
            _check(
                "account_isolated",
                "账户之间互不召回",
                account_isolated,
                f"A 查摄影：{isolation_a}；B 查跑步：{isolation_b}",
                hard_gate=True,
            ),
            _check(
                "usage_back_recall",
                "重新开启后可再用未删除信息",
                usage_back,
                "切片恢复",
            ),
        ],
        measurements={
            "controls_after_off": lab.automatic.account_controls(ACCOUNT_A).model_dump(mode="json"),
        },
    )


# ---------------------------------------------------------------------------
# 场景 12：页面依据能解释四类反馈路径
# ---------------------------------------------------------------------------


def scenario_evidence_feedback_path() -> ScenarioResult:
    lab = ProfileLab()
    expired = lab.atomic.remember(
        ACCOUNT_A,
        "我计划下周通过英语六级考试",
        source_message_id="m-evi-1",
        source_at=ANCHOR,
    )
    long_term = lab.atomic.remember(
        ACCOUNT_A, "我平时喜欢跑步", source_message_id="m-evi-2", source_at=ANCHOR
    )
    evidence = lab.atomic.item_evidence(
        ACCOUNT_A,
        expired.profile_item_id,
        now=(
            expired.valid_until + timedelta(seconds=1)
            if expired.valid_until is not None
            else ANCHOR + timedelta(days=14)
        ),
    )
    expired_explains = (
        evidence.validity_status == AtomicProfileValidityStatus.EXPIRED
        and evidence.evidence_quote_status is not None
    )
    expected_effects = {
        AtomicProfileFeedbackKind.FACT_WRONG.value: (
            AtomicProfileFeedbackEffect.SUGGEST_FACT_CORRECTION.value
        ),
        AtomicProfileFeedbackKind.EXPIRED.value: (
            AtomicProfileFeedbackEffect.SUGGEST_VALIDITY_REVIEW.value
        ),
        AtomicProfileFeedbackKind.SCOPE_INAPPLICABLE.value: (
            AtomicProfileFeedbackEffect.NO_FACT_CHANGE.value
        ),
        AtomicProfileFeedbackKind.PREFERENCE_NOT_FOLLOWED.value: (
            AtomicProfileFeedbackEffect.NO_FACT_CHANGE.value
        ),
    }
    feedback_effects: dict[str, str] = {}
    for kind in AtomicProfileFeedbackKind:
        projection = lab.atomic.record_feedback(
            ACCOUNT_A,
            long_term.profile_item_id,
            AtomicProfileItemFeedbackRequest(kind=kind),
        )
        feedback_effects[kind.value] = projection.effect.value
    four_paths_explained = feedback_effects == expected_effects
    # 反馈不自动删改事实。
    still_active = any("跑步" in text for text in lab.active_texts(ACCOUNT_A))
    return ScenarioResult(
        scenario_id="evidence_feedback_path",
        title="页面依据可解释过期/记错等反馈，反馈不自动删改事实",
        checkpoints=[
            _check(
                "expired_evidence_explains",
                "过期事实的依据展开给出时效状态",
                expired_explains,
                f"validity={evidence.validity_status.value}",
            ),
            _check(
                "feedback_effect_explains",
                "四类反馈路径各有确定性效果",
                four_paths_explained,
                f"effects={feedback_effects}",
            ),
            _check(
                "feedback_does_not_delete",
                "反馈不自动删除仍正确的事实",
                still_active,
                "反馈后条目仍活动",
                hard_gate=True,
            ),
        ],
        measurements={"feedback_effects": feedback_effects},
    )


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------


_SCENARIOS = (
    scenario_negated_preference,
    scenario_multi_fact_coexistence,
    scenario_mixed_subject_quote_emotion,
    scenario_anaphora,
    scenario_time_anchor,
    scenario_parallel_goals_precise_change,
    scenario_delete_synonym_and_recovery,
    scenario_low_confidence_mirror,
    scenario_stop_recording_forget_and_late_task,
    scenario_self_report_vs_answer_evidence,
    scenario_switches_and_isolation,
    scenario_evidence_feedback_path,
    scenario_sqlite_async_transactions,
)


def run_profile_quality_evaluation() -> ProfileQualityReport:
    """运行全部确定性纵向场景，返回汇总报告。"""

    scenarios = [scenario() for scenario in _SCENARIOS]
    return ProfileQualityReport(
        scenarios=scenarios,
        environment={
            "layer": "deterministic-mechanism",
            "account_model": "two synthetic accounts",
            "anchor": ANCHOR.isoformat(),
        },
    )


__all__ = [
    "ACCOUNT_A",
    "ACCOUNT_B",
    "ANCHOR",
    "Checkpoint",
    "FixedCandidateExtractor",
    "ProfileLab",
    "ProfileQualityReport",
    "ScenarioResult",
    "run_profile_quality_evaluation",
]
