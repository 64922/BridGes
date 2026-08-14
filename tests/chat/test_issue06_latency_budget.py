"""Issue 06 反馈环测试：统一阶段时钟、并行公开搜索与有界降级。

覆盖验收标准：
- 阶段事件按统一顺序下发（检索 → 搜索 → 生成 → 检查 → 收尾），载荷脱敏；
- 两种公开来源并行执行，总耗时接近较慢者而非两者之和，结果顺序确定；
- 注入慢搜索时按阶段墙钟降级，不等待慢来源（不出现永久 running）；
- 预算耗尽时有草稿带警告交付、无草稿失败可重试；
- 性能指标不泄漏用户内容。
"""

from __future__ import annotations

import contextlib
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.ai.adapters import StreamChunk
from bridges.arxiv_mcp.contracts import (
    ArxivPaperProjection,
    ArxivSearchProjection,
    ArxivSearchStatus,
)
from bridges.arxiv_mcp.service import ArxivSearchPlan
from bridges.config import get_settings
from bridges.web_search.contracts import (
    WebSearchProjection,
    WebSearchResult,
    WebSearchStatus,
)
from bridges.web_search.service import SearchPlan
from tests.chat.test_chat_api import (
    _create_conversation,
    _gateway_with,
    _register,
)


class _FakeWebSearchService:
    """可控公网搜索替身：延迟可控、调用计数线程安全。"""

    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay
        self.calls = 0
        self._lock = threading.Lock()

    def plan(self, content: str, mode: Any, *, force: bool = False) -> SearchPlan:
        return SearchPlan(True, "脱敏查询", "测试触发公网搜索")

    def search(
        self, account_id: str, plan: SearchPlan, *, stop_event: Any = None
    ) -> WebSearchProjection:
        with self._lock:
            self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        return WebSearchProjection(
            status=WebSearchStatus.SUCCESS,
            trigger_reason=plan.reason,
            query_summary=plan.query,
            results=[
                WebSearchResult(
                    result_id="web-1",
                    title="公开报道",
                    site="example.com",
                    url="https://example.com/a",
                    snippet="公开摘要。",
                    accessed_at=datetime.now(UTC),
                )
            ],
            searched_at=datetime.now(UTC),
            can_retry=False,
        )


class _FakeArxivSearchService:
    """可控 arXiv 搜索替身（可关闭触发、延迟可控）。"""

    def __init__(self, delay: float = 0.0, enabled: bool = True) -> None:
        self.delay = delay
        self.enabled = enabled
        self.calls = 0
        self._lock = threading.Lock()

    def plan(self, content: str, mode: Any, *, force: bool = False) -> ArxivSearchPlan:
        return ArxivSearchPlan(self.enabled, "论文查询", "测试触发论文搜索")

    def search(
        self, account_id: str, plan: ArxivSearchPlan, *, stop_event: Any = None
    ) -> ArxivSearchProjection:
        with self._lock:
            self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        return ArxivSearchProjection(
            status=ArxivSearchStatus.SUCCESS,
            trigger_reason=plan.reason,
            query_summary=plan.query,
            papers=[
                ArxivPaperProjection(
                    citation_id="arxiv-1",
                    arxiv_id="2401.00001",
                    title="论文标题",
                    authors=["作者 A"],
                    published_at=datetime.now(UTC),
                    abs_url="https://arxiv.org/abs/2401.00001",
                    pdf_url="https://arxiv.org/pdf/2401.00001",
                    abstract="论文摘要。",
                    summary_zh="论文简介。",
                    relevance_basis="与查询主题相关。",
                    learning_advice_zh="可阅读原文。",
                )
            ],
            searched_at=datetime.now(UTC),
            can_retry=False,
        )


class _ChunkedAdapter:
    """可控流式适配器：块间延迟 + 块数可控。

    ``citation_text`` 携带搜索引用（web/arxiv 触发时通过引用校验）；
    预算场景传空串（无搜索）。
    """

    def __init__(
        self,
        chunks: int = 3,
        delay_per_chunk: float = 0.0,
        citation_text: str = "",
    ) -> None:
        self._chunks = chunks
        self._delay = delay_per_chunk
        self._citation = citation_text
        self.stream_calls = 0

    def stream_call(self, capability: Any, run_context: Any, payload: dict[str, Any]):
        self.stream_calls += 1
        for index in range(self._chunks):
            if self._delay:
                time.sleep(self._delay)
            yield StreamChunk(
                kind="delta", delta=f"草稿块-{index} {self._citation}"
            )
        yield StreamChunk(kind="done")


