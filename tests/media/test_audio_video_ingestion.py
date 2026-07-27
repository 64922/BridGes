"""Module-interface tests for audio/video media ingestion and correction (T031).

The seam under test: an authenticated subject uploads audio or video, receives
a timed transcript, speaker segments, captions and keyframe interpretations,
and can correct low-confidence terms while preserving the original asset and
the full correction chain.
"""

from __future__ import annotations

import base64

import pytest

from science_companion.contracts.identity import AuthMethod, SubjectContext
from science_companion.contracts.media import (
    AudioVideoDerivedData,
    MediaAssetKind,
    MediaAssetStatus,
    MediaCorrectionRequest,
    MediaCorrectionType,
    MediaGateResult,
    MediaIngestionStatus,
    MediaQualityGate,
    MediaUploadRequest,
)
from science_companion.contracts.science import LicenseState, MediaType
from science_companion.media import MediaError, MediaIngestionService


@pytest.fixture
def service() -> MediaIngestionService:
    return MediaIngestionService()


@pytest.fixture
def alice() -> SubjectContext:
    return SubjectContext(
        account_id="account-alice",
        session_id="session-alice",
        auth_method=AuthMethod.PASSWORD,
    )


@pytest.fixture
def bob() -> SubjectContext:
    return SubjectContext(
        account_id="account-bob",
        session_id="session-bob",
        auth_method=AuthMethod.PASSWORD,
    )


def _mp3_bytes() -> bytes:
    """Minimal MP3 magic header for deterministic tests."""
    return b"ID3\x04\x00" + b"\x00" * 28


def _wav_bytes() -> bytes:
    """Minimal WAV magic header for deterministic tests."""
    return b"RIFF" + b"\x00" * 4 + b"WAVE" + b"\x00" * 20


def _mp4_bytes() -> bytes:
    """Minimal MP4 magic header for deterministic tests."""
    return b"\x00\x00\x00\x20ftypisom" + b"\x00" * 12


def _webm_bytes() -> bytes:
    """Minimal WebM magic header for deterministic tests."""
    return b"\x1a\x45\xdf\xa3" + b"\x00" * 28


def _upload(
    service: MediaIngestionService,
    account_id: str,
    filename: str,
    media_type: MediaType,
    content: bytes,
    *,
    project_id: str | None = None,
    license_state: LicenseState | None = LicenseState.USER_OWNED,
) -> str:
    request = MediaUploadRequest(
        filename=filename,
        media_type=media_type,
        content=base64.b64encode(content).decode("ascii"),
        license_state=license_state,
    )
    run = service.ingest_upload(account_id, project_id, request)
    assert run.asset_id is not None
    return run.asset_id


class TestAudioIngestion:
    def test_audio_upload_creates_timed_transcript(
        self, service: MediaIngestionService, alice: SubjectContext
    ) -> None:
        asset_id = _upload(
            service,
            alice.account_id,
            "lecture-photosynthesis.mp3",
            MediaType.AUDIO_MPEG,
            _mp3_bytes(),
        )

        projection = service.get_asset(alice.account_id, asset_id)
        assert projection.source_asset.media_type == MediaType.AUDIO_MPEG
        assert projection.source_asset.status == MediaAssetStatus.PARSED
        assert projection.can_enter_evidence is True
        assert len(projection.derived_assets) == 1

        derived = projection.derived_assets[0]
        assert derived.derivation_type == MediaAssetKind.AUDIO_TRANSCRIPT
        assert derived.locator is not None
        assert derived.locator.start_time == 0.0
        assert derived.locator.end_time is not None
        assert derived.locator.end_time > 0.0

        payload = AudioVideoDerivedData(**derived.payload)
        assert len(payload.transcript_segments) >= 1
        assert len(payload.speaker_segments) == 1
        assert len(payload.captions) == len(payload.transcript_segments)
        assert payload.keyframes == []
        assert payload.language == "auto"

        lock = derived.parameters.get("model_run_lock")
        assert lock is not None
        assert lock["capability_name"] == "qwen_asr_short"
        assert lock["actual_model_id"] == "qwen3-asr-flash"
        assert lock["status"] == "success"

    def test_audio_low_confidence_scientific_term_is_injected(
        self, service: MediaIngestionService, alice: SubjectContext
    ) -> None:
        asset_id = _upload(
            service,
            alice.account_id,
            "biology-photosynthesis.mp3",
            MediaType.AUDIO_MPEG,
            _mp3_bytes(),
        )

        projection = service.get_asset(alice.account_id, asset_id)
        derived = projection.derived_assets[0]
        payload = AudioVideoDerivedData(**derived.payload)

        low_confidence_segments = [
            seg for seg in payload.transcript_segments if seg.low_confidence
        ]
        assert len(low_confidence_segments) >= 1
        segment = low_confidence_segments[0]
        assert "ph0t0synthesis" in segment.text

    def test_missing_audio_track_is_marked(
        self, service: MediaIngestionService, alice: SubjectContext
    ) -> None:
        asset_id = _upload(
            service,
            alice.account_id,
            "silent-demo.mp4",
            MediaType.VIDEO_MP4,
            _mp4_bytes(),
        )

        projection = service.get_asset(alice.account_id, asset_id)
        derived = projection.derived_assets[0]
        payload = AudioVideoDerivedData(**derived.payload)
        assert payload.missing_audio_track is True
        assert len(payload.transcript_segments) == 1
        assert payload.transcript_segments[0].text == "[无音轨]"


