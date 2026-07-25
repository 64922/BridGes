"""Vault application service.

The vault service coordinates the stable vault repository port, the device port,
and the cloud control projection store. Domain modules call this service (or the
repository port directly) instead of touching device frameworks or cloud vendor
objects.
"""

from __future__ import annotations

import base64
import secrets
from datetime import datetime, timezone

from science_companion.contracts.identity import SubjectContext
from science_companion.contracts.projects import ObjectDomain, ObjectRef
from science_companion.contracts.scope import ScopeAction, ScopeIsolationError
from science_companion.contracts.vault import (
    CapsuleIssueRequest,
    CloudControlProjection,
    CloudProjectionStatus,
    ContentAuthority,
    DeviceUnavailableState,
    TemporaryTaskCapsule,
    VaultObject,
    VaultObjectCreateRequest,
    VaultObjectDomain,
    VaultObjectSummary,
    VaultShareRequest,
)
from science_companion.scope import ScopeEnforcer
from science_companion.vault.adapters import VaultError
from science_companion.vault.ports import DeviceVaultPort, VaultRepository


def _now() -> datetime:
    return datetime.now(timezone.utc)


class VaultService:
    """Application service for personal vault boundaries and task capsules."""

    def __init__(
        self,
        repository: VaultRepository,
        device_port: DeviceVaultPort,
        scope_enforcer: ScopeEnforcer | None = None,
    ) -> None:
        self._repository = repository
        self._device_port = device_port
        self._scope_enforcer = scope_enforcer or ScopeEnforcer()

    def _subject(self, account_id: str) -> SubjectContext:
        """Build a minimal subject context from an account id for scope checks."""
        from science_companion.contracts.identity import AuthMethod

        return SubjectContext(
            account_id=account_id,
            session_id="service-session",
            auth_method=AuthMethod.PASSWORD,
        )

    def create_private_object(
        self,
        owner_account_id: str,
        content: bytes,
        *,
        content_authority: ContentAuthority = ContentAuthority.DEVICE_LOCAL,
        device_id: str | None = None,
        purpose: str = "general",
    ) -> VaultObject:
        """Create a private vault object.

        The full plaintext is stored according to content_authority. The cloud
        only receives a control projection (hash and metadata).
        """
        request = VaultObjectCreateRequest(
            owner_account_id=owner_account_id,
            content_authority=content_authority,
            content=base64.b64encode(content).decode("ascii"),
            device_id=device_id,
            purpose=purpose,
        )
        return self._repository.create_object(request)

    def get_object(
        self, owner_id: str, object_id: str
    ) -> VaultObject | DeviceUnavailableState:
        """Return the vault object projection.

        This returns metadata only; use get_content to request plaintext.
        """
        obj = self._repository.get_object(owner_id, object_id)
        if isinstance(obj, DeviceUnavailableState):
            return obj
        subject = self._subject(owner_id)
        try:
            self._scope_enforcer.authorize_vault(subject, ScopeAction.READ, obj.ref)
        except ScopeIsolationError as exc:
            raise VaultError(str(exc)) from exc
        return obj

    def get_content(
        self, owner_id: str, object_id: str
    ) -> bytes | DeviceUnavailableState:
        """Request plaintext content for a vault object.

        If the content authority is DEVICE_LOCAL, the device runtime must be
        available. The system returns DeviceUnavailableState instead of silently
        uploading or reconstructing full text from the cloud projection.
        """
        obj = self._repository.get_object(owner_id, object_id)
        if isinstance(obj, DeviceUnavailableState):
            return obj

        subject = self._subject(owner_id)
        try:
            self._scope_enforcer.authorize_vault(subject, ScopeAction.READ, obj.ref)
        except ScopeIsolationError as exc:
            raise VaultError(str(exc)) from exc

        if obj.content_authority == ContentAuthority.DEVICE_LOCAL:
            if obj.device_id is None:
                return DeviceUnavailableState(
                    object_ref=obj.ref,
                    reason="missing_device_id",
                )
            content = self._device_port.get_content(owner_id, object_id)
            if isinstance(content, DeviceUnavailableState):
                return content
            return content

        # Server replica or project copy: content is reachable through the repo.
        return self._repository.get_content(owner_id, object_id)

    def list_objects(self, owner_id: str) -> list[VaultObjectSummary]:
        """List vault object summaries for the owner."""
        subject = self._subject(owner_id)
        summaries = self._repository.list_objects(owner_id)
        # Explicitly authorize each summary against the compiled scope to mirror
        # RLS: even in memory we fail closed on any mismatch.
        authorized: list[VaultObjectSummary] = []
        for summary in summaries:
            try:
                self._scope_enforcer.authorize_vault(
                    subject, ScopeAction.READ, summary.ref
                )
                authorized.append(summary)
            except ScopeIsolationError:
                continue
        return authorized

    def issue_task_capsule(
        self,
        owner_account_id: str,
        object_id: str,
        run_id: str,
        purpose: str,
        *,
        ttl_seconds: int = 3600,
    ) -> TemporaryTaskCapsule:
        """Issue a temporary task capsule bound to a run and purpose."""
        obj = self._repository.get_object(owner_account_id, object_id)
        if isinstance(obj, DeviceUnavailableState):
            raise VaultError(str(obj.reason))
        subject = self._subject(owner_account_id)
        try:
            self._scope_enforcer.authorize_vault(
                subject, ScopeAction.EXECUTE, obj.ref
            )
        except ScopeIsolationError as exc:
            raise VaultError(str(exc)) from exc

        request = CapsuleIssueRequest(
            owner_account_id=owner_account_id,
            object_id=object_id,
            run_id=run_id,
            purpose=purpose,
            ttl_seconds=ttl_seconds,
        )
        return self._repository.issue_task_capsule(request)

    def revoke_capsule(
        self, owner_id: str, capsule_id: str
    ) -> TemporaryTaskCapsule:
        """Revoke a previously issued capsule."""
        capsule = self._repository.revoke_capsule(owner_id, capsule_id)
        subject = self._subject(owner_id)
        for ref in capsule.object_refs:
            try:
                self._scope_enforcer.authorize_vault(
                    subject, ScopeAction.DELETE, ref
                )
            except ScopeIsolationError as exc:
                raise VaultError(str(exc)) from exc
        return capsule

    def get_cloud_projection(
        self, owner_id: str, object_id: str
    ) -> CloudControlProjection | None:
        """Return the cloud control projection for a vault object."""
        projection = self._repository.get_cloud_projection(owner_id, object_id)
        if projection is None:
            return None
        subject = self._subject(owner_id)
        try:
            self._scope_enforcer.authorize_vault(
                subject, ScopeAction.READ, projection.object_ref
            )
        except ScopeIsolationError:
            return None
        return projection

    def share_as_project_copy(
        self,
        owner_account_id: str,
        request: VaultShareRequest,
    ) -> ObjectRef:
        """Share a personal vault object as an independent minimized project copy.

        The copy receives a new object identifier, lives in the shared project
        domain, and does not expose the personal vault original or its full
        private metadata.
        """
        source = self._repository.get_object(
            owner_account_id, request.source_object_id
        )
        if isinstance(source, DeviceUnavailableState):
            raise VaultError("源对象当前不可用。")

        subject = self._subject(owner_account_id)
        try:
            self._scope_enforcer.authorize_vault(
                subject, ScopeAction.SHARE, source.ref
            )
        except ScopeIsolationError as exc:
            raise VaultError(str(exc)) from exc

        # Create a minimized project copy. The full content is not duplicated here;
        # instead the copy references the same content hash and receives its own
        # object identity and domain.
        copy_content = self.get_content(owner_account_id, request.source_object_id)
        if isinstance(copy_content, DeviceUnavailableState):
            raise VaultError("源对象当前不可用。")

        copy_request = VaultObjectCreateRequest(
            owner_account_id=request.target_project_id,
            content_authority=ContentAuthority.PROJECT_COPY,
            content=base64.b64encode(copy_content).decode("ascii"),
            purpose=request.grant_purpose,
            key_epoch=source.projection.key_epoch,
            authorization_version=source.projection.authorization_version,
        )
        copy = self._repository.create_object(copy_request)
        return ObjectRef(
            domain=ObjectDomain.SHARED_PROJECT,
            owner_id=request.target_project_id,
            object_id=copy.ref.object_id,
            version=copy.ref.version,
        )
