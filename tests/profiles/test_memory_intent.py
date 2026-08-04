"""Issue 26: 记忆意图提取、分级许可、自动写入、候选箱与一键撤回。

The seam under test: labeled conversation messages map to visible memory
intents through deterministic rules; low-risk auto-write happens only under a
user-granted category+scene permission (default off, never model-granted);
sensitive content only enters the candidate box; transient emotions stay in
the session; dedup, frozen categories, permission recall, one-click recall,
batch decisions, account isolation and restart persistence all hold.
"""

from __future__ import annotations

import pytest

from bridges.contracts.observability import AuditAction
from bridges.contracts.profiles import (
    USER_CONFIRMED_DIMENSIONS,
    AssertionStatus,
    CandidateReviewStatus,
    DecisionType,
    ProfileBatchCandidateDecisionRequest,
    ProfileDimension,
    ProfileNotificationKind,
    ProfilePermissionUpdateRequest,
)
from bridges.invalidation import InvalidationService
from bridges.observability.service import ObservabilityService
from bridges.profiles import (
    InMemoryProfileRepository,
    ProfileError,
    ProfileService,
)


@pytest.fixture
def repository() -> InMemoryProfileRepository:
    return InMemoryProfileRepository()


@pytest.fixture
def observability_service() -> ObservabilityService:
    return ObservabilityService()


@pytest.fixture
def invalidation_service() -> InvalidationService:
    return InvalidationService()


@pytest.fixture
def service(
    repository: InMemoryProfileRepository,
    invalidation_service: InvalidationService,
    observability_service: ObservabilityService,
) -> ProfileService:
    return ProfileService(
        repository=repository,
        invalidation_service=invalidation_service,
        observability_service=observability_service,
    )


@pytest.fixture
def alice_id() -> str:
    return "account-alice"


def _send(
    service: ProfileService,
    account_id: str,
    content: str,
    *,
    message_id: str = "m1",
    conversation_id: str = "c1",
    mode: str = "companion",
):
    return service.process_conversation_message(
        account_id,
        message_id=message_id,
        conversation_id=conversation_id,
        content=content,
        mode=mode,
    )


def _permission(
    service: ProfileService,
    account_id: str,
    dimension: ProfileDimension,
    scene: str = "companion",
    enabled: bool = True,
) -> None:
    service.set_permission(
        account_id,
        ProfilePermissionUpdateRequest(
            dimension=dimension, scene=scene, enabled=enabled
        ),
    )


# ----------------------------------------------------------------------
# 明确记忆意图（标注对话集）
# ----------------------------------------------------------------------


