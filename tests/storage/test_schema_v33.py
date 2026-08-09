"""Issue 14: 四维画像目标表和迁移审计的 SQLite 持久化。"""

from __future__ import annotations

from datetime import UTC, datetime

from bridges.contracts.profiles import ProfileAssertion
from bridges.profiles import (
    FourDimensionProfileService,
    SqliteFourDimensionProfileRepository,
)
from bridges.profiles.sqlite_repository import SqliteProfileRepository
from bridges.storage import BridgesDatabase, SCHEMA_VERSION


def test_schema_v33_persists_four_dimension_target_and_internal_artifacts(tmp_path) -> None:
    database = BridgesDatabase(tmp_path / "bridges.db")
    assert database.initialize() == SCHEMA_VERSION

    old_repository = SqliteProfileRepository(database)
    source = ProfileAssertion(
        assertion_id="old-goal",
        owner_account_id="account-alice",
        canonical_dimension="stage_goal",
        value_or_rule="完成实验报告",
        authorization_scope="general",
        status="active",
        version=1,
        created_at=datetime(2026, 8, 9, tzinfo=UTC),
        updated_at=datetime(2026, 8, 9, tzinfo=UTC),
    )
    old_repository.save_assertion(source)

    target_repository = SqliteFourDimensionProfileRepository(database)
    service = FourDimensionProfileService(old_repository, target_repository)
    report = service.migrate_account("account-alice")
    record = service.list_records("account-alice")[0]

    reopened = BridgesDatabase(tmp_path / "bridges.db")
    reopened_target = SqliteFourDimensionProfileRepository(reopened)
    reopened_record = reopened_target.get_record("account-alice", record.record_id)
    reopened_report = reopened_target.get_latest_migration_report("account-alice")

    assert report.four_dimension_migrated == 1
    assert reopened_record.content == "完成实验报告"
    assert reopened_record.first_stable_recorded_at == record.first_stable_recorded_at
    assert reopened_report is not None
    assert reopened_report.stable_record_ids == [record.record_id]
    assert reopened_target.list_records("account-bob") == []


def test_schema_v33_upgrade_preserves_legacy_profile_tables(tmp_path) -> None:
    path = tmp_path / "bridges.db"
    old_database = BridgesDatabase(path)
    old_database.initialize()
    old_database.connection.execute(
        "INSERT INTO profile_assertions ("
        "assertion_id, account_id, canonical_dimension, value_or_rule, "
        "applicable_scenes_json, supporting_observation_ids_json, "
        "contradicting_observation_ids_json, authorization_scope, status, "
        "sensitivity_class, version, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, '[]', '[]', '[]', 'general', 'active', 'preference', 1, ?, ?)",
        (
            "old-legacy",
            "account-alice",
            "stage_goal",
            "旧目标",
            datetime(2026, 8, 9, tzinfo=UTC).isoformat(),
            datetime(2026, 8, 9, tzinfo=UTC).isoformat(),
        ),
    )

    reopened = BridgesDatabase(path)
    assert reopened.initialize() == SCHEMA_VERSION
    assert reopened.connection.execute(
        "SELECT value_or_rule FROM profile_assertions WHERE assertion_id = 'old-legacy'"
    ).fetchone()[0] == "旧目标"
