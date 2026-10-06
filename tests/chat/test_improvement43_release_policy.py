"""工单 43：候选未放行时只停候选默认规则，保留边界/画像/预算。"""

from bridges.chat.global_writing_policy import GlobalWritingPolicyCompiler
from bridges.chat.lightweight_policy import (
    FORM_RULES,
    GLOBAL_CHAT_LIGHTWEIGHT_VERSION,
    GLOBAL_DEFAULT_RULES,
    RELEASE_BASELINE_POLICY_VERSION,
    ChatLightweightPolicyCompiler,
)
from bridges.evaluation.expression_policy_arms import ArmPolicyCompiler, StrategyArm
from tests.chat.test_improvement22_unified_profile_expression import BREVITY, _adopted


def test_low_level_default_cannot_bypass_release_gate() -> None:
    baseline = ChatLightweightPolicyCompiler().compile(
        "study", user_text="详细解释贝叶斯定理", adopted_slice=_adopted([BREVITY]),
    )
    assert baseline.version == RELEASE_BASELINE_POLICY_VERSION
    assert "release-task-baseline" in baseline.rule_ids
    assert "answer-first" not in baseline.rule_ids
    assert baseline.profile_slice_id
    assert baseline.output_tokens == GlobalWritingPolicyCompiler().compile(
        "study", user_text="详细解释贝叶斯定理",
    ).output_tokens


def test_production_baseline_retains_constraints_profile_and_budget() -> None:
    adopted = _adopted([BREVITY])
    text = "解释贝叶斯定理，详细推导，但不要安慰，不要追问"
    baseline = GlobalWritingPolicyCompiler().compile(
        "companion", user_text=text, adopted_slice=adopted,
    )
    candidate = GlobalWritingPolicyCompiler(candidate_enabled=True).compile(
        "companion", user_text=text, adopted_slice=adopted,
    )
    assert baseline.version == RELEASE_BASELINE_POLICY_VERSION
    assert candidate.version == GLOBAL_CHAT_LIGHTWEIGHT_VERSION
    assert baseline.constraints == candidate.constraints
    assert baseline.constraints
    assert baseline.output_tokens == candidate.output_tokens
    assert baseline.profile_slice_id == adopted.slice_id
    assert baseline.profile_revocation_version == adopted.revocation_version
    assert baseline.profile_items == candidate.profile_items
    assert baseline.profile_decisions == candidate.profile_decisions
    assert baseline.contract_version_hash == candidate.contract_version_hash
    assert "本轮没有可用画像信息" not in baseline.system_block
    candidate_ids = {rule_id for rule_id, _ in GLOBAL_DEFAULT_RULES}
    candidate_ids.update(rule_id for rules in FORM_RULES.values() for rule_id, _ in rules)
    assert candidate_ids.isdisjoint(baseline.rule_ids)
    assert candidate_ids.intersection(candidate.rule_ids)
    assert "release-task-baseline" in baseline.rule_ids
    assert "受保护区" in baseline.system_block


def test_release_baseline_still_adopts_valid_profile_rules() -> None:
    adopted = _adopted([BREVITY])
    baseline = GlobalWritingPolicyCompiler().compile(
        "companion", user_text="解释贝叶斯定理", adopted_slice=adopted,
    )
    assert "adopted-brevity-default" in baseline.rule_ids
    assert "用户长期偏好简短直接" in baseline.system_block
    assert baseline.profile_decisions == ("expression_length",)


def test_evaluation_current_arm_explicitly_uses_candidate() -> None:
    snapshot = ArmPolicyCompiler(StrategyArm.CURRENT).compile(
        "companion", user_text="你好",
    )
    assert snapshot.version == GLOBAL_CHAT_LIGHTWEIGHT_VERSION
    assert {rule_id for rule_id, _ in GLOBAL_DEFAULT_RULES}.issubset(snapshot.rule_ids)


def test_policy_rollback_keeps_existing_complete_snapshot_for_history() -> None:
    existing = GlobalWritingPolicyCompiler(candidate_enabled=True).compile(
        "companion", user_text="你好",
    )
    assert GlobalWritingPolicyCompiler().compile(
        "companion", existing_snapshot=existing,
    ) == existing
