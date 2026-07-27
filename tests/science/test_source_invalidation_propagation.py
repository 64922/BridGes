"""Source invalidation propagation and downstream impact tests (T017).

The seam under test: when a source is revoked, marked status-unknown, or
superseded by a new version, the change flows through the general invalidation
foundation, the science domain impact resolver locates affected claim graphs and
fact lock sets, and revalidation creates new graph versions without overwriting
the original run snapshot.
"""

from __future__ import annotations

import base64

import pytest

from science_companion.contracts.identity import AuthMethod, SubjectContext
from science_companion.contracts.invalidation import (
    InvalidationEventType,
    InvalidationState,
    RevalidationStatus,
)
from science_companion.contracts.projects import ObjectDomain, ObjectRef
from science_companion.contracts.science import (
    ChunkCorrection,
    CitationVerificationStatus,
    ClaimRequest,
    ClaimTrustStatus,
    LicenseState,
    MediaType,
    PublishGateCheck,
    SearchRequest,
    Source,
    SourceStatus,
    SourceUploadRequest,
    SourceVersionRequest,
)
from science_companion.invalidation import InvalidationService
from science_companion.science import ClaimEvidenceService, ScienceError, ScienceSourceService
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


def _source_ref(source: Source) -> ObjectRef:
    domain = ObjectDomain.SHARED_PROJECT if source.project_id else ObjectDomain.PERSONAL_VAULT
    owner_id = source.project_id if source.project_id else source.account_id
    return ObjectRef(domain=domain, owner_id=owner_id, object_id=source.source_id, version=1)


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


class TestSourceRetractionPropagation:
    def test_revoke_source_records_immutable_invalidation_event(
        self,
        source_service: ScienceSourceService,
        invalidation_service: InvalidationService,
        alice: SubjectContext,
    ) -> None:
        source_id = _upload_text(source_service, alice.account_id, "To be retracted.")
        source_ref, event = source_service.revoke_source(
            alice.account_id, source_id, "用户撤权", subject=alice
        )

        assert event is not None
        assert event.event_type == InvalidationEventType.SOURCE_RETRACTED
        assert event.object_ref == source_ref
        assert event.subject.account_id == alice.account_id

        state = invalidation_service.check_state(source_ref)
        assert state.state == InvalidationState.REVOKED

    def test_revoked_source_blocks_new_claim_graphs(
        self,
        source_service: ScienceSourceService,
        search_service: ScienceSearchService,
        claim_service: ClaimEvidenceService,
        alice: SubjectContext,
    ) -> None:
        _upload_text(source_service, alice.account_id, "Retracted claim about dark matter.")
        before = claim_service.generate_claim_graph(alice, ClaimRequest(query="dark matter"))
        assert before.publish_gate.passed is True

        source_id = before.search_result.candidates[0].source_id
        source_service.revoke_source(alice.account_id, source_id, "撤权", subject=alice)

        after = search_service.search(alice, SearchRequest(query="dark matter"))
        assert after.candidates == []

    def test_revoked_source_blocks_publish_of_existing_artifact(
        self,
        source_service: ScienceSourceService,
        claim_service: ClaimEvidenceService,
        invalidation_service: InvalidationService,
        alice: SubjectContext,
    ) -> None:
        source_id = _upload_text(
            source_service, alice.account_id, "Published claim about neuron firing."
        )
        result = claim_service.generate_claim_graph(
            alice, ClaimRequest(query="neuron firing")
        )
        assert result.publish_gate.passed is True

        source_ref, event = source_service.revoke_source(
            alice.account_id, source_id, "撤权", subject=alice
        )
        assert event is not None
        plan = invalidation_service.plan_invalidation(event.event_id)

        schedule = next(
            s for s in plan.revalidation_schedules if s.revalidation_type == "claim_graph"
        )
        invalidation_service.run_revalidation(schedule.schedule_id)

        # Find the new graph version by superseded link.
        old_graph = claim_service.get_claim_graph(alice.account_id, result.graph.graph_id)
        assert old_graph.superseded_by_graph_id is not None
        new_graph_id = old_graph.superseded_by_graph_id

        publish_gate = claim_service.run_publish_gate(alice.account_id, new_graph_id)
        assert publish_gate.passed is False
        assert PublishGateCheck.SOURCE_ACTIVE in publish_gate.failed_checks


