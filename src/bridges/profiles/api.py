"""Profile observation-candidate API routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from bridges.api.auth import SubjectDep
from bridges.contracts.profiles import (
    CandidateDecision,
    FourDimensionMigrationReport,
    FourDimensionProfileModifyRequest,
    FourDimensionProfileRecord,
    FourDimensionProfileWithdrawRequest,
    ManualAssertionCreateRequest,
    ProfileAssertion,
    ProfileAssertionHistory,
    ProfileAssertionModifyRequest,
    ProfileAssertionRollbackRequest,
    ProfileBatchCandidateDecisionRequest,
    ProfileBatchCandidateResult,
    ProfileCandidate,
    ProfileCandidateCreateRequest,
    ProfileDeleteRequest,
    ProfileError,
    ProfileExport,
    ProfileFreezeRequest,
    ProfileNotification,
    ProfileObservation,
    ProfileObservationCreateRequest,
    ProfilePermission,
    ProfilePermissionUpdateRequest,
    ProfileSensitivityClass,
    ProfileSlice,
)
from bridges.profiles import ProfileService
from bridges.profiles.adapters import ProfileError as ProfileAdapterError
from bridges.profiles.four_dimensions import (
    FourDimensionProfileError,
    FourDimensionProfileService,
)

router = APIRouter(prefix="/profiles", tags=["profiles"])


def _get_profile_service(request: Request) -> ProfileService:
    service: ProfileService | None = getattr(request.app.state, "profile_service", None)
    if service is None:
        raise RuntimeError("ProfileService not attached to application state.")
    return service


ProfileServiceDep = Annotated[ProfileService, Depends(_get_profile_service)]


def _get_four_dimension_profile_service(request: Request) -> FourDimensionProfileService:
    service: FourDimensionProfileService | None = getattr(
        request.app.state, "four_dimension_profile_service", None
    )
    if service is None:
        raise RuntimeError("FourDimensionProfileService not attached to application state.")
    return service


FourDimensionProfileServiceDep = Annotated[
    FourDimensionProfileService, Depends(_get_four_dimension_profile_service)
]


def _profile_error(status_code: int, error: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=ProfileError(error=error, message=message).model_dump(),
    )


def _assertion_error(exc: ProfileAdapterError, failure_code: str) -> HTTPException:
    """Map account-scoped assertion failures to 404 or 422 without leaking state."""
    message = str(exc)
    if "访问权限" in message or "不存在" in message:
        return _profile_error(
            status.HTTP_404_NOT_FOUND, "assertion_not_found", message
        )
    return _profile_error(
        status.HTTP_422_UNPROCESSABLE_CONTENT, failure_code, message
    )


def _four_dimension_error(
    exc: FourDimensionProfileError, failure_code: str
) -> HTTPException:
    message = str(exc)
    if "对象不存在" in message or "访问权限" in message:
        return _profile_error(status.HTTP_404_NOT_FOUND, "four_dimension_not_found", message)
    if "版本冲突" in message or "已撤回" in message:
        return _profile_error(status.HTTP_409_CONFLICT, "four_dimension_conflict", message)
    return _profile_error(status.HTTP_422_UNPROCESSABLE_CONTENT, failure_code, message)


@router.post(
    "/observations",
    response_model=ProfileObservation,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ProfileError},
        status.HTTP_403_FORBIDDEN: {"model": ProfileError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ProfileError},
    },
)
async def create_observation(
    service: ProfileServiceDep,
    subject: SubjectDep,
    request: ProfileObservationCreateRequest,
) -> ProfileObservation:
    """Record a traceable profile observation owned by the current account."""
    if request.owner_account_id != subject.account_id:
        raise _profile_error(
            status.HTTP_403_FORBIDDEN,
            "ownership_mismatch",
            "只能为自己的账户记录画像观察。",
        )
    try:
        return service.record_observation(request)
    except ProfileAdapterError as exc:
        raise _profile_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "observation_failed",
            str(exc),
        ) from exc


@router.get(
    "/observations",
    response_model=list[ProfileObservation],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ProfileError},
    },
)
async def list_observations(
    service: ProfileServiceDep,
    subject: SubjectDep,
) -> list[ProfileObservation]:
    """List profile observations for the current account."""
    return service.list_observations(subject.account_id)


@router.get(
    "/observations/{observation_id}",
    response_model=ProfileObservation,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ProfileError},
        status.HTTP_404_NOT_FOUND: {"model": ProfileError},
    },
)
async def get_observation(
    service: ProfileServiceDep,
    subject: SubjectDep,
    observation_id: str,
) -> ProfileObservation:
    """Get a single profile observation."""
    try:
        return service.get_observation(subject.account_id, observation_id)
    except ProfileAdapterError as exc:
        raise _profile_error(
            status.HTTP_404_NOT_FOUND,
            "observation_not_found",
            str(exc),
        ) from exc


@router.post(
    "/candidates",
    response_model=ProfileCandidate,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ProfileError},
        status.HTTP_403_FORBIDDEN: {"model": ProfileError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ProfileError},
    },
)
async def propose_candidate(
    service: ProfileServiceDep,
    subject: SubjectDep,
    request: ProfileCandidateCreateRequest,
) -> ProfileCandidate:
    """Propose a candidate profile from existing observations."""
    if request.owner_account_id != subject.account_id:
        raise _profile_error(
            status.HTTP_403_FORBIDDEN,
            "ownership_mismatch",
            "只能为自己的账户提出候选画像。",
        )
    try:
        return service.propose_candidate(
            subject.account_id,
            canonical_dimension=request.canonical_dimension,
            value_or_rule=request.value_or_rule,
            applicable_scenes=request.applicable_scenes,
            non_applicable_scenes=request.non_applicable_scenes,
            supporting_observation_ids=request.supporting_observation_ids,
            contradicting_observation_ids=request.contradicting_observation_ids,
            evidence_summary=request.evidence_summary,
            authorization_scope=request.authorization_scope,
            promotion_policy_version=request.promotion_policy_version,
            expires_at=request.expires_at,
        )
    except ProfileAdapterError as exc:
        raise _profile_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "candidate_proposal_failed",
            str(exc),
        ) from exc


@router.get(
    "/candidates",
    response_model=list[ProfileCandidate],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ProfileError},
    },
)
async def list_candidates(
    service: ProfileServiceDep,
    subject: SubjectDep,
) -> list[ProfileCandidate]:
    """List candidate profiles for the current account."""
    return service.list_candidates(subject.account_id)


@router.get(
    "/candidates/{candidate_id}",
    response_model=ProfileCandidate,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ProfileError},
        status.HTTP_404_NOT_FOUND: {"model": ProfileError},
    },
)
async def get_candidate(
    service: ProfileServiceDep,
    subject: SubjectDep,
    candidate_id: str,
) -> ProfileCandidate:
    """Get a single candidate profile."""
    try:
        return service.get_candidate(subject.account_id, candidate_id)
    except ProfileAdapterError as exc:
        raise _profile_error(
            status.HTTP_404_NOT_FOUND,
            "candidate_not_found",
            str(exc),
        ) from exc


@router.post(
    "/candidates/{candidate_id}/decision",
    response_model=ProfileCandidate,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ProfileError},
        status.HTTP_404_NOT_FOUND: {"model": ProfileError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ProfileError},
    },
)
async def decide_candidate(
    service: ProfileServiceDep,
    subject: SubjectDep,
    candidate_id: str,
    request: CandidateDecision,
) -> ProfileCandidate:
    """Accept, reject or modify a candidate profile."""
    try:
        return service.decide_candidate(subject.account_id, candidate_id, request)
    except ProfileAdapterError as exc:
        msg = str(exc)
        if "访问权限" in msg or "不存在" in msg:
            raise _profile_error(
                status.HTTP_404_NOT_FOUND,
                "candidate_not_found",
                msg,
            ) from exc
        raise _profile_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "candidate_decision_failed",
            msg,
        ) from exc


@router.get(
    "/assertions",
    response_model=list[ProfileAssertion],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ProfileError},
    },
)
async def list_assertions(
    service: ProfileServiceDep,
    subject: SubjectDep,
) -> list[ProfileAssertion]:
    """List promoted profile assertions for the current account."""
    return service.list_assertions(subject.account_id)


@router.get(
    "/memory-slice",
    response_model=ProfileSlice,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ProfileError},
    },
)
async def compile_memory_slice(
    service: ProfileServiceDep,
    subject: SubjectDep,
    purpose: Annotated[str, Query(description="Declared processing purpose.")],
    run_id: Annotated[str, Query(description="Run identifier.")],
    project_id: Annotated[str | None, Query(description="Project scope.")] = None,
    sensitivity_class: Annotated[
        list[ProfileSensitivityClass] | None,
        Query(description="Allowed sensitivity classes."),
    ] = None,
    ttl_seconds: Annotated[int, Query(description="Slice time-to-live in seconds.")] = 3600,
    authorization_version: Annotated[
        str, Query(description="Authorization policy version snapshot.")
    ] = "authz-1.0",
    key_epoch: Annotated[str, Query(description="Key epoch.")] = "epoch-0",
) -> ProfileSlice:
    """Compile the minimal profile slice for a run.

    The slice is bound to the run and filtered by purpose, project scope,
    authorization snapshot, key epoch, expiration and sensitivity class.
    Unconfirmed candidates are explicitly excluded so they are never used as
    stable facts in downstream tasks.
    """
    return service.compile_memory_slice(
        subject.account_id,
        purpose=purpose,
        run_id=run_id,
        project_id=project_id,
        sensitivity_classes=sensitivity_class,
        ttl_seconds=ttl_seconds,
        authorization_version=authorization_version,
        key_epoch=key_epoch,
    )


@router.get(
    "/memory-slices/{slice_id}",
    response_model=ProfileSlice,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ProfileError},
        status.HTTP_404_NOT_FOUND: {"model": ProfileError},
    },
)
async def get_memory_slice(
    service: ProfileServiceDep,
    subject: SubjectDep,
    slice_id: str,
) -> ProfileSlice:
    """Return a compiled memory slice owned by the current account."""
    try:
        return service.get_slice(subject.account_id, slice_id)
    except ProfileAdapterError as exc:
        raise _profile_error(
            status.HTTP_404_NOT_FOUND,
            "slice_not_found",
            str(exc),
        ) from exc


@router.get(
    "/memory-slices/{slice_id}/inspector",
    response_model=ProfileSlice,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ProfileError},
        status.HTTP_404_NOT_FOUND: {"model": ProfileError},
    },
)
async def inspect_memory_slice(
    service: ProfileServiceDep,
    subject: SubjectDep,
    slice_id: str,
) -> ProfileSlice:
    """Inspect a memory slice: used, unused and rejected items with reasons.

    The context inspector uses this view to explain why each profile entry was
    or was not included in the run context.
    """
    try:
        return service.get_slice(subject.account_id, slice_id)
    except ProfileAdapterError as exc:
        raise _profile_error(
            status.HTTP_404_NOT_FOUND,
            "slice_not_found",
            str(exc),
        ) from exc


@router.post(
    "/memory-slices/{slice_id}/access-check",
    response_model=dict[str, object],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ProfileError},
        status.HTTP_404_NOT_FOUND: {"model": ProfileError},
        status.HTTP_403_FORBIDDEN: {"model": ProfileError},
    },
)
async def check_memory_slice_access(
    service: ProfileServiceDep,
    subject: SubjectDep,
    slice_id: str,
    run_id: Annotated[str, Query(description="Run identifier the slice must be bound to.")],
) -> dict[str, object]:
    """Verify that a model or worker node can access only the bound slice.

    This endpoint fails closed when the slice is not bound to the run, has
    expired, or has been revoked/cancelled. It proves that downstream nodes
    cannot browse the full profile vault.
    """
    try:
        service.require_slice_for_run(slice_id, run_id)
    except ProfileAdapterError as exc:
        msg = str(exc)
        if "不存在" in msg or "不一致" in msg:
            raise _profile_error(
                status.HTTP_404_NOT_FOUND,
                "slice_not_found",
                msg,
            ) from exc
        raise _profile_error(
            status.HTTP_403_FORBIDDEN,
            "slice_not_usable",
            msg,
        ) from exc
    return {"slice_id": slice_id, "run_id": run_id, "accessible": True}


@router.get(
    "/assertions/{assertion_id}",
    response_model=ProfileAssertion,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ProfileError},
        status.HTTP_404_NOT_FOUND: {"model": ProfileError},
    },
)
async def get_assertion(
    service: ProfileServiceDep,
    subject: SubjectDep,
    assertion_id: str,
) -> ProfileAssertion:
    """Return a single promoted profile assertion."""
    try:
        return service.get_assertion(subject.account_id, assertion_id)
    except ProfileAdapterError as exc:
        raise _profile_error(
            status.HTTP_404_NOT_FOUND,
            "assertion_not_found",
            str(exc),
        ) from exc


@router.post(
    "/assertions/manual",
    response_model=ProfileAssertion,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ProfileError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ProfileError},
    },
)
async def create_manual_assertion(
    service: ProfileServiceDep,
    subject: SubjectDep,
    request: ManualAssertionCreateRequest,
) -> ProfileAssertion:
    """Manually create a governed profile record declared by the current account.

    The user declares the fact together with its applicable scenes, sensitivity
    and authorization scope; the record is promoted immediately with a
    traceable observation and candidate chain.
    """
    try:
        return service.manual_create_assertion(subject.account_id, request)
    except ProfileAdapterError as exc:
        raise _profile_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "assertion_manual_create_failed",
            str(exc),
        ) from exc


@router.post(
    "/assertions/{assertion_id}/freeze",
    response_model=ProfileAssertion,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ProfileError},
        status.HTTP_404_NOT_FOUND: {"model": ProfileError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ProfileError},
    },
)
async def freeze_assertion(
    service: ProfileServiceDep,
    subject: SubjectDep,
    assertion_id: str,
    request: ProfileFreezeRequest,
) -> ProfileAssertion:
    """Freeze a profile assertion so it is no longer used in new runs."""
    try:
        return service.freeze_assertion(
            subject.account_id, assertion_id, request.reason
        )
    except ProfileAdapterError as exc:
        raise _assertion_error(exc, "assertion_freeze_failed") from exc


@router.post(
    "/assertions/{assertion_id}/withdraw",
    response_model=ProfileAssertion,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ProfileError},
        status.HTTP_404_NOT_FOUND: {"model": ProfileError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ProfileError},
    },
)
async def withdraw_assertion(
    service: ProfileServiceDep,
    subject: SubjectDep,
    assertion_id: str,
    request: ProfileFreezeRequest,
) -> ProfileAssertion:
    """Withdraw a profile assertion so it is no longer used in answers."""
    try:
        return service.withdraw_assertion(
            subject.account_id, assertion_id, request.reason
        )
    except ProfileAdapterError as exc:
        raise _assertion_error(exc, "assertion_withdraw_failed") from exc


@router.post(
    "/assertions/{assertion_id}/unfreeze",
    response_model=ProfileAssertion,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ProfileError},
        status.HTTP_404_NOT_FOUND: {"model": ProfileError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ProfileError},
    },
)
async def unfreeze_assertion(
    service: ProfileServiceDep,
    subject: SubjectDep,
    assertion_id: str,
    request: ProfileFreezeRequest,
) -> ProfileAssertion:
    """Restore a frozen or withdrawn profile assertion to active use."""
    try:
        return service.unfreeze_assertion(
            subject.account_id, assertion_id, request.reason
        )
    except ProfileAdapterError as exc:
        raise _assertion_error(exc, "assertion_unfreeze_failed") from exc


@router.post(
    "/assertions/{assertion_id}/modify",
    response_model=ProfileAssertion,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ProfileError},
        status.HTTP_404_NOT_FOUND: {"model": ProfileError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ProfileError},
    },
)
async def modify_assertion(
    service: ProfileServiceDep,
    subject: SubjectDep,
    assertion_id: str,
    request: ProfileAssertionModifyRequest,
) -> ProfileAssertion:
    """Modify a profile assertion, creating a new version."""
    try:
        return service.modify_assertion(
            subject.account_id,
            assertion_id,
            request.value_or_rule,
            request.applicable_scenes,
            request.reason,
        )
    except ProfileAdapterError as exc:
        raise _assertion_error(exc, "assertion_modify_failed") from exc


@router.post(
    "/assertions/{assertion_id}/rollback",
    response_model=ProfileAssertion,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ProfileError},
        status.HTTP_404_NOT_FOUND: {"model": ProfileError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ProfileError},
    },
)
async def rollback_assertion(
    service: ProfileServiceDep,
    subject: SubjectDep,
    assertion_id: str,
    request: ProfileAssertionRollbackRequest,
) -> ProfileAssertion:
    """Roll a profile assertion back to a previous version."""
    try:
        return service.rollback_assertion(
            subject.account_id, assertion_id, request.to_version, request.reason
        )
    except ProfileAdapterError as exc:
        raise _assertion_error(exc, "assertion_rollback_failed") from exc


@router.post(
    "/assertions/{assertion_id}/delete",
    response_model=ProfileAssertion,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ProfileError},
        status.HTTP_404_NOT_FOUND: {"model": ProfileError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ProfileError},
    },
)
async def delete_assertion(
    service: ProfileServiceDep,
    subject: SubjectDep,
    assertion_id: str,
    request: ProfileDeleteRequest,
) -> ProfileAssertion:
    """Delete a profile assertion and propagate the deletion downstream."""
    try:
        return service.delete_assertion(
            subject.account_id, assertion_id, request.reason
        )
    except ProfileAdapterError as exc:
        raise _assertion_error(exc, "assertion_delete_failed") from exc


@router.get(
    "/assertions/{assertion_id}/history",
    response_model=ProfileAssertionHistory,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ProfileError},
        status.HTTP_404_NOT_FOUND: {"model": ProfileError},
    },
)
async def get_assertion_history(
    service: ProfileServiceDep,
    subject: SubjectDep,
    assertion_id: str,
) -> ProfileAssertionHistory:
    """Return the version history of one profile assertion.

    The history lets the user compare versions and see whether a change was
    made by a user operation or by a candidate promotion, without erasing
    provenance on overwrite.
    """
    try:
        return service.get_assertion_history(subject.account_id, assertion_id)
    except ProfileAdapterError as exc:
        raise _assertion_error(exc, "assertion_history_failed") from exc


@router.get(
    "/export",
    response_model=ProfileExport,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ProfileError},
    },
)
async def export_profile(
    service: ProfileServiceDep,
    subject: SubjectDep,
) -> ProfileExport:
    """Export the current account's profile assertions and governance history."""
    return service.export_profile_data(subject.account_id)


