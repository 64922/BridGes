"""工单 39：人味表达盲评的编排、运行锁与报告（复用现有评测底座）。

本模块不复制评测平台：策略臂复用生产 ChatService 与轻量策略编译接缝，
运行锁复用 ``contracts.evaluation_suite.SuiteRunLock``，脱敏与报告沿用
40/41 评测票的既有模式（代码提交、源码哈希、模型锁、量表版本）。

真实调用只发生在 ``RealArmSender``（脚本以 ``--real-probes`` 显式开启）；
确定性部分验证配对/盲化/门禁/消融/覆盖机制，真实体验结论来自真实模型
配对与人工盲评，二者在报告中分开陈述。
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bridges.chat.lightweight_policy import ChatLightweightPolicySnapshot, ToolOutcome
from bridges.contracts.ai import ModelRunLock
from bridges.contracts.evaluation_suite import SuiteRunLock, now_iso
from bridges.contracts.profile_adoption import AdoptedProfileSlice
from bridges.evaluation.expression_corpus import (
    FORMAL_PATHS,
    SCENARIOS,
    ExpressionScenario,
    ToolSignal,
    validate_coverage,
)
from bridges.evaluation.expression_gates import GateResult, evaluate_hard_gates
from bridges.evaluation.expression_policy_arms import (
    ARM_STRATEGY_VERSIONS,
    ARM_TITLES,
    CONCISE_BASELINE_BLOCK,
    ArmPolicyCompiler,
    StrategyArm,
)
from bridges.evaluation.expression_review import (
    REVIEW_DIMENSIONS,
    SCALE_VERSION,
    ScenarioTranscript,
    TranscriptTurn,
)

#: 评测套件身份（进入运行锁）。
SUITE_ID = "human-expression-blind-evaluation"
SUITE_VERSION = "1.0.0"
#: 确定性门禁与检查点版本。
GATE_VERSION = "human-expression-gates-v1"
REPO_ROOT = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# 运行锁与摘要
# ---------------------------------------------------------------------------


def _git_commit() -> str | None:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() or None


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def scenario_digest() -> str:
    payload = [
        {
            "scenario_id": scenario.scenario_id,
            "category": scenario.category.value,
            "formal_path": scenario.formal_path,
            "turns": list(scenario.turns),
            "profile_facts": list(scenario.profile_facts),
            "tool_outcome": scenario.tool_outcome.value,
            "protected_facts": list(scenario.protected_facts),
            "boundary": scenario.boundary,
            "required_any": list(scenario.required_any),
            "forbidden_any": list(scenario.forbidden_any),
            "detail_required": scenario.detail_required,
            "expects_continuation": scenario.expects_continuation,
            "real_runnable": scenario.real_runnable,
        }
        for scenario in SCENARIOS
    ]
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_run_lock(
    *,
    lock_id: str,
    dataset_versions: dict[str, str],
    prompt_versions: dict[str, str],
    model_run_locks: list[ModelRunLock] | None = None,
    random_seeds: list[int] | None = None,
    execution_count: int = 1,
    suite_digest_value: str,
    network_cache_policy: str,
) -> SuiteRunLock:
    """构造复用合同的不可变运行锁（真实运行附带观察到的模型锁）。"""

    from bridges.storage.database import SCHEMA_VERSION

    return SuiteRunLock(
        lock_id=lock_id,
        suite_id=SUITE_ID,
        suite_version=SUITE_VERSION,
        suite_digest=suite_digest_value,
        code_commit_or_build_digest=_git_commit() or "unknown",
        runtime_identifier="conda-agent-windows-eval",
        os_hardware_summary=(
            f"{platform.system()} {platform.release()} {platform.machine()} / "
            f"Python {platform.python_version()}"
        ),
        database_migration_version=str(SCHEMA_VERSION),
        config_digest=_sha256_text(
            json.dumps(
                {
                    "arms": {arm.value: version for arm, version in ARM_STRATEGY_VERSIONS.items()},
                    "scales": SCALE_VERSION,
                    "gates": GATE_VERSION,
                    "seeds": random_seeds or [],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        ),
        dataset_versions=dict(dataset_versions),
        model_run_locks=list(model_run_locks or []),
        prompt_versions=dict(prompt_versions),
        schema_versions={"expression-task-contract": "expression-task-v1"},
        tool_adapter_versions={"real_qwen_gateway": "production-composition"},
        judge_versions={"deterministic-gates": GATE_VERSION},
        scoring_scale_versions={
            dimension.dimension_id: SCALE_VERSION for dimension in REVIEW_DIMENSIONS
        },
        random_seeds=list(random_seeds or [39]),
        execution_count=execution_count,
        network_cache_policy=network_cache_policy,
        created_at=now_iso(),
    )


# ---------------------------------------------------------------------------
# 确定性机制验证：覆盖、配对、消融、固定文案
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Checkpoint:
    checkpoint_id: str
    title: str
    passed: bool
    detail: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "title": self.title,
            "passed": self.passed,
            "detail": self.detail,
        }


@dataclass
class DeterministicReport:
    passed: bool
    checkpoints: list[Checkpoint]
    ablations: dict[str, list[Checkpoint]]
    corpus_digest: str
    coverage_problems: list[str]
    formal_paths: list[dict[str, object]]
    locks: dict[str, str] = field(default_factory=dict)

    def counts(self) -> tuple[int, int]:
        total = len(self.checkpoints)
        passed = sum(1 for checkpoint in self.checkpoints if checkpoint.passed)
        return passed, total

    def to_dict(self) -> dict[str, Any]:
        passed, total = self.counts()
        return {
            "passed": self.passed,
            "checkpoint_count": total,
            "checkpoint_passed": passed,
            "checkpoints": [checkpoint.to_dict() for checkpoint in self.checkpoints],
            "ablations": {
                key: [checkpoint.to_dict() for checkpoint in value]
                for key, value in self.ablations.items()
            },
            "corpus_digest": self.corpus_digest,
            "coverage_problems": list(self.coverage_problems),
            "formal_paths": self.formal_paths,
            "locks": self.locks,
        }


def _compile_arm(
    arm: StrategyArm,
    scenario: ExpressionScenario,
    turn_index: int,
    *,
    adopted_slice: Any = None,
    continuation_text: str = "",
    tool_outcome: ToolOutcome | None = None,
) -> ChatLightweightPolicySnapshot:
    compiler = ArmPolicyCompiler(arm)
    signal = tool_outcome
    if signal is None:
        signal = {
            ToolSignal.NONE: ToolOutcome.NONE,
            ToolSignal.SUCCESS: ToolOutcome.SUCCESS,
            ToolSignal.PARTIAL: ToolOutcome.PARTIAL,
            ToolSignal.ERROR: ToolOutcome.ERROR,
        }[scenario.tool_outcome]
    return compiler.compile(
        scenario.mode,
        user_text=scenario.turns[turn_index],
        adopted_slice=adopted_slice,
        continuation_text=continuation_text,
        tool_outcome=signal,
        lesson=scenario.mode == "study",
    )


def _brevity_adopted_slice() -> AdoptedProfileSlice:
    """真实原子服务编译「喜欢简短直接」采用切片（内存仓库）。"""

    from bridges.profiles.adapters import InMemoryProfileRepository
    from bridges.profiles.atomic import (
        AtomicProfileService,
        InMemoryAtomicProfileRepository,
    )
    from bridges.profiles.four_dimensions import (
        FourDimensionProfileService,
        InMemoryFourDimensionProfileRepository,
    )
    from bridges.profiles.purpose import build_purpose

    four = FourDimensionProfileService(
        InMemoryProfileRepository(), InMemoryFourDimensionProfileRepository()
    )
    atomic = AtomicProfileService(four, InMemoryAtomicProfileRepository())
    atomic.remember("eval39-ablation", "回答喜欢简短直接", source_message_id="ablation")
    return atomic.compile_adopted_slice(
        "eval39-ablation",
        run_id="eval39-ablation",
        purpose=build_purpose(mode="companion", query="解释这个概念"),
    )


def _ablations() -> tuple[dict[str, list[Checkpoint]], dict[str, str]]:
    """三项消融：使用前文、明确偏好、条件承接；各自记录运行锁。"""

    ablations: dict[str, list[Checkpoint]] = {}
    locks: dict[str, str] = {}
    checks: list[Checkpoint] = []

    with_context = _compile_arm(
        StrategyArm.CURRENT,
        _probe_scenario("continuation-long-story"),
        1,
        continuation_text="写一篇两千字的故事。",
    )
    without_context = _compile_arm(
        StrategyArm.CURRENT,
        _probe_scenario("continuation-long-story"),
        1,
        continuation_text="",
    )
    checks.extend(
        [
            Checkpoint(
                "prior-context-detail-retained",
                "使用前文时保留长任务额度与详细约束",
                "detail_requested" in with_context.constraints
                and with_context.output_tokens > without_context.output_tokens,
                f"constraints={with_context.constraints}, tokens={with_context.output_tokens}",
            ),
            Checkpoint(
                "prior-context-ablated",
                "去掉前文后不再推断长任务",
                "detail_requested" not in without_context.constraints,
                f"constraints={without_context.constraints}",
            ),
        ]
    )
    ablations["prior-context"] = checks
    locks["prior-context"] = _ablation_lock("prior-context", checks).digest()

    adopted = _brevity_adopted_slice()
    preference_checks: list[Checkpoint] = []
    with_profile = _compile_arm(
        StrategyArm.CURRENT, _probe_scenario("pref-brief"), 0, adopted_slice=adopted
    )
    without_profile = _compile_arm(StrategyArm.CURRENT, _probe_scenario("pref-brief"), 0)
    legacy_with_profile = _compile_arm(
        StrategyArm.LEGACY, _probe_scenario("pref-brief"), 0, adopted_slice=adopted
    )
    preference_checks.extend(
        [
            Checkpoint(
                "preference-rules-present",
                "明确偏好进入现行策略（adopted-* 规则）",
                any(rule.startswith("adopted-") for rule in with_profile.rule_ids),
                f"rule_ids={with_profile.rule_ids}",
            ),
            Checkpoint(
                "preference-rules-ablated",
                "去掉采用切片后不再注入偏好规则",
                not any(rule.startswith("adopted-") for rule in without_profile.rule_ids),
                f"rule_ids={without_profile.rule_ids}",
            ),
            Checkpoint(
                "legacy-white-list-drops-atomic",
                "历史 v2 白名单丢弃无类别原子条目（历史事实对照）",
                not any(rule.startswith("adopted-") for rule in legacy_with_profile.rule_ids)
                and not legacy_with_profile.profile_items,
                f"rule_ids={legacy_with_profile.rule_ids}, "
                f"profile={legacy_with_profile.profile_items}",
            ),
        ]
    )
    ablations["explicit-preferences"] = preference_checks
    locks["explicit-preferences"] = _ablation_lock(
        "explicit-preferences", preference_checks
    ).digest()

    partial_checks: list[Checkpoint] = []
    partial = _compile_arm(
        StrategyArm.CURRENT,
        _probe_scenario("tool-search-partial"),
        1,
        continuation_text="帮我找一下这三个问题的资料。",
        tool_outcome=ToolOutcome.PARTIAL,
    )
    no_partial = _compile_arm(
        StrategyArm.CURRENT,
        _probe_scenario("tool-search-partial"),
        1,
        continuation_text="帮我找一下这三个问题的资料。",
        tool_outcome=ToolOutcome.NONE,
    )
    legacy_partial = _compile_arm(
        StrategyArm.LEGACY,
        _probe_scenario("tool-search-partial"),
        1,
        tool_outcome=ToolOutcome.PARTIAL,
    )
    partial_checks.extend(
        [
            Checkpoint(
                "partial-acknowledgement-present",
                "部分结果触发如实说明约束",
                "partial_results" in partial.constraints
                and "partial-results-stated" in partial.rule_ids,
                f"constraints={partial.constraints}",
            ),
            Checkpoint(
                "partial-acknowledgement-ablated",
                "去掉部分结果信号后不再要求如实说明",
                "partial_results" not in no_partial.constraints
                and "partial-results-stated" not in no_partial.rule_ids,
                f"constraints={no_partial.constraints}",
            ),
            Checkpoint(
                "legacy-no-partial-signal",
                "历史 v2 无部分结果条件承接信号（历史事实对照）",
                "partial_results" not in legacy_partial.constraints,
                f"constraints={legacy_partial.constraints}",
            ),
        ]
    )
    ablations["conditional-acknowledgement"] = partial_checks
    locks["conditional-acknowledgement"] = _ablation_lock(
        "conditional-acknowledgement", partial_checks
    ).digest()
    return ablations, locks


def _probe_scenario(scenario_id: str) -> ExpressionScenario:
    for scenario in SCENARIOS:
        if scenario.scenario_id == scenario_id:
            return scenario
    raise KeyError(scenario_id)


def _ablation_lock(ablation_id: str, checkpoints: list[Checkpoint]) -> SuiteRunLock:
    payload = json.dumps(
        [checkpoint.to_dict() for checkpoint in checkpoints], ensure_ascii=False
    )
    return build_run_lock(
        lock_id=f"ablation-{ablation_id}",
        dataset_versions={"expression-scenarios": scenario_digest()[:16]},
        prompt_versions={
            arm.value: version for arm, version in ARM_STRATEGY_VERSIONS.items()
        },
        random_seeds=[39],
        suite_digest_value=scenario_digest(),
        network_cache_policy="none-deterministic",
    ).model_copy(update={"config_digest": _sha256_text(payload)})


_FIXED_COPY_PATHS: dict[str, tuple[str, ...]] = {
    "fixed-error-web": ("web_search.degradation.provider_unready",),
    "fixed-empty-retrieval": ("retrieval.empty.no_hits",),
    "fixed-clarification-route": ("chat.clarification.task_ambiguity",),
    "fixed-progress-stop": ("chat.stop.user_stopped", "chat.progress.thinking_stopped"),
    "fixed-partial-result": ("retrieval.partial.conflict", "chat.result.outcome.partial"),
}


def run_deterministic_suite() -> DeterministicReport:
    """确定性验证覆盖矩阵、策略臂、三项消融与固定文案路径。"""

    checkpoints: list[Checkpoint] = []
    coverage_problems = validate_coverage()
    checkpoints.append(
        Checkpoint(
            "coverage-matrix",
            "原创场景矩阵覆盖全部类别与正式路径",
            not coverage_problems,
            "；".join(coverage_problems) or f"场景 {len(SCENARIOS)} 组",
        )
    )

    arm_checkpoints: list[Checkpoint] = []
    for scenario in SCENARIOS:
        for turn_index, _turn in enumerate(scenario.turns):
            continuation = scenario.turns[turn_index - 1] if turn_index else ""
            snapshots = {
                arm: _compile_arm(
                    arm, scenario, turn_index, continuation_text=continuation
                )
                for arm in StrategyArm
            }
            ok = all(
                snapshots[arm].version == ARM_STRATEGY_VERSIONS[arm]
                for arm in StrategyArm
            )
            if not ok:
                arm_checkpoints.append(
                    Checkpoint(
                        f"arm-version-{scenario.scenario_id}-{turn_index}",
                        "策略臂版本正确",
                        False,
                        f"{ {arm: snapshots[arm].version for arm in StrategyArm} }",
                    )
                )
            budgets = {snapshots[arm].output_tokens for arm in StrategyArm}
            if len(budgets) != 1:
                arm_checkpoints.append(
                    Checkpoint(
                        f"arm-budget-{scenario.scenario_id}-{turn_index}",
                        "三臂输出额度可比",
                        False,
                        f"budgets={budgets}",
                    )
                )
            if snapshots[StrategyArm.BASELINE].system_block != CONCISE_BASELINE_BLOCK:
                arm_checkpoints.append(
                    Checkpoint(
                        f"baseline-block-{scenario.scenario_id}",
                        "简洁基线只含事实/任务合同",
                        False,
                        "基线系统块被改变",
                    )
                )
            if scenario.boundary == "no_advice" and turn_index == 0:
                with_hint = _compile_arm(StrategyArm.CURRENT, scenario, turn_index)
                legacy = _compile_arm(StrategyArm.LEGACY, scenario, turn_index)
                if "no_advice" not in with_hint.constraints:
                    arm_checkpoints.append(
                        Checkpoint(
                            f"boundary-{scenario.scenario_id}",
                            "现行策略识别明确边界",
                            False,
                            f"constraints={with_hint.constraints}",
                        )
                    )
                if "no_advice" in legacy.constraints:
                    arm_checkpoints.append(
                        Checkpoint(
                            f"legacy-boundary-{scenario.scenario_id}",
                            "历史 v2 不识别明确边界（历史对照）",
                            False,
                            "v2 意外识别了 no_advice",
                        )
                    )
    checkpoints.extend(arm_checkpoints)
    checkpoints.append(
        Checkpoint(
            "arm-compile-sweep",
            "全部场景三臂编译版本/额度一致",
            not arm_checkpoints,
            f"检查 {sum(len(s.turns) for s in SCENARIOS)} 个回合",
        )
    )

    formal_path_entries: list[dict[str, object]] = []
    for path in FORMAL_PATHS:
        evidence_file = path.evidence.split("::", 1)[0]
        evidence_exists = (REPO_ROOT / evidence_file).exists()
        scenario_count = sum(1 for s in SCENARIOS if s.formal_path == path.path_id)
        formal_path_entries.append(
            {
                **path.__dict__,
                "scenario_count": scenario_count,
                "evidence_exists": evidence_exists,
            }
        )
        checkpoints.append(
            Checkpoint(
                f"formal-path-{path.path_id}",
                f"正式路径有场景与接线证据：{path.title}",
                scenario_count > 0 and evidence_exists,
                f"scenarios={scenario_count}, evidence={path.evidence}",
            )
        )

    fixed_copy_checks: list[Checkpoint] = []
    from bridges.state_copy.registry import state_copy_entry

    for scenario_id, paths in _FIXED_COPY_PATHS.items():
        for state_path in paths:
            try:
                entry = state_copy_entry(state_path)
                passed = bool(entry.text) and bool(entry.states)
                detail = f"{state_path}: states={list(entry.states)}"
            except Exception as exc:  # noqa: BLE001 - 未登记即覆盖失败
                passed = False
                detail = f"{state_path}: {exc}"
            fixed_copy_checks.append(
                Checkpoint(
                    f"fixed-copy-{scenario_id}-{state_path}",
                    "固定文案路径已登记真实状态",
                    passed,
                    detail,
                )
            )
    checkpoints.extend(fixed_copy_checks)

    ablations, locks = _ablations()
    for ablation_id, ablation_checks in ablations.items():
        checkpoints.extend(
            Checkpoint(
                f"ablation-{ablation_id}-{checkpoint.checkpoint_id}",
                checkpoint.title,
                checkpoint.passed,
                checkpoint.detail,
            )
            for checkpoint in ablation_checks
        )
        if ablation_checks and not all(check.passed for check in ablation_checks):
            checkpoints.append(
                Checkpoint(
                    f"ablation-{ablation_id}",
                    f"消融 {ablation_id} 通过",
                    False,
                    "存在失败检查点",
                )
            )
    return DeterministicReport(
        passed=all(checkpoint.passed for checkpoint in checkpoints),
        checkpoints=checkpoints,
        ablations=ablations,
        corpus_digest=scenario_digest(),
        coverage_problems=coverage_problems,
        formal_paths=formal_path_entries,
        locks=locks,
    )


# ---------------------------------------------------------------------------
# 真实模型配对
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TurnMeasurement:
    arm: str
    scenario_id: str
    turn_index: int
    status: str
    answer_chars: int
    latency_ms: int
    first_token_ms: int | None
    input_tokens: int | None
    output_tokens: int | None
    retry_count: int
    capability_names: tuple[str, ...]
    model_id: str | None
    error_code: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "scenario_id": self.scenario_id,
            "turn_index": self.turn_index,
            "status": self.status,
            "answer_chars": self.answer_chars,
            "latency_ms": self.latency_ms,
            "first_token_ms": self.first_token_ms,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "retry_count": self.retry_count,
            "capability_names": list(self.capability_names),
            "model_id": self.model_id,
            "error_code": self.error_code,
        }

    @property
    def chat_calls(self) -> int:
        return sum(1 for name in self.capability_names if name == "qwen_text_chat")

    @property
    def total_calls(self) -> int:
        return len(self.capability_names)


@dataclass
class ArmRunResult:
    scenario: ExpressionScenario
    arm: StrategyArm
    transcript: ScenarioTranscript
    measurements: list[TurnMeasurement]
    locks: list[ModelRunLock]
    gates: list[GateResult]

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario.scenario_id,
            "arm": self.arm.value,
            "turns": [
                {
                    "user": turn.user,
                    "assistant": turn.assistant,
                    "status": turn.status,
                    "error_code": turn.error_code,
                }
                for turn in self.transcript.turns
            ],
            "measurements": [item.to_dict() for item in self.measurements],
            "gates": [gate.to_dict() for gate in self.gates],
        }


class RealArmSender:
    """真实模型配对发送器：每场景每臂独立会话，多轮保留前文。"""

    def __init__(self, gateway: Any, *, account_id: str = "eval39-account") -> None:
        self._gateway = gateway
        self._account_id = account_id

    def run(self, scenario: ExpressionScenario, arm: StrategyArm) -> ArmRunResult:
        from datetime import UTC as _UTC
        from datetime import datetime as _datetime

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
        now = _datetime.now(_UTC)
        for index, fact in enumerate(scenario.profile_facts):
            atomic.remember(
                self._account_id,
                fact,
                source_message_id=f"{scenario.scenario_id}-profile-{index}",
                source_at=now,
            )
        service = ChatService(
            repository=conversations,
            gateway=self._gateway,
            four_dimension_profile_service=four,
            atomic_profile_service=atomic,
            automatic_profile_service=automatic,
            writing_policy_compiler=ArmPolicyCompiler(arm),  # type: ignore[arg-type]
        )
        mode = ChatMode.STUDY if scenario.mode == "study" else ChatMode.COMPANION
        conversation = service.create_conversation(self._account_id, mode=mode)

        turns: list[TranscriptTurn] = []
        measurements: list[TurnMeasurement] = []
        observed_locks: list[ModelRunLock] = []
        gates: list[GateResult] = []
        for turn_index, user_text in enumerate(scenario.turns, start=1):
            started = time.monotonic()
            user, assistant, _ = service.start_generation(
                self._account_id, conversation.conversation_id, user_text
            )
            context = RunContextEnvelope(
                run_id=f"eval39-{scenario.scenario_id}-{arm.value}-{turn_index}",
                account_id=self._account_id,
                project_id=conversation.conversation_id,
                workflow_name="chat",
                workflow_version="1",
                object_domain=ObjectDomain.PERSONAL_VAULT,
                submitted_at=_datetime.now(_UTC),
            )
            first_token_ms: int | None = None
            error_code: str | None = None
            turn_locks: list[ModelRunLock] = []
            for event in service.stream_generation(
                self._account_id,
                conversation.conversation_id,
                assistant.message_id,
                context,
                until_user_message_id=user.message_id,
            ):
                kind = getattr(event, "kind", None)
                if kind == "error":
                    error_code = getattr(event, "error_code", None)
                if kind == "delta" and first_token_ms is None:
                    first_token_ms = int((time.monotonic() - started) * 1000)
                stage_first = getattr(event, "first_token_ms", None)
                if first_token_ms is None and isinstance(stage_first, int):
                    first_token_ms = stage_first
                lock = getattr(event, "lock", None)
                if lock is not None:
                    turn_locks.append(lock)
            latency_ms = int((time.monotonic() - started) * 1000)
            final = service.message_projection(self._account_id, assistant.message_id)
            answer = final.content if final is not None else ""
            status = final.status.value if final is not None else "error"
            if final is not None and final.error_code:
                error_code = final.error_code
            observed_locks.extend(turn_locks)
            usage_input = sum(
                int((lock.usage or {}).get("prompt_tokens") or 0) for lock in turn_locks
            )
            usage_output = sum(
                int((lock.usage or {}).get("completion_tokens") or 0) for lock in turn_locks
            )
            measurements.append(
                TurnMeasurement(
                    arm=arm.value,
                    scenario_id=scenario.scenario_id,
                    turn_index=turn_index,
                    status=status,
                    answer_chars=len(answer),
                    latency_ms=latency_ms,
                    first_token_ms=first_token_ms,
                    input_tokens=usage_input if turn_locks else None,
                    output_tokens=usage_output if turn_locks else None,
                    retry_count=sum(int(lock.retry_count or 0) for lock in turn_locks),
                    capability_names=tuple(lock.capability_name for lock in turn_locks),
                    model_id=(
                        turn_locks[0].actual_model_id if turn_locks else None
                    ),
                    error_code=error_code,
                )
            )
            turns.append(
                TranscriptTurn(
                    user=user_text,
                    assistant=answer,
                    status=status,
                    error_code=error_code,
                    tool_outcome=scenario.tool_outcome.value,
                )
            )
            gates.extend(
                evaluate_hard_gates(
                    scenario,
                    turn_index=turn_index,
                    answer=answer,
                    status=status,
                )
            )
        transcript = ScenarioTranscript(
            scenario_id=scenario.scenario_id,
            title=scenario.title,
            category=scenario.category.value,
            formal_path=scenario.formal_path,
            arm_id=arm.value,
            turns=tuple(turns),
        )
        return ArmRunResult(
            scenario=scenario,
            arm=arm,
            transcript=transcript,
            measurements=measurements,
            locks=observed_locks,
            gates=gates,
        )


def summarize_costs(results: list[ArmRunResult]) -> dict[str, Any]:
    """按臂汇总真实测量；人味专属新增调用必须为零。"""

    by_arm: dict[str, dict[str, Any]] = {}
    measurements_by_key: dict[tuple[str, str, int], TurnMeasurement] = {}
    for result in results:
        arm = result.arm.value
        summary = by_arm.setdefault(
            arm,
            {
                "turns": 0,
                "calls": 0,
                "chat_calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "latency_ms": [],
                "first_token_ms": [],
                "answer_chars": 0,
                "retries": 0,
                "errors": 0,
            },
        )
        for measurement in result.measurements:
            summary["turns"] += 1
            summary["calls"] += measurement.total_calls
            summary["chat_calls"] += measurement.chat_calls
            summary["input_tokens"] += measurement.input_tokens or 0
            summary["output_tokens"] += measurement.output_tokens or 0
            summary["latency_ms"].append(measurement.latency_ms)
            if measurement.first_token_ms is not None:
                summary["first_token_ms"].append(measurement.first_token_ms)
            summary["answer_chars"] += measurement.answer_chars
            summary["retries"] += measurement.retry_count
            if measurement.status != "done":
                summary["errors"] += 1
            measurements_by_key[
                (measurement.scenario_id, arm, measurement.turn_index)
            ] = measurement
    for summary in by_arm.values():
        for key in ("latency_ms", "first_token_ms"):
            values = sorted(summary[key])
            summary[f"{key}_mean"] = round(sum(values) / len(values)) if values else None
            summary[f"{key}_p50"] = values[len(values) // 2] if values else None
            summary[key] = len(values)
    extra_calls: list[dict[str, Any]] = []
    baseline_keys = {
        key for key in measurements_by_key if key[1] == StrategyArm.BASELINE.value
    }
    for scenario_id, arm, turn_index in sorted(baseline_keys):
        baseline = measurements_by_key[(scenario_id, arm, turn_index)]
        for other_arm in (StrategyArm.CURRENT.value, StrategyArm.LEGACY.value):
            other = measurements_by_key.get((scenario_id, other_arm, turn_index))
            if other is None:
                continue
            delta = other.total_calls - baseline.total_calls
            if delta != 0:
                extra_calls.append(
                    {
                        "scenario_id": scenario_id,
                        "arm": other_arm,
                        "turn_index": turn_index,
                        "extra_calls": delta,
                    }
                )
    return {
        "arms": by_arm,
        "humanization_specific_calls_zero": not extra_calls,
        "extra_calls": extra_calls,
    }


def build_real_report(
    *,
    deterministic: DeterministicReport,
    results: list[ArmRunResult],
    environment: dict[str, Any],
    review_items: list[Any],
    review_mapping: dict[str, dict[str, str]],
    aggregate: dict[str, Any],
    release: dict[str, Any],
) -> dict[str, Any]:
    """汇总真实配对、硬门、盲评与放行结论（脱敏，不含密钥）。"""

    hard_gate_failures: list[dict[str, Any]] = []
    hard_gate_totals: dict[str, dict[str, int]] = {}
    for result in results:
        for gate in result.gates:
            key = gate.gate.value
            totals = hard_gate_totals.setdefault(
                key, {"passed": 0, "failed": 0}
            )
            if gate.passed:
                totals["passed"] += 1
            else:
                totals["failed"] += 1
                hard_gate_failures.append(
                    {
                        "arm": result.arm.value,
                        "scenario_id": result.scenario.scenario_id,
                        "turn_index": gate.turn_index,
                        "gate": key,
                        "detail": gate.detail,
                    }
                )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "environment": environment,
        "suite": {
            "suite_id": SUITE_ID,
            "suite_version": SUITE_VERSION,
            "corpus_digest": deterministic.corpus_digest,
        },
        "deterministic": deterministic.to_dict(),
        "arm_truths": {
            arm.value: ARM_TITLES[arm] for arm in StrategyArm
        },
        "runs": [result.to_dict() for result in results],
        "cost": summarize_costs(results),
        "hard_gates": {
            "totals": hard_gate_totals,
            "failures": hard_gate_failures,
            "separate_from_warmth": True,
        },
        "blind_review": {
            "item_count": len(review_items),
            "items": [item.to_dict() for item in review_items],
            "mapping": dict(review_mapping),
            "reviewer_count": aggregate.get("reviewer_count", 0),
            "submission_count": aggregate.get("submission_count", 0),
            "dimensions": aggregate.get("dimensions", []),
        },
        "release": release,
        "rollback": {
            "expression_strategy": (
                "表达策略按运行快照持久化，可回滚到安全基线或旧快照；"
                "只影响之后创建的运行。"
            ),
            "deterministic_fixes_locked": (
                "工单 05/06 的事实保护与流式追加协议属于确定性缺陷修复，"
                "不在提示策略回滚范围内。"
            ),
        },
    }
