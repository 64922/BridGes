"""旧九维画像治理合同的兼容窗口路由。

这些路由只保留退役期间的明确失败语义：不解析请求正文、不读取画像、
不调用模型、不创建任务，也不产生任何画像副作用。最终从 410 收缩为
404 由 Issue 24 统一处理。
"""

from __future__ import annotations

from typing import NoReturn

from fastapi import APIRouter, Request, status

from bridges.api.auth import SubjectDep
from bridges.contracts.retirement import RetiredCapabilityError
from bridges.retirement import raise_retired_capability

router = APIRouter(prefix="/profiles", tags=["profiles-retired"])

_GONE_RESPONSES = {status.HTTP_410_GONE: {"model": RetiredCapabilityError}}
_REPLACEMENT_PATH = "/account/profile"
_RETIRED_ERROR = "profile_governance_retired"
_RETIRED_MESSAGE = "旧画像治理接口已退役，请在四维画像页面修改或撤回已有记录。"


def _gone(request: Request, endpoint: str) -> NoReturn:
    raise_retired_capability(
        request,
        endpoint=endpoint,
        error=_RETIRED_ERROR,
        replacement_path=_REPLACEMENT_PATH,
        message=_RETIRED_MESSAGE,
    )


@router.post("/observations", responses=_GONE_RESPONSES)
async def create_observation(request: Request, _subject: SubjectDep) -> None:
    _gone(request, "profiles.observations.create")


@router.get("/observations", responses=_GONE_RESPONSES)
async def list_observations(request: Request, _subject: SubjectDep) -> None:
    _gone(request, "profiles.observations.list")


@router.get("/observations/{observation_id}", responses=_GONE_RESPONSES)
async def get_observation(
    observation_id: str, request: Request, _subject: SubjectDep
) -> None:
    _gone(request, "profiles.observations.detail")


@router.post("/candidates", responses=_GONE_RESPONSES)
async def propose_candidate(request: Request, _subject: SubjectDep) -> None:
    _gone(request, "profiles.candidates.create")


@router.get("/candidates", responses=_GONE_RESPONSES)
async def list_candidates(request: Request, _subject: SubjectDep) -> None:
    _gone(request, "profiles.candidates.list")


@router.get("/candidates/{candidate_id}", responses=_GONE_RESPONSES)
async def get_candidate(candidate_id: str, request: Request, _subject: SubjectDep) -> None:
    _gone(request, "profiles.candidates.detail")


@router.post("/candidates/{candidate_id}/decision", responses=_GONE_RESPONSES)
async def decide_candidate(
    candidate_id: str, request: Request, _subject: SubjectDep
) -> None:
    _gone(request, "profiles.candidates.decision")


@router.post("/candidates/batch-decision", responses=_GONE_RESPONSES)
async def batch_decide_candidates(request: Request, _subject: SubjectDep) -> None:
    _gone(request, "profiles.candidates.batch-decision")


@router.get("/assertions", responses=_GONE_RESPONSES)
async def list_assertions(request: Request, _subject: SubjectDep) -> None:
    _gone(request, "profiles.assertions.list")


@router.get("/assertions/{assertion_id}", responses=_GONE_RESPONSES)
async def get_assertion(assertion_id: str, request: Request, _subject: SubjectDep) -> None:
    _gone(request, "profiles.assertions.detail")


@router.post("/assertions/manual", responses=_GONE_RESPONSES)
async def create_manual_assertion(request: Request, _subject: SubjectDep) -> None:
    _gone(request, "profiles.assertions.manual")


@router.post("/assertions/{assertion_id}/freeze", responses=_GONE_RESPONSES)
async def freeze_assertion(
    assertion_id: str, request: Request, _subject: SubjectDep
) -> None:
    _gone(request, "profiles.assertions.freeze")


@router.post("/assertions/{assertion_id}/withdraw", responses=_GONE_RESPONSES)
async def withdraw_assertion(
    assertion_id: str, request: Request, _subject: SubjectDep
) -> None:
    _gone(request, "profiles.assertions.withdraw")


