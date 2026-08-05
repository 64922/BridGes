"""听写与朗读服务测试（Issue 30）。

使用内存 sqlite + 可编程网关适配器验证：听写只在内存直传不落盘、空/
超限/不支持 MIME 确定性拒绝、失败只重试同一快照（固定模型标识进运行
记录）、朗读合成→转存账户对象库→快照写回、受控重试清理旧对象、
账户隔离、删除幂等与最小留存。审计 spy 验证音频/转写/正文不进日志。
"""

from __future__ import annotations

import io
import struct
import wave
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from bridges.ai import ModelGateway
from bridges.ai.adapters import AdapterResult, TransientError
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.chat.repository import ConversationRepository, MessageRecord
from bridges.contracts.ai import CapabilityKind, CapabilityRecord
from bridges.contracts.chat import (
    ChatMessageRole,
    ChatMessageStatus,
)
from bridges.contracts.speech import ReadAloudState, SpeechError
from bridges.speech.service import SpeechService, markdown_to_plain_text
from bridges.storage.database import BridgesDatabase
from bridges.storage.object_store import EncryptedFileObjectStore
from bridges.storage.repository import BridgesObjectRepository

ASR_MODEL = "qwen3-asr-flash-2025-09-08"
TTS_MODEL = "qwen3-tts-flash-2025-11-27"
_AUDIO_URL = "http://tts.local/audio.wav"
_WAV_BYTES = b"\x52\x49\x46\x46" + b"\x00" * 40


class _RecordingObservability:
    """记录 log_audit 调用的测试替身（验证审计不含正文）。"""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def log_audit(self, **kwargs: Any) -> None:
        self.events.append(kwargs)


class _ProgrammableAsrAdapter:
    """可编程 ASR 适配器：返回固定转写或抛出可分类错误。"""

    def __init__(self, transcript: str | None = None, error: Exception | None = None) -> None:
        self._transcript = transcript
        self._error = error
        self.calls: list[dict[str, Any]] = []

    def call(
        self,
        capability: CapabilityRecord,
        run_context: Any,
        payload: dict[str, Any],
    ) -> AdapterResult:
        self.calls.append(payload)
        if self._error is not None:
            raise self._error
        return AdapterResult(
            actual_model_id=capability.model_id,
            output={"transcript": self._transcript or ""},
        )


class _ProgrammableTtsAdapter:
    """可编程 TTS 适配器：返回供应商临时 URL 或抛出可分类错误。"""

    def __init__(
        self,
        audio_url: str | None = _AUDIO_URL,
        error: Exception | None = None,
    ) -> None:
        self._audio_url = audio_url
        self._error = error
        self.calls: list[dict[str, Any]] = []

    def call(
        self,
        capability: CapabilityRecord,
        run_context: Any,
        payload: dict[str, Any],
    ) -> AdapterResult:
        self.calls.append(payload)
        if self._error is not None:
            raise self._error
        return AdapterResult(
            actual_model_id=capability.model_id,
            output={
                "audio_url": self._audio_url,
                "mime_type": "audio/wav",
            },
        )


def _make_probe_wav() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(struct.pack("<h", 0) * 16000)
    return buffer.getvalue()


