"""工单 07：楼中楼 worker 接入、公开投影与引用合同回归。"""

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any

from bridges.tieba import browser_nested_enrich, browser_thread_read
from bridges.tieba.browser import read_projection
from bridges.tieba.browser_nested import (
    NESTED_PAGE_LIMIT,
    NestedRead,
)
from bridges.tieba.contracts import (
    ReadStatus,
    TiebaNestedState,
    TiebaReply,
    TiebaSummaryCitation,
)
from tests.tieba.nested_reply_support import (
    PARENT,
    PARENT_FLOOR,
    PARENT_ID,
    PREVIEW,
    THREAD_ID,
    URL,
)


def test_worker_enriches_parent_progress_and_dedupes_preview(
    monkeypatch: Any,
) -> None:
    page_value = {
        "forum": "华东交通大学吧",
        "title": "宿舍热水",
        "original": "北区热水几点停？",
        "thread_id": THREAD_ID,
        "total_replies": 18,
        "total_pages": 1,
        "current_page": 1,
        "has_more": False,
        "loaded_reply_count": 1,
        "page_reply_count": 5,
        "pages_read": 1,
        "pages_limit": 1,
        "page_evidence": [],
        "source": "structured",
        "response_valid": True,
        "replies": [
            {**PARENT, "parent_reply_id": None, "nested": False},
            *PREVIEW,
        ],
    }
    calls: list[str] = []

    def fake_read(
        page: Any,
        *,
        thread_id: str,
        forum_id: str | None,
        parent: dict[str, Any],
        preview_replies: Any,
        pages_limit: int,
        closed: threading.Event,
        deadline: float | None,
    ) -> NestedRead:
        del page, thread_id, forum_id, preview_replies, pages_limit, closed, deadline
        calls.append(str(parent["reply_id"]))
        return NestedRead(
            replies=tuple({**item, "nested_preview": False} for item in PREVIEW),
            progress={
                "state": TiebaNestedState.COMPLETE,
                "complete": True,
                "total_replies": 17,
                "preview_count": 4,
                "read_count": 17,
                "pages_read": 2,
                "pages_limit": 2,
                "total_pages": 2,
                "error_code": None,
                "error_message": None,
            },
        )

    def fake_page(
        page: Any, target: str, page_number: int, closed: Any, auth: Any
    ) -> tuple[dict[str, Any], Any]:
        del target, page_number, closed, auth
        return page_value, page

    monkeypatch.setattr(browser_thread_read, "_read_thread_page", fake_page)
    monkeypatch.setattr(browser_nested_enrich, "read_nested_replies", fake_read)

    result = browser_thread_read.read_thread(
        object(),
        URL,
        8,
        threading.Event(),
        SimpleNamespace(deadline=None),
        pages_limit=1,
        nested_pages_limit=2,
    )

    assert calls == [PARENT_ID]
    assert len(result["replies"]) == 5
    assert result["replies"][0]["nested_progress"]["complete"] is True
    assert result["replies"][1]["nested_preview"] is False
    assert result["page_reply_count"] == 5


