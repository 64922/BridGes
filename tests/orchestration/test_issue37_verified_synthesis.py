"""工单 37：生产证据核验、跨轮失效与最终门一轮修复的独立回归。"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bridges.chat.repository import ConversationRepository
from bridges.contracts.modules import ModuleDelivery
from bridges.kernel.contracts import ArtifactTrust, NodeArtifact
from bridges.kernel.repository import NodeKernelRepository
from bridges.orchestration.contracts import (
    Claim,
    CompositeStatus,
    StepFailure,
    StepResult,
    StepState,
    SynthesisDraft,
    VerificationVerdict,
)
from bridges.orchestration.evidence import DeliveryEvidence, EvidenceVerifier
from bridges.orchestration.executor import (
    FunctionStepRunner,
    StepRunContext,
)
from bridges.orchestration.planner import CompositePlanner
from bridges.orchestration.production import CompositeOrchestrationService
from bridges.orchestration.synthesis import FinalGate, Synthesizer
from bridges.storage.database import BridgesDatabase
from tests.orchestration.issue37_chains import (
    seed_career,
    seed_resources,
)

ACCOUNT = "acc-37-evidence"
CONVERSATION = "conv-37-evidence"
RUN = "run-37-evidence"


def _database(tmp_path: Path) -> BridgesDatabase:
    database = BridgesDatabase(tmp_path / "bridges.db")
    assert database.initialize() > 0
    return database


def _context(
    *,
    run_id: str = RUN,
    task_id: str | None = None,
    task_version: int | None = None,
) -> StepRunContext:
    return StepRunContext(
        account_id=ACCOUNT,
        conversation_id=CONVERSATION,
        run_id=run_id,
        user_message_id="msg-user-37",
        assistant_message_id="msg-assistant-37",
        goal="目标",
        task_id=task_id,
        task_version=task_version,
    )


def _step(module: str):
    plan = CompositePlanner().plan(
        goal="目标", user_message_id="msg-user-37", module_ids=["paper", "resources"]
    )
    assert plan is not None
    return plan.step(module)


def test_qualify_extracts_short_claims_from_real_chain(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _, delivery = seed_resources(
        database, run_id=RUN, account_id=ACCOUNT, conversation_id=CONVERSATION
    )
    result = DeliveryEvidence(ConversationRepository(database)).qualify(
        _step("resources"), _context(), delivery
    )
    assert result.state is StepState.COMPLETED
    assert result.trust_state == "qualified"
    assert result.claims and result.claims[0].text.startswith("入门资料")
    assert result.evidence_refs == list(result.evidence)
    assert result.summary
    assert result.scope_key


def test_done_without_real_artifacts_never_qualifies(tmp_path: Path) -> None:
    database = _database(tmp_path)
    delivery = ModuleDelivery(
        module_id="paper",
        status="success",
        projection_field="paper_search",
        projection={},
        content="该方法显著优于全部现有方法。",
        message_status="done",
        artifact_refs={"paper.verify": "art-missing"},
    )
    result = DeliveryEvidence(ConversationRepository(database)).qualify(
        _step("paper"), _context(), delivery
    )
    assert result.state is StepState.COMPLETED
    assert result.trust_state == "draft"
    assert not result.claims
    assert result.failure is not None
    assert result.failure.code == "delivery_evidence_unverified"


def test_tampered_payload_hash_blocks_qualification(tmp_path: Path) -> None:
    database = _database(tmp_path)
    chain, delivery = seed_resources(
        database, run_id=RUN, account_id=ACCOUNT, conversation_id=CONVERSATION
    )
    verify = chain["resources.verify"]
    tampered = dataclasses.replace(
        verify,
        artifact_id="art_tampered_verify",
        payload={"projection": {"status": "success", "items": []}},
    )
    NodeKernelRepository(database).save_artifact(tampered)
    forged = delivery.model_copy(
        update={"artifact_refs": {"resources.verify": tampered.artifact_id}}
    )
    result = DeliveryEvidence(ConversationRepository(database)).qualify(
        _step("resources"), _context(), forged
    )
    assert result.trust_state == "draft"
    assert not result.claims


def test_artifact_from_other_conversation_is_rejected(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _, delivery = seed_resources(
        database, run_id=RUN, account_id=ACCOUNT, conversation_id="conv-other"
    )
    result = DeliveryEvidence(ConversationRepository(database)).qualify(
        _step("resources"), _context(), delivery
    )
    assert result.trust_state == "draft"


def test_stale_public_evidence_cannot_be_restored(tmp_path: Path) -> None:
    database = _database(tmp_path)
    stale = datetime.now(UTC) - timedelta(hours=25)
    _, delivery = seed_resources(
        database,
        run_id=RUN,
        account_id=ACCOUNT,
        conversation_id=CONVERSATION,
        created_at=stale,
    )
    evidence = DeliveryEvidence(ConversationRepository(database))
    record = {
        "module_id": "resources",
        "artifact_refs": dict(delivery.artifact_refs),
        "input_fingerprint": "fp",
        "resolved_param_names": ["topic"],
    }
    assert evidence.restore(_step("resources"), _context(run_id="run-new"), record) is None
    result = evidence.qualify(
        _step("resources"), _context(run_id="run-new"), delivery
    )
    assert result.trust_state == "draft"


def test_capability_version_mismatch_blocks_qualification(tmp_path: Path) -> None:
    database = _database(tmp_path)
    chain, delivery = seed_resources(
        database, run_id=RUN, account_id=ACCOUNT, conversation_id=CONVERSATION
    )
    verify = chain["resources.verify"]
    outdated = dataclasses.replace(
        verify,
        artifact_id="art_outdated_verify",
        capability_version="resources-verify-old",
    )
    NodeKernelRepository(database).save_artifact(outdated)
    forged = delivery.model_copy(
        update={"artifact_refs": {"resources.verify": outdated.artifact_id}}
    )
    result = DeliveryEvidence(ConversationRepository(database)).qualify(
        _step("resources"), _context(), forged
    )
    assert result.trust_state == "draft"


def test_schema_version_mismatch_blocks_qualification(tmp_path: Path) -> None:
    database = _database(tmp_path)
    chain, delivery = seed_resources(
        database, run_id=RUN, account_id=ACCOUNT, conversation_id=CONVERSATION
    )
    verify = chain["resources.verify"]
    old_schema = dataclasses.replace(
        verify, artifact_id="art_old_schema_verify", schema_version="artifact-v0"
    )
    NodeKernelRepository(database).save_artifact(old_schema)
    forged = delivery.model_copy(
        update={"artifact_refs": {"resources.verify": old_schema.artifact_id}}
    )
    result = DeliveryEvidence(ConversationRepository(database)).qualify(
        _step("resources"), _context(), forged
    )
    assert result.trust_state == "draft"


def test_invalidated_or_revoked_artifact_cannot_be_reused(tmp_path: Path) -> None:
    database = _database(tmp_path)
    chain, delivery = seed_resources(
        database, run_id=RUN, account_id=ACCOUNT, conversation_id=CONVERSATION
    )
    verify = chain["resources.verify"]
    revoked = dataclasses.replace(
        verify,
        artifact_id="art_revoked_verify",
        trust_state=ArtifactTrust.INVALIDATED,
    )
    NodeKernelRepository(database).save_artifact(revoked)
    forged = delivery.model_copy(
        update={"artifact_refs": {"resources.verify": revoked.artifact_id}}
    )
    evidence = DeliveryEvidence(ConversationRepository(database))
    result = evidence.qualify(_step("resources"), _context(), forged)
    assert result.trust_state == "draft"
    record = {
        "module_id": "resources",
        "artifact_refs": {"resources.verify": revoked.artifact_id},
    }
    assert evidence.restore(_step("resources"), _context(run_id="run-new"), record) is None


def test_career_private_background_stays_local(tmp_path: Path) -> None:
    from bridges.career_plan.contracts import CareerPlanProjection

    database = _database(tmp_path)
    projection = CareerPlanProjection(
        status="success",
        topic="Java 后端开发",
        original_request="私人简历正文-绝密：找实习",
        samples=[],
        gaps=[],
        personal_advices=[],
        combination_requirements=[],
        background={
            "checked_at": datetime.now(UTC),
            "items": [
                {
                    "source": "resume",
                    "text": "私人简历正文-绝密",
                    "source_ref": "resume:1",
                }
            ],
        },
        personal_boundary=["私人背景只在本地参与对照。"],
    ).model_dump(mode="json")
    chain, _ = seed_career(
        database,
        run_id=RUN,
        account_id=ACCOUNT,
        conversation_id=CONVERSATION,
        requirements=[],
    )
    # 用带私人背景的投影替换链上 payload（保持哈希自洽）。
    verify = chain["career.verify"]
    rebuilt = NodeArtifact.build(
        account_id=ACCOUNT,
        conversation_id=CONVERSATION,
        run_id=RUN,
        task_id=None,
        task_version=None,
        recipe_id=verify.recipe_id,
        recipe_version=verify.recipe_version,
        node=verify.node,
        artifact_type=verify.artifact_type,
        capability_version=verify.capability_version,
        trust_state=ArtifactTrust.QUALIFIED,
        input_key="test:career:private",
        input_deps=verify.input_deps,
        source_refs=(),
        read_scope=verify.read_scope,
        requirement_coverage=(),
        unconfirmed=("私人背景只在本地参与对照。",),
        error=None,
        payload={"projection": projection},
        now=datetime.now(UTC),
    )
    NodeKernelRepository(database).save_artifact(rebuilt)
    delivery = ModuleDelivery(
        module_id="career",
        status="success",
        projection_field="career_plan",
        projection=projection,
        content="岗位正文。",
        message_status="done",
        artifact_refs={"career.verify": rebuilt.artifact_id},
    )
    result = DeliveryEvidence(ConversationRepository(database)).qualify(
        _step("resources").model_copy(
            update={"step_id": "career", "module_id": "career"}
        ),
        _context(),
        delivery,
    )
    assert result.trust_state == "qualified"
    # 私人背景只留在本地证据里（供确定性复核重算），
    # 不进入会外发/综合的短结论与限定以外的正文。
    assert "私人简历正文-绝密" not in result.summary
    assert all("私人简历正文-绝密" not in claim.text for claim in result.claims)
    assert "私人背景只在本地参与对照。" in result.unconfirmed


def test_forged_conclusion_is_blocked_by_independent_verifier(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _, delivery = seed_career(
        database, run_id=RUN, account_id=ACCOUNT, conversation_id=CONVERSATION
    )
    result = DeliveryEvidence(ConversationRepository(database)).qualify(
        _step("resources").model_copy(
            update={"step_id": "career", "module_id": "career"}
        ),
        _context(),
        delivery,
    )
    assert result.trust_state == "qualified"
    claim = result.claims[0]
    forged = claim.model_copy(update={"text": "本轮公开岗位样本：999 个；仅描述这些样本。"})
    gate = FinalGate(verifier=EvidenceVerifier()).check(
        SynthesisDraft(summary="x", limitations=list(result.unconfirmed)),
        [result.model_copy(update={"claims": [forged]})],
    )
    assert not gate.passed
    assert gate.verifications
    assert gate.verifications[0].verdict is VerificationVerdict.BLOCK


def _repair_plan():
    plan = CompositePlanner().plan(
        goal="查论文和资料", user_message_id="msg-u", module_ids=["paper", "resources"]
    )
    assert plan is not None
    return plan


def _step_result(step_id: str, *, good: bool) -> StepResult:
    claim = Claim(
        text="结论",
        evidence_refs=["paper:1" if good else "paper:ghost"],
    )
    return StepResult(
        step_id=step_id,
        module_id=step_id,
        state=StepState.COMPLETED,
        trust_state="qualified",
        claims=[claim],
        evidence_refs=list(claim.evidence_refs) if good else [],
        evidence={"paper:1": "{}"} if good else {},
        summary="结论",
        artifact_refs={f"{step_id}.verify": f"art-{step_id}"},
    )


def _runner(calls: list[int], make) -> FunctionStepRunner:
    def resolve(step, context):  # noqa: ANN001
        return {"topic": "目标"}

    def run(step, context, resolved, remaining_budget_ms):  # noqa: ANN001
        calls.append(context.attempt)
        return make(step, context)

    return FunctionStepRunner(resolve=resolve, run=run)


class _Budget:
    """共享预算替身：只允许一轮调整。"""

    def __init__(self) -> None:
        self.adjustments = 0

    def remaining_work_ms(self) -> int:
        return 10000

    def external_parallel_max(self) -> int:
        return 2

    def adjustment_rounds_max(self) -> int:
        return 1

    def begin_adjustment(self, reason_code: str) -> bool:
        self.adjustments += 1
        return self.adjustments == 1

    def end_adjustment(self, outcome_code: str, detail: dict | None = None) -> None:
        self.ended = (outcome_code, detail)


def test_gate_failure_triggers_one_controlled_repair(tmp_path: Path) -> None:
    calls: list[int] = []
    runners = {
        "paper": _runner(
            calls,
            lambda step, context: _step_result("paper", good=context.attempt >= 1),
        ),
        "resources": _runner(
            calls, lambda step, context: _step_result("resources", good=True)
        ),
    }
    service = CompositeOrchestrationService(
        gate=FinalGate(verifier=EvidenceVerifier())
    )
    result = service.run(
        plan=_repair_plan(),
        runners=runners,
        context=_context(),
        budget=_Budget(),
    )
    assert result.gate.passed
    assert result.outcome.plan.revision == 1
    assert calls.count(0) == 2
    assert calls.count(1) == 1


def test_second_gate_repair_round_is_rejected(tmp_path: Path) -> None:
    calls: list[int] = []
    runners = {
        "paper": _runner(calls, lambda step, context: _step_result("paper", good=False)),
        "resources": _runner(
            calls, lambda step, context: _step_result("resources", good=True)
        ),
    }
    service = CompositeOrchestrationService(
        gate=FinalGate(verifier=EvidenceVerifier())
    )
    result = service.run(
        plan=_repair_plan(), runners=runners, context=_context(), budget=_Budget()
    )
    assert not result.gate.passed
    assert result.outcome.plan.revision == 1
    assert calls.count(0) == 2
    assert calls.count(1) == 1


def test_step_adjustment_and_gate_repair_share_one_round(tmp_path: Path) -> None:
    calls: list[int] = []
    budget = _Budget()

    def paper(step, context):  # noqa: ANN001
        if context.attempt == 0:
            return StepResult(
                step_id="paper",
                module_id="paper",
                state=StepState.FAILED,
                failure=StepFailure(code="timeout", message="临时超时", retryable=True),
            )
        return _step_result("paper", good=False)

    runners = {
        "paper": _runner(calls, paper),
        "resources": _runner(
            calls, lambda step, context: _step_result("resources", good=True)
        ),
    }
    service = CompositeOrchestrationService(
        gate=FinalGate(verifier=EvidenceVerifier())
    )
    result = service.run(
        plan=_repair_plan(), runners=runners, context=_context(), budget=budget
    )
    assert not result.gate.passed
    assert budget.adjustments == 1
    assert calls.count(0) == 2
    assert calls.count(1) == 1


def test_planner_rejects_second_adjustment() -> None:
    plan = _repair_plan()
    first, violation = CompositePlanner().adjusted(plan, reason="gate_repair")
    assert first is not None and violation is None
    second, second_violation = CompositePlanner().adjusted(
        first, reason="gate_repair"
    )
    assert second is None
    assert second_violation is not None


def test_synthesizer_excludes_unqualified_steps(tmp_path: Path) -> None:
    draft_bad = _step_result("paper", good=True).model_copy(
        update={"trust_state": "draft"}
    )
    outcome = FinalGate(verifier=EvidenceVerifier()).check(
        Synthesizer().build(
            _repair_outcome([draft_bad])
        ),
        [draft_bad],
    )
    assert outcome.passed


def _repair_outcome(steps: list[StepResult]):
    from bridges.orchestration.contracts import CompositeOutcome

    return CompositeOutcome(
        status=CompositeStatus.COMPLETED,
        plan=_repair_plan(),
        steps=steps,
        created_at=datetime.now(UTC),
    )


def test_planner_sorts_dependencies_before_dependents() -> None:
    plan = CompositePlanner().plan(
        goal="换杭州后重新找岗位和资料",
        user_message_id="msg-user-37",
        module_ids=["resources", "career"],
    )
    assert plan is not None
    step_ids = [step.step_id for step in plan.steps]
    assert step_ids.index("career") < step_ids.index("resources")


def test_evidence_bound_terminal_is_not_upgraded(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _, delivery = seed_resources(
        database,
        run_id=RUN,
        account_id=ACCOUNT,
        conversation_id=CONVERSATION,
        terminal_trust=ArtifactTrust.EVIDENCE_BOUND,
    )
    result = DeliveryEvidence(ConversationRepository(database)).qualify(
        _step("resources"), _context(), delivery
    )
    assert result.trust_state == "draft"
    assert not result.claims


def test_restore_rejects_unqualified_step_record(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _, delivery = seed_resources(
        database, run_id=RUN, account_id=ACCOUNT, conversation_id=CONVERSATION
    )
    record = {
        "module_id": "resources",
        "trust_state": "draft",
        "artifact_refs": dict(delivery.artifact_refs),
    }
    evidence = DeliveryEvidence(ConversationRepository(database))
    assert evidence.restore(_step("resources"), _context(run_id="run-new"), record) is None


def test_draft_prerequisite_blocks_dependent_step() -> None:
    from bridges.orchestration.executor import CompositeExecutor

    plan = CompositePlanner().plan(
        goal="找岗位和资料",
        user_message_id="msg-user-37",
        module_ids=["career", "resources"],
    )
    assert plan is not None
    calls: list[str] = []

    def resolve(step, context):  # noqa: ANN001
        return {"topic": "Java"}

    def run(step, context, resolved, remaining_budget_ms):  # noqa: ANN001
        calls.append(step.step_id)
        if step.step_id == "career":
            return StepResult(
                step_id="career",
                module_id="career",
                state=StepState.COMPLETED,
                trust_state="draft",
                failure=StepFailure(
                    code="delivery_evidence_unverified",
                    message="交付证据未通过核验。",
                    retryable=False,
                ),
            )
        return _step_result("resources", good=True)

    runner = FunctionStepRunner(resolve=resolve, run=run)
    outcome = CompositeExecutor(planner=CompositePlanner()).execute(
        plan=plan,
        runners={"career": runner, "resources": runner},
        context=_context(),
    )
    assert plan.step("resources").depends_on == ["career"]
    steps = {step.step_id: step for step in outcome.steps}
    assert steps["career"].trust_state == "draft"
    assert steps["resources"].state is StepState.BLOCKED
    assert calls == ["career"]


def test_unqualified_projection_is_not_committed(tmp_path: Path) -> None:
    from bridges.orchestration.production import (
        persist_synthesis_artifact,
        projection_updates,
    )

    database = _database(tmp_path)
    _, delivery = seed_resources(
        database, run_id=RUN, account_id=ACCOUNT, conversation_id=CONVERSATION
    )
    draft_step = _step_result("resources", good=True).model_copy(
        update={
            "trust_state": "draft",
            "delivery": delivery,
        }
    )
    outcome = _repair_outcome([draft_step])
    assert projection_updates(outcome) == {}
    gate = FinalGate().check(Synthesizer().build(outcome), outcome.steps)
    artifact = persist_synthesis_artifact(
        NodeKernelRepository(database),
        account_id=ACCOUNT,
        conversation_id=CONVERSATION,
        run_id=RUN,
        task_id=None,
        task_version=None,
        outcome=outcome,
        gate=gate,
        draft=Synthesizer().build(outcome),
        conditions=[],
        now=datetime.now(UTC),
    )
    assert artifact.trust_state is ArtifactTrust.EVIDENCE_BOUND
