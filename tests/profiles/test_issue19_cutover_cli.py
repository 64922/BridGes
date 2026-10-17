"""画像工单 19：``BridGes profile-cutover`` CLI 合同测试。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bridges.cli.profile_cutover import profile_cutover_app
from bridges.config import get_settings
from bridges.profiles.replay import create_verified_backup, resolve_authoritative_database
from bridges.storage.database import BridgesDatabase
from tests.profiles.test_issue16_history_preview import (
    ACCOUNT,
    _open_database,
    _seed_account,
    _seed_item,
    _settings,
)
from tests.profiles.test_issue19_cutover_recovery import (
    _mutate_after_cutover,
    _seed_cutover_template,
)


def _prepare_environment(
    monkeypatch: pytest.MonkeyPatch, database_path: Path, tmp_path: Path
) -> None:
    app_home = tmp_path / "app-home"
    app_home.mkdir(exist_ok=True)
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{database_path.as_posix()}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "test-secret")
    monkeypatch.setenv("BRIDGES_HOME", str(app_home))
    get_settings.cache_clear()


def test_audit_and_recovery_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database_path, database = _open_database(tmp_path)
    _seed_cutover_template(database)
    database.close()
    _prepare_environment(monkeypatch, database_path, tmp_path)
    runner = CliRunner()

    result = runner.invoke(profile_cutover_app, ["audit", "--account", ACCOUNT])
    assert result.exit_code == 0, result.output
    audit = json.loads(result.output)
    assert audit["passed"] is True
    assert audit["counts"]["atomic_items"] == 3

    authority = resolve_authoritative_database(
        settings=_settings(database_path), explicit_path=database_path
    )
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    backup = create_verified_backup(authority, backup_dir / "cutover.db")

    current_path = tmp_path / "current" / "bridges.db"
    current_path.parent.mkdir()
    shutil.copy2(backup.path, current_path)
    current = BridgesDatabase(current_path)
    current.initialize()
    _mutate_after_cutover(current)
    current.close()
    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{current_path.as_posix()}")
    get_settings.cache_clear()

    target = tmp_path / "recovered" / "bridges.db"
    target.parent.mkdir()
    result = runner.invoke(
        profile_cutover_app,
        [
            "recover",
            "--backup",
            str(backup.path),
            "--target",
            str(target),
            "--account",
            ACCOUNT,
        ],
    )
    assert result.exit_code == 0, result.output
    recovered = json.loads(result.output)
    assert recovered["passed"] is True
    assert recovered["counts"]["items_added_since_backup"] == 1
    assert target.exists()


def test_audit_failure_uses_exit_code_four(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database_path, database = _open_database(tmp_path)
    _seed_account(database, ACCOUNT)
    _seed_item(
        database,
        ACCOUNT,
        "item-tombstone",
        "",
        status="withdrawn",
        stored_identity_key="withdrawn-identity",
    )
    with database.transaction():
        database.connection.execute(
            "UPDATE profile_items SET text = '墓碑正文' WHERE account_id = ?",
            (ACCOUNT,),
        )
    database.close()
    _prepare_environment(monkeypatch, database_path, tmp_path)

    result = CliRunner().invoke(profile_cutover_app, ["audit", "--account", ACCOUNT])
    assert result.exit_code == 4, result.output
    report = json.loads(result.output)
    assert report["passed"] is False