@router.get(
    "/permissions",
    response_model=list[ProfilePermission],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ProfileError},
    },
)
async def list_permissions(
    service: ProfileServiceDep,
    subject: SubjectDep,
) -> list[ProfilePermission]:
    """List low-risk automatic-update permissions for the current account."""
    return service.list_permissions(subject.account_id)


@router.put(
    "/permissions",
    response_model=ProfilePermission,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ProfileError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ProfileError},
    },
)
async def update_permission(
    service: ProfileServiceDep,
    subject: SubjectDep,
    request: ProfilePermissionUpdateRequest,
) -> ProfilePermission:
    """Enable or disable one low-risk automatic-update permission.

    Only the authenticated user can change permissions; the model or any
    background task has no path to grant authorization (ADR-0002).
    """
    try:
        return service.set_permission(subject.account_id, request)
    except ProfileAdapterError as exc:
        raise _profile_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "permission_update_failed",
            str(exc),
        ) from exc


@router.get(
    "/notifications",
    response_model=list[ProfileNotification],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ProfileError},
    },
)
async def list_notifications(
    service: ProfileServiceDep,
    subject: SubjectDep,
) -> list[ProfileNotification]:
    """List profile notifications for the current account, newest first."""
    return service.list_notifications(subject.account_id)


@router.post(
    "/notifications/{notification_id}/read",
    response_model=ProfileNotification,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ProfileError},
        status.HTTP_404_NOT_FOUND: {"model": ProfileError},
    },
)
async def mark_notification_read(
    service: ProfileServiceDep,
    subject: SubjectDep,
    notification_id: str,
) -> ProfileNotification:
    """Mark one profile notification as read."""
    try:
        return service.mark_notification_read(subject.account_id, notification_id)
    except ProfileAdapterError as exc:
        raise _profile_error(
            status.HTTP_404_NOT_FOUND,
            "notification_not_found",
            str(exc),
        ) from exc


