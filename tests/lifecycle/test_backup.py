"""加密备份与恢复测试（Issue 37，AC5-8）。

覆盖：备份创建（加密/manifest/金丝雀不出现/一致性快照）、损坏包与摘要
不符与口令错误与版本不兼容与空间不足全部在替换前拒绝（不破坏现有数据）、
恢复一致性（SQLite/对象/索引摘要比对）、失败原子回滚（替换中途失败回
到恢复前状态）、凭据待重新配置、索引重建调度、恢复后登录可用。
"""

from __future__ import annotations

import json
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from harness import (
    PASSWORD_CANARY,
    QWEN_CANARY,
    SMTP_CANARY,
    Harness,
    logical_summary,
)

from bridges.contracts.lifecycle import DataLifecycleError
from bridges.lifecycle.backup import BACKUP_FORMAT_VERSION, BACKUP_MAGIC, _sha256

PASSPHRASE = "备份口令-测试123"


def _create_and_backup(harness: Harness) -> tuple[str, bytes]:
    harness.seed_everything()
    return harness.backup.create_backup(PASSPHRASE)


def _split_container(backup_bytes: bytes) -> tuple[dict, bytes]:
    manifest_line, _, payload = backup_bytes[len(BACKUP_MAGIC):].partition(b"\n")
    return json.loads(manifest_line), payload


# ---------------------------------------------------------------------------
# 创建备份
# ---------------------------------------------------------------------------


def test_backup_contains_manifest_and_encrypted_payload(tmp_path) -> None:
    harness = Harness(tmp_path)
    name, backup_bytes = _create_and_backup(harness)
    assert name.endswith(".bridgesbackup")
    assert backup_bytes.startswith(BACKUP_MAGIC)
    manifest, payload = _split_container(backup_bytes)
    assert manifest["format_version"] == BACKUP_FORMAT_VERSION
    assert manifest["payload_sha256"] == _sha256(payload)
    assert manifest["account_count"] == 2
    assert manifest["stats"]["conversations"] >= 2
    assert manifest["schema_version"] >= 25
    entry_names = [entry["name"] for entry in manifest["files"]]
    assert "bridges.db" in entry_names
    assert "identity.json" in entry_names
    assert any(name.startswith("objects/") for name in entry_names)


def test_backup_excludes_secrets_and_sessions(tmp_path) -> None:
    harness = Harness(tmp_path)
    _, backup_bytes = _create_and_backup(harness)
    raw = backup_bytes.decode("utf-8", errors="replace")
    assert QWEN_CANARY not in raw
    assert SMTP_CANARY not in raw
    # 身份只含账户数据：密码哈希在（恢复后可登录），会话/恢复令牌与设备
    # 绑定不在 payload 中（identity.json 只有账户与索引）。
    manifest, payload = _split_container(backup_bytes)
    assert manifest is not None and payload is not None
    # 用口令解密 payload，检查 identity.json 不含会话字段。
    from bridges.lifecycle.backup import _fernet_for

    decrypted = _fernet_for(PASSPHRASE, bytes.fromhex(manifest["salt"])).decrypt(payload)
    with zipfile.ZipFile(BytesIO(decrypted)) as archive:
        identity = json.loads(archive.read("identity.json"))
    assert "sessions" not in identity
    assert "recovery_states" not in identity
    assert "devices" not in identity
    assert "accounts" in identity and identity["accounts"]
    account = next(iter(identity["accounts"].values()))
    assert account["password_hash"]  # argon2 哈希（非明文），恢复后可登录
    assert PASSWORD_CANARY not in account["password_hash"]


def test_backup_requires_passphrase(tmp_path) -> None:
    harness = Harness(tmp_path)
    harness.seed_everything()
    with pytest.raises(DataLifecycleError) as excinfo:
        harness.backup.create_backup("")
    assert excinfo.value.code == "backup_passphrase_required"


# ---------------------------------------------------------------------------
# 恢复预检拒绝路径（不破坏现有数据）
# ---------------------------------------------------------------------------


def test_restore_rejects_corrupted_magic(tmp_path) -> None:
    harness = Harness(tmp_path)
    harness.seed_everything()
    with pytest.raises(DataLifecycleError) as excinfo:
        harness.backup.restore_backup(PASSPHRASE, b"NOT-A-BACKUP", confirmation="恢复")
    assert excinfo.value.code == "backup_invalid_format"
    # 现有数据未受影响。
    assert harness.identity.get_account(harness.acc1) is not None


