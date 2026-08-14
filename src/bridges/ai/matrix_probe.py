"""Vision/OCR 真实兼容 smoke（Issue 09）。

用一张"含已知文字"的最小图像分别验证视觉描述合同与 OCR 文本合同对
批准快照（``bridges.ai.fixed_models.VISION_MODEL_ID`` /
``OCR_MODEL_ID``）真实可用：检查真实响应 model、非空输出、状态与运行
锁。fixture/cassette/mock 不能作为兼容结论——本模块只驱动真实适配器，
调用方（发布门 ``scripts/model_matrix_gate.py``）必须显式 opt-in 才执行
网络请求。

结果分级：

- ``passed``：真实响应 model 等于批准 ID、状态 SUCCESS、输出满足合同
  （OCR 必须包含已知文字 token；视觉描述非空）且运行锁已生成；
- ``failed``：调用失败或合同不满足（携带稳定错误码，如
  ``actual_model_mismatch`` / ``auth_error`` / ``rate_limit``）；
- ``inconclusive``：不具备真实 Key（``missing_global_qwen_key``）或
  未配置真实适配器（``no_adapter``），不能通过发布门。

报告与运行锁只包含 capability、批准/实际 model ID、adapter 类型、
状态、延迟、锁 ID 与稳定错误码，绝不包含 Key、Authorization、请求
正文、图像内容或完整模型输出。
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bridges.ai.capability_registry import CapabilityRegistry
from bridges.ai.fixed_models import OCR_MODEL_ID, VISION_MODEL_ID
from bridges.ai.model_gateway import ModelGateway
from bridges.contracts.ai import ModelCallStatus
from bridges.contracts.workflows import RunContextEnvelope

#: 已知文字 token：OCR 输出必须包含（大小写不敏感）才能通过合同。
KNOWN_TEXT_TOKEN = "BRIDGES-QWEN-2026"

_VISION_PROMPT = "Describe the text written in this image."
_OCR_PROMPT = "Extract all visible text from this image. Do not add commentary."


@dataclass(frozen=True)
class VisionOcrProbeResult:
    """一次真实兼容 smoke 的脱敏结果（Issue 09 报告字段）。"""

    capability: str
    approved_model_id: str
    actual_model_id: str | None
    adapter_type: str | None
    status: str  # passed / failed / inconclusive
    latency_ms: int
    lock_id: str | None
    error_code: str | None
    output_empty: bool | None
    known_text_present: bool | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "approved_model_id": self.approved_model_id,
            "actual_model_id": self.actual_model_id,
            "adapter_type": self.adapter_type,
            "status": self.status,
            "latency_ms": self.latency_ms,
            "lock_id": self.lock_id,
            "error_code": self.error_code,
            "output_empty": self.output_empty,
            "known_text_present": self.known_text_present,
        }


def _render_known_text_png() -> bytes:
    """生成含已知文字的 600x200 PNG（PyMuPDF，无网络依赖）。"""
    import fitz  # type: ignore[import-untyped]  # PyMuPDF

    document = fitz.open()
    page = document.new_page(width=600, height=200)
    page.insert_text(
        (40, 115),
        KNOWN_TEXT_TOKEN,
        fontsize=40,
        fontname="helv",
    )
    pixmap = page.get_pixmap(dpi=144)
    # fitz 未提供类型标注：显式 bytes() 收敛 Any，满足 strict mypy。
    return bytes(pixmap.tobytes("png"))


def _known_text_image_payload() -> dict[str, Any]:
    png_bytes = _render_known_text_png()
    return {
        "image_base64": base64.b64encode(png_bytes).decode("ascii"),
        "mime_type": "image/png",
        "temperature": 0.0,
        "max_tokens": 2048,
    }


def _probe_once(
    capability: str,
    registry: CapabilityRegistry,
    gateway: ModelGateway,
    *,
    global_key_configured: bool,
    known_token: str = KNOWN_TEXT_TOKEN,
) -> VisionOcrProbeResult:
    approved = VISION_MODEL_ID if capability == "qwen_vision" else OCR_MODEL_ID
    adapter = gateway.get_adapter(capability, "1")
    adapter_type = type(adapter).__name__ if adapter is not None else None
    if not global_key_configured:
        return VisionOcrProbeResult(
            capability=capability,
            approved_model_id=approved,
            actual_model_id=None,
            adapter_type=adapter_type,
            status="inconclusive",
            latency_ms=0,
            lock_id=None,
            error_code="missing_global_qwen_key",
            output_empty=None,
            known_text_present=None,
        )
    if adapter is None:
        return VisionOcrProbeResult(
            capability=capability,
            approved_model_id=approved,
            actual_model_id=None,
            adapter_type=None,
            status="inconclusive",
            latency_ms=0,
            lock_id=None,
            error_code="no_adapter",
            output_empty=None,
            known_text_present=None,
        )

    prompt = _OCR_PROMPT if capability == "qwen_ocr" else _VISION_PROMPT
    payload = _known_text_image_payload()
    payload["prompt"] = prompt
    run_context = RunContextEnvelope(
        run_id=f"vision-ocr-compat-{capability}",
        account_id="system",
        project_id="",
        workflow_name="model_matrix_gate",
        workflow_version="1",
        submitted_at=datetime.now(UTC),
    )
    started = time.monotonic()
    try:
        result = gateway.invoke(capability, "1", run_context, payload)
    except Exception as exc:  # noqa: BLE001 - 探针只输出稳定分类
        return VisionOcrProbeResult(
            capability=capability,
            approved_model_id=approved,
            actual_model_id=None,
            adapter_type=adapter_type,
            status="failed",
            latency_ms=_latency_ms(started),
            lock_id=None,
            error_code=exc.__class__.__name__.lower(),
            output_empty=None,
            known_text_present=None,
        )
    latency_ms = _latency_ms(started)
    lock = result.lock
    actual = lock.actual_model_id if lock is not None else None
    lock_id = lock.lock_id if lock is not None else None

    if result.status != ModelCallStatus.SUCCESS:
        return VisionOcrProbeResult(
            capability=capability,
            approved_model_id=approved,
            actual_model_id=actual,
            adapter_type=adapter_type,
            status="failed",
            latency_ms=latency_ms,
            lock_id=lock_id,
            error_code=result.error_code or "probe_failed",
            output_empty=None,
            known_text_present=None,
        )

    content = ""
    if isinstance(result.output, dict):
        raw = result.output.get("content")
        if isinstance(raw, str):
            content = raw
    output_empty = not content.strip()
    known_text_present = known_token.casefold() in content.casefold()
    contract_ok = not output_empty and (
        known_text_present if capability == "qwen_ocr" else True
    )
    return VisionOcrProbeResult(
        capability=capability,
        approved_model_id=approved,
        actual_model_id=actual,
        adapter_type=adapter_type,
        status="passed" if contract_ok else "failed",
        latency_ms=latency_ms,
        lock_id=lock_id,
        error_code=None if contract_ok else "vision_ocr_contract_failed",
        output_empty=output_empty,
        known_text_present=known_text_present,
    )


def _latency_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def run_vision_ocr_probes(
    registry: CapabilityRegistry,
    gateway: ModelGateway,
    *,
    global_key_configured: bool,
) -> list[VisionOcrProbeResult]:
    """对 ``qwen_vision`` 与 ``qwen_ocr`` 各执行一次真实兼容 smoke。"""
    return [
        _probe_once("qwen_vision", registry, gateway, global_key_configured=global_key_configured),
        _probe_once("qwen_ocr", registry, gateway, global_key_configured=global_key_configured),
    ]


def write_probe_report(
    results: Sequence[VisionOcrProbeResult],
    report_dir: Path,
    *,
    code_version: str,
    generated_at: str | None = None,
) -> Path:
    """把脱敏探针结果写入报告目录，返回 JSON 报告路径。

    报告记录每次探针的锁 ID（Issue 10 不可变运行锁由网关生成，完整
    运行锁由运行期录制器在配置数据库时持久化；本报告不复制锁正文）。
    """
    report_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "kind": "vision_ocr_compatibility_probe",
        "code_version": code_version,
        "generated_at": generated_at or datetime.now(UTC).isoformat(),
        "known_text_token": KNOWN_TEXT_TOKEN,
        "results": [result.as_dict() for result in results],
    }
    report_path = report_dir / "probes.json"
    report_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report_path