@router.post("/assertions/{assertion_id}/unfreeze", responses=_GONE_RESPONSES)
async def unfreeze_assertion(
    assertion_id: str, request: Request, _subject: SubjectDep
) -> None:
    _gone(request, "profiles.assertions.unfreeze")


@router.post("/assertions/{assertion_id}/modify", responses=_GONE_RESPONSES)
async def modify_assertion(
    assertion_id: str, request: Request, _subject: SubjectDep
) -> None:
    _gone(request, "profiles.assertions.modify")


@router.post("/assertions/{assertion_id}/rollback", responses=_GONE_RESPONSES)
async def rollback_assertion(
    assertion_id: str, request: Request, _subject: SubjectDep
) -> None:
    _gone(request, "profiles.assertions.rollback")


@router.post("/assertions/{assertion_id}/delete", responses=_GONE_RESPONSES)
async def delete_assertion(
    assertion_id: str, request: Request, _subject: SubjectDep
) -> None:
    _gone(request, "profiles.assertions.delete")


@router.get("/assertions/{assertion_id}/history", responses=_GONE_RESPONSES)
async def get_assertion_history(
    assertion_id: str, request: Request, _subject: SubjectDep
) -> None:
    _gone(request, "profiles.assertions.history")


@router.get("/export", responses=_GONE_RESPONSES)
async def export_profile(request: Request, _subject: SubjectDep) -> None:
    _gone(request, "profiles.export")


@router.get("/permissions", responses=_GONE_RESPONSES)
async def list_permissions(request: Request, _subject: SubjectDep) -> None:
    _gone(request, "profiles.permissions.list")


@router.put("/permissions", responses=_GONE_RESPONSES)
async def update_permission(request: Request, _subject: SubjectDep) -> None:
    _gone(request, "profiles.permissions.update")


@router.get("/notifications", responses=_GONE_RESPONSES)
async def list_notifications(request: Request, _subject: SubjectDep) -> None:
    _gone(request, "profiles.notifications.list")


@router.post("/notifications/{notification_id}/read", responses=_GONE_RESPONSES)
async def mark_notification_read(
    notification_id: str, request: Request, _subject: SubjectDep
) -> None:
    _gone(request, "profiles.notifications.read")


@router.post("/notifications/{notification_id}/recall", responses=_GONE_RESPONSES)
async def recall_notification(
    notification_id: str, request: Request, _subject: SubjectDep
) -> None:
    _gone(request, "profiles.notifications.recall")


@router.get("/memory-slice", responses=_GONE_RESPONSES)
async def compile_memory_slice(request: Request, _subject: SubjectDep) -> None:
    _gone(request, "profiles.memory-slice.compile")


@router.get("/memory-slices/{slice_id}", responses=_GONE_RESPONSES)
async def get_memory_slice(slice_id: str, request: Request, _subject: SubjectDep) -> None:
    _gone(request, "profiles.memory-slice.detail")


@router.get("/memory-slices/{slice_id}/inspector", responses=_GONE_RESPONSES)
async def inspect_memory_slice(
    slice_id: str, request: Request, _subject: SubjectDep
) -> None:
    _gone(request, "profiles.memory-slice.inspector")


@router.post("/memory-slices/{slice_id}/access-check", responses=_GONE_RESPONSES)
async def check_memory_slice_access(
    slice_id: str, request: Request, _subject: SubjectDep
) -> None:
    _gone(request, "profiles.memory-slice.access-check")


@router.get("/four-dimensions/migration-report", responses=_GONE_RESPONSES)
async def get_four_dimension_migration_report(
    request: Request, _subject: SubjectDep
) -> None:
    _gone(request, "profiles.four-dimensions.migration-report")


@router.post("/four-dimensions/migrate", responses=_GONE_RESPONSES)
async def migrate_four_dimension_records(
    request: Request, _subject: SubjectDep
) -> None:
    _gone(request, "profiles.four-dimensions.migrate")


__all__ = ["router"]
