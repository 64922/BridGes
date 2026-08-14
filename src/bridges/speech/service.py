"""听写与单条回答朗读编排服务（Issue 30）。

听写：请求体音频只在内存中 base64 后直传固定 ASR 快照
（qwen3-asr-flash），不落盘、不产生对象记录；空音频、
超限音频与不支持 MIME 在调用前确定性拒绝；失败只重试同一快照。
朗读：以当前账户对象库为音频归宿——合成成功后先清理旧对象（受控
重试不遗留孤儿），再把新音频转存为账户隔离对象，并把状态快照写回
消息的 ``read_aloud`` 列，刷新后可从同一快照重新请求播放。

两项能力的固定模型标识经 ModelGateway 进入不可变运行锁
（model_run_locks），实现"固定模型标识进入运行记录"。音频请求体
只包含音频与固定提示，不携带百炼密钥、完整画像或无关聊天历史；
审计日志不复制音频、转写文本或回答正文。
"""

from __future__ import annotations

import base64
import contextlib
import re
import secrets
from datetime import UTC, datetime
from typing import Any

import httpx

from bridges.ai.model_gateway import ModelGateway
from bridges.ai.qwen_asr_adapter import (
    SHORT_AUDIO_MAX_BYTES,
    SHORT_AUDIO_MAX_SECONDS,
    SUPPORTED_AUDIO_MIME_TYPES,
)
from bridges.ai.qwen_tts_adapter import TTS_MAX_INPUT_CHARACTERS
from bridges.chat.repository import ConversationRepository
from bridges.contracts.ai import ModelCallStatus, ModelRunLock
from bridges.contracts.chat import ChatMessageRole, ChatMessageStatus
from bridges.contracts.observability import AuditAction, AuditResult
from bridges.contracts.projects import ObjectDomain
from bridges.contracts.speech import (
    DictationProjection,
    DictationStatus,
    ReadAloudProjection,
    ReadAloudState,
    SpeechError,
)
from bridges.contracts.workflows import RunContextEnvelope
from bridges.observability.service import ObservabilityService
from bridges.storage.repository import BridgesObjectRepository

#: 朗读默认音色（与固定矩阵一致，用户不可选）。
_DEFAULT_VOICE = "Cherry"

#: 稳定错误码 → 可操作中文提示（GQ-03：与主对话同源，指向服务运行
#: 配置或稍后重试；只收录语音链路实际可能产生的供应商错误分类，未命
#: 中的错误码回退网关原始消息，与 chat/turn.py 的 user_facing_error
#: 语义一致，不伪造分类）。
_USER_FACING_ERRORS: dict[str, str] = {
    "rate_limit": "请求过于频繁（已触发限流），请稍后重试。",
    "transient": "连接中断或服务暂时不可用，请检查网络后重试。",
    "region_error": "无法连接 Qwen 服务，请检查网络后重试。",
    "auth_error": "Qwen API Key 无效或已失效，请检查启动服务的全局百炼配置与权限。",
    "capability_not_verified": "语音能力未通过验证，请检查启动服务的全局百炼配置与权限。",
}


def _user_facing_error(
    error_code: str | None, fallback_message: str | None, default_message: str
) -> str:
    """把网关错误码折叠为面向用户的中文说明。

    ``fallback_message`` 是网关返回的原始消息（可能为供应商英文原文），
    只在映射未命中时保留，保证分类错误仍给出可读中文。
    """
    mapped = _USER_FACING_ERRORS.get(error_code or "")
    if mapped is not None:
        return mapped
    return fallback_message or default_message

#: Markdown 语法剥离：代码围栏、内联代码、标题、强调、链接、图片、
#: 列表、引用与表格符号。只用于让朗读文本可听，不改变事实内容。
_MD_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_MD_INLINE_CODE_RE = re.compile(r"`([^`]*)`")
_MD_HEADER_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_MD_EMPHASIS_RE = re.compile(r"\*\*([^*]+)\*\*|\*([^*]+)\*|__([^_]+)__|_([^_]+)_")
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_MD_LIST_RE = re.compile(r"^\s*[-*+]\s+|^\s*\d+[.)]\s+", re.MULTILINE)
_MD_QUOTE_RE = re.compile(r"^\s*>\s?", re.MULTILINE)
_MD_TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$", re.MULTILINE)
_MD_TABLE_PIPE_RE = re.compile(r"\|")
_MD_COMMA_SPACING_RE = re.compile(r"\s*，\s*")
_MD_BLANK_RUN_RE = re.compile(r"[ \t]+\n|\n{3,}")