@router.post(
    "/notifications/{notification_id}/recall",
    response_model=ProfileNotification,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ProfileError},
        status.HTTP_404_NOT_FOUND: {"model": ProfileError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ProfileError},
    },
)
async def recall_notification(
    service: ProfileServiceDep,
    subject: SubjectDep,
    notification_id: str,
) -> ProfileNotification:
    """One-click recall of an auto-written profile record.

    The recall withdraws the record (auditable history is kept), blocks
    future automatic writes of the same fact, and is idempotent so a failed
    retry does not duplicate anything.
    """
    try:
        return service.recall_auto_write(subject.account_id, notification_id)
    except ProfileAdapterError as exc:
        msg = str(exc)
        if "访问权限" in msg or "不存在" in msg:
            raise _profile_error(
                status.HTTP_404_NOT_FOUND,
                "notification_not_found",
                msg,
            ) from exc
        raise _profile_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "notification_recall_failed",
            msg,
        ) from exc


@router.post(
    "/candidates/batch-decision",
    response_model=ProfileBatchCandidateResult,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ProfileError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ProfileError},
    },
)
async def batch_decide_candidates(
    service: ProfileServiceDep,
    subject: SubjectDep,
    request: ProfileBatchCandidateDecisionRequest,
) -> ProfileBatchCandidateResult:
    """Apply one decision to multiple candidates (idempotent, retry-safe).

    Candidates already in the target state are reported as ``already_decided``
    rather than failed, so a failed batch can be retried safely without
    duplicate writes.
    """
    try:
        return service.decide_candidates_batch(subject.account_id, request)
    except ProfileAdapterError as exc:
        raise _profile_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "candidate_batch_decision_failed",
            str(exc),
        ) from exc


