"""Real Qwen ASR adapter for T060.

The adapter translates logical ``qwen_asr_short`` and ``qwen_asr_long``
capability invocations into DashScope native synchronous
``multimodal-generation`` requests carrying inline base64 audio
（``input.messages`` + ``audio`` 内容项，百炼官方示例格式；OpenAI 兼容
端点的 ``audio_url`` 内容类型不被该模型接受，实测返回 400）。它按能力
强制文档化输入上限，并返回归一化的转写文本供媒体提取层生成带时间的
``DerivedAsset`` 载荷。
"""

from __future__ import annotations

import base64
from typing import Any

from bridges.ai.adapters import (
    AdapterError,
    AdapterResult,
    CapabilityAdapter,
)
from bridges.ai.qwen_client import QwenApiClient
from bridges.contracts.ai import CapabilityRecord
from bridges.contracts.workflows import RunContextEnvelope

# Official input limits documented for the Qwen ASR family.
# These are the single source of truth — import them, don't re-define.
SHORT_AUDIO_MAX_SECONDS = 300  # 5 minutes
SHORT_AUDIO_MAX_BYTES = 10 * 1024 * 1024
LONG_AUDIO_MAX_SECONDS = 12 * 3600  # 12 hours
LONG_AUDIO_MAX_BYTES = 2 * 1024 * 1024 * 1024

# MIME types supported by the Qwen ASR models per official documentation.
#: 支持的音频 MIME 白名单（Issue 30 听写入口与服务共用）。
SUPPORTED_AUDIO_MIME_TYPES: frozenset[str] = frozenset({
    "audio/mpeg",
    "audio/mp3",
    "audio/wav",
    "audio/wave",
    "audio/ogg",
    "audio/opus",
    "audio/flac",
    "audio/aac",
    "audio/mp4",
    "audio/x-m4a",
    "audio/amr",
    "audio/webm",
})


class QwenAsrAdapter(CapabilityAdapter):
    """Adapter for ``qwen_asr_short`` and ``qwen_asr_long``.

    The payload must contain:

    - ``audio_base64`` (required): base64-encoded audio bytes.
    - ``mime_type`` (optional): MIME type of the audio, default ``audio/mpeg``.
    - ``duration_seconds`` (optional): estimated duration; used for limit checks.
    - ``language`` (optional): declared/detected language hint.
    - ``prompt`` (optional): task-specific prompt; a sensible default is used
      when absent.

    The adapter returns the model's transcript text under the ``transcript`` key
    together with any language hint returned by the model or supplied by the
    caller.
    """

    def __init__(self, client: QwenApiClient) -> None:
        self._client = client

    def call(
        self,
        capability: CapabilityRecord,
        run_context: RunContextEnvelope,
        payload: dict[str, Any],
    ) -> AdapterResult:
        audio_base64 = payload.get("audio_base64")
        if not audio_base64 or not isinstance(audio_base64, str):
            raise AdapterError(
                code="missing_audio",
                message="ASR payload must contain a base64-encoded audio.",
                retryable=False,
            )

        mime_type = str(payload.get("mime_type") or "audio/mpeg")
        duration_seconds = float(payload.get("duration_seconds") or 0)

        if mime_type not in SUPPORTED_AUDIO_MIME_TYPES:
            raise AdapterError(
                code="unsupported_mime_type",
                message=(
                    f"Unsupported audio MIME type {mime_type!r}. "
                    f"Supported: {', '.join(sorted(SUPPORTED_AUDIO_MIME_TYPES))}."
                ),
                retryable=False,
            )

        try:
            audio_bytes = base64.b64decode(audio_base64)
        except Exception as exc:
            raise AdapterError(
                code="invalid_audio_base64",
                message=f"ASR audio could not be decoded: {exc}",
                retryable=False,
            ) from exc

        self._enforce_limits(capability, audio_bytes, duration_seconds)

        data_url = f"data:{mime_type};base64,{audio_base64}"
        prompt = str(
            payload.get("prompt")
            or "Transcribe the audio accurately. Preserve the original language."
        )

        # DashScope 原生同步格式：system 消息承载识别上下文（官方示例），
        # user 消息携带内联音频。同步服务不接受 X-DashScope-Async 头。
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": [{"text": prompt}]},
            {"role": "user", "content": [{"audio": data_url}]},
        ]

        request_body: dict[str, Any] = {
            "model": capability.model_id,
            "input": {"messages": messages},
            "parameters": {
                "temperature": payload.get("temperature", 0.0),
                "max_tokens": payload.get("max_tokens", 4096),
            },
        }

        response_body = self._client.dashscope_native(
            "/api/v1/services/aigc/multimodal-generation/generation",
            request_body,
        )
        transcript = self._extract_transcript(response_body)
        return AdapterResult(
            actual_model_id=capability.model_id,
            output={
                "transcript": transcript,
                "language": payload.get("language"),
            },
            usage=response_body.get("usage"),
        )

    @staticmethod
    def _extract_transcript(response_body: dict[str, Any]) -> str:
        """从同步多模态响应提取转写文本。

        响应结构为 ``output.choices[0].message.content`` 文本项列表
        （每项 ``{"text": "..."}``，静音输入时为空列表）。
        """
        output = response_body.get("output")
        if not isinstance(output, dict):
            raise AdapterError(
                code="invalid_response",
                message="ASR 接口返回格式异常。",
                retryable=False,
            )
        choices = output.get("choices")
        if not (isinstance(choices, list) and choices):
            raise AdapterError(
                code="invalid_response",
                message="ASR 接口返回格式异常。",
                retryable=False,
            )
        content = choices[0].get("message", {}).get("content")
        if not isinstance(content, list):
            raise AdapterError(
                code="invalid_response",
                message="ASR 接口返回格式异常。",
                retryable=False,
            )
        return "".join(
            str(item["text"])
            for item in content
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        )

    def _enforce_limits(
        self,
        capability: CapabilityRecord,
        audio_bytes: bytes,
        duration_seconds: float,
    ) -> None:
        if capability.name == "qwen_asr_long":
            max_seconds = LONG_AUDIO_MAX_SECONDS
            max_bytes = LONG_AUDIO_MAX_BYTES
        else:
            # Default to short-audio limits for ``qwen_asr_short`` and any
            # other capability routed through this adapter.
            max_seconds = SHORT_AUDIO_MAX_SECONDS
            max_bytes = SHORT_AUDIO_MAX_BYTES

        if duration_seconds > max_seconds:
            raise AdapterError(
                code="audio_too_long",
                message=(
                    f"Audio duration {duration_seconds}s exceeds "
                    f"{capability.name} limit of {max_seconds}s."
                ),
                retryable=False,
            )
        if len(audio_bytes) > max_bytes:
            raise AdapterError(
                code="audio_too_large",
                message=(
                    f"Audio size {len(audio_bytes)} bytes exceeds "
                    f"{capability.name} limit of {max_bytes} bytes."
                ),
                retryable=False,
            )

