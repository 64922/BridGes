"""Unit tests for the expression draft service (T025).

The seam under test: an expression task brief and a claim graph are compiled into
a fact-lock-bound draft. The draft must bind every important judgment to its
claim, evidence, citation and fact locks; personalization must come from the
memory slice and must not change fact strength; missing evidence, authorization
or genre duty must produce an explainable block.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from science_companion.contracts.expression import (
    ExpressionBrief,
    ExpressionDraftRequest,
    ExpressionDraftStatus,
    ExpressionGateCheck,
    Genre,
    RiskTier,
)
from science_companion.contracts.identity import AuthMethod, SubjectContext
from science_companion.contracts.profiles import (
    CandidateDecision,
    DecisionType,
    ProfileObservationCreateRequest,
    ProfileSensitivityClass,
    ProfileSignalKind,
    ProfileSourceType,
)
from science_companion.contracts.science import (
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
from science_companion.expression import ExpressionService, ExpressionServiceError
from science_companion.profiles import InMemoryProfileRepository, ProfileService
from science_companion.science import (
    ClaimEvidenceService,
    ScienceSearchService,
    ScienceSourceService,
)
from science_companion.science.fact_lock import apply_honest_degradation, compile_fact_locks


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
    had_candidates: bool = True,
) -> ClaimGraph:
    return ClaimGraph(
        graph_id="graph-1",
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
def profile_service() -> ProfileService:
    return ProfileService(repository=InMemoryProfileRepository())


class TestExpressionDraftBinding:
    def test_draft_binds_claims_citations_and_fact_locks(self) -> None:
        graph = _make_graph()
        claim = _add_claim(graph, text="线粒体通过细胞呼吸产生 ATP。")
        evidence = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        _add_citation(graph, claim, evidence)

        service = ExpressionService(claim_service=_FakeClaimService(graph))
        brief = _brief(required_claim_ids=[claim.claim_id])
        request = ExpressionDraftRequest(brief=brief, graph_id=graph.graph_id)

        result = service.create_draft(_subject("account-alice"), request)

        draft = result.draft
        assert draft.status == ExpressionDraftStatus.DRAFTED
        assert result.gate.passed is True
        assert draft.fact_lock_set_id

        claim_spans = [s for s in draft.spans if claim.claim_id in s.claim_ids]
        assert claim_spans
        span = claim_spans[0]
        assert span.citation_ids == claim.citation_ids
        assert span.fact_lock_ids
        assert span.argument_node_ids

    def test_draft_includes_argument_plan(self) -> None:
        graph = _make_graph()
        claim = _add_claim(graph, text="X 导致 Y。", claim_type=ClaimType.CAUSAL)
        evidence = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        _add_evidence(graph, claim, EvidenceRelation.LIMITS)
        _add_citation(graph, claim, evidence)

        service = ExpressionService(claim_service=_FakeClaimService(graph))
        request = ExpressionDraftRequest(
            brief=_brief(required_claim_ids=[claim.claim_id]), graph_id=graph.graph_id
        )

        result = service.create_draft(_subject("account-alice"), request)

        roles = {node.role for node in result.draft.argument_plan.nodes}
        assert "question" in roles
        assert "claim" in roles
        assert "limitation" in roles
        assert "action" in roles


class TestExpressionGateBlocking:
    def test_missing_success_criteria_blocks(self) -> None:
        graph = _make_graph()
        claim = _add_claim(graph, text="A 是正确的。")
        evidence = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        _add_citation(graph, claim, evidence)

        service = ExpressionService(claim_service=_FakeClaimService(graph))
        brief = _brief(success_criteria=[], required_claim_ids=[])
        request = ExpressionDraftRequest(brief=brief, graph_id=graph.graph_id)

        result = service.create_draft(_subject("account-alice"), request)

        assert result.draft.status == ExpressionDraftStatus.BLOCKED
        assert result.gate.passed is False
        assert ExpressionGateCheck.BRIEF_COMPLETE in result.gate.failed_checks

    def test_missing_required_claim_blocks(self) -> None:
        graph = _make_graph()
        claim = _add_claim(graph, text="A 是正确的。")
        evidence = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        _add_citation(graph, claim, evidence)

        service = ExpressionService(claim_service=_FakeClaimService(graph))
        brief = _brief(required_claim_ids=["missing-claim"])
        request = ExpressionDraftRequest(brief=brief, graph_id=graph.graph_id)

        result = service.create_draft(_subject("account-alice"), request)

        assert result.draft.status == ExpressionDraftStatus.BLOCKED
        assert ExpressionGateCheck.REQUIRED_CLAIMS_PRESENT in result.gate.failed_checks
        assert "missing-claim" in result.gate.blocked_claim_ids

    def test_key_claim_without_evidence_blocks(self) -> None:
        graph = _make_graph()
        _add_claim(graph, text="A 是正确的。", importance=ClaimImportance.KEY)

        service = ExpressionService(claim_service=_FakeClaimService(graph))
        brief = _brief()
        request = ExpressionDraftRequest(brief=brief, graph_id=graph.graph_id)

        result = service.create_draft(_subject("account-alice"), request)

        assert result.draft.status == ExpressionDraftStatus.BLOCKED
        assert ExpressionGateCheck.KEY_CLAIMS_FACT_LOCKED in result.gate.failed_checks
        assert ExpressionGateCheck.SOURCE_EVIDENCE_PRESENT in result.gate.failed_checks

    def test_high_risk_conflicted_graph_waits_human(self) -> None:
        graph = _make_graph(status=ClaimTrustStatus.CONFLICTED)
        claim = _add_claim(graph, text="X 导致 Y。", claim_type=ClaimType.CAUSAL)
        support = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        refute = _add_evidence(graph, claim, EvidenceRelation.REFUTES)
        _add_citation(graph, claim, support)
        _add_citation(graph, claim, refute)

        service = ExpressionService(claim_service=_FakeClaimService(graph))
        brief = _brief(risk_tier=RiskTier.HIGH, required_claim_ids=[claim.claim_id])
        request = ExpressionDraftRequest(brief=brief, graph_id=graph.graph_id)

        result = service.create_draft(_subject("account-alice"), request)

        assert result.draft.status == ExpressionDraftStatus.WAITING_HUMAN
        assert result.gate.passed is False
        assert ExpressionGateCheck.RISK_TIER_HUMAN_REVIEW in result.gate.failed_checks


class TestMemorySlicePersonalization:
    def test_personalization_uses_memory_slice_without_changing_strength(
        self, profile_service: ProfileService
    ) -> None:
        account_id = "account-alice"
        observation = profile_service.record_observation(
            ProfileObservationCreateRequest(
                owner_account_id=account_id,
                source_type=ProfileSourceType.EXPLICIT_STATEMENT,
                source_ref="conversation-1",
                source_span_or_event="msg-1",
                scene="expression_task",
                purpose="expression_preference",
                observed_content="Prefer short answers.",
                signal_kind=ProfileSignalKind.PREFERENCE,
                extractor_and_version="rule-1",
                reliability_factors=["explicit_statement"],
                sensitivity_class=ProfileSensitivityClass.PREFERENCE,
                retention_policy="account_lifetime",
            )
        )
        candidate = profile_service.propose_candidate(
            account_id,
            canonical_dimension="expression_brevity",
            value_or_rule="short_answers",
            applicable_scenes=["expression_task"],
            supporting_observation_ids=[observation.observation_id],
        )
        profile_service.decide_candidate(
            account_id,
            candidate.candidate_id,
            CandidateDecision(decision=DecisionType.ACCEPT, reason="Confirmed."),
        )
        slice_ = profile_service.compile_memory_slice(
            account_id, purpose="expression_task", run_id="run-1"
        )

        graph = _make_graph()
        claim = _add_claim(graph, text="线粒体通过细胞呼吸产生 ATP。")
        evidence = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        _add_citation(graph, claim, evidence)

        service = ExpressionService(
            claim_service=_FakeClaimService(graph),
            profile_service=profile_service,
        )
        brief = _brief(
            required_claim_ids=[claim.claim_id],
            memory_slice_id=slice_.slice_id,
        )
        request = ExpressionDraftRequest(
            brief=brief,
            graph_id=graph.graph_id,
            memory_slice_id=slice_.slice_id,
        )

        result = service.create_draft(_subject(account_id), request)

        assert result.draft.status == ExpressionDraftStatus.DRAFTED
        assert result.draft.memory_slice_id == slice_.slice_id
        assert result.draft.personalization_note is not None
        assert "expression_brevity" in result.draft.personalization_note
        # Wording ceiling comes from the claim graph, not from the memory slice.
        assert result.draft.wording_strength_ceiling.value == "high"

    def test_unusable_memory_slice_blocks(self, profile_service: ProfileService) -> None:
        account_id = "account-alice"
        graph = _make_graph()
        claim = _add_claim(graph, text="A 是正确的。")
        evidence = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        _add_citation(graph, claim, evidence)

        service = ExpressionService(
            claim_service=_FakeClaimService(graph),
            profile_service=profile_service,
        )
        brief = _brief(
            required_claim_ids=[claim.claim_id],
            memory_slice_id="nonexistent-slice",
        )
        request = ExpressionDraftRequest(
            brief=brief,
            graph_id=graph.graph_id,
            memory_slice_id="nonexistent-slice",
        )

        with pytest.raises(ExpressionServiceError, match="不存在"):
            service.create_draft(_subject(account_id), request)
