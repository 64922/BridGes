"""V2 Issue 09：网关按运行配置解析主模型，并遵守运行级锁定。

覆盖范围：可手填的主对话/结构化/画像/视觉/OCR 能力；向量化等其余能力保持
出厂矩阵绑定；``model_override``（进行中的轮次）优先于运行配置。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from bridges.ai import CapabilityRegistry, ModelGateway
from bridges.ai.adapters import AdapterResult
from bridges.ai.fixed_models import CHAT_MODEL_ID, EMBEDDING_MODEL_ID
from bridges.ai.run_model_config import RunModelConfigProvider
from bridges.contracts.ai import (
    CapabilityKind,
    CapabilityRecord,
    CapabilityStatus,
    FallbackPolicy,
    ModelCallStatus,
    ModelCapabilities,
    RetryPolicy,
)
from bridges.contracts.workflows import RunContextEnvelope

_USER_MODEL_ID = "qwen-user-model"


class _RecordingAdapter:
    """记录每次调用的能力绑定（模型 ID 与输入额度）。"""

    def __init__(self, actual_model_id: str | None = None) -> None:
        self.calls: list[CapabilityRecord] = []
        self._actual_model_id = actual_model_id

    def call(
        self,
        capability: CapabilityRecord,
        run_context: RunContextEnvelope,
        payload: dict[str, Any],
    ) -> AdapterResult:
        self.calls.append(capability)
        return AdapterResult(
            actual_model_id=self._actual_model_id or capability.model_id,
            output={"ok": True},
        )


def _capability(name: str, model_id: str) -> CapabilityRecord:
    return CapabilityRecord(
        name=name,
        version="1",
        kind=CapabilityKind.MODEL,
        vendor="qwen",
        region="cn-beijing",
        model_id=model_id,
        input_schema_version="in-v1",
        output_schema_version="out-v1",
        status=CapabilityStatus.VERIFIED,
        fallback_policy=FallbackPolicy(),
        retry_policy=RetryPolicy(max_attempts=1),
    )


def _context() -> RunContextEnvelope:
    return RunContextEnvelope(
        run_id="run-1",
        account_id="account-1",
        project_id="project-1",
        workflow_name="wf",
        workflow_version="1",
        submitted_at=datetime.now(UTC),
    )


def _gateway_with_config() -> tuple[ModelGateway, dict[str, _RecordingAdapter]]:
    registry = CapabilityRegistry()
    adapters: dict[str, _RecordingAdapter] = {}
    for name, model_id in (
        ("qwen_text_chat", CHAT_MODEL_ID),
        ("qwen_vision", CHAT_MODEL_ID),
        ("qwen_embedding", EMBEDDING_MODEL_ID),
    ):
        registry.register(_capability(name, model_id))
    gateway = ModelGateway(
        registry, model_config_provider=RunModelConfigProvider()
    )
    for name in ("qwen_text_chat", "qwen_vision", "qwen_embedding"):
        adapter = _RecordingAdapter()
        adapters[name] = adapter
        gateway.register_adapter(name, "1", adapter)
    return gateway, adapters


def _activate(provider: RunModelConfigProvider) -> None:
    provider.activate(
        model_id=_USER_MODEL_ID,
        capabilities=ModelCapabilities(
            text=True, image=True, tool_calling=True, structured_output=True
        ),
        context_window=131_072,
        max_input_tokens=130_048,
        validated_at=datetime(2026, 9, 25, tzinfo=UTC),
    )


def test_active_config_rebinds_chat_and_vision_without_touching_embedding() -> None:
    gateway, adapters = _gateway_with_config()
    provider = gateway._model_config_provider  # noqa: SLF001 - 测试注入点
    assert provider is not None
    _activate(provider)

    chat_result = gateway.invoke("qwen_text_chat", "1", _context())
    embedding_result = gateway.invoke("qwen_embedding", "1", _context())

    assert chat_result.status == ModelCallStatus.SUCCESS
    assert adapters["qwen_text_chat"].calls[0].model_id == _USER_MODEL_ID
    assert adapters["qwen_text_chat"].calls[0].max_input_tokens == 130_048
    assert chat_result.lock is not None
    assert chat_result.lock.actual_model_id == _USER_MODEL_ID
    # 向量化独立固定：注册表绑定保持不变。
    assert adapters["qwen_embedding"].calls[0].model_id == EMBEDDING_MODEL_ID
    assert embedding_result.lock is not None
    assert embedding_result.lock.actual_model_id == EMBEDDING_MODEL_ID


def test_factory_snapshot_keeps_the_approved_matrix_binding() -> None:
    gateway, adapters = _gateway_with_config()

    gateway.invoke("qwen_text_chat", "1", _context())

    assert adapters["qwen_text_chat"].calls[0].model_id == CHAT_MODEL_ID


def test_run_override_wins_over_a_newly_activated_config() -> None:
    """进行中的轮次沿用启动时模型：换配置不改变本轮实际调用。"""
    gateway, adapters = _gateway_with_config()
    provider = gateway._model_config_provider  # noqa: SLF001 - 测试注入点
    assert provider is not None
    _activate(provider)

    result = gateway.invoke(
        "qwen_text_chat", "1", _context(), model_override=CHAT_MODEL_ID
    )

    assert result.status == ModelCallStatus.SUCCESS
    assert adapters["qwen_text_chat"].calls[0].model_id == CHAT_MODEL_ID
    assert result.lock is not None
    assert result.lock.actual_model_id == CHAT_MODEL_ID


def test_served_model_drift_is_still_fail_closed_under_a_user_config() -> None:
    gateway, _ = _gateway_with_config()
    provider = gateway._model_config_provider  # noqa: SLF001 - 测试注入点
    assert provider is not None
    _activate(provider)
    gateway.register_adapter(
        "qwen_text_chat", "1", _RecordingAdapter(actual_model_id="qwen-something-else")
    )

    result = gateway.invoke("qwen_text_chat", "1", _context())

    assert result.status == ModelCallStatus.BLOCKED
    assert result.error_code == "actual_model_mismatch"
    assert result.lock is not None
    assert result.lock.actual_model_id == "qwen-something-else"


def test_stream_applies_the_run_override() -> None:
    gateway, adapters = _gateway_with_config()
    provider = gateway._model_config_provider  # noqa: SLF001 - 测试注入点
    assert provider is not None
    _activate(provider)

    events = list(
        gateway.stream(
            "qwen_text_chat", "1", _context(), model_override=CHAT_MODEL_ID
        )
    )

    assert [event.kind for event in events] == ["done"]
    assert adapters["qwen_text_chat"].calls[0].model_id == CHAT_MODEL_ID
    assert events[-1].lock is not None
    assert events[-1].lock.actual_model_id == CHAT_MODEL_ID
