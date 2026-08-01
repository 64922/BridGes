"""Tests for the Qwen ASR-backed audio/video extraction port.

These tests wire a stub ``ModelGateway`` so they do not require a real API key.
They verify that the extraction produces structured ``AudioVideoDerivedData``,
binds the model run lock, marks low-confidence scientific terms, preserves the
original asset timeline and respects account/project scope.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from typing import Any

from bridges.ai import CapabilityRegistry, ModelGateway
from bridges.ai.adapters import AdapterResult, CapabilityAdapter
from bridges.contracts.ai import (
    CapabilityKind,
    CapabilityRecord,
    RetryPolicy,
)
from bridges.contracts.media import (
    AudioVideoDerivedData,
    MediaAssetKind,
    MediaAssetStatus,
    MediaGateResult,
    MediaQualityGate,
    MediaUploadRequest,
)
from bridges.contracts.science import LicenseState, MediaType
from bridges.contracts.workflows import RunContextEnvelope
from bridges.media import MediaIngestionService


def _context(run_id: str = "run-1") -> RunContextEnvelope:
    return RunContextEnvelope(
        run_id=run_id,
        account_id="account-1",
        project_id="project-1",
        workflow_name="media_ingestion",
        workflow_version="1",
        submitted_at=datetime.now(UTC),
    )


def _asr_capability(name: str) -> CapabilityRecord:
    return CapabilityRecord(
        name=name,
        version="1",
        kind=CapabilityKind.MODEL,
        vendor="qwen",
        region="cn-beijing",
        model_id="qwen3-asr-flash" if name == "qwen_asr_short" else "qwen3-asr-flash-filetrans",
        input_schema_version="audio-upload-v1",
        output_schema_version="transcript-v1",
        retry_policy=RetryPolicy(max_attempts=1),
    )


class _StubAsrAdapter(CapabilityAdapter):
    """Returns a programmable transcript; captures the payload for assertions."""

    def __init__(self, transcript: str) -> None:
        self.transcript = transcript
        self.captured_payloads: list[dict[str, Any]] = []

    def call(
        self,
        capability: CapabilityRecord,
        run_context: RunContextEnvelope,
        payload: dict[str, Any],
    ) -> AdapterResult:
        self.captured_payloads.append(payload)
        return AdapterResult(
            actual_model_id=capability.model_id,
            output={"transcript": self.transcript, "language": "zh"},
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )


def _gateway_with_asr(transcript: str) -> ModelGateway:
    registry = CapabilityRegistry()
    registry.register(_asr_capability("qwen_asr_short"))
    registry.register(_asr_capability("qwen_asr_long"))
    gateway = ModelGateway(registry)
    adapter = _StubAsrAdapter(transcript)
    gateway.register_adapter("qwen_asr_short", "1", adapter)
    gateway.register_adapter("qwen_asr_long", "1", adapter)
    return gateway


def _mp3_bytes() -> bytes:
    return b"ID3\x04\x00" + b"\x00" * 28


def _mp4_bytes() -> bytes:
    return b"\x00\x00\x00\x20ftypisom" + b"\x00" * 12


def test_audio_extraction_records_model_run_lock() -> None:
    gateway = _gateway_with_asr("欢迎收听科学讲座。今天讨论光合作用。")
    service = MediaIngestionService(model_gateway=gateway)

    request = MediaUploadRequest(
        filename="lecture-zh.mp3",
        media_type=MediaType.AUDIO_MPEG,
        content=base64.b64encode(_mp3_bytes()).decode("ascii"),
        license_state=LicenseState.USER_OWNED,
    )
    run = service.ingest_upload("account-alice", None, request)

    assert run.asset_id is not None
    assert run.status == "completed"

    projection = service.get_asset("account-alice", run.asset_id)
    assert projection.source_asset.status == MediaAssetStatus.PARSED
    assert len(projection.derived_assets) == 1

    derived = projection.derived_assets[0]
    assert derived.derivation_type == MediaAssetKind.AUDIO_TRANSCRIPT
    assert derived.tool == "bridges.asr.qwen"
    assert derived.parameters["asr_capability"] == "qwen_asr_short"

    lock = derived.parameters.get("model_run_lock")
    assert lock is not None
    assert lock["capability_name"] == "qwen_asr_short"
    assert lock["actual_model_id"] == "qwen3-asr-flash"
    assert lock["status"] == "success"
    assert lock["project_id"] == ""

    payload = AudioVideoDerivedData(**derived.payload)
    assert len(payload.transcript_segments) >= 1
    assert any("科学讲座" in seg.text for seg in payload.transcript_segments)
    assert payload.language == "zh"


def test_low_confidence_scientific_term_is_marked() -> None:
    gateway = _gateway_with_asr("Today we discuss [?photosynthesis?].")
    service = MediaIngestionService(model_gateway=gateway)

    request = MediaUploadRequest(
        filename="biology-photosynthesis.mp3",
        media_type=MediaType.AUDIO_MPEG,
        content=base64.b64encode(_mp3_bytes()).decode("ascii"),
        license_state=LicenseState.USER_OWNED,
    )
    run = service.ingest_upload("account-alice", None, request)
    assert run.asset_id is not None

    projection = service.get_asset("account-alice", run.asset_id)
    derived = projection.derived_assets[0]
    payload = AudioVideoDerivedData(**derived.payload)

    low_confidence = [seg for seg in payload.transcript_segments if seg.low_confidence]
    assert len(low_confidence) >= 1
    segment = low_confidence[0]
    assert "photosynthesis" in segment.text
    assert "[?" not in segment.text
    assert any(w.text == "photosynthesis" for w in segment.words)


def test_missing_audio_track_is_marked() -> None:
    gateway = _gateway_with_asr("ignored")
    service = MediaIngestionService(model_gateway=gateway)

    request = MediaUploadRequest(
        filename="silent-demo.mp4",
        media_type=MediaType.VIDEO_MP4,
        content=base64.b64encode(_mp4_bytes()).decode("ascii"),
        license_state=LicenseState.USER_OWNED,
    )
    run = service.ingest_upload("account-alice", None, request)
    assert run.asset_id is not None

    projection = service.get_asset("account-alice", run.asset_id)
    derived = projection.derived_assets[0]
    payload = AudioVideoDerivedData(**derived.payload)

    assert payload.missing_audio_track is True
    assert len(payload.transcript_segments) == 1
    assert payload.transcript_segments[0].text == "[无音轨]"
    # Video still produces keyframes even when the audio track is missing.
    assert len(payload.keyframes) >= 2


def test_video_extraction_creates_keyframes() -> None:
    gateway = _gateway_with_asr("请看这张科学示意图。")
    service = MediaIngestionService(model_gateway=gateway)

    request = MediaUploadRequest(
        filename="slides-diagram.mp4",
        media_type=MediaType.VIDEO_MP4,
        content=base64.b64encode(_mp4_bytes()).decode("ascii"),
        license_state=LicenseState.USER_OWNED,
    )
    run = service.ingest_upload("account-alice", None, request)
    assert run.asset_id is not None

    projection = service.get_asset("account-alice", run.asset_id)
    derived = projection.derived_assets[0]
    payload = AudioVideoDerivedData(**derived.payload)

    assert len(payload.keyframes) >= 2
    assert any("diagram" in k.interpretation for k in payload.keyframes)


def test_asr_failure_keeps_asset_for_correction() -> None:
    registry = CapabilityRegistry()
    registry.register(_asr_capability("qwen_asr_short"))
    gateway = ModelGateway(registry)

    class _FailingAdapter(CapabilityAdapter):
        def call(
            self,
            capability: CapabilityRecord,
            run_context: RunContextEnvelope,
            payload: dict[str, Any],
        ) -> AdapterResult:
            from bridges.ai.adapters import TransientError
            raise TransientError("ASR service unavailable")

    gateway.register_adapter("qwen_asr_short", "1", _FailingAdapter())
    service = MediaIngestionService(model_gateway=gateway)

    request = MediaUploadRequest(
        filename="lecture-zh.mp3",
        media_type=MediaType.AUDIO_MPEG,
        content=base64.b64encode(_mp3_bytes()).decode("ascii"),
        license_state=LicenseState.USER_OWNED,
    )
    run = service.ingest_upload("account-alice", None, request)

    assert run.asset_id is not None
    assert run.status == "completed"

    # The original source asset is preserved with a degraded placeholder.
    projection = service.get_asset("account-alice", run.asset_id)
    assert projection.source_asset.status == MediaAssetStatus.PARSED
    derived = projection.derived_assets[0]
    assert derived.parameters.get("degraded") is True
    assert derived.parameters.get("degradation_reason") is not None
    payload = AudioVideoDerivedData(**derived.payload)
    assert any("[ASR转录失败]" in seg.text for seg in payload.transcript_segments)



def test_long_audio_uses_filetrans_capability() -> None:
    gateway = _gateway_with_asr("这是一段很长的录音。")
    service = MediaIngestionService(model_gateway=gateway)

    # Create content large enough to push the estimated duration over 300s.
    large_content = b"ID3\x04\x00" + b"\x00" * (400 * 1024)
    request = MediaUploadRequest(
        filename="long-lecture.mp3",
        media_type=MediaType.AUDIO_MPEG,
        content=base64.b64encode(large_content).decode("ascii"),
        license_state=LicenseState.USER_OWNED,
    )
    run = service.ingest_upload("account-alice", None, request)
    assert run.asset_id is not None

    projection = service.get_asset("account-alice", run.asset_id)
    derived = projection.derived_assets[0]
    assert derived.parameters["asr_capability"] == "qwen_asr_long"
    lock = derived.parameters["model_run_lock"]
    assert lock["capability_name"] == "qwen_asr_long"
    assert lock["actual_model_id"] == "qwen3-asr-flash-filetrans"
