"""构建后台图片与视频任务服务。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from bridges.ai import (
    CapabilityRegistry,
    CassetteStore,
    ModelGateway,
    QwenApiClient,
    QwenImageAdapter,
    QwenVisionAdapter,
    QwenWanAdapter,
)
from bridges.ai.fixed_models import IMAGE_MODEL_ID, VIDEO_MODEL_ID, VISION_MODEL_ID
from bridges.ai.run_model_config import RunModelConfigProvider
from bridges.chat.repository import ConversationRepository
from bridges.config import Settings
from bridges.contracts.ai import (
    CapabilityKind,
    CapabilityRecord,
    CapabilityStatus,
    RetryPolicy,
)
from bridges.image.service import ImageService
from bridges.observability.service import ObservabilityService
from bridges.storage import BridgesDatabase, BridgesObjectRepository
from bridges.video.service import VideoService


def build_image_service(
    settings: Settings,
    database: BridgesDatabase,
    repository: BridgesObjectRepository,
    model_config_provider: RunModelConfigProvider,
    *,
    client_factory: Callable[..., QwenApiClient] = QwenApiClient,
) -> ImageService:
    """用后台执行器当前凭据构造图片生成与视觉替代文本能力。"""
    client = _qwen_client(settings, client_factory)
    registry = CapabilityRegistry()
    registry.register(
        CapabilityRecord(
            name="qwen_image",
            version="1",
            kind=CapabilityKind.MODEL,
            vendor="qwen",
            region="cn-beijing",
            model_id=IMAGE_MODEL_ID,
            input_schema_version="image-prompt-v1",
            output_schema_version="image-task-v1",
            status=CapabilityStatus.VERIFIED,
            retry_policy=RetryPolicy(max_attempts=2, backoff_seconds=1.0),
            prompt_version="2026-08-05",
        )
    )
    # 替代文本由核心视觉模型自动生成；失败时服务层确定性降级。
    registry.register(
        CapabilityRecord(
            name="qwen_vision",
            version="1",
            kind=CapabilityKind.MODEL,
            vendor="qwen",
            region="cn-beijing",
            model_id=VISION_MODEL_ID,
            input_schema_version="image-vision-v1",
            output_schema_version="vision-text-v1",
            status=CapabilityStatus.VERIFIED,
            retry_policy=RetryPolicy(max_attempts=2, backoff_seconds=1.0),
            prompt_version="2026-08-05",
        )
    )
    gateway = ModelGateway(registry, model_config_provider=model_config_provider)
    gateway.register_adapter("qwen_image", "1", QwenImageAdapter(client))
    gateway.register_adapter("qwen_vision", "1", QwenVisionAdapter(client))
    return ImageService(
        database=database,
        gateway=gateway,
        object_repository=repository,
        chat_repository=ConversationRepository(database),
        observability_service=ObservabilityService(),
    )


def build_video_service(
    settings: Settings,
    database: BridgesDatabase,
    repository: BridgesObjectRepository,
    model_config_provider: RunModelConfigProvider,
    *,
    client_factory: Callable[..., QwenApiClient] = QwenApiClient,
) -> VideoService:
    """用后台执行器当前凭据构造视频生成能力。"""
    client = _qwen_client(settings, client_factory)
    registry = CapabilityRegistry()
    # Wan 是模型矩阵唯一非 Qwen 系列例外（ADR-0007），仍使用同一全局
    # 百炼运行凭据并遵守单类别单快照合同。
    registry.register(
        CapabilityRecord(
            name="qwen_wan",
            version="1",
            kind=CapabilityKind.MODEL,
            vendor="wan",
            region="cn-beijing",
            model_id=VIDEO_MODEL_ID,
            input_schema_version="video-prompt-v1",
            output_schema_version="video-task-v1",
            status=CapabilityStatus.VERIFIED,
            retry_policy=RetryPolicy(max_attempts=2, backoff_seconds=1.0),
            prompt_version="2026-08-05",
        )
    )
    gateway = ModelGateway(registry, model_config_provider=model_config_provider)
    gateway.register_adapter("qwen_wan", "1", QwenWanAdapter(client))
    return VideoService(
        database=database,
        gateway=gateway,
        object_repository=repository,
        chat_repository=ConversationRepository(database),
        observability_service=ObservabilityService(),
    )


def _qwen_client(
    settings: Settings, client_factory: Callable[..., QwenApiClient]
) -> QwenApiClient:
    cassette_store = (
        CassetteStore(Path(settings.qwen_cassette_dir))
        if settings.qwen_cassette_dir is not None
        else None
    )
    # Production 强制禁止 cassette 录制，避免私人提示或响应正文落盘。
    record_mode = (
        settings.qwen_record_cassettes
        and settings.environment.lower() != "production"
    )
    return client_factory(
        api_key=settings.qwen_api_key,
        workspace_id=settings.qwen_workspace_id,
        region=settings.qwen_region,
        cassette_store=cassette_store,
        record_mode=record_mode,
    )
