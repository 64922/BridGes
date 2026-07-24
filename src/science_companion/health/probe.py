"""Health probe logic.

Probes assemble the public HealthProjection without exposing internal objects.
All dependencies are logical; concrete infrastructure checks are added as they
are implemented in later tickets.
"""

from typing import Callable

from science_companion import __version__
from science_companion.contracts.health import DependencyHealth, HealthProjection, HealthStatus


REQUIRED_DEPENDENCIES = ["configuration"]
OPTIONAL_DEPENDENCIES: list[str] = []


def _probe_configuration() -> DependencyHealth:
    return DependencyHealth(
        name="configuration",
        status=HealthStatus.PASS,
        required=True,
        message="Configuration schema loaded with safe defaults.",
    )


_DEPENDENCY_PROBES: dict[str, Callable[[], DependencyHealth]] = {
    "configuration": _probe_configuration,
}


def _probe_dependency(name: str, required: bool) -> DependencyHealth:
    """Return dependency status.

    In T001 the only required dependency is configuration parsing. Later tickets
    add PostgreSQL, object storage, Temporal, domain packs, etc.
    """
    probe = _DEPENDENCY_PROBES.get(name)
    if probe is None:
        return DependencyHealth(
            name=name,
            status=HealthStatus.UNKNOWN,
            required=required,
            message="Probe not yet implemented.",
        )
    health = probe()
    health.required = required
    return health


def build_health_projection(service: str) -> HealthProjection:
    """Assemble a HealthProjection for the named service."""
    dependencies: list[DependencyHealth] = []
    ready = HealthStatus.PASS
    degraded = HealthStatus.PASS

    for name in REQUIRED_DEPENDENCIES:
        dep = _probe_dependency(name, required=True)
        dependencies.append(dep)
        if dep.status != HealthStatus.PASS:
            ready = HealthStatus.FAIL

    for name in OPTIONAL_DEPENDENCIES:
        dep = _probe_dependency(name, required=False)
        dependencies.append(dep)
        if dep.status != HealthStatus.PASS:
            degraded = HealthStatus.FAIL

    return HealthProjection(
        service=service,
        version=__version__,
        live=HealthStatus.PASS,
        ready=ready,
        degraded=degraded,
        dependencies=dependencies,
    )
