"""FastAPI application for the Science Companion API."""

from fastapi import FastAPI

from science_companion import __version__
from science_companion.contracts.health import HealthProjection, HealthStatus
from science_companion.health.probe import build_health_projection


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Science Companion API",
        version=__version__,
        description="长期科学学习与表达伙伴 API",
    )

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

    return app
