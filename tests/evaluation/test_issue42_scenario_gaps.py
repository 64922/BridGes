"""工单 42 场景缺口回归：A02、A08、A09、R07 的精确场景行为断言。

工单 42 要求 39 个 A/L/R 场景都有行为断言或真实外部门限制；本文件补上
此前只有相邻机制证据的三个场景与一个端到端恢复场景：

- A02：保留论文模块提示、正文明确问校内通勤时按正文路由通勤，历史标识不改写；
- A08：多个历史任务都可能被「继续那个」指代时只问一个澄清，不按最近模块强行归属；
- A09：视频来源失败时仍交付可核验书目与真实缺口，不凑视频、不整体抹掉书目；
- R07：用户停止不自动续跑；明确继续创建新运行并复用原任务（版本、条件、来源消息）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.contracts.chat import ChatMessageStatus
from bridges.contracts.modules import ModuleQueryRecord, ModuleQueryStatus
from bridges.contracts.tasks import TaskStatus
from bridges.resources.sources import (
    VideoDiscoveryOutcome,
)
from bridges.state_copy import CLARIFICATION_TASK_HISTORY_AMBIGUITY_TEXT
from tests.chat.test_improvement12_hybrid_entry import (
    _chat_service,
    _current_task,
    _finish_turn,
    _task,
    _understand,
)
from tests.resources.test_resources_module_flow import (
    BEGINNER_BOOK,
    HANDBOOK_BOOK,
    _FakeBookSource,
    _FakeVerifier,
    _install_resources_service,
    _run_and_read,
    _send,
)

# ---------------------------------------------------------------------------
# A02：论文提示 + 通勤正文
# ---------------------------------------------------------------------------


def test_a02_paper_hint_with_commute_body_routes_commute() -> None:
    """正文明确是校内出行时按正文路由通勤，模块提示只作历史标识保留。"""

    result = _understand(
        "我明天要从南区宿舍走到图书馆，走路大概要多久",
        requested_module_id="paper",
    )

    assert result.requested_module_id == "paper"
    assert result.actual_module_id == "commute"
    assert result.route_source.value == "body_intent"
    assert result.target_task_id is None


def test_a02_service_keeps_requested_hint_and_uses_actual_route(tmp_path: Path) -> None:
    """服务闭环：用户消息保留请求提示，助手路由记录实际通勤与请求来源。"""

    service, _ = _chat_service(tmp_path)
    conversation = service.create_conversation("alice")

    user, assistant, _ = service.start_generation(
        "alice",
        conversation.conversation_id,
        "我明天要从南区宿舍走到图书馆，走路大概要多久",
        module_id="paper",
    )

    assert user.module_id == "paper"
    assert assistant.route is not None
    assert assistant.route.requested_module_id == "paper"
    assert assistant.route.module_id == "commute"
    assert assistant.route.route_source == "body_intent"


# ---------------------------------------------------------------------------
# A08：多个历史任务的泛化继续指代
# ---------------------------------------------------------------------------


def test_a08_generic_continue_with_multiple_tasks_asks_one_clarification() -> None:
    """两个历史任务都可能被「继续那个」指代时，只问一个必要澄清。"""

    tasks = (
        _task(task_id="task-1", goal="找几篇大模型安全方向的论文"),
        _task(task_id="task-2", goal="推荐几本 Python 的入门教程"),
    )

    result = _understand("继续那个", tasks=tasks)

    assert result.target_task_id is None
    assert result.actual_module_id is None
    assert result.clarification_question == CLARIFICATION_TASK_HISTORY_AMBIGUITY_TEXT
    assert result.clarification_question is not None
    assert result.clarification_question.endswith("哪一个。")
    assert result.missing_fields == ["task"]


def test_a08_unique_task_term_resolves_continue_without_clarification() -> None:
    """继续指代能由原话唯一匹配某个任务时直接续接，不制造多余澄清。"""

    tasks = (
        _task(task_id="task-1", goal="找几篇大模型安全方向的论文"),
        _task(task_id="task-2", goal="推荐几本 Python 的入门教程"),
    )

    result = _understand("继续那个 Python 教程的任务", tasks=tasks)

    assert result.clarification_question is None
    assert result.target_task_id == "task-2"
    assert result.task_relation is not None
    assert result.task_relation.value == "continue"


# ---------------------------------------------------------------------------
# A09：视频来源失败、书目仍交付
# ---------------------------------------------------------------------------


class _FailingVideoDiscoverer:
    """视频发现固定超时：返回真实结构的失败记录，不改书目分支。"""

    def __init__(self) -> None:
        self.queries: list[str] = []

    def discover(
        self,
        account_id: str,
        query: str,
        *,
        limit: int,
        stop_event: Any = None,
        deadline: float | None = None,
    ) -> VideoDiscoveryOutcome:
        del account_id, limit, stop_event, deadline
        self.queries.append(query)
        return VideoDiscoveryOutcome(
            query=query,
            direct_pages=[],
            record=ModuleQueryRecord(
                source="tavily",
                query=query,
                status=ModuleQueryStatus.TIMEOUT,
                evidence_count=0,
                retrieved_at=datetime(2026, 10, 6, tzinfo=UTC),
                error_code="tavily_timeout",
                error_message="公网搜索超时，请稍后重试。",
                retryable=True,
            ),
        )

    def close(self) -> None:
        return None


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


def test_a09_video_timeout_keeps_books_and_reports_real_gap(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """视频发现超时：照样交付可核验书目，视频缺口如实标注且不伪造条目。"""

    from tests.chat.test_chat_api import _create_conversation, _register

    _register(client)
    book_source = _FakeBookSource(candidates=[BEGINNER_BOOK, HANDBOOK_BOOK])
    discoverer = _FailingVideoDiscoverer()
    _install_resources_service(
        sqlite_app,
        books=[book_source],
        discoverer=discoverer,
        verifier=_FakeVerifier(),
    )
    conversation_id = _create_conversation(client)
    _send(client, conversation_id, "我是零基础，想学深度学习", module_id="resources")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

    assert assistant["status"] == "done"
    resources = assistant["learning_resources"]
    assert resources["status"] == "success"
    kinds = [item["kind"] for item in resources["items"]]
    assert kinds and all(kind == "book" for kind in kinds), kinds
    assert resources["requested_videos"] > 0
    video_records = [
        record for record in resources["queries"] if record["status"] == "timeout"
    ]
    assert video_records and video_records[0]["error_code"] == "tavily_timeout"
    assert any("视频还差" in note for note in resources["evidence_notes"])
    assert all(
        not item["title"].startswith("BV") for item in resources["items"]
    )


# ---------------------------------------------------------------------------
# R07：停止不自动续跑；明确继续创建新运行并复用原任务
# ---------------------------------------------------------------------------


def _conversation_runs(service: Any, account_id: str, conversation_id: str) -> list[Any]:
    """按会话读取全部生成运行（含终态），用于证明没有自动续跑。"""

    return service._repo.database.scoped(account_id).execute(  # noqa: SLF001
        "SELECT run_id, status FROM generation_runs"
        " WHERE conversation_id = ? AND account_id = ?"
        " ORDER BY created_at, run_id",
        (conversation_id, account_id),
    ).fetchall()


def test_r07_stop_has_no_auto_continue_and_explicit_continue_reuses_task(
    tmp_path: Path,
) -> None:
    service, tasks = _chat_service(tmp_path)
    conversation = service.create_conversation("alice")
    conversation_id = conversation.conversation_id

    _, first, _ = service.start_generation(
        "alice", conversation_id, "推荐几本 Python 的入门教程"
    )
    _finish_turn(service, first)
    task = _current_task(tasks, "alice", conversation_id)
    assert task.task.status == TaskStatus.ACTIVE

    repo = service._repo  # noqa: SLF001 - 评测读正式持久化
    runs_after_first = _conversation_runs(service, "alice", conversation_id)
    assert len(runs_after_first) == 1
    first_run_id = str(runs_after_first[0]["run_id"])

    # 第二轮回合进行中被用户停止：消息收敛为 stopped、任务暂停、账本关闭。
    _, second, _ = service.start_generation(
        "alice", conversation_id, "继续推荐更多 Python 教程"
    )
    stopped = service.stop_generation("alice", conversation_id, second.message_id)
    assert stopped.status == ChatMessageStatus.STOPPED
    paused = _current_task(tasks, "alice", conversation_id)
    assert paused.task.status == TaskStatus.PAUSED
    runs_after_stop = _conversation_runs(service, "alice", conversation_id)
    assert len(runs_after_stop) == 2
    second_run_id = repo.get_run_by_message("alice", second.message_id).run_id
    assert second_run_id != first_run_id

    # 停止不自动续跑：运行停在终态，没有第三个运行被后台偷偷创建。
    assert all(
        str(row["status"]) in {"done", "stopped", "failed"} for row in runs_after_stop
    )

    # 明确继续：创建新运行、复用同一任务（目标/版本/来源消息保留）。
    _, third, _ = service.start_generation(
        "alice", conversation_id, "继续推荐更多 Python 教程"
    )
    resumed_run = repo.get_run_by_message("alice", third.message_id)
    assert resumed_run is not None
    assert resumed_run.run_id not in {first_run_id, second_run_id}
    assert resumed_run.config["task_binding"]["task_id"] == task.task.task_id
    resumed = _current_task(tasks, "alice", conversation_id)
    assert resumed.task.task_id == task.task.task_id
    assert resumed.task.status == TaskStatus.ACTIVE
    assert resumed.task.goal == task.task.goal
    assert resumed.task.current_version == task.task.current_version
    context = tasks.current_reference_context("alice", conversation_id)
    assert context is not None
    message_ids = {
        message.message_id for message in repo.list_messages("alice", conversation_id)
    }
    for message_id in context.source_message_ids:
        assert message_id in message_ids
    assert first.message_id in message_ids
