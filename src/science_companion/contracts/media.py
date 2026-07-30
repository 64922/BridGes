"""Multimodal scientific media asset contracts.

These models define the public surface of imported scientific images, scans,
formulas and tables. They follow the SourceAsset / DerivedAsset / MediaManifest
pattern from the multimodal-science-studio research and are the authoritative
shape of T030's media ingestion and correction contracts.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from science_companion.contracts.ai import ModelRunLock
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
    claim_ids: list[str] = Field(
        default_factory=list,
        description="T034: 字幕覆盖的科学 Claim ID 列表，与朗读和文字稿共享。",
    )
    source_version: str | None = Field(
        default=None,
        description="T034: 字幕绑定的源内容版本标识。",
    )


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


# ── T032: 可编辑静态科学图与数据图表 ──────────────────────────────────


class ChartMark(StrEnum):
    """Visual mark type for a data chart."""

    BAR = "bar"
    LINE = "line"
    POINT = "point"
    AREA = "area"
    SCATTER = "scatter"
    ERROR_BAR = "error_bar"
    HISTOGRAM = "histogram"


class ChartAxisType(StrEnum):
    """Scale type of a chart axis."""

    LINEAR = "linear"
    LOG = "log"
    CATEGORICAL = "categorical"
    TEMPORAL = "temporal"


class MediaObjectType(StrEnum):
    """Type of a generated scientific media object."""

    CHART = "chart"
    SCIENTIFIC_FIGURE = "scientific_figure"
    INTERACTIVE_HTML = "interactive_html"
    ANIMATION = "animation"


class GenerationStatus(StrEnum):
    """Lifecycle status of a media generation task."""

    PENDING = "pending"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"
    VALIDATION_FAILED = "validation_failed"


class ChartAxis(BaseModel):
    """Axis specification for a chart."""

    field: str = Field(description="Data field bound to this axis.")
    label: str = Field(description="Axis display label.")
    unit: str | None = Field(default=None, description="Physical unit if applicable.")
    axis_type: ChartAxisType = Field(default=ChartAxisType.LINEAR)
    domain_min: float | None = Field(default=None)
    domain_max: float | None = Field(default=None)
    ticks: list[float] | None = Field(default=None)


class ChartEncodingMapping(BaseModel):
    """Mapping from a visual encoding channel to a data field."""

    encoding: str = Field(
        description="Encoding channel: x, y, color, shape, size, row, column."
    )
    field: str = Field(description="Data field mapped to this channel.")
    label: str | None = Field(default=None, description="Legend label if applicable.")


class ChartDataColumn(BaseModel):
    """Schema of one column in chart source data."""

    name: str = Field(description="Column name.")
    data_type: str = Field(default="number", description="Inferred data type.")
    unit: str | None = Field(default=None)


class ChartDataPoint(BaseModel):
    """A single data point in a chart dataset."""

    values: dict[str, str | float | None] = Field(
        description="Field -> value mapping."
    )
    is_missing: bool = Field(default=False)


class ChartDataTable(BaseModel):
    """Structured source data for a chart."""

    columns: list[ChartDataColumn] = Field(default_factory=list)
    rows: list[ChartDataPoint] = Field(default_factory=list)
    source_note: str | None = Field(
        default=None, description="Attribution or trace note."
    )


class ChartSpec(BaseModel):
    """Declarative specification of a data chart.

    The spec holds the source data, axis definitions, visual encoding mappings,
    and rendering hints. It IS the editable source — users can modify the spec
    and re-validate. The SVG is a derived rendering of this spec.
    """

    spec_id: str = Field(description="Stable spec identifier.")
    title: str = Field(description="Chart title.")
    mark: ChartMark = Field(description="Visual mark type.")
    data: ChartDataTable = Field(description="Source data for the chart.")
    axes: list[ChartAxis] = Field(default_factory=list)
    encodings: list[ChartEncodingMapping] = Field(default_factory=list)
    has_error_bars: bool = Field(default=False, description="Whether error bars are shown.")
    aggregation: str | None = Field(
        default=None,
        description="Aggregation function if any: sum, mean, count, etc.",
    )
    color_legend_title: str | None = Field(default=None)
    note: str | None = Field(default=None, description="Footnote or caveat.")


class FigureElement(BaseModel):
    """A labeled element in a scientific figure."""

    element_id: str = Field(description="Stable element identifier.")
    role: str = Field(
        description="Semantic role: axis_label, curve, annotation, legend, scale_bar, etc."
    )
    label: str = Field(description="Display label text.")
    claim_id: str | None = Field(default=None, description="Bound claim if any.")
    svg_fragment: str | None = Field(
        default=None, description="Inline SVG markup for this element."
    )


class ScientificFigureSpec(BaseModel):
    """Declarative specification of a scientific figure.

    A scientific figure consists of labeled elements with semantic roles,
    optionally bound to claims. The SVG is derived from the element definitions.
    """

    spec_id: str = Field(description="Stable spec identifier.")
    title: str = Field(description="Figure title.")
    elements: list[FigureElement] = Field(default_factory=list)
    description: str | None = Field(
        default=None, description="Overall figure description."
    )
    note: str | None = Field(default=None)


class EditableSource(BaseModel):
    """An editable source that defines a scientific media object.

    The source is the authority — rendered SVGs and PNGs are derived from it.
    Users can edit the source and re-validate to ensure consistency.
    """

    source_id: str = Field(description="Stable source identifier.")
    source_type: str = Field(
        description="Type: chart_spec, figure_spec, svg, storyboard_spec, sandbox_code, static_validation_report."
    )
    content: str = Field(
        description="JSON-encoded spec or raw SVG content."
    )
    format: str = Field(
        default="application/json",
        description="MIME type of the content field.",
    )
    version: int = Field(default=1, ge=1, description="Monotonic version.")


class AccessibilityAlternative(BaseModel):
    """Accessibility alternative for a visual media object."""

    alt_text: str = Field(
        description="Concise alternative text describing the visual.",
        min_length=1,
    )
    long_description: str | None = Field(
        default=None, description="Detailed description for complex visuals."
    )
    data_table: ChartDataTable | None = Field(
        default=None,
        description="Equivalent data table for charts; None for figures.",
    )


class ClaimVisualBinding(BaseModel):
    """Binding from a visual element to a claim, evidence, or fact lock."""

    binding_id: str = Field(description="Stable binding identifier.")
    element_ref: str = Field(
        description="Reference to the visual element: axis label, figure element id, etc."
    )
    claim_id: str | None = Field(default=None)
    evidence_id: str | None = Field(default=None)
    fact_lock_id: str | None = Field(default=None)


class ScientificMediaObject(BaseModel):
    """A generated scientific media object with editable source and bindings.

    This is the output of the generation pipeline — a stand-alone media work
    with its editable source, rendered SVG, claim bindings and accessibility
    alternative. It is versioned and can enter the task stage artifact system.
    """

    media_object_id: str = Field(description="Stable media object identifier.")
    account_id: str = Field(description="Owning account.")
    project_id: str | None = Field(default=None)
    media_type: MediaObjectType = Field(description="Chart or scientific figure.")
    editable_source: EditableSource = Field(description="Editable source spec.")
    svg_content: str | None = Field(
        default=None, description="Rendered SVG output."
    )
    claim_bindings: list[ClaimVisualBinding] = Field(default_factory=list)
    data_bindings: list[DataBinding] = Field(default_factory=list)
    fact_lock_set_id: str | None = Field(default=None)
    accessibility: AccessibilityAlternative = Field(
        description="Alt text and equivalent data table."
    )
    validation_errors: list[str] = Field(default_factory=list)
    status: GenerationStatus = Field(default=GenerationStatus.COMPLETED)
    created_at: datetime = Field(description="Creation timestamp.")
    updated_at: datetime = Field(description="Last update timestamp.")


class ChartGenerationRequest(BaseModel):
    """Request to generate a data chart from source data.

    The caller provides structured data and describes what to plot.
    """

    title: str = Field(description="Chart title.", min_length=1)
    mark: ChartMark = Field(description="Visual mark type.")
    data: ChartDataTable = Field(description="Source data for the chart.")
    x_field: str = Field(description="Field name for the x-axis.", min_length=1)
    y_field: str = Field(description="Field name for the y-axis.", min_length=1)
    color_field: str | None = Field(
        default=None, description="Field name for color encoding."
    )
    error_field: str | None = Field(
        default=None, description="Field name for error/uncertainty values."
    )
    x_label: str | None = Field(default=None, description="X-axis label (auto if omitted).")
    y_label: str | None = Field(default=None, description="Y-axis label (auto if omitted).")
    x_unit: str | None = Field(default=None)
    y_unit: str | None = Field(default=None)
    claim_ids: list[str] = Field(default_factory=list)
    fact_lock_ids: list[str] = Field(default_factory=list)
    aggregation: str | None = Field(default=None)
    project_id: str | None = Field(default=None)


class FigureGenerationRequest(BaseModel):
    """Request to generate a scientific figure.

    The caller provides element definitions and optional claim bindings.
    """

    title: str = Field(description="Figure title.", min_length=1)
    elements: list[FigureElement] = Field(
        default_factory=list,
        description="Figure elements. When empty, the service generates defaults.",
    )
    claim_ids: list[str] = Field(default_factory=list)
    fact_lock_ids: list[str] = Field(default_factory=list)
    description: str | None = Field(default=None)
    project_id: str | None = Field(default=None)


class GenerationResult(BaseModel):
    """Result of a media generation task."""

    media_object: ScientificMediaObject = Field(description="Generated media object.")
    model_run_lock: ModelRunLock | None = Field(
        default=None, description="Model run lock when a model was used."
    )


class SpecValidationResult(BaseModel):
    """Result of validating a chart/figure spec against source data and facts."""

    valid: bool = Field(description="Whether the spec passes all checks.")
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    data_consistent: bool = Field(
        default=True, description="Whether values match the source data."
    )
    claims_consistent: bool = Field(
        default=True, description="Whether claim bindings are valid."
    )


# ── T033: 结构化分镜与沙箱运行 ──────────────────────────────────────


class SceneAccessibility(BaseModel):
    """无障碍描述信息，附加给一个场景。"""

    alt_text: str = Field(description="场景替代文本。")
    long_description: str | None = Field(
        default=None, description="场景长描述，用于复杂视觉场景。"
    )


class LifecycleStage(BaseModel):
    """动画对象的一个生命周期阶段。"""

    stage: Literal["enter", "hold", "transform", "exit"] = Field(
        description="阶段类型：进入、保持、变换、退出。"
    )
    timing_seconds: float = Field(
        ge=0.0, description="阶段持续时间，单位秒。"
    )
    description: str = Field(description="阶段描述。")


class VisualObject(BaseModel):
    """分镜场景中的一个视觉对象。"""

    object_id: str = Field(description="稳定对象标识符。")
    label: str = Field(description="对象显示标签。")
    role: str = Field(
        description="语义角色，如 axis_label, curve, annotation, particle。"
    )
    initial_state: str = Field(description="初始状态描述。")
    final_state: str | None = Field(default=None, description="终态描述。")
    lifecycle_stages: list[LifecycleStage] = Field(
        default_factory=list,
        description="动画对象的进入/保持/变换/退出阶段。",
    )
    motion_trajectory: str | None = Field(
        default=None, description="运动轨迹描述，动画关键信息。"
    )
    claim_id: str | None = Field(default=None, description="绑定的 Claim ID。")


class SceneSpec(BaseModel):
    """单场景视觉定义，可独立编辑和验证。

    SceneSpec 定义单个场景的静态视觉设计，包含视觉对象列表、布局、状态、
    数据绑定和无障碍描述。被 MediaStoryboard 中的镜头引用。
    """

    scene_spec_id: str = Field(description="稳定场景规格标识符。")
    title: str = Field(description="场景标题。")
    visual_objects: list[VisualObject] = Field(
        default_factory=list, description="场景中的视觉对象列表。"
    )
    layout_description: str = Field(
        default="", description="布局描述。"
    )
    state_description: str | None = Field(
        default=None, description="场景状态描述。"
    )
    accessibility: SceneAccessibility | None = Field(
        default=None, description="场景级无障碍描述。"
    )
    created_at: datetime = Field(description="创建时间戳。")


class StoryboardStatus(StrEnum):
    """结构化分镜生命周期状态，独立于 T032 的 GenerationStatus。"""

    DRAFT = "draft"
    DESIGNING = "designing"
    SOURCE_GENERATED = "source_generated"
    STATIC_VALIDATED = "static_validated"
    SANDBOX_RENDERING = "sandbox_rendering"
    COMPLETED = "completed"
    REPAIRABLE = "repairable"
    REPAIR_EXHAUSTED = "repair_exhausted"
    QUARANTINED = "quarantined"
    FAILED = "failed"


class StoryboardNarration(BaseModel):
    """分镜中一个镜头的旁白描述。"""

    text: str = Field(description="旁白正文。")
    language: str = Field(default="zh-CN", description="旁白语言。")
    voice_over_text: str | None = Field(
        default=None, description="录音文本，与旁白不同时使用。"
    )
    claim_ids: list[str] = Field(
        default_factory=list, description="本旁白覆盖的 Claim ID 列表。"
    )


class StoryboardClaimBinding(BaseModel):
    """分镜场景中视觉元素到 Claim/FactLock 的绑定。"""

    binding_id: str = Field(description="稳定绑定标识符。")
    scene_id: str = Field(description="关联的镜头 ID。")
    element_ref: str = Field(description="引用的视觉元素引用。")
    claim_id: str | None = Field(default=None, description="绑定的 Claim ID。")
    fact_lock_id: str | None = Field(default=None, description="绑定的 FactLock ID。")


class StoryboardScene(BaseModel):
    """分镜中的一个镜头，引用 SceneSpec。

    镜头是时间序列中的一段，包含对 SceneSpec 的引用、持续时间、
    过渡类型、旁白、Claim 绑定和无障碍描述。
    """

    scene_id: str = Field(description="稳定镜头标识符。")
    scene_number: int = Field(ge=1, description="镜头序号。")
    scene_spec_id: str = Field(description="引用的 SceneSpec ID。")
    timing_seconds: float = Field(
        ge=0.0, description="镜头持续时间，单位秒。"
    )
    transition_type: Literal["cut", "dissolve", "push"] | None = Field(
        default=None,
        description="镜头间过渡类型：cut（直接切换）、dissolve（溶解）、push（推入）。",
    )
    narration: StoryboardNarration | None = Field(
        default=None, description="镜头旁白。"
    )
    scene_claim_bindings: list[StoryboardClaimBinding] = Field(
        default_factory=list, description="场景级 Claim 绑定。"
    )
    scene_accessibility: str | None = Field(
        default=None, description="场景级无障碍描述。"
    )


class MediaStoryboard(BaseModel):
    """结构化分镜——镜头时间序列。

    定义教学目标、镜头序列（引用 SceneSpec）、媒体类型和 Claim 绑定。
    每个镜头包含时间、过渡、旁白和无障碍描述。
    """

    storyboard_id: str = Field(description="稳定分镜标识符。")
    account_id: str = Field(description="拥有账户 ID。")
    project_id: str | None = Field(default=None, description="所属项目 ID。")
    title: str = Field(description="分镜标题。")
    teaching_objectives: list[str] = Field(
        default_factory=list, description="教学目标列表。"
    )
    scenes: list[StoryboardScene] = Field(
        default_factory=list, description="镜头列表。"
    )
    media_type: Literal["animation", "interactive_html"] = Field(
        description="媒体类型：animation（动画）或 interactive_html（交互 HTML）。"
    )
    status: StoryboardStatus = Field(
        default=StoryboardStatus.DRAFT, description="分镜状态。"
    )
    created_at: datetime = Field(description="创建时间戳。")
    updated_at: datetime = Field(description="最后更新时间戳。")


class StoryboardGenerationRequest(BaseModel):
    """创建结构化分镜的请求。"""

    title: str = Field(description="分镜标题。", min_length=1)
    teaching_objectives: list[str] = Field(
        default_factory=list, description="教学目标列表。"
    )
    media_type: Literal["animation", "interactive_html"] = Field(
        description="媒体类型。"
    )
    scenes: list[StoryboardScene] = Field(
        default_factory=list,
        description="预定义的镜头列表。为空时由生成器创建默认分镜。",
    )
    claim_ids: list[str] = Field(default_factory=list)
    fact_lock_ids: list[str] = Field(default_factory=list)
    project_id: str | None = Field(default=None)


class StoryboardResult(BaseModel):
    """分镜生成结果。"""

    storyboard: MediaStoryboard = Field(description="生成的分镜。")
    scene_specs: dict[str, SceneSpec] = Field(
        default_factory=dict, description="分镜引用的 SceneSpec 字典。"
    )


# ── T033: 沙箱运行 ──────────────────────────────────────────────────


class SandboxRunStatus(StrEnum):
    """沙箱运行状态，只描述运行本身。"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class SandboxResourceLimits(BaseModel):
    """沙箱资源限制。"""

    max_cpu_seconds: float = Field(
        default=30.0, ge=1.0, description="最大 CPU 时间，单位秒。"
    )
    max_memory_mb: int = Field(
        default=512, ge=32, description="最大内存，单位 MB。"
    )
    max_disk_mb: int = Field(
        default=100, ge=10, description="最大磁盘，单位 MB。"
    )
    max_processes: int = Field(
        default=10, ge=1, description="最大进程数。"
    )
    network_allowed: bool = Field(
        default=False, description="是否允许网络访问，默认禁止。"
    )
    keys_allowed: bool = Field(
        default=False, description="是否允许访问密钥，默认禁止。"
    )


