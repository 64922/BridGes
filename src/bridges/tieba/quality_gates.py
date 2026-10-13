"""在交付前重新计算并检查贴吧质量门。"""

from __future__ import annotations

from typing import Any

from bridges.kernel.contracts import (
    ArtifactTrust,
    KernelResult,
    NodeExecution,
    NodeInvocation,
    QualityGateResult,
    QualityVerdict,
)
from bridges.tieba.contracts import (
    TiebaEvidencePlan,
    TiebaOfficialCheck,
    TiebaPostProjection,
    TiebaQuestionAnalysis,
    TiebaResearchProjection,
    TiebaVerification,
)
from bridges.tieba.evidence_verification import run_verification
from bridges.tieba.recipe import (
    NODE_PARSE,
    NODE_PLAN,
    NODE_READ,
    NODE_SYNTHESIZE,
    NODE_VERIFY,
    NODE_VERIFY_OFFICIAL,
    TIEBA_RECIPE_ID,
    TIEBA_RECIPE_VERSION,
    build_tieba_recipe,
)


def verified_projection(result: KernelResult) -> TiebaResearchProjection | None:
    """交付前复验完整依赖链，拒绝未合格核验、篡改引用或候选归纳。"""
    artifacts = {artifact.node: artifact for artifact in result.artifacts}
    verify = artifacts.get(NODE_VERIFY)
    if verify is None or verify.trust_state is not ArtifactTrust.QUALIFIED:
        return None
    for spec in build_tieba_recipe().nodes:
        artifact = artifacts.get(spec.name)
        if (
            artifact is None
            or not artifact.verify_hash()
            or not artifact.reusable
            or artifact.recipe_id != TIEBA_RECIPE_ID
            or artifact.recipe_version != TIEBA_RECIPE_VERSION
            or artifact.capability_version != spec.capability_version
            or artifact.artifact_type != spec.artifact_type
            or (
                artifact.account_id,
                artifact.conversation_id,
                artifact.task_id,
                artifact.task_version,
            )
            != (verify.account_id, verify.conversation_id, verify.task_id, verify.task_version)
            or {dep.node for dep in artifact.input_deps} != set(spec.depends_on)
        ):
            return None
        for dep in artifact.input_deps:
            upstream = artifacts.get(dep.node)
            if (
                upstream is None
                or upstream.artifact_id != dep.artifact_id
                or upstream.content_hash != dep.content_hash
            ):
                return None
    try:
        analysis = TiebaQuestionAnalysis.model_validate(artifacts[NODE_PARSE].payload["analysis"])
        plan = TiebaEvidencePlan.model_validate(artifacts[NODE_PLAN].payload["plan"])
        checks = [
            TiebaOfficialCheck.model_validate(raw)
            for raw in artifacts[NODE_VERIFY_OFFICIAL].payload["checks"]
        ]
        posts = [
            TiebaPostProjection.model_validate(raw)
            for raw in artifacts[NODE_READ].payload["confirmed"]
        ]
        synthesis = TiebaResearchProjection.model_validate(
            artifacts[NODE_SYNTHESIZE].payload["projection"]
        )
        projected = TiebaResearchProjection.model_validate(verify.payload["projection"])
        verification = run_verification(analysis, plan, synthesis, checks, posts)
    except (ValueError, KeyError, TypeError):
        return None
    if not all(
        (
            verification.attribution_consistent,
            verification.read_scope_consistent,
            verification.unread_claims_absent,
            verification.consensus_claims_absent,
            verification.official_scope_consistent,
            verification.conflicts_disclosed,
        )
    ):
        return None
    return (
        projected
        if projected == synthesis.model_copy(update={"verification": verification})
        else None
    )


# ---------------------------------------------------------------------------
# 质量门：代码复核核验产物与依赖产物，模型不能自行宣布通过
# ---------------------------------------------------------------------------
def _gate_result(gate: str, passed: bool, *, code: str, message: str) -> QualityGateResult:
    return QualityGateResult(
        gate=gate,
        verdict=QualityVerdict.PASS if passed else QualityVerdict.BLOCKED,
        code="" if passed else code,
        message="" if passed else message,
    )


def _recomputed_verification(invocation: NodeInvocation) -> TiebaVerification:
    analysis = TiebaQuestionAnalysis.model_validate(
        dict(invocation.dependencies[NODE_PARSE].payload)["analysis"]
    )
    plan = TiebaEvidencePlan.model_validate(
        dict(invocation.dependencies[NODE_PLAN].payload)["plan"]
    )
    checks: list[TiebaOfficialCheck] = []
    for raw in dict(invocation.dependencies[NODE_VERIFY_OFFICIAL].payload).get("checks", []):
        try:
            checks.append(TiebaOfficialCheck.model_validate(raw))
        except ValueError:
            continue
    posts: list[TiebaPostProjection] = []
    for raw in dict(invocation.dependencies[NODE_READ].payload).get("confirmed", []):
        try:
            posts.append(TiebaPostProjection.model_validate(raw))
        except ValueError:
            continue
    projection = TiebaResearchProjection.model_validate(
        dict(invocation.dependencies[NODE_SYNTHESIZE].payload)["projection"]
    )
    return run_verification(analysis, plan, projection, checks, posts)


def _gate_attribution(invocation: NodeInvocation, _execution: NodeExecution) -> QualityGateResult:
    verification = _recomputed_verification(invocation)
    return _gate_result(
        "tieba.attribution_evidence",
        verification.attribution_consistent,
        code="tieba_attribution_inconsistent",
        message="确认帖的归属依据与真实读取页面不一致。",
    )


def _gate_read_scope(invocation: NodeInvocation, _execution: NodeExecution) -> QualityGateResult:
    verification = _recomputed_verification(invocation)
    passed = (
        verification.read_scope_consistent
        and verification.unread_claims_absent
        and verification.consensus_claims_absent
    )
    return _gate_result(
        "tieba.read_scope_fidelity",
        passed,
        code="tieba_read_scope_inconsistent",
        message="引用超出实际读取范围，或存在无法回溯的摘录/共识表述。",
    )


def _gate_official_scope(
    invocation: NodeInvocation, _execution: NodeExecution
) -> QualityGateResult:
    verification = _recomputed_verification(invocation)
    return _gate_result(
        "tieba.official_scope",
        verification.official_scope_consistent,
        code="tieba_official_scope_inconsistent",
        message="官方说法超出已取得且适用的页面范围（域名不自动放行）。",
    )


def _gate_conflict_disclosure(
    invocation: NodeInvocation, _execution: NodeExecution
) -> QualityGateResult:
    verification = _recomputed_verification(invocation)
    return _gate_result(
        "tieba.conflict_disclosure",
        verification.conflicts_disclosed,
        code="tieba_conflict_not_disclosed",
        message="官方与帖子的冲突没有按时间/范围如实分列。",
    )


TIEBA_GATE_HANDLERS: dict[str, Any] = {
    "tieba.attribution_evidence": _gate_attribution,
    "tieba.read_scope_fidelity": _gate_read_scope,
    "tieba.official_scope": _gate_official_scope,
    "tieba.conflict_disclosure": _gate_conflict_disclosure,
}
