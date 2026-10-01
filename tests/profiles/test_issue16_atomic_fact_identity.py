"""Issue 16：以完整事实身份保存并处理并存更新。

覆盖验收：喜欢/正在学习保留关系不推出已掌握；年级/专业与考研/六级并存，
明确变更只替代对应事实；来源、编辑权威与被替代版本可追溯；旧条目迁移补齐
身份且报告可对账；用户编辑换值后旧身份不复活。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from bridges.contracts.atomic_profile import (
    AtomicProfileFactRelation,
    AtomicProfileItem,
    AtomicProfileItemModifyRequest,
    AtomicProfileItemStatus,
    AtomicProfileWriteOrigin,
)
from bridges.contracts.profiles import FourDimension, FourDimensionConfidence
from bridges.profiles.adapters import InMemoryProfileRepository
from bridges.profiles.atomic import (
    AtomicProfileService,
    InMemoryAtomicProfileRepository,
    SqliteAtomicProfileRepository,
    identity_key,
    parse_explicit_change,
    parse_fact_identity,
)
from bridges.profiles.automatic import (
    AutomaticProfileService,
    InMemoryAutomaticProfileRepository,
)
from bridges.profiles.four_dimensions import (
    FourDimensionProfileService,
    InMemoryFourDimensionProfileRepository,
)
from bridges.storage.database import BridgesDatabase

ACCOUNT = "account-alice"


def _services() -> tuple[
    FourDimensionProfileService,
    InMemoryAtomicProfileRepository,
    AtomicProfileService,
    AutomaticProfileService,
]:
    four = FourDimensionProfileService(
        InMemoryProfileRepository(), InMemoryFourDimensionProfileRepository()
    )
    repository = InMemoryAtomicProfileRepository()
    atomic = AtomicProfileService(four, repository)
    automatic = AutomaticProfileService(
        four_dimension_service=four,
        repository=InMemoryAutomaticProfileRepository(),
        atomic_profile_service=atomic,
    )
    return four, repository, atomic, automatic


def _ingest(automatic: AutomaticProfileService, text: str, number: int) -> None:
    automatic.preprocess_message(
        ACCOUNT,
        conversation_id="conversation-1",
        message_id=f"message-{number}",
        content=text,
        run_id=f"run-{number}",
        mode="daily",
    )


def _texts(atomic: AtomicProfileService) -> set[str]:
    return {item.text for item in atomic.list_items(ACCOUNT)}


def _item(atomic: AtomicProfileService, text: str) -> AtomicProfileItem:
    return next(item for item in atomic.list_items(ACCOUNT) if item.text == text)


# ---------------------------------------------------------------------------
# 身份解析：关系从完整事实分句中来
# ---------------------------------------------------------------------------


def test_parse_fact_identity_reads_relation_from_full_clause() -> None:
    interest = parse_fact_identity("我喜欢Python")
    learning = parse_fact_identity("我正在学习Python")
    assert interest.relation == AtomicProfileFactRelation.INTEREST
    assert interest.object == "Python"
    assert learning.relation == AtomicProfileFactRelation.LEARNING
    assert learning.object == "Python"

    grade = parse_fact_identity("我是大二学生")
    major = parse_fact_identity("我是软件工程专业")
    assert (grade.relation, grade.object) == (AtomicProfileFactRelation.GRADE, "大二")
    assert (major.relation, major.object) == (AtomicProfileFactRelation.MAJOR, "软件工程")

    goal = parse_fact_identity("我计划考研")
    assert (goal.relation, goal.object) == (AtomicProfileFactRelation.GOAL, "考研")


def test_negation_only_statement_is_not_a_new_positive_fact() -> None:
    identity = parse_fact_identity("我不考研了")
    assert identity.relation == AtomicProfileFactRelation.STATEMENT
    assert identity.object == "我不考研了"


def test_parse_explicit_change_reads_withdrawal_and_redirect() -> None:
    assert parse_explicit_change("我不考研了，转为准备就业") == (["考研"], "准备就业")


# ---------------------------------------------------------------------------
# 并存：不同关系/属性/目标互不覆盖
# ---------------------------------------------------------------------------


def test_preference_and_learning_coexist_with_relations_preserved() -> None:
    _, _, atomic, automatic = _services()
    _ingest(automatic, "我喜欢Python", 1)
    _ingest(automatic, "我正在学习Python", 2)

    assert _texts(atomic) == {"我喜欢Python", "我正在学习Python"}
    assert _item(atomic, "我喜欢Python").fact_relation == (
        AtomicProfileFactRelation.INTEREST
    )
    assert _item(atomic, "我正在学习Python").fact_relation == (
        AtomicProfileFactRelation.LEARNING
    )


def test_grade_and_major_coexist() -> None:
    _, _, atomic, automatic = _services()
    _ingest(automatic, "我是大二学生", 1)
    _ingest(automatic, "我是软件工程专业", 2)

    assert _texts(atomic) == {"大二学生", "软件工程专业"}
    assert _item(atomic, "大二学生").fact_relation == AtomicProfileFactRelation.GRADE
    assert _item(atomic, "软件工程专业").fact_relation == (
        AtomicProfileFactRelation.MAJOR
    )


def test_parallel_goals_coexist() -> None:
    _, _, atomic, automatic = _services()
    _ingest(automatic, "我计划考研", 1)
    _ingest(automatic, "我计划通过英语六级", 2)

    assert _texts(atomic) == {"考研", "通过英语六级"}


def test_close_synonyms_are_not_auto_merged() -> None:
    _, _, atomic, _ = _services()
    atomic.remember(ACCOUNT, "我喜欢跑步")
    atomic.remember(ACCOUNT, "我爱慢跑")

    assert _texts(atomic) == {"我喜欢跑步", "我爱慢跑"}


# ---------------------------------------------------------------------------
# 明确变更：只替代对应事实，保留可追溯的被替代版本
# ---------------------------------------------------------------------------


def test_explicit_change_replaces_only_the_withdrawn_goal() -> None:
    _, repository, atomic, _ = _services()
    atomic.remember(ACCOUNT, "我计划考研")
    atomic.remember(ACCOUNT, "我计划通过英语六级")

    atomic.remember(ACCOUNT, "我不考研了，转为准备就业")

    assert _texts(atomic) == {"我不考研了，转为准备就业", "我计划通过英语六级"}
    old = next(
        item
        for item in repository.list_items(ACCOUNT, include_withdrawn=True)
        if item.fact_object == "考研"
    )
    new = _item(atomic, "我不考研了，转为准备就业")
    assert old.status == AtomicProfileItemStatus.SUPERSEDED
    assert old.superseded_by_id == new.profile_item_id
    assert new.supersedes_id == old.profile_item_id


def test_pure_withdrawal_replaces_only_the_matching_fact() -> None:
    _, repository, atomic, _ = _services()
    atomic.remember(ACCOUNT, "我计划考研")
    atomic.remember(ACCOUNT, "我计划通过英语六级")

    atomic.remember(ACCOUNT, "我不考研了")

    assert _texts(atomic) == {"我计划通过英语六级", "我不考研了"}
    old = next(
        item
        for item in repository.list_items(ACCOUNT, include_withdrawn=True)
        if item.fact_object == "考研" and item.text == "我计划考研"
    )
    assert old.status == AtomicProfileItemStatus.SUPERSEDED
    assert old.superseded_by_id is not None


def test_withdrawal_preserves_other_relations_and_scopes() -> None:
    _, _, atomic, _ = _services()
    atomic.remember(ACCOUNT, "我喜欢Python")
    atomic.remember(ACCOUNT, "我正在学习Python")
    atomic.remember(ACCOUNT, "今天我喜欢Python")

    atomic.remember(ACCOUNT, "我不喜欢Python了")

    assert _texts(atomic) == {
        "我正在学习Python", "今天我喜欢Python", "我不喜欢Python了"
    }


def test_remember_rewording_updates_text_identity_key() -> None:
    _, repository, atomic, _ = _services()
    first = atomic.remember(ACCOUNT, "我喜欢Python")
    updated = atomic.remember(ACCOUNT, "我特别喜欢Python")

    assert updated.profile_item_id == first.profile_item_id
    assert updated.identity_key == identity_key(ACCOUNT, updated.text)
    assert repository.find_item_by_identity(ACCOUNT, updated.identity_key) is not None


def test_grade_word_in_interest_does_not_replace_actual_grade() -> None:
    _, _, atomic, _ = _services()
    atomic.remember(ACCOUNT, "我现在大二")
    item = atomic.remember(ACCOUNT, "我喜欢大三的高等数学课")

    assert item.fact_relation == AtomicProfileFactRelation.INTEREST
    assert _texts(atomic) == {"我现在大二", "我喜欢大三的高等数学课"}


@pytest.mark.parametrize("sqlite", [False, True])
def test_replacement_uses_only_new_value_evidence_messages(
    tmp_path: Path, sqlite: bool,
) -> None:
    four, repository, atomic, _ = _services()
    if sqlite:
        database = BridgesDatabase(tmp_path / "bridges.db")
        database.initialize()
        repository = SqliteAtomicProfileRepository(database, initialize=False)
        atomic = AtomicProfileService(four, repository)
    first = four.upsert_automatic_record(
        ACCOUNT, dimension=FourDimension.ACADEMIC_STATUS, content="大二",
        action="create", confidence=FourDimensionConfidence.HIGH,
    )
    atomic.mirror_record(ACCOUNT, first, evidence_message_id="old-message")
    second = first.model_copy(update={"content": "大三"})
    new = atomic.mirror_record(ACCOUNT, second, evidence_message_id="new-message")

    assert new is not None
    assert new.source_message_ids == ["new-message"]
    old = repository.get_item(ACCOUNT, new.supersedes_id)
    assert old.source_message_ids == ["old-message"]

    third = atomic.mirror_record(
        ACCOUNT, first.model_copy(update={"content": "大四"}),
        evidence_message_id="third-message",
    )
    assert third is not None
    assert _texts(atomic) == {"大四"}
    assert third.source_message_ids == ["third-message"]
    assert atomic.mirror_record(ACCOUNT, first, evidence_message_id="old-message") is None
    assert _texts(atomic) == {"大四"}


def test_single_valued_slot_replacement_keeps_history_traceable() -> None:
    four, repository, atomic, _ = _services()
    first = four.upsert_automatic_record(
        ACCOUNT,
        dimension=FourDimension.ACADEMIC_STATUS,
        content="大二",
        action="create",
        confidence=FourDimensionConfidence.HIGH,
        evidence_quote="我现在大二",
    )
    atomic.mirror_record(ACCOUNT, first)
    second = four.upsert_automatic_record(
        ACCOUNT,
        dimension=FourDimension.ACADEMIC_STATUS,
        content="大三",
        action="create",
        confidence=FourDimensionConfidence.HIGH,
        evidence_quote="我现在大三了",
    )
    atomic.mirror_record(ACCOUNT, second)

    assert _texts(atomic) == {"大三"}
    old = next(
        item
        for item in repository.list_items(ACCOUNT, include_withdrawn=True)
        if item.fact_object == "大二"
    )
    new = _item(atomic, "大三")
    assert old.status == AtomicProfileItemStatus.SUPERSEDED
    assert new.supersedes_id == old.profile_item_id
    assert new.evidence_quote == "我现在大三了"
    projection = next(
        entry for entry in atomic.projections(ACCOUNT) if entry.text == "大三"
    )
    assert projection.evidence_quote == "我现在大三了"
    assert projection.supersedes_id == old.profile_item_id


def test_automatic_extraction_never_overwrites_user_edited_fact() -> None:
    _, _, atomic, _ = _services()
    original = atomic.remember(ACCOUNT, "我喜欢跑步")
    atomic.modify_item(
        ACCOUNT,
        original.profile_item_id,
        AtomicProfileItemModifyRequest(text="我最爱跑步", version=original.version),
    )
    four = FourDimensionProfileService(
        InMemoryProfileRepository(), InMemoryFourDimensionProfileRepository()
    )
    record = four.upsert_automatic_record(
        ACCOUNT,
        dimension=FourDimension.HOBBY,
        content="跑步",
        action="create",
        confidence=FourDimensionConfidence.HIGH,
    )

    mirrored = atomic.mirror_record(
        ACCOUNT, record, fact_text="我特别喜欢跑步"
    )

    assert mirrored is not None
    assert mirrored.text == "我最爱跑步"
    assert _texts(atomic) == {"我最爱跑步"}


def test_new_value_does_not_inherit_previous_evidence_quote() -> None:
    four, _, atomic, _ = _services()
    first = four.upsert_automatic_record(
        ACCOUNT,
        dimension=FourDimension.ACADEMIC_STATUS,
        content="大二",
        action="create",
        confidence=FourDimensionConfidence.HIGH,
        evidence_quote="我现在大二",
    )
    atomic.mirror_record(ACCOUNT, first)
    second = four.upsert_automatic_record(
        ACCOUNT,
        dimension=FourDimension.ACADEMIC_STATUS,
        content="大三",
        action="create",
        confidence=FourDimensionConfidence.HIGH,
    )
    atomic.mirror_record(ACCOUNT, second)

    item = _item(atomic, "大三")
    assert item.evidence_quote is None


def test_user_edit_suppression_uses_original_fact_relation() -> None:
    _, _, atomic, _ = _services()
    original = atomic.remember(ACCOUNT, "我计划考研")
    atomic.modify_item(
        ACCOUNT,
        original.profile_item_id,
        AtomicProfileItemModifyRequest(text="我计划就业", version=original.version),
    )
    four = FourDimensionProfileService(
        InMemoryProfileRepository(), InMemoryFourDimensionProfileRepository()
    )
    record = four.upsert_automatic_record(
        ACCOUNT,
        dimension=FourDimension.STAGE_GOAL,
        content="考研",
        action="create",
        confidence=FourDimensionConfidence.HIGH,
    )

    assert atomic.mirror_record(ACCOUNT, record, fact_text="我计划考研") is None
    assert _texts(atomic) == {"我计划就业"}


def test_user_edit_changes_fact_slot_and_suppresses_old_identity() -> None:
    _, _, atomic, _ = _services()
    original = atomic.remember(ACCOUNT, "我喜欢跑步")
    edited = atomic.modify_item(
        ACCOUNT,
        original.profile_item_id,
        AtomicProfileItemModifyRequest(text="我喜欢游泳", version=original.version),
    )

    assert edited.fact_relation == AtomicProfileFactRelation.INTEREST
    assert edited.fact_object == "游泳"
    assert _texts(atomic) == {"我喜欢游泳"}

    # 旧值即使再次被自动抽取镜像，也不得复活为活动条目。
    four = FourDimensionProfileService(
        InMemoryProfileRepository(), InMemoryFourDimensionProfileRepository()
    )
    record = four.upsert_automatic_record(
        ACCOUNT,
        dimension=FourDimension.HOBBY,
        content="跑步",
        action="create",
        confidence=FourDimensionConfidence.HIGH,
    )
    assert atomic.mirror_record(ACCOUNT, record, fact_text="我喜欢跑步") is None
    assert _texts(atomic) == {"我喜欢游泳"}


# ---------------------------------------------------------------------------
# 迁移：旧条目补齐身份、幂等、报告可对账
# ---------------------------------------------------------------------------


def _legacy_item(account_id: str, text: str, *, topic_hint: str | None) -> AtomicProfileItem:
    now = datetime.now(UTC)
    return AtomicProfileItem(
        profile_item_id=f"item-legacy-{text}",
        owner_account_id=account_id,
        text=text,
        identity_key=identity_key(account_id, text),
        topic_hint=topic_hint,
        status=AtomicProfileItemStatus.ACTIVE,
        write_origin=AtomicProfileWriteOrigin.AUTOMATIC,
        confidence=FourDimensionConfidence.HIGH,
        version=1,
        created_at=now,
        updated_at=now,
        user_edited_at=None,
        migration_run_id=None,
    )


def test_migration_backfills_identity_and_reports_convergence() -> None:
    _, repository, atomic, _ = _services()
    repository.save_item(_legacy_item(ACCOUNT, "我是大二学生", topic_hint=None))
    repository.save_item(_legacy_item(ACCOUNT, "我喜欢跑步", topic_hint="hobby"))

    report = atomic.migrate_account(ACCOUNT)

    assert report.identity_backfilled == 2
    assert report.converged is True
    backfilled = repository.find_item_by_identity(
        ACCOUNT, identity_key(ACCOUNT, "我是大二学生")
    )
    assert backfilled is not None
    assert backfilled.fact_relation == AtomicProfileFactRelation.GRADE
    assert backfilled.fact_object == "大二"
    assert backfilled.fact_key != ""

    # 幂等：再次迁移不重复补写。
    second = atomic.migrate_account(ACCOUNT)
    assert second.identity_backfilled == 0


def test_migration_does_not_infer_missing_relation_from_legacy_dimension() -> None:
    four, repository, atomic, _ = _services()
    repository.save_item(_legacy_item(ACCOUNT, "Python", topic_hint="knowledge_interest"))
    atomic.migrate_account(ACCOUNT)
    legacy = _item(atomic, "Python")
    assert legacy.fact_relation == AtomicProfileFactRelation.STATEMENT

    record = four.upsert_automatic_record(
        ACCOUNT, dimension=FourDimension.KNOWLEDGE_INTEREST, content="Python",
        action="create", confidence=FourDimensionConfidence.HIGH,
    )
    atomic.mirror_record(ACCOUNT, record, fact_text="我喜欢Python")
    assert _texts(atomic) == {"Python", "我喜欢Python"}


def test_mirror_replay_merges_same_fact_without_duplicates() -> None:
    four, repository, atomic, _ = _services()
    record = four.upsert_automatic_record(
        ACCOUNT,
        dimension=FourDimension.KNOWLEDGE_INTEREST,
        content="Python",
        action="create",
        confidence=FourDimensionConfidence.MEDIUM,
        evidence_quote="我喜欢Python",
    )
    first = atomic.mirror_record(
        ACCOUNT, record, evidence_message_id="message-1", fact_text="我喜欢Python"
    )
    assert first is not None
    replay = atomic.mirror_record(
        ACCOUNT, record, evidence_message_id="message-1", fact_text="我喜欢Python"
    )
    assert replay is not None

    active = repository.list_items(ACCOUNT)
    assert len(active) == 1
    assert active[0].source_message_ids == ["message-1"]


def test_sqlite_round_trips_fact_identity_evidence_and_report(tmp_path: Path) -> None:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    four = FourDimensionProfileService(
        InMemoryProfileRepository(), InMemoryFourDimensionProfileRepository()
    )
    repository = SqliteAtomicProfileRepository(database, initialize=False)
    service = AtomicProfileService(four, repository)
    first = four.upsert_automatic_record(
        ACCOUNT,
        dimension=FourDimension.ACADEMIC_STATUS,
        content="大二",
        action="create",
        confidence=FourDimensionConfidence.HIGH,
        evidence_quote="我现在大二",
    )
    service.mirror_record(ACCOUNT, first)
    second = four.upsert_automatic_record(
        ACCOUNT,
        dimension=FourDimension.ACADEMIC_STATUS,
        content="大三",
        action="create",
        confidence=FourDimensionConfidence.HIGH,
        evidence_quote="我现在大三了",
    )
    service.mirror_record(ACCOUNT, second)
    report = service.migrate_account(ACCOUNT)

    reopened = AtomicProfileService(
        four,
        SqliteAtomicProfileRepository(
            BridgesDatabase(tmp_path / "bridges.db"), initialize=False
        ),
    )
    items = reopened.list_items(ACCOUNT)
    assert [item.text for item in items] == ["大三"]
    assert items[0].fact_relation == AtomicProfileFactRelation.GRADE
    assert items[0].fact_object == "大三"
    assert items[0].evidence_quote == "我现在大三了"
    assert items[0].supersedes_id is not None
    stored = reopened.latest_migration_report(ACCOUNT)
    assert stored is not None
    assert stored.run_id == report.run_id
    assert stored.identity_backfilled == report.identity_backfilled