class TestExplicitRemember:
    def test_low_risk_remember_writes_evidence_record(
        self, service: ProfileService, alice_id: str
    ) -> None:
        notifications = _send(
            service, alice_id, "记住我的目标是今年通过雅思考试"
        )
        assert len(notifications) == 1
        assert notifications[0].kind == ProfileNotificationKind.INTENT_RECORDED
        assertions = service.list_assertions(alice_id)
        assert len(assertions) == 1
        assert assertions[0].canonical_dimension == ProfileDimension.STAGE_GOAL.value
        assert assertions[0].value_or_rule == "今年通过雅思考试"
        assert assertions[0].status == AssertionStatus.ACTIVE
        # 证据可追溯：候选链保留来源观察
        assert assertions[0].supporting_observation_ids

    def test_remember_without_category_prompts_not_guesses(
        self, service: ProfileService, alice_id: str
    ) -> None:
        notifications = _send(service, alice_id, "记住这件事")
        assert len(notifications) == 1
        assert notifications[0].kind == ProfileNotificationKind.INTENT_RECORDED
        assert "类别" in notifications[0].message
        # 不猜测类别：不产生任何断言
        assert service.list_assertions(alice_id) == []
        assert service.list_candidates(alice_id) == []

    def test_sensitive_remember_only_enters_candidate_box(
        self, service: ProfileService, alice_id: str
    ) -> None:
        for content, dimension in [
            ("记住我的情绪是焦虑", ProfileDimension.EMOTION_TREND),
            ("记住我的经历是去年参加过数学竞赛", ProfileDimension.IMPORTANT_EXPERIENCE),
            ("记住我正在面对的问题是论文写不完", ProfileDimension.CURRENT_PROBLEM),
        ]:
            notifications = _send(
                service, alice_id, content, message_id=f"m-{dimension.value}"
            )
            assert len(notifications) == 1
            assert notifications[0].kind == ProfileNotificationKind.CANDIDATE_PROPOSED
            assert "确认" in notifications[0].message
        # 敏感候选未确认前绝不成为断言（不进入跨会话切片事实）
        assert service.list_assertions(alice_id) == []
        candidates = service.list_candidates(alice_id)
        assert len(candidates) == 3
        assert all(
            candidate.review_status == CandidateReviewStatus.PROPOSED
            for candidate in candidates
        )
        assert {c.canonical_dimension for c in candidates} == {
            d.value for d in USER_CONFIRMED_DIMENSIONS
        }
        # 候选确认后进入断言；拒绝则排除在切片之外
        target = next(
            c for c in candidates if c.canonical_dimension == ProfileDimension.EMOTION_TREND.value
        )
        service.decide_candidates_batch(
            alice_id,
            ProfileBatchCandidateDecisionRequest(
                candidate_ids=[target.candidate_id],
                decision=DecisionType.ACCEPT,
                reason="用户确认",
            ),
        )
        assertions = service.list_assertions(alice_id)
        assert len(assertions) == 1
        assert assertions[0].value_or_rule == "焦虑"

    def test_remember_audited(self, service: ProfileService, alice_id: str) -> None:
        _send(service, alice_id, "记住我的目标是今年通过雅思考试")
        actions = {
            event.action
            for event in service._observability_service.list_audit_events(  # type: ignore[attr-defined]
                account_id=alice_id
            )
        }
        assert AuditAction.PROFILE_INTENT in actions
        assert AuditAction.PROFILE_CANDIDATE_DECISION in actions

    def test_explicit_remember_overrides_auto_write(
        self, service: ProfileService, alice_id: str
    ) -> None:
        _permission(
            service, alice_id, ProfileDimension.INTEREST_PREFERENCE
        )
        notifications = _send(service, alice_id, "记住我喜欢蓝色")
        # 显式记住优先：只有 intent_recorded 一条，无 auto_write
        assert len(notifications) == 1
        assert notifications[0].kind == ProfileNotificationKind.INTENT_RECORDED
        notifications = _send(
            service, alice_id, "记住我喜欢蓝色", message_id="m2"
        )
        # 同一事实去重：合并证据，不重复通知
        assert notifications == []
        assert len(service.list_assertions(alice_id)) == 1


class TestForgetAndSessionOnly:
    def test_forget_blocks_future_writes_and_withdraws_existing(
        self, service: ProfileService, alice_id: str
    ) -> None:
        _permission(service, alice_id, ProfileDimension.INTEREST_PREFERENCE)
        _send(service, alice_id, "我喜欢蓝色")
        assert len(service.list_assertions(alice_id)) == 1
        notifications = _send(service, alice_id, "不要记住我喜欢蓝色")
        assert len(notifications) == 1
        assert notifications[0].kind == ProfileNotificationKind.INTENT_RECORDED
        # 既有记录被撤回（保留审计历史），不是静默删除
        assertion = service.list_assertions(alice_id)[0]
        assert assertion.status == AssertionStatus.WITHDRAWN
        # 未来同一事实不再自动写入
        notifications = _send(service, alice_id, "我喜欢蓝色", message_id="m3")
        assert notifications == []
        assert len(service.list_assertions(alice_id)) == 1

    def test_forget_without_target_still_notifies(
        self, service: ProfileService, alice_id: str
    ) -> None:
        notifications = _send(service, alice_id, "别记住这个")
        assert len(notifications) == 1
        assert "不会写入长期画像" in notifications[0].message

    def test_session_only_stays_in_conversation(
        self, service: ProfileService, alice_id: str
    ) -> None:
        notifications = _send(service, alice_id, "只在本对话使用，我的目标先不定")
        assert len(notifications) == 1
        assert "只作为本次对话" in notifications[0].message
        assert service.list_assertions(alice_id) == []
        # 后续许可内的同句自动写入被阻断（显式"仅会话"指令仍会确认提示）
        _permission(service, alice_id, ProfileDimension.STAGE_GOAL)
        notifications = _send(
            service, alice_id, "只在本对话使用，我的目标先不定", message_id="m2"
        )
        assert len(notifications) == 1
        assert notifications[0].kind == ProfileNotificationKind.INTENT_RECORDED
        assert service.list_assertions(alice_id) == []

    def test_session_only_wins_over_remember(
        self, service: ProfileService, alice_id: str
    ) -> None:
        notifications = _send(
            service, alice_id, "记住我的目标是过雅思，但只在本对话使用"
        )
        assert service.list_assertions(alice_id) == []
        assert any(
            n.kind == ProfileNotificationKind.INTENT_RECORDED for n in notifications
        )


