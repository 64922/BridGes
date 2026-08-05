"""QwenWanAdapter 适配器单元测试（Issue 32）。

覆盖 submit/poll/fetch/cancel 四模式：固定模型与尺寸、提示词校验、
同步结果兜底、云端状态字段兼容（status/task_status）、结果地址提取
（video_url 优先、results 兜底）、下载判型、失败分类与固定模型标识。
"""

from __future__ import annotations

import httpx

from bridges.ai.qwen_wan_adapter import (
    DEFAULT_VIDEO_SIZE,
    SUPPORTED_RESULT_MEDIA_TYPES,
    QwenWanAdapter,
)
from bridges.contracts.ai import CapabilityKind, CapabilityRecord

#: 固定视频模型快照（ADR-0007：Wan 是矩阵唯一非 Qwen 系列例外）。
VIDEO_MODEL = "wan2.7-t2v-2026-06-12"


class _FakeClient:
    """内存假客户端，录下 dashscope 调用痕迹。"""

    def __init__(
        self,
        native_body: dict | None = None,
        task_body: dict | None = None,
        cancel_body: dict | None = None,
    ) -> None:
        self._native_body = native_body or {"output": {"task_id": "cloud-1"}}
        self._task_body = task_body or {
            "output": {"task_id": "cloud-1", "status": "SUCCEEDED"}
        }
        self._cancel_body = cancel_body or {"output": {"task_id": "cloud-1"}}
        self.native_calls: list[tuple[str, dict]] = []
        self.task_calls: list[str] = []
        self.cancel_calls: list[str] = []

    def dashscope_native(self, path: str, body: dict) -> dict:
        self.native_calls.append((path, body))
        return self._native_body

    def dashscope_task_get(self, task_id: str) -> dict:
        self.task_calls.append(task_id)
        return self._task_body

    def dashscope_task_cancel(self, task_id: str) -> dict:
        self.cancel_calls.append(task_id)
        return self._cancel_body


def _capability() -> CapabilityRecord:
    return CapabilityRecord(
        name="qwen_wan",
        version="1",
        kind=CapabilityKind.MODEL,
        vendor="wan",
        region="cn-beijing",
        model_id=VIDEO_MODEL,
        input_schema_version="video-prompt-v1",
        output_schema_version="video-task-v1",
    )


def _run_context() -> dict:
    return {}


def test_submit_returns_cloud_task_id_with_fixed_model_and_size() -> None:
    client = _FakeClient()
    adapter = QwenWanAdapter(client)
    result = adapter.call(_capability(), _run_context(), {"prompt": "一条静谧的河"})
    assert result.output == {"cloud_task_id": "cloud-1"}
    assert result.actual_model_id == VIDEO_MODEL
    path, body = client.native_calls[0]
    assert path == "/api/v1/services/aigc/video-generation/video-synthesis"
    assert body["model"] == VIDEO_MODEL
    assert body["input"]["prompt"] == "一条静谧的河"
    assert body["parameters"]["size"] == DEFAULT_VIDEO_SIZE


def test_submit_missing_prompt_rejected_without_network_call() -> None:
    client = _FakeClient()
    adapter = QwenWanAdapter(client)
    try:
        adapter.call(_capability(), _run_context(), {"prompt": "  "})
    except Exception as exc:
        assert getattr(exc, "code", None) == "missing_prompt"
    else:
        raise AssertionError("空提示词应被拒绝")
    assert client.native_calls == []


def test_submit_sync_result_url_fallback() -> None:
    client = _FakeClient(
        native_body={"output": {"video_url": "http://video.local/a.mp4"}}
    )
    adapter = QwenWanAdapter(client)
    result = adapter.call(_capability(), _run_context(), {"prompt": "一条静谧的河"})
    assert result.output == {"cloud_task_id": "", "result_url": "http://video.local/a.mp4"}


def test_submit_task_rejected_when_no_task_id() -> None:
    client = _FakeClient(native_body={"output": {}})
    adapter = QwenWanAdapter(client)
    try:
        adapter.call(_capability(), _run_context(), {"prompt": "一条静谧的河"})
    except Exception as exc:
        assert getattr(exc, "code", None) == "task_rejected"
        assert getattr(exc, "retryable", False) is True
    else:
        raise AssertionError("无 task_id 应报 task_rejected")


