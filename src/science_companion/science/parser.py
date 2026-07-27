"""Simple text and PDF parsers for source ingestion.

These parsers are intentionally minimal: they prove the source-versioning seam
without requiring heavy external dependencies. Production may later swap them
for LlamaIndex, OCR or sandbox-based parsers behind the same `ParserPort`.
"""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
from datetime import datetime, timezone
from typing import Any, Protocol

from science_companion.contracts.science import (
    ChunkStructurePath,
    ChunkVersion,
    GateResult,
    InputQualityGate,
    LicenseState,
    MediaType,
    ParseResult,
    SourceLicense,
)


# Maximum source content size (10 MiB) before gating.
MAX_CONTENT_BYTES = 10 * 1024 * 1024
# Maximum number of PDF objects / stream pairs to avoid file bombs.
MAX_PDF_OBJECT_COUNT = 100_000
# Maximum recursion / stream nesting depth to avoid bombs.
MAX_PDF_STREAM_DEPTH = 100


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _chunk_id() -> str:
    return secrets.token_urlsafe(16)


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ParserError(Exception):
    """Domain exception for parser failures."""


class ParserPort(Protocol):
    """Abstract port for source parsers."""

    def parse(
        self,
        source_id: str,
        document_id: str,
        content: bytes,
    ) -> tuple[ParseResult, SourceLicense, dict[InputQualityGate, GateResult]]:
        """Parse content into chunks and return license snapshot plus gate results."""
        ...


class TextParser(ParserPort):
    """Parser for plain text scientific sources."""

    parser_id: str = "science_companion.text"
    parser_version: str = "1"

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
        }

        if len(content) > MAX_CONTENT_BYTES:
            gate_results[InputQualityGate.SIZE_LIMIT] = GateResult.FAIL
            return ParseResult(), SourceLicense(), gate_results

        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            gate_results[InputQualityGate.PARSE] = GateResult.FAIL
            return ParseResult(parse_warnings=[str(exc)]), SourceLicense(), gate_results

        injection_flags = _detect_text_injection_flags(text)
        if injection_flags:
            gate_results[InputQualityGate.MALICIOUS_CONTENT] = GateResult.FAIL

        chunks = _split_text_into_chunks(source_id, document_id, text, injection_flags)
        return (
            ParseResult(
                chunks=chunks,
                language=_detect_language(text),
                injection_flags=injection_flags,
            ),
            SourceLicense(state=LicenseState.USER_OWNED),
            gate_results,
        )


class MinimalPDFParser(ParserPort):
    """Minimal PDF parser for text-layer extraction.

    Handles simple text-based PDFs by scanning content streams. It is not a full
    PDF renderer and does not replace OCR for scanned documents.
    """

    parser_id: str = "science_companion.pdf.minimal"
    parser_version: str = "1"

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
        }

        if len(content) > MAX_CONTENT_BYTES:
            gate_results[InputQualityGate.SIZE_LIMIT] = GateResult.FAIL
            return ParseResult(), SourceLicense(), gate_results

        if not content.startswith(b"%PDF"):
            gate_results[InputQualityGate.MAGIC_NUMBER] = GateResult.FAIL
            return ParseResult(), SourceLicense(), gate_results

        # Bomb detection: count xref/objects/streams and measure stream depth.
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
        injection_flags = _detect_text_injection_flags(text)
        if injection_flags:
            gate_results[InputQualityGate.MALICIOUS_CONTENT] = GateResult.FAIL

        chunks = _split_text_into_chunks(source_id, document_id, text, injection_flags)
        return (
            ParseResult(
                chunks=chunks,
                language=_detect_language(text),
                parse_warnings=[
                    "minimal_pdf_parser: only text layer extracted; "
                    "scanned pages require OCR pipeline"
                ]
                if not text
                else [],
                injection_flags=injection_flags,
            ),
            SourceLicense(state=LicenseState.USER_OWNED),
            gate_results,
        )


def _detect_text_injection_flags(text: str) -> list[str]:
    """Detect patterns in text that should not enter trusted evidence as instructions.

    These are heuristics, not complete defenses. They flag content that should be
    quarantined for human review rather than silently trusted.
    """
    flags: list[str] = []
    # Null bytes and unusual control characters in the middle of text.
    if "\x00" in text:
        flags.append("null_byte")
    # Bidirectional override characters used in spoofing.
    if any(c in text for c in "\u202A\u202B\u202C\u202D\u202E\u2066\u2067\u2068\u2069"):
        flags.append("bidi_override")
    # Suspicious instruction-like prefixes common in prompt injection tests.
    lowered = text.lower()
    suspicious = [
        "ignore previous instructions",
        "ignore all prior",
        "system prompt",
        "you are a",
        "disregard",
        "new instructions:",
    ]
    for phrase in suspicious:
        if phrase in lowered:
            flags.append(f"suspicious_phrase:{phrase.replace(' ', '_')}")
            break
    return flags


def _pdf_stream_depth(content: bytes) -> int:
    """Estimate maximum stream nesting depth in a PDF."""
    depth = 0
    max_depth = 0
    i = 0
    length = len(content)
    while i < length - 6:
        if content.startswith(b"stream", i):
            depth += 1
            max_depth = max(max_depth, depth)
            i += 6
        elif content.startswith(b"endstream", i):
            depth = max(0, depth - 1)
            i += 9
        else:
            i += 1
    return max_depth


