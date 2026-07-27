"""Multimodal scientific media asset contracts.

These models define the public surface of imported scientific images, scans,
formulas and tables. They follow the SourceAsset / DerivedAsset / MediaManifest
pattern from the multimodal-science-studio research and are the authoritative
shape of T030's media ingestion and correction contracts.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from science_companion.contracts.science import (
    LicenseState,
    MediaType,
    SourceLicense,
)


class MediaAssetStatus(StrEnum):
    """Lifecycle status of a media asset or derived asset."""

    DISCOVERED = "discovered"
    PARSING = "parsing"
    PARSED = "parsed"
    QUARANTINED = "quarantined"
    BLOCKED = "blocked"
    CORRECTED = "corrected"
    REVOKED = "revoked"


class MediaAssetKind(StrEnum):
    """Kind of derived media asset."""

    IMAGE_REGIONS = "image_regions"
    IMAGE_OCR = "image_ocr"
    IMAGE_LEGEND = "image_legend"
    IMAGE_SCALE = "image_scale"
    FORMULA = "formula"
    TABLE = "table"
    CORRECTION = "correction"
    AUDIO_TRANSCRIPT = "audio_transcript"
    AUDIO_SEGMENT = "audio_segment"
    VIDEO_KEYFRAME = "video_keyframe"
    CAPTION_TRACK = "caption_track"


class MediaIngestionStatus(StrEnum):
    """Status of a media ingestion run."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    QUARANTINED = "quarantined"


class MediaGateResult(StrEnum):
    """Result of a single media input quality gate."""

    PASS = "pass"
    WAIT = "wait"
    FAIL = "fail"


class MediaQualityGate(StrEnum):
    """Named gates that imported media must pass before entering evidence."""

    MIME_TYPE = "mime_type"
    MAGIC_NUMBER = "magic_number"
    SIZE_LIMIT = "size_limit"
    MALICIOUS_CONTENT = "malicious_content"
    LICENSE = "license"
    PARSE = "parse"
    SCOPE = "scope"


class BoundingBox(BaseModel):
    """Normalized or pixel bounding box within an image."""

    x: float = Field(description="Left coordinate.")
    y: float = Field(description="Top coordinate.")
    width: float = Field(description="Box width.")
    height: float = Field(description="Box height.")
    unit: str = Field(default="pixel", description="Coordinate unit: pixel or normalized.")


class AssetRegion(BaseModel):
    """A detected region inside an image or scan."""

    region_id: str = Field(description="Stable region identifier.")
    label: str = Field(description="Semantic label, e.g. figure, axis, label, curve.")
    bbox: BoundingBox = Field(description="Region location.")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class OCRToken(BaseModel):
    """A single OCR token with location and confidence."""

    text: str = Field(description="Recognized text.")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    bbox: BoundingBox | None = Field(default=None)


class SymbolDefinition(BaseModel):
    """Definition of a symbol used in a formula."""

    symbol: str = Field(description="Symbol as rendered, e.g. 'E', 'α', 'ħ'.")
    definition: str | None = Field(default=None, description="Plain-language definition.")
    unit: str | None = Field(default=None, description="Physical unit if any.")
    first_occurrence: str | None = Field(
        default=None, description="Locator of first occurrence in the source."
    )


class FormulaAsset(BaseModel):
    """Structured representation of a scientific formula."""

    latex: str = Field(description="LaTeX source.")
    mathml: str | None = Field(default=None, description="MathML structured markup.")
    ast: dict[str, Any] = Field(
        default_factory=dict, description="Abstract syntax tree (operator/operand form)."
    )
    symbol_table: list[SymbolDefinition] = Field(default_factory=list)
    accessible_text: str | None = Field(
        default=None, description="Structured spoken/accessible representation."
    )


class TableColumn(BaseModel):
    """Schema column for a detected table."""

    name: str = Field(description="Column header.")
    data_type: str = Field(default="string", description="Inferred data type.")
    unit: str | None = Field(default=None, description="Physical unit if detected.")


