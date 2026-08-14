"""Issue 05：来源感知预算与统一截止时间回归测试。"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from bridges.arxiv_mcp.contracts import ArxivPaper, ArxivSearchStatus
from bridges.arxiv_mcp.service import ArxivSearchPlan, ArxivSearchService
from bridges.chat import budget as budget_module
from bridges.chat.budget import (
    SEARCH_HANDOFF_RESERVE_SECONDS,
    PUBLIC_SEARCH_STAGE_SECONDS,
    public_search_deadlines,
    source_aware_search_budget_seconds,
)
from bridges.web_search.contracts import WebSearchResult, WebSearchStatus
from bridges.web_search.service import SearchPlan, WebSearchService
from tests.chat.test_arxiv_search_chat import _CapturingAdapter, _context, _service


@pytest.mark.parametrize(
    ("active_sources", "remaining_ms", "expected_seconds"),
    [
        (set(), None, 0.0),
        ({"web"}, None, 8.0),
        ({"arxiv"}, None, 10.0),
        ({"arxiv", "web"}, None, 10.0),
        ({"arxiv", "web"}, 7_500, 7.5),
        ({"arxiv"}, 500, 0.5),
        ({"web"}, 0, 0.0),
    ],
)
def test_source_aware_search_budget_uses_only_active_sources(
    active_sources: set[str], remaining_ms: int | None, expected_seconds: float
) -> None:
    assert (
        source_aware_search_budget_seconds(active_sources, remaining_ms=remaining_ms)
        == expected_seconds
    )


def test_public_search_deadlines_share_stage_budget_and_scaled_handoff() -> None:
    deadlines = public_search_deadlines(100.0)

    assert deadlines.stage_deadline == 100.0 + PUBLIC_SEARCH_STAGE_SECONDS
    assert deadlines.provider_deadline == (
        deadlines.stage_deadline - SEARCH_HANDOFF_RESERVE_SECONDS
    )
    assert deadlines.handoff_reserve_seconds == SEARCH_HANDOFF_RESERVE_SECONDS

    scaled = public_search_deadlines(100.0, scale=0.1)
    assert scaled.stage_deadline == 100.8
    assert scaled.provider_deadline == 100.725
    assert scaled.handoff_reserve_seconds == pytest.approx(0.075)


class _DelayedArxivClient:
    def __init__(self, delay_seconds: float) -> None:
        self._delay_seconds = delay_seconds
        self.deadlines: list[float | None] = []

    def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        stop_event: Any | None = None,
        deadline: float | None = None,
    ) -> list[ArxivPaper]:
        self.deadlines.append(deadline)
        finish_at = time.monotonic() + self._delay_seconds
        while time.monotonic() < finish_at:
            if stop_event is not None and stop_event.is_set():
                return []
            time.sleep(0.005)
        return [
            ArxivPaper(
                arxiv_id="2401.00001",
                title="Transformer 论文",
                authors=["作者"],
                published_at=datetime(2024, 1, 1, tzinfo=UTC),
                abs_url="https://arxiv.org/abs/2401.00001",
                pdf_url="https://arxiv.org/pdf/2401.00001",
                abstract="Transformer 摘要。",
            )
        ][:max_results]


def test_paper_route_keeps_arxiv_only_request_inside_its_own_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """只启动 arXiv 时，9 秒替身不应被未参与的 web 预算提前截断。"""

    monkeypatch.setitem(budget_module.EXTERNAL_TIMEOUT_SECONDS, "web_search", 0.08)
    monkeypatch.setitem(budget_module.EXTERNAL_TIMEOUT_SECONDS, "arxiv_search", 0.10)
    client = _DelayedArxivClient(delay_seconds=0.09)
    service = _service(
        tmp_path,
        ArxivSearchService(client=client),
        _CapturingAdapter(),
    )
    conversation = service.create_conversation("alice")
    user, assistant = service.start_generation(
        "alice",
        conversation.conversation_id,
        "给我找几篇Transformer方向相关的论文",
    )

    started = time.monotonic()
    events = list(
        service.stream_generation(
            "alice",
            conversation.conversation_id,
            assistant.message_id,
            _context(),
            until_user_message_id=user.message_id,
        )
    )
    elapsed = time.monotonic() - started

    final = service.message_projection("alice", assistant.message_id)
    assert elapsed < 0.16
    assert events[-1].kind == "done"
    assert final is not None and final.arxiv_search is not None
    assert final.arxiv_search.status == ArxivSearchStatus.SUCCESS
    assert final.arxiv_search.papers[0].arxiv_id == "2401.00001"
    assert client.deadlines and client.deadlines[0] is not None
    captured_deadline = client.deadlines[0]
    assert captured_deadline is not None
    assert captured_deadline - started < 0.15


class _DeadlineWebClient:
    def __init__(self, delay_seconds: float) -> None:
        self._delay_seconds = delay_seconds
        self.deadlines: list[float | None] = []

    def search(
        self,
        query: str,
        *,
        deadline: float | None = None,
        stop_event: Any | None = None,
    ) -> list[WebSearchResult]:
        self.deadlines.append(deadline)
        end = time.monotonic() + self._delay_seconds
        while time.monotonic() < end:
            if stop_event is not None and stop_event.is_set():
                return []
            time.sleep(0.005)
        return [
            WebSearchResult(
                result_id="web-1",
                title="公开资料",
                site="example.org",
                url="https://example.org/source",
                snippet="公开摘要",
                accessed_at=datetime.now(UTC),
            )
        ]


def test_web_search_sets_internal_stop_on_absolute_deadline() -> None:
    client = _DeadlineWebClient(delay_seconds=1.0)
    service = WebSearchService(client=client)
    plan = SearchPlan(
        True,
        "公开主题",
        "测试绝对截止时间",
        queries=("公开主题",),
        max_retries=0,
        total_timeout_seconds=2.0,
    )
    deadline = time.monotonic() + 0.08

    started = time.monotonic()
    projection = service.search("acct-1", plan, deadline=deadline)
    elapsed = time.monotonic() - started

    assert projection is not None
    assert projection.status == WebSearchStatus.ERROR
    assert projection.error_code == "web_search_timeout"
    assert elapsed < 0.25
    captured_deadline = client.deadlines[0]
    assert captured_deadline is not None
    assert captured_deadline <= deadline


class _DeadlineArxivClient(_DelayedArxivClient):
    def __init__(self) -> None:
        super().__init__(delay_seconds=0.0)


def test_arxiv_search_receives_the_same_absolute_deadline() -> None:
    client = _DeadlineArxivClient()
    deadline = time.monotonic() + 1.0
    projection = ArxivSearchService(client=client).search(
        "acct-1",
        ArxivSearchPlan(True, "Transformer", "测试截止时间"),
        deadline=deadline,
    )

    assert projection is not None
    assert projection.status == ArxivSearchStatus.SUCCESS
    assert client.deadlines == [deadline]
