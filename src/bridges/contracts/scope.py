"""Scope isolation, RLS, cache key, and background-task contracts.

These models define the cross-cutting scope envelope used by database rows,
cache keys, background tasks, object references, and vault ports. They are the
authoritative shape of CONTRACT-AUTH-01.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from bridges.contracts.identity import SubjectContext
from bridges.contracts.projects import ObjectDomain, ObjectRef
from bridges.contracts.vault import VaultObjectRef


class ScopeAction(str, Enum):
    """Actions that scope isolation can authorize or deny."""

    CREATE = "create"
    READ = "read"
    UPDATE = "update"
    DELETE = "delete"
    SHARE = "share"
    EXECUTE = "execute"
    ADMINISTER = "administer"


class ScopeIsolationError(Exception):
    """Domain exception for scope isolation failures.

    The message is safe to expose to callers; it never leaks whether an object
    exists or belongs to another account.
    """


class ScopeEnvelope(BaseModel):
    """Immutable scope snapshot carried by requests, runs, cache keys and RLS.

    The envelope binds an operation to a subject, tenant, project, object domain,
    authorization version and key epoch. Services must compile it before touching
    storage, cache, index or model context.
    """

    account_id: str = Field(description="Authenticated account identifier.")
    tenant_id: str | None = Field(
        default=None,
        description="Institution/organization tenant when applicable.",
    )
    project_id: str | None = Field(
        default=None,
        description="Project within which the operation is scoped.",
    )
    object_domain: ObjectDomain = Field(
        default=ObjectDomain.PERSONAL_VAULT,
        description="Authority domain that owns the target objects.",
    )
    authorization_version: str = Field(
        default="authz-1.0",
        description="Authorization policy version at compile time.",
    )
    key_epoch: str = Field(
        default="epoch-0",
        description="Key epoch under which secrets and capsules are bound.",
    )
    purpose: str = Field(
        default="general",
        description="Declared purpose for the operation.",
    )
    requested_object_refs: list[ObjectRef] = Field(
        default_factory=list,
        description="Object references the operation requests to touch.",
    )
    requested_vault_refs: list[VaultObjectRef] = Field(
        default_factory=list,
        description="Vault object references the operation requests to touch.",
    )


class RLSContext(BaseModel):
    """Transaction-level row-level security context.

    In persistent deployments this is set once per transaction and cannot be
    forged by application code. The in-memory adapters emulate it by carrying the
    same fields and validating them on every access.
    """

    subject: SubjectContext = Field(description="Authenticated subject.")
    scope_envelope: ScopeEnvelope = Field(description="Compiled scope snapshot.")
    set_at: datetime = Field(description="When the RLS context was established.")


class ScopeCacheKey(BaseModel):
    """Structured cache key that prevents cross-user or cross-scope collisions.

    Redis/cache keys must include deployment cell, tenant/user, object domain,
    project, authorization version, key epoch, object/content version and model
    schema version. Private responses must never be cached across users.
    """

    deployment_cell: str = Field(description="Deployment cell identifier.")
    tenant_id: str | None = Field(default=None)
    account_id: str | None = Field(default=None)
    object_domain: ObjectDomain = Field(default=ObjectDomain.PERSONAL_VAULT)
    project_id: str | None = Field(default=None)
    authorization_version: str = Field(default="authz-1.0")
    key_epoch: str = Field(default="epoch-0")
    object_version: str | None = Field(default=None)
    content_version: str | None = Field(default=None)
    model_schema_version: str | None = Field(default=None)

    def build(self) -> str:
        """Return a deterministic, colon-separated cache key."""
        parts = [
            "sc",
            self.deployment_cell,
            self.tenant_id or "_",
            self.account_id or "_",
            self.object_domain.value,
            self.project_id or "_",
            self.authorization_version,
            self.key_epoch,
            self.object_version or "_",
            self.content_version or "_",
            self.model_schema_version or "_",
        ]
        return ":".join(parts)


class BackgroundTaskEnvelope(BaseModel):
    """Required context for every background task.

    A background task must carry a subject, object domain, project, authorization
    version and key epoch. Missing any of these causes deterministic failure
    closure rather than running with ambient or guessed context.
    """

    task_id: str = Field(description="Stable background task identifier.")
    task_type: str = Field(description="Logical task type, e.g. ingestion, index.")
    subject: SubjectContext = Field(description="Subject that authorized the task.")
    object_domain: ObjectDomain = Field(description="Domain in which the task runs.")
    project_id: str | None = Field(
        default=None,
        description="Project scope when the task belongs to a project.",
    )
    authorization_version: str = Field(
        description="Authorization policy version at task creation.",
    )
    key_epoch: str = Field(
        description="Key epoch under which task secrets are bound.",
    )
    purpose: str = Field(description="Declared purpose of the task.")
    object_refs: list[str] = Field(
        default_factory=list,
        description="Object identifiers the task is authorized to touch.",
    )
    memory_slice_refs: list[str] = Field(
        default_factory=list,
        description="Memory slice identifiers compiled for the task.",
    )


class ScopeViolationReport(BaseModel):
    """Audit-safe report when a scope check fails.

    The report intentionally does not include private content, full prompts or
    keys; it records only the subject, requested scope, reason and timestamp.
    """

    actor_account_id: str = Field(description="Account that attempted the action.")
    actor_session_id: str = Field(description="Session that attempted the action.")
    action: ScopeAction = Field(description="Action that was denied.")
    requested_scope: ScopeEnvelope = Field(description="Scope that was denied.")
    reason: str = Field(description="Human-readable, non-leaking reason.")
    occurred_at: datetime = Field(description="When the violation was detected.")
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Opaque detail safe for logging; must not expose internal state.",
    )
