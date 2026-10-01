"""两轴代码评审后的回归测试（工单 08）。

覆盖评审确认的缺陷与补齐项：

- 一轮消息的原子性：版本冲突不得留下「等待已解决」的半成品。
- 范围隔离：任务级条件不得取代同 ``kind`` 的会话级限制。
- 终态任务不得登记等待（已取消/已完成不悄悄恢复）。
- ``completed`` / ``blocked`` 可达，且完成目标可继续修订。
- 结果引用随版本持久化。
- 显式撤销条件且不因话题往返复活。
- 会话当前任务指针经 ``chat/`` 属主仓库写入，不直连非己表。
- 第二轮两轴评审补齐：普通聊天不改动旧任务、多字段等待整组匹配、
  ``replaces`` 不得跨任务取代、开新任务失败回滚暂停、等待→答复→完成贯通。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bridges.chat.repository import ConversationRepository
from bridges.contracts.tasks import (
    ConditionScope,
    ConditionStatus,
    TaskConditionInput,
    TaskEventKind,
    TaskRecord,
    TaskRelation,
    TaskStatus,
    TaskTurnRequest,
    WaitResolution,
    WaitStatus,
)
from bridges.storage.database import BridgesDatabase
from bridges.tasks.repository import (
    TaskError,
    TaskNotFound,
    TaskRepository,
    TaskStateConflict,
    TaskVersionConflict,
)
from bridges.tasks.service import TaskService, user_condition

_BASE = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


def _clock(step: int = 1):
    state = {"n": 0}

    def tick() -> datetime:
        state["n"] += 1
        return _BASE + timedelta(seconds=state["n"] * step)

    return tick


@pytest.fixture
def env(tmp_path: Path) -> dict[str, object]:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    now = _clock()
    ConversationRepository(database).create_conversation(
        account_id="acct-1",
        conversation_id="conv-1",
        title="选购",
        mode="companion",
        created_at=now(),
    )
    service = TaskService(TaskRepository(database))
    return {"db": database, "service": service}


def _new_task(env, **kwargs) -> object:
    return env["service"].apply_turn(  # type: ignore[union-attr]
        "acct-1",
        TaskTurnRequest(
            conversation_id="conv-1",
            user_message_id=kwargs.pop("user_message_id", "u1"),
            relation=TaskRelation.NEW,
            goal=kwargs.pop("goal", "推荐笔记本"),
            **kwargs,
        ),
    )


# -- 原子性 -----------------------------------------------------------------


def test_version_conflict_leaves_no_partial_wait_write(env) -> None:
    """乐观版本冲突必须整体回滚：等待不得被提前置为已解决。"""
    service = env["service"]
    created = _new_task(env)
    task_id = created.task.task.task_id
    wait = service.open_wait(
        "acct-1",
        conversation_id="conv-1",
        question="你的预算大概是多少？",
        missing_fields=["budget"],
        origin_message_id="a1",
        source_message_id="u1",
    )
    assert service.repository.get_task("acct-1", task_id).status == TaskStatus.WAITING

    # 本轮既补齐了字段（会命中等待），又携带过期期望版本（必然冲突）。
    with pytest.raises(TaskVersionConflict):
        service.apply_turn(
            "acct-1",
            TaskTurnRequest(
                conversation_id="conv-1",
                user_message_id="u2",
                relation=TaskRelation.REVISE,
                expected_version=0,
                answer_fields=["budget"],
                answer_text="预算 3,000",
                conditions=[
                    TaskConditionInput(kind="budget", text="预算 3,000 元", source_message_id="u2")
                ],
            ),
        )

    # 冲突后：等待仍开启、任务仍在等待态、没有产生新版本或条件。
    assert service.repository.get_wait("acct-1", wait.wait_id).status == WaitStatus.OPEN
    assert service.repository.get_task("acct-1", task_id).status == TaskStatus.WAITING
    assert [v.version for v in service.list_versions("acct-1", task_id)] == [1]
    assert service.list_conditions("acct-1", task_id) == []


# -- 范围隔离 ---------------------------------------------------------------


def test_task_condition_does_not_supersede_conversation_limit(env) -> None:
    """同 kind 的任务级条件不得取代会话级限制（验收 2）。"""
    service = env["service"]
    result = service.apply_turn(
        "acct-1",
        TaskTurnRequest(
            conversation_id="conv-1",
            user_message_id="u1",
            relation=TaskRelation.NEW,
            goal="推荐笔记本",
            conditions=[
                # 会话级限制先落地，任务级同 kind 条件紧随其后。
                user_condition(
                    kind="budget",
                    text="整个聊天都不要超过 5,000",
                    source_message_id="u1",
                    scope=ConditionScope.CONVERSATION,
                ),
                user_condition(
                    kind="budget",
                    text="本次选购预算 3,000",
                    source_message_id="u1",
                ),
            ],
        ),
    )
    task_id = result.task.task.task_id
    assert [c.text for c in result.task.conversation_conditions] == ["整个聊天都不要超过 5,000"]
    assert [c.text for c in result.task.effective_conditions] == ["本次选购预算 3,000"]

    conditions = {c.text: c for c in service.list_conditions("acct-1", task_id)}
    # 会话限制仍是有效状态，未被任务条件取代。
    conversation_limit = conditions["整个聊天都不要超过 5,000"]
    assert conversation_limit.status == ConditionStatus.EFFECTIVE
    assert conversation_limit.superseded_by is None


# -- 终态任务不登记等待 -----------------------------------------------------


def test_open_wait_rejected_on_terminal_task(env) -> None:
    """已取消/已完成的任务不得再登记澄清等待（不悄悄恢复旧等待）。"""
    service = env["service"]
    created = _new_task(env)
    task_id = created.task.task.task_id
    service.apply_turn(
        "acct-1",
        TaskTurnRequest(
            conversation_id="conv-1",
            user_message_id="u2",
            relation=TaskRelation.CANCEL,
            explicit_task_id=task_id,
        ),
    )
    with pytest.raises(TaskStateConflict):
        service.open_wait(
            "acct-1",
            conversation_id="conv-1",
            question="预算多少？",
            missing_fields=["budget"],
            origin_message_id="a1",
            source_message_id="u2",
            task_id=task_id,
        )
    assert service.repository.get_task("acct-1", task_id).status == TaskStatus.CANCELLED


# -- 完成 / 阻塞可达 --------------------------------------------------------


def test_complete_is_reachable_and_revise_reactivates(env) -> None:
    """完成目标仍可继续修订（任务内容第 7 条）。"""
    service = env["service"]
    created = _new_task(env)
    task_id = created.task.task.task_id

    completed = service.apply_turn(
        "acct-1",
        TaskTurnRequest(
            conversation_id="conv-1",
            user_message_id="u2",
            relation=TaskRelation.COMPLETE,
            explicit_task_id=task_id,
        ),
    )
    assert completed.task.task.status == TaskStatus.COMPLETED
    assert completed.task.task.completed_at is not None
    assert TaskEventKind.TASK_COMPLETED.value in completed.events

    revised = service.apply_turn(
        "acct-1",
        TaskTurnRequest(
            conversation_id="conv-1",
            user_message_id="u3",
            relation=TaskRelation.REVISE,
            explicit_task_id=task_id,
            conditions=[
                TaskConditionInput(kind="budget", text="预算 3,000 元", source_message_id="u3")
            ],
        ),
    )
    assert revised.task.task.status == TaskStatus.ACTIVE
    assert [v.version for v in service.list_versions("acct-1", task_id)] == [1, 2]


def test_blocked_task_suspends_waits_and_revise_reactivates(env) -> None:
    service = env["service"]
    created = _new_task(env)
    task_id = created.task.task.task_id
    wait = service.open_wait(
        "acct-1",
        conversation_id="conv-1",
        question="预算多少？",
        missing_fields=["budget"],
        origin_message_id="a1",
        source_message_id="u1",
    )
    blocked = service.apply_turn(
        "acct-1",
        TaskTurnRequest(
            conversation_id="conv-1",
            user_message_id="u2",
            relation=TaskRelation.BLOCK,
            explicit_task_id=task_id,
        ),
    )
    assert blocked.task.task.status == TaskStatus.BLOCKED
    assert service.repository.get_wait("acct-1", wait.wait_id).status == WaitStatus.SUSPENDED
    # 阻塞任务不接收答复。
    answer = service.apply_turn(
        "acct-1",
        TaskTurnRequest(
            conversation_id="conv-1",
            user_message_id="u3",
            relation=TaskRelation.CONTINUE,
            explicit_task_id=task_id,
            answer_fields=["budget"],
        ),
    )
    assert answer.resolved_wait is None
    assert service.repository.get_wait("acct-1", wait.wait_id).status == WaitStatus.SUSPENDED


# -- 结果引用 ---------------------------------------------------------------


def test_result_refs_persisted_on_version(env) -> None:
    """结果引用随版本快照持久化（任务内容第 2 条）。"""
    service = env["service"]
    created = _new_task(env, result_refs=["artifact-1", "run-9"])
    task_id = created.task.task.task_id
    version = service.list_versions("acct-1", task_id)[0]
    assert version.result_refs == ["artifact-1", "run-9"]


# -- 显式撤销 ---------------------------------------------------------------


def test_explicit_revoke_retires_condition_and_does_not_revive(env) -> None:
    """撤销后的条件不因话题往返复活（任务内容第 3 条）。"""
    service = env["service"]
    created = _new_task(
        env,
        conditions=[
            TaskConditionInput(kind="budget", text="预算 5,000 元", source_message_id="u1")
        ],
    )
    task_id = created.task.task.task_id
    condition_id = created.task.effective_conditions[0].condition_id

    revoked = service.apply_turn(
        "acct-1",
        TaskTurnRequest(
            conversation_id="conv-1",
            user_message_id="u2",
            relation=TaskRelation.REVISE,
            explicit_task_id=task_id,
            revoked_condition_ids=[condition_id],
        ),
    )
    assert revoked.task.effective_conditions == []
    assert (
        service.repository.get_condition("acct-1", condition_id).status == ConditionStatus.REVOKED
    )
    kinds = [e.kind for e in service.list_events("acct-1", "conv-1")]
    assert TaskEventKind.CONDITION_REVOKED.value in kinds

    # 暂停后返回：被撤销的值不复活。
    service.apply_turn(
        "acct-1",
        TaskTurnRequest(
            conversation_id="conv-1",
            user_message_id="u3",
            relation=TaskRelation.PAUSE,
            explicit_task_id=task_id,
        ),
    )
    returned = service.apply_turn(
        "acct-1",
        TaskTurnRequest(
            conversation_id="conv-1",
            user_message_id="u4",
            relation=TaskRelation.CONTINUE,
            explicit_task_id=task_id,
        ),
    )
    assert returned.task.effective_conditions == []


# -- 跨域写入经属主仓库 -----------------------------------------------------


class _SpyConversationRepository(ConversationRepository):
    """记录 ``set_current_task`` 调用的会话仓库替身。"""

    def __init__(self, database: BridgesDatabase) -> None:
        super().__init__(database)
        self.calls: list[tuple[str, str, str | None]] = []

    def set_current_task(
        self,
        account_id: str,
        conversation_id: str,
        task_id: str | None,
    ) -> None:
        self.calls.append((account_id, conversation_id, task_id))
        super().set_current_task(account_id, conversation_id, task_id)


def test_current_pointer_write_goes_through_conversation_owner(tmp_path: Path) -> None:
    """会话当前任务指针经 ``chat/`` 属主仓库写入（table-owners 纪律）。"""
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    now = _clock()
    spy = _SpyConversationRepository(database)
    spy.create_conversation(
        account_id="acct-1",
        conversation_id="conv-1",
        title="选购",
        mode="companion",
        created_at=now(),
    )
    service = TaskService(TaskRepository(database, conversations=spy))
    created = service.apply_turn(
        "acct-1",
        TaskTurnRequest(
            conversation_id="conv-1",
            user_message_id="u1",
            relation=TaskRelation.NEW,
            goal="推荐笔记本",
        ),
    )
    task_id = created.task.task.task_id
    assert spy.calls, "创建任务必须经属主仓库写入会话指针"
    assert spy.calls[-1][2] == task_id
    # 指针确实落库，且经属主仓库可读回。
    assert spy.current_task_id("acct-1", "conv-1") == task_id


# -- 第二轮评审补充 -----------------------------------------------------------


def test_lightweight_new_chatter_does_not_pause_active_task(env) -> None:
    """普通聊天（无目标、未声明换话题）不改动旧任务与旧等待。"""
    service = env["service"]
    created = _new_task(env)
    task_id = created.task.task.task_id
    wait = service.open_wait(
        "acct-1",
        conversation_id="conv-1",
        question="预算多少？",
        missing_fields=["budget"],
        origin_message_id="a1",
        source_message_id="u1",
    )

    chatter = service.apply_turn(
        "acct-1",
        TaskTurnRequest(
            conversation_id="conv-1",
            user_message_id="u2",
            relation=TaskRelation.NEW,
        ),
    )
    assert chatter.created is False
    assert chatter.paused_task_ids == []
    assert service.repository.get_task("acct-1", task_id).status == TaskStatus.WAITING
    assert service.repository.get_wait("acct-1", wait.wait_id).status == WaitStatus.OPEN
    assert service.repository.current_task("acct-1", "conv-1").task_id == task_id

    # 明确换话题（is_new_topic）仍暂停旧任务并挂起旧等待。
    switched = service.apply_turn(
        "acct-1",
        TaskTurnRequest(
            conversation_id="conv-1",
            user_message_id="u3",
            relation=TaskRelation.NEW,
            goal="校园通勤路线",
            is_new_topic=True,
        ),
    )
    assert switched.paused_task_ids == [task_id]
    assert service.repository.get_wait("acct-1", wait.wait_id).status == WaitStatus.SUSPENDED


def test_multi_field_wait_requires_all_fields(env) -> None:
    """多字段等待必须一次补齐全部字段；只答一部分不解决等待。"""
    service = env["service"]
    created = _new_task(env)
    task_id = created.task.task.task_id
    wait = service.open_wait(
        "acct-1",
        conversation_id="conv-1",
        question="预算和截止时间分别是什么？",
        missing_fields=["budget", "deadline"],
        origin_message_id="a1",
        source_message_id="u1",
    )
    partial = service.apply_turn(
        "acct-1",
        TaskTurnRequest(
            conversation_id="conv-1",
            user_message_id="u2",
            relation=TaskRelation.CONTINUE,
            explicit_task_id=task_id,
            answer_fields=["budget"],
            answer_text="预算 3,000",
        ),
    )
    assert partial.wait_resolution == WaitResolution.REJECTED
    assert service.repository.get_wait("acct-1", wait.wait_id).status == WaitStatus.OPEN
    assert service.repository.get_task("acct-1", task_id).status == TaskStatus.WAITING

    full = service.apply_turn(
        "acct-1",
        TaskTurnRequest(
            conversation_id="conv-1",
            user_message_id="u3",
            relation=TaskRelation.CONTINUE,
            explicit_task_id=task_id,
            answer_fields=["budget", "deadline"],
            answer_text="预算 3,000，周五前",
        ),
    )
    assert full.wait_resolution == WaitResolution.ACCEPTED
    resolved = service.repository.get_wait("acct-1", wait.wait_id)
    assert resolved.status == WaitStatus.RESOLVED
    assert resolved.released_at is not None
    assert service.repository.get_task("acct-1", task_id).status == TaskStatus.ACTIVE


def test_replaces_cannot_supersede_condition_of_another_task(env) -> None:
    """显式 replaces 只能取代目标任务自己的条件。"""
    service = env["service"]
    shopping = _new_task(
        env,
        conditions=[
            TaskConditionInput(kind="budget", text="预算 5,000 元", source_message_id="u1")
        ],
    )
    budget_id = shopping.task.effective_conditions[0].condition_id
    commute = _new_task(env, user_message_id="u2", goal="校园通勤路线", is_new_topic=True)
    commute_id = commute.task.task.task_id

    with pytest.raises(TaskNotFound):
        service.apply_turn(
            "acct-1",
            TaskTurnRequest(
                conversation_id="conv-1",
                user_message_id="u3",
                relation=TaskRelation.REVISE,
                explicit_task_id=commute_id,
                conditions=[
                    TaskConditionInput(
                        kind="budget",
                        text="通勤预算 3,000 元",
                        source_message_id="u3",
                        replaces=budget_id,
                    )
                ],
            ),
        )
    # 选购任务的条件保持有效，通勤任务未产生新版本或条件。
    assert service.repository.get_condition("acct-1", budget_id).status == ConditionStatus.EFFECTIVE
    assert [v.version for v in service.list_versions("acct-1", commute_id)] == [1]
    assert service.list_conditions("acct-1", commute_id) == []


class _FailingCreateRepository(TaskRepository):
    """创建任务必败的仓库替身。"""

    def create_task(
        self,
        *,
        account_id: str,
        conversation_id: str,
        goal: str,
        now: datetime | None = None,
    ) -> TaskRecord:
        raise TaskError("模拟创建任务失败")


def test_new_task_creation_failure_rolls_back_pause(env) -> None:
    """开新任务失败时，旧任务的暂停与等待挂起必须整体回滚。"""
    service = env["service"]
    created = _new_task(env)
    task_id = created.task.task.task_id
    wait = service.open_wait(
        "acct-1",
        conversation_id="conv-1",
        question="预算多少？",
        missing_fields=["budget"],
        origin_message_id="a1",
        source_message_id="u1",
    )
    failing = TaskService(_FailingCreateRepository(env["db"]))
    with pytest.raises(TaskError):
        failing.apply_turn(
            "acct-1",
            TaskTurnRequest(
                conversation_id="conv-1",
                user_message_id="u2",
                relation=TaskRelation.NEW,
                goal="校园通勤路线",
                is_new_topic=True,
            ),
        )
    assert service.repository.get_task("acct-1", task_id).status == TaskStatus.WAITING
    assert service.repository.get_wait("acct-1", wait.wait_id).status == WaitStatus.OPEN
    assert service.repository.current_task("acct-1", "conv-1").task_id == task_id


def test_wait_answer_then_complete_end_to_end(env) -> None:
    """等待→答复→完成：普通任务端到端贯通持久版本与状态。"""
    service = env["service"]
    created = _new_task(env)
    task_id = created.task.task.task_id
    # 事件在结果合同里是类型化枚举，不是散落字符串。
    assert all(isinstance(event, TaskEventKind) for event in created.events)
    wait = service.open_wait(
        "acct-1",
        conversation_id="conv-1",
        question="预算多少？",
        missing_fields=["budget"],
        origin_message_id="a1",
        source_message_id="u1",
    )
    answered = service.apply_turn(
        "acct-1",
        TaskTurnRequest(
            conversation_id="conv-1",
            user_message_id="u2",
            relation=TaskRelation.CONTINUE,
            explicit_task_id=task_id,
            answer_fields=["budget"],
            answer_text="预算 3,000",
        ),
    )
    assert answered.wait_resolution == WaitResolution.ACCEPTED
    assert answered.resolved_wait is not None
    assert answered.resolved_wait.wait_id == wait.wait_id
    assert TaskEventKind.WAIT_RESOLVED in answered.events

    completed = service.apply_turn(
        "acct-1",
        TaskTurnRequest(
            conversation_id="conv-1",
            user_message_id="u3",
            relation=TaskRelation.COMPLETE,
            explicit_task_id=task_id,
        ),
    )
    assert completed.task.task.status == TaskStatus.COMPLETED
    assert completed.task.task.completed_at is not None
    assert TaskEventKind.TASK_COMPLETED in completed.events
    # 已完成任务的历史等待仍是已解决终态，不被重开。
    assert service.repository.get_wait("acct-1", wait.wait_id).status == WaitStatus.RESOLVED
