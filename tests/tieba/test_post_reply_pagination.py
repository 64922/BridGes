"""工单 06：普通楼层跨页读取、逐页证据与有界失败回归。"""

from __future__ import annotations

import json
import queue
import threading
import time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bridges.tieba import browser_thread_read
from bridges.tieba.browser import read_projection
from bridges.tieba.browser_pagination import (
    COUNT_CONFLICT,
    CURSOR_MISMATCH,
    DUPLICATE_CURSOR,
    EMPTY_PAGE,
    PAGE_FAILED,
    PAGE_LIMIT,
    PAGINATION_MISSING,
    WORKER_EXITED,
    PageStop,
    check_page,
    merge_thread_pages,
    page_url,
)
from bridges.tieba.browser_receive import receive_result
from bridges.tieba.browser_structured import StructuredResponseError, parse_thread_payload
from bridges.tieba.contracts import (
    ReadStatus,
    TiebaPageEvidence,
    TiebaPostProjection,
    TiebaSearchHit,
)
from bridges.tieba.evidence import confirmed_post

FIXTURE = Path(__file__).parent / "fixtures" / "tieba_thread_pages.json"
URL = "https://tieba.baidu.com/p/10897336006"


@pytest.fixture
def pages() -> list[dict[str, Any]]:
    payloads = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return [
        parse_thread_payload(payloads["thread_page_1"]["data"], url=URL),
        parse_thread_payload(payloads["thread_page_2"]["data"], url=URL),
    ]


def test_structured_page_exposes_cursor_next_semantics_and_counts(
    pages: list[dict[str, Any]],
) -> None:
    first, second = pages

    assert first["thread_id"] == "10897336006"
    assert first["forum"] == "华东交通大学吧"
    assert first["current_page"] == 1
    assert first["total_pages"] == 10
    assert first["has_more"] is True
    assert first["top_level_count"] == 3
    assert first["page_reply_count"] == 4
    assert first["total_replies"] == 242
    assert [reply["reply_id"] for reply in first["replies"]] == [
        "153764903877",
        "153765210147",
        "153765230585",
        "153765230586",
    ]
    assert second["current_page"] == 2
    assert second["has_more"] is True
    assert second["replies"][0]["floor"] == 17


def test_page_url_preserves_thread_and_sets_cursor() -> None:
    assert page_url(f"{URL}?fr=pb", 3) == f"{URL}?fr=pb&pn=3"


def test_two_pages_merge_page_two_replies_and_dedupe_upstream_ids(
    pages: list[dict[str, Any]],
) -> None:
    first, second = pages
    second["replies"].append(deepcopy(first["replies"][0]))

    merged = merge_thread_pages([first, second], pages_limit=2)

    assert merged["pages_read"] == 2
    assert merged["pages_limit"] == 2
    assert merged["pages_complete"] is False
    assert merged["pagination_error_code"] == PAGE_LIMIT
    assert [item["page"] for item in merged["page_evidence"]] == [1, 2]
    assert [item["page"] for item in merged["page_evidence"]] == [1, 2]
    assert merged["page_evidence"][1]["top_level_count"] == 3
    reply_ids = [reply.get("reply_id") for reply in merged["replies"]]
    assert reply_ids.count("153764903877") == 1
    assert "153766936409" in reply_ids
    assert merged["loaded_reply_count"] == 6
    assert merged["replies"][0]["content"]


def test_same_page_retry_is_idempotent(pages: list[dict[str, Any]]) -> None:
    merged = merge_thread_pages([pages[0], deepcopy(pages[0])], pages_limit=2)

    reply_ids = [reply.get("reply_id") for reply in merged["replies"]]
    assert len(reply_ids) == len(set(reply_ids))
    assert merged["loaded_reply_count"] == 3


