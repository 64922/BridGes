"""贴吧搜索与帖子页结构化 JSON 的防御式解析。

搜索页实际触发 ``/mo/q/search/thread``，帖子页实际触发
``/c/f/pb/page_pc``。两个响应都只作为「页面原文的传输形态」使用：字段缺失
保持未知，结构不符抛出显式错误，绝不用看板文字或搜索摘要补齐。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from bridges.tieba.lexicon import TARGET_FORUM_NAME

BROWSER_PROTOCOL_VERSION = "tieba-browser-protocol-v1"

_CHINA_TZ = timezone(timedelta(hours=8))


class StructuredResponseError(ValueError):
    """结构化响应缺失关键字段或声明失败。"""


def parse_search_payload(
    payload: Any,
    *,
    discovery_type: str,
) -> list[dict[str, object]]:
    """把一次吧内搜索响应转成候选；摘要只用于发现，不进入总结证据。"""
    _validate_envelope(payload)
    data = _mapping(_mapping(payload).get("data"))
    posts = data.get("post_list")
    if not isinstance(posts, list):
        raise StructuredResponseError("搜索响应缺少 post_list")
    hits: list[dict[str, object]] = []
    for item in posts:
        if not isinstance(item, dict):
            continue
        tid = _text(item.get("tid"))
        if not tid:
            continue
        forum = _text(item.get("forum_name"))
        content = _text(item.get("content"))
        snippet = "\n".join(
            part for part in (_with_forum_suffix(forum), content) if part
        )[:900]
        hits.append(
            {
                "url": _thread_url(tid, item.get("pb_url")),
                "title": (_text(item.get("title")) or "（页面未给出标题）")[:160],
                "snippet": snippet,
                "discovery_type": discovery_type,
                "matched_reply": content or None if discovery_type == "reply" else None,
            }
        )
    if posts and not hits:
        raise StructuredResponseError("搜索响应的候选都缺少帖子 ID")
    return hits


def parse_thread_payload(
    payload: Any,
    *,
    url: str,
    floors: dict[str, int] | None = None,
) -> dict[str, object]:
    """把一次帖子页响应转成与 DOM 路径同形的读取结果。"""
    _validate_envelope(payload)
    root = _mapping(payload)
    forum = _mapping(root.get("forum"))
    forum_name = _text(forum.get("name"))
    if not forum_name:
        raise StructuredResponseError("帖子响应缺少吧名")
    thread = _mapping(root.get("thread"))
    first_floor = root.get("first_floor")
    if not isinstance(first_floor, dict):
        raise StructuredResponseError("帖子响应缺少主帖 first_floor")
    posts = root.get("post_list")
    if not isinstance(posts, list):
        raise StructuredResponseError("帖子响应缺少 post_list")

    users = _user_names(root.get("user_list"))
    title = _text(thread.get("title")) or _text(first_floor.get("title")) or None
    original = _rich_text(first_floor.get("content"))
    original_id = _text(first_floor.get("id") or first_floor.get("pid"))
    original_floor = _floor(first_floor.get("floor"), floors, original_id)
    replies: list[dict[str, object]] = []
    loaded_reply_count = 0
    for item in posts:
        if not isinstance(item, dict):
            continue
        reply_id = _text(item.get("id") or item.get("pid"))
        floor = _floor(item.get("floor"), floors, reply_id)
        author = _author(item, users)
        content = _rich_text(item.get("content"))
        if content:
            replies.append(
                {
                    "content": content[:600],
                    "reply_id": reply_id or None,
                    "floor": floor,
                    "posted_at": _format_time(item.get("time")),
                    "author": author,
                    "parent_reply_id": None,
                    "nested": False,
                }
            )
        loaded_reply_count += 1
        for sub in _sub_posts(item):
            sub_id = _text(sub.get("id") or sub.get("spid") or sub.get("pid"))
            sub_content = _rich_text(sub.get("content"))
            if not sub_content:
                continue
            replies.append(
                {
                    "content": sub_content[:600],
                    "reply_id": sub_id or None,
                    "floor": floor,
                    "posted_at": _format_time(sub.get("time")),
                    "author": _author(sub, users),
                    "parent_reply_id": reply_id or None,
                    "nested": True,
                }
            )
    page = _mapping(root.get("page"))
    total_pages = _positive_int(page.get("total_page"))
    total_replies = _as_int(thread.get("reply_num"))
    if total_replies is None:
        total_replies = _as_int(first_floor.get("sub_post_number"))
    return {
        "url": _canonical_url(url),
        "thread_id": _text(thread.get("id") or thread.get("tid")) or None,
        "forum": _with_forum_suffix(forum_name),
        "title": title,
        "original": original[:2000],
        "posted_at": _format_time(first_floor.get("time")),
        "author": _author(first_floor, users),
        "original_floor": original_floor,
        "origin_reply_id": original_id or None,
        "loaded_reply_count": loaded_reply_count,
        "total_replies": total_replies,
        "total_pages": total_pages,
        "pages_read": 1,
        "pages_limit": 1,
        "replies": replies[:120],
        "response_valid": True,
        "source": "structured",
        "protocol_version": BROWSER_PROTOCOL_VERSION,
    }


def _validate_envelope(payload: Any) -> None:
    if not isinstance(payload, dict):
        raise StructuredResponseError("结构化响应不是对象")
    number = _as_int(payload.get("no"))
    if number is not None and number != 0:
        raise StructuredResponseError("结构化响应声明失败")
    error = _text(payload.get("error"))
    if error and error != "success":
        raise StructuredResponseError("结构化响应声明失败")
    error_code = _as_int(payload.get("error_code"))
    if error_code is not None and error_code != 0:
        raise StructuredResponseError("结构化响应返回错误码")
    if _text(payload.get("error_msg")):
        raise StructuredResponseError("结构化响应返回错误说明")


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _user_names(value: Any) -> dict[str, str]:
    names: dict[str, str] = {}
    if not isinstance(value, list):
        return names
    for user in value:
        if not isinstance(user, dict):
            continue
        user_id = _text(user.get("id") or user.get("user_id"))
        name = _text(
            user.get("name_show")
            or user.get("name")
            or user.get("show_nickname")
            or user.get("user_name")
        )
        if user_id and name:
            names[user_id] = name
    return names


def _author(item: dict[str, Any], users: dict[str, str]) -> str | None:
    author = item.get("author")
    if isinstance(author, dict):
        name = _text(author.get("name_show") or author.get("name") or author.get("user_name"))
        if name:
            return name
    author_id = _text(item.get("author_id") or item.get("uid"))
    return users.get(author_id) if author_id else None


def _sub_posts(item: dict[str, Any]) -> list[dict[str, Any]]:
    raw = item.get("sub_post_list")
    if isinstance(raw, dict):
        raw = raw.get("sub_post_list")
    if not isinstance(raw, list):
        return []
    return [value for value in raw if isinstance(value, dict)]


def _floor(
    value: Any,
    floors: dict[str, int] | None,
    reply_id: str,
) -> int | None:
    number = _positive_int(value)
    if number is not None:
        return number
    if floors and reply_id:
        return floors.get(reply_id)
    return None


def _format_time(value: Any) -> str | None:
    number = _as_int(value)
    if number is None or number <= 0:
        return None
    if number > 100_000_000_000:
        number //= 1000
    try:
        moment = datetime.fromtimestamp(number, tz=_CHINA_TZ)
    except (OverflowError, OSError, ValueError):
        return None
    return moment.strftime("%Y-%m-%d %H:%M")


def _rich_text(value: Any) -> str:
    if isinstance(value, str):
        return _clean(value)
    if isinstance(value, list):
        return _clean("".join(_rich_text(item) for item in value))
    if isinstance(value, dict):
        text = _text(value.get("text"))
        if text and not text.startswith("image_emoticon"):
            return _clean(text)
        return _rich_text(value.get("content"))
    return ""


def _thread_url(tid: str, pb_url: Any) -> str:
    raw = _text(pb_url)
    if raw:
        parsed = urlparse(raw)
        if parsed.hostname == "tieba.baidu.com" and parsed.path.startswith("/p/"):
            return _canonical_url(raw)
    return f"https://tieba.baidu.com/p/{tid}"


def _canonical_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.hostname == "tieba.baidu.com" and parsed.path.startswith("/p/"):
        parts = parsed.path.rstrip("/").split("/")
        if len(parts) >= 3 and parts[2]:
            return f"https://tieba.baidu.com/p/{parts[2]}"
    return value


def _with_forum_suffix(name: str) -> str:
    if not name:
        return ""
    return name if name.endswith("吧") else f"{name}吧"


def _clean(value: str) -> str:
    text = " ".join(value.replace("\u3000", " ").split())
    return text.strip()


def _text(value: Any) -> str:
    return _clean(str(value)) if value is not None else ""


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _positive_int(value: Any) -> int | None:
    number = _as_int(value)
    return number if number is not None and number > 0 else None


__all__ = [
    "BROWSER_PROTOCOL_VERSION",
    "StructuredResponseError",
    "TARGET_FORUM_NAME",
    "parse_search_payload",
    "parse_thread_payload",
]
