"""生产组合装配：活跃 capability 注册表与真实适配器（Issue 09）。

单一事实源：``bridges.ai.fixed_models`` 的批准矩阵。本模块只负责把活跃
MODEL capability 与真实 Qwen 适配器装配成生产组合，供 API 组合根、CLI
启动门与发布门复用同一接线：

- 注册表枚举与适配器绑定一一对应，model ID 全部来自批准矩阵；
- 缺少安装级全局 Qwen Key（``BRIDGES_QWEN_API_KEY`` / ``_FILE``）时不
  注册任何真实适配器，由生产组合校验器以 ``missing_global_qwen_key``
  失败关闭；
- cassette 仅在配置了 cassette 目录时挂载，校验器据
  ``cassette_enabled`` 拒绝生产组合使用回放适配器；
- 测试环境由调用方（``bridges.api.main``）另行注册确定性适配器，
  本模块不注册任何 Stub。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from bridges.ai import (
    CapabilityRegistry,
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
)
from bridges.ai.fixed_models import (
    ASR_LONG_MODEL_ID,
    ASR_MODEL_ID,
    CHAT_MODEL_ID,
    IMAGE_MODEL_ID,
    OCR_MODEL_ID,
    TTS_MODEL_ID,
    VIDEO_MODEL_ID,
    VISION_MODEL_ID,
)
from bridges.config import Settings
from bridges.contracts.ai import (
    CapabilityKind,
    CapabilityRecord,
    CapabilityStatus,
    RetryPolicy,
    StructuredOutputFormat,
)
from bridges.credentials.global_credential import is_global_qwen_key_configured


def register_builtin_capabilities(registry: CapabilityRegistry) -> None:
    """Register the Qwen capabilities used by built-in workflows at T009.

    Issue 10 起按 ADR-0009 固定模型矩阵：核心对话绑定
    ``qwen3.7-plus-2026-05-26`` 唯一快照，不注册备用模型——失败只允许
    重试同一绑定，不得暗中切换模型。Issue 09 起全部 model ID 取自
    ``bridges.ai.fixed_models``，注册表不再出现模型字面量；视觉/OCR
    与核心对话对齐同一快照（兼容性由生产组合门禁验证）。
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
            model_id=CHAT_MODEL_ID,
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
            model_id=CHAT_MODEL_ID,
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
    # Issue 09：与核心对话对齐同一快照，兼容性由真实 smoke 门禁验证。
    registry.register(
        CapabilityRecord(
            name="qwen_ocr",
            version="1",
            kind=CapabilityKind.MODEL,
            vendor="qwen",
            region="cn-beijing",
            model_id=OCR_MODEL_ID,
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
            model_id=VISION_MODEL_ID,
            input_schema_version="image-vision-v1",
            output_schema_version="vision-text-v1",
            supported_modalities=["text", "image"],
            status=CapabilityStatus.VERIFIED,
            retry_policy=RetryPolicy(max_attempts=3, backoff_seconds=1.0),
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
            model_id=ASR_LONG_MODEL_ID,
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
            model_id=IMAGE_MODEL_ID,
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
            model_id=VIDEO_MODEL_ID,
            input_schema_version="video-prompt-v1",
            output_schema_version="video-task-v1",
            supported_modalities=["text", "video"],
            status=CapabilityStatus.VERIFIED,
            retry_policy=RetryPolicy(max_attempts=2, backoff_seconds=1.0),
            prompt_version="2026-08-05",
        )
    )


@dataclass(frozen=True)
class ProductionComposition:
    """一次生产组合的不可变快照：注册表、网关与门禁输入。"""

    registry: CapabilityRegistry
    gateway: ModelGateway
    cassette_enabled: bool
    global_key_configured: bool


def build_production_composition(settings: Settings | None) -> ProductionComposition:
    """装配生产组合：注册批准矩阵 + 真实适配器接线。

    - 缺少全局 Key 时只注册矩阵、不绑定适配器（门禁报
      ``missing_global_qwen_key`` 与 ``missing_adapter``，调用方决定
      如何失败关闭）；
    - 配置了 cassette 目录时挂载回放存储并置 ``cassette_enabled``，
      生产组合门禁据此拒绝（``production_test_adapter``）；
    - 绝不注册 Stub/确定性适配器；测试环境由 API 组合根另行补齐。
    """
    registry = CapabilityRegistry()
    register_builtin_capabilities(registry)
    gateway = ModelGateway(registry)
    cassette_enabled = False
    global_key_configured = settings is not None and is_global_qwen_key_configured(
        settings
    )
    if not global_key_configured:
        return ProductionComposition(
            registry=registry,
            gateway=gateway,
            cassette_enabled=False,
            global_key_configured=False,
        )

    assert settings is not None
    # Issue 39 AC5：cassette 会把完整请求/响应正文以明文 JSON 落盘，
    # 生产环境强制禁止录制，避免私人对话正文落盘泄露。
    cassette_record_mode = (
        settings.qwen_record_cassettes and settings.environment.lower() != "production"
    )
    cassette_store: CassetteStore | None = None
    if settings.qwen_cassette_dir is not None:
        cassette_store = CassetteStore(Path(settings.qwen_cassette_dir))
        cassette_enabled = True
    qwen_client = QwenApiClient(
        api_key=settings.qwen_api_key,
        workspace_id=settings.qwen_workspace_id,
        region=settings.qwen_region,
        cassette_store=cassette_store,
        record_mode=cassette_record_mode,
    )
    gateway.register_adapter("qwen_text_chat", "1", QwenTextChatAdapter(qwen_client))
    gateway.register_adapter(
        "qwen_structured_output", "1", QwenStructuredOutputAdapter(qwen_client)
    )
    gateway.register_adapter(
        "qwen_profile_extraction", "1", QwenStructuredOutputAdapter(qwen_client)
    )
    gateway.register_adapter("qwen_ocr", "1", QwenOcrAdapter(qwen_client))
    gateway.register_adapter("qwen_vision", "1", QwenVisionAdapter(qwen_client))
    gateway.register_adapter("qwen_asr_short", "1", QwenAsrAdapter(qwen_client))
    gateway.register_adapter("qwen_asr_long", "1", QwenAsrAdapter(qwen_client))
    # T062: TTS adapter for accessibility narration synthesis.
    gateway.register_adapter("qwen_tts", "1", QwenTtsAdapter(qwen_client))
    # Issue 31: 图片生成与编辑异步任务适配器（submit/poll/fetch/cancel）。
    gateway.register_adapter("qwen_image", "1", QwenImageAdapter(qwen_client))
    # Issue 32: 文生视频异步任务适配器（Wan 例外，submit/poll/fetch/cancel）。
    gateway.register_adapter("qwen_wan", "1", QwenWanAdapter(qwen_client))
    return ProductionComposition(
        registry=registry,
        gateway=gateway,
        cassette_enabled=cassette_enabled,
        global_key_configured=True,
    )