def test_missing_upstream_id_is_not_deduped_by_identical_text(
    pages: list[dict[str, Any]],
) -> None:
    first = deepcopy(pages[0])
    first["replies"][0]["reply_id"] = None
    first["replies"][1]["reply_id"] = None
    first["replies"][1]["content"] = first["replies"][0]["content"]

    merged = merge_thread_pages([first], pages_limit=2)

    assert [reply.get("reply_id") for reply in merged["replies"][:2]] == [None, None]
    assert len(merged["replies"]) == 4


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda page: page.pop("current_page"), PAGINATION_MISSING),
        (lambda page: page.update({"has_more": None}), PAGINATION_MISSING),
        (
            lambda page: page.update({"current_page": 1}),
            DUPLICATE_CURSOR,
        ),
        (
            lambda page: page.update({"current_page": 3}),
            CURSOR_MISMATCH,
        ),
        (
            lambda page: page.update({"top_level_count": 0, "page_reply_count": 0, "replies": []}),
            EMPTY_PAGE,
        ),
        (lambda page: page.update({"total_pages": 11}), COUNT_CONFLICT),
        (lambda page: page.update({"total_replies": 243}), COUNT_CONFLICT),
    ],
)
def test_page_checks_stop_with_explicit_reasons(
    pages: list[dict[str, Any]], mutate: Any, expected: str
) -> None:
    page = deepcopy(pages[1])
    mutate(page)

    stop = check_page(
        page,
        requested_page=2,
        previous_page=1,
        expected_total_pages=10,
        expected_total_replies=242,
    )

    assert isinstance(stop, PageStop)
    assert stop.code == expected
    assert stop.message


def test_reaching_page_limit_is_not_reported_as_complete(
    pages: list[dict[str, Any]],
) -> None:
    merged = merge_thread_pages(pages, pages_limit=2)

    assert merged["pages_complete"] is False
    assert merged["pagination_error_code"] == PAGE_LIMIT
    assert "第 3 页" in merged["pagination_error_message"]


def test_two_page_thread_can_be_complete(pages: list[dict[str, Any]]) -> None:
    first, second = deepcopy(pages)
    first["total_pages"] = 2
    second["total_pages"] = 2
    second["has_more"] = False

    merged = merge_thread_pages([first, second], pages_limit=2)

    assert merged["pages_complete"] is True
    assert merged["pagination_error_code"] is None


def test_second_page_failure_keeps_first_page_evidence(
    pages: list[dict[str, Any]],
) -> None:
    stop = PageStop(PAGE_FAILED, "第 2 页读取失败：页面结构无法解析。")

    merged = merge_thread_pages([pages[0]], pages_limit=2, stop=stop)

    assert merged["pages_read"] == 1
    assert merged["pages_complete"] is False
    assert merged["pagination_error_code"] == PAGE_FAILED
    assert merged["pagination_error_message"] == stop.message
    assert [reply["floor"] for reply in merged["replies"]] == [3, 4, 5, 5]


def test_worker_reads_two_pages_and_emits_page_progress(
    monkeypatch: pytest.MonkeyPatch, pages: list[dict[str, Any]]
) -> None:
    requested: list[int] = []
    progress: list[dict[str, Any]] = []
    values = {1: pages[0], 2: pages[1]}

    def fake_page(
        page: Any, target: str, page_number: int, closed: Any, auth: Any
    ) -> tuple[dict[str, Any], Any]:
        del target, closed, auth
        requested.append(page_number)
        return values[page_number], page

    monkeypatch.setattr(browser_thread_read, "_read_thread_page", fake_page)
    result = browser_thread_read.read_thread(
        object(),
        URL,
        8,
        threading.Event(),
        SimpleNamespace(deadline=None),
        pages_limit=2,
        progress=progress.append,
    )

    assert requested == [1, 2]
    assert result["pages_read"] == 2
    assert result["pagination_error_code"] == PAGE_LIMIT
    assert [item["pages_read"] for item in progress] == [1, 2]
    assert progress[0].get("pagination_error_code") is None


