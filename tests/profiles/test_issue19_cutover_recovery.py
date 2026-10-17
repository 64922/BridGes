"""画像工单 19：切换恢复、整库账户覆盖与重升级合同测试。"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from bridges.profiles.cutover import audit_profile_retirement
from bridges.profiles.cutover_recovery import (
    recover_all_cutover_state,
    recover_cutover_state,
)
from bridges.profiles.replay import (
    ProfileReplayError,
    create_verified_backup,
    resolve_authoritative_database,
)
from bridges.storage.database import BridgesDatabase
from tests.profiles.test_issue16_history_preview import (
    ACCOUNT,
    TS,
    _open_database,
    _seed_account,
    _seed_item,
    _settings,
)

OTHER_ACCOUNT = "account-bob"


def _authority(database_path: Path):
    return resolve_authoritative_database(settings=_settings(database_path))


def _seed_claim(database: BridgesDatabase, claim_id: str, task_key: str) -> None:
    with database.transaction():
        database.connection.execute(
            "INSERT INTO task_claims "
            "(claim_id, queue_name, task_key, status, created_at, updated_at) "
            "VALUES (?, 'profile-extraction', ?, 'queued', ?, ?)",
            (claim_id, task_key, TS, TS),
        )


def _seed_cutover_template(database: BridgesDatabase) -> None:
    _seed_account(database, ACCOUNT)
    _seed_account(database, OTHER_ACCOUNT)
    _seed_item(database, ACCOUNT, "item-edit", "我住在北京")
    _seed_item(database, ACCOUNT, "item-delete", "我下周去上海")
    _seed_item(database, ACCOUNT, "item-backup-only", "我养了一只猫")
    _seed_item(database, OTHER_ACCOUNT, "item-other", "另一个账户的私有事实")
    with database.transaction():
        database.connection.execute(
            "INSERT INTO profile_overview "
            "(account_id, context_version, epoch, dependency_fingerprint, payload_json) "
            "VALUES (?, 'overview-v1', 1, '', '[]')",
            (ACCOUNT,),
        )
        database.connection.execute(
            "INSERT INTO profile_ongoing_context "
            "(account_id, context_version, epoch, dependency_fingerprint, payload_json) "
            "VALUES (?, 'ongoing-v1', 1, '', '[]')",
            (ACCOUNT,),
        )
        database.connection.execute(
            "INSERT INTO profile_recall_index_versions "
            "(version_id, account_id, contract_json, contract_hash, status, created_at) "
            "VALUES ('version-1', ?, '{}', 'hash-1', 'ready', ?)",
            (ACCOUNT, TS),
        )
        database.connection.execute(
            "INSERT INTO profile_recall_index_entries "
            "(version_id, account_id, profile_item_id, item_version, text_hash, updated_at) "
            "VALUES ('version-1', ?, 'item-edit', 1, 'text-hash', ?)",
            (ACCOUNT, TS),
        )
        database.connection.execute(
            "INSERT INTO profile_extraction_runs "
            "(extraction_id, account_id, message_id, extractor_version, source_hash, "
            "source_snapshot, status, created_at, updated_at) "
            "VALUES ('run-backup-pending', ?, 'message-backup-pending', 'old-v1', "
            "'hash-backup', '旧来源快照', 'pending', ?, ?)",
            (ACCOUNT, TS, TS),
        )
        database.connection.execute(
            "INSERT INTO profile_extraction_tasks "
            "(task_id, account_id, message_id, extractor_version, source_hash, status, "
            "created_at, updated_at) "
            "VALUES ('task-backup-pending', ?, 'message-backup-pending', 'old-v1', "
            "'hash-backup', 'pending', ?, ?)",
            (ACCOUNT, TS, TS),
        )
    _seed_claim(database, "claim-backup-pending", "task-backup-pending")


def _mutate_after_cutover(database: BridgesDatabase) -> None:
    _seed_item(database, ACCOUNT, "item-new", "我加入了摄影社")
    with database.transaction():
        database.connection.execute(
            "UPDATE profile_items SET text = ?, user_edited_at = ?, version = 2, "
            "updated_at = ? WHERE account_id = ? AND profile_item_id = 'item-edit'",
            ("我住在杭州", TS, TS, ACCOUNT),
        )
        database.connection.execute(
            "UPDATE profile_items SET text = '', status = 'withdrawn', version = 2, "
            "updated_at = ? WHERE account_id = ? AND profile_item_id = 'item-delete'",
            (TS, ACCOUNT),
        )
        database.connection.execute(
            "DELETE FROM profile_items WHERE account_id = ? "
            "AND profile_item_id = 'item-backup-only'",
            (ACCOUNT,),
        )
        database.connection.execute(
            "INSERT INTO profile_account_controls "
            "(account_id, profile_usage_enabled, controls_version, "
            "usage_control_version, updated_at) "
            "VALUES (?, 0, 'profile-controls-v1', 2, ?)",
            (ACCOUNT, TS),
        )
        database.connection.execute(
            "INSERT INTO profile_item_feedback "
            "(feedback_id, account_id, profile_item_id, kind, note, item_version, created_at) "
            "VALUES ('feedback-1', ?, 'item-edit', 'accurate', NULL, 2, ?)",
            (ACCOUNT, TS),
        )
        database.connection.execute(
            "INSERT INTO profile_extraction_tombstones "
            "(account_id, message_id, created_at) VALUES (?, 'message-deleted', ?)",
            (ACCOUNT, TS),
        )
        database.connection.execute(
            "INSERT INTO profile_extraction_privacy_blocks "
            "(block_id, account_id, scope, normalized_value, created_at) "
            "VALUES ('block-account', ?, 'account', '', ?)",
            (ACCOUNT, TS),
        )
        database.connection.execute(
            "INSERT INTO profile_item_migrations "
            "(run_id, account_id, migration_version, status, migrated, duplicated, "
            "tombstoned, skipped, failed, created_item_ids_json, source_record_ids_json, "
            "reconciliation_digest, retryable, created_at) "
            "VALUES ('run-cutover', ?, 'profile-migration-v1', 'completed', 1, 0, 0, 0, 0, "
            "'[\"item-new\"]', '[]', 'digest-1', 0, ?)",
            (ACCOUNT, TS),
        )
        database.connection.execute(
            "INSERT INTO profile_item_migration_records "
            "(run_id, account_id, source_record_id, outcome, reason_code, "
            "profile_item_id, recorded_at) "
            "VALUES ('run-cutover', ?, 'record-1', 'migrated', 'ok', 'item-new', ?)",
            (ACCOUNT, TS),
        )
        database.connection.execute(
            "INSERT INTO profile_extraction_runs "
            "(extraction_id, account_id, message_id, extractor_version, source_hash, "
            "source_snapshot, status, created_at, updated_at) "
            "VALUES ('run-current-pending', ?, 'message-current-pending', 'new-v1', "
            "'hash-current', '切换后来源快照', 'pending', ?, ?)",
            (ACCOUNT, TS, TS),
        )
        database.connection.execute(
            "INSERT INTO profile_extraction_tasks "
            "(task_id, account_id, message_id, extractor_version, source_hash, status, "
            "created_at, updated_at) "
            "VALUES ('task-current-pending', ?, 'message-current-pending', 'new-v1', "
            "'hash-current', 'pending', ?, ?)",
            (ACCOUNT, TS, TS),
        )
    _seed_claim(database, "claim-current-pending", "task-current-pending")


def _backup_and_current(tmp_path: Path):
    database_path, template = _open_database(tmp_path)
    _seed_cutover_template(template)
    template.close()
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    backup = create_verified_backup(_authority(database_path), backup_dir / "cutover.db")
    current_path = tmp_path / "current" / "bridges.db"
    current_path.parent.mkdir()
    shutil.copy2(backup.path, current_path)
    current = BridgesDatabase(current_path)
    current.initialize()
    _mutate_after_cutover(current)
    current.close()
    return backup, current_path


def test_recovery_preserves_post_cutover_state_and_reupgrades(tmp_path: Path) -> None:
    backup, current_path = _backup_and_current(tmp_path)
    target_dir = tmp_path / "restored"
    target_dir.mkdir()
    target_path = target_dir / "bridges.db"
    report = recover_cutover_state(
        backup=backup,
        current_path=current_path,
        target_path=target_path,
        account_id=ACCOUNT,
    )
    assert report.passed
    assert report.counts["items_added_since_backup"] == 1
    assert report.counts["backup_only_items_retained"] == 1
    assert report.counts["changed_items_replaced"] == 2

    target = BridgesDatabase(target_path)
    target.initialize()
    items = {
        str(row["profile_item_id"]): dict(row)
        for row in target.connection.execute(
            "SELECT * FROM profile_items WHERE account_id = ?", (ACCOUNT,)
        ).fetchall()
    }
    assert items["item-new"]["text"] == "我加入了摄影社"
    assert items["item-edit"]["text"] == "我住在杭州"
    assert items["item-edit"]["user_edited_at"] == TS
    assert items["item-delete"]["status"] == "withdrawn"
    assert items["item-delete"]["text"] == ""
    assert items["item-backup-only"]["text"] == "我养了一只猫"

    controls = target.connection.execute(
        "SELECT profile_usage_enabled FROM profile_account_controls WHERE account_id = ?",
        (ACCOUNT,),
    ).fetchone()
    assert controls is not None and int(controls["profile_usage_enabled"]) == 0
    assert target.connection.execute(
        "SELECT 1 FROM profile_item_feedback WHERE account_id = ? AND feedback_id = 'feedback-1'",
        (ACCOUNT,),
    ).fetchone()
    assert target.connection.execute(
        "SELECT 1 FROM profile_extraction_tombstones "
        "WHERE account_id = ? AND message_id = 'message-deleted'",
        (ACCOUNT,),
    ).fetchone()
    assert target.connection.execute(
        "SELECT 1 FROM profile_item_migrations WHERE account_id = ? AND run_id = 'run-cutover'",
        (ACCOUNT,),
    ).fetchone()
    assert (
        target.connection.execute(
            "SELECT 1 FROM profile_extraction_runs "
            "WHERE account_id = ? AND extraction_id = 'run-backup-pending'",
            (ACCOUNT,),
        ).fetchone()
        is None
    )
    assert (
        target.connection.execute(
            "SELECT 1 FROM profile_extraction_tasks "
            "WHERE account_id = ? AND task_id = 'task-backup-pending'",
            (ACCOUNT,),
        ).fetchone()
        is None
    )
    assert target.connection.execute(
        "SELECT 1 FROM profile_extraction_runs "
        "WHERE account_id = ? AND extraction_id = 'run-current-pending'",
        (ACCOUNT,),
    ).fetchone()
    assert target.connection.execute(
        "SELECT 1 FROM profile_extraction_tasks "
        "WHERE account_id = ? AND task_id = 'task-current-pending'",
        (ACCOUNT,),
    ).fetchone()
    claims = {
        str(row["task_key"])
        for row in target.connection.execute(
            "SELECT task_key FROM task_claims WHERE queue_name = 'profile-extraction'"
        ).fetchall()
    }
    assert claims == {"task-current-pending"}
    assert report.counts["profile_queue_claims"] == 1
    assert report.counts["stale_extraction_rows_remaining"] == 0
    for table in (
        "profile_overview",
        "profile_ongoing_context",
        "profile_recall_index_versions",
        "profile_recall_index_entries",
    ):
        row = target.connection.execute(
            f"SELECT COUNT(*) AS total FROM {table} WHERE account_id = ?",
            (ACCOUNT,),
        ).fetchone()
        assert row is not None and int(row["total"]) == 0

    other = target.connection.execute(
        "SELECT text FROM profile_items WHERE account_id = ? AND profile_item_id = 'item-other'",
        (OTHER_ACCOUNT,),
    ).fetchone()
    assert other is not None and other["text"] == "另一个账户的私有事实"

    audit = audit_profile_retirement(target_path, account_id=ACCOUNT)
    assert audit.passed
    target.close()


def test_recovery_refuses_existing_target(tmp_path: Path) -> None:
    database_path, template = _open_database(tmp_path)
    _seed_cutover_template(template)
    template.close()
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    backup = create_verified_backup(_authority(database_path), backup_dir / "cutover.db")
    target = tmp_path / "target.db"
    target.write_text("do not overwrite", encoding="utf-8")
    with pytest.raises(Exception) as exc:
        recover_cutover_state(
            backup=backup,
            current_path=database_path,
            target_path=target,
            account_id=ACCOUNT,
        )
    assert getattr(exc.value, "code", "") == "target_exists"
    assert target.read_text(encoding="utf-8") == "do not overwrite"


def test_recovery_rejects_active_writer(tmp_path: Path) -> None:
    backup, current_path = _backup_and_current(tmp_path)
    (current_path.parent / ".bridges.lock").write_text(
        f"pid={os.getpid()}",
        encoding="ascii",
    )
    target = tmp_path / "recovered" / "bridges.db"
    target.parent.mkdir()

    with pytest.raises(ProfileReplayError) as exc:
        recover_all_cutover_state(
            backup=backup,
            current_path=current_path,
            target_path=target,
        )

    assert exc.value.code == "writer_active"
    assert not target.exists()


def test_recover_all_reports_every_account(tmp_path: Path) -> None:
    backup, current_path = _backup_and_current(tmp_path)
    target = tmp_path / "recovered" / "bridges.db"
    target.parent.mkdir()

    result = recover_all_cutover_state(
        backup=backup,
        current_path=current_path,
        target_path=target,
    )

    assert result.passed
    assert set(result.account_ids) == {ACCOUNT, OTHER_ACCOUNT}
    assert {report.account_id for report in result.recoveries} == {
        ACCOUNT,
        OTHER_ACCOUNT,
    }


def test_recovery_rejects_account_set_changed(tmp_path: Path) -> None:
    backup, current_path = _backup_and_current(tmp_path)
    current = BridgesDatabase(current_path)
    current.initialize()
    _seed_account(current, "account-created-after-backup")
    _seed_item(current, "account-created-after-backup", "item-new-account", "新账户")
    current.close()
    target = tmp_path / "recovered" / "bridges.db"
    target.parent.mkdir()

    with pytest.raises(ProfileReplayError) as exc:
        recover_all_cutover_state(
            backup=backup,
            current_path=current_path,
            target_path=target,
        )

    assert exc.value.code == "account_set_changed"
    assert not target.exists()
