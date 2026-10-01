"""Issue 11 独立验收：旧列表、最终模型载荷与历史轮次边界。"""

from dataclasses import replace

from bridges.chat.context_compiler import compile_turn_context
from bridges.chat.reference_resolution import resolve_references
from bridges.contracts.chat import ChatMessageStatus, ChatMode
from bridges.contracts.references import ReferenceStatus
from bridges.contracts.tasks import ConditionStatus
from tests.chat.test_improvement11_reference_resolution import (
    _all_content,
    _assistant,
    _condition,
    _github_projection,
    _paper_projection,
    _task,
    _user,
)


def test_old_ordinal_recovers_sources_and_passes_object_to_model() -> None:
    records = [
        _user("u0", "找论文，只要开源实现"),
        _assistant(
            "a0",
            "背景。" * 100 + "论文末尾说明",
            paper_search=_paper_projection("甲", "目标论文乙"),
        ),
        _user("u1", "闲聊。" * 100),
        _assistant("a1", "回答。" * 100),
        _user("now", "第二篇讲什么"),
    ]
    compiled = compile_turn_context(
        messages=records,
        current_user_message_id="now",
        model_id="probe-model",
        mode=ChatMode.COMPANION,
        context_window=2000,
    )
    assert {"u0", "a0"} <= set(compiled.recovered_message_ids)
    assert "目标论文乙" in _all_content(compiled)
    assert "2401.00002" in _all_content(compiled)
    assert "论文末尾说明" in _all_content(compiled)


def test_latest_shorter_list_does_not_resurrect_old_item() -> None:
    records = [
        _user("u0", "找论文"),
        _assistant("a0", "旧版", paper_search=_paper_projection("甲", "乙")),
        _user("u1", "再找几篇"),
        _assistant("a1", "新版", paper_search=_paper_projection("丙")),
        _user("now", "第二篇是什么"),
    ]
    result = resolve_references(
        request=records[-1].content, messages=records, current_user_message_id="now"
    )
    assert result.status == ReferenceStatus.UNRESOLVED
    assert result.adopted_object_ids == []


def test_last_paper_classifier_disambiguates_repository() -> None:
    records = [
        _user("u0", "找论文"),
        _assistant("a0", "论文", paper_search=_paper_projection("甲", "乙")),
        _user("u1", "找仓库"),
        _assistant("a1", "仓库", github_projects=_github_projection("o/a", "o/b")),
        _user("now", "最后一篇讲什么"),
    ]
    result = resolve_references(
        request=records[-1].content, messages=records, current_user_message_id="now"
    )
    assert result.status == ReferenceStatus.RESOLVED
    assert result.adopted_object_ids == ["2401.00002"]


def test_retried_turn_cannot_read_future_or_failed_results() -> None:
    records = [
        _user("u0", "找论文"),
        _assistant("a0", "有效版", paper_search=_paper_projection("甲", "乙")),
        replace(
            _assistant("failed", "失败版", paper_search=_paper_projection("失败甲", "失败乙")),
            status=ChatMessageStatus.ERROR,
        ),
        _user("now", "第二篇讲什么"),
        _assistant("future", "未来版", paper_search=_paper_projection("未来甲", "未来乙")),
        _user("later", "未来预算是九千元"),
    ]
    result = resolve_references(
        request="第二篇讲什么", messages=records, current_user_message_id="now"
    )
    assert result.adopted_message_ids == ["u0", "a0"]
    assert result.anchors[-1].label == "乙"


def test_empty_history_reports_true_reference_gap() -> None:
    result = resolve_references(
        request="之前说好的预算是多少",
        messages=[_user("now", "之前说好的预算是多少")],
        current_user_message_id="now",
    )
    assert result.status == ReferenceStatus.UNRESOLVED
    assert result.missing_requirements


def test_independent_paper_lists_require_clarification() -> None:
    records = [
        _user("u0", "找机器学习论文"),
        _assistant("a0", "机器学习", paper_search=_paper_projection("甲", "乙")),
        _user("u1", "找量子物理论文"),
        _assistant("a1", "量子物理", paper_search=_paper_projection("丙", "丁")),
        _user("now", "第二篇讲什么"),
    ]
    result = resolve_references(
        request=records[-1].content, messages=records, current_user_message_id="now"
    )
    assert result.status == ReferenceStatus.AMBIGUOUS
    assert result.clarification is not None
    assert len(set(result.clarification.options)) == 2
    assert result.adopted_object_ids == []


