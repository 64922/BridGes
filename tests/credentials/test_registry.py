"""Issue 10 注册表合同：ADR-0009 固定绑定与"无 Stub 伪装成功"。

Issue 41（AC3）：``qwen_force_stub`` 开关已移除。StubQwenAdapter 只注册在
显式 test 环境（本地与 CI 测试确定性，与 /_test/* 端点同一门控）；
development/production 配置绝不注册任何 Stub——未配置账户密钥时真实模型
能力保持未绑定，网关返回明确的"未绑定适配器"阻塞结果。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from bridges.ai import (
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
from bridges.ai.fixed_models import (
    ASR_MODEL_ID,
    CHAT_MODEL_ID,
    TTS_MODEL_ID,
)
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


def test_test_environment_with_global_key_registers_all_fixed_adapters(
    monkeypatch,
) -> None:
    """test 环境注入非秘密占位全局 Key 时，全部固定适配器完成真实注册。

    GQ-01：占位值只用于验证接线——QwenApiClient 构造不发起真实网络请求；
    已注册的真实适配器不被 Stub 覆盖，未绑定的剩余能力由确定性 Stub 补齐。
    """
    # 注意：不复用 _app（它会删除 QWEN_API_KEY）；占位 Key 必须保留以
    # 走真实适配器注册分支。
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    monkeypatch.setenv("BRIDGES_QWEN_API_KEY", "placeholder-global-key-not-real")
    monkeypatch.delenv("BRIDGES_QWEN_API_KEY_FILE", raising=False)
    get_settings.cache_clear()
    client = TestClient(create_app())
    gateway = client.app.state.model_gateway  # type: ignore[attr-defined]

    expected_real = {
        "qwen_text_chat": QwenTextChatAdapter,
        "qwen_structured_output": QwenStructuredOutputAdapter,
        "qwen_ocr": QwenOcrAdapter,
        "qwen_vision": QwenVisionAdapter,
        "qwen_asr_short": QwenAsrAdapter,
        "qwen_asr_long": QwenAsrAdapter,
        "qwen_tts": QwenTtsAdapter,
        "qwen_image": QwenImageAdapter,
        "qwen_wan": QwenWanAdapter,
    }
    for name, adapter_type in expected_real.items():
        adapter = gateway._adapters[(name, "1")]
        assert isinstance(adapter, adapter_type), name
        assert not isinstance(adapter, StubQwenAdapter), name


def test_fixed_matrix_model_ids_are_pinned_in_registry(monkeypatch) -> None:
    monkeypatch.setenv("BRIDGES_QWEN_API_KEY", "")
    client = _app(monkeypatch, "test")
    registry = client.app.state.capability_registry  # type: ignore[attr-defined]

    # ADR-0009 固定绑定：唯一模型快照，无备用模型。断言值来自单一事实源
    # （bridges.ai.fixed_models），测试合同不另写模型字面量（Issue 09）。
    chat = registry.get("qwen_text_chat", "1")
    assert chat.model_id == CHAT_MODEL_ID
    assert not chat.fallback_policy.fallback_capability_name
    tts = registry.get("qwen_tts", "1")
    assert tts.model_id == TTS_MODEL_ID
    assert not tts.fallback_policy.fallback_capability_name
    asr = registry.get("qwen_asr_short", "1")
    assert asr.model_id == ASR_MODEL_ID

    # 备用模型能力已从注册表移除。
    from bridges.ai.capability_registry import CapabilityRegistryError

    for name in ("qwen_text_chat_fallback", "qwen_tts_instruct"):
        with pytest.raises(CapabilityRegistryError):
            registry.get(name, "1")


def test_every_active_capability_matches_approved_matrix(monkeypatch) -> None:
    """Issue 09 AC：逐项断言活跃 capability → 固定模型映射与批准矩阵一致。

    注册表暴露的实际 ID 与 ``bridges.ai.fixed_models.MODEL_BY_CAPABILITY``
    完全一致；结构化输出/画像与核心对话复用同一快照；vision/OCR 与核心
    对话对齐同一快照；不存在矩阵之外的模型字面量。
    """
    from bridges.ai.fixed_models import (
        ASR_LONG_MODEL_ID,
        CHAT_MODEL_ID,
        IMAGE_MODEL_ID,
        MODEL_BY_CAPABILITY,
        OCR_MODEL_ID,
        VIDEO_MODEL_ID,
        VISION_MODEL_ID,
    )
    from bridges.contracts.ai import CapabilityKind

    monkeypatch.setenv("BRIDGES_QWEN_API_KEY", "")
    client = _app(monkeypatch, "test")
    registry = client.app.state.capability_registry  # type: ignore[attr-defined]

    # 只枚举 MODEL 能力（TOOL 能力的 "deterministic" model_id 是领域包
    # 校验器标记，不属于批准矩阵）。
    active = [
        c
        for c in registry.list_active()
        if c.kind == CapabilityKind.MODEL and c.model_id
    ]
    assert {c.name for c in active} == set(MODEL_BY_CAPABILITY)
    for capability in active:
        assert capability.model_id == MODEL_BY_CAPABILITY[capability.name], capability.name
        assert capability.vendor in {"qwen", "wan"}, capability.name
        assert capability.region == "cn-beijing", capability.name
        assert not capability.fallback_policy.fallback_capability_name, capability.name

    # 关键绑定的显式断言（与确认矩阵逐项一致）。
    structured = registry.get("qwen_structured_output", "1")
    profile = registry.get("qwen_profile_extraction", "1")
    assert structured.model_id == CHAT_MODEL_ID
    assert profile.model_id == CHAT_MODEL_ID
    vision = registry.get("qwen_vision", "1")
    ocr = registry.get("qwen_ocr", "1")
    assert vision.model_id == VISION_MODEL_ID == CHAT_MODEL_ID
    assert ocr.model_id == OCR_MODEL_ID == CHAT_MODEL_ID
    assert registry.get("qwen_asr_long", "1").model_id == ASR_LONG_MODEL_ID
    assert registry.get("qwen_image", "1").model_id == IMAGE_MODEL_ID
    assert registry.get("qwen_wan", "1").model_id == VIDEO_MODEL_ID


def test_retired_expression_capability_is_not_registered(monkeypatch) -> None:
    """Issue 09 AC：已退役 expression_draft_generation 不在生产注册表。"""
    monkeypatch.setenv("BRIDGES_QWEN_API_KEY", "")
    client = _app(monkeypatch, "test")
    registry = client.app.state.capability_registry  # type: ignore[attr-defined]
    from bridges.ai.capability_registry import CapabilityRegistryError

    with pytest.raises(CapabilityRegistryError):
        registry.get("expression_draft_generation", "1")
    assert "expression_draft_generation" not in {
        c.name for c in registry.list_active()
    }
