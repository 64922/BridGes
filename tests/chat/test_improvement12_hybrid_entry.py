"""改进工单 12 定向测试：混合入口与跨轮任务关系的确定性主理解。

覆盖工单验收标准中的入口判定部分：

1. 明确单模块意图直达（正文优先，模块提示只作辅助/历史标识）；
2. 普通聊天不创建任务、不触碰已有任务；
3. 歧义只问一个必要问题；
4. 硬条件（不联网/只用本地/只查论文）进入理解快照；
5. 续接/修订/暂停/取消关系与目标任务版本；
6. 学习模式不派发日常模块；点击建议绑定任务版本不改原消息。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from bridges.chat.repository import MessageRecord
from bridges.chat.understanding import MainAgentUnderstanding, task_turn_request
from bridges.contracts.chat import ChatMessageRole, ChatMessageStatus, ChatMode
from bridges.contracts.references import ReferenceTaskContext
from bridges.contracts.tasks import (
    TaskRecord,
    TaskRelation,
    TaskStatus,
    TaskWait,
    WaitStatus,
)
from bridges.contracts.understanding import HardConditionKind, RouteSource

_BASE = datetime(2026, 10, 2, 9, 0, tzinfo=UTC)


def _task(
    task_id: str = "task-1",
    goal: str = "找几篇大模型安全方向的论文",
    status: TaskStatus = TaskStatus.ACTIVE,
    version: int = 3,
) -> TaskRecord:
    return TaskRecord(
        task_id=task_id,
        account_id="acc-12",
        conversation_id="conv-12",
        goal=goal,
        status=status,
        current_version=version,
        created_at=_BASE,
        updated_at=_BASE,
    )


def _wait(
    missing: list[str],
    *,
    task_id: str = "task-1",
    version: int = 3,
) -> TaskWait:
    return TaskWait(
        wait_id="wait-1",
        task_id=task_id,
        conversation_id="conv-12",
        expected_version=version,
        missing_fields=missing,
        question="请补充城市",
        origin_message_id="assistant-1",
        status=WaitStatus.OPEN,
        source_message_id="user-1",
        created_at=_BASE,
        updated_at=_BASE,
    )


def _context(task: TaskRecord | None = None) -> ReferenceTaskContext:
    item = task or _task()
    return ReferenceTaskContext(
        task_id=item.task_id, goal=item.goal, version=item.current_version, status=item.status.value
    )


def _user_message(
    message_id: str, content: str, *, conversation_id: str = "conv-12"
) -> MessageRecord:
    return MessageRecord(
        message_id=message_id,
        conversation_id=conversation_id,
        account_id="acc-12",
        role=ChatMessageRole.USER,
        attempt_number=1,
        status=ChatMessageStatus.DONE,
        content=content,
        thinking=None,
        error_code=None,
        error_message=None,
        duration_ms=None,
        model_id=None,
        run_lock_id=None,
        created_at=_BASE,
        updated_at=_BASE,
    )


def _understand(content: str, **kwargs: object):
    engine = MainAgentUnderstanding()
    defaults: dict[str, object] = {
        "conversation_id": "conv-12",
        "user_message_id": "user-now",
        "content": content,
        "mode": ChatMode.COMPANION,
        "messages": [_user_message("user-now", content)],
    }
    defaults.update(kwargs)
    return engine.understand(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 1. 正文优先与模块提示
# ---------------------------------------------------------------------------


def test_body_intent_paper_routes_to_paper() -> None:
    result = _understand("帮我找几篇关于 Transformer 高效推理的论文")

    assert result.actual_module_id == "paper"
    assert result.route_source == RouteSource.BODY_INTENT
    assert result.task_relation == TaskRelation.NEW
    assert result.goal == "帮我找几篇关于 Transformer 高效推理的论文"


def test_module_hint_kept_as_history_when_body_has_no_intent() -> None:
    result = _understand("帮我看看", requested_module_id="github")

    assert result.requested_module_id == "github"
    assert result.actual_module_id == "github"
    assert result.route_source == RouteSource.MODULE_HINT


def test_body_intent_overrides_module_hint() -> None:
    result = _understand("帮我找几篇关于图神经网络的论文", requested_module_id="github")

    assert result.requested_module_id == "github"
    assert result.actual_module_id == "paper"
    assert result.route_source == RouteSource.BODY_INTENT


def test_multiple_modules_ask_one_question() -> None:
    result = _understand("帮我找几篇论文，再找几本入门教材")

    assert result.actual_module_id is None
    assert result.clarification_question is not None
    assert result.missing_fields == ["capability"]
    assert result.route_source == RouteSource.ORDINARY_CHAT


# ---------------------------------------------------------------------------
# 2. 普通聊天不创建任务
# ---------------------------------------------------------------------------


def test_ordinary_chat_creates_no_task_relation() -> None:
    result = _understand("今天天气怎么样")

    assert result.actual_module_id is None
    assert result.task_relation is None
    assert task_turn_request(result, conversation_id="conv-12") is None


def test_meta_reply_does_not_fill_open_wait() -> None:
    result = _understand(
        "好的",
        task_context=_context(),
        tasks=[_task()],
        open_waits=[_wait(["city"])],
    )

    assert result.task_relation is None
    assert result.answer_fields == []


# ---------------------------------------------------------------------------
# 3. 硬条件
# ---------------------------------------------------------------------------


def test_no_network_hard_condition_blocks_network() -> None:
    result = _understand("不要联网，只根据我上传的资料回答")

    assert result.blocks_network is True
    kinds = {item.kind for item in result.hard_conditions}
    assert HardConditionKind.NO_NETWORK in kinds or HardConditionKind.LOCAL_ONLY in kinds


def test_paper_only_restriction_recorded() -> None:
    result = _understand("只查论文，不要知识库里的资料")

    assert HardConditionKind.SOURCE_RESTRICTION in {
        item.kind for item in result.hard_conditions
    }


def test_year_and_count_conditions_recorded() -> None:
    result = _understand("找 3 篇近三年的向量数据库论文")

    kinds = {item.kind for item in result.hard_conditions}
    assert HardConditionKind.YEAR_RANGE in kinds
    assert HardConditionKind.COUNT in kinds


def test_no_local_condition_keeps_web_open() -> None:
    result = _understand("不要查知识库，帮我搜一下网上的最新消息")

    condition = next(
        item
        for item in result.hard_conditions
        if item.kind == HardConditionKind.NO_LOCAL
    )
    assert condition.source_span == condition.text == "不要查知识库"
    assert result.blocks_network is False
    assert result.blocks_knowledge_base is True


def test_paused_task_ignores_unrelated_long_message() -> None:
    paused = _task(status=TaskStatus.PAUSED)

    unrelated = _understand(
        "今天天气怎么样", task_context=_context(paused), tasks=[paused]
    )
    resumed = _understand("继续找", task_context=_context(paused), tasks=[paused])

    assert unrelated.task_relation is None
    assert resumed.task_relation == TaskRelation.CONTINUE


# ---------------------------------------------------------------------------
# 4. 任务关系
# ---------------------------------------------------------------------------


def test_continue_marker_with_task_context() -> None:
    result = _understand("继续", task_context=_context(), tasks=[_task()])

    assert result.task_relation == TaskRelation.CONTINUE
    assert result.target_task_id == "task-1"
    assert result.expected_task_version == 3
    assert result.route_source == RouteSource.TASK_CONTINUATION


def test_pause_and_cancel_relations() -> None:
    paused = _understand("先暂停一下", task_context=_context(), tasks=[_task()])
    cancelled = _understand("算了，不找了", task_context=_context(), tasks=[_task()])

    assert paused.task_relation == TaskRelation.PAUSE
    assert cancelled.task_relation == TaskRelation.CANCEL


def test_revise_relation_records_city_condition() -> None:
    result = _understand("城市换成杭州", task_context=_context(), tasks=[_task()])

    assert result.task_relation == TaskRelation.REVISE
    assert HardConditionKind.CITY in {item.kind for item in result.hard_conditions}


def test_new_module_request_is_not_continuation() -> None:
    result = _understand(
        "帮我找几篇关于联邦学习的论文",
        task_context=_context(),
        tasks=[_task()],
    )

    assert result.task_relation == TaskRelation.NEW
    assert result.target_task_id is None


def test_answer_fields_only_when_value_present() -> None:
    answered = _understand(
        "杭州",
        task_context=_context(),
        tasks=[_task()],
        open_waits=[_wait(["city"])],
    )
    unanswered = _understand(
        "到时候再说",
        task_context=_context(),
        tasks=[_task()],
        open_waits=[_wait(["city"])],
    )

    assert answered.answer_fields == ["city"]
    assert answered.task_relation == TaskRelation.CONTINUE
    assert unanswered.answer_fields == []


def test_profile_command_marked() -> None:
    result = _understand("记住我常驻城市是杭州", task_context=_context(), tasks=[_task()])

    assert result.is_profile_command is True
    assert result.answer_fields == []


# ---------------------------------------------------------------------------
# 5. 学习模式与建议点击
# ---------------------------------------------------------------------------


def test_study_mode_never_dispatches_daily_module() -> None:
    result = _understand("帮我找几篇论文", mode=ChatMode.STUDY)

    assert result.is_learning_action is True
    assert result.actual_module_id is None
    assert result.route_source == RouteSource.LEARNING_STRATEGY
    assert result.task_relation is None


def test_continue_with_task_context_binds_task_version() -> None:
    result = _understand(
        "继续找这个方向的论文",
        requested_module_id="paper",
        task_context=_context(),
        tasks=[_task()],
    )

    assert result.actual_module_id == "paper"
    assert result.target_task_id == "task-1"
    assert result.expected_task_version == 3
    assert result.task_relation == TaskRelation.CONTINUE


# ---------------------------------------------------------------------------
# 服务级：一次理解的关系落地、硬条件与停止语义
# ---------------------------------------------------------------------------


def _chat_service(tmp_path: Path):
    from bridges.ai import ModelGateway
    from bridges.ai.capability_registry import CapabilityRegistry
    from bridges.chat.repository import ConversationRepository
    from bridges.chat.service import ChatService
    from bridges.storage.database import BridgesDatabase
    from bridges.tasks.repository import TaskRepository
    from bridges.tasks.service import TaskService
    from tests.chat.test_arxiv_search_chat import _capability, _CapturingAdapter

    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    repository = ConversationRepository(database)
    registry = CapabilityRegistry()
    registry.register(_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", _CapturingAdapter())
    tasks = TaskService(TaskRepository(database))
    service = ChatService(
        repository=repository, gateway=gateway, task_service=tasks
    )
    return service, tasks


def _current_task(tasks: object, account_id: str, conversation_id: str):
    projections = tasks.list_projections(account_id, conversation_id)  # type: ignore[attr-defined]
    assert len(projections) == 1
    return projections[0]


def _finish_turn(service: object, assistant: object) -> None:
    """测试内无后台执行器：直接收敛上一轮，允许下一轮发送。"""

    from bridges.chat.terminal import stopped_outcome

    run = service._repo.get_run_by_message(  # type: ignore[attr-defined]  # noqa: SLF001
        "alice", assistant.message_id  # type: ignore[attr-defined]
    )
    service.terminal.converge(  # type: ignore[attr-defined]
        "alice",
        run.run_id if run is not None else None,
        assistant.message_id,  # type: ignore[attr-defined]
        fallback=stopped_outcome(),
    )


def test_ordinary_chat_leaves_task_domain_untouched(tmp_path: Path) -> None:
    service, tasks = _chat_service(tmp_path)
    conversation = service.create_conversation("alice")

    _, assistant, _ = service.start_generation(
        "alice", conversation.conversation_id, "今天天气怎么样"
    )

    assert tasks.list_projections("alice", conversation.conversation_id) == []
    run = service._repo.get_run_by_message("alice", assistant.message_id)  # noqa: SLF001
    assert run is not None
    snapshot = run.config["understanding"]
    assert snapshot["task_relation"] is None
    assert snapshot["actual_module_id"] is None
    assert assistant.route is not None
    assert assistant.route.understanding_version == "main-understanding-v1"


def test_module_intent_creates_task_with_goal(tmp_path: Path) -> None:
    service, tasks = _chat_service(tmp_path)
    conversation = service.create_conversation("alice")

    user, assistant, _ = service.start_generation(
        "alice", conversation.conversation_id, "推荐几本机器学习的入门教材"
    )

    assert user.module_id == "resources"
    assert assistant.route is not None
    task = _current_task(tasks, "alice", conversation.conversation_id)
    assert task.task.goal == "推荐几本机器学习的入门教材"
    assert task.task.current_version == 1


def test_city_revision_updates_task_version_and_condition(tmp_path: Path) -> None:
    service, tasks = _chat_service(tmp_path)
    conversation = service.create_conversation("alice")
    _, first, _ = service.start_generation(
        "alice", conversation.conversation_id, "推荐几本 Python 的入门教程"
    )
    _finish_turn(service, first)
    task = _current_task(tasks, "alice", conversation.conversation_id)

    service.start_generation(
        "alice", conversation.conversation_id, "城市换成杭州"
    )

    updated = _current_task(tasks, "alice", conversation.conversation_id)
    assert updated.task.task_id == task.task.task_id
    assert updated.task.current_version == 2
    assert any(
        condition.kind == "city" and "杭州" in condition.text
        for condition in updated.effective_conditions
    )


def test_pause_continue_and_cancel_transitions(tmp_path: Path) -> None:
    service, tasks = _chat_service(tmp_path)
    conversation = service.create_conversation("alice")
    _, first, _ = service.start_generation(
        "alice", conversation.conversation_id, "推荐几本 Python 的入门教程"
    )
    _finish_turn(service, first)
    assert (
        _current_task(tasks, "alice", conversation.conversation_id).task.status
        == TaskStatus.ACTIVE
    )

    _, second, _ = service.start_generation(
        "alice", conversation.conversation_id, "先暂停一下"
    )
    _finish_turn(service, second)
    assert (
        _current_task(tasks, "alice", conversation.conversation_id).task.status
        == TaskStatus.PAUSED
    )

    _, third, _ = service.start_generation(
        "alice", conversation.conversation_id, "继续"
    )
    _finish_turn(service, third)
    assert (
        _current_task(tasks, "alice", conversation.conversation_id).task.status
        == TaskStatus.ACTIVE
    )

    service.start_generation("alice", conversation.conversation_id, "算了，不找了")
    assert (
        _current_task(tasks, "alice", conversation.conversation_id).task.status
        == TaskStatus.CANCELLED
    )


def test_multi_module_clarification_opens_wait_and_answer_resolves(
    tmp_path: Path,
) -> None:
    service, tasks = _chat_service(tmp_path)
    conversation = service.create_conversation("alice")

    _, assistant, _ = service.start_generation(
        "alice", conversation.conversation_id, "帮我找几篇论文，再找几本入门教材"
    )
    _finish_turn(service, assistant)

    assert assistant.route is not None
    assert assistant.route.status.value == "clarify"
    task = _current_task(tasks, "alice", conversation.conversation_id)
    assert [wait.missing_fields for wait in task.open_waits] == [["capability"]]

    service.start_generation(
        "alice",
        conversation.conversation_id,
        "先查量子纠错方向的论文",
    )

    answered = _current_task(tasks, "alice", conversation.conversation_id)
    assert answered.task.task_id == task.task.task_id
    assert answered.open_waits == []


def test_new_topic_pauses_task_and_suspends_old_wait(tmp_path: Path) -> None:
    service, tasks = _chat_service(tmp_path)
    conversation = service.create_conversation("alice")
    _, assistant, _ = service.start_generation(
        "alice", conversation.conversation_id, "帮我找几篇论文，再找几本入门教材"
    )
    _finish_turn(service, assistant)
    task = _current_task(tasks, "alice", conversation.conversation_id)
    assert task.open_waits

    service.start_generation(
        "alice", conversation.conversation_id, "换个话题，今天天气怎么样"
    )

    changed = _current_task(tasks, "alice", conversation.conversation_id)
    assert changed.task.task_id == task.task.task_id
    assert changed.task.status == TaskStatus.PAUSED
    assert changed.open_waits == []


def test_stop_generation_pauses_current_task(tmp_path: Path) -> None:
    service, tasks = _chat_service(tmp_path)
    conversation = service.create_conversation("alice")

    _, assistant, _ = service.start_generation(
        "alice", conversation.conversation_id, "推荐几本 Python 的入门教程"
    )
    service.stop_generation("alice", conversation.conversation_id, assistant.message_id)

    assert (
        _current_task(tasks, "alice", conversation.conversation_id).task.status
        == TaskStatus.PAUSED
    )


def test_hard_condition_blocks_web_and_marks_route(tmp_path: Path) -> None:
    service, _ = _chat_service(tmp_path)
    conversation = service.create_conversation("alice")

    _, assistant, _ = service.start_generation(
        "alice",
        conversation.conversation_id,
        "不要联网，只根据我上传的资料回答这个问题",
    )

    assert assistant.route is not None
    assert assistant.route.web_search_allowed is False
    run = service._repo.get_run_by_message("alice", assistant.message_id)  # noqa: SLF001
    assert run is not None
    kinds = {
        item["kind"] for item in run.config["understanding"]["hard_conditions"]
    }
    assert "no_network" in kinds


def test_no_local_condition_route_keeps_web_allowed(tmp_path: Path) -> None:
    service, _ = _chat_service(tmp_path)
    conversation = service.create_conversation("alice")

    _, assistant, _ = service.start_generation(
        "alice",
        conversation.conversation_id,
        "不要查知识库，帮我搜一下网上的最新消息",
    )

    assert assistant.route is not None
    assert assistant.route.web_search_allowed is True
    assert assistant.route.knowledge_base_allowed is False


def test_paused_task_not_revived_by_unrelated_message(tmp_path: Path) -> None:
    service, tasks = _chat_service(tmp_path)
    conversation = service.create_conversation("alice")

    _, first, _ = service.start_generation(
        "alice", conversation.conversation_id, "推荐几本 Python 的入门教程"
    )
    _finish_turn(service, first)
    _, pause, _ = service.start_generation(
        "alice", conversation.conversation_id, "先暂停这个任务"
    )
    _finish_turn(service, pause)
    assert (
        _current_task(tasks, "alice", conversation.conversation_id).task.status
        == TaskStatus.PAUSED
    )

    service.start_generation(
        "alice", conversation.conversation_id, "今天天气怎么样"
    )

    assert (
        _current_task(tasks, "alice", conversation.conversation_id).task.status
        == TaskStatus.PAUSED
    )


def test_retry_keeps_understanding_snapshot_and_hard_conditions(tmp_path: Path) -> None:
    service, _ = _chat_service(tmp_path)
    conversation = service.create_conversation("alice")

    _, assistant, _ = service.start_generation(
        "alice", conversation.conversation_id, "不要联网，帮我看看这个"
    )
    _finish_turn(service, assistant)
    _, retry, _ = service.retry_generation(
        "alice", conversation.conversation_id, assistant.message_id, module_id="github"
    )

    assert retry.route is not None
    assert retry.route.route_source == "suggestion_click"
    assert retry.route.module_id == "github"
    assert retry.route.web_search_allowed is False
    run = service._repo.get_run_by_message("alice", retry.message_id)  # noqa: SLF001
    assert run is not None
    kinds = {
        item["kind"] for item in run.config["understanding"]["hard_conditions"]
    }
    assert "no_network" in kinds


def test_study_route_does_not_fall_back_to_paper_clarify(tmp_path: Path) -> None:
    from bridges.routing import RouteStatus

    service, _ = _chat_service(tmp_path)
    understanding = MainAgentUnderstanding().understand(
        conversation_id="conv-12",
        user_message_id="user-now",
        content="帮我找几篇论文",
        mode=ChatMode.STUDY,
    )

    route = service._route_for_turn(
        image_payload=None,
        video_payload=None,
        mcp_call_payload=None,
        content="帮我找几篇论文",
        understanding=understanding,
    )

    assert route.status == RouteStatus.ORDINARY
    assert route.route_source == RouteSource.LEARNING_STRATEGY.value
    assert route.is_paper_search is False


def test_suggestion_click_retry_records_actual_route_source(tmp_path: Path) -> None:
    service, tasks = _chat_service(tmp_path)
    conversation = service.create_conversation("alice")

    # 先有一轮普通消息（旧建议条绑定场景），再用建议点击重试。
    _, assistant, _ = service.start_generation(
        "alice", conversation.conversation_id, "帮我看看这个"
    )
    _finish_turn(service, assistant)
    _, retry, _ = service.retry_generation(
        "alice", conversation.conversation_id, assistant.message_id, module_id="github"
    )

    assert retry.route is not None
    assert retry.route.route_source == "suggestion_click"
    assert retry.route.requested_module_id == "github"
    assert retry.route.module_id == "github"
    assert service._repo.get_message("alice", assistant.message_id).module_id is None  # noqa: SLF001


def test_understanding_snapshot_export_and_deletion_contract(tmp_path: Path) -> None:
    """新增持久状态的版本化/导出/删除合同：快照带版本，导出不泄正文材料。"""

    import json

    from bridges.lifecycle.catalog import delete_account_rows, export_rows

    service, _ = _chat_service(tmp_path)
    database = service._repo.database  # noqa: SLF001
    conversation = service.create_conversation("alice")
    _, assistant, _ = service.start_generation(
        "alice", conversation.conversation_id, "帮我找几篇关于大模型安全的论文"
    )
    _finish_turn(service, assistant)

    run = service._repo.get_run_by_message("alice", assistant.message_id)  # noqa: SLF001
    assert run is not None
    snapshot = run.config["understanding"]
    assert snapshot["contract_version"] == "main-understanding-v1"
    assert snapshot["user_message_id"] == run.user_message_id
    assert assistant.route is not None
    assert assistant.route.understanding_version == snapshot["contract_version"]

    exported_runs = export_rows(database, "alice", "generation_runs")
    assert exported_runs
    assert "config_json" not in exported_runs[0]
    assert "understanding" not in exported_runs[0]

    exported_messages = export_rows(database, "alice", "messages")
    routed = next(
        row for row in exported_messages if row["message_id"] == assistant.message_id
    )
    route_document = json.loads(routed["route"])
    assert route_document["understanding_version"] == snapshot["contract_version"]
    assert route_document["route_source"]

    delete_account_rows(database, "alice")
    for table in ("messages", "generation_runs", "conversation_tasks"):
        row = database.scoped("alice").execute(
            f"SELECT COUNT(*) AS count FROM {table} WHERE account_id = ?", ("alice",)
        ).fetchone()
        assert row["count"] == 0


# ---------------------------------------------------------------------------
# 学习阶段门（L05/L08）：否定/引语/假设/作答不切换阶段
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("开始复盘", "start"),
        ("学完本节了，开始复盘吧", "start"),
        ("暂停复盘", "pause"),
        ("先暂停一下复盘吧", "pause"),
        ("回到辅导", "pause"),
        ("给我讲解一下", "tutor"),
        ("再讲讲", "tutor"),
        ("还没学完", None),
        ("我不想开始复盘", None),
        ("他说“开始复盘”", None),
        ("如果开始复盘呢", None),
        ("不用开始复盘了", None),
        ("取消整个学习任务", None),
        ("暂不暂停复盘", None),
    ],
)
def test_review_stage_gate_negation_quote_and_pause(
    content: str, expected: str | None
) -> None:
    from bridges.study.review import review_intent

    assert review_intent(content) == expected