class SandboxDependency(BaseModel):
    """沙箱运行的依赖项记录。"""

    name: str = Field(description="依赖名称。")
    version: str | None = Field(default=None, description="版本。")
    allowed: bool = Field(
        default=True, description="是否在白名单中。"
    )


class FactLockViolation(BaseModel):
    """沙箱修复过程中触犯事实锁的记录。"""

    lock_id: str = Field(description="触犯的 FactLock ID。")
    claim_id: str | None = Field(default=None, description="关联的 Claim ID。")
    attempted_change: str = Field(description="试图修改的值。")
    reason: str = Field(description="被拒绝的原因。")


class SandboxResourceUsage(BaseModel):
    """沙箱运行实际资源使用记录。"""

    cpu_time_ms: int = Field(default=0, ge=0, description="CPU 时间，单位毫秒。")
    memory_bytes: int = Field(default=0, ge=0, description="内存使用，单位字节。")
    disk_bytes: int = Field(default=0, ge=0, description="磁盘使用，单位字节。")
    network_blocked: bool = Field(
        default=True, description="网络是否被阻止。"
    )
    keys_blocked: bool = Field(
        default=True, description="密钥访问是否被阻止。"
    )


class SandboxRunRequest(BaseModel):
    """提交沙箱运行的请求。"""

    storyboard_id: str = Field(description="关联的分镜 ID。")
    source_code: str = Field(description="可执行代码（Python/HTML/JS）。")
    code_language: str = Field(
        description="代码语言：python, html, javascript。"
    )
    repair_budget: int = Field(
        default=3, ge=0, description="有限修复次数上限。"
    )
    fact_lock_ids: list[str] = Field(
        default_factory=list,
        description="禁止触犯的事实锁 ID 列表。",
    )
    resource_limits: SandboxResourceLimits = Field(
        default_factory=SandboxResourceLimits, description="资源限制。"
    )


