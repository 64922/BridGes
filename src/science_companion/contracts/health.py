"""Authoritative health contract shared between API and Web.

This module is the single source of truth for the health projection schema.
TypeScript types are generated from the OpenAPI document produced by the API.
"""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class HealthStatus(str, Enum):
    """Top-level health verdict."""

    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"


class DependencyHealth(BaseModel):
    """Health of one external dependency."""

    name: str = Field(description="Logical dependency name.")
    status: HealthStatus = Field(description="Current dependency status.")
    required: bool = Field(
        description="Whether this dependency is required for the service to be ready."
    )
    message: str | None = Field(default=None, description="Human-readable detail.")
    latency_ms: float | None = Field(
        default=None, description="Probe latency in milliseconds if measured."
    )


class HealthProjection(BaseModel):
    """Unified health projection exposed by /health/* endpoints and consumed by the Web UI.

    live:   process event loop can respond.
    ready:  all required dependencies are healthy.
    degraded: optional dependencies that are unhealthy or operating in degraded mode.
    """

    service: str = Field(description="Service logical name, e.g. api or web.")
    version: str = Field(description="Product version.")
    live: HealthStatus = Field(description="Process liveness.")
    ready: HealthStatus = Field(description="Readiness for traffic.")
    degraded: HealthStatus = Field(description="Degraded state of optional dependencies.")
    dependencies: list[DependencyHealth] = Field(
        default_factory=list, description="Per-dependency status."
    )
    extensions: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional opaque telemetry; UI must not depend on undocumented keys.",
    )