def test_restore_rejects_tampered_payload(tmp_path) -> None:
    harness = Harness(tmp_path)
    _, backup_bytes = _create_and_backup(harness)
    # 篡改载荷字节（最后一位翻转）→ 完整性摘要不符 → 拒绝且不破坏数据。
    tampered = backup_bytes[:-1] + bytes([backup_bytes[-1] ^ 0xFF])
    preview = harness.backup.restore_backup(PASSPHRASE, tampered, confirmation="恢复")
    assert not preview.ok
    assert any("完整性" in reason for reason in preview.reasons)
    assert harness.identity.get_account(harness.acc1) is not None


def test_restore_rejects_wrong_passphrase(tmp_path) -> None:
    harness = Harness(tmp_path)
    _, backup_bytes = _create_and_backup(harness)
    preview = harness.backup.restore_backup("错误口令", backup_bytes, confirmation="恢复")
    assert not preview.ok
    assert any("口令错误" in reason for reason in preview.reasons)


def test_restore_rejects_wrong_confirmation(tmp_path) -> None:
    harness = Harness(tmp_path)
    _, backup_bytes = _create_and_backup(harness)
    preview = harness.backup.restore_backup(PASSPHRASE, backup_bytes, confirmation="取消")
    assert not preview.ok
    assert any("确认文本" in reason for reason in preview.reasons)


def test_restore_rejects_future_format_version(tmp_path) -> None:
    harness = Harness(tmp_path)
    _, backup_bytes = _create_and_backup(harness)
    manifest_line, _, rest = backup_bytes[len(BACKUP_MAGIC):].partition(b"\n")
    future = json.loads(manifest_line)
    future["format_version"] = BACKUP_FORMAT_VERSION + 1
    forged = (
        BACKUP_MAGIC
        + json.dumps(future, ensure_ascii=False).encode("utf-8")
        + b"\n"
        + rest
    )
    preview = harness.backup.restore_backup(PASSPHRASE, forged, confirmation="恢复")
    assert not preview.ok
    assert any("版本" in reason for reason in preview.reasons)
    # 未来 schema 版本同样拒绝。
    future["format_version"] = BACKUP_FORMAT_VERSION
    future["schema_version"] = 999
    forged = (
        BACKUP_MAGIC
        + json.dumps(future, ensure_ascii=False).encode("utf-8")
        + b"\n"
        + rest
    )
    preview = harness.backup.restore_backup(PASSPHRASE, forged, confirmation="恢复")
    assert not preview.ok
    assert any("更高版本" in reason for reason in preview.reasons)


def test_restore_rejects_insufficient_space(tmp_path, monkeypatch) -> None:
    harness = Harness(tmp_path)
    _, backup_bytes = _create_and_backup(harness)

    import shutil

    class _LowDisk:
        def __init__(self) -> None:
            self.free = 0

    low = _LowDisk()
    monkeypatch.setattr(shutil, "disk_usage", lambda _path: low)
    preview = harness.backup.restore_backup(PASSPHRASE, backup_bytes, confirmation="恢复")
    assert not preview.ok
    assert any("空间不足" in reason for reason in preview.reasons)
    # 数据未被破坏。
    assert harness.identity.get_account(harness.acc1) is not None


# ---------------------------------------------------------------------------
# 恢复成功路径：一致性 + 凭据复位 + 索引调度
# ---------------------------------------------------------------------------


def test_restore_rolls_back_data_and_objects(tmp_path) -> None:
    harness = Harness(tmp_path)
    harness.seed_everything()
    summary_before = logical_summary(harness, harness.acc1)
    _, backup_bytes = harness.backup.create_backup(PASSPHRASE)
    # 篡改数据，然后恢复。
    harness.database.connection.execute("UPDATE messages SET content = '被篡改'")
    harness.database.connection.execute(
        "UPDATE conversations SET title = '被篡改的对话' WHERE account_id = ?",
        (harness.acc1,),
    )
    preview = harness.backup.restore_backup(PASSPHRASE, backup_bytes, confirmation="恢复")
    assert preview.ok, preview.reasons
    assert summary_before == logical_summary(harness, harness.acc1)
    row = harness.database.connection.execute(
        "SELECT content FROM messages WHERE content LIKE '第 1 条消息%'"
    ).fetchone()
    assert row is not None and "被篡改" not in row["content"]
    # 对象内容可读（同一运行密钥解密）。
    object_rows = harness.database.scoped(harness.acc1).execute(
        "SELECT object_id FROM objects WHERE account_id = ?", (harness.acc1,)
    ).fetchall()
    assert object_rows
    content = harness.objects.get_content(harness.acc1, str(object_rows[0]["object_id"]))
    assert content == b"alice material bytes"