class SandboxRunResult(BaseModel):
    """沙箱运行结果。"""

    run_id: str = Field(description="沙箱运行标识符。")
    storyboard_id: str = Field(description="关联的分镜 ID。")
    status: SandboxRunStatus = Field(description="运行状态。")
    output: str | None = Field(default=None, description="标准输出或渲染产物。")
    error_log: list[str] = Field(
        default_factory=list, description="运行错误日志。"
    )
    resource_usage: SandboxResourceUsage = Field(
        default_factory=SandboxResourceUsage, description="资源使用记录。"
    )
    dependencies: list[SandboxDependency] = Field(
        default_factory=list, description="依赖项记录。"
    )
    content_hash: str = Field(description="输出内容 SHA-256 哈希。")
    fact_lock_violations: list[FactLockViolation] = Field(
        default_factory=list, description="触犯的事实锁记录。"
    )
    repair_attempts: int = Field(default=0, ge=0, description="已尝试的修复次数。")
    created_at: datetime = Field(description="创建时间戳。")
    completed_at: datetime | None = Field(
        default=None, description="完成时间戳。"
    )


# ── T033: 静态检查与验证报告 ────────────────────────────────────────


class StaticCheckResult(BaseModel):
    """代码生成后的静态检查结果。"""

    passed: bool = Field(description="是否通过静态检查。")
    ast_valid: bool = Field(default=True, description="AST 解析是否有效。")
    deps_whitelisted: bool = Field(
        default=True, description="依赖是否在白名单中。"
    )
    lint_ok: bool = Field(
        default=True, description="代码规范检查是否通过。"
    )
    errors: list[str] = Field(
        default_factory=list, description="检查错误列表。"
    )


