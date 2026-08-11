"""Issue 08：profile-auto-v2 安全回放的公开接缝测试。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import SecretStr

from bridges.config import Settings
from bridges.profiles.automatic import RuleBasedAutomaticProfileExtractor
from bridges.profiles.replay import (
    ProfileReplayCoordinator,
    ProfileReplayError,
    create_verified_backup,
    load_runtime_application_state,
    resolve_authoritative_database,
)
from bridges.storage.database import BridgesDatabase


def _settings(path: Path) -> Settings:
    return Settings(
        environment="test",
        database_url=SecretStr(f"sqlite:///{path.as_posix()}"),
    )


def _seed_message(
    database: BridgesDatabase,
    *,
    account_id: str,
    message_id: str,
    content: str,
    status: str = "done",
) -> None:
    conversation_id = f"conversation-{account_id}"
    with database.transaction():
        database.connection.execute(
            "INSERT OR IGNORE INTO conversations "
            "(conversation_id, account_id, title, mode, created_at, updated_at) "
            "VALUES (?, ?, '', 'companion', ?, ?)",
            (conversation_id, account_id, "2026-08-12T00:00:00+00:00", "2026-08-12T00:00:00+00:00"),
        )
        database.connection.execute(
            "INSERT INTO messages "
            "(message_id, conversation_id, account_id, role, attempt_number, status, "
            "content, created_at, updated_at) VALUES (?, ?, ?, 'user', 1, ?, ?, ?, ?)",
            (
                message_id,
                conversation_id,
                account_id,
                status,
                content,
                "2026-08-12T00:00:00+00:00",
                "2026-08-12T00:00:00+00:00",
            ),
        )


def _seed_v1_run(
    database: BridgesDatabase,
    *,
    account_id: str,
    message_id: str,
    extraction_id: str,
    status: str,
    attempts: int,
    last_error: str | None,
) -> None:
    timestamp = "2026-08-12T00:00:00+00:00"
    with database.transaction():
        database.connection.execute(
            "INSERT INTO profile_extraction_runs ("
            "extraction_id, account_id, message_id, extractor_version, source_hash, "
            "source_snapshot, status, outcome, attempts, record_ids_json, observed_count, "
            "last_error, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                extraction_id,
                account_id,
                message_id,
                "profile-auto-v1",
                f"old-{message_id}",
                "历史私密正文不应进入报告",
                status,
                "permanent_failure" if status == "exhausted" else "succeeded_empty",
                attempts,
                "[]",
                0,
                last_error,
                timestamp,
                timestamp,
            ),
        )


def test_authority_resolution_rejects_conflicting_candidate_paths(tmp_path: Path) -> None:
    configured = tmp_path / "runtime" / "bridges.db"
    candidate = tmp_path / "repository" / "bridges.db"
    configured.parent.mkdir()
    candidate.parent.mkdir()
    configured.touch()
    candidate.touch()

    with pytest.raises(ProfileReplayError) as exc_info:
        resolve_authoritative_database(
            settings=_settings(configured),
            explicit_path=candidate,
        )

    assert exc_info.value.code == "database_path_conflict"


def test_authority_resolution_reads_unified_runtime_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "runtime" / "bridges.db"
    database_path.parent.mkdir()
    database = BridgesDatabase(database_path)
    database.initialize()
    database.close()
    app_home = tmp_path / "app-home"
    app_home.mkdir()
    (app_home / "config.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "database_url": f"sqlite:///{database_path.as_posix()}",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("BRIDGES_HOME", str(app_home))

    authority = resolve_authoritative_database(
        settings=Settings(environment="test"),
        application_state=load_runtime_application_state(),
    )

    assert authority.path == database_path.resolve()


def test_dry_run_is_read_only_and_does_not_leak_accounts_or_messages(tmp_path: Path) -> None:
    database_path = tmp_path / "runtime" / "bridges.db"
    database_path.parent.mkdir()
    database = BridgesDatabase(database_path)
    database.initialize()
    _seed_message(
        database,
        account_id="account-live",
        message_id="message-live",
        content="我想学习 Transformer",
    )
    _seed_v1_run(
        database,
        account_id="account-live",
        message_id="message-live",
        extraction_id="run-live",
        status="exhausted",
        attempts=3,
        last_error="client_error_400: old contract",
    )
    _seed_message(
        database,
        account_id="account-silent",
        message_id="message-silent",
        content="今天天气不错",
    )
    _seed_v1_run(
        database,
        account_id="account-silent",
        message_id="message-silent",
        extraction_id="run-silent",
        status="succeeded",
        attempts=0,
        last_error=None,
    )
    database.close()

    authority = resolve_authoritative_database(settings=_settings(database_path))
    before = hashlib.sha256(database_path.read_bytes()).hexdigest()
    report = ProfileReplayCoordinator(authority).dry_run()
    after = hashlib.sha256(database_path.read_bytes()).hexdigest()

    assert before == after
    assert report.counts["eligible_exhausted_client_error_400"] == 1
    assert report.counts["eligible_succeeded_attempts_0"] == 1
    rendered = json.dumps(report.as_dict(), ensure_ascii=False)
    assert "account-live" not in rendered
    assert "历史私密正文" not in rendered


def test_formal_replay_uses_v2_idempotency_and_supervised_worker(tmp_path: Path) -> None:
    database_path = tmp_path / "runtime" / "bridges.db"
    database_path.parent.mkdir()
    database = BridgesDatabase(database_path)
    database.initialize()
    _seed_message(
        database,
        account_id="account-live",
        message_id="message-live",
        content="我想学习 Transformer",
    )
    _seed_v1_run(
        database,
        account_id="account-live",
        message_id="message-live",
        extraction_id="run-live",
        status="exhausted",
        attempts=3,
        last_error="client_error_400: old contract",
    )
    database.close()

    authority = resolve_authoritative_database(settings=_settings(database_path))
    backup = create_verified_backup(authority, tmp_path / "profile-replay.db")
    coordinator = ProfileReplayCoordinator(authority)
    first_dry_run = coordinator.dry_run()
    first = coordinator.replay(
        backup=backup,
        confirmation="回放",
        dry_run_report=first_dry_run,
        extractor=RuleBasedAutomaticProfileExtractor(),
        drain=True,
    )
    second_dry_run = coordinator.dry_run()
    second = coordinator.replay(
        backup=backup,
        confirmation="回放",
        dry_run_report=second_dry_run,
        extractor=RuleBasedAutomaticProfileExtractor(),
        drain=True,
    )

    database = BridgesDatabase(database_path)
    v2_runs = database.connection.execute(
        "SELECT extractor_version, status FROM profile_extraction_runs "
        "WHERE extractor_version LIKE 'profile-auto-v2%'"
    ).fetchall()
    v2_tasks = database.connection.execute(
        "SELECT extractor_version, status FROM profile_extraction_tasks "
        "WHERE extractor_version LIKE 'profile-auto-v2%'"
    ).fetchall()
    observations = database.connection.execute(
        "SELECT extractor_version FROM profile_extraction_observations "
        "WHERE extractor_version LIKE 'profile-auto-v2%'"
    ).fetchall()
    records = database.connection.execute(
        "SELECT migration_version FROM profile_four_dimension_records "
        "WHERE migration_version LIKE 'profile-auto-v2%'"
    ).fetchall()
    queue_rows = database.connection.execute(
        "SELECT status FROM task_claims WHERE queue_name = 'profile-replay-v2'"
    ).fetchall()
    database.close()

    assert first.counts["queued"] == 1
    assert second.counts["already_processed"] == 1
    assert len(v2_runs) == len(v2_tasks) == 1
    assert v2_runs[0][0].startswith("profile-auto-v2")
    assert v2_runs[0][1] == "succeeded"
    assert v2_tasks[0][1] == "succeeded"
    assert len(observations) == 1
    assert len(records) == 1
    assert len(queue_rows) == 1
    assert queue_rows[0][0] == "completed"
