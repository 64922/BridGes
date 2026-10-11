"""解析 OpenAlex 与 Crossref 响应字段。"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from hashlib import sha256
from html.parser import HTMLParser
from typing import Any


def _first_title(value: Any) -> str:
    if isinstance(value, list):
        return str(value[0] or "").strip() if value else ""
    return str(value or "").strip()


def _integer(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _date_or_year(value: Any, year: int | None) -> datetime:
    if value:
        try:
            return datetime.fromisoformat(str(value)).replace(tzinfo=UTC)
        except ValueError:
            pass
    return datetime(year or 1, 1, 1, tzinfo=UTC)


def _first_crossref_date_parts(item: dict[str, Any], key: str) -> list[Any]:
    date = item.get(key)
    date_parts = date.get("date-parts") if isinstance(date, dict) else None
    if not isinstance(date_parts, list) or not date_parts or not isinstance(date_parts[0], list):
        return []
    return date_parts[0]


def _date_from_crossref(item: dict[str, Any], year: int | None) -> datetime:
    for key in ("published", "published-print", "issued"):
        parts = _first_crossref_date_parts(item, key)
        if parts:
            year_part = _integer(parts[0])
            if year_part is None:
                continue
            month_part = _integer(parts[1]) if len(parts) > 1 else None
            day_part = _integer(parts[2]) if len(parts) > 2 else None
            if month_part is None and day_part is not None:
                continue
            month_part = 1 if month_part is None else month_part
            day_part = 1 if day_part is None else day_part
            try:
                return datetime(year_part, month_part, day_part, tzinfo=UTC)
            except ValueError:
                continue
    return datetime(year or 1, 1, 1, tzinfo=UTC)


def _crossref_year(item: dict[str, Any]) -> int | None:
    for key in ("published", "published-print", "issued"):
        parts = _first_crossref_date_parts(item, key)
        if parts:
            year = _integer(parts[0])
            if year is not None:
                return year
    return None


def _abstract_from_index(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    positioned: dict[int, str] = {}
    for word, positions in value.items():
        if isinstance(positions, list):
            for position in positions:
                try:
                    positioned[int(position)] = str(word)
                except (TypeError, ValueError):
                    continue
    return " ".join(positioned[index] for index in sorted(positioned))[:8000]


class _TextOnly(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data.strip())


def _strip_markup(value: str) -> str:
    parser = _TextOnly()
    try:
        parser.feed(value)
    except Exception:  # noqa: BLE001 - malformed JATS is still useful as plain text
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value)).strip()[:8000]
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()[:8000]


def _doi(value: Any) -> str | None:
    if not value:
        return None
    result = str(value).strip()
    return re.sub(r"^https?://(?:dx\.)?doi\.org/", "", result, flags=re.IGNORECASE) or None


def _arxiv_id(ids: dict[str, Any]) -> str | None:
    value = ids.get("arxiv")
    return str(value).rsplit("/", 1)[-1] if value else None


def _openalex_key(value: str) -> str:
    return value.rstrip("/").rsplit("/", 1)[-1]


def _canonical_openalex_id(value: str) -> str:
    return f"https://openalex.org/{_openalex_key(value).upper()}"


def _openalex_venue(item: dict[str, Any]) -> str | None:
    location = item.get("primary_location") or {}
    source = location.get("source") or {}
    return str(source.get("display_name") or "") or None


def _fingerprint(query: str) -> str:
    return sha256(" ".join(query.split()).encode("utf-8")).hexdigest()[:16]
