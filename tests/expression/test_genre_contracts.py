"""Genre-specific contract tests for T026.

The seam under test: the expression service can render the same fact-lock set as
a popular-science copy or a lecture script, expose the required structural
elements for each genre, explain why genre rules require or prohibit content,
and preserve scientific boundaries when converting between genres.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from science_companion.contracts.expression import (
    ConvertGenreRequest,
    ExpressionBrief,
    ExpressionDraftRequest,
    ExpressionDraftStatus,
    ExpressionGateCheck,
    Genre,
    GenreElementRole,
    ReviewFindingKind,
    RiskTier,
)
from science_companion.contracts.identity import AuthMethod, SubjectContext
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
) -> ClaimGraph:
    return ClaimGraph(
        graph_id="graph-genre",
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
    """In-memory stand-in for claim evidence service in genre tests."""

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
        "brief_id": "brief-genre",
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


class TestPopularScienceGenreElements:
    def test_popular_science_distinguishes_core_concept_analogy_boundary_and_action(
        self,
    ) -> None:
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

        draft = result.draft
        assert draft.status == ExpressionDraftStatus.DRAFTED
        elements = draft.popular_science_elements
        assert elements

        roles = {e.role for e in elements}
        assert GenreElementRole.CORE_CONCEPT in roles
        assert GenreElementRole.ANALOGY in roles
        assert GenreElementRole.ANALOGY_BOUNDARY in roles
        assert GenreElementRole.ACTION_RELEVANCE in roles

        boundary = next(e for e in elements if e.role == GenreElementRole.ANALOGY_BOUNDARY)
        assert boundary.boundary_note
        assert "类比" in boundary.boundary_note

        action = next(e for e in elements if e.role == GenreElementRole.ACTION_RELEVANCE)
        assert action.action_relevance

    def test_popular_science_review_report_explains_required_and_prohibited_rules(
        self,
    ) -> None:
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

        report = result.draft.review_report
        assert report is not None
        assert report.genre == Genre.POPULAR_SCIENCE

        required = [f for f in report.findings if f.kind == ReviewFindingKind.REQUIRED]
        prohibited = [f for f in report.findings if f.kind == ReviewFindingKind.PROHIBITED]
        assert required
        assert prohibited
        for finding in required + prohibited:
            assert finding.reason


class TestLectureScriptGenreElements:
    def test_lecture_script_contains_objective_prerequisite_check_and_pause(self) -> None:
        graph = _make_graph()
        claim = _add_claim(graph, text="线粒体通过细胞呼吸产生 ATP。")
        evidence = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        _add_citation(graph, claim, evidence)

        service = ExpressionService(claim_service=_FakeClaimService(graph))
        request = ExpressionDraftRequest(
            brief=_brief(
                genre=Genre.LECTURE_SCRIPT,
                deliverable_type="课程讲稿",
                required_claim_ids=[claim.claim_id],
            ),
            graph_id=graph.graph_id,
        )
        result = service.create_draft(_subject("account-alice"), request)

        draft = result.draft
        assert draft.status == ExpressionDraftStatus.DRAFTED
        elements = draft.lecture_script_elements
        assert elements

        roles = {e.role for e in elements}
        assert GenreElementRole.LEARNING_OBJECTIVE in roles
        assert GenreElementRole.PREREQUISITE in roles
        assert GenreElementRole.COMPREHENSION_CHECK in roles
        assert GenreElementRole.PRACTICE_PAUSE in roles

        check = next(e for e in elements if e.role == GenreElementRole.COMPREHENSION_CHECK)
        assert check.checkpoint_question
        assert check.expected_answer

        pause = next(e for e in elements if e.role == GenreElementRole.PRACTICE_PAUSE)
        assert pause.pause_prompt

    def test_lecture_script_review_report_explains_why_checks_are_required(self) -> None:
        graph = _make_graph()
        claim = _add_claim(graph, text="线粒体通过细胞呼吸产生 ATP。")
        evidence = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        _add_citation(graph, claim, evidence)

        service = ExpressionService(claim_service=_FakeClaimService(graph))
        request = ExpressionDraftRequest(
            brief=_brief(
                genre=Genre.LECTURE_SCRIPT,
                deliverable_type="课程讲稿",
                required_claim_ids=[claim.claim_id],
            ),
            graph_id=graph.graph_id,
        )
        result = service.create_draft(_subject("account-alice"), request)

        report = result.draft.review_report
        assert report is not None
        assert report.genre == Genre.LECTURE_SCRIPT

        check_finding = next(
            (f for f in report.findings if "理解检查" in f.rule), None
        )
        assert check_finding is not None
        assert "掌握" in check_finding.reason


class TestGenreConversionInvariance:
    def test_genre_conversion_preserves_fact_locks_citations_and_strength(self) -> None:
        graph = _make_graph()
        claim = _add_claim(graph, text="线粒体通过细胞呼吸产生 ATP。")
        evidence = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        _add_citation(graph, claim, evidence)

        service = ExpressionService(claim_service=_FakeClaimService(graph))
        pop_request = ExpressionDraftRequest(
            brief=_brief(genre=Genre.POPULAR_SCIENCE, required_claim_ids=[claim.claim_id]),
            graph_id=graph.graph_id,
        )
        pop_result = service.create_draft_and_store(_subject("account-alice"), pop_request)
        pop_draft = pop_result.draft

        convert_result = service.convert_genre(
            _subject("account-alice"),
            pop_draft.draft_id,
            ConvertGenreRequest(target_genre=Genre.LECTURE_SCRIPT),
        )
        converted = convert_result.converted_draft
        invariance = convert_result.invariance

        assert invariance.original_genre == Genre.POPULAR_SCIENCE
        assert invariance.target_genre == Genre.LECTURE_SCRIPT
        assert invariance.fact_lock_set_id_preserved is True
        assert invariance.citation_ids_preserved is True
        assert invariance.claim_ids_preserved is True
        assert invariance.wording_strength_ceiling_preserved is True
        assert invariance.passed is True

        assert converted.genre == Genre.LECTURE_SCRIPT
        assert converted.fact_lock_set_id == pop_draft.fact_lock_set_id
        assert converted.wording_strength_ceiling == pop_draft.wording_strength_ceiling

        pop_claim_ids = {cid for s in pop_draft.spans for cid in s.claim_ids}
        converted_claim_ids = {cid for s in converted.spans for cid in s.claim_ids}
        assert pop_claim_ids == converted_claim_ids

        pop_citation_ids = {cid for s in pop_draft.spans for cid in s.citation_ids}
        converted_citation_ids = {cid for s in converted.spans for cid in s.citation_ids}
        assert pop_citation_ids == converted_citation_ids

    def test_genre_conversion_changes_only_presentation_elements(self) -> None:
        graph = _make_graph()
        claim = _add_claim(graph, text="线粒体通过细胞呼吸产生 ATP。")
        evidence = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        _add_citation(graph, claim, evidence)

        service = ExpressionService(claim_service=_FakeClaimService(graph))
        pop_request = ExpressionDraftRequest(
            brief=_brief(genre=Genre.POPULAR_SCIENCE, required_claim_ids=[claim.claim_id]),
            graph_id=graph.graph_id,
        )
        pop_result = service.create_draft_and_store(_subject("account-alice"), pop_request)

        convert_result = service.convert_genre(
            _subject("account-alice"),
            pop_result.draft.draft_id,
            ConvertGenreRequest(target_genre=Genre.LECTURE_SCRIPT),
        )
        converted = convert_result.converted_draft

        assert converted.popular_science_elements == []
        assert converted.lecture_script_elements
        assert converted.review_report is not None
        assert converted.review_report.genre == Genre.LECTURE_SCRIPT

    def test_convert_genre_rejects_foreign_draft(self) -> None:
        graph = _make_graph()
        claim = _add_claim(graph, text="A 是正确的。")
        evidence = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        _add_citation(graph, claim, evidence)

        service = ExpressionService(claim_service=_FakeClaimService(graph))
        request = ExpressionDraftRequest(
            brief=_brief(genre=Genre.POPULAR_SCIENCE, required_claim_ids=[claim.claim_id]),
            graph_id=graph.graph_id,
        )
        result = service.create_draft_and_store(_subject("account-alice"), request)

        with pytest.raises(ExpressionServiceError, match="访问权限"):
            service.convert_genre(
                _subject("account-bob"),
                result.draft.draft_id,
                ConvertGenreRequest(target_genre=Genre.LECTURE_SCRIPT),
            )


class TestResearchReportGenreElements:
    def test_research_report_separates_observation_analysis_interpretation_limitation_next(
        self,
    ) -> None:
        graph = _make_graph()
        claim = _add_claim(graph, text="线粒体通过细胞呼吸产生 ATP。")
        evidence = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        _add_evidence(graph, claim, EvidenceRelation.LIMITS)
        _add_citation(graph, claim, evidence)

        service = ExpressionService(claim_service=_FakeClaimService(graph))
        request = ExpressionDraftRequest(
            brief=_brief(
                genre=Genre.RESEARCH_REPORT,
                deliverable_type="科研汇报",
                required_claim_ids=[claim.claim_id],
            ),
            graph_id=graph.graph_id,
        )
        result = service.create_draft(_subject("account-alice"), request)

        draft = result.draft
        assert draft.status == ExpressionDraftStatus.DRAFTED
        elements = draft.research_report_elements
        assert elements

        roles = {e.role for e in elements}
        assert GenreElementRole.OBSERVATION in roles
        assert GenreElementRole.ANALYSIS in roles
        assert GenreElementRole.INTERPRETATION in roles
        assert GenreElementRole.LIMITATION in roles
        assert GenreElementRole.NEXT_STEP in roles

        observation = next(e for e in elements if e.role == GenreElementRole.OBSERVATION)
        assert observation.observation_data_ref
        analysis = next(e for e in elements if e.role == GenreElementRole.ANALYSIS)
        assert analysis.analysis_method
        limitation = next(e for e in elements if e.role == GenreElementRole.LIMITATION)
        assert limitation.limitation_note

    def test_research_report_review_report_flags_missing_sections(self) -> None:
        graph = _make_graph()
        claim = _add_claim(graph, text="线粒体通过细胞呼吸产生 ATP。")
        evidence = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        _add_evidence(graph, claim, EvidenceRelation.LIMITS)
        _add_citation(graph, claim, evidence)

        service = ExpressionService(claim_service=_FakeClaimService(graph))
        request = ExpressionDraftRequest(
            brief=_brief(
                genre=Genre.RESEARCH_REPORT,
                deliverable_type="科研汇报",
                required_claim_ids=[claim.claim_id],
            ),
            graph_id=graph.graph_id,
        )
        result = service.create_draft(_subject("account-alice"), request)

        report = result.draft.review_report
        assert report is not None
        assert report.genre == Genre.RESEARCH_REPORT
        section_finding = next(
            (f for f in report.findings if "观测、分析、解释、限制和下一步" in f.rule), None
        )
        assert section_finding is not None
        assert section_finding.kind == ReviewFindingKind.PRESERVED


class TestPaperAssistGenreElements:
    def test_paper_assist_only_suggests_structure_language_citation_argument(
        self,
    ) -> None:
        graph = _make_graph()
        claim = _add_claim(graph, text="线粒体通过细胞呼吸产生 ATP。")
        evidence = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        _add_evidence(graph, claim, EvidenceRelation.LIMITS)
        _add_citation(graph, claim, evidence)

        service = ExpressionService(claim_service=_FakeClaimService(graph))
        request = ExpressionDraftRequest(
            brief=_brief(
                genre=Genre.PAPER_ASSIST,
                deliverable_type="论文辅助",
                required_claim_ids=[claim.claim_id],
            ),
            graph_id=graph.graph_id,
        )
        result = service.create_draft(_subject("account-alice"), request)

        draft = result.draft
        assert draft.status == ExpressionDraftStatus.WAITING_HUMAN
        elements = draft.paper_assist_elements
        assert elements

        roles = {e.role for e in elements}
        assert GenreElementRole.STRUCTURE_SUGGESTION in roles
        assert GenreElementRole.LANGUAGE_SUGGESTION in roles
        assert GenreElementRole.CITATION_VERIFICATION in roles
        assert GenreElementRole.ARGUMENT_SUGGESTION in roles
        assert GenreElementRole.AI_DISCLOSURE_REMINDER in roles

        for element in elements:
            assert element.requires_author_confirm is True

        citation_element = next(
            e for e in elements if e.role == GenreElementRole.CITATION_VERIFICATION
        )
        assert citation_element.verified is True
        assert "核验" in (citation_element.suggestion_text or "")

    def test_paper_assist_includes_author_responsibility_statement(self) -> None:
        graph = _make_graph()
        claim = _add_claim(graph, text="线粒体通过细胞呼吸产生 ATP。")
        evidence = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        _add_evidence(graph, claim, EvidenceRelation.LIMITS)
        _add_citation(graph, claim, evidence)

        service = ExpressionService(claim_service=_FakeClaimService(graph))
        request = ExpressionDraftRequest(
            brief=_brief(
                genre=Genre.PAPER_ASSIST,
                deliverable_type="论文辅助",
                required_claim_ids=[claim.claim_id],
            ),
            graph_id=graph.graph_id,
        )
        result = service.create_draft(_subject("account-alice"), request)

        statement = result.draft.author_responsibility_statement
        assert statement is not None
        assert statement.genre == Genre.PAPER_ASSIST
        assert "作者" in statement.responsibility_text
        assert "不生成数据" in statement.responsibility_text
        assert statement.ai_disclosure_required is True
        assert statement.author_confirm_required is True
        assert statement.confirmed_at is None

        gate = result.gate
        assert ExpressionGateCheck.AUTHOR_RESPONSIBILITY_PRESENT not in gate.failed_checks
        assert ExpressionGateCheck.PAPER_ASSIST_AUTHOR_CONFIRMATION_REQUIRED in gate.failed_checks

    def test_paper_assist_review_report_flags_missing_ai_disclosure(self) -> None:
        graph = _make_graph()
        claim = _add_claim(graph, text="线粒体通过细胞呼吸产生 ATP。")
        evidence = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        _add_evidence(graph, claim, EvidenceRelation.LIMITS)
        _add_citation(graph, claim, evidence)

        service = ExpressionService(claim_service=_FakeClaimService(graph))
        request = ExpressionDraftRequest(
            brief=_brief(
                genre=Genre.PAPER_ASSIST,
                deliverable_type="论文辅助",
                required_claim_ids=[claim.claim_id],
            ),
            graph_id=graph.graph_id,
        )
        result = service.create_draft(_subject("account-alice"), request)

        report = result.draft.review_report
        assert report is not None
        disclosure_finding = next(
            (f for f in report.findings if "AI 披露提醒" in f.rule), None
        )
        assert disclosure_finding is not None
        assert disclosure_finding.kind == ReviewFindingKind.PRESERVED

        responsibility_finding = next(
            (f for f in report.findings if "作者责任声明" in f.rule), None
        )
        assert responsibility_finding is not None
        assert responsibility_finding.kind == ReviewFindingKind.PRESERVED


class TestStrengthEscalationHumanReview:
    def test_strength_escalation_request_waits_human(self) -> None:
        graph = _make_graph()
        claim = _add_claim(graph, text="线粒体通过细胞呼吸产生 ATP。")
        evidence = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        _add_citation(graph, claim, evidence)

        service = ExpressionService(claim_service=_FakeClaimService(graph))
        request = ExpressionDraftRequest(
            brief=_brief(
                genre=Genre.POPULAR_SCIENCE,
                required_claim_ids=[claim.claim_id],
                requested_strength_upgrade=True,
            ),
            graph_id=graph.graph_id,
        )
        result = service.create_draft(_subject("account-alice"), request)

        assert result.draft.status == ExpressionDraftStatus.WAITING_HUMAN
        assert result.gate.passed is False
        assert ExpressionGateCheck.STRENGTH_ESCALATION_HUMAN_REVIEW in result.gate.failed_checks


class TestResearchPaperGenreConversion:
    def test_research_report_conversion_preserves_scientific_invariants(self) -> None:
        graph = _make_graph()
        claim = _add_claim(graph, text="线粒体通过细胞呼吸产生 ATP。")
        evidence = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        _add_evidence(graph, claim, EvidenceRelation.LIMITS)
        _add_citation(graph, claim, evidence)

        service = ExpressionService(claim_service=_FakeClaimService(graph))
        research_request = ExpressionDraftRequest(
            brief=_brief(
                genre=Genre.RESEARCH_REPORT,
                deliverable_type="科研汇报",
                required_claim_ids=[claim.claim_id],
            ),
            graph_id=graph.graph_id,
        )
        research_result = service.create_draft_and_store(
            _subject("account-alice"), research_request
        )
        research_draft = research_result.draft

        convert_result = service.convert_genre(
            _subject("account-alice"),
            research_draft.draft_id,
            ConvertGenreRequest(target_genre=Genre.PAPER_ASSIST),
        )
        converted = convert_result.converted_draft
        invariance = convert_result.invariance

        assert invariance.fact_lock_set_id_preserved is True
        assert invariance.citation_ids_preserved is True
        assert invariance.claim_ids_preserved is True
        assert invariance.wording_strength_ceiling_preserved is True
        assert invariance.passed is True

        assert converted.genre == Genre.PAPER_ASSIST
        assert converted.research_report_elements == []
        assert converted.paper_assist_elements
        assert converted.author_responsibility_statement is not None