class TestVideoIngestion:
    def test_video_upload_creates_keyframes(
        self, service: MediaIngestionService, alice: SubjectContext
    ) -> None:
        asset_id = _upload(
            service,
            alice.account_id,
            "slides-diagram.webm",
            MediaType.VIDEO_WEBM,
            _webm_bytes(),
        )

        projection = service.get_asset(alice.account_id, asset_id)
        derived = projection.derived_assets[0]
        payload = AudioVideoDerivedData(**derived.payload)
        assert len(payload.keyframes) >= 2
        assert any("diagram" in k.interpretation for k in payload.keyframes)


class TestAudioVideoCorrection:
    def test_transcript_term_correction_creates_new_version(
        self, service: MediaIngestionService, alice: SubjectContext
    ) -> None:
        asset_id = _upload(
            service,
            alice.account_id,
            "photosynthesis-lecture.mp3",
            MediaType.AUDIO_MPEG,
            _mp3_bytes(),
        )

        projection = service.get_asset(alice.account_id, asset_id)
        original_version = projection.manifest.version
        derived_id = projection.derived_assets[0].derived_asset_id
        payload = AudioVideoDerivedData(**projection.derived_assets[0].payload)
        low_confidence = next(
            seg for seg in payload.transcript_segments if seg.low_confidence
        )

        correction = MediaCorrectionRequest(
            derived_asset_id=derived_id,
            correction_type=MediaCorrectionType.TRANSCRIPT_TERM,
            target_ref=low_confidence.segment_id,
            corrected_value="photosynthesis",
            reason="fix ASR error",
        )
        new_derived = service.correct_derived_asset(
            alice.account_id, asset_id, correction
        )

        assert new_derived.derived_from_asset_id == derived_id
        assert new_derived.human_corrected is True
        updated_payload = AudioVideoDerivedData(**new_derived.payload)
        corrected_segment = next(
            seg
            for seg in updated_payload.transcript_segments
            if seg.segment_id == low_confidence.segment_id
        )
        assert corrected_segment.text == "photosynthesis"
        assert corrected_segment.low_confidence is False

        updated_projection = service.get_asset(alice.account_id, asset_id)
        assert updated_projection.manifest.version == original_version + 1
        assert (
            updated_projection.source_asset.content_hash
            == projection.source_asset.content_hash
        )

    def test_speaker_segment_correction(
        self, service: MediaIngestionService, alice: SubjectContext
    ) -> None:
        asset_id = _upload(
            service,
            alice.account_id,
            "interview.wav",
            MediaType.AUDIO_WAV,
            _wav_bytes(),
        )

        projection = service.get_asset(alice.account_id, asset_id)
        derived_id = projection.derived_assets[0].derived_asset_id
        payload = AudioVideoDerivedData(**projection.derived_assets[0].payload)
        speaker = payload.speaker_segments[0]

        correction = MediaCorrectionRequest(
            derived_asset_id=derived_id,
            correction_type=MediaCorrectionType.SPEAKER_SEGMENT,
            target_ref=speaker.segment_id,
            corrected_value="SPEAKER_HOST",
            reason="host identified",
        )
        new_derived = service.correct_derived_asset(
            alice.account_id, asset_id, correction
        )
        updated = AudioVideoDerivedData(**new_derived.payload)
        assert updated.speaker_segments[0].speaker_id == "SPEAKER_HOST"

    def test_caption_correction(
        self, service: MediaIngestionService, alice: SubjectContext
    ) -> None:
        asset_id = _upload(
            service,
            alice.account_id,
            "lecture-en.mp3",
            MediaType.AUDIO_MPEG,
            _mp3_bytes(),
        )

        projection = service.get_asset(alice.account_id, asset_id)
        derived_id = projection.derived_assets[0].derived_asset_id
        payload = AudioVideoDerivedData(**projection.derived_assets[0].payload)
        caption = payload.captions[0]

        correction = MediaCorrectionRequest(
            derived_asset_id=derived_id,
            correction_type=MediaCorrectionType.CAPTION_TEXT,
            target_ref=caption.caption_id,
            corrected_value="Corrected caption text.",
            reason="manual subtitle edit",
        )
        new_derived = service.correct_derived_asset(
            alice.account_id, asset_id, correction
        )
        updated = AudioVideoDerivedData(**new_derived.payload)
        assert any(
            c.text == "Corrected caption text." for c in updated.captions
        )

    def test_keyframe_interpretation_correction(
        self, service: MediaIngestionService, alice: SubjectContext
    ) -> None:
        asset_id = _upload(
            service,
            alice.account_id,
            "slides-diagram.mp4",
            MediaType.VIDEO_MP4,
            _mp4_bytes(),
        )

        projection = service.get_asset(alice.account_id, asset_id)
        derived_id = projection.derived_assets[0].derived_asset_id
        payload = AudioVideoDerivedData(**projection.derived_assets[0].payload)
        keyframe = payload.keyframes[0]

        correction = MediaCorrectionRequest(
            derived_asset_id=derived_id,
            correction_type=MediaCorrectionType.KEYFRAME_INTERPRETATION,
            target_ref=keyframe.keyframe_id,
            corrected_value="Free-body diagram at t=0.",
            reason="clarify frame content",
        )
        new_derived = service.correct_derived_asset(
            alice.account_id, asset_id, correction
        )
        updated = AudioVideoDerivedData(**new_derived.payload)
        assert any(
            k.interpretation == "Free-body diagram at t=0."
            for k in updated.keyframes
        )