class ValidationReport(BaseModel):
    """分镜沙箱运行的完整验证报告。"""

    report_id: str = Field(description="稳定报告标识符。")
    run_id: str = Field(description="关联的沙箱运行 ID。")
    storyboard_id: str = Field(description="关联的分镜 ID。")
    static_check: StaticCheckResult | None = Field(
        default=None, description="静态检查结果。"
    )
    sandbox_result: SandboxRunResult | None = Field(
        default=None, description="沙箱运行结果。"
    )
    science_valid: bool = Field(
        default=False, description="科学一致性验证是否通过。"
    )
    fact_locks_preserved: bool = Field(
        default=False, description="事实锁是否完整保持。"
    )
    accessibility_checked: bool = Field(
        default=False, description="是否进行了无障碍检查。"
    )
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime = Field(description="报告创建时间戳。")


# ── T034: 朗读、字幕与完整无障碍替代 ──────────────────────────────────


class AccessibilityTargetKind(StrEnum):
    """无障碍包的目标对象类型。"""

    STORYBOARD = "storyboard"
    MEDIA_OBJECT = "media_object"
    MEDIA_ASSET = "media_asset"


class NarrationSynthesisStatus(StrEnum):
    """朗读音频的合成状态。

    T034 使用确定性合成器；真实 Qwen TTS 适配器在 T062 接入。
    未通过科学校验的文本保持 PENDING_SYNTHESIS，不直接合成语音。
    """

    PENDING_SYNTHESIS = "pending_synthesis"
    SYNTHESIZED = "synthesized"
    FAILED = "failed"


