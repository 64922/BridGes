"""FastAPI application for the Science Companion API."""

from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request, status
from pydantic import ValidationError as PydanticValidationError

from science_companion import __version__
from science_companion.ai import CapabilityRegistry, ModelGateway, StubQwenAdapter
from science_companion.api import auth, evaluation, projects, science, scope, vault, workflows
from science_companion.config import get_settings
from science_companion.contracts.ai import CapabilityKind, CapabilityRecord, CapabilityStatus, FallbackPolicy, RetryPolicy
from science_companion.evaluation import EvaluationService
from science_companion.contracts.health import HealthProjection, HealthStatus
from science_companion.contracts.workflows import RunProjection, WorkflowRunStatus
from science_companion.health.probe import build_health_projection
from science_companion.identity import IdentityService
from science_companion.invalidation import AffectedDownstream, InvalidationService
from science_companion.observability.service import ObservabilityService
from science_companion.projects import ProjectService
from science_companion.science import ScienceSearchService, ScienceSourceService
from science_companion.science.service import build_source_impact_resolver
from science_companion.scope import ScopeEnforcer
from science_companion.vault import (
    InMemoryVaultRepository,
    MemoryDeviceVaultPort,
    VaultService,
)
from science_companion.workflows import WorkflowError, WorkflowService


def _register_builtin_capabilities(registry: CapabilityRegistry) -> None:
    """Register the Qwen capabilities used by built-in workflows at T009."""
    registry.register(
        CapabilityRecord(
            name="qwen_text_chat",
            version="1",
            kind=CapabilityKind.MODEL,
            vendor="qwen",
            region="cn-beijing",
            model_id="qwen3.7-plus",
            input_schema_version="chat-messages-v1",
            output_schema_version="chat-completion-v1",
            status=CapabilityStatus.VERIFIED,
            retry_policy=RetryPolicy(max_attempts=3, backoff_seconds=1.0),
            fallback_policy=FallbackPolicy(
                fallback_capability_name="qwen_text_chat_fallback",
                fallback_capability_version="1",
            ),
            prompt_version="2026-07-24",
        )
    )
    registry.register(
        CapabilityRecord(
            name="qwen_text_chat_fallback",
            version="1",
            kind=CapabilityKind.MODEL,
            vendor="qwen",
            region="cn-beijing",
            model_id="qwen3.6-flash",
            input_schema_version="chat-messages-v1",
            output_schema_version="chat-completion-v1",
            status=CapabilityStatus.VERIFIED,
            retry_policy=RetryPolicy(max_attempts=2, backoff_seconds=1.0),
            prompt_version="2026-07-24",
        )
    )
    registry.register(
        CapabilityRecord(
            name="qwen_structured_output",
            version="1",
            kind=CapabilityKind.MODEL,
            vendor="qwen",
            region="cn-beijing",
            model_id="qwen3.6-flash",
            input_schema_version="structured-messages-v1",
            output_schema_version="json-schema-v1",
            status=CapabilityStatus.VERIFIED,
            retry_policy=RetryPolicy(max_attempts=2, backoff_seconds=1.0),
            prompt_version="2026-07-24",
        )
    )


def _register_builtin_workflows(service: WorkflowService) -> None:
    """Register the minimal workflow templates available at T006/T009."""
    service.register_workflow(
        name="generic_science_task",
        version="1",
        nodes=[
            {
                "node_id": "compile_context",
                "node_name": "编译上下文",
                "human_gate": False,
                "capability_name": "qwen_text_chat",
                "capability_version": "1",
            },
            {
                "node_id": "produce_output",
                "node_name": "生成产物",
                "human_gate": False,
                "capability_name": "qwen_structured_output",
                "capability_version": "1",
            },
        ],
        terminal_states=[
            WorkflowRunStatus.SUCCEEDED,
            WorkflowRunStatus.BLOCKED,
            WorkflowRunStatus.CANCELLED,
        ],
    )