def test_restore_preserves_login_but_clears_external_credentials(tmp_path) -> None:
    harness = Harness(tmp_path)
    harness.seed_everything()
    _, backup_bytes = harness.backup.create_backup(PASSPHRASE)
    # 恢复后：密码哈希保留（可登录）、外部凭据清除（待重新配置）。
    preview = harness.backup.restore_backup(PASSPHRASE, backup_bytes, confirmation="恢复")
    assert preview.ok
    assert harness.identity.get_account(harness.acc1) is not None
    assert harness.credentials.get(harness.acc1) is None
    assert harness.smtp_credentials.get(harness.acc1) is None
    # 会话全部失效（备份不含会话）。
    stored = harness.state.load("identity") or {}
    assert not stored.get("sessions")


def test_restore_schedules_index_rebuild_on_mismatch(tmp_path) -> None:
    harness = Harness(tmp_path)
    harness.seed_everything()
    # 造一条已就绪的文档（含分块）与一个计数不一致的活跃索引版本：
    # index_versions 声明 2 chunk/2 vector，实际只有 1 chunk/0 vector →
    # 恢复流程必须标记该账户 rebuild_requested（由后台执行器重建）。
    harness.database.scoped(harness.acc1).execute(
        "INSERT INTO document_records(document_id, account_id, object_id,"
        " content_hash, parser_version, status, source, created_at, updated_at)"
        " VALUES ('doc-r', ?, 'obj-r', 'hash-r', 'v1', 'ready', 'knowledge_base',"
        " '2026-08-06T00:00:00+00:00', '2026-08-06T00:00:00+00:00')",
        (harness.acc1,),
    )
    harness.database.scoped(harness.acc1).execute(
        "INSERT INTO document_chunks(chunk_id, document_id, account_id, chunk_index,"
        " content, start_offset, end_offset, content_hash, vector_status,"
        " created_at, updated_at)"
        " VALUES ('chunk-r', 'doc-r', ?, 0, '分块文本', 0, 12, 'hash-r', 'indexed',"
        " '2026-08-06T00:00:00+00:00', '2026-08-06T00:00:00+00:00')",
        (harness.acc1,),
    )
    harness.database.scoped(harness.acc1).execute(
        "INSERT INTO index_versions(version_id, account_id, contract_json,"
        " contract_hash, status, expected_chunk_count, chunk_count, vector_count,"
        " built_at, created_at) VALUES ('v1', ?, '{}', 'h', 'active', 2, 2, 2,"
        " '2026-08-06T00:00:00+00:00', '2026-08-06T00:00:00+00:00')",
        (harness.acc1,),
    )
    harness.database.scoped(harness.acc1).execute(
        "INSERT INTO index_active(account_id, version_id) VALUES (?, 'v1')",
        (harness.acc1,),
    )
    _, backup_bytes = harness.backup.create_backup(PASSPHRASE)
    # 恢复后索引计数不一致（版本声明 2/2，实际 1/0）→ 必须标记重建。
    preview = harness.backup.restore_backup(PASSPHRASE, backup_bytes, confirmation="恢复")
    assert preview.ok
    row = harness.database.scoped(harness.acc1).execute(
        "SELECT rebuild_requested FROM document_records WHERE account_id = ?"
        " AND rebuild_requested = 1",
        (harness.acc1,),
    ).fetchone()
    assert row is not None and int(row["rebuild_requested"]) == 1
    # 计数一致时不标记（对照组）。
    harness.database.scoped(harness.acc1).execute(
        "INSERT INTO document_chunks(chunk_id, document_id, account_id, chunk_index,"
        " content, start_offset, end_offset, content_hash, vector_status,"
        " created_at, updated_at)"
        " VALUES ('chunk-r2', 'doc-r', ?, 1, '第二块', 0, 12, 'hash-r2', 'indexed',"
        " '2026-08-06T00:00:00+00:00', '2026-08-06T00:00:00+00:00')",
        (harness.acc1,),
    )
    harness.database.scoped(harness.acc1).execute(
        "INSERT INTO index_vectors(vector_id, version_id, account_id, chunk_id,"
        " vector_json, dimension_count, created_at) VALUES"
        " ('vec-1', 'v1', ?, 'chunk-r', '[0.1]', 1, '2026-08-06T00:00:00+00:00')",
        (harness.acc1,),
    )
    harness.database.scoped(harness.acc1).execute(
        "INSERT INTO index_vectors(vector_id, version_id, account_id, chunk_id,"
        " vector_json, dimension_count, created_at) VALUES"
        " ('vec-2', 'v1', ?, 'chunk-r2', '[0.2]', 1, '2026-08-06T00:00:00+00:00')",
        (harness.acc1,),
    )
    # 对照组：数据与索引计数一致（2 chunk/2 vector）后重新备份并恢复，
    # 不应标记重建。
    harness.database.scoped(harness.acc1).execute(
        "UPDATE document_records SET rebuild_requested = 0 WHERE account_id = ?",
        (harness.acc1,),
    )
    _, consistent_backup = harness.backup.create_backup(PASSPHRASE)
    harness.backup.restore_backup(
        PASSPHRASE, consistent_backup, confirmation="恢复"
    )
    row = harness.database.scoped(harness.acc1).execute(
        "SELECT rebuild_requested FROM document_records WHERE account_id = ?"
        " AND rebuild_requested = 1",
        (harness.acc1,),
    ).fetchone()
    assert row is None


