"""Issue 22：受限 arXiv MCP 的客户端、脱敏与状态投影测试。"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from threading import Event

import httpx
import pytest

from bridges.arxiv_mcp.client import ArxivMcpClient, ArxivMcpError
from bridges.arxiv_mcp.contracts import ArxivPaper, ArxivSearchStatus
from bridges.arxiv_mcp.manifest import assert_registered_arxiv_url, builtin_arxiv_manifest
from bridges.arxiv_mcp.service import ArxivQueryPlanner, ArxivSearchPlan, ArxivSearchService
from bridges.contracts.chat import ChatMode
from bridges.observability.service import ObservabilityService

ATOM_RESPONSE = """
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2401.12345v2</id>
    <title>Quantum Error Correction with Structured Codes</title>
    <summary>We study structured codes for correcting quantum errors.</summary>
    <published>2024-01-18T12:00:00Z</published>
    <author><name>Ada Lovelace</name></author>
    <author><name>Alan Turing</name></author>
    <link rel="alternate" type="text/html" href="http://arxiv.org/abs/2401.12345v2" />
    <link title="pdf" type="application/pdf" href="http://arxiv.org/pdf/2401.12345v2" />
  </entry>
</feed>
"""


def test_builtin_arxiv_manifest_is_minimal_and_default_deny() -> None:
    manifest = builtin_arxiv_manifest()

    assert manifest.version == "2026.08.04"
    assert manifest.network_domains == ["export.arxiv.org"]
    assert manifest.filesystem_read == []
    assert manifest.filesystem_write == []
    assert manifest.external_commands == []
    assert manifest.secret_names == []


def test_manifest_rejects_unregistered_domains() -> None:
    with pytest.raises(PermissionError):
        assert_registered_arxiv_url("https://example.com/api/query")


def test_client_parses_real_atom_metadata_and_derives_only_matching_links() -> None:
    client = ArxivMcpClient(
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, text=ATOM_RESPONSE)
            )
        )
    )

    papers = client.search("量子 纠错", max_results=5)

    assert len(papers) == 1
    paper = papers[0]
    assert paper.arxiv_id == "2401.12345v2"
    assert paper.title == "Quantum Error Correction with Structured Codes"
    assert paper.authors == ["Ada Lovelace", "Alan Turing"]
    assert paper.published_at == datetime(2024, 1, 18, 12, tzinfo=UTC)
    assert paper.abs_url == "https://arxiv.org/abs/2401.12345v2"
    assert paper.pdf_url == "https://arxiv.org/pdf/2401.12345v2"


def test_client_preserves_structured_route_constraints_in_arxiv_query() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text=ATOM_RESPONSE)

    client = ArxivMcpClient(
        http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )

    client.search(
        "quantum error correction author:Ada Lovelace title:structured codes year:2020-2024",
        max_results=3,
    )

    params = dict(requests[0].url.params.multi_items())
    assert params["max_results"] == "3"
    assert 'all:quantum AND all:error AND all:correction' in params["search_query"]
    assert 'au:"Ada Lovelace"' in params["search_query"]
    assert 'ti:"structured codes"' in params["search_query"]
    assert "submittedDate:[202001010000 TO 202412312359]" in params["search_query"]


@pytest.mark.parametrize(
    ("status_code", "body", "expected_code"),
    [
        (429, "", "arxiv_rate_limit"),
        (200, "not xml", "arxiv_parse"),
    ],
)
def test_client_maps_rate_limit_and_corrupt_response(
    status_code: int, body: str, expected_code: str
) -> None:
    client = ArxivMcpClient(
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(status_code, text=body)
            )
        )
    )

    with pytest.raises(ArxivMcpError) as exc_info:
        client.search("公开主题")

    assert exc_info.value.code == expected_code


def test_arxiv_planner_searches_in_both_modes_and_scrubs_private_context() -> None:
    planner = ArxivQueryPlanner()
    plan = planner.plan(
        "请帮我找近三年量子纠错综述论文。我的个人资料是研究员，"
        "attachment: 内部实验记录，邮箱 alice@example.com，api_key=secret-123。",
        ChatMode.STUDY,
    )

    assert plan.should_search is True
    assert "量子" in plan.query
    for private_value in ("内部实验记录", "研究员", "alice@example.com", "secret-123"):
        assert private_value not in plan.query
    assert planner.plan("陪我聊聊今天的心情", ChatMode.COMPANION).should_search is False


def test_forced_planner_does_not_fallback_to_a_broad_public_topic() -> None:
    plan = ArxivQueryPlanner().plan("给我找几篇论文", ChatMode.STUDY, force=True)

    assert plan.should_search is False
    assert plan.query == ""


def _paper() -> ArxivPaper:
    return ArxivPaper(
        arxiv_id="2401.12345v2",
        title="Quantum Error Correction with Structured Codes",
        authors=["Ada Lovelace"],
        published_at=datetime(2024, 1, 18, tzinfo=UTC),
        abs_url="https://arxiv.org/abs/2401.12345v2",
        pdf_url="https://arxiv.org/pdf/2401.12345v2",
        abstract="We study structured codes for correcting quantum errors.",
    )


class _FakeArxivClient:
    def __init__(self, papers: list[ArxivPaper] | None = None) -> None:
        self.queries: list[str] = []
        self.papers = papers if papers is not None else [_paper()]

    def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        stop_event: Event | None = None,
    ) -> list[ArxivPaper]:
        self.queries.append(query)
        return self.papers[:max_results]


def test_service_returns_chinese_context_and_learning_advice_without_fallback() -> None:
    client = _FakeArxivClient()
    service = ArxivSearchService(client=client)
    plan = ArxivSearchPlan(True, "量子 纠错", "用户明确要求搜索论文")

    projection = service.search("acct-1", plan)

    assert projection is not None
    assert projection.status == ArxivSearchStatus.SUCCESS
    assert projection.papers[0].citation_id == "arxiv-1"
    assert "论文摘要" in projection.papers[0].summary_zh
    assert "量子" in projection.papers[0].relevance_basis
    assert projection.papers[0].learning_advice_zh
    assert client.queries == ["量子 纠错"]


def test_service_whitelists_only_deterministically_relevant_papers() -> None:
    relevant = _paper()
    relevant = ArxivPaper(
        arxiv_id=relevant.arxiv_id,
        title="Transformer Models for Structured Representations",
        authors=relevant.authors,
        published_at=relevant.published_at,
        abs_url=relevant.abs_url,
        pdf_url=relevant.pdf_url,
        abstract="We evaluate Transformer architectures on structured data.",
    )
    unrelated = _paper()
    client = _FakeArxivClient([relevant, unrelated])
    planner = ArxivQueryPlanner()
    service = ArxivSearchService(client=client)

    plan = planner.plan("给我找几篇Transformer方向相关的论文", ChatMode.COMPANION)
    projection = service.search("acct-1", plan)

    assert projection is not None
    assert projection.status == ArxivSearchStatus.SUCCESS
    assert [paper.title for paper in projection.papers] == [relevant.title]


def test_all_unrelated_papers_fail_closed_with_a_distinct_result_code() -> None:
    client = _FakeArxivClient([_paper()])
    planner = ArxivQueryPlanner()
    service = ArxivSearchService(client=client)

    plan = planner.plan("搜索Transformer论文", ChatMode.COMPANION)
    projection = service.search("acct-1", plan)

    assert projection is not None
    assert projection.status == ArxivSearchStatus.EMPTY
    assert projection.error_code == "arxiv_no_relevant_results"
    assert projection.can_retry is True


def test_arxiv_audit_contains_counts_and_no_query_or_private_body() -> None:
    private_values = ("内部代号蓝鲸", "alice@example.com", "secret-123")
    client = _FakeArxivClient([])
    observations = ObservabilityService()
    service = ArxivSearchService(client=client, observability=observations)
    plan = ArxivSearchPlan(
        True,
        "Transformer",
        "用户输入：" + "；".join(private_values),
    )

    projection = service.search("acct-1", plan)
    events = observations.list_audit_events(account_id="acct-1")

    assert projection is not None
    assert len(events) == 1
    details = events[0].details
    assert details["public_term_count"] == 0
    assert details["removed_categories"] == []
    assert details["candidate_count"] == 0
    assert details["relevant_result_count"] == 0
    serialized = str(events[0])
    for private_value in private_values:
        assert private_value not in serialized


def test_service_exposes_empty_permission_cancelled_and_recovery_states() -> None:
    empty = ArxivSearchService(client=_FakeArxivClient([])).search(
        "acct-1", ArxivSearchPlan(True, "不存在主题", "用户明确要求搜索论文")
    )
    assert empty is not None and empty.status == ArxivSearchStatus.EMPTY
    assert empty.can_retry is True

    class _PermissionClient:
        def search(
            self,
            query: str,
            *,
            max_results: int = 5,
            stop_event: Event | None = None,
        ) -> list[ArxivPaper]:
            raise ArxivMcpError("arxiv_permission", "arXiv 网络权限未通过。", permission=True)

    denied = ArxivSearchService(client=_PermissionClient()).search(
        "acct-1", ArxivSearchPlan(True, "公开主题", "用户明确要求搜索论文")
    )
    assert denied is not None and denied.status == ArxivSearchStatus.PERMISSION

    cancelled_event = Event()
    cancelled_event.set()
    cancelled = ArxivSearchService(client=_FakeArxivClient()).search(
        "acct-1",
        ArxivSearchPlan(True, "公开主题", "用户明确要求搜索论文"),
        stop_event=cancelled_event,
    )
    assert cancelled is not None and cancelled.status == ArxivSearchStatus.CANCELLED

    recovery = ArxivSearchService(client=_FakeArxivClient()).initial_projection(
        ArxivSearchPlan(True, "公开主题", "用户明确要求搜索论文"), recovery=True
    )
    assert recovery.status == ArxivSearchStatus.RECOVERY


def test_service_projects_mid_search_cancel_as_cancelled_not_startup() -> None:
    """Issue 05：搜索期间取消必须投影为 cancelled，而不是折叠成启动失败。"""

    class _CancelClient:
        def search(
            self,
            query: str,
            *,
            max_results: int = 5,
            stop_event: Event | None = None,
        ) -> list[ArxivPaper]:
            raise ArxivMcpError("arxiv_cancelled", "已取消本轮论文搜索。")

    projection = ArxivSearchService(client=_CancelClient()).search(
        "acct-1", ArxivSearchPlan(True, "公开主题", "用户明确要求搜索论文")
    )

    assert projection is not None
    assert projection.status == ArxivSearchStatus.CANCELLED
    assert projection.error_code == "arxiv_cancelled"
    assert projection.error_message == "已取消本轮论文搜索。"
    assert projection.can_retry is False


@pytest.mark.skipif(
    os.getenv("BRIDGES_ARXIV_SMOKE") != "1",
    reason="显式设置 BRIDGES_ARXIV_SMOKE=1 才运行真实 arXiv 冒烟",
)
def test_real_arxiv_smoke_matches_official_metadata() -> None:
    client = ArxivMcpClient(timeout=15)
    try:
        papers = client.search("quantum error correction", max_results=1)
    finally:
        client.close()

    assert papers
    assert papers[0].arxiv_id
    assert papers[0].authors
    assert papers[0].abs_url.endswith(papers[0].arxiv_id)
    assert papers[0].pdf_url.endswith(papers[0].arxiv_id)
