"""Real Qwen ASR adapter for T060.

The adapter translates logical ``qwen_asr_short`` and ``qwen_asr_long``
capability invocations into Qwen OpenAI-compatible Chat Completions requests
carrying base64-encoded audio. It enforces the documented input limits for each
capability and returns a normalized transcript output that the media extraction
layer turns into timed ``DerivedAsset`` payloads.
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
_SUPPORTED_AUDIO_MIME_TYPES: frozenset[str] = frozenset({
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

        if mime_type not in _SUPPORTED_AUDIO_MIME_TYPES:
            raise AdapterError(
                code="unsupported_mime_type",
                message=(
                    f"Unsupported audio MIME type {mime_type!r}. "
                    f"Supported: {', '.join(sorted(_SUPPORTED_AUDIO_MIME_TYPES))}."
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

        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": [
                    {"type": "audio_url", "audio_url": {"url": data_url}},
                    {"type": "text", "text": prompt},
                ],
            }
        ]

        request_body: dict[str, Any] = {
            "model": capability.model_id,
            "messages": messages,
            "temperature": payload.get("temperature", 0.0),
            "max_tokens": payload.get("max_tokens", 4096),
        }

        response_body = self._client.chat_completions(request_body)
        content = self._first_choice_content(response_body)
        return AdapterResult(
            actual_model_id=response_body.get("model") or capability.model_id,
            output={
                "transcript": content,
                "language": payload.get("language"),
            },
            usage=response_body.get("usage"),
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

    @staticmethod
    def _first_choice_content(response_body: dict[str, Any]) -> str:
        choices = response_body.get("choices")
        if not choices or not isinstance(choices, list):
            raise AdapterError(
                code="empty_response",
                message="Qwen ASR response contained no choices.",
                retryable=False,
            )
        choice = choices[0]
        if not isinstance(choice, dict):
            raise AdapterError(
                code="empty_response",
                message="Qwen ASR response contained no choices.",
                retryable=False,
            )
        return str(choice.get("message", {}).get("content", ""))
