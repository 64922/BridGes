"""Shared cross-cutting contracts."""

from .health import DependencyHealth, HealthProjection, HealthStatus

__all__ = ["DependencyHealth", "HealthProjection", "HealthStatus"]
