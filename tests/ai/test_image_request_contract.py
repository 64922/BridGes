"""图片请求参数合同（issue 04）。

背景：OCR 与视觉适配器曾各自硬编码 ``min_pixels=3072``，而实际生效模型
（``qwen3.7-plus-2026-05-26``）的下限是 65536——业务请求因此被
``InternalError.Algo.InvalidParameter: Parameter min_pixels must be greater than
or equal to 65536`` 以 400 拒绝；设置页的能力探测不带这些可选参数，于是出现
"探测通过、业务请求必然失败"的假通过。

本文件用 ``httpx.MockTransport`` 复刻该上游合同（仅测试，不发真实请求）：
只要请求带上低于下限的像素参数就被拒绝，两个适配器与探测载荷都必须通过。
"""

from __future__ import annotations

import base64
import json
import struct
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from bridges.ai import ModelGateway
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.ai.image_request import image_content_part, image_data_url
from bridges.ai.model_probe import PROBE_IMAGE_DATA_URL, ModelCapabilityProbe
from bridges.ai.qwen_client import QwenApiClient
from bridges.ai.qwen_vision_adapters import QwenOcrAdapter, QwenVisionAdapter
from bridges.contracts.ai import (
    CapabilityKind,
    CapabilityRecord,
    ModelCallStatus,
    ModelCapabilities,
)
from bridges.contracts.workflows import RunContextEnvelope

#: 实测下限：小于该值的 ``min_pixels`` 会被服务端以 400 拒绝。
VERIFIED_MIN_PIXELS_FLOOR = 65536
MODEL_ID = "qwen3.7-plus-2026-05-26"


def _context() -> RunContextEnvelope:
    return RunContextEnvelope(
        run_id="run-issue04",
        account_id="account-1",
        project_id="project-1",
        workflow_name="wf",
        workflow_version="1",
        submitted_at=datetime.now(UTC),
    )


def _capability(name: str) -> CapabilityRecord:
    return CapabilityRecord(
        name=name,
        version="1",
        kind=CapabilityKind.MODEL,
        vendor="qwen",
        region="cn-beijing",
        model_id=MODEL_ID,
        input_schema_version="image-v1",
        output_schema_version="text-v1",
        supported_modalities=["text", "image"],
    )


def _success_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-issue04",
            "model": MODEL_ID,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "识别结果"},
                    "finish_reason": "stop",
                }
            ],
        },
    )


def _rejecting_upstream(captured: list[dict[str, Any]]) -> httpx.MockTransport:
    """复刻实测上游合同：``min_pixels`` 低于下限即 400。"""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured.append(body)
        for message in body.get("messages", []):
            for part in message.get("content", []):
                if not isinstance(part, dict) or "min_pixels" not in part:
                    continue
                if part["min_pixels"] < VERIFIED_MIN_PIXELS_FLOOR:
                    return httpx.Response(
                        400,
                        json={
                            "error": {
                                "message": (
                                    "<400> InternalError.Algo.InvalidParameter: "
                                    "Parameter min_pixels must be greater than or "
                                    "equal to 65536"
                                )
                            }
                        },
                    )
        return _success_response()

    return httpx.MockTransport(handler)


def _gateway_with(adapter: Any, name: str) -> ModelGateway:
    registry = CapabilityRegistry()
    registry.register(_capability(name))
    gateway = ModelGateway(registry)
    gateway.register_adapter(name, "1", adapter)
    return gateway


def _client(captured: list[dict[str, Any]]) -> QwenApiClient:
    client = QwenApiClient(api_key=None, workspace_id=None, region="cn-beijing")
    client._client = httpx.Client(transport=_rejecting_upstream(captured))
    return client


# ---------------------------------------------------------------------------
# 参数不兼容场景：修复前失败、修复后通过
# ---------------------------------------------------------------------------

def test_ocr_adapter_passes_verified_parameter_contract() -> None:
    captured: list[dict[str, Any]] = []
    gateway = _gateway_with(QwenOcrAdapter(_client(captured)), "qwen_ocr")

    result = gateway.invoke(
        "qwen_ocr", "1", _context(), payload={"image_base64": "aGVsbG8="}
    )

    assert result.status == ModelCallStatus.SUCCESS, (
        result.error_code,
        result.error_message,
    )
    part = captured[0]["messages"][0]["content"][0]
    # 修复前这里带 min_pixels=3072，被上游以 400 拒绝（同 study.recognize 的
    # client_error_400）；现在不再发明可选参数，由服务端按其默认值处理。
    assert "min_pixels" not in part
    assert "max_pixels" not in part
    assert part["type"] == "image_url"
    assert part["image_url"]["url"] == "data:image/png;base64,aGVsbG8="


def test_vision_adapter_passes_verified_parameter_contract() -> None:
    captured: list[dict[str, Any]] = []
    gateway = _gateway_with(QwenVisionAdapter(_client(captured)), "qwen_vision")

    result = gateway.invoke(
        "qwen_vision",
        "1",
        _context(),
        payload={"image_base64": "aGVsbG8=", "mime_type": "image/jpeg"},
    )

    assert result.status == ModelCallStatus.SUCCESS, (
        result.error_code,
        result.error_message,
    )
    part = captured[0]["messages"][0]["content"][0]
    assert "min_pixels" not in part
    assert "max_pixels" not in part
    assert part["image_url"]["url"] == "data:image/jpeg;base64,aGVsbG8="


