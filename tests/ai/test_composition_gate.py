"""生产组合门禁测试（Issue 09）。

分别注入缺失全局 Key、missing adapter、Stub/closeout/deterministic
adapter、cassette、矩阵漂移、未知/重复/退役 capability、MODEL fallback
与 vision/OCR 未证实，断言启动失败及稳定错误码；无违规组合必须通过。
"""

from __future__ import annotations

from typing import Any

import pytest

from bridges.ai.adapters import AdapterResult, CapabilityAdapter, StubQwenAdapter
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.ai.composition import (
    DUPLICATE_BINDING,
    MISSING_ADAPTER,
    MISSING_GLOBAL_QWEN_KEY,
    MODEL_FALLBACK_CONFIGURED,
    MODEL_MATRIX_DRIFT,
    PRODUCTION_TEST_ADAPTER,
    UNAPPROVED_MODEL_ID,
    UNKNOWN_CAPABILITY,
    VISION_OCR_COMPATIBILITY_UNPROVEN,
    CompositionViolation,
    ProductionCompositionError,
    enforce_production_composition,
    validate_production_composition,
)
from bridges.ai.fixed_models import CHAT_MODEL_ID, MODEL_BY_CAPABILITY
from bridges.ai.model_gateway import ModelGateway
from bridges.ai.production import register_builtin_capabilities
from bridges.contracts.ai import (
    CapabilityKind,
    CapabilityRecord,
    FallbackPolicy,
)
from bridges.contracts.workflows import RunContextEnvelope


class _RealLikeAdapter(CapabilityAdapter):
    """模拟真实适配器：类名不含 stub/deterministic，可过门禁。"""

    def call(
        self,
        capability: CapabilityRecord,
        run_context: RunContextEnvelope,
        payload: dict[str, Any],
    ) -> AdapterResult:
        return AdapterResult(
            actual_model_id=capability.model_id,
            output={"content": "ok"},
        )


class _DeterministicQwenAdapter(CapabilityAdapter):
    """类名含 deterministic：必须被门禁拒绝的测试替身。"""

    def call(
        self,
        capability: CapabilityRecord,
        run_context: RunContextEnvelope,
        payload: dict[str, Any],
    ) -> AdapterResult:
        return AdapterResult(actual_model_id=capability.model_id, output={"content": "ok"})


def _composition() -> tuple[CapabilityRegistry, ModelGateway]:
    registry = CapabilityRegistry()
    register_builtin_capabilities(registry)
    gateway = ModelGateway(registry)
    for capability in registry.list_active():
        gateway.register_adapter(capability.name, capability.version, _RealLikeAdapter())
    return registry, gateway


def _codes(violations: list[CompositionViolation]) -> set[str]:
    return {violation.code for violation in violations}


def test_clean_production_composition_passes_with_proven_vision_ocr() -> None:
    registry, gateway = _composition()
    violations = validate_production_composition(
        registry,
        gateway,
        global_key_configured=True,
        vision_ocr_compatibility_proven=True,
    )
    assert violations == []


def test_missing_global_key_fails_with_stable_code() -> None:
    registry = CapabilityRegistry()
    register_builtin_capabilities(registry)
    # 真实缺 Key 组合不绑定任何适配器（build_production_composition 语义）。
    gateway = ModelGateway(registry)
    violations = validate_production_composition(
        registry,
        gateway,
        global_key_configured=False,
        vision_ocr_compatibility_proven=True,
    )
    assert MISSING_GLOBAL_QWEN_KEY in _codes(violations)
    # 缺 Key 时生产组合没有真实适配器：全部活跃 capability 同步报
    # missing_adapter，但绝不注册 Stub。
    assert len([v for v in violations if v.code == MISSING_ADAPTER]) == len(
        [c for c in registry.list_active() if c.model_id]
    )
    assert PRODUCTION_TEST_ADAPTER not in _codes(violations)


