"""工单 03：arXiv 请求诊断、来源约束与单源失败下的多来源交付。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Event
from typing import Any

from fastapi.testclient import TestClient

from bridges.arxiv_mcp.cache import ArxivResultCache
from bridges.arxiv_mcp.errors import ArxivMcpError
from bridges.arxiv_mcp.guard import ArxivCooldown, ArxivThrottle
from bridges.arxiv_mcp.service import ArxivSearchService
from bridges.contracts.modules import ModuleQueryRecord, ModuleQueryStatus
from bridges.kernel.contracts import NodeInvocation, RecipeInputs
from bridges.paper.budget import PaperBudget
from bridges.paper.kernel import (
    NODE_PARSE,
    NODE_PLAN,
    NODE_SEARCH,
    PaperNodeFlow,
    build_paper_recipe,
)
from bridges.paper.service import PaperSearchService
from bridges.paper.source_types import CandidateSearchOutcome, PaperCandidate
from bridges.paper.sources import ArxivPaperSource
from tests.chat.test_chat_api import _create_conversation, _gateway_with, _register
from tests.paper.test_paper_module_flow import (  # noqa: F401 - pytest 夹具注入
    _run_and_read,
    _send,
    _SilentAdapter,
    client,
    sqlite_app,
)

NOW = datetime(2026, 10, 18, tzinfo=UTC)


def _candidate(
    title: str = "Machine Learning: A Beginner's Guide",
    abstract: str = "An introduction to machine learning methods for beginners.",
    *,
    source: str = "openalex",
    identifier: str = "W100",
) -> PaperCandidate:
    return PaperCandidate(
        arxiv_id=None,
        title=title,
        authors=["Ada Lovelace"],
        published_at=datetime(2024, 1, 1, tzinfo=UTC),
        abs_url=f"https://openalex.org/{identifier}",
        pdf_url="",
        abstract=abstract,
        source=source,
        source_id=identifier,
        openalex_id=f"https://openalex.org/{identifier}",
        sources=(source,),
        source_ids=(f"{source}:{identifier.lower()}",),
    )


class _OutcomeSource:
    """确定性来源：返回固定候选与统一记录，并记录实际查询。"""

    def __init__(
        self,
        source: str,
        *,
        candidates: list[PaperCandidate] | None = None,
        status: ModuleQueryStatus = ModuleQueryStatus.SUCCESS,
        error_code: str | None = None,
        error_message: str | None = None,
        retryable: bool = False,
        on_search: Any = None,
    ) -> None:
        self.source = source
        self.candidates = candidates or []
        self.status = status
        self.error_code = error_code
        self.error_message = error_message
        self.retryable = retryable
        self.on_search = on_search
        self.queries: list[str] = []

    def search(
        self,
        account_id: str,
        query: str,
        *,
        max_results: int,
        stop_event: Event | None = None,
        deadline: float | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
    ) -> CandidateSearchOutcome:
        del account_id, deadline, year_from, year_to
        self.queries.append(query)
        if self.on_search is not None:
            self.on_search(stop_event)
        selected = self.candidates[:max_results]
        return CandidateSearchOutcome(
            query=query,
            candidates=selected,
            record=ModuleQueryRecord(
                source=self.source,
                query=query,
                status=self.status,
                evidence_count=len(selected),
                retrieved_at=NOW,
                error_code=self.error_code,
                error_message=self.error_message,
                retryable=self.retryable,
            ),
        )


def _run_nodes(
    flow: PaperNodeFlow,
    content: str,
    names: tuple[str, ...],
) -> dict[str, Any]:
    artifacts: dict[str, Any] = {}
    inputs = RecipeInputs(
        account_id="account",
        conversation_id="conversation",
        run_id="run",
        user_message_id="message",
        user_content=content,
        task_id=None,
        task_version=None,
        wait_identity=None,
        artifacts={},
    )
    results: dict[str, Any] = {}
    for name in names:
        spec = next(item for item in build_paper_recipe().nodes if item.name == name)
        result = flow.run_node(
            NodeInvocation(
                spec=spec,
                inputs=replace(inputs, artifacts=artifacts),
                dependencies=artifacts,
                remaining_budget_ms=10000,
            )
        )
        results[name] = result
        assert result.artifact is not None
        artifacts[name] = result.artifact
    return results


def test_only_arxiv_constraint_never_calls_other_sources() -> None:
    arxiv = _OutcomeSource(
        "arxiv", candidates=[_candidate(source="arxiv", identifier="2401.00001")]
    )
    openalex = _OutcomeSource("openalex", candidates=[_candidate()])
    flow = PaperNodeFlow(
        source=arxiv,
        sources={"arxiv": arxiv, "openalex": openalex},
        enricher=None,
        intent_analyzer=None,
    )

    results = _run_nodes(
        flow,
        "只允许 arXiv，帮我找几篇机器学习入门论文",
        (NODE_PARSE, NODE_PLAN, NODE_SEARCH),
    )

    plans = results[NODE_PLAN].artifact.payload["plans"]
    assert [item["source"] for item in plans] == ["arxiv"]
    assert openalex.queries == []
    assert arxiv.queries
    assert results[NODE_SEARCH].failure is None


def test_all_sources_fail_keeps_each_query_record_and_retry_policy() -> None:
    arxiv = _OutcomeSource(
        "arxiv",
        status=ModuleQueryStatus.ERROR,
        error_code="arxiv_request",
        error_message="arXiv 未接受本次查询参数（HTTP 400）；本轮未使用该来源。",
        retryable=False,
    )
    openalex = _OutcomeSource(
        "openalex",
        status=ModuleQueryStatus.TIMEOUT,
        error_code="openalex_timeout",
        error_message="学术来源检索超时。",
        retryable=True,
    )
    flow = PaperNodeFlow(
        source=arxiv,
        sources={"arxiv": arxiv, "openalex": openalex},
        enricher=None,
        intent_analyzer=None,
    )

    results = _run_nodes(
        flow, "帮我找几篇机器学习入门论文", (NODE_PARSE, NODE_PLAN, NODE_SEARCH)
    )

    result = results[NODE_SEARCH]
    assert result.failure is not None
    assert result.failure["code"] == "paper_search_failed"
    assert result.failure["retryable"] is True
    records = result.artifact.payload["records"]
    assert [item["source"] for item in records] == ["arxiv", "openalex"]
    assert [item["error_code"] for item in records] == [
        "arxiv_request",
        "openalex_timeout",
    ]
    assert [item["retryable"] for item in records] == [False, True]


class _OneCallBudgetLedger:
    def __init__(self) -> None:
        self.calls = 0

    def register_external_call(self, **kwargs: Any) -> bool:
        del kwargs
        self.calls += 1
        return self.calls <= 1

    def record_external_call_result(self, **kwargs: Any) -> None:
        del kwargs

    def begin_adjustment(self, **kwargs: Any) -> bool:
        del kwargs
        return True

    def end_adjustment(self, **kwargs: Any) -> None:
        del kwargs


def test_shared_budget_stops_second_source_without_faking_success() -> None:
    arxiv = _OutcomeSource(
        "arxiv", candidates=[_candidate(source="arxiv", identifier="2401.00001")]
    )
    openalex = _OutcomeSource("openalex", candidates=[_candidate()])
    flow = PaperNodeFlow(
        source=arxiv,
        sources={"arxiv": arxiv, "openalex": openalex},
        enricher=None,
        intent_analyzer=None,
        budget=PaperBudget(
            ledger=_OneCallBudgetLedger(),
            account_id="account",
            run_id="run",
            work_deadline=NOW + timedelta(minutes=5),
        ),
    )

    results = _run_nodes(
        flow, "帮我找几篇机器学习入门论文", (NODE_PARSE, NODE_PLAN, NODE_SEARCH)
    )

    result = results[NODE_SEARCH]
    assert result.failure is None
    assert len(arxiv.queries) == 1
    assert openalex.queries == []
    assert any(
        "共享运行预算不足" in note for note in result.artifact.payload["notes"]
    )


def test_single_source_failure_keeps_its_specific_code() -> None:
    arxiv = _OutcomeSource(
        "arxiv",
        status=ModuleQueryStatus.ERROR,
        error_code="arxiv_request",
        error_message="arXiv 未接受本次查询参数（HTTP 400）；本轮未使用该来源。",
        retryable=False,
    )
    flow = PaperNodeFlow(
        source=arxiv,
        sources={"arxiv": arxiv},
        enricher=None,
        intent_analyzer=None,
    )

    results = _run_nodes(
        flow, "只允许 arXiv，帮我找几篇机器学习入门论文", (NODE_PARSE, NODE_PLAN, NODE_SEARCH)
    )

    result = results[NODE_SEARCH]
    assert result.failure is not None
    assert result.failure["code"] == "arxiv_request"
    assert result.failure["retryable"] is False
    assert [item["source"] for item in result.artifact.payload["records"]] == ["arxiv"]


def test_cancelled_sources_stop_as_cancelled_not_generic_failure() -> None:
    stop_event = Event()

    def cancel(stop: Event | None) -> None:
        assert stop is stop_event
        stop_event.set()

    arxiv = _OutcomeSource(
        "arxiv",
        status=ModuleQueryStatus.CANCELLED,
        on_search=cancel,
    )
    openalex = _OutcomeSource(
        "openalex",
        status=ModuleQueryStatus.CANCELLED,
        on_search=cancel,
    )
    flow = PaperNodeFlow(
        source=arxiv,
        sources={"arxiv": arxiv, "openalex": openalex},
        enricher=None,
        intent_analyzer=None,
        stop_event=stop_event,
    )

    results = _run_nodes(
        flow, "帮我找几篇机器学习入门论文", (NODE_PARSE, NODE_PLAN, NODE_SEARCH)
    )

    result = results[NODE_SEARCH]
    assert result.failure is not None
    assert result.failure["code"] == "paper_cancelled"
    assert result.failure["retryable"] is True
    assert [item["status"] for item in result.artifact.payload["records"]] == [
        "cancelled",
        "cancelled",
    ]


class _FailingArxivClient:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        stop_event: Event | None = None,
        deadline: float | None = None,
    ) -> list[Any]:
        del max_results, stop_event, deadline
        self.queries.append(query)
        raise ArxivMcpError(
            "arxiv_request",
            "arXiv 未接受本次查询参数（HTTP 400）；本轮未使用该来源，其他来源结果不受影响。",
            upstream_status="http_400",
            retryable=False,
        )

    def warmup(self) -> bool:
        return False

    def close(self) -> None:
        return None


def _install_multi_source(app: Any, sources: dict[str, Any]) -> PaperSearchService:
    service = PaperSearchService(
        source=sources["arxiv"],
        sources=sources,
        enricher=None,
        summarizer=None,
    )
    app.state.paper_search_service = service
    app.state.chat_service._paper_search = service  # noqa: SLF001
    return service


def test_arxiv_failure_still_delivers_verified_openalex_recommendation(
    sqlite_app: Any,  # noqa: F811
    client: TestClient,  # noqa: F811
    generation_helpers: dict[str, Any],
) -> None:
    _register(client)
    failing_client = _FailingArxivClient()
    arxiv_source = ArxivPaperSource(
        ArxivSearchService(
            client=failing_client,
            cache=ArxivResultCache(enabled=False),
            throttle=ArxivThrottle(enabled=False),
            cooldown=ArxivCooldown(enabled=False),
        )
    )
    openalex = _OutcomeSource("openalex", candidates=[_candidate()])
    _install_multi_source(
        sqlite_app, {"arxiv": arxiv_source, "openalex": openalex}
    )
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001

    conversation_id = _create_conversation(client)
    created = _send(client, conversation_id, "帮我找几篇机器学习入门论文")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id, created
    )

    assert assistant["status"] == "done"
    paper = assistant["paper_search"]
    assert paper["status"] == "success"
    assert [item["title"] for item in paper["papers"]] == [
        "Machine Learning: A Beginner's Guide"
    ]
    assert paper["papers"][0]["match_evidence"]
    assert paper["papers"][0]["source_abstract"]
    records = {item["source"]: item for item in paper["queries"]}
    assert records["arxiv"]["status"] == "error"
    assert records["arxiv"]["error_code"] == "arxiv_request"
    assert "http_400" in (records["arxiv"]["detail"] or "")
    assert records["openalex"]["status"] == "success"
    assert records["openalex"]["evidence_count"] == 1
    assert len(failing_client.queries) == 1
    assert openalex.queries == ["machine learning"]
    assert "未实际调用来源" not in assistant["content"]