def _extract_pdf_text(content: bytes) -> str:
    """Extract text strings from PDF content streams.

    Looks for the most common text-showing operators:
    - `(text) Tj`
    - `[(text)] TJ`
    - `<hex> Tj`
    """
    text_parts: list[str] = []
    # Plain strings: ( ... ) Tj
    for match in re.finditer(rb"\((.*?)\)\s*Tj", content, re.DOTALL):
        raw = match.group(1)
        text_parts.append(_decode_pdf_string(raw))
    # Array strings: [( ... )(...)] TJ
    for match in re.finditer(rb"\[((?:\([^)]*\)|<[^>]*>)+)\]\s*TJ", content, re.DOTALL):
        array_content = match.group(1)
        for sub in re.finditer(rb"\((.*?)\)", array_content, re.DOTALL):
            text_parts.append(_decode_pdf_string(sub.group(1)))
        for sub in re.finditer(rb"<([0-9A-Fa-f]+)>", array_content):
            text_parts.append(_decode_pdf_hex_string(sub.group(1)))
    # Hex strings: <hex> Tj
    for match in re.finditer(rb"<([0-9A-Fa-f]+)>\s*Tj", content):
        text_parts.append(_decode_pdf_hex_string(match.group(1)))
    return " ".join(text_parts)


def _decode_pdf_string(raw: bytes) -> str:
    """Decode a PDF literal string, handling common escapes."""
    result: list[str] = []
    i = 0
    while i < len(raw):
        byte = raw[i]
        if byte == ord("\\") and i + 1 < len(raw):
            next_byte = raw[i + 1]
            escapes = {
                ord("n"): "\n",
                ord("r"): "\r",
                ord("t"): "\t",
                ord("b"): "\b",
                ord("f"): "\f",
                ord("("): "(",
                ord(")"): ")",
                ord("\\"): "\\",
            }
            if next_byte in escapes:
                result.append(escapes[next_byte])
                i += 2
                continue
            # Octal escape
            octal_match = re.match(rb"[0-7]{1,3}", raw[i + 1 :])
            if octal_match:
                value = int(octal_match.group(), 8)
                result.append(chr(value))
                i += 1 + len(octal_match.group())
                continue
            result.append(chr(next_byte))
            i += 2
            continue
        try:
            result.append(chr(byte))
        except ValueError:
            result.append(" ")
        i += 1
    return "".join(result)


def _decode_pdf_hex_string(raw: bytes) -> str:
    """Decode a PDF hex string."""
    try:
        hex_str = raw.decode("ascii")
        if len(hex_str) % 2 == 1:
            hex_str += "0"
        decoded = bytes.fromhex(hex_str)
        # Try UTF-16BE if BOM present, otherwise UTF-8 fallback.
        if decoded.startswith(b"\xfe\xff"):
            return decoded.decode("utf-16-be", errors="replace")
        return decoded.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return ""


def _split_text_into_chunks(
    source_id: str, document_id: str, text: str,
    injection_flags: list[str] | None = None,
) -> list[ChunkVersion]:
    """Split text into paragraph/header chunks preserving structure."""
    chunks: list[ChunkVersion] = []
    offset = 0
    flags = injection_flags or []
    # Split by blank lines only — do NOT consume header markers.
    paragraphs = re.split(r"\n\s*\n", text)
    previous_chunk_id: str | None = None
    header_pattern = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)

    for index, raw in enumerate(paragraphs):
        stripped = raw.strip()
        if not stripped:
            offset += len(raw) + 1
            continue

        # Detect markdown headers for structure tracking.
        structure = ChunkStructurePath()
        header_match = header_pattern.match(stripped)
        if header_match:
            level = len(header_match.group(1))
            title = header_match.group(2).strip()
            if level == 1:
                structure.section = title
            elif level == 2:
                structure.subsection = title
            else:
                structure.section = title
        else:
            structure.paragraph = index + 1

        chunk_id = _chunk_id()
        chunk = ChunkVersion(
            chunk_id=chunk_id,
            document_id=document_id,
            source_id=source_id,
            previous_chunk_id=previous_chunk_id,
            structure_path=structure,
            start_offset=offset,
            end_offset=offset + len(stripped),
            text=stripped,
            text_hash=_text_hash(stripped),
            parse_confidence=1.0,
            injection_flags=flags,
            created_at=_now(),
        )
        # Link previous chunk forward.
        if chunks:
            previous = chunks[-1]
            previous.next_chunk_id = chunk_id
            chunks[-1] = previous
        chunks.append(chunk)
        previous_chunk_id = chunk_id
        offset += len(raw) + 1

    return chunks


def _detect_language(text: str) -> str | None:
    """Naive language detection; returns 'zh' for CJK text, 'en' otherwise."""
    if any("\u4e00" <= c <= "\u9fff" for c in text):
        return "zh"
    if text.strip():
        return "en"
    return None


_PARSER_MAP: dict[MediaType, tuple[ParserPort, str, str]] = {
    MediaType.TEXT_PLAIN: (TextParser(), TextParser.parser_id, TextParser.parser_version),
    MediaType.APPLICATION_PDF: (
        MinimalPDFParser(),
        MinimalPDFParser.parser_id,
        MinimalPDFParser.parser_version,
    ),
}


def select_parser(media_type: MediaType) -> ParserPort:
    entry = _PARSER_MAP.get(media_type)
    if entry is None:
        raise ParserError(f"不支持的媒体类型：{media_type}")
    return entry[0]


def parser_id_for(media_type: MediaType) -> str:
    entry = _PARSER_MAP.get(media_type)
    if entry is None:
        raise ParserError(f"不支持的媒体类型：{media_type}")
    return entry[1]


def parser_version_for(media_type: MediaType) -> str:
    entry = _PARSER_MAP.get(media_type)
    if entry is None:
        raise ParserError(f"不支持的媒体类型：{media_type}")
    return entry[2]
