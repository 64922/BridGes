"""画像工单 16：历史只读预览、可恢复备份与来源漂移校验的合同测试。"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import SecretStr

from bridges.config import Settings
from bridges.profiles.atomic import identity_key
from bridges.profiles.history_contracts import HistoryPreviewManifest
from bridges.profiles.history_preview import (
    ProfileHistoryError,
    create_history_backup,
    load_preview_manifest,
    preview_history,
    save_preview_manifest,
    verify_history_restore,
    verify_preview_manifest,
)
from bridges.profiles.replay import (
    ProfileReplayError,
    create_verified_backup,
    resolve_authoritative_database,
)
from bridges.runtime.lock import DataDirectoryLock
from bridges.storage.database import SCHEMA_VERSION, BridgesDatabase

ACCOUNT = "account-alice"
OTHER_ACCOUNT = "account-bob"
TS = "2026-08-12T00:00:00+00:00"
FIXED_NOW = datetime(2026, 10, 16, 4, 0, 0, tzinfo=UTC)


def _settings(path: Path) -> Settings:
    return Settings(
        environment="test",
        database_url=SecretStr(f"sqlite:///{path.as_posix()}"),
    )


def _open_database(tmp_path: Path) -> tuple[Path, BridgesDatabase]:
    database_path = tmp_path / "runtime" / "bridges.db"
    database_path.parent.mkdir(parents=True, exist_ok=True)
    database = BridgesDatabase(database_path)
    assert database.initialize() == SCHEMA_VERSION
    return database_path, database


def _seed_account(database: BridgesDatabase, account_id: str) -> None:
    with database.transaction():
        database.connection.execute(
            "INSERT INTO accounts (account_id, email, created_at) VALUES (?, ?, ?)",
            (account_id, f"{account_id}@example.com", TS),
        )
        database.connection.execute(
            "INSERT INTO conversations "
            "(conversation_id, account_id, title, mode, created_at, updated_at) "
            "VALUES (?, ?, '', 'companion', ?, ?)",
            (f"conversation-{account_id}", account_id, TS, TS),
        )


def _seed_message(
    database: BridgesDatabase,
    account_id: str,
    message_id: str,
    content: str,
    *,
    role: str = "user",
    status: str = "done",
    created_at: str = TS,
) -> None:
    with database.transaction():
        database.connection.execute(
            "INSERT INTO messages "
            "(message_id, conversation_id, account_id, role, attempt_number, "
            "status, content, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)",
            (
                message_id,
                f"conversation-{account_id}",
                account_id,
                role,
                status,
                content,
                created_at,
                created_at,
            ),
        )


def _seed_item(
    database: BridgesDatabase,
    account_id: str,
    item_id: str,
    text: str,
    *,
    status: str = "active",
    origin: str = "automatic",
    sources: tuple[str, ...] = (),
    quotes: dict[str, str] | None = None,
    usage_purpose: str | None = None,
    stored_identity_key: str | None = None,
    user_edited_at: str | None = None,
) -> None:
    with database.transaction():
        database.connection.execute(
            "INSERT INTO profile_items "
            "(profile_item_id, account_id, text, identity_key, fact_key, "
            "source_message_ids_json, source_quotes_json, status, write_origin, "
            "confidence, version, created_at, updated_at, user_edited_at, "
            "usage_purpose) "
            "VALUES (?, ?, ?, ?, '', ?, ?, ?, ?, 'medium', 1, ?, ?, ?, ?)",
            (
                item_id,
                account_id,
                text,
                stored_identity_key or identity_key(account_id, text),
                json.dumps(list(sources), ensure_ascii=False),
                json.dumps(quotes or {}, ensure_ascii=False),
                status,
                origin,
                TS,
                TS,
                user_edited_at,
                usage_purpose,
            ),
        )


def _seed_persona(database: BridgesDatabase, account_id: str = ACCOUNT) -> dict[str, str]:
    """合成旧库：人工、可核验自动、缺证据、敏感、凭据、删除与禁止记录。"""

    _seed_account(database, account_id)
    prefix = account_id.rsplit("-", 1)[-1]
    messages = {
        "cat": f"msg-{prefix}-cat",
        "tombstoned": f"msg-{prefix}-tombstoned",
        "stopped": f"msg-{prefix}-stopped",
        "blocked": f"msg-{prefix}-blocked",
        "withdrawn": f"msg-{prefix}-withdrawn",
        "eligible": f"msg-{prefix}-eligible",
        "assistant": f"msg-{prefix}-assistant",
        "missing": f"msg-{prefix}-deleted-source",
    }
    _seed_message(database, account_id, messages["cat"], "我养了一只猫叫团子")
    _seed_message(
        database, account_id, messages["tombstoned"], "这段历史已经被删除"
    )
    _seed_message(
        database,
        account_id,
        messages["stopped"],
        "这是一个被停止的回合",
        status="stopped",
    )
    _seed_message(
        database, account_id, messages["blocked"], "这段内容被禁止记录"
    )
    _seed_message(
        database, account_id, messages["withdrawn"], "我其实搬去了上海"
    )
    _seed_message(
        database, account_id, messages["eligible"], "我平时喜欢跑步"
    )
    _seed_message(
        database,
        account_id,
        messages["assistant"],
        "好的，我记住了",
        role="assistant",
    )
    items = {
        "manual": f"item-{prefix}-manual",
        "auto_ok": f"item-{prefix}-auto-ok",
        "auto_missing": f"item-{prefix}-auto-missing",
        "auto_partial": f"item-{prefix}-auto-partial",
        "auto_deleted": f"item-{prefix}-auto-deleted",
        "auto_no_source": f"item-{prefix}-auto-no-source",
        "sensitive_authorized": f"item-{prefix}-sensitive-ok",
        "sensitive_unauthorized": f"item-{prefix}-sensitive-no",
        "credential": f"item-{prefix}-credential",
        "withdrawn": f"item-{prefix}-withdrawn",
        "superseded": f"item-{prefix}-superseded",
    }
    _seed_item(database, account_id, items["manual"], "我叫小林", origin="user")
    _seed_item(
        database,
        account_id,
        items["auto_ok"],
        "我养了一只猫叫团子",
        sources=(messages["cat"],),
        quotes={messages["cat"]: "我养了一只猫叫团子"},
    )
    _seed_item(
        database,
        account_id,
        items["auto_missing"],
        "我在读大二",
        sources=(messages["missing"],),
    )
    _seed_item(
        database,
        account_id,
        items["auto_partial"],
        "我周末去爬山",
        sources=(messages["cat"], messages["missing"]),
    )
    _seed_item(
        database,
        account_id,
        items["auto_deleted"],
        "这段历史已经被删除",
        sources=(messages["tombstoned"],),
    )
    _seed_item(
        database,
        account_id,
        items["auto_no_source"],
        "这是没有来源的旧条目",
    )
    _seed_item(
        database,
        account_id,
        items["sensitive_authorized"],
        "我有花粉过敏，户外活动要注意",
        origin="user",
        usage_purpose="户外建议",
    )
    _seed_item(
        database,
        account_id,
        items["sensitive_unauthorized"],
        "我最近有点焦虑",
        sources=(messages["eligible"],),
    )
    _seed_item(
        database,
        account_id,
        items["credential"],
        "我的密码是 abc12345",
        origin="user",
    )
    _seed_item(
        database,
        account_id,
        items["withdrawn"],
        "",
        status="withdrawn",
        stored_identity_key=identity_key(account_id, "我其实搬去了上海"),
        sources=(messages["withdrawn"],),
    )
    _seed_item(
        database, account_id, items["superseded"], "我在读大一", status="superseded"
    )
    with database.transaction():
        database.connection.execute(
            "INSERT INTO profile_extraction_tombstones "
            "(account_id, message_id, created_at) VALUES (?, ?, ?)",
            (account_id, messages["tombstoned"], TS),
        )
        database.connection.execute(
            "INSERT INTO profile_extraction_privacy_blocks "
            "(block_id, account_id, scope, normalized_value, created_at) "
            "VALUES (?, ?, 'content', ?, ?)",
            (f"block-{prefix}", account_id, "这段内容被禁止记录", TS),
        )
    return messages | items


def _business_row_counts(database: BridgesDatabase, account_id: str) -> dict[str, int]:
    tables = (
        "profile_items",
        "profile_extraction_runs",
        "profile_extraction_tasks",
        "profile_extraction_observations",
        "profile_extraction_tombstones",
        "profile_extraction_privacy_blocks",
        "profile_account_controls",
        "profile_item_feedback",
    )
    counts: dict[str, int] = {}
    for table in tables:
        row = database.connection.execute(
            f"SELECT COUNT(*) AS total FROM {table} WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        counts[table] = 0 if row is None else int(row["total"])
    return counts


def _preview(tmp_path: Path, database_path: Path) -> HistoryPreviewManifest:
    authority = resolve_authoritative_database(settings=_settings(database_path))
    return preview_history(authority, account_id=ACCOUNT, now=FIXED_NOW)


def test_preview_is_read_only_repeatable_and_never_leaks_text(tmp_path: Path) -> None:
    database_path, database = _open_database(tmp_path)
    _seed_persona(database)
    before_counts = _business_row_counts(database, ACCOUNT)
    database.close()
    before_db_digest = hashlib.sha256(database_path.read_bytes()).hexdigest()

    manifest = _preview(tmp_path, database_path)
    second = _preview(tmp_path, database_path)

    assert manifest.schema_version == SCHEMA_VERSION
    assert len(manifest.source_digest) == 64
    assert manifest.as_dict() == second.as_dict()
    assert hashlib.sha256(database_path.read_bytes()).hexdigest() == before_db_digest

    database = BridgesDatabase(database_path)
    try:
        assert database.schema_version == SCHEMA_VERSION
        assert _business_row_counts(database, ACCOUNT) == before_counts
        row = database.connection.execute(
            "SELECT COUNT(*) AS total FROM profile_extraction_runs"
        ).fetchone()
        assert row is not None and int(row["total"]) == 0
    finally:
        database.close()

    rendered = json.dumps(manifest.as_dict(), ensure_ascii=False)
    for private_text in (
        "我养了一只猫叫团子",
        "我叫小林",
        "我有花粉过敏",
        "我最近有点焦虑",
        "我的密码是",
        "我其实搬去了上海",
        "这段内容被禁止记录",
    ):
        assert private_text not in rendered


def test_preview_classifies_items_chat_scope_controls_and_barriers(
    tmp_path: Path,
) -> None:
    database_path, database = _open_database(tmp_path)
    keys = _seed_persona(database)
    database.close()

    manifest = _preview(tmp_path, database_path)

    items = manifest.items
    assert items.active_total == 9
    assert items.manual_preserved == 3
    assert items.automatic_verifiable == 2
    assert items.automatic_partial_evidence == 1
    assert items.automatic_deleted_evidence == 1
    assert items.automatic_unverified_source == 1
    assert items.automatic_missing_evidence == 1
    assert items.sensitive_authorized == 1
    assert items.sensitive_unauthorized == 1
    assert items.credentials_flagged == 1
    assert items.withdrawn == 1
    assert items.superseded == 1
    assert items.locators["manual_preserved"] == sorted(
        [
            keys["manual"],
            keys["sensitive_authorized"],
            keys["credential"],
        ]
    )
    assert items.locators["automatic_verifiable"] == sorted(
        [keys["auto_ok"], keys["sensitive_unauthorized"]]
    )
    assert items.locators["automatic_unverified_source"] == [keys["auto_missing"]]
    assert items.locators["automatic_deleted_evidence"] == [keys["auto_deleted"]]
    assert items.locators["automatic_missing_evidence"] == [keys["auto_no_source"]]
    assert items.locators["automatic_partial_evidence"] == [keys["auto_partial"]]
    assert items.locators["withdrawn"] == [keys["withdrawn"]]

    chat = manifest.chat_scope
    assert chat.user_messages_total == 6
    assert chat.eligible == 2
    assert chat.skipped_tombstoned == 1
    assert chat.skipped_unfinished == 1
    assert chat.skipped_content_blocked == 1
    assert chat.skipped_withdrawn_suppressed == 1
    assert chat.skipped_recording_off == 0
    assert chat.eligible_conversation_ids == (f"conversation-{ACCOUNT}",)

    assert manifest.controls.recording_enabled is True
    assert manifest.controls.usage_enabled is True
    assert manifest.controls.content_block_count == 1
    assert manifest.controls.tombstone_count == 1
    assert manifest.domain_refs.feedback_count == 0


def test_preview_honours_recording_off_and_usage_switch(tmp_path: Path) -> None:
    database_path, database = _open_database(tmp_path)
    _seed_persona(database)
    with database.transaction():
        database.connection.execute(
            "INSERT INTO profile_extraction_privacy_blocks "
            "(block_id, account_id, scope, normalized_value, created_at) "
            "VALUES (?, ?, 'account', '', ?)",
            ("block-off", ACCOUNT, TS),
        )
        database.connection.execute(
            "INSERT INTO profile_account_controls "
            "(account_id, profile_usage_enabled, controls_version, "
            "usage_control_version, updated_at) "
            "VALUES (?, 0, 'profile-controls-v1', 3, ?)",
            (ACCOUNT, TS),
        )
    database.close()

    manifest = _preview(tmp_path, database_path)

    assert manifest.controls.recording_enabled is False
    assert manifest.controls.usage_enabled is False
    assert manifest.controls.usage_control_version == 3
    assert manifest.chat_scope.eligible == 0
    assert manifest.chat_scope.skipped_recording_off == 2
    assert manifest.chat_scope.skipped_tombstoned == 1
    assert manifest.chat_scope.skipped_withdrawn_suppressed == 1


def test_preview_is_account_scoped_and_digest_binds_account(tmp_path: Path) -> None:
    database_path, database = _open_database(tmp_path)
    keys = _seed_persona(database, ACCOUNT)
    other_keys = _seed_persona(database, OTHER_ACCOUNT)
    database.close()

    alice = _preview(tmp_path, database_path)
    authority = resolve_authoritative_database(settings=_settings(database_path))
    bob = preview_history(authority, account_id=OTHER_ACCOUNT, now=FIXED_NOW)

    alice_ids = {
        item_id
        for locators in alice.items.locators.values()
        for item_id in locators
    }
    assert other_keys["manual"] not in alice_ids
    assert keys["manual"] in alice_ids
    assert f"conversation-{OTHER_ACCOUNT}" not in alice.chat_scope.eligible_conversation_ids
    assert alice.state_digest != bob.state_digest
    assert alice.account_id == ACCOUNT


def test_preview_rejects_unknown_account(tmp_path: Path) -> None:
    database_path, database = _open_database(tmp_path)
    _seed_account(database, ACCOUNT)
    database.close()

    authority = resolve_authoritative_database(settings=_settings(database_path))
    with pytest.raises(ProfileHistoryError) as exc_info:
        preview_history(authority, account_id="account-missing", now=FIXED_NOW)
    assert exc_info.value.code == "account_missing"


def test_preview_rejects_old_and_newer_schema_without_upgrade(tmp_path: Path) -> None:
    database_path, database = _open_database(tmp_path)
    _seed_persona(database)
    with database.transaction():
        database.connection.execute(
            "UPDATE schema_meta SET value = ? WHERE key = 'version'", ("83",)
        )
    database.close()

    authority = resolve_authoritative_database(settings=_settings(database_path))
    with pytest.raises(ProfileHistoryError) as exc_info:
        preview_history(authority, account_id=ACCOUNT, now=FIXED_NOW)
    assert exc_info.value.code == "schema_upgrade_required"

    database = BridgesDatabase(database_path)
    assert database.schema_version == 83
    database.close()

    database = BridgesDatabase(database_path)
    with database.transaction():
        database.connection.execute(
            "UPDATE schema_meta SET value = ? WHERE key = 'version'", ("999",)
        )
    database.close()
    with pytest.raises(ProfileHistoryError) as exc_info:
        preview_history(authority, account_id=ACCOUNT, now=FIXED_NOW)
    assert exc_info.value.code == "schema_version_unsupported"


def test_preview_rejects_conflicting_data_source(tmp_path: Path) -> None:
    database_path, database = _open_database(tmp_path)
    database.close()
    candidate = tmp_path / "other" / "bridges.db"
    candidate.parent.mkdir()
    shutil.copyfile(database_path, candidate)

    with pytest.raises(ProfileReplayError) as exc_info:
        resolve_authoritative_database(
            settings=_settings(database_path),
            explicit_path=candidate,
        )
    assert exc_info.value.code == "database_path_conflict"


def test_verify_detects_new_message_source_edit_and_control_change(
    tmp_path: Path,
) -> None:
    database_path, database = _open_database(tmp_path)
    _seed_persona(database)
    database.close()
    manifest = _preview(tmp_path, database_path)
    authority = resolve_authoritative_database(settings=_settings(database_path))

    fresh = verify_preview_manifest(authority, manifest, now=FIXED_NOW)
    assert fresh.fresh is True
    assert fresh.requires_recompute is False

    database = BridgesDatabase(database_path)
    _seed_message(database, ACCOUNT, "msg-alice-new", "我最近开始学吉他")
    database.close()
    drifted = verify_preview_manifest(authority, manifest, now=FIXED_NOW)
    assert drifted.fresh is False
    assert drifted.requires_recompute is True
    assert drifted.deltas["user_messages"] == {"preview": 6, "current": 7}
    assert "重新" in drifted.affected_scope

    database = BridgesDatabase(database_path)
    with database.transaction():
        database.connection.execute(
            "UPDATE messages SET content = ? WHERE message_id = ?",
            ("我养了一只狗叫团子", "msg-alice-cat"),
        )
    database.close()
    edited = verify_preview_manifest(authority, manifest, now=FIXED_NOW)
    assert edited.fresh is False

    database = BridgesDatabase(database_path)
    with database.transaction():
        database.connection.execute(
            "INSERT INTO profile_account_controls "
            "(account_id, profile_usage_enabled, controls_version, "
            "usage_control_version, updated_at) VALUES (?, 0, ?, 1, ?) "
            "ON CONFLICT(account_id) DO UPDATE SET profile_usage_enabled = 0",
            (ACCOUNT, "profile-controls-v1", TS),
        )
    database.close()
    controlled = verify_preview_manifest(authority, manifest, now=FIXED_NOW)
    assert controlled.fresh is False
    assert controlled.deltas["controls_changed"] is True


def test_backup_creates_verified_record_with_isolated_restore_drill(
    tmp_path: Path,
) -> None:
    database_path, database = _open_database(tmp_path)
    _seed_persona(database)
    database.close()
    manifest = _preview(tmp_path, database_path)
    authority = resolve_authoritative_database(settings=_settings(database_path))
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    target = tmp_path / "backup" / "history.db"
    target.parent.mkdir()

    record = create_history_backup(
        authority, target, preview=manifest, work_dir=work_dir, now=FIXED_NOW
    )

    assert record.verified is True
    assert target.exists()
    assert Path(f"{target}.manifest.json").exists()
    record_path = Path(f"{target}.history-record.json")
    assert record_path.exists()
    payload = json.loads(record_path.read_text(encoding="utf-8"))
    assert payload["verified"] is True
    assert payload["consistency"]["method"] == "quiesced_sqlite_online_backup"
    assert payload["consistency"]["restore_requirements"] == {
        "program_schema_version": SCHEMA_VERSION,
        "backup_schema_version": SCHEMA_VERSION,
    }
    restore = payload["restore"]
    assert restore["opened"] is True
    assert restore["integrity_check"] == "ok"
    assert restore["state_digest_matches_preview"] is True
    assert restore["reconciliation_passed"] is True
    assert restore["isolated_copy_removed"] is True
    for key in (
        "items.counts.withdrawn",
        "items.counts.manual_preserved",
        "items.locators.withdrawn",
        "chat_scope.skipped_tombstoned",
        "chat_scope.eligible",
        "controls.recording_enabled",
        "controls.tombstone_count",
        "domain_refs.feedback_count",
    ):
        assert key in restore["checked_keys"]
    assert list(work_dir.iterdir()) == []

    replay = verify_history_restore(target, preview=manifest, work_dir=work_dir)
    assert replay.verified is True
    assert list(work_dir.iterdir()) == []


def test_backup_refuses_overwrite_and_source_target(tmp_path: Path) -> None:
    database_path, database = _open_database(tmp_path)
    _seed_persona(database)
    database.close()
    manifest = _preview(tmp_path, database_path)
    authority = resolve_authoritative_database(settings=_settings(database_path))
    target = tmp_path / "history.db"

    create_history_backup(authority, target, preview=manifest, now=FIXED_NOW)

    with pytest.raises(ProfileHistoryError) as exc_info:
        create_history_backup(authority, target, preview=manifest, now=FIXED_NOW)
    assert exc_info.value.code == "backup_target_exists"

    with pytest.raises(ProfileHistoryError) as exc_info:
        create_history_backup(
            authority, database_path, preview=manifest, now=FIXED_NOW
        )
    assert exc_info.value.code == "backup_target_conflict"


def test_backup_requires_fresh_preview_and_quiesced_writer(tmp_path: Path) -> None:
    database_path, database = _open_database(tmp_path)
    _seed_persona(database)
    database.close()
    manifest = _preview(tmp_path, database_path)
    authority = resolve_authoritative_database(settings=_settings(database_path))

    database = BridgesDatabase(database_path)
    _seed_message(database, ACCOUNT, "msg-alice-late", "我最近换了工作")
    database.close()
    with pytest.raises(ProfileHistoryError) as exc_info:
        create_history_backup(
            authority, tmp_path / "stale.db", preview=manifest, now=FIXED_NOW
        )
    assert exc_info.value.code == "preview_stale"
    assert not (tmp_path / "stale.db").exists()

    fresh_manifest = _preview(tmp_path, database_path)
    lock = DataDirectoryLock(database_path.parent)
    lock.acquire()
    try:
        with pytest.raises(ProfileHistoryError) as exc_info:
            create_history_backup(
                authority,
                tmp_path / "locked.db",
                preview=fresh_manifest,
                now=FIXED_NOW,
            )
        assert exc_info.value.code in {"writer_active", "writer_state_unknown"}
        assert not (tmp_path / "locked.db").exists()
    finally:
        lock.release()


def test_restore_drill_detects_drifted_backup_rows(tmp_path: Path) -> None:
    database_path, database = _open_database(tmp_path)
    keys = _seed_persona(database)
    database.close()
    manifest = _preview(tmp_path, database_path)
    authority = resolve_authoritative_database(settings=_settings(database_path))

    database = BridgesDatabase(database_path)
    with database.transaction():
        database.connection.execute(
            "UPDATE profile_items SET status = 'withdrawn', text = '' "
            "WHERE profile_item_id = ?",
            (keys["manual"],),
        )
    database.close()

    drifted = tmp_path / "drifted.db"
    create_verified_backup(authority, drifted)

    with pytest.raises(ProfileHistoryError) as exc_info:
        verify_history_restore(drifted, preview=manifest, now=FIXED_NOW)
    assert exc_info.value.code == "backup_drill_failed"
    assert "items." in (exc_info.value.message or "")

    with pytest.raises(ProfileHistoryError) as exc_info:
        verify_history_restore(tmp_path / "missing-manifest.db", preview=manifest)
    assert exc_info.value.code == "backup_unverified"


def test_manifest_roundtrip_and_version_gate(tmp_path: Path) -> None:
    database_path, database = _open_database(tmp_path)
    _seed_persona(database)
    database.close()
    manifest = _preview(tmp_path, database_path)
    manifest_path = tmp_path / "preview.json"
    save_preview_manifest(manifest, manifest_path)

    loaded = load_preview_manifest(manifest_path)
    assert loaded.as_dict() == manifest.as_dict()

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["manifest_version"] = "profile-history-preview-v99"
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ProfileHistoryError) as exc_info:
        load_preview_manifest(manifest_path)
    assert exc_info.value.code == "manifest_version_unsupported"

    manifest_path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ProfileHistoryError) as exc_info:
        load_preview_manifest(manifest_path)
    assert exc_info.value.code == "manifest_invalid"


def test_tampered_manifest_account_fails_closed(tmp_path: Path) -> None:
    database_path, database = _open_database(tmp_path)
    _seed_persona(database)
    _seed_account(database, OTHER_ACCOUNT)
    database.close()
    manifest = _preview(tmp_path, database_path)
    authority = resolve_authoritative_database(settings=_settings(database_path))

    migrated = replace(manifest, account_id=OTHER_ACCOUNT)
    report = verify_preview_manifest(authority, migrated, now=FIXED_NOW)
    assert report.fresh is False
    assert report.requires_recompute is True

    ghost = replace(manifest, account_id="account-ghost")
    with pytest.raises(ProfileHistoryError) as exc_info:
        verify_preview_manifest(authority, ghost, now=FIXED_NOW)
    assert exc_info.value.code == "account_missing"

    with pytest.raises(ProfileHistoryError) as exc_info:
        create_history_backup(
            authority, tmp_path / "ghost.db", preview=ghost, now=FIXED_NOW
        )
    assert exc_info.value.code == "account_missing"
