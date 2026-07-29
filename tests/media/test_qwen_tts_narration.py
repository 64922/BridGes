"""Tests for the QwenTtsNarrationSynthesizer (T062).

These tests verify that the narration synthesizer correctly:
- Calls TTS via the model gateway and transfers the temporary URL to controlled
  storage.
- Records MIME type, byte size and content hash in the stored audio.
- Marks narration as FAILED when TTS or download fails, while keeping the text
  answer deliverable.
- Surfaces degraded pronunciation notes from the TTS adapter output.
- Fails gracefully when no synthesis context is provided.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx

from science_companion.ai import ModelGateway
from science_companion.ai.capability_registry import CapabilityRegistry
from science_companion.ai.qwen_client import QwenApiClient
from science_companion.ai.qwen_tts_adapter import QwenTtsAdapter
from science_companion.contracts.ai import (
    CapabilityKind,
    CapabilityRecord,
    ModelCallStatus,
    RetryPolicy,
)
from science_companion.contracts.media import (
    NarrationAudio,
    NarrationSynthesisStatus,
    PronunciationNote,
    PronunciationNoteKind,
)
from science_companion.contracts.workflows import RunContextEnvelope
from science_companion.media.accessibility_service import (
    InMemoryAudioStorage,
    NarrationSynthesisContext,
    QwenTtsNarrationSynthesizer,
)

ACCOUNT = "account-1"
PROJECT = "project-1"
TTS_URL = "http://mock-audio.example.com/audio.wav"
AUDIO_BYTES = b"\x52\x49\x46\x46\x24\x00\x00\x00\x57\x41\x56\x45"


# ── Helpers ─────────────────────────────────────────────────────────


def _tts_capability() -> CapabilityRecord:
    return CapabilityRecord(
        name="qwen_tts",
        version="1",
        kind=CapabilityKind.MODEL,
        vendor="qwen",
        region="cn-beijing",
        model_id="qwen3-tts-flash",
        input_schema_version="tts-text-v1",
        output_schema_version="tts-audio-v1",
        supported_modalities=["text", "audio"],
        retry_policy=RetryPolicy(max_attempts=1),
    )


def _tts_response(
    *,
    url: str = TTS_URL,
    expires_at: int = 1766113409,
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "status_code": 200,
            "request_id": "req-narration-001",
            "code": "",
            "message": "",
            "output": {
                "text": None,
                "finish_reason": "stop",
                "choices": None,
                "audio": {
                    "data": "",
                    "url": url,
                    "id": "audio_req-narration-001",
                    "expires_at": expires_at,
                },
            },
            "usage": {"input_tokens": 0, "output_tokens": 0, "characters": 50},
        },
    )


def _build_gateway(
    handler: Any,
) -> tuple[ModelGateway, QwenApiClient]:
    """Build a ModelGateway with a QwenTtsAdapter backed by a mock transport."""
    client = QwenApiClient(api_key=None, workspace_id=None, region="cn-beijing")
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = QwenTtsAdapter(client)

    registry = CapabilityRegistry()
    registry.register(_tts_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_tts", "1", adapter)
    return gateway, client


def _narration(
    *,
    text: str = "重力加速度约为 9.8 m/s²。",
    notes: list[PronunciationNote] | None = None,
) -> NarrationAudio:
    return NarrationAudio(
        narration_id="narr-001",
        language="zh-CN",
        text=text,
        voice="default",
        status=NarrationSynthesisStatus.PENDING_SYNTHESIS,
        pronunciation_notes=notes or [
            PronunciationNote(
                token="9.8",
                kind=PronunciationNoteKind.NUMBER,
                spoken_form="9.8",
            ),
            PronunciationNote(
                token="m/s²",
                kind=PronunciationNoteKind.UNIT,
                spoken_form="米每二次方秒",
            ),
        ],
        claim_ids=["claim-1"],
    )


def _context() -> NarrationSynthesisContext:
    return NarrationSynthesisContext(
        account_id=ACCOUNT,
        project_id=PROJECT,
        run_id="tts-bundle-001",
    )


def _download_handler(
    *,
    content: bytes = AUDIO_BYTES,
    status: int = 200,
) -> Any:
    """Return a mock transport handler for audio download requests."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=content)

    return handler


# ── Tests ───────────────────────────────────────────────────────────