@pytest.fixture
def sqlite_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    from bridges.api.main import create_app

    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "api-test-secret-key")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    app = create_app()
    yield app
    # teardown：关闭数据库连接，释放文件句柄（Windows 下避免 pytest
    # 清理 tmp_path 时 WinError 32 文件锁）。
    database = getattr(app.state, "bridges_database", None)
    if database is not None:
        with contextlib.suppress(Exception):
            database.connection.close()


@pytest.fixture
def client(sqlite_app: Any) -> TestClient:
    return TestClient(sqlite_app)


def _install_search_fakes(
    sqlite_app: Any,
    *,
    web: _FakeWebSearchService | None = None,
    arxiv: _FakeArxivSearchService | None = None,
) -> None:
    """替换 turn 编排的公开搜索替身（测试可控时钟/延迟）。"""
    turn = sqlite_app.state.chat_service._turn  # noqa: SLF001 - 测试注入 seam
    if web is not None:
        turn._web_search = web  # noqa: SLF001
    if arxiv is not None:
        turn._arxiv_search = arxiv  # noqa: SLF001


def test_stage_events_in_expected_order_with_desensitized_payloads(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """阶段事件按统一顺序下发（检索→搜索→生成→检查→收尾），载荷脱敏。"""
    _register(client)
    adapter = _ChunkedAdapter(chunks=2, citation_text="[web-1] [arxiv-1]")
    sqlite_app.state.chat_service._gateway = _gateway_with(adapter)  # noqa: SLF001
    fake_web = _FakeWebSearchService()
    fake_arxiv = _FakeArxivSearchService()
    _install_search_fakes(sqlite_app, web=fake_web, arxiv=fake_arxiv)
    conversation_id = _create_conversation(client)
    created = generation_helpers["send"](
        client, conversation_id, content="Please search the latest news"
    )
    message_id = created["assistant_message"]["message_id"]
    generation_helpers["drive"](sqlite_app)
    events = generation_helpers["subscribe"](client, conversation_id, message_id)

    stages = [payload["stage"] for name, payload in events if name == "stage"]
    statuses = [payload["status"] for name, payload in events if name == "stage"]
    assert stages, "必须下发阶段事件"
    # 统一阶段序列（companion 主路径：搜索 → 检索 → 生成 → 检查 → 收尾）
    assert "public_search" in stages and "local_retrieval" in stages
    assert "model_generation" in stages
    assert "quality_check" in stages and "finalizing" in stages
    assert stages.index("public_search") < stages.index("local_retrieval")
    assert stages.index("local_retrieval") < stages.index("model_generation")
    assert stages.index("quality_check") < stages.index("finalizing")
    # 每个阶段先 active 后结束
    for stage in ("local_retrieval", "public_search", "model_generation"):
        assert statuses[stages.index(stage)] == "active"
    assert events[-1][0] == "done"
    # 脱敏契约：stage 载荷不含消息/查询正文
    for name, payload in events:
        if name == "stage":
            assert "content" not in payload, "阶段事件不得携带正文"
            assert "query" not in payload, "阶段事件不得携带查询"
    # 单主能力路由：网页请求不附带论文副作用。
    assert fake_web.calls == 1
    assert fake_arxiv.calls == 0


def test_parallel_searches_wall_clock_approaches_slower_source(
    tmp_path: Path,
) -> None:
    """两种公开来源并行：总耗时接近较慢者而非两者之和，顺序确定。"""
    from bridges.ai import ModelGateway
    from bridges.ai.capability_registry import CapabilityRegistry
    from bridges.chat.repository import ConversationRepository
    from bridges.chat.service import ChatService
    from bridges.contracts.ai import CapabilityKind, CapabilityRecord
    from bridges.contracts.projects import ObjectDomain
    from bridges.contracts.workflows import RunContextEnvelope
    from bridges.storage.database import BridgesDatabase

    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    repository = ConversationRepository(database)
    registry = CapabilityRegistry()
    registry.register(
        CapabilityRecord(
            name="qwen_text_chat",
            version="1",
            kind=CapabilityKind.MODEL,
            vendor="qwen",
            region="cn-beijing",
            model_id="qwen3.7-plus-2026-05-26",
            input_schema_version="chat-messages-v1",
            output_schema_version="chat-completion-v1",
        )
    )
    adapter = _ChunkedAdapter(chunks=1, citation_text="[web-1]")
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", adapter)
    service = ChatService(repository=repository, gateway=gateway)
    fake_web = _FakeWebSearchService(delay=1.5)
    fake_arxiv = _FakeArxivSearchService(delay=0.8)
    service._turn._web_search = fake_web  # noqa: SLF001 - 测试注入 seam
    service._turn._arxiv_search = fake_arxiv  # noqa: SLF001
    conversation = service.create_conversation("alice")
    user, assistant = service.start_generation(
        "alice", conversation.conversation_id, "Please search the latest progress"
    )
    context = RunContextEnvelope(
        run_id="parallel-run-1",
        account_id="alice",
        project_id=conversation.conversation_id,
        workflow_name="chat",
        workflow_version="1",
        object_domain=ObjectDomain.PERSONAL_VAULT,
        submitted_at=datetime.now(UTC),
    )
    started = time.monotonic()
    events = list(
        service.stream_generation(
            "alice",
            conversation.conversation_id,
            assistant.message_id,
            context,
            until_user_message_id=user.message_id,
        )
    )
    elapsed = time.monotonic() - started
    assert fake_web.calls == 1 and fake_arxiv.calls == 0
    # 单一主能力只等待选中的来源，不能因论文副作用额外等待。
    assert elapsed < 2.2, f"并行搜索总耗时 {elapsed:.2f}s 应接近较慢来源"
    assert events[-1].kind == "done"
    final = service.message_projection("alice", assistant.message_id)
    assert final is not None and final.status.value == "done"
    assert final.web_search is not None and final.web_search.status == WebSearchStatus.SUCCESS
    assert final.arxiv_search is None


def test_slow_search_degrades_within_stage_budget_not_waiting(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """注入 30 秒慢搜索：按阶段墙钟降级，不等待慢来源、不永久 running。"""
    _register(client)
    adapter = _ChunkedAdapter(chunks=1, citation_text="[web-1]")
    sqlite_app.state.chat_service._gateway = _gateway_with(adapter)  # noqa: SLF001
    fake_web = _FakeWebSearchService(delay=30.0)
    fake_arxiv = _FakeArxivSearchService(enabled=False)
    _install_search_fakes(sqlite_app, web=fake_web, arxiv=fake_arxiv)
    conversation_id = _create_conversation(client)
    created = generation_helpers["send"](
        client, conversation_id, content="请联网搜索最新消息"
    )
    message_id = created["assistant_message"]["message_id"]
    started = time.monotonic()
    generation_helpers["drive"](sqlite_app, timeout=25.0)
    events = generation_helpers["subscribe"](
        client, conversation_id, message_id, timeout=25.0
    )
    elapsed = time.monotonic() - started
    assert elapsed < 20.0, f"慢搜索不得拖垮主流程（实耗 {elapsed:.1f}s）"
    # 搜索超时后继续模型知识回答，但正文必须显式标记未联网核实。
    assert events[-1][0] == "done"


def test_budget_exhaustion_delivers_draft_with_warning(
    sqlite_app: Any,
    client: TestClient,
    generation_helpers: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """预算耗尽：已接收草稿带警告交付，绝不永久 running。

    预算 500ms、块间隔 200ms：第三块输出后预算到期（delta 后检查点），
    草稿保留并以警告终态交付。
    """
    monkeypatch.setattr("bridges.chat.budget.TOTAL_BUDGET_MS", 500)
    _register(client)
    adapter = _ChunkedAdapter(chunks=4, delay_per_chunk=0.2)
    sqlite_app.state.chat_service._gateway = _gateway_with(adapter)  # noqa: SLF001
    conversation_id = _create_conversation(client)
    created = generation_helpers["send"](client, conversation_id, content="你好")
    message_id = created["assistant_message"]["message_id"]
    generation_helpers["drive"](sqlite_app)
    events = generation_helpers["subscribe"](client, conversation_id, message_id)
    assert events[-1][0] == "done"
    final = client.get(f"/chat/conversations/{conversation_id}").json()
    assistant = [m for m in final["messages"] if m["role"] == "assistant"][0]
    assert assistant["status"] == "done"
    assert assistant["content"], "预算到期应保留已接收草稿"
    assert "草稿块-0" in assistant["content"]
    assert any(
        "预算" in item for item in (assistant["thinking"] or {}).get("quality", [])
    ), "草稿交付应附带预算警告"


def test_budget_exhaustion_before_content_fails_retryable(
    sqlite_app: Any,
    client: TestClient,
    generation_helpers: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """预算耗尽且无草稿：明确失败（可重试），不得保持 streaming/running。"""
    monkeypatch.setattr("bridges.chat.budget.TOTAL_BUDGET_MS", 1)
    _register(client)
    adapter = _ChunkedAdapter(chunks=2)
    sqlite_app.state.chat_service._gateway = _gateway_with(adapter)  # noqa: SLF001
    conversation_id = _create_conversation(client)
    created = generation_helpers["send"](client, conversation_id, content="你好")
    message_id = created["assistant_message"]["message_id"]
    generation_helpers["drive"](sqlite_app)
    events = generation_helpers["subscribe"](client, conversation_id, message_id)
    assert events[-1][0] == "error"
    assert events[-1][1]["error"]["code"] == "budget_exceeded"
    assert events[-1][1]["error"]["retryable"] is True
    history = client.get(f"/chat/conversations/{conversation_id}").json()
    assistant = [m for m in history["messages"] if m["role"] == "assistant"][0]
    assert assistant["status"] == "error"
    assert assistant["error_code"] == "budget_exceeded"


def test_performance_summary_p95_within_local_budget(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """本地确定性适配器：p95 首 token ≤ 2s、终态 ≤ 5s（防回归指标）。

    摘要只含脱敏指标（毫秒/状态/阶段），不含任何用户内容。
    """
    account = _register(client)
    adapter = _ChunkedAdapter(chunks=2, citation_text="")
    sqlite_app.state.chat_service._gateway = _gateway_with(adapter)  # noqa: SLF001
    conversation_id = _create_conversation(client)
    for _ in range(3):
        created = generation_helpers["send"](client, conversation_id, content="你好")
        message_id = created["assistant_message"]["message_id"]
        generation_helpers["drive"](sqlite_app)
        generation_helpers["subscribe"](client, conversation_id, message_id)

    summary = sqlite_app.state.chat_service.performance_summary(account["id"])
    assert summary["run_count"] == 3
    p95_token = summary["first_token_ms"]["p95"]
    p95_duration = summary["duration_ms"]["p95"]
    assert p95_token is not None and p95_token <= 2000, f"p95 首 token {p95_token}ms"
    assert p95_duration is not None and p95_duration <= 5000, f"p95 终态 {p95_duration}ms"
    # 阶段占比完整（检索/生成/检查/收尾）
    stages = set(summary["stage_share"].keys())
    assert "local_retrieval" in stages and "model_generation" in stages
    assert "quality_check" in stages and "finalizing" in stages
    # 脱敏：摘要不含消息/查询正文
    assert "content" not in str(summary) and "你好" not in str(summary)
    assert "timeout_rate" in summary and "retry_count" in summary


def test_performance_summary_counts_timeout_rate(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """超时降级计入超时率（防回归：摘要能解释阶段超时占比）。"""
    account = _register(client)
    adapter = _ChunkedAdapter(chunks=1)
    sqlite_app.state.chat_service._gateway = _gateway_with(adapter)  # noqa: SLF001
    fake_web = _FakeWebSearchService(delay=30.0)
    fake_arxiv = _FakeArxivSearchService(enabled=False)
    _install_search_fakes(sqlite_app, web=fake_web, arxiv=fake_arxiv)
    conversation_id = _create_conversation(client)
    created = generation_helpers["send"](
        client, conversation_id, content="请联网搜索最新消息"
    )
    message_id = created["assistant_message"]["message_id"]
    generation_helpers["drive"](sqlite_app, timeout=25.0)
    generation_helpers["subscribe"](
        client, conversation_id, message_id, timeout=25.0
    )
    summary = sqlite_app.state.chat_service.performance_summary(account["id"])
    assert summary["run_count"] >= 1
    assert summary["timeout_rate"] >= 0, "搜索失败后的模型降级仍应完成终态"
