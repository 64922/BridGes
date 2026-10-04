"""工单 37：跨模块复合计划的聊天父图派发与一次终态提交。

模块服务以延迟交付（``defer_finalization``）替身参与：内核产物引用、
运行/可信状态、共享预算与守卫都走真实持久化路径，验证父图在一次
``finalize_message`` 中提交多个投影、正文与终态。
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from bridges.chat.graph import (
    DailyGraphSuperseded,
    _node_invoke_subgraph_or_chat,
    _node_persist_result,
    _node_verify_output,
)
from bridges.chat.repository import ConversationRepository
from bridges.chat.run_budget_ledger import (
    RunBudgetClass,
    RunBudgetLedgerRepository,
    derive_run_budget_plan,
)
from bridges.contracts.chat import ChatMessageStatus
from bridges.contracts.modules import ModuleDelivery
from bridges.contracts.understanding import MainUnderstanding
from bridges.storage.database import BridgesDatabase

ACCOUNT = "acc-37-chat"
CONVERSATION = "conv-37-chat"
RUN = "run-37-chat"
ASSISTANT = "msg-assistant-37-chat"
USER = "msg-user-37-chat"
NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


def _seed(database: BridgesDatabase, understanding: dict) -> None:
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
            " VALUES (?, ?, ?, 'assistant', 'streaming', '', ?, ?)",
            (ASSISTANT, CONVERSATION, ACCOUNT, stamp, stamp),
        )
        database.connection.execute(
            "INSERT OR IGNORE INTO generation_runs"
            "(run_id, account_id, conversation_id, user_message_id,"
            " assistant_message_id, status, lease_owner, lease_expires_at,"
            " stop_requested, config_json, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, 'running', 'worker-37', ?, 0, ?, ?, ?)",
            (
                RUN,
                ACCOUNT,
                CONVERSATION,
                USER,
                ASSISTANT,
                (NOW + timedelta(minutes=5)).isoformat(),
                json.dumps({"understanding": understanding}, ensure_ascii=False),
                stamp,
                stamp,
            ),
        )
    RunBudgetLedgerRepository(database).freeze_for_run(
        account_id=ACCOUNT,
        run_id=RUN,
        conversation_id=CONVERSATION,
        plan=derive_run_budget_plan(
            RunBudgetClass.NORMAL, deadline_at=NOW + timedelta(minutes=5)
        ),
        now=NOW,
    )


def _understanding() -> MainUnderstanding:
    return MainUnderstanding(
        user_message_id=USER,
        mode="companion",
        reason="测试复合意图",
        actual_module_id=None,
        route_source="body_intent",
        capability_list=["paper", "resources"],
        goal="找入门论文和学习资料",
    )


def _delivery(
    *,
    module_id: str,
    projection_field: str,
    topic: str,
    artifact_node: str,
    content: str,
    status: str = "success",
    wait_reason: str | None = None,
) -> ModuleDelivery:
    return ModuleDelivery(
        module_id=module_id,
        status=status,
        projection_field=projection_field,
        projection={"status": status, "topic": topic, "items": []},
        content=content,
        message_status=ChatMessageStatus.DONE.value,
        artifact_refs={artifact_node: f"art-{module_id}"},
        wait_reason=wait_reason,
    )


def _deps(database: BridgesDatabase, understanding: MainUnderstanding):
    repo = ConversationRepository(database)
    run = repo.get_generation_run(ACCOUNT, RUN)
    assert run is not None
    service = MagicMock()
    service.module_task_context.return_value = None
    service.run_model_quota.return_value = None
    service.paper_search_service.run.return_value = SimpleNamespace(
        delivery=_delivery(
            module_id="paper",
            projection_field="paper_search",
            topic="入门论文",
            artifact_node="paper.verify",
            content="论文正文：两篇入门论文。",
        ),
        wait_reason=None,
    )
    service.learning_resources_service.run.return_value = SimpleNamespace(
        delivery=_delivery(
            module_id="resources",
            projection_field="learning_resources",
            topic="入门资料",
            artifact_node="resources.verify",
            content="资料正文：一条精简路径。",
        ),
        wait_reason=None,
    )
    return SimpleNamespace(
        service=service,
        repo=repo,
        run=run,
        stop_event=None,
        emit=MagicMock(),
        emit_node=MagicMock(),
        current_node=None,
        started=time.monotonic(),
        terminal=MagicMock(),
    )


def test_composite_dispatch_commits_all_projections_once(tmp_path: Path) -> None:
    database = BridgesDatabase(tmp_path / "bridges.db")
    assert database.initialize() > 0
    _seed(database, _understanding().model_dump(mode="json"))
    deps = _deps(database, _understanding())
    state: dict = {"module_dispatch": "chat", "assistant_message_id": ASSISTANT}

    updates = _node_invoke_subgraph_or_chat(
        state, {"configurable": {"deps": deps}}
    )
    assert updates["composite_outcome"] is not None
    outcome = updates["composite_outcome"]
    assert outcome["status"] in {"completed", "partial"}
    assert {step["module_id"] for step in outcome["steps"]} == {"paper", "resources"}
    assert updates["composite_gate"]["passed"] is True
    deps.service.paper_search_service.run.assert_called_once()
    assert (
        deps.service.paper_search_service.run.call_args.kwargs["defer_finalization"]
        is True
    )
    assert (
        deps.service.learning_resources_service.run.call_args.kwargs[
            "defer_finalization"
        ]
        is True
    )

    state.update(updates)
    verified = _node_verify_output(state, {"configurable": {"deps": deps}})
    assert verified == {"composite_verified": True}
    state.update(verified)

    assert _node_persist_result(state, {"configurable": {"deps": deps}}) == {}
    message = ConversationRepository(database).get_message(ACCOUNT, ASSISTANT)
    assert message is not None
    assert message.status is ChatMessageStatus.DONE
    assert message.paper_search is not None
    assert message.learning_resources is not None
    assert "论文正文" in message.content
    assert "资料正文" in message.content
    runs = ConversationRepository(database).get_generation_run(ACCOUNT, RUN)
    assert runs is not None and runs.wait_reason is None


def test_composite_clarification_writes_wait_and_marks_needs_input(
    tmp_path: Path,
) -> None:
    database = BridgesDatabase(tmp_path / "bridges.db")
    assert database.initialize() > 0
    _seed(database, _understanding().model_dump(mode="json"))
    deps = _deps(database, _understanding())
    deps.service.paper_search_service.run.return_value = SimpleNamespace(
        delivery=_delivery(
            module_id="paper",
            projection_field="paper_search",
            topic="入门论文",
            artifact_node="paper.verify",
            content="请确认你想看的方向。",
            status="clarification",
            wait_reason="paper_clarification",
        ),
        wait_reason="paper_clarification",
    )
    state: dict = {"module_dispatch": "chat", "assistant_message_id": ASSISTANT}

    updates = _node_invoke_subgraph_or_chat(
        state, {"configurable": {"deps": deps}}
    )
    assert updates["composite_outcome"]["status"] == "needs_input"
    run = ConversationRepository(database).get_generation_run(ACCOUNT, RUN)
    assert run is not None and run.wait_reason == "paper_clarification"

    state.update(updates)
    state.update(_node_verify_output(state, {"configurable": {"deps": deps}}))
    _node_persist_result(state, {"configurable": {"deps": deps}})
    message = ConversationRepository(database).get_message(ACCOUNT, ASSISTANT)
    assert message is not None
    assert message.status is ChatMessageStatus.DONE
    assert message.paper_search is not None


def test_composite_late_write_rejected(tmp_path: Path) -> None:
    database = BridgesDatabase(tmp_path / "bridges.db")
    assert database.initialize() > 0
    _seed(database, _understanding().model_dump(mode="json"))
    deps = _deps(database, _understanding())
    state: dict = {"module_dispatch": "chat", "assistant_message_id": ASSISTANT}
    state.update(_node_invoke_subgraph_or_chat(state, {"configurable": {"deps": deps}}))
    state.update(_node_verify_output(state, {"configurable": {"deps": deps}}))
    # 助手消息已被另一执行者收敛：旧执行的复合结果整体被拒绝，不写交付。
    with database.transaction():
        database.connection.execute(
            "UPDATE messages SET status = 'done' WHERE message_id = ?",
            (ASSISTANT,),
        )

    with pytest.raises(DailyGraphSuperseded):
        _node_persist_result(state, {"configurable": {"deps": deps}})
    message = ConversationRepository(database).get_message(ACCOUNT, ASSISTANT)
    assert message is not None and message.paper_search is None
    assert message.status is ChatMessageStatus.DONE
