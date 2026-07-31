"""领域包专家工作台 API 路由。

路由实现阶段控制台：登记、三签、语义 Diff、灰度与发行。
person_id 使用当前账户 ID（一个账户代表一个自然人），职责冲突
按 person_id + 包版本摘要检查，不按按钮权限判断。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from science_companion.api.auth import SubjectDep
from science_companion.contracts.domain import (
    AttestationConclusion,
    ConflictDisclosure,
    ConflictOfInterestDeclaration,
    GrayReleaseCandidate,
    PackRelease,
    QualificationRecord,
    ReviewAttestation,
    SemanticDiff,
    WorkbenchPackRecord,
)
from science_companion.domain.workbench import (
    DomainPackWorkbenchError,
    DomainPackWorkbenchService,
)

router = APIRouter(prefix="/domain-packs", tags=["domain-packs"])


def _get_workbench(request: Request) -> DomainPackWorkbenchService:
    service: DomainPackWorkbenchService | None = getattr(
        request.app.state, "domain_pack_workbench", None
    )
    if service is None:
        raise RuntimeError("DomainPackWorkbenchService not attached to application state.")
    return service


WorkbenchDep = Annotated[DomainPackWorkbenchService, Depends(_get_workbench)]


def _workbench_error(status_code: int, error: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"error": error, "message": message},
    )


def _handle(exc: DomainPackWorkbenchError) -> HTTPException:
    if exc.code == "not_found":
        return _workbench_error(status.HTTP_404_NOT_FOUND, exc.code, str(exc))
    if exc.code in {"role_required", "role_conflict"}:
        return _workbench_error(status.HTTP_403_FORBIDDEN, exc.code, str(exc))
    # 其余均为状态或签名门冲突，与路由 responses 声明的 409 一致。
    return _workbench_error(status.HTTP_409_CONFLICT, exc.code, str(exc))


def _participant_ids(record: WorkbenchPackRecord) -> set[str]:
    return {
        record.maintainer_id,
        *(item for item in (record.reviewer_id, record.releaser_id) if item),
    }


def _require_participant(
    record: WorkbenchPackRecord, account_id: str
) -> None:
    """普通用户不进入本工作台；只有参与者可以查看治理记录。"""
    if account_id not in _participant_ids(record):
        raise _workbench_error(
            status.HTTP_403_FORBIDDEN,
            "role_required",
            "只有该包版本的维护者、独立复核者或平台发行者可以查看治理记录。",
        )


class RegisterPackRequest(BaseModel):
    """登记一个已注册的领域包版本到专家工作台。"""

    pack_id: str = Field(min_length=1)
    version: str = Field(min_length=1)


class SignatureRequest(BaseModel):
    """提交签名时的意见与结论。"""

    opinion: str = Field(default="")
    conclusion: AttestationConclusion = AttestationConclusion.APPROVE


class AssignRoleRequest(BaseModel):
    """分配独立复核者或平台发行者。"""

    person_id: str = Field(min_length=1)


class ConflictDeclarationRequest(BaseModel):
    """本版本利益冲突声明。"""

    disclosures: list[str] = Field(default_factory=list)


class ConflictDisclosureRequest(BaseModel):
    """少数意见与未决依据。"""

    item_ref: str = Field(min_length=1)
    minority_opinion: str = Field(min_length=1)
    basis: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    decision: str = Field(default="")


@router.get(
    "/workbench",
    response_model=list[WorkbenchPackRecord],
    responses={status.HTTP_401_UNAUTHORIZED: {"description": "未认证"}},
)
async def list_workbench(
    service: WorkbenchDep,
    subject: SubjectDep,
) -> list[WorkbenchPackRecord]:
    """列出当前用户参与（维护者、复核者或发行者）的包版本治理记录。"""
    return [
        record
        for record in service.list_records()
        if subject.account_id in _participant_ids(record)
    ]


@router.post(
    "/workbench/register",
    response_model=WorkbenchPackRecord,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "未认证"},
        status.HTTP_404_NOT_FOUND: {"description": "包版本未登记"},
        status.HTTP_409_CONFLICT: {"description": "工作台已登记该版本"},
    },
)
async def register_pack(
    service: WorkbenchDep,
    subject: SubjectDep,
    request: RegisterPackRequest,
) -> WorkbenchPackRecord:
    """当前用户以内容维护者身份登记一个包版本。"""
    try:
        loaded = service.get_loaded(request.pack_id, request.version)
        return service.register_pack(subject.account_id, loaded)
    except DomainPackWorkbenchError as exc:
        raise _handle(exc) from exc


@router.get(
    "/workbench/{pack_id}/{version}",
    response_model=WorkbenchPackRecord,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "未认证"},
        status.HTTP_404_NOT_FOUND: {"description": "工作台未登记"},
    },
)
async def get_workbench_record(
    service: WorkbenchDep,
    subject: SubjectDep,
    pack_id: str,
    version: str,
) -> WorkbenchPackRecord:
    """读取一个包版本的完整工作台治理记录（仅参与者）。"""
    try:
        record = service.get_record(pack_id, version)
        _require_participant(record, subject.account_id)
        return record
    except DomainPackWorkbenchError as exc:
        raise _handle(exc) from exc


@router.get(
    "/workbench/{pack_id}/{version}/semantic-diff",
    response_model=SemanticDiff,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "未认证"},
        status.HTTP_404_NOT_FOUND: {"description": "工作台未登记"},
    },
)
async def get_semantic_diff(
    service: WorkbenchDep,
    subject: SubjectDep,
    pack_id: str,
    version: str,
) -> SemanticDiff:
    """查看与上一版本（或空基线）之间的判定差异（仅参与者）。"""
    try:
        record = service.get_record(pack_id, version)
        _require_participant(record, subject.account_id)
        return service.semantic_diff_for(pack_id, version)
    except DomainPackWorkbenchError as exc:
        raise _handle(exc) from exc


@router.post(
    "/workbench/{pack_id}/{version}/content-signature",
    response_model=ReviewAttestation,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "未认证"},
        status.HTTP_403_FORBIDDEN: {"description": "不是内容维护者"},
        status.HTTP_404_NOT_FOUND: {"description": "工作台未登记"},
        status.HTTP_409_CONFLICT: {"description": "签名门不满足"},
    },
)
async def submit_content_signature(
    service: WorkbenchDep,
    subject: SubjectDep,
    pack_id: str,
    version: str,
    request: SignatureRequest,
) -> ReviewAttestation:
    """内容维护者对规范化包摘要提交内容签名。"""
    try:
        return service.submit_content_signature(
            subject.account_id,
            pack_id,
            version,
            conclusion=request.conclusion,
            opinion=request.opinion,
        )
    except DomainPackWorkbenchError as exc:
        raise _handle(exc) from exc


@router.post(
    "/workbench/{pack_id}/{version}/reviewer",
    response_model=WorkbenchPackRecord,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "未认证"},
        status.HTTP_403_FORBIDDEN: {"description": "职责冲突或角色不符"},
        status.HTTP_404_NOT_FOUND: {"description": "工作台未登记"},
    },
)
async def assign_reviewer(
    service: WorkbenchDep,
    subject: SubjectDep,
    pack_id: str,
    version: str,
    request: AssignRoleRequest,
) -> WorkbenchPackRecord:
    """内容维护者分配独立复核者；同人双角色被拒绝。"""
    try:
        return service.assign_reviewer(
            subject.account_id, pack_id, version, request.person_id
        )
    except DomainPackWorkbenchError as exc:
        raise _handle(exc) from exc


@router.post(
    "/workbench/{pack_id}/{version}/releaser",
    response_model=WorkbenchPackRecord,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "未认证"},
        status.HTTP_403_FORBIDDEN: {"description": "职责冲突或角色不符"},
        status.HTTP_404_NOT_FOUND: {"description": "工作台未登记"},
    },
)
async def assign_releaser(
    service: WorkbenchDep,
    subject: SubjectDep,
    pack_id: str,
    version: str,
    request: AssignRoleRequest,
) -> WorkbenchPackRecord:
    """内容维护者分配平台发行者；发行者不能兼任维护者或复核者。"""
    try:
        return service.assign_releaser(
            subject.account_id, pack_id, version, request.person_id
        )
    except DomainPackWorkbenchError as exc:
        raise _handle(exc) from exc


@router.post(
    "/workbench/{pack_id}/{version}/independent-signature",
    response_model=ReviewAttestation,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "未认证"},
        status.HTTP_403_FORBIDDEN: {"description": "职责冲突、角色或资质不符"},
        status.HTTP_404_NOT_FOUND: {"description": "工作台未登记"},
        status.HTTP_409_CONFLICT: {"description": "夹具覆盖或重放失败"},
    },
)
async def submit_independent_signature(
    service: WorkbenchDep,
    subject: SubjectDep,
    pack_id: str,
    version: str,
    request: SignatureRequest,
) -> ReviewAttestation:
    """独立复核者重跑夹具后提交独立验证签名。"""
    try:
        return service.submit_independent_signature(
            subject.account_id,
            pack_id,
            version,
            conclusion=request.conclusion,
            opinion=request.opinion,
        )
    except DomainPackWorkbenchError as exc:
        raise _handle(exc) from exc


@router.post(
    "/workbench/{pack_id}/{version}/gray-release",
    response_model=GrayReleaseCandidate,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "未认证"},
        status.HTTP_403_FORBIDDEN: {"description": "角色不符"},
        status.HTTP_404_NOT_FOUND: {"description": "工作台未登记"},
        status.HTTP_409_CONFLICT: {"description": "签名或摘要不满足"},
    },
)
async def prepare_gray_release(
    service: WorkbenchDep,
    subject: SubjectDep,
    pack_id: str,
    version: str,
) -> GrayReleaseCandidate:
    """生成可发行候选；灰度结果不会自动激活领域包。"""
    try:
        return service.prepare_gray_release(subject.account_id, pack_id, version)
    except DomainPackWorkbenchError as exc:
        raise _handle(exc) from exc


@router.get(
    "/workbench/{pack_id}/{version}/gray-release",
    response_model=GrayReleaseCandidate | None,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "未认证"},
        status.HTTP_404_NOT_FOUND: {"description": "工作台未登记"},
    },
)
async def get_gray_release(
    service: WorkbenchDep,
    subject: SubjectDep,
    pack_id: str,
    version: str,
) -> GrayReleaseCandidate | None:
    """读取最近的灰度候选（仅参与者）。"""
    try:
        record = service.get_record(pack_id, version)
        _require_participant(record, subject.account_id)
        return service.get_gray_candidate(pack_id, version)
    except DomainPackWorkbenchError as exc:
        raise _handle(exc) from exc


@router.post(
    "/workbench/{pack_id}/{version}/release",
    response_model=PackRelease,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "未认证"},
        status.HTTP_403_FORBIDDEN: {"description": "不是平台发行者"},
        status.HTTP_404_NOT_FOUND: {"description": "工作台未登记"},
        status.HTTP_409_CONFLICT: {"description": "签名、灰度或摘要门不满足"},
    },
)
async def release_pack(
    service: WorkbenchDep,
    subject: SubjectDep,
    pack_id: str,
    version: str,
) -> PackRelease:
    """平台发行者追加发行签名并激活；任何门变化都会回到相应阶段。"""
    try:
        return service.release(subject.account_id, pack_id, version)
    except DomainPackWorkbenchError as exc:
        raise _handle(exc) from exc


@router.post(
    "/qualifications",
    response_model=QualificationRecord,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "未认证"},
        status.HTTP_409_CONFLICT: {"description": "资质已登记"},
    },
)
async def register_qualification(
    service: WorkbenchDep,
    subject: SubjectDep,
    qualification: QualificationRecord,
) -> QualificationRecord:
    """登记当前账户自己的资质记录；不能为他人登记。"""
    if qualification.person_id != subject.account_id:
        raise _workbench_error(
            status.HTTP_403_FORBIDDEN,
            "role_required",
            "只能为当前账户登记资质记录。",
        )
    try:
        service.register_qualification(qualification)
        return qualification
    except DomainPackWorkbenchError as exc:
        raise _handle(exc) from exc


@router.get(
    "/qualifications",
    response_model=list[QualificationRecord],
    responses={status.HTTP_401_UNAUTHORIZED: {"description": "未认证"}},
)
async def list_qualifications(
    service: WorkbenchDep,
    subject: SubjectDep,
) -> list[QualificationRecord]:
    """列出当前账户（自然人）的资质记录。"""
    return service.list_qualifications(subject.account_id)


@router.post(
    "/workbench/{pack_id}/{version}/conflict-of-interest",
    response_model=ConflictOfInterestDeclaration,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "未认证"},
        status.HTTP_404_NOT_FOUND: {"description": "工作台未登记"},
    },
)
async def declare_conflict_of_interest(
    service: WorkbenchDep,
    subject: SubjectDep,
    pack_id: str,
    version: str,
    request: ConflictDeclarationRequest,
) -> ConflictOfInterestDeclaration:
    """对当前包版本声明利益冲突；签名前必须存在。"""
    try:
        return service.declare_conflict_of_interest(
            subject.account_id, pack_id, version, request.disclosures
        )
    except DomainPackWorkbenchError as exc:
        raise _handle(exc) from exc


@router.post(
    "/workbench/{pack_id}/{version}/conflict-disclosure",
    response_model=ConflictDisclosure,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "未认证"},
        status.HTTP_403_FORBIDDEN: {"description": "不是参与者"},
        status.HTTP_404_NOT_FOUND: {"description": "工作台未登记"},
    },
)
async def add_conflict_disclosure(
    service: WorkbenchDep,
    subject: SubjectDep,
    pack_id: str,
    version: str,
    request: ConflictDisclosureRequest,
) -> ConflictDisclosure:
    """参与者登记少数意见与未决依据；不能迫使少数方签署。"""
    try:
        record = service.get_record(pack_id, version)
        _require_participant(record, subject.account_id)
        return service.add_conflict_disclosure(
            pack_id,
            version,
            item_ref=request.item_ref,
            minority_opinion=request.minority_opinion,
            basis=request.basis,
            missing_evidence=request.missing_evidence,
            decision=request.decision,
        )
    except DomainPackWorkbenchError as exc:
        raise _handle(exc) from exc
