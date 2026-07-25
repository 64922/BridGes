"""Scientific project space domain service.

This module implements the deep module boundary for project creation, selection,
renaming, archival, and object ownership. T004 uses an in-memory adapter so the
seam can be exercised without requiring PostgreSQL. The public interface is
stable and will later be backed by the project schema.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

from science_companion.contracts.projects import (
    ObjectDomain,
    ObjectRef,
    Project,
    ProjectCreateRequest,
    ProjectListProjection,
    ProjectRole,
    ProjectStatus,
    ProjectSummary,
    ProjectUpdateRequest,
)


class ProjectError(Exception):
    """Domain exception for project failures.

    The message is safe to expose to callers; it never leaks whether a project
    exists or belongs to another account.
    """


@dataclass
class _StoredProject:
    project: Project


class ProjectService:
    """In-memory project service for T004.

    The interface intentionally mirrors the eventual database-backed adapter so
    that later tickets can swap the implementation without changing callers.
    """

    def __init__(self) -> None:
        self._projects: dict[str, _StoredProject] = {}

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def create_project(self, account_id: str, request: ProjectCreateRequest) -> Project:
        """Create a new scientific project space owned by the account."""
        now = self._now()
        project = Project(
            id=secrets.token_urlsafe(16),
            account_id=account_id,
            name=request.name,
            description=request.description,
            object_domain=ObjectDomain.PERSONAL_VAULT,
            role=ProjectRole.OWNER,
            status=ProjectStatus.ACTIVE,
            version=1,
            created_at=now,
            updated_at=now,
            archived_at=None,
        )
        self._projects[project.id] = _StoredProject(project=project)
        return project

    def list_projects(self, account_id: str) -> ProjectListProjection:
        """Return active and archived projects for the account."""
        active: list[ProjectSummary] = []
        archived: list[ProjectSummary] = []

        for stored in self._projects.values():
            project = stored.project
            if project.account_id != account_id:
                continue
            summary = ProjectSummary(
                ref=ObjectRef(
                    domain=project.object_domain,
                    owner_id=project.account_id,
                    object_id=project.id,
                    version=project.version,
                ),
                name=project.name,
                status=project.status,
                updated_at=project.updated_at,
            )
            if project.status == ProjectStatus.ARCHIVED:
                archived.append(summary)
            else:
                active.append(summary)

        active.sort(key=lambda s: s.updated_at, reverse=True)
        archived.sort(key=lambda s: s.updated_at, reverse=True)
        return ProjectListProjection(active=active, archived=archived)

    def get_project(self, account_id: str, project_id: str) -> Project:
        """Return a single project projection if owned by the account."""
        stored = self._projects.get(project_id)
        if stored is None or stored.project.account_id != account_id:
            raise ProjectError("项目不存在或没有访问权限。")
        return stored.project

    def update_project(
        self, account_id: str, project_id: str, request: ProjectUpdateRequest
    ) -> Project:
        """Rename or update a project description."""
        stored = self._projects.get(project_id)
        if stored is None or stored.project.account_id != account_id:
            raise ProjectError("项目不存在或没有访问权限。")

        project = stored.project
        if request.name is not None:
            project.name = request.name
        if request.description is not None:
            project.description = request.description
        project.version += 1
        project.updated_at = self._now()
        return project

    def archive_project(self, account_id: str, project_id: str) -> Project:
        """Archive a project."""
        stored = self._projects.get(project_id)
        if stored is None or stored.project.account_id != account_id:
            raise ProjectError("项目不存在或没有访问权限。")

        project = stored.project
        project.status = ProjectStatus.ARCHIVED
        project.archived_at = self._now()
        project.version += 1
        project.updated_at = project.archived_at
        return project
