"""画像改进工单 17：``BridGes profile-rebuild`` CLI 合同测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bridges.cli.profile_rebuild import profile_rebuild_app
from bridges.config import get_settings
from bridges.profiles.history_preview import preview_history, save_preview_manifest
from bridges.profiles.replay import resolve_authoritative_database
from bridges.storage.database import BridgesDatabase
from tests.profiles.test_issue17_history_rebuild import (
    ACCOUNT,
    FIXED_NOW,
    _open_database,
    _seed_history,
)


def _prepare_environment(
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


def _make_manifest(database_path: Path, target: Path) -> None:
    authority = resolve_authoritative_database(
        settings=get_settings(), explicit_path=database_path
    )
    manifest = preview_history(authority, account_id=ACCOUNT, now=FIXED_NOW)
    save_preview_manifest(manifest, target)


def test_cli_start_run_status_reconcile_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, database = _open_database(tmp_path)
    _seed_history(database)
    database.close()
    _prepare_environment(monkeypatch, database_path, tmp_path)
    runner = CliRunner()
    work = tmp_path / "work"
    manifest_path = tmp_path / "preview.json"
    _make_manifest(database_path, manifest_path)

    result = runner.invoke(
        profile_rebuild_app,
        [
            "start",
            "--manifest",
            str(manifest_path),
            "--work-dir",
            str(work),
        ],
    )
    assert result.exit_code == 0, result.output
    started = json.loads(result.output)
    assert started["reused"] is False
    assert started["progress"]["eligible"] == 3

    result = runner.invoke(
        profile_rebuild_app, ["run", "--work-dir", str(work), "--max-ticks", "1"]
    )
    assert result.exit_code == 0, result.output
    partial = json.loads(result.output)
    assert partial["ticks"] == 1
    assert partial["completed"] is False

    result = runner.invoke(
        profile_rebuild_app, ["run", "--work-dir", str(work)]
    )
    assert result.exit_code == 0, result.output
    finished = json.loads(result.output)
    assert finished["completed"] is True
    assert finished["progress"]["extracted"] == 3
    assert finished["progress"]["committed"] == 2

    result = runner.invoke(
        profile_rebuild_app, ["status", "--work-dir", str(work)]
    )
    assert result.exit_code == 0, result.output
    status = json.loads(result.output)
    assert status["rebuild_id"] == started["rebuild_id"]
    assert status["completed_at"] is not None

    result = runner.invoke(
        profile_rebuild_app, ["reconcile", "--work-dir", str(work)]
    )
    assert result.exit_code == 0, result.output
    reconciled = json.loads(result.output)
    assert reconciled["passed"] is True
    assert (work / "reconciliation.json").exists()

    result = runner.invoke(
        profile_rebuild_app, ["cleanup", "--work-dir", str(work), "--purge"]
    )
    assert result.exit_code == 0, result.output
    cleaned = json.loads(result.output)
    assert cleaned["purged"] is True
    assert not (work / "rebuild.json").exists()


def test_cli_errors_are_json_with_exit_code_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, database = _open_database(tmp_path)
    _seed_history(database)
    database.close()
    _prepare_environment(monkeypatch, database_path, tmp_path)
    runner = CliRunner()
    work = tmp_path / "work"

    result = runner.invoke(
        profile_rebuild_app, ["status", "--work-dir", str(work)]
    )
    assert result.exit_code == 2
    error = json.loads(result.output)
    assert error["error"]["code"] == "work_dir_missing"

    manifest_path = tmp_path / "preview.json"
    _make_manifest(database_path, manifest_path)
    result = runner.invoke(
        profile_rebuild_app,
        [
            "start",
            "--manifest",
            str(manifest_path),
            "--work-dir",
            str(work),
            "--start-at",
            "not-a-time",
        ],
    )
    assert result.exit_code == 2
    assert json.loads(result.output)["error"]["code"] == "window_invalid"


def test_cli_reconcile_failure_exits_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, database = _open_database(tmp_path)
    _seed_history(database)
    database.close()
    _prepare_environment(monkeypatch, database_path, tmp_path)
    runner = CliRunner()
    work = tmp_path / "work"
    manifest_path = tmp_path / "preview.json"
    _make_manifest(database_path, manifest_path)

    assert (
        runner.invoke(
            profile_rebuild_app,
            ["start", "--manifest", str(manifest_path), "--work-dir", str(work)],
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            profile_rebuild_app, ["run", "--work-dir", str(work)]
        ).exit_code
        == 0
    )

    working = BridgesDatabase(work / "working" / "bridges.db")
    working.initialize()
    with working.transaction():
        working.connection.execute(
            "UPDATE profile_items SET text = '我叫小李' WHERE profile_item_id = ?",
            ("item-alice-manual",),
        )
    working.close()

    result = runner.invoke(
        profile_rebuild_app, ["reconcile", "--work-dir", str(work)]
    )
    assert result.exit_code == 2
    report = json.loads(result.output)
    assert report["passed"] is False
    checks = {str(error["check"]) for error in report["errors"]}
    assert "manual_items_preserved" in checks