@router.get(
    "/four-dimensions",
    response_model=list[FourDimensionProfileRecord],
    responses={status.HTTP_401_UNAUTHORIZED: {"model": ProfileError}},
)
async def list_four_dimension_records(
    service: FourDimensionProfileServiceDep,
    subject: SubjectDep,
) -> list[FourDimensionProfileRecord]:
    """List only active four-dimension records for the current account."""
    return service.list_records(subject.account_id)


@router.get(
    "/four-dimensions/migration-report",
    response_model=FourDimensionMigrationReport,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ProfileError},
        status.HTTP_404_NOT_FOUND: {"model": ProfileError},
    },
)
async def get_four_dimension_migration_report(
    service: FourDimensionProfileServiceDep,
    subject: SubjectDep,
) -> FourDimensionMigrationReport:
    """Return the current account's migration summary without profile正文."""
    report = service.latest_migration_report(subject.account_id)
    if report is None:
        raise _profile_error(
            status.HTTP_404_NOT_FOUND,
            "migration_report_not_found",
            "迁移报告不存在。",
        )
    return report


@router.post(
    "/four-dimensions/migrate",
    response_model=FourDimensionMigrationReport,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ProfileError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ProfileError},
    },
)
async def migrate_four_dimension_records(
    service: FourDimensionProfileServiceDep,
    subject: SubjectDep,
) -> FourDimensionMigrationReport:
    """Run the account-scoped, deterministic expand/migrate projection."""
    try:
        return service.migrate_account(subject.account_id)
    except FourDimensionProfileError as exc:
        raise _four_dimension_error(exc, "four_dimension_migration_failed") from exc


