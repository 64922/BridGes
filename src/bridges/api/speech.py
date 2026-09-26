"""听写与单条回答朗读 API 路由（Issue 30，GQ-03 迁移；Issue 21 退役朗读）。

听写：前端录音并停止后，把完整音频 POST 到固定 ASR 快照，返回可编辑
转写文本；音频只在请求体内存中直传，不落盘。朗读：ADR-0030 把「回答
朗读」列为退役能力，因此生成与停止清理一律稳定返回 410，只保留历史
只读面（GET 投影与 GET 音频），旧回答上已生成的音频仍可回放。两项
能力的读取路径都不依赖账户凭据服务或账户探测快照（GQ-03）：请求
直接进入已由启动硬门保证完成注册的全局模型网关固定适配器；音频
请求体只含音频与固定提示，不携带密钥、画像或无关聊天历史。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from bridges.ai.qwen_asr_adapter import (
    SHORT_AUDIO_MAX_BYTES,
    SUPPORTED_AUDIO_MIME_TYPES,
)
from bridges.api.auth import SubjectDep
from bridges.contracts.chat import ChatError
from bridges.contracts.speech import (
    DictationProjection,
    ReadAloudProjection,
    SpeechError,
)
from bridges.retirement import raise_retired_capability
from bridges.speech.service import SpeechService

router = APIRouter(prefix="/chat", tags=["speech"])

_RETIRED_ERROR = "legacy_read_aloud_retired"
_RETIRED_MESSAGE = "回答朗读已退役，已生成的音频仍可回放。"
_RETIRED_RESPONSES = {status.HTTP_410_GONE: {"model": ChatError}}


def _get_speech_service(request: Request) -> SpeechService:
    service: SpeechService | None = getattr(request.app.state, "speech_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=ChatError(
                error="speech_unavailable",
                message="语音服务未启用，当前实例拒绝听写与朗读操作。",
            ).model_dump(),
        )
    return service


SpeechServiceDep = Annotated[SpeechService, Depends(_get_speech_service)]


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=ChatError(error=code, message=message).model_dump(),
    )


def _handle_speech_error(exc: SpeechError) -> HTTPException:
    return _error(exc.status_code, exc.code, exc.message)


@router.post(
    "/conversations/{conversation_id}/dictation",
    response_model=DictationProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ChatError},
        status.HTTP_409_CONFLICT: {"model": ChatError},
        status.HTTP_413_CONTENT_TOO_LARGE: {"model": ChatError},
    },
)
async def transcribe_dictation(
    conversation_id: str,
    request: Request,
    service: SpeechServiceDep,
    subject: SubjectDep,
) -> Response:
    """提交一段完整录音音频到固定 ASR 快照，返回可编辑转写文本。

    音频通过请求体原样上传（Content-Type 为音频 MIME，时长经
    ``x-bridges-audio-duration`` 头声明）；只接受音频 MIME，空音频、
    超限音频与不支持格式在调用前确定性拒绝。不检查账户凭据或探测
    快照（GQ-03）：新账户无需任何个人 Qwen 配置即可提交。转写失败
    返回 failed 投影（含稳定错误码与中文原因），不产生用户消息、
    不落盘音频。
    """
    mime_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if mime_type not in SUPPORTED_AUDIO_MIME_TYPES:
        raise _error(
            status.HTTP_400_BAD_REQUEST,
            "unsupported_mime_type",
            f"不支持 {mime_type or '空'} 音频格式，请使用录音支持的格式。",
        )
    duration_header = request.headers.get("x-bridges-audio-duration")
    try:
        duration_seconds = max(0.0, float(duration_header)) if duration_header else 0.0
    except ValueError as exc:
        raise _error(
            status.HTTP_400_BAD_REQUEST,
            "invalid_request",
            "录音时长声明无效。",
        ) from exc

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > SHORT_AUDIO_MAX_BYTES:
            raise _error(
                status.HTTP_413_CONTENT_TOO_LARGE,
                "audio_too_large",
                "音频超过 10 MB 大小限制，请缩短后重试。",
            )
        chunks.append(chunk)

    try:
        projection = service.transcribe(
            subject.account_id,
            conversation_id,
            b"".join(chunks),
            mime_type,
            duration_seconds,
        )
    except SpeechError as exc:
        raise _handle_speech_error(exc) from exc
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=projection.model_dump(mode="json"),
    )


@router.post(
    "/conversations/{conversation_id}/messages/{message_id}/read-aloud",
    status_code=status.HTTP_410_GONE,
    responses=_RETIRED_RESPONSES,
)
def generate_read_aloud(
    conversation_id: str,
    message_id: str,
    request: Request,
    _subject: SubjectDep,
) -> None:
    """为一条已完成的助手回答生成朗读。已退役：不再接受生成请求，稳定返回 410。"""
    raise_retired_capability(
        request,
        endpoint="legacy.speech.read_aloud.generate",
        error=_RETIRED_ERROR,
        replacement_path="/",
        message=_RETIRED_MESSAGE,
    )


@router.get(
    "/conversations/{conversation_id}/messages/{message_id}/read-aloud",
    response_model=ReadAloudProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ChatError},
        status.HTTP_404_NOT_FOUND: {"model": ChatError},
    },
)
def get_read_aloud(
    conversation_id: str,
    message_id: str,
    service: SpeechServiceDep,
    subject: SubjectDep,
) -> Response:
    """读取消息上的朗读状态快照；刷新后从同一快照恢复播放入口。"""
    try:
        projection = service.get_read_aloud(
            subject.account_id, conversation_id, message_id
        )
    except SpeechError as exc:
        raise _handle_speech_error(exc) from exc
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=projection.model_dump(mode="json"),
    )


@router.get(
    "/conversations/{conversation_id}/messages/{message_id}/read-aloud/audio",
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ChatError},
        status.HTTP_404_NOT_FOUND: {"model": ChatError},
    },
)
def get_read_aloud_audio(
    conversation_id: str,
    message_id: str,
    service: SpeechServiceDep,
    subject: SubjectDep,
) -> Response:
    """流式返回朗读音频字节；未生成或跨账户一律 404。

    音频按账户对象库授权校验后返回，不携带任何会话或凭据信息。
    """
    try:
        audio_bytes, media_type, content_length = service.get_read_aloud_audio(
            subject.account_id, conversation_id, message_id
        )
    except SpeechError as exc:
        raise _handle_speech_error(exc) from exc
    return Response(
        content=audio_bytes,
        media_type=media_type,
        headers={
            "Content-Length": str(content_length),
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
    )


@router.delete(
    "/conversations/{conversation_id}/messages/{message_id}/read-aloud",
    status_code=status.HTTP_410_GONE,
    responses=_RETIRED_RESPONSES,
)
def delete_read_aloud(
    conversation_id: str,
    message_id: str,
    request: Request,
    _subject: SubjectDep,
) -> None:
    """停止并清理朗读。已退役：不再接受删除请求，稳定返回 410。

    历史音频随旧消息保留，按账户导出与账户删除合同处置。
    """
    raise_retired_capability(
        request,
        endpoint="legacy.speech.read_aloud.delete",
        error=_RETIRED_ERROR,
        replacement_path="/",
        message=_RETIRED_MESSAGE,
    )
