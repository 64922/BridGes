"""工单 39：确定性机制验证（覆盖、三臂编译、消融、固定文案）。

全部使用确定性模型/工具响应，只证明配对/门禁/消融/覆盖机制，
不产生真实体验结论；真实配对见 `expression_real_run`。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from bridges.chat.lightweight_policy import (
    ChatLightweightPolicySnapshot,
    ToolOutcome,
)
from bridges.contracts.evaluation_suite import SuiteRunLock
from bridges.contracts.profile_adoption import AdoptedProfileSlice
from bridges.evaluation.expression_corpus import (
    FORMAL_PATHS,
    SCENARIOS,
    ExpressionScenario,
    ToolSignal,
    coverage_matrix,
    scenario_digest,
    validate_coverage,
)
from bridges.evaluation.expression_policy_arms import (
    ARM_STRATEGY_VERSIONS,
    CONCISE_BASELINE_BLOCK,
    ArmPolicyCompiler,
    StrategyArm,
)
from bridges.evaluation.expression_provenance import (
    REPO_ROOT,
    build_run_lock,
    sha256_text,
)


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
            "coverage": coverage_matrix(),
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
    ).model_copy(update={"config_digest": sha256_text(payload)})


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


__all__ = [
    "Checkpoint",
    "DeterministicReport",
    "run_deterministic_suite",
]
