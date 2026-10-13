"""贴吧总结的证据转换、模型载荷和结果解码边界。"""

from __future__ import annotations

from typing import Any, cast

from bridges.contracts.ai import ModelCallResult
from bridges.tieba.contracts import TiebaPostProjection, TiebaSummaryClaim, TiebaSummaryClaimKind


def sources(posts: list[TiebaPostProjection]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    duplicate_groups: dict[tuple[str, str], str] = {}
    duplicate_counts: dict[str, int] = {}
    for post in posts:
        for reply in post.replies:
            content = reply.content.strip()
            if not content:
                continue
            if reply.reply_id:
                key = (post.thread_id or post.url, reply.reply_id)
                if key in seen:
                    continue
                seen.add(key)
            source: dict[str, Any] = {
                "id": f"E{len(values) + 1}",
                "thread_id": post.thread_id,
                "url": post.url,
                "title": post.title,
                "reply_id": reply.reply_id,
                "parent_reply_id": reply.parent_reply_id,
                "floor": reply.floor,
                "posted_at": reply.posted_at,
                "temporal_scope": reply.temporal_scope.value,
                "identity_state": "upstream_id" if reply.reply_id else "unknown",
                "content": content[:800],
            }
            if not reply.reply_id:
                normalized = " ".join(content.casefold().split())
                group_key = (post.thread_id or post.url, normalized)
                group = duplicate_groups.setdefault(
                    group_key, f"D{len(duplicate_groups) + 1}"
                )
                source["duplicate_text_group"] = group
                duplicate_counts[group] = duplicate_counts.get(group, 0) + 1
            values.append(source)
    repeated_groups = {
        group for group, count in duplicate_counts.items() if count > 1
    }
    for item in values:
        duplicate_group = item.get("duplicate_text_group")
        if not isinstance(duplicate_group, str) or duplicate_group not in repeated_groups:
            item.pop("duplicate_text_group", None)
        else:
            item["duplicate_text_count"] = duplicate_counts[duplicate_group]
    priority = {"recent": 0, "unknown": 1, "historical": 2}
    values.sort(key=lambda item: priority.get(str(item.get("temporal_scope")), 1))
    for index, item in enumerate(values, start=1):
        item["id"] = f"E{index}"
    return values


def summary_payload(topic: str, sources: list[dict[str, Any]]) -> dict[str, Any]:
    return {"topic": topic, "evidence": sources}


def build_verification_payload(
    claims: list[TiebaSummaryClaim],
    sources: dict[str, dict[str, Any]],
    *,
    summary_text: str,
    summary_citation_ids: list[str],
) -> dict[str, Any]:
    items = [
        {
            "index": index,
            "text": claim.text,
            "evidence": [
                {
                    "id": citation_id,
                    "content": sources[citation_id]["content"][:400],
                    "time": sources[citation_id].get("posted_at"),
                    "scope": sources[citation_id].get("temporal_scope"),
                    "identity_state": sources[citation_id].get("identity_state"),
                    "duplicate_text_group": sources[citation_id].get(
                        "duplicate_text_group"
                    ),
                    "duplicate_text_count": sources[citation_id].get(
                        "duplicate_text_count"
                    ),
                }
                for citation_id in claim.citation_ids
            ],
        }
        for index, claim in enumerate(claims)
    ]
    items.append(
        {
            "index": len(claims),
            "text": summary_text,
            "evidence": [
                {
                    "id": citation_id,
                    "content": sources[citation_id]["content"][:400],
                    "time": sources[citation_id].get("posted_at"),
                    "scope": sources[citation_id].get("temporal_scope"),
                    "identity_state": sources[citation_id].get("identity_state"),
                    "duplicate_text_group": sources[citation_id].get(
                        "duplicate_text_group"
                    ),
                    "duplicate_text_count": sources[citation_id].get(
                        "duplicate_text_count"
                    ),
                }
                for citation_id in summary_citation_ids
            ],
        }
    )
    return {"claims": items}


def read_claims(
    result: ModelCallResult,
) -> list[tuple[TiebaSummaryClaimKind, str, list[str]]]:
    if result.status.value not in {"success", "degraded"} or not isinstance(result.output, dict):
        return []
    values: list[tuple[TiebaSummaryClaimKind, str, list[str]]] = []
    for row in result.output.get("claims", []):
        if not isinstance(row, dict):
            continue
        kind = str(row.get("kind") or "finding")
        if kind not in {"finding", "difference", "uncertainty", "time_change"}:
            kind = "finding"
        text = str(row.get("text") or "").strip()[:260]
        ids = [str(value) for value in row.get("citation_ids", []) if isinstance(value, str)]
        if text and ids:
            values.append((cast(TiebaSummaryClaimKind, kind), text, ids))
    return values


def read_summary(result: ModelCallResult) -> str | None:
    if result.status.value not in {"success", "degraded"} or not isinstance(result.output, dict):
        return None
    summary = str(result.output.get("summary") or "").strip()[:1200]
    return summary or None


def aggregation_payload(topic: str, claims: list[TiebaSummaryClaim]) -> dict[str, Any]:
    return {
        "topic": topic,
        "claims": [
            {
                "kind": claim.kind,
                "text": claim.text,
                "citation_ids": claim.citation_ids,
            }
            for claim in claims
        ],
    }


def supported_indices(result: ModelCallResult, count: int) -> set[int]:
    if result.status.value not in {"success", "degraded"} or not isinstance(result.output, dict):
        return set()
    return {
        value
        for value in result.output.get("supported", [])
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < count
    }
