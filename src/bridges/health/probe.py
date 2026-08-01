"""Health probe logic.

Probes assemble the public HealthProjection without exposing internal objects.
All dependencies are logical; concrete infrastructure checks are added as they
are implemented in later tickets.
"""

from typing import Callable

from pydantic import ValidationError

from bridges import __version__
from bridges.config import get_settings
from bridges.contracts.health import DependencyHealth, HealthProjection, HealthStatus
from bridges.contracts.observability import SLIMetricKind, SLISeverity


REQUIRED_DEPENDENCIES = ["configuration"]
OPTIONAL_DEPENDENCIES: list[str] = ["observability"]


def _probe_configuration() -> DependencyHealth:
    try:
        settings = get_settings()
    except (ValidationError, ValueError) as exc:
        return DependencyHealth(
            name="configuration",
            status=HealthStatus.FAIL,
            required=True,
            message=f"Configuration load failed: {exc}",
        )
    return DependencyHealth(
        name="configuration",
        status=HealthStatus.PASS,
        required=True,
        message=f"Configuration loaded: environment={settings.environment}",
    )


def _probe_observability() -> DependencyHealth:
    """T010: verify the observability contract modules are importable and sane."""
    try:
        from bridges.observability.service import ObservabilityService

        service = ObservabilityService()
        sli = service.register_sli(
            workload_name="health_probe",
            metric_kind=SLIMetricKind.AVAILABILITY,
            description="Health probe availability check",
            unit="ratio",
            window="1m",
            owner="platform",
        )
        service.register_slo(
            sli_id=sli.sli_id,
            target=0.99,
            alert_threshold=0.95,
            severity=SLISeverity.HIGH,
        )
    except Exception as exc:  # noqa: BLE001
        return DependencyHealth(
            name="observability",
            status=HealthStatus.FAIL,
            required=False,
            message=f"Observability contract failed: {exc}",
        )
    return DependencyHealth(
        name="observability",
        status=HealthStatus.PASS,
        required=False,
        message="Observability contract loaded and SLI/SLO registration works",
    )


_DEPENDENCY_PROBES: dict[str, Callable[[], DependencyHealth]] = {
    "configuration": _probe_configuration,
    "observability": _probe_observability,
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