class TableCell(BaseModel):
    """A single cell in a detected table."""

    value: str | None = Field(default=None, description="Cell value as text.")
    is_missing: bool = Field(default=False, description="Whether the value is missing/empty.")
    source_region: str | None = Field(
        default=None, description="Region or page locator in the original asset."
    )


class TableRow(BaseModel):
    """A row of table cells."""

    cells: list[TableCell] = Field(default_factory=list)
    row_index: int = Field(default=0, description="Zero-based row index.")


class TableSchema(BaseModel):
    """Schema for a detected table."""

    columns: list[TableColumn] = Field(default_factory=list)
    header_row_index: int | None = Field(default=None)
    footer_note: str | None = Field(default=None)


class TableAsset(BaseModel):
    """Structured representation of a scientific table."""

    table_schema: TableSchema = Field(default_factory=TableSchema)
    rows: list[TableRow] = Field(default_factory=list)
    missing_value_marker: str = Field(default="—", description="Marker used for missing values.")
    source_note: str | None = Field(
        default=None, description="Note tracing the table to its original asset region."
    )


class ImageDerivedData(BaseModel):
    """Structured payload for image-derived assets."""

    regions: list[AssetRegion] = Field(default_factory=list)
    ocr_tokens: list[OCRToken] = Field(default_factory=list)
    legend: str | None = Field(default=None)
    scale: str | None = Field(default=None, description="Scale bar or measurement note.")


class SpatialTemporalLocator(BaseModel):
    """Locator of a derived asset within its source asset."""

    page: int | None = Field(default=None)
    region: AssetRegion | None = Field(default=None)
    start_time: float | None = Field(default=None)
    end_time: float | None = Field(default=None)


class TranscriptWord(BaseModel):
    """A single word inside a transcript segment."""

    text: str = Field(description="Recognized word.")
    start_time: float = Field(description="Start time in seconds.")
    end_time: float = Field(description="End time in seconds.")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class TranscriptSegment(BaseModel):
    """A timed transcript segment with optional word-level alignment."""

    segment_id: str = Field(description="Stable segment identifier.")
    start_time: float = Field(description="Start time in seconds.")
    end_time: float = Field(description="End time in seconds.")
    text: str = Field(description="Transcribed text.")
    language: str | None = Field(default=None, description="Detected or declared language.")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    low_confidence: bool = Field(default=False, description="Whether the segment needs review.")
    words: list[TranscriptWord] = Field(default_factory=list)


class SpeakerSegment(BaseModel):
    """A speaker diarization segment."""

    segment_id: str = Field(description="Stable segment identifier.")
    speaker_id: str = Field(description="Speaker identifier, e.g. SPEAKER_00.")
    start_time: float = Field(description="Start time in seconds.")
    end_time: float = Field(description="End time in seconds.")


class Caption(BaseModel):
    """A single subtitle/caption cue."""

    caption_id: str = Field(description="Stable caption identifier.")
    start_time: float = Field(description="Start time in seconds.")
    end_time: float = Field(description="End time in seconds.")
    text: str = Field(description="Caption text.")
    language: str | None = Field(default=None)


class CaptionTrack(BaseModel):
    """Timed caption track for an audio or video asset."""

    track_id: str = Field(description="Stable track identifier.")
    language: str | None = Field(default=None)
    captions: list[Caption] = Field(default_factory=list)


class Keyframe(BaseModel):
    """A keyframe interpretation inside a video asset."""

    keyframe_id: str = Field(description="Stable keyframe identifier.")
    time: float = Field(description="Time in seconds.")
    interpretation: str = Field(description="Visual/scientific interpretation.")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class AudioVideoDerivedData(BaseModel):
    """Structured payload for audio/video-derived assets.

    Wraps ASR transcripts, speaker diarization, captions and video keyframe
    interpretations. All timed elements are bound to the original asset timeline.
    """

    transcript_segments: list[TranscriptSegment] = Field(default_factory=list)
    speaker_segments: list[SpeakerSegment] = Field(default_factory=list)
    captions: list[Caption] = Field(default_factory=list)
    keyframes: list[Keyframe] = Field(default_factory=list)
    language: str | None = Field(default=None, description="Primary detected/declared language.")
    multi_language: bool = Field(
        default=False,
        description="Whether multiple languages were detected.",
    )
    missing_audio_track: bool = Field(
        default=False,
        description="True when the video has no usable audio track.",
    )


