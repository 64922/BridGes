"""Human-flavor diagnostics and per-patch revision loop for T028.

The seam under test: a generated expression draft receives a Chinese expression
diagnostic report with concrete text fragments, issue types and reasons. Users
can accept, reject or rewrite individual patches; each action records a fact-
lock invariance check. User feedback is routed to the correct downstream object,
and AI-detector scores are never used as pass/fail gates.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from bridges.contracts.expression import (
    ApplyRevisionPatchRequest,
    ExpressionBrief,
    ExpressionDraftRequest,
    ExpressionDraftStatus,
    ExpressionGateCheck,
    Genre,
    PatchAction,
    RiskTier,
    StyleDiagnosticFinding,
    StyleDiagnosticRequest,
    StyleDiagnosticSeverity,
    StyleIssueType,
    SubmitExpressionFeedbackRequest,
    UserFeedbackTarget,
)
from bridges.contracts.identity import AuthMethod, SubjectContext
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
from bridges.expression import ExpressionService, ExpressionServiceError
from bridges.expression.style import (
    build_style_policy,
    run_diagnostic,
    suggest_patch_for_finding,
)
from bridges.science import (
    ClaimEvidenceService,
    ScienceSearchService,
    ScienceSourceService,
)
from bridges.science.fact_lock import apply_honest_degradation, compile_fact_locks


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
) -> ClaimGraph:
    return ClaimGraph(
        graph_id="graph-style",
        account_id="account-alice",
        project_id="project-1",
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
) -> Evidence:
    evidence = Evidence(
        evidence_id=f"ev-{len(graph.evidence)}",
        claim_id=claim.claim_id,
        document_id="doc-1",
        chunk_ids=["chunk-1"],
        relation=relation,
        valid_from=_now(),
    )
    claim.evidence_ids.append(evidence.evidence_id)
    graph.evidence.append(evidence)
    return evidence


def _add_citation(graph: ClaimGraph, claim: Claim, evidence: Evidence) -> Citation:
    citation = Citation(
        citation_id=f"cite-{len(graph.citations)}",
        evidence_id=evidence.evidence_id,
        claim_id=claim.claim_id,
        locator=CitationLocator(),
        identifier_snapshot={"source_id": "source-1", "document_id": "doc-1"},
        accessed_at=_now(),
        verification_status=CitationVerificationStatus.VERIFIED,
    )
    claim.citation_ids.append(citation.citation_id)
    graph.citations.append(citation)
    return citation


class _FakeClaimService(ClaimEvidenceService):
    """In-memory stand-in for claim evidence service in style tests."""

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


def _brief(**overrides: object) -> ExpressionBrief:
    defaults: dict[str, Any] = {
        "brief_id": "brief-style",
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


class TestStyleDiagnosticReport:
    def test_diagnostic_finding_anchors_to_span_text_and_issue_type(self) -> None:
        graph = _make_graph()
        claim = _add_claim(graph, text="线粒体通过细胞呼吸产生 ATP。")
        evidence = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        _add_citation(graph, claim, evidence)

        service = ExpressionService(claim_service=_FakeClaimService(graph))
        request = ExpressionDraftRequest(
            brief=_brief(required_claim_ids=[claim.claim_id]),
            graph_id=graph.graph_id,
        )
        result = service.create_draft(_subject("account-alice"), request)

        draft = result.draft
        assert draft.style_policy is not None
        assert draft.style_diagnostic_report is not None
        report = draft.style_diagnostic_report
        assert report.draft_id == draft.draft_id
        assert report.ai_detector_used_as_gate is False

    def test_diagnostic_detects_template_pattern_and_translation_pattern(self) -> None:
        graph = _make_graph()
        claim = _add_claim(
            graph,
            text="本文将讨论线粒体。对于细胞来说，线粒体通过呼吸产生 ATP。",
        )
        evidence = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        _add_citation(graph, claim, evidence)

        service = ExpressionService(claim_service=_FakeClaimService(graph))
        request = ExpressionDraftRequest(
            brief=_brief(required_claim_ids=[claim.claim_id]),
            graph_id=graph.graph_id,
        )
        result = service.create_draft_and_store(_subject("account-alice"), request)

        report = result.draft.style_diagnostic_report
        assert report is not None
        issue_types = {f.issue_type for f in report.findings}
        assert StyleIssueType.TEMPLATE_PATTERN in issue_types
        assert StyleIssueType.TRANSLATION_PATTERN in issue_types

        template = next(
            f for f in report.findings if f.issue_type == StyleIssueType.TEMPLATE_PATTERN
        )
        assert "本文将" in template.original_text
        assert template.reason
        assert template.suggested_patch is not None

    def test_pending_patches_are_created_from_findings(self) -> None:
        graph = _make_graph()
        claim = _add_claim(graph, text="本文将讨论线粒体功能。")
        evidence = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        _add_citation(graph, claim, evidence)

        service = ExpressionService(claim_service=_FakeClaimService(graph))
        request = ExpressionDraftRequest(
            brief=_brief(required_claim_ids=[claim.claim_id]),
            graph_id=graph.graph_id,
        )
        result = service.create_draft(_subject("account-alice"), request)

        draft = result.draft
        assert draft.pending_patches
        for patch in draft.pending_patches:
            assert patch.target_span_id
            assert patch.original_text
            assert patch.patched_text != patch.original_text
            assert patch.issue_type


class TestRevisionPatchApplication:
    def test_accept_patch_preserves_fact_locks_citations_and_strength(self) -> None:
        graph = _make_graph()
        claim = _add_claim(graph, text="本文将讨论线粒体功能。")
        evidence = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        _add_citation(graph, claim, evidence)

        service = ExpressionService(claim_service=_FakeClaimService(graph))
        request = ExpressionDraftRequest(
            brief=_brief(required_claim_ids=[claim.claim_id]),
            graph_id=graph.graph_id,
        )
        result = service.create_draft_and_store(_subject("account-alice"), request)
        draft = result.draft
        patch = draft.pending_patches[0]

        apply_result = service.apply_revision_patch(
            _subject("account-alice"),
            draft.draft_id,
            patch.patch_id,
            ApplyRevisionPatchRequest(action=PatchAction.ACCEPT),
        )

        invariance = apply_result.invariance
        assert invariance.claim_ids_preserved is True
        assert invariance.citation_ids_preserved is True
        assert invariance.fact_lock_ids_preserved is True
        assert invariance.wording_strength_ceiling_preserved is True
        assert invariance.passed is True

        updated = apply_result.draft
        assert patch.patch_id not in {p.patch_id for p in updated.pending_patches}
        assert patch.patch_id in {p.patch_id for p in updated.applied_patches}

    def test_reject_patch_moves_to_rejected_list(self) -> None:
        graph = _make_graph()
        claim = _add_claim(graph, text="本文将讨论线粒体功能。")
        evidence = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        _add_citation(graph, claim, evidence)

        service = ExpressionService(claim_service=_FakeClaimService(graph))
        request = ExpressionDraftRequest(
            brief=_brief(required_claim_ids=[claim.claim_id]),
            graph_id=graph.graph_id,
        )
        result = service.create_draft_and_store(_subject("account-alice"), request)
        draft = result.draft
        patch = draft.pending_patches[0]

        apply_result = service.apply_revision_patch(
            _subject("account-alice"),
            draft.draft_id,
            patch.patch_id,
            ApplyRevisionPatchRequest(action=PatchAction.REJECT),
        )

        updated = apply_result.draft
        assert patch.patch_id not in {p.patch_id for p in updated.pending_patches}
        assert patch.patch_id in {p.patch_id for p in updated.rejected_patches}

    def test_rewrite_patch_with_user_text_updates_span(self) -> None:
        graph = _make_graph()
        claim = _add_claim(graph, text="本文将讨论线粒体功能。")
        evidence = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        _add_citation(graph, claim, evidence)

        service = ExpressionService(claim_service=_FakeClaimService(graph))
        request = ExpressionDraftRequest(
            brief=_brief(required_claim_ids=[claim.claim_id]),
            graph_id=graph.graph_id,
        )
        result = service.create_draft_and_store(_subject("account-alice"), request)
        draft = result.draft
        patch = draft.pending_patches[0]
        rewrite = "线粒体是细胞的能量工厂。"

        apply_result = service.apply_revision_patch(
            _subject("account-alice"),
            draft.draft_id,
            patch.patch_id,
            ApplyRevisionPatchRequest(action=PatchAction.REWRITE, rewrite_text=rewrite),
        )

        updated_span = next(
            s for s in apply_result.draft.spans if s.span_id == patch.target_span_id
        )
        assert updated_span.text == rewrite
        applied = next(
            p for p in apply_result.draft.applied_patches if p.patch_id == patch.patch_id
        )
        assert applied.user_rewrite == rewrite

    def test_patch_violating_fact_lock_is_blocked(self) -> None:
        graph = _make_graph()
        claim = _add_claim(graph, text="本文将讨论线粒体功能。")
        evidence = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        _add_citation(graph, claim, evidence)

        service = ExpressionService(claim_service=_FakeClaimService(graph))
        request = ExpressionDraftRequest(
            brief=_brief(required_claim_ids=[claim.claim_id]),
            graph_id=graph.graph_id,
        )
        result = service.create_draft_and_store(_subject("account-alice"), request)
        draft = result.draft
        patch = draft.pending_patches[0]

        # Rewriting with a number that was not in the original violates numeric
        # value invariance and must be rejected.
        with pytest.raises(ExpressionServiceError, match="事实锁"):
            service.apply_revision_patch(
                _subject("account-alice"),
                draft.draft_id,
                patch.patch_id,
                ApplyRevisionPatchRequest(
                    action=PatchAction.REWRITE,
                    rewrite_text="线粒体产生 999 ATP。",
                ),
            )


class TestUserFeedbackRouting:
    def test_factual_correction_routes_to_fact_review(self) -> None:
        graph = _make_graph()
        claim = _add_claim(graph, text="线粒体通过细胞呼吸产生 ATP。")
        evidence = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        _add_citation(graph, claim, evidence)

        service = ExpressionService(claim_service=_FakeClaimService(graph))
        request = ExpressionDraftRequest(
            brief=_brief(required_claim_ids=[claim.claim_id]),
            graph_id=graph.graph_id,
        )
        result = service.create_draft_and_store(_subject("account-alice"), request)
        draft = result.draft

        fb_result = service.submit_user_feedback(
            _subject("account-alice"),
            draft.draft_id,
            SubmitExpressionFeedbackRequest(
                target=UserFeedbackTarget.CURRENT_VERSION,
                message="这个数字不对，文献不支持。",
                referenced_claim_id=claim.claim_id,
            ),
        )

        assert fb_result.routed_to == UserFeedbackTarget.FACT_REVIEW
        assert fb_result.feedback_id
        logged = fb_result.draft.feedback_log[-1]
        assert logged.target == UserFeedbackTarget.FACT_REVIEW

    def test_learning_feedback_routes_to_learning_record(self) -> None:
        graph = _make_graph()
        claim = _add_claim(graph, text="线粒体通过细胞呼吸产生 ATP。")
        evidence = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        _add_citation(graph, claim, evidence)

        service = ExpressionService(claim_service=_FakeClaimService(graph))
        request = ExpressionDraftRequest(
            brief=_brief(required_claim_ids=[claim.claim_id]),
            graph_id=graph.graph_id,
        )
        result = service.create_draft_and_store(_subject("account-alice"), request)

        fb_result = service.submit_user_feedback(
            _subject("account-alice"),
            result.draft.draft_id,
            SubmitExpressionFeedbackRequest(
                target=UserFeedbackTarget.CURRENT_VERSION,
                message="我没有理解这个练习，需要更多解释。",
            ),
        )

        assert fb_result.routed_to == UserFeedbackTarget.LEARNING_RECORD

    def test_preference_feedback_routes_to_candidate_preference(self) -> None:
        graph = _make_graph()
        claim = _add_claim(graph, text="线粒体通过细胞呼吸产生 ATP。")
        evidence = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        _add_citation(graph, claim, evidence)

        service = ExpressionService(claim_service=_FakeClaimService(graph))
        request = ExpressionDraftRequest(
            brief=_brief(required_claim_ids=[claim.claim_id]),
            graph_id=graph.graph_id,
        )
        result = service.create_draft_and_store(_subject("account-alice"), request)

        fb_result = service.submit_user_feedback(
            _subject("account-alice"),
            result.draft.draft_id,
            SubmitExpressionFeedbackRequest(
                target=UserFeedbackTarget.CURRENT_VERSION,
                message="以后科普文案少用标题，我喜欢段落式。",
            ),
        )

        assert fb_result.routed_to == UserFeedbackTarget.CANDIDATE_PREFERENCE
        logged = fb_result.draft.feedback_log[-1]
        assert logged.creates_candidate_preference is True

    def test_style_feedback_can_stay_on_current_version(self) -> None:
        graph = _make_graph()
        claim = _add_claim(graph, text="线粒体通过细胞呼吸产生 ATP。")
        evidence = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        _add_citation(graph, claim, evidence)

        service = ExpressionService(claim_service=_FakeClaimService(graph))
        request = ExpressionDraftRequest(
            brief=_brief(required_claim_ids=[claim.claim_id]),
            graph_id=graph.graph_id,
        )
        result = service.create_draft_and_store(_subject("account-alice"), request)

        fb_result = service.submit_user_feedback(
            _subject("account-alice"),
            result.draft.draft_id,
            SubmitExpressionFeedbackRequest(
                target=UserFeedbackTarget.CURRENT_VERSION,
                message="这一段的节奏现在可以了。",
            ),
        )

        assert fb_result.routed_to == UserFeedbackTarget.CURRENT_VERSION


class TestAIDetectorNotGate:
    def test_ai_detector_score_is_not_used_as_gate(self) -> None:
        graph = _make_graph()
        claim = _add_claim(graph, text="本文将讨论线粒体功能。")
        evidence = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        _add_citation(graph, claim, evidence)

        service = ExpressionService(claim_service=_FakeClaimService(graph))
        request = ExpressionDraftRequest(
            brief=_brief(required_claim_ids=[claim.claim_id]),
            graph_id=graph.graph_id,
        )
        result = service.create_draft(_subject("account-alice"), request)

        report = result.draft.style_diagnostic_report
        assert report is not None
        assert report.ai_detector_used_as_gate is False
        assert ExpressionGateCheck.AI_DETECTOR_NOT_GATE not in result.gate.failed_checks


class TestStyleDiagnosticAPIFlow:
    def test_run_style_diagnostic_repopulates_findings(self) -> None:
        graph = _make_graph()
        claim = _add_claim(graph, text="本文将讨论线粒体功能。")
        evidence = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        _add_citation(graph, claim, evidence)

        service = ExpressionService(claim_service=_FakeClaimService(graph))
        request = ExpressionDraftRequest(
            brief=_brief(required_claim_ids=[claim.claim_id]),
            graph_id=graph.graph_id,
        )
        result = service.create_draft_and_store(_subject("account-alice"), request)
        draft_id = result.draft.draft_id

        diagnostic = service.run_style_diagnostic(
            _subject("account-alice"),
            StyleDiagnosticRequest(draft_id=draft_id),
        )
        assert diagnostic.report.draft_id == draft_id
        assert diagnostic.report.findings

    def test_foreign_user_cannot_apply_patch(self) -> None:
        graph = _make_graph()
        claim = _add_claim(graph, text="本文将讨论线粒体功能。")
        evidence = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        _add_citation(graph, claim, evidence)

        service = ExpressionService(claim_service=_FakeClaimService(graph))
        request = ExpressionDraftRequest(
            brief=_brief(required_claim_ids=[claim.claim_id]),
            graph_id=graph.graph_id,
        )
        result = service.create_draft_and_store(_subject("account-alice"), request)
        patch = result.draft.pending_patches[0]

        with pytest.raises(ExpressionServiceError, match="访问权限"):
            service.apply_revision_patch(
                _subject("account-bob"),
                result.draft.draft_id,
                patch.patch_id,
                ApplyRevisionPatchRequest(action=PatchAction.ACCEPT),
            )


class TestStyleEngineHelpers:
    def test_build_style_policy_includes_genre_forbidden_phrases(self) -> None:
        graph = _make_graph()
        claim = _add_claim(graph, text="线粒体通过细胞呼吸产生 ATP。")
        evidence = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        _add_citation(graph, claim, evidence)

        service = ExpressionService(claim_service=_FakeClaimService(graph))
        request = ExpressionDraftRequest(
            brief=_brief(genre=Genre.POPULAR_SCIENCE, required_claim_ids=[claim.claim_id]),
            graph_id=graph.graph_id,
        )
        result = service.create_draft(_subject("account-alice"), request)

        policy = build_style_policy(result.draft)
        assert policy.genre == Genre.POPULAR_SCIENCE
        assert "把类比当机制" in policy.forbidden_phrases

    def test_suggest_patch_returns_none_when_no_change(self) -> None:
        graph = _make_graph()
        claim = _add_claim(graph, text="线粒体通过细胞呼吸产生 ATP。")
        evidence = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        _add_citation(graph, claim, evidence)

        service = ExpressionService(claim_service=_FakeClaimService(graph))
        request = ExpressionDraftRequest(
            brief=_brief(required_claim_ids=[claim.claim_id]),
            graph_id=graph.graph_id,
        )
        result = service.create_draft(_subject("account-alice"), request)

        # Create an INFO finding with no suggested patch.

        finding = StyleDiagnosticFinding(
            finding_id="find-info",
            span_id=result.draft.spans[0].span_id,
            issue_type=StyleIssueType.RHYTHM_ISSUE,
            severity=StyleDiagnosticSeverity.INFO,
            original_text=result.draft.spans[0].text,
            reason="信息性提示。",
            suggested_patch=None,
        )
        patch = suggest_patch_for_finding(result.draft, finding)
        assert patch is None
