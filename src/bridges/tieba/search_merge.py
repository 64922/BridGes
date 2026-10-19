"""按帖子链接合并搜索候选，并保留全部发现路径。"""

from __future__ import annotations

from bridges.tieba.contracts import TiebaSearchDiscovery, TiebaSearchHit


def merge_search_hit(left: TiebaSearchHit, right: TiebaSearchHit) -> TiebaSearchHit:
    """合并同一帖子的两条发现路径，不丢掉后来的回复命中。"""
    left = with_discovery(left)
    right = with_discovery(right)
    discoveries = dedupe_discoveries([*left.discoveries, *right.discoveries])
    becomes_reply = left.discovery_type != "reply" and right.discovery_type == "reply"
    return left.model_copy(
        update={
            "discovery_type": "reply" if becomes_reply else left.discovery_type,
            "matched_reply": (
                right.matched_reply if becomes_reply else left.matched_reply
            )
            or left.matched_reply
            or right.matched_reply,
            "matched_reply_id": (
                right.matched_reply_id if becomes_reply else left.matched_reply_id
            )
            or left.matched_reply_id
            or right.matched_reply_id,
            "matched_author": (
                right.matched_author if becomes_reply else left.matched_author
            )
            or left.matched_author
            or right.matched_author,
            "search_page": (
                right.search_page if becomes_reply else left.search_page
            )
            or left.search_page
            or right.search_page,
            "title": left.title or right.title,
            "snippet": left.snippet or right.snippet,
            "source": left.source or right.source,
            "discoveries": discoveries,
        }
    )


def with_discovery(hit: TiebaSearchHit) -> TiebaSearchHit:
    if hit.discoveries:
        return hit
    return hit.model_copy(
        update={
            "discoveries": [
                TiebaSearchDiscovery(
                    discovery_type=hit.discovery_type,
                    search_page=hit.search_page,
                    matched_reply=hit.matched_reply,
                    matched_reply_id=hit.matched_reply_id,
                    matched_author=hit.matched_author,
                    source=hit.source,
                )
            ]
        }
    )


def dedupe_discoveries(
    discoveries: list[TiebaSearchDiscovery],
) -> list[TiebaSearchDiscovery]:
    unique: list[TiebaSearchDiscovery] = []
    seen: set[tuple[object, ...]] = set()
    for item in discoveries:
        key = (
            item.discovery_type,
            item.query,
            item.search_page,
            item.matched_reply_id,
            item.matched_reply,
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique
