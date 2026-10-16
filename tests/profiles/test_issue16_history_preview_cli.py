"""画像工单 16：``BridGes profile-history`` CLI 合同测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bridges.cli.profile_history import profile_history_app
from bridges.config import get_settings
from bridges.storage.database import SCHEMA_VERSION, BridgesDatabase

ACCOUNT = "account-alice"
TS = "2026-08-12T00:00:00+00:00"


def _open_database(tmp_path: Path) -> tuple[Path, BridgesDatabase]:
    database_path = tmp_path / "runtime" / "bridges.db"
    database_path.parent.mkdir(parents=True, exist_ok=True)
    database = BridgesDatabase(database_path)
    assert database.initialize() == SCHEMA_VERSION
    with database.transaction():
        database.connection.execute(
            "INSERT INTO accounts (account_id, email, created_at) VALUES (?, ?, ?)",
            (ACCOUNT, f"{ACCOUNT}@example.com", TS),
        )
        database.connection.execute(
            "INSERT INTO conversations "
            "(conversation_id, account_id, title, mode, created_at, updated_at) "
            "VALUES (?, ?, '', 'companion', ?, ?)",
            (f"conversation-{ACCOUNT}", ACCOUNT, TS, TS),
        )
        database.connection.execute(
            "INSERT INTO messages "
            "(message_id, conversation_id, account_id, role, attempt_number, "
            "status, content, created_at, updated_at) "
            "VALUES ('msg-1', ?, ?, 'user', 1, 'done', '我养了一只猫叫团子', ?, ?)",
            (f"conversation-{ACCOUNT}", ACCOUNT, TS, TS),
        )
        database.connection.execute(
            "INSERT INTO profile_items "
            "(profile_item_id, account_id, text, identity_key, write_origin, "
            "created_at, updated_at) "
            "VALUES ('item-1', ?, '我叫小林', 'key-1', 'user', ?, ?)",
            (ACCOUNT, TS, TS),
        )
    return database_path, database


def _prepare_cli_environment(
    monkeypatch: pytest.MonkeyPatch, database_path: Path, tmp_path: Path
) -> None:
    app_home = tmp_path / "app-home"
    app_home.mkdir(exist_ok=True)
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    monkeypatch.setenv(
        "BRIDGES_DATABASE_URL", f"sqlite:///{database_path.as_posix()}"
    )
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "test-secret")
    monkeypatch.setenv("BRIDGES_HOME", str(app_home))
    get_settings.cache_clear()


def test_cli_preview_verify_backup_and_restore_verify(tmp_path: Path, monkeypatch) -> None:
    database_path, database = _open_database(tmp_path)
    database.close()
    _prepare_cli_environment(monkeypatch, database_path, tmp_path)
    runner = CliRunner()

    manifest_path = tmp_path / "preview.json"
    result = runner.invoke(
        profile_history_app,
        ["preview", "--account", ACCOUNT, "--out", str(manifest_path)],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["account_id"] == ACCOUNT
    assert payload["items"]["counts"]["manual_preserved"] == 1
    assert manifest_path.exists()

    result = runner.invoke(
        profile_history_app, ["verify", "--manifest", str(manifest_path)]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["fresh"] is True

    target = tmp_path / "backup" / "history.db"
    target.parent.mkdir()
    result = runner.invoke(
        profile_history_app,
        [
            "backup",
            "--manifest",
            str(manifest_path),
            "--target",
            str(target),
            "--work-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    record = json.loads(result.output)
    assert record["verified"] is True
    assert Path(f"{target}.history-record.json").exists()

    result = runner.invoke(
        profile_history_app,
        [
            "restore-verify",
            "--manifest",
            str(manifest_path),
            "--backup",
            str(target),
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["verified"] is True


def test_cli_missing_account_and_stale_manifest_fail_explicitly(
    tmp_path: Path, monkeypatch
) -> None:
    database_path, database = _open_database(tmp_path)
    database.close()
    _prepare_cli_environment(monkeypatch, database_path, tmp_path)
    runner = CliRunner()

    result = runner.invoke(
        profile_history_app, ["preview", "--account", "account-ghost"]
    )
    assert result.exit_code == 2
    assert "account_missing" in result.output

    manifest_path = tmp_path / "preview.json"
    result = runner.invoke(
        profile_history_app,
        ["preview", "--account", ACCOUNT, "--out", str(manifest_path)],
    )
    assert result.exit_code == 0

    database = BridgesDatabase(database_path)
    with database.transaction():
        database.connection.execute(
            "INSERT INTO messages "
            "(message_id, conversation_id, account_id, role, attempt_number, "
            "status, content, created_at, updated_at) "
            "VALUES ('msg-late', ?, ?, 'user', 1, 'done', '我最近换了工作', ?, ?)",
            (f"conversation-{ACCOUNT}", ACCOUNT, TS, TS),
        )
    database.close()

    result = runner.invoke(
        profile_history_app, ["verify", "--manifest", str(manifest_path)]
    )
    assert result.exit_code == 0
    assert json.loads(result.output)["fresh"] is False

    result = runner.invoke(
        profile_history_app,
        ["verify", "--manifest", str(manifest_path), "--require-fresh"],
    )
    assert result.exit_code == 2
    assert '"fresh": false' in result.output


def test_cli_backup_rejects_stale_manifest(tmp_path: Path, monkeypatch) -> None:
    database_path, database = _open_database(tmp_path)
    database.close()
    _prepare_cli_environment(monkeypatch, database_path, tmp_path)
    runner = CliRunner()

    manifest_path = tmp_path / "preview.json"
    runner.invoke(
        profile_history_app,
        ["preview", "--account", ACCOUNT, "--out", str(manifest_path)],
    )
    database = BridgesDatabase(database_path)
    with database.transaction():
        database.connection.execute(
            "INSERT INTO messages "
            "(message_id, conversation_id, account_id, role, attempt_number, "
            "status, content, created_at, updated_at) "
            "VALUES ('msg-late', ?, ?, 'user', 1, 'done', '我最近换了工作', ?, ?)",
            (f"conversation-{ACCOUNT}", ACCOUNT, TS, TS),
        )
    database.close()

    result = runner.invoke(
        profile_history_app,
        [
            "backup",
            "--manifest",
            str(manifest_path),
            "--target",
            str(tmp_path / "backup.db"),
        ],
    )
    assert result.exit_code == 2
    assert "preview_stale" in result.output
    assert not (tmp_path / "backup.db").exists()