class TestTransientEmotion:
    def test_transient_emotion_never_becomes_long_term_fact(
        self, service: ProfileService, alice_id: str
    ) -> None:
        notifications = _send(service, alice_id, "我今天有点焦虑")
        assert len(notifications) == 1
        assert notifications[0].kind == ProfileNotificationKind.TRANSIENT_EMOTION
        assert service.list_assertions(alice_id) == []
        assert service.list_candidates(alice_id) == []
        assert service.list_observations(alice_id) == []

    def test_transient_emotion_not_persisted_across_sessions(
        self, service: ProfileService, alice_id: str
    ) -> None:
        # 会话一：情绪消息 → 无长期事实
        _send(service, alice_id, "我今天有点焦虑", conversation_id="c1")
        # 会话结束、新建会话：情绪不进入长期画像
        _send(service, alice_id, "我们继续学习", conversation_id="c2")
        assert service.list_assertions(alice_id) == []

    def test_transient_hint_once_per_message(
        self, service: ProfileService, alice_id: str
    ) -> None:
        notifications = _send(service, alice_id, "我今天有点焦虑和难过")
        transient = [
            n for n in notifications if n.kind == ProfileNotificationKind.TRANSIENT_EMOTION
        ]
        assert len(transient) == 1


# ----------------------------------------------------------------------
# 分级许可
# ----------------------------------------------------------------------


class TestPermissions:
    def test_default_off_no_auto_write(
        self, service: ProfileService, alice_id: str
    ) -> None:
        notifications = _send(service, alice_id, "我喜欢蓝色")
        assert notifications == []
        assert service.list_assertions(alice_id) == []
        assert service.list_notifications(alice_id) == []

    def test_granted_permission_auto_writes_with_notification_and_recall(
        self, service: ProfileService, alice_id: str
    ) -> None:
        _permission(service, alice_id, ProfileDimension.INTEREST_PREFERENCE)
        notifications = _send(service, alice_id, "我喜欢蓝色")
        assert len(notifications) == 1
        assert notifications[0].kind == ProfileNotificationKind.AUTO_WRITE
        assert notifications[0].recallable is True
        assert notifications[0].assertion_id is not None
        assertions = service.list_assertions(alice_id)
        assert len(assertions) == 1
        assert assertions[0].value_or_rule == "蓝色"
        assert assertions[0].applicable_scenes == ["companion"]

    def test_scene_isolation(self, service: ProfileService, alice_id: str) -> None:
        _permission(service, alice_id, ProfileDimension.INTEREST_PREFERENCE, scene="companion")
        notifications = _send(
            service, alice_id, "我喜欢蓝色", mode="study"
        )
        # companion 许可不作用于 study 场景
        assert notifications == []
        assert service.list_assertions(alice_id) == []

    def test_permission_cannot_be_granted_for_sensitive_dimension(
        self, service: ProfileService, alice_id: str
    ) -> None:
        with pytest.raises(ProfileError):
            _permission(service, alice_id, ProfileDimension.EMOTION_TREND)
        with pytest.raises(ProfileError):
            _permission(
                service, alice_id, ProfileDimension.IMPORTANT_EXPERIENCE, scene="study"
            )

    def test_model_or_background_cannot_grant_permission(
        self, service: ProfileService, alice_id: str
    ) -> None:
        # 服务层没有任何"推断授权"入口：唯一写许可的 set_permission 需要
        # 用户显式请求，且仅接受 AUTO_WRITABLE 类别；process_conversation_message
        # 只读许可，永不创建许可行。
        assert service.list_permissions(alice_id) == []
        _send(service, alice_id, "请你帮我记住我所有的偏好", message_id="m1")
        _send(service, alice_id, "我今天有点焦虑", message_id="m2")
        _send(service, alice_id, "我想你记住我", message_id="m3")
        assert service.list_permissions(alice_id) == []

    def test_disabling_permission_stops_future_writes_keeps_records(
        self, service: ProfileService, alice_id: str
    ) -> None:
        _permission(service, alice_id, ProfileDimension.INTEREST_PREFERENCE)
        _send(service, alice_id, "我喜欢蓝色")
        _permission(
            service,
            alice_id,
            ProfileDimension.INTEREST_PREFERENCE,
            enabled=False,
        )
        notifications = _send(service, alice_id, "我喜欢绿色", message_id="m2")
        assert notifications == []
        # 既有记录不被静默删除
        assert len(service.list_assertions(alice_id)) == 1
        assert service.list_assertions(alice_id)[0].value_or_rule == "蓝色"

    def test_permission_changes_audited(
        self, service: ProfileService, alice_id: str
    ) -> None:
        _permission(service, alice_id, ProfileDimension.STAGE_GOAL)
        _permission(
            service, alice_id, ProfileDimension.STAGE_GOAL, enabled=False
        )
        actions = {
            event.action
            for event in service._observability_service.list_audit_events(  # type: ignore[attr-defined]
                account_id=alice_id
            )
        }
        assert AuditAction.PROFILE_PERMISSION_ENABLE in actions
        assert AuditAction.PROFILE_PERMISSION_DISABLE in actions


