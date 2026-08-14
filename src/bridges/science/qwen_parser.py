"""Model-backed PDF parser for T061.

For text-based PDFs the parser behaves like ``MinimalPDFParser``. When the text
layer is empty and a ``ModelGateway`` is available, it falls back to the
``qwen_ocr`` capability so scanned documents are not silently skipped. Failures
are surfaced as parse errors so the original asset is preserved and the source
enters a blocked/quarantined state rather than receiving fabricated text.
"""

from __future__ import annotations

import base64

from bridges.ai.model_gateway import ModelGateway
from bridges.contracts.ai import ModelCallStatus
from bridges.contracts.science import (
    GateResult,
    InputQualityGate,
    LicenseState,
    ParseResult,
    SourceLicense,
)
from bridges.contracts.workflows import RunContextEnvelope
from bridges.science.parser import (
    MAX_CONTENT_BYTES,
    MAX_PDF_OBJECT_COUNT,
    MAX_PDF_STREAM_DEPTH,
    ParserError,
    ParserPort,
    _detect_language,
    _detect_text_injection_flags,
    _extract_pdf_text,
    _pdf_stream_depth,
    _split_text_into_chunks,
)


class QwenOcrPDFParser(ParserPort):
    """PDF parser that uses the Qwen OCR capability for scanned pages."""

    parser_id: str = "bridges.pdf.qwen_ocr"
    parser_version: str = "1"

    def __init__(
        self,
        gateway: ModelGateway,
        run_context: RunContextEnvelope,
    ) -> None:
        self._gateway = gateway
        self._run_context = run_context

    def parse(
        self,
        source_id: str,
        document_id: str,
        content: bytes,
    ) -> tuple[ParseResult, SourceLicense, dict[InputQualityGate, GateResult]]:
        gate_results: dict[InputQualityGate, GateResult] = {
            InputQualityGate.MIME_TYPE: GateResult.PASS,
            InputQualityGate.MAGIC_NUMBER: GateResult.PASS,
            InputQualityGate.SIZE_LIMIT: GateResult.PASS,
            InputQualityGate.DECOMPRESSION_BOMB: GateResult.PASS,
            InputQualityGate.MALICIOUS_CONTENT: GateResult.PASS,
            InputQualityGate.PARSE: GateResult.PASS,
        }

        if len(content) > MAX_CONTENT_BYTES:
            gate_results[InputQualityGate.SIZE_LIMIT] = GateResult.FAIL
            return ParseResult(), SourceLicense(), gate_results

        if not content.startswith(b"%PDF"):
            gate_results[InputQualityGate.MAGIC_NUMBER] = GateResult.FAIL
            return ParseResult(), SourceLicense(), gate_results

        object_count = content.count(b" obj")
        stream_count = content.count(b"stream")
        if object_count > MAX_PDF_OBJECT_COUNT or stream_count > MAX_PDF_OBJECT_COUNT:
            gate_results[InputQualityGate.DECOMPRESSION_BOMB] = GateResult.FAIL
            return ParseResult(), SourceLicense(), gate_results

        max_depth = _pdf_stream_depth(content)
        if max_depth > MAX_PDF_STREAM_DEPTH:
            gate_results[InputQualityGate.DECOMPRESSION_BOMB] = GateResult.FAIL
            return ParseResult(), SourceLicense(), gate_results

        text = _extract_pdf_text(content)
        if not text:
            text = self._ocr_pdf(content)

        injection_flags = _detect_text_injection_flags(text)
        if injection_flags:
            gate_results[InputQualityGate.MALICIOUS_CONTENT] = GateResult.FAIL

        chunks = _split_text_into_chunks(source_id, document_id, text, injection_flags)
        return (
            ParseResult(
                chunks=chunks,
                language=_detect_language(text),
                parse_warnings=[],
                injection_flags=injection_flags,
            ),
            SourceLicense(state=LicenseState.USER_OWNED),
            gate_results,
        )

    def _ocr_pdf(self, content: bytes) -> str:
        """Render PDF pages to images via PyMuPDF, then OCR each page.

        This approach is required because the OCR capability (Issue 09 起
        aligned to the fixed Qwen chat snapshot) accepts images (PNG/JPEG)
        through the Chat Completions ``image_url`` content type; it does not
        accept raw PDF bytes.  Each page is rendered at 300 DPI and sent as a
        separate OCR call; results are concatenated with page markers so the
        extraction layer can attribute text to the correct page.
        """
        try:
            import fitz  # type: ignore[import-untyped]  # PyMuPDF
        except ImportError as exc:
            raise ParserError("PyMuPDF (fitz) is required to OCR scanned PDFs.") from exc

        pages_text: list[str] = []
        doc = fitz.open(stream=content, filetype="pdf")

        if doc.page_count > 50:
            raise ParserError(f"Scanned PDF has {doc.page_count} pages; maximum is 50.")

        for page_num in range(doc.page_count):
            page = doc.load_page(page_num)
            pix = page.get_pixmap(dpi=300)
            img_bytes = pix.tobytes("png")
            image_base64 = base64.b64encode(img_bytes).decode("ascii")

            result = self._gateway.invoke(
                "qwen_ocr",
                "1",
                self._run_context,
                payload={
                    "image_base64": image_base64,
                    "mime_type": "image/png",
                    "prompt": (
                        f"Extract all readable text from page {page_num + 1} "
                        "of this scanned PDF. Preserve reading order. "
                        "Do not add commentary."
                    ),
                    "task": "document_parsing",
                    "temperature": 0.01,
                    "max_tokens": 4096,
                },
            )

            if result.status != ModelCallStatus.SUCCESS or result.output is None:
                message = (
                    result.error_message
                    or f"Qwen OCR failed on page {page_num + 1}."
                )
                raise ParserError(message)

            page_text = str(result.output.get("content", ""))
            pages_text.append(f"--- Page {page_num + 1} ---\n{page_text}")

        doc.close()
        return "\n\n".join(pages_text)
