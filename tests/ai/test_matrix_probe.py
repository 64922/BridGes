"""Vision/OCR 真实兼容 smoke 探针测试（Issue 09）。

探针只驱动真实适配器；测试用可编程替身注入成功/失败响应，验证分级
（passed/failed/inconclusive）、合同检查（真实 model、非空输出、状态、
运行锁）与脱敏报告。真实 Key 缺失时结果必须为 ``inconclusive``。
"""

from __future__ import annotations

import json
from typing import Any

from bridges.ai.adapters import AdapterError, AdapterResult, AuthError, CapabilityAdapter
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.ai.matrix_probe import (
    KNOWN_TEXT_TOKEN,
    VisionOcrProbeResult,
    _render_known_text_png,
    run_vision_ocr_probes,
    write_probe_report,
)
from bridges.ai.model_gateway import ModelGateway
from bridges.ai.production import register_builtin_capabilities
from bridges.contracts.ai import CapabilityRecord
from bridges.contracts.workflows import RunContextEnvelope


class _ProgrammableAdapter(CapabilityAdapter):
    """可编程真实-like 适配器：默认返回与 capability 一致的实际模型。"""

    def __init__(self, content: str = "", error: AdapterError | None = None) -> None:
        self._content = content
        self._error = error
        self.calls: list[dict[str, Any]] = []

    def call(
        self,
        capability: CapabilityRecord,
        run_context: RunContextEnvelope,
        payload: dict[str, Any],
    ) -> AdapterResult:
        self.calls.append(payload)
        if self._error is not None:
            raise self._error
        return AdapterResult(
            actual_model_id=capability.model_id,
            output={"content": self._content},
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        )


def _composition() -> tuple[CapabilityRegistry, ModelGateway]:
    registry = CapabilityRegistry()
    register_builtin_capabilities(registry)
    return registry, ModelGateway(registry)


def test_probe_inconclusive_without_global_key() -> None:
    registry, gateway = _composition()
    results = run_vision_ocr_probes(registry, gateway, global_key_configured=False)
    assert [r.status for r in results] == ["inconclusive", "inconclusive"]
    assert all(r.error_code == "missing_global_qwen_key" for r in results)
    assert all(r.lock_id is None for r in results)


def test_probe_inconclusive_without_adapter() -> None:
    registry, gateway = _composition()
    results = run_vision_ocr_probes(registry, gateway, global_key_configured=True)
    assert [r.status for r in results] == ["inconclusive", "inconclusive"]
    assert all(r.error_code == "no_adapter" for r in results)


def test_probe_passes_when_real_contract_is_honored() -> None:
    registry, gateway = _composition()
    gateway.register_adapter(
        "qwen_vision", "1", _ProgrammableAdapter(content=f"文字内容是 {KNOWN_TEXT_TOKEN}")
    )
    gateway.register_adapter(
        "qwen_ocr", "1", _ProgrammableAdapter(content=f"OCR 结果 {KNOWN_TEXT_TOKEN}")
    )
    results = run_vision_ocr_probes(registry, gateway, global_key_configured=True)

    assert all(r.status == "passed" for r in results)
    assert all(r.actual_model_id == r.approved_model_id for r in results)
    assert all(r.lock_id for r in results)
    assert all(r.error_code is None for r in results)
    assert all(r.output_empty is False for r in results)
    ocr = next(r for r in results if r.capability == "qwen_ocr")
    assert ocr.known_text_present is True


def test_probe_fails_when_ocr_misses_known_text() -> None:
    registry, gateway = _composition()
    gateway.register_adapter("qwen_vision", "1", _ProgrammableAdapter(content="a picture"))
    gateway.register_adapter("qwen_ocr", "1", _ProgrammableAdapter(content="nothing useful"))
    results = run_vision_ocr_probes(registry, gateway, global_key_configured=True)

    vision = next(r for r in results if r.capability == "qwen_vision")
    assert vision.status == "passed"
    ocr = next(r for r in results if r.capability == "qwen_ocr")
    assert ocr.status == "failed"
    assert ocr.error_code == "vision_ocr_contract_failed"
    assert ocr.known_text_present is False


def test_probe_fails_on_auth_error_with_stable_code() -> None:
    registry, gateway = _composition()
    gateway.register_adapter(
        "qwen_vision", "1", _ProgrammableAdapter(error=AuthError("bad key"))
    )
    gateway.register_adapter("qwen_ocr", "1", _ProgrammableAdapter(content=KNOWN_TEXT_TOKEN))
    results = run_vision_ocr_probes(registry, gateway, global_key_configured=True)

    vision = next(r for r in results if r.capability == "qwen_vision")
    assert vision.status == "failed"
    assert vision.error_code == "auth_error"
    # 失败同样携带运行锁（Issue 10 不可变运行锁语义），供门禁复核。
    assert vision.lock_id is not None


def test_known_text_image_renders_png() -> None:
    png = _render_known_text_png()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png) > 100


def test_write_probe_report_is_sanitized(tmp_path) -> None:
    registry, gateway = _composition()
    gateway.register_adapter("qwen_vision", "1", _ProgrammableAdapter(content="ok"))
    gateway.register_adapter("qwen_ocr", "1", _ProgrammableAdapter(content=KNOWN_TEXT_TOKEN))
    results = run_vision_ocr_probes(registry, gateway, global_key_configured=True)

    report_path = write_probe_report(results, tmp_path, code_version="test-hash")
    payload = json.loads(report_path.read_text(encoding="utf-8"))

    assert payload["kind"] == "vision_ocr_compatibility_probe"
    assert len(payload["results"]) == 2
    for entry in payload["results"]:
        assert set(entry) == {
            "capability",
            "approved_model_id",
            "actual_model_id",
            "adapter_type",
            "status",
            "latency_ms",
            "lock_id",
            "error_code",
            "output_empty",
            "known_text_present",
        }
        # 绝不包含图像内容、请求正文或秘密形态。
        assert "image_base64" not in json.dumps(entry)
        assert "sk-" not in json.dumps(payload)


def test_probe_result_serializes_all_report_fields() -> None:
    result = VisionOcrProbeResult(
        capability="qwen_vision",
        approved_model_id="approved",
        actual_model_id="actual",
        adapter_type="QwenVisionAdapter",
        status="passed",
        latency_ms=12,
        lock_id="lock-1",
        error_code=None,
        output_empty=False,
        known_text_present=True,
    )
    data = result.as_dict()
    assert data["capability"] == "qwen_vision"
    assert data["approved_model_id"] == "approved"
    assert data["adapter_type"] == "QwenVisionAdapter"
    assert data["lock_id"] == "lock-1"