class TestAutoWriteGates:
    def test_frozen_category_refuses_auto_write(
        self, service: ProfileService, alice_id: str
    ) -> None:
        _permission(service, alice_id, ProfileDimension.INTEREST_PREFERENCE)
        _send(service, alice_id, "我喜欢蓝色")
        assertion = service.list_assertions(alice_id)[0]
        service.freeze_assertion(alice_id, assertion.assertion_id, "用户冻结")
        notifications = _send(service, alice_id, "我喜欢绿色", message_id="m2")
        # 冻结类别不接收自动写入
        assert notifications == []
        assert len(service.list_assertions(alice_id)) == 1

    def test_dedup_merges_evidence_without_duplicate_notifications(
        self, service: ProfileService, alice_id: str
    ) -> None:
        _permission(service, alice_id, ProfileDimension.STAGE_GOAL)
        _send(service, alice_id, "我的目标是读完三本书")
        _send(service, alice_id, "我的目标是读完三本书", message_id="m2")
        notifications = _send(
            service, alice_id, "我的目标是读完三本书", message_id="m3"
        )
        assert notifications == []
        assertions = service.list_assertions(alice_id)
        assert len(assertions) == 1
        # 证据合并：三条来源观察都挂在同一断言上
        assert len(assertions[0].supporting_observation_ids) == 3
        audit_writes = [
            event
            for event in service._observability_service.list_audit_events(  # type: ignore[attr-defined]
                account_id=alice_id
            )
            if event.action == AuditAction.PROFILE_AUTO_WRITE
        ]
        assert len(audit_writes) >= 3

    def test_one_click_recall_withdraws_and_blocks(
        self, service: ProfileService, alice_id: str
    ) -> None:
        _permission(service, alice_id, ProfileDimension.INTEREST_PREFERENCE)
        _send(service, alice_id, "我喜欢蓝色")
        notification = service.list_notifications(alice_id)[0]
        recalled = service.recall_auto_write(alice_id, notification.notification_id)
        assert recalled.recalled_at is not None
        assert service.list_assertions(alice_id)[0].status == AssertionStatus.WITHDRAWN
        # 幂等：重复撤回直接返回
        again = service.recall_auto_write(alice_id, notification.notification_id)
        assert again.recalled_at is not None
        # 撤回后同一事实不再自动写入
        notifications = _send(service, alice_id, "我喜欢蓝色", message_id="m2")
        assert notifications == []
        assert len(service.list_assertions(alice_id)) == 1

    def test_recall_audited(
        self, service: ProfileService, alice_id: str
    ) -> None:
        _permission(service, alice_id, ProfileDimension.STAGE_GOAL)
        _send(service, alice_id, "我的目标是读完三本书")
        notification = service.list_notifications(alice_id)[0]
        service.recall_auto_write(alice_id, notification.notification_id)
        actions = {
            event.action
            for event in service._observability_service.list_audit_events(  # type: ignore[attr-defined]
                account_id=alice_id
            )
        }
        assert AuditAction.PROFILE_AUTO_WRITE_RECALL in actions
        assert AuditAction.PROFILE_WITHDRAW in actions

    def test_expression_habit_hint_guard(
        self, service: ProfileService, alice_id: str
    ) -> None:
        _permission(service, alice_id, ProfileDimension.EXPRESSION_HABIT)
        # "我习惯早起"不是表达风格，不写入
        notifications = _send(service, alice_id, "我习惯早起")
        assert notifications == []
        # "叫我小谷"是称呼类表达习惯，写入
        notifications = _send(service, alice_id, "请叫我小谷", message_id="m2")
        assert len(notifications) == 1
        assert service.list_assertions(alice_id)[0].value_or_rule == "小谷"


