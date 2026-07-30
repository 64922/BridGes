"""Scientific project space domain service.

This module implements the deep module boundary for project creation, selection,
renaming, archival, and object ownership. T004 uses an in-memory adapter so the
seam can be exercised without requiring PostgreSQL. The public interface is
stable and will later be backed by the project schema.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from science_companion.contracts.identity import SubjectContext
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
from science_companion.contracts.scope import ScopeAction, ScopeIsolationError
from science_companion.persistence import StateStore
from science_companion.scope import ScopeEnforcer


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

    def __init__(
        self,
        scope_enforcer: ScopeEnforcer | None = None,
        state_store: StateStore | None = None,
    ) -> None:
        self._projects: dict[str, _StoredProject] = {}
        self._scope_enforcer = scope_enforcer or ScopeEnforcer()
        self._state_store = state_store
        self._load_state()

    def _load_state(self) -> None:
        if self._state_store is None:
            return
        state = self._state_store.load("projects") or {}
        self._projects = {
            project_id: _StoredProject(Project.model_validate(value))
            for project_id, value in state.get("projects", {}).items()
        }

    def _persist(self) -> None:
        if self._state_store is None:
            return
        self._state_store.save(
            "projects",
            {
                "projects": {
                    project_id: stored.project.model_dump(mode="json")
                    for project_id, stored in self._projects.items()
                }
            },
        )

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def _subject(self, account_id: str) -> SubjectContext:
        """Build a minimal subject context from an account id for scope checks."""
        from science_companion.contracts.identity import AuthMethod

        return SubjectContext(
            account_id=account_id,
            session_id="service-session",
            auth_method=AuthMethod.PASSWORD,
        )

    def _object_ref(self, project: Project) -> ObjectRef:
        return ObjectRef(
            domain=project.object_domain,
            owner_id=project.account_id,
            object_id=project.id,
            version=project.version,
        )

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
        self._persist()
        return project

    def list_projects(self, account_id: str) -> ProjectListProjection:
        """Return active and archived projects for the account."""
        subject = self._subject(account_id)
        active: list[ProjectSummary] = []
        archived: list[ProjectSummary] = []

        for stored in self._projects.values():
            project = stored.project
            if project.account_id != account_id:
                continue
            # Explicit scope authorization on each project; this mirrors RLS.
            try:
                self._scope_enforcer.authorize(
                    subject, ScopeAction.READ, self._object_ref(project)
                )
            except ScopeIsolationError as exc:
                # Should not happen given the account filter, but the contract
                # requires failing closed if scope and owner disagree.
                raise ProjectError(str(exc)) from exc
            summary = ProjectSummary(
                ref=self._object_ref(project),
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
        subject = self._subject(account_id)
        try:
            self._scope_enforcer.authorize(
                subject, ScopeAction.READ, self._object_ref(stored.project)
            )
        except ScopeIsolationError as exc:
            raise ProjectError(str(exc)) from exc
        return stored.project

    def update_project(
        self, account_id: str, project_id: str, request: ProjectUpdateRequest
    ) -> Project:
        """Rename or update a project description."""
        stored = self._projects.get(project_id)
        if stored is None or stored.project.account_id != account_id:
            raise ProjectError("项目不存在或没有访问权限。")
        subject = self._subject(account_id)
        try:
            self._scope_enforcer.authorize(
                subject, ScopeAction.UPDATE, self._object_ref(stored.project)
            )
        except ScopeIsolationError as exc:
            raise ProjectError(str(exc)) from exc

        project = stored.project
        if request.name is not None:
            project.name = request.name
        if request.description is not None:
            project.description = request.description
        project.version += 1
        project.updated_at = self._now()
        self._persist()
        return project

    def archive_project(self, account_id: str, project_id: str) -> Project:
        """Archive a project."""
        stored = self._projects.get(project_id)
        if stored is None or stored.project.account_id != account_id:
            raise ProjectError("项目不存在或没有访问权限。")
        subject = self._subject(account_id)
        try:
            self._scope_enforcer.authorize(
                subject, ScopeAction.DELETE, self._object_ref(stored.project)
            )
        except ScopeIsolationError as exc:
            raise ProjectError(str(exc)) from exc

        project = stored.project
        project.status = ProjectStatus.ARCHIVED
        project.archived_at = self._now()
        project.version += 1
        project.updated_at = project.archived_at
        self._persist()
        return project
