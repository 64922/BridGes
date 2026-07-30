"""FastAPI application for the Science Companion API."""

from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request, status
from pydantic import ValidationError as PydanticValidationError

from science_companion import __version__
from science_companion.ai import (
    CapabilityRegistry,
    CassetteStore,
    ModelGateway,
    QwenApiClient,
    QwenAsrAdapter,
    QwenOcrAdapter,
    QwenStructuredOutputAdapter,
    QwenTextChatAdapter,
    QwenTtsAdapter,
    QwenVisionAdapter,
    StubQwenAdapter,
)
from science_companion.api import (
    auth,
    evaluation,
    expression,
    institution,
    projects,
    science,
    scope,
    sharing,
    vault,
    workflows,
)
from science_companion.api.media import router as media_router
from science_companion.config import get_settings
from science_companion.contracts.ai import (
    CapabilityKind,
    CapabilityRecord,
    CapabilityStatus,
    FallbackPolicy,
    RetryPolicy,
)
from science_companion.contracts.health import HealthProjection, HealthStatus
from science_companion.contracts.workflows import RunProjection, WorkflowRunStatus
from science_companion.evaluation import EvaluationService
from science_companion.expression import ExpressionService
from science_companion.health.probe import build_health_projection
from science_companion.identity import IdentityService
from science_companion.institution import InstitutionService
from science_companion.invalidation import AffectedDownstream, InvalidationService
from science_companion.learning import (
    InMemoryLearningRepository,
    LearningPathService,
    LearningService,
    ReviewSchedulingService,
    TeachingService,
)
from science_companion.learning.api import router as learning_router
from science_companion.media import (
    AccessibilityService,
    InMemoryAudioStorage,
    MediaGenerationService,
    MediaIngestionService,
    MediaPublishService,
    QwenTtsNarrationSynthesizer,
    SandboxService,
    StoryboardService,
    build_media_impact_resolver,
    build_media_publish_impact_resolver,
)
from science_companion.observability.service import ObservabilityService
from science_companion.profiles import InMemoryProfileRepository, ProfileService
from science_companion.profiles.api import router as profiles_router
from science_companion.projects import ProjectService
from science_companion.sharing import SharingService
from science_companion.science import (
    ClaimEvidenceService,
    ScienceSearchService,
    ScienceSourceService,
)
from science_companion.science.claims import (
    ClaimGraphRevalidationHandler,
    build_claim_impact_resolver,
)
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
    # T061: real Qwen OCR and vision capabilities for media/science ingestion.
    registry.register(
        CapabilityRecord(
            name="qwen_ocr",
            version="1",
            kind=CapabilityKind.MODEL,
            vendor="qwen",
            region="cn-beijing",
            model_id="qwen-vl-ocr",
            input_schema_version="image-ocr-v1",
            output_schema_version="ocr-text-v1",
            supported_modalities=["text", "image"],
            status=CapabilityStatus.VERIFIED,
            retry_policy=RetryPolicy(max_attempts=3, backoff_seconds=1.0),
            prompt_version="2026-07-24",
        )
    )
    registry.register(
        CapabilityRecord(
            name="qwen_vision",
            version="1",
            kind=CapabilityKind.MODEL,
            vendor="qwen",
            region="cn-beijing",
            model_id="qwen3-vl-plus",
            input_schema_version="image-vision-v1",
            output_schema_version="vision-text-v1",
            supported_modalities=["text", "image"],
            status=CapabilityStatus.VERIFIED,
            retry_policy=RetryPolicy(max_attempts=3, backoff_seconds=1.0),
            prompt_version="2026-07-24",
        )
    )
    # T025: expression draft generation capability; deterministic generator owns
    # fact-lock binding, but the capability records an immutable run lock.
    registry.register(
        CapabilityRecord(
            name="expression_draft_generation",
            version="1",
            kind=CapabilityKind.MODEL,
            vendor="qwen",
            region="cn-beijing",
            model_id="qwen3.6-flash",
            input_schema_version="expression-brief-v1",
            output_schema_version="draft-spans-v1",
            status=CapabilityStatus.VERIFIED,
            retry_policy=RetryPolicy(max_attempts=2, backoff_seconds=1.0),
            prompt_version="2026-07-24",
        )
    )
    # T060: ASR capabilities for audio/video ingestion. Real Qwen ASR adapters
    # are registered when an API key is available; otherwise the stub adapter
    # keeps local tests deterministic.
    registry.register(
        CapabilityRecord(
            name="qwen_asr_short",
            version="1",
            kind=CapabilityKind.MODEL,
            vendor="qwen",
            region="cn-beijing",
            model_id="qwen3-asr-flash",
            input_schema_version="audio-upload-v1",
            output_schema_version="transcript-v1",
            status=CapabilityStatus.VERIFIED,
            retry_policy=RetryPolicy(max_attempts=2, backoff_seconds=1.0),
            prompt_version="2026-07-24",
        )
    )
    registry.register(
        CapabilityRecord(
            name="qwen_asr_long",
            version="1",
            kind=CapabilityKind.MODEL,
            vendor="qwen",
            region="cn-beijing",
            model_id="qwen3-asr-flash-filetrans",
            input_schema_version="audio-file-v1",
            output_schema_version="transcript-v1",
            status=CapabilityStatus.VERIFIED,
            retry_policy=RetryPolicy(max_attempts=2, backoff_seconds=1.0),
            prompt_version="2026-07-24",
        )
    )
    # T062: real Qwen TTS capabilities for accessibility narration synthesis.
    # qwen3-tts-flash is the primary; qwen3-tts-instruct-flash is the fallback
    # when instruction-controlled speech is needed.
    registry.register(
        CapabilityRecord(
            name="qwen_tts",
            version="1",
            kind=CapabilityKind.MODEL,
            vendor="qwen",
            region="cn-beijing",
            model_id="qwen3-tts-flash",
            input_schema_version="tts-text-v1",
            output_schema_version="tts-audio-v1",
            supported_modalities=["text", "audio"],
            status=CapabilityStatus.VERIFIED,
            retry_policy=RetryPolicy(max_attempts=2, backoff_seconds=1.0),
            fallback_policy=FallbackPolicy(
                fallback_capability_name="qwen_tts_instruct",
                fallback_capability_version="1",
            ),
            prompt_version="2026-07-24",
        )
    )
    registry.register(
        CapabilityRecord(
            name="qwen_tts_instruct",
            version="1",
            kind=CapabilityKind.MODEL,
            vendor="qwen",
            region="cn-beijing",
            model_id="qwen3-tts-instruct-flash",
            input_schema_version="tts-instruct-v1",
            output_schema_version="tts-audio-v1",
            supported_modalities=["text", "audio"],
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
    # T024: review tasks can be materialized as timed workflow runs.
    service.register_workflow(
        name="review_task",
        version="1",
        nodes=[
            {
                "node_id": "review_prompt",
                "node_name": "复习提示",
                "human_gate": False,
                "capability_name": "qwen_text_chat",
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

    # T036: attach the explicit sharing service. It coordinates share previews,
    # minimized project copies, object grants and short-lived invite tokens.
    app.state.sharing_service = SharingService(
        vault_service=app.state.vault_service,
        scope_enforcer=app.state.scope_enforcer,
        identity_service=app.state.identity_service,
    )

    # T037: attach the institution management service. It manages institutions,
    # memberships, policies, institution-owned projects and controlled content
    # access while preserving the personal vault boundary.
    app.state.institution_service = InstitutionService()
    app.state.institution_service.bind_sharing_service(app.state.sharing_service)

    # T037: bind institution providers into the scope enforcer now that both the
    # institution service and sharing service exist. This breaks the construction
    # cycle without changing the enforcer's public interface.
    app.state.scope_enforcer.set_institution_providers(
        institution_membership_provider=app.state.institution_service.get_membership,
        project_tenant_provider=app.state.sharing_service.get_project_institution_id,
    )

    # T010: attach the observability service early so downstream services can
    # emit privacy-preserving audit events.
    app.state.observability_service = ObservabilityService()

    # T018/T020: attach the in-memory profile service. Candidate profiles cannot be
    # treated as stable facts until the user accepts them through the human
    # decision loop; accepted candidates are promoted to active assertions. At
    # T020 the service also freezes, deletes, rolls back and exports assertions
    # while propagating invalidations to slices, cache, index and runs.
    app.state.profile_service = ProfileService(
        repository=InMemoryProfileRepository(),
        scope_enforcer=app.state.scope_enforcer,
        invalidation_service=invalidation_service,
        observability_service=app.state.observability_service,
    )

    # T020: register a profile-specific impact resolver so assertion deletions
    # produce scope-correct memory-slice downstreams in addition to the generic
    # cache, index and run resolvers registered by T011.
    def _profile_assertion_resolver(event: Any) -> list[AffectedDownstream]:
        affected: list[AffectedDownstream] = []
        profile_service = app.state.profile_service
        object_ref = event.object_ref
        if object_ref.domain.value != "personal_vault":
            return affected
        slices = profile_service.find_slices_for_assertion(
            object_ref.owner_id, object_ref.object_id
        )
        for slice_ in slices:
            affected.append(
                AffectedDownstream(
                    downstream_id=f"memory_slice:{slice_.slice_id}",
                    downstream_type="memory_slice",
                    object_refs=[slice_.slice_id],
                    scope_envelope=event.scope_envelope,
                    action="revoke",
                    details={"assertion_id": object_ref.object_id},
                )
            )
        return affected

    invalidation_service.register_impact_resolver(
        "profile_assertion", _profile_assertion_resolver
    )

    # T009/T059: attach the capability registry, model gateway and adapters.
    capability_registry = CapabilityRegistry()
    _register_builtin_capabilities(capability_registry)
    model_gateway = ModelGateway(capability_registry)

    settings = app.state.settings
    if (
        settings is not None
        and settings.qwen_api_key is not None
        and not settings.qwen_force_stub
    ):
        cassette_store = None
        if settings.qwen_cassette_dir is not None:
            cassette_store = CassetteStore(Path(settings.qwen_cassette_dir))
        qwen_client = QwenApiClient(
            api_key=settings.qwen_api_key,
            workspace_id=settings.qwen_workspace_id,
            region=settings.qwen_region,
            cassette_store=cassette_store,
            record_mode=settings.qwen_record_cassettes,
        )
        model_gateway.register_adapter(
            "qwen_text_chat", "1", QwenTextChatAdapter(qwen_client)
        )
        model_gateway.register_adapter(
            "qwen_text_chat_fallback", "1", QwenTextChatAdapter(qwen_client)
        )
        model_gateway.register_adapter(
            "qwen_structured_output", "1", QwenStructuredOutputAdapter(qwen_client)
        )
        model_gateway.register_adapter(
            "qwen_ocr", "1", QwenOcrAdapter(qwen_client)
        )
        model_gateway.register_adapter(
            "qwen_vision", "1", QwenVisionAdapter(qwen_client)
        )
        model_gateway.register_adapter(
            "qwen_asr_short", "1", QwenAsrAdapter(qwen_client)
        )
        model_gateway.register_adapter(
            "qwen_asr_long", "1", QwenAsrAdapter(qwen_client)
        )
        # T062: TTS adapters for accessibility narration synthesis.
        tts_adapter = QwenTtsAdapter(qwen_client)
        model_gateway.register_adapter("qwen_tts", "1", tts_adapter)
        model_gateway.register_adapter("qwen_tts_instruct", "1", tts_adapter)

    stub_adapter = StubQwenAdapter()
    for capability in capability_registry.list_active():
        if not model_gateway.is_adapter_registered(capability.name, capability.version):
            model_gateway.register_adapter(capability.name, capability.version, stub_adapter)
    app.state.capability_registry = capability_registry
    app.state.model_gateway = model_gateway

    # T006/T009: attach the in-memory workflow service and register workflows.
    workflow_service = WorkflowService(
        scope_enforcer=app.state.scope_enforcer,
        model_gateway=model_gateway,
        observability_service=app.state.observability_service,
        invalidation_service=invalidation_service,
        profile_service=app.state.profile_service,
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
        model_gateway=(
            model_gateway if settings is not None and not settings.qwen_force_stub else None
        ),
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

    # T015: attach the claim--evidence--citation service.
    claim_evidence_service = ClaimEvidenceService(
        source_service=science_source_service,
        search_service=app.state.science_search_service,
        invalidation_service=invalidation_service,
        model_gateway=model_gateway,
    )
    app.state.claim_evidence_service = claim_evidence_service

    # T017: source invalidation must propagate to claim graphs and fact lock sets,
    # and revalidation must create new graph versions rather than overwrite history.
    invalidation_service.register_impact_resolver(
        "claim_graph", build_claim_impact_resolver(claim_evidence_service)
    )
    invalidation_service.register_revalidation_handler(
        "claim_graph", ClaimGraphRevalidationHandler(claim_evidence_service)
    )

    # T030: attach the media ingestion service and register its impact resolver so
    # media asset invalidation propagates to index, cache and runs.
    media_ingestion_service = MediaIngestionService(
        scope_enforcer=app.state.scope_enforcer,
        invalidation_service=invalidation_service,
        model_gateway=(
            model_gateway if settings is not None and not settings.qwen_force_stub else None
        ),
    )
    invalidation_service.register_impact_resolver(
        "media_asset", build_media_impact_resolver(media_ingestion_service)
    )
    app.state.media_ingestion_service = media_ingestion_service

    # T033: attach the storyboard and sandbox services for structured storyboard
    # generation and isolated code execution.
    app.state.storyboard_service = StoryboardService()
    app.state.sandbox_service = SandboxService()

    # T032/T034: attach the media generation service and the accessibility
    # service that produces narration, captions, transcripts, keyboard paths,
    # reduced-motion variants and sequential reading views for media targets.
    # T062: when TTS is available, use QwenTtsNarrationSynthesizer to call
    # real Qwen TTS and transfer audio to controlled storage.
    app.state.media_generation_service = MediaGenerationService()
    narration_synthesizer = None
    if (
        settings is not None
        and settings.qwen_api_key is not None
        and not settings.qwen_force_stub
        and model_gateway.is_adapter_registered("qwen_tts", "1")
    ):
        narration_synthesizer = QwenTtsNarrationSynthesizer(
            model_gateway=model_gateway,
            audio_storage=InMemoryAudioStorage(),
        )
    app.state.accessibility_service = AccessibilityService(
        storyboard_service=app.state.storyboard_service,
        generation_service=app.state.media_generation_service,
        media_ingestion_service=media_ingestion_service,
        narration_synthesizer=narration_synthesizer,
    )

    # T035: attach the multi-modal publish service for cross-media consistency
    # checking and multi-modal publish gate evaluation. Register the publish
    # impact resolver so source or fact-lock invalidation propagates.
    publish_service = MediaPublishService(
        generation_service=app.state.media_generation_service,
        storyboard_service=app.state.storyboard_service,
        media_ingestion_service=media_ingestion_service,
        accessibility_service=app.state.accessibility_service,
        invalidation_service=invalidation_service,
    )
    app.state.media_publish_service = publish_service
    invalidation_service.register_impact_resolver(
        "media_publish", build_media_publish_impact_resolver(publish_service)
    )

    # T025/T029: attach the expression service. It consumes claim graphs and fact
    # locks from T016, memory slices from T019, records model run locks from T009,
    # and uses the workflow service and invalidation service for release gating.
    expression_service = ExpressionService(
        claim_service=claim_evidence_service,
        profile_service=app.state.profile_service,
        model_gateway=model_gateway,
        invalidation_service=invalidation_service,
        workflow_service=workflow_service,
    )
    app.state.expression_service = expression_service

    # T021: attach the in-memory learning service for missions and diagnosis.
    learning_repository = InMemoryLearningRepository()
    learning_service = LearningService(repository=learning_repository)
    app.state.learning_service = learning_service

    # T022: attach the in-memory teaching service for short lessons and retrieval.
    app.state.teaching_service = TeachingService(repository=learning_repository)

    # T024: attach the in-memory review scheduling service. It depends on the
    # same learning repository and is injected into the learning-path service so
    # that path changes automatically cancel or reschedule future review tasks.
    review_scheduling_service = ReviewSchedulingService(repository=learning_repository)
    app.state.review_scheduling_service = review_scheduling_service

    # T023: attach the in-memory learning-path service for recording learning
    # evidence, proposing knowledge-state updates, human confirmation and path
    # recompilation. Rejected proposals cannot be silently reapplied.
    app.state.learning_path_service = LearningPathService(
        repository=learning_repository,
        review_scheduler=review_scheduling_service,
    )

    app.include_router(auth.router)
    app.include_router(projects.router)
    app.include_router(vault.router)
    app.include_router(sharing.router)
    app.include_router(institution.router)
    app.include_router(profiles_router)
    app.include_router(workflows.router)
    app.include_router(scope.router)
    app.include_router(evaluation.router)
    app.include_router(science.router)
    app.include_router(expression.router)
    app.include_router(media_router)
    app.include_router(learning_router)

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