# ----------------------------------------------------------------------
# 候选批量决策
# ----------------------------------------------------------------------


class TestBatchDecisions:
    def test_batch_accept_and_idempotent_retry(
        self, service: ProfileService, alice_id: str
    ) -> None:
        _send(service, alice_id, "记住我的情绪是焦虑", message_id="m1")
        _send(service, alice_id, "记住我的经历是去年参加过数学竞赛", message_id="m2")
        candidates = service.list_candidates(alice_id)
        assert len(candidates) == 2
        result = service.decide_candidates_batch(
            alice_id,
            ProfileBatchCandidateDecisionRequest(
                candidate_ids=[c.candidate_id for c in candidates],
                decision=DecisionType.ACCEPT,
                reason="用户批量确认",
            ),
        )
        assert len(result.succeeded) == 2
        assert result.failed == []
        # 幂等重试：已确认的记为 already_decided，不重复写入
        result = service.decide_candidates_batch(
            alice_id,
            ProfileBatchCandidateDecisionRequest(
                candidate_ids=[c.candidate_id for c in candidates],
                decision=DecisionType.ACCEPT,
                reason="用户批量确认",
            ),
        )
        assert len(result.already_decided) == 2
        assert result.succeeded == []
        assert len(service.list_assertions(alice_id)) == 2

    def test_batch_reject_and_cross_account(
        self, service: ProfileService, alice_id: str, repository: InMemoryProfileRepository
    ) -> None:
        bob_id = "account-bob"
        _send(service, alice_id, "记住我的情绪是焦虑", message_id="m1")
        _send(service, bob_id, "记住我的情绪是担忧", message_id="m1", conversation_id="c2")
        result = service.decide_candidates_batch(
            alice_id,
            ProfileBatchCandidateDecisionRequest(
                candidate_ids=[c.candidate_id for c in service.list_candidates(alice_id)],
                decision=DecisionType.REJECT,
                reason="用户拒绝",
            ),
        )
        assert len(result.succeeded) == 1
        assert result.failed == []
        # bob 的候选不受 alice 的批量操作影响
        bob_candidates = repository.list_candidates(bob_id)
        assert len(bob_candidates) == 1
        assert bob_candidates[0].review_status == CandidateReviewStatus.PROPOSED

    def test_batch_modify(
        self, service: ProfileService, alice_id: str
    ) -> None:
        _send(service, alice_id, "记住我的情绪是焦虑", message_id="m1")
        candidate = service.list_candidates(alice_id)[0]
        result = service.decide_candidates_batch(
            alice_id,
            ProfileBatchCandidateDecisionRequest(
                candidate_ids=[candidate.candidate_id],
                decision=DecisionType.MODIFY,
                reason="用户编辑后确认",
                modified_value_or_rule="轻度焦虑",
            ),
        )
        assert result.succeeded == [candidate.candidate_id]
        assertions = service.list_assertions(alice_id)
        assert assertions[0].value_or_rule == "轻度焦虑"


# ----------------------------------------------------------------------
# 跨账户隔离与通知
# ----------------------------------------------------------------------


