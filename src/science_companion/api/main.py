"""FastAPI application for the Science Companion API."""

from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request, status
from pydantic import ValidationError as PydanticValidationError

from science_companion import __version__
from science_companion.api import auth, projects, scope, vault, workflows
from science_companion.config import get_settings
from science_companion.contracts.health import HealthProjection, HealthStatus
from science_companion.contracts.workflows import RunProjection, WorkflowRunStatus
from science_companion.health.probe import build_health_projection
from science_companion.identity import IdentityService
from science_companion.projects import ProjectService
from science_companion.scope import ScopeEnforcer
from science_companion.vault import (
    InMemoryVaultRepository,
    MemoryDeviceVaultPort,
    VaultService,
)
from science_companion.workflows import WorkflowError, WorkflowService


def _register_builtin_workflows(service: WorkflowService) -> None:
    """Register the minimal workflow templates available at T006."""
    service.register_workflow(
        name="generic_science_task",
        version="1",
        nodes=[
            {"node_id": "compile_context", "node_name": "编译上下文", "human_gate": False},
            {"node_id": "produce_output", "node_name": "生成产物", "human_gate": False},
        ],
        terminal_states=[
            WorkflowRunStatus.SUCCEEDED,
            WorkflowRunStatus.BLOCKED,
            WorkflowRunStatus.CANCELLED,
        ],
    )


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Science Companion API",
        version=__version__,
        description="长期科学学习与表达伙伴 API",
    )

    # T008: load the unified runtime configuration. All carriers (manual,
    # unified CLI, Docker, Podman) resolve the same schema and secret rules.
    # If configuration fails (e.g. missing secret file), the health probe
    # reports FAIL rather than crashing the process outright.
    try:
        app.state.settings = get_settings()
    except (PydanticValidationError, ValueError):
        app.state.settings = None

    # T007: attach the shared scope enforcer. All services, routes and background
    # task validators use the same interpreter so isolation rules do not drift.
    app.state.scope_enforcer = ScopeEnforcer()

    # T003: attach the in-memory identity service. Later tickets will switch to a
    # persistent adapter while keeping the same interface.
    app.state.identity_service = IdentityService()

    # T004: attach the in-memory project service. Later tickets will switch to a
    # persistent adapter while keeping the same interface.
    app.state.project_service = ProjectService(
        scope_enforcer=app.state.scope_enforcer,
    )

    # T005: attach the in-memory vault service and device port.
    vault_repository = InMemoryVaultRepository()
    app.state.vault_service = VaultService(
        repository=vault_repository,
        device_port=MemoryDeviceVaultPort(vault_repository),
        scope_enforcer=app.state.scope_enforcer,
    )

    # T006: attach the in-memory workflow service and register the first workflow.
    workflow_service = WorkflowService(scope_enforcer=app.state.scope_enforcer)
    _register_builtin_workflows(workflow_service)
    app.state.workflow_service = workflow_service

    app.include_router(auth.router)
    app.include_router(projects.router)
    app.include_router(vault.router)
    app.include_router(workflows.router)
    app.include_router(scope.router)

    @app.get("/health/live", response_model=HealthProjection)
    async def health_live() -> HealthProjection:
        """Liveness probe: process event loop can respond."""
        projection = build_health_projection(service="api")
        projection.ready = HealthStatus.UNKNOWN
        projection.degraded = HealthStatus.UNKNOWN
        projection.dependencies = []
        return projection

    @app.get("/health/ready", response_model=HealthProjection)
    async def health_ready() -> HealthProjection:
        """Readiness probe: required dependencies are healthy."""
        return build_health_projection(service="api")

    @app.get("/health/degraded", response_model=HealthProjection)
    async def health_degraded() -> HealthProjection:
        """Degraded probe: optional dependencies and degradation capability."""
        return build_health_projection(service="api")

    @app.get("/health", response_model=HealthProjection)
    async def health_summary() -> HealthProjection:
        """Combined health summary."""
        return build_health_projection(service="api")

    @app.get("/me", response_model=dict[str, Any])
    async def me(subject: auth.SubjectDep) -> dict[str, Any]:
        """Protected demo route: returns the resolved subject.

        T003 uses this route to prove that API endpoints share the same
        authentication guard as the Web frontend.
        """
        return {"account_id": subject.account_id, "session_id": subject.session_id}

    @app.get("/_test/recovery-token", response_model=dict[str, Any])
    async def test_recovery_token(email: str) -> dict[str, Any]:
        """Test-only endpoint to retrieve a recovery token without email delivery.

        This endpoint is prefixed with `/_test/` and is only safe because the
        T003 identity service is in-memory. It must not be exposed in production.
        """
        from science_companion.identity import IdentityError

        service: IdentityService = app.state.identity_service
        try:
            token = service.test_create_recovery_token(email)
        except IdentityError as exc:
            return {"error": str(exc)}
        return {"token": token}

    def _get_workflow_service(request: Request) -> WorkflowService:
        service: WorkflowService | None = getattr(request.app.state, "workflow_service", None)
        if service is None:
            raise RuntimeError("WorkflowService not attached to application state.")
        return service

    @app.post("/_test/runs/{run_id}/advance", response_model=RunProjection)
    async def test_advance_run(
        run_id: str,
        service: Annotated[WorkflowService, Depends(_get_workflow_service)],
        subject: auth.SubjectDep,
    ) -> RunProjection:
        """Test-only endpoint to deterministically advance a run by one node."""
        try:
            return service.advance_run(subject.account_id, run_id)
        except WorkflowError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": "workflow_transition_failed", "message": str(exc)},
            ) from exc

    return app
