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

from science_companion.contracts.media import (
    AssetRegion,
    BoundingBox,
    DerivedAsset,
    FormulaAsset,
    ImageDerivedData,
    MediaAssetKind,
    MediaAssetStatus,
    MediaGateResult,
    MediaQualityGate,
    OCRToken,
    SourceAsset,
    SpatialTemporalLocator,
    SymbolDefinition,
    TableAsset,
    TableCell,
    TableColumn,
    TableRow,
    TableSchema,
)
from science_companion.contracts.science import MediaType

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

    tool: str = "science_companion.image.deterministic"
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

    tool: str = "science_companion.formula.deterministic"
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

    Parses CSV content or uses filename hints to produce a TableAsset with schema,
    units and explicit missing-value handling.
    """

    tool: str = "science_companion.table.deterministic"
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

        if source_asset.media_type == MediaType.TEXT_CSV and text:
            table = self._parse_csv(text)
        elif "pressure" in filename or "temperature" in filename:
            table = self._sample_physics_table()
        elif "experiment" in filename:
            table = self._sample_experiment_table()
        else:
            table = self._sample_physics_table()

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

    def _parse_csv(self, text: str) -> TableAsset:
        reader = csv.reader(io.StringIO(text.strip()))
        rows: list[list[str]] = list(reader)
        if not rows:
            return self._sample_physics_table()

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

    def _sample_physics_table(self) -> TableAsset:
        return TableAsset(
            table_schema=TableSchema(
                columns=[
                    TableColumn(name="Temp (K)", data_type="number", unit="K"),
                    TableColumn(name="Pressure (Pa)", data_type="number", unit="Pa"),
                ],
                header_row_index=0,
            ),
            rows=[
                TableRow(
                    cells=[
                        TableCell(value="273.15"),
                        TableCell(value="101325"),
                    ],
                    row_index=0,
                ),
                TableRow(
                    cells=[
                        TableCell(value="373.15"),
                        TableCell(value="", is_missing=True),
                    ],
                    row_index=1,
                ),
            ],
            source_note="物理实验数据示例",
        )

    def _sample_experiment_table(self) -> TableAsset:
        return TableAsset(
            table_schema=TableSchema(
                columns=[
                    TableColumn(name="Sample", data_type="string"),
                    TableColumn(name="Concentration (mol/L)", data_type="number", unit="mol/L"),
                    TableColumn(name="Absorbance", data_type="number"),
                ],
                header_row_index=0,
            ),
            rows=[
                TableRow(
                    cells=[
                        TableCell(value="A"),
                        TableCell(value="0.1"),
                        TableCell(value="0.25"),
                    ],
                    row_index=0,
                ),
                TableRow(
                    cells=[
                        TableCell(value="B"),
                        TableCell(value="0.2"),
                        TableCell(value="0.48"),
                    ],
                    row_index=1,
                ),
            ],
            source_note="化学实验数据示例",
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
    raise ExtractionError(f"不支持的媒体类型：{media_type}")
