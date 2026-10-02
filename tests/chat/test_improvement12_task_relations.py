"""工单 12 验收回归：真实任务等待与受保护的关系入口。"""

from pathlib import Path

import pytest

from bridges.contracts.tasks import TaskRelation, TaskStatus
from tests.chat.test_improvement12_hybrid_entry import (
    _chat_service,
    _context,
    _finish_turn,
    _task,
    _understand,
    _wait,
)


@pytest.mark.parametrize("text", [
    "不要取消这个任务", "他说取消这个任务", "如果取消这个任务会怎样",
    "“取消这个任务”是什么意思", "不要暂停这个任务", "假如先放一放会怎样",
    "不要换成杭州", "他说换成杭州", "如果换成杭州会怎样", "今天天气怎么样",
])
def test_non_action_does_not_change_task(text: str) -> None:
    result = _understand(text, task_context=_context(), tasks=[_task()])
    assert result.task_relation is None


@pytest.mark.parametrize("wait", [_wait(["city"], task_id="other"), _wait(["city"], version=2)])
def test_unrelated_or_stale_wait_does_not_consume_answer(wait: object) -> None:
    result = _understand("杭州", task_context=_context(), tasks=[_task()], open_waits=[wait])
    assert result.answer_fields == []
    assert result.task_relation is None


@pytest.mark.parametrize("text", ["记住我住在杭州", "换个话题，聊聊杭州"])
def test_profile_or_new_topic_does_not_answer_wait(text: str) -> None:
    result = _understand(
        text, task_context=_context(), tasks=[_task()], open_waits=[_wait(["city"])]
    )
    assert result.answer_fields == []


def test_restart_without_task_has_new_goal() -> None:
    result = _understand("重新开始")
    assert result.task_relation == TaskRelation.NEW
    assert result.goal == "重新开始"


def test_paper_clarification_is_persisted_and_short_answer_continues(tmp_path: Path) -> None:
    service, tasks = _chat_service(tmp_path)
    conversation = service.create_conversation("alice")
    _, first, _ = service.start_generation("alice", conversation.conversation_id, "给我找几篇论文")
    _finish_turn(service, first)
    pending = tasks.list_projections("alice", conversation.conversation_id)[0]
    assert pending.task.status == TaskStatus.WAITING
    assert pending.open_waits[0].missing_fields == ["topic"]
    _, answer, _ = service.start_generation("alice", conversation.conversation_id, "量子纠错")
    continued = tasks.list_projections("alice", conversation.conversation_id)
    assert len(continued) == 1
    assert continued[0].task.task_id == pending.task.task_id
    assert continued[0].open_waits == []
    assert answer.route is not None and answer.route.is_paper_search
    assert answer.route.paper_search is not None
    assert "量子纠错" in answer.route.paper_search.normalized_query


def test_career_clarification_is_owned_by_task(tmp_path: Path) -> None:
    service, tasks = _chat_service(tmp_path)
    conversation = service.create_conversation("alice")
    _, first, _ = service.start_generation("alice", conversation.conversation_id, "我想找工作")
    _finish_turn(service, first)
    pending = tasks.list_projections("alice", conversation.conversation_id)[0]
    assert pending.open_waits[0].missing_fields == ["direction"]
    service.start_generation("alice", conversation.conversation_id, "数据分析")
    continued = tasks.list_projections("alice", conversation.conversation_id)
    assert len(continued) == 1
    assert continued[0].task.task_id == pending.task.task_id
    assert continued[0].open_waits[0].missing_fields == ["stage"]