class _SpeechHarness:
    """组装内存数据库、对象库、网关与服务的测试台。"""

    def __init__(self, tmp_path: Path) -> None:
        self.db = BridgesDatabase(":memory:")
        self.db.initialize()
        self.repo = ConversationRepository(self.db)
        self.object_repo = BridgesObjectRepository(
            self.db, EncryptedFileObjectStore(tmp_path, encryption_key="speech-test-key")
        )
        self.registry = CapabilityRegistry()
        self.registry.register(
            CapabilityRecord(
                name="qwen_asr_short",
                version="1",
                kind=CapabilityKind.MODEL,
                vendor="qwen",
                region="cn-beijing",
                model_id=ASR_MODEL,
                input_schema_version="audio-upload-v1",
                output_schema_version="transcript-v1",
            )
        )
        self.registry.register(
            CapabilityRecord(
                name="qwen_tts",
                version="1",
                kind=CapabilityKind.MODEL,
                vendor="qwen",
                region="cn-beijing",
                model_id=TTS_MODEL,
                input_schema_version="tts-text-v1",
                output_schema_version="tts-audio-v1",
                supported_modalities=["text", "audio"],
            )
        )
        self.gateway = ModelGateway(self.registry)
        self.asr = _ProgrammableAsrAdapter(transcript="你好，今天天气不错。")
        self.tts = _ProgrammableTtsAdapter()
        self.gateway.register_adapter("qwen_asr_short", "1", self.asr)
        self.gateway.register_adapter("qwen_tts", "1", self.tts)
        self.audit = _RecordingObservability()
        self.service = SpeechService(
            gateway=self.gateway,
            chat_repository=self.repo,
            object_repository=self.object_repo,
            observability_service=self.audit,  # type: ignore[arg-type]
            download_client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, content=_WAV_BYTES)
                )
            ),
        )
        self.account_id = "account-a"
        self.conversation_id = "conv-1"
        now = datetime.now(UTC)
        self.object_repo.ensure_account(self.account_id, "alice@example.com")
        self.repo.create_conversation(
            account_id=self.account_id,
            conversation_id=self.conversation_id,
            title="测试对话",
            mode="companion",
            created_at=now,
        )

    def seed_conversation(
        self, account_id: str, conversation_id: str, *, mode: str = "companion"
    ) -> None:
        self.object_repo.ensure_account(account_id, f"{account_id}@example.com")
        self.repo.create_conversation(
            account_id=account_id,
            conversation_id=conversation_id,
            title="其他对话",
            mode=mode,
            created_at=datetime.now(UTC),
        )

    def seed_message(
        self,
        message_id: str,
        *,
        role: ChatMessageRole = ChatMessageRole.ASSISTANT,
        status: ChatMessageStatus = ChatMessageStatus.DONE,
        content: str = "这是**一条**测试回答。",
        account_id: str | None = None,
        conversation_id: str | None = None,
    ) -> None:
        now = datetime.now(UTC)
        self.repo.insert_message(
            MessageRecord(
                message_id=message_id,
                conversation_id=conversation_id or self.conversation_id,
                account_id=account_id or self.account_id,
                role=role,
                attempt_number=1,
                status=status,
                content=content,
                thinking=None,
                error_code=None,
                error_message=None,
                duration_ms=None,
                model_id=None,
                run_lock_id=None,
                created_at=now,
                updated_at=now,
            )
        )

    def lock_model_ids(self) -> list[str]:
        """返回运行记录中的实际模型标识列表。"""
        rows = self.db.connection.execute("SELECT actual_model_id FROM model_run_locks").fetchall()
        return [str(row["actual_model_id"]) for row in rows]

    def object_count(self, account_id: str) -> int:
        return len(self.object_repo.list_objects(account_id))


# ---------------------------------------------------------------------------
# 听写
# ---------------------------------------------------------------------------


def test_transcribe_success_returns_editable_text_without_objects(
    tmp_path: Path,
) -> None:
    h = _SpeechHarness(tmp_path)
    projection = h.service.transcribe(
        h.account_id, h.conversation_id, _make_probe_wav(), "audio/wav", 1.0
    )
    assert projection.status.value == "success"
    assert projection.transcript == "你好，今天天气不错。"
    assert projection.model_id == ASR_MODEL
    assert projection.error_message is None
    # 固定模型标识进入运行记录；听写不产生任何对象（不落盘）。
    assert h.lock_model_ids() == [ASR_MODEL]
    assert h.object_count(h.account_id) == 0
    # 音频请求体只含音频与固定提示：无密钥/画像/聊天历史字段。
    payload = h.asr.calls[0]
    assert "audio_base64" in payload and "mime_type" in payload
    assert not any(key in payload for key in ("api_key", "apiKey", "profile"))


def test_transcribe_unknown_conversation_rejected(tmp_path: Path) -> None:
    h = _SpeechHarness(tmp_path)
    with pytest.raises(SpeechError) as exc:
        h.service.transcribe(
            h.account_id, "conv-ghost", _make_probe_wav(), "audio/wav", 1.0
        )
    assert exc.value.code == "message_not_found"
    assert exc.value.status_code == 404


def test_transcribe_empty_audio_rejected(tmp_path: Path) -> None:
    h = _SpeechHarness(tmp_path)
    with pytest.raises(SpeechError) as exc:
        h.service.transcribe(h.account_id, h.conversation_id, b"", "audio/wav", 1.0)
    assert exc.value.code == "empty_audio"


def test_transcribe_too_long_audio_rejected(tmp_path: Path) -> None:
    h = _SpeechHarness(tmp_path)
    with pytest.raises(SpeechError) as exc:
        h.service.transcribe(h.account_id, h.conversation_id, _make_probe_wav(), "audio/wav", 301)
    assert exc.value.code == "audio_too_long"


def test_transcribe_unsupported_mime_rejected(tmp_path: Path) -> None:
    h = _SpeechHarness(tmp_path)
    with pytest.raises(SpeechError) as exc:
        h.service.transcribe(h.account_id, h.conversation_id, _make_probe_wav(), "text/plain", 1.0)
    assert exc.value.code == "unsupported_mime_type"


