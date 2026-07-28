"""Tests for model-backed OCR/vision extraction ports (T061).

The tests use a programmable gateway adapter so they do not call the real Qwen
API. They verify that image, formula and table assets are parsed from model
output, that the immutable ``ModelRunLock`` is recorded, and that failures keep
the original asset intact.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from typing import Any

import pytest

from science_companion.ai import CapabilityRegistry, ModelGateway
from science_companion.ai.adapters import AdapterResult
from science_companion.contracts.ai import (
    CapabilityKind,
    CapabilityRecord,
    RetryPolicy,
)
from science_companion.contracts.media import (
    MediaAssetKind,
    MediaAssetStatus,
)
from science_companion.contracts.science import (
    LicenseState,
    MediaType,
    SourceLicense,
)
from science_companion.contracts.workflows import RunContextEnvelope
from science_companion.media import MediaIngestionService
from science_companion.media.qwen_extraction import QwenOcrExtractor


def _context() -> RunContextEnvelope:
    return RunContextEnvelope(
        run_id="run-1",
        account_id="account-1",
        project_id="project-1",
        workflow_name="wf",
        workflow_version="1",
        submitted_at=datetime.now(UTC),
    )


def _source_asset(filename: str, media_type: MediaType) -> Any:
    from science_companion.contracts.media import SourceAsset

    return SourceAsset(
        asset_id="asset-1",
        account_id="account-1",
        project_id="project-1",
        media_type=media_type,
        detected_format=media_type.value,
        original_filename=filename,
        byte_size=100,
        content_hash="hash",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        license=SourceLicense(state=LicenseState.USER_OWNED),
        status=MediaAssetStatus.DISCOVERED,
    )


class _ProgrammableOcrAdapter:
    def __init__(self, responses: list[AdapterResult | Exception]) -> None:
        self.responses = list(responses)
        self.last_payload: dict[str, Any] | None = None

    def call(
        self,
        capability: CapabilityRecord,
        run_context: RunContextEnvelope,
        payload: dict[str, Any],
    ) -> AdapterResult:
        self.last_payload = payload
        if not self.responses:
            raise RuntimeError("exhausted")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _ocr_gateway(result: AdapterResult | Exception) -> ModelGateway:
    registry = CapabilityRegistry()
    registry.register(
        CapabilityRecord(
            name="qwen_ocr",
            version="1",
            kind=CapabilityKind.MODEL,
            vendor="qwen",
            region="cn-beijing",
            model_id="qwen-vl-ocr",
            input_schema_version="image-ocr-v1",
            output_schema_version="ocr-text-v1",
            retry_policy=RetryPolicy(max_attempts=1),
        )
    )
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_ocr", "1", _ProgrammableOcrAdapter([result]))
    return gateway


def _png_bytes() -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def test_image_extraction_produces_ocr_tokens_and_regions() -> None:
    gateway = _ocr_gateway(
        AdapterResult(
            actual_model_id="qwen-vl-ocr",
            output={"content": "Figure 1. Growth over time.", "task": "advanced_recognition"},
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )
    )
    extractor = QwenOcrExtractor(gateway, _context())
    derived = extractor.extract(_source_asset("graph.png", MediaType.IMAGE_PNG), _png_bytes())

    assert len(derived) == 1
    asset = derived[0]
    assert asset.derivation_type == MediaAssetKind.IMAGE_REGIONS
    assert asset.confidence < 1.0
    assert asset.parameters["ocr_task"] == "advanced_recognition"
    assert "model_run_lock" in asset.parameters

    payload = asset.payload
    assert any(token["text"] == "Figure" for token in payload["ocr_tokens"])
    assert payload["legend"] == "Figure 1. Growth over time."
    assert any(region["label"] == "full_image" for region in payload["regions"])


def test_formula_extraction_parses_json_symbol_table() -> None:
    gateway = _ocr_gateway(
        AdapterResult(
            actual_model_id="qwen-vl-ocr",
            output={
                "content": (
                    '{"latex": "E = mc^2", "accessible_text": "E equals m c squared", '
                    '"symbol_table": [{"symbol": "E", "definition": "energy", "unit": "J"}]}'
                ),
                "task": "formula_recognition",
            },
        )
    )
    extractor = QwenOcrExtractor(gateway, _context())
    derived = extractor.extract(
        _source_asset("mass-energy-formula.png", MediaType.IMAGE_PNG), _png_bytes()
    )

    assert len(derived) == 1
    asset = derived[0]
    assert asset.derivation_type == MediaAssetKind.FORMULA
    formula = asset.payload
    assert formula["latex"] == "E = mc^2"
    symbols = {s["symbol"]: s for s in formula["symbol_table"]}
    assert symbols["E"]["unit"] == "J"


def test_table_extraction_parses_markdown_table() -> None:
    gateway = _ocr_gateway(
        AdapterResult(
            actual_model_id="qwen-vl-ocr",
            output={
                "content": (
                    "| Temp (K) | Pressure (Pa) |\n"
                    "|---|---|\n"
                    "| 273.15 | 101325 |\n"
                    "| 373.15 | |"
                ),
                "task": "table_parsing",
            },
        )
    )
    extractor = QwenOcrExtractor(gateway, _context())
    derived = extractor.extract(
        _source_asset("data-table.png", MediaType.IMAGE_PNG), _png_bytes()
    )

    assert len(derived) == 1
    asset = derived[0]
    assert asset.derivation_type == MediaAssetKind.TABLE
    table = asset.payload
    columns = table["table_schema"]["columns"]
    assert columns[0]["name"] == "Temp (K)"
    assert columns[0]["unit"] == "K"
    rows = table["rows"]
    assert len(rows) == 2
    assert rows[1]["cells"][1]["is_missing"] is True


def test_extraction_failure_does_not_fabricate_data() -> None:
    from science_companion.ai.adapters import AdapterError

    gateway = _ocr_gateway(AdapterError(code="transient", message="down", retryable=True))
    extractor = QwenOcrExtractor(gateway, _context())

    from science_companion.media.extraction import ExtractionError

    with pytest.raises(ExtractionError):
        extractor.extract(_source_asset("graph.png", MediaType.IMAGE_PNG), _png_bytes())


def test_service_uses_model_backed_extractor_when_gateway_present() -> None:
    gateway = _ocr_gateway(
        AdapterResult(
            actual_model_id="qwen-vl-ocr",
            output={"content": "Service OCR result", "task": "advanced_recognition"},
        )
    )
    service = MediaIngestionService(model_gateway=gateway)
    from science_companion.contracts.media import MediaUploadRequest

    upload = MediaUploadRequest(
        filename="scan.png",
        media_type=MediaType.IMAGE_PNG,
        content=base64.b64encode(_png_bytes()).decode("ascii"),
        license_state=LicenseState.USER_OWNED,
    )
    run = service.ingest_upload("account-1", None, upload)
    assert run.status.name == "COMPLETED"
    assert run.asset_id is not None

    projection = service.get_asset("account-1", run.asset_id)
    assert projection.derived_assets[0].tool == "science_companion.ocr.qwen"