def markdown_to_plain_text(markdown: str) -> str:
    """把消息正文的 Markdown 剥离为适合朗读的纯文本（不改变事实）。

    保留句子结构与换行（换行即停顿）；不剥离代码块内容之外的任何
    数字、单位或结论，避免朗读内容与正文事实漂移。
    """
    text = markdown
    text = _MD_FENCE_RE.sub("", text)
    text = _MD_IMAGE_RE.sub(lambda m: m.group(1), text)
    text = _MD_LINK_RE.sub(lambda m: m.group(1), text)
    text = _MD_INLINE_CODE_RE.sub(lambda m: m.group(1), text)
    text = _MD_HEADER_RE.sub("", text)
    text = _MD_EMPHASIS_RE.sub(
        lambda m: next(g for g in m.groups() if g is not None), text
    )
    text = _MD_QUOTE_RE.sub("", text)
    text = _MD_TABLE_SEP_RE.sub("", text)
    text = _MD_TABLE_PIPE_RE.sub("，", text)
    text = _MD_COMMA_SPACING_RE.sub("，", text)
    text = _MD_LIST_RE.sub("", text)
    text = _MD_BLANK_RUN_RE.sub("\n", text)
    return text.strip()


def _truncate_for_tts(text: str) -> tuple[str, bool]:
    """按固定 TTS 输入上限截断到句子边界；截断时明确标记披露。

    返回 (文本, 是否截断)。上限是适配器合同（TTS_MAX_INPUT_CHARACTERS），
    这里只做边界优化：在最后一个句末标点/换行处切断，绝不从字中间砍断。
    """
    if len(text) <= TTS_MAX_INPUT_CHARACTERS:
        return text, False
    window = text[:TTS_MAX_INPUT_CHARACTERS]
    boundary = max(
        window.rfind("。"),
        window.rfind("！"),
        window.rfind("？"),
        window.rfind("."),
        window.rfind("!\n"),
        window.rfind("\n"),
    )
    cut = boundary + 1 if boundary >= 0 else TTS_MAX_INPUT_CHARACTERS
    return window[:cut].rstrip(), True


