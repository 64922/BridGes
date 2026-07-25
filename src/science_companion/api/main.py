"""FastAPI application for the Science Companion API."""

from typing import Any

from fastapi import FastAPI

from science_companion import __version__
from science_companion.api import auth, projects, vault
from science_companion.contracts.health import HealthProjection, HealthStatus
from science_companion.health.probe import build_health_projection
from science_companion.identity import IdentityService
from science_companion.projects import ProjectService
from science_companion.vault import (
    InMemoryVaultRepository,
    MemoryDeviceVaultPort,
    VaultService,
)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Science Companion API",
        version=__version__,
        description="长期科学学习与表达伙伴 API",
    )

    # T003: attach the in-memory identity service. Later tickets will switch to a
    # persistent adapter while keeping the same interface.
    app.state.identity_service = IdentityService()

    # T004: attach the in-memory project service. Later tickets will switch to a
    # persistent adapter while keeping the same interface.
    app.state.project_service = ProjectService()

    # T005: attach the in-memory vault service and device port.
    vault_repository = InMemoryVaultRepository()
    app.state.vault_service = VaultService(
        repository=vault_repository,
        device_port=MemoryDeviceVaultPort(vault_repository),
    )

    app.include_router(auth.router)
    app.include_router(projects.router)
    app.include_router(vault.router)

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

    return app
