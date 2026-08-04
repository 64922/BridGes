"""Issue 27: 任务级最小画像切片编译器测试。

验证：按对话模式（companion/study）只选择任务相关、仍有效且授权范围
匹配的最小记录；撤回/冻结/过期/敏感/场景不匹配/授权范围不匹配一律
排除；未确认候选绝不进入；单维度与总量最小化上限生效；值截断；跨
账户编译互不影响。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bridges.contracts.profiles import (
    AssertionStatus,
    ManualAssertionCreateRequest,
    ProfileDimension,
    ProfileSensitivityClass,
)
from bridges.profiles import InMemoryProfileRepository, ProfileService


@pytest.fixture
def service() -> ProfileService:
    return ProfileService(InMemoryProfileRepository())


def _manual(
    dimension: ProfileDimension,
    value: str,
    *,
    scenes: list[str] | None = None,
    sensitivity: ProfileSensitivityClass = ProfileSensitivityClass.PREFERENCE,
    authorization_scope: str = "general",
) -> ManualAssertionCreateRequest:
    return ManualAssertionCreateRequest(
        dimension=dimension,
        value_or_rule=value,
        applicable_scenes=scenes or ["companion", "study"],
        sensitivity_class=sensitivity,
        authorization_scope=authorization_scope,
        source_note="用户手动记录",
    )


def _included_dimensions(slice_) -> list[str]:
    return [item.dimension for item in slice_.included_items]


def _unused_reasons(slice_) -> list[str]:
    return [item.exclusion_reason for item in slice_.unused_items]


def test_study_mode_only_selects_learning_dimensions(service: ProfileService) -> None:
    """学习模式只取阶段目标/知识状态/兴趣；日常偏好不进入。"""
    for dimension, value in [
        (ProfileDimension.STAGE_GOAL, "目标：通过雅思考试"),
        (ProfileDimension.KNOWLEDGE_STATE, "微积分已掌握极限与导数"),
        (ProfileDimension.INTEREST_PREFERENCE, "对深度学习感兴趣"),
        (ProfileDimension.EXPRESSION_HABIT, "喜欢简洁回答"),
        (ProfileDimension.BASIC_INFORMATION, "在校大学生"),
    ]:
        service.manual_create_assertion("alice", _manual(dimension, value))

    slice_ = service.compile_chat_slice("alice", mode="study", run_id="run-1")
    assert set(_included_dimensions(slice_)) == {
        ProfileDimension.KNOWLEDGE_STATE.value,
        ProfileDimension.STAGE_GOAL.value,
        ProfileDimension.INTEREST_PREFERENCE.value,
    }
    # 日常维度被排除并给出原因
    reasons = _unused_reasons(slice_)
    assert any("不属于study模式可用范围" in reason for reason in reasons)


def test_companion_mode_only_selects_preference_dimensions(
    service: ProfileService,
) -> None:
    """日常模式只取偏好/表达习惯/基本情况，不把教学结构强加给陪伴。"""
    for dimension, value in [
        (ProfileDimension.STAGE_GOAL, "目标：通过雅思考试"),
        (ProfileDimension.KNOWLEDGE_STATE, "微积分已掌握极限与导数"),
        (ProfileDimension.EXPRESSION_HABIT, "喜欢简洁回答"),
        (ProfileDimension.BASIC_INFORMATION, "在校大学生"),
    ]:
        service.manual_create_assertion("alice", _manual(dimension, value))

    slice_ = service.compile_chat_slice("alice", mode="companion", run_id="run-1")
    assert set(_included_dimensions(slice_)) == {
        ProfileDimension.BASIC_INFORMATION.value,
        ProfileDimension.EXPRESSION_HABIT.value,
    }
    assert any("不属于companion模式可用范围" in r for r in _unused_reasons(slice_))


def test_withdrawn_and_frozen_assertions_are_excluded(
    service: ProfileService,
) -> None:
    """已撤回/已冻结记录禁止进入模型上下文，但保留排除原因。"""
    active = service.manual_create_assertion(
        "alice", _manual(ProfileDimension.KNOWLEDGE_STATE, "当前知识")
    )
    withdrawn = service.manual_create_assertion(
        "alice", _manual(ProfileDimension.STAGE_GOAL, "旧目标：学吉他")
    )
    frozen = service.manual_create_assertion(
        "alice", _manual(ProfileDimension.KNOWLEDGE_STATE, "冻结的旧知识")
    )
    service.withdraw_assertion("alice", withdrawn.assertion_id, "不再相关")
    service.freeze_assertion("alice", frozen.assertion_id, "手动冻结")

    slice_ = service.compile_chat_slice("alice", mode="study", run_id="run-1")
    included_ids = [item.assertion_id for item in slice_.included_items]
    assert active.assertion_id in included_ids  # 活动记录正常进入
    assert withdrawn.assertion_id not in included_ids
    assert frozen.assertion_id not in included_ids
    reasons = _unused_reasons(slice_)
    assert any("withdrawn" in reason for reason in reasons)
    assert any("frozen" in reason for reason in reasons)


def test_expired_assertion_is_excluded(service: ProfileService) -> None:
    """已过期记录不能进入本轮切片。"""
    active = service.manual_create_assertion(
        "alice", _manual(ProfileDimension.KNOWLEDGE_STATE, "当前知识状态")
    )
    expired = service.manual_create_assertion(
        "alice", _manual(ProfileDimension.STAGE_GOAL, "过期的旧目标")
    )
    service._repository.save_assertion(  # type: ignore[attr-defined]
        service._repository.get_assertion("alice", expired.assertion_id).model_copy(
            update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)}
        )
    )
    service._repository.save_assertion(  # type: ignore[attr-defined]
        service._repository.get_assertion("alice", active.assertion_id).model_copy(
            update={"expires_at": datetime.now(UTC) + timedelta(days=1)}
        )
    )

    slice_ = service.compile_chat_slice("alice", mode="study", run_id="run-1")
    assert expired.assertion_id not in [i.assertion_id for i in slice_.included_items]
    assert active.assertion_id in [i.assertion_id for i in slice_.included_items]
    assert any("已过期" in r for r in _unused_reasons(slice_))


def test_sensitive_records_default_excluded_from_chat_slice(
    service: ProfileService,
) -> None:
    """聊天切片默认排除 SENSITIVE：不随对话进入模型上下文（AC-01）。"""
    service.manual_create_assertion(
        "alice",
        _manual(
            ProfileDimension.KNOWLEDGE_STATE,
            "正在面对的健康问题",
            sensitivity=ProfileSensitivityClass.SENSITIVE,
        ),
    )
    service.manual_create_assertion(
        "alice",
        _manual(ProfileDimension.KNOWLEDGE_STATE, "普通知识状态"),
    )
    slice_ = service.compile_chat_slice("alice", mode="study", run_id="run-1")
    values = [item.value_or_rule for item in slice_.included_items]
    assert "正在面对的健康问题" not in values
    assert "普通知识状态" in values
    assert any("敏感度" in r for r in _unused_reasons(slice_))


def test_prohibited_sensitivity_is_excluded(service: ProfileService) -> None:
    """敏感度不允许的记录不能进入切片。

    PROHIBITED 在观察/候选源头就被系统拒绝（无法手动创建），编译器作为
    第二道闸门：即使存在 PROHIBITED 记录也会被排除；SENSITIVE 不在
    PREFERENCE 白名单内同样排除。
    """
    service.manual_create_assertion(
        "alice",
        _manual(
            ProfileDimension.KNOWLEDGE_STATE,
            "健康知识",
            sensitivity=ProfileSensitivityClass.SENSITIVE,
        ),
    )
    from bridges.contracts.profiles import (
        AssertionStatus,
        ProfileAssertion,
        ProfileSignalKind,
        ProfileSourceType,
    )

    # 直接注入一条 PROHIBITED 断言（模拟绕过源头的情形，验证编译器闸门）
    prohibited = ProfileAssertion(
        assertion_id="prohibited-1",
        owner_account_id="alice",
        canonical_dimension=ProfileDimension.STAGE_GOAL.value,
        value_or_rule="危险目标",
        applicable_scenes=["study"],
        authorization_scope="general",
        status=AssertionStatus.ACTIVE,
        sensitivity_class=ProfileSensitivityClass.PROHIBITED,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    service._repository.save_assertion(prohibited)  # type: ignore[attr-defined]

    slice_ = service.compile_chat_slice(
        "alice",
        mode="study",
        run_id="run-1",
        sensitivity_classes=[ProfileSensitivityClass.PREFERENCE],
    )
    values = [item.value_or_rule for item in slice_.included_items]
    assert "危险目标" not in values
    assert "健康知识" not in values  # SENSITIVE 也不在 PREFERENCE 白名单内
    assert any("敏感度" in r for r in _unused_reasons(slice_))


def test_authorization_scope_mismatch_is_excluded(service: ProfileService) -> None:
    """授权范围不匹配（既非 general 也非当前模式）的记录不进入。"""
    service.manual_create_assertion(
        "alice",
        _manual(
            ProfileDimension.KNOWLEDGE_STATE,
            "仅项目授权",
            authorization_scope="project:xyz",
        ),
    )
    service.manual_create_assertion(
        "alice",
        _manual(ProfileDimension.KNOWLEDGE_STATE, "通用授权知识"),
    )
    slice_ = service.compile_chat_slice("alice", mode="study", run_id="run-1")
    values = [item.value_or_rule for item in slice_.included_items]
    assert "仅项目授权" not in values
    assert "通用授权知识" in values
    assert any("授权范围" in r for r in _unused_reasons(slice_))


def test_scene_mismatch_is_excluded(service: ProfileService) -> None:
    """适用场景不包含当前模式的记录不进入。"""
    service.manual_create_assertion(
        "alice",
        _manual(
            ProfileDimension.INTEREST_PREFERENCE,
            "仅陪伴场景兴趣",
            scenes=["companion"],
        ),
    )
    slice_ = service.compile_chat_slice("alice", mode="study", run_id="run-1")
    assert slice_.included_items == []
    assert any("适用场景" in r for r in _unused_reasons(slice_))


def test_unconfirmed_candidates_never_enter_slice(service: ProfileService) -> None:
    """未确认候选进入 rejected_items，绝不作为稳定事实使用。"""
    from bridges.contracts.profiles import (
        ProfileCandidateCreateRequest,
        ProfileObservationCreateRequest,
        ProfileSignalKind,
        ProfileSourceType,
    )

    service.manual_create_assertion(
        "alice", _manual(ProfileDimension.KNOWLEDGE_STATE, "已确认知识")
    )
    observation = service.record_observation(
        ProfileObservationCreateRequest(
            owner_account_id="alice",
            source_type=ProfileSourceType.EXPLICIT_STATEMENT,
            source_ref="conv-1",
            source_span_or_event="msg-1",
            scene="study",
            purpose="knowledge_state",
            observed_content="我在学概率论",
            signal_kind=ProfileSignalKind.PRIOR_KNOWLEDGE,
            extractor_and_version="test-1",
            sensitivity_class=ProfileSensitivityClass.LEARNING,
            retention_policy="keep",
        ),
    )
    service.propose_candidate(
        "alice",
        canonical_dimension="knowledge_state",
        value_or_rule="候选：正在学习概率论",
        applicable_scenes=["study"],
        supporting_observation_ids=[observation.observation_id],
        evidence_summary="观察证据",
        authorization_scope="general",
    )

    slice_ = service.compile_chat_slice("alice", mode="study", run_id="run-1")
    values = [item.value_or_rule for item in slice_.included_items]
    assert "候选：正在学习概率论" not in values
    assert len(slice_.rejected_items) == 1
    assert "未经确认" in slice_.rejected_items[0].rejection_reason
    assert slice_.excluded_candidate_ids


def test_minimality_limits_per_dimension_and_total(service: ProfileService) -> None:
    """单维度最多 2 条、整卷最多 6 条；超出记录被排除并给出原因。"""
    # study 模式 3 个可参与维度：知识状态 ×4、阶段目标 ×4、兴趣 ×2 = 10 条
    for index in range(4):
        service.manual_create_assertion(
            "alice",
            _manual(
                ProfileDimension.KNOWLEDGE_STATE,
                f"知识记录 {index}",
                scenes=["study"],
            ),
        )
    for index in range(4):
        service.manual_create_assertion(
            "alice",
            _manual(
                ProfileDimension.STAGE_GOAL,
                f"目标 {index}",
                scenes=["study"],
            ),
        )
    for index in range(2):
        service.manual_create_assertion(
            "alice",
            _manual(
                ProfileDimension.INTEREST_PREFERENCE,
                f"兴趣 {index}",
                scenes=["study"],
            ),
        )
    slice_ = service.compile_chat_slice("alice", mode="study", run_id="run-1")
    # 单维度 ≤2、总量 ≤6（3 × 2 恰好满额；再多的记录被排除）
    knowledge = [
        i for i in slice_.included_items if i.dimension == "knowledge_state"
    ]
    goals = [i for i in slice_.included_items if i.dimension == "stage_goal"]
    assert len(knowledge) == 2
    assert len(goals) == 2
    assert len(slice_.included_items) == 6
    # 超出的记录被排除并给出原因
    reasons = _unused_reasons(slice_)
    assert any("单类别上限" in r for r in reasons)
    assert len(slice_.included_items) <= 6  # 总量上限兜底


def test_slice_value_summary_is_truncated(service: ProfileService) -> None:
    """超长值在切片中截断，不上传完整长文本。"""
    long_value = "长" * 500
    service.manual_create_assertion(
        "alice",
        _manual(ProfileDimension.KNOWLEDGE_STATE, long_value, scenes=["study"]),
    )
    slice_ = service.compile_chat_slice("alice", mode="study", run_id="run-1")
    assert slice_.included_items
    assert len(slice_.included_items[0].value_or_rule) <= 81  # 80 + 省略号
    assert slice_.included_items[0].value_or_rule.endswith("…")


def test_chat_slice_is_account_isolated(service: ProfileService) -> None:
    """跨账户编译互不影响：A 的记录绝不进入 B 的切片。"""
    service.manual_create_assertion(
        "alice",
        _manual(ProfileDimension.KNOWLEDGE_STATE, "Alice 的知识", scenes=["study"]),
    )
    service.manual_create_assertion(
        "bob",
        _manual(ProfileDimension.KNOWLEDGE_STATE, "Bob 的知识", scenes=["study"]),
    )
    alice_slice = service.compile_chat_slice("alice", mode="study", run_id="run-a")
    bob_slice = service.compile_chat_slice("bob", mode="study", run_id="run-b")
    assert [i.value_or_rule for i in alice_slice.included_items] == ["Alice 的知识"]
    assert [i.value_or_rule for i in bob_slice.included_items] == ["Bob 的知识"]


def test_chat_slice_updates_last_used_at(service: ProfileService) -> None:
    """编译时更新最近使用时间，供画像中心展示「最近使用」。"""
    assertion = service.manual_create_assertion(
        "alice",
        _manual(ProfileDimension.KNOWLEDGE_STATE, "知识", scenes=["study"]),
    )
    assert assertion.last_used_at is None
    service.compile_chat_slice("alice", mode="study", run_id="run-1")
    updated = service.get_assertion("alice", assertion.assertion_id)
    assert updated.last_used_at is not None


def test_unknown_mode_fails_closed(service: ProfileService) -> None:
    """未知模式不匹配任何维度，切片为空但不报错（安全兜底）。"""
    service.manual_create_assertion(
        "alice", _manual(ProfileDimension.KNOWLEDGE_STATE, "知识")
    )
    slice_ = service.compile_chat_slice("alice", mode="unknown-mode", run_id="run-1")
    assert slice_.included_items == []
    assert len(slice_.unused_items) >= 1
