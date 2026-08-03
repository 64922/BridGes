"""结构锚点的哈希分块（Issue 17 索引合同的分块器）。

分块器在解析器产出的归一文本上按结构跨度滑动：段落优先作为分块边界，
目标块长约 ``TARGET_CHUNK_CHARS``，超长段落按 ``MAX_CHUNK_CHARS`` 硬切。
每个分块携带其在原文中的字符偏移、页码与章节标题，内容哈希为确定性
SHA-256——同一解析文本在任何机器上产生完全相同的分块序列。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from bridges.ingestion.parsers import ParsedDocument, ParsedSpan

#: 分块器版本：分块策略变化时递增，索引合同随之变化并触发全量重建。
CHUNKER_VERSION = "hash-chunker-v1"
#: 目标块长（字符）。
TARGET_CHUNK_CHARS = 900
#: 单块硬上限（字符）：超长段落在此处硬切。
MAX_CHUNK_CHARS = 2000


@dataclass(frozen=True)
class TextChunk:
    """一个哈希分块：可追溯到原文范围（跨度 + 偏移）。"""

    content: str
    chunk_index: int
    section_title: str | None
    page_number: int | None
    start_offset: int
    end_offset: int
    content_hash: str


def chunk_document(document: ParsedDocument) -> list[TextChunk]:
    """把解析文档切成确定性分块序列（空文本产出空列表）。"""
    chunks: list[TextChunk] = []
    index = 0
    for span in document.spans:
        if span.end <= span.start:
            continue
        text = document.text[span.start : span.end]
        index = _chunk_span(text, span, index, chunks)
    return chunks


#: 段落：连续非空行（空行是段落分隔符，不进入匹配）。
_PARAGRAPH_RE = re.compile(r"[^\n]+(?:\n[^\n]+)*")


def _chunk_span(
    text: str, span: ParsedSpan, start_index: int, chunks: list[TextChunk]
) -> int:
    index = start_index
    buffer: list[str] = []
    buffer_size = 0
    buffer_start = 0  # 缓冲区首段在 span 文本内的起点
    buffer_end = 0  # 缓冲区末段在 span 文本内的终点

    def flush() -> None:
        """把缓冲区中积累的段落合并为一个分块。

        内容直接取原文切片（含段落间分隔符），保证内容长度与偏移跨度
        严格一致，避免重新拼接造成漂移。
        """
        nonlocal buffer, buffer_size, index
        if not buffer:
            return
        content = text[buffer_start:buffer_end]
        chunks.append(
            _make_chunk(
                content,
                index,
                span,
                offset_start=span.start + buffer_start,
                offset_end=span.start + buffer_end,
            )
        )
        index += 1
        buffer = []
        buffer_size = 0

    for match in _PARAGRAPH_RE.finditer(text):
        paragraph = match.group()
        paragraph_start, paragraph_end = match.start(), match.end()
        if len(paragraph) > MAX_CHUNK_CHARS:
            # 超长段落：先冲刷缓冲区，再把段落硬切成固定长度分块。
            flush()
            for start in range(0, len(paragraph), MAX_CHUNK_CHARS):
                piece = paragraph[start : start + MAX_CHUNK_CHARS]
                chunks.append(
                    _make_chunk(
                        piece,
                        index,
                        span,
                        offset_start=span.start + paragraph_start + start,
                        offset_end=span.start + paragraph_start + start + len(piece),
                    )
                )
                index += 1
            continue
        if not buffer:
            buffer_start = paragraph_start
        if buffer and buffer_size + len(paragraph) + 1 > TARGET_CHUNK_CHARS:
            flush()
            buffer_start = paragraph_start
        buffer.append(paragraph)
        buffer_size += len(paragraph) + 1
        buffer_end = paragraph_end
    flush()
    return index


def _make_chunk(
    content: str,
    chunk_index: int,
    span: ParsedSpan,
    *,
    offset_start: int,
    offset_end: int,
) -> TextChunk:
    return TextChunk(
        content=content,
        chunk_index=chunk_index,
        section_title=span.section_title,
        page_number=span.page_number,
        start_offset=offset_start,
        end_offset=offset_end,
        content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
    )
