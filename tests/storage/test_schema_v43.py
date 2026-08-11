"""Issue 06：四维画像证据字段的 v42→v43 迁移。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from bridges.storage.database import MIGRATIONS, SCHEMA_VERSION, BridgesDatabase


def _build_v42_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        for version in range(1, 43):
            for statement in MIGRATIONS[version]:
                connection.execute(statement)
        connection.execute(
            "INSERT INTO schema_meta(key, value) VALUES ('version', '42')"
        )
        connection.execute(
            "INSERT INTO profile_four_dimension_records ("
            "record_id, account_id, dimension, label, content, first_stable_recorded_at, "
            "updated_at, version, status, source_record_id, source_version, content_hash, "
            "write_origin, migration_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)" ,
            (
                "record-1",
                "account-alice",
                "stage_goal",
                "阶段目标",
                "完成实验",
                "2026-08-01T00:00:00+00:00",
                "2026-08-01T00:00:00+00:00",
                1,
                "active",
                "source-1",
                1,
                "hash-1",
                "migration",
                "profile-four-dimensions-v1",
            ),
        )


def test_v44_migration_preserves_records_and_adds_default_evidence_fields(
    tmp_path: Path,
) -> None:
    path = tmp_path / "bridges.db"
    _build_v42_database(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO profile_extraction_runs ("
            "extraction_id, account_id, message_id, extractor_version, source_hash, "
            "source_snapshot, status, attempts, record_ids_json, observed_count, "
            "last_error, created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "failed-run",
                "account-alice",
                "message-1",
                "profile-auto-v1",
                "hash-1",
                "snapshot",
                "exhausted",
                3,
                "[]",
                0,
                "client_error_400",
                "2026-08-01T00:00:00+00:00",
                "2026-08-01T00:00:00+00:00",
            ),
        )

    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION == 44

    columns = {
        str(row[1])
        for row in database.connection.execute(
            "PRAGMA table_info(profile_four_dimension_records)"
        ).fetchall()
    }
    assert {
        "confidence",
        "evidence_quote",
        "evidence_message_id",
        "correction_count",
        "change_note",
    } <= columns
    extraction_columns = {
        str(row[1])
        for row in database.connection.execute(
            "PRAGMA table_info(profile_extraction_runs)"
        ).fetchall()
    }
    assert "outcome" in extraction_columns
    outcome = database.connection.execute(
        "SELECT outcome FROM profile_extraction_runs WHERE extraction_id = 'failed-run'"
    ).fetchone()
    assert outcome["outcome"] == "permanent_failure"

    row = database.connection.execute(
        "SELECT content, confidence, evidence_quote, evidence_message_id, "
        "correction_count, change_note FROM profile_four_dimension_records "
        "WHERE record_id = 'record-1'"
    ).fetchone()
    assert tuple(row) == (
        "完成实验",
        "low",
        None,
        None,
        0,
        "由 v42 存量记录迁移，等待新的证据确认",
    )
