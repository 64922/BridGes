"""改进工单 11 验收测试：解析任务指代并补回必要原文。

覆盖工单验收标准：

1. 无引号「之前说好的预算是多少」、末尾约束后的「继续推荐」和近期唯一对象
   均正确回补；
2. 两张列表歧义只问一个必要问题并绑定任务版本；唯一列表直接续接，返回
   目标最新版本；
3. 恢复原文包含关键纠正关系，不复活撤销值，回补受统一预算与账户/会话权限
   控制；
4. 无法定位时给真实缺口，推测不得写入有效状态；
5. 记录采用消息/对象 ID 与未采用原因，诊断不含完整私人正文。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from bridges.ai.adapters import StreamChunk
from bridges.chat.context_compiler import compile_turn_context
from bridges.chat.reference_resolution import resolve_references
from bridges.chat.repository import ConversationRepository, MessageRecord
from bridges.contracts.chat import ChatMessageRole, ChatMessageStatus, ChatMode
from bridges.contracts.observability import AuditAction
from bridges.contracts.references import (
    ReferenceStatus,
    ReferenceTaskContext,
)
from bridges.contracts.tasks import (
    ConditionOrigin,
    ConditionScope,
    ConditionStatus,
    TaskCondition,
    TaskRelation,
    TaskStatus,
    TaskTurnRequest,
)
from bridges.storage.database import BridgesDatabase
from bridges.tasks.repository import TaskRepository
from bridges.tasks.service import TaskService, user_condition
from tests.chat.test_chat_api import _create_conversation, _gateway_with, _register
from tests.chat.test_issue02_durable_generation import (  # noqa: F401 - 复用夹具
    client as client,
)
from tests.chat.test_issue02_durable_generation import (
    sqlite_app as sqlite_app,
)

_CONV = "conv-11"
_ACC = "acc-11"
_BASE = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# 测试材料构造
# ---------------------------------------------------------------------------


def _record(
    message_id: str,
    role: ChatMessageRole,
    content: str,
    *,
    seconds: int = 0,
    **extra: Any,
) -> MessageRecord:
    created = _BASE + timedelta(seconds=seconds)
    return MessageRecord(
        message_id=message_id,
        conversation_id=_CONV,
        account_id=_ACC,
        role=role,
        attempt_number=1,
        status=ChatMessageStatus.DONE,
        content=content,
        thinking=None,
        error_code=None,
        error_message=None,
        duration_ms=None,
        model_id=None,
        run_lock_id=None,
        created_at=created,
        updated_at=created,
        **extra,
    )


def _user(message_id: str, content: str, **extra: Any) -> MessageRecord:
    return _record(message_id, ChatMessageRole.USER, content, **extra)


def _assistant(message_id: str, content: str, **extra: Any) -> MessageRecord:
    return _record(message_id, ChatMessageRole.ASSISTANT, content, **extra)


def _condition(
    condition_id: str,
    kind: str,
    text: str,
    status: ConditionStatus,
    source_message_id: str,
    *,
    version: int = 1,
    supersedes: str | None = None,
) -> TaskCondition:
    return TaskCondition(
        condition_id=condition_id,
        task_id="task-11",
        version=version,
        kind=kind,
        text=text,
        scope=ConditionScope.TASK,
        origin=ConditionOrigin.USER_STATED,
        status=status,
        source_message_id=source_message_id,
        supersedes_condition_id=supersedes,
        created_at=_BASE,
        updated_at=_BASE,
    )


def _task(
    *,
    conditions: list[TaskCondition] | None = None,
    goal: str = "推荐设备",
    version: int = 1,
    source_message_ids: list[str] | None = None,
) -> ReferenceTaskContext:
    return ReferenceTaskContext(
        task_id="task-11",
        goal=goal,
        version=version,
        status=TaskStatus.ACTIVE.value,
        conditions=conditions or [],
        source_message_ids=source_message_ids or [],
    )


def _all_content(compiled: Any) -> str:
    return "\n".join(message["content"] for message in compiled.messages)


def _system_blocks(compiled: Any) -> list[str]:
    return [message["content"] for message in compiled.messages if message["role"] == "system"]


def _paper_projection(*titles: str) -> dict[str, Any]:
    return {
        "papers": [
            {"order": index, "title": title, "arxiv_id": f"2401.{index:05d}"}
            for index, title in enumerate(titles, start=1)
        ]
    }


def _github_projection(*names: str) -> dict[str, Any]:
    return {
        "recommendations": [
            {"rank": index, "full_name": name} for index, name in enumerate(names, start=1)
        ]
    }


# ---------------------------------------------------------------------------
# 验收 1：无引号回指、末尾约束续接与近期唯一对象
# ---------------------------------------------------------------------------


def test_quoteless_chinese_reference_recovers_budget_source() -> None:
    """无引号「之前说好的预算是多少」按中文关键词回补原文。"""
    records = [
        _user("m0", "背景说明。" * 50 + "最终预算不得超过三千元。"),
        *[
            _user(f"u{i}", "闲聊。" * 50) if i % 2 else _assistant(f"a{i}", "嗯。" * 50)
            for i in range(1, 8)
        ],
        _user("m8", "之前说好的预算是多少"),
    ]
    resolution = resolve_references(
        request="之前说好的预算是多少",
        messages=records,
        current_user_message_id="m8",
        recent_message_ids=["m7", "m8"],
    )
    assert resolution.status == ReferenceStatus.RESOLVED
    assert resolution.recovered_message_ids == ["m0"]
    assert resolution.missing_requirements == []

    compiled = compile_turn_context(
        messages=records,
        current_user_message_id="m8",
        model_id="probe-model",
        mode=ChatMode.COMPANION,
        context_window=2000,
    )
    assert "最终预算不得超过三千元。" in _all_content(compiled)
    assert compiled.unresolved_reference is False
    assert compiled.reference_status == ReferenceStatus.RESOLVED.value


def test_continue_recommendation_recovers_trailing_constraint() -> None:
    """末尾约束后的「继续推荐」补回约束原文（无任务状态时的确定性续接）。"""
    records = [
        _user("m0", "帮我看看笔记本。" * 20 + "最终预算不得超过三千元。"),
        _assistant("m1", "好的，为你推荐以下设备：设备甲、设备乙。"),
        *[
            _user(f"u{i}", "闲聊。" * 50) if i % 2 else _assistant(f"a{i}", "嗯。" * 50)
            for i in range(2, 8)
        ],
        _user("m8", "继续推荐"),
    ]
    resolution = resolve_references(
        request="继续推荐",
        messages=records,
        current_user_message_id="m8",
        recent_message_ids=["m7", "m8"],
    )
    assert resolution.status == ReferenceStatus.RESOLVED
    assert resolution.recovered_message_ids == ["m0"]
    assert resolution.missing_requirements == []

    compiled = compile_turn_context(
        messages=records,
        current_user_message_id="m8",
        model_id="probe-model",
        mode=ChatMode.COMPANION,
        context_window=2000,
    )
    assert "最终预算不得超过三千元。" in _all_content(compiled)
    assert compiled.unresolved_reference is False


def test_continue_with_object_recovers_result_message_and_adjacent_source() -> None:
    """续接同时点名对象时，补回结果消息与其直接来源轮次（必要相邻原文）。"""
    records = [
        _user("m0", "帮我看看笔记本。" * 20 + "最终预算不得超过三千元。"),
        _assistant("m1", "好的，为你推荐以下设备：设备甲、设备乙。"),
        *[
            _user(f"u{i}", "闲聊。" * 50) if i % 2 else _assistant(f"a{i}", "嗯。" * 50)
            for i in range(2, 8)
        ],
        _user("m8", "继续推荐设备"),
    ]
    resolution = resolve_references(
        request="继续推荐设备",
        messages=records,
        current_user_message_id="m8",
        recent_message_ids=["m7", "m8"],
    )
    assert resolution.status == ReferenceStatus.RESOLVED
    # 结果消息（助手推荐）带出其直接来源用户消息，约束在相邻轮次里。
    assert set(resolution.recovered_message_ids) == {"m0", "m1"}
    assert resolution.adopted_message_ids == ["m0", "m1"]
    assert resolution.missing_requirements == []


def test_recent_unique_object_is_resolved_without_false_not_found() -> None:
    """近期原文已含引用对象时不误报「未找到」，也不重复补回。"""
    records = [
        *[
            _user(f"u{i}", "闲聊。" * 50) if i % 2 else _assistant(f"a{i}", "嗯。" * 50)
            for i in range(0, 7)
        ],
        _user("m7", "最终预算不得超过三千元。"),
        _assistant("m8", "好的，记下了。"),
        _user("m9", "之前说好的预算是多少"),
    ]
    resolution = resolve_references(
        request="之前说好的预算是多少",
        messages=records,
        current_user_message_id="m9",
        recent_message_ids=["m7", "m8", "m9"],
    )
    assert resolution.status == ReferenceStatus.RESOLVED
    assert resolution.adopted_message_ids == ["m7"]
    assert resolution.recovered_message_ids == []
    assert resolution.missing_requirements == []

    compiled = compile_turn_context(
        messages=records,
        current_user_message_id="m9",
        model_id="probe-model",
        mode=ChatMode.COMPANION,
        context_window=3000,
    )
    assert compiled.unresolved_reference is False
    assert compiled.reference_status == ReferenceStatus.RESOLVED.value
    assert "最终预算不得超过三千元。" in _all_content(compiled)


# ---------------------------------------------------------------------------
# 验收 2：两张列表歧义与唯一列表最新版本
# ---------------------------------------------------------------------------


def test_two_lists_ordinal_asks_one_needed_question_and_binds_task_version() -> None:
    """论文与仓库两张列表都能满足「第二个」时只问一个必要问题。"""
    records = [
        _user("m0", "找几篇论文"),
        _assistant("m1", "以下是论文", paper_search=_paper_projection("论文甲", "论文乙")),
        _user("m2", "找实现仓库"),
        _assistant("m3", "以下是仓库", github_projects=_github_projection("owner/a", "owner/b")),
        _user("m4", "第二个有什么区别"),
    ]
    task = _task(version=2, source_message_ids=["m2"])
    resolution = resolve_references(
        request="第二个有什么区别",
        messages=records,
        current_user_message_id="m4",
        recent_message_ids=["m3", "m4"],
        task=task,
    )
    assert resolution.status == ReferenceStatus.AMBIGUOUS
    assert resolution.adopted_message_ids == []
    assert resolution.clarification is not None
    assert resolution.clarification.options == ["第 2 篇论文", "第 2 个仓库"]
    assert resolution.clarification.question == "你指的是第 2 篇论文，还是第 2 个仓库？"
    assert resolution.clarification.task_id == "task-11"
    assert resolution.clarification.expected_version == 2
    # 未采用的候选列表带原因，等待澄清后再选择。
    assert len(resolution.rejected) == 2
    assert all("等待用户澄清" in (anchor.reason or "") for anchor in resolution.rejected)

    compiled = compile_turn_context(
        messages=records,
        current_user_message_id="m4",
        model_id="probe-model",
        mode=ChatMode.COMPANION,
        context_window=3000,
    )
    assert compiled.ambiguous_reference is True
    assert compiled.reference_status == ReferenceStatus.AMBIGUOUS.value
    assert any(
        "只问一个必要的澄清问题" in block and "你指的是第 2 篇论文，还是第 2 个仓库？" in block
        for block in _system_blocks(compiled)
    )
    # 不做词面回补：歧义时不把原文块塞进载荷。
    assert "找回的相关原文" not in _all_content(compiled)


def test_explicit_kind_resolves_latest_paper_list_item() -> None:
    """「第二篇论文」按类别过滤列表，取同类最新版本并返回对象 ID。"""
    records = [
        _user("m0", "找几篇论文"),
        _assistant("m1", "第一版", paper_search=_paper_projection("旧论文A", "旧论文B")),
        _user("m2", "再找几篇"),
        _assistant("m3", "第二版", paper_search=_paper_projection("新论文A", "新论文B")),
        _user("m4", "第二篇论文讲了什么"),
    ]
    resolution = resolve_references(
        request="第二篇论文讲了什么",
        messages=records,
        current_user_message_id="m4",
        recent_message_ids=["m3", "m4"],
    )
    assert resolution.status == ReferenceStatus.RESOLVED
    assert resolution.adopted_object_ids == ["2401.00002"]
    item_anchor = next(anchor for anchor in resolution.anchors if anchor.kind == "list_item")
    assert item_anchor.label == "新论文B"
    assert item_anchor.list_version == 2
    # 采用顺序按会话时间：来源用户消息在前，结果消息在后。
    assert resolution.adopted_message_ids == ["m2", "m3"]


def test_unique_list_ordinal_continues_with_latest_version() -> None:
    """只有一类列表时「最后一个」直接续接，返回最新版本的最后一项。"""
    records = [
        _user("m0", "找论文"),
        _assistant("m1", "第一版", paper_search=_paper_projection("旧A", "旧B", "旧C")),
        _user("m2", "再多找几篇"),
        _assistant(
            "m3",
            "第二版",
            paper_search=_paper_projection("新A", "新B"),
        ),
        _user("m4", "最后一个论文是什么"),
    ]
    resolution = resolve_references(
        request="最后一个论文是什么",
        messages=records,
        current_user_message_id="m4",
        recent_message_ids=["m3", "m4"],
    )
    assert resolution.status == ReferenceStatus.RESOLVED
    assert resolution.adopted_object_ids == ["2401.00002"]
    assert resolution.clarification is None
    assert resolution.adopted_message_ids == ["m2", "m3"]


def test_ordinal_without_result_lists_is_not_artifact_reference() -> None:
    """没有结果列表时普通「第六个问题」不触发产物指代或误报缺口。"""
    records = [
        _user("m0", "第1个问题正文"),
        _assistant("m1", "第1个回答正文"),
        _user("m2", "第六个问题当前请求"),
    ]
    resolution = resolve_references(
        request="第六个问题当前请求",
        messages=records,
        current_user_message_id="m2",
        recent_message_ids=["m0", "m1", "m2"],
    )
    assert resolution.status == ReferenceStatus.NONE
    assert resolution.missing_requirements == []


# ---------------------------------------------------------------------------
# 验收 3：纠正关系、撤销不复活与预算/权限
# ---------------------------------------------------------------------------


def test_correction_chain_recovers_original_then_correction() -> None:
    """恢复原文保留原值→纠正关系：旧值补回但标注不采用，新值仍有效。"""
    conditions = [
        _condition("c1", "budget", "预算五千元", ConditionStatus.SUPERSEDED, "m0"),
        _condition(
            "c2",
            "budget",
            "预算三千元",
            ConditionStatus.EFFECTIVE,
            "m2",
            version=2,
            supersedes="c1",
        ),
    ]
    records = [
        _user("m0", "预算五千元"),
        _assistant("m1", "好。"),
        _user("m2", "改成预算三千元"),
        _assistant("m3", "好。"),
        *[
            _user(f"u{i}", "闲聊。" * 50) if i % 2 else _assistant(f"a{i}", "嗯。" * 50)
            for i in range(4, 12)
        ],
        _user("m12", "之前说好的预算是多少"),
    ]
    task = _task(
        conditions=conditions,
        version=3,
        source_message_ids=["m0", "m2"],
    )
    resolution = resolve_references(
        request="之前说好的预算是多少",
        messages=records,
        current_user_message_id="m12",
        recent_message_ids=["a11", "m12"],
        task=task,
    )
    assert resolution.status == ReferenceStatus.RESOLVED
    assert set(resolution.recovered_message_ids) == {"m0", "m2"}
    assert resolution.correction_notes["m0"].startswith("该值已被后续条件纠正")
    assert "m2" not in resolution.correction_notes

    compiled = compile_turn_context(
        messages=records,
        current_user_message_id="m12",
        model_id="probe-model",
        mode=ChatMode.COMPANION,
        context_window=2000,
        task=task,
    )
    content = _all_content(compiled)
    assert "预算五千元" in content
    assert "预算三千元" in content
    assert "不采用旧值" in content
    assert compiled.reference_status == ReferenceStatus.RESOLVED.value


def test_reference_to_superseded_value_recovers_current_effective_value() -> None:
    """引用被取代的旧值（「之前说的五千」）时采用当前有效值，不误报缺口。"""
    conditions = [
        _condition("c1", "budget", "预算五千元", ConditionStatus.SUPERSEDED, "m0"),
        _condition(
            "c2",
            "budget",
            "预算三千元",
            ConditionStatus.EFFECTIVE,
            "m2",
            version=2,
            supersedes="c1",
        ),
    ]
    records = [
        _user("m0", "预算五千元"),
        _assistant("m1", "好。"),
        _user("m2", "改成预算三千元"),
        _assistant("m3", "好。"),
        *[
            _user(f"u{i}", "闲聊。" * 50) if i % 2 else _assistant(f"a{i}", "嗯。" * 50)
            for i in range(4, 12)
        ],
        _user("m12", "之前说的五千呢"),
    ]
    task = _task(conditions=conditions, version=3, source_message_ids=["m0", "m2"])
    resolution = resolve_references(
        request="之前说的五千呢",
        messages=records,
        current_user_message_id="m12",
        recent_message_ids=["a11", "m12"],
        task=task,
    )
    # 命中的是旧值，但同类条件存在当前有效值：采用有效值来源并保留纠正关系。
    assert resolution.status == ReferenceStatus.RESOLVED
    assert set(resolution.recovered_message_ids) == {"m0", "m2"}
    assert resolution.correction_notes["m0"].startswith("该值已被后续条件纠正")
    assert resolution.missing_requirements == []


def test_reference_to_stated_conditions_continues_with_effective_conditions() -> None:
    """「照之前的条件」按续接处理：采用当前任务的有效条件并补回来源原文。"""
    conditions = [
        _condition(
            "c1",
            "budget",
            "最终预算不得超过三千元",
            ConditionStatus.EFFECTIVE,
            "m0",
        ),
    ]
    records = [
        _user("m0", "帮我看看笔记本。" * 20 + "最终预算不得超过三千元。"),
        _assistant("m1", "好的。"),
        *[
            _user(f"u{i}", "闲聊。" * 50) if i % 2 else _assistant(f"a{i}", "嗯。" * 50)
            for i in range(2, 8)
        ],
        _user("m8", "照之前的条件"),
    ]
    task = _task(conditions=conditions, version=2, source_message_ids=["m0"])
    resolution = resolve_references(
        request="照之前的条件",
        messages=records,
        current_user_message_id="m8",
        recent_message_ids=["m7", "m8"],
        task=task,
    )
    assert resolution.status == ReferenceStatus.RESOLVED
    assert resolution.recovered_message_ids == ["m0"]
    assert resolution.missing_requirements == []

    compiled = compile_turn_context(
        messages=records,
        current_user_message_id="m8",
        model_id="probe-model",
        mode=ChatMode.COMPANION,
        context_window=2000,
        task=task,
    )
    assert "最终预算不得超过三千元。" in _all_content(compiled)
    assert compiled.unresolved_reference is False


def test_compiled_correction_block_marks_superseded_and_effective() -> None:
    """条件来源都在近期原文时，任务描述块仍显式给出纠正关系。"""
    conditions = [
        _condition("c1", "budget", "预算五千元", ConditionStatus.SUPERSEDED, "m0"),
        _condition(
            "c2",
            "budget",
            "预算三千元",
            ConditionStatus.EFFECTIVE,
            "m2",
            version=2,
            supersedes="c1",
        ),
    ]
    records = [
        _user("m0", "预算五千元"),
        _assistant("m1", "好。"),
        _user("m2", "改成预算三千元"),
        _assistant("m3", "好。"),
        _user("m4", "之前说好的预算是多少"),
    ]
    task = _task(conditions=conditions, version=2, source_message_ids=["m0", "m2"])
    compiled = compile_turn_context(
        messages=records,
        current_user_message_id="m4",
        model_id="probe-model",
        mode=ChatMode.COMPANION,
        context_window=4000,
        task=task,
    )
    block = next(text for text in _system_blocks(compiled) if "当前任务快照" in text)
    assert "预算五千元（已被后续条件纠正，不采用旧值。" in block
    assert "预算三千元（仍有效，作为当前条件。" in block
    assert set(compiled.reference_anchor_ids) == {"condition:m0", "condition:m2"}


def test_revoked_value_is_not_revived_and_reported_as_gap() -> None:
    """撤销值既不补回也不采用；给出真实缺口与撤销说明。"""
    conditions = [
        _condition("c3", "exclusion", "排除红色", ConditionStatus.REVOKED, "m0"),
    ]
    records = [
        _user("m0", "排除红色"),
        _assistant("m1", "好。"),
        _user("m2", "之前说好的排除项是什么"),
    ]
    task = _task(conditions=conditions, version=2, source_message_ids=["m0"])
    resolution = resolve_references(
        request="之前说好的排除项是什么",
        messages=records,
        current_user_message_id="m2",
        recent_message_ids=["m2"],
        task=task,
    )
    assert resolution.status == ReferenceStatus.UNRESOLVED
    assert resolution.adopted_message_ids == []
    assert resolution.recovered_message_ids == []
    assert any("已被撤销" in item for item in resolution.missing_requirements)
    assert any(anchor.anchor_id == "condition:c3" for anchor in resolution.rejected)

    compiled = compile_turn_context(
        messages=records,
        current_user_message_id="m2",
        model_id="probe-model",
        mode=ChatMode.COMPANION,
        context_window=3000,
        task=task,
    )
    assert compiled.unresolved_reference is True
    assert compiled.recovered_message_ids == []
    assert "已被撤销" in _all_content(compiled)
    assert "已撤销，不复活旧值" in _all_content(compiled)
    assert "condition:c3" not in compiled.reference_anchor_ids


def test_partial_hit_lists_missing_other_requirement() -> None:
    """部分命中不能掩盖其他必要条件缺失。"""
    records = [
        _user("m0", "预算五千元"),
        _assistant("m1", "好。"),
        _user("m2", "之前说好的预算和排除项是什么"),
    ]
    resolution = resolve_references(
        request="之前说好的预算和排除项是什么",
        messages=records,
        current_user_message_id="m2",
        recent_message_ids=["m2"],
    )
    assert resolution.status == ReferenceStatus.PARTIAL
    assert resolution.adopted_message_ids == ["m0"]
    assert resolution.missing_requirements == ["引用内容「排除项」本轮未定位。"]

    compiled = compile_turn_context(
        messages=records,
        current_user_message_id="m2",
        model_id="probe-model",
        mode=ChatMode.COMPANION,
        context_window=3000,
    )
    assert compiled.unresolved_reference is True
    assert "排除项" in _all_content(compiled)
    assert "预算五千元" in _all_content(compiled)


def test_unresolved_reference_gives_true_gap() -> None:
    """无法定位时给出真实缺口，不声称用户从未讲过。"""
    records = [
        _user("m0", "我喜欢蓝色的笔记本"),
        _assistant("m1", "收到。"),
        _user("m2", "之前说好的约定具体是什么？"),
    ]
    resolution = resolve_references(
        request="之前说好的约定具体是什么？",
        messages=records,
        current_user_message_id="m2",
        recent_message_ids=["m0", "m1", "m2"],
    )
    assert resolution.status == ReferenceStatus.UNRESOLVED
    assert resolution.recovered_message_ids == []
    assert resolution.missing_requirements

    compiled = compile_turn_context(
        messages=records,
        current_user_message_id="m2",
        model_id="probe-model",
        mode=ChatMode.COMPANION,
        context_window=3000,
    )
    rule = next(block for block in _system_blocks(compiled) if "未能在会话历史中找到" in block)
    assert "不要把「本轮未定位」说成用户从未讲过" in rule


def test_new_topic_continuation_does_not_pull_previous_task_conditions() -> None:
    """换话题后的「继续」只用当前任务，不携带其他任务的旧条件。"""
    records = [
        _user("m0", "预算五千元"),
        _assistant("m1", "好。"),
        _user("m2", "我们换个话题"),
        _assistant("m3", "好的。"),
        _user("m4", "继续"),
    ]
    task = _task(
        conditions=[],
        goal="新话题",
        version=4,
        source_message_ids=["m2"],
    )
    resolution = resolve_references(
        request="继续",
        messages=records,
        current_user_message_id="m4",
        recent_message_ids=["m3", "m4"],
        task=task,
    )
    assert resolution.status == ReferenceStatus.RESOLVED
    assert "m0" not in resolution.adopted_message_ids
    assert "condition:c1" not in resolution.adopted_anchor_ids()


def test_recovery_is_bounded_by_unified_budget() -> None:
    """补回受统一预算控制：可容纳时估算不越门，触底时如实记录。"""
    records = [
        _user("m0", "背景。" * 30 + "最终预算不得超过三千元。"),
        *[
            _user(f"u{i}", "闲聊。" * 40) if i % 2 else _assistant(f"a{i}", "嗯。" * 40)
            for i in range(1, 8)
        ],
        _user("m8", "之前说好的预算是多少"),
    ]
    fitted = compile_turn_context(
        messages=records,
        current_user_message_id="m8",
        model_id="probe-model",
        mode=ChatMode.COMPANION,
        context_window=6000,
    )
    assert fitted.input_token_estimate <= fitted.input_budget_tokens
    assert "最终预算不得超过三千元。" in _all_content(fitted)

    floored = compile_turn_context(
        messages=records,
        current_user_message_id="m8",
        model_id="probe-model",
        mode=ChatMode.COMPANION,
        context_window=800,
    )
    # 触底时不静默丢证据：当前请求与补回的约束仍在，只是如实记录触底。
    assert floored.budget_floor_exceeded is True
    assert "最终预算不得超过三千元。" in _all_content(floored)


# ---------------------------------------------------------------------------
# 验收 4/5：只读、诊断与账户隔离
# ---------------------------------------------------------------------------


def test_resolution_is_read_only_for_task_state(tmp_path: Path) -> None:
    """指代解析不产生新用户条件：任务、版本与条件在解析后保持不变。"""
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    conversations = ConversationRepository(database)
    conversations.create_conversation(
        account_id=_ACC,
        conversation_id=_CONV,
        title="选购",
        mode="companion",
        created_at=_BASE,
    )
    service = TaskService(TaskRepository(database))
    services_turn = service.apply_turn(
        _ACC,
        TaskTurnRequest(
            conversation_id=_CONV,
            user_message_id="m0",
            relation=TaskRelation.NEW,
            goal="推荐设备",
            conditions=[user_condition(kind="budget", text="预算三千元", source_message_id="m0")],
        ),
    )
    assert services_turn.task is not None
    context = service.current_reference_context(_ACC, _CONV)
    assert context is not None
    before_conditions = service.list_conditions(_ACC, context.task_id)
    before_task = service.repository.current_task(_ACC, _CONV)
    before_events = service.list_events(_ACC, _CONV)

    messages = [
        _user("m0", "最终预算不得超过三千元。"),
        _assistant("m1", "好。"),
        _user("m2", "之前说好的预算是多少"),
    ]
    resolution = resolve_references(
        request="之前说好的预算是多少",
        messages=messages,
        current_user_message_id="m2",
        recent_message_ids=["m2"],
        task=context,
    )
    # 解析产出只读回显：有效条件被采用，但没有新增条件/等待。
    assert resolution.status == ReferenceStatus.RESOLVED
    assert "m0" in resolution.adopted_message_ids
    assert service.list_conditions(_ACC, context.task_id) == before_conditions
    refreshed = service.repository.current_task(_ACC, _CONV)
    assert refreshed is not None and before_task is not None
    assert refreshed.current_version == before_task.current_version
    after_events = service.list_events(_ACC, _CONV)
    assert len(after_events) == len(before_events)


def test_compiled_record_keeps_ids_and_reasons_without_private_text() -> None:
    """审计记录含采用/未采用 ID 与原因，但不含完整私人正文。"""
    secret = "私人材料：我的银行卡号是 6222 0000 1111 2222"
    records = [
        _user("m0", secret),
        _assistant("m1", "收到。"),
        _user("m2", "之前说好的预算是多少"),
    ]
    compiled = compile_turn_context(
        messages=records,
        current_user_message_id="m2",
        model_id="probe-model",
        mode=ChatMode.COMPANION,
        context_window=3000,
    )
    record = compiled.to_record()
    dumped = json.dumps(record, ensure_ascii=False)
    assert secret not in dumped
    assert "6222" not in dumped
    assert record["reference_status"] == ReferenceStatus.UNRESOLVED.value
    assert record["reference_contract_version"] == "reference-v1"
    assert record["reference_missing_count"] >= 1
    assert isinstance(record["reference_rejected"], list)


def test_truncated_condition_label_and_rejection_reasons() -> None:
    """条件锚点标签截断；未采用锚点带原因（诊断不携带完整正文）。"""
    text = "预算" + "这是一条很长的条件正文" * 5
    conditions = [
        _condition("c1", "budget", text, ConditionStatus.EFFECTIVE, "m0"),
        _condition("c2", "exclusion", "排除红色", ConditionStatus.REVOKED, "m1"),
    ]
    records = [
        _user("m0", text),
        _user("m1", "排除红色"),
        _assistant("m2", "好。"),
        _user("m3", "之前说好的预算和排除项是什么"),
    ]
    resolution = resolve_references(
        request="之前说好的预算和排除项是什么",
        messages=records,
        current_user_message_id="m3",
        recent_message_ids=["m3"],
        task=_task(conditions=conditions, version=2, source_message_ids=["m0", "m1"]),
    )
    condition_anchor = next(anchor for anchor in resolution.anchors if anchor.kind == "condition")
    assert condition_anchor.label.endswith("…")
    assert len(condition_anchor.label) <= len("budget:") + 24
    # 被撤销的排他条件：未采用且带原因。
    revoked = next(anchor for anchor in resolution.rejected if anchor.anchor_id == "condition:c2")
    assert "不复活" in (revoked.reason or "")


class _CapturingAdapter:
    """捕获模型载荷的流式适配器（跨账户隔离用）。"""

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def stream_call(self, capability: Any, run_context: Any, payload: dict[str, Any]):
        self.payloads.append(payload)
        yield StreamChunk(kind="delta", delta="已收到")


def test_cross_account_material_is_not_recallable(
    sqlite_app: Any,
    client: TestClient,
    generation_helpers: dict[str, Any],
) -> None:
    """回补受账户作用域限制：账户 B 只能补回自己会话的原文。"""
    adapter = _CapturingAdapter()
    sqlite_app.state.chat_service._gateway = _gateway_with(adapter)  # noqa: SLF001

    account_a = _register(client, tag="111")["id"]
    conv_a = _create_conversation(client)
    generation_helpers["send"](client, conv_a, content="账户A：最终预算不得超过三千元。")
    generation_helpers["drive"](sqlite_app)

    account_b = _register(client, tag="112")["id"]
    conv_b = _create_conversation(client)
    generation_helpers["send"](client, conv_b, content="账户B：最终预算不得超过两万元。")
    generation_helpers["drive"](sqlite_app)
    generation_helpers["send"](client, conv_b, content="之前说好的预算是多少")
    generation_helpers["drive"](sqlite_app)

    payloads_b = [
        payload
        for payload in adapter.payloads
        if any("账户B" in message.get("content", "") for message in payload.get("messages", []))
    ]
    assert len(payloads_b) == 2
    second_b = "\n".join(message.get("content", "") for message in payloads_b[1]["messages"])
    assert "不得超过两万元" in second_b
    assert "不得超过三千元" not in second_b

    observability = sqlite_app.state.observability_service
    records = observability.list_audit_events(
        account_id=account_b, action=AuditAction.CONTEXT_COMPILED
    )
    repo = sqlite_app.state.chat_service._repo  # noqa: SLF001
    b_message_ids = {record.message_id for record in repo.list_messages(account_b, conv_b)}
    assert records
    assert set(records[-1].details["adopted_message_ids"]) <= b_message_ids
    assert set(records[-1].details["recovered_message_ids"]) <= b_message_ids
    assert account_a != account_b
