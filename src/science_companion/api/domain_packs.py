"""领域包专家工作台 API 路由。

路由实现阶段控制台：登记、三签、语义 Diff、灰度、发行、失效、
紧急撤销、重验证与受信回滚。
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
    PackImpactCategory,
    PackImpactSet,
    PackInvalidationEvent,
    PackInvalidationStage,
    PackInvalidationTrigger,
    PackRelease,
    PackRollbackRecord,
    QualificationRecord,
    RevalidationReport,
    ReviewAttestation,
    ReviewRole,
    RevocationEvent,
    SemanticDiff,
    WorkbenchPackRecord,
)
from science_companion.domain.pack_lifecycle import (
    DomainPackLifecycleError,
    DomainPackLifecycleService,
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


def _get_lifecycle(request: Request) -> DomainPackLifecycleService:
    service: DomainPackLifecycleService | None = getattr(
        request.app.state, "domain_pack_lifecycle", None
    )
    if service is None:
        raise RuntimeError("DomainPackLifecycleService not attached to application state.")
    return service


WorkbenchDep = Annotated[DomainPackWorkbenchService, Depends(_get_workbench)]
LifecycleDep = Annotated[DomainPackLifecycleService, Depends(_get_lifecycle)]


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


def _handle_lifecycle(exc: DomainPackLifecycleError) -> HTTPException:
    if exc.code == "not_found":
        return _workbench_error(status.HTTP_404_NOT_FOUND, exc.code, str(exc))
    if exc.code in {"role_required", "role_conflict", "second_factor_required"}:
        return _workbench_error(status.HTTP_403_FORBIDDEN, exc.code, str(exc))
    # 其余均为状态机、影响集、重验证或信任门冲突，与 409 一致。
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


class InvalidationEventRequest(BaseModel):
    """登记领域包失效事件。"""

    pack_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    trigger: PackInvalidationTrigger
    reason: str = Field(min_length=1)
    emergency: bool = False


class AdvanceStageRequest(BaseModel):
    """推进失效事件阶段。"""

    to_stage: PackInvalidationStage
    note: str = Field(default="")


class RevalidateRequest(BaseModel):
    """登记一个影响类别中已完成重验证的对象。"""

    area: PackImpactCategory
    ref_ids: list[str] = Field(default_factory=list)
    failed: list[str] = Field(default_factory=list)


class RevocationRequest(BaseModel):
    """安全管理员紧急撤销请求；要求二次认证并确认影响范围。"""

    pack_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    trigger: PackInvalidationTrigger
    reason: str = Field(min_length=1)
    second_factor: str = Field(min_length=1)


class RollbackRequest(BaseModel):
    """提议回滚到仍受信、依赖兼容且通过平台下限的旧版。"""

    pack_id: str = Field(min_length=1)
    from_version: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class RollbackConfirmRequest(BaseModel):
    """独立复核者或平台发行者确认回滚。"""

    role: ReviewRole
    conclusion: AttestationConclusion = AttestationConclusion.APPROVE
    opinion: str = Field(default="")


def _require_lifecycle_actor(
    lifecycle: DomainPackLifecycleService,
    workbench: DomainPackWorkbenchService,
    account_id: str,
    pack_id: str,
    version: str,
) -> None:
    """普通用户不进入本工作台；失效与回滚操作只允许参与者或安全管理员。"""
    record = workbench.get_record(pack_id, version)
    participants = {
        record.maintainer_id,
        *(item for item in (record.reviewer_id, record.releaser_id) if item),
    }
    if account_id not in participants and not lifecycle.is_security_admin(account_id):
        raise _workbench_error(
            status.HTTP_403_FORBIDDEN,
            "role_required",
            "只有该包版本的维护者、独立复核者、平台发行者或安全管理员可以查看失效记录。",
        )


@router.post(
    "/security-admins",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "未认证"},
    },
)
async def register_security_admin(
    service: LifecycleDep,
    subject: SubjectDep,
) -> None:
    """登记当前账户为安全管理员（平台引导身份；类比机构创建者为管理员）。"""
    service.register_security_admin(subject.account_id)


@router.get(
    "/security-admins",
    responses={status.HTTP_401_UNAUTHORIZED: {"description": "未认证"}},
)
async def security_admin_status(
    service: LifecycleDep,
    subject: SubjectDep,
) -> dict[str, bool]:
    """当前账户是否具备安全管理员身份。"""
    return {"is_security_admin": service.is_security_admin(subject.account_id)}


@router.post(
    "/invalidations",
    response_model=PackInvalidationEvent,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "未认证"},
        status.HTTP_403_FORBIDDEN: {"description": "角色不符"},
        status.HTTP_404_NOT_FOUND: {"description": "工作台未登记"},
    },
)
async def record_invalidation(
    lifecycle: LifecycleDep,
    workbench: WorkbenchDep,
    subject: SubjectDep,
    request: InvalidationEventRequest,
) -> PackInvalidationEvent:
    """记录一次失效事件（DETECTED；紧急失效直接进入 CONTAINED）。"""
    _require_lifecycle_actor(
        lifecycle, workbench, subject.account_id, request.pack_id, request.version
    )
    try:
        return lifecycle.record_invalidation_event(
            subject.account_id,
            request.pack_id,
            request.version,
            request.trigger,
            request.reason,
            emergency=request.emergency,
        )
    except DomainPackLifecycleError as exc:
        raise _handle_lifecycle(exc) from exc


@router.get(
    "/invalidations",
    response_model=list[PackInvalidationEvent],
    responses={status.HTTP_401_UNAUTHORIZED: {"description": "未认证"}},
)
async def list_invalidations(
    lifecycle: LifecycleDep,
    subject: SubjectDep,
    pack_id: str | None = None,
) -> list[PackInvalidationEvent]:
    """列出当前自然人可以处置的失效事件；可按包过滤。"""
    return lifecycle.list_invalidation_events(subject.account_id, pack_id=pack_id)


@router.get(
    "/invalidations/{event_id}",
    response_model=PackInvalidationEvent,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "未认证"},
        status.HTTP_403_FORBIDDEN: {"description": "角色不符"},
        status.HTTP_404_NOT_FOUND: {"description": "失效事件不存在"},
    },
)
async def get_invalidation(
    lifecycle: LifecycleDep,
    workbench: WorkbenchDep,
    subject: SubjectDep,
    event_id: str,
) -> PackInvalidationEvent:
    """读取一个失效事件（仅参与者或安全管理员）。"""
    try:
        event = lifecycle.get_invalidation_event(event_id)
        _require_lifecycle_actor(
            lifecycle, workbench, subject.account_id, event.pack_id, event.pack_version
        )
        return event
    except DomainPackLifecycleError as exc:
        raise _handle_lifecycle(exc) from exc


@router.post(
    "/invalidations/{event_id}/advance",
    response_model=PackInvalidationEvent,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "未认证"},
        status.HTTP_403_FORBIDDEN: {"description": "角色不符"},
        status.HTTP_404_NOT_FOUND: {"description": "失效事件不存在"},
        status.HTTP_409_CONFLICT: {"description": "状态机或硬门冲突"},
    },
)
async def advance_invalidation(
    lifecycle: LifecycleDep,
    workbench: WorkbenchDep,
    subject: SubjectDep,
    event_id: str,
    request: AdvanceStageRequest,
) -> PackInvalidationEvent:
    """按 DETECTED→TRIAGED→CONTAINED→…→CLOSED 推进失效事件。"""
    try:
        event = lifecycle.get_invalidation_event(event_id)
        _require_lifecycle_actor(
            lifecycle, workbench, subject.account_id, event.pack_id, event.pack_version
        )
        return lifecycle.advance_stage(
            subject.account_id,
            event_id,
            request.to_stage,
            note=request.note,
        )
    except DomainPackLifecycleError as exc:
        raise _handle_lifecycle(exc) from exc


@router.post(
    "/invalidations/{event_id}/resolve-impact",
    response_model=PackImpactSet,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "未认证"},
        status.HTTP_403_FORBIDDEN: {"description": "角色不符"},
        status.HTTP_404_NOT_FOUND: {"description": "失效事件不存在"},
    },
)
async def resolve_impact(
    lifecycle: LifecycleDep,
    workbench: WorkbenchDep,
    subject: SubjectDep,
    event_id: str,
) -> PackImpactSet:
    """构建影响集：包、运行、Claim、Evidence、Wording、产物、项目和用户动作。"""
    try:
        event = lifecycle.get_invalidation_event(event_id)
        _require_lifecycle_actor(
            lifecycle, workbench, subject.account_id, event.pack_id, event.pack_version
        )
        return lifecycle.resolve_impact(subject.account_id, event_id)
    except DomainPackLifecycleError as exc:
        raise _handle_lifecycle(exc) from exc


@router.get(
    "/invalidations/{event_id}/impact",
    response_model=PackImpactSet,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "未认证"},
        status.HTTP_403_FORBIDDEN: {"description": "角色不符"},
        status.HTTP_404_NOT_FOUND: {"description": "失效事件或影响集不存在"},
    },
)
async def get_impact(
    lifecycle: LifecycleDep,
    workbench: WorkbenchDep,
    subject: SubjectDep,
    event_id: str,
) -> PackImpactSet:
    """读取失效事件的影响集（仅参与者或安全管理员）。"""
    try:
        event = lifecycle.get_invalidation_event(event_id)
        _require_lifecycle_actor(
            lifecycle, workbench, subject.account_id, event.pack_id, event.pack_version
        )
        return lifecycle.get_impact_set(event_id)
    except DomainPackLifecycleError as exc:
        raise _handle_lifecycle(exc) from exc


@router.post(
    "/invalidations/{event_id}/revalidate",
    response_model=RevalidationReport,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "未认证"},
        status.HTTP_403_FORBIDDEN: {"description": "角色不符"},
        status.HTTP_404_NOT_FOUND: {"description": "失效事件不存在"},
        status.HTTP_409_CONFLICT: {"description": "对象不在影响带或类别无效"},
    },
)
async def report_revalidated(
    lifecycle: LifecycleDep,
    workbench: WorkbenchDep,
    subject: SubjectDep,
    event_id: str,
    request: RevalidateRequest,
) -> RevalidationReport:
    """登记一个影响类别中已完成重验证的对象。"""
    try:
        event = lifecycle.get_invalidation_event(event_id)
        _require_lifecycle_actor(
            lifecycle, workbench, subject.account_id, event.pack_id, event.pack_version
        )
        return lifecycle.report_revalidated(
            subject.account_id,
            event_id,
            request.area,
            request.ref_ids,
            failed=request.failed,
        )
    except DomainPackLifecycleError as exc:
        raise _handle_lifecycle(exc) from exc


@router.get(
    "/invalidations/{event_id}/revalidation",
    response_model=RevalidationReport | None,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "未认证"},
        status.HTTP_403_FORBIDDEN: {"description": "角色不符"},
        status.HTTP_404_NOT_FOUND: {"description": "失效事件不存在"},
    },
)
async def get_revalidation(
    lifecycle: LifecycleDep,
    workbench: WorkbenchDep,
    subject: SubjectDep,
    event_id: str,
) -> RevalidationReport | None:
    """读取失效事件的重验证报告（仅参与者或安全管理员）。"""
    try:
        event = lifecycle.get_invalidation_event(event_id)
        _require_lifecycle_actor(
            lifecycle, workbench, subject.account_id, event.pack_id, event.pack_version
        )
        return lifecycle.get_revalidation_report(event_id)
    except DomainPackLifecycleError as exc:
        raise _handle_lifecycle(exc) from exc


@router.post(
    "/revocations",
    response_model=dict[str, PackInvalidationEvent | RevocationEvent],
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "未认证"},
        status.HTTP_403_FORBIDDEN: {"description": "不是安全管理员或缺少二次认证"},
        status.HTTP_404_NOT_FOUND: {"description": "工作台未登记"},
        status.HTTP_409_CONFLICT: {"description": "撤销门冲突"},
    },
)
async def emergency_revoke(
    lifecycle: LifecycleDep,
    subject: SubjectDep,
    request: RevocationRequest,
) -> dict[str, PackInvalidationEvent | RevocationEvent]:
    """安全管理员紧急撤销：阻止新运行，但不能编辑规则或直接发布替代版本。"""
    try:
        event, revocation = lifecycle.emergency_revoke(
            subject.account_id,
            request.pack_id,
            request.version,
            request.trigger,
            request.reason,
            second_factor=request.second_factor,
        )
        return {"event": event, "revocation": revocation}
    except DomainPackLifecycleError as exc:
        raise _handle_lifecycle(exc) from exc


@router.get(
    "/revocations",
    response_model=list[RevocationEvent],
    responses={status.HTTP_401_UNAUTHORIZED: {"description": "未认证"}},
)
async def list_revocations(
    lifecycle: LifecycleDep,
    subject: SubjectDep,
) -> list[RevocationEvent]:
    """列出当前自然人可以处置的紧急撤销事件。"""
    return lifecycle.list_revocations(subject.account_id)


@router.post(
    "/rollbacks",
    response_model=PackRollbackRecord,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "未认证"},
        status.HTTP_403_FORBIDDEN: {"description": "角色不符"},
        status.HTTP_404_NOT_FOUND: {"description": "工作台未登记"},
        status.HTTP_409_CONFLICT: {"description": "没有受信回滚目标或门冲突"},
    },
)
async def propose_rollback(
    lifecycle: LifecycleDep,
    workbench: WorkbenchDep,
    subject: SubjectDep,
    request: RollbackRequest,
) -> PackRollbackRecord:
    """提议回滚到仍受信、依赖兼容且通过平台下限的最高旧版。"""
    _require_lifecycle_actor(
        lifecycle, workbench, subject.account_id, request.pack_id, request.from_version
    )
    try:
        return lifecycle.propose_rollback(
            subject.account_id,
            request.pack_id,
            request.from_version,
            request.reason,
        )
    except DomainPackLifecycleError as exc:
        raise _handle_lifecycle(exc) from exc


@router.get(
    "/rollbacks",
    response_model=list[PackRollbackRecord],
    responses={status.HTTP_401_UNAUTHORIZED: {"description": "未认证"}},
)
async def list_rollbacks(
    lifecycle: LifecycleDep,
    subject: SubjectDep,
) -> list[PackRollbackRecord]:
    """列出当前自然人可以处置的回滚记录。"""
    return lifecycle.list_rollbacks(subject.account_id)


@router.get(
    "/rollbacks/{rollback_id}",
    response_model=PackRollbackRecord,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "未认证"},
        status.HTTP_404_NOT_FOUND: {"description": "回滚记录不存在"},
    },
)
async def get_rollback(
    lifecycle: LifecycleDep,
    workbench: WorkbenchDep,
    subject: SubjectDep,
    rollback_id: str,
) -> PackRollbackRecord:
    """读取一条回滚记录（仅参与者或安全管理员）。"""
    try:
        rollback = lifecycle.get_rollback(rollback_id)
        _require_lifecycle_actor(
            lifecycle,
            workbench,
            subject.account_id,
            rollback.pack_id,
            rollback.from_version,
        )
        return rollback
    except DomainPackLifecycleError as exc:
        raise _handle_lifecycle(exc) from exc


@router.post(
    "/rollbacks/{rollback_id}/confirm",
    response_model=PackRollbackRecord,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "未认证"},
        status.HTTP_403_FORBIDDEN: {"description": "角色不符"},
        status.HTTP_404_NOT_FOUND: {"description": "回滚记录不存在"},
        status.HTTP_409_CONFLICT: {"description": "已确认或回滚已关闭"},
    },
)
async def confirm_rollback(
    lifecycle: LifecycleDep,
    subject: SubjectDep,
    rollback_id: str,
    request: RollbackConfirmRequest,
) -> PackRollbackRecord:
    """独立复核者或平台发行者确认回滚影响与目标版本。"""
    try:
        return lifecycle.confirm_rollback(
            subject.account_id,
            rollback_id,
            role=request.role,
            conclusion=request.conclusion,
            opinion=request.opinion,
        )
    except DomainPackLifecycleError as exc:
        raise _handle_lifecycle(exc) from exc


@router.post(
    "/rollbacks/{rollback_id}/execute",
    response_model=PackRollbackRecord,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "未认证"},
        status.HTTP_403_FORBIDDEN: {"description": "不是平台发行者"},
        status.HTTP_404_NOT_FOUND: {"description": "回滚记录不存在"},
        status.HTTP_409_CONFLICT: {"description": "未经双方确认或夹具未通过"},
    },
)
async def execute_rollback(
    lifecycle: LifecycleDep,
    subject: SubjectDep,
    rollback_id: str,
) -> PackRollbackRecord:
    """平台发行者执行回滚：把受信旧版重新设为项目可选版本。

    被撤销版本不会被复活；回滚只切换包的可选默认版本。
    """
    try:
        return lifecycle.execute_rollback(subject.account_id, rollback_id)
    except DomainPackLifecycleError as exc:
        raise _handle_lifecycle(exc) from exc


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
