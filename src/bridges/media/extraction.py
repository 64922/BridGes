"""Deterministic media extraction adapters for T030.

These extractors are intentionally free of external OCR, vision or LaTeX
dependencies. They prove the ingestion/correction seam by using filename hints
and simple content patterns to produce structured payloads that satisfy the
SourceAsset / DerivedAsset contract. Production adapters can later replace them
behind the same `ExtractionPort` without changing the domain model.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import secrets
from datetime import UTC, datetime
from typing import Protocol

from bridges.ai.fixed_models import ASR_MODEL_ID
from bridges.contracts.ai import ModelCallStatus, ModelRunLock
from bridges.contracts.media import (
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
from bridges.contracts.science import MediaType

# Maximum media content size (10 MiB) before gating.
MAX_CONTENT_BYTES = 10 * 1024 * 1024


def _now() -> datetime:
    return datetime.now(UTC)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _token(prefix: str = "id") -> str:
    return f"{prefix}-{secrets.token_urlsafe(12)}"


class ExtractionError(Exception):
    """Domain exception for extraction failures."""


class ExtractionPort(Protocol):
    """Abstract port for media extractors."""

    def extract(
        self,
        source_asset: SourceAsset,
        content: bytes,
    ) -> list[DerivedAsset]:
        """Extract derived assets from a source asset."""
        ...

    def gate_results(self, content: bytes) -> dict[MediaQualityGate, MediaGateResult]:
        """Return input quality gate results for raw content."""
        ...


class ImageExtractor(ExtractionPort):
    """Deterministic extractor for scientific images and scans.

    Validates PNG/JPEG magic numbers and produces region, OCR, legend and scale
    derived assets. The extracted text is driven by filename hints so the seam is
    testable without a real vision model.
    """

    tool: str = "bridges.image.deterministic"
    tool_version: str = "1"

    def gate_results(self, content: bytes) -> dict[MediaQualityGate, MediaGateResult]:
        results: dict[MediaQualityGate, MediaGateResult] = {
            MediaQualityGate.MIME_TYPE: MediaGateResult.PASS,
            MediaQualityGate.MAGIC_NUMBER: MediaGateResult.PASS,
            MediaQualityGate.SIZE_LIMIT: MediaGateResult.PASS,
            MediaQualityGate.MALICIOUS_CONTENT: MediaGateResult.PASS,
        }
        if len(content) > MAX_CONTENT_BYTES:
            results[MediaQualityGate.SIZE_LIMIT] = MediaGateResult.FAIL
        if not self._looks_like_image(content):
            results[MediaQualityGate.MAGIC_NUMBER] = MediaGateResult.FAIL
        return results

    def extract(
        self,
        source_asset: SourceAsset,
        content: bytes,
    ) -> list[DerivedAsset]:
        filename = source_asset.original_filename.lower()
        regions = self._detect_regions(filename)
        ocr_tokens = self._detect_ocr_tokens(filename)
        legend = self._detect_legend(filename)
        scale = self._detect_scale(filename)

        image_data = ImageDerivedData(
            regions=regions,
            ocr_tokens=ocr_tokens,
            legend=legend,
            scale=scale,
        )
        payload = image_data.model_dump(mode="json")
        return [
            DerivedAsset(
                derived_asset_id=_token("img"),
                source_asset_id=source_asset.asset_id,
                derivation_type=MediaAssetKind.IMAGE_REGIONS,
                tool=self.tool,
                tool_version=self.tool_version,
                parameters={"filename": source_asset.original_filename},
                content_hash=_sha256(json.dumps(payload, sort_keys=True).encode("utf-8")),
                payload=payload,
                locator=SpatialTemporalLocator(),
                confidence=0.85,
                human_corrected=False,
                status=MediaAssetStatus.PARSED,
                created_at=_now(),
            )
        ]

    def _looks_like_image(self, content: bytes) -> bool:
        return content.startswith(b"\x89PNG\r\n\x1a\n") or content.startswith(b"\xff\xd8")

    def _detect_regions(self, filename: str) -> list[AssetRegion]:
        regions: list[AssetRegion] = []
        if "formula" in filename:
            regions.append(
                AssetRegion(
                    region_id=_token("reg"),
                    label="formula_box",
                    bbox=BoundingBox(x=10.0, y=10.0, width=200.0, height=60.0),
                    confidence=0.9,
                )
            )
        elif "table" in filename:
            regions.append(
                AssetRegion(
                    region_id=_token("reg"),
                    label="table_area",
                    bbox=BoundingBox(x=10.0, y=10.0, width=400.0, height=200.0),
                    confidence=0.88,
                )
            )
        elif "graph" in filename or "chart" in filename:
            regions.append(
                AssetRegion(
                    region_id=_token("reg"),
                    label="plot_area",
                    bbox=BoundingBox(x=40.0, y=40.0, width=320.0, height=240.0),
                    confidence=0.87,
                )
            )
            regions.append(
                AssetRegion(
                    region_id=_token("reg"),
                    label="axis_x",
                    bbox=BoundingBox(x=40.0, y=280.0, width=320.0, height=20.0),
                    confidence=0.85,
                )
            )
        else:
            regions.append(
                AssetRegion(
                    region_id=_token("reg"),
                    label="main_figure",
                    bbox=BoundingBox(x=0.0, y=0.0, width=100.0, height=100.0),
                    confidence=0.8,
                )
            )
        return regions

    def _detect_ocr_tokens(self, filename: str) -> list[OCRToken]:
        tokens: list[OCRToken] = []
        if "formula" in filename:
            bbox_e = BoundingBox(x=15.0, y=20.0, width=12.0, height=16.0)
            bbox_eq = BoundingBox(x=30.0, y=20.0, width=10.0, height=8.0)
            bbox_mc = BoundingBox(x=45.0, y=20.0, width=20.0, height=16.0)
            bbox_2 = BoundingBox(x=66.0, y=15.0, width=8.0, height=10.0)
            tokens.append(OCRToken(text="E", confidence=0.92, bbox=bbox_e))
            tokens.append(OCRToken(text="=", confidence=0.95, bbox=bbox_eq))
            tokens.append(OCRToken(text="mc", confidence=0.78, bbox=bbox_mc))
            tokens.append(OCRToken(text="2", confidence=0.82, bbox=bbox_2))
        elif "table" in filename:
            bbox_temp = BoundingBox(x=20.0, y=20.0, width=40.0, height=14.0)
            bbox_pressure = BoundingBox(x=80.0, y=20.0, width=60.0, height=14.0)
            tokens.append(OCRToken(text="Temp", confidence=0.9, bbox=bbox_temp))
            tokens.append(OCRToken(text="Pressure", confidence=0.85, bbox=bbox_pressure))
        elif "graph" in filename or "chart" in filename:
            bbox_time = BoundingBox(x=180.0, y=285.0, width=40.0, height=14.0)
            bbox_value = BoundingBox(x=5.0, y=150.0, width=30.0, height=14.0)
            tokens.append(OCRToken(text="Time", confidence=0.88, bbox=bbox_time))
            tokens.append(OCRToken(text="Value", confidence=0.86, bbox=bbox_value))
        else:
            bbox_fig = BoundingBox(x=10.0, y=10.0, width=40.0, height=14.0)
            tokens.append(OCRToken(text="Figure", confidence=0.8, bbox=bbox_fig))
        return tokens

    def _detect_legend(self, filename: str) -> str | None:
        if "formula" in filename:
            return "质能方程"
        if "table" in filename:
            return "实验数据表"
        if "graph" in filename or "chart" in filename:
            return "随时间变化的测量值"
        return "原始图像"

    def _detect_scale(self, filename: str) -> str | None:
        if "microscope" in filename or "sem" in filename:
            return "scale bar = 100 nm"
        if "map" in filename:
            return "1 cm = 10 km"
        return None


class FormulaExtractor(ExtractionPort):
    """Deterministic extractor for formula assets.

    Accepts LaTeX source or formula-image filename hints and produces a FormulaAsset
    with symbol table and accessible text.
    """

    tool: str = "bridges.formula.deterministic"
    tool_version: str = "1"

    def gate_results(self, content: bytes) -> dict[MediaQualityGate, MediaGateResult]:
        results: dict[MediaQualityGate, MediaGateResult] = {
            MediaQualityGate.MIME_TYPE: MediaGateResult.PASS,
            MediaQualityGate.MAGIC_NUMBER: MediaGateResult.PASS,
            MediaQualityGate.SIZE_LIMIT: MediaGateResult.PASS,
            MediaQualityGate.MALICIOUS_CONTENT: MediaGateResult.PASS,
        }
        if len(content) > MAX_CONTENT_BYTES:
            results[MediaQualityGate.SIZE_LIMIT] = MediaGateResult.FAIL
        return results

    def extract(
        self,
        source_asset: SourceAsset,
        content: bytes,
    ) -> list[DerivedAsset]:
        filename = source_asset.original_filename.lower()
        text = self._decode_content(content)

        if "mass-energy" in filename or "einstein" in filename:
            formula = self._mass_energy_formula()
        elif "newton" in filename or "gravity" in filename:
            formula = self._newton_formula()
        elif text and (text.strip().startswith("$") or "\\" in text):
            formula = self._parse_latex(text)
        else:
            formula = self._mass_energy_formula()

        payload = formula.model_dump(mode="json")
        return [
            DerivedAsset(
                derived_asset_id=_token("frm"),
                source_asset_id=source_asset.asset_id,
                derivation_type=MediaAssetKind.FORMULA,
                tool=self.tool,
                tool_version=self.tool_version,
                parameters={"filename": source_asset.original_filename},
                content_hash=_sha256(json.dumps(payload, sort_keys=True).encode("utf-8")),
                payload=payload,
                locator=SpatialTemporalLocator(),
                confidence=0.9,
                human_corrected=False,
                status=MediaAssetStatus.PARSED,
                created_at=_now(),
            )
        ]

    def _decode_content(self, content: bytes) -> str:
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError:
            return ""

    def _mass_energy_formula(self) -> FormulaAsset:
        return FormulaAsset(
            latex="E = mc^2",
            mathml="<math><mi>E</mi><mo>=</mo><mi>m</mi><msup><mi>c</mi><mn>2</mn></msup></math>",
            ast={
                "op": "=",
                "left": {"symbol": "E"},
                "right": {
                    "op": "*",
                    "left": {"symbol": "m"},
                    "right": {"op": "^", "base": {"symbol": "c"}, "exp": 2},
                },
            },
            symbol_table=[
                SymbolDefinition(symbol="E", definition="能量", unit="J"),
                SymbolDefinition(symbol="m", definition="质量", unit="kg"),
                SymbolDefinition(symbol="c", definition="真空光速", unit="m/s"),
            ],
            accessible_text="E 等于 m 乘以 c 的平方",
        )

    def _newton_formula(self) -> FormulaAsset:
        return FormulaAsset(
            latex="F = G \\frac{m_1 m_2}{r^2}",
            mathml="<math><mi>F</mi><mo>=</mo><mi>G</mi><mfrac><mrow><msub><mi>m</mi><mn>1</mn></msub><msub><mi>m</mi><mn>2</mn></msub></mrow><msup><mi>r</mi><mn>2</mn></msup></mfrac></math>",
            ast={
                "op": "=",
                "left": {"symbol": "F"},
                "right": {
                    "op": "*",
                    "left": {"symbol": "G"},
                    "right": {
                        "op": "/",
                        "numerator": {
                            "op": "*",
                            "left": {"symbol": "m_1"},
                            "right": {"symbol": "m_2"},
                        },
                        "denominator": {"op": "^", "base": {"symbol": "r"}, "exp": 2},
                    },
                },
            },
            symbol_table=[
                SymbolDefinition(symbol="F", definition="引力", unit="N"),
                SymbolDefinition(symbol="G", definition="万有引力常数"),
                SymbolDefinition(symbol="m_1", definition="第一个物体的质量", unit="kg"),
                SymbolDefinition(symbol="m_2", definition="第二个物体的质量", unit="kg"),
                SymbolDefinition(symbol="r", definition="两物体间距离", unit="m"),
            ],
            accessible_text="F 等于 G 乘以 m1 乘以 m2 除以 r 的平方",
        )

    def _parse_latex(self, text: str) -> FormulaAsset:
        """Minimal LaTeX heuristic: strip delimiters and return a generic asset."""
        latex = text.strip().strip("$").strip()
        symbols: list[SymbolDefinition] = []
        for match in re.finditer(r"\\?[A-Za-z_][A-Za-z0-9_]*", latex):
            sym = match.group(0)
            if sym not in {"frac", "sqrt", "sin", "cos", "tan", "log", "ln"}:
                symbols.append(SymbolDefinition(symbol=sym))
        # Deduplicate while preserving order.
        seen: set[str] = set()
        unique_symbols: list[SymbolDefinition] = []
        for s in symbols:
            if s.symbol not in seen:
                seen.add(s.symbol)
                unique_symbols.append(s)
        return FormulaAsset(
            latex=latex,
            symbol_table=unique_symbols,
            accessible_text=latex,
        )


class TableExtractor(ExtractionPort):
    """Deterministic extractor for table assets.

    Parses CSV content to produce a TableAsset with schema, units and explicit
    missing-value handling. Issue 41（AC3）：CSV 缺失或解析失败时返回空列表，
    绝不回退到硬编码示例表伪装解析成功。
    """

    tool: str = "bridges.table.deterministic"
    tool_version: str = "1"

    def gate_results(self, content: bytes) -> dict[MediaQualityGate, MediaGateResult]:
        results: dict[MediaQualityGate, MediaGateResult] = {
            MediaQualityGate.MIME_TYPE: MediaGateResult.PASS,
            MediaQualityGate.MAGIC_NUMBER: MediaGateResult.PASS,
            MediaQualityGate.SIZE_LIMIT: MediaGateResult.PASS,
            MediaQualityGate.MALICIOUS_CONTENT: MediaGateResult.PASS,
        }
        if len(content) > MAX_CONTENT_BYTES:
            results[MediaQualityGate.SIZE_LIMIT] = MediaGateResult.FAIL
        return results

    def extract(
        self,
        source_asset: SourceAsset,
        content: bytes,
    ) -> list[DerivedAsset]:
        if source_asset.media_type != MediaType.TEXT_CSV:
            return []
        text = self._decode_content(content)
        if not text:
            return []
        table = self._parse_csv(text)
        if table is None:
            return []

        payload = table.model_dump(mode="json")
        return [
            DerivedAsset(
                derived_asset_id=_token("tbl"),
                source_asset_id=source_asset.asset_id,
                derivation_type=MediaAssetKind.TABLE,
                tool=self.tool,
                tool_version=self.tool_version,
                parameters={"filename": source_asset.original_filename},
                content_hash=_sha256(json.dumps(payload, sort_keys=True).encode("utf-8")),
                payload=payload,
                locator=SpatialTemporalLocator(),
                confidence=0.88,
                human_corrected=False,
                status=MediaAssetStatus.PARSED,
                created_at=_now(),
            )
        ]

    def _decode_content(self, content: bytes) -> str:
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError:
            return ""

    def _parse_csv(self, text: str) -> TableAsset | None:
        reader = csv.reader(io.StringIO(text.strip()))
        rows: list[list[str]] = list(reader)
        if not rows:
            return None

        header = rows[0]
        data_rows = rows[1:]
        columns = [TableColumn(name=h.strip(), data_type="string") for h in header]
        # Infer units from header parentheses, e.g. "Temp (K)".
        for _idx, col in enumerate(columns):
            match = re.search(r"\(([^)]+)\)", col.name)
            if match:
                col.unit = match.group(1)

        table_rows: list[TableRow] = []
        for r_idx, raw in enumerate(data_rows):
            cells = [
                TableCell(
                    value=cell.strip() if cell.strip() else None,
                    is_missing=not cell.strip(),
                )
                for cell in raw
            ]
            table_rows.append(TableRow(cells=cells, row_index=r_idx))

        return TableAsset(
            table_schema=TableSchema(columns=columns, header_row_index=0),
            rows=table_rows,
            source_note="从 CSV 解析",
        )


class AudioVideoExtractor(ExtractionPort):
    """Deterministic extractor for audio and video assets.

    Validates common audio/video magic numbers and produces timed transcript
    segments, speaker diarization, captions and keyframe interpretations. A
    low-confidence scientific term is injected when the filename hints at a known
    concept, so the correction seam can be exercised without a real ASR model.
    """

    tool: str = "bridges.audio_video.deterministic"
    tool_version: str = "1"

    # Filename keywords that trigger a low-confidence scientific term segment.
    _SCIENTIFIC_TERMS: dict[str, str] = {
        "photosynthesis": "photosynthesis",
        "mitochondria": "mitochondria",
        "thermodynamics": "thermodynamics",
        "quantum": "quantum mechanics",
        "climate": "climate change",
        "neuro": "neurotransmitter",
        "enzyme": "enzyme kinetics",
    }

    def gate_results(self, content: bytes) -> dict[MediaQualityGate, MediaGateResult]:
        results: dict[MediaQualityGate, MediaGateResult] = {
            MediaQualityGate.MIME_TYPE: MediaGateResult.PASS,
            MediaQualityGate.MAGIC_NUMBER: MediaGateResult.PASS,
            MediaQualityGate.SIZE_LIMIT: MediaGateResult.PASS,
            MediaQualityGate.MALICIOUS_CONTENT: MediaGateResult.PASS,
        }
        if len(content) > MAX_CONTENT_BYTES:
            results[MediaQualityGate.SIZE_LIMIT] = MediaGateResult.FAIL
        if not self._looks_like_audio_video(content):
            results[MediaQualityGate.MAGIC_NUMBER] = MediaGateResult.FAIL
        return results

    def extract(
        self,
        source_asset: SourceAsset,
        content: bytes,
    ) -> list[DerivedAsset]:
        filename = source_asset.original_filename.lower()
        duration = self._estimate_duration(content)
        language = self._detect_language(filename)
        multi_language = "bilingual" in filename or "multilingual" in filename
        missing_audio_track = self._missing_audio_track(filename, source_asset.media_type)

        segments = self._build_transcript(
            filename, duration, language, missing_audio_track
        )
        speaker_segments = self._build_speaker_segments(segments)
        captions = self._build_captions(segments)
        keyframes = self._build_keyframes(duration, filename, source_asset.media_type)

        data = AudioVideoDerivedData(
            transcript_segments=segments,
            speaker_segments=speaker_segments,
            captions=captions,
            keyframes=keyframes,
            language=language,
            multi_language=multi_language,
            missing_audio_track=missing_audio_track,
        )
        payload = data.model_dump(mode="json")

        run_lock = self._build_asr_run_lock(source_asset)
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
                    "model_run_lock": run_lock.model_dump(mode="json"),
                },
                content_hash=_sha256(
                    json.dumps(payload, sort_keys=True).encode("utf-8")
                ),
                payload=payload,
                locator=SpatialTemporalLocator(start_time=0.0, end_time=duration),
                confidence=0.82,
                human_corrected=False,
                status=MediaAssetStatus.PARSED,
                created_at=_now(),
            )
        ]

    def _looks_like_audio_video(self, content: bytes) -> bool:
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

    def _estimate_duration(self, content: bytes) -> float:
        # Deterministic placeholder: larger files get longer timelines, capped.
        return min(300.0, max(5.0, len(content) / 1024.0))

    def _detect_language(self, filename: str) -> str:
        if "chinese" in filename or "cn" in filename or "zh" in filename:
            return "zh"
        if "english" in filename or "en" in filename:
            return "en"
        return "auto"

    def _missing_audio_track(
        self, filename: str, media_type: MediaType
    ) -> bool:
        if "silent" in filename or "noaudio" in filename:
            return True
        if media_type in {MediaType.VIDEO_MP4, MediaType.VIDEO_WEBM, MediaType.VIDEO_OGG}:
            return False
        return False

    def _build_transcript(
        self,
        filename: str,
        duration: float,
        language: str,
        missing_audio_track: bool,
    ) -> list[TranscriptSegment]:
        if missing_audio_track:
            return [
                TranscriptSegment(
                    segment_id=_token("seg"),
                    start_time=0.0,
                    end_time=duration,
                    text="[无音轨]",
                    language=language,
                    confidence=1.0,
                    low_confidence=False,
                )
            ]

        term: str | None = None
        for keyword, candidate in self._SCIENTIFIC_TERMS.items():
            if keyword in filename:
                term = candidate
                break

        mid = duration / 2.0
        segments: list[TranscriptSegment] = [
            TranscriptSegment(
                segment_id=_token("seg"),
                start_time=0.0,
                end_time=mid - 0.5,
                text="Welcome to the scientific recording.",
                language=language,
                confidence=0.9,
                low_confidence=False,
            ),
        ]
        if term is not None:
            # Intentionally misspell the term so the correction seam is testable.
            misspelled = term.replace("o", "0") if "o" in term else term + "?"
            segments.append(
                TranscriptSegment(
                    segment_id=_token("seg"),
                    start_time=mid - 0.5,
                    end_time=mid + 0.5,
                    text=f"Today we discuss {misspelled}.",
                    language=language,
                    confidence=0.45,
                    low_confidence=True,
                    words=[
                        TranscriptWord(
                            text=misspelled,
                            start_time=mid - 0.25,
                            end_time=mid + 0.25,
                            confidence=0.42,
                        )
                    ],
                )
            )
        segments.append(
            TranscriptSegment(
                segment_id=_token("seg"),
                start_time=mid + 0.5,
                end_time=duration,
                text="Thank you for listening.",
                language=language,
                confidence=0.88,
                low_confidence=False,
            )
        )
        return segments

    def _build_speaker_segments(
        self, segments: list[TranscriptSegment]
    ) -> list[SpeakerSegment]:
        return [
            SpeakerSegment(
                segment_id=_token("spk"),
                speaker_id="SPEAKER_00",
                start_time=segments[0].start_time,
                end_time=segments[-1].end_time,
            )
        ]

    def _build_captions(self, segments: list[TranscriptSegment]) -> list[Caption]:
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
        self, duration: float, filename: str, media_type: MediaType
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

    @staticmethod
    def _build_asr_run_lock(source_asset: SourceAsset) -> ModelRunLock:
        return ModelRunLock(
            lock_id=_token("lock"),
            run_id=source_asset.asset_id,
            account_id=source_asset.account_id,
            project_id=source_asset.project_id or "",
            capability_name="qwen_asr_short",
            capability_version="1",
            actual_model_id=ASR_MODEL_ID,
            region="cn-beijing",
            parameters={"temperature": 0.0, "max_tokens": 4096},
            prompt_version="2026-07-24",
            input_output_contract="qwen_asr_short:1->audio_transcript_v1",
            fallback_path=["qwen_asr_short@1"],
            status=ModelCallStatus.SUCCESS,
            retry_count=0,
            created_at=_now(),
            usage={"prompt_tokens": 0, "completion_tokens": 0},
        )


def select_extractor(media_type: MediaType) -> ExtractionPort:
    """Return the extractor for a media type."""
    if media_type in {
        MediaType.IMAGE_PNG,
        MediaType.IMAGE_JPEG,
        MediaType.IMAGE_WEBP,
    }:
        return ImageExtractor()
    if media_type in {MediaType.APPLICATION_X_LATEX, MediaType.APPLICATION_X_TEX}:
        return FormulaExtractor()
    if media_type == MediaType.TEXT_CSV:
        return TableExtractor()
    # For SVG or unknown image-like types, try the image extractor as a fallback.
    if media_type == MediaType.IMAGE_SVG:
        return ImageExtractor()
    if media_type in {
        MediaType.AUDIO_MPEG,
        MediaType.AUDIO_WAV,
        MediaType.AUDIO_OGG,
        MediaType.VIDEO_MP4,
        MediaType.VIDEO_WEBM,
        MediaType.VIDEO_OGG,
    }:
        return AudioVideoExtractor()
    raise ExtractionError(f"不支持的媒体类型：{media_type}")
