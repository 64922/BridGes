"""知识库 OCR 端口：固定请求边界与凭据降级。"""

from __future__ import annotations

import base64
from typing import Any

import pytest
from pydantic import SecretStr

from bridges.ai.adapters import AdapterResult
from bridges.ai.qwen_vision_adapters import QwenOcrAdapter
from bridges.ingestion.ocr import OCR_IMAGE_PROMPT, OcrError, QwenOcrPort


def test_qwen_ocr_port_sends_only_image_and_fixed_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_call(
        _adapter: QwenOcrAdapter,
        _capability: Any,
        _run_context: Any,
        payload: dict[str, Any],
    ) -> AdapterResult:
        calls.append(payload)
        return AdapterResult(
            actual_model_id="qwen-vl-ocr",
            output={"content": "图片中的可检索文字"},
        )

    monkeypatch.setattr(QwenOcrAdapter, "call", fake_call)
    port = QwenOcrPort(api_key=SecretStr("test-key"))

    result = port.extract("account-a", b"image-bytes", "image/png")

    assert result == "图片中的可检索文字"
    assert len(calls) == 1
    payload = calls[0]
    assert set(payload) == {
        "image_base64",
        "mime_type",
        "prompt",
        "temperature",
        "max_tokens",
    }
    assert payload["image_base64"] == base64.b64encode(b"image-bytes").decode("ascii")
    assert payload["mime_type"] == "image/png"
    assert payload["prompt"] == OCR_IMAGE_PROMPT
    assert payload["temperature"] == 0.01
    assert payload["max_tokens"] == 4096


def test_qwen_ocr_port_without_global_key_fails_honestly() -> None:
    port = QwenOcrPort(api_key=None)

    with pytest.raises(OcrError) as exc_info:
        port.extract("account-a", b"image-bytes", "image/png")

    assert "未配置全局百炼运行凭据" in str(exc_info.value)