def test_missing_adapter_fails() -> None:
    registry = CapabilityRegistry()
    register_builtin_capabilities(registry)
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", _RealLikeAdapter())

    violations = validate_production_composition(
        registry, gateway, global_key_configured=True, vision_ocr_compatibility_proven=True
    )
    assert MISSING_ADAPTER in _codes(violations)
    assert any(v.capability == "qwen_wan" for v in violations if v.code == MISSING_ADAPTER)


def test_stub_adapter_fails() -> None:
    registry, gateway = _composition()
    gateway.register_adapter("qwen_text_chat", "1", StubQwenAdapter())
    violations = validate_production_composition(
        registry, gateway, global_key_configured=True, vision_ocr_compatibility_proven=True
    )
    assert PRODUCTION_TEST_ADAPTER in _codes(violations)
    assert any(v.capability == "qwen_text_chat" for v in violations)


def test_closeout_adapter_fails() -> None:
    from bridges.closeout.fixtures import CloseoutQwenAdapter

    registry, gateway = _composition()
    gateway.register_adapter("qwen_tts", "1", CloseoutQwenAdapter())
    violations = validate_production_composition(
        registry, gateway, global_key_configured=True, vision_ocr_compatibility_proven=True
    )
    assert any(
        v.code == PRODUCTION_TEST_ADAPTER and v.capability == "qwen_tts"
        for v in violations
    )


def test_deterministic_adapter_fails() -> None:
    registry, gateway = _composition()
    gateway.register_adapter("qwen_vision", "1", _DeterministicQwenAdapter())
    violations = validate_production_composition(
        registry, gateway, global_key_configured=True, vision_ocr_compatibility_proven=True
    )
    assert any(
        v.code == PRODUCTION_TEST_ADAPTER and v.capability == "qwen_vision"
        for v in violations
    )


def test_cassette_enabled_fails() -> None:
    registry, gateway = _composition()
    violations = validate_production_composition(
        registry,
        gateway,
        global_key_configured=True,
        cassette_enabled=True,
        vision_ocr_compatibility_proven=True,
    )
    assert PRODUCTION_TEST_ADAPTER in _codes(violations)


def test_matrix_drift_and_unapproved_model_fail() -> None:
    registry, gateway = _composition()
    drifted = registry.get("qwen_text_chat", "1")
    drifted.model_id = "qwen3.6-flash"
    violations = validate_production_composition(
        registry, gateway, global_key_configured=True, vision_ocr_compatibility_proven=True
    )
    codes = _codes(violations)
    assert MODEL_MATRIX_DRIFT in codes
    assert UNAPPROVED_MODEL_ID in codes


def test_unknown_capability_fails() -> None:
    registry, gateway = _composition()
    registry.register(
        CapabilityRecord(
            name="qwen_mystery",
            version="1",
            kind=CapabilityKind.MODEL,
            vendor="qwen",
            region="cn-beijing",
            model_id="mystery-model",
            input_schema_version="in-v1",
            output_schema_version="out-v1",
        )
    )
    gateway.register_adapter("qwen_mystery", "1", _RealLikeAdapter())
    violations = validate_production_composition(
        registry, gateway, global_key_configured=True, vision_ocr_compatibility_proven=True
    )
    assert any(
        v.code == UNKNOWN_CAPABILITY and v.capability == "qwen_mystery"
        for v in violations
    )


def test_retired_expression_capability_blocks_startup() -> None:
    """Issue 09 AC：退役 expression_draft_generation 出现在注册表即失败关闭。"""
    registry, gateway = _composition()
    registry.register(
        CapabilityRecord(
            name="expression_draft_generation",
            version="1",
            kind=CapabilityKind.MODEL,
            vendor="qwen",
            region="cn-beijing",
            model_id=CHAT_MODEL_ID,
            input_schema_version="in-v1",
            output_schema_version="out-v1",
        )
    )
    gateway.register_adapter("expression_draft_generation", "1", _RealLikeAdapter())
    violations = validate_production_composition(
        registry, gateway, global_key_configured=True, vision_ocr_compatibility_proven=True
    )
    assert any(
        v.code == UNKNOWN_CAPABILITY and v.capability == "expression_draft_generation"
        for v in violations
    )


