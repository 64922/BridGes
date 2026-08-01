"""Unit tests for expression version comparison and release gate (T029).

The seam under test: a user compares two expression drafts (versions), approves an
artifact, and attempts to publish. Release eligibility depends on the expression
gate, human approval, workflow success, open human todos, and the active state of
upstream sources, claim graphs and fact locks.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from bridges.contracts.expression import (
    ApplyRevisionPatchRequest,
    ApproveArtifactRequest,
    CompareVersionsRequest,
    ExpressionBrief,
    ExpressionDraftRequest,
    Genre,
    HumanDecisionType,
    PatchAction,
    PublishArtifactRequest,
    ReleaseEligibilityStatus,
    ReleaseGateCheck,
    RiskTier,
    VersionDifferenceField,
)
from bridges.contracts.identity import AuthMethod, SubjectContext
from bridges.contracts.invalidation import InvalidationEventType
from bridges.contracts.projects import ObjectDomain, ObjectRef
from bridges.contracts.science import (
    Citation,
    CitationLocator,
    CitationVerificationStatus,
    Claim,
    ClaimGraph,
    ClaimImportance,
    ClaimTrustStatus,
    ClaimType,
    Evidence,
    EvidenceRelation,
    FactLockSet,
    ValidationReport,
)
from bridges.contracts.workflows import WorkOrder
from bridges.expression import ExpressionService, ExpressionServiceError
from bridges.invalidation import InvalidationService
from bridges.science import (
    ClaimEvidenceService,
    ScienceSearchService,
    ScienceSourceService,
)
from bridges.contracts.expression import StyleDiagnosticSeverity
from bridges.science.fact_lock import apply_honest_degradation, compile_fact_locks
from bridges.scope import ScopeEnforcer
from bridges.workflows import WorkflowService


def _now() -> datetime:
    return datetime.now(UTC)


def _subject(account_id: str) -> SubjectContext:
    return SubjectContext(
        account_id=account_id,
        session_id="test-session",
        auth_method=AuthMethod.PASSWORD,
    )


def _make_graph(
    *,
    status: ClaimTrustStatus = ClaimTrustStatus.VERIFIED,
    project_id: str | None = None,
) -> ClaimGraph:
    return ClaimGraph(
        graph_id="graph-1",
        account_id="account-alice",
        project_id=project_id,
        query="test query",
        status=status,
        claims=[],
        evidence=[],
        citations=[],
        created_at=_now(),
    )


def _add_claim(
    graph: ClaimGraph,
    *,
    text: str,
    importance: ClaimImportance = ClaimImportance.KEY,
    claim_type: ClaimType = ClaimType.DESCRIPTIVE,
    scope: str | None = None,
) -> Claim:
    claim = Claim(
        claim_id=f"claim-{len(graph.claims)}",
        graph_id=graph.graph_id,
        text=text,
        importance=importance,
        claim_type=claim_type,
        scope=scope,
        status=ClaimTrustStatus.VERIFIED,
        created_at=_now(),
    )
    graph.claims.append(claim)
    return claim


def _add_evidence(
    graph: ClaimGraph,
    claim: Claim,
    relation: EvidenceRelation,
    document_id: str = "doc-1",
) -> Evidence:
    evidence = Evidence(
        evidence_id=f"ev-{len(graph.evidence)}",
        claim_id=claim.claim_id,
        document_id=document_id,
        chunk_ids=["chunk-1"],
        relation=relation,
        valid_from=_now(),
    )
    claim.evidence_ids.append(evidence.evidence_id)
    graph.evidence.append(evidence)
    return evidence


def _add_citation(
    graph: ClaimGraph,
    claim: Claim,
    evidence: Evidence,
    source_id: str = "source-1",
) -> Citation:
    citation = Citation(
        citation_id=f"cite-{len(graph.citations)}",
        evidence_id=evidence.evidence_id,
        claim_id=claim.claim_id,
        locator=CitationLocator(),
        identifier_snapshot={"source_id": source_id, "document_id": evidence.document_id},
        accessed_at=_now(),
        verification_status=CitationVerificationStatus.VERIFIED,
    )
    claim.citation_ids.append(citation.citation_id)
    graph.citations.append(citation)
    return citation


class _FakeClaimService(ClaimEvidenceService):
    """In-memory stand-in for claim evidence service in expression tests."""

    def __init__(self, graph: ClaimGraph) -> None:
        source_service = ScienceSourceService()
        super().__init__(
            source_service=source_service,
            search_service=ScienceSearchService(source_service=source_service),
        )
        self._graph = graph
        self._locks = compile_fact_locks(graph)
        self._report = apply_honest_degradation(graph, had_candidates=True)

    def get_claim_graph(self, account_id: str, graph_id: str) -> ClaimGraph:
        return self._graph

    def compile_fact_locks(self, account_id: str, graph_id: str) -> FactLockSet:
        return self._locks

    def validate_claim_graph(
        self, account_id: str, graph_id: str, *, apply: bool = False
    ) -> ValidationReport:
        return self._report

    def find_source_id_for_document(self, document_id: str) -> str | None:
        """Return a deterministic source id for tests."""
        return "source-1"


def _brief(**overrides: object) -> ExpressionBrief:
    defaults: dict[str, Any] = {
        "brief_id": "brief-1",
        "task_goal": "向非专业读者解释线粒体功能",
        "deliverable_type": "科普文案",
        "genre": Genre.POPULAR_SCIENCE,
        "audience_id": "audience-general",
        "channel": "公众号",
        "length_or_duration": "800字",
        "language_locale": "zh-CN",
        "risk_tier": RiskTier.LOW,
        "required_claim_ids": [],
        "optional_claim_ids": [],
        "forbidden_content": [],
        "success_criteria": ["每个核心判断有引用", "不夸大结论"],
        "deadline_and_context": None,
        "memory_slice_id": None,
    }
    defaults.update(overrides)
    return ExpressionBrief(**defaults)


@pytest.fixture
def services() -> dict[str, Any]:
    """Build expression service with workflow and invalidation collaborators."""
    scope_enforcer = ScopeEnforcer()
    invalidation_service = InvalidationService(scope_enforcer=scope_enforcer)
    workflow_service = WorkflowService(
        scope_enforcer=scope_enforcer,
        invalidation_service=invalidation_service,
    )
    workflow_service.register_workflow(
        name="expression_task",
        version="1",
        nodes=[
            {
                "node_id": "compile",
                "node_name": "编译表达任务",
                "human_gate": False,
            },
        ],
        terminal_states=["succeeded", "blocked", "cancelled"],
    )
    return {
        "scope_enforcer": scope_enforcer,
        "invalidation_service": invalidation_service,
        "workflow_service": workflow_service,
    }


class TestVersionComparison:
    def test_compare_versions_highlights_fact_claim_and_wording_changes(
        self, services: dict[str, Any]
    ) -> None:
        graph = _make_graph()
        claim = _add_claim(graph, text="线粒体通过细胞呼吸产生 ATP。")
        evidence = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        _add_citation(graph, claim, evidence)

        claim_service = _FakeClaimService(graph)
        expression_service = ExpressionService(
            claim_service=claim_service,
            invalidation_service=services["invalidation_service"],
            workflow_service=services["workflow_service"],
        )
        subject = _subject("account-alice")

        request_a = ExpressionDraftRequest(
            brief=_brief(genre=Genre.POPULAR_SCIENCE),
            graph_id=graph.graph_id,
        )
        result_a = expression_service.create_draft_and_store(subject, request_a)
        draft_a = result_a.draft

        # Version B: same graph rendered as research report, so argument plan and
        # genre elements differ while claims and fact locks stay the same.
        request_b = ExpressionDraftRequest(
            brief=_brief(genre=Genre.RESEARCH_REPORT),
            graph_id=graph.graph_id,
        )
        result_b = expression_service.create_draft_and_store(subject, request_b)
        draft_b = result_b.draft

        comparison = expression_service.compare_versions(
            subject,
            CompareVersionsRequest(draft_id_a=draft_a.draft_id, draft_id_b=draft_b.draft_id),
        )

        assert comparison.draft_id_a == draft_a.draft_id
        assert comparison.draft_id_b == draft_b.draft_id
        fields = {d.field for d in comparison.differences}
        assert VersionDifferenceField.GENRE in fields
        assert VersionDifferenceField.ARGUMENT_PLAN in fields
        # Claims, citations and fact locks are preserved across genre conversion.
        assert VersionDifferenceField.CLAIM_IDS not in fields
        assert VersionDifferenceField.CITATION_IDS not in fields
        assert VersionDifferenceField.FACT_LOCK_SET not in fields
        assert VersionDifferenceField.WORDING_STRENGTH_CEILING not in fields

    def test_compare_versions_detects_manual_patch_changes(
        self, services: dict[str, Any]
    ) -> None:
        graph = _make_graph()
        # Include a template pattern so the diagnostic produces a patch that can be
        # applied without changing fact locks.
        claim = _add_claim(graph, text="本文将解释线粒体通过细胞呼吸产生 ATP。")
        evidence = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        _add_citation(graph, claim, evidence)

        expression_service = ExpressionService(
            claim_service=_FakeClaimService(graph),
            invalidation_service=services["invalidation_service"],
            workflow_service=services["workflow_service"],
        )
        subject = _subject("account-alice")

        request = ExpressionDraftRequest(brief=_brief(), graph_id=graph.graph_id)
        result_a = expression_service.create_draft_and_store(subject, request)
        draft_a = result_a.draft

        # Apply the suggested patch to version A, creating a manual modification.
        assert draft_a.pending_patches
        patch = draft_a.pending_patches[0]
        expression_service.apply_revision_patch(
            subject,
            draft_a.draft_id,
            patch.patch_id,
            ApplyRevisionPatchRequest(action=PatchAction.ACCEPT),
        )

        request_b = ExpressionDraftRequest(
            brief=_brief(brief_id="brief-2"), graph_id=graph.graph_id
        )
        result_b = expression_service.create_draft_and_store(subject, request_b)
        draft_b = result_b.draft

        comparison = expression_service.compare_versions(
            subject,
            CompareVersionsRequest(draft_id_a=draft_a.draft_id, draft_id_b=draft_b.draft_id),
        )

        fields = {d.field for d in comparison.differences}
        assert VersionDifferenceField.SPAN_TEXT in fields
        assert VersionDifferenceField.APPLIED_PATCHES in fields

    def test_compare_versions_detects_human_decision_changes(
        self, services: dict[str, Any]
    ) -> None:
        graph = _make_graph()
        claim = _add_claim(graph, text="线粒体通过细胞呼吸产生 ATP。")
        evidence = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        _add_citation(graph, claim, evidence)

        expression_service = ExpressionService(
            claim_service=_FakeClaimService(graph),
            invalidation_service=services["invalidation_service"],
            workflow_service=services["workflow_service"],
        )
        subject = _subject("account-alice")

        request = ExpressionDraftRequest(brief=_brief(), graph_id=graph.graph_id)
        result_a = expression_service.create_draft_and_store(subject, request)
        draft_a = result_a.draft
        expression_service.approve_artifact(
            subject, draft_a.draft_id, ApproveArtifactRequest(reason="内容准确。")
        )

        request_b = ExpressionDraftRequest(
            brief=_brief(brief_id="brief-2"), graph_id=graph.graph_id
        )
        result_b = expression_service.create_draft_and_store(subject, request_b)
        draft_b = result_b.draft
        expression_service.approve_artifact(
            subject, draft_b.draft_id,
            ApproveArtifactRequest(reason="内容准确。", decision=HumanDecisionType.REJECT),
        )

        comparison = expression_service.compare_versions(
            subject,
            CompareVersionsRequest(draft_id_a=draft_a.draft_id, draft_id_b=draft_b.draft_id),
        )

        fields = {d.field for d in comparison.differences}
        assert VersionDifferenceField.HUMAN_DECISIONS in fields


class TestReleaseGate:
    def test_approval_required_for_release_eligibility(
        self, services: dict[str, Any]
    ) -> None:
        graph = _make_graph()
        claim = _add_claim(graph, text="线粒体通过细胞呼吸产生 ATP。")
        evidence = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        _add_citation(graph, claim, evidence)

        expression_service = ExpressionService(
            claim_service=_FakeClaimService(graph),
            invalidation_service=services["invalidation_service"],
            workflow_service=services["workflow_service"],
        )
        subject = _subject("account-alice")

        request = ExpressionDraftRequest(brief=_brief(), graph_id=graph.graph_id)
        result = expression_service.create_draft_and_store(subject, request)
        draft = result.draft

        eligibility = expression_service.evaluate_release_eligibility(subject, draft.draft_id)

        assert eligibility.passed is False
        assert eligibility.status == ReleaseEligibilityStatus.WAITING_APPROVAL
        assert ReleaseGateCheck.ARTIFACT_APPROVED in eligibility.failed_checks

    def test_workflow_success_required_when_run_linked(
        self, services: dict[str, Any]
    ) -> None:
        graph = _make_graph()
        claim = _add_claim(graph, text="线粒体通过细胞呼吸产生 ATP。")
        evidence = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        _add_citation(graph, claim, evidence)

        workflow_service: WorkflowService = services["workflow_service"]
        expression_service = ExpressionService(
            claim_service=_FakeClaimService(graph),
            invalidation_service=services["invalidation_service"],
            workflow_service=workflow_service,
        )
        subject = _subject("account-alice")

        # Create a workflow run that has not yet succeeded.
        order = WorkOrder(
            workflow_name="expression_task",
            workflow_version="1",
            project_id="project-1",
            objective="生成表达草稿",
            success_criteria="草稿通过表达门",
            risk_statement="低风险",
        )
        projection = workflow_service.submit_work_order(subject.account_id, order)
        workflow_service.confirm_work_order(subject.account_id, projection.run_id)
        run_id = projection.run_id

        request = ExpressionDraftRequest(
            brief=_brief(), graph_id=graph.graph_id, run_id=run_id
        )
        result = expression_service.create_draft_and_store(subject, request)
        draft = result.draft
        expression_service.approve_artifact(
            subject, draft.draft_id, ApproveArtifactRequest(reason="内容准确。")
        )

        eligibility = expression_service.evaluate_release_eligibility(subject, draft.draft_id)

        assert eligibility.passed is False
        assert eligibility.status == ReleaseEligibilityStatus.WAITING_WORKFLOW
        assert ReleaseGateCheck.WORKFLOW_SUCCEEDED in eligibility.failed_checks

    def test_release_eligible_when_approved_and_run_succeeded(
        self, services: dict[str, Any]
    ) -> None:
        graph = _make_graph()
        claim = _add_claim(graph, text="线粒体通过细胞呼吸产生 ATP。")
        evidence = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        _add_citation(graph, claim, evidence)

        workflow_service: WorkflowService = services["workflow_service"]
        expression_service = ExpressionService(
            claim_service=_FakeClaimService(graph),
            invalidation_service=services["invalidation_service"],
            workflow_service=workflow_service,
        )
        subject = _subject("account-alice")

        order = WorkOrder(
            workflow_name="expression_task",
            workflow_version="1",
            project_id="project-1",
            objective="生成表达草稿",
            success_criteria="草稿通过表达门",
            risk_statement="低风险",
        )
        projection = workflow_service.submit_work_order(subject.account_id, order)
        workflow_service.confirm_work_order(subject.account_id, projection.run_id)
        workflow_service.advance_run(subject.account_id, projection.run_id)
        run_id = projection.run_id

        request = ExpressionDraftRequest(
            brief=_brief(), graph_id=graph.graph_id, run_id=run_id
        )
        result = expression_service.create_draft_and_store(subject, request)
        draft = result.draft
        expression_service.approve_artifact(
            subject, draft.draft_id, ApproveArtifactRequest(reason="内容准确。")
        )

        eligibility = expression_service.evaluate_release_eligibility(subject, draft.draft_id)

        assert eligibility.passed is True
        assert eligibility.status == ReleaseEligibilityStatus.ELIGIBLE
        assert ReleaseGateCheck.ARTIFACT_APPROVED not in eligibility.failed_checks
        assert ReleaseGateCheck.WORKFLOW_SUCCEEDED not in eligibility.failed_checks

    def test_blocking_style_findings_block_release_even_when_approved(
        self, services: dict[str, Any]
    ) -> None:
        """A claim with scientific-overreach wording generates a BLOCKING style
        finding; the release gate must reject even when the artifact is approved."""
        graph = _make_graph()
        _add_claim(graph, text="实验证明该方法会导致线粒体功能异常。")
        evidence = _add_evidence(graph, graph.claims[0], EvidenceRelation.SUPPORTS)
        _add_citation(graph, graph.claims[0], evidence)

        expression_service = ExpressionService(
            claim_service=_FakeClaimService(graph),
            invalidation_service=services["invalidation_service"],
            workflow_service=services["workflow_service"],
        )
        subject = _subject("account-alice")

        request = ExpressionDraftRequest(brief=_brief(), graph_id=graph.graph_id)
        result = expression_service.create_draft_and_store(subject, request)
        draft = result.draft

        expression_service.approve_artifact(
            subject, draft.draft_id, ApproveArtifactRequest(reason="内容准确。")
        )

        eligibility = expression_service.evaluate_release_eligibility(subject, draft.draft_id)

        assert eligibility.passed is False
        assert ReleaseGateCheck.NO_BLOCKING_STYLE_FINDINGS in eligibility.failed_checks

    def test_open_human_todos_appear_in_failed_checks(
        self, services: dict[str, Any]
    ) -> None:
        """When a run linked to the draft has open human todos, the release gate
        must include NO_OPEN_HUMAN_TODOS in its failed checks.  The high-level
        status shows WAITING_WORKFLOW because the run hasn't succeeded yet."""
        graph = _make_graph()
        claim = _add_claim(graph, text="线粒体通过细胞呼吸产生 ATP。")
        evidence = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        _add_citation(graph, claim, evidence)

        workflow_service: WorkflowService = services["workflow_service"]
        workflow_service.register_workflow(
            name="human_gated_run",
            version="1",
            nodes=[
                {"node_id": "review", "node_name": "人工审查", "human_gate": True},
            ],
            terminal_states=["succeeded", "blocked", "cancelled"],
        )
        expression_service = ExpressionService(
            claim_service=_FakeClaimService(graph),
            invalidation_service=services["invalidation_service"],
            workflow_service=workflow_service,
        )
        subject = _subject("account-alice")

        order = WorkOrder(
            workflow_name="human_gated_run",
            workflow_version="1",
            project_id="project-1",
            objective="生成表达草稿",
            success_criteria="通过审查",
            risk_statement="低风险",
        )
        projection = workflow_service.submit_work_order(subject.account_id, order)
        workflow_service.confirm_work_order(subject.account_id, projection.run_id)
        run_id = projection.run_id

        request = ExpressionDraftRequest(
            brief=_brief(), graph_id=graph.graph_id, run_id=run_id, project_id="project-1"
        )
        result = expression_service.create_draft_and_store(subject, request)
        draft = result.draft
        expression_service.approve_artifact(
            subject, draft.draft_id, ApproveArtifactRequest(reason="内容准确。")
        )

        eligibility = expression_service.evaluate_release_eligibility(subject, draft.draft_id)

        assert eligibility.passed is False
        assert ReleaseGateCheck.NO_OPEN_HUMAN_TODOS in eligibility.failed_checks

    def test_upstream_source_revocation_revokes_release_eligibility(
        self, services: dict[str, Any]
    ) -> None:
        graph = _make_graph(project_id="project-1")
        claim = _add_claim(graph, text="线粒体通过细胞呼吸产生 ATP。")
        evidence = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS, document_id="doc-1")
        _add_citation(graph, claim, evidence, source_id="source-1")

        invalidation_service: InvalidationService = services["invalidation_service"]
        workflow_service: WorkflowService = services["workflow_service"]
        expression_service = ExpressionService(
            claim_service=_FakeClaimService(graph),
            invalidation_service=invalidation_service,
            workflow_service=workflow_service,
        )
        subject = _subject("account-alice")

        order = WorkOrder(
            workflow_name="expression_task",
            workflow_version="1",
            project_id="project-1",
            objective="生成表达草稿",
            success_criteria="草稿通过表达门",
            risk_statement="低风险",
        )
        projection = workflow_service.submit_work_order(subject.account_id, order)
        workflow_service.confirm_work_order(subject.account_id, projection.run_id)
        workflow_service.advance_run(subject.account_id, projection.run_id)
        run_id = projection.run_id

        request = ExpressionDraftRequest(
            brief=_brief(), graph_id=graph.graph_id, run_id=run_id, project_id="project-1"
        )
        result = expression_service.create_draft_and_store(subject, request)
        draft = result.draft
        expression_service.approve_artifact(
            subject, draft.draft_id, ApproveArtifactRequest(reason="内容准确。")
        )

        # Revoke the upstream source.
        source_ref = ObjectRef(
            domain=ObjectDomain.SHARED_PROJECT,
            owner_id="project-1",
            object_id="source-1",
            version=1,
        )
        invalidation_service.record_invalidation_event(
            subject,
            source_ref,
            InvalidationEventType.SOURCE_RETRACTED,
            "来源被撤回",
        )

        eligibility = expression_service.evaluate_release_eligibility(subject, draft.draft_id)

        assert eligibility.passed is False
        assert eligibility.status == ReleaseEligibilityStatus.UPSTREAM_INVALIDATED
        assert ReleaseGateCheck.UPSTREAM_OBJECTS_ACTIVE in eligibility.failed_checks
        assert any(ref.object_id == "source-1" for ref in eligibility.upstream_invalid_object_refs)


