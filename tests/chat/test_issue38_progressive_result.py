"""渐进结果的正式图发布、原子快照、重放和停止守卫回归。"""

import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bridges.chat.graph import _node_invoke_subgraph_or_chat
from bridges.chat.progressive_result import ProgressiveResultPublisher
from bridges.chat.repository import ConversationRepository
from bridges.contracts.chat import ChatMessageStatus, TurnOutcome
from bridges.orchestration.contracts import CompositeStatus, StepFailure, StepResult, StepState
from bridges.orchestration.production import turn_result_for_outcome
from bridges.storage.database import BridgesDatabase
from tests.chat.test_issue37_composite_dispatch import (
    ACCOUNT,
    ASSISTANT,
    RUN,
    _deps,
    _seed,
    _seed_qualified_deliveries,
    _understanding,
)
from tests.chat.test_issue38_turn_result import _bare_service, _outcome


@pytest.fixture
def repo(tmp_path: Path):
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    _seed(database, _understanding().model_dump(mode="json"))
    yield ConversationRepository(database)
    database.close()


def _step(module: str = "paper", trust: str = "qualified") -> StepResult:
    return StepResult(step_id=module, module_id=module, state=StepState.COMPLETED,
                      trust_state=trust, summary="不应进入公开快照的证据原文")


def test_formal_graph_publishes_qualified_snapshots_before_terminal(repo):
    paper, resources = _seed_qualified_deliveries(repo.database)
    deps = _deps(repo.database, _understanding(),
                 paper_delivery=paper, resources_delivery=resources)
    _node_invoke_subgraph_or_chat(
        {"module_dispatch": "chat", "assistant_message_id": ASSISTANT},
        {"configurable": {"deps": deps}},
    )
    message = repo.get_message(ACCOUNT, ASSISTANT)
    assert message.status is ChatMessageStatus.STREAMING
    assert message.content == ""
    events = repo.list_generation_events(ACCOUNT, RUN, 0)
    assert [event.kind for event in events] == ["result", "result"]
    assert [len(event.payload["result"]["delivered"]) for event in events] == [1, 2]
    result = _bare_service(repo.database)._turn_result_projection(message, None)
    assert result.outcome is TurnOutcome.RUNNING
    assert {block.module_id for block in result.delivered} == {"paper", "resources"}
    assert all(block.detail for block in result.delivered)
    # 重放覆盖式快照，游标恢复只取尚未消费的事件，不生成新事件或附件。
    assert repo.list_generation_events(ACCOUNT, RUN, events[0].seq) == events[1:]


@pytest.mark.parametrize("reason", ["stop", "lease", "task"])
def test_progressive_rejects_late_result_and_never_publishes_draft(repo, reason):
    run = repo.get_generation_run(ACCOUNT, RUN)
    stop = threading.Event()
    publisher = ProgressiveResultPublisher(repo, run, (None, None), stop)
    publisher.publish(_step(trust="draft"))
    assert repo.list_generation_events(ACCOUNT, RUN, 0) == []
    publisher.publish(_step())
    publisher.publish(_step())
    assert len(repo.list_generation_events(ACCOUNT, RUN, 0)) == 1
    if reason == "stop":
        stop.set()
    elif reason == "lease":
        repo.database.connection.execute(
            "UPDATE generation_runs SET lease_owner = 'another' WHERE run_id = ?", (RUN,),
        )
    else:
        # 发布者的任务快照与当前任务不一致，也不允许写入。
        publisher._task_ref = ("other-task", 2)
    publisher.publish(_step("resources"))
    assert len(repo.list_generation_events(ACCOUNT, RUN, 0)) == 1
    assert "证据原文" not in str(repo.get_message(ACCOUNT, ASSISTANT).turn_result)


def test_cancelled_history_keeps_qualified_blocks_without_claiming_complete(repo):
    run = repo.get_generation_run(ACCOUNT, RUN)
    ProgressiveResultPublisher(repo, run, (None, None), None).publish(_step())
    repo.finalize_message(
        ACCOUNT, ASSISTANT, status=ChatMessageStatus.STOPPED, error_code=None,
        error_message=None, duration_ms=12, model_id=None, updated_at=datetime.now(UTC),
    )
    result = _bare_service(repo.database)._turn_result_projection(
        repo.get_message(ACCOUNT, ASSISTANT), None,
    )
    assert result.outcome is TurnOutcome.CANCELLED
    assert len(result.delivered) == 1
    assert result.recovery is None


def test_composite_public_failure_does_not_copy_provider_secrets():
    result = turn_result_for_outcome(_outcome(
        CompositeStatus.FAILED,
        StepResult(step_id="github", module_id="github", state=StepState.FAILED,
                   failure=StepFailure(code="module_error", message="token=secret-38"),
                   blocked_reason="token=secret-38"),
        blocked=["token=secret-38"],
    ))
    assert "secret-38" not in result.model_dump_json()
    assert result.blocked and result.gaps


def test_event_write_failure_rolls_back_snapshot_and_can_retry(repo, monkeypatch):
    run = repo.get_generation_run(ACCOUNT, RUN)
    publisher = ProgressiveResultPublisher(repo, run, (None, None), None)
    append = repo.append_generation_event_in_transaction

    def fail(*args):
        raise RuntimeError("注入事件写入失败")

    monkeypatch.setattr(repo, "append_generation_event_in_transaction", fail)
    with pytest.raises(RuntimeError, match="注入事件写入失败"):
        publisher.publish(_step())
    assert repo.get_message(ACCOUNT, ASSISTANT).turn_result is None
    assert repo.list_generation_events(ACCOUNT, RUN, 0) == []
    monkeypatch.setattr(repo, "append_generation_event_in_transaction", append)
    publisher.publish(_step())
    assert len(repo.list_generation_events(ACCOUNT, RUN, 0)) == 1
