"""Module-interface tests for locatable Claim--Evidence--Citation graphs (T015).

The seam under test: an authenticated subject submits a scientific question and
receives a claim graph where each key claim is bound to source-versioned evidence
with precise locators. Citation verification reacts to source version changes
and revocation; claim/evidence/citation modifications create new versions; and
the publish gate rejects fabricated or unlocatable critical citations.
"""

from __future__ import annotations

import base64

import pytest

from science_companion.contracts.identity import AuthMethod, SubjectContext
from science_companion.contracts.invalidation import (
    InvalidationEventType,
    RevalidationStatus,
)
from science_companion.contracts.science import (
    CitationVerificationStatus,
    ClaimImportance,
    ClaimRequest,
    ClaimTrustStatus,
    ClaimType,
    EvidenceRelation,
    LicenseState,
    MediaType,
    PublishGateCheck,
    SourceUploadRequest,
)
from science_companion.invalidation import InvalidationService
from science_companion.science import ClaimEvidenceService, ScienceSourceService
from science_companion.science.claims import (
    ClaimGraphRevalidationHandler,
    build_claim_impact_resolver,
)
from science_companion.science.search import ScienceSearchService
from science_companion.scope import ScopeEnforcer


@pytest.fixture
def scope_enforcer() -> ScopeEnforcer:
    return ScopeEnforcer()


@pytest.fixture
def invalidation_service(scope_enforcer: ScopeEnforcer) -> InvalidationService:
    return InvalidationService(scope_enforcer=scope_enforcer)


@pytest.fixture
def source_service(invalidation_service: InvalidationService) -> ScienceSourceService:
    svc = ScienceSourceService(invalidation_service=invalidation_service)
    from science_companion.science.service import build_source_impact_resolver

    invalidation_service.register_impact_resolver(
        "science_source", build_source_impact_resolver(svc)
    )
    return svc


@pytest.fixture
def search_service(
    source_service: ScienceSourceService,
    scope_enforcer: ScopeEnforcer,
    invalidation_service: InvalidationService,
) -> ScienceSearchService:
    return ScienceSearchService(
        source_service=source_service,
        scope_enforcer=scope_enforcer,
        invalidation_service=invalidation_service,
    )


@pytest.fixture
def claim_service(
    source_service: ScienceSourceService,
    search_service: ScienceSearchService,
    invalidation_service: InvalidationService,
) -> ClaimEvidenceService:
    svc = ClaimEvidenceService(
        source_service=source_service,
        search_service=search_service,
        invalidation_service=invalidation_service,
    )
    invalidation_service.register_impact_resolver(
        "claim_graph", build_claim_impact_resolver(svc)
    )
    invalidation_service.register_revalidation_handler(
        "claim_graph", ClaimGraphRevalidationHandler(svc)
    )
    return svc


@pytest.fixture
def alice() -> SubjectContext:
    return SubjectContext(
        account_id="account-alice",
        session_id="session-alice",
        auth_method=AuthMethod.PASSWORD,
    )


@pytest.fixture
def bob() -> SubjectContext:
    return SubjectContext(
        account_id="account-bob",
        session_id="session-bob",
        auth_method=AuthMethod.PASSWORD,
    )


def _upload_text(
    service: ScienceSourceService,
    account_id: str,
    text: str,
    *,
    project_id: str | None = None,
    filename: str = "paper.txt",
) -> str:
    request = SourceUploadRequest(
        filename=filename,
        media_type=MediaType.TEXT_PLAIN,
        content=base64.b64encode(text.encode("utf-8")).decode("ascii"),
        license_state=LicenseState.USER_OWNED,
        title=filename,
    )
    run = service.ingest_upload(account_id, project_id, request)
    assert run.source_id is not None
    return run.source_id


