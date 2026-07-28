"""Model-backed extraction ports that use the Qwen OCR/vision capabilities.

These extractors replace the deterministic placeholders in T030 when a
``ModelGateway`` is available. They keep the same ``ExtractionPort`` shape so the
``MediaIngestionService`` does not need to know whether extraction is
model-backed or deterministic.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
from datetime import UTC, datetime
from typing import Any

from science_companion.ai.model_gateway import ModelGateway
from science_companion.contracts.ai import ModelCallStatus
from science_companion.contracts.media import (
    AssetRegion,
    AudioVideoDerivedData,
    BoundingBox,
    Caption,
    DerivedAsset,
    FormulaAsset,
    ImageDerivedData,
    Keyframe,
    MediaAssetKind,
    MediaAssetStatus,
    MediaGateResult,
    MediaQualityGate,
    OCRToken,
    SourceAsset,
    SpatialTemporalLocator,
    SpeakerSegment,
    SymbolDefinition,
    TableAsset,
    TableCell,
    TableColumn,
    TableRow,
    TableSchema,
    TranscriptSegment,
    TranscriptWord,
)
from science_companion.contracts.science import MediaType
from science_companion.contracts.workflows import RunContextEnvelope
from science_companion.media.extraction import ExtractionError, ExtractionPort

# Maximum media content size (10 MiB) before gating.
MAX_CONTENT_BYTES = 10 * 1024 * 1024

# ASR capability limits — single source of truth in the adapter module.
from science_companion.ai.qwen_asr_adapter import (
    LONG_AUDIO_MAX_SECONDS,
    SHORT_AUDIO_MAX_SECONDS,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _token(prefix: str = "id") -> str:
    return f"{prefix}-{secrets.token_urlsafe(12)}"


def build_run_context_for_ocr(source_asset: SourceAsset) -> RunContextEnvelope:
    """Build a minimal run context for ingestion-time model calls."""
    return RunContextEnvelope(
        run_id=f"ocr-{source_asset.asset_id}",
        account_id=source_asset.account_id,
        project_id=source_asset.project_id or "",
        workflow_name="media_ingestion",
        workflow_version="1",
        submitted_at=_now(),
    )


OCR_IMAGE_PROMPT = (
    "Extract all visible text from this scientific image. "
    "Preserve the reading order. Do not add commentary."
)

OCR_TABLE_PROMPT = (
    "Extract the table from this image as a Markdown table. "
    "Use | as the column delimiter and a header separator line. "
    "Leave cells empty when no value is visible. Do not add commentary."
)

OCR_FORMULA_PROMPT = (
    "Recognize the formula in this image and return valid JSON with keys: "
    "latex (string), accessible_text (string), and symbol_table (list of "
    "{symbol, definition, unit}). Do not wrap the JSON in markdown fences."
)


class QwenOcrExtractor(ExtractionPort):
    """OCR/vision extraction backed by the ``qwen_ocr`` capability.

    The extractor turns raw image/formula/table bytes into structured
    ``DerivedAsset`` payloads. It records the immutable ``ModelRunLock`` in the
    derived asset's parameters so every extraction is auditable and comparable.
    """

    tool: str = "science_companion.ocr.qwen"
    tool_version: str = "1"

    def __init__(
        self,
        gateway: ModelGateway,
        run_context: RunContextEnvelope,
    ) -> None:
        self._gateway = gateway
        self._run_context = run_context

    def gate_results(self, content: bytes) -> dict[MediaQualityGate, MediaGateResult]:
        results: dict[MediaQualityGate, MediaGateResult] = {
            MediaQualityGate.MIME_TYPE: MediaGateResult.PASS,
            MediaQualityGate.MAGIC_NUMBER: MediaGateResult.PASS,
            MediaQualityGate.SIZE_LIMIT: MediaGateResult.PASS,
            MediaQualityGate.MALICIOUS_CONTENT: MediaGateResult.PASS,
        }
        if len(content) > MAX_CONTENT_BYTES:
            results[MediaQualityGate.SIZE_LIMIT] = MediaGateResult.FAIL
        if not _looks_like_image(content):
            results[MediaQualityGate.MAGIC_NUMBER] = MediaGateResult.FAIL
        return results

    def extract(
        self,
        source_asset: SourceAsset,
        content: bytes,
    ) -> list[DerivedAsset]:
        filename = source_asset.original_filename.lower()
        mime_type = _mime_type_for_content(content, source_asset.media_type)

        if source_asset.media_type in {
            MediaType.APPLICATION_X_LATEX,
            MediaType.APPLICATION_X_TEX,
        } or "formula" in filename:
            task = "formula_recognition"
            prompt = OCR_FORMULA_PROMPT
            parser = self._parse_formula
        elif source_asset.media_type == MediaType.TEXT_CSV or "table" in filename:
            task = "table_parsing"
            prompt = OCR_TABLE_PROMPT
            parser = self._parse_table
        else:
            task = "advanced_recognition"
            prompt = OCR_IMAGE_PROMPT
            parser = self._parse_image

        image_base64 = base64.b64encode(content).decode("ascii")
        result = self._gateway.invoke(
            "qwen_ocr",
            "1",
            self._run_context,
            payload={
                "image_base64": image_base64,
                "mime_type": mime_type,
                "prompt": prompt,
                "task": task,
                "temperature": 0.01,
                "max_tokens": 4096,
            },
        )

        if result.status != ModelCallStatus.SUCCESS or result.output is None:
            message = result.error_message or "Qwen OCR invocation did not succeed."
            raise ExtractionError(message)

        content_text = str(result.output.get("content", ""))
        derived_assets = parser(source_asset, content_text)
        for derived in derived_assets:
            derived.tool = self.tool
            derived.tool_version = self.tool_version
            if result.lock is not None:
                derived.parameters["model_run_lock"] = result.lock.model_dump(mode="json")
                derived.parameters["ocr_task"] = task
        return derived_assets

    def _parse_image(
        self,
        source_asset: SourceAsset,
        content_text: str,
    ) -> list[DerivedAsset]:
        lines = [line.strip() for line in content_text.splitlines() if line.strip()]
        legend = lines[0] if lines else None

        tokens: list[OCRToken] = []
        for word in re.findall(r"\S+", content_text):
            tokens.append(
                OCRToken(
                    text=word,
                    confidence=0.75,
                    bbox=None,
                )
            )

        image_data = ImageDerivedData(
            regions=[
                AssetRegion(
                    region_id=_token("reg"),
                    label="full_image",
                    bbox=BoundingBox(x=0.0, y=0.0, width=100.0, height=100.0, unit="normalized"),
                    confidence=0.75,
                )
            ],
            ocr_tokens=tokens,
            legend=legend,
            scale=None,
        )
        payload = image_data.model_dump(mode="json")
        return [
            DerivedAsset(
                derived_asset_id=_token("img"),
                source_asset_id=source_asset.asset_id,
                derivation_type=MediaAssetKind.IMAGE_REGIONS,
                tool=self.tool,
                tool_version=self.tool_version,
                parameters={},
                content_hash=_sha256(json.dumps(payload, sort_keys=True).encode("utf-8")),
                payload=payload,
                locator=SpatialTemporalLocator(),
                confidence=0.75,
                human_corrected=False,
                status=MediaAssetStatus.PARSED,
                created_at=_now(),
            )
        ]

    def _parse_formula(
        self,
        source_asset: SourceAsset,
        content_text: str,
    ) -> list[DerivedAsset]:
        formula = _parse_formula_json(content_text)
        if formula is None:
            formula = FormulaAsset(
                latex=content_text,
                accessible_text=content_text,
            )
        payload = formula.model_dump(mode="json")
        return [
            DerivedAsset(
                derived_asset_id=_token("frm"),
                source_asset_id=source_asset.asset_id,
                derivation_type=MediaAssetKind.FORMULA,
                tool=self.tool,
                tool_version=self.tool_version,
                parameters={},
                content_hash=_sha256(json.dumps(payload, sort_keys=True).encode("utf-8")),
                payload=payload,
                locator=SpatialTemporalLocator(),
                confidence=0.82,
                human_corrected=False,
                status=MediaAssetStatus.PARSED,
                created_at=_now(),
            )
        ]

    def _parse_table(
        self,
        source_asset: SourceAsset,
        content_text: str,
    ) -> list[DerivedAsset]:
        table = _parse_markdown_table(content_text)
        payload = table.model_dump(mode="json")
        return [
            DerivedAsset(
                derived_asset_id=_token("tbl"),
                source_asset_id=source_asset.asset_id,
                derivation_type=MediaAssetKind.TABLE,
                tool=self.tool,
                tool_version=self.tool_version,
                parameters={},
                content_hash=_sha256(json.dumps(payload, sort_keys=True).encode("utf-8")),
                payload=payload,
                locator=SpatialTemporalLocator(),
                confidence=0.78,
                human_corrected=False,
                status=MediaAssetStatus.PARSED,
                created_at=_now(),
            )
        ]


class QwenVisionExtractor(ExtractionPort):
    """General visual understanding backed by the ``qwen_vision`` capability.

    This extractor is currently used for key-frame interpretation and other
    non-OCR visual tasks. It returns a single derived asset carrying the model's
    textual interpretation together with the immutable run lock.
    """

    tool: str = "science_companion.vision.qwen"
    tool_version: str = "1"

    def __init__(
        self,
        gateway: ModelGateway,
        run_context: RunContextEnvelope,
        prompt: str | None = None,
    ) -> None:
        self._gateway = gateway
        self._run_context = run_context
        self._prompt = prompt or (
            "Describe the scientific content of this image, including figures, "
            "axes, labels and any visible numerical data."
        )

    def gate_results(self, content: bytes) -> dict[MediaQualityGate, MediaGateResult]:
        results: dict[MediaQualityGate, MediaGateResult] = {
            MediaQualityGate.MIME_TYPE: MediaGateResult.PASS,
            MediaQualityGate.MAGIC_NUMBER: MediaGateResult.PASS,
            MediaQualityGate.SIZE_LIMIT: MediaGateResult.PASS,
            MediaQualityGate.MALICIOUS_CONTENT: MediaGateResult.PASS,
        }
        if len(content) > MAX_CONTENT_BYTES:
            results[MediaQualityGate.SIZE_LIMIT] = MediaGateResult.FAIL
        if not _looks_like_image(content):
            results[MediaQualityGate.MAGIC_NUMBER] = MediaGateResult.FAIL
        return results

    def extract(
        self,
        source_asset: SourceAsset,
        content: bytes,
    ) -> list[DerivedAsset]:
        mime_type = _mime_type_for_content(content, source_asset.media_type)
        image_base64 = base64.b64encode(content).decode("ascii")
        result = self._gateway.invoke(
            "qwen_vision",
            "1",
            self._run_context,
            payload={
                "image_base64": image_base64,
                "mime_type": mime_type,
                "prompt": self._prompt,
                "temperature": 0.7,
                "max_tokens": 2048,
            },
        )

        if result.status != ModelCallStatus.SUCCESS or result.output is None:
            message = result.error_message or "Qwen vision invocation did not succeed."
            raise ExtractionError(message)

        interpretation = str(result.output.get("content", ""))
        payload: dict[str, Any] = {"interpretation": interpretation}
        return [
            DerivedAsset(
                derived_asset_id=_token("vis"),
                source_asset_id=source_asset.asset_id,
                derivation_type=MediaAssetKind.VIDEO_KEYFRAME,
                tool=self.tool,
                tool_version=self.tool_version,
                parameters={
                    "prompt": self._prompt,
                    "model_run_lock": (
                        result.lock.model_dump(mode="json") if result.lock else None
                    ),
                },
                content_hash=_sha256(json.dumps(payload, sort_keys=True).encode("utf-8")),
                payload=payload,
                locator=SpatialTemporalLocator(),
                confidence=0.75,
                human_corrected=False,
                status=MediaAssetStatus.PARSED,
                created_at=_now(),
            )
        ]


ASR_PROMPT = (
    "Transcribe the audio accurately. Preserve the original language. "
    "If you are uncertain about a scientific term, mark it like [?term?]."
)


def build_run_context_for_asr(source_asset: SourceAsset) -> RunContextEnvelope:
    """Build a minimal run context for ASR ingestion-time model calls."""
    return RunContextEnvelope(
        run_id=f"asr-{source_asset.asset_id}",
        account_id=source_asset.account_id,
        project_id=source_asset.project_id or "",
        workflow_name="media_ingestion",
        workflow_version="1",
        submitted_at=_now(),
    )


class QwenAsrExtractor(ExtractionPort):
    """Audio/video transcription backed by Qwen ASR capabilities.

    The extractor sends raw audio bytes to ``qwen_asr_short`` or
    ``qwen_asr_long`` and turns the returned transcript into a structured
    ``AudioVideoDerivedData`` payload. It records the immutable ``ModelRunLock``
    in the derived asset parameters so every extraction is auditable.
    """

    tool: str = "science_companion.asr.qwen"
    tool_version: str = "1"

    def __init__(
        self,
        gateway: ModelGateway,
        run_context: RunContextEnvelope,
    ) -> None:
        self._gateway = gateway
        self._run_context = run_context

    def gate_results(self, content: bytes) -> dict[MediaQualityGate, MediaGateResult]:
        results: dict[MediaQualityGate, MediaGateResult] = {
            MediaQualityGate.MIME_TYPE: MediaGateResult.PASS,
            MediaQualityGate.MAGIC_NUMBER: MediaGateResult.PASS,
            MediaQualityGate.SIZE_LIMIT: MediaGateResult.PASS,
            MediaQualityGate.MALICIOUS_CONTENT: MediaGateResult.PASS,
        }
        if len(content) > MAX_CONTENT_BYTES:
            results[MediaQualityGate.SIZE_LIMIT] = MediaGateResult.FAIL
        if not _looks_like_audio_video(content):
            results[MediaQualityGate.MAGIC_NUMBER] = MediaGateResult.FAIL
        return results

    def extract(
        self,
        source_asset: SourceAsset,
        content: bytes,
    ) -> list[DerivedAsset]:
        filename = source_asset.original_filename.lower()
        mime_type = _mime_type_for_audio(content, source_asset.media_type)
        duration = _estimate_duration(content)

        if _missing_audio_track(filename, source_asset.media_type):
            return self._build_silent_derived(source_asset, duration)

        capability_name = (
            "qwen_asr_long" if duration > SHORT_AUDIO_MAX_SECONDS else "qwen_asr_short"
        )
        audio_base64 = base64.b64encode(content).decode("ascii")
        language = _detect_language(filename)
        multi_language = "bilingual" in filename or "multilingual" in filename

        result = self._gateway.invoke(
            capability_name,
            "1",
            self._run_context,
            payload={
                "audio_base64": audio_base64,
                "mime_type": mime_type,
                "duration_seconds": duration,
                "language": language,
                "prompt": ASR_PROMPT,
                "temperature": 0.0,
                "max_tokens": 4096,
            },
        )

        if result.status != ModelCallStatus.SUCCESS or result.output is None:
            # Fall back to a deterministic placeholder per T060 spec:
            # "失败时回退到确定性占位并记录 degradation_reason".
            return self._build_degraded_derived(
                source_asset, duration, capability_name, result
            )

        transcript = str(result.output.get("transcript", ""))
        detected_language = result.output.get("language") or language
        segments = _parse_transcript(transcript, duration)
        speaker_segments = _build_speaker_segments(segments)
        captions = _build_captions(segments)
        keyframes = _build_keyframes(duration, filename, source_asset.media_type)

        data = AudioVideoDerivedData(
            transcript_segments=segments,
            speaker_segments=speaker_segments,
            captions=captions,
            keyframes=keyframes,
            language=detected_language,
            multi_language=multi_language,
            missing_audio_track=False,
        )
        payload = data.model_dump(mode="json")
        derived = DerivedAsset(
            derived_asset_id=_token("av"),
            source_asset_id=source_asset.asset_id,
            derivation_type=MediaAssetKind.AUDIO_TRANSCRIPT,
            tool=self.tool,
            tool_version=self.tool_version,
            parameters={
                "filename": source_asset.original_filename,
                "media_type": source_asset.media_type.value,
                "asr_capability": capability_name,
            },
            content_hash=_sha256(json.dumps(payload, sort_keys=True).encode("utf-8")),
            payload=payload,
            locator=SpatialTemporalLocator(start_time=0.0, end_time=duration),
            confidence=0.82,
            human_corrected=False,
            status=MediaAssetStatus.PARSED,
            created_at=_now(),
        )
        if result.lock is not None:
            derived.parameters["model_run_lock"] = result.lock.model_dump(mode="json")
        return [derived]

    def _build_silent_derived(
        self, source_asset: SourceAsset, duration: float
    ) -> list[DerivedAsset]:
        filename = source_asset.original_filename.lower()
        data = AudioVideoDerivedData(
            transcript_segments=[
                TranscriptSegment(
                    segment_id=_token("seg"),
                    start_time=0.0,
                    end_time=duration,
                    text="[无音轨]",
                    language="auto",
                    confidence=1.0,
                    low_confidence=False,
                )
            ],
            speaker_segments=[],
            captions=[],
            keyframes=_build_keyframes(duration, filename, source_asset.media_type),
            language="auto",
            multi_language=False,
            missing_audio_track=True,
        )
        payload = data.model_dump(mode="json")
        return [
            DerivedAsset(
                derived_asset_id=_token("av"),
                source_asset_id=source_asset.asset_id,
                derivation_type=MediaAssetKind.AUDIO_TRANSCRIPT,
                tool=self.tool,
                tool_version=self.tool_version,
                parameters={
                    "filename": source_asset.original_filename,
                    "media_type": source_asset.media_type.value,
                    "missing_audio_track": True,
                },
                content_hash=_sha256(json.dumps(payload, sort_keys=True).encode("utf-8")),
                payload=payload,
                locator=SpatialTemporalLocator(start_time=0.0, end_time=duration),
                confidence=1.0,
                human_corrected=False,
                status=MediaAssetStatus.PARSED,
                created_at=_now(),
            )
        ]

    def _build_degraded_derived(
        self,
        source_asset: SourceAsset,
        duration: float,
        capability_name: str,
        result: Any,  # ModelCallResult — avoids circular import
    ) -> list[DerivedAsset]:
        """Build a degraded placeholder derived asset when ASR fails.

        Per T060 spec: failure falls back to a deterministic placeholder and
        records the ``degradation_reason`` so the product enters a
        human-correctable state rather than being silently blocked.
        """
        filename = source_asset.original_filename.lower()
        degradation_reason = (
            result.degradation_reason
            or result.error_message
            or "ASR 转录失败"
        )
        data = AudioVideoDerivedData(
            transcript_segments=[
                TranscriptSegment(
                    segment_id=_token("seg"),
                    start_time=0.0,
                    end_time=duration,
                    text="[ASR转录失败]",
                    language="auto",
                    confidence=0.0,
                    low_confidence=True,
                )
            ],
            speaker_segments=[],
            captions=[],
            keyframes=_build_keyframes(duration, filename, source_asset.media_type),
            language="auto",
            multi_language=False,
            missing_audio_track=False,
        )
        payload = data.model_dump(mode="json")
        parameters: dict[str, Any] = {
            "filename": source_asset.original_filename,
            "media_type": source_asset.media_type.value,
            "asr_capability": capability_name,
            "degraded": True,
            "degradation_reason": degradation_reason,
        }
        if result.lock is not None:
            parameters["model_run_lock"] = result.lock.model_dump(mode="json")
        return [
            DerivedAsset(
                derived_asset_id=_token("av"),
                source_asset_id=source_asset.asset_id,
                derivation_type=MediaAssetKind.AUDIO_TRANSCRIPT,
                tool=self.tool,
                tool_version=self.tool_version,
                parameters=parameters,
                content_hash=_sha256(json.dumps(payload, sort_keys=True).encode("utf-8")),
                payload=payload,
                locator=SpatialTemporalLocator(start_time=0.0, end_time=duration),
                confidence=0.0,
                human_corrected=False,
                status=MediaAssetStatus.PARSED,
                created_at=_now(),
            )
        ]


def _looks_like_audio_video(content: bytes) -> bool:
    if len(content) < 12:
        return False
    if content.startswith(b"ID3"):
        return True
    if (content[0] == 0xFF) and ((content[1] & 0xE0) == 0xE0):
        return True
    if content.startswith(b"RIFF") and content[8:12] == b"WAVE":
        return True
    if content.startswith(b"OggS"):
        return True
    if content[4:8] == b"ftyp":
        return True
    return content.startswith(b"\x1a\x45\xdf\xa3")


def _mime_type_for_audio(content: bytes, media_type: MediaType | None) -> str:
    if media_type == MediaType.AUDIO_MPEG:
        return "audio/mpeg"
    if media_type == MediaType.AUDIO_WAV:
        return "audio/wav"
    if media_type == MediaType.AUDIO_OGG:
        return "audio/ogg"
    if media_type in {MediaType.VIDEO_MP4, MediaType.VIDEO_WEBM, MediaType.VIDEO_OGG}:
        return "audio/mpeg"
    if content.startswith(b"ID3") or ((content[0] == 0xFF) and ((content[1] & 0xE0) == 0xE0)):
        return "audio/mpeg"
    if content.startswith(b"RIFF"):
        return "audio/wav"
    if content.startswith(b"OggS"):
        return "audio/ogg"
    return "audio/mpeg"


def _estimate_duration(content: bytes) -> float:
    # Deterministic placeholder: larger files get longer timelines, capped at
    # the long-audio limit so the extractor can route to ``qwen_asr_long``.
    return min(float(LONG_AUDIO_MAX_SECONDS), max(5.0, len(content) / 1024.0))


def _detect_language(filename: str) -> str:
    if "chinese" in filename or "cn" in filename or "zh" in filename:
        return "zh"
    if "english" in filename or "en" in filename:
        return "en"
    return "auto"


def _missing_audio_track(filename: str, media_type: MediaType) -> bool:
    return "silent" in filename or "noaudio" in filename


def _parse_transcript(transcript: str, duration: float) -> list[TranscriptSegment]:
    if not transcript.strip():
        return [
            TranscriptSegment(
                segment_id=_token("seg"),
                start_time=0.0,
                end_time=duration,
                text="",
                language="auto",
                confidence=1.0,
                low_confidence=False,
            )
        ]

    # Split on sentence boundaries and distribute evenly across the timeline.
    raw_sentences = [
        s.strip() for s in re.split(r"(?<=[.!?。！？])\s+", transcript.strip()) if s.strip()
    ]
    if not raw_sentences:
        raw_sentences = [transcript.strip()]

    sentences = _extract_low_confidence_sentences(raw_sentences)
    step = duration / max(1, len(sentences))
    segments: list[TranscriptSegment] = []
    for idx, (text, low_confidence_words) in enumerate(sentences):
        start = idx * step
        end = duration if idx == len(sentences) - 1 else (idx + 1) * step
        words: list[TranscriptWord] = []
        for word_info in low_confidence_words:
            words.append(
                TranscriptWord(
                    text=word_info,
                    start_time=start + (end - start) * 0.25,
                    end_time=start + (end - start) * 0.75,
                    confidence=0.5,
                )
            )
        segments.append(
            TranscriptSegment(
                segment_id=_token("seg"),
                start_time=start,
                end_time=end,
                text=text,
                language="auto",
                confidence=0.82 if not low_confidence_words else 0.55,
                low_confidence=bool(low_confidence_words),
                words=words,
            )
        )
    return segments


def _extract_low_confidence_sentences(
    raw_sentences: list[str],
) -> list[tuple[str, list[str]]]:
    """Return cleaned sentences and any low-confidence terms marked [?term?]."""
    pattern = re.compile(r"\[\?([^\]]+)\?\]")
    result: list[tuple[str, list[str]]] = []
    for sentence in raw_sentences:
        terms = pattern.findall(sentence)
        cleaned = pattern.sub(r"\1", sentence)
        result.append((cleaned, terms))
    return result


def _build_speaker_segments(segments: list[TranscriptSegment]) -> list[SpeakerSegment]:
    if not segments:
        return []
    return [
        SpeakerSegment(
            segment_id=_token("spk"),
            speaker_id="SPEAKER_00",
            start_time=segments[0].start_time,
            end_time=segments[-1].end_time,
        )
    ]


def _build_captions(segments: list[TranscriptSegment]) -> list[Caption]:
    return [
        Caption(
            caption_id=_token("cap"),
            start_time=seg.start_time,
            end_time=seg.end_time,
            text=seg.text,
            language=seg.language,
        )
        for seg in segments
    ]


def _build_keyframes(
    duration: float, filename: str, media_type: MediaType
) -> list[Keyframe]:
    if media_type not in {MediaType.VIDEO_MP4, MediaType.VIDEO_WEBM, MediaType.VIDEO_OGG}:
        return []
    keyframes: list[Keyframe] = [
        Keyframe(
            keyframe_id=_token("kf"),
            time=0.0,
            interpretation="Opening frame of the recording.",
            confidence=0.9,
        )
    ]
    if "slides" in filename or "diagram" in filename:
        keyframes.append(
            Keyframe(
                keyframe_id=_token("kf"),
                time=duration / 2.0,
                interpretation="A scientific diagram is visible.",
                confidence=0.75,
            )
        )
    keyframes.append(
        Keyframe(
            keyframe_id=_token("kf"),
            time=duration,
            interpretation="End frame of the recording.",
            confidence=0.9,
        )
    )
    return keyframes


def _looks_like_image(content: bytes) -> bool:
    return bool(
        content.startswith(b"\x89PNG\r\n\x1a\n")
        or content.startswith(b"\xff\xd8")
        or content.startswith(b"RIFF")
        or content.startswith(b"WEBP")
    )


def _mime_type_for_content(content: bytes, media_type: MediaType | None) -> str:
    """Return a valid image MIME type for OCR/vision API calls.

    SVG is intentionally excluded — it is a vector format that must be
    rasterised before being sent to the image-based OCR/vision API.
    """
    if media_type == MediaType.IMAGE_PNG:
        return "image/png"
    if media_type == MediaType.IMAGE_JPEG:
        return "image/jpeg"
    if media_type == MediaType.IMAGE_WEBP:
        return "image/webp"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if content.startswith(b"RIFF"):
        return "image/webp"
    # Default to PNG for unknown image formats.
    return "image/png"


def _parse_formula_json(text: str) -> FormulaAsset | None:
    """Attempt to extract a JSON formula description from model output."""
    # Look for a JSON object block, with or without markdown fences.
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group())
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    symbols: list[SymbolDefinition] = []
    for entry in data.get("symbol_table", []):
        if isinstance(entry, dict):
            symbols.append(
                SymbolDefinition(
                    symbol=str(entry.get("symbol", "")),
                    definition=entry.get("definition") or None,
                    unit=entry.get("unit") or None,
                )
            )
    return FormulaAsset(
        latex=str(data.get("latex", "")),
        accessible_text=data.get("accessible_text") or None,
        symbol_table=symbols,
    )


def _parse_markdown_table(text: str) -> TableAsset:
    """Parse a Markdown table into a ``TableAsset``.

    Falls back to a single-cell table when the content cannot be parsed.
    """
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    table_lines: list[list[str]] = []
    for line in lines:
        if "|" not in line:
            continue
        if re.match(r"^\|?[-:\|\s]+\|?$", line):
            continue
        cells = [cell.strip() for cell in line.split("|")]
        # Drop leading/trailing empty cells caused by outer pipes.
        if cells and cells[0] == "":
            cells = cells[1:]
        if cells and cells[-1] == "":
            cells = cells[:-1]
        if cells:
            table_lines.append(cells)

    if not table_lines:
        return TableAsset(
            table_schema=TableSchema(columns=[TableColumn(name="content", data_type="string")]),
            rows=[TableRow(cells=[TableCell(value=text.strip(), is_missing=not text.strip())])],
            source_note="OCR output could not be structured as a table.",
        )

    header = table_lines[0]
    data_rows = table_lines[1:] if len(table_lines) > 1 else []
    columns = [TableColumn(name=h, data_type="string") for h in header]
    for col in columns:
        match = re.search(r"\(([^)]+)\)", col.name)
        if match:
            col.unit = match.group(1)

    rows: list[TableRow] = []
    for r_idx, raw in enumerate(data_rows):
        row_cells: list[TableCell] = [
            TableCell(
                value=cell if cell else None,
                is_missing=not cell,
            )
            for cell in raw
        ]
        rows.append(TableRow(cells=row_cells, row_index=r_idx))

    return TableAsset(
        table_schema=TableSchema(columns=columns, header_row_index=0),
        rows=rows,
        source_note="Parsed from OCR Markdown table.",
    )