def test_duplicate_binding_fails() -> None:
    registry, gateway = _composition()
    duplicate = registry.get("qwen_text_chat", "1").model_copy(update={"version": "2"})
    registry.register(duplicate)
    gateway.register_adapter("qwen_text_chat", "2", _RealLikeAdapter())
    violations = validate_production_composition(
        registry, gateway, global_key_configured=True, vision_ocr_compatibility_proven=True
    )
    assert any(
        v.code == DUPLICATE_BINDING and v.capability == "qwen_text_chat"
        for v in violations
    )


def test_model_fallback_configuration_fails() -> None:
    registry, gateway = _composition()
    with_fallback = registry.get("qwen_tts", "1").model_copy(
        update={
            "fallback_policy": FallbackPolicy(
                fallback_capability_name="qwen_text_chat",
                fallback_capability_version="1",
            )
        }
    )
    registry.register(with_fallback)
    violations = validate_production_composition(
        registry, gateway, global_key_configured=True, vision_ocr_compatibility_proven=True
    )
    assert any(
        v.code == MODEL_FALLBACK_CONFIGURED and v.capability == "qwen_tts"
        for v in violations
    )


def test_vision_ocr_unproven_fails_closed() -> None:
    registry, gateway = _composition()
    unproven = validate_production_composition(
        registry,
        gateway,
        global_key_configured=True,
        vision_ocr_compatibility_proven=False,
    )
    assert VISION_OCR_COMPATIBILITY_UNPROVEN in _codes(unproven)
    proven = validate_production_composition(
        registry,
        gateway,
        global_key_configured=True,
        vision_ocr_compatibility_proven=True,
    )
    assert VISION_OCR_COMPATIBILITY_UNPROVEN not in _codes(proven)


def test_startup_gate_can_defer_vision_ocr_evidence_to_release_gate() -> None:
    """阶段 1 production-like 启动门：组合完整性仍强制，vision/OCR 证据
    由发布门强制（``vision_ocr_compatibility_required=False``）。"""
    registry, gateway = _composition()
    startup = validate_production_composition(
        registry,
        gateway,
        global_key_configured=True,
        vision_ocr_compatibility_proven=False,
        vision_ocr_compatibility_required=False,
    )
    assert VISION_OCR_COMPATIBILITY_UNPROVEN not in _codes(startup)
    # 启动门不豁免其他完整性检查：漂移仍失败关闭。
    drifted = registry.get("qwen_text_chat", "1")
    drifted.model_id = "qwen3.6-flash"
    startup_with_drift = validate_production_composition(
        registry,
        gateway,
        global_key_configured=True,
        vision_ocr_compatibility_proven=False,
        vision_ocr_compatibility_required=False,
    )
    assert MODEL_MATRIX_DRIFT in _codes(startup_with_drift)


def test_enforce_raises_with_stable_error_codes() -> None:
    registry = CapabilityRegistry()
    register_builtin_capabilities(registry)
    gateway = ModelGateway(registry)

    with pytest.raises(ProductionCompositionError) as exc_info:
        enforce_production_composition(
            registry,
            gateway,
            global_key_configured=False,
            cassette_enabled=True,
            vision_ocr_compatibility_proven=False,
        )
    codes = {violation.code for violation in exc_info.value.violations}
    assert {MISSING_GLOBAL_QWEN_KEY, MISSING_ADAPTER, PRODUCTION_TEST_ADAPTER} <= codes
    # 错误正文只含稳定错误码，绝不包含 Key 或正文。
    message = str(exc_info.value)
    assert "missing_global_qwen_key" in message
    assert "sk-" not in message


def test_approved_matrix_covers_all_builtin_capabilities() -> None:
    """批准矩阵必须枚举全部内置活跃 MODEL capability（防新增能力漏配）。"""
    registry = CapabilityRegistry()
    register_builtin_capabilities(registry)
    registered = {c.name for c in registry.list_active() if c.model_id}
    assert registered == set(MODEL_BY_CAPABILITY)