class PronunciationNoteKind(StrEnum):
    """需要发音处理的朗读内容类型。"""

    NUMBER = "number"
    UNIT = "unit"
    FORMULA = "formula"
    ABBREVIATION = "abbreviation"


class PronunciationNote(BaseModel):
    """朗读中数字、单位、公式和缩写的发音处理记录。"""

    token: str = Field(description="原始文本片段。")
    kind: PronunciationNoteKind = Field(description="片段类型。")
    spoken_form: str | None = Field(
        default=None, description="确定的口语化读法；无法可靠朗读时为 None。"
    )
    degraded: bool = Field(
        default=False,
        description="无法可靠朗读时的显式降级标记，提示查看可访问公式或文本。",
    )


class NarrationTimingEntry(BaseModel):
    """句段到朗读音频时间轴的映射。"""

    segment_id: str = Field(description="对应的文字稿句段 ID。")
    text: str = Field(description="句段文本。")
    start_time: float = Field(ge=0.0, description="开始时间，单位秒。")
    end_time: float = Field(ge=0.0, description="结束时间，单位秒。")


class NarrationAudio(BaseModel):
    """科学媒体对象的朗读替代。

    朗读文本必须来自与字幕/文字稿相同的、已经过科学校验的内容，
    与它们共享同一 Claim 集合和源版本。
    """

    narration_id: str = Field(description="稳定朗读标识符。")
    language: str = Field(default="zh-CN", description="朗读语言。")
    text: str = Field(description="朗读正文，与文字稿同源。")
    voice: str = Field(default="default", description="声音标识。")
    status: NarrationSynthesisStatus = Field(
        default=NarrationSynthesisStatus.PENDING_SYNTHESIS,
        description="合成状态。",
    )
    audio_ref: str | None = Field(
        default=None, description="合成音频的受控存储引用；未合成时为 None。"
    )
    duration_seconds: float = Field(default=0.0, ge=0.0)
    timing: list[NarrationTimingEntry] = Field(
        default_factory=list, description="句段到音频时间的映射。"
    )
    pronunciation_notes: list[PronunciationNote] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)
    source_version: str | None = Field(default=None)


