"""工单 37：三个模块的延迟终态（headless）交付合同。

- ``defer_finalization=True`` 时不写消息终态（助手消息仍是 ``streaming``），
  只返回 :class:`~bridges.contracts.modules.ModuleDelivery`；
- 交付的投影/正文/状态与非延迟路径一致，内核仍真实执行并提交产物；
- 模块级失败不再抛出，而是以 ``message_status="error"`` 的交付返回；
- 迟到/失效（superseded）仍按原样抛出。

全部直接调用服务（不起 HTTP），数据库与消息行按内核测试的最小种子构造。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from bridges.career_plan.service import CareerPlanService
from bridges.chat.repository import ConversationRepository
from bridges.contracts.modules import ModuleDelivery, ModuleQueryStatus
from bridges.contracts.workflows import RunContextEnvelope
from bridges.kernel.repository import NodeKernelRepository
from bridges.paper.service import PaperSearchService
from bridges.resources.service import LearningResourcesService
from bridges.storage.database import BridgesDatabase
from tests.career_plan.test_career_plan_kernel_acceptance import _Ports
from tests.career_plan.test_career_plan_module_flow import _FakeReader, _FakeSearchPort
from tests.paper.test_paper_module_flow import ATTENTION_CANDIDATES, _FakePaperSource
from tests.resources.test_resources_module_flow import (
    BEGINNER_BOOK,
    HANDBOOK_BOOK,
    _FakeBookSource,
    _FakeDiscoverer,
    _FakeInsightReader,
    _FakeVerifier,
)

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
ACCOUNT = "acc-37"
CONVERSATION = "conv-37"
USER_MESSAGE = "msg-user-37"
LEASE_OWNER = "worker-37"

PAPER_QUERY = "Transformer 的注意力机制入门"
RESOURCES_QUERY = "我是零基础，想学深度学习"
CAREER_QUERY = "我想找 Java 后端开发，城市南昌，经验 1-3年"

#: 跨运行不稳定或每次运行必变的字段：比较延迟交付与非延迟消息投影时忽略。
_VOLATILE = {
    "artifacts",
    "completed_at",
    "created_at",
    "retrieved_at",
    "searched_at",
    "updated_at",
}

#: 区分「显式传 None」与「未传」的哨兵（None 是部分服务的合法关闭值）。
_UNSET: Any = object()


def _database(tmp_path: Path, name: str) -> BridgesDatabase:
    database = BridgesDatabase(tmp_path / name)
    assert database.initialize() > 0
    return database


def _seed(
    database: BridgesDatabase,
    *,
    run_id: str,
    assistant_message_id: str,
    user_content: str,
) -> None:
    """最小运行种子：会话、用户消息、助手消息与持租约的运行。"""
    stamp = NOW.isoformat()
    with database.transaction():
        database.connection.execute(
            "INSERT OR IGNORE INTO conversations"
            "(conversation_id, account_id, title, mode, created_at, updated_at)"
            " VALUES (?, ?, '', 'companion', ?, ?)",
            (CONVERSATION, ACCOUNT, stamp, stamp),
        )
        database.connection.execute(
            "INSERT OR IGNORE INTO messages"
            "(message_id, conversation_id, account_id, role, status, content,"
            " created_at, updated_at)"
            " VALUES (?, ?, ?, 'user', 'done', ?, ?, ?)",
            (USER_MESSAGE, CONVERSATION, ACCOUNT, user_content, stamp, stamp),
        )
        database.connection.execute(
            "INSERT OR IGNORE INTO messages"
            "(message_id, conversation_id, account_id, role, status, content,"
            " created_at, updated_at)"
            " VALUES (?, ?, ?, 'assistant', 'streaming', '', ?, ?)",
            (assistant_message_id, CONVERSATION, ACCOUNT, stamp, stamp),
        )
        database.connection.execute(
            "INSERT OR IGNORE INTO generation_runs"
            "(run_id, account_id, conversation_id, user_message_id,"
            " assistant_message_id, status, lease_owner, lease_expires_at,"
            " stop_requested, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, 'running', ?, ?, 0, ?, ?)",
            (
                run_id,
                ACCOUNT,
                CONVERSATION,
                USER_MESSAGE,
                assistant_message_id,
                LEASE_OWNER,
                (NOW + timedelta(minutes=5)).isoformat(),
                stamp,
                stamp,
            ),
        )


def _run_context(run_id: str) -> RunContextEnvelope:
    return RunContextEnvelope(
        run_id=run_id,
        account_id=ACCOUNT,
        project_id="proj-37",
        workflow_name="daily",
        workflow_version="v1",
        submitted_at=NOW,
    )


def _emit_node(node: str, status: str, duration_ms: int | None) -> None:
    del node, status, duration_ms


def _strip_volatile(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_volatile(item)
            for key, item in value.items()
            if key not in _VOLATILE
        }
    if isinstance(value, list):
        return [_strip_volatile(item) for item in value]
    return value


def _run_paper(
    database: BridgesDatabase,
    *,
    run_id: str,
    assistant_message_id: str,
    defer: bool,
    source: Any | None = None,
) -> Any:
    service = PaperSearchService(
        source=source or _FakePaperSource(candidates=ATTENTION_CANDIDATES),
        enricher=None,
        summarizer=None,
        clock=lambda: NOW,
    )
    return service.run(
        repo=ConversationRepository(database),
        account_id=ACCOUNT,
        conversation_id=CONVERSATION,
        user_message_id=USER_MESSAGE,
        assistant_message_id=assistant_message_id,
        run_context=_run_context(run_id),
        run_model_id=None,
        emit_node=_emit_node,
        stop_event=None,
        defer_finalization=defer,
    )


def _run_resources(
    database: BridgesDatabase,
    *,
    run_id: str,
    assistant_message_id: str,
    defer: bool,
    books: Any = _UNSET,
    discoverer: Any = _UNSET,
    verifier: Any = _UNSET,
) -> Any:
    service = LearningResourcesService(
        books=(
            books
            if books is not _UNSET
            else [_FakeBookSource(candidates=[BEGINNER_BOOK, HANDBOOK_BOOK])]
        ),
        discoverer=discoverer if discoverer is not _UNSET else _FakeDiscoverer(),
        verifier=verifier if verifier is not _UNSET else _FakeVerifier(),
        insight_reader=_FakeInsightReader(),
    )
    return service.run(
        repo=ConversationRepository(database),
        account_id=ACCOUNT,
        conversation_id=CONVERSATION,
        user_message_id=USER_MESSAGE,
        assistant_message_id=assistant_message_id,
        run_context=_run_context(run_id),
        run_model_id=None,
        emit_node=_emit_node,
        stop_event=None,
        defer_finalization=defer,
    )


def _run_career(
    database: BridgesDatabase,
    *,
    run_id: str,
    assistant_message_id: str,
    defer: bool,
    service: CareerPlanService | None = None,
) -> Any:
    service = service or CareerPlanService(search=_Ports(), reader=_Ports())
    return service.run(
        repo=ConversationRepository(database),
        account_id=ACCOUNT,
        conversation_id=CONVERSATION,
        user_message_id=USER_MESSAGE,
        assistant_message_id=assistant_message_id,
        run_context=_run_context(run_id),
        emit_node=_emit_node,
        stop_event=None,
        defer_finalization=defer,
    )


def _assert_delivery_references_kernel_artifacts(
    database: BridgesDatabase, delivery: ModuleDelivery
) -> None:
    repository = NodeKernelRepository(database)
    assert delivery.artifact_refs
    rows = repository.list_artifacts(ACCOUNT, CONVERSATION)
    assert {artifact.node for artifact in rows} == set(delivery.artifact_refs)
    for artifact_id in delivery.artifact_refs.values():
        assert repository.get_artifact(ACCOUNT, artifact_id) is not None


def _assert_message_streaming(
    database: BridgesDatabase,
    assistant_message_id: str,
    projection_field: str,
) -> None:
    message = ConversationRepository(database).get_message(
        ACCOUNT, assistant_message_id
    )
    assert message is not None
    assert message.status.value == "streaming"
    assert getattr(message, projection_field) is None


def test_paper_defer_returns_delivery_and_keeps_message_streaming(
    tmp_path: Path,
) -> None:
    defer_database = _database(tmp_path, "paper-defer.db")
    _seed(
        defer_database,
        run_id="run-paper-defer",
        assistant_message_id="msg-paper-defer",
        user_content=PAPER_QUERY,
    )
    deferred = _run_paper(
        defer_database,
        run_id="run-paper-defer",
        assistant_message_id="msg-paper-defer",
        defer=True,
    )

    delivery = deferred.delivery
    assert isinstance(delivery, ModuleDelivery)
    assert delivery.module_id == "paper"
    assert delivery.projection_field == "paper_search"
    assert delivery.status == "success"
    assert delivery.message_status == "done"
    assert delivery.content
    assert delivery.wait_reason is None
    assert delivery.error_code is None
    _assert_message_streaming(defer_database, "msg-paper-defer", "paper_search")
    _assert_delivery_references_kernel_artifacts(defer_database, delivery)

    control_database = _database(tmp_path, "paper-control.db")
    _seed(
        control_database,
        run_id="run-paper-control",
        assistant_message_id="msg-paper-control",
        user_content=PAPER_QUERY,
    )
    _run_paper(
        control_database,
        run_id="run-paper-control",
        assistant_message_id="msg-paper-control",
        defer=False,
    )
    control = ConversationRepository(control_database).get_message(
        ACCOUNT, "msg-paper-control"
    )
    assert control is not None and control.status.value == "done"
    assert control.paper_search is not None
    assert _strip_volatile(delivery.projection) == _strip_volatile(
        control.paper_search
    )
    assert delivery.content == control.content
    assert delivery.status == control.paper_search["status"]


def test_paper_defer_failure_returns_error_delivery_instead_of_raising(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path, "paper-failure.db")
    _seed(
        database,
        run_id="run-paper-failure",
        assistant_message_id="msg-paper-failure",
        user_content=PAPER_QUERY,
    )
    outcome = _run_paper(
        database,
        run_id="run-paper-failure",
        assistant_message_id="msg-paper-failure",
        defer=True,
        source=_FakePaperSource(
            status=ModuleQueryStatus.TIMEOUT,
            error_code="arxiv_timeout",
            error_message="arXiv 搜索超时，请重试。",
            retryable=True,
        ),
    )

    delivery = outcome.delivery
    assert isinstance(delivery, ModuleDelivery)
    assert delivery.status == "error"
    assert delivery.message_status == "error"
    assert delivery.error_code == "arxiv_timeout"
    assert delivery.error_message == "arXiv 搜索超时，请重试。"
    assert delivery.retryable is True
    assert delivery.error_node
    _assert_message_streaming(database, "msg-paper-failure", "paper_search")


def test_resources_defer_returns_delivery_and_keeps_message_streaming(
    tmp_path: Path,
) -> None:
    defer_database = _database(tmp_path, "resources-defer.db")
    _seed(
        defer_database,
        run_id="run-resources-defer",
        assistant_message_id="msg-resources-defer",
        user_content=RESOURCES_QUERY,
    )
    deferred = _run_resources(
        defer_database,
        run_id="run-resources-defer",
        assistant_message_id="msg-resources-defer",
        defer=True,
    )

    delivery = deferred.delivery
    assert isinstance(delivery, ModuleDelivery)
    assert delivery.module_id == "resources"
    assert delivery.projection_field == "learning_resources"
    assert delivery.status == "success"
    assert delivery.message_status == "done"
    assert delivery.content
    assert delivery.wait_reason is None
    assert delivery.error_code is None
    _assert_message_streaming(
        defer_database, "msg-resources-defer", "learning_resources"
    )
    _assert_delivery_references_kernel_artifacts(defer_database, delivery)

    control_database = _database(tmp_path, "resources-control.db")
    _seed(
        control_database,
        run_id="run-resources-control",
        assistant_message_id="msg-resources-control",
        user_content=RESOURCES_QUERY,
    )
    _run_resources(
        control_database,
        run_id="run-resources-control",
        assistant_message_id="msg-resources-control",
        defer=False,
    )
    control = ConversationRepository(control_database).get_message(
        ACCOUNT, "msg-resources-control"
    )
    assert control is not None and control.status.value == "done"
    assert control.learning_resources is not None
    assert _strip_volatile(delivery.projection) == _strip_volatile(
        control.learning_resources
    )
    assert delivery.content == control.content
    assert delivery.status == control.learning_resources["status"]


def test_resources_defer_failure_returns_error_delivery_instead_of_raising(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path, "resources-failure.db")
    _seed(
        database,
        run_id="run-resources-failure",
        assistant_message_id="msg-resources-failure",
        user_content=RESOURCES_QUERY,
    )
    outcome = _run_resources(
        database,
        run_id="run-resources-failure",
        assistant_message_id="msg-resources-failure",
        defer=True,
        books=[
            _FakeBookSource(
                status=ModuleQueryStatus.ERROR,
                error_code="openlibrary_offline",
                error_message="当前无法连接 Open Library，请检查网络后重试。",
                retryable=True,
            )
        ],
        discoverer=None,
        verifier=None,
    )

    delivery = outcome.delivery
    assert isinstance(delivery, ModuleDelivery)
    assert delivery.status == "error"
    assert delivery.message_status == "error"
    assert delivery.error_code == "openlibrary_offline"
    assert delivery.retryable is True
    _assert_message_streaming(
        database, "msg-resources-failure", "learning_resources"
    )


def test_career_defer_returns_delivery_and_keeps_message_streaming(
    tmp_path: Path,
) -> None:
    defer_database = _database(tmp_path, "career-defer.db")
    _seed(
        defer_database,
        run_id="run-career-defer",
        assistant_message_id="msg-career-defer",
        user_content=CAREER_QUERY,
    )
    deferred = _run_career(
        defer_database,
        run_id="run-career-defer",
        assistant_message_id="msg-career-defer",
        defer=True,
    )

    delivery = deferred.delivery
    assert isinstance(delivery, ModuleDelivery)
    assert delivery.module_id == "career"
    assert delivery.projection_field == "career_plan"
    assert delivery.status == "success"
    assert delivery.message_status == "done"
    assert delivery.content
    assert delivery.wait_reason is None
    assert delivery.error_code is None
    _assert_message_streaming(defer_database, "msg-career-defer", "career_plan")
    _assert_delivery_references_kernel_artifacts(defer_database, delivery)

    control_database = _database(tmp_path, "career-control.db")
    _seed(
        control_database,
        run_id="run-career-control",
        assistant_message_id="msg-career-control",
        user_content=CAREER_QUERY,
    )
    _run_career(
        control_database,
        run_id="run-career-control",
        assistant_message_id="msg-career-control",
        defer=False,
    )
    control = ConversationRepository(control_database).get_message(
        ACCOUNT, "msg-career-control"
    )
    assert control is not None and control.status.value == "done"
    assert control.career_plan is not None
    assert _strip_volatile(delivery.projection) == _strip_volatile(
        control.career_plan
    )
    assert delivery.content == control.content
    assert delivery.status == control.career_plan["status"]


def test_career_defer_failure_returns_error_delivery_instead_of_raising(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path, "career-failure.db")
    _seed(
        database,
        run_id="run-career-failure",
        assistant_message_id="msg-career-failure",
        user_content=CAREER_QUERY,
    )
    service = CareerPlanService(
        search=_FakeSearchPort(
            status=ModuleQueryStatus.ERROR,
            error_code="career_search_failed",
            error_message="岗位检索没有形成结果，请稍后重试。",
        ),
        reader=_FakeReader(),
    )
    outcome = _run_career(
        database,
        run_id="run-career-failure",
        assistant_message_id="msg-career-failure",
        defer=True,
        service=service,
    )

    delivery = outcome.delivery
    assert isinstance(delivery, ModuleDelivery)
    assert delivery.status == "error"
    assert delivery.message_status == "error"
    assert delivery.error_code == "career_search_failed"
    assert delivery.retryable is True
    _assert_message_streaming(database, "msg-career-failure", "career_plan")
