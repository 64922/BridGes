"""FastAPI application for the BridGes API."""

from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError as PydanticValidationError

from bridges import __version__
from bridges.ai import (
    CapabilityRegistry,
    CapabilityRegistryError,
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
from bridges.api import (
    auth,
    chat,
    credentials,
    domain_packs,
    evaluation,
    expression,
    institution,
    projects,
    science,
    scope,
    sharing,
    sync,
    vault,
    workflows,
)
from bridges.api.media import router as media_router
from bridges.chat import ChatAttachmentService, ChatService, ConversationRepository
from bridges.config import get_settings
from bridges.contracts.ai import (
    CapabilityKind,
    CapabilityRecord,
    CapabilityStatus,
    RetryPolicy,
)
from bridges.contracts.domain import (
    PackImpactAction,
    PackImpactCategory,
    PackImpactItem,
)
from bridges.contracts.health import DependencyHealth, HealthProjection, HealthStatus
from bridges.contracts.projects import ObjectRef
from bridges.contracts.workflows import RunProjection, WorkflowRunStatus
from bridges.credentials.probes import CapabilityProbeService
from bridges.credentials.service import KeyCredentialService
from bridges.credentials.store import (
    CredentialStorePort,
    EncryptedVolumeCredentialStore,
    InMemoryCredentialStore,
    OsCredentialStore,
)
from bridges.domain import (
    DomainPackLoader,
    DomainPackRegistry,
    DomainPackValidationRuntime,
    create_astronomy_pack,
    create_computer_science_pack,
    create_earth_climate_pack,
    create_life_science_pack,
    create_math_formal_proof_pack,
    create_medical_high_risk_pack,
    create_physics_chemistry_pack,
    create_standards_datasets_pack,
)
from bridges.domain.pack_lifecycle import (
    DomainPackLifecycleError,
    DomainPackLifecycleService,
)
from bridges.domain.workbench import DomainPackWorkbenchService
from bridges.evaluation import EvaluationService
from bridges.expression import ExpressionService
from bridges.health.probe import build_health_projection
from bridges.identity import IdentityService
from bridges.institution import InstitutionService
from bridges.invalidation import AffectedDownstream, InvalidationService
from bridges.learning import (
    InMemoryLearningRepository,
    LearningPathService,
    LearningService,
    ReviewSchedulingService,
    TeachingService,
)
from bridges.learning.api import router as learning_router
from bridges.media import (
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
from bridges.observability.service import ObservabilityService
from bridges.persistence import (
    PersistenceError,
    SqliteStateStore,
    StateStore,
    build_state_store,
)
from bridges.profiles import InMemoryProfileRepository, ProfileService
from bridges.profiles.api import router as profiles_router
from bridges.projects import ProjectService
from bridges.science import (
    ClaimEvidenceService,
    ScienceSearchService,
    ScienceSourceService,
)
from bridges.science.claims import (
    ClaimGraphRevalidationHandler,
    build_claim_impact_resolver,
)
from bridges.science.service import build_source_impact_resolver
from bridges.scope import ScopeEnforcer
from bridges.sharing import SharingService
from bridges.storage import (
    BridgesDatabase,
    BridgesObjectRepository,
    EncryptedFileObjectStore,
    StorageError,
)
from bridges.sync import SyncService
from bridges.vault import (
    FernetVaultEncryptionAdapter,
    InMemoryDeviceKeychain,
    InMemoryDevicePairingRepository,
    InMemoryVaultRepository,
    MemoryDeviceVaultPort,
    VaultService,
)
from bridges.workflows import WorkflowError, WorkflowService


def _register_builtin_capabilities(registry: CapabilityRegistry) -> None:
    """Register the Qwen capabilities used by built-in workflows at T009.

    Issue 10 起按 ADR-0009 固定模型矩阵：核心对话绑定
    ``qwen3.7-plus-2026-05-26`` 唯一快照，不注册备用模型——失败只允许
    重试同一绑定，不得暗中切换模型。
    """
    registry.register(
        CapabilityRecord(
            name="qwen_text_chat",
            version="1",
            kind=CapabilityKind.MODEL,
            vendor="qwen",
            region="cn-beijing",
            model_id="qwen3.7-plus-2026-05-26",
            input_schema_version="chat-messages-v1",
            output_schema_version="chat-completion-v1",
            status=CapabilityStatus.VERIFIED,
            retry_policy=RetryPolicy(max_attempts=3, backoff_seconds=1.0),
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
            model_id="qwen3-asr-flash-2025-09-08",
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
    # T062: real Qwen TTS capability for accessibility narration synthesis.
    # Issue 10 起按 ADR-0009 固定绑定 qwen3-tts-flash-2025-11-27，不注册
    # 备用模型——失败只重试同一绑定。
    registry.register(
        CapabilityRecord(
            name="qwen_tts",
            version="1",
            kind=CapabilityKind.MODEL,
            vendor="qwen",
            region="cn-beijing",
            model_id="qwen3-tts-flash-2025-11-27",
            input_schema_version="tts-text-v1",
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


def _register_domain_pack_capabilities(capability_registry: CapabilityRegistry) -> None:
    """Register the deterministic TOOL capabilities declared by built-in packs.

    The packs declare logical capabilities (e.g. formal_proof_check) without
    binding to a concrete implementation; the registry maps each logical
    capability to a verified deterministic adapter, as required by T040.
    """
    factories = (
        create_math_formal_proof_pack,
        create_physics_chemistry_pack,
        create_life_science_pack,
        create_medical_high_risk_pack,
        create_earth_climate_pack,
        create_astronomy_pack,
        create_computer_science_pack,
        create_standards_datasets_pack,
    )
    for factory in factories:
        manifest = factory().manifest
        for validator in manifest.validators:
            try:
                capability_registry.get(
                    validator.capability_name, validator.capability_version
                )
            except CapabilityRegistryError:
                capability_registry.register(
                    CapabilityRecord(
                        name=validator.capability_name,
                        version=validator.capability_version,
                        kind=CapabilityKind.TOOL,
                        vendor="builtin",
                        region="local",
                        model_id="deterministic",
                        input_schema_version=validator.input_schema_version,
                        output_schema_version=validator.output_schema_version,
                        status=CapabilityStatus.VERIFIED,
                    )
                )


def _register_builtin_domain_packs(registry: DomainPackRegistry) -> None:
    """Register the eight built-in domain packs as draft candidates.

    T040–T045 deliver the packs as loadable candidates; T046 exposes them to
    the expert workbench where maintainers, reviewers and releasers govern
    signing, semantic diffs, gray release and activation.
    """
    for factory in (
        create_math_formal_proof_pack,
        create_physics_chemistry_pack,
        create_life_science_pack,
        create_medical_high_risk_pack,
        create_earth_climate_pack,
        create_astronomy_pack,
        create_computer_science_pack,
        create_standards_datasets_pack,
    ):
        registry.register(factory())


def create_app(state_store: StateStore | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="BridGes API",
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

    app.state.persistence_error = None
    if state_store is None and app.state.settings is not None:
        try:
            state_store = build_state_store(
                app.state.settings.database_url,
                encryption_key=app.state.settings.secret_key,
            )
        except PersistenceError as exc:
            app.state.persistence_error = str(exc)
    if (
        app.state.settings is not None
        and app.state.settings.environment.lower() == "production"
        and state_store is None
        and app.state.persistence_error is None
    ):
        app.state.persistence_error = (
            "生产环境必须配置 BRIDGES_DATABASE_URL，不能使用进程内存储。"
        )
    app.state.state_store = state_store
    app.state.persistence_mode = (
        "error"
        if app.state.persistence_error
        else "sqlite"
        if state_store is not None
        else "memory"
    )

    # Issue 05: 配置数据库时以同一数据目录初始化版本化 bridges.db 与账户隔离
    # 加密对象库（对象目录为数据库同目录下的 objects/）。首次启动事务化创建
    # 带版本记录的 bridges.db；失败与持久化错误同样进入 503 拒绝路径，绝不
    # 静默降级。对象加密密钥派生自 BRIDGES_SECRET_KEY，任何位置不落盘密钥。
    app.state.bridges_database = None
    app.state.object_repository = None
    if (
        isinstance(state_store, SqliteStateStore)
        and state_store.path != ":memory:"
        and app.state.persistence_error is None
    ):
        settings = app.state.settings
        if settings is not None and settings.secret_key is not None:
            secret_value = settings.secret_key.get_secret_value()
            if secret_value:
                try:
                    database = BridgesDatabase(Path(state_store.path))
                    app.state.bridges_database = database
                    app.state.object_repository = BridgesObjectRepository(
                        database,
                        EncryptedFileObjectStore(
                            Path(state_store.path).parent / "objects",
                            encryption_key=settings.secret_key,
                        ),
                    )
                except StorageError as exc:
                    app.state.persistence_error = str(exc)

    @app.middleware("http")
    async def reject_unpersisted_requests(request: Request, call_next: Any) -> Any:
        """持久化不可用时只保留健康检查，阻止私人数据进入内存。"""
        if app.state.persistence_error and not request.url.path.startswith("/health"):
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"detail": "持久化不可用，当前实例拒绝数据读写。"},
            )
        return await call_next(request)

    def app_health_projection() -> HealthProjection:
        """将持久化配置纳入统一就绪检查，避免静默降级到内存。"""
        projection = build_health_projection(service="api")
        persistence_error = getattr(app.state, "persistence_error", None)
        if persistence_error:
            projection.dependencies.append(
                DependencyHealth(
                    name="persistence",
                    status=HealthStatus.FAIL,
                    required=True,
                    message=persistence_error,
                )
            )
            projection.ready = HealthStatus.FAIL
        # Issue 05: 配置了版本化数据库时，把 bridges.db 健康度作为可选依赖
        # 上报；数据库不可查询时进入降级状态而非静默成功。
        bridges_database = getattr(app.state, "bridges_database", None)
        if bridges_database is not None:
            database_healthy = bridges_database.health_check()
            projection.dependencies.append(
                DependencyHealth(
                    name="bridges_storage",
                    status=(
                        HealthStatus.PASS if database_healthy else HealthStatus.FAIL
                    ),
                    required=False,
                    message=(
                        None
                        if database_healthy
                        else "bridges.db 当前不可查询，请检查数据目录。"
                    ),
                )
            )
            if not database_healthy:
                projection.degraded = HealthStatus.FAIL
        return projection

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

    # T003: use the durable state port when configured; tests can omit it and
    # retain isolated in-memory services.
    app.state.identity_service = IdentityService(
        state_store=state_store,
        object_repository=app.state.object_repository,
    )

    # T004: project ownership is durable whenever the configured state store is.
    app.state.project_service = ProjectService(
        scope_enforcer=app.state.scope_enforcer,
        state_store=state_store,
    )

    # T005/T038: attach the in-memory vault service, device port, and encrypted
    # local storage seam. Device-local objects are encrypted with a data key
    # wrapped by the device key stored in the system keychain.
    vault_repository = InMemoryVaultRepository(state_store=state_store)
    device_keychain = InMemoryDeviceKeychain()
    vault_encryption = FernetVaultEncryptionAdapter()
    device_pairing_repository = InMemoryDevicePairingRepository(state_store=state_store)
    app.state.vault_service = VaultService(
        repository=vault_repository,
        device_port=MemoryDeviceVaultPort(vault_repository),
        scope_enforcer=app.state.scope_enforcer,
        invalidation_service=invalidation_service,
        device_keychain=device_keychain,
        vault_encryption=vault_encryption,
        device_pairing_repository=device_pairing_repository,
    )
    app.state.device_keychain = device_keychain

    def authorize_sync_object(account_id: str, object_ref: ObjectRef) -> bool:
        """复用共享项目成员检查，默认拒绝未知对象域。"""
        if object_ref.domain.value == "personal_vault":
            return object_ref.owner_id == account_id
        if object_ref.domain.value == "shared_project":
            sharing_service = getattr(app.state, "sharing_service", None)
            if sharing_service is None:
                return False
            try:
                sharing_service.get_shared_project(account_id, object_ref.owner_id)
            except Exception:
                return False
            return True
        return False

    # T039: synchronization reads the same device certificates and key epochs
    # as the vault boundary, so revoked devices cannot submit old outbox edits.
    app.state.sync_service = SyncService(
        device_pairing_repository=device_pairing_repository,
        state_store=state_store,
        object_authorizer=authorize_sync_object,
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

    # Issue 10: 账户级百炼凭据存储与固定能力探测。
    # 源码环境使用操作系统凭据库（keyring，Windows 兜底 DPAPI）；容器环境
    # 使用自动生成主密钥保护的加密凭据卷（数据目录 credentials/ 子目录）。
    # 未配置数据库（内存模式，测试/E2E）时使用进程内替身，保证测试确定性。
    settings_at_credential = app.state.settings
    credential_store: CredentialStorePort = InMemoryCredentialStore()
    data_dir: Path | None = None
    if isinstance(state_store, SqliteStateStore) and state_store.path != ":memory:":
        data_dir = Path(state_store.path).parent
    if settings_at_credential is not None and data_dir is not None:
        if settings_at_credential.credential_backend == "encrypted-volume":
            credential_store = EncryptedVolumeCredentialStore(data_dir)
        else:
            credential_store = OsCredentialStore(data_dir=data_dir)
    app.state.credential_service = KeyCredentialService(
        credential_store=credential_store,
        probe_service=CapabilityProbeService(
            state_store=state_store,
            region=(
                settings_at_credential.qwen_region
                if settings_at_credential is not None
                else "cn-beijing"
            ),
            workspace_id=(
                settings_at_credential.qwen_workspace_id
                if settings_at_credential is not None
                else None
            ),
            cassette_dir=(
                settings_at_credential.qwen_cassette_dir
                if settings_at_credential is not None
                else None
            ),
            record_mode=(
                settings_at_credential.qwen_record_cassettes
                if settings_at_credential is not None
                else False
            ),
        ),
        observability_service=app.state.observability_service,
        state_store=state_store,
    )
    app.state.credential_store = credential_store

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
        # T062: TTS adapter for accessibility narration synthesis.
        tts_adapter = QwenTtsAdapter(qwen_client)
        model_gateway.register_adapter("qwen_tts", "1", tts_adapter)

    # Issue 10：生产环境禁止为真实模型 ID 注册 Stub 成功。StubQwenAdapter
    # 只允许在显式测试开关（qwen_force_stub）下使用，或绑定在内置
    # deterministic 工具能力上；缺少真实适配器的云端能力保持未注册，
    # 由网关返回明确的"未绑定适配器"阻塞结果，绝不伪装可用。
    stub_adapter = StubQwenAdapter()
    force_stub = settings is not None and settings.qwen_force_stub
    for capability in capability_registry.list_active():
        if model_gateway.is_adapter_registered(capability.name, capability.version):
            continue
        if force_stub or capability.model_id == "deterministic":
            model_gateway.register_adapter(capability.name, capability.version, stub_adapter)
    app.state.capability_registry = capability_registry
    app.state.model_gateway = model_gateway

    # Issue 11: 持久化流式聊天纵向切片。对话/消息/运行锁写入 bridges.db；
    # 未配置持久化数据目录（内存模式，仅测试/E2E）时不挂载聊天服务，
    # 路由返回"对话存储未启用"，绝不静默降级到内存。
    bridges_database = getattr(app.state, "bridges_database", None)
    if bridges_database is not None:
        object_repository = getattr(app.state, "object_repository", None)
        if object_repository is not None:
            app.state.chat_attachment_service = ChatAttachmentService(
                bridges_database, object_repository
            )
        app.state.chat_service = ChatService(
            repository=ConversationRepository(bridges_database),
            gateway=model_gateway,
            attachment_service=getattr(app.state, "chat_attachment_service", None),
        )

    # T040/T046: register the built-in domain packs as candidates and attach the
    # expert workbench. The workbench owns three-signature release, semantic
    # diffs and gray-release candidates; it never activates a pack implicitly.
    _register_domain_pack_capabilities(capability_registry)
    domain_pack_loader = DomainPackLoader(capability_registry=capability_registry)
    domain_pack_registry = DomainPackRegistry(domain_pack_loader)
    _register_builtin_domain_packs(domain_pack_registry)
    domain_pack_workbench = DomainPackWorkbenchService(
        registry=domain_pack_registry,
        runtime=DomainPackValidationRuntime(domain_pack_loader),
        loader=domain_pack_loader,
    )
    app.state.domain_pack_workbench = domain_pack_workbench

    # T047: 挂接领域包生命周期服务。它负责失效事件、紧急撤销、影响解析、
    # 重验证推进与受信回滚；影响源随各下游服务出现时注册。
    domain_pack_lifecycle = DomainPackLifecycleService(
        workbench=domain_pack_workbench,
        loader=domain_pack_loader,
    )
    app.state.domain_pack_lifecycle = domain_pack_lifecycle

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

    # T047: 撤销或失效的领域包不能开始新运行；运行门由生命周期服务裁决。
    def _pack_gate(pack_refs: Sequence[str]) -> None:
        try:
            domain_pack_lifecycle.require_packs_usable(pack_refs)
        except DomainPackLifecycleError as exc:
            raise WorkflowError(str(exc)) from exc

    workflow_service.set_pack_gate(_pack_gate)

    # T047: 运行影响源——引用该包版本的全部运行。
    def _pack_run_source(pack_id: str, version: str) -> list[PackImpactItem]:
        return [
            PackImpactItem(
                item_id=f"run:{projection.run_id}",
                category=PackImpactCategory.RUN,
                ref_id=projection.run_id,
                label=f"运行 {projection.run_id}（{projection.workflow_name}）",
                action=PackImpactAction.PRESERVE_AND_MARK,
                account_id=projection.context_envelope.account_id,
                project_id=projection.project_id,
                details={"run_status": projection.run_status.value},
            )
            for projection in workflow_service.find_runs_using_pack(pack_id, version)
        ]

    domain_pack_lifecycle.register_pack_impact_source(
        PackImpactCategory.RUN, _pack_run_source
    )

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

    # T047: Claim、Evidence 与 Wording 影响源——运行链路上派生图内的
    # Claim、Evidence 与措辞约束需要重验证。
    def _pack_runs_for(pack_id: str, version: str) -> list[RunProjection]:
        return workflow_service.find_runs_using_pack(pack_id, version)

    def _pack_graphs_for(pack_id: str, version: str) -> list[Any]:
        runs = _pack_runs_for(pack_id, version)
        return claim_evidence_service.find_graphs_by_run_ids(
            [projection.run_id for projection in runs]
        )

    def _pack_claim_source(pack_id: str, version: str) -> list[PackImpactItem]:
        return [
            PackImpactItem(
                item_id=f"claim:{graph.graph_id}",
                category=PackImpactCategory.CLAIM,
                ref_id=graph.graph_id,
                label=f"Claim 图 {graph.graph_id}（{len(graph.claims)} 条 Claim）",
                action=PackImpactAction.REVALIDATE,
                account_id=graph.account_id,
                project_id=graph.project_id,
                details={"graph_status": graph.status.value},
            )
            for graph in _pack_graphs_for(pack_id, version)
        ]

    def _pack_evidence_source(pack_id: str, version: str) -> list[PackImpactItem]:
        return [
            PackImpactItem(
                item_id=f"evidence:{evidence.evidence_id}",
                category=PackImpactCategory.EVIDENCE,
                ref_id=evidence.evidence_id,
                label=f"Evidence {evidence.evidence_id}（{evidence.relation.value}）",
                action=PackImpactAction.REVALIDATE,
                account_id=graph.account_id,
                project_id=graph.project_id,
            )
            for graph in _pack_graphs_for(pack_id, version)
            for evidence in graph.evidence
        ]

    def _pack_wording_source(pack_id: str, version: str) -> list[PackImpactItem]:
        return [
            PackImpactItem(
                item_id=f"wording:{graph.graph_id}",
                category=PackImpactCategory.WORDING,
                ref_id=graph.graph_id,
                label=f"措辞约束 {graph.graph_id}（校验报告需重验证）",
                action=PackImpactAction.REVALIDATE,
                account_id=graph.account_id,
                project_id=graph.project_id,
            )
            for graph in _pack_graphs_for(pack_id, version)
        ]

    def _pack_project_source(pack_id: str, version: str) -> list[PackImpactItem]:
        projects_seen: dict[str, str] = {}
        for projection in _pack_runs_for(pack_id, version):
            projects_seen[projection.project_id] = projection.context_envelope.account_id
        return [
            PackImpactItem(
                item_id=f"project:{project_id}",
                category=PackImpactCategory.PROJECT,
                ref_id=project_id,
                label=f"项目 {project_id}",
                action=PackImpactAction.PRESERVE_AND_MARK,
                account_id=account_id,
                project_id=project_id,
            )
            for project_id, account_id in projects_seen.items()
        ]

    def _pack_user_action_source(pack_id: str, version: str) -> list[PackImpactItem]:
        return [
            PackImpactItem(
                item_id=f"user_action:{projection.context_envelope.account_id}:{projection.run_id}",
                category=PackImpactCategory.USER_ACTION,
                ref_id=projection.run_id,
                label=(
                    f"用户动作：{projection.context_envelope.account_id} 提交了运行 "
                    f"{projection.run_id}（{projection.project_id}）"
                ),
                action=PackImpactAction.PRESERVE_AND_MARK,
                account_id=projection.context_envelope.account_id,
                project_id=projection.project_id,
                details={"run_status": projection.run_status.value},
            )
            for projection in _pack_runs_for(pack_id, version)
        ]

    for category, source in (
        (PackImpactCategory.CLAIM, _pack_claim_source),
        (PackImpactCategory.EVIDENCE, _pack_evidence_source),
        (PackImpactCategory.WORDING, _pack_wording_source),
        (PackImpactCategory.PROJECT, _pack_project_source),
        (PackImpactCategory.USER_ACTION, _pack_user_action_source),
    ):
        domain_pack_lifecycle.register_pack_impact_source(category, source)

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

    # T047: 产物影响源——运行链路派生的表达产物需要重验证。
    def _pack_artifact_source(pack_id: str, version: str) -> list[PackImpactItem]:
        runs = _pack_runs_for(pack_id, version)
        return [
            PackImpactItem(
                item_id=f"artifact:{draft.draft_id}",
                category=PackImpactCategory.ARTIFACT,
                ref_id=draft.draft_id,
                label=f"表达产物 {draft.draft_id}（{draft.genre.value}）",
                action=PackImpactAction.REVALIDATE,
                account_id=draft.account_id,
                project_id=draft.project_id,
                details={"artifact_trust_status": draft.artifact_trust_status.value},
            )
            for draft in expression_service.find_drafts_by_run_ids(
                [projection.run_id for projection in runs]
            )
        ]

    domain_pack_lifecycle.register_pack_impact_source(
        PackImpactCategory.ARTIFACT, _pack_artifact_source
    )

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
    app.include_router(chat.router)
    app.include_router(credentials.router)
    app.include_router(domain_packs.router)
    app.include_router(projects.router)
    app.include_router(vault.router)
    app.include_router(sharing.router)
    app.include_router(sync.router)
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
        return app_health_projection()

    @app.get("/health/degraded", response_model=HealthProjection)
    async def health_degraded() -> HealthProjection:
        """Degraded probe: optional dependencies and degradation capability."""
        return app_health_projection()

    @app.get("/health", response_model=HealthProjection)
    async def health_summary() -> HealthProjection:
        """Combined health summary."""
        return app_health_projection()

    @app.get("/me", response_model=dict[str, Any])
    async def me(subject: auth.SubjectDep) -> dict[str, Any]:
        """Protected demo route: returns the resolved subject.

        T003 uses this route to prove that API endpoints share the same
        authentication guard as the Web frontend.
        """
        return {"account_id": subject.account_id, "session_id": subject.session_id}

    @app.get("/_test/recovery-token", response_model=dict[str, Any])
    async def test_recovery_token(qq_email: str) -> dict[str, Any]:
        """Test-only endpoint to retrieve a recovery token without email delivery.

        This endpoint is prefixed with `/_test/` and is only safe because the
        identity service is local. It must not be exposed in production.
        """
        from bridges.identity import IdentityError

        service: IdentityService = app.state.identity_service
        try:
            token = service.test_create_recovery_token(qq_email)
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
