"""Unit tests for fact lock compilation, evidence conflict detection and honest

degradation (T016).

The seam under test: a claim graph with known evidence relations is analysed by
the deterministic fact-lock service, producing fact locks, conflicts, wording
strength ceilings and a validation report.
"""

from __future__ import annotations

from datetime import UTC, datetime

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
    EvidenceState,
    FactLockType,
    ScientificQualityGateCheck,
    WordingStrength,
)
from science_companion.science.fact_lock import (
    apply_honest_degradation,
    compile_fact_locks,
    detect_conflicts,
)


def _now() -> datetime:
    return datetime.now(UTC)


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
    *,
    lifecycle_status: str = "active",
    source_proximity: str = "full_text",
) -> Evidence:
    evidence = Evidence(
        evidence_id=f"ev-{len(graph.evidence)}",
        claim_id=claim.claim_id,
        document_id="doc-1",
        chunk_ids=["chunk-1"],
        relation=relation,
        lifecycle_status=lifecycle_status,
        source_proximity=source_proximity,
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


class TestFactLockCompilation:
    def test_compiles_citation_and_strength_locks(self) -> None:
        graph = _make_graph()
        claim = _add_claim(graph, text="Mitochondria generate ATP.")
        evidence = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        _add_citation(graph, claim, evidence)

        lock_set = compile_fact_locks(graph)

        assert lock_set.graph_id == graph.graph_id
        types = {lock.lock_type for lock in lock_set.locks}
        assert FactLockType.IDENTIFIER in types
        assert FactLockType.STRENGTH in types

        strength_lock = next(
            lock for lock in lock_set.locks if lock.lock_type == FactLockType.STRENGTH
        )
        assert strength_lock.wording_strength_ceiling == WordingStrength.HIGH

    def test_compiles_number_unit_locks(self) -> None:
        graph = _make_graph()
        claim = _add_claim(
            graph,
            text="The reaction rate is 3.5 mol/L at 25 °C.",
            claim_type=ClaimType.QUANTITATIVE,
        )
        evidence = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        _add_citation(graph, claim, evidence)

        lock_set = compile_fact_locks(graph)

        exact_locks = [
            lock for lock in lock_set.locks if lock.lock_type == FactLockType.EXACT_VALUE
        ]
        assert len(exact_locks) >= 2
        canonical_values = {lock.canonical_value for lock in exact_locks}
        assert "value=3.5,unit=mol/L" in canonical_values
        assert "value=25,unit=°C" in canonical_values

    def test_compiles_term_formula_locks(self) -> None:
        graph = _make_graph()
        claim = _add_claim(
            graph,
            text="Force is defined as F = ma.",
            claim_type=ClaimType.DEFINITION,
        )
        evidence = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        _add_citation(graph, claim, evidence)

        lock_set = compile_fact_locks(graph)

        term_locks = [
            lock for lock in lock_set.locks if lock.lock_type == FactLockType.TERM_FORMULA
        ]
        assert len(term_locks) >= 1
        assert any("F = ma" in lock.canonical_value for lock in term_locks)

    def test_compiles_relation_and_condition_locks(self) -> None:
        graph = _make_graph()
        claim = _add_claim(
            graph,
            text="Smoking causes lung cancer in adults.",
            claim_type=ClaimType.CAUSAL,
            scope="adults",
        )
        evidence = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        _add_citation(graph, claim, evidence)

        lock_set = compile_fact_locks(graph)

        types = {lock.lock_type for lock in lock_set.locks}
        assert FactLockType.RELATION in types
        assert FactLockType.CONDITION in types

        relation_lock = next(
            lock for lock in lock_set.locks if lock.lock_type == FactLockType.RELATION
        )
        assert "不得改变关系方向" in relation_lock.forbidden_transformations


class TestEvidenceConflictDetection:
    def test_support_and_refute_evidence_creates_conflict(self) -> None:
        graph = _make_graph()
        claim = _add_claim(graph, text="X causes Y.", claim_type=ClaimType.CAUSAL)
        support = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        refute = _add_evidence(graph, claim, EvidenceRelation.REFUTES)
        _add_citation(graph, claim, support)
        _add_citation(graph, claim, refute)

        conflicts = detect_conflicts(graph)

        assert len(conflicts) == 1
        assert claim.claim_id in conflicts[0].claim_ids
        assert support.evidence_id in conflicts[0].evidence_ids
        assert refute.evidence_id in conflicts[0].evidence_ids

    def test_support_and_limit_evidence_does_not_create_conflict(self) -> None:
        graph = _make_graph()
        claim = _add_claim(graph, text="X is associated with Y.")
        support = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        limit = _add_evidence(graph, claim, EvidenceRelation.LIMITS)
        _add_citation(graph, claim, support)
        _add_citation(graph, claim, limit)

        conflicts = detect_conflicts(graph)

        assert conflicts == []


class TestHonestDegradation:
    def test_supported_evidence_is_verified(self) -> None:
        graph = _make_graph()
        claim = _add_claim(graph, text="Cellular respiration produces ATP.")
        evidence = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        _add_citation(graph, claim, evidence)

        report = apply_honest_degradation(graph, had_candidates=True)

        assert report.status == ClaimTrustStatus.VERIFIED
        assert report.evidence_states[claim.claim_id] == EvidenceState.SUPPORTED
        assert report.wording_strength_ceiling == WordingStrength.HIGH
        assert report.scientific_gate is not None
        assert report.scientific_gate.passed is True

    def test_limited_evidence_is_qualified(self) -> None:
        graph = _make_graph()
        claim = _add_claim(graph, text="X may improve Y under lab conditions.")
        support = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        _add_evidence(graph, claim, EvidenceRelation.LIMITS)
        _add_citation(graph, claim, support)

        report = apply_honest_degradation(graph, had_candidates=True)

        assert report.status == ClaimTrustStatus.QUALIFIED
        assert report.wording_strength_ceiling == WordingStrength.MODERATE
        assert report.human_gate_required is False

    def test_conflict_evidence_is_conflicted(self) -> None:
        graph = _make_graph()
        claim = _add_claim(graph, text="X causes Y.", claim_type=ClaimType.CAUSAL)
        support = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        refute = _add_evidence(graph, claim, EvidenceRelation.REFUTES)
        _add_citation(graph, claim, support)
        _add_citation(graph, claim, refute)

        report = apply_honest_degradation(graph, had_candidates=True)

        assert report.status == ClaimTrustStatus.CONFLICTED
        assert report.evidence_states[claim.claim_id] == EvidenceState.CONFLICTED
        assert report.wording_strength_ceiling == WordingStrength.CONFLICTING
        assert len(report.conflicts) == 1
        assert report.human_gate_required is True
        assert "human_review" in report.recovery_actions

    def test_unknown_evidence_is_blocked_by_quality_gate(self) -> None:
        graph = _make_graph()
        claim = _add_claim(graph, text="Z affects W.")
        evidence = _add_evidence(
            graph,
            claim,
            EvidenceRelation.SUPPORTS,
            lifecycle_status="unknown",
        )
        _add_citation(graph, claim, evidence)

        report = apply_honest_degradation(graph, had_candidates=True)

        assert report.evidence_states[claim.claim_id] == EvidenceState.UNKNOWN
        assert report.wording_strength_ceiling == WordingStrength.UNASSESSABLE
        assert report.scientific_gate is not None
        assert report.scientific_gate.passed is False
        assert (
            ScientificQualityGateCheck.WORDING_STRENGTH_WITHIN_EVIDENCE
            in report.scientific_gate.failed_checks
        )
        assert report.human_gate_required is True

    def test_insufficient_evidence_for_key_claim_is_partial(self) -> None:
        graph = _make_graph()
        _add_claim(graph, text="A is true.", importance=ClaimImportance.KEY)

        report = apply_honest_degradation(graph, had_candidates=True)

        assert report.status == ClaimTrustStatus.PARTIAL
        assert report.wording_strength_ceiling == WordingStrength.VERY_LOW
        assert report.scientific_gate.passed is False
        assert (
            ScientificQualityGateCheck.KEY_CLAIM_COVERAGE
            in report.scientific_gate.failed_checks
        )
        assert "add_evidence" in report.recovery_actions
        assert report.human_gate_required is True

    def test_no_candidates_preserves_metadata_only(self) -> None:
        graph = _make_graph(status=ClaimTrustStatus.METADATA_ONLY)
        _add_claim(graph, text="A is true.", importance=ClaimImportance.KEY)

        report = apply_honest_degradation(graph, had_candidates=False)

        assert report.status == ClaimTrustStatus.METADATA_ONLY
        assert report.wording_strength_ceiling == WordingStrength.METADATA_ONLY

    def test_support_refute_limit_evidence_preserved(self) -> None:
        """Supporting, refuting and limiting evidence are kept side by side."""
        graph = _make_graph()
        claim = _add_claim(graph, text="X causes Y.", claim_type=ClaimType.CAUSAL)
        support = _add_evidence(graph, claim, EvidenceRelation.SUPPORTS)
        refute = _add_evidence(graph, claim, EvidenceRelation.REFUTES)
        limit = _add_evidence(graph, claim, EvidenceRelation.LIMITS)
        _add_citation(graph, claim, support)
        _add_citation(graph, claim, refute)
        _add_citation(graph, claim, limit)

        report = apply_honest_degradation(graph, had_candidates=True)

        assert report.status == ClaimTrustStatus.CONFLICTED
        relations = {
            e.relation
            for e in graph.evidence
            if e.claim_id == claim.claim_id
        }
        assert EvidenceRelation.SUPPORTS in relations
        assert EvidenceRelation.REFUTES in relations
        assert EvidenceRelation.LIMITS in relations
