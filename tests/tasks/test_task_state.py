"""跨轮任务、有效条件与澄清等待的领域验收测试（改进工单 08）。

覆盖工单五条验收标准：

1. 末尾预算有来源；5,000 改 3,000 本轮即有效，话题往返不复活旧值。
2. 选购→通勤→返回选购只携带各自相关条件；明确全会话限制正确保留。
3. 旧澄清后换话题再说「好的」不误填等待；等待终态释放租约。
4. 助手提议与模型推测不能直接变成用户约束；工具事实保留自身证据来源。
5. 账户隔离、乐观版本冲突与审计可验证。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bridges.chat.repository import ConversationRepository
from bridges.contracts.tasks import (
    ConditionOrigin,
    ConditionScope,
    ConditionStatus,
    TaskConditionInput,
    TaskRelation,
    TaskStatus,
    TaskTurnRequest,
    WaitResolution,
    WaitStatus,
)
from bridges.storage.database import BridgesDatabase
from bridges.tasks.repository import (
    TaskRepository,
    TaskStateConflict,
    TaskVersionConflict,
)
from bridges.tasks.service import TaskService, user_condition

_BASE = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


def _clock(step: int = 1) -> datetime:
    """确定性时钟：每次调用前进 step 秒，保证事件顺序可断言。"""
    state = {"n": 0}

    def tick() -> datetime:
        state["n"] += 1
        return _BASE + timedelta(seconds=state["n"] * step)

    return tick


@pytest.fixture
def env(tmp_path: Path) -> dict[str, object]:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    conversations = ConversationRepository(database)
    now = _clock()
    account_id = "acct-1"
    conversation_id = "conv-1"
    conversations.create_conversation(
        account_id=account_id,
        conversation_id=conversation_id,
        title="选购",
        mode="companion",
        created_at=now(),
    )
    service = TaskService(TaskRepository(database))
    return {
        "db": database,
        "service": service,
        "account_id": account_id,
        "conversation_id": conversation_id,
    }


def _turn(env: dict[str, object], **kwargs: object) -> object:
    request = TaskTurnRequest(
        conversation_id=str(env["conversation_id"]),
        user_message_id=str(kwargs.pop("user_message_id", "msg")),
        **kwargs,
    )
    return env["service"].apply_turn(str(env["account_id"]), request)  # type: ignore[union-attr]


# -- 验收 1：末尾预算与纠正 -------------------------------------------------


def test_trailing_budget_has_source_and_latest_correction_wins(env) -> None:
    account = str(env["account_id"])
    service = env["service"]
    # 长消息末尾的预算条件：携带来源消息 ID 与原话范围。
    result = service.apply_turn(
        account,
        TaskTurnRequest(
            conversation_id="conv-1",
            user_message_id="u1",
            relation=TaskRelation.NEW,
            goal="推荐一台笔记本电脑",
            conditions=[
                TaskConditionInput(
                    kind="budget",
                    text="预算 5,000 元",
                    source_message_id="u1",
                    source_span="预算 5,000 元",
                )
            ],
        ),
    )
    assert result.created is True
    task_id = result.task.task.task_id
    effective = result.task.effective_conditions
    assert [c.text for c in effective] == ["预算 5,000 元"]
    assert effective[0].source_message_id == "u1"
    assert effective[0].source_span == "预算 5,000 元"

    # 用户本轮纠正：5,000 改 3,000，立即生效并取代旧值。
    revised = service.apply_turn(
        account,
        TaskTurnRequest(
            conversation_id="conv-1",
            user_message_id="u2",
            relation=TaskRelation.REVISE,
            conditions=[
                TaskConditionInput(
                    kind="budget",
                    text="预算 3,000 元",
                    source_message_id="u2",
                    source_span="改成 3,000",
                )
            ],
        ),
    )
    assert revised.task.task.task_id == task_id
    assert [c.text for c in revised.task.effective_conditions] == ["预算 3,000 元"]
    # 旧值只读保留，标为 superseded，且指向取代它的新条件。
    all_conditions = service.list_conditions(account, task_id)
    superseded = [c for c in all_conditions if c.status == ConditionStatus.SUPERSEDED]
    assert [c.text for c in superseded] == ["预算 5,000 元"]
    assert superseded[0].superseded_by == revised.task.effective_conditions[0].condition_id
    # 版本不可变：两个版本都保留。
    versions = service.list_versions(account, task_id)
    assert [v.version for v in versions] == [1, 2]

    # 话题往返：暂停后返回，恢复的是最新有效值 3,000，不复活 5,000。
    service.apply_turn(
        account,
        TaskTurnRequest(
            conversation_id="conv-1",
            user_message_id="u3",
            relation=TaskRelation.PAUSE,
        ),
    )
    returned = service.apply_turn(
        account,
        TaskTurnRequest(
            conversation_id="conv-1",
            user_message_id="u4",
            relation=TaskRelation.CONTINUE,
            explicit_task_id=task_id,
        ),
    )
    assert returned.task.task.status == TaskStatus.ACTIVE
    assert [c.text for c in returned.task.effective_conditions] == ["预算 3,000 元"]


# -- 验收 2：范围隔离与全会话限制 -------------------------------------------


def test_task_conditions_are_isolated_but_conversation_limits_persist(env) -> None:
    account = str(env["account_id"])
    service = env["service"]
    shopping = service.apply_turn(
        account,
        TaskTurnRequest(
            conversation_id="conv-1",
            user_message_id="u1",
            relation=TaskRelation.NEW,
            goal="推荐笔记本",
            conditions=[
                TaskConditionInput(
                    kind="budget",
                    text="预算 5,000 元",
                    source_message_id="u1",
                    source_span="预算 5,000 元",
                ),
                # 用户明示「这个聊天都不要推荐二手」→ 全会话范围。
                user_condition(
                    kind="exclusion",
                    text="不要二手",
                    source_message_id="u1",
                    scope=ConditionScope.CONVERSATION,
                ),
            ],
        ),
    )
    shopping_id = shopping.task.task.task_id

    commute = service.apply_turn(
        account,
        TaskTurnRequest(
            conversation_id="conv-1",
            user_message_id="u2",
            relation=TaskRelation.NEW,
            goal="校园通勤路线",
        ),
    )
    commute_id = commute.task.task.task_id
    assert commute_id != shopping_id
    # 通勤任务不携带选购预算。
    assert commute.task.effective_conditions == []
    # 但明示的全会话限制仍在会话内有效。
    assert [c.text for c in commute.task.conversation_conditions] == ["不要二手"]

    # 返回选购：恢复仍有效的选购条件（预算 5,000）。
    returned = service.apply_turn(
        account,
        TaskTurnRequest(
            conversation_id="conv-1",
            user_message_id="u3",
            relation=TaskRelation.CONTINUE,
            explicit_task_id=shopping_id,
        ),
    )
    assert [c.text for c in returned.task.effective_conditions] == ["预算 5,000 元"]


# -- 验收 3：等待匹配与终态释放 ---------------------------------------------


def test_topic_switch_does_not_fill_old_wait_and_cancel_releases(env) -> None:
    account = str(env["account_id"])
    service = env["service"]
    created = service.apply_turn(
        account,
        TaskTurnRequest(
            conversation_id="conv-1",
            user_message_id="u1",
            relation=TaskRelation.NEW,
            goal="推荐笔记本",
        ),
    )
    task_id = created.task.task.task_id
    wait = service.open_wait(
        account,
        conversation_id="conv-1",
        question="你的预算大概是多少？",
        missing_fields=["budget"],
        origin_message_id="a1",
        source_message_id="u1",
    )
    assert wait.status == WaitStatus.OPEN
    assert service.projection(account, task_id).task.status == TaskStatus.WAITING

    # 换话题（新任务）后旧澄清失去当前激活状态，不被填充。
    switched = service.apply_turn(
        account,
        TaskTurnRequest(
            conversation_id="conv-1",
            user_message_id="u2",
            relation=TaskRelation.NEW,
            goal="校园通勤路线",
            is_new_topic=True,
        ),
    )
    assert switched.wait_resolution == WaitResolution.REJECTED
    assert service.projection(account, task_id).open_waits == []
    assert service.repository.get_wait(account, wait.wait_id).status == WaitStatus.SUSPENDED

    # 换话题后再说「好的」（不补齐缺失字段）也不误填等待：等待已失去
    # 当前激活状态，本轮既不会被填充（NONE/REJECTED 皆非 ACCEPTED）。
    answer = service.apply_turn(
        account,
        TaskTurnRequest(
            conversation_id="conv-1",
            user_message_id="u3",
            relation=TaskRelation.CONTINUE,
            explicit_task_id=task_id,
            answer_fields=[],
            answer_text="好的",
        ),
    )
    assert answer.wait_resolution in {WaitResolution.NONE, WaitResolution.REJECTED}
    assert answer.resolved_wait is None
    assert service.repository.get_wait(account, wait.wait_id).status == WaitStatus.SUSPENDED

    # 取消任务：等待进入终态并释放资源（released_at 非空），不再被填充。
    cancelled = service.apply_turn(
        account,
        TaskTurnRequest(
            conversation_id="conv-1",
            user_message_id="u4",
            relation=TaskRelation.CANCEL,
            explicit_task_id=task_id,
        ),
    )
    assert cancelled.task.task.status == TaskStatus.CANCELLED
    terminal = service.repository.get_wait(account, wait.wait_id)
    assert terminal.status == WaitStatus.EXPIRED
    assert terminal.released_at is not None


def test_matching_answer_fills_wait_and_restores_active(env) -> None:
    account = str(env["account_id"])
    service = env["service"]
    created = service.apply_turn(
        account,
        TaskTurnRequest(
            conversation_id="conv-1",
            user_message_id="u1",
            relation=TaskRelation.NEW,
            goal="推荐笔记本",
        ),
    )
    task_id = created.task.task.task_id
    wait = service.open_wait(
        account,
        conversation_id="conv-1",
        question="你的预算大概是多少？",
        missing_fields=["budget"],
        origin_message_id="a1",
        source_message_id="u1",
    )
    answered = service.apply_turn(
        account,
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
    assert answered.resolved_wait.wait_id == wait.wait_id
    assert answered.resolved_wait.released_at is not None
    assert answered.task.task.status == TaskStatus.ACTIVE


def test_open_wait_rejects_unmatched_reply_and_stays_open(env) -> None:
    """旧澄清仍开启时，未补齐字段的答复不误填等待。"""
    account = str(env["account_id"])
    service = env["service"]
    created = service.apply_turn(
        account,
        TaskTurnRequest(
            conversation_id="conv-1",
            user_message_id="u1",
            relation=TaskRelation.NEW,
            goal="推荐笔记本",
        ),
    )
    task_id = created.task.task.task_id
    wait = service.open_wait(
        account,
        conversation_id="conv-1",
        question="你的预算大概是多少？",
        missing_fields=["budget"],
        origin_message_id="a1",
        source_message_id="u1",
    )
    reply = service.apply_turn(
        account,
        TaskTurnRequest(
            conversation_id="conv-1",
            user_message_id="u2",
            relation=TaskRelation.CONTINUE,
            explicit_task_id=task_id,
            answer_fields=[],
            answer_text="好的",
        ),
    )
    assert reply.wait_resolution == WaitResolution.REJECTED
    assert service.repository.get_wait(account, wait.wait_id).status == WaitStatus.OPEN


def test_profile_command_and_learning_action_do_not_fill_wait(env) -> None:
    account = str(env["account_id"])
    service = env["service"]
    created = service.apply_turn(
        account,
        TaskTurnRequest(
            conversation_id="conv-1",
            user_message_id="u1",
            relation=TaskRelation.NEW,
            goal="推荐笔记本",
        ),
    )
    task_id = created.task.task.task_id
    service.open_wait(
        account,
        conversation_id="conv-1",
        question="你的预算大概是多少？",
        missing_fields=["budget"],
        origin_message_id="a1",
        source_message_id="u1",
    )
    for flag in ("is_profile_command", "is_learning_action"):
        result = service.apply_turn(
            account,
            TaskTurnRequest(
                conversation_id="conv-1",
                user_message_id="u2",
                relation=TaskRelation.CONTINUE,
                explicit_task_id=task_id,
                answer_fields=["budget"],
                **{flag: True},
            ),
        )
        assert result.wait_resolution == WaitResolution.REJECTED


# -- 验收 4：来源分级 -------------------------------------------------------


def test_origin_grading_keeps_proposals_and_inferences_out_of_constraints(env) -> None:
    account = str(env["account_id"])
    service = env["service"]
    result = service.apply_turn(
        account,
        TaskTurnRequest(
            conversation_id="conv-1",
            user_message_id="u1",
            relation=TaskRelation.NEW,
            goal="推荐笔记本",
            conditions=[
                TaskConditionInput(
                    kind="budget",
                    text="预算 5,000 元",
                    source_message_id="u1",
                    origin=ConditionOrigin.USER_STATED,
                ),
                TaskConditionInput(
                    kind="brand",
                    text="助手建议优先 ThinkPad",
                    source_message_id="a1",
                    origin=ConditionOrigin.ASSISTANT_PROPOSAL,
                ),
                TaskConditionInput(
                    kind="usage",
                    text="推测你可能偏重续航",
                    source_message_id="u1",
                    origin=ConditionOrigin.MODEL_INFERENCE,
                ),
                TaskConditionInput(
                    kind="screen",
                    text="实测屏幕为 14 英寸",
                    source_message_id="tool-1",
                    origin=ConditionOrigin.TOOL_OBSERVATION,
                ),
            ],
        ),
    )
    effective = {c.kind: c for c in result.task.effective_conditions}
    # 用户约束与工具事实生效；助手草案与模型推测不进入有效条件。
    assert set(effective) == {"budget", "screen"}
    assert effective["screen"].origin == ConditionOrigin.TOOL_OBSERVATION
    assert effective["screen"].source_message_id == "tool-1"
    all_conditions = {c.kind: c for c in service.list_conditions(account, result.task.task.task_id)}
    assert all_conditions["brand"].status == ConditionStatus.DRAFT
    assert all_conditions["usage"].status == ConditionStatus.CLUE


def test_assistant_proposal_alone_does_not_create_task(env) -> None:
    """普通聊天轻量：只有草案/推测、没有目标时不创建任务。"""
    account = str(env["account_id"])
    service = env["service"]
    result = service.apply_turn(
        account,
        TaskTurnRequest(
            conversation_id="conv-1",
            user_message_id="u1",
            relation=TaskRelation.NEW,
            conditions=[
                TaskConditionInput(
                    kind="brand",
                    text="助手建议 ThinkPad",
                    source_message_id="a1",
                    origin=ConditionOrigin.ASSISTANT_PROPOSAL,
                )
            ],
        ),
    )
    assert result.created is False
    assert service.list_projections(account, "conv-1") == []


# -- 验收 5：乐观版本、账户隔离与审计 ---------------------------------------


def test_optimistic_version_conflict_rejects_revision(env) -> None:
    account = str(env["account_id"])
    service = env["service"]
    created = service.apply_turn(
        account,
        TaskTurnRequest(
            conversation_id="conv-1",
            user_message_id="u1",
            relation=TaskRelation.NEW,
            goal="推荐笔记本",
        ),
    )
    task_id = created.task.task.task_id
    # 当前版本为 1；提交一个期望版本为 0 的修订被拒绝。
    with pytest.raises(TaskVersionConflict):
        service.apply_turn(
            account,
            TaskTurnRequest(
                conversation_id="conv-1",
                user_message_id="u2",
                relation=TaskRelation.REVISE,
                expected_version=0,
                conditions=[
                    TaskConditionInput(
                        kind="budget",
                        text="预算 3,000 元",
                        source_message_id="u2",
                    )
                ],
            ),
        )
    # 拒绝后没有产生新版本或新条件。
    assert [v.version for v in service.list_versions(account, task_id)] == [1]
    assert service.list_conditions(account, task_id) == []


def test_cancelled_task_cannot_be_continued(env) -> None:
    account = str(env["account_id"])
    service = env["service"]
    created = service.apply_turn(
        account,
        TaskTurnRequest(
            conversation_id="conv-1",
            user_message_id="u1",
            relation=TaskRelation.NEW,
            goal="推荐笔记本",
        ),
    )
    task_id = created.task.task.task_id
    service.apply_turn(
        account,
        TaskTurnRequest(
            conversation_id="conv-1",
            user_message_id="u2",
            relation=TaskRelation.CANCEL,
            explicit_task_id=task_id,
        ),
    )
    with pytest.raises(TaskStateConflict):
        service.apply_turn(
            account,
            TaskTurnRequest(
                conversation_id="conv-1",
                user_message_id="u3",
                relation=TaskRelation.CONTINUE,
                explicit_task_id=task_id,
            ),
        )


def test_account_isolation_hides_other_account_tasks(env) -> None:
    account = str(env["account_id"])
    service = env["service"]
    created = service.apply_turn(
        account,
        TaskTurnRequest(
            conversation_id="conv-1",
            user_message_id="u1",
            relation=TaskRelation.NEW,
            goal="推荐笔记本",
        ),
    )
    task_id = created.task.task.task_id
    # 另一个账户读同一 task_id 视为不存在。
    assert service.projection("acct-2", task_id) is None
    assert service.list_projections("acct-2", "conv-1") == []
    # 跨账户提交续接被拒绝。
    from bridges.tasks.repository import TaskNotFound

    with pytest.raises(TaskNotFound):
        service.apply_turn(
            "acct-2",
            TaskTurnRequest(
                conversation_id="conv-1",
                user_message_id="u2",
                relation=TaskRelation.CONTINUE,
                explicit_task_id=task_id,
            ),
        )


def test_audit_events_and_pointer_rebuild(env) -> None:
    account = str(env["account_id"])
    service = env["service"]
    repository = service.repository
    created = service.apply_turn(
        account,
        TaskTurnRequest(
            conversation_id="conv-1",
            user_message_id="u1",
            relation=TaskRelation.NEW,
            goal="推荐笔记本",
        ),
    )
    task_id = created.task.task.task_id
    kinds = [event.kind for event in repository.list_events(account, "conv-1")]
    assert "task_created" in kinds
    # 指针重建（恢复入口）后仍指向同一任务。
    assert repository.rebuild_current_pointer(account, "conv-1") == task_id
    assert repository.current_task(account, "conv-1").task_id == task_id
