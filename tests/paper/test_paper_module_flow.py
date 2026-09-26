"""Issue 11 端到端合同：显式模块派发、真实结果、澄清等待、失败与停止。

全部走真实 HTTP + SQLite + 后台执行器（假模型与假 arXiv 客户端，无外网）：

- 论文子图只由逐消息 ``module_id`` 或「点击建议」显式启动；未接入的模块仍被拒绝；
- 结果消息包含保留的原词、实际查询词、真实链接、选择理由、阅读顺序与证据边界；
- 孤立 Transformer 先问一项；回答后从等待状态恢复并检索相应主题；
- 检索失败保留查询词与真实分类且可重试；停止显示已停止；
- 普通聊天中的明显论文请求只给建议，不产生任何检索记录。
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.ai.adapters import StreamChunk
from bridges.arxiv_mcp.contracts import ArxivPaper
from bridges.arxiv_mcp.service import ArxivSearchService
from bridges.contracts.modules import ModuleQueryRecord, ModuleQueryStatus
from bridges.paper.service import PaperSearchService
from bridges.paper.sources import (
    ArxivPaperSource,
    CandidateSearchOutcome,
    PaperCandidate,
)
from tests.chat.test_chat_api import _create_conversation, _gateway_with, _register


def _candidate(
    arxiv_id: str,
    title: str,
    abstract: str,
    year: int = 2023,
    category: str = "cs.LG",
) -> PaperCandidate:
    return PaperCandidate(
        arxiv_id=arxiv_id,
        title=title,
        authors=["Ashish Vaswani"],
        published_at=datetime(year, 6, 12, tzinfo=UTC),
        abs_url=f"https://arxiv.org/abs/{arxiv_id}",
        pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
        abstract=abstract,
        primary_category=category,
    )


ATTENTION_CANDIDATES = [
    _candidate(
        "1706.03762",
        "Attention Is All You Need",
        "We propose the Transformer, a model relying entirely on the attention mechanism.",
        year=2017,
    ),
    _candidate(
        "2301.00774",
        "A Survey of Transformer Attention Mechanisms",
        "This survey reviews the attention mechanism in transformer models.",
        year=2023,
    ),
    _candidate(
        "2401.00001",
        "Efficient Transformer Attention for Long Documents",
        "We study efficient attention mechanism variants for transformer models.",
        year=2024,
    ),
]

DISTILLATION_CANDIDATES = [
    _candidate(
        "1503.02531",
        "Distilling the Knowledge in a Neural Network",
        "We introduce knowledge distillation, transferring knowledge to a smaller model.",
        year=2015,
    ),
    _candidate(
        "2006.05525",
        "Knowledge Distillation: A Survey",
        "This survey reviews knowledge distillation methods and applications.",
        year=2020,
    ),
    _candidate(
        "2401.00009",
        "Knowledge Distillation for Compact Language Models",
        "We apply knowledge distillation to compress language models.",
        year=2024,
    ),
]

POWER_CANDIDATE = _candidate(
    "2401.00002",
    "Power Transformer Fault Diagnosis in Substations",
    "Monitoring of power transformer and substation grid voltage.",
    year=2024,
    category="eess.SY",
)


class _FakeArxivClient:
    """可控 arXiv 客户端：返回真实结构的论文条目、抛错或在检索时请求停止。"""

    def __init__(
        self,
        papers: list[ArxivPaper] | None = None,
        error: Exception | None = None,
        *,
        stop_on_search: bool = False,
    ) -> None:
        self.papers = papers or []
        self.error = error
        self.stop_on_search = stop_on_search
        self.queries: list[str] = []
        self._lock = threading.Lock()

    def search(
        self, query: str, *, max_results: int = 5, stop_event: Any = None
    ) -> list[ArxivPaper]:
        with self._lock:
            self.queries.append(query)
        if self.error is not None:
            raise self.error
        if self.stop_on_search and stop_event is not None:
            # 模拟检索期间用户点停止：模块在下一个节点边界如实收敛为已停止。
            stop_event.set()
        return self.papers[:max_results]

    def warmup(self) -> bool:
        return False

    def close(self) -> None:
        return None


class _FakePaperSource:
    """可控论文来源：直接给出候选与统一记录，精确驱动模块各条终态路径。"""

    def __init__(
        self,
        candidates: list[PaperCandidate] | None = None,
        *,
        status: ModuleQueryStatus = ModuleQueryStatus.SUCCESS,
        error_code: str | None = None,
        error_message: str | None = None,
        retryable: bool = False,
        stop_on_search: bool = False,
    ) -> None:
        self.candidates = candidates or []
        self.status = status
        self.error_code = error_code
        self.error_message = error_message
        self.retryable = retryable
        self.stop_on_search = stop_on_search
        self.queries: list[str] = []

    def search(
        self,
        account_id: str,
        query: str,
        *,
        max_results: int,
        stop_event: Any = None,
        deadline: float | None = None,
    ) -> CandidateSearchOutcome:
        del account_id, deadline
        self.queries.append(query)
        if self.stop_on_search and stop_event is not None:
            stop_event.set()
        return CandidateSearchOutcome(
            query=query,
            candidates=self.candidates[:max_results],
            record=ModuleQueryRecord(
                source="arxiv",
                query=query,
                status=self.status,
                evidence_count=len(self.candidates[:max_results]),
                retrieved_at=datetime.now(UTC),
                error_code=self.error_code,
                error_message=self.error_message,
                retryable=self.retryable,
            ),
        )


class _StagedPaperSource:
    """两段式假来源：精确查询候选过少，放宽查询才给出足量候选。

    用于验证「稀疏领域有界放宽一次」：两次调用的统一记录都留在消息里，
    结果取自候选更多的那次，绝不凑篇数、也绝不无限重试。``relaxed_error``
    指定时第二次调用超时失败，用于验证失败的那次不会掩盖已取得的真实结果。
    """

    def __init__(
        self,
        sparse: list[PaperCandidate],
        relaxed: list[PaperCandidate],
        *,
        relaxed_error: str | None = None,
    ) -> None:
        self.sparse = sparse
        self.relaxed = relaxed
        self.relaxed_error = relaxed_error
        self.queries: list[str] = []

    def search(
        self,
        account_id: str,
        query: str,
        *,
        max_results: int,
        stop_event: Any = None,
        deadline: float | None = None,
    ) -> CandidateSearchOutcome:
        del account_id, stop_event, deadline
        self.queries.append(query)
        if len(self.queries) > 1 and self.relaxed_error is not None:
            return CandidateSearchOutcome(
                query=query,
                candidates=[],
                record=ModuleQueryRecord(
                    source="arxiv",
                    query=query,
                    status=ModuleQueryStatus.TIMEOUT,
                    evidence_count=0,
                    retrieved_at=datetime.now(UTC),
                    error_code=self.relaxed_error,
                    error_message="arXiv 搜索超时，请重试。",
                    retryable=True,
                ),
            )
        candidates = self.relaxed if len(self.queries) > 1 else self.sparse
        selected = candidates[:max_results]
        return CandidateSearchOutcome(
            query=query,
            candidates=selected,
            record=ModuleQueryRecord(
                source="arxiv",
                query=query,
                status=ModuleQueryStatus.SUCCESS,
                evidence_count=len(selected),
                retrieved_at=datetime.now(UTC),
            ),
        )


class _SilentAdapter:
    """普通聊天用的静默流式适配器（论文轮不调用模型）。"""

    def stream_call(self, capability: Any, run_context: Any, payload: dict[str, Any]) -> Any:
        del capability, run_context, payload
        yield StreamChunk(kind="delta", delta="好的。")


def _paper(
    arxiv_id: str,
    title: str,
    abstract: str,
    year: int = 2023,
    category: str = "cs.LG",
) -> ArxivPaper:
    return ArxivPaper(
        arxiv_id=arxiv_id,
        title=title,
        authors=["Ashish Vaswani"],
        published_at=datetime(year, 6, 12, tzinfo=UTC),
        abs_url=f"https://arxiv.org/abs/{arxiv_id}",
        pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
        abstract=abstract,
        primary_category=category,
    )


ATTENTION_PAPERS = [
    _paper(
        "1706.03762",
        "Attention Is All You Need",
        "We propose the Transformer, a model relying entirely on the attention mechanism.",
        year=2017,
    ),
    _paper(
        "2301.00774",
        "A Survey of Transformer Attention Mechanisms",
        "This survey reviews the attention mechanism in transformer models.",
        year=2023,
    ),
    _paper(
        "2401.00001",
        "Efficient Transformer Attention for Long Documents",
        "We study efficient attention mechanism variants for transformer models.",
        year=2024,
    ),
]

POWER_PAPER = _paper(
    "2401.00002",
    "Power Transformer Fault Diagnosis in Substations",
    "Monitoring of power transformer and substation grid voltage.",
    year=2024,
    category="eess.SY",
)


@pytest.fixture
def sqlite_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    from bridges.api.main import create_app
    from bridges.config import get_settings

    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "api-test-secret-key")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    return create_app()


@pytest.fixture
def client(sqlite_app: Any) -> TestClient:
    return TestClient(sqlite_app)


def _install_paper_service(
    app: Any,
    papers: list[ArxivPaper] | None = None,
    error: Exception | None = None,
    *,
    stop_on_search: bool = False,
) -> _FakeArxivClient:
    """把论文模块的 arXiv 客户端换成可控替身（其余编排保持真实）。"""
    fake = _FakeArxivClient(papers=papers, error=error, stop_on_search=stop_on_search)
    app.state.paper_search_service = PaperSearchService(
        source=ArxivPaperSource(ArxivSearchService(client=fake)),
        enricher=None,
        summarizer=None,
    )
    app.state.chat_service._paper_search = app.state.paper_search_service  # noqa: SLF001
    return fake


def _install_paper_source(app: Any, source: Any) -> Any:
    """把论文模块的来源换成替身（模块编排、父图派发与消息落库保持真实）。"""
    app.state.paper_search_service = PaperSearchService(
        source=source, enricher=None, summarizer=None
    )
    app.state.chat_service._paper_search = app.state.paper_search_service  # noqa: SLF001
    return source


def _send(
    client: TestClient,
    conversation_id: str,
    content: str,
    *,
    module_id: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"content": content}
    if module_id is not None:
        body["module_id"] = module_id
    response = client.post(f"/chat/conversations/{conversation_id}/messages", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def _run_and_read(
    app: Any,
    client: TestClient,
    drive: Any,
    conversation_id: str,
    created: dict[str, Any],
) -> dict[str, Any]:
    """驱动执行器到终态并返回该轮最新助手消息投影。"""
    drive(app)
    projection = client.get(f"/chat/conversations/{conversation_id}").json()
    assistant = [
        message for message in projection["messages"] if message["role"] == "assistant"
    ][-1]
    return assistant


def test_explicit_paper_module_returns_real_results_with_reasons(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """显式选择论文搜索：真实链接、选择理由、阅读顺序与证据边界。"""
    _register(client)
    _install_paper_service(sqlite_app, papers=ATTENTION_PAPERS)
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)
    created = _send(
        client, conversation_id, "Transformer 的注意力机制入门", module_id="paper"
    )
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id, created
    )

    assert assistant["status"] == "done"
    paper = assistant["paper_search"]
    assert paper["status"] == "success"
    assert paper["original_phrase"].lower() == "transformer"
    assert "transformer" in paper["final_query"].lower()
    assert paper["queries"][0]["source"] == "arxiv"
    assert paper["queries"][0]["status"] == "success"
    assert paper["queries"][0]["evidence_count"] == len(paper["papers"])
    assert assistant["content"].count("https://arxiv.org/abs/") >= 3
    orders = [item["order"] for item in paper["papers"]]
    assert orders == sorted(orders), "阅读顺序从 1 递增"
    assert all(item["reason_zh"] for item in paper["papers"])
    assert all(item["unverified"] for item in paper["papers"])
    assert paper["papers"][0]["role"] == "survey"
    assert paper["requested_count"] >= 3
    assert paper["pending"] is None
    # 原词与实际查询词都出现在正文里（用户可据此纠正）
    assert "Attention Is All You Need" in assistant["content"]


def test_isolated_transformer_asks_one_question_then_resumes(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """孤立 Transformer：先消歧，回答后从等待状态恢复并检索相应主题。"""
    _register(client)
    fake = _install_paper_source(
        sqlite_app, _FakePaperSource(candidates=ATTENTION_CANDIDATES)
    )
    conversation_id = _create_conversation(client)
    created = _send(
        client, conversation_id, "帮我找 Transformer 的论文", module_id="paper"
    )
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id, created
    )

    paper = assistant["paper_search"]
    assert paper["status"] == "clarification"
    assert paper["pending"] is not None
    assert paper["pending"]["kind"] == "clarification"
    assert assistant["content"].count("？") == 1
    assert fake.queries == [], "澄清轮绝不检索"

    # 回答后从该处恢复：原词保留、语境确定、真实检索
    resumed = _send(client, conversation_id, "机器学习方向的，入门", module_id="paper")
    assistant2 = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id, resumed
    )
    paper2 = assistant2["paper_search"]
    assert paper2["status"] == "success"
    assert paper2["context_label"] is not None
    assert paper2["original_phrase"].lower() == "transformer"
    assert fake.queries, "恢复后确实检索"
    assert paper2["papers"], "恢复后给出真实结果"


def test_waiting_state_is_persisted_across_reopen(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """等待状态随消息落库：重开历史仍能看到澄清问题与等待标记。"""
    _register(client)
    _install_paper_source(sqlite_app, _FakePaperSource())
    conversation_id = _create_conversation(client)
    created = _send(client, conversation_id, "找 Transformer 论文", module_id="paper")
    _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id, created
    )

    reopened = client.get(f"/chat/conversations/{conversation_id}").json()
    assistant = [m for m in reopened["messages"] if m["role"] == "assistant"][0]
    assert assistant["paper_search"]["pending"]["question"] == assistant["content"]
    assert assistant["paper_search"]["pending"]["module_id"] == "paper"


def test_search_failure_reports_query_and_is_retryable(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """检索失败：同一消息显示真实查询词与分类，并可按既有重试路径重试。"""
    _register(client)
    _install_paper_source(
        sqlite_app,
        _FakePaperSource(
            status=ModuleQueryStatus.TIMEOUT,
            error_code="arxiv_timeout",
            error_message="arXiv 搜索超时，请重试。",
            retryable=True,
        ),
    )
    conversation_id = _create_conversation(client)
    created = _send(
        client, conversation_id, "Transformer 的注意力机制入门", module_id="paper"
    )
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id, created
    )

    assert assistant["status"] == "error"
    assert assistant["error_code"] == "arxiv_timeout"
    assert "arXiv 搜索超时" in assistant["error_message"]
    paper = assistant["paper_search"]
    assert paper["status"] == "error"
    assert paper["final_query"]
    assert paper["queries"][0]["source"] == "arxiv"
    assert paper["queries"][0]["status"] == "timeout"
    assert paper["retryable"] is True

    # 重试：换回可用来源后同一用户消息重新派发到论文模块
    _install_paper_source(
        sqlite_app, _FakePaperSource(candidates=ATTENTION_CANDIDATES)
    )
    retried = client.post(
        f"/chat/conversations/{conversation_id}/messages/"
        f"{assistant['message_id']}/retry",
        json={},
    )
    assert retried.status_code == 200, retried.text
    new_message_id = retried.json()["assistant_message"]["message_id"]
    assert new_message_id != assistant["message_id"]
    generation_helpers["drive"](sqlite_app)
    final = client.get(f"/chat/conversations/{conversation_id}").json()
    attempt = next(m for m in final["messages"] if m["message_id"] == new_message_id)
    assert attempt["paper_search"]["status"] == "success"


def test_other_modules_still_rejected_and_no_silent_search(
    sqlite_app: Any,
    client: TestClient,
    generation_helpers: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """请求契约内但子图尚未接入的模块仍被明确拒绝；普通聊天不产生任何论文检索记录。"""
    _register(client)
    # 六个日常模块已全部接入，这里把 career 临时从可用集合摘掉，复现
    # 「请求契约合法、子图尚未接入」的构造（拒绝路径与具体模块无关）。
    monkeypatch.setattr(
        "bridges.chat.graph.AVAILABLE_MODULE_IDS",
        frozenset({"paper", "commute", "resources", "tieba", "github"}),
    )
    fake = _install_paper_source(sqlite_app, _FakePaperSource())
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)
    created = _send(client, conversation_id, "帮我推荐几个开源项目", module_id="career")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id, created
    )
    assert assistant["status"] == "error"
    assert assistant["error_code"] == "module_not_available"

    plain = _send(client, conversation_id, "你好，随便聊聊")
    plain_assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id, plain
    )
    assert plain_assistant["paper_search"] is None
    assert fake.queries == [], "普通聊天绝不暗中检索"


def test_plain_chat_suggests_paper_module_without_searching(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """普通聊天里的明显论文请求只给一键建议（原文启动，不检索）。"""
    _register(client)
    fake = _install_paper_source(
        sqlite_app, _FakePaperSource(candidates=DISTILLATION_CANDIDATES)
    )
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)
    created = _send(client, conversation_id, "帮我找几篇知识蒸馏的论文")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id, created
    )

    suggestion = assistant["module_suggestion"]
    assert suggestion is not None
    assert suggestion["module_id"] == "paper"
    assert suggestion["label"] == "使用论文搜索"
    assert suggestion["text"] == "帮我找几篇知识蒸馏的论文"
    assert fake.queries == [], "建议本身绝不发起检索"

    # 点击建议：同一用户消息以显式模块重新派发（不重复写用户消息）
    dispatched = client.post(
        f"/chat/conversations/{conversation_id}/messages/"
        f"{assistant['message_id']}/retry",
        json={"module_id": "paper"},
    )
    assert dispatched.status_code == 200, dispatched.text
    assert (
        dispatched.json()["user_message"]["message_id"]
        == created["user_message"]["message_id"]
    )
    generation_helpers["drive"](sqlite_app)
    final = client.get(f"/chat/conversations/{conversation_id}").json()
    users = [m for m in final["messages"] if m["role"] == "user"]
    assert len(users) == 1, "点击建议不得重复写用户消息"
    assert users[0]["module_id"] is None, "历史模块标识不被改写"
    latest = [m for m in final["messages"] if m["role"] == "assistant"][-1]
    assert latest["paper_search"] is not None
    assert latest["paper_search"]["status"] == "success"
    assert fake.queries, "点击建议后确实检索"


def test_module_suggestion_absent_for_ambiguous_chat(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """歧义大的普通消息不给建议（只有请求词、没有可检索主题）。"""
    _register(client)
    _install_paper_source(sqlite_app, _FakePaperSource())
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)
    created = _send(client, conversation_id, "帮我找几篇论文吧")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id, created
    )
    assert assistant["module_suggestion"] is None


def test_module_suggestion_flags_ambiguous_term(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """歧义术语仍给建议，但标记需要先消歧（点击后模块会先问一项）。"""
    _register(client)
    _install_paper_source(sqlite_app, _FakePaperSource())
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)
    created = _send(client, conversation_id, "帮我找 Transformer 的论文")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id, created
    )
    suggestion = assistant["module_suggestion"]
    assert suggestion is not None
    assert suggestion["needs_disambiguation"] is True


def test_public_query_carries_no_private_context(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """公开检索只发送最小查询词：请求里不含账户/会话/私有材料。"""
    _register(client)
    fake = _install_paper_service(sqlite_app, papers=ATTENTION_PAPERS)
    conversation_id = _create_conversation(client)
    created = _send(
        client, conversation_id, "Transformer 的注意力机制入门", module_id="paper"
    )
    _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id, created
    )
    assert fake.queries
    for query in fake.queries:
        assert conversation_id not in query
        assert "@" not in query
        assert len(query) < 200


def test_power_context_results_are_not_mixed_into_ml_query(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """语境核对：电力 transformer 结果不混进机器学习检索。"""
    _register(client)
    _install_paper_source(
        sqlite_app,
        _FakePaperSource(candidates=[POWER_CANDIDATE, *ATTENTION_CANDIDATES]),
    )
    conversation_id = _create_conversation(client)
    created = _send(
        client, conversation_id, "Transformer 的注意力机制入门", module_id="paper"
    )
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id, created
    )
    titles = [item["title"] for item in assistant["paper_search"]["papers"]]
    assert "Power Transformer Fault Diagnosis in Substations" not in titles
    assert any("Attention" in title for title in titles)


def test_stop_during_search_marks_message_stopped(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """停止：消息显示已停止并保留真实查询记录，不会变成失败。"""
    _register(client)
    fake = _install_paper_source(
        sqlite_app,
        _FakePaperSource(candidates=ATTENTION_CANDIDATES, stop_on_search=True),
    )
    conversation_id = _create_conversation(client)
    created = _send(
        client, conversation_id, "Transformer 的注意力机制入门", module_id="paper"
    )
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id, created
    )
    assert assistant["status"] == "stopped"
    assert assistant["paper_search"]["status"] == "stopped"
    assert "停止" in assistant["content"]
    assert len(fake.queries) == 1, "停止在节点边界生效，不再发起第二次检索"
    assert assistant["paper_search"]["queries"][0]["query"]


def test_topic_mismatch_stops_and_asks_for_clarification(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """主题不匹配：停止推荐并请用户澄清，不凑篇数。"""
    _register(client)
    _install_paper_source(sqlite_app, _FakePaperSource(candidates=[POWER_CANDIDATE]))
    conversation_id = _create_conversation(client)
    created = _send(
        client, conversation_id, "Transformer 的注意力机制入门", module_id="paper"
    )
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id, created
    )
    paper = assistant["paper_search"]
    assert paper["status"] == "clarification"
    assert paper["papers"] == []
    assert paper["pending"] is not None
    assert "不匹配" in assistant["content"]


def test_paper_round_never_calls_chat_model(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """论文轮不调用普通聊天模型：正文与结论只来自真实检索证据。"""
    _register(client)
    _install_paper_source(
        sqlite_app, _FakePaperSource(candidates=ATTENTION_CANDIDATES)
    )

    class _ExplodingAdapter:
        def stream_call(self, capability: Any, run_context: Any, payload: dict[str, Any]) -> Any:
            del capability, run_context, payload
            raise AssertionError("论文轮不得调用聊天模型")

    sqlite_app.state.chat_service._gateway = _gateway_with(_ExplodingAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)
    created = _send(
        client, conversation_id, "Transformer 的注意力机制入门", module_id="paper"
    )
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id, created
    )
    assert assistant["status"] == "done"
    assert assistant["paper_search"]["status"] == "success"


def test_error_does_not_leave_waiting_state_behind(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """失败轮不写入澄清等待：下一轮不会误以为在等回答。"""
    _register(client)
    _install_paper_source(
        sqlite_app,
        _FakePaperSource(
            status=ModuleQueryStatus.ERROR,
            error_code="arxiv_offline",
            error_message="当前无法连接 arXiv，请检查网络后重试。",
            retryable=True,
        ),
    )
    conversation_id = _create_conversation(client)
    created = _send(
        client, conversation_id, "Transformer 的注意力机制入门", module_id="paper"
    )
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id, created
    )
    assert assistant["paper_search"]["status"] == "error"
    assert assistant["paper_search"]["pending"] is None

def test_sparse_precise_query_relaxes_once_and_keeps_both_records(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """稀疏领域：精确查询偏少时用主词放宽一次，两条查询记录都如实保留。"""
    _register(client)
    source = _StagedPaperSource(
        sparse=ATTENTION_CANDIDATES[:1],
        relaxed=ATTENTION_CANDIDATES,
    )
    _install_paper_source(sqlite_app, source)
    conversation_id = _create_conversation(client)
    created = _send(
        client, conversation_id, "Transformer 的注意力机制入门", module_id="paper"
    )
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id, created
    )

    paper = assistant["paper_search"]
    assert len(source.queries) == 2, "精确查询偏少才放宽一次，且只放宽一次"
    relaxed_query = source.queries[1]
    assert relaxed_query.lower() in source.queries[0].lower()
    assert source.queries[0].lower().startswith(relaxed_query.lower())
    assert [record["query"] for record in paper["queries"]] == source.queries
    assert paper["status"] == "success"
    assert len(paper["papers"]) > 1, "结果取自放宽召回的那次调用"


def test_relaxed_attempt_failure_keeps_first_result_and_both_records(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """放宽检索失败不掩盖已取得的真实结果：两条记录都留着，超时如实标出。"""
    _register(client)
    source = _StagedPaperSource(
        sparse=ATTENTION_CANDIDATES[:1],
        relaxed=ATTENTION_CANDIDATES,
        relaxed_error="arxiv_timeout",
    )
    _install_paper_source(sqlite_app, source)
    conversation_id = _create_conversation(client)
    created = _send(
        client, conversation_id, "Transformer 的注意力机制入门", module_id="paper"
    )
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id, created
    )

    paper = assistant["paper_search"]
    assert len(source.queries) == 2
    statuses = [record["status"] for record in paper["queries"]]
    assert statuses[0] == "success"
    assert statuses[1] == "timeout"
    assert paper["queries"][1]["error_code"] == "arxiv_timeout"
    # 采用候选更多的那次（放宽失败返回 0 篇）：结果仍来自第一次成功调用。
    assert assistant["status"] == "done"
    assert paper["status"] == "success"
    assert [item["title"] for item in paper["papers"]] == [
        ATTENTION_CANDIDATES[0].title
    ]
