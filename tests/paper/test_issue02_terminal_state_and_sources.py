"""工单 02：论文终态、重试与真实来源的回归测试。

覆盖失败终态不再残留 arXiv 恢复卡、模块声明的可重试性传播到消息与
错误事件、失败/停止投影保留实际调用来源、无实际调用时不伪造来源，
以及历史终态消息的投影收敛。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.arxiv_mcp.contracts import ArxivSearchProjection, ArxivSearchStatus
from bridges.chat.repository import MessageRecord
from bridges.config import get_settings
from bridges.contracts.chat import ChatMessageRole, ChatMessageStatus
from bridges.contracts.modules import ModuleQueryRecord, ModuleQueryStatus
from bridges.paper.contracts import (
    PaperResearchConcept,
    PaperSearchProjection,
    PaperSearchStatus,
    PaperTermAnalysis,
)
from bridges.paper.service import PaperSearchService
from tests.chat.test_chat_api import (
    _create_conversation,
    _gateway_with,
    _parse_sse,
    _register,
)
from tests.paper.test_paper_module_flow import (
    ATTENTION_CANDIDATES,
    _FakePaperSource,
    _install_paper_source,
    _run_and_read,
    _send,
    _SilentAdapter,
)


@pytest.fixture
def sqlite_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    from bridges.api.main import create_app

    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "api-test-secret-key")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    return create_app()


@pytest.fixture
def client(sqlite_app: Any) -> TestClient:
    return TestClient(sqlite_app)


def _prepare(app: Any) -> None:
    app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001


class _ConceptAnalyzer:
    """把请求解析成带研究概念的查询，让专业角色参与语义核验�?"""

    def __init__(self, sources: tuple[str, ...] = ("arxiv",)) -> None:
        self._sources = list(sources)

    def analyze(self, content: str, **_kwargs: Any) -> PaperTermAnalysis:
        return PaperTermAnalysis(
            original_phrase=content,
            original_request=content,
            normalized_term=content,
            confidence=1.0,
            concepts=[
                PaperResearchConcept(
                    concept="attention mechanism",
                    source_phrase=content,
                    kind="method",
                    english_search_terms=["attention mechanism"],
                )
            ],
            recommended_sources=self._sources,
            final_query="transformer attention",
        )


def _install_judge(app: Any, judge: Any) -> None:
    app.state.paper_search_service = PaperSearchService(
        source=_FakePaperSource(candidates=ATTENTION_CANDIDATES),
        enricher=None,
        summarizer=None,
        judge=judge,
        gateway=object(),
        intent_analyzer_factory=lambda *_args, **_kwargs: _ConceptAnalyzer(),
    )
    app.state.chat_service._paper_search = app.state.paper_search_service  # noqa: SLF001


def _error_events(
    client: TestClient,
    conversation_id: str,
    message_id: str,
) -> list[dict[str, Any]]:
    text = client.get(
        f"/chat/conversations/{conversation_id}/messages/{message_id}/events"
    ).text
    return [data for name, data in _parse_sse(text) if name == "error"]


def test_failed_paper_retry_does_not_recreate_arxiv_loading_card(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """失败后的重试只启动论文模块，不再写入旧的 arXiv loading 卡。"""
    _register(client)
    _prepare(sqlite_app)
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
    first = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id, created
    )

    assert first["status"] == "error"
    assert first["paper_search"]["retryable"] is True
    assert first["paper_search"]["selected_sources"] == ["arxiv"]
    assert first["arxiv_search"] is None
    assert first["turn_result"]["outcome"] == "failed"
    assert first["turn_result"]["recovery"]["retryable"] is True
    errors = _error_events(client, conversation_id, first["message_id"])
    assert errors and errors[-1]["error"]["retryable"] is True

    _install_paper_source(
        sqlite_app, _FakePaperSource(candidates=ATTENTION_CANDIDATES)
    )
    retried = client.post(
        f"/chat/conversations/{conversation_id}/messages/"
        f"{first['message_id']}/retry",
        json={},
    )
    assert retried.status_code == 200, retried.text
    new_message = retried.json()["assistant_message"]
    assert new_message["arxiv_search"] is None, "重试响应不得携带旧 arXiv 恢复卡"

    generation_helpers["drive"](sqlite_app)
    final = client.get(f"/chat/conversations/{conversation_id}").json()
    attempt = next(
        message
        for message in final["messages"]
        if message["message_id"] == new_message["message_id"]
    )
    assert attempt["status"] == "done"
    assert attempt["arxiv_search"] is None
    assert attempt["paper_search"]["status"] == "success"
    assert attempt["paper_search"]["selected_sources"] == ["arxiv"]


def test_semantic_evaluation_failure_keeps_retry_and_actual_sources(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """语义筛选失败：消息、错误事件与回合结果都保留真实可重试性。"""

    class FailedJudge:
        def judge_many(self, *_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("budget exhausted")

    _register(client)
    _prepare(sqlite_app)
    _install_judge(sqlite_app, FailedJudge())
    conversation_id = _create_conversation(client)
    created = _send(
        client, conversation_id, "Transformer 的注意力机制入门", module_id="paper"
    )
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id, created
    )

    assert assistant["status"] == "error"
    assert assistant["error_code"] == "paper_semantic_evaluation_unavailable"
    paper = assistant["paper_search"]
    assert paper["status"] == "error"
    assert paper["retryable"] is True
    assert paper["selected_sources"] == ["arxiv"]
    assert assistant["turn_result"]["outcome"] == "failed"
    assert assistant["turn_result"]["recovery"]["retryable"] is True

    errors = _error_events(client, conversation_id, assistant["message_id"])
    assert errors[-1]["error"]["code"] == "paper_semantic_evaluation_unavailable"
    assert errors[-1]["error"]["retryable"] is True
    assert errors[-1]["paper_search"]["selected_sources"] == ["arxiv"]


def test_non_retryable_paper_failure_has_no_clickable_retry(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """不可恢复的来源失败保持失败类别，不伪装成可点击重试。"""
    _register(client)
    _prepare(sqlite_app)
    _install_paper_source(
        sqlite_app,
        _FakePaperSource(
            status=ModuleQueryStatus.ERROR,
            error_code="paper_source_unavailable",
            error_message="当前来源设置下没有可执行的论文来源。",
            retryable=False,
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
    assert assistant["error_code"] == "paper_source_unavailable"
    assert assistant["paper_search"]["retryable"] is False
    assert assistant["turn_result"]["recovery"]["retryable"] is False
    errors = _error_events(client, conversation_id, assistant["message_id"])
    assert errors[-1]["error"]["retryable"] is False
    assert errors[-1]["paper_search"]["selected_sources"] == ["arxiv"]


def test_analysis_failure_before_search_keeps_no_sources(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """分析失败发生在调用来源之前，不能伪造实际来源。"""

    class _UnavailableAnalyzer:
        def analyze(self, *_args: Any, **_kwargs: Any) -> None:
            return None

    _register(client)
    _prepare(sqlite_app)
    sqlite_app.state.paper_search_service = PaperSearchService(
        source=_FakePaperSource(status=ModuleQueryStatus.SUCCESS),
        enricher=None,
        summarizer=None,
        gateway=object(),
        intent_analyzer_factory=lambda *_args, **_kwargs: _UnavailableAnalyzer(),
    )
    sqlite_app.state.chat_service._paper_search = (  # noqa: SLF001
        sqlite_app.state.paper_search_service
    )
    conversation_id = _create_conversation(client)
    created = _send(
        client, conversation_id, "Transformer 的注意力机制入门", module_id="paper"
    )
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id, created
    )

    assert assistant["status"] == "error"
    assert assistant["error_code"] == "paper_intent_analysis_failed"
    assert assistant["paper_search"]["queries"] == []
    assert assistant["paper_search"]["selected_sources"] == []
    assert assistant["paper_search"]["retryable"] is True
    errors = _error_events(client, conversation_id, assistant["message_id"])
    assert errors[-1]["paper_search"]["selected_sources"] == []


def test_single_source_failure_keeps_other_source_and_actual_sources(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """一个来源失败时，另一个来源仍交付，并如实列出两者。"""

    class _PassingJudge:
        def judge_many(self, _analysis: Any, candidates: Any) -> Any:
            return {
                candidate.candidate_id: {
                    "relevant": True,
                    "evidence": [
                        {
                            "requirement": "c1",
                            "source": "title",
                            "quote": candidate.title[:160],
                        }
                    ],
                }
                for candidate in candidates
            }

    _register(client)
    _prepare(sqlite_app)
    arxiv = _FakePaperSource(candidates=ATTENTION_CANDIDATES, source="arxiv")
    openalex = _FakePaperSource(
        status=ModuleQueryStatus.ERROR,
        error_code="openalex_search_error",
        error_message="OpenAlex 本轮检索失败，其他来源仍可继续。",
        retryable=True,
        source="openalex",
    )
    sqlite_app.state.paper_search_service = PaperSearchService(
        source=arxiv,
        sources={"arxiv": arxiv, "openalex": openalex},
        enricher=None,
        summarizer=None,
        judge=_PassingJudge(),
        gateway=object(),
        intent_analyzer_factory=lambda *_args, **_kwargs: _ConceptAnalyzer(
            sources=("arxiv", "openalex")
        ),
    )
    sqlite_app.state.chat_service._paper_search = (  # noqa: SLF001
        sqlite_app.state.paper_search_service
    )
    conversation_id = _create_conversation(client)
    created = _send(
        client, conversation_id, "Transformer 的注意力机制入门", module_id="paper"
    )
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id, created
    )

    assert assistant["status"] == "done", (
        assistant.get("error_code"),
        assistant.get("error_message"),
        assistant.get("paper_search"),
    )
    assert assistant["paper_search"]["status"] == "success"
    assert set(assistant["paper_search"]["selected_sources"]) == {"arxiv", "openalex"}
    records = {
        item["source"]: item for item in assistant["paper_search"]["queries"]
    }
    assert records["arxiv"]["status"] == "success"
    assert records["openalex"]["status"] == "error"


def test_all_sources_failing_is_retryable_across_message_event_and_turn(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """来源异常的统一码必须与消息、错误事件和回合恢复保持一致。"""

    class _ExplodingSource:
        def __init__(self, source: str) -> None:
            self.source = source

        def search(self, *_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("source unavailable")

    _register(client)
    _prepare(sqlite_app)
    sqlite_app.state.paper_search_service = PaperSearchService(
        source=_ExplodingSource("arxiv"),
        enricher=None,
        summarizer=None,
        gateway=object(),
        intent_analyzer_factory=lambda *_args, **_kwargs: _ConceptAnalyzer(),
    )
    sqlite_app.state.chat_service._paper_search = (  # noqa: SLF001
        sqlite_app.state.paper_search_service
    )
    conversation_id = _create_conversation(client)
    created = _send(
        client, conversation_id, "Transformer 的注意力机制入门", module_id="paper"
    )
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id, created
    )

    assert assistant["status"] == "error"
    assert assistant["error_code"] == "arxiv_search_error"
    assert assistant["paper_search"]["retryable"] is True
    assert assistant["paper_search"]["selected_sources"] == ["arxiv"]
    assert assistant["turn_result"]["recovery"]["retryable"] is True
    errors = _error_events(client, conversation_id, assistant["message_id"])
    assert errors[-1]["error"]["code"] == "arxiv_search_error"
    assert errors[-1]["error"]["retryable"] is True


def test_stopped_paper_projection_keeps_actual_sources_without_recovery(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """用户停止不是失败：保留已调用来源，但不提供失败恢复动作。"""
    _register(client)
    _prepare(sqlite_app)
    _install_paper_source(
        sqlite_app,
        _FakePaperSource(
            candidates=ATTENTION_CANDIDATES,
            stop_on_search=True,
        ),
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
    assert assistant["paper_search"]["selected_sources"] == ["arxiv"]
    assert assistant["turn_result"]["outcome"] == "cancelled"
    assert assistant["turn_result"]["recovery"] is None


@pytest.mark.parametrize(
    ("status", "expected", "can_cancel"),
    [
        (ChatMessageStatus.ERROR, ArxivSearchStatus.ERROR, False),
        (ChatMessageStatus.STOPPED, ArxivSearchStatus.CANCELLED, False),
    ],
)
def test_terminal_message_projection_converges_stale_arxiv_card(
    sqlite_app: Any,
    client: TestClient,
    status: ChatMessageStatus,
    expected: ArxivSearchStatus,
    can_cancel: bool,
) -> None:
    """历史终态消息读取时也不再把 arXiv 卡片投影成仍在检索。"""
    account = _register(client)
    conversation_id = _create_conversation(client)
    now = datetime.now(UTC)
    repo = sqlite_app.state.chat_service._repo  # noqa: SLF001
    record = MessageRecord(
        message_id="stale-terminal-message",
        conversation_id=conversation_id,
        account_id=account["id"],
        role=ChatMessageRole.ASSISTANT,
        attempt_number=1,
        status=status,
        content="",
        thinking=None,
        error_code="stream_interrupted" if status is ChatMessageStatus.ERROR else None,
        error_message="连接中断，已保留已接收内容，可点击重试。"
        if status is ChatMessageStatus.ERROR
        else None,
        duration_ms=1,
        model_id=None,
        run_lock_id=None,
        created_at=now,
        updated_at=now,
        arxiv_search=ArxivSearchProjection(
            status=ArxivSearchStatus.RECOVERY,
            trigger_reason="用户明确要求搜索论文",
            query_summary="Transformer",
            can_cancel=True,
        ).model_dump(mode="json"),
        paper_search=PaperSearchProjection(
            status=PaperSearchStatus.SEARCHING,
            queries=[
                ModuleQueryRecord(
                    source="arxiv",
                    query="transformer attention",
                    status=ModuleQueryStatus.SUCCESS,
                    retrieved_at=now,
                ),
                ModuleQueryRecord(
                    source="openalex",
                    query="transformer attention",
                    status=ModuleQueryStatus.ERROR,
                    retrieved_at=now,
                    error_code="openalex_search_error",
                    retryable=True,
                ),
            ],
        ).model_dump(mode="json"),
    )
    repo.insert_message(record)
    assert record.paper_search is not None
    repo.update_message_paper_search(
        account["id"], record.message_id, record.paper_search, now
    )

    projection = sqlite_app.state.chat_service.message_projection(  # noqa: SLF001
        account["id"], record.message_id
    )
    again = sqlite_app.state.chat_service.message_projection(  # noqa: SLF001
        account["id"], record.message_id
    )
    assert projection is not None
    assert again is not None
    assert projection.arxiv_search is not None
    assert projection.arxiv_search.status is expected
    assert projection.arxiv_search.can_cancel is can_cancel
    assert projection.arxiv_search.can_retry is False
    assert projection.arxiv_search.searched_at == again.arxiv_search.searched_at
    assert projection.paper_search is not None
    assert projection.paper_search.status is (
        PaperSearchStatus.ERROR
        if status is ChatMessageStatus.ERROR
        else PaperSearchStatus.STOPPED
    )
    assert projection.paper_search.selected_sources == ["arxiv", "openalex"]
    assert projection.paper_search.retryable is (
        status is ChatMessageStatus.ERROR
    )
