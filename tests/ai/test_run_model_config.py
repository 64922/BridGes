"""V2 Issue 09：主模型运行配置的激活、共享读取与覆盖范围。"""

from __future__ import annotations

from datetime import UTC, datetime

from bridges.ai.fixed_models import CHAT_MODEL_ID, EMBEDDING_MODEL_ID
from bridges.ai.run_model_config import (
    CONFIGURABLE_CAPABILITIES,
    MODEL_CONFIG_NAMESPACE,
    ModelConfigSource,
    RunModelConfigProvider,
    configured_model_id,
    factory_run_model_config,
)
from bridges.contracts.ai import ModelCapabilities


class _MemoryStatePort:
    """最小状态端口替身：与 SqliteStateStore 同语义（整体替换命名空间）。"""

    def __init__(self) -> None:
        self.values: dict[str, dict[str, object]] = {}
        self.fail_load = False

    def load(self, namespace: str) -> dict[str, object] | None:
        if self.fail_load:
            raise RuntimeError("state store unavailable")
        return self.values.get(namespace)

    def save(self, namespace: str, state: dict[str, object]) -> None:
        self.values[namespace] = dict(state)


def _capabilities() -> ModelCapabilities:
    return ModelCapabilities(
        text=True, image=True, tool_calling=True, structured_output=True
    )


def test_factory_config_uses_the_approved_snapshot() -> None:
    config = factory_run_model_config()

    assert config.model_id == CHAT_MODEL_ID
    assert config.source == ModelConfigSource.FACTORY
    assert config.revision == 0
    assert config.capabilities == ModelCapabilities(
        text=True, image=True, tool_calling=True, structured_output=True
    )
    assert config.context_window and config.max_input_tokens


def test_activation_is_atomic_and_visible_to_other_process_readers() -> None:
    shared = _MemoryStatePort()
    provider = RunModelConfigProvider(shared)
    assert provider.snapshot().model_id == CHAT_MODEL_ID

    provider.activate(
        model_id="qwen-probe-candidate",
        capabilities=_capabilities(),
        context_window=131_072,
        max_input_tokens=130_048,
        validated_at=datetime(2026, 9, 25, tzinfo=UTC),
    )

    stored = shared.values[MODEL_CONFIG_NAMESPACE]
    assert stored["model_id"] == "qwen-probe-candidate"
    assert stored["revision"] == 1

    # 新进程（或后台执行器）由共享状态表读到同一激活结果。
    other_process = RunModelConfigProvider(shared)
    snapshot = other_process.snapshot()
    assert snapshot.model_id == "qwen-probe-candidate"
    assert snapshot.context_window == 131_072
    assert snapshot.max_input_tokens == 130_048
    assert snapshot.source == ModelConfigSource.SETTINGS


def test_activation_revision_increases_and_can_be_repeated() -> None:
    provider = RunModelConfigProvider(_MemoryStatePort())

    first = provider.activate(
        model_id="qwen-first",
        capabilities=_capabilities(),
        context_window=1,
        max_input_tokens=1,
        validated_at=None,
    )
    second = provider.activate(
        model_id="qwen-second",
        capabilities=_capabilities(),
        context_window=2,
        max_input_tokens=2,
        validated_at=None,
    )

    assert (first.revision, second.revision) == (1, 2)
    assert provider.snapshot().model_id == "qwen-second"


def test_unreadable_persisted_config_falls_back_without_breaking_calls() -> None:
    shared = _MemoryStatePort()
    provider = RunModelConfigProvider(shared)
    provider.activate(
        model_id="qwen-activated",
        capabilities=_capabilities(),
        context_window=10,
        max_input_tokens=10,
        validated_at=None,
    )

    shared.fail_load = True
    assert provider.snapshot().model_id == "qwen-activated"
    assert provider.load_error is not None

    shared.fail_load = False
    shared.values[MODEL_CONFIG_NAMESPACE] = {"model_id": "not-a-valid-snapshot"}
    assert provider.snapshot().model_id == "qwen-activated"
    assert provider.load_error is not None


def test_missing_persisted_config_restores_the_factory_snapshot() -> None:
    shared = _MemoryStatePort()
    provider = RunModelConfigProvider(shared)
    provider.activate(
        model_id="qwen-activated",
        capabilities=_capabilities(),
        context_window=10,
        max_input_tokens=10,
        validated_at=None,
    )

    shared.values.clear()

    assert provider.snapshot().model_id == CHAT_MODEL_ID
    assert provider.snapshot().source == ModelConfigSource.FACTORY


def test_configured_model_id_covers_the_aligned_capabilities_only() -> None:
    config = factory_run_model_config().model_copy(
        update={"model_id": "qwen-user-model"}
    )

    assert CONFIGURABLE_CAPABILITIES == {
        "qwen_text_chat",
        "qwen_structured_output",
        "qwen_profile_extraction",
        "qwen_vision",
        "qwen_ocr",
    }
    for capability in CONFIGURABLE_CAPABILITIES:
        assert configured_model_id(capability, config) == "qwen-user-model"
    # 知识库向量化独立固定：绝不随主模型改变（ADR-0008/0009）。
    assert configured_model_id("qwen_embedding", config) is None
    assert configured_model_id("qwen_image", config) is None
    assert configured_model_id("qwen_wan", config) is None
    assert configured_model_id("qwen_tts", config) is None
    assert EMBEDDING_MODEL_ID not in CONFIGURABLE_CAPABILITIES