class TestPublish:
    def test_publish_creates_event_only_when_eligible(
        self, services: dict[str, Any]
    ) -> None:
        graph = _make_graph()
        claim = _add_claim(graph, text="线粒体通过细胞呼吸产生 ATP。")
        evidence = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        _add_citation(graph, claim, evidence)

        workflow_service: WorkflowService = services["workflow_service"]
        expression_service = ExpressionService(
            claim_service=_FakeClaimService(graph),
            invalidation_service=services["invalidation_service"],
            workflow_service=workflow_service,
        )
        subject = _subject("account-alice")

        order = WorkOrder(
            workflow_name="expression_task",
            workflow_version="1",
            project_id="project-1",
            objective="生成表达草稿",
            success_criteria="草稿通过表达门",
            risk_statement="低风险",
        )
        projection = workflow_service.submit_work_order(subject.account_id, order)
        workflow_service.confirm_work_order(subject.account_id, projection.run_id)
        workflow_service.advance_run(subject.account_id, projection.run_id)
        run_id = projection.run_id

        request = ExpressionDraftRequest(
            brief=_brief(), graph_id=graph.graph_id, run_id=run_id
        )
        result = expression_service.create_draft_and_store(subject, request)
        draft = result.draft
        expression_service.approve_artifact(
            subject, draft.draft_id, ApproveArtifactRequest(reason="内容准确。")
        )

        publish_result = expression_service.publish_artifact(
            subject, draft.draft_id, PublishArtifactRequest(run_id=run_id)
        )

        assert publish_result.event.draft_id == draft.draft_id
        assert publish_result.event.account_id == subject.account_id
        assert publish_result.event.release_gate_result.passed is True
        assert publish_result.draft.artifact_trust_status == "approved"

    def test_publish_blocked_when_not_approved(
        self, services: dict[str, Any]
    ) -> None:
        graph = _make_graph()
        claim = _add_claim(graph, text="线粒体通过细胞呼吸产生 ATP。")
        evidence = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        _add_citation(graph, claim, evidence)

        expression_service = ExpressionService(
            claim_service=_FakeClaimService(graph),
            invalidation_service=services["invalidation_service"],
            workflow_service=services["workflow_service"],
        )
        subject = _subject("account-alice")

        request = ExpressionDraftRequest(brief=_brief(), graph_id=graph.graph_id)
        result = expression_service.create_draft_and_store(subject, request)
        draft = result.draft

        with pytest.raises(ExpressionServiceError, match="发布资格"):
            expression_service.publish_artifact(
                subject, draft.draft_id, PublishArtifactRequest()
            )


