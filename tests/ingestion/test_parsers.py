"""Issue 17：解析器与哈希分块的确定性、结构提取与失败原因。"""

from __future__ import annotations

import struct
import zipfile
from io import BytesIO

import pytest

from bridges.ingestion.chunker import chunk_document
from bridges.ingestion.parsers import (
    DOCX_PARSER_VERSION,
    IMAGE_PARSER_VERSION,
    MARKDOWN_PARSER_VERSION,
    PDF_PARSER_VERSION,
    TEXT_PARSER_VERSION,
    ParseError,
    parse_document,
)

MD_TEXT = "# 标题一\n\n第一段内容。\n\n## 小节\n\n第二段内容。\n"


def _pdf_bytes(pages: list[str]) -> bytes:
    import fitz  # type: ignore[import-untyped]  # PyMuPDF（项目声明依赖）

    document = fitz.open()
    for text in pages:
        page = document.new_page()
        page.insert_text((72, 72), text, fontsize=12)
    data = document.tobytes()
    document.close()
    return data


def _docx_bytes(paragraphs: list[tuple[str, str]]) -> bytes:
    """构造 DOCX：段落列表 [(pStyle, 文本)]。"""
    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body: list[str] = []
    for style, text in paragraphs:
        ppr = (
            f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        )
        body.append(f"<w:p>{ppr}<w:r><w:t>{text}</w:t></w:r></w:p>")
    document_xml = (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<w:document xmlns:w="{ns}"><w:body>{"".join(body)}</w:body></w:document>'
    )
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", document_xml)
    return buffer.getvalue()


# ----------------------------------------------------------------------
# Markdown / TXT
# ----------------------------------------------------------------------


def test_markdown_parses_heading_sections_and_title() -> None:
    parsed = parse_document(MD_TEXT.encode("utf-8"), "test.md", "text/markdown")
    assert parsed.title == "标题一"
    assert parsed.parser_version == MARKDOWN_PARSER_VERSION
    assert parsed.section_count == 2
    titles = [span.section_title for span in parsed.spans]
    assert titles == ["标题一", "小节"]
    # 跨度覆盖全部文本且互不重叠
    assert parsed.spans[0].start == 0
    assert parsed.spans[-1].end == len(parsed.text)


def test_chunks_traceable_to_source_range() -> None:
    parsed = parse_document(MD_TEXT.encode("utf-8"), "test.md", "text/markdown")
    chunks = chunk_document(parsed)
    assert len(chunks) >= 2
    for chunk in chunks:
        # 偏移跨度与内容长度严格一致，且落在归一文本范围内
        assert chunk.end_offset - chunk.start_offset == len(chunk.content)
        assert 0 <= chunk.start_offset < chunk.end_offset <= len(parsed.text)
        assert parsed.text[chunk.start_offset : chunk.end_offset] == chunk.content
    assert chunks[0].section_title == "标题一"
    assert chunks[1].section_title == "小节"


def test_chunking_is_deterministic() -> None:
    parsed = parse_document(MD_TEXT.encode("utf-8"), "test.md", "text/markdown")
    first = chunk_document(parsed)
    second = chunk_document(parsed)
    assert [(c.content, c.start_offset, c.end_offset, c.content_hash) for c in first] == [
        (c.content, c.start_offset, c.end_offset, c.content_hash) for c in second
    ]


def test_plain_text_parses_without_sections() -> None:
    parsed = parse_document(
        "你好世界，这是一个文本文件。\n\n第二段内容。".encode(),
        "notes.txt",
        "text/plain",
    )
    assert parsed.title == "你好世界，这是一个文本文件。"
    assert parsed.parser_version == TEXT_PARSER_VERSION
    assert parsed.section_count == 0
    chunks = chunk_document(parsed)
    assert len(chunks) == 1
    assert chunks[0].start_offset == 0
    assert chunks[0].end_offset == len(parsed.text)


def test_long_paragraph_hard_cut_keeps_offsets() -> None:
    long_text = ("超长段落内容。" * 600).encode("utf-8")
    parsed = parse_document(long_text, "long.txt", "text/plain")
    chunks = chunk_document(parsed)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk.content) <= 2000
        assert chunk.end_offset - chunk.start_offset == len(chunk.content)


