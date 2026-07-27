"""Science source API routes.

Routes implement uploading, listing, retrieving, versioning and correcting text
and PDF scientific sources within the scope of an account and project.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from science_companion.api.auth import SubjectDep
from science_companion.contracts.invalidation import InvalidationEventType
from science_companion.contracts.science import (
    ChunkVersion,
    CitationValidationResult,
    ClaimGraph,
    ClaimGraphResult,
    ClaimRequest,
    DocumentVersion,
    FactLockSet,
    IngestionRunRef,
    PublishGateResult,
    ScientificQualityGateResult,
    SearchRequest,
    SearchResult,
    SourceError,
    SourceProjection,
    SourceSummary,
    SourceUploadRequest,
    SourceVersionRequest,
    ValidationReport,
)
from science_companion.invalidation import InvalidationService
from science_companion.science import (
    ClaimEvidenceService,
    ScienceError,
    ScienceSearchService,
    ScienceSourceService,
)

router = APIRouter(prefix="/science", tags=["science"])


def _get_science_service(request: Request) -> ScienceSourceService:
    service: ScienceSourceService | None = getattr(
        request.app.state, "science_source_service", None
    )
    if service is None:
        raise RuntimeError("ScienceSourceService not attached to application state.")
    return service


def _get_search_service(request: Request) -> ScienceSearchService:
    service: ScienceSearchService | None = getattr(
        request.app.state, "science_search_service", None
    )
    if service is None:
        raise RuntimeError("ScienceSearchService not attached to application state.")
    return service


def _get_claim_service(request: Request) -> ClaimEvidenceService:
    service: ClaimEvidenceService | None = getattr(
        request.app.state, "claim_evidence_service", None
    )
    if service is None:
        raise RuntimeError("ClaimEvidenceService not attached to application state.")
    return service


ScienceServiceDep = Annotated[ScienceSourceService, Depends(_get_science_service)]
SearchServiceDep = Annotated[ScienceSearchService, Depends(_get_search_service)]
ClaimServiceDep = Annotated[ClaimEvidenceService, Depends(_get_claim_service)]


def _science_error(status_code: int, error: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=SourceError(error=error, message=message).model_dump(),
    )


@router.post(
    "/projects/{project_id}/sources",
    response_model=IngestionRunRef,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": SourceError},
        status.HTTP_403_FORBIDDEN: {"model": SourceError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": SourceError},
    },
)
async def upload_source_to_project(
    service: ScienceServiceDep,
    subject: SubjectDep,
    project_id: str,
    request: SourceUploadRequest,
) -> IngestionRunRef:
    """Upload a text or PDF source into a project."""
    try:
        return service.ingest_upload(
            account_id=subject.account_id,
            project_id=project_id,
            request=request,
        )
    except ScienceError as exc:
        raise _science_error(
            status.HTTP_403_FORBIDDEN, "source_ingestion_failed", str(exc)
        ) from exc


@router.post(
    "/sources",
    response_model=IngestionRunRef,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": SourceError},
        status.HTTP_403_FORBIDDEN: {"model": SourceError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": SourceError},
    },
)
async def upload_source(
    service: ScienceServiceDep,
    subject: SubjectDep,
    request: SourceUploadRequest,
) -> IngestionRunRef:
    """Upload a personal text or PDF source (not bound to a project)."""
    try:
        return service.ingest_upload(
            account_id=subject.account_id,
            project_id=None,
            request=request,
        )
    except ScienceError as exc:
        raise _science_error(
            status.HTTP_403_FORBIDDEN, "source_ingestion_failed", str(exc)
        ) from exc


@router.get(
    "/projects/{project_id}/sources",
    response_model=list[SourceSummary],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": SourceError},
    },
)
async def list_project_sources(
    service: ScienceServiceDep,
    subject: SubjectDep,
    project_id: str,
) -> list[SourceSummary]:
    """List sources uploaded to a project."""
    return service.list_sources(account_id=subject.account_id, project_id=project_id)


@router.get(
    "/sources",
    response_model=list[SourceSummary],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": SourceError},
    },
)
async def list_sources(
    service: ScienceServiceDep,
    subject: SubjectDep,
) -> list[SourceSummary]:
    """List personal sources for the current account."""
    return service.list_sources(account_id=subject.account_id, project_id=None)


@router.get(
    "/sources/{source_id}",
    response_model=SourceProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": SourceError},
        status.HTTP_404_NOT_FOUND: {"model": SourceError},
    },
)
async def get_source(
    service: ScienceServiceDep,
    subject: SubjectDep,
    source_id: str,
) -> SourceProjection:
    """Get a source projection with current document and chunks."""
    try:
        return service.get_source(account_id=subject.account_id, source_id=source_id)
    except ScienceError as exc:
        raise _science_error(
            status.HTTP_404_NOT_FOUND, "source_not_found", str(exc)
        ) from exc


@router.get(
    "/sources/{source_id}/versions",
    response_model=list[DocumentVersion],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": SourceError},
        status.HTTP_404_NOT_FOUND: {"model": SourceError},
    },
)
async def list_source_versions(
    service: ScienceServiceDep,
    subject: SubjectDep,
    source_id: str,
) -> list[DocumentVersion]:
    """List all document versions of a source."""
    try:
        return service.list_document_versions(
            account_id=subject.account_id, source_id=source_id
        )
    except ScienceError as exc:
        raise _science_error(
            status.HTTP_404_NOT_FOUND, "source_not_found", str(exc)
        ) from exc


@router.post(
    "/sources/{source_id}/versions",
    response_model=DocumentVersion,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": SourceError},
        status.HTTP_404_NOT_FOUND: {"model": SourceError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": SourceError},
    },
)
async def create_source_version(
    service: ScienceServiceDep,
    subject: SubjectDep,
    source_id: str,
    request: SourceVersionRequest,
) -> DocumentVersion:
    """Create a new document version by applying chunk corrections."""
    try:
        return service.create_new_version(
            account_id=subject.account_id,
            source_id=source_id,
            request=request,
        )
    except ScienceError as exc:
        raise _science_error(
            status.HTTP_404_NOT_FOUND, "source_not_found", str(exc)
        ) from exc


@router.get(
    "/sources/{source_id}/chunks/{chunk_id}",
    response_model=ChunkVersion,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": SourceError},
        status.HTTP_404_NOT_FOUND: {"model": SourceError},
    },
)
async def get_chunk(
    service: ScienceServiceDep,
    subject: SubjectDep,
    source_id: str,
    chunk_id: str,
) -> ChunkVersion:
    """Get a single structural chunk."""
    try:
        return service.get_chunk(
            account_id=subject.account_id,
            source_id=source_id,
            chunk_id=chunk_id,
        )
    except ScienceError as exc:
        raise _science_error(
            status.HTTP_404_NOT_FOUND, "chunk_not_found", str(exc)
        ) from exc


@router.post(
    "/sources/{source_id}/revoke",
    response_model=dict[str, Any],
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": SourceError},
        status.HTTP_404_NOT_FOUND: {"model": SourceError},
    },
)
async def revoke_source(
    service: ScienceServiceDep,
    subject: SubjectDep,
    source_id: str,
    request: Request,
) -> dict[str, Any]:
    """Revoke a source so it cannot be used in new evidence."""
    try:
        source_ref = service.revoke_source(
            account_id=subject.account_id,
            source_id=source_id,
            reason="用户撤权",
        )
    except ScienceError as exc:
        raise _science_error(
            status.HTTP_404_NOT_FOUND, "source_not_found", str(exc)
        ) from exc

    # Record an invalidation event so downstream guards (cache, index, runs) activate.
    invalidation_service: InvalidationService = request.app.state.invalidation_service
    invalidation_service.record_invalidation_event(
        subject,
        source_ref,
        InvalidationEventType.SOURCE_RETRACTED,
        "用户撤权",
    )

    return {
        "source_id": source_id,
        "status": "revoked",
        "object_ref": source_ref.model_dump(),
    }



@router.post(
    "/projects/{project_id}/search",
    response_model=SearchResult,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": SourceError},
        status.HTTP_403_FORBIDDEN: {"model": SourceError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": SourceError},
    },
)
async def search_project_sources(
    service: SearchServiceDep,
    subject: SubjectDep,
    project_id: str,
    request: SearchRequest,
) -> SearchResult:
    """Search scientific sources within a project using scoped hybrid retrieval."""
    from science_companion.contracts.projects import ObjectDomain

    scoped_request = request.model_copy(update={"project_id": project_id})
    if scoped_request.object_domain == ObjectDomain.PERSONAL_VAULT:
        scoped_request = scoped_request.model_copy(
            update={"object_domain": ObjectDomain.SHARED_PROJECT}
        )
    return service.search(subject, scoped_request)


@router.post(
    "/search",
    response_model=SearchResult,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": SourceError},
        status.HTTP_403_FORBIDDEN: {"model": SourceError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": SourceError},
    },
)
async def search_personal_sources(
    service: SearchServiceDep,
    subject: SubjectDep,
    request: SearchRequest,
) -> SearchResult:
    """Search personal scientific sources using scoped hybrid retrieval."""
    return service.search(subject, request)


@router.post(
    "/projects/{project_id}/claim-graphs",
    response_model=ClaimGraphResult,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": SourceError},
        status.HTTP_403_FORBIDDEN: {"model": SourceError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": SourceError},
    },
)
async def generate_project_claim_graph(
    service: ClaimServiceDep,
    subject: SubjectDep,
    project_id: str,
    request: ClaimRequest,
) -> ClaimGraphResult:
    """Generate a locatable claim--evidence--citation graph for a project question."""
    from science_companion.contracts.projects import ObjectDomain

    scoped_request = request.model_copy(update={"project_id": project_id})
    if scoped_request.object_domain == ObjectDomain.PERSONAL_VAULT:
        scoped_request = scoped_request.model_copy(
            update={"object_domain": ObjectDomain.SHARED_PROJECT}
        )
    try:
        return service.generate_claim_graph(subject, scoped_request)
    except ScienceError as exc:
        raise _science_error(
            status.HTTP_403_FORBIDDEN, "claim_generation_failed", str(exc)
        ) from exc


@router.post(
    "/claim-graphs",
    response_model=ClaimGraphResult,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": SourceError},
        status.HTTP_403_FORBIDDEN: {"model": SourceError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": SourceError},
    },
)
async def generate_personal_claim_graph(
    service: ClaimServiceDep,
    subject: SubjectDep,
    request: ClaimRequest,
) -> ClaimGraphResult:
    """Generate a locatable claim--evidence--citation graph for a personal question."""
    try:
        return service.generate_claim_graph(subject, request)
    except ScienceError as exc:
        raise _science_error(
            status.HTTP_403_FORBIDDEN, "claim_generation_failed", str(exc)
        ) from exc


@router.get(
    "/claim-graphs/{graph_id}",
    response_model=ClaimGraph,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": SourceError},
        status.HTTP_404_NOT_FOUND: {"model": SourceError},
    },
)
async def get_claim_graph(
    service: ClaimServiceDep,
    subject: SubjectDep,
    graph_id: str,
) -> ClaimGraph:
    """Retrieve a claim graph by id."""
    try:
        return service.get_claim_graph(subject.account_id, graph_id)
    except ScienceError as exc:
        raise _science_error(
            status.HTTP_404_NOT_FOUND, "claim_graph_not_found", str(exc)
        ) from exc


@router.get(
    "/claim-graphs/{graph_id}/publish-gate",
    response_model=PublishGateResult,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": SourceError},
        status.HTTP_404_NOT_FOUND: {"model": SourceError},
    },
)
async def run_claim_graph_publish_gate(
    service: ClaimServiceDep,
    subject: SubjectDep,
    graph_id: str,
) -> PublishGateResult:
    """Re-run the publish gate for a claim graph."""
    try:
        return service.run_publish_gate(subject.account_id, graph_id)
    except ScienceError as exc:
        raise _science_error(
            status.HTTP_404_NOT_FOUND, "claim_graph_not_found", str(exc)
        ) from exc


@router.get(
    "/claim-graphs/{graph_id}/fact-locks",
    response_model=FactLockSet,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": SourceError},
        status.HTTP_404_NOT_FOUND: {"model": SourceError},
    },
)
async def get_fact_locks(
    service: ClaimServiceDep,
    subject: SubjectDep,
    graph_id: str,
) -> FactLockSet:
    """Compile the fact lock set for a claim graph."""
    try:
        return service.compile_fact_locks(subject.account_id, graph_id)
    except ScienceError as exc:
        raise _science_error(
            status.HTTP_404_NOT_FOUND, "claim_graph_not_found", str(exc)
        ) from exc


@router.get(
    "/claim-graphs/{graph_id}/validate",
    response_model=ValidationReport,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": SourceError},
        status.HTTP_404_NOT_FOUND: {"model": SourceError},
    },
)
async def validate_claim_graph(
    service: ClaimServiceDep,
    subject: SubjectDep,
    graph_id: str,
    apply: bool = False,
) -> ValidationReport:
    """Run honest-degradation analysis and return a validation report.

    Pass `apply=true` to update the stored graph status from the report.
    """
    try:
        return service.validate_claim_graph(subject.account_id, graph_id, apply=apply)
    except ScienceError as exc:
        raise _science_error(
            status.HTTP_404_NOT_FOUND, "claim_graph_not_found", str(exc)
        ) from exc


@router.get(
    "/claim-graphs/{graph_id}/scientific-quality-gate",
    response_model=ScientificQualityGateResult,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": SourceError},
        status.HTTP_404_NOT_FOUND: {"model": SourceError},
    },
)
async def run_claim_graph_scientific_quality_gate(
    service: ClaimServiceDep,
    subject: SubjectDep,
    graph_id: str,
) -> ScientificQualityGateResult:
    """Run the scientific quality gate for a claim graph."""
    try:
        return service.run_scientific_quality_gate(subject.account_id, graph_id)
    except ScienceError as exc:
        raise _science_error(
            status.HTTP_404_NOT_FOUND, "claim_graph_not_found", str(exc)
        ) from exc


@router.get(
    "/claim-graphs",
    response_model=list[ClaimGraph],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": SourceError},
    },
)
async def list_claim_graphs(
    service: ClaimServiceDep,
    subject: SubjectDep,
    project_id: str | None = None,
) -> list[ClaimGraph]:
    """List claim graphs accessible to the current account, optionally filtered by project."""
    return service.list_claim_graphs(subject.account_id, project_id=project_id)


@router.get(
    "/claim-graphs/{graph_id}/citations/{citation_id}/verify",
    response_model=CitationValidationResult,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": SourceError},
        status.HTTP_404_NOT_FOUND: {"model": SourceError},
    },
)
async def verify_citation(
    service: ClaimServiceDep,
    subject: SubjectDep,
    graph_id: str,
    citation_id: str,
) -> CitationValidationResult:
    """Re-verify a citation against current source state."""
    try:
        return service.validate_citation(subject.account_id, graph_id, citation_id)
    except ScienceError as exc:
        raise _science_error(
            status.HTTP_404_NOT_FOUND, "citation_not_found", str(exc)
        ) from exc
