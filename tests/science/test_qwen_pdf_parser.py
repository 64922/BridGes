"""Tests for the model-backed PDF OCR parser (T061).

These tests use a programmable gateway adapter so they do not call the real Qwen
API. They verify that text-based PDFs still parse and that scanned PDFs fall
back to OCR, recording the model run context and failing closed on OCR errors.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from science_companion.ai import CapabilityRegistry, ModelGateway
from science_companion.ai.adapters import AdapterError, AdapterResult
from science_companion.contracts.ai import (
    CapabilityKind,
    CapabilityRecord,
    RetryPolicy,
)
from science_companion.contracts.science import GateResult, InputQualityGate
from science_companion.contracts.workflows import RunContextEnvelope
from science_companion.science.parser import ParserError
from science_companion.science.qwen_parser import QwenOcrPDFParser


def _context() -> RunContextEnvelope:
    return RunContextEnvelope(
        run_id="run-1",
        account_id="account-1",
        project_id="project-1",
        workflow_name="wf",
        workflow_version="1",
        submitted_at=datetime.now(UTC),
    )


class _ProgrammableOcrAdapter:
    def __init__(self, responses: list[AdapterResult | AdapterError]) -> None:
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


def _ocr_gateway(result: AdapterResult | AdapterError) -> ModelGateway:
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


def _text_pdf_bytes(text: str) -> bytes:
    return (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]"
        b" /Contents 4 0 R >>\nendobj\n"
        b"4 0 obj\n<< /Length "
        + str(len(text) + 10).encode()
        + b" >>\nstream\nBT /F1 12 Tf 100 700 Td ("
        + text.encode("utf-8")
        + b") Tj ET\nendstream\nendobj\n"
        b"xref\n0 5\n0000000000 65535 f \n"
        b"trailer\n<< /Size 5 /Root 1 0 R >>\n"
        b"startxref\n0\n%%EOF\n"
    )


def _scanned_pdf_bytes() -> bytes:
    """A minimal PDF with a blank page — no extractable text layer.

    PyMuPDF renders it as an empty white page, which triggers the OCR
    fallback path (text layer is empty so ``_ocr_pdf`` is called).
    """
    import fitz

    doc = fitz.open()
    doc.new_page(width=612, height=792)
    pdf_bytes = doc.write()
    doc.close()
    return pdf_bytes


def test_text_pdf_still_parses_without_ocr() -> None:
    gateway = _ocr_gateway(AdapterResult(actual_model_id="qwen-vl-ocr", output={}))
    parser = QwenOcrPDFParser(gateway, _context())
    parse_result, _rights, gates = parser.parse("src-1", "doc-1", _text_pdf_bytes("Hello PDF"))

    assert gates[InputQualityGate.PARSE] == GateResult.PASS
    assert any(chunk.text == "Hello PDF" for chunk in parse_result.chunks)


def test_scanned_pdf_falls_back_to_ocr() -> None:
    gateway = _ocr_gateway(
        AdapterResult(
            actual_model_id="qwen-vl-ocr",
            output={"content": "Scanned page text."},
        )
    )
    parser = QwenOcrPDFParser(gateway, _context())
    parse_result, _rights, gates = parser.parse("src-1", "doc-1", _scanned_pdf_bytes())

    assert gates[InputQualityGate.PARSE] == GateResult.PASS
    assert any("Scanned page text." in chunk.text for chunk in parse_result.chunks)


def test_ocr_failure_raises_parser_error() -> None:
    gateway = _ocr_gateway(AdapterError(code="transient", message="down", retryable=True))
    parser = QwenOcrPDFParser(gateway, _context())

    with pytest.raises(ParserError):
        parser.parse("src-1", "doc-1", _scanned_pdf_bytes())