def test_parse_cache_roundtrip_preserves_structure() -> None:
    parsed = parse_document(MD_TEXT.encode("utf-8"), "test.md", "text/markdown")
    restored = type(parsed).from_json(parsed.to_json())
    assert restored.text == parsed.text
    assert restored.title == parsed.title
    assert restored.spans == parsed.spans
    assert restored.parser_version == parsed.parser_version
    assert chunk_document(restored) == chunk_document(parsed)


# ----------------------------------------------------------------------
# PDF / DOCX
# ----------------------------------------------------------------------


def test_pdf_parses_pages_and_title() -> None:
    # PyMuPDF 内置 Helvetica 字体不渲染 CJK，测试用 ASCII 正文验证页码结构。
    content = _pdf_bytes(["First page content", "Second page content"])
    parsed = parse_document(content, "paper.pdf", "application/pdf")
    assert parsed.parser_version == PDF_PARSER_VERSION
    assert parsed.page_count == 2
    page_numbers = [span.page_number for span in parsed.spans]
    assert page_numbers == [1, 2]
    assert "First page content" in parsed.text
    assert "Second page content" in parsed.text
    for chunk in chunk_document(parsed):
        assert chunk.end_offset - chunk.start_offset == len(chunk.content)


def test_pdf_corrupt_raises_chinese_error() -> None:
    with pytest.raises(ParseError) as excinfo:
        parse_document(b"not a pdf at all", "bad.pdf", "application/pdf")
    assert "PDF 解析失败" in str(excinfo.value)


def test_docx_parses_headings_and_text() -> None:
    content = _docx_bytes(
        [
            ("Heading1", "第一章 引言"),
            ("Normal", "正文段落一"),
            ("Heading2", "第一节 背景"),
            ("Normal", "正文段落二"),
            ("", "无样式段落"),
        ]
    )
    parsed = parse_document(
        content,
        "报告.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert parsed.parser_version == DOCX_PARSER_VERSION
    assert parsed.title == "第一章 引言"
    assert parsed.section_count == 2
    titles = [span.section_title for span in parsed.spans]
    assert titles == ["第一章 引言", "第一节 背景"]
    assert "正文段落一" in parsed.text
    assert "正文段落二" in parsed.text
    for chunk in chunk_document(parsed):
        assert chunk.end_offset - chunk.start_offset == len(chunk.content)


def test_docx_missing_document_xml_raises_chinese_error() -> None:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("other/entry.xml", "<x/>")
    with pytest.raises(ParseError) as excinfo:
        parse_document(
            buffer.getvalue(),
            "bad.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    assert "DOCX 解析失败" in str(excinfo.value)


# ----------------------------------------------------------------------
# 图片
# ----------------------------------------------------------------------


def test_image_parses_metadata_and_marks_missing_ocr() -> None:
    png = (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\x0dIHDR"
        + struct.pack(">II", 800, 600)
        + b"\x08\x06\x00\x00\x00"
        + b"\x00" * 40
    )
    parsed = parse_document(png, "图表.png", "image/png")
    assert IMAGE_PARSER_VERSION == "image-ocr-v2"
    assert parsed.parser_version == IMAGE_PARSER_VERSION
    assert parsed.title == "图表"
    assert "800×600 像素" in parsed.text
    assert "image/png" in parsed.text
    assert "图片内容未做文字识别" in parsed.text
    chunks = chunk_document(parsed)
    assert len(chunks) == 1
    # 尾随换行不属于段落内容；分块内容与原文切片严格一致
    assert chunks[0].end_offset == len(parsed.text.rstrip("\n"))
    assert parsed.text[chunks[0].start_offset : chunks[0].end_offset] == chunks[0].content


def test_image_parses_ocr_text_after_metadata() -> None:
    png = (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\x0dIHDR"
        + struct.pack(">II", 800, 600)
        + b"\x08\x06\x00\x00\x00"
        + b"\x00" * 40
    )
    parsed = parse_document(
        png,
        "图表.png",
        "image/png",
        ocr_text="牛顿第二定律 F=ma",
    )

    assert "牛顿第二定律 F=ma" in parsed.text
    assert "图片：图表.png" in parsed.text
    assert parsed.text.index("图片：图表.png") < parsed.text.index("牛顿第二定律 F=ma")
    assert "图片内容未做文字识别" not in parsed.text


def test_unsupported_type_raises_chinese_error() -> None:
    with pytest.raises(ParseError) as excinfo:
        parse_document(b"{}", "data.csv", "text/csv")
    assert "不支持解析" in str(excinfo.value)
