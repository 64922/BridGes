"""画像工单 07：当前状态、历史经历与有效期。

覆盖验收：

- 相对日期按产品默认时区（Asia/Shanghai）的自然日/周解释，来源靠近本地
  零点、跨日/跨周重试都不漂移；周期安排（每周三晚上）解析为结构化条件，
  长期有效、不设统一 TTL。
- 周期安排与过期约束只按词面相关进入当前用途切片；召回不改变事实与
  可靠度（没有访问计数或衰减）。
- 已完成/已过期的真实经历只在明确回顾（复盘）任务里按历史采用；错误
  更正与撤回墓碑绝不作为经历读取。
- 完成、暂停、恢复只操作明确定位的唯一目标；歧义时不批量修改；「继续」
  不自动延长旧期限，续期需要新的明确日期。
- 页面依据（证据投影）与存储、采用一致，含周期安排与时效状态；SQLite
  重启后状态保持。

内存与 SQLite 两种 adapter 跑同一组时间/采用场景，保证持久化形态不改变
业务结果；API 证据投影单独在落盘组合下验证。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from bridges.api.main import create_app
from bridges.config import get_settings
from bridges.contracts.atomic_profile import (
    AtomicProfileGoalState,
    AtomicProfileItem,
    AtomicProfileItemModifyRequest,
    AtomicProfileRecurrenceKind,
    AtomicProfileRevisionKind,
    AtomicProfileValidityStatus,
)
from bridges.contracts.profile_adoption import (
    ProfileSlicePurpose,
    ProfileTaskKind,
)
from bridges.contracts.profiles import FourDimension, FourDimensionConfidence
from bridges.profiles.adapters import InMemoryProfileRepository
from bridges.profiles.atomic import (
    PROFILE_TIMEZONE,
    AtomicProfileRepository,
    AtomicProfileService,
    InMemoryAtomicProfileRepository,
    SqliteAtomicProfileRepository,
    parse_recurrence,
    parse_validity_window,
)
from bridges.profiles.commit import ProfileFactSubmission
from bridges.profiles.four_dimensions import (
    FourDimensionProfileRepository,
    FourDimensionProfileService,
    InMemoryFourDimensionProfileRepository,
    SqliteFourDimensionProfileRepository,
)
from bridges.storage.database import BridgesDatabase

ACCOUNT = "account-alice"
#: 可控来源时间锚：2026-01-05 是周一（Asia/Shanghai 17:00）。
ANCHOR = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
LOCAL = ZoneInfo(PROFILE_TIMEZONE)
EXAM_TEXT = "我下周有英语六级考试"
GOAL_TEXT = "我计划下周考六级"
LAB_TEXT = "我每周三晚上去实验室做实验"


def _local(y: int, m: int, d: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(y, m, d, hour, minute, tzinfo=LOCAL)


def _utc(moment: datetime) -> datetime:
    return moment.astimezone(UTC)


@dataclass
class _Stack:
    database: BridgesDatabase | None
    dimensions: FourDimensionProfileRepository
    atomic: AtomicProfileRepository

    def close(self) -> None:
        if self.database is not None:
            self.database.close()


def _open_stack(flavor: str, db_path: Path) -> _Stack:
    if flavor == "sqlite":
        database = BridgesDatabase(db_path)
        database.initialize()
        return _Stack(
            database=database,
            dimensions=SqliteFourDimensionProfileRepository(database),
            atomic=SqliteAtomicProfileRepository(database, initialize=False),
        )
    return _Stack(
        database=None,
        dimensions=InMemoryFourDimensionProfileRepository(),
        atomic=InMemoryAtomicProfileRepository(),
    )


class _Harness:
    def __init__(self, stack: _Stack) -> None:
        self._stack = stack
        self.repository = stack.atomic
        self.atomic = AtomicProfileService(
            FourDimensionProfileService(
                source_repository=InMemoryProfileRepository(),
                repository=stack.dimensions,
            ),
            stack.atomic,
        )

    def close(self) -> None:
        self._stack.close()

    def commit(
        self,
        text: str,
        *,
        message_id: str | None = None,
        source_at: datetime | None = None,
        evidence_quote: str | None = None,
        dimension: FourDimension | None = None,
    ) -> AtomicProfileItem | None:
        return self.atomic.commit_fact(
            ACCOUNT,
            ProfileFactSubmission(
                text=text,
                dimension=dimension,
                confidence=FourDimensionConfidence.HIGH,
                evidence_quote=evidence_quote,
                evidence_message_id=message_id,
            ),
            source_at=source_at,
        )

    def item_of(self, text: str) -> AtomicProfileItem:
        return next(
            item for item in self.atomic.list_items(ACCOUNT) if item.text == text
        )

    def review(
        self,
        query: str,
        *,
        now: datetime | None = None,
        run_id: str = "run-review",
    ) -> Any:
        return self.atomic.compile_adopted_slice(
            ACCOUNT,
            run_id=run_id,
            purpose=ProfileSlicePurpose(
                mode="companion", task_kind=ProfileTaskKind.REVIEW, query=query
            ),
            now=now,
        )

    def slice(
        self,
        query: str,
        *,
        now: datetime | None = None,
        task_kind: ProfileTaskKind = ProfileTaskKind.GENERAL,
    ) -> Any:
        return self.atomic.compile_adopted_slice(
            ACCOUNT,
            run_id="run-current",
            purpose=ProfileSlicePurpose(
                mode="companion", task_kind=task_kind, query=query
            ),
            now=now,
        )


@pytest.fixture(params=["memory", "sqlite"])
def harness(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[_Harness]:
    built = _Harness(_open_stack(request.param, tmp_path / "bridges.db"))
    try:
        yield built
    finally:
        built.close()


def _adopted_texts(slice_: Any) -> set[str]:
    return {entry.fact_text for entry in slice_.adopted_items}


def _exclusion_reason(slice_: Any, profile_item_id: str) -> str | None:
    return next(
        (
            entry.exclusion_reason
            for entry in slice_.excluded_items
            if entry.profile_item_id == profile_item_id
        ),
        None,
    )


# ---------------------------------------------------------------------------
# 相对日期与时区：本地自然日边界、跨日重试不漂移
# ---------------------------------------------------------------------------


def test_relative_dates_use_product_timezone_day_boundaries(
    harness: _Harness,
) -> None:
    """本地 23:30 说的「明天」是本地次日，而不是 UTC 次日。"""

    late_night = _utc(_local(2026, 1, 5, 23, 30))  # 2026-01-05T15:30Z
    item = harness.commit("我明天早上有专业课", message_id="m-class", source_at=late_night)
    assert item is not None
    assert item.valid_from == _utc(_local(2026, 1, 6))
    assert item.valid_until == _utc(_local(2026, 1, 6, 23, 59)) + timedelta(seconds=59)

    # 本地次日白天仍在窗口内；本地次日结束（UTC 01-06T16:30Z）即过期。
    during = harness.slice("我明天早上有专业课", now=_local(2026, 1, 6, 18))
    assert "我明天早上有专业课" in _adopted_texts(during)
    after = harness.slice("我明天早上有专业课", now=_local(2026, 1, 7, 0, 30))
    assert "我明天早上有专业课" not in _adopted_texts(after)
    reason = _exclusion_reason(after, item.profile_item_id)
    assert reason is not None and reason.startswith("已过期")


def test_new_source_message_reanchors_across_days_without_drift(
    harness: _Harness,
) -> None:
    """同一相对表达按各自来源消息解释；重试沿用原锚，不按重试当天漂移。"""

    first = _utc(_local(2026, 1, 5, 23, 30))
    harness.commit("我明天要交作业", message_id="m-job-1", source_at=first)
    item = harness.item_of("我明天要交作业")
    first_until = item.valid_until
    assert first_until == _utc(_local(2026, 1, 6, 23, 59)) + timedelta(seconds=59)

    second = _utc(_local(2026, 1, 6, 8, 30))
    harness.commit("我明天要交作业", message_id="m-job-2", source_at=second)
    reanchored = harness.item_of("我明天要交作业")
    assert reanchored.validity_anchor_at == second
    assert reanchored.valid_until == _utc(_local(2026, 1, 7, 23, 59)) + timedelta(
        seconds=59
    )
    assert set(reanchored.source_message_ids) == {"m-job-1", "m-job-2"}


def test_parse_validity_window_accepts_explicit_timezone() -> None:
    """解释时区是显式参数：同一锚在不同时区得到不同的本地周界。"""

    shanghai = parse_validity_window("我下周有考试", ANCHOR)
    utc = parse_validity_window("我下周有考试", ANCHOR, timezone="UTC")
    assert shanghai is not None and utc is not None
    assert shanghai.valid_from == datetime(2026, 1, 11, 16, 0, tzinfo=UTC)
    assert utc.valid_from == datetime(2026, 1, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# 周期安排：结构化解析、长期有效、按约束进入当前用途
# ---------------------------------------------------------------------------


def test_weekly_schedule_parses_without_expiry(harness: _Harness) -> None:
    """「每周三晚上」解析为结构化周期：没有期限，多年后仍按约束可用。"""

    item = harness.commit(LAB_TEXT, message_id="m-lab", source_at=ANCHOR)
    assert item is not None
    assert item.recurrence is not None
    assert item.recurrence.kind is AtomicProfileRecurrenceKind.WEEKLY
    assert item.recurrence.weekdays == (2,)
    assert item.recurrence.time_of_day == "晚上"
    assert item.recurrence.phrase == "每周三晚上"
    assert item.valid_until is None

    far_future = harness.slice(
        f"帮我安排复习计划，晚上要避开{LAB_TEXT}",
        now=ANCHOR + timedelta(days=365 * 3),
        task_kind=ProfileTaskKind.PLAN,
    )
    assert LAB_TEXT in _adopted_texts(far_future)


def test_recurrence_parser_covers_daily_weekend_and_lists() -> None:
    daily = parse_recurrence("我每天背单词")
    assert daily is not None
    assert daily.kind is AtomicProfileRecurrenceKind.DAILY
    weekend = parse_recurrence("我每周末去图书馆")
    assert weekend is not None and weekend.weekdays == (5, 6)
    listed = parse_recurrence("我每周三、五晚上跑步")
    assert listed is not None and listed.weekdays == (2, 4)
    assert parse_recurrence("我平时喜欢跑步") is None


def test_repeated_use_does_not_change_facts_or_confidence(
    harness: _Harness,
) -> None:
    """反复采用不改变事实：没有访问计数、衰减或统一过期。"""

    item = harness.commit(LAB_TEXT, message_id="m-lab", source_at=ANCHOR)
    assert item is not None
    before = harness.item_of(LAB_TEXT)
    baseline = (before.version, before.confidence, before.updated_at)

    for index in range(4):
        harness.slice(
            f"第 {index + 1} 次问：怎么安排晚自习和{LAB_TEXT}",
            now=ANCHOR + timedelta(days=index),
            task_kind=ProfileTaskKind.PLAN,
        )
    after = harness.item_of(LAB_TEXT)
    assert (after.version, after.confidence, after.updated_at) == baseline
    assert after.recurrence is not None and after.recurrence.phrase == "每周三晚上"


def test_evidence_projection_exposes_recurrence_and_validity(
    harness: _Harness,
) -> None:
    lab = harness.commit(LAB_TEXT, message_id="m-lab", source_at=ANCHOR)
    exam = harness.commit(EXAM_TEXT, message_id="m-exam", source_at=ANCHOR)
    assert lab is not None and exam is not None

    lab_evidence = harness.atomic.item_evidence(ACCOUNT, lab.profile_item_id, now=ANCHOR)
    assert lab_evidence.recurrence is not None
    assert lab_evidence.recurrence.phrase == "每周三晚上"
    assert lab_evidence.validity_status is AtomicProfileValidityStatus.UNBOUNDED

    expired = harness.atomic.item_evidence(
        ACCOUNT, exam.profile_item_id, now=ANCHOR + timedelta(days=20)
    )
    assert expired.validity_status is AtomicProfileValidityStatus.EXPIRED
    assert expired.recurrence is None
    assert expired.validity_phrase == "下周"


# ---------------------------------------------------------------------------
# 生命周期定位：歧义不批量修改、精确目标优先
# ---------------------------------------------------------------------------


def test_ambiguous_lifecycle_signal_leaves_every_goal_untouched(
    harness: _Harness,
) -> None:
    """「暂时不考研」面对考研英语/考研数学无法确定对象：不修改任何一条。"""

    harness.atomic.remember(ACCOUNT, "我计划考研英语")
    harness.atomic.remember(ACCOUNT, "我计划考研数学")

    outcome = harness.atomic.apply_lifecycle_signal(ACCOUNT, "暂时不考研")

    assert outcome is None
    states = {
        item.text: item.goal_state for item in harness.atomic.list_items(ACCOUNT)
    }
    assert states == {
        "我计划考研英语": AtomicProfileGoalState.ACTIVE,
        "我计划考研数学": AtomicProfileGoalState.ACTIVE,
    }


def test_exact_goal_target_wins_over_containment(harness: _Harness) -> None:
    """目标词精确命中时只操作该目标，不波及含同词的其他目标。"""

    harness.atomic.remember(ACCOUNT, "我计划考研")
    harness.atomic.remember(ACCOUNT, "我计划考研英语")

    outcome = harness.atomic.apply_lifecycle_signal(ACCOUNT, "暂时不考研了")

    assert outcome is not None and outcome.matched_count == 1
    states = {
        item.text: item.goal_state for item in harness.atomic.list_items(ACCOUNT)
    }
    assert states["我计划考研"] is AtomicProfileGoalState.PAUSED
    assert states["我计划考研英语"] is AtomicProfileGoalState.ACTIVE


# ---------------------------------------------------------------------------
# 恢复不续期：旧期限保留，过期不因「继续」复活；新日期才更新
# ---------------------------------------------------------------------------


def test_resume_keeps_old_deadline_and_expired_goal_stays_out(
    harness: _Harness,
) -> None:
    goal = harness.atomic.remember(ACCOUNT, GOAL_TEXT, source_at=ANCHOR)
    assert goal.valid_until == _utc(_local(2026, 1, 18, 23, 59)) + timedelta(seconds=59)

    harness.atomic.apply_lifecycle_signal(ACCOUNT, "暂时不考六级")
    paused = harness.item_of(GOAL_TEXT)
    assert paused.goal_state is AtomicProfileGoalState.PAUSED

    # 过期之后「继续」：解除暂停，但旧窗口不被延长，仍按过期排除。
    after_deadline = ANCHOR + timedelta(days=20)
    resumed = harness.atomic.apply_lifecycle_signal(ACCOUNT, "继续备考六级了")
    assert resumed is not None and resumed.matched_count == 1
    restored = harness.item_of(GOAL_TEXT)
    assert restored.goal_state is AtomicProfileGoalState.ACTIVE
    assert restored.valid_until == paused.valid_until

    current = harness.slice(f"帮我规划{GOAL_TEXT}", now=after_deadline)
    assert GOAL_TEXT not in _adopted_texts(current)
    reason = _exclusion_reason(current, restored.profile_item_id)
    assert reason is not None and reason.startswith("已过期")


def test_resume_with_new_explicit_date_renews_window(harness: _Harness) -> None:
    """续期需要新的明确依据：带新日期的「继续」才更新窗口。"""

    harness.atomic.remember(ACCOUNT, GOAL_TEXT, source_at=ANCHOR)
    harness.atomic.apply_lifecycle_signal(ACCOUNT, "暂时不考六级")
    harness.atomic.apply_lifecycle_signal(
        ACCOUNT, "继续准备，我计划下个月考六级", source_at=ANCHOR
    )

    renewed = harness.item_of(GOAL_TEXT)
    assert renewed.goal_state is AtomicProfileGoalState.ACTIVE
    # 「下个月」= 本地 2026-02-01 ~ 2026-02-28。
    assert renewed.valid_from == _utc(_local(2026, 2, 1))
    assert renewed.valid_until == _utc(_local(2026, 2, 28, 23, 59)) + timedelta(
        seconds=59
    )
    assert renewed.validity_phrase == "下个月"


# ---------------------------------------------------------------------------
# 历史回顾：真实经历只在复盘任务里读取，纠错与撤回永不作为经历
# ---------------------------------------------------------------------------


def test_expired_experience_enters_review_purpose_only(harness: _Harness) -> None:
    item = harness.commit(EXAM_TEXT, message_id="m-exam", source_at=ANCHOR)
    assert item is not None
    after = ANCHOR + timedelta(days=30)

    current = harness.slice(f"帮我规划{EXAM_TEXT}", now=after)
    assert EXAM_TEXT not in _adopted_texts(current)

    review = harness.review(
        "帮我复盘一下英语六级考试的经历", now=after, run_id="run-review-expired"
    )
    adopted = {entry.fact_text: entry for entry in review.adopted_items}
    assert EXAM_TEXT in adopted
    assert adopted[EXAM_TEXT].adoption_reason == "既往经历（回顾用途）"
    assert adopted[EXAM_TEXT].applicable_to == ("review_history",)
    assert review.select_subset("review_history")


def test_review_history_slice_stays_current_until_item_changes(
    harness: _Harness,
) -> None:
    """回归：过期正是回顾采用的原因，发送前失效核对不能因此判失效。"""

    item = harness.commit(EXAM_TEXT, message_id="m-exam", source_at=ANCHOR)
    assert item is not None
    after = ANCHOR + timedelta(days=30)
    review = harness.review(
        "帮我复盘一下英语六级考试的经历",
        now=after,
        run_id="run-still-current",
    )
    assert EXAM_TEXT in _adopted_texts(review)
    assert harness.atomic.is_adopted_slice_current(ACCOUNT, review, now=after)

    harness.atomic.modify_item(
        ACCOUNT,
        item.profile_item_id,
        AtomicProfileItemModifyRequest(
            text="我上个月考完了英语六级", version=item.version
        ),
    )
    assert not harness.atomic.is_adopted_slice_current(ACCOUNT, review, now=after)


def test_completed_goal_is_history_not_current_plan(harness: _Harness) -> None:
    harness.atomic.remember(ACCOUNT, "我计划三个月后考完雅思")
    harness.atomic.apply_lifecycle_signal(ACCOUNT, "雅思考完了")
    completed = harness.item_of("我计划三个月后考完雅思")
    assert completed.goal_state is AtomicProfileGoalState.COMPLETED

    current = harness.slice("帮我制定雅思的后续计划")
    assert "我计划三个月后考完雅思" not in _adopted_texts(current)
    reason = _exclusion_reason(current, completed.profile_item_id)
    assert reason is not None and reason.startswith("目标已完成")

    review = harness.review("回顾一下我计划三个月后考完雅思的经历")
    assert "我计划三个月后考完雅思" in _adopted_texts(review)


def test_correction_never_reads_as_history_state_change_does() -> None:
    """状态变化保留为经历；明确认错的旧值是错误记录，不作经历。"""

    _, state_change_service = _memory_service()
    state_change_service.commit_fact(
        ACCOUNT,
        ProfileFactSubmission(
            text="大二",
            dimension=FourDimension.ACADEMIC_STATUS,
            confidence=FourDimensionConfidence.HIGH,
            evidence_quote="我现在大二",
            evidence_message_id="m1",
        ),
        source_at=ANCHOR,
    )
    state_change_service.commit_fact(
        ACCOUNT,
        ProfileFactSubmission(
            text="大三",
            dimension=FourDimension.ACADEMIC_STATUS,
            confidence=FourDimensionConfidence.HIGH,
            evidence_quote="我现在升大三了",
            evidence_message_id="m2",
        ),
        source_at=ANCHOR + timedelta(days=1),
    )
    history = [
        item
        for item in state_change_service._repository.list_items(
            ACCOUNT, include_withdrawn=True
        )
        if item.text == "大二"
    ]
    assert history[0].retired_reason is AtomicProfileRevisionKind.STATE_CHANGE
    review = state_change_service.compile_adopted_slice(
        ACCOUNT,
        run_id="run-state-change",
        purpose=ProfileSlicePurpose(
            mode="companion",
            task_kind=ProfileTaskKind.REVIEW,
            query="回顾我从大二开始的经历",
        ),
    )
    assert "大二" in {entry.fact_text for entry in review.adopted_items}

    _, correction_service = _memory_service()
    correction_service.commit_fact(
        ACCOUNT,
        ProfileFactSubmission(
            text="大四",
            dimension=FourDimension.ACADEMIC_STATUS,
            confidence=FourDimensionConfidence.HIGH,
            evidence_quote="我现在大四",
            evidence_message_id="m1",
        ),
        source_at=ANCHOR,
    )
    correction_service.commit_fact(
        ACCOUNT,
        ProfileFactSubmission(
            text="大三",
            dimension=FourDimension.ACADEMIC_STATUS,
            confidence=FourDimensionConfidence.HIGH,
            evidence_quote="我说错了，我现在大三",
            evidence_message_id="m2",
        ),
        source_at=ANCHOR + timedelta(days=1),
    )
    retired = [
        item
        for item in correction_service._repository.list_items(
            ACCOUNT, include_withdrawn=True
        )
        if item.text == "大四"
    ]
    assert retired[0].retired_reason is AtomicProfileRevisionKind.CORRECTION
    review = correction_service.compile_adopted_slice(
        ACCOUNT,
        run_id="run-correction",
        purpose=ProfileSlicePurpose(
            mode="companion",
            task_kind=ProfileTaskKind.REVIEW,
            query="回顾我大四的经历",
        ),
    )
    assert "大四" not in {entry.fact_text for entry in review.adopted_items}


def _memory_service() -> tuple[AtomicProfileRepository, AtomicProfileService]:
    repository = InMemoryAtomicProfileRepository()
    service = AtomicProfileService(
        FourDimensionProfileService(
            InMemoryProfileRepository(), InMemoryFourDimensionProfileRepository()
        ),
        repository,
    )
    return repository, service


# ---------------------------------------------------------------------------
# 页面/API 与存储一致：证据投影携带周期安排
# ---------------------------------------------------------------------------


@pytest.fixture
def sqlite_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "issue07-redesign-test-secret")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    return create_app()


def test_api_evidence_returns_recurrence_for_page_consistency(
    sqlite_app: Any,
) -> None:
    client = TestClient(sqlite_app)
    registered = client.post(
        "/auth/register",
        json={
            "username": "issue07-redesign",
            "qq_email": "070001@qq.com",
            "password": "correct-horse-07",
        },
    )
    assert registered.status_code == 201, registered.text
    account_id = registered.json()["account"]["id"]
    item = sqlite_app.state.atomic_profile_service.remember(
        account_id, LAB_TEXT, source_message_id="m-lab"
    )

    evidence = client.get(f"/profiles/items/{item.profile_item_id}/evidence")

    assert evidence.status_code == 200, evidence.text
    body = evidence.json()
    assert body["recurrence"]["kind"] == "weekly"
    assert body["recurrence"]["weekdays"] == [2]
    assert body["recurrence"]["time_of_day"] == "晚上"
    assert body["recurrence"]["phrase"] == "每周三晚上"
    assert body["validity_status"] == "unbounded"
    listed = client.get("/profiles/items").json()
    assert [entry["text"] for entry in listed] == [LAB_TEXT]