def test_successful_synthesis_transfers_to_controlled_storage() -> None:
    """TTS succeeds, audio is downloaded and stored with metadata."""
    captured: dict[str, Any] = {}

    def tts_handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _tts_response()

    gateway, _ = _build_gateway(tts_handler)
    storage = InMemoryAudioStorage()
    download_client = httpx.Client(
        transport=httpx.MockTransport(_download_handler())
    )

    synthesizer = QwenTtsNarrationSynthesizer(
        model_gateway=gateway,
        audio_storage=storage,
        download_client=download_client,
    )

    result = synthesizer.synthesize(_narration(), _context())

    assert result.status == NarrationSynthesisStatus.SYNTHESIZED
    assert result.audio_ref is not None
    assert result.audio_ref.startswith("memory://tts/")
    # Audio bytes should be retrievable from storage.
    stored_bytes = storage.retrieve(result.audio_ref)
    assert stored_bytes == AUDIO_BYTES


def test_stored_audio_records_mime_size_and_hash() -> None:
    """The InMemoryAudioStorage stores MIME, byte size and content hash."""

    def tts_handler(_req: httpx.Request) -> httpx.Response:
        return _tts_response()

    gateway, _ = _build_gateway(tts_handler)
    storage = InMemoryAudioStorage()
    download_client = httpx.Client(
        transport=httpx.MockTransport(_download_handler())
    )

    synthesizer = QwenTtsNarrationSynthesizer(
        model_gateway=gateway,
        audio_storage=storage,
        download_client=download_client,
    )

    synthesizer.synthesize(_narration(), _context())

    # Verify the stored audio metadata via a direct store call to check the
    # StoredAudio shape.
    stored = storage.store(
        audio_bytes=AUDIO_BYTES,
        mime_type="audio/wav",
        account_id=ACCOUNT,
        project_id=PROJECT,
        narration_id="narr-check",
    )
    assert stored.mime_type == "audio/wav"
    assert stored.byte_size == len(AUDIO_BYTES)
    assert len(stored.content_hash) == 64  # SHA-256 hex


def test_tts_failure_marks_narration_failed() -> None:
    """When TTS returns BLOCKED, narration is FAILED but text remains intact."""

    def tts_handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    gateway, _ = _build_gateway(tts_handler)
    download_client = httpx.Client(
        transport=httpx.MockTransport(_download_handler())
    )

    synthesizer = QwenTtsNarrationSynthesizer(
        model_gateway=gateway,
        download_client=download_client,
    )

    narration = _narration()
    result = synthesizer.synthesize(narration, _context())

    assert result.status == NarrationSynthesisStatus.FAILED
    assert result.audio_ref is None
    # Text answer remains deliverable.
    assert result.text == narration.text


def test_download_failure_marks_narration_failed() -> None:
    """When audio download fails, narration is FAILED."""
    tts_calls: list[bool] = []

    def tts_handler(_req: httpx.Request) -> httpx.Response:
        tts_calls.append(True)
        return _tts_response()

    gateway, _ = _build_gateway(tts_handler)
    download_client = httpx.Client(
        transport=httpx.MockTransport(_download_handler(status=500))
    )

    synthesizer = QwenTtsNarrationSynthesizer(
        model_gateway=gateway,
        download_client=download_client,
    )

    result = synthesizer.synthesize(_narration(), _context())

    assert result.status == NarrationSynthesisStatus.FAILED
    assert result.audio_ref is None
    # TTS was called successfully.
    assert len(tts_calls) == 1


def test_no_context_marks_narration_failed() -> None:
    """Without context, synthesis cannot create a scoped run and fails."""
    def tts_handler(_req: httpx.Request) -> httpx.Response:
        return _tts_response()

    gateway, _ = _build_gateway(tts_handler)
    download_client = httpx.Client(
        transport=httpx.MockTransport(_download_handler())
    )

    synthesizer = QwenTtsNarrationSynthesizer(
        model_gateway=gateway,
        download_client=download_client,
    )

    result = synthesizer.synthesize(_narration(), context=None)

    assert result.status == NarrationSynthesisStatus.FAILED
    assert result.audio_ref is None