class SourceAsset(BaseModel):
    """Original, immutable media input.

    A SourceAsset is the raw uploaded image, scan fragment, formula image or table
    file. Its bytes are hashed and its status is tracked independently of any
    derived extraction result.
    """

    asset_id: str = Field(description="Stable asset identifier.")
    account_id: str = Field(description="Owning account identifier.")
    project_id: str | None = Field(
        default=None, description="Project scope when owned by a project."
    )
    media_type: MediaType = Field(description="Declared media type.")
    detected_format: str | None = Field(default=None)
    original_filename: str = Field(description="Original filename.")
    byte_size: int = Field(ge=0)
    content_hash: str = Field(description="SHA-256 hash of raw content bytes.")
    created_at: datetime = Field(description="Asset creation timestamp.")
    acquired_at: datetime | None = Field(default=None)
    license: SourceLicense = Field(default_factory=SourceLicense)
    status: MediaAssetStatus = Field(default=MediaAssetStatus.DISCOVERED)
    integrity_status: str = Field(default="ok")
    object_storage_ref: str | None = Field(default=None)
    provenance_bundle_id: str | None = Field(default=None)
    updated_at: datetime = Field(description="Last asset update timestamp.")


class DerivedAsset(BaseModel):
    """Any extraction, OCR, structure parse or human correction result.

    DerivedAssets are immutable. A human correction creates a new DerivedAsset
    that points to the previous one via `derived_from_asset_id`, preserving the
    full correction chain.
    """

    derived_asset_id: str = Field(description="Stable derived asset identifier.")
    source_asset_id: str = Field(description="Original SourceAsset identifier.")
    derived_from_asset_id: str | None = Field(
        default=None, description="Previous derived asset when this is a correction."
    )
    derivation_type: MediaAssetKind = Field(description="Kind of derivation.")
    tool: str = Field(description="Tool/model that produced the derivation.")
    tool_version: str = Field(description="Tool/model version.")
    parameters: dict[str, Any] = Field(default_factory=dict)
    content_hash: str = Field(description="SHA-256 hash of canonical payload.")
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Domain-specific structured payload, e.g. formula or table asset.",
    )
    locator: SpatialTemporalLocator | None = Field(default=None)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    human_corrected: bool = Field(default=False)
    status: MediaAssetStatus = Field(default=MediaAssetStatus.PARSED)
    correction_reason: str | None = Field(default=None)
    created_at: datetime = Field(description="Derived asset creation timestamp.")


class ClaimBinding(BaseModel):
    """Binding from a media element to a Claim / Evidence / FactLock."""

    binding_id: str = Field(description="Stable binding identifier.")
    derived_asset_id: str = Field(description="Derived asset that carries the element.")
    element_ref: str = Field(description="Region, token, cell or formula element reference.")
    claim_id: str | None = Field(default=None)
    evidence_id: str | None = Field(default=None)
    fact_lock_id: str | None = Field(default=None)


class DataBinding(BaseModel):
    """Binding from a table/chart to a dataset version and transformation."""

    binding_id: str = Field(description="Stable binding identifier.")
    derived_asset_id: str = Field(description="Derived asset that carries the data.")
    source_data_ref: str | None = Field(default=None)
    transformation: str | None = Field(default=None)
    units: dict[str, str | None] = Field(default_factory=dict)
    missing_value_policy: str = Field(default="explicit")


class FormulaBinding(BaseModel):
    """Binding from a formula asset to its scientific context."""

    binding_id: str = Field(description="Stable binding identifier.")
    derived_asset_id: str = Field(description="Derived formula asset.")
    source_asset_id: str = Field(description="Original source asset.")
    definition_refs: list[str] = Field(default_factory=list)