class TestClaimGeneration:
    def test_generate_claim_graph_creates_claims_with_evidence_and_citations(
        self,
        claim_service: ClaimEvidenceService,
        source_service: ScienceSourceService,
        alice: SubjectContext,
    ) -> None:
        _upload_text(
            source_service,
            alice.account_id,
            "Mitochondria generate ATP through cellular respiration.",
        )
        request = ClaimRequest(query="mitochondria ATP respiration", top_k=3)
        result = claim_service.generate_claim_graph(alice, request)

        assert result.graph.graph_id
        assert result.graph.account_id == alice.account_id
        assert result.graph.query == request.query
        assert len(result.graph.claims) > 0

        key_claims = [c for c in result.graph.claims if c.importance == ClaimImportance.KEY]
        assert len(key_claims) > 0
        claim = key_claims[0]
        assert claim.evidence_ids
        assert claim.citation_ids

        evidence = next(e for e in result.graph.evidence if e.evidence_id == claim.evidence_ids[0])
        assert evidence.relation == EvidenceRelation.SUPPORTS
        assert evidence.document_id
        assert evidence.chunk_ids

        citation = next(c for c in result.graph.citations if c.citation_id == claim.citation_ids[0])
        assert citation.evidence_id == evidence.evidence_id
        assert citation.locator is not None
        assert citation.source_version_label
        assert citation.identifier_snapshot["document_id"] == evidence.document_id

    def test_claims_bind_to_source_version_and_chunk_locator(
        self,
        claim_service: ClaimEvidenceService,
        source_service: ScienceSourceService,
        alice: SubjectContext,
    ) -> None:
        _upload_text(
            source_service,
            alice.account_id,
            "# Results\n\nPhotosynthesis converts light energy into chemical energy.",
        )
        request = ClaimRequest(query="photosynthesis light energy")
        result = claim_service.generate_claim_graph(alice, request)

        citation = result.graph.citations[0]
        assert citation.locator.section == "Results" or citation.locator.paragraph is not None
        assert citation.source_version_label == "v1"
        assert citation.verification_status == CitationVerificationStatus.VERIFIED

    def test_high_stakes_claims_include_limiting_evidence(
        self,
        claim_service: ClaimEvidenceService,
        source_service: ScienceSourceService,
        alice: SubjectContext,
    ) -> None:
        _upload_text(
            source_service,
            alice.account_id,
            "Smoking causes lung cancer according to multiple cohort studies.",
        )
        request = ClaimRequest(query="smoking causes lung cancer", include_refutations=True)
        result = claim_service.generate_claim_graph(alice, request)

        causal_claims = [c for c in result.graph.claims if c.claim_type == ClaimType.CAUSAL]
        assert len(causal_claims) > 0
        claim = causal_claims[0]
        relations = {
            e.relation
            for e in result.graph.evidence
            if e.claim_id == claim.claim_id
        }
        assert EvidenceRelation.SUPPORTS in relations
        assert EvidenceRelation.LIMITS in relations

    def test_empty_retrieval_marks_graph_metadata_only(
        self,
        claim_service: ClaimEvidenceService,
        source_service: ScienceSourceService,
        alice: SubjectContext,
    ) -> None:
        _upload_text(source_service, alice.account_id, "Neuroscience of sleep.")
        request = ClaimRequest(query="xyznonexistentterm12345", top_k=3)
        result = claim_service.generate_claim_graph(alice, request)

        assert result.graph.status == ClaimTrustStatus.METADATA_ONLY
        assert result.publish_gate.passed is False
        assert PublishGateCheck.KEY_CLAIM_COVERAGE in result.publish_gate.failed_checks


class TestCitationVerification:
    def test_citation_becomes_stale_after_source_version_change(
        self,
        claim_service: ClaimEvidenceService,
        source_service: ScienceSourceService,
        alice: SubjectContext,
    ) -> None:
        source_id = _upload_text(
            source_service,
            alice.account_id,
            "Original claim about dark matter.",
        )
        request = ClaimRequest(query="dark matter")
        result = claim_service.generate_claim_graph(alice, request)
        graph_id = result.graph.graph_id
        citation_id = result.graph.claims[0].citation_ids[0]

        # Create a new version of the source.
        from science_companion.contracts.science import ChunkCorrection, SourceVersionRequest

        projection = source_service.get_source(alice.account_id, source_id)
        assert projection.current_document is not None
        source_service.create_new_version(
            alice.account_id,
            source_id,
            SourceVersionRequest(
                base_document_id=projection.current_document.document_id,
                chunk_corrections=[
                    ChunkCorrection(
                        chunk_id=projection.current_chunks[0].chunk_id,
                        corrected_text="Corrected claim about dark matter.",
                        reason="typo",
                    )
                ],
                reason="typo",
            ),
        )

        # The old citation should now be stale or superseded.
        validation = claim_service.validate_citation(
            alice.account_id, graph_id, citation_id
        )
        assert (
            validation.verification_status
            == CitationVerificationStatus.SOURCE_SUPERSEDED
        )

    def test_revoked_source_creates_new_graph_version_and_blocks_publish(
        self,
        claim_service: ClaimEvidenceService,
        source_service: ScienceSourceService,
        invalidation_service: InvalidationService,
        alice: SubjectContext,
    ) -> None:
        source_id = _upload_text(
            source_service,
            alice.account_id,
            "Claim about exoplanets.",
        )
        request = ClaimRequest(query="exoplanets")
        result = claim_service.generate_claim_graph(alice, request)
        old_graph_id = result.graph.graph_id
        citation_id = result.graph.claims[0].citation_ids[0]

        source_ref, event = source_service.revoke_source(
            alice.account_id, source_id, "撤权", subject=alice
        )
        assert event is not None
        assert event.event_type == InvalidationEventType.SOURCE_RETRACTED

        # Build the invalidation plan: the claim_graph resolver adds downstreams.
        plan = invalidation_service.plan_invalidation(event.event_id)
        downstream_types = {d.downstream_type for d in plan.impact_set.affected_downstreams}
        assert "claim_graph" in downstream_types
        assert "fact_lock_set" in downstream_types

        # Run the scheduled revalidation: it creates a new graph version.
        schedule = next(
            s for s in plan.revalidation_schedules if s.revalidation_type == "claim_graph"
        )
        revalidation_result = invalidation_service.run_revalidation(schedule.schedule_id)
        assert revalidation_result.success is True
        updated_schedule = invalidation_service.get_revalidation_schedule(schedule.schedule_id)
        assert updated_schedule is not None
        assert updated_schedule.status == RevalidationStatus.COMPLETED

        new_graph_ids = revalidation_result.details.get("new_graph_ids", [])
        assert len(new_graph_ids) == 1
        new_graph_id = new_graph_ids[0]

        # The original graph is preserved as an immutable run snapshot.
        old_graph = claim_service.get_claim_graph(alice.account_id, old_graph_id)
        assert old_graph.superseded_by_graph_id == new_graph_id
        old_citation = next(c for c in old_graph.citations if c.citation_id == citation_id)
        assert old_citation.verification_status == CitationVerificationStatus.VERIFIED

        # The new graph version shows the current revoked state.
        new_graph = claim_service.get_claim_graph(alice.account_id, new_graph_id)
        new_citation = next(c for c in new_graph.citations if c.citation_id == citation_id)
        assert new_citation.verification_status == CitationVerificationStatus.SOURCE_REVOKED

        # The new graph's publish gate fails.
        publish_gate = claim_service.run_publish_gate(alice.account_id, new_graph_id)
        assert publish_gate.passed is False


