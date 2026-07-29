"""Real Qwen TTS adapter for T062.

The adapter translates logical ``qwen_tts`` and ``qwen_tts_instruct`` capability
invocations into DashScope-native TTS API requests. It enforces the documented
input limits for each capability and returns a normalized output containing the
temporary audio URL, expiry timestamp and usage metadata.

The adapter only calls the TTS API; the caller (``QwenTtsNarrationSynthesizer``)
is responsible for transferring the temporary URL to controlled object storage
before it expires.

Pronunciation handling: the adapter accepts optional ``pronunciation_notes`` in
the payload. Known unit spoken forms are pre-expanded in the text before sending
to TTS. Formulas, abbreviations and foreign proper nouns that cannot be reliably
expanded are returned with ``degraded=True`` so the caller can surface an
explicit degradation hint.
"""

from __future__ import annotations

import re
from typing import Any

from science_companion.ai.adapters import (
    AdapterError,
    AdapterResult,
    CapabilityAdapter,
)
from science_companion.ai.qwen_client import QwenApiClient
from science_companion.contracts.ai import CapabilityRecord
from science_companion.contracts.workflows import RunContextEnvelope

# Official input limit: 512 tokens for Qwen3-TTS, ~600 characters for other models.
# We use a conservative character limit that works across both model families.
TTS_MAX_INPUT_CHARACTERS = 600

# Default voice for Chinese narration. Callers can override via payload.
DEFAULT_VOICE = "Cherry"

# Default audio format. The DashScope TTS API returns WAV by default.
DEFAULT_AUDIO_FORMAT = "wav"

# Common scientific unit spoken forms for text pre-processing before TTS.
# This is a subset of the accessibility service's dictionary; we duplicate it
# here so the adapter is self-contained and testable.
_UNIT_SPOKEN_FORMS: dict[str, str] = {
    "m/s²": "米每二次方秒",
    "m/s^2": "米每二次方秒",
    "m/s": "米每秒",
    "km/h": "千米每小时",
    "mol": "摩尔",
    "°C": "摄氏度",
    "Hz": "赫兹",
    "Pa": "帕斯卡",
    "kg": "千克",
    "km": "千米",
}

# Pattern to detect number+unit sequences for pre-expansion.
_NUMBER_UNIT_RE = re.compile(
    r"(?P<number>\d+(?:\.\d+)?)\s*(?P<unit>m/s²|m/s\^2|m/s|km/h|mol|°C|Hz|Pa|kg|km)"
)


def _expand_units(text: str) -> str:
    """Pre-expand known number+unit sequences to their spoken forms.

    This improves TTS pronunciation for scientific measurements by replacing
    symbols with their Chinese spoken equivalents before synthesis.
    """

    def _replace(match: re.Match[str]) -> str:
        number = match.group("number")
        unit = match.group("unit")
        spoken = _UNIT_SPOKEN_FORMS.get(unit)
        if spoken:
            return f"{number}{spoken}"
        return match.group(0)

    return _NUMBER_UNIT_RE.sub(_replace, text)


class QwenTtsAdapter(CapabilityAdapter):
    """Adapter for ``qwen_tts`` and ``qwen_tts_instruct``.

    The payload must contain:

    - ``text`` (required): the text to synthesize. Must have passed fact-lock
      validation before reaching the adapter.
    - ``voice`` (optional): voice identifier, default ``Cherry``.
    - ``language_type`` (optional): language hint for better pronunciation.
    - ``instructions`` (optional): instruction control text; only effective
      with the ``qwen_tts_instruct`` capability.
    - ``optimize_instructions`` (optional): whether to optimize instructions.
    - ``pronunciation_notes`` (optional): list of pronunciation note dicts
      with ``token``, ``kind``, ``spoken_form`` and ``degraded`` fields.

    The adapter returns the audio URL, audio id, expiry timestamp and usage
    under the ``audio_url``, ``audio_id``, ``expires_at`` and ``usage`` keys.
    """

    def __init__(self, client: QwenApiClient) -> None:
        self._client = client

    def call(
        self,
        capability: CapabilityRecord,
        run_context: RunContextEnvelope,
        payload: dict[str, Any],
    ) -> AdapterResult:
        text = payload.get("text")
        if not text or not isinstance(text, str) or not text.strip():
            raise AdapterError(
                code="missing_text",
                message="TTS payload must contain non-empty text.",
                retryable=False,
            )

        if len(text) > TTS_MAX_INPUT_CHARACTERS:
            raise AdapterError(
                code="text_too_long",
                message=(
                    f"TTS input length {len(text)} characters exceeds "
                    f"limit of {TTS_MAX_INPUT_CHARACTERS}."
                ),
                retryable=False,
            )

        # Pre-expand known units for better pronunciation.
        processed_text = _expand_units(text)

        voice = str(payload.get("voice") or DEFAULT_VOICE)
        language_type = payload.get("language_type")

        input_data: dict[str, Any] = {
            "text": processed_text,
            "voice": voice,
        }
        if language_type is not None:
            input_data["language_type"] = str(language_type)

        # Instruction control is only supported by qwen3-tts-instruct-flash.
        if capability.name == "qwen_tts_instruct":
            instructions = payload.get("instructions")
            if instructions:
                input_data["instructions"] = str(instructions)
                if payload.get("optimize_instructions"):
                    input_data["optimize_instructions"] = True

        request_body: dict[str, Any] = {
            "model": capability.model_id,
            "input": input_data,
        }

        response_body = self._client.text_to_speech(request_body)

        audio_info = self._extract_audio(response_body)
        usage = response_body.get("usage")

        # Collect degraded pronunciation notes to surface in the output.
        degraded_notes: list[dict[str, Any]] = []
        for note in payload.get("pronunciation_notes") or []:
            if isinstance(note, dict) and note.get("degraded"):
                degraded_notes.append(note)

        output: dict[str, Any] = {
            "audio_url": audio_info["url"],
            "audio_id": audio_info["id"],
            "expires_at": audio_info["expires_at"],
            "mime_type": "audio/wav",
            "processed_text": processed_text,
            "degraded_pronunciation_notes": degraded_notes,
        }

        return AdapterResult(
            actual_model_id=response_body.get("model") or capability.model_id,
            output=output,
            usage=usage if isinstance(usage, dict) else None,
        )

    @staticmethod
    def _extract_audio(response_body: dict[str, Any]) -> dict[str, Any]:
        """Extract audio URL, id and expiry from the TTS response body."""
        output = response_body.get("output")
        if not isinstance(output, dict):
            raise AdapterError(
                code="empty_response",
                message="Qwen TTS response contained no output.",
                retryable=False,
            )
        audio = output.get("audio")
        if not isinstance(audio, dict):
            raise AdapterError(
                code="empty_response",
                message="Qwen TTS response contained no audio.",
                retryable=False,
            )
        url = audio.get("url")
        if not url or not isinstance(url, str):
            raise AdapterError(
                code="empty_response",
                message="Qwen TTS response contained no audio URL.",
                retryable=False,
            )
        audio_id = str(audio.get("id") or "")
        expires_at = audio.get("expires_at")
        return {
            "url": url,
            "id": audio_id,
            "expires_at": expires_at if isinstance(expires_at, int) else 0,
        }
