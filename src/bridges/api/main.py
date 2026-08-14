"""FastAPI application for the BridGes API."""

import hashlib
import logging
import os
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Annotated, Any, cast

logger = logging.getLogger(__name__)

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
    QwenImageAdapter,
    QwenOcrAdapter,
    QwenStructuredOutputAdapter,
    QwenTextChatAdapter,
    QwenTtsAdapter,
    QwenVisionAdapter,
    QwenWanAdapter,
    StubQwenAdapter,
)
from bridges.ai.fixed_models import ASR_MODEL_ID, CHAT_MODEL_ID, TTS_MODEL_ID
from bridges.api import (
    auth,
    chat,
    compatibility,
    domain_packs,
    evaluation,
    expression,
    ingestion,
    institution,
    knowledge_base,
    learning_project_migration,
    learning_projects,
    projects,
    science,
    scope,
    search,
    sharing,
    skills,
    sync,
    vault,
    workflows,
)
from bridges.api.csrf import CsrfOriginMiddleware
from bridges.api.data import router as data_router
from bridges.api.image import router as image_router
from bridges.api.mcp import router as mcp_router
from bridges.api.media import router as media_router
from bridges.api.plugins import router as plugins_router
from bridges.api.reminder import router as reminder_router
from bridges.api.speech import router as speech_router
from bridges.api.video import router as video_router
from bridges.arxiv_mcp.service import ArxivSearchService
from bridges.career.service import CareerPlannerService
from bridges.chat import (
    AttachmentRepository,
    ChatAttachmentService,
    ChatService,
    ConversationRepository,
)
from bridges.chat.routing import NaturalLanguageImageRouter
from bridges.chat.run_executor import GenerationRunExecutor
from bridges.chat.selections import ChatSelectionsService
from bridges.closeout.fixtures import (
    CloseoutArxivClient,
    CloseoutQwenAdapter,
    CloseoutWebSearchClient,
)
from bridges.config import Settings, get_settings
from bridges.contracts.ai import (
    CapabilityKind,
    CapabilityRecord,
    CapabilityStatus,
    RetryPolicy,
    StructuredOutputFormat,
)
from bridges.contracts.domain import (
    PackImpactAction,
    PackImpactCategory,
    PackImpactItem,
)
from bridges.contracts.health import DependencyHealth, HealthProjection, HealthStatus
from bridges.contracts.projects import ObjectRef
from bridges.contracts.workflows import RunProjection, WorkflowRunStatus
from bridges.credentials.global_credential import (
    GLOBAL_QWEN_KEY_GUIDANCE,
    is_global_qwen_key_configured,
)
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
from bridges.image.service import ImageService
from bridges.ingestion.embedding import QwenEmbeddingPort
from bridges.ingestion.ocr import QwenOcrPort
from bridges.ingestion.service import IngestionService
from bridges.institution import InstitutionService
from bridges.invalidation import AffectedDownstream, InvalidationService
from bridges.knowledge_base import KnowledgeBaseService
from bridges.learning import (
    InMemoryLearningRepository,
    LearningPathService,
    LearningService,
    TeachingProgressService,
    TeachingService,
)
from bridges.learning.api import router as learning_router
from bridges.learning_projects import LearningProjectService
from bridges.learning_projects.migration import ProjectMigrationService
from bridges.lifecycle.backup import BackupService
from bridges.lifecycle.deletion import DeletionService
from bridges.lifecycle.exports import ExportService
from bridges.mcp.runtime import McpRuntime
from bridges.mcp.service import McpService
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
from bridges.plugins.service import PluginService
from bridges.profiles import (
    AutomaticProfileService,
    FourDimensionContractGate,
    FourDimensionProfileService,
    GatewayAutomaticProfileExtractor,
    InMemoryAutomaticProfileRepository,
    InMemoryFourDimensionProfileRepository,
    InMemoryProfileRepository,
    ProfileService,
    RuleBasedAutomaticProfileExtractor,
    SqliteAutomaticProfileRepository,
    SqliteFourDimensionProfileRepository,
)
from bridges.profiles.api import router as profiles_router
from bridges.profiles.legacy_api import router as legacy_profiles_router
from bridges.profiles.sqlite_repository import SqliteProfileRepository
from bridges.projects import ProjectService
from bridges.retirement import CompatibilityMetrics, retire_user_extensions, run_reminder_retirement
from bridges.retrieval.service import LayeredRetrievalService
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
from bridges.search import SearchService
from bridges.sharing import SharingService
from bridges.skills import create_builtin_registry
from bridges.skills.humanizer.service import HumanizerService
from bridges.speech.service import SpeechService
from bridges.storage import (
    SCHEMA_INTEGRITY_ERROR_CODE,
    SCHEMA_VERSION,
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
from bridges.video.service import VideoService
from bridges.web_search.repository import WebSearchCacheRepository
from bridges.web_search.providers import build_fallback_provider
from bridges.web_search.service import WebSearchService
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
            model_id=CHAT_MODEL_ID,
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
    registry.register(
        CapabilityRecord(
            name="qwen_profile_extraction",
            version="1",
            kind=CapabilityKind.MODEL,
            vendor="qwen",
            region="cn-beijing",
            model_id="qwen3.6-flash",
            input_schema_version="profile-message-v1",
            output_schema_version="profile-extraction-v2",
            structured_output_format=StructuredOutputFormat.JSON_OBJECT,
            status=CapabilityStatus.VERIFIED,
            retry_policy=RetryPolicy(max_attempts=1, backoff_seconds=0),
            prompt_version="2026-08-12",
            validation_probe_version="profile-json-object-v2",
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
            model_id=ASR_MODEL_ID,
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
            model_id=TTS_MODEL_ID,
            input_schema_version="tts-text-v1",
            output_schema_version="tts-audio-v1",
            supported_modalities=["text", "audio"],
            status=CapabilityStatus.VERIFIED,
            retry_policy=RetryPolicy(max_attempts=2, backoff_seconds=1.0),
            prompt_version="2026-07-24",
        )
    )
    # Issue 31: 图片生成与编辑（ADR-0009 固定绑定 qwen-image-2.0-pro-
    # 2026-06-22）。异步任务经后台执行器轮询，不注册备用模型——失败
    # 只重试同一绑定。
    registry.register(
        CapabilityRecord(
            name="qwen_image",
            version="1",
            kind=CapabilityKind.MODEL,
            vendor="qwen",
            region="cn-beijing",
            model_id="qwen-image-2.0-pro-2026-06-22",
            input_schema_version="image-prompt-v1",
            output_schema_version="image-task-v1",
            supported_modalities=["text", "image"],
            status=CapabilityStatus.VERIFIED,
            retry_policy=RetryPolicy(max_attempts=2, backoff_seconds=1.0),
            prompt_version="2026-08-05",
        )
    )
    # Issue 32: 文生视频（ADR-0007：Wan 是模型矩阵唯一非 Qwen 系列例外，
    # 使用全局百炼运行凭据）。固定绑定 wan2.7-t2v-2026-06-12，异步
    # 任务经后台执行器轮询，不注册备用模型——失败只重试同一绑定。
    registry.register(
        CapabilityRecord(
            name="qwen_wan",
            version="1",
            kind=CapabilityKind.MODEL,
            vendor="wan",
            region="cn-beijing",
            model_id="wan2.7-t2v-2026-06-12",
            input_schema_version="video-prompt-v1",
            output_schema_version="video-task-v1",
            supported_modalities=["text", "video"],
            status=CapabilityStatus.VERIFIED,
            retry_policy=RetryPolicy(max_attempts=2, backoff_seconds=1.0),
            prompt_version="2026-08-05",
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


def _has_real_qwen_key(settings: Settings | None) -> bool:
    """是否配置了非空全局百炼运行凭据（BRIDGES_QWEN_API_KEY/_FILE）。

    GQ-01：全局凭据是正式运行唯一的 Qwen 认证来源，真实适配器注册与模型
    网关接线都以本判定为准——无真实凭据时能力保持未绑定，绝不注册 Stub
    或假成功；启动硬门（CLI 与健康检查）保证正式运行不会缺 Key 半启动。
    账户级密钥已随 GQ-06/GQ-07 整体清退，不再驱动任何能力判定。
    """
    return settings is not None and is_global_qwen_key_configured(settings)


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
    app.state.compatibility_metrics = CompatibilityMetrics(state_store)
    app.state.persistence_mode = (
        "error"
        if app.state.persistence_error
        else "sqlite"
        if state_store is not None
        else "memory"
    )

    # GQ-01：全局百炼运行凭据是正式运行的必需配置。development/production
    # 缺少 Key 时设置不含秘密的错误标记，就绪检查报告 FAIL——即使绕过
    # ``BridGes start`` 直接启动 API，也不会出现 "ready=pass、模型不可用"
    # 的半启动实例。test 环境由确定性适配器驱动，豁免此门。
    app.state.qwen_key_error = None
    app.state.profile_capability_canary_error = None
    settings_for_qwen_gate = app.state.settings
    if (
        settings_for_qwen_gate is not None
        and settings_for_qwen_gate.environment.lower() != "test"
        and not is_global_qwen_key_configured(settings_for_qwen_gate)
    ):
        app.state.qwen_key_error = GLOBAL_QWEN_KEY_GUIDANCE

    # Issue 05/06: 配置数据库时以同一数据目录初始化版本化 bridges.db 与账户隔离
    # 加密对象库（对象目录为数据库同目录下的 objects/）。首次启动事务化创建
    # 带版本记录的 bridges.db；失败与持久化错误同样进入 503 拒绝路径，绝不
    # 静默降级。对象加密密钥派生自 BRIDGES_SECRET_KEY，任何位置不落盘密钥。
    # Issue 06: 构造任何会话 repository 前必须完成 initialize() 并校验 schema
    # 版本与核心契约表/索引；失败关闭并报告 database_schema_integrity。
    app.state.bridges_database = None
    app.state.object_repository = None
    app.state.database_path_fingerprint = None
    if (
        isinstance(state_store, SqliteStateStore)
        and state_store.path != ":memory:"
        and app.state.persistence_error is None
    ):
        settings = app.state.settings
        if settings is not None and settings.secret_key is not None:
            secret_value = settings.secret_key.get_secret_value()
            if secret_value:
                database_path = Path(state_store.path)
                run_id = os.environ.get("BRIDGES_RUN_ID", "")
                path_fingerprint = (
                    hashlib.sha256(str(database_path).encode("utf-8")).hexdigest()[:8]
                )
                app.state.database_path_fingerprint = path_fingerprint
                try:
                    database = BridgesDatabase(database_path)
                    logger.info(
                        "bridges_database_migration_start",
                        extra={
                            "run_id": run_id,
                            "path_fingerprint": path_fingerprint,
                            "expected_schema_version": SCHEMA_VERSION,
                        },
                    )
                    migrate_start = time.monotonic()
                    initialized_version = database.initialize()
                    migrate_duration_ms = int(
                        (time.monotonic() - migrate_start) * 1000
                    )
                    # Issue 06 启动契约：调用方校验返回版本等于当前支持版本，
                    # 不能只依赖 initialize() 内部路径（纵深防御）。
                    if initialized_version != SCHEMA_VERSION:
                        raise StorageError(
                            "数据库 schema 版本校验失败："
                            f"initialize 返回 {initialized_version}，"
                            f"当前程序支持 {SCHEMA_VERSION}。"
                        )
                    logger.info(
                        "bridges_database_initialized",
                        extra={
                            "run_id": run_id,
                            "path_fingerprint": path_fingerprint,
                            "schema_version": initialized_version,
                            "expected_schema_version": SCHEMA_VERSION,
                            "schema_ready": database.schema_ready,
                            "migrate_duration_ms": migrate_duration_ms,
                        },
                    )
                    app.state.bridges_database = database
                    object_store = EncryptedFileObjectStore(
                        database_path.parent / "objects",
                        encryption_key=settings.secret_key,
                    )
                    app.state.object_store = object_store
                    app.state.object_repository = BridgesObjectRepository(
                        database,
                        object_store,
                    )
                except StorageError as exc:
                    logger.error(
                        "bridges_database_initialization_failed",
                        extra={
                            "run_id": run_id,
                            "path_fingerprint": path_fingerprint,
                            "error_code": SCHEMA_INTEGRITY_ERROR_CODE
                            if SCHEMA_INTEGRITY_ERROR_CODE in str(exc)
                            else "database_initialization_failed",
                        },
                    )
                    app.state.persistence_error = str(exc)

    # Issue 39 AC2：CSRF 来源校验（在所有业务路由之前、持久化拒绝之后执行）。
    # 只校验改变状态的请求；未显式配置允许来源时按 X-Forwarded-* / Host /
    # 环回规则推导（见 api/csrf.py），本地开发无需配置即可工作。
    allowed_origins: list[str] = []
    if app.state.settings is not None:
        allowed_origins = list(app.state.settings.allowed_origins)
    app.add_middleware(CsrfOriginMiddleware, allowed_origins=allowed_origins)

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
        projection.extensions["run_id"] = os.environ.get("BRIDGES_RUN_ID", "")
        projection.extensions["database_path_fingerprint"] = getattr(
            app.state, "database_path_fingerprint", None
        )
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
        # GQ-01：正式环境缺少全局百炼凭据时就绪检查必须 FAIL（纵深防御，
        # 正常路径由 CLI 启动硬门在进程启动前拦截；此处兜底直接 uvicorn
        # 启动的实例，避免 "ready=pass、模型不可用"）。
        qwen_key_error = getattr(app.state, "qwen_key_error", None)
        if qwen_key_error:
            projection.dependencies.append(
                DependencyHealth(
                    name="qwen_global_key",
                    status=HealthStatus.FAIL,
                    required=True,
                    message=qwen_key_error,
                )
            )
            projection.ready = HealthStatus.FAIL
        settings = getattr(app.state, "settings", None)
        if settings is None or settings.environment.lower() != "test":
            profile_capability_error = getattr(
                getattr(app.state, "automatic_profile_service", None),
                "capability_degraded_reason",
                None,
            )
            profile_capability_error = profile_capability_error or getattr(
                app.state, "profile_capability_canary_error", None
            )
            capability_registry = getattr(app.state, "capability_registry", None)
            model_gateway = getattr(app.state, "model_gateway", None)
            if capability_registry is None or model_gateway is None:
                profile_capability_error = profile_capability_error or "not_bound"
            else:
                try:
                    profile_capability = capability_registry.get(
                        "qwen_profile_extraction", "1"
                    )
                    if profile_capability.status != CapabilityStatus.VERIFIED:
                        profile_capability_error = profile_capability_error or "not_verified"
                    elif not profile_capability.validation_probe_version:
                        profile_capability_error = profile_capability_error or "canary_not_configured"
                    elif not model_gateway.is_adapter_registered(
                        "qwen_profile_extraction", "1"
                    ):
                        profile_capability_error = profile_capability_error or "not_bound"
                except Exception:  # noqa: BLE001 - 健康检查必须继续返回结构化结果
                    profile_capability_error = profile_capability_error or "not_registered"
            if profile_capability_error:
                projection.dependencies.append(
                    DependencyHealth(
                        name="profile_extraction",
                        status=HealthStatus.FAIL,
                        required=False,
                        message="画像整理能力当前不可用。",
                    )
                )
                projection.degraded = HealthStatus.FAIL
        # Issue 05/06: 配置了版本化数据库时，把 bridges.db schema 就绪状态
        # 作为必需依赖上报；schema 版本不正确或核心表/索引缺失时服务必须
        # FAIL，阻止 Playwright 在数据库未就绪时开始测试。
        # extensions 只暴露脱敏遥测（版本号、对象名清单），绝不返回表内容
        # 或任何账户数据；E2E 契约测试据此证明后端使用了本 run 的数据库。
        bridges_database = getattr(app.state, "bridges_database", None)
        if bridges_database is not None:
            schema_version = 0
            schema_ready = False
            schema_ready_message: str | None = None
            schema_tables: list[str] = []
            try:
                schema_version = bridges_database.schema_version
                schema_ready = bridges_database.schema_ready
                schema_tables = bridges_database.schema_table_names()
                if not schema_ready:
                    # 拿到具体缺失对象信息，避免「version 已等于 expected 却
                    # 报未就绪」的自相矛盾消息（verify 会抛稳定错误码）。
                    bridges_database.verify_schema_integrity()
            except StorageError as exc:
                schema_ready = False
                schema_ready_message = str(exc)
            if not schema_ready and schema_ready_message is None:
                schema_ready_message = (
                    f"数据库 schema 未就绪："
                    f"version={schema_version}，"
                    f"expected={SCHEMA_VERSION}。"
                )
            projection.extensions["database_schema_version"] = schema_version
            projection.extensions["expected_database_schema_version"] = SCHEMA_VERSION
            projection.extensions["database_schema_tables"] = schema_tables
            projection.dependencies.append(
                DependencyHealth(
                    name="database_schema_ready",
                    status=(
                        HealthStatus.PASS if schema_ready else HealthStatus.FAIL
                    ),
                    required=True,
                    message=schema_ready_message,
                )
            )
            if not schema_ready:
                projection.ready = HealthStatus.FAIL
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
    # cycle without changing the enforcer's public interface. Issue 44: shared
    # project membership is also bound here so every service asks the enforcer
    # the same "who may access this project" question.
    app.state.scope_enforcer.set_institution_providers(
        institution_membership_provider=app.state.institution_service.get_membership,
        project_tenant_provider=app.state.sharing_service.get_project_institution_id,
        shared_project_membership_provider=app.state.sharing_service.is_member,
    )

    # T010: attach the observability service early so downstream services can
    # emit privacy-preserving audit events.
    app.state.observability_service = ObservabilityService()
    # T021-T024: 学习域服务（纯内存，无数据库依赖）在聊天服务之前挂载——
    # 生涯规划（Issue 29）需要按账户作用域读取学习使命/知识状态/学习记录。
    learning_repository = InMemoryLearningRepository()
    app.state.learning_service = LearningService(repository=learning_repository)
    app.state.teaching_service = TeachingService(repository=learning_repository)
    app.state.learning_path_service = LearningPathService(
        repository=learning_repository,
    )
    # Issue 21/02：DuckDuckGo 是默认主用；结构化备用源只有在部署配置显式
    # 启用且通过注册表合同后才实例化。凭据只在此处注入客户端，不进入聊天
    # 投影、审计、日志或前端。
    web_search_database = getattr(app.state, "bridges_database", None)
    use_closeout_fixtures = bool(
        app.state.settings is not None
        and app.state.settings.environment.lower() == "test"
        and app.state.settings.closeout_fixture_mode
    )
    fallback_provider = (
        build_fallback_provider(app.state.settings)
        if app.state.settings is not None
        else None
    )
    app.state.web_search_service = WebSearchService(
        client=CloseoutWebSearchClient() if use_closeout_fixtures else None,
        observability=app.state.observability_service,
        fallback_client=fallback_provider,
        cache=(
            WebSearchCacheRepository(web_search_database)
            if web_search_database is not None
            else None
        ),
    )
    # Issue 22：固定版本、只读、受限的 arXiv MCP；只接收本地脱敏后的
    # public_query_terms，不继承账户凭据或画像上下文。
    app.state.arxiv_search_service = ArxivSearchService(
        client=CloseoutArxivClient() if use_closeout_fixtures else None,
        observability=app.state.observability_service
    )
    app.router.add_event_handler("shutdown", app.state.arxiv_search_service.close)

    def _shutdown_mcp_servers() -> None:
        service: McpService | None = getattr(app.state, "mcp_service", None)
        if service is not None:
            service.shutdown()

    app.router.add_event_handler("shutdown", _shutdown_mcp_servers)

    # Issue 33: QQ SMTP 授权码使用独立命名空间（smtp）的凭据存储（GQ-07
    # 后是凭据存储唯一用途，账户 Qwen 命名空间已整体清退）；授权码绝不
    # 进入 SQLite、日志、模型或导出。源码环境使用操作系统凭据库（keyring，
    # Windows 兜底 DPAPI）；容器环境使用自动生成主密钥保护的加密凭据卷
    # （数据目录 credentials/ 子目录）。未配置数据库（内存模式，测试/E2E）
    # 时使用进程内替身，保证测试确定性。
    settings_at_credential = app.state.settings
    data_dir: Path | None = None
    if isinstance(state_store, SqliteStateStore) and state_store.path != ":memory:":
        data_dir = Path(state_store.path).parent
    smtp_credential_store: CredentialStorePort = InMemoryCredentialStore(
        namespace="smtp"
    )
    if settings_at_credential is not None and data_dir is not None:
        if settings_at_credential.credential_backend == "encrypted-volume":
            smtp_credential_store = EncryptedVolumeCredentialStore(
                data_dir, namespace="smtp"
            )
        else:
            smtp_credential_store = OsCredentialStore(
                data_dir=data_dir, namespace="smtp"
            )
    app.state.smtp_credential_store = smtp_credential_store

    # Issue 03：退役前先按账户幂等停止遗留提醒、结束 SMTP 验证并清除
    # 专用授权码。报告只保存数量和状态；空环境也写入零迁移结果。
    if app.state.bridges_database is not None:
        app.state.reminder_retirement = run_reminder_retirement(
            database=app.state.bridges_database,
            credential_store=smtp_credential_store,
            state_store=state_store,
        )

    # T018/T020: attach the profile service. Candidate profiles cannot be
    # treated as stable facts until the user accepts them through the human
    # decision loop; accepted candidates are promoted to active assertions. At
    # T020 the service also freezes, deletes, rolls back and exports assertions
    # while propagating invalidations to slices, cache, index and runs.
    # Issue 26: 配置持久化数据目录时改用 SQLite 仓库，画像、许可与通知在
    # 重启后仍可追溯；未配置（内存模式，仅测试/E2E）时保持进程内仓库。
    profile_database = getattr(app.state, "bridges_database", None)
    profile_repository = (
        SqliteProfileRepository(profile_database)
        if profile_database is not None
        else InMemoryProfileRepository()
    )
    app.state.profile_service = ProfileService(
        repository=profile_repository,
        scope_enforcer=app.state.scope_enforcer,
        invalidation_service=invalidation_service,
        observability_service=app.state.observability_service,
    )
    app.state.teaching_progress_service = (
        TeachingProgressService(profile_database) if profile_database is not None else None
    )

    def _adjust_learning_after_profile_change(
        account_id: str,
        trigger: Any,
        trigger_key: str,
        reason: str,
    ) -> None:
        if app.state.teaching_progress_service is not None:
            app.state.teaching_progress_service.apply_profile_event(
                account_id, trigger, trigger_key, reason
            )

    four_dimension_repository = (
        SqliteFourDimensionProfileRepository(profile_database)
        if profile_database is not None
        else InMemoryFourDimensionProfileRepository()
    )
    app.state.four_dimension_profile_service = FourDimensionProfileService(
        source_repository=profile_repository,
        repository=four_dimension_repository,
        learning_adjustment_callback=_adjust_learning_after_profile_change,
    )
    app.state.four_dimension_contract_gate = FourDimensionContractGate(
        app.state.four_dimension_profile_service
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
    if _has_real_qwen_key(settings):
        cassette_store = None
        # Issue 39 AC5：cassette 会把完整请求/响应正文以明文 JSON 落盘，
        # 生产环境强制禁止录制，避免私人对话正文落盘泄露。
        cassette_record_mode = (
            settings.qwen_record_cassettes
            and settings.environment.lower() != "production"
        )
        if settings.qwen_cassette_dir is not None:
            cassette_store = CassetteStore(Path(settings.qwen_cassette_dir))
        qwen_client = QwenApiClient(
            api_key=settings.qwen_api_key,
            workspace_id=settings.qwen_workspace_id,
            region=settings.qwen_region,
            cassette_store=cassette_store,
            record_mode=cassette_record_mode,
        )
        model_gateway.register_adapter(
            "qwen_text_chat", "1", QwenTextChatAdapter(qwen_client)
        )
        model_gateway.register_adapter(
            "qwen_structured_output", "1", QwenStructuredOutputAdapter(qwen_client)
        )
        model_gateway.register_adapter(
            "qwen_profile_extraction", "1", QwenStructuredOutputAdapter(qwen_client)
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
        # Issue 31: 图片生成与编辑异步任务适配器（submit/poll/fetch/cancel）。
        image_adapter = QwenImageAdapter(qwen_client)
        model_gateway.register_adapter("qwen_image", "1", image_adapter)
        # Issue 32: 文生视频异步任务适配器（Wan 例外，submit/poll/fetch/cancel）。
        wan_adapter = QwenWanAdapter(qwen_client)
        model_gateway.register_adapter("qwen_wan", "1", wan_adapter)

    # Issue 41（AC3）：StubQwenAdapter 只注册在显式 test 环境（本地与 CI
    # 测试确定性，与 /_test/* 端点同一门控）——development/production
    # 配置绝不注册任何 Stub：真实模型能力未配置全局凭据时保持未绑定，由
    # 网关返回明确的"未绑定适配器"阻塞结果；内置 deterministic 工具能力
    # （领域包校验器）由领域包运行时直接执行。qwen_force_stub 环境开关
    # 已随 Issue 41 移除，任何环境都无法通过配置项开启生产假成功。
    if settings is not None and settings.environment.lower() == "test":
        stub_adapter = (
            CloseoutQwenAdapter()
            if settings.closeout_fixture_mode
            else StubQwenAdapter()
        )
        for capability in capability_registry.list_active():
            if not model_gateway.is_adapter_registered(capability.name, capability.version):
                model_gateway.register_adapter(
                    capability.name, capability.version, stub_adapter
                )
    app.state.capability_registry = capability_registry
    app.state.model_gateway = model_gateway

    # Issue 15：消息持久化后、生成前执行默认画像预处理；测试环境使用确定性抽取器，
    # 其他环境使用固定版本的结构化画像能力，失败时由同一执行器驱动持久重试。
    automatic_profile_repository = (
        SqliteAutomaticProfileRepository(profile_database)
        if profile_database is not None
        else InMemoryAutomaticProfileRepository()
    )
    app.state.four_dimension_profile_service.set_observation_delete_callback(
        automatic_profile_repository.delete_observations_for_record
    )
    automatic_profile_extractor = (
        RuleBasedAutomaticProfileExtractor()
        if settings is not None and settings.environment.lower() == "test"
        else GatewayAutomaticProfileExtractor(model_gateway)
    )
    app.state.automatic_profile_service = AutomaticProfileService(
        four_dimension_service=app.state.four_dimension_profile_service,
        repository=automatic_profile_repository,
        extractor=automatic_profile_extractor,
        message_reader=(
            ConversationRepository(profile_database).get_message
            if profile_database is not None
            else None
        ),
        observability_service=app.state.observability_service,
    )

    # Issue 11: 持久化流式聊天纵向切片。对话/消息/运行锁写入 bridges.db；
    # 未配置持久化数据目录（内存模式，仅测试/E2E）时不挂载聊天服务，
    # 路由返回"对话存储未启用"，绝不静默降级到内存。
    bridges_database = getattr(app.state, "bridges_database", None)
    if bridges_database is not None:
        # Issue 28：内置只读 SKILL 注册表（humanizer 与插件中心共用同源）。
        skill_registry = create_builtin_registry()
        app.state.skill_registry = skill_registry
        object_repository = getattr(app.state, "object_repository", None)
        if object_repository is not None:
            # Issue 45：附件服务经 chat 域仓库访问数据库（chat_attachments
            # / chat_attachment_cancellations 属主 AttachmentRepository，
            # conversations 属主 ConversationRepository，objects 属主
            # BridgesObjectRepository）。
            app.state.chat_attachment_service = ChatAttachmentService(
                bridges_database,
                object_repository,
                attachment_repository=AttachmentRepository(bridges_database),
                conversation_repository=ConversationRepository(bridges_database),
            )
            # Issue 17: 文档摄取服务（API 进程只做入队/重试/投影，处理在后台
            # 执行器进程）。GQ-05：查询向量与 worker 摄取/重建共用同一全局
            # Embedding 端口；可用性由构造与调用结果决定，不再依赖账户探测。
            embedding_port = QwenEmbeddingPort(
                api_key=(settings.qwen_api_key if settings is not None else None),
                region=(
                    settings.qwen_region if settings is not None else "cn-beijing"
                ),
                workspace_id=(
                    settings.qwen_workspace_id if settings is not None else None
                ),
                cassette_dir=(
                    settings.qwen_cassette_dir if settings is not None else None
                ),
                # Issue 39 AC5 / GQ-04：生产环境强制禁止录制（与 QwenApiClient
                # 一致），API 与 worker 同一 cassette 策略。
                record_mode=(
                    settings.qwen_record_cassettes
                    and settings.environment.lower() != "production"
                    if settings is not None
                    else False
                ),
            )
            ocr_port = QwenOcrPort(
                api_key=(settings.qwen_api_key if settings is not None else None),
                region=(
                    settings.qwen_region if settings is not None else "cn-beijing"
                ),
                workspace_id=(
                    settings.qwen_workspace_id if settings is not None else None
                ),
                cassette_dir=(
                    settings.qwen_cassette_dir if settings is not None else None
                ),
                record_mode=(
                    settings.qwen_record_cassettes
                    and settings.environment.lower() != "production"
                    if settings is not None
                    else False
                ),
            )
            app.state.ingestion_service = IngestionService(
                database=bridges_database,
                object_repository=object_repository,
                embedding=embedding_port,
                ocr=ocr_port,
            )
            app.state.learning_project_migration_service = ProjectMigrationService(
                database=bridges_database,
                object_repository=object_repository,
                ingestion_service=app.state.ingestion_service,
            )
            # Issue 18: 全局本地知识库（材料不绑定对话，复用摄取状态机）。
            app.state.knowledge_base_service = KnowledgeBaseService(
                bridges_database,
                object_repository,
                app.state.ingestion_service,
                app.state.learning_project_migration_service,
            )
            # Issue 19: 文件夹式学习项目（对话归属 + 项目级文件，复用摄取状态机）。
            app.state.learning_project_service = LearningProjectService(
                bridges_database,
                object_repository,
                app.state.ingestion_service,
                ConversationRepository(bridges_database),
            )
            # Issue 20: 分层本地检索（API 进程只读检索 + 写入检索记录）。查询
            # 向量经唯一的全局百炼运行凭据调用固定 Embedding 模型（GQ-05）。
            app.state.retrieval_service = LayeredRetrievalService(
                database=bridges_database,
                embedding=embedding_port,
                # Issue 45：跨域读取经属主仓库构造注入（conversations/
                # chat_attachments 属 chat 域、objects 属 storage 域），
                # 检索服务不再直读非己表。
                conversation_repository=ConversationRepository(bridges_database),
                attachment_repository=AttachmentRepository(bridges_database),
                object_repository=object_repository,
            )
            # Issue 31: 图片生成与编辑（固定 qwen-image-2.0-pro-2026-06-22）。
            # API 进程只做提交/查询/取消/重试与资产管理；云端轮询在后台
            # 执行器进程内按租约执行，任务与消息投影原子落库。
            app.state.image_service = ImageService(
                database=bridges_database,
                gateway=model_gateway,
                object_repository=object_repository,
                chat_repository=ConversationRepository(bridges_database),
                observability_service=app.state.observability_service,
            )
            # Issue 32: 文生视频（固定 wan2.7-t2v-2026-06-12，ADR-0007
            # Wan 例外）。API 进程只做提交/查询/取消/重试与资产管理；云端
            # 轮询在后台执行器进程内按租约执行，任务与消息投影原子落库。
            app.state.video_service = VideoService(
                database=bridges_database,
                gateway=model_gateway,
                object_repository=object_repository,
                chat_repository=ConversationRepository(bridges_database),
                observability_service=app.state.observability_service,
            )
            # Issue 34：SKILL 插件中心。内置包随应用发布（humanizer 条目
            # 与 SKILL 注册表同源），用户包经安全闭锁后按账户安装到对象库
            # 与 skill_packages 表；启停/卸载/演示全部账户作用域并写审计。
            mcp_pid_dir = Path(cast(SqliteStateStore, state_store).path).parent / "mcp-pids"
            mcp_runtime = McpRuntime(pid_dir=mcp_pid_dir)
            retire_user_extensions(bridges_database, runtime=mcp_runtime)
            app.state.plugin_service = PluginService(
                database=bridges_database,
                object_repository=object_repository,
                observability_service=app.state.observability_service,
                skill_registry=skill_registry,
            )
            # Issue 35：显式授权 MCP 插件管理。安装描述按账户持久化（版本锁/
            # 来源/完整性哈希），进程在独立受限环境中惰性运行；pid 文件落数据
            # 目录用于重启后回收异常退出遗留的孤儿进程（AC3 重启恢复合法配置，
            # 不继承僵尸进程）；关闭时停止全部进程。
            # isinstance 收窄在联合类型上不保留：显式 cast。
            mcp_service = McpService(
                database=bridges_database,
                object_repository=object_repository,
                observability_service=app.state.observability_service,
                runtime=mcp_runtime,
                scope_enforcer=app.state.scope_enforcer,
            )
            # 应用启动：回收上次异常退出遗留的孤儿 MCP 进程（pid 文件兜底）。
            mcp_service.reap_orphans()
            app.state.mcp_service = mcp_service
        # Issue 24: 跨内容统一桌面搜索（只读实时 SQL，无进程内缓存）。
        app.state.search_service = SearchService(bridges_database)
        # Issue 28：内置只读 SKILL 注册表 + bridges-humanizer 编排服务。
        # SKILL 随应用发布、版本固定、只读来源；编排复用同一模型网关与
        # 附件/检索/联网证据合同，不依赖用户手工上传或 `.env`。
        app.state.humanizer_service = HumanizerService(
            registry=skill_registry,
            gateway=model_gateway,
            attachment_service=getattr(app.state, "chat_attachment_service", None),
            knowledge_base_service=getattr(app.state, "knowledge_base_service", None),
            retrieval_service=getattr(app.state, "retrieval_service", None),
            web_search_service=getattr(app.state, "web_search_service", None),
            observability_service=app.state.observability_service,
        )
        # Issue 29：生涯规划编排服务（复用画像切片编译、学习记录、检索与
        # 联网证据合同；六类输出由结构化模型生成并确定性复核）。
        app.state.career_planner_service = CareerPlannerService(
            gateway=model_gateway,
            profile_service=getattr(app.state, "profile_service", None),
            learning_service=app.state.learning_service,
            observability_service=app.state.observability_service,
        )
        # Issue 36：对话级插件选择域（校验/失效清洗/工具上下文编译）。
        # 可用集合来自插件中心（SKILL 已安装且启用）与 MCP 服务器（已
        # 安装且启用），选择随对话持久化；撤权动作从全部会话选择移除。
        app.state.chat_selections_service = ChatSelectionsService(
            repository=ConversationRepository(bridges_database),
            database=bridges_database,
            plugin_service=getattr(app.state, "plugin_service", None),
            mcp_service=getattr(app.state, "mcp_service", None),
        )
        app.state.chat_service = ChatService(
            repository=ConversationRepository(bridges_database),
            gateway=model_gateway,
            attachment_service=getattr(app.state, "chat_attachment_service", None),
            retrieval_service=getattr(app.state, "retrieval_service", None),
            web_search_service=getattr(app.state, "web_search_service", None),
            arxiv_search_service=getattr(app.state, "arxiv_search_service", None),
            profile_service=getattr(app.state, "profile_service", None),
            teaching_progress_service=getattr(
                app.state, "teaching_progress_service", None
            ),
            four_dimension_profile_service=getattr(
                app.state, "four_dimension_profile_service", None
            ),
            observability_service=app.state.observability_service,
            humanizer_service=app.state.humanizer_service,
            career_planner_service=app.state.career_planner_service,
            image_service=app.state.image_service,
            video_service=app.state.video_service,
            selections_service=app.state.chat_selections_service,
            mcp_service=getattr(app.state, "mcp_service", None),
            automatic_profile_service=app.state.automatic_profile_service,
            natural_language_router=NaturalLanguageImageRouter(bridges_database),
        )
        # Issue 02：持久化生成运行的后台执行器（ADR-0013）。API 进程内
        # 受监督线程按租约领取生成运行并执行——HTTP/SSE 只创建与订阅。
        # test 环境（确定性适配器驱动）不自动启动线程，由测试显式驱动
        # run_tick 或自行启动，避免与同步消费生成器的既有测试竞态。
        app.state.generation_executor = GenerationRunExecutor(
            app.state.chat_service,
            bridges_database,
            profile_extraction_service=app.state.automatic_profile_service,
        )
        app.state.generation_executor_stop = threading.Event()

        def _start_generation_executor() -> None:
            environment = (
                app.state.settings.environment
                if app.state.settings is not None
                else "production"
            )
            # test 环境（确定性适配器驱动）默认不自动启动执行器线程，由
            # pytest 显式驱动 run_tick 或自行启动线程，避免与同步消费生成
            # 器的既有测试竞态；e2e（真实部署形态）经环境变量显式启用。
            force_executor = (
                os.environ.get("BRIDGES_GENERATION_EXECUTOR", "").strip().lower()
                in {"1", "true", "yes"}
            )
            if environment.lower() == "test" and not force_executor:
                return
            thread = threading.Thread(
                target=app.state.generation_executor.run_loop,
                kwargs={
                    "stop": app.state.generation_executor_stop,
                    "emit": lambda line: None,  # noqa: E731 - 执行器心跳静默
                },
                name="generation-executor",
                daemon=True,
            )
            thread.start()
            app.state.generation_executor_thread = thread

        def _stop_generation_executor() -> None:
            app.state.generation_executor_stop.set()
            thread = getattr(app.state, "generation_executor_thread", None)
            if thread is not None:
                thread.join(timeout=5)

        app.router.add_event_handler("startup", _start_generation_executor)
        app.router.add_event_handler("shutdown", _stop_generation_executor)
        # Issue 30: 听写与单条回答朗读（固定 ASR/TTS 快照）。复用同一
        # 模型网关（固定模型标识进运行记录）、账户对象库（朗读音频按
        # 账户隔离留存）与对话仓库；听写音频不落盘，失败只重试同一快照。
        app.state.speech_service = SpeechService(
            gateway=model_gateway,
            object_repository=object_repository,
            chat_repository=ConversationRepository(bridges_database),
            observability_service=app.state.observability_service,
        )
    # Issue 37: 数据生命周期（导出/删除/备份/恢复）。导出只读业务表（不读
    # 凭据/会话）；删除与恢复会清除账户级外部凭据（QQ SMTP 授权码，恢复后
    # 需重新配置；账户 Qwen Key 已由 GQ-07 启动清退整体退役，不再参与）；
    # 备份在受控一致性点打包数据库快照、对象与身份账户数据，绝不包含凭据、
    # 会话令牌或运行密钥。全部敏感端点经 RecentAuthRequired 敏感门；未配置
    # 数据库（内存模式）时服务为 None，
    # 路由统一 503（与既有持久化服务一致）。
    bridges_database_for_lifecycle = getattr(app.state, "bridges_database", None)
    object_repository_for_lifecycle = getattr(app.state, "object_repository", None)
    object_store_for_lifecycle = getattr(app.state, "object_store", None)
    if (
        bridges_database_for_lifecycle is not None
        and object_repository_for_lifecycle is not None
        and object_store_for_lifecycle is not None
    ):
        app.state.export_service = ExportService(
            database=bridges_database_for_lifecycle,
            identity_service=app.state.identity_service,
            observability_service=app.state.observability_service,
        )
        app.state.deletion_service = DeletionService(
            database=bridges_database_for_lifecycle,
            object_repository=object_repository_for_lifecycle,
            identity_service=app.state.identity_service,
            smtp_credential_store=app.state.smtp_credential_store,
            observability_service=app.state.observability_service,
        )
        app.state.backup_service = BackupService(
            database=bridges_database_for_lifecycle,
            object_repository=object_repository_for_lifecycle,
            object_store=object_store_for_lifecycle,
            identity_service=app.state.identity_service,
            smtp_credential_store=app.state.smtp_credential_store,
            observability_service=app.state.observability_service,
            # 挂载条件保证 state_store 是 SqliteStateStore（bridges.db 与
            # 状态存储共用同一文件，恢复需重开其连接）。
            state_store=cast(SqliteStateStore, state_store),
            mcp_runtime=mcp_runtime,
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

    # T006/T009: attach the workflow service and register workflows。
    # Issue 43：运行状态落 SQLite（workflow_runs），进程重建后从磁盘
    # 恢复进行中的运行；未配置数据库时保持纯内存。
    workflow_service = WorkflowService(
        scope_enforcer=app.state.scope_enforcer,
        model_gateway=model_gateway,
        observability_service=app.state.observability_service,
        invalidation_service=invalidation_service,
        profile_service=app.state.profile_service,
        database=bridges_database,
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
            # Issue 41（AC3）：只有真实环境密钥已配置时才接模型网关——
            # 无密钥时能力明确停用，媒体/科学提取走确定性提取器，绝不
            # 用 Stub 适配器产出假模型结果。
            model_gateway if _has_real_qwen_key(settings) else None
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
        scope_enforcer=app.state.scope_enforcer,
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
            # 与 ScienceSourceService 同一合同（Issue 41 AC3）：无真实密钥
            # 时不接模型网关，提取走确定性提取器。
            model_gateway if _has_real_qwen_key(settings) else None
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
    if _has_real_qwen_key(settings):
        # Issue 41（AC3）：只有真实 TTS 适配器（真实环境密钥）才挂接旁白
        # 合成，测试环境的 Stub 适配器不驱动合成——无真实密钥时走确定性
        # 旁白路径。
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

    app.include_router(auth.router)
    app.include_router(compatibility.router)
    app.include_router(chat.router)
    app.include_router(ingestion.router)
    app.include_router(knowledge_base.router)
    app.include_router(learning_project_migration.router)
    app.include_router(learning_projects.router)
    app.include_router(search.router)
    app.include_router(domain_packs.router)
    app.include_router(projects.router)
    app.include_router(vault.router)
    app.include_router(sharing.router)
    app.include_router(sync.router)
    app.include_router(institution.router)
    app.include_router(profiles_router)
    app.include_router(legacy_profiles_router)
    app.include_router(workflows.router)
    app.include_router(scope.router)
    app.include_router(evaluation.router)
    app.include_router(science.router)
    app.include_router(expression.router)
    app.include_router(media_router)
    app.include_router(learning_router)
    app.include_router(speech_router)
    app.include_router(image_router)
    app.include_router(video_router)
    app.include_router(reminder_router)
    app.include_router(plugins_router)
    app.include_router(mcp_router)
    app.include_router(skills.router)
    app.include_router(data_router)

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
        """Readiness probe: required dependencies are healthy.

        Issue 06：未就绪（含数据库 schema 未达当前版本或核心对象缺失）时
        返回 HTTP 503 而非 200——Playwright 的 webServer URL 轮询只认状态码，
        只有 schema-ready 后才放行用户测试；初始化失败时启动明确失败而不是
        在首个会话请求中报 500。响应体始终是同一份 HealthProjection。
        """
        projection = app_health_projection()
        if projection.ready != HealthStatus.PASS:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content=projection.model_dump(mode="json"),
            )
        return projection

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

    if settings is not None and settings.environment.lower() == "test":
        # Issue 41（AC3/AC10）：测试专用端点只在 test 环境注册，生产配置不
        # 暴露任何 `/_test/` 路由。
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

    if settings is not None and settings.environment.lower() == "test":
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