class TestAccountIsolation:
    def test_notifications_isolated(
        self, service: ProfileService, alice_id: str
    ) -> None:
        bob_id = "account-bob"
        _permission(service, alice_id, ProfileDimension.INTEREST_PREFERENCE)
        _send(service, alice_id, "我喜欢蓝色")
        assert len(service.list_notifications(alice_id)) == 1
        assert service.list_notifications(bob_id) == []

    def test_background_processing_cannot_cross_account(
        self, service: ProfileService, alice_id: str
    ) -> None:
        bob_id = "account-bob"
        # 模拟后台任务串号：bob 的消息用 alice 的账户 ID 处理被拒绝，
        # 许可与断言都按账户隔离写入。
        _permission(service, alice_id, ProfileDimension.INTEREST_PREFERENCE)
        _send(service, alice_id, "我喜欢蓝色")
        with pytest.raises(ProfileError):
            service.recall_auto_write(
                bob_id, service.list_notifications(alice_id)[0].notification_id
            )
        # alice 的撤回动作不能作用于 bob（bob 无通知，直接 404 语义）
        with pytest.raises(ProfileError):
            service.mark_notification_read(
                bob_id, service.list_notifications(alice_id)[0].notification_id
            )


# ----------------------------------------------------------------------
# 重启持久化（SQLite 仓库）
# ----------------------------------------------------------------------


class TestRestartPersistence:
    def test_permissions_assertions_notifications_survive_restart(
        self, tmp_path, alice_id: str
    ) -> None:
        from bridges.profiles.sqlite_repository import SqliteProfileRepository
        from bridges.storage.database import BridgesDatabase

        path = tmp_path / "bridges.db"
        db = BridgesDatabase(path)
        repo = SqliteProfileRepository(db)
        service = ProfileService(repository=repo)
        _permission(service, alice_id, ProfileDimension.INTEREST_PREFERENCE)
        _send(service, alice_id, "我喜欢蓝色")
        _send(service, alice_id, "我今天有点焦虑", message_id="m2")

        # 模拟重启：同一文件新建连接
        db2 = BridgesDatabase(path)
        repo2 = SqliteProfileRepository(db2)
        service2 = ProfileService(repository=repo2)
        permissions = service2.list_permissions(alice_id)
        assert len(permissions) == 1
        assert permissions[0].enabled is True
        assertions = service2.list_assertions(alice_id)
        assert len(assertions) == 1
        assert assertions[0].value_or_rule == "蓝色"
        notifications = service2.list_notifications(alice_id)
        assert {n.kind for n in notifications} == {
            ProfileNotificationKind.AUTO_WRITE,
            ProfileNotificationKind.TRANSIENT_EMOTION,
        }
        # 单次情绪在重启后仍不是长期画像事实
        assert all(
            a.canonical_dimension != ProfileDimension.EMOTION_TREND.value
            for a in assertions
        )

    def test_sqlite_repo_isolates_accounts(self, tmp_path, alice_id: str) -> None:
        from bridges.profiles.sqlite_repository import SqliteProfileRepository
        from bridges.storage.database import BridgesDatabase

        bob_id = "account-bob"
        db = BridgesDatabase(tmp_path / "bridges.db")
        repo = SqliteProfileRepository(db)
        service = ProfileService(repository=repo)
        _permission(service, alice_id, ProfileDimension.INTEREST_PREFERENCE)
        _send(service, alice_id, "我喜欢蓝色")
        assert repo.list_assertions(bob_id) == []
        assert repo.list_notifications(bob_id) == []
        assert repo.list_permissions(bob_id) == []

    def test_slice_roundtrip_through_sqlite(self, tmp_path, alice_id: str) -> None:
        from bridges.profiles.sqlite_repository import SqliteProfileRepository
        from bridges.storage.database import BridgesDatabase

        db = BridgesDatabase(tmp_path / "bridges.db")
        repo = SqliteProfileRepository(db)
        service = ProfileService(repository=repo)
        _send(service, alice_id, "记住我的目标是今年通过雅思考试")
        slice_ = service.compile_memory_slice(
            alice_id, purpose="companion", run_id="run-1"
        )
        assert slice_.included_items
        fetched = repo.list_slices_for_run(alice_id, "run-1")
        assert len(fetched) == 1
        assert fetched[0].included_items[0].value_or_rule == "今年通过雅思考试"


# ----------------------------------------------------------------------
# 会话情境边界（重启 + 新会话）
# ----------------------------------------------------------------------