class Transcript(BaseModel):
    """媒体对象的完整文字稿，与朗读、字幕共享 Claim 与版本。"""

    transcript_id: str = Field(description="稳定文字稿标识符。")
    language: str = Field(default="zh-CN")
    segments: list[TranscriptSegment] = Field(default_factory=list)
    full_text: str = Field(default="", description="全文文本。")
    claim_ids: list[str] = Field(default_factory=list)
    source_version: str | None = Field(default=None)


class KeyboardPathStep(BaseModel):
    """键盘路径中的一个操作步骤。"""

    step_number: int = Field(ge=1, description="步骤序号。")
    action: str = Field(description="操作描述。")
    keys: str = Field(description="按键组合，如 Space、Tab、ArrowRight。")
    screen_reader_announcement: str = Field(
        description="屏幕阅读器在该步骤的播报文本。", min_length=1
    )


class KeyboardAccessPath(BaseModel):
    """一个核心媒体任务的完整键盘操作路径。"""

    path_id: str = Field(description="稳定路径标识符。")
    task: str = Field(
        description="任务标识：play_pause, seek, toggle_captions, "
        "toggle_reduced_motion, open_transcript。"
    )
    steps: list[KeyboardPathStep] = Field(default_factory=list)


class PlaybackControls(BaseModel):
    """时间内容的播放控制能力声明。"""

    can_pause: bool = Field(default=True)
    can_seek: bool = Field(default=True)
    can_change_speed: bool = Field(default=True)
    captions_available: bool = Field(default=True)
    reduced_motion_available: bool = Field(default=True)
    keyboard_operable: bool = Field(default=True)


class ReducedMotionFrame(BaseModel):
    """减少动画模式下的一个静态帧。"""

    frame_id: str = Field(description="稳定帧标识符。")
    order: int = Field(ge=1, description="帧序号。")
    source_ref: str = Field(description="对应的镜头/场景/元素引用。")
    description: str = Field(description="帧的科学内容描述，与旁白一致。")
    start_time: float = Field(default=0.0, ge=0.0)
    end_time: float = Field(default=0.0, ge=0.0)
    claim_ids: list[str] = Field(default_factory=list)


class ReducedMotionVariant(BaseModel):
    """减少动画合同：用静态帧序列替代连续动画。"""

    variant_id: str = Field(description="稳定变体标识符。")
    frames: list[ReducedMotionFrame] = Field(default_factory=list)
    note: str | None = Field(
        default=None, description="时间内容被逐帧展示的说明。"
    )
    claim_ids: list[str] = Field(default_factory=list)
    source_version: str | None = Field(default=None)


class SequentialReadingBlock(BaseModel):
    """顺序阅读视图中的一个文本块。"""

    order: int = Field(ge=1, description="阅读顺序。")
    role: str = Field(
        description="块角色：title, objective, scene_narration, description, data_row。"
    )
    text: str = Field(description="块文本。")
    claim_ids: list[str] = Field(default_factory=list)


