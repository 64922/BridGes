"""固定能力矩阵真实探测单元测试。

真实执行器（RealCapabilityProbeRunner）用假客户端验证六能力的请求构造与
响应判定（无网络）；探测服务验证状态机（未探测→探测中→可用/不可用）、
同绑定重试、单项失败不拖累他项、不可变记录与持久化。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import SecretStr

from bridges.ai.adapters import AdapterError, AuthError, TransientError
from bridges.contracts.credentials import ProbeStatus
from bridges.credentials.matrix import (
    ASR_MODEL_ID,
    CHAT_MODEL_ID,
    EMBEDDING_MODEL_ID,
    FIXED_CAPABILITY_MATRIX,
    IMAGE_MODEL_ID,
    TTS_MODEL_ID,
    VIDEO_MODEL_ID,
    get_binding,
)
from bridges.credentials.probes import (
    CapabilityProbeService,
    ProbeError,
    ProbeOutcome,
    ProbeRunnerPort,
    RealCapabilityProbeRunner,
)


class _FakeStateStore:
    """满足 StateStore 协议的最小内存实现。"""

    def __init__(self) -> None:
        self._state: dict[str, dict[str, Any]] = {}

    def load(self, namespace: str) -> dict[str, Any] | None:
        return self._state.get(namespace)

    def save(self, namespace: str, state: dict[str, Any]) -> None:
        self._state[namespace] = state


class _ScriptedRunner(ProbeRunnerPort):
    """按能力脚本化成功/失败/异常的探测替身。"""

    def __init__(
        self,
        *,
        failures: dict[str, str] | None = None,
        exceptions: dict[str, Exception] | None = None,
    ) -> None:
        self.failures = failures or {}
        self.exceptions = exceptions or {}
        self.calls: list[str] = []

    def probe(self, binding, client) -> ProbeOutcome:
        self.calls.append(binding.capability_id)
        if binding.capability_id in self.exceptions:
            raise self.exceptions[binding.capability_id]
        if binding.capability_id in self.failures:
            raise ProbeError(self.failures[binding.capability_id], code="scripted")
        return ProbeOutcome(success=True, message="探测成功。")


def _service(runner: ProbeRunnerPort | None = None, state_store=None) -> CapabilityProbeService:
    return CapabilityProbeService(
        state_store=state_store,
        runner=runner or _ScriptedRunner(),
        region="cn-beijing",
    )


def test_run_all_marks_probing_then_available_and_records_binding() -> None:
    runner = _ScriptedRunner()
    service = _service(runner)
    key = SecretStr("sk-test-123456")

    records = service.run_all("alice", key)
    assert [r.capability_id for r in records] == [
        b.capability_id for b in FIXED_CAPABILITY_MATRIX
    ]
    assert all(r.status == ProbeStatus.AVAILABLE for r in records)
    # 不可变记录：模型/区域/参数与固定矩阵一致，probe_id 为追踪标识。
    by_id = {r.capability_id: r for r in records}
    assert by_id["chat"].model_id == CHAT_MODEL_ID
    assert by_id["chat"].region == "cn-beijing"
    assert by_id["chat"].parameters["max_tokens"] == 8
    assert by_id["chat"].probe_id
    assert by_id["embedding"].model_id == EMBEDDING_MODEL_ID
    assert by_id["asr"].model_id == ASR_MODEL_ID
    assert by_id["tts"].model_id == TTS_MODEL_ID
    assert by_id["image"].model_id == IMAGE_MODEL_ID
    assert by_id["video"].model_id == VIDEO_MODEL_ID

    snapshot = service.status_snapshot("alice")
    assert all(c.status == ProbeStatus.AVAILABLE for c in snapshot)
    assert all(c.can_retry is False for c in snapshot)


def test_failure_only_disables_that_capability() -> None:
    service = _service(_ScriptedRunner(failures={"asr": "语音转写探测失败。"}))
    records = service.run_all("alice", SecretStr("sk-test-123456"))

    by_id = {r.capability_id: r for r in records}
    assert by_id["asr"].status == ProbeStatus.UNAVAILABLE
    assert by_id["asr"].error_message == "语音转写探测失败。"
    assert by_id["chat"].status == ProbeStatus.AVAILABLE

    snapshot = {c.capability_id: c for c in service.status_snapshot("alice")}
    assert snapshot["asr"].status == ProbeStatus.UNAVAILABLE
    assert snapshot["asr"].can_retry is True
    assert snapshot["chat"].status == ProbeStatus.AVAILABLE


def test_probe_retries_same_binding_on_transient_then_succeeds() -> None:
    class _FlakyRunner(ProbeRunnerPort):
        def __init__(self) -> None:
            self.calls = 0

        def probe(self, binding, client) -> ProbeOutcome:
            self.calls += 1
            if self.calls == 1:
                raise TransientError("network hiccup")
            return ProbeOutcome(success=True, message="探测成功。")

    runner = _FlakyRunner()
    service = _service(runner)
    record = service.run_single("alice", "chat", SecretStr("sk-test-123456"))
    assert record.status == ProbeStatus.AVAILABLE
    assert record.attempt_count == 2
    # 重试保持同一绑定。
    assert record.model_id == CHAT_MODEL_ID


def test_auth_error_is_immediate_failure_with_chinese_reason() -> None:
    service = _service(
        _ScriptedRunner(exceptions={"tts": AuthError("bad key")})
    )
    record = service.run_single("alice", "tts", SecretStr("sk-test-123456"))
    assert record.status == ProbeStatus.UNAVAILABLE
    assert record.attempt_count == 1  # 认证失败不重试
    assert "凭据无效" in (record.error_message or "")


def test_stale_probing_falls_back_to_not_probed() -> None:
    runner = _ScriptedRunner()
    service = _service(runner)
    service.mark_probing("alice")
    # 手动把探测中的记录时间改到 10 分钟前，模拟进程中断。
    service._records["alice"]["chat"].probed_at = datetime.now(UTC) - timedelta(
        minutes=11
    )

    snapshot = {c.capability_id: c for c in service.status_snapshot("alice")}
    assert snapshot["chat"].status == ProbeStatus.NOT_PROBED
    assert "中断" in (snapshot["chat"].message or "")
    assert snapshot["chat"].can_retry is True
    # 未过期的探测中状态保持原样。
    service._records["alice"]["chat"].probed_at = datetime.now(UTC)
    assert service.status_snapshot("alice")[0].status == ProbeStatus.PROBING


def test_probe_records_persist_across_service_instances() -> None:
    state_store = _FakeStateStore()
    service = _service(_ScriptedRunner(), state_store=state_store)
    service.run_all("alice", SecretStr("sk-test-123456"))

    reopened = _service(_ScriptedRunner(), state_store=state_store)
    snapshot = {c.capability_id: c for c in reopened.status_snapshot("alice")}
    assert all(c.status == ProbeStatus.AVAILABLE for c in snapshot.values())


def test_reset_clears_records() -> None:
    service = _service()
    service.run_all("alice", SecretStr("sk-test-123456"))
    service.reset("alice")
    snapshot = service.status_snapshot("alice")
    assert all(c.status == ProbeStatus.NOT_PROBED for c in snapshot)


def test_unknown_capability_retry_raises() -> None:
    service = _service()
    with pytest.raises(KeyError):
        service.run_single("alice", "unknown", SecretStr("sk-test-123456"))


# ---------------------------------------------------------------------------
# 真实执行器：假客户端验证六能力的请求构造与响应判定
# ---------------------------------------------------------------------------


class _FakeClient:
    """记录请求并返回脚本化响应的 QwenApiClient 替身。"""

    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def chat_completions(self, body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("chat", body))
        return self._respond("chat")

    def embeddings(self, body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("embeddings", body))
        return self._respond("embeddings")

    def text_to_speech(self, body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("tts", body))
        return self._respond("tts")

    def dashscope_native(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((path, body))
        return self._respond(path)

    def _respond(self, key: str) -> dict[str, Any]:
        response = self.responses.get(key)
        if isinstance(response, Exception):
            raise response
        return dict(response)


def _chat_response() -> dict[str, Any]:
    return {
        "choices": [{"message": {"role": "assistant", "content": "测试"}}],
        "usage": {"total_tokens": 3},
    }


def _embedding_response(dimensions: int = 1024) -> dict[str, Any]:
    return {"data": [{"embedding": [0.1] * dimensions}]}


def _tts_response() -> dict[str, Any]:
    return {
        "output": {
            "audio": {"url": "https://example.invalid/a.wav", "id": "a1"}
        },
        "usage": {"characters": 2},
    }


def _image_task_response() -> dict[str, Any]:
    return {"output": {"task_id": "task-image-1", "task_status": "PENDING"}}


def _video_task_response() -> dict[str, Any]:
    return {"output": {"task_id": "task-video-1", "task_status": "PENDING"}}


def test_real_runner_chat_probe() -> None:
    client = _FakeClient({"chat": _chat_response()})
    outcome = RealCapabilityProbeRunner().probe(get_binding("chat"), client)  # type: ignore[arg-type]
    assert outcome.success
    body = client.calls[0][1]
    assert body["model"] == CHAT_MODEL_ID
    # 非用户固定数据，最小化生成。
    assert body["messages"][0]["content"] == "测试"
    assert body["max_tokens"] == 8


def test_real_runner_embedding_probe_checks_dimension() -> None:
    client = _FakeClient({"embeddings": _embedding_response(1024)})
    outcome = RealCapabilityProbeRunner().probe(get_binding("embedding"), client)  # type: ignore[arg-type]
    assert outcome.success
    assert client.calls[0][1]["model"] == EMBEDDING_MODEL_ID

    wrong_dim = _FakeClient({"embeddings": _embedding_response(512)})
    with pytest.raises(ProbeError) as exc_info:
        RealCapabilityProbeRunner().probe(get_binding("embedding"), wrong_dim)  # type: ignore[arg-type]
    assert "1024" in exc_info.value.message


def test_real_runner_asr_probe_sends_wav() -> None:
    client = _FakeClient({"chat": _chat_response()})
    outcome = RealCapabilityProbeRunner().probe(get_binding("asr"), client)  # type: ignore[arg-type]
    assert outcome.success
    body = client.calls[0][1]
    assert body["model"] == ASR_MODEL_ID
    content = body["messages"][0]["content"]
    audio_part = next(part for part in content if part.get("type") == "audio_url")
    assert audio_part["audio_url"]["url"].startswith("data:audio/wav;base64,")


def test_real_runner_tts_probe_requires_audio_url() -> None:
    client = _FakeClient({"tts": _tts_response()})
    outcome = RealCapabilityProbeRunner().probe(get_binding("tts"), client)  # type: ignore[arg-type]
    assert outcome.success
    assert client.calls[0][1]["model"] == TTS_MODEL_ID

    empty = _FakeClient({"tts": {"output": {"audio": {}}}})
    with pytest.raises(AdapterError):
        RealCapabilityProbeRunner().probe(get_binding("tts"), empty)  # type: ignore[arg-type]


def test_real_runner_image_probe_accepts_task() -> None:
    client = _FakeClient(
        {
            "/api/v1/services/aigc/text2image/image-synthesis": _image_task_response()
        }
    )
    outcome = RealCapabilityProbeRunner().probe(get_binding("image"), client)  # type: ignore[arg-type]
    assert outcome.success
    path, body = client.calls[0]
    assert path.endswith("text2image/image-synthesis")
    assert body["model"] == IMAGE_MODEL_ID
    assert "桥" in body["input"]["prompt"]

    rejected = _FakeClient(
        {
            "/api/v1/services/aigc/text2image/image-synthesis": {
                "output": {"task_status": "FAILED"}
            }
        }
    )
    with pytest.raises(ProbeError) as exc_info:
        RealCapabilityProbeRunner().probe(get_binding("image"), rejected)  # type: ignore[arg-type]
    assert "未被接受" in exc_info.value.message


def test_real_runner_video_probe_accepts_task() -> None:
    client = _FakeClient(
        {"/api/v1/services/aigc/video-generation/video-synthesis": _video_task_response()}
    )
    outcome = RealCapabilityProbeRunner().probe(get_binding("video"), client)  # type: ignore[arg-type]
    assert outcome.success
    path, body = client.calls[0]
    assert path.endswith("video-generation/video-synthesis")
    assert body["model"] == VIDEO_MODEL_ID
    assert "河" in body["input"]["prompt"]

    rejected = _FakeClient(
        {"/api/v1/services/aigc/video-generation/video-synthesis": {"output": {}}}
    )
    with pytest.raises(ProbeError):
        RealCapabilityProbeRunner().probe(get_binding("video"), rejected)  # type: ignore[arg-type]


def test_real_runner_surfaces_auth_error() -> None:
    client = _FakeClient({"chat": AdapterError(code="auth_error", message="401")})
    with pytest.raises(AdapterError):
        RealCapabilityProbeRunner().probe(get_binding("chat"), client)  # type: ignore[arg-type]


def test_concurrent_record_updates_do_not_corrupt_snapshot() -> None:
    """后台探测线程与请求线程并发读写记录映射时不抛结构错误。"""
    import threading

    service = _service()
    errors: list[Exception] = []

    def writer(account_id: str) -> None:
        try:
            for _ in range(50):
                service.mark_probing(account_id)
        except Exception as exc:  # pragma: no cover - 失败才触发
            errors.append(exc)

    threads = [
        threading.Thread(target=writer, args=(f"acct-{i % 3}",))
        for i in range(6)
    ]
    for thread in threads:
        thread.start()
    for _ in range(200):
        service.status_snapshot("acct-0")
        service.status_snapshot("acct-1")
        service.status_snapshot("acct-2")
    for thread in threads:
        thread.join()

    assert not errors
    snapshot = {c.capability_id: c for c in service.status_snapshot("acct-0")}
    assert len(snapshot) == len(FIXED_CAPABILITY_MATRIX)


def test_mark_unavailable_writes_chinese_reason() -> None:
    service = _service()
    service.mark_unavailable("alice", "操作系统凭据库不可用，请检查系统凭据管理器状态。")

    snapshot = {c.capability_id: c for c in service.status_snapshot("alice")}
    assert all(c.status == ProbeStatus.UNAVAILABLE for c in snapshot.values())
    assert all(c.can_retry is True for c in snapshot.values())
    assert "凭据库不可用" in snapshot["chat"].message
