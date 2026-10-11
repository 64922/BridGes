"""将本会话上一轮选定论文转换为引用扩展的确定性起点。"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from bridges.paper.identity import normalize_doi
from bridges.paper.sources import PaperCandidate

_ORDINAL = re.compile(r"第\s*([一二两三四五六七八九十\d]+)\s*篇")
_FOLLOWUP = re.compile(r"后续|后继|引用|沿.{0,12}篇|follow.?up|citing", re.IGNORECASE)


@dataclass(frozen=True)
class PaperContinuation:
    """引用请求的解析结果；引用失败不能静默改成普通主题搜索。"""

    requested: bool = False
    seed: PaperCandidate | None = None
    error: str | None = None


def resolve_paper_continuation(
    messages: Sequence[Any], *, user_message_id: str, request: str,
) -> PaperContinuation:
    """消息必须由仓库按账户与会话隔离读取；只使用本轮之前的已核验投影。"""
    match = _ORDINAL.search(request)
    if match is None or not _FOLLOWUP.search(request):
        return PaperContinuation()
    order = _number(match.group(1))
    previous: list[Any] = []
    for message in messages:
        if message.message_id == user_message_id:
            break
        previous.append(message)
    for message in reversed(previous):
        projection = getattr(message, "paper_search", None)
        if not isinstance(projection, dict) or projection.get("status") != "success":
            continue
        selected = [item for item in projection.get("selected", ()) if isinstance(item, dict)]
        identity = next((item for item in selected if item.get("order") == order), None)
        if identity is None:
            return PaperContinuation(True, error="上轮结果没有你引用的论文序号，请确认目标论文。")
        rows = [*projection.get("candidates", ()), *projection.get("papers", ())]
        row = next(
            (item for item in rows if isinstance(item, dict) and _same_identity(identity, item)),
            None,
        )
        if row is None:
            return PaperContinuation(
                True, error="无法核验上轮目标论文的来源记录，请重试或提供论文链接。",
            )
        openalex_id = str(row.get("openalex_id") or "")
        if not openalex_id:
            openalex_id = next(
                (str(value).split(":", 1)[1] for value in row.get("source_ids", ())
                 if str(value).startswith("openalex:")),
                "",
            )
        return PaperContinuation(True, seed=PaperCandidate(
            arxiv_id=row.get("arxiv_id"), doi=row.get("doi"),
            title=str(row.get("title") or ""),
            authors=[str(value) for value in row.get("authors", ())],
            published_at=datetime(int(row.get("published_year") or 1), 1, 1, tzinfo=UTC),
            abs_url=str(row.get("abs_url") or ""), pdf_url=str(row.get("pdf_url") or ""),
            abstract=str(row.get("abstract") or row.get("source_abstract") or ""),
            source=str(row.get("primary_source") or row.get("source") or "openalex"),
            sources=tuple(str(value) for value in row.get("sources", ())),
            source_ids=tuple(str(value) for value in row.get("source_ids", ())),
            openalex_id=openalex_id or None,
        ))
    return PaperContinuation(True, error="本会话还没有可引用的论文结果，请先检索或提供目标论文。")


def _same_identity(identity: dict[str, Any], row: dict[str, Any]) -> bool:
    if identity.get("candidate_id") and identity["candidate_id"] == row.get("candidate_id"):
        return True
    doi = normalize_doi(identity.get("doi"))
    if doi and doi == normalize_doi(row.get("doi")):
        return True
    arxiv = identity.get("arxiv_id")
    return bool(arxiv and arxiv == row.get("arxiv_id"))


def _number(value: str) -> int:
    if value.isdigit():
        return int(value)
    digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}
    if "十" in value:
        tens, _, units = value.partition("十")
        return digits.get(tens, 1) * 10 + digits.get(units, 0)
    return digits.get(value, 0)
