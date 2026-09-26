"""Science source API routes.

ADR-0030 把「旧科学文章创作路径」列为退役能力：本路由只保留历史只读面
（来源/版本/分块/主张图与各校验门的读取），供旧结果继续查看与导出。
来源上传、版本修正、撤权、科学检索与主张图生成一律稳定返回 410，
历史链接不会误触发新执行。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from bridges.api.auth import SubjectDep
from bridges.contracts.science import (
    ChunkVersion,
    CitationValidationResult,
    ClaimGraph,
    DocumentVersion,
    FactLockSet,
    PublishGateResult,
    ScientificQualityGateResult,
    SourceError,
    SourceProjection,
    SourceSummary,
    ValidationReport,
)
from bridges.retirement import raise_retired_capability
from bridges.science import ClaimEvidenceService, ScienceError, ScienceSourceService

router = APIRouter(prefix="/science", tags=["science"])

_RETIRED_ERROR = "legacy_science_retired"
_RETIRED_MESSAGE = "旧科学文章创作路径已退役，历史来源与结论仍可查看与导出。"
_RETIRED_RESPONSES = {status.HTTP_410_GONE: {"model": SourceError}}


def _retire_science_write(request: Request, endpoint: str) -> None:
    """统一拒绝旧科学创作写入口，不解析请求正文。"""
    raise_retired_capability(
        request,
        endpoint=endpoint,
        error=_RETIRED_ERROR,
        replacement_path="/knowledge-base",
        message=_RETIRED_MESSAGE,
    )


def _get_science_service(request: Request) -> ScienceSourceService:
    service: ScienceSourceService | None = getattr(
        request.app.state, "science_source_service", None
    )
    if service is None:
        raise RuntimeError("ScienceSourceService not attached to application state.")
    return service


def _get_claim_service(request: Request) -> ClaimEvidenceService:
    service: ClaimEvidenceService | None = getattr(
        request.app.state, "claim_evidence_service", None
    )
    if service is None:
        raise RuntimeError("ClaimEvidenceService not attached to application state.")
    return service


ScienceServiceDep = Annotated[ScienceSourceService, Depends(_get_science_service)]
ClaimServiceDep = Annotated[ClaimEvidenceService, Depends(_get_claim_service)]


def _science_error(status_code: int, error: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=SourceError(error=error, message=message).model_dump(),
    )


@router.post(
    "/projects/{project_id}/sources",
    status_code=status.HTTP_410_GONE,
    responses=_RETIRED_RESPONSES,
)
async def upload_source_to_project(
    request: Request,
    _subject: SubjectDep,
    project_id: str,
) -> None:
    """上传来源到项目。已退役：不再接受写入，稳定返回 410。"""
    _retire_science_write(request, "legacy.science.source.upload_project")


@router.post(
    "/sources",
    status_code=status.HTTP_410_GONE,
    responses=_RETIRED_RESPONSES,
)
async def upload_source(
    request: Request,
    _subject: SubjectDep,
) -> None:
    """上传个人来源。已退役：不再接受写入，稳定返回 410。"""
    _retire_science_write(request, "legacy.science.source.upload")


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
    status_code=status.HTTP_410_GONE,
    responses=_RETIRED_RESPONSES,
)
async def create_source_version(
    request: Request,
    _subject: SubjectDep,
    source_id: str,
) -> None:
    """按分块修正创建新版本。已退役：不再接受写入，稳定返回 410。"""
    _retire_science_write(request, "legacy.science.source.version_create")


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
    status_code=status.HTTP_410_GONE,
    responses=_RETIRED_RESPONSES,
)
async def revoke_source(
    request: Request,
    _subject: SubjectDep,
    source_id: str,
) -> None:
    """撤权来源。已退役：不再接受写入，稳定返回 410。"""
    _retire_science_write(request, "legacy.science.source.revoke")



@router.post(
    "/projects/{project_id}/search",
    status_code=status.HTTP_410_GONE,
    responses=_RETIRED_RESPONSES,
)
async def search_project_sources(
    request: Request,
    _subject: SubjectDep,
    project_id: str,
) -> None:
    """项目内科学来源检索。已退役：不再接受执行请求，稳定返回 410。"""
    _retire_science_write(request, "legacy.science.search.project")


@router.post(
    "/search",
    status_code=status.HTTP_410_GONE,
    responses=_RETIRED_RESPONSES,
)
async def search_personal_sources(
    request: Request,
    _subject: SubjectDep,
) -> None:
    """个人科学来源检索。已退役：不再接受执行请求，稳定返回 410。"""
    _retire_science_write(request, "legacy.science.search.personal")


@router.post(
    "/projects/{project_id}/claim-graphs",
    status_code=status.HTTP_410_GONE,
    responses=_RETIRED_RESPONSES,
)
async def generate_project_claim_graph(
    request: Request,
    _subject: SubjectDep,
    project_id: str,
) -> None:
    """生成项目主张—证据图。已退役：不再接受执行请求，稳定返回 410。"""
    _retire_science_write(request, "legacy.science.claim_graph.project")


@router.post(
    "/claim-graphs",
    status_code=status.HTTP_410_GONE,
    responses=_RETIRED_RESPONSES,
)
async def generate_personal_claim_graph(
    request: Request,
    _subject: SubjectDep,
) -> None:
    """生成个人主张—证据图。已退役：不再接受执行请求，稳定返回 410。"""
    _retire_science_write(request, "legacy.science.claim_graph.personal")


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
