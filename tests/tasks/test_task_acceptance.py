"""Issue 08 独立验收发现的边界回归。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event

import pytest

from bridges.chat.repository import ConversationRepository
from bridges.contracts.tasks import (
    ConditionOrigin,
    ConditionScope,
    ConditionStatus,
    TaskEventKind,
    TaskRelation,
    TaskStatus,
    TaskTurnRequest,
    WaitResolution,
    WaitStatus,
)
from bridges.storage.database import BridgesDatabase
from bridges.tasks.repository import TaskRepository, TaskStateConflict, TaskVersionConflict
from bridges.tasks.service import TaskService, user_condition


@pytest.fixture
def service(tmp_path):
    database = BridgesDatabase(tmp_path / "tasks.db")
    database.initialize()
    ConversationRepository(database).create_conversation(
        account_id="a",
        conversation_id="c",
        title="验收",
        mode="companion",
        created_at=datetime.now(UTC),
    )
    yield TaskService(TaskRepository(database))
    database.close()


def turn(service, relation, **kwargs):
    message_id = kwargs.pop("user_message_id", f"u-{len(service.list_events('a', 'c'))}")
    return service.apply_turn(
        "a",
        TaskTurnRequest(
            conversation_id="c",
            user_message_id=message_id,
            relation=relation,
            **kwargs,
        ),
    )


@pytest.mark.parametrize("relation", [TaskRelation.CONTINUE, TaskRelation.REVISE])
def test_return_to_task_moves_pointer_and_pauses_other_topic(service, relation):
    old = turn(service, TaskRelation.NEW, goal="选购").task.task
    other = turn(service, TaskRelation.NEW, goal="通勤").task.task
    turn(service, relation, explicit_task_id=old.task_id)
    assert service.repository.current_task("a", "c").task_id == old.task_id
    assert service.projection("a", other.task_id).task.status == TaskStatus.PAUSED
    updated = turn(
        service,
        TaskRelation.REVISE,
        conditions=[
            user_condition(kind="budget", text="3000", source_message_id="u2"),
        ],
    )
    assert updated.task.task.task_id == old.task_id


@pytest.mark.parametrize(
    "origin", [ConditionOrigin.ASSISTANT_PROPOSAL, ConditionOrigin.MODEL_INFERENCE]
)
def test_unaccepted_replacement_keeps_user_condition_effective(service, origin):
    original = turn(
        service,
        TaskRelation.NEW,
        goal="选购",
        conditions=[
            user_condition(kind="budget", text="3000", source_message_id="u1"),
        ],
    ).task
    proposal = user_condition(
        kind="budget",
        text="5000",
        source_message_id="a1",
        replaces=original.effective_conditions[0].condition_id,
    )
    proposal.origin = origin
    result = turn(service, TaskRelation.REVISE, conditions=[proposal])
    assert [c.text for c in result.task.effective_conditions] == ["3000"]
    assert (
        service.repository.get_condition(
            "a",
            original.effective_conditions[0].condition_id,
        ).status
        == ConditionStatus.EFFECTIVE
    )


def test_revision_snapshot_retains_unchanged_conditions_and_result_refs(service):
    original = turn(
        service,
        TaskRelation.NEW,
        goal="选购",
        user_message_id="u1",
        result_refs=["result-1"],
        conditions=[
            user_condition(kind="budget", text="3000", source_message_id="u1"),
            user_condition(kind="requirement", text="轻便", source_message_id="u1"),
        ],
    ).task
    result = turn(
        service,
        TaskRelation.REVISE,
        user_message_id="u2",
        conditions=[
            user_condition(kind="budget", text="2000", source_message_id="u2"),
        ],
    ).task
    version = service.repository.get_version("a", original.task.task_id, 2)
    assert set(version.condition_ids) == {c.condition_id for c in result.effective_conditions}
    assert set(version.source_message_ids) == {"u1", "u2"}
    assert version.result_refs == ["result-1"]


def test_repository_transactions_do_not_join_another_threads_transaction(service):
    entered = Event()
    attempted = Event()
    finished = Event()
    release = Event()

    def outer():
        with pytest.raises(RuntimeError), service.repository.transaction():
            entered.set()
            assert release.wait(5)
            raise RuntimeError("回滚第一线程")

    def independent():
        assert entered.wait(5)
        attempted.set()
        with pytest.raises(RuntimeError), service.repository.transaction():
            service.repository.record_event(
                account_id="a",
                conversation_id="c",
                kind=TaskEventKind.TASK_CREATED,
            )
            raise RuntimeError("独立事务也必须整体回滚")
        finished.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(outer)
        second = pool.submit(independent)
        try:
            assert attempted.wait(5)
            assert not finished.wait(0.2)
        finally:
            release.set()
        first.result(timeout=5)
        second.result(timeout=5)
    assert service.list_events("a", "c") == []


def test_delete_conversation_removes_task_state_and_rejects_orphans(service):
    task = turn(
        service,
        TaskRelation.NEW,
        goal="选购",
        conditions=[
            user_condition(kind="budget", text="3000", source_message_id="u1"),
        ],
    ).task.task
    ConversationRepository(service.repository.database).delete_conversation("a", "c")
    assert service.projection("a", task.task_id) is None
    assert service.list_conditions("a", task.task_id) == []
    assert service.list_versions("a", task.task_id) == []
    assert service.list_events("a", "c") == []
    from bridges.tasks.repository import TaskNotFound

    with pytest.raises(TaskNotFound):
        turn(service, TaskRelation.NEW, goal="不存在的会话")


def test_partial_answer_revision_keeps_wait_matchable(service):
    turn(service, TaskRelation.NEW, goal="通勤")
    wait = service.open_wait(
        "a",
        conversation_id="c",
        question="起点终点？",
        missing_fields=["start", "end"],
        origin_message_id="q",
        source_message_id="u1",
    )
    partial = turn(
        service,
        TaskRelation.CONTINUE,
        answer_fields=["start"],
        conditions=[
            user_condition(kind="start", text="宿舍", source_message_id="u2"),
        ],
    )
    assert partial.wait_resolution == WaitResolution.REJECTED
    assert service.repository.get_wait("a", wait.wait_id).expected_version == 2
    full = turn(
        service,
        TaskRelation.CONTINUE,
        answer_fields=["start", "end"],
        conditions=[
            user_condition(kind="end", text="图书馆", source_message_id="u3"),
        ],
    )
    assert full.wait_resolution == WaitResolution.ACCEPTED
    assert service.repository.get_wait("a", wait.wait_id).status == WaitStatus.RESOLVED


def test_completion_saves_result_refs(service):
    task = turn(service, TaskRelation.NEW, goal="选购").task.task
    turn(service, TaskRelation.COMPLETE, result_refs=["artifact-1"])
    assert service.repository.get_version("a", task.task_id, 2).result_refs == ["artifact-1"]


def test_turn_replay_survives_repository_restart(service):
    first = turn(service, TaskRelation.NEW, goal="选购", user_message_id="same")
    restarted = TaskService(TaskRepository(service.repository.database))
    second = turn(restarted, TaskRelation.NEW, goal="选购", user_message_id="same")
    assert second == first
    assert len(restarted.list_projections("a", "c")) == 1
    before = len(restarted.list_versions("a", first.task.task.task_id))
    revision = turn(restarted, TaskRelation.REVISE, goal="修改", user_message_id="revision")
    assert turn(restarted, TaskRelation.REVISE, goal="修改", user_message_id="revision") == revision
    assert len(restarted.list_versions("a", first.task.task.task_id)) == before + 1


@pytest.mark.parametrize(
    "relation",
    [
        TaskRelation.CONTINUE,
        TaskRelation.COMPLETE,
        TaskRelation.PAUSE,
        TaskRelation.BLOCK,
        TaskRelation.CANCEL,
    ],
)
def test_stale_version_cannot_change_task_status(service, relation):
    task = turn(service, TaskRelation.NEW, goal="选购").task.task
    turn(service, TaskRelation.REVISE, goal="新版选购")
    with pytest.raises(TaskVersionConflict):
        turn(service, relation, expected_version=1)
    assert service.projection("a", task.task_id).task.status == TaskStatus.ACTIVE


def test_accepting_proposal_replaces_previous_effective_budget(service):
    original = turn(
        service,
        TaskRelation.NEW,
        goal="选购",
        conditions=[
            user_condition(kind="budget", text="3000", source_message_id="u1"),
        ],
    ).task
    draft = user_condition(
        kind="budget",
        text="5000",
        source_message_id="a1",
        replaces=original.effective_conditions[0].condition_id,
    )
    draft.origin = ConditionOrigin.ASSISTANT_PROPOSAL
    turn(service, TaskRelation.REVISE, conditions=[draft])
    draft_id = next(
        c.condition_id
        for c in service.list_conditions("a", original.task.task_id)
        if c.status == ConditionStatus.DRAFT
    )
    confirmed = turn(
        service,
        TaskRelation.REVISE,
        conditions=[
            user_condition(kind="budget", text="5000", source_message_id="u3", replaces=draft_id),
        ],
    )
    assert [c.text for c in confirmed.task.effective_conditions] == ["5000"]


def test_initial_snapshot_includes_conversation_condition(service):
    original = turn(
        service,
        TaskRelation.NEW,
        goal="选购",
        conditions=[
            user_condition(
                kind="language",
                text="中文",
                source_message_id="u1",
                scope=ConditionScope.CONVERSATION,
            ),
        ],
    ).task
    other = turn(service, TaskRelation.NEW, goal="通勤").task
    version = service.repository.get_version("a", other.task.task_id, 1)
    assert original.conversation_conditions[0].condition_id in version.condition_ids
    assert "u1" in version.source_message_ids


def test_reused_message_id_with_different_content_conflicts(service):
    turn(service, TaskRelation.NEW, goal="选购", user_message_id="same")
    with pytest.raises(TaskStateConflict):
        turn(service, TaskRelation.NEW, goal="通勤", user_message_id="same")
    assert len(service.list_projections("a", "c")) == 1


def test_multiple_waits_match_fields_and_preserve_other_pending_question(service):
    turn(service, TaskRelation.NEW, goal="通勤")
    first = service.open_wait(
        "a",
        conversation_id="c",
        question="起点？",
        missing_fields=["start"],
        origin_message_id="q1",
        source_message_id="u1",
    )
    second = service.open_wait(
        "a",
        conversation_id="c",
        question="终点？",
        missing_fields=["end"],
        origin_message_id="q2",
        source_message_id="u1",
    )
    answer = turn(
        service,
        TaskRelation.CONTINUE,
        answer_fields=["end"],
        conditions=[
            user_condition(kind="end", text="图书馆", source_message_id="u2"),
        ],
    )
    assert answer.resolved_wait.wait_id == second.wait_id
    assert answer.task.task.status == TaskStatus.WAITING
    assert service.repository.get_wait("a", first.wait_id).status == WaitStatus.OPEN
    assert service.repository.get_wait("a", first.wait_id).expected_version == 2