def test_degraded_pronunciation_notes_preserved_through_synthesis() -> None:
    """Pronunciation notes flagged as degraded are preserved in the result.

    The accessibility service marks formulas and unknown abbreviations as
    ``degraded=True`` when no reliable spoken form exists. The TTS adapter
    passes these through and the synthesizer preserves them in the output.
    """
    notes = [
        PronunciationNote(
            token="E=mc²",
            kind=PronunciationNoteKind.FORMULA,
            spoken_form=None,
            degraded=True,
        ),
        PronunciationNote(
            token="9.8",
            kind=PronunciationNoteKind.NUMBER,
            spoken_form="9.8",
        ),
        PronunciationNote(
            token="m/s²",
            kind=PronunciationNoteKind.UNIT,
            spoken_form="米每二次方秒",
        ),
    ]

    def tts_handler(_req: httpx.Request) -> httpx.Response:
        return _tts_response()

    gateway, _ = _build_gateway(tts_handler)
    download_client = httpx.Client(
        transport=httpx.MockTransport(_download_handler())
    )

    synthesizer = QwenTtsNarrationSynthesizer(
        model_gateway=gateway,
        download_client=download_client,
    )

    narration = _narration(
        text="质能方程 E=mc² 与重力加速度 9.8 m/s²。",
        notes=notes,
    )
    result = synthesizer.synthesize(narration, _context())

    assert result.status == NarrationSynthesisStatus.SYNTHESIZED
    # The formula note should remain degraded through the synthesis pipeline.
    formula_note = next(
        n for n in result.pronunciation_notes if n.token == "E=mc²"
    )
    assert formula_note.degraded is True
    # Non-degraded notes should remain non-degraded.
    number_note = next(
        n for n in result.pronunciation_notes if n.token == "9.8"
    )
    assert number_note.degraded is False


def test_missing_audio_url_marks_narration_failed() -> None:
    """When TTS output has no audio URL, narration is FAILED."""
    def tts_handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status_code": 200,
                "request_id": "req-no-url",
                "code": "",
                "message": "",
                "output": {
                    "audio": {
                        "url": "",
                        "id": "audio-x",
                        "expires_at": 0,
                    }
                },
                "usage": {},
            },
        )

    gateway, _ = _build_gateway(tts_handler)
    download_client = httpx.Client(
        transport=httpx.MockTransport(_download_handler())
    )

    synthesizer = QwenTtsNarrationSynthesizer(
        model_gateway=gateway,
        download_client=download_client,
    )

    result = synthesizer.synthesize(_narration(), _context())

    assert result.status == NarrationSynthesisStatus.FAILED
    assert result.audio_ref is None


def test_language_type_passed_from_narration() -> None:
    """The narration language is forwarded as language_type to the TTS adapter."""
    captured: dict[str, Any] = {}

    def tts_handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _tts_response()

    gateway, _ = _build_gateway(tts_handler)
    download_client = httpx.Client(
        transport=httpx.MockTransport(_download_handler())
    )

    synthesizer = QwenTtsNarrationSynthesizer(
        model_gateway=gateway,
        download_client=download_client,
    )

    narration = NarrationAudio(
        narration_id="narr-lang",
        language="en-US",
        text="The gravitational acceleration is 9.8 m/s².",
        pronunciation_notes=[],
    )
    result = synthesizer.synthesize(narration, _context())

    assert result.status == NarrationSynthesisStatus.SYNTHESIZED
    body = captured["body"]
    assert body["input"]["language_type"] == "en-US"


def test_audio_ref_uses_controlled_storage_scheme() -> None:
    """The audio reference should point to the controlled storage, not the vendor URL."""
    def tts_handler(_req: httpx.Request) -> httpx.Response:
        return _tts_response(url="http://vendor-temp.example.com/123.wav")

    gateway, _ = _build_gateway(tts_handler)
    download_client = httpx.Client(
        transport=httpx.MockTransport(_download_handler())
    )

    synthesizer = QwenTtsNarrationSynthesizer(
        model_gateway=gateway,
        download_client=download_client,
    )

    result = synthesizer.synthesize(_narration(), _context())

    assert result.status == NarrationSynthesisStatus.SYNTHESIZED
    assert result.audio_ref is not None
    # Must NOT be the vendor temporary URL.
    assert "vendor-temp" not in result.audio_ref
    assert result.audio_ref.startswith("memory://tts/")