def test_poll_maps_cloud_statuses() -> None:
    cases: list[tuple[dict, str]] = [
        ({"status": "PENDING"}, "RUNNING"),
        ({"status": "RUNNING"}, "RUNNING"),
        ({"status": "PROCESSING"}, "RUNNING"),
        ({"status": "SUCCEEDED", "video_url": "http://v/a.mp4"}, "SUCCEEDED"),
        ({"task_status": "SUCCEEDED", "video_url": "http://v/a.mp4"}, "SUCCEEDED"),
        ({"status": "FAILED", "message": "内容违规"}, "FAILED"),
    ]
    for output, expected in cases:
        client = _FakeClient(task_body={"output": output})
        adapter = QwenWanAdapter(client)
        result = adapter.call(
            _capability(), _run_context(), {"kind": "poll", "cloud_task_id": "c1"}
        )
        assert result.output.get("cloud_status") == expected, output


def test_poll_succeeded_prefers_video_url_over_results() -> None:
    client = _FakeClient(
        task_body={
            "output": {
                "status": "SUCCEEDED",
                "video_url": "http://v/direct.mp4",
                "results": [{"url": "http://v/nested.mp4"}],
            }
        }
    )
    adapter = QwenWanAdapter(client)
    result = adapter.call(_capability(), _run_context(), {"kind": "poll", "cloud_task_id": "c1"})
    assert result.output["result_url"] == "http://v/direct.mp4"


def test_poll_succeeded_falls_back_to_results_url() -> None:
    client = _FakeClient(
        task_body={
            "output": {"status": "SUCCEEDED", "results": [{"url": "http://v/n.mp4"}]}
        }
    )
    adapter = QwenWanAdapter(client)
    result = adapter.call(_capability(), _run_context(), {"kind": "poll", "cloud_task_id": "c1"})
    assert result.output["result_url"] == "http://v/n.mp4"


def test_poll_succeeded_without_result_url_is_empty_result() -> None:
    client = _FakeClient(task_body={"output": {"status": "SUCCEEDED"}})
    adapter = QwenWanAdapter(client)
    try:
        adapter.call(_capability(), _run_context(), {"kind": "poll", "cloud_task_id": "c1"})
    except Exception as exc:
        assert getattr(exc, "code", None) == "empty_result"
        assert getattr(exc, "retryable", False) is False
    else:
        raise AssertionError("完成但没有视频应报 empty_result")


def test_poll_missing_cloud_task_id_rejected() -> None:
    adapter = QwenWanAdapter(_FakeClient())
    try:
        adapter.call(_capability(), _run_context(), {"kind": "poll"})
    except Exception as exc:
        assert getattr(exc, "code", None) == "missing_cloud_task_id"
    else:
        raise AssertionError("缺少云端任务标识应被拒绝")


def test_fetch_downloads_bytes_with_media_type() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"mp4-bytes", headers={"content-type": "video/mp4"})

    client = _FakeClient()
    adapter = QwenWanAdapter(
        client, download_client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    result = adapter.call(
        _capability(), _run_context(), {"kind": "fetch", "result_url": "http://v/a.mp4"}
    )
    assert result.output["video_bytes"] == b"mp4-bytes"
    assert result.output["media_type"] == "video/mp4"
    assert result.output["media_type"] in SUPPORTED_RESULT_MEDIA_TYPES


def test_fetch_guesses_media_type_from_url_when_header_missing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x")

    client = _FakeClient()
    adapter = QwenWanAdapter(
        client, download_client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    result = adapter.call(
        _capability(), _run_context(), {"kind": "fetch", "result_url": "http://v/a.mov"}
    )
    assert result.output["media_type"] == "video/quicktime"


def test_fetch_download_failure_classified_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable")

    client = _FakeClient()
    adapter = QwenWanAdapter(
        client, download_client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    try:
        adapter.call(_capability(), _run_context(), {"kind": "fetch", "result_url": "http://v/a.mp4"})
    except Exception as exc:
        assert getattr(exc, "code", None) == "download_failed"
        assert getattr(exc, "retryable", False) is True
    else:
        raise AssertionError("下载失败应被分类为可重试")


def test_cancel_mode_calls_task_cancel() -> None:
    client = _FakeClient()
    adapter = QwenWanAdapter(client)
    result = adapter.call(_capability(), _run_context(), {"kind": "cancel", "cloud_task_id": "c1"})
    assert client.cancel_calls == ["c1"]
    assert result.output == {"cancelled": True}


def test_unknown_mode_rejected() -> None:
    adapter = QwenWanAdapter(_FakeClient())
    try:
        adapter.call(_capability(), _run_context(), {"kind": "weird"})
    except Exception as exc:
        assert getattr(exc, "code", None) == "unknown_mode"
    else:
        raise AssertionError("未知模式应被拒绝")