class TestSourceVersionSupersededPropagation:
    def test_new_version_superseded_event_is_recorded(
        self,
        source_service: ScienceSourceService,
        invalidation_service: InvalidationService,
        alice: SubjectContext,
    ) -> None:
        source_id = _upload_text(source_service, alice.account_id, "Version one.")
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
                        corrected_text="Version two.",
                        reason="typo",
                    )
                ],
                reason="correction",
            ),
            subject=alice,
        )

        source_ref = _source_ref(source_service._sources[source_id].source)
        events = invalidation_service.list_events(source_ref)
        superseded_events = [
            e for e in events if e.event_type == InvalidationEventType.SOURCE_VERSION_SUPERSEDED
        ]
        assert len(superseded_events) == 1

    def test_superseded_source_creates_revalidated_claim_graph(
        self,
        source_service: ScienceSourceService,
        claim_service: ClaimEvidenceService,
        invalidation_service: InvalidationService,
        alice: SubjectContext,
    ) -> None:
        source_id = _upload_text(
            source_service, alice.account_id, "Original claim about sleep cycles."
        )
        result = claim_service.generate_claim_graph(alice, ClaimRequest(query="sleep cycles"))
        old_graph_id = result.graph.graph_id
        citation_id = result.graph.claims[0].citation_ids[0]

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
                        corrected_text="Corrected claim about sleep cycles.",
                        reason="clarification",
                    )
                ],
                reason="clarification",
            ),
            subject=alice,
        )

        # The source object now has a superseded event; build and run the plan.
        source_ref = _source_ref(source_service._sources[source_id].source)
        events = invalidation_service.list_events(source_ref)
        superseded_event = next(
            e for e in events if e.event_type == InvalidationEventType.SOURCE_VERSION_SUPERSEDED
        )
        plan = invalidation_service.plan_invalidation(superseded_event.event_id)

        schedule = next(
            s for s in plan.revalidation_schedules if s.revalidation_type == "claim_graph"
        )
        invalidation_service.run_revalidation(schedule.schedule_id)

        old_graph = claim_service.get_claim_graph(alice.account_id, old_graph_id)
        assert old_graph.superseded_by_graph_id is not None
        new_graph = claim_service.get_claim_graph(
            alice.account_id, old_graph.superseded_by_graph_id
        )
        new_citation = next(c for c in new_graph.citations if c.citation_id == citation_id)
        assert new_citation.verification_status == CitationVerificationStatus.SOURCE_SUPERSEDED


class TestSourceStatusUnknownPropagation:
    def test_status_unknown_source_blocks_publish(
        self,
        source_service: ScienceSourceService,
        claim_service: ClaimEvidenceService,
        invalidation_service: InvalidationService,
        alice: SubjectContext,
    ) -> None:
        source_id = _upload_text(
            source_service, alice.account_id, "Claim with uncertain source status."
        )
        result = claim_service.generate_claim_graph(alice, ClaimRequest(query="uncertain"))
        assert result.publish_gate.passed is True

        source_ref, event = source_service.mark_source_status_unknown(
            alice.account_id, source_id, "外部元数据返回状态未知", subject=alice
        )
        assert event is not None
        assert event.event_type == InvalidationEventType.SOURCE_STATUS_UNKNOWN

        plan = invalidation_service.plan_invalidation(event.event_id)
        schedule = next(
            s for s in plan.revalidation_schedules if s.revalidation_type == "claim_graph"
        )
        invalidation_service.run_revalidation(schedule.schedule_id)

        source = source_service._sources[source_id].source
        assert source.status == SourceStatus.STATUS_UNKNOWN

        old_graph = claim_service.get_claim_graph(alice.account_id, result.graph.graph_id)
        assert old_graph.superseded_by_graph_id is not None
        new_graph = claim_service.get_claim_graph(
            alice.account_id, old_graph.superseded_by_graph_id
        )
        publish_gate = claim_service.run_publish_gate(alice.account_id, new_graph.graph_id)
        assert publish_gate.passed is False


class TestRevalidationPreservesHistory:
    def test_revalidation_creates_new_version_without_overwriting_original(
        self,
        source_service: ScienceSourceService,
        claim_service: ClaimEvidenceService,
        invalidation_service: InvalidationService,
        alice: SubjectContext,
    ) -> None:
        source_id = _upload_text(
            source_service, alice.account_id, "Historical claim about gravity."
        )
        result = claim_service.generate_claim_graph(alice, ClaimRequest(query="gravity"))
        old_graph = result.graph
        original_version_number = old_graph.version_number

        source_ref, event = source_service.revoke_source(
            alice.account_id, source_id, "撤权", subject=alice
        )
        assert event is not None
        plan = invalidation_service.plan_invalidation(event.event_id)
        schedule = next(
            s for s in plan.revalidation_schedules if s.revalidation_type == "claim_graph"
        )
        revalidation_result = invalidation_service.run_revalidation(schedule.schedule_id)
        assert revalidation_result.success is True
        updated_schedule = invalidation_service.get_revalidation_schedule(schedule.schedule_id)
        assert updated_schedule is not None
        assert updated_schedule.status == RevalidationStatus.COMPLETED

        # Original graph is untouched: it remains the immutable run snapshot.
        original_again = claim_service.get_claim_graph(alice.account_id, old_graph.graph_id)
        assert original_again.version_number == original_version_number
        assert original_again.status == ClaimTrustStatus.VERIFIED
        assert original_again.superseded_by_graph_id is not None

        # New version reflects invalidation.
        new_graph = claim_service.get_claim_graph(
            alice.account_id, original_again.superseded_by_graph_id
        )
        assert new_graph.version_number == original_version_number + 1
        assert new_graph.status in {
            ClaimTrustStatus.BLOCKED,
            ClaimTrustStatus.CONFLICTED,
        }


class TestScopeIsolation:
    def test_cross_account_cannot_revoke_source(
        self,
        source_service: ScienceSourceService,
        alice: SubjectContext,
        bob: SubjectContext,
    ) -> None:
        source_id = _upload_text(source_service, alice.account_id, "Alice private source.")
        with pytest.raises(ScienceError, match="访问权限"):
            source_service.revoke_source(bob.account_id, source_id, "越权撤权", subject=bob)