class SequentialReadingView(BaseModel):
    """顺序阅读合同：屏幕阅读器可线性遍历的内容版本。"""

    view_id: str = Field(description="稳定视图标识符。")
    blocks: list[SequentialReadingBlock] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)
    source_version: str | None = Field(default=None)


class AccessibilityBundle(BaseModel):
    """一个科学媒体对象的完整无障碍替代包。

    汇集朗读、文字稿、字幕、替代文本、键盘路径、减少动画和顺序阅读版本。
    所有替代共享同一 Claim 集合和源版本，并经过相同的科学校验。
    """

    bundle_id: str = Field(description="稳定无障碍包标识符。")
    account_id: str = Field(description="拥有账户 ID。")
    project_id: str | None = Field(default=None)
    target_kind: AccessibilityTargetKind = Field(description="目标对象类型。")
    target_id: str = Field(description="目标对象 ID。")
    source_version: str = Field(description="目标内容版本标识（内容哈希或版本号）。")
    language: str = Field(default="zh-CN")
    claim_ids: list[str] = Field(default_factory=list)
    alt_text: str = Field(description="简短替代文本。", min_length=1)
    long_description: str | None = Field(default=None)
    transcript: Transcript = Field(description="完整文字稿。")
    caption_track: CaptionTrack = Field(description="字幕轨。")
    narration: NarrationAudio = Field(description="朗读替代。")
    keyboard_paths: list[KeyboardAccessPath] = Field(default_factory=list)
    playback_controls: PlaybackControls = Field(default_factory=PlaybackControls)
    reduced_motion: ReducedMotionVariant = Field(description="减少动画变体。")
    sequential_view: SequentialReadingView = Field(description="顺序阅读视图。")
    science_validated: bool = Field(
        default=False,
        description="无障碍替代是否通过了与主内容相同的科学校验。",
    )
    validation_errors: list[str] = Field(default_factory=list)
    created_at: datetime = Field(description="创建时间戳。")
    updated_at: datetime = Field(description="最后更新时间戳。")


class AccessibilityBundleRequest(BaseModel):
    """生成无障碍包的请求。"""

    target_kind: AccessibilityTargetKind = Field(description="目标对象类型。")
    target_id: str = Field(description="目标对象 ID。", min_length=1)
    language: str = Field(default="zh-CN")
    project_id: str | None = Field(default=None)


class AccessibilityValidationResult(BaseModel):
    """无障碍包的验证结果。"""

    valid: bool = Field(description="是否通过全部检查。")
    claims_consistent: bool = Field(
        default=True, description="音频、字幕和文字稿是否共享同一 Claim 集合。"
    )
    version_consistent: bool = Field(
        default=True, description="所有替代是否绑定同一源版本。"
    )
    keyboard_operable: bool = Field(
        default=True, description="核心媒体任务是否可由键盘与屏幕阅读器完成。"
    )
    playback_controllable: bool = Field(
        default=True, description="是否支持暂停、时间控制和减少动画。"
    )
    science_validated: bool = Field(
        default=True, description="替代内容是否通过相同的科学校验。"
    )
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class PlaybackControlRequest(BaseModel):
    """播放控制请求。"""

    action: Literal[
        "start", "pause", "resume", "seek", "set_reduced_motion"
    ] = Field(description="控制动作。")
    position_seconds: float | None = Field(
        default=None, ge=0.0, description="seek 目标位置，单位秒。"
    )
    enabled: bool | None = Field(
        default=None, description="set_reduced_motion 的开关值。"
    )


class PlaybackState(BaseModel):
    """一个无障碍包的播放状态，支持暂停、时间控制和减少动画。"""

    state_id: str = Field(description="稳定状态标识符。")
    bundle_id: str = Field(description="关联的无障碍包 ID。")
    paused: bool = Field(default=False)
    position_seconds: float = Field(default=0.0, ge=0.0)
    duration_seconds: float = Field(default=0.0, ge=0.0)
    speed: float = Field(default=1.0, gt=0.0)
    reduced_motion_enabled: bool = Field(default=False)
    captions_enabled: bool = Field(default=True)
    active_caption_text: str | None = Field(
        default=None, description="当前时间点的字幕文本。"
    )
    active_frame_description: str | None = Field(
        default=None,
        description="减少动画模式下当前时间点的静态帧描述。",
    )
    updated_at: datetime = Field(description="最后更新时间戳。")


# ── T035: 跨媒体一致性与多模态发布门 ─────────────────────────────────


