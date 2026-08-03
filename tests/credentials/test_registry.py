"""Issue 10 注册表合同：ADR-0009 固定绑定与"无 Stub 伪装成功"。

生产路径（未显式开启测试 Stub 开关）下，任何带真实模型 ID 的能力都不得
绑定 StubQwenAdapter；内置 deterministic 工具能力除外。测试环境通过
``BRIDGES_QWEN_FORCE_STUB=true`` 显式启用 Stub，保证本地开发确定性。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from bridges.api.main import create_app
from bridges.config import get_settings


def _app_with_force_stub(force_stub: str) -> TestClient:
    import os

    os.environ["BRIDGES_QWEN_FORCE_STUB"] = force_stub
    get_settings.cache_clear()
    return TestClient(create_app())


def test_production_registry_never_binds_stub_to_real_model_ids(
    monkeypatch,
) -> None:
    monkeypatch.setenv("BRIDGES_QWEN_FORCE_STUB", "false")
    monkeypatch.delenv("BRIDGES_QWEN_API_KEY", raising=False)
    monkeypatch.delenv("BRIDGES_QWEN_API_KEY_FILE", raising=False)
    get_settings.cache_clear()
    client = _app_with_force_stub("false")
    gateway = client.app.state.model_gateway  # type: ignore[attr-defined]
    registry = client.app.state.capability_registry  # type: ignore[attr-defined]

    real_model_capabilities = [
        capability
        for capability in registry.list_active()
        if capability.model_id not in (None, "deterministic")
    ]
    assert real_model_capabilities
    # 生产环境（无凭据、未显式开启测试 Stub）：真实模型能力均未绑定
    # Stub 适配器——不伪装成功。
    for capability in real_model_capabilities:
        assert not gateway.is_adapter_registered(capability.name, capability.version)


def test_fixed_matrix_model_ids_are_pinned_in_registry(monkeypatch) -> None:
    monkeypatch.setenv("BRIDGES_QWEN_FORCE_STUB", "false")
    monkeypatch.setenv("BRIDGES_QWEN_API_KEY", "")
    get_settings.cache_clear()
    client = _app_with_force_stub("false")
    registry = client.app.state.capability_registry  # type: ignore[attr-defined]

    # ADR-0009 固定绑定：唯一模型快照，无备用模型。
    chat = registry.get("qwen_text_chat", "1")
    assert chat.model_id == "qwen3.7-plus-2026-05-26"
    assert not chat.fallback_policy.fallback_capability_name
    tts = registry.get("qwen_tts", "1")
    assert tts.model_id == "qwen3-tts-flash-2025-11-27"
    assert not tts.fallback_policy.fallback_capability_name
    asr = registry.get("qwen_asr_short", "1")
    assert asr.model_id == "qwen3-asr-flash-2025-09-08"

    # 备用模型能力已从注册表移除。
    from bridges.ai.capability_registry import CapabilityRegistryError

    for name in ("qwen_text_chat_fallback", "qwen_tts_instruct"):
        with pytest.raises(CapabilityRegistryError):
            registry.get(name, "1")


def test_force_stub_keeps_local_tests_deterministic(monkeypatch) -> None:
    monkeypatch.setenv("BRIDGES_QWEN_FORCE_STUB", "true")
    monkeypatch.setenv("BRIDGES_QWEN_API_KEY", "")
    get_settings.cache_clear()
    client = _app_with_force_stub("true")
    gateway = client.app.state.model_gateway  # type: ignore[attr-defined]
    registry = client.app.state.capability_registry  # type: ignore[attr-defined]

    for capability in registry.list_active():
        if capability.model_id not in (None, "deterministic"):
            assert gateway.is_adapter_registered(capability.name, capability.version)