def test_raw_correction_recovers_latest_value_without_task_snapshot() -> None:
    records = [
        _user("u0", "预算五千元"),
        _assistant("a0", "好的"),
        _user("u1", "改成三千元"),
        _assistant("a1", "好的"),
        _user("now", "之前说好的预算是多少"),
    ]
    result = resolve_references(
        request=records[-1].content, messages=records, current_user_message_id="now"
    )
    assert result.status == ReferenceStatus.RESOLVED
    assert {"u0", "u1"} <= set(result.recovered_message_ids)
    assert "u0" in result.correction_notes


def test_task_source_recovery_cap_is_reported_as_partial() -> None:
    records = [_user(f"u{i}", f"条件{i}") for i in range(5)] + [_user("now", "继续")]
    task = _task(source_message_ids=[f"u{i}" for i in range(5)])
    result = resolve_references(
        request="继续", messages=records, current_user_message_id="now", task=task
    )
    assert result.status == ReferenceStatus.PARTIAL
    assert len(result.recovered_message_ids) == 4
    assert result.missing_requirements


def test_recent_correction_precedes_longer_old_match() -> None:
    records = [
        _user("u0", "设备预算五千元"),
        _assistant("a0", "好的"),
        _user("u1", "预算改为三千元"),
        _assistant("a1", "好的"),
        _user("now", "之前设备预算是多少"),
    ]
    result = resolve_references(
        request=records[-1].content,
        messages=records,
        current_user_message_id="now",
        recent_message_ids=["u1", "a1"],
    )
    assert "u1" in result.adopted_message_ids


def test_raw_revocation_does_not_report_old_value_as_effective() -> None:
    records = [
        _user("u0", "预算五千元"),
        _assistant("a0", "好的"),
        _user("u1", "取消预算限制"),
        _assistant("a1", "好的"),
        _user("now", "之前说好的预算是多少"),
    ]
    result = resolve_references(
        request=records[-1].content, messages=records, current_user_message_id="now"
    )
    assert result.status == ReferenceStatus.PARTIAL
    assert "u0" in result.correction_notes
    assert "u1" in result.adopted_message_ids


def test_unrelated_negative_constraint_does_not_revoke_budget() -> None:
    records = [
        _user("u0", "预算五千元"), _assistant("a0", "好的"),
        _user("u1", "不用Windows，只要Linux"), _assistant("a1", "好的"),
        _user("now", "之前说好的预算是多少"),
    ]
    result = resolve_references(
        request=records[-1].content, messages=records, current_user_message_id="now"
    )
    assert result.status == ReferenceStatus.RESOLVED
    assert result.correction_notes == {}
    assert result.adopted_message_ids == ["u0"]


def test_explicit_list_reduction_never_selects_removed_item() -> None:
    records = [
        _user("u0", "找论文"),
        _assistant("a0", "旧版", paper_search=_paper_projection("甲", "乙")),
        _user("u1", "只留一个"),
        _assistant("a1", "新版", paper_search=_paper_projection("甲")),
        _user("now", "第二篇是什么"),
    ]
    result = resolve_references(
        request=records[-1].content, messages=records, current_user_message_id="now"
    )
    assert result.status == ReferenceStatus.UNRESOLVED
    assert result.adopted_object_ids == []


def test_ordinal_object_kind_is_not_overridden_by_followup_topic() -> None:
    records = [
        _user("u0", "找论文"),
        _assistant("a0", "论文", paper_search=_paper_projection("甲", "乙")),
        _user("u1", "找仓库"),
        _assistant("a1", "仓库", github_projects=_github_projection("o/a", "o/b")),
        _user("now", "第二个仓库对应哪篇论文"),
    ]
    result = resolve_references(
        request=records[-1].content, messages=records, current_user_message_id="now"
    )
    assert result.status == ReferenceStatus.RESOLVED
    assert result.adopted_object_ids == ["o/b"]


def test_retried_turn_does_not_inject_future_task_snapshot() -> None:
    records = [
        _user("u0", "原任务预算五千元"), _assistant("a0", "好的"),
        _user("now", "继续"), _assistant("a1", "好的"),
        _user("future", "新的任务预算九千元"),
    ]
    task = _task(
        goal="新的任务预算九千元", source_message_ids=["future"],
        conditions=[_condition("c0", "budget", "九千元", ConditionStatus.EFFECTIVE, "future")],
    )
    compiled = compile_turn_context(
        messages=records, current_user_message_id="now", model_id="probe-model",
        mode=ChatMode.COMPANION, task=task,
    )
    assert "九千元" not in _all_content(compiled)
    assert "五千元" in _all_content(compiled)
