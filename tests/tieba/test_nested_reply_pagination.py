"""工单 07：楼中楼独立分页、父楼层校验与预览／已读状态回归。"""

from __future__ import annotations

import html
import json
import threading
from typing import Any

import pytest

from bridges.tieba.browser_nested import (
    NESTED_AUTH_REQUIRED,
    NESTED_CANCELLED,
    NESTED_COUNT_CONFLICT,
    NESTED_CURSOR_MISMATCH,
    NESTED_DUPLICATE_CURSOR,
    NESTED_EMPTY_PAGE,
    NESTED_PAGE_LIMIT,
    NESTED_PAGINATION_MISSING,
    NESTED_PARENT_MISMATCH,
    NestedPageError,
    NestedRead,
    nested_comment_url,
    parse_nested_comment_fragment,
    read_nested_replies,
)
from bridges.tieba.contracts import TiebaNestedState
from tests.tieba.nested_reply_support import (
    FORUM_ID,
    PARENT,
    PARENT_FLOOR,
    PARENT_ID,
    PREVIEW,
    THREAD_ID,
)


def _nested_html(
    entries: list[tuple[str, str, str, str]],
    *,
    current: int,
    total_pages: int,
    total_replies: int,
) -> str:
    items: list[str] = []
    for spid, author, content, posted_at in entries:
        field = {
            "spid": spid,
            "showname": author,
            "user_name": author,
            "portrait": "portrait",
        }
        items.append(
            '<li class="lzl_single_post j_lzl_s_p" '
            f'data-field="{html.escape(json.dumps(field, ensure_ascii=False))}">'
            '<a rel="noopener" class="j_user_card" href="#">'
            f"{html.escape(author)}</a>:"
            f'<span class="lzl_content_main" data-username="">'
            f"        {html.escape(content)}        </span>"
            f'<span class="lzl_time">{html.escape(posted_at)}</span></li>'
        )
    pager = {
        "total_num": total_replies,
        "total_page": total_pages,
    }
    return (
        "<html><body>"
        + "".join(items)
        + '<li class="lzl_li_pager j_lzl_l_p" '
        + f'data-field="{html.escape(json.dumps(pager))}">'
        + f'<span class="tP">{current}</span></li>'
        + "</body></html>"
    )


def test_single_page_pager_without_current_marker_uses_page_one() -> None:
    fragment = _nested_html(
        PAGE_ONE,
        current=1,
        total_pages=1,
        total_replies=10,
    ).replace('<span class="tP">1</span>', "")

    parsed = parse_nested_comment_fragment(fragment, requested_page=1)
    assert parsed.current_page == 1
    assert parsed.total_pages == 1
    assert parsed.total_replies == 10

    def handler(page: Any, url: str, closed: threading.Event, deadline: float | None) -> str:
        del page, url, closed, deadline
        return fragment

    result = _read(handler, parent={**PARENT, "sub_post_number": 10})

    assert result.progress["state"] == TiebaNestedState.COMPLETE
    assert result.progress["pages_read"] == 1
    assert result.progress["read_count"] == 10
    assert result.progress["complete"] is True


def _entry(index: int) -> tuple[str, str, str, str]:
    return (
        f"n{index}",
        f"吧友{index}",
        f"第 {index} 条楼中楼",
        f"2026-08-{index:02d} 10:00",
    )


PAGE_ONE = [_entry(index) for index in range(1, 11)]
PAGE_TWO = [_entry(index) for index in range(11, 18)]


def _read(
    handler: Any,
    *,
    preview: list[dict[str, Any]] | None = None,
    parent: dict[str, Any] | None = None,
    pages_limit: int = 2,
    closed: threading.Event | None = None,
) -> NestedRead:
    return read_nested_replies(
        object(),
        thread_id=THREAD_ID,
        forum_id=FORUM_ID,
        parent=parent or PARENT,
        preview_replies=PREVIEW if preview is None else preview,
        pages_limit=pages_limit,
        closed=closed or threading.Event(),
        deadline=None,
        fetch_html=handler,
    )


def test_nested_comment_url_pins_thread_parent_forum_and_page() -> None:
    url = nested_comment_url(THREAD_ID, PARENT_ID, FORUM_ID, 2)

    assert f"tid={THREAD_ID}" in url
    assert f"pid={PARENT_ID}" in url
    assert f"fid={FORUM_ID}" in url
    assert url.endswith("pn=2")