def test_transcribe_transient_failure_retryable_same_snapshot(tmp_path: Path) -> None:
    h = _SpeechHarness(tmp_path)
    h.asr._error = TransientError("network")
    projection = h.service.transcribe(
        h.account_id, h.conversation_id, _make_probe_wav(), "audio/wav", 1.0
    )
    assert projection.status.value == "failed"
    assert projection.retryable is True
    assert projection.model_id == ASR_MODEL
    assert projection.transcript == ""
    # 失败只重试同一快照：运行记录模型标识仍是固定 ASR 快照。
    assert h.lock_model_ids() == [ASR_MODEL]


def test_transcribe_empty_transcript_failed_retryable(tmp_path: Path) -> None:
    h = _SpeechHarness(tmp_path)
    h.asr._transcript = "   "
    projection = h.service.transcribe(
        h.account_id, h.conversation_id, _make_probe_wav(), "audio/wav", 1.0
    )
    assert projection.status.value == "failed"
    assert projection.error_code == "empty_transcript"
    assert projection.retryable is True


def test_transcribe_audit_excludes_transcript_and_audio(tmp_path: Path) -> None:
    h = _SpeechHarness(tmp_path)
    h.service.transcribe(h.account_id, h.conversation_id, _make_probe_wav(), "audio/wav", 1.0)
    assert h.audit.events, "应有 ASR 审计事件"
    event = h.audit.events[0]
    assert event["action"].value == "asr_transcribe"
    serialized = repr(event)
    assert "你好" not in serialized
    assert "audio" not in str(event["details"])
    assert event["details"]["model_id"] == ASR_MODEL


# ---------------------------------------------------------------------------
# 朗读
# ---------------------------------------------------------------------------


def test_generate_read_aloud_stores_object_and_snapshot(tmp_path: Path) -> None:
    h = _SpeechHarness(tmp_path)
    h.seed_message("m-1", content="## 标题\n这是**一条**测试回答。")
    projection = h.service.generate_read_aloud(h.account_id, h.conversation_id, "m-1")
    assert projection.state == ReadAloudState.READY
    assert projection.model_id == TTS_MODEL
    assert projection.audio_ref is not None
    assert projection.char_count > 0
    assert projection.truncated is False
    assert projection.error_message is None
    # 音频转存账户对象库；运行记录为固定 TTS 快照。
    assert h.object_count(h.account_id) == 1
    assert h.lock_model_ids() == [TTS_MODEL]
    audio_bytes, media_type, length = h.service.get_read_aloud_audio(
        h.account_id, h.conversation_id, "m-1"
    )
    assert audio_bytes == _WAV_BYTES and media_type == "audio/wav" and length == len(_WAV_BYTES)
    # TTS 请求体只含文本与固定音色，无密钥/画像/聊天历史。
    payload = h.tts.calls[0]
    assert "text" in payload and payload["voice"] == "Cherry"
    assert not any(key in payload for key in ("api_key", "apiKey", "profile"))


def test_read_aloud_requires_done_assistant_message(tmp_path: Path) -> None:
    h = _SpeechHarness(tmp_path)
    h.seed_message("m-user", role=ChatMessageRole.USER, content="你好")
    with pytest.raises(SpeechError) as exc:
        h.service.generate_read_aloud(h.account_id, h.conversation_id, "m-user")
    assert exc.value.code == "message_not_readable"
    h.seed_message("m-streaming", status=ChatMessageStatus.STREAMING, content="正在生成")
    with pytest.raises(SpeechError) as exc:
        h.service.generate_read_aloud(h.account_id, h.conversation_id, "m-streaming")
    assert exc.value.code == "message_not_readable"


def test_read_aloud_cross_conversation_and_account_denied(tmp_path: Path) -> None:
    h = _SpeechHarness(tmp_path)
    h.seed_message("m-1", content="回答")
    with pytest.raises(SpeechError) as exc:
        h.service.generate_read_aloud(h.account_id, "conv-other", "m-1")
    assert exc.value.code == "message_not_found"
    h.seed_conversation("account-b", "conv-b")
    h.seed_message(
        "m-other", account_id="account-b", conversation_id="conv-b", content="别人的回答"
    )
    with pytest.raises(SpeechError) as exc:
        h.service.generate_read_aloud(h.account_id, "conv-b", "m-other")
    assert exc.value.code == "message_not_found"
    # 跨账户读取音频同样拒绝。
    with pytest.raises(SpeechError) as exc:
        h.service.get_read_aloud_audio(h.account_id, "conv-b", "m-other")
    assert exc.value.code == "message_not_found"


