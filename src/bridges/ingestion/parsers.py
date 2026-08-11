"""文档解析器：PDF / DOCX / TXT / Markdown / 常见图片 → 归一文本与结构跨度。

每个解析器产出同一坐标系（归一文本）上的结构跨度：每个跨度携带
原文中的起始/结束字符偏移、页码（可无）与章节标题（可无），下游哈希
分块据此保持可追溯。图片优先保留元数据（类型、尺寸、大小），OCR 文本
由上层能力注入；本模块只负责确定性归一，不发起外部调用。
"""

from __future__ import annotations

import io
import json
import re
import struct
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path

#: 单份 PDF 的最大页数（防御畸形文档）。
MAX_PDF_PAGES = 200
#: 单份文档解析出的最大文本长度（防御超长文档导致的分块失控）。
MAX_PARSE_TEXT_CHARS = 5_000_000

_DOCX_WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_HEADING_STYLE_RE = re.compile(r"^(heading\s*)?[1-6]$|^heading[1-6]$", re.IGNORECASE)
_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


class ParseError(Exception):
    """文档解析失败；message 为面向用户的中文原因。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class ParsedSpan:
    """归一文本上的一个结构跨度（原文范围）。"""

    start: int
    end: int
    page_number: int | None = None
    section_title: str | None = None


@dataclass(frozen=True)
class ParsedDocument:
    """一次解析的确定性产物：归一文本 + 结构跨度。

    可序列化到 ``parsed_json`` 供账户内解析缓存复用；反序列化后与
    重新解析得到完全相同的文本与跨度（分块坐标不变）。
    """

    title: str
    text: str
    spans: tuple[ParsedSpan, ...]
    page_count: int
    section_count: int
    parser_version: str

    def to_json(self) -> str:
        return json.dumps(
            {
                "title": self.title,
                "text": self.text,
                "spans": [
                    {
                        "start": span.start,
                        "end": span.end,
                        "page": span.page_number,
                        "section": span.section_title,
                    }
                    for span in self.spans
                ],
                "page_count": self.page_count,
                "section_count": self.section_count,
                "parser_version": self.parser_version,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, value: str) -> ParsedDocument:
        raw = json.loads(value)
        return cls(
            title=str(raw["title"]),
            text=str(raw["text"]),
            spans=tuple(
                ParsedSpan(
                    start=int(item["start"]),
                    end=int(item["end"]),
                    page_number=item.get("page"),
                    section_title=item.get("section"),
                )
                for item in raw["spans"]
            ),
            page_count=int(raw["page_count"]),
            section_count=int(raw["section_count"]),
            parser_version=str(raw["parser_version"]),
        )


#: 解析器版本：任一解析行为变化都递增对应版本，旧解析缓存自动失效。
PDF_PARSER_VERSION = "pdf-pymupdf-v1"
DOCX_PARSER_VERSION = "docx-xml-v1"
TEXT_PARSER_VERSION = "text-utf8-v1"
MARKDOWN_PARSER_VERSION = "markdown-v1"
IMAGE_PARSER_VERSION = "image-ocr-v1"
IMAGE_OCR_FALLBACK_MARKER = "图片内容未做文字识别"


def parse_document(
    content: bytes,
    filename: str,
    media_type: str,
    *,
    ocr_text: str | None = None,
) -> ParsedDocument:
    """按媒体类型分发解析；未知类型或解析失败抛中文 ParseError。"""
    if media_type == "application/pdf":
        return _parse_pdf(content, filename)
    if media_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        return _parse_docx(content, filename)
    if media_type == "text/plain":
        return _parse_text(content, filename, TEXT_PARSER_VERSION)
    if media_type == "text/markdown":
        return _parse_markdown(content, filename)
    if media_type.startswith("image/"):
        return _parse_image(content, filename, media_type, ocr_text)
    raise ParseError(
        f"不支持解析该文件类型（{media_type}），仅支持 PDF、DOCX、TXT、Markdown 与常见图片。"
    )


def _normalize_text(raw: str) -> str:
    """归一化换行与行尾空白；长度超限时截断并保留前缀（确定性）。"""
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    text = "\n".join(lines).strip("\n")
    if len(text) > MAX_PARSE_TEXT_CHARS:
        text = text[:MAX_PARSE_TEXT_CHARS]
    return text


def _title_from_text(text: str, fallback: str) -> str:
    """取首个非空行作为标题；无内容时回退文件名主名。"""
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped:
            return stripped[:120]
    return fallback


def _stem(filename: str) -> str:
    return Path(filename).stem[:120] or filename[:120]


def _append_span(
    spans: list[ParsedSpan], text: str, start: int, *, page: int | None, section: str | None
) -> int:
    """追加一个跨度并返回新文本末尾偏移（跳过空跨度）。"""
    end = len(text)
    if end > start:
        spans.append(ParsedSpan(start, end, page, section))
    return end


def _parse_pdf(content: bytes, filename: str) -> ParsedDocument:
    try:
        import fitz  # type: ignore[import-untyped]  # PyMuPDF（项目声明依赖）
    except ImportError as exc:
        raise ParseError("PDF 解析失败：缺少 PyMuPDF 库，请升级环境后重试。") from exc
    try:
        document = fitz.open(stream=content, filetype="pdf")
    except Exception as exc:  # noqa: BLE001 - PyMuPDF 对损坏文件抛多种异常
        raise ParseError("PDF 解析失败：文件已损坏或不是有效的 PDF。") from exc
    try:
        if document.page_count > MAX_PDF_PAGES:
            raise ParseError(
                f"PDF 解析失败：页数（{document.page_count}）超过 {MAX_PDF_PAGES} 页上限。"
            )
        parts: list[str] = []
        spans: list[ParsedSpan] = []
        start = 0
        for page_number in range(document.page_count):
            page = document.load_page(page_number)
            raw = page.get_text("text")
            page_text = _normalize_text(raw)
            if page_text:
                parts.append(page_text)
                start = _append_span(
                    spans, "\n\n".join(parts), start, page=page_number + 1, section=None
                )
        text = "\n\n".join(parts)
        if not text:
            # 无可提取文本（如扫描件）不是失败：由摄取服务标记 empty。
            return ParsedDocument(
                title=_stem(filename),
                text="",
                spans=(),
                page_count=document.page_count,
                section_count=0,
                parser_version=PDF_PARSER_VERSION,
            )
        return ParsedDocument(
            title=_title_from_text(text, _stem(filename)),
            text=text,
            spans=tuple(spans),
            page_count=document.page_count,
            section_count=0,
            parser_version=PDF_PARSER_VERSION,
        )
    finally:
        document.close()


def _parse_docx(content: bytes, filename: str) -> ParsedDocument:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = archive.namelist()
            if "word/document.xml" not in names:
                raise ParseError("DOCX 解析失败：压缩包中缺少 word/document.xml。")
            xml_bytes = archive.read("word/document.xml")
    except (zipfile.BadZipFile, OSError) as exc:
        raise ParseError("DOCX 解析失败：文件已损坏或不是有效的 DOCX。") from exc

    sections: list[tuple[str | None, list[str]]] = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise ParseError("DOCX 解析失败：正文 XML 结构损坏。") from exc

    current_section: str | None = None
    current_lines: list[str] = []
    for paragraph in root.iter(_DOCX_WORD_NS + "p"):
        ppr = paragraph.find(f"{_DOCX_WORD_NS}pPr")
        pstyle = ppr.find(f"{_DOCX_WORD_NS}pStyle") if ppr is not None else None
        style_val = pstyle.get(_DOCX_WORD_NS + "val") if pstyle is not None else None
        is_heading = bool(style_val) and bool(_HEADING_STYLE_RE.match(style_val or ""))
        text = "".join(
            node.text or ""
            for node in paragraph.iter(_DOCX_WORD_NS + "t")
        )
        normalized = text.strip()
        if is_heading and normalized:
            if current_lines:
                sections.append((current_section, current_lines))
            current_section = normalized[:120]
            current_lines = []
            continue
        if normalized:
            current_lines.append(normalized)
    if current_lines:
        sections.append((current_section, current_lines))

    if not sections:
        # 空文档不是失败：由摄取服务标记 empty（无可索引内容）。
        return ParsedDocument(
            title=_stem(filename),
            text="",
            spans=(),
            page_count=0,
            section_count=0,
            parser_version=DOCX_PARSER_VERSION,
        )
    parts: list[str] = []
    spans: list[ParsedSpan] = []
    section_count = 0
    start = 0
    for section_title, lines in sections:
        if section_title is not None:
            section_count += 1
        block = "\n".join(lines)
        parts.append(block)
        start = _append_span(spans, "\n\n".join(parts), start, page=None, section=section_title)
    text = "\n\n".join(parts)
    return ParsedDocument(
        title=(
            next((title for title, _ in sections if title is not None), None)
            or _title_from_text(text, _stem(filename))
        ),
        text=text,
        spans=tuple(spans),
        page_count=0,
        section_count=section_count,
        parser_version=DOCX_PARSER_VERSION,
    )


def _parse_text(content: bytes, filename: str, parser_version: str) -> ParsedDocument:
    try:
        text = _normalize_text(content.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ParseError("文本解析失败：内容不是有效的 UTF-8 文本。") from exc
    if not text:
        # 空内容不是失败：由摄取服务标记 empty（无可索引内容）。
        return ParsedDocument(
            title=_stem(filename),
            text="",
            spans=(),
            page_count=0,
            section_count=0,
            parser_version=parser_version,
        )
    spans = [ParsedSpan(0, len(text), None, None)]
    return ParsedDocument(
        title=_title_from_text(text, _stem(filename)),
        text=text,
        spans=tuple(spans),
        page_count=0,
        section_count=0,
        parser_version=parser_version,
    )


def _parse_markdown(content: bytes, filename: str) -> ParsedDocument:
    base = _parse_text(content, filename, MARKDOWN_PARSER_VERSION)
    # 按标题行把归一文本切分为带章节标题的跨度。
    spans: list[ParsedSpan] = []
    current_title: str | None = None
    current_start = 0
    in_fence = False
    for match in re.finditer(r"(?m)^([ \t]*)(#{1,6})\s+(.*)$|^(```+|~~~+)", base.text):
        if match.group(4):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        title = match.group(3).strip()[:120]
        end = match.start()
        if end > current_start:
            spans.append(ParsedSpan(current_start, end, None, current_title))
        current_title = title
        current_start = end
    if len(base.text) > current_start:
        spans.append(ParsedSpan(current_start, len(base.text), None, current_title))
    section_count = sum(1 for span in spans if span.section_title is not None)
    first_heading = next(
        (span.section_title for span in spans if span.section_title is not None), None
    )
    return ParsedDocument(
        title=first_heading or _title_from_text(base.text, _stem(filename)),
        text=base.text,
        spans=tuple(spans),
        page_count=0,
        section_count=section_count,
        parser_version=MARKDOWN_PARSER_VERSION,
    )


def _parse_image(
    content: bytes,
    filename: str,
    media_type: str,
    ocr_text: str | None = None,
) -> ParsedDocument:
    width, height = _image_dimensions(content)
    metadata = (
        f"图片：{filename}\n"
        f"类型：{media_type}\n"
        f"尺寸：{width}×{height} 像素\n"
        f"大小：{len(content)} 字节\n"
    )
    normalized_ocr = ocr_text.strip() if ocr_text and ocr_text.strip() else None
    if normalized_ocr is None:
        text = f"{metadata}{IMAGE_OCR_FALLBACK_MARKER}\n"
    else:
        text = f"{metadata}OCR 文本：\n{normalized_ocr}\n"
    return ParsedDocument(
        title=_stem(filename),
        text=text,
        spans=(ParsedSpan(0, len(text), None, None),),
        page_count=0,
        section_count=0,
        parser_version=IMAGE_PARSER_VERSION,
    )


def _image_dimensions(content: bytes) -> tuple[int | None, int | None]:
    """从常见图片头解析像素尺寸；未知格式返回 (None, None)。"""
    try:
        if content.startswith(b"\x89PNG\r\n\x1a\n") and len(content) >= 24:
            return struct.unpack(">II", content[16:24])
        if len(content) >= 10 and content[:6] in (b"GIF87a", b"GIF89a"):
            return struct.unpack("<HH", content[6:10])
        if len(content) >= 30 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
            return struct.unpack("<HH", content[26:30])
        if content.startswith(b"\xff\xd8\xff"):
            return _jpeg_dimensions(content)
    except (struct.error, ValueError, IndexError):
        return None, None
    return None, None


def _jpeg_dimensions(content: bytes) -> tuple[int | None, int | None]:
    """扫描 JPEG 段查找 SOF0/SOF1/SOF2 获得尺寸（无第三方依赖）。"""
    offset = 2
    length = len(content)
    while offset + 9 < length:
        if content[offset] != 0xFF:
            offset += 1
            continue
        marker = content[offset + 1]
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            offset += 2
            continue
        segment_length = struct.unpack(">H", content[offset + 2 : offset + 4])[0]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            height, width = struct.unpack(">HH", content[offset + 5 : offset + 9])
            return width, height
        offset += 2 + segment_length
    return None, None
