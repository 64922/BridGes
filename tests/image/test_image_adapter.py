"""图片生成适配器测试（Issue 31）。

用假 QwenApiClient 验证 submit/poll/fetch/cancel 四模式：固定模型标识、
编辑带 data URL 直传、云端状态映射（RUNNING/SUCCEEDED/FAILED）、结果
下载判型、缺失参数与供应商格式异常的错误分类。
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from bridges.ai.adapters import AdapterError
from bridges.ai.qwen_image_adapter import QwenImageAdapter, image_data_url
from bridges.contracts.ai import CapabilityKind, CapabilityRecord
from bridges.contracts.workflows import RunContextEnvelope

IMAGE_MODEL = "qwen-image-2.0-pro-2026-06-22"
_IMAGE_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


class _FakeClient:
    """记录调用的假 QwenApiClient（只实现适配器用到的两个方法）。"""

    def __init__(
        self,
        native_body: dict[str, Any] | None = None,
        task_body: dict[str, Any] | None = None,
        cancel_body: dict[str, Any] | None = None,
    ) -> None:
        self._native_body = native_body or {
            "output": {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": [{"image": "http://img.local/result.png"}],
                        }
                    }
                ]
            }
        }
        self._task_body = task_body or {
            "output": {"task_id": "cloud-1", "status": "SUCCEEDED"}
        }
        self._cancel_body = cancel_body or {"output": {"task_id": "cloud-1"}}
        self.native_calls: list[tuple[str, dict[str, Any], bool, float | None]] = []
        self.task_calls: list[str] = []
        self.cancel_calls: list[str] = []

    def dashscope_native(
        self,
        path: str,
        request_body: dict[str, Any],
        *,
        async_call: bool = False,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        self.native_calls.append((path, request_body, async_call, timeout))
        return self._native_body

    def dashscope_task_get(self, task_id: str) -> dict[str, Any]:
        self.task_calls.append(task_id)
        return self._task_body

    def dashscope_task_cancel(self, task_id: str) -> dict[str, Any]:
        self.cancel_calls.append(task_id)
        return self._cancel_body


def _capability() -> CapabilityRecord:
    return CapabilityRecord(
        name="qwen_image",
        version="1",
        kind=CapabilityKind.MODEL,
        vendor="qwen",
        region="cn-beijing",
        model_id=IMAGE_MODEL,
        input_schema_version="image-prompt-v1",
        output_schema_version="image-task-v1",
    )


def _run_context() -> RunContextEnvelope:
    from datetime import UTC, datetime

    return RunContextEnvelope(
        run_id="run-1",
        account_id="account-a",
        project_id="conv-1",
        workflow_name="image_generation",
        workflow_version="1",
        submitted_at=datetime.now(UTC),
    )


def test_submit_generation_returns_sync_result_url() -> None:
    client = _FakeClient()
    adapter = QwenImageAdapter(client)
    result = adapter.call(
        _capability(), _run_context(), {"kind": "submit", "prompt": "一座桥的素描"}
    )
    assert result.actual_model_id == IMAGE_MODEL
    assert result.output["cloud_task_id"] == ""
    assert result.output["result_url"] == "http://img.local/result.png"
    path, body, async_call, timeout = client.native_calls[0]
    assert path == "/api/v1/services/aigc/multimodal-generation/generation"
    assert async_call is False  # 同步服务不带 async 头
    assert timeout == 180.0  # 同步生成独立放宽超时
    assert body["model"] == IMAGE_MODEL
    message = body["input"]["messages"][0]
    assert message["role"] == "user"
    assert message["content"] == [{"text": "一座桥的素描"}]
    assert body["parameters"]["size"] == "1024*1024"
    assert body["parameters"]["n"] == 1


def test_submit_edit_embeds_source_as_data_url() -> None:
    client = _FakeClient()
    adapter = QwenImageAdapter(client)
    data_url = image_data_url(_IMAGE_BYTES, "image/png")
    result = adapter.call(
        _capability(),
        _run_context(),
        {
            "kind": "submit",
            "prompt": "把背景改为夜空",
            "base_image": data_url,
        },
    )
    assert result.output["result_url"] == "http://img.local/result.png"
    content = client.native_calls[0][1]["input"]["messages"][0]["content"]
    assert content[0]["image"] == data_url
    assert content[1]["text"] == "把背景改为夜空"
    assert data_url.startswith("data:image/png;base64,")


def test_submit_missing_prompt_rejected() -> None:
    client = _FakeClient()
    adapter = QwenImageAdapter(client)
    with pytest.raises(AdapterError) as exc:
        adapter.call(_capability(), _run_context(), {"kind": "submit", "prompt": "  "})
    assert exc.value.code == "missing_prompt"
    assert client.native_calls == []


def test_submit_rejects_response_without_image() -> None:
    client = _FakeClient(
        native_body={"output": {"choices": [{"message": {"role": "assistant", "content": [{"text": "拒绝"}]}}]}}
    )
    adapter = QwenImageAdapter(client)
    with pytest.raises(AdapterError) as exc:
        adapter.call(
            _capability(), _run_context(), {"kind": "submit", "prompt": "一座桥的素描"}
        )
    assert exc.value.code == "task_rejected"
    assert exc.value.retryable is True


def test_poll_maps_cloud_statuses() -> None:
    run = _run_context()
    capability = _capability()
    for cloud_status in ("PENDING", "RUNNING", "PROCESSING"):
        client = _FakeClient(
            task_body={"output": {"task_id": "cloud-1", "status": cloud_status}}
        )
        result = QwenImageAdapter(client).call(
            capability, run, {"kind": "poll", "cloud_task_id": "cloud-1"}
        )
        assert result.output["cloud_status"] == "RUNNING"
    client = _FakeClient(
        task_body={
            "output": {
                "task_id": "cloud-1",
                "status": "SUCCEEDED",
                "results": [{"url": "http://img.local/result.png"}],
            }
        }
    )
    result = QwenImageAdapter(client).call(
        capability, run, {"kind": "poll", "cloud_task_id": "cloud-1"}
    )
    assert result.output["cloud_status"] == "SUCCEEDED"
    assert result.output["result_url"] == "http://img.local/result.png"
    client = _FakeClient(
        task_body={"output": {"task_id": "cloud-1", "status": "FAILED", "message": "拒绝"}}
    )
    result = QwenImageAdapter(client).call(
        capability, run, {"kind": "poll", "cloud_task_id": "cloud-1"}
    )
    assert result.output["cloud_status"] == "FAILED"
    assert result.output["error_message"] == "拒绝"


def test_fetch_downloads_bytes_with_media_type() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "http://img.local/result.png"
        return httpx.Response(
            200,
            content=_IMAGE_BYTES,
            headers={"content-type": "image/png"},
        )

    adapter = QwenImageAdapter(
        _FakeClient(),
        download_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = adapter.call(
        _capability(),
        _run_context(),
        {"kind": "fetch", "result_url": "http://img.local/result.png"},
    )
    assert result.output["image_bytes"] == _IMAGE_BYTES
    assert result.output["media_type"] == "image/png"


def test_fetch_download_failure_classified_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    adapter = QwenImageAdapter(
        _FakeClient(),
        download_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(AdapterError) as exc:
        adapter.call(
            _capability(),
            _run_context(),
            {"kind": "fetch", "result_url": "http://img.local/result.png"},
        )
    assert exc.value.code == "download_failed"
    assert exc.value.retryable is True


def test_cancel_mode_calls_task_cancel() -> None:
    client = _FakeClient()
    adapter = QwenImageAdapter(client)
    result = adapter.call(
        _capability(),
        _run_context(),
        {"kind": "cancel", "cloud_task_id": "cloud-1"},
    )
    assert result.output["cancelled"] is True
    assert client.cancel_calls == ["cloud-1"]


def test_unknown_mode_rejected() -> None:
    adapter = QwenImageAdapter(_FakeClient())
    with pytest.raises(AdapterError) as exc:
        adapter.call(_capability(), _run_context(), {"kind": "explode"})
    assert exc.value.code == "unknown_mode"