class TestAudioVideoScopeIsolation:
    def test_bob_cannot_read_alice_audio_asset(
        self, service: MediaIngestionService, alice: SubjectContext, bob: SubjectContext
    ) -> None:
        asset_id = _upload(
            service,
            alice.account_id,
            "alice-recording.mp3",
            MediaType.AUDIO_MPEG,
            _mp3_bytes(),
        )
        with pytest.raises(MediaError, match="访问权限"):
            service.get_asset(bob.account_id, asset_id)

    def test_same_content_hash_does_not_leak_other_account(
        self, service: MediaIngestionService, alice: SubjectContext, bob: SubjectContext
    ) -> None:
        _upload(
            service, alice.account_id, "same.mp3", MediaType.AUDIO_MPEG, _mp3_bytes()
        )
        _upload(
            service, bob.account_id, "same.mp3", MediaType.AUDIO_MPEG, _mp3_bytes()
        )

        alice_assets = service.list_assets(alice.account_id)
        bob_assets = service.list_assets(bob.account_id)
        assert len(alice_assets) == 1
        assert len(bob_assets) == 1
        assert alice_assets[0].asset_id != bob_assets[0].asset_id


class TestAudioVideoGates:
    def test_bad_magic_quarantines_audio(
        self, service: MediaIngestionService, alice: SubjectContext
    ) -> None:
        request = MediaUploadRequest(
            filename="fake.mp3",
            media_type=MediaType.AUDIO_MPEG,
            content=base64.b64encode(b"not an audio file").decode("ascii"),
            license_state=LicenseState.USER_OWNED,
        )
        run = service.ingest_upload(alice.account_id, None, request)
        assert run.status == MediaIngestionStatus.QUARANTINED
        assert run.gate_results[MediaQualityGate.MAGIC_NUMBER] == MediaGateResult.FAIL