class MediaManifest(BaseModel):
    """Unified manifest for one scientific media object.

    The manifest links the original SourceAsset to its DerivedAssets and to any
    scientific bindings. It is versioned; corrections update the manifest without
    overwriting the previous version.
    """

    manifest_id: str = Field(description="Stable manifest identifier.")
    version: int = Field(ge=1, description="Monotonic manifest version.")
    account_id: str = Field(description="Owning account identifier.")
    project_id: str | None = Field(default=None)
    source_asset_id: str = Field(description="Original source asset.")
    derived_asset_ids: list[str] = Field(default_factory=list)
    claim_bindings: list[ClaimBinding] = Field(default_factory=list)
    data_bindings: list[DataBinding] = Field(default_factory=list)
    formula_bindings: list[FormulaBinding] = Field(default_factory=list)
    validation_report_ids: list[str] = Field(default_factory=list)
    publication_state: str = Field(default="draft")
    created_at: datetime = Field(description="Manifest creation timestamp.")


class MediaUploadRequest(BaseModel):
    """Request to upload a scientific media asset."""

    filename: str = Field(description="Original filename.", min_length=1, max_length=500)
    media_type: MediaType = Field(description="Declared media type.")
    content: str = Field(
        description="Base64-encoded raw content bytes.", min_length=1
    )
    license_state: LicenseState | None = Field(default=None)
    title: str | None = Field(default=None)


class MediaCorrectionType(StrEnum):
    """Kind of human correction applied to a derived asset."""

    OCR_TEXT = "ocr_text"
    REGION_LABEL = "region_label"
    FORMULA_LATEX = "formula_latex"
    FORMULA_SYMBOL = "formula_symbol"
    TABLE_CELL = "table_cell"
    TABLE_SCHEMA = "table_schema"
    SCALE = "scale"
    LEGEND = "legend"
    TRANSCRIPT_TERM = "transcript_term"
    SPEAKER_SEGMENT = "speaker_segment"
    CAPTION_TEXT = "caption_text"
    KEYFRAME_INTERPRETATION = "keyframe_interpretation"


class MediaCorrectionRequest(BaseModel):
    """Request to correct a derived asset and produce a new version."""

    derived_asset_id: str = Field(description="Derived asset to correct.")
    correction_type: MediaCorrectionType = Field(description="Kind of correction.")
    target_ref: str = Field(
        description="Reference to the corrected element: token id, region id, cell coordinate, etc."
    )
    corrected_value: str = Field(description="New value.")
    reason: str | None = Field(default=None)


class MediaProjection(BaseModel):
    """Public projection of a media asset with its manifest and derived assets."""

    source_asset: SourceAsset = Field(description="Original source asset.")
    manifest: MediaManifest = Field(description="Current media manifest.")
    derived_assets: list[DerivedAsset] = Field(
        default_factory=list, description="Derived assets referenced by the manifest."
    )
    version_count: int = Field(default=1, ge=1, description="Number of manifest versions.")
    can_enter_evidence: bool = Field(
        default=False, description="Whether the asset passed all input quality gates."
    )
    gate_results: dict[MediaQualityGate, MediaGateResult] = Field(default_factory=dict)


class MediaIngestionRunRef(BaseModel):
    """Reference to an asynchronous media ingestion run."""

    run_id: str = Field(description="Ingestion run identifier.")
    asset_id: str | None = Field(default=None)
    status: MediaIngestionStatus = Field(default=MediaIngestionStatus.PENDING)
    gate_results: dict[MediaQualityGate, MediaGateResult] = Field(default_factory=dict)
    error: str | None = Field(default=None)
    created_at: datetime = Field(description="Run creation timestamp.")
    updated_at: datetime = Field(description="Last run update timestamp.")


class MediaError(BaseModel):
    """Uniform media error response."""

    error: str = Field(description="Stable error code.")
    message: str = Field(description="Human-readable, non-leaking message.")
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Opaque detail safe for logging; must not expose internal state.",
    )