def test_read_aloud_retry_replaces_old_object(tmp_path: Path) -> None:
    h = _SpeechHarness(tmp_path)
    h.seed_message("m-1", content="回答")
    first = h.service.generate_read_aloud(h.account_id, h.conversation_id, "m-1")
    assert first.audio_ref is not None
    second = h.service.generate_read_aloud(h.account_id, h.conversation_id, "m-1")
    assert second.audio_ref is not None
    assert second.audio_ref != first.audio_ref
    # 旧对象已物理清理（最小留存），账户内只剩新音频对象。
    remaining = h.object_repo.list_objects(h.account_id)
    assert [obj.object_id for obj in remaining] == [second.audio_ref]
    assert h.object_count(h.account_id) == 1


def test_read_aloud_tts_failure_marks_failed_retryable(tmp_path: Path) -> None:
    h = _SpeechHarness(tmp_path)
    h.seed_message("m-1", content="回答")
    h.tts._error = TransientError("network")
    projection = h.service.generate_read_aloud(h.account_id, h.conversation_id, "m-1")
    assert projection.state == ReadAloudState.FAILED
    assert projection.retryable is True
    assert projection.error_code is not None
    assert h.object_count(h.account_id) == 0


def test_read_aloud_download_failure_marks_failed(tmp_path: Path) -> None:
    h = _SpeechHarness(tmp_path)
    h.seed_message("m-1", content="回答")

    def failing_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down", request=request)

    h.service._download_client = httpx.Client(transport=httpx.MockTransport(failing_handler))
    projection = h.service.generate_read_aloud(h.account_id, h.conversation_id, "m-1")
    assert projection.state == ReadAloudState.FAILED
    assert projection.error_code == "audio_download_failed"
    assert projection.retryable is True
    assert h.object_count(h.account_id) == 0


def test_read_aloud_delete_is_idempotent_and_cleans_object(tmp_path: Path) -> None:
    h = _SpeechHarness(tmp_path)
    h.seed_message("m-1", content="回答")
    generated = h.service.generate_read_aloud(h.account_id, h.conversation_id, "m-1")
    assert generated.state == ReadAloudState.READY
    reset = h.service.delete_read_aloud(h.account_id, h.conversation_id, "m-1")
    assert reset.state == ReadAloudState.NOT_GENERATED
    assert reset.audio_ref is None
    # 音频对象已从账户对象库清除。
    assert h.object_count(h.account_id) == 0
    # 幂等：再次删除不报错。
    again = h.service.delete_read_aloud(h.account_id, h.conversation_id, "m-1")
    assert again.state == ReadAloudState.NOT_GENERATED
    # 删除后音频不可再请求。
    with pytest.raises(SpeechError) as exc:
        h.service.get_read_aloud_audio(h.account_id, h.conversation_id, "m-1")
    assert exc.value.code == "audio_not_ready"


def test_read_aloud_snapshot_survives_reload(tmp_path: Path) -> None:
    h = _SpeechHarness(tmp_path)
    h.seed_message("m-1", content="回答")
    h.service.generate_read_aloud(h.account_id, h.conversation_id, "m-1")
    # 刷新后从同一快照恢复：新服务实例读同一数据库。
    service2 = SpeechService(
        gateway=h.gateway,
        chat_repository=h.repo,
        object_repository=h.object_repo,
        observability_service=h.audit,  # type: ignore[arg-type]
    )
    projection = service2.get_read_aloud(h.account_id, h.conversation_id, "m-1")
    assert projection.state == ReadAloudState.READY
    assert projection.audio_ref is not None


def test_read_aloud_truncates_long_text_with_disclosure(tmp_path: Path) -> None:
    h = _SpeechHarness(tmp_path)
    h.seed_message("m-1", content="开始。" + "啊" * 1200)
    projection = h.service.generate_read_aloud(h.account_id, h.conversation_id, "m-1")
    assert projection.state == ReadAloudState.READY
    assert projection.truncated is True
    assert projection.char_count is not None and projection.char_count <= 602


# ---------------------------------------------------------------------------
# 纯文本化
# ---------------------------------------------------------------------------


def test_markdown_to_plain_text_keeps_facts() -> None:
    md = (
        "## 结论\n"
        "根据**实验**，速度是 [10 m/s](https://example.com)，"
        "`x + 1 = 2`。\n\n"
        "- 第一点\n- 第二点\n\n"
        "> 引用内容\n\n"
        "| 列A | 列B |\n| --- | --- |\n| 1 | 2 |\n"
    )
    text = markdown_to_plain_text(md)
    assert "结论" in text
    assert "10 m/s" in text
    assert "x + 1 = 2" in text
    assert "第一点" in text
    assert "1，2" in text
    assert "https://example.com" not in text
    assert "**" not in text
    assert "#" not in text


def test_truncate_breaks_at_sentence_boundary() -> None:
    from bridges.speech.service import _truncate_for_tts

    text = "第一句。" + "中" * 900 + "第二句。"
    truncated, flagged = _truncate_for_tts(text)
    assert flagged is True
    assert len(truncated) <= 600
    assert truncated.endswith("。")
