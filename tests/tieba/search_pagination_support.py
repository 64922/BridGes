"""搜索分页用例共享的真实响应形状与候选夹具。"""

from __future__ import annotations

from typing import Any

from bridges.tieba.contracts import TiebaSearchDiscovery, TiebaSearchHit

URL_A = "https://tieba.baidu.com/p/10707882244"
URL_B = "https://tieba.baidu.com/p/9049536696"


def _search_payload(
    *,
    page: int = 1,
    has_more: int = 0,
    posts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "no": 0,
        "error": "success",
        "data": {
            "current_page": page,
            "has_more": has_more,
            "post_list": posts or [],
        },
    }


def _post(
    *,
    tid: str = "10707882244",
    title: str = "南区健身房",
    content: str = "有女生专区，空调看老板心情开。",
    pid: int = 153508151470,
    cid: str = "153449627007",
) -> dict[str, Any]:
    return {
        "tid": tid,
        "pid": pid,
        "cid": cid,
        "title": title,
        "content": content,
        "forum_name": "华东交通大学",
        "pb_url": f"https://tieba.baidu.com/p/{tid}?tid={tid}&jump_tieba_native=1",
        "user": {"show_nickname": "吧友甲"},
    }


def _discovery(
    *,
    discovery_type: str = "thread",
    query: str = "空调",
    page: int | None = 1,
    matched_reply: str | None = None,
    matched_reply_id: str | None = None,
) -> TiebaSearchDiscovery:
    return TiebaSearchDiscovery(
        discovery_type=discovery_type,
        query=query,
        search_page=page,
        matched_reply=matched_reply,
        matched_reply_id=matched_reply_id,
    )


def _hit(
    url: str = URL_A,
    *,
    title: str = "南区健身房",
    discovery_type: str = "thread",
    page: int | None = 1,
    matched_reply: str | None = None,
    matched_reply_id: str | None = None,
    discoveries: list[TiebaSearchDiscovery] | None = None,
) -> TiebaSearchHit:
    return TiebaSearchHit(
        url=url,
        title=title,
        snippet="华东交通大学吧",
        discovery_type=discovery_type,
        matched_reply=matched_reply,
        matched_reply_id=matched_reply_id,
        search_page=page,
        discoveries=discoveries
        or [
            _discovery(
                discovery_type=discovery_type,
                page=page,
                matched_reply=matched_reply,
                matched_reply_id=matched_reply_id,
            )
        ],
    )
