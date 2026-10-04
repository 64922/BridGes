"""独立验收发现的交付、最终门和澄清回归。"""

from datetime import UTC, datetime

from bridges.contracts.chat import ChatMessageStatus
from bridges.contracts.modules import ModuleDelivery
from bridges.github.contracts import (
    GithubProjectsProjection,
    GithubProjectStatus,
    GithubRateLimitState,
)
from bridges.github.service import GithubDelivery, GithubRunOutcome
from bridges.orchestration import CompositePlanner
from bridges.orchestration.contracts import (
    Claim,
    CompositeOutcome,
    CompositeStatus,
    IndependentVerification,
    StepResult,
    StepState,
    SynthesisDraft,
    SynthesisSection,
    VerificationSource,
    VerificationTrigger,
    VerificationVerdict,
)
from bridges.orchestration.executor import StepRunContext
from bridges.orchestration.production import ModuleServiceStepRunner, render_final_content
from bridges.orchestration.synthesis import FinalGate, Synthesizer


def _plan():
    plan = CompositePlanner().plan(
        goal="查论文和项目", user_message_id="user", module_ids=["paper", "github"]
    )
    assert plan is not None
    return plan


def test_real_github_delivery_is_accepted() -> None:
    projection = GithubProjectsProjection(
        status=GithubProjectStatus.EMPTY, scenario="项目", original_request="查项目",
        rate_limit=GithubRateLimitState(),
    )
    delivery = GithubDelivery(
        projection=projection, content="尚无匹配项目。",
        message_status=ChatMessageStatus.DONE,
        verification_artifact_id="verify", present_artifact_id="present",
    )
    runner = ModuleServiceStepRunner(
        resolve=lambda step, context: {},
        run=lambda step, context, resolved: GithubRunOutcome(
            status=projection.status, delivery=delivery
        ),
    )
    result = runner.run(
        _plan().steps[1],
        StepRunContext(
            account_id="account", conversation_id="conversation", run_id="run",
            user_message_id="user", assistant_message_id="assistant", goal="查项目",
        ), {}, 1000,
    )
    assert result.state is StepState.COMPLETED
    assert result.artifact_refs == {"github.verify": "verify", "github.present": "present"}
    assert result.delivery.projection_field == "github_projects"


def _step() -> StepResult:
    return StepResult(
        step_id="paper", module_id="paper", state=StepState.COMPLETED,
        trust_state="qualified", summary="只读取了摘要。", evidence_refs=["evidence"],
    )


def test_final_gate_rejects_new_text_even_with_valid_refs() -> None:
    step = _step()
    draft = SynthesisDraft(summary="已取得论文。", sections=[SynthesisSection(
        title="论文", body="该实现已经运行验证。", step_ids=["paper"],
        evidence_refs=["evidence"],
    )])
    gate = FinalGate().check(draft, [step])
    assert not gate.passed and gate.new_facts


def test_final_gate_rejects_unknown_step_without_refs() -> None:
    draft = SynthesisDraft(summary="结果", sections=[SynthesisSection(
        title="论文", body="新的结论", step_ids=["missing"],
    )])
    assert not FinalGate().check(draft, [_step()]).passed


def test_risk_verifier_cannot_opt_out_of_required_check() -> None:
    step = _step()
    step.claims = [Claim(
        text=step.summary, evidence_refs=["evidence"], risk="personal_gap"
    )]

    class Verifier:
        def verify(self, **kwargs):
            return IndependentVerification(
                required=False, trigger=VerificationTrigger.ORDINARY_CHAT,
                verdict=VerificationVerdict.NOT_APPLICABLE,
                source=VerificationSource.SAME_MODEL_INDEPENDENT_CALL,
                rules=["无需核验"],
            )

    gate = FinalGate(verifier=Verifier()).check(
        SynthesisDraft(summary="结果"), [step]
    )
    assert not gate.passed
    assert gate.verifications[0].required


def test_synthesis_keeps_clarification_question() -> None:
    step = StepResult(
        step_id="paper", module_id="paper", state=StepState.NEEDS_INPUT,
        summary="请确认你想看的方向。",
        delivery=ModuleDelivery(
            module_id="paper", status="clarification", projection_field="paper_search",
            projection={}, content="请确认你想看的方向。", message_status="done",
            wait_reason="paper_clarification",
        ),
    )
    draft = Synthesizer().build(CompositeOutcome(
        status=CompositeStatus.NEEDS_INPUT, plan=_plan(), steps=[step],
        created_at=datetime.now(UTC),
    ))
    assert "请确认你想看的方向。" in render_final_content(draft)


def test_synthesis_does_not_publish_unqualified_completed_body() -> None:
    step = _step().model_copy(update={"trust_state": "draft"})
    draft = Synthesizer().build(CompositeOutcome(
        status=CompositeStatus.COMPLETED, plan=_plan(), steps=[step],
        created_at=datetime.now(UTC),
    ))
    assert not draft.sections
