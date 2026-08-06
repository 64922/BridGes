"""Issue 10 注册表合同：ADR-0009 固定绑定与"无 Stub 伪装成功"。

Issue 41（AC3）：``qwen_force_stub`` 开关已移除。StubQwenAdapter 只注册在
显式 test 环境（本地与 CI 测试确定性，与 /_test/* 端点同一门控）；
development/production 配置绝不注册任何 Stub——未配置账户密钥时真实模型
能力保持未绑定，网关返回明确的"未绑定适配器"阻塞结果。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from bridges.api.main import create_app
from bridges.config import get_settings


def _app(monkeypatch: pytest.MonkeyPatch, environment: str) -> TestClient:
    """按环境构建应用；环境变量经 monkeypatch 设置，测试结束自动恢复。"""
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", environment)
    monkeypatch.delenv("BRIDGES_QWEN_API_KEY", raising=False)
    monkeypatch.delenv("BRIDGES_QWEN_API_KEY_FILE", raising=False)
    get_settings.cache_clear()
    return TestClient(create_app())


def test_production_registry_never_binds_stub_to_real_model_ids(
    monkeypatch,
) -> None:
    client = _app(monkeypatch, "production")
    gateway = client.app.state.model_gateway  # type: ignore[attr-defined]
    registry = client.app.state.capability_registry  # type: ignore[attr-defined]

    real_model_capabilities = [
        capability
        for capability in registry.list_active()
        if capability.model_id not in (None, "deterministic")
    ]
    assert real_model_capabilities
    # 生产配置（无凭据、无任何 Stub 开关）：真实模型能力均未绑定
    # Stub 适配器——不伪装成功。
    for capability in real_model_capabilities:
        assert not gateway.is_adapter_registered(capability.name, capability.version)


def test_development_registry_never_binds_stub(monkeypatch) -> None:
    """development（源码环境真实用户默认值）同样不注册 Stub。

    AC3：用户从源码运行 ``BridGes start`` 未配置密钥时，能力明确停用并
    显示真实错误，而不是获得确定性假回答。
    """
    client = _app(monkeypatch, "development")
    gateway = client.app.state.model_gateway  # type: ignore[attr-defined]
    registry = client.app.state.capability_registry  # type: ignore[attr-defined]

    for capability in registry.list_active():
        assert not gateway.is_adapter_registered(capability.name, capability.version)


def test_test_environment_binds_stub_for_determinism(monkeypatch) -> None:
    """显式 test 环境才绑定确定性适配器（本地与 CI 测试确定性）。"""
    client = _app(monkeypatch, "test")
    gateway = client.app.state.model_gateway  # type: ignore[attr-defined]
    registry = client.app.state.capability_registry  # type: ignore[attr-defined]

    real_model_capabilities = [
        capability
        for capability in registry.list_active()
        if capability.model_id not in (None, "deterministic")
    ]
    assert real_model_capabilities
    for capability in real_model_capabilities:
        assert gateway.is_adapter_registered(capability.name, capability.version)


def test_fixed_matrix_model_ids_are_pinned_in_registry(monkeypatch) -> None:
    monkeypatch.setenv("BRIDGES_QWEN_API_KEY", "")
    client = _app(monkeypatch, "test")
    registry = client.app.state.capability_registry  # type: ignore[attr-defined]

    # ADR-0009 固定绑定：唯一模型快照，无备用模型。
    chat = registry.get("qwen_text_chat", "1")
    assert chat.model_id == "qwen3.7-plus-2026-05-26"
    assert not chat.fallback_policy.fallback_capability_name
    tts = registry.get("qwen_tts", "1")
    assert tts.model_id == "qwen3-tts-flash-2025-11-27"
    assert not tts.fallback_policy.fallback_capability_name
    asr = registry.get("qwen_asr_short", "1")
    assert asr.model_id == "qwen3-asr-flash"

    # 备用模型能力已从注册表移除。
    from bridges.ai.capability_registry import CapabilityRegistryError

    for name in ("qwen_text_chat_fallback", "qwen_tts_instruct"):
        with pytest.raises(CapabilityRegistryError):
            registry.get(name, "1")