class TestSessionBoundary:
    def test_confirmed_records_still_used_after_new_conversation(
        self, service: ProfileService, alice_id: str
    ) -> None:
        _send(service, alice_id, "记住我的目标是今年通过雅思考试")
        # 新建对话后，已确认记录仍可用于切片
        slice_ = service.compile_memory_slice(
            alice_id, purpose="companion", run_id="run-new"
        )
        assert [i.value_or_rule for i in slice_.included_items] == [
            "今年通过雅思考试"
        ]

    def test_unconfirmed_candidate_never_enters_slice(
        self, service: ProfileService, alice_id: str
    ) -> None:
        _send(service, alice_id, "记住我的情绪是焦虑")
        slice_ = service.compile_memory_slice(
            alice_id, purpose="companion", run_id="run-1"
        )
        assert slice_.included_items == []
        assert any(
            c.candidate_id in slice_.excluded_candidate_ids
            for c in service.list_candidates(alice_id)
        )


# ----------------------------------------------------------------------
# code-review 回归：审查修复验证
# ----------------------------------------------------------------------


class TestReviewRegressions:
    def test_descriptive_forget_phrasing_is_not_a_command(
        self, service: ProfileService, alice_id: str
    ) -> None:
        # "我忘了密码"是描述性陈述，不是记忆指令：不产生任何通知与阻断。
        notifications = _send(service, alice_id, "我忘了密码，帮我找回一下")
        assert notifications == []
        assert service.list_assertions(alice_id) == []

    def test_retry_round_does_not_duplicate_observations(
        self, service: ProfileService, alice_id: str
    ) -> None:
        # 同一消息（重试轮）重跑意图管线：断言/通知/观察均不重复累积。
        _send(service, alice_id, "记住我的目标是今年通过雅思考试", message_id="m-retry")
        _send(service, alice_id, "记住我的目标是今年通过雅思考试", message_id="m-retry")
        assertions = service.list_assertions(alice_id)
        assert len(assertions) == 1
        assert len(assertions[0].supporting_observation_ids) == 1
        observations = service.list_observations(alice_id)
        assert len(observations) == 1

    def test_session_only_blocks_auto_write_of_same_content(
        self, service: ProfileService, alice_id: str
    ) -> None:
        # 审查复现场景："只在本对话使用"后，同内容的许可内自动写入必须被阻断
        # （阻断键与自动写入提取键对齐，而不是落在错位的键上）。
        _permission(service, alice_id, ProfileDimension.STAGE_GOAL)
        _send(service, alice_id, "只在本对话使用，我的目标是读完三本书", message_id="m1")
        assert service.list_assertions(alice_id) == []
        notifications = _send(
            service, alice_id, "我的目标是读完三本书", message_id="m2"
        )
        assert notifications == []
        assert service.list_assertions(alice_id) == []

    def test_study_scene_auto_write_positive(
        self, service: ProfileService, alice_id: str
    ) -> None:
        _permission(
            service, alice_id, ProfileDimension.STAGE_GOAL, scene="study"
        )
        notifications = _send(
            service, alice_id, "我的目标是读完三本书", mode="study"
        )
        assert len(notifications) == 1
        assert notifications[0].kind == ProfileNotificationKind.AUTO_WRITE
        assertions = service.list_assertions(alice_id)
        assert assertions[0].applicable_scenes == ["study"]

    def test_candidate_propose_audited_on_all_paths(
        self, service: ProfileService, alice_id: str
    ) -> None:
        # API/手动路径与聊天管线一样，候选提出都写 PROFILE_CANDIDATE_PROPOSE 审计。
        _send(service, alice_id, "记住我的情绪是焦虑")
        from bridges.contracts.profiles import (
            ProfileObservationCreateRequest,
        )

        service.record_observation(
            ProfileObservationCreateRequest(
                owner_account_id=alice_id,
                source_type="explicit_statement",
                source_ref="manual-path",
                source_span_or_event="m1",
                scene="companion",
                purpose="propose-audit",
                observed_content="手动候选",
                signal_kind="preference",
                extractor_and_version="test-1",
                sensitivity_class="preference",
                retention_policy="account_lifetime",
            )
        )
        observation = service.list_observations(alice_id)[0]
        service.propose_candidate(
            alice_id,
            canonical_dimension="interest_preference",
            value_or_rule="手动候选",
            supporting_observation_ids=[observation.observation_id],
        )
        proposes = [
            event
            for event in service._observability_service.list_audit_events(  # type: ignore[attr-defined]
                account_id=alice_id
            )
            if event.action == AuditAction.PROFILE_CANDIDATE_PROPOSE
        ]
        assert len(proposes) == 2
