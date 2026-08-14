"""Issue 10：model_run_locks schema 迁移与 legacy 行兼容测试。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from bridges.ai.sqlite_recorder import SqliteModelRunLockRecorder
from bridges.storage import SCHEMA_VERSION, BridgesDatabase
from bridges.storage.database import MIGRATIONS


def _create_legacy_database(path: Path) -> None:
    """Build a database up to v44 and insert a legacy model_run_locks row."""
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_meta ("
            " key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        for version in range(1, 45):
            if version == 34:
                # v34 migration validates conversation modes; provide valid data.
                connection.execute(
                    "INSERT INTO conversations"
                    " (conversation_id, account_id, title, mode, created_at, updated_at)"
                    " VALUES ('c-legacy', 'acc-legacy', 'legacy', 'companion', 't', 't')"
                )
            for statement in MIGRATIONS[version]:
                connection.execute(statement)
        connection.execute(
            "INSERT INTO model_run_locks"
            " (lock_id, account_id, capability_name, capability_version,"
            " actual_model_id, region, status, error_code, error_message,"
            " usage, created_at)"
            " VALUES ('legacy-lock', 'acc-legacy', 'chat', '1', 'qwen-plus',"
            " 'cn-beijing', 'success', NULL, NULL, '{\"tokens\": 10}', '2026-01-01T00:00:00Z')"
        )
        connection.execute(
            "INSERT INTO schema_meta(key, value) VALUES ('version', '44')"
        )


def test_migration_adds_audit_columns_and_links_table(tmp_path: Path) -> None:
    path = tmp_path / "bridges.db"
    _create_legacy_database(path)

    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION

    with sqlite3.connect(path) as connection:
        columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(model_run_locks)")
        }
        assert "run_id" in columns
        assert "project_id" in columns
        assert "parameters" in columns
        assert "prompt_version" in columns
        assert "input_output_contract" in columns
        assert "fallback_path_json" in columns
        assert "retry_count" in columns
        assert "degradation_reason" in columns
        assert "cost_estimate_json" in columns
        assert "canonical_hash" in columns

        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "model_run_lock_links" in tables


def test_migration_preserves_legacy_fields(tmp_path: Path) -> None:
    path = tmp_path / "bridges.db"
    _create_legacy_database(path)

    database = BridgesDatabase(path)
    database.initialize()

    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT lock_id, account_id, capability_name, status, created_at,"
            " run_id, project_id, prompt_version, input_output_contract,"
            " canonical_hash"
            " FROM model_run_locks WHERE lock_id = ?",
            ("legacy-lock",),
        ).fetchone()

    assert row is not None
    assert str(row["lock_id"]) == "legacy-lock"
    assert str(row["account_id"]) == "acc-legacy"
    assert str(row["capability_name"]) == "chat"
    assert str(row["status"]) == "success"
    assert str(row["created_at"]) == "2026-01-01T00:00:00Z"
    assert str(row["run_id"]) == "legacy-unknown"
    assert str(row["project_id"]) == "legacy-unknown"
    assert str(row["prompt_version"]) == "legacy"
    assert str(row["input_output_contract"]) == "legacy"
    assert str(row["canonical_hash"]).startswith("legacy-")


def test_migration_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "bridges.db"
    _create_legacy_database(path)

    first = BridgesDatabase(path)
    first.initialize()

    second = BridgesDatabase(path)
    assert second.initialize() == SCHEMA_VERSION

    with sqlite3.connect(path) as connection:
        count = int(
            connection.execute(
                "SELECT COUNT(*) FROM model_run_locks WHERE lock_id = ?",
                ("legacy-lock",),
            ).fetchone()[0]
        )
    assert count == 1


def test_legacy_rows_are_readable_as_legacy(tmp_path: Path) -> None:
    path = tmp_path / "bridges.db"
    _create_legacy_database(path)

    database = BridgesDatabase(path)
    database.initialize()
    recorder = SqliteModelRunLockRecorder(database)

    legacy = recorder.get_lock("legacy-lock", "acc-legacy")
    assert legacy is not None
    assert legacy.legacy is True
    assert legacy.run_id == "legacy-unknown"
    assert legacy.project_id == "legacy-unknown"
    assert legacy.prompt_version == "legacy"
    assert legacy.input_output_contract == "legacy"
    # 迁移回填的全部新列都标记为 legacy，绝不当作真实调用事实。
    assert {
        "run_id",
        "project_id",
        "prompt_version",
        "input_output_contract",
        "canonical_hash",
        "parameters",
        "fallback_path_json",
        "retry_count",
        "degradation_reason",
        "cost_estimate_json",
    } <= legacy.legacy_missing_fields