def test_callers_can_still_send_explicit_pixel_parameters() -> None:
    """显式给出有效像素参数时照原样透传（不为修复删掉调用方能力）。"""
    captured: list[dict[str, Any]] = []
    gateway = _gateway_with(QwenVisionAdapter(_client(captured)), "qwen_vision")

    result = gateway.invoke(
        "qwen_vision",
        "1",
        _context(),
        payload={"image_base64": "aGVsbG8=", "min_pixels": VERIFIED_MIN_PIXELS_FLOOR},
    )

    assert result.status == ModelCallStatus.SUCCESS
    part = captured[0]["messages"][0]["content"][0]
    assert part["min_pixels"] == VERIFIED_MIN_PIXELS_FLOOR
    assert "max_pixels" not in part


@pytest.mark.parametrize(
    ("name", "adapter_type"),
    [("qwen_ocr", QwenOcrAdapter), ("qwen_vision", QwenVisionAdapter)],
)
def test_pre_fix_parameters_fail_through_both_adapters(
    name: str, adapter_type: type
) -> None:
    """修复前的参数形态经两个适配器都必然 400——通过不是空断言。

    旧实现把 ``min_pixels=3072, max_pixels=8388608`` 当默认值拆进请求；这里
    显式按旧默认值调用同一个上游合同，确认拒绝分支确实会触发（修复后之所以
    通过，是因为不再发明这两个可选参数），OCR 与视觉两条路径都覆盖。
    """
    captured: list[dict[str, Any]] = []
    gateway = _gateway_with(adapter_type(_client(captured)), name)

    result = gateway.invoke(
        name,
        "1",
        _context(),
        payload={
            "image_base64": "aGVsbG8=",
            "min_pixels": 3072,
            "max_pixels": 8388608,
        },
    )

    assert result.status == ModelCallStatus.BLOCKED, result.status
    assert result.error_code == "client_error_400"
    part = captured[0]["messages"][0]["content"][0]
    assert part["min_pixels"] == 3072


def test_knowledge_base_ocr_and_vision_share_the_same_construction() -> None:
    """共用适配器的请求构造同源：两个能力发出的图片块完全一致。"""
    captured: list[dict[str, Any]] = []
    for name, adapter in (
        ("qwen_ocr", QwenOcrAdapter(_client(captured))),
        ("qwen_vision", QwenVisionAdapter(_client(captured))),
    ):
        captured.clear()
        _gateway_with(adapter, name).invoke(
            name, "1", _context(), payload={"image_base64": "aGVsbG8="}
        )
        assert captured[0]["messages"][0]["content"][0] == image_content_part(
            image_data_url("aGVsbG8=", None)
        )


# ---------------------------------------------------------------------------
# 能力探测：与业务请求同形，不再假通过
# ---------------------------------------------------------------------------

def test_model_probe_image_payload_matches_business_request_shape() -> None:
    """探测图片载荷与业务图片调用经同一构造，参数形态不可能再分叉。"""
    payload = ModelCapabilityProbe._image_payload(MODEL_ID)

    assert payload["messages"][0]["content"][0] == image_content_part(
        PROBE_IMAGE_DATA_URL
    )


def test_model_probe_image_meets_verified_dimension_floor() -> None:
    """探测图必须满足模型尺寸合同。

    旧的 8×8 图被实测拒绝（``height:8 or width:8 must be larger than 10``），
    会把图片能力误判为不可用。
    """
    encoded = PROBE_IMAGE_DATA_URL.split(",", 1)[1]
    png = base64.b64decode(encoded)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", png[16:24])
    assert width > 10 and height > 10, (width, height)


def test_model_probe_runs_through_the_shared_construction() -> None:
    """探测走真实 HTTP 路径时，图片块与适配器形态一致（含省略可选参数）。"""
    captured: list[dict[str, Any]] = []
    probe = ModelCapabilityProbe(_client(captured))

    outcomes = probe.run(
        model_id=MODEL_ID,
        capabilities=ModelCapabilities(text=False, image=True),
    )

    assert [outcome.capability for outcome in outcomes] == ["image"]
    assert outcomes[0].ok is True, outcomes[0].message
    part = captured[0]["messages"][0]["content"][0]
    assert part == image_content_part(PROBE_IMAGE_DATA_URL)


def test_image_adapters_honor_the_call_timeout_seam() -> None:
    """整页图片调用需要比默认 60 秒更长的窗口：适配器透传保留超时键。

    实测原始教材整页：OCR 30—44 秒、视觉 45—59 秒，贴着默认超时上限
    （issue 04），因此适配器必须与文本适配器一样支持按调用方给出的超时
    覆盖默认值。
    """
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["timeout"] = request.extensions.get("timeout")
        return _success_response()

    for name, adapter in (
        ("qwen_ocr", QwenOcrAdapter),
        ("qwen_vision", QwenVisionAdapter),
    ):
        client = QwenApiClient(api_key=None, workspace_id=None, region="cn-beijing")
        client._client = httpx.Client(transport=httpx.MockTransport(handler))
        gateway = _gateway_with(adapter(client), name)
        result = gateway.invoke(
            name,
            "1",
            _context(),
            payload={"image_base64": "aGVsbG8=", "request_timeout_seconds": 180.0},
        )
        assert result.status == ModelCallStatus.SUCCESS
        assert captured["timeout"]["read"] == 180.0, name
