"""Issue 21：公网搜索触发、隐私脱敏、失败与取消的确定性测试。"""

from __future__ import annotations

import os
from threading import Event

import httpx
import pytest

from bridges.contracts.chat import ChatMode
from bridges.contracts.observability import AuditAction
from bridges.observability.service import ObservabilityService
from bridges.web_search.client import DuckDuckGoClient, WebSearchError
from bridges.web_search.contracts import WebSearchStatus
from bridges.web_search.service import LocalQueryPlanner, SearchPlan, WebSearchService


class _FakeSearchClient:
    def __init__(self, results: list[dict[str, str]] | None = None) -> None:
        self.queries: list[str] = []
        self.results = results or []

    def search(self, query: str):
        self.queries.append(query)
        return self.results


def test_planner_only_triggers_on_explicit_fresh_or_fact_request() -> None:
    planner = LocalQueryPlanner()

    assert planner.plan("陪我聊聊我的心情", ChatMode.COMPANION).should_search is False
    assert "明确要求" in planner.plan("请联网查一下量子计算", ChatMode.COMPANION).reason
    assert "最新信息" in planner.plan("量子计算最新进展", ChatMode.COMPANION).reason
    assert "事实核查" in planner.plan("请核实这条消息是否属实", ChatMode.COMPANION).reason
    assert planner.plan("量子计算最新进展", ChatMode.STUDY).should_search is False


def test_planner_never_sends_private_documents_profile_accounts_or_credentials() -> None:
    plan = LocalQueryPlanner().plan(
        "请联网核实量子计算最新进展。私人文档：内部代号蓝鲸和客户计划。"
        "我的个人资料是研究员，QQ 123456789，邮箱 alice@example.com，密码=secret-123。",
        ChatMode.COMPANION,
    )

    assert plan.should_search is True
    for private_value in (
        "内部代号蓝鲸",
        "客户计划",
        "研究员",
        "123456789",
        "alice@example.com",
        "secret-123",
    ):
        assert private_value not in plan.query
    assert "量子" in plan.query
    assert len(plan.query) < 80


def test_planner_removes_english_private_markers_and_avoids_personal_freshness_false_positive(
) -> None:
    planner = LocalQueryPlanner()
    plan = planner.plan(
        "请联网查查机器学习最新进展。attachment: 私人合同，profile: researcher，"
        "account_id=acct-123，api_key=top-secret。",
        ChatMode.COMPANION,
    )

    assert plan.should_search is True
    for private_value in ("私人合同", "researcher", "acct-123", "top-secret"):
        assert private_value not in plan.query
    assert planner.plan("陪我聊聊今天的心情", ChatMode.COMPANION).should_search is False


def test_planner_drops_unmarked_personal_profile_sentences() -> None:
    plan = LocalQueryPlanner().plan(
        "请联网查量子计算最新进展。我是一名研究员，研究方向是内部项目。",
        ChatMode.COMPANION,
    )

    assert plan.should_search is True
    assert "量子" in plan.query
    assert "研究员" not in plan.query
    assert "内部项目" not in plan.query


def test_duckduckgo_client_sends_only_minimal_public_query_and_real_links() -> None:
    captured: list[httpx.QueryParams] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.url.params)
        return httpx.Response(
            200,
            json={
                "Results": [
                    {
                        "FirstURL": "https://example.com/news",
                        "Heading": "公开新闻",
                        "Text": "公开摘要",
                    }
                ]
            },
        )

    results = DuckDuckGoClient(
        http_client=httpx.Client(transport=httpx.MockTransport(handler))
    ).search("量子 计算")

    assert captured[0]["q"] == "量子 计算"
    assert set(captured[0].keys()) == {
        "q",
        "format",
        "no_html",
        "no_redirect",
        "skip_disambig",
    }
    assert results[0].url == "https://example.com/news"
    assert results[0].site == "example.com"


@pytest.mark.parametrize(
    ("status_code", "body", "expected_code"),
    [
        (429, b"{}", "web_search_rate_limit"),
        (200, b"not-json", "web_search_parse"),
    ],
)
def test_duckduckgo_client_maps_rate_limit_and_parse_failure(
    status_code: int, body: bytes, expected_code: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, content=body)

    client = DuckDuckGoClient(
        http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(WebSearchError) as exc_info:
        client.search("公开主题")
    assert exc_info.value.code == expected_code


def test_duckduckgo_client_maps_offline_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    client = DuckDuckGoClient(
        http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(WebSearchError) as exc_info:
        client.search("公开主题")
    assert exc_info.value.code == "web_search_offline"


@pytest.mark.skipif(
    os.getenv("BRIDGES_DDG_SMOKE") != "1",
    reason="显式设置 BRIDGES_DDG_SMOKE=1 才运行真实 DuckDuckGo 冒烟",
)
def test_explicit_duckduckgo_smoke_needs_no_key() -> None:
    results = DuckDuckGoClient().search("OpenAI")
    assert results
    assert all(result.url.startswith(("https://", "http://")) for result in results)


def test_service_exposes_empty_error_and_cancelled_states_and_audits_without_query() -> None:
    observability = ObservabilityService()
    client = _FakeSearchClient()
    service = WebSearchService(client=client, observability=observability)
    plan = SearchPlan(True, "公开主题", "你明确要求联网搜索")

    empty = service.search("acct-1", plan)
    assert empty is not None and empty.status == WebSearchStatus.EMPTY
    event = next(iter(observability.list_audit_events(action=AuditAction.WEB_SEARCH)))
    assert "公开主题" not in str(event.model_dump())
    assert event.details is not None
    assert event.details["data_categories"] == ["public_query_terms"]

    class _FailingClient:
        def search(self, query: str):
            raise WebSearchError("web_search_timeout", "联网搜索超时，请重试。")

    failed = WebSearchService(client=_FailingClient()).search("acct-1", plan)
    assert failed is not None
    assert failed.status == WebSearchStatus.ERROR
    assert failed.error_code == "web_search_timeout"
    assert failed.can_retry is True

    cancelled_event = Event()
    cancelled_event.set()
    cancelled = service.search("acct-1", plan, stop_event=cancelled_event)
    assert cancelled is not None
    assert cancelled.status == WebSearchStatus.CANCELLED
    assert client.queries == ["公开主题"]