class TestExpressionGateStillBlocksRelease:
    def test_expression_gate_failure_blocks_release_even_if_approved(
        self, services: dict[str, Any]
    ) -> None:
        graph = _make_graph()
        # Key claim without evidence blocks the expression gate.
        _add_claim(graph, text="A 是正确的。", importance=ClaimImportance.KEY)

        expression_service = ExpressionService(
            claim_service=_FakeClaimService(graph),
            invalidation_service=services["invalidation_service"],
            workflow_service=services["workflow_service"],
        )
        subject = _subject("account-alice")

        request = ExpressionDraftRequest(brief=_brief(), graph_id=graph.graph_id)
        result = expression_service.create_draft_and_store(subject, request)
        draft = result.draft
        assert result.gate.passed is False

        expression_service.approve_artifact(
            subject, draft.draft_id, ApproveArtifactRequest(reason="内容准确。")
        )

        eligibility = expression_service.evaluate_release_eligibility(subject, draft.draft_id)

        assert eligibility.passed is False
        assert eligibility.status == ReleaseEligibilityStatus.BLOCKED
        assert ReleaseGateCheck.EXPRESSION_GATE_PASSED in eligibility.failed_checks
        assert ReleaseGateCheck.ARTIFACT_APPROVED not in eligibility.failed_checks