def test_worker_marks_unattempted_parents_as_preview(monkeypatch: Any) -> None:
    parents = [
        {
            "reply_id": f"p{floor}",
            "floor": floor,
            "content": f"父楼层{floor}",
            "sub_post_number": 11,
            "parent_reply_id": None,
            "nested": False,
        }
        for floor in range(2, 10)
    ]
    preview = [
        {
            "reply_id": f"p{floor}-n1",
            "floor": floor,
            "content": "预览",
            "parent_reply_id": f"p{floor}",
            "nested": True,
            "nested_preview": True,
        }
        for floor in range(2, 10)
    ]
    page_value = {
        "forum": "华东交通大学吧",
        "title": "多父楼层",
        "original": "正文",
        "total_replies": 80,
        "total_pages": 1,
        "current_page": 1,
        "has_more": False,
        "loaded_reply_count": 8,
        "page_reply_count": 16,
        "pages_read": 1,
        "pages_limit": 1,
        "page_evidence": [],
        "source": "structured",
        "response_valid": True,
        "replies": [item for pair in zip(parents, preview, strict=True) for item in pair],
    }
    calls: list[str] = []

    def fake_read(
        page: Any,
        *,
        thread_id: str,
        forum_id: str | None,
        parent: dict[str, Any],
        preview_replies: Any,
        pages_limit: int,
        closed: threading.Event,
        deadline: float | None,
    ) -> NestedRead:
        del page, thread_id, forum_id, preview_replies, pages_limit, closed, deadline
        calls.append(str(parent["reply_id"]))
        return NestedRead(
            replies=(),
            progress={
                "state": TiebaNestedState.PARTIAL,
                "complete": False,
                "total_replies": 11,
                "preview_count": 1,
                "read_count": 0,
                "pages_read": 1,
                "pages_limit": 2,
                "total_pages": 2,
                "error_code": NESTED_PAGE_LIMIT,
                "error_message": "页数上限",
            },
        )

    def fake_page(
        page: Any, target: str, page_number: int, closed: Any, auth: Any
    ) -> tuple[dict[str, Any], Any]:
        del target, page_number, closed, auth
        return page_value, page

    monkeypatch.setattr(browser_thread_read, "_read_thread_page", fake_page)
    monkeypatch.setattr(browser_nested_enrich, "read_nested_replies", fake_read)

    result = browser_thread_read.read_thread(
        object(),
        URL,
        8,
        threading.Event(),
        SimpleNamespace(deadline=None),
        pages_limit=1,
        nested_pages_limit=2,
        nested_parents_limit=2,
    )

    assert calls == ["p2", "p3"]
    progress = {
        str(item["reply_id"]): item["nested_progress"]
        for item in result["replies"]
        if not item.get("nested")
    }
    assert progress["p2"]["error_code"] == NESTED_PAGE_LIMIT
    assert progress["p4"]["state"] == TiebaNestedState.PREVIEW
    assert progress["p4"]["error_code"] == "tieba_browser_nested_not_attempted"


def test_projection_exposes_nested_scope_and_keeps_preview_identity() -> None:
    parent = TiebaReply(
        floor=PARENT_FLOOR,
        reply_id=PARENT_ID,
        content="北区热水十一点停。",
        nested_progress={
            "state": TiebaNestedState.PREVIEW,
            "complete": False,
            "total_replies": 17,
            "preview_count": 4,
            "read_count": 0,
            "pages_read": 0,
            "pages_limit": 2,
            "total_pages": None,
            "error_code": NESTED_PAGE_LIMIT,
            "error_message": "第 4 楼楼中楼未读完。",
        },
    )
    value = {
        "forum": "华东交通大学吧",
        "title": "宿舍热水",
        "original": "北区热水几点停？",
        "total_replies": 18,
        "pages_read": 1,
        "pages_limit": 1,
        "pages_complete": True,
        "source": "structured",
        "response_valid": True,
        "replies": [
            parent.model_dump(),
            *PREVIEW,
        ],
    }

    result = read_projection(URL, value)

    assert result.nested_complete is False
    assert result.nested_incomplete_count == 1
    assert result.nested_error_code == NESTED_PAGE_LIMIT
    assert result.status is ReadStatus.PARTIAL
    assert result.error_code == NESTED_PAGE_LIMIT
    preview = next(reply for reply in result.replies if reply.reply_id == "n1")
    assert preview.nested is True
    assert preview.nested_preview is True
    assert preview.parent_reply_id == PARENT_ID
    assert "楼中楼" in (result.error_message or "")


def test_projection_does_not_invent_parent_for_orphan_nested_reply() -> None:
    value = {
        "forum": "华东交通大学吧",
        "title": "缺失父楼层",
        "original": "正文",
        "total_replies": 1,
        "pages_read": 1,
        "pages_limit": 1,
        "pages_complete": True,
        "source": "structured",
        "response_valid": True,
        "replies": [
            {
                "content": "找不到父楼层的楼中楼",
                "reply_id": "n-orphan",
                "parent_reply_id": "p-missing",
                "floor": None,
                "nested": True,
                "nested_preview": False,
            }
        ],
    }

    result = read_projection(URL, value)

    orphan = next(reply for reply in result.replies if reply.reply_id == "n-orphan")
    assert result.nested_complete is False
    assert orphan.parent_reply_id == "p-missing"
    assert orphan.floor is None
    assert result.nested_error_code == "tieba_browser_nested_parent_missing"


def test_citation_contract_keeps_nested_flag() -> None:
    citation = TiebaSummaryCitation(
        citation_id="E2",
        url=URL,
        reply_id="n5",
        parent_reply_id=PARENT_ID,
        floor=PARENT_FLOOR,
        quote="更正：北区是十一点半停水。",
        nested=True,
    )

    assert citation.nested is True
    assert citation.model_dump()["parent_reply_id"] == PARENT_ID
