"""Project and object-ownership contracts shared by API and Web.

These models define the public surface of scientific project spaces, their
object domain, owner, role, and version. They are the authoritative shape of
CONTRACT-OBJ-01.
"""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ObjectDomain(str, Enum):
    """Authority domain that owns the object.

    - PERSONAL_VAULT: owned by an individual account; not visible to collaborators
      or admins by default.
    - SHARED_PROJECT: owned by a project, created from an explicit share decision.
    - INSTITUTION_OWNED: owned by an institution management domain.
    """

    PERSONAL_VAULT = "personal_vault"
    SHARED_PROJECT = "shared_project"
    INSTITUTION_OWNED = "institution_owned"


class ProjectRole(str, Enum):
    """Role of the current subject inside a project."""

    OWNER = "owner"
    EDITOR = "editor"
    REVIEWER = "reviewer"
    COMMENTER = "commenter"
    VIEWER = "viewer"


class ProjectStatus(str, Enum):
    """Lifecycle status of a project."""

    ACTIVE = "active"
    ARCHIVED = "archived"


class ObjectRef(BaseModel):
    """Stable reference to an owned object.

    ObjectRef carries enough context to re-authenticate and re-authorize a deep
    link without relying on ambient session state alone.
    """

    domain: ObjectDomain = Field(description="Authority domain of the object.")
    owner_id: str = Field(description="Identifier of the owning account, project, or institution.")
    object_id: str = Field(description="Stable object identifier.")
    version: int = Field(default=1, description="Optimistic concurrency version.")


class Project(BaseModel):
    """Public project projection.

    Every project is owned by an account, lives in an object domain, and carries
    a monotonic version for optimistic concurrency. Institution-owned projects
    also carry a tenant_id linking them to the institution management domain.
    """

    id: str = Field(description="Stable project identifier.")
    account_id: str = Field(description="Owning account identifier.")
    tenant_id: str | None = Field(
        default=None,
        description="Institution tenant identifier when the project is institution-owned.",
    )
    name: str = Field(description="Human-readable project name.", min_length=1, max_length=200)
    description: str | None = Field(default=None, description="Optional project purpose or goal.")
    object_domain: ObjectDomain = Field(description="Authority domain for the project and its objects.")
    role: ProjectRole = Field(description="Current subject's role in the project.")
    status: ProjectStatus = Field(description="Lifecycle status.")
    version: int = Field(default=1, description="Optimistic concurrency version.")
    created_at: datetime = Field(description="Project creation timestamp.")
    updated_at: datetime = Field(description="Last project update timestamp.")
    archived_at: datetime | None = Field(default=None, description="If set, the project is archived.")


class ProjectCreateRequest(BaseModel):
    """Request to create a new scientific project space."""

    name: str = Field(description="Project name.", min_length=1, max_length=200)
    description: str | None = Field(default=None, description="Optional project goal.")


class ProjectUpdateRequest(BaseModel):
    """Request to rename or update a project."""

    name: str | None = Field(default=None, description="New project name.", min_length=1, max_length=200)
    description: str | None = Field(default=None, description="New project goal.")


class ProjectSummary(BaseModel):
    """List item for the project selector."""

    ref: ObjectRef = Field(description="Owned object reference.")
    name: str = Field(description="Project name.")
    status: ProjectStatus = Field(description="Lifecycle status.")
    updated_at: datetime = Field(description="Last update timestamp.")


class ProjectListProjection(BaseModel):
    """Collection of projects visible to the current subject."""

    active: list[ProjectSummary] = Field(default_factory=list, description="Active projects.")
    archived: list[ProjectSummary] = Field(default_factory=list, description="Archived projects.")
    selected_project_id: str | None = Field(
        default=None,
        description="Currently selected project, if any.",
    )


class ProjectError(BaseModel):
    """Uniform project error response."""

    error: str = Field(description="Stable error code.")
    message: str = Field(description="Human-readable, non-leaking message.")
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Opaque detail safe for logging; must not expose internal state.",
    )
