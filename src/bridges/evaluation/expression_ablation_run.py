"""工单 39：三项真实消融执行（策略信号对照，真实模型回答与运行锁）。

消融在真实 ChatService 管线上运行候选臂，只在策略接缝上抑制或注入单项
信号；模型回答、延迟、用量与运行锁全部来自真实调用。工具部分结果信号
由评测侧在接缝上注入（评测发送器不执行检索工具），报告如实标注。
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from bridges.chat.lightweight_policy import ToolOutcome
from bridges.contracts.chat import ChatMode
from bridges.evaluation.expression_policy_arms import ArmPolicyCompiler, StrategyArm
from bridges.evaluation.expression_provenance import (
    SUITE_ID,
    SUITE_VERSION,
    code_commit,
    sha256_text,
)
from bridges.evaluation.expression_real_run import ArmRunResult, RealArmSender
from bridges.evaluation.expression_scale import SCALE_VERSION
from bridges.evaluation.expression_spec import ExpressionCategory, ExpressionScenario


class AblationId(StrEnum):
    """三项预注册消融。"""

    PRIOR_CONTEXT = "prior-context"
    EXPLICIT_PREFERENCES = "explicit-preferences"
    CONDITIONAL_ACKNOWLEDGEMENT = "conditional-acknowledgement"


class SignalCondition(StrEnum):
    """单项信号的对照条件：注入/存在 vs 抑制/缺失。"""

    PRESENT = "signal-present"
    ABSENT = "signal-absent"


ABLATION_TITLES: dict[AblationId, str] = {
    AblationId.PRIOR_CONTEXT: "使用前文（续接信号）",
    AblationId.EXPLICIT_PREFERENCES: "明确偏好（采用切片）",
    AblationId.CONDITIONAL_ACKNOWLEDGEMENT: "条件承接（部分结果如实说明）",
}

ABLATION_DESCRIPTIONS: dict[AblationId, str] = {
    AblationId.PRIOR_CONTEXT: (
        "候选臂在同一连续会话中，对照抑制传给策略编译器的续接文本；"
        "观察长任务/续接约束是否仍被识别并影响真实回答。"
    ),
    AblationId.EXPLICIT_PREFERENCES: (
        "候选臂在存在明确偏好画像事实时，对照不注入采用切片；"
        "观察 adopted-* 偏好规则是否进入系统块并影响真实回答。"
    ),
    AblationId.CONDITIONAL_ACKNOWLEDGEMENT: (
        "候选臂在部分结果信号下，对照移除该信号；评测发送器不执行检索"
        "工具，信号在策略接缝上注入，真实模型回答与运行锁仍然可查。"
    ),
}

#: 前文消融探针：第二轮继承第一轮的长任务请求；抑制续接后应不再识别。
#: 请求带长度上限，保证两轮都在输出额度内真实完成（避免以截断充当回答）。
_PRIOR_CONTEXT_PROBE = ExpressionScenario(
    scenario_id="ablation-prior-context",
    title="消融探针：短故事续写",
    category=ExpressionCategory.LONG_TASK,
    formal_path="chat.companion",
    turns=(
        "完整写一个雨天搬家的短故事，三百字以内。",
        "继续，把后半段写完。",
    ),
    detail_required=True,
    real_runnable=False,
    notes="仅用于真实消融对照，不进入主配对语料。",
)


class AblationPolicyCompiler:
    """把单项消融信号叠加在候选臂编译器上，并记录每次真实编译的快照。"""

    def __init__(
        self,
        arm: StrategyArm,
        ablation: AblationId,
        condition: SignalCondition,
        *,
        delegate: Any = None,
    ) -> None:
        self.arm = arm
        self.ablation = ablation
        self.condition = condition
        self._inner = ArmPolicyCompiler(arm, delegate=delegate)
        self.compiled_snapshots: list[dict[str, Any]] = []

    @property
    def strategy_version(self) -> str:
        return self._inner.strategy_version

    def seed(self, mode: ChatMode | str) -> Any:
        return self._inner.seed(mode)

    def compile(self, mode: ChatMode | str, **kwargs: Any) -> Any:
        if (
            self.ablation is AblationId.PRIOR_CONTEXT
            and self.condition is SignalCondition.ABSENT
        ):
            kwargs["continuation_text"] = ""
        elif (
            self.ablation is AblationId.EXPLICIT_PREFERENCES
            and self.condition is SignalCondition.ABSENT
        ):
            kwargs["adopted_slice"] = None
            kwargs["profile_slice_id"] = None
            kwargs["profile_items"] = ()
            kwargs["profile_context"] = None
            kwargs["profile_failed"] = False
        elif self.ablation is AblationId.CONDITIONAL_ACKNOWLEDGEMENT:
            if self.condition is SignalCondition.PRESENT:
                kwargs["tool_outcome"] = ToolOutcome.PARTIAL
                kwargs["tool_result"] = True
                kwargs["tool_error"] = False
            else:
                kwargs["tool_outcome"] = ToolOutcome.NONE
                kwargs["tool_result"] = False
                kwargs["tool_error"] = False
        snapshot = self._inner.compile(mode, **kwargs)
        self.compiled_snapshots.append(
            {
                "version": snapshot.version,
                "rule_ids": list(snapshot.rule_ids),
                "constraints": list(snapshot.constraints),
                "output_tokens": snapshot.output_tokens,
                "system_block_digest": sha256_text(snapshot.system_block),
            }
        )
        return snapshot


def ablation_probe(ablation: AblationId) -> ExpressionScenario:
    """每个消融的代表探针场景。"""

    from bridges.evaluation.expression_corpus import SCENARIOS

    probe_ids = {
        AblationId.PRIOR_CONTEXT: _PRIOR_CONTEXT_PROBE.scenario_id,
        AblationId.EXPLICIT_PREFERENCES: "pref-brief",
        AblationId.CONDITIONAL_ACKNOWLEDGEMENT: "tool-search-partial",
    }
    scenario_id = probe_ids[ablation]
    for scenario in SCENARIOS:
        if scenario.scenario_id == scenario_id:
            return scenario
    if ablation is AblationId.PRIOR_CONTEXT:
        return _PRIOR_CONTEXT_PROBE
    raise KeyError(scenario_id)


def signal_effect(
    ablation: AblationId, present: list[dict[str, Any]], absent: list[dict[str, Any]]
) -> tuple[bool, str]:
    """比对两侧真实编译快照，确认目标信号确实在场/缺席。

    多轮探针只看最后一次编译（续接信号的落点轮）：第一轮的约束可能来自
    当前请求本身，按全集比较会掩盖对照差异。
    """

    def constraints(snapshots: list[dict[str, Any]]) -> set[str]:
        if not snapshots:
            return set()
        return set(snapshots[-1].get("constraints", []))

    def rules(snapshots: list[dict[str, Any]]) -> set[str]:
        if not snapshots:
            return set()
        return set(snapshots[-1].get("rule_ids", []))

    present_constraints, absent_constraints = constraints(present), constraints(absent)
    present_rules, absent_rules = rules(present), rules(absent)
    if ablation is AblationId.PRIOR_CONTEXT:
        target = "detail_requested"
        effective = target in present_constraints and target not in absent_constraints
        detail = (
            f"present={sorted(present_constraints)}, absent={sorted(absent_constraints)}"
        )
        return effective, detail
    if ablation is AblationId.EXPLICIT_PREFERENCES:
        present_adopted = {rule for rule in present_rules if rule.startswith("adopted-")}
        absent_adopted = {rule for rule in absent_rules if rule.startswith("adopted-")}
        effective = bool(present_adopted) and not absent_adopted
        detail = (
            f"present_adopted={sorted(present_adopted)}, "
            f"absent_adopted={sorted(absent_adopted)}"
        )
        return effective, detail
    target = "partial_results"
    effective = target in present_constraints and target not in absent_constraints
    detail = (
        f"present={sorted(present_constraints)}, absent={sorted(absent_constraints)}"
    )
    return effective, detail


def run_real_ablations(
    gateway: Any,
    *,
    ablations: tuple[AblationId, ...] = tuple(AblationId),
) -> dict[str, Any]:
    """真实执行三项消融：每项两个条件各一轮候选臂会话。"""

    entries: list[dict[str, Any]] = []
    all_locks: list[dict[str, Any]] = []
    for ablation in ablations:
        scenario = ablation_probe(ablation)
        conditions: list[dict[str, Any]] = []
        snapshot_by_condition: dict[SignalCondition, list[dict[str, Any]]] = {}
        for condition in (SignalCondition.PRESENT, SignalCondition.ABSENT):
            compiler_holder: dict[str, AblationPolicyCompiler] = {}

            def factory(
                arm: StrategyArm,
                *,
                _ablation: AblationId = ablation,
                _condition: SignalCondition = condition,
                _holder: dict[str, AblationPolicyCompiler] = compiler_holder,
            ) -> AblationPolicyCompiler:
                compiler = AblationPolicyCompiler(arm, _ablation, _condition)
                _holder["compiler"] = compiler
                return compiler

            sender = RealArmSender(
                gateway,
                compiler_factory=factory,
                run_label=f"{ablation.value}-{condition.value}",
            )
            result: ArmRunResult = sender.run(scenario, StrategyArm.CURRENT)
            compiled = list(compiler_holder["compiler"].compiled_snapshots)
            snapshot_by_condition[condition] = compiled
            locks = [lock.model_dump(mode="json") for lock in result.locks]
            all_locks.extend(locks)
            conditions.append(
                {
                    "condition": condition.value,
                    "transcript": result.to_dict()["turns"],
                    "measurements": [item.to_dict() for item in result.measurements],
                    "compiled_snapshots": compiled,
                    "model_locks": locks,
                    "gates": [gate.to_dict() for gate in result.gates],
                }
            )
        effective, detail = signal_effect(
            ablation,
            snapshot_by_condition[SignalCondition.PRESENT],
            snapshot_by_condition[SignalCondition.ABSENT],
        )
        entries.append(
            {
                "ablation_id": ablation.value,
                "title": ABLATION_TITLES[ablation],
                "description": ABLATION_DESCRIPTIONS[ablation],
                "probe": {
                    "scenario_id": scenario.scenario_id,
                    "turns": list(scenario.turns),
                    "profile_facts": list(scenario.profile_facts),
                    "tool_signal": scenario.tool_outcome.value,
                },
                "conditions": conditions,
                "signal_effective": effective,
                "signal_evidence": detail,
            }
        )
    model_ids = sorted(
        {
            str(lock.get("actual_model_id"))
            for lock in all_locks
            if lock.get("actual_model_id")
        }
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "code_commit": code_commit(),
        "suite": {"suite_id": SUITE_ID, "suite_version": SUITE_VERSION},
        "scale_version": SCALE_VERSION,
        "candidate_arm": StrategyArm.CURRENT.value,
        "model_ids": model_ids,
        "model_call_count": len(all_locks),
        "ablations": entries,
        "limitations": [
            "工具部分结果信号由评测侧在策略接缝注入；评测发送器不执行检索工具。",
            "每项消融每条件一轮真实候选臂会话，样本用于信号效果核查，不单独宣称收益。",
            "消融回答未进行新增人工评分；主盲评与放行结论仍以冻结评分入口为准。",
        ],
    }


def verify_ablation_evidence(payload: dict[str, Any]) -> list[str]:
    """校验消融证据完整性；返回问题清单（空表示可用）。"""

    problems: list[str] = []
    entries = payload.get("ablations") or []
    if {entry.get("ablation_id") for entry in entries} != {
        ablation.value for ablation in AblationId
    }:
        problems.append("消融证据缺少预注册的三项之一。")
        return problems
    for entry in entries:
        conditions = entry.get("conditions") or []
        if {item.get("condition") for item in conditions} != {
            SignalCondition.PRESENT.value,
            SignalCondition.ABSENT.value,
        }:
            problems.append(f"{entry.get('ablation_id')} 缺少对照条件。")
            continue
        if not entry.get("signal_effective"):
            problems.append(f"{entry.get('ablation_id')} 信号在场/缺席未被真实编译快照证实。")
        for condition in conditions:
            locks = condition.get("model_locks") or []
            if not locks:
                problems.append(
                    f"{entry.get('ablation_id')}/{condition.get('condition')} 缺少模型运行锁。"
                )
            transcript = condition.get("transcript") or []
            if not transcript:
                problems.append(
                    f"{entry.get('ablation_id')}/{condition.get('condition')} 缺少真实回答。"
                )
            for index, turn in enumerate(transcript, start=1):
                if turn.get("status") != "done" or not (
                    turn.get("assistant") or ""
                ).strip():
                    problems.append(
                        f"{entry.get('ablation_id')}/{condition.get('condition')} "
                        f"第 {index} 回合未真实完成"
                        f"（{turn.get('status')}/{turn.get('error_code')}）。"
                    )
    return problems


__all__ = [
    "ABLATION_DESCRIPTIONS",
    "ABLATION_TITLES",
    "AblationId",
    "AblationPolicyCompiler",
    "SignalCondition",
    "ablation_probe",
    "run_real_ablations",
    "signal_effect",
    "verify_ablation_evidence",
]