def test_parser_keeps_upstream_identity_author_time_and_pager() -> None:
    fragment = _nested_html(
        PAGE_ONE,
        current=1,
        total_pages=2,
        total_replies=17,
    )

    page = parse_nested_comment_fragment(fragment, requested_page=1)

    assert page.current_page == 1
    assert page.total_pages == 2
    assert page.total_replies == 17
    assert page.missing_identity == 0
    assert page.replies[0]["reply_id"] == "n1"
    assert page.replies[0]["author"] == "吧友1"
    assert page.replies[0]["content"] == "第 1 条楼中楼"
    assert page.replies[0]["posted_at"] == "2026-08-01 10:00"


def test_two_pages_are_merged_with_actual_parent_and_complete_state() -> None:
    fetched: list[int] = []

    def handler(page: Any, url: str, closed: threading.Event, deadline: float | None) -> str:
        del page, closed, deadline
        page_number = int(url.rsplit("pn=", 1)[1])
        fetched.append(page_number)
        entries = PAGE_ONE if page_number == 1 else PAGE_TWO
        return _nested_html(
            entries,
            current=page_number,
            total_pages=2,
            total_replies=17,
        )

    result = _read(handler)

    assert fetched == [1, 2]
    assert result.progress["state"] == TiebaNestedState.COMPLETE
    assert result.progress["complete"] is True
    assert result.progress["pages_read"] == 2
    assert result.progress["total_pages"] == 2
    assert result.progress["total_replies"] == 17
    assert result.progress["preview_count"] == 4
    assert result.progress["read_count"] == 17
    assert [item["reply_id"] for item in result.replies] == [f"n{index}" for index in range(1, 18)]
    assert result.replies[0]["nested_preview"] is False
    assert all(
        item["parent_reply_id"] == PARENT_ID and item["floor"] == PARENT_FLOOR
        for item in result.replies
    )


def test_page_limit_keeps_read_pages_and_reports_partial() -> None:
    def handler(page: Any, url: str, closed: threading.Event, deadline: float | None) -> str:
        del page, url, closed, deadline
        return _nested_html(
            PAGE_ONE,
            current=1,
            total_pages=2,
            total_replies=17,
        )

    result = _read(handler, pages_limit=1)

    assert result.progress["state"] == TiebaNestedState.PARTIAL
    assert result.progress["complete"] is False
    assert result.progress["error_code"] == NESTED_PAGE_LIMIT
    assert result.progress["pages_read"] == 1
    assert result.progress["total_pages"] == 2
    assert len(result.replies) == 10
    message = str(result.progress["error_message"])
    assert f"第 {PARENT_FLOOR} 楼" in message
    assert "2 页" in message


def test_first_page_without_preview_identity_is_rejected() -> None:
    def handler(page: Any, url: str, closed: threading.Event, deadline: float | None) -> str:
        del page, url, closed, deadline
        return _nested_html(
            [_entry(index) for index in range(101, 111)],
            current=1,
            total_pages=2,
            total_replies=17,
        )

    result = _read(handler)

    assert result.progress["error_code"] == NESTED_PARENT_MISMATCH
    assert result.progress["pages_read"] == 0
    assert [item["reply_id"] for item in result.replies] == [f"n{index}" for index in range(1, 5)]


def test_duplicate_page_cursor_stops_without_infinite_pagination() -> None:
    def handler(page: Any, url: str, closed: threading.Event, deadline: float | None) -> str:
        del page, url, closed, deadline
        return _nested_html(
            PAGE_ONE,
            current=1,
            total_pages=2,
            total_replies=17,
        )

    result = _read(handler)

    assert result.progress["error_code"] == NESTED_DUPLICATE_CURSOR
    assert result.progress["pages_read"] == 1
    assert len(result.replies) == 10


def test_changed_total_replies_stops_with_count_conflict() -> None:
    def handler(page: Any, url: str, closed: threading.Event, deadline: float | None) -> str:
        del page, url, closed, deadline
        page_number = 1 if len(fetched) == 0 else 2
        fetched.append(page_number)
        return _nested_html(
            PAGE_ONE if page_number == 1 else PAGE_TWO,
            current=page_number,
            total_pages=2,
            total_replies=18 if page_number == 2 else 17,
        )

    fetched: list[int] = []
    result = _read(handler)

    assert result.progress["error_code"] == NESTED_COUNT_CONFLICT
    assert result.progress["complete"] is False
    assert result.progress["pages_read"] == 2
    assert len(result.replies) == 17


