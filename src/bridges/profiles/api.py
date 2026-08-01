"""Profile observation-candidate API routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from bridges.api.auth import SubjectDep
from bridges.contracts.profiles import (
    CandidateDecision,
    ProfileAssertion,
    ProfileAssertionModifyRequest,
    ProfileAssertionRollbackRequest,
    ProfileCandidate,
    ProfileCandidateCreateRequest,
    ProfileDeleteRequest,
    ProfileError,
    ProfileExport,
    ProfileFreezeRequest,
    ProfileObservation,
    ProfileObservationCreateRequest,
    ProfileSensitivityClass,
    ProfileSlice,
)
from bridges.profiles import ProfileService
from bridges.profiles.adapters import ProfileError as ProfileAdapterError

router = APIRouter(prefix="/profiles", tags=["profiles"])


def _get_profile_service(request: Request) -> ProfileService:
    service: ProfileService | None = getattr(request.app.state, "profile_service", None)
    if service is None:
        raise RuntimeError("ProfileService not attached to application state.")
    return service


ProfileServiceDep = Annotated[ProfileService, Depends(_get_profile_service)]


def _profile_error(status_code: int, error: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=ProfileError(error=error, message=message).model_dump(),
    )


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
        msg = str(exc)
        if "访问权限" in msg or "不存在" in msg:
            raise _profile_error(
                status.HTTP_404_NOT_FOUND,
                "assertion_not_found",
                msg,
            ) from exc
        raise _profile_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "assertion_freeze_failed",
            msg,
        ) from exc


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
        msg = str(exc)
        if "访问权限" in msg or "不存在" in msg:
            raise _profile_error(
                status.HTTP_404_NOT_FOUND,
                "assertion_not_found",
                msg,
            ) from exc
        raise _profile_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "assertion_modify_failed",
            msg,
        ) from exc


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
        msg = str(exc)
        if "访问权限" in msg or "不存在" in msg:
            raise _profile_error(
                status.HTTP_404_NOT_FOUND,
                "assertion_not_found",
                msg,
            ) from exc
        raise _profile_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "assertion_rollback_failed",
            msg,
        ) from exc


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
        msg = str(exc)
        if "访问权限" in msg or "不存在" in msg:
            raise _profile_error(
                status.HTTP_404_NOT_FOUND,
                "assertion_not_found",
                msg,
            ) from exc
        raise _profile_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "assertion_delete_failed",
            msg,
        ) from exc


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