def _register_builtin_invalidation_resolvers(service: InvalidationService) -> None:
    """Register generic impact resolvers for cache, index, runs and vault capsules.

    T011 foundation provides these resolvers so that any object invalidation
    produces a scope-correct impact set covering the most common downstream
    consumers. Domain modules register additional resolvers later without
    changing tombstone priority, history retention or failure lockout.
    """

    def _cache_resolver(event: Any) -> list[AffectedDownstream]:
        return [
            AffectedDownstream(
                downstream_id=f"cache:{event.object_ref.object_id}",
                downstream_type="cache",
                object_refs=[event.object_ref.object_id],
                scope_envelope=event.scope_envelope,
                action="invalidate",
            )
        ]

    def _index_resolver(event: Any) -> list[AffectedDownstream]:
        return [
            AffectedDownstream(
                downstream_id=f"index:{event.object_ref.object_id}",
                downstream_type="index_projection",
                object_refs=[event.object_ref.object_id],
                scope_envelope=event.scope_envelope,
                action="revalidate",
            )
        ]

    def _run_resolver(event: Any) -> list[AffectedDownstream]:
        return [
            AffectedDownstream(
                downstream_id=f"run:{event.object_ref.object_id}",
                downstream_type="workflow_run",
                object_refs=[event.object_ref.object_id],
                scope_envelope=event.scope_envelope,
                action="block_new_use",
            )
        ]

    def _vault_capsule_resolver(event: Any) -> list[AffectedDownstream]:
        return [
            AffectedDownstream(
                downstream_id=f"vault_capsule:{event.object_ref.object_id}",
                downstream_type="vault_capsule",
                object_refs=[event.object_ref.object_id],
                scope_envelope=event.scope_envelope,
                action="revoke",
            )
        ]

    service.register_impact_resolver("cache", _cache_resolver)
    service.register_impact_resolver("index_projection", _index_resolver)
    service.register_impact_resolver("workflow_run", _run_resolver)
    service.register_impact_resolver("vault_capsule", _vault_capsule_resolver)


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

    # T011: attach the shared invalidation service and register generic downstream
    # resolvers before any consumer is constructed.
    invalidation_service = InvalidationService(
        scope_enforcer=app.state.scope_enforcer,
    )
    _register_builtin_invalidation_resolvers(invalidation_service)
    app.state.invalidation_service = invalidation_service

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
        invalidation_service=invalidation_service,
    )

    # T009: attach the capability registry, model gateway and stub adapter.
    capability_registry = CapabilityRegistry()
    _register_builtin_capabilities(capability_registry)
    model_gateway = ModelGateway(capability_registry)
    stub_adapter = StubQwenAdapter()
    for capability in capability_registry.list_active():
        model_gateway.register_adapter(capability.name, capability.version, stub_adapter)
    app.state.capability_registry = capability_registry
    app.state.model_gateway = model_gateway

    # T010: attach the observability service. It is passed to services so that
    # trace/metric/log/audit events share the same run/subject/project/object
    # correlation and never copy private body, full prompts or keys.
    app.state.observability_service = ObservabilityService()

    # T006/T009: attach the in-memory workflow service and register workflows.
    workflow_service = WorkflowService(
        scope_enforcer=app.state.scope_enforcer,
        model_gateway=model_gateway,
        observability_service=app.state.observability_service,
        invalidation_service=invalidation_service,
    )
    _register_builtin_workflows(workflow_service)
    app.state.workflow_service = workflow_service

    # T012: attach the in-memory evaluation service and routes.
    evaluation_service = EvaluationService(workflow_service=workflow_service)
    app.state.evaluation_service = evaluation_service

    # T013: attach the in-memory science source service and register its impact
    # resolver so source invalidation propagates to index, cache and runs.
    science_source_service = ScienceSourceService(
        scope_enforcer=app.state.scope_enforcer,
        invalidation_service=invalidation_service,
    )
    invalidation_service.register_impact_resolver(
        "science_source", build_source_impact_resolver(science_source_service)
    )
    app.state.science_source_service = science_source_service

    # T014: attach the scoped hybrid search service.
    app.state.science_search_service = ScienceSearchService(
        source_service=science_source_service,
        scope_enforcer=app.state.scope_enforcer,
        invalidation_service=invalidation_service,
    )

    app.include_router(auth.router)
    app.include_router(projects.router)
    app.include_router(vault.router)
    app.include_router(workflows.router)
    app.include_router(scope.router)
    app.include_router(evaluation.router)
    app.include_router(science.router)

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