def test_worker_second_page_failure_returns_first_page(
    monkeypatch: pytest.MonkeyPatch, pages: list[dict[str, Any]]
) -> None:
    def fake_page(
        page: Any, target: str, page_number: int, closed: Any, auth: Any
    ) -> tuple[dict[str, Any], Any]:
        del target, closed, auth
        if page_number == 1:
            return pages[0], page
        raise StructuredResponseError("第二页结构无法解析")

    monkeypatch.setattr(browser_thread_read, "_read_thread_page", fake_page)
    result = browser_thread_read.read_thread(
        object(),
        URL,
        8,
        threading.Event(),
        SimpleNamespace(deadline=None),
        pages_limit=2,
    )

    assert result["pages_read"] == 1
    assert result["pages_complete"] is False
    assert result["pagination_error_code"] == PAGE_FAILED
    assert result["pagination_error_message"] == (
        "第 2 页读取失败：第二页结构无法解析；已保留此前成功页证据。"
    )
    assert [reply["floor"] for reply in result["replies"]] == [3, 4, 5, 5]


def test_read_projection_keeps_pages_evidence_and_unread_reason(
    pages: list[dict[str, Any]],
) -> None:
    merged = merge_thread_pages(pages, pages_limit=2)

    result = read_projection(URL, merged)

    assert result.status is ReadStatus.PARTIAL
    assert result.pages_read == 2
    assert result.pages_complete is False
    assert result.page_evidence == [
        TiebaPageEvidence(
            page=1,
            total_pages=10,
            has_more=True,
            top_level_count=3,
            reply_count=4,
        ),
        TiebaPageEvidence(
            page=2,
            total_pages=10,
            has_more=True,
            top_level_count=3,
            reply_count=4,
        ),
    ]
    assert result.error_code == PAGE_LIMIT
    assert "第 3 页" in (result.error_message or "")
    assert result.floor_min == 1 and result.floor_max == 19
    assert any(reply.floor == 17 for reply in result.replies)


def test_page_evidence_survives_projection_serialization(
    pages: list[dict[str, Any]],
) -> None:
    result = read_projection(URL, merge_thread_pages(pages, pages_limit=2))
    post = confirmed_post(
        TiebaSearchHit(url=URL, title="宿舍热水和查寝情况"), result
    )

    reloaded = TiebaPostProjection.model_validate(json.loads(post.model_dump_json()))

    assert reloaded.page_evidence == result.page_evidence
    assert reloaded.pages_complete == result.pages_complete
    assert reloaded.pagination_error_message == result.pagination_error_message


def test_worker_exit_returns_the_last_successful_page() -> None:
    worker = SimpleNamespace(output=queue.Queue())
    progress = {
        "forum": "华东交通大学吧",
        "title": "宿舍热水和查寝情况",
        "original": "想问问北区宿舍的热水。",
        "replies": [{"content": "第一页回复", "reply_id": "r-1", "floor": 3}],
        "pages_read": 1,
        "pages_limit": 2,
        "total_pages": 10,
        "pages_complete": False,
        "page_evidence": [
            {
                "page": 1,
                "total_pages": 10,
                "has_more": True,
                "top_level_count": 1,
                "reply_count": 1,
            }
        ],
        "source": "structured",
        "response_valid": True,
    }
    worker.output.put(json.dumps({"progress": progress}, ensure_ascii=False))
    worker.output.put(None)

    value = receive_result(
        worker,
        stop_event=None,
        deadline=time.monotonic() + 5,
        request_budget=None,
        request_guard=None,
        send=lambda _payload: None,
    )

    assert value["pages_read"] == 1
    assert value["replies"][0]["reply_id"] == "r-1"
    assert value["pages_complete"] is False
    assert value["pagination_error_code"] == WORKER_EXITED
    assert value["pagination_error_message"]


def test_dom_fallback_cannot_claim_complete_pagination() -> None:
    result = read_projection(
        URL,
        {
            "forum": "华东交通大学吧",
            "original": "主题正文",
            "replies": [{"content": "滚动可见回复", "posted_at": "10-06"}],
            "source": "dom",
            "pages_read": 1,
            "pages_limit": 1,
            "pages_complete": False,
            "pagination_error_code": "tieba_browser_pagination_unavailable",
            "pagination_error_message": "DOM 只补充有限滚动范围，未声称读取全部页。",
        },
    )

    assert result.status is ReadStatus.PARTIAL
    assert result.pages_complete is False
    assert result.error_code == "tieba_browser_pagination_unavailable"
    assert "未穷尽" in (result.error_message or "")
    assert "分页" in (result.error_message or "")
