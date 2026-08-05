"""听写与单条回答朗读 API 路由（Issue 30）。

听写：前端录音并停止后，把完整音频 POST 到固定 ASR 快照，返回可编辑
转写文本；音频只在请求体内存中直传，不落盘。朗读：每条已完成的助手
回答可 POST 生成朗读（固定 TTS 快照），GET 投影/音频，DELETE 停止并
清理。两项能力在调用前按账户级探测快照门控（与聊天发送同一模式）：
无 Key / 探测中 / 对应能力不可用分别返回可操作中文提示，绝不切换
模型或模拟成功。音频请求体只含音频与固定提示，不携带密钥、画像或
无关聊天历史。
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
from bridges.contracts.credentials import ProbeStatus
from bridges.contracts.speech import (
    DictationProjection,
    ReadAloudProjection,
    SpeechError,
)
from bridges.credentials.service import KeyCredentialService
from bridges.speech.service import SpeechService

router = APIRouter(prefix="/chat", tags=["speech"])


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


def _get_credential_service(request: Request) -> KeyCredentialService:
    service: KeyCredentialService | None = getattr(
        request.app.state, "credential_service", None
    )
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=ChatError(
                error="credentials_unavailable",
                message="凭据服务未启用，当前实例拒绝语音操作。",
            ).model_dump(),
        )
    return service


CredentialServiceDep = Annotated[
    KeyCredentialService, Depends(_get_credential_service)
]


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=ChatError(error=code, message=message).model_dump(),
    )


def _ensure_capability_ready(
    account_id: str,
    capability_id: str,
    display_name: str,
    credential_service: KeyCredentialService,
) -> None:
    """调用前核对账户级能力探测快照（ADR-0005/0009）。

    ASR 与 TTS 各自独立探测；任何一项不可用时只停用对应入口并说明
    原因，不切换到其他模型、不模拟成功。
    """
    try:
        projection = credential_service.get_projection(account_id)
    except Exception as exc:  # noqa: BLE001 - 凭据存储异常统一折叠为可操作提示
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "credential_store_unavailable",
            "凭据存储当前不可用，请稍后重试。",
        ) from exc
    if not projection.configured:
        raise _error(
            status.HTTP_409_CONFLICT,
            "no_api_key",
            "尚未配置 Qwen API Key，请前往「设置」中的密钥页配置后再试。",
        )
    capability = next(
        (
            item
            for item in projection.capabilities
            if item.capability_id == capability_id
        ),
        None,
    )
    if capability is None:
        raise _error(
            status.HTTP_409_CONFLICT,
            "capability_unavailable",
            f"{display_name}能力信息缺失，请前往「设置」重新探测。",
        )
    if capability.status == ProbeStatus.PROBING:
        raise _error(
            status.HTTP_409_CONFLICT,
            "capability_probing",
            f"{display_name}能力正在探测中，请稍候再试。",
        )
    if capability.status != ProbeStatus.AVAILABLE:
        reason = capability.message or "未知原因"
        raise _error(
            status.HTTP_409_CONFLICT,
            "capability_unavailable",
            f"{display_name}能力当前不可用：{reason}。请前往「设置」重新探测。",
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
    credential_service: CredentialServiceDep,
    subject: SubjectDep,
) -> Response:
    """提交一段完整录音音频到固定 ASR 快照，返回可编辑转写文本。

    音频通过请求体原样上传（Content-Type 为音频 MIME，时长经
    ``x-bridges-audio-duration`` 头声明）；只接受音频 MIME，空音频、
    超限音频与不支持格式在调用前确定性拒绝。转写失败返回 failed
    投影（含稳定错误码与中文原因），不产生用户消息、不落盘音频。
    """
    _ensure_capability_ready(
        subject.account_id, "asr", "语音转写", credential_service
    )
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
    response_model=ReadAloudProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ChatError},
        status.HTTP_404_NOT_FOUND: {"model": ChatError},
        status.HTTP_409_CONFLICT: {"model": ChatError},
    },
)
def generate_read_aloud(
    conversation_id: str,
    message_id: str,
    service: SpeechServiceDep,
    credential_service: CredentialServiceDep,
    subject: SubjectDep,
) -> Response:
    """为一条已完成的助手回答生成朗读（固定 TTS 快照）。

    同一条回答的受控重试复用同一消息正文重新合成；成功后旧音频先
    清理再写新对象。生成失败返回 failed 投影与重试语义，回答正文
    不受影响。
    """
    _ensure_capability_ready(
        subject.account_id, "tts", "语音朗读", credential_service
    )
    try:
        projection = service.generate_read_aloud(
            subject.account_id, conversation_id, message_id
        )
    except SpeechError as exc:
        raise _handle_speech_error(exc) from exc
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=projection.model_dump(mode="json"),
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
    response_model=ReadAloudProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ChatError},
        status.HTTP_404_NOT_FOUND: {"model": ChatError},
    },
)
def delete_read_aloud(
    conversation_id: str,
    message_id: str,
    service: SpeechServiceDep,
    subject: SubjectDep,
) -> Response:
    """停止并清理朗读：删除账户对象库中的音频并复位状态，幂等。"""
    try:
        projection = service.delete_read_aloud(
            subject.account_id, conversation_id, message_id
        )
    except SpeechError as exc:
        raise _handle_speech_error(exc) from exc
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=projection.model_dump(mode="json"),
    )