class CrossMediaClaimEntry(BaseModel):
    """一个媒体对象中引用的 Claim 及其上下文。"""

    claim_id: str = Field(description="Claim ID。")
    media_ref: str = Field(
        description="媒体对象中的引用位置：对象 ID、元素引用或轴字段。"
    )
    media_kind: str = Field(
        description="媒体类型：text, chart, figure, storyboard, audio, video, interactive。"
    )
    canonical_value: str | None = Field(
        default=None, description="该 Claim 在此媒体中表达的标准值。"
    )
    qualifiers: list[str] = Field(
        default_factory=list, description="限定条件列表。"
    )
    citation_ids: list[str] = Field(default_factory=list)


class CrossMediaInconsistency(BaseModel):
    """跨媒体一致性检查发现的单条不一致。"""

    inconsistency_id: str = Field(description="稳定不一致标识符。")
    claim_id: str = Field(description="涉及的 Claim ID。")
    kind: str = Field(
        description="不一致类型：value_mismatch, qualifier_missing, "
        "citation_missing, terminology_conflict。"
    )
    media_refs: list[str] = Field(
        default_factory=list, description="涉及的媒体引用位置。"
    )
    description: str = Field(description="不一致描述。")
    severity: str = Field(
        default="error", description="严重程度：error 或 warning。"
    )


class CrossMediaConsistencyResult(BaseModel):
    """跨媒体 Claim 一致性检查结果。"""

    consistent: bool = Field(description="是否全部一致。")
    entries_checked: int = Field(default=0, ge=0, description="检查的条目数。")
    inconsistencies: list[CrossMediaInconsistency] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class MultimodalPublishGate(StrEnum):
    """多模态发布门名称。"""

    CLAIM_CONSISTENCY = "claim_consistency"
    LICENSE = "license"
    AUTHENTICITY = "authenticity"
    SANDBOX = "sandbox"
    ACCESSIBILITY = "accessibility"
    FACT_LOCK = "fact_lock"
    INVALIDATION = "invalidation"


class MultimodalGateResult(StrEnum):
    """多模态发布门单项结果。"""

    PASS = "pass"
    FAIL = "fail"


class MultimodalPublishGateResult(BaseModel):
    """多模态发布门综合结果。"""

    passed: bool = Field(description="是否全部通过。")
    gate_results: dict[MultimodalPublishGate, MultimodalGateResult] = Field(
        default_factory=dict
    )
    failed_gates: list[MultimodalPublishGate] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    consistency_result: CrossMediaConsistencyResult | None = Field(default=None)


class MediaPublishRecord(BaseModel):
    """多模态发布版本记录。

    保留原始资产、分镜、可编辑源、渲染物和验证记录的完整引用。
    """

    record_id: str = Field(description="稳定发布记录标识符。")
    account_id: str = Field(description="发布账户 ID。")
    project_id: str | None = Field(default=None)
    media_object_ids: list[str] = Field(
        default_factory=list, description="发布的媒体对象 ID 列表。"
    )
    source_asset_ids: list[str] = Field(
        default_factory=list, description="关联的原始资产 ID 列表。"
    )
    storyboard_ids: list[str] = Field(
        default_factory=list, description="关联的分镜 ID 列表。"
    )
    editable_source_ids: list[str] = Field(
        default_factory=list, description="关联的可编辑源 ID 列表。"
    )
    rendered_artifact_refs: list[str] = Field(
        default_factory=list, description="渲染产物存储引用列表。"
    )
    validation_report_ids: list[str] = Field(
        default_factory=list, description="验证报告 ID 列表。"
    )
    accessibility_bundle_ids: list[str] = Field(
        default_factory=list, description="无障碍包 ID 列表。"
    )
    claim_ids: list[str] = Field(
        default_factory=list, description="发布版本绑定的 Claim ID 列表。"
    )
    fact_lock_set_id: str | None = Field(default=None)
    gate_result: MultimodalPublishGateResult = Field(
        description="发布门检查结果。"
    )
    published_at: datetime = Field(description="发布时间戳。")
    invalidated: bool = Field(
        default=False, description="发布后是否被失效传播。"
    )
    invalidation_reason: str | None = Field(default=None)


class MediaPublishRequest(BaseModel):
    """多模态发布请求。"""

    media_object_ids: list[str] = Field(
        default_factory=list, description="要发布的媒体对象 ID 列表。"
    )
    source_asset_ids: list[str] = Field(
        default_factory=list, description="关联的原始资产 ID 列表。"
    )
    storyboard_ids: list[str] = Field(
        default_factory=list, description="关联的分镜 ID 列表。"
    )
    accessibility_bundle_ids: list[str] = Field(
        default_factory=list, description="关联的无障碍包 ID 列表。"
    )
    project_id: str | None = Field(default=None)