@router.patch(
    "/four-dimensions/{record_id}",
    response_model=FourDimensionProfileRecord,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ProfileError},
        status.HTTP_404_NOT_FOUND: {"model": ProfileError},
        status.HTTP_409_CONFLICT: {"model": ProfileError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ProfileError},
    },
)
async def modify_four_dimension_record(
    service: FourDimensionProfileServiceDep,
    subject: SubjectDep,
    record_id: str,
    request: FourDimensionProfileModifyRequest,
) -> FourDimensionProfileRecord:
    """Modify one existing record; there is deliberately no create route."""
    try:
        return service.modify_record(subject.account_id, record_id, request)
    except FourDimensionProfileError as exc:
        raise _four_dimension_error(exc, "four_dimension_modify_failed") from exc


@router.post(
    "/four-dimensions/{record_id}/withdraw",
    response_model=FourDimensionProfileRecord,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ProfileError},
        status.HTTP_404_NOT_FOUND: {"model": ProfileError},
        status.HTTP_409_CONFLICT: {"model": ProfileError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ProfileError},
    },
)
async def withdraw_four_dimension_record(
    service: FourDimensionProfileServiceDep,
    subject: SubjectDep,
    record_id: str,
    request: FourDimensionProfileWithdrawRequest,
) -> FourDimensionProfileRecord:
    """Withdraw one record while retaining its internal tombstone."""
    try:
        return service.withdraw_record(subject.account_id, record_id, request.version)
    except FourDimensionProfileError as exc:
        raise _four_dimension_error(exc, "four_dimension_withdraw_failed") from exc