def test_cursor_mismatch_stops_before_trusting_page() -> None:
    def handler(page: Any, url: str, closed: threading.Event, deadline: float | None) -> str:
        del page, url, closed, deadline
        return _nested_html(
            PAGE_ONE,
            current=3,
            total_pages=3,
            total_replies=17,
        )

    result = _read(handler)

    assert result.progress["error_code"] == NESTED_CURSOR_MISMATCH
    assert result.progress["pages_read"] == 0


def test_auth_required_keeps_preview_and_explicit_reason() -> None:
    def handler(page: Any, url: str, closed: threading.Event, deadline: float | None) -> str:
        del page, url, closed, deadline
        return "<html><head><title>百度安全验证</title></head><body></body></html>"

    result = _read(handler)

    assert result.progress["error_code"] == NESTED_AUTH_REQUIRED
    assert result.progress["state"] == TiebaNestedState.PREVIEW
    assert len(result.replies) == 4
    assert "登录" in str(result.progress["error_message"])


def test_empty_later_page_stops_without_claiming_complete() -> None:
    def handler(page: Any, url: str, closed: threading.Event, deadline: float | None) -> str:
        del page, closed, deadline
        if url.endswith("pn=1"):
            return _nested_html(
                PAGE_ONE,
                current=1,
                total_pages=2,
                total_replies=17,
            )
        return "<html><body></body></html>"

    result = _read(handler)

    assert result.progress["error_code"] == NESTED_EMPTY_PAGE
    assert result.progress["complete"] is False
    assert len(result.replies) == 10


def test_cancellation_before_read_keeps_preview() -> None:
    closed = threading.Event()
    closed.set()

    def handler(page: Any, url: str, closed: threading.Event, deadline: float | None) -> str:
        raise AssertionError("cancelled read must not fetch")

    result = _read(handler, closed=closed)

    assert result.progress["error_code"] == NESTED_CANCELLED
    assert result.progress["state"] == TiebaNestedState.PREVIEW
    assert len(result.replies) == 4


def test_declared_count_fully_covered_by_preview_needs_no_request() -> None:
    def handler(page: Any, url: str, closed: threading.Event, deadline: float | None) -> str:
        raise AssertionError("preview already complete")

    result = _read(
        handler,
        parent={**PARENT, "sub_post_number": 4},
    )

    assert result.progress["state"] == TiebaNestedState.COMPLETE
    assert result.progress["complete"] is True
    assert result.progress["pages_read"] == 0


def test_preview_without_declared_count_is_not_reported_complete() -> None:
    def handler(page: Any, url: str, closed: threading.Event, deadline: float | None) -> str:
        raise AssertionError("unknown count must not fetch")

    result = _read(
        handler,
        parent={**PARENT, "sub_post_number": None},
    )

    assert result.progress["error_code"] == NESTED_PAGINATION_MISSING
    assert result.progress["state"] == TiebaNestedState.PREVIEW
    assert result.progress["complete"] is False


def test_auth_page_parser_raises_stable_code() -> None:
    with pytest.raises(NestedPageError) as error:
        parse_nested_comment_fragment(
            "<html><title>百度安全验证</title><body>请登录</body></html>",
            requested_page=1,
        )

    assert error.value.code == NESTED_AUTH_REQUIRED


def test_missing_reply_identity_is_reported_without_inventing_id() -> None:
    fragment = _nested_html(
        PAGE_ONE,
        current=1,
        total_pages=2,
        total_replies=17,
    ).replace("&quot;spid&quot;: &quot;n1&quot;", "&quot;spid&quot;: &quot;&quot;")

    parsed = parse_nested_comment_fragment(fragment, requested_page=1)
    assert parsed.missing_identity == 1
    assert "n1" not in {item["reply_id"] for item in parsed.replies}

    def handler(page: Any, url: str, closed: threading.Event, deadline: float | None) -> str:
        del page, url, closed, deadline
        return fragment

    result = _read(handler)

    assert result.progress["error_code"] == NESTED_COUNT_CONFLICT
    assert result.progress["complete"] is False
    assert "缺少稳定 ID" in str(result.progress["error_message"])
    assert all(item["reply_id"] for item in result.replies)