class TestClaimVersioning:
    def test_claim_modification_creates_new_graph_version(
        self,
        claim_service: ClaimEvidenceService,
        source_service: ScienceSourceService,
        alice: SubjectContext,
    ) -> None:
        _upload_text(source_service, alice.account_id, "Alpha mechanism.")
        result = claim_service.generate_claim_graph(alice, ClaimRequest(query="Alpha mechanism"))
        old_graph = result.graph
        claim_id = old_graph.claims[0].claim_id

        new_graph = claim_service.create_claim_version(
            alice.account_id,
            old_graph.graph_id,
            claim_id,
            new_text="Revised Alpha mechanism.",
            reason="clarification",
        )

        assert new_graph.graph_id != old_graph.graph_id
        assert new_graph.version_number == old_graph.version_number + 1
        assert old_graph.superseded_by_graph_id == new_graph.graph_id

        new_claim = next(c for c in new_graph.claims if c.superseded_by_claim_id is None)
        assert new_claim.text == "Revised Alpha mechanism."
        assert new_claim.version_number == 2

        old_claim = next(c for c in old_graph.claims if c.claim_id == claim_id)
        assert old_claim.superseded_by_claim_id == new_claim.claim_id


class TestPublishGate:
    def test_publish_gate_passes_for_locatable_key_claims(
        self,
        claim_service: ClaimEvidenceService,
        source_service: ScienceSourceService,
        alice: SubjectContext,
    ) -> None:
        _upload_text(
            source_service,
            alice.account_id,
            "Cellular respiration produces ATP in mitochondria.",
        )
        result = claim_service.generate_claim_graph(alice, ClaimRequest(query="ATP mitochondria"))

        assert result.publish_gate.passed is True
        assert result.publish_gate.graph_status == ClaimTrustStatus.VERIFIED
        assert all(
            result.publish_gate.checks[check]
            for check in [
                PublishGateCheck.KEY_CLAIM_COVERAGE,
                PublishGateCheck.CITATION_LOCATABLE,
                PublishGateCheck.SOURCE_ACTIVE,
            ]
        )

    def test_publish_gate_blocks_when_critical_citation_unlocatable(
        self,
        claim_service: ClaimEvidenceService,
        source_service: ScienceSourceService,
        alice: SubjectContext,
    ) -> None:
        _upload_text(source_service, alice.account_id, "Important result.")
        result = claim_service.generate_claim_graph(alice, ClaimRequest(query="Important result"))
        graph = result.graph
        claim = [c for c in graph.claims if c.importance == ClaimImportance.KEY][0]

        # Simulate a fabricated/unlocatable citation by mutating verification status.
        for citation in graph.citations:
            if citation.citation_id in claim.citation_ids:
                citation.verification_status = CitationVerificationStatus.UNLOCATABLE
                citation.verification_reason = "Locator could not be verified."

        gate = claim_service.run_publish_gate(alice.account_id, graph.graph_id)
        assert gate.passed is False
        assert PublishGateCheck.CITATION_LOCATABLE in gate.failed_checks
        assert claim.claim_id in gate.blocked_claim_ids


class TestScopeIsolation:
    def test_bob_cannot_read_alice_claim_graph(
        self,
        claim_service: ClaimEvidenceService,
        source_service: ScienceSourceService,
        alice: SubjectContext,
        bob: SubjectContext,
    ) -> None:
        _upload_text(source_service, alice.account_id, "Alice private claim.")
        result = claim_service.generate_claim_graph(alice, ClaimRequest(query="Alice private"))

        with pytest.raises(Exception, match="访问权限"):
            claim_service.get_claim_graph(bob.account_id, result.graph.graph_id)