def test_restore_swap_failure_rolls_back_atomically(tmp_path) -> None:
    harness = Harness(tmp_path)
    harness.seed_everything()
    original_db_bytes = (tmp_path / "bridges.db").read_bytes()
    _, backup_bytes = harness.backup.create_backup(PASSPHRASE)
    # 在替换阶段注入失败：构造「执行阶段失败」——把备份 payload 替换为
    # 缺失 identity.json 的 zip（预检只验证 payload 摘要，执行阶段校验
    # 文件清单时失败）。
    from bridges.lifecycle.backup import _fernet_for as fernet_for

    manifest, payload = _split_container(backup_bytes)
    decrypted = fernet_for(PASSPHRASE, bytes.fromhex(manifest["salt"])).decrypt(payload)
    with zipfile.ZipFile(BytesIO(decrypted)) as archive:
        entries = {info.filename: archive.read(info.filename) for info in archive.infolist()}
    del entries["identity.json"]
    rebuilt = BytesIO()
    with zipfile.ZipFile(rebuilt, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    decrypted_new = rebuilt.getvalue()

    forged_payload = fernet_for(PASSPHRASE, bytes.fromhex(manifest["salt"])).encrypt(
        decrypted_new
    )
    # 重建清单：新 payload 的摘要与大小必须同步，否则预检（而非执行阶段）
    # 直接拒绝——本测试要覆盖的是「执行阶段失败后原子回滚」。
    forged_manifest = dict(manifest)
    forged_manifest["payload_sha256"] = _sha256(forged_payload)
    forged_manifest["payload_size"] = len(forged_payload)
    forged = (
        BACKUP_MAGIC
        + json.dumps(forged_manifest, ensure_ascii=False).encode("utf-8")
        + b"\n"
        + forged_payload
    )
    with pytest.raises(DataLifecycleError) as excinfo:
        harness.backup.restore_backup(PASSPHRASE, forged, confirmation="恢复")
    assert "不完整" in excinfo.value.message
    # 原子回滚：原数据库字节保留，身份与凭据未动。
    assert (tmp_path / "bridges.db").read_bytes() == original_db_bytes
    assert harness.identity.get_account(harness.acc1) is not None


def test_restore_audit_events(tmp_path) -> None:
    harness = Harness(tmp_path)
    harness.seed_everything()
    _, backup_bytes = harness.backup.create_backup(PASSPHRASE)
    harness.backup.restore_backup(PASSPHRASE, backup_bytes, confirmation="恢复")
    assert "backup_create" in harness.audit_actions()
    assert "restore_complete" in harness.audit_actions()
    for details in harness.audit_details():
        serialized = str(details)
        assert QWEN_CANARY not in serialized
        assert SMTP_CANARY not in serialized


def test_restore_after_backup_without_objects(tmp_path) -> None:
    """空对象库备份恢复：对象目录重建且内容一致。"""
    harness = Harness(tmp_path)
    harness.seed_everything()
    harness.delete_account(harness.acc2)
    harness.create_object(harness.acc1, "only.txt", b"only content")
    name, backup_bytes = harness.backup.create_backup(PASSPHRASE)
    # 清空对象行再恢复。
    harness.database.scoped(harness.acc1).execute(
        "DELETE FROM objects WHERE account_id = ?", (harness.acc1,)
    )
    preview = harness.backup.restore_backup(PASSPHRASE, backup_bytes, confirmation="恢复")
    assert preview.ok, preview.reasons
    rows = harness.database.scoped(harness.acc1).execute(
        "SELECT object_id, original_filename FROM objects WHERE account_id = ?",
        (harness.acc1,),
    ).fetchall()
    assert len(rows) == 2
    only_row = next(r for r in rows if r["original_filename"] == "only.txt")
    assert harness.objects.get_content(harness.acc1, str(only_row["object_id"])) == (
        b"only content"
    )


def test_restore_swap_midway_failure_rolls_back(tmp_path, monkeypatch) -> None:
    """Verification 4「中途终止」：替换中途（rename 阶段）失败原子回滚。

    在数据库文件改名阶段注入 OSError：新文件未完全就位，恢复必须回滚
    到恢复前状态（数据、身份、会话均不变）。
    """
    harness = Harness(tmp_path)
    harness.seed_everything()
    summary_before = logical_summary(harness, harness.acc1)
    original_state = harness.state.load("identity") or {}
    _, backup_bytes = harness.backup.create_backup(PASSPHRASE)

    real_replace = Path.replace

    def _failing_replace(self: Path, target: object) -> None:
        if self.name == "bridges.db" and "pre-restore" in str(target):
            raise OSError("模拟替换中途磁盘错误")
        real_replace(self, target)

    monkeypatch.setattr(Path, "replace", _failing_replace)
    with pytest.raises(DataLifecycleError) as excinfo:
        harness.backup.restore_backup(PASSPHRASE, backup_bytes, confirmation="恢复")
    assert excinfo.value.code == "restore_swap_failed"
    monkeypatch.undo()
    # 原子回滚：数据（逻辑摘要；WAL checkpoint 会重写文件字节，按内容
    # 比对）、身份、凭据全部保持恢复前状态。
    assert logical_summary(harness, harness.acc1) == summary_before
    assert harness.identity.get_account(harness.acc1) is not None
    assert harness.credentials.get(harness.acc1) is not None
    assert (harness.state.load("identity") or {}) == original_state


def test_restore_finalize_failure_rolls_back_identity(tmp_path, monkeypatch) -> None:
    """Verification 4：身份复位阶段失败同样原子回滚（数据与身份都不变）。"""
    harness = Harness(tmp_path)
    harness.seed_everything()
    summary_before = logical_summary(harness, harness.acc1)
    _, backup_bytes = harness.backup.create_backup(PASSPHRASE)

    calls = {"count": 0}

    def _failing_replace(accounts_data: dict) -> None:
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("模拟身份写入失败")
        real_replace(accounts_data)

    real_replace = harness.identity.replace_accounts_from_backup
    monkeypatch.setattr(
        harness.identity, "replace_accounts_from_backup", _failing_replace
    )
    with pytest.raises(DataLifecycleError) as excinfo:
        harness.backup.restore_backup(PASSPHRASE, backup_bytes, confirmation="恢复")
    assert excinfo.value.code == "restore_finalize_failed"
    monkeypatch.undo()
    # 数据与身份都回滚：逻辑摘要保留、账户仍可登录（身份未变）。
    assert logical_summary(harness, harness.acc1) == summary_before
    assert harness.identity.get_account(harness.acc1) is not None
    assert harness.identity.get_account(harness.acc2) is not None