class SpeechService:
    """听写与朗读编排；全部操作限定在传入的账户 ID 内。

    GQ-03 后不再有账户级能力门禁：新账户无需任何个人 Qwen 配置即可
    提交听写与朗读，调用由已注册的全局模型网关固定适配器执行；供应商
    错误按稳定错误码折叠为指向全局运行配置的中文提示。本服务只依赖
    固定绑定与 ModelGateway，绝不切换模型或模拟成功。
    """

    def __init__(
        self,
        *,
        gateway: ModelGateway,
        chat_repository: ConversationRepository,
        object_repository: BridgesObjectRepository | None = None,
        observability_service: ObservabilityService | None = None,
        download_client: httpx.Client | None = None,
    ) -> None:
        self._gateway = gateway
        self._objects = object_repository
        self._repo = chat_repository
        self._observability = observability_service
        self._download_client = download_client or httpx.Client(timeout=120.0)

    # ------------------------------------------------------------------
    # 听写
    # ------------------------------------------------------------------

    def transcribe(
        self,
        account_id: str,
        conversation_id: str,
        audio_bytes: bytes,
        mime_type: str,
        duration_seconds: float,
    ) -> DictationProjection:
        """把完整音频直传固定 ASR 快照，返回可编辑转写文本。

        音频只在内存中出现：base64 后随请求体发送，成功后立即释放，
        不落盘、不写入对象库、不写入消息正文。失败只重试同一快照。
        """
        started = datetime.now(UTC)
        # 对话归属校验：听写记录与审计只允许出现在当前账户自己的对话名下
        #（与朗读端点的消息校验同一语义，跨账户一律 404，不泄漏存在性）。
        conversation = self._repo.get_conversation(account_id, conversation_id)
        if conversation is None:
            raise SpeechError(
                "message_not_found",
                "对话不存在或不属于当前账户。",
                status_code=404,
            )
        if not audio_bytes:
            raise SpeechError(
                "empty_audio", "没有收到音频数据，请重新录制后再试。"
            )
        if mime_type not in SUPPORTED_AUDIO_MIME_TYPES:
            raise SpeechError(
                "unsupported_mime_type",
                f"不支持 {mime_type} 音频格式，请使用录音支持的格式。",
            )
        if duration_seconds > SHORT_AUDIO_MAX_SECONDS:
            raise SpeechError(
                "audio_too_long",
                f"音频超过 {SHORT_AUDIO_MAX_SECONDS} 秒限制，请缩短后重试。",
            )
        if len(audio_bytes) > SHORT_AUDIO_MAX_BYTES:
            raise SpeechError(
                "audio_too_large",
                "音频超过 10 MB 大小限制，请缩短后重试。",
                status_code=413,
            )

        run_context = self._run_context(
            account_id, conversation_id, workflow_name="speech_dictation"
        )
        result = self._gateway.invoke(
            "qwen_asr_short",
            "1",
            run_context,
            payload={
                "audio_base64": base64.b64encode(audio_bytes).decode("ascii"),
                "mime_type": mime_type,
                "duration_seconds": duration_seconds,
                "temperature": 0.0,
            },
        )
        self._persist_lock(account_id, result.lock)
        completed = datetime.now(UTC)
        duration_ms = max(1, int((completed - started).total_seconds() * 1000))

        if result.status == ModelCallStatus.SUCCESS and result.output is not None:
            transcript = str(result.output.get("transcript") or "").strip()
            if not transcript:
                self._audit(
                    account_id,
                    conversation_id,
                    AuditAction.ASR_TRANSCRIBE,
                    AuditResult.RETRYABLE_FAIL,
                    "ASR 返回空转写。",
                    {
                        "duration_ms": duration_ms,
                        "model_id": self._lock_model_id(result.lock),
                        "char_count": 0,
                    },
                )
                return DictationProjection(
                    status=DictationStatus.FAILED,
                    transcript="",
                    model_id=self._lock_model_id(result.lock),
                    duration_ms=duration_ms,
                    error_code="empty_transcript",
                    error_message="转写没有返回文本，请重试或重新录制。",
                    retryable=True,
                    created_at=completed,
                )
            self._audit(
                account_id,
                conversation_id,
                AuditAction.ASR_TRANSCRIBE,
                AuditResult.SUCCESS,
                "听写转写完成。",
                {
                    "duration_ms": duration_ms,
                    "model_id": self._lock_model_id(result.lock),
                    "char_count": len(transcript),
                },
            )
            return DictationProjection(
                status=DictationStatus.SUCCESS,
                transcript=transcript,
                model_id=self._lock_model_id(result.lock),
                duration_ms=duration_ms,
                created_at=completed,
            )

        error_code = result.error_code or "transcription_failed"
        error_message = _user_facing_error(
            error_code, result.error_message, "语音转写失败，请重试。"
        )
        retryable = result.status == ModelCallStatus.RETRYABLE_FAIL
        self._audit(
            account_id,
            conversation_id,
            AuditAction.ASR_TRANSCRIBE,
            AuditResult.RETRYABLE_FAIL if retryable else AuditResult.BLOCKED,
            "听写转写失败。",
            {
                "duration_ms": duration_ms,
                "model_id": self._lock_model_id(result.lock),
                "error_code": error_code,
            },
        )
        return DictationProjection(
            status=DictationStatus.FAILED,
            transcript="",
            model_id=self._lock_model_id(result.lock),
            duration_ms=duration_ms,
            error_code=error_code,
            error_message=error_message,
            retryable=retryable,
            created_at=completed,
        )

    # ------------------------------------------------------------------
    # 单条回答朗读
    # ------------------------------------------------------------------

    def generate_read_aloud(
        self,
        account_id: str,
        conversation_id: str,
        message_id: str,
    ) -> ReadAloudProjection:
        """为一条已完成的助手文本回答生成朗读并转存账户对象库。

        同一条回答的受控重试复用同一消息正文重新合成；重试前先清理
        旧音频对象，任何路径都不遗留孤儿或跨账户音频。
        """
        record = self._repo.get_message(account_id, message_id)
        if record is None or record.conversation_id != conversation_id:
            raise SpeechError(
                "message_not_found",
                "消息不存在或不属于当前对话。",
                status_code=404,
            )
        if (
            record.role != ChatMessageRole.ASSISTANT
            or record.status != ChatMessageStatus.DONE
            or not record.content.strip()
        ):
            raise SpeechError(
                "message_not_readable",
                "只能为已完成的助手回答生成朗读。",
                status_code=409,
            )

        run_context = self._run_context(
            account_id, conversation_id, workflow_name="speech_read_aloud"
        )
        plain_text, truncated = _truncate_for_tts(
            markdown_to_plain_text(record.content)
        )
        if not plain_text:
            raise SpeechError(
                "message_not_readable",
                "回答没有可朗读的文本内容。",
                status_code=409,
            )

        result = self._gateway.invoke(
            "qwen_tts",
            "1",
            run_context,
            payload={"text": plain_text, "voice": _DEFAULT_VOICE},
        )
        self._persist_lock(account_id, result.lock)
        completed = datetime.now(UTC)
        generated_at = completed
        model_id = self._lock_model_id(result.lock)

        if result.status != ModelCallStatus.SUCCESS or result.output is None:
            error_code = result.error_code or "synthesis_failed"
            error_message = _user_facing_error(
                error_code, result.error_message, "朗读生成失败，请重试。"
            )
            retryable = result.status == ModelCallStatus.RETRYABLE_FAIL
            projection = ReadAloudProjection(
                message_id=message_id,
                state=ReadAloudState.FAILED,
                model_id=model_id,
                char_count=len(plain_text),
                truncated=truncated,
                error_code=error_code,
                error_message=error_message,
                retryable=retryable,
                generated_at=generated_at,
            )
            self._save_projection(account_id, message_id, projection)
            self._audit(
                account_id,
                message_id,
                AuditAction.READ_ALOUD_GENERATE,
                AuditResult.RETRYABLE_FAIL if retryable else AuditResult.BLOCKED,
                "朗读生成失败。",
                {
                    "char_count": len(plain_text),
                    "truncated": truncated,
                    "model_id": model_id,
                    "error_code": error_code,
                },
            )
            return projection

        audio_url = result.output.get("audio_url")
        media_type = str(result.output.get("mime_type") or "audio/wav")
        if not audio_url or not isinstance(audio_url, str):
            return self._synthesis_output_error(
                account_id,
                message_id,
                plain_text,
                truncated,
                model_id,
                generated_at,
                "empty_response",
                "朗读接口没有返回音频。",
                retryable=False,
            )

        try:
            response = self._download_client.get(audio_url)
            response.raise_for_status()
            audio_bytes = response.content
        except Exception as exc:
            return self._synthesis_output_error(
                account_id,
                message_id,
                plain_text,
                truncated,
                model_id,
                generated_at,
                "audio_download_failed",
                "朗读音频下载失败，请重试。",
                retryable=True,
                cause=str(exc)[:200],
            )
        if not audio_bytes:
            return self._synthesis_output_error(
                account_id,
                message_id,
                plain_text,
                truncated,
                model_id,
                generated_at,
                "empty_audio",
                "朗读接口返回了空音频，请重试。",
                retryable=True,
            )

        # 受控重试不遗留孤儿：先清理旧对象（幂等），再写新对象。
        self._discard_previous_audio(account_id, message_id)

        if self._objects is None:
            return self._synthesis_output_error(
                account_id,
                message_id,
                plain_text,
                truncated,
                model_id,
                generated_at,
                "storage_unavailable",
                "音频存储当前不可用，请稍后重试。",
                retryable=True,
            )
        stored = self._objects.create_object(
            account_id,
            original_filename=f"read-aloud-{message_id}.wav",
            content=audio_bytes,
            media_type=media_type,
        )
        projection = ReadAloudProjection(
            message_id=message_id,
            state=ReadAloudState.READY,
            model_id=model_id,
            audio_ref=stored.object_id,
            media_type=media_type,
            content_length=stored.content_length,
            char_count=len(plain_text),
            truncated=truncated,
            generated_at=generated_at,
        )
        self._save_projection(account_id, message_id, projection)
        self._audit(
            account_id,
            message_id,
            AuditAction.READ_ALOUD_GENERATE,
            AuditResult.SUCCESS,
            "朗读生成完成。",
            {
                "char_count": len(plain_text),
                "truncated": truncated,
                "model_id": model_id,
                "content_length": stored.content_length,
            },
        )
        return projection

    def get_read_aloud(
        self,
        account_id: str,
        conversation_id: str,
        message_id: str,
    ) -> ReadAloudProjection:
        """读取消息上的朗读快照；消息不存在报 404。"""
        record = self._repo.get_message(account_id, message_id)
        if record is None or record.conversation_id != conversation_id:
            raise SpeechError(
                "message_not_found",
                "消息不存在或不属于当前对话。",
                status_code=404,
            )
        projection = self._projection_from_record(record.read_aloud, message_id)
        if projection is None:
            projection = ReadAloudProjection(
                message_id=message_id, state=ReadAloudState.NOT_GENERATED
            )
            self._save_projection(account_id, message_id, projection)
        return projection

    def get_read_aloud_audio(
        self,
        account_id: str,
        conversation_id: str,
        message_id: str,
    ) -> tuple[bytes, str, int]:
        """返回朗读音频字节与媒体类型；未生成或跨账户一律 404。"""
        record = self._repo.get_message(account_id, message_id)
        if record is None or record.conversation_id != conversation_id:
            raise SpeechError(
                "message_not_found",
                "消息不存在或不属于当前对话。",
                status_code=404,
            )
        projection = self._projection_from_record(record.read_aloud, message_id)
        if (
            projection is None
            or projection.state != ReadAloudState.READY
            or projection.audio_ref is None
            or self._objects is None
        ):
            raise SpeechError(
                "audio_not_ready",
                "该回答还没有可播放的朗读音频。",
                status_code=404,
            )
        content = self._objects.get_content(account_id, projection.audio_ref)
        return (
            content,
            projection.media_type or "audio/wav",
            len(content),
        )

    def delete_read_aloud(
        self,
        account_id: str,
        conversation_id: str,
        message_id: str,
    ) -> ReadAloudProjection:
        """停止并清理朗读：删除账户对象库中的音频并复位快照。

        幂等：消息不存在、未生成或音频已清理都返回复位后的投影，
        不抛错、不遗留孤儿音频。
        """
        record = self._repo.get_message(account_id, message_id)
        if record is None or record.conversation_id != conversation_id:
            raise SpeechError(
                "message_not_found",
                "消息不存在或不属于当前对话。",
                status_code=404,
            )
        projection = self._projection_from_record(record.read_aloud, message_id)
        self._discard_previous_audio(account_id, message_id)
        reset = ReadAloudProjection(
            message_id=message_id, state=ReadAloudState.NOT_GENERATED
        )
        self._save_projection(account_id, message_id, reset)
        self._audit(
            account_id,
            message_id,
            AuditAction.READ_ALOUD_DELETE,
            AuditResult.SUCCESS,
            "朗读音频已清理。",
            {"had_audio": projection is not None and projection.audio_ref is not None},
        )
        return reset

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _synthesis_output_error(
        self,
        account_id: str,
        message_id: str,
        plain_text: str,
        truncated: bool,
        model_id: str | None,
        generated_at: datetime,
        code: str,
        message: str,
        *,
        retryable: bool,
        cause: str | None = None,
    ) -> ReadAloudProjection:
        projection = ReadAloudProjection(
            message_id=message_id,
            state=ReadAloudState.FAILED,
            model_id=model_id,
            char_count=len(plain_text),
            truncated=truncated,
            error_code=code,
            error_message=message,
            retryable=retryable,
            generated_at=generated_at,
        )
        self._save_projection(account_id, message_id, projection)
        details: dict[str, Any] = {
            "char_count": len(plain_text),
            "truncated": truncated,
            "model_id": model_id,
            "error_code": code,
        }
        if cause:
            details["cause"] = cause
        self._audit(
            account_id,
            message_id,
            AuditAction.READ_ALOUD_GENERATE,
            AuditResult.RETRYABLE_FAIL if retryable else AuditResult.BLOCKED,
            "朗读生成失败。",
            details,
        )
        return projection

    def _discard_previous_audio(self, account_id: str, message_id: str) -> None:
        """清理消息上已保存的旧音频对象（幂等，跨账户不可能访问）。

        只在 ``read_aloud`` 快照指向当前账户对象时调用；对象缺失或
        快照为空都静默成功，不把清理失败上升为生成失败。
        """
        record = self._repo.get_message(account_id, message_id)
        if record is None or record.read_aloud is None or self._objects is None:
            return
        projection = self._projection_from_record(record.read_aloud, message_id)
        if projection is None or projection.audio_ref is None:
            return
        with contextlib.suppress(Exception):
            # 清理失败不阻断重新生成（孤儿由对象库清理轮兜底）。
            self._objects.delete_object(account_id, projection.audio_ref)

    def _save_projection(
        self, account_id: str, message_id: str, projection: ReadAloudProjection
    ) -> None:
        self._repo.update_message_read_aloud(
            account_id,
            message_id,
            projection.model_dump(mode="json"),
        )

    @staticmethod
    def _projection_from_record(
        raw: dict[str, Any] | None, message_id: str
    ) -> ReadAloudProjection | None:
        if not raw:
            return None
        try:
            projection = ReadAloudProjection.model_validate(raw)
        except Exception:  # noqa: BLE001 - 损坏快照按未生成处理
            return None
        return projection.model_copy(update={"message_id": message_id})

    def _run_context(
        self,
        account_id: str,
        conversation_id: str,
        *,
        workflow_name: str,
    ) -> RunContextEnvelope:
        return RunContextEnvelope(
            run_id=f"{workflow_name}-{secrets.token_urlsafe(8)}",
            account_id=account_id,
            project_id=conversation_id,
            workflow_name=workflow_name,
            workflow_version="1",
            object_domain=ObjectDomain.PERSONAL_VAULT,
            submitted_at=datetime.now(UTC),
        )

    def _persist_lock(self, account_id: str, lock: ModelRunLock | None) -> None:
        """把固定模型标识写入运行记录（model_run_locks）。

        失败锁的错误字段按 GQ-03 同源映射折叠为面向用户的中文提示后再
        持久化：供应商原文（可能包含 authorization/token 等疑似凭据词）
        绝不写入运行记录（Issue 10 录制器安全扫描拒绝此类自由文本），
        也不把上游正文带进审计库。
        """
        if lock is None or self._repo is None:
            return
        if lock.error_message is not None or lock.degradation_reason is not None:
            safe_message = _user_facing_error(
                lock.error_code, None, "语音能力调用失败，请重试。"
            )
            lock = lock.model_copy(
                update={
                    "error_message": safe_message,
                    "degradation_reason": safe_message,
                }
            )
        self._repo.insert_run_lock(account_id, lock)

    @staticmethod
    def _lock_model_id(lock: ModelRunLock | None) -> str | None:
        return lock.actual_model_id if lock is not None else None

    def _audit(
        self,
        account_id: str,
        ref: str,
        action: AuditAction,
        result: AuditResult,
        reason: str,
        details: dict[str, Any],
    ) -> None:
        if self._observability is None:
            return
        self._observability.log_audit(
            actor_account_id=account_id,
            action=action,
            result=result,
            object_refs=[ref],
            reason=reason,
            details=details,
        )


__all__ = [
    "SpeechService",
    "markdown_to_plain_text",
]
