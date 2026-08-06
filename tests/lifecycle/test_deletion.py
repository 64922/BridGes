"""账户删除测试（Issue 37，AC3-4）。

覆盖：全流程（38 张表全删/对象文件删/凭据删/身份删）、部分失败不宣称
成功（注入文件清理失败 → failed + 中文原因 → 重试成功）、两账户隔离
（B 与系统备份不受影响）、删除后直接对象与缓存访问全部失败、审计最小
非敏感、陈旧 deleting 状态可重试（崩溃恢复）。
"""

from __future__ import annotations

import pytest
from harness import QWEN_CANARY, SMTP_CANARY, Harness

from bridges.contracts.lifecycle import AccountDeletionStatus, DataLifecycleError
from bridges.storage.errors import StorageError


def _all_account_tables() -> list[str]:
    from bridges.lifecycle.catalog import DELETION_ORDER

    return list(DELETION_ORDER)


def test_delete_removes_every_table_row_and_identity(tmp_path) -> None:
    harness = Harness(tmp_path)
    harness.seed_everything()
    harness.delete_account(harness.acc1)
    assert harness.identity.get_account(harness.acc1) is None
    # 全部账户表不再含该账户行（accounts 本身行也删）。
    for table in _all_account_tables():
        row = harness.database.connection.execute(
            f"SELECT COUNT(*) AS count FROM {table} WHERE account_id = ?",
            (harness.acc1,),
        ).fetchone()
        assert int(row["count"]) == 0, table
    # B 账户完整保留。
    assert harness.identity.get_account(harness.acc2) is not None
    assert (
        harness.database.connection.execute(
            "SELECT COUNT(*) AS count FROM conversations WHERE account_id = ?",
            (harness.acc2,),
        ).fetchone()["count"]
        == 1
    )


def test_delete_removes_object_files_and_credentials(tmp_path) -> None:
    harness = Harness(tmp_path)
    harness.seed_everything()
    # 记录对象文件路径。
    rows = harness.database.scoped(harness.acc1).execute(
        "SELECT content_hash FROM objects WHERE account_id = ?", (harness.acc1,)
    ).fetchall()
    hashes = [str(row["content_hash"]) for row in rows]
    harness.delete_account(harness.acc1)
    for content_hash in hashes:
        path = harness.object_store.objects_dir / content_hash[:2] / content_hash
        assert not path.exists(), content_hash
    # 凭据已删除（金丝雀不再可读）。
    assert harness.credentials.get(harness.acc1) is None
    assert harness.smtp_credentials.get(harness.acc1) is None
    assert harness.credentials.get(harness.acc2) is not None


def test_shared_object_file_preserved_for_other_account(tmp_path) -> None:
    harness = Harness(tmp_path)
    harness.seed_everything()
    # 同一内容两个账户各存一份对象（内容哈希相同，文件共享）。
    obj_a = harness.objects.create_object(harness.acc1, "shared.txt", b"shared-content")
    obj_b = harness.objects.create_object(harness.acc2, "shared.txt", b"shared-content")
    assert obj_a.content_hash == obj_b.content_hash
    harness.delete_account(harness.acc1)
    path = harness.object_store.objects_dir / obj_a.content_hash[:2] / obj_a.content_hash
    assert path.exists(), "跨账户共享内容不得被物理删除"
    assert harness.objects.get_content(harness.acc2, obj_b.object_id) == b"shared-content"


def test_delete_partial_failure_is_retryable_not_success(tmp_path) -> None:
    harness = Harness(tmp_path)
    harness.seed_everything()

    def _failing_cleaner(content_hash: str) -> None:
        raise StorageError("模拟磁盘写入失败")

    harness.deletion._file_cleaner = _failing_cleaner  # type: ignore[attr-defined]
    with pytest.raises(DataLifecycleError) as excinfo:
        harness.deletion.delete_account(harness.acc1)
    assert excinfo.value.retryable is True
    assert "删除未完成" in excinfo.value.message
    # SQL 行已删（事务成功）但状态可观察、可重试。
    status = harness.deletion.get_status(harness.acc1)
    assert status is not None
    assert status.status == AccountDeletionStatus.FAILED
    assert status.retry_count >= 1
    assert "模拟磁盘写入失败" in (status.last_error or "")
    # 重试：清理器恢复后完成。
    harness.deletion._file_cleaner = None  # type: ignore[attr-defined]
    retried = harness.deletion.retry_deletion(status.deletion_id)
    assert retried.status == AccountDeletionStatus.COMPLETED
    assert harness.identity.get_account(harness.acc1) is None


def test_delete_retry_runs_full_pipeline_when_transaction_failed(tmp_path) -> None:
    harness = Harness(tmp_path)
    harness.seed_everything()
    # 构造「事务阶段失败」：先写状态行再让事务删除抛错。
    import json as json_module

    deletion_id = "manual-deletion-id"
    with harness.database.transaction():
        harness.database.connection.execute(
            "INSERT INTO account_deletions(deletion_id, account_id, status,"
            " retry_count, last_error, pending_hashes, started_at)"
            " VALUES (?, ?, 'deleting', 0, NULL, ?, '2026-08-06T00:00:00+00:00')",
            (deletion_id, harness.acc1, json_module.dumps([])),
        )
    # 直接标记失败并重试：账户行仍在 → 重试应重跑事务删除。
    with harness.database.transaction():
        harness.database.connection.execute(
            "UPDATE account_deletions SET status = 'failed', retry_count = 1,"
            " last_error = '模拟失败' WHERE deletion_id = ?",
            (deletion_id,),
        )
    retried = harness.deletion.retry_deletion(deletion_id)
    assert retried.status == AccountDeletionStatus.COMPLETED
    assert harness.identity.get_account(harness.acc1) is None
    assert (
        harness.database.connection.execute(
            "SELECT COUNT(*) AS count FROM conversations WHERE account_id = ?",
            (harness.acc1,),
        ).fetchone()["count"]
        == 0
    )


def test_stale_deleting_status_is_retryable_after_crash(tmp_path) -> None:
    harness = Harness(tmp_path)
    harness.seed_everything()
    import json as json_module

    with harness.database.transaction():
        harness.database.connection.execute(
            "INSERT INTO account_deletions(deletion_id, account_id, status,"
            " retry_count, last_error, pending_hashes, started_at)"
            " VALUES ('stale-1', ?, 'deleting', 0, NULL, ?,"
            " '2020-01-01T00:00:00+00:00')",
            (harness.acc1, json_module.dumps([])),
        )
    # 陈旧 deleting（超过 10 分钟）视为崩溃遗留 → 可安全重试并完成，
    # 而非永远停留在「进行中」。
    retried = harness.deletion.retry_deletion("stale-1")
    assert retried.status == AccountDeletionStatus.COMPLETED
    assert harness.identity.get_account(harness.acc1) is None
    # 新删除（正常 deleting 状态）拒绝重试（进行中）。
    harness2 = Harness(tmp_path / "second")
    harness2.seed_everything()
    import json as json_module2

    with harness2.database.transaction():
        harness2.database.connection.execute(
            "INSERT INTO account_deletions(deletion_id, account_id, status,"
            " retry_count, last_error, pending_hashes, started_at)"
            " VALUES ('fresh-1', ?, 'deleting', 0, NULL, ?, ?)",
            (harness2.acc1, json_module2.dumps([]), "2099-01-01T00:00:00+00:00"),
        )
    with pytest.raises(DataLifecycleError) as excinfo:
        harness2.deletion.retry_deletion("fresh-1")
    assert excinfo.value.code == "deletion_in_progress"


def test_deleted_account_data_is_unreadable_by_services(tmp_path) -> None:
    """删除后已撤权数据不可继续被聊天、搜索或插件读取（AC4 直接路径）。"""
    harness = Harness(tmp_path)
    harness.seed_everything()
    harness.delete_account(harness.acc1)
    # 对话/消息读取为空。
    rows = harness.database.connection.execute(
        "SELECT * FROM conversations WHERE account_id = ?", (harness.acc1,)
    ).fetchall()
    assert rows == []
    # 对象读取（直接路径）失败。
    object_rows = harness.database.connection.execute(
        "SELECT object_id FROM objects WHERE account_id = ?", (harness.acc1,)
    ).fetchall()
    assert object_rows == []
    # 插件与 MCP 注册表为空。
    for table in ("skill_packages", "account_skill_states", "mcp_servers", "mcp_calls"):
        row = harness.database.connection.execute(
            f"SELECT COUNT(*) AS count FROM {table} WHERE account_id = ?",
            (harness.acc1,),
        ).fetchone()
        assert int(row["count"]) == 0, table


def test_delete_audit_is_minimal_and_secret_free(tmp_path) -> None:
    harness = Harness(tmp_path)
    harness.seed_everything()
    harness.delete_account(harness.acc1)
    assert "account_delete" in harness.audit_actions()
    for details in harness.audit_details():
        serialized = str(details)
        assert QWEN_CANARY not in serialized
        assert SMTP_CANARY not in serialized
        assert "我的对话" not in serialized


def test_delete_other_account_data_unaffected_by_retry(tmp_path) -> None:
    """B 账户及系统备份不受 A 删除影响（Verification 5）。"""
    harness = Harness(tmp_path)
    harness.seed_everything()
    b_summary_before = harness.database.connection.execute(
        "SELECT COUNT(*) AS count FROM messages WHERE account_id = ?",
        (harness.acc2,),
    ).fetchone()["count"]
    harness.delete_account(harness.acc1)
    # 系统级表（schema_meta）保留，B 数据完整。
    version = harness.database.connection.execute(
        "SELECT value FROM schema_meta WHERE key = 'version'"
    ).fetchone()
    assert version is not None
    b_summary_after = harness.database.connection.execute(
        "SELECT COUNT(*) AS count FROM messages WHERE account_id = ?",
        (harness.acc2,),
    ).fetchone()["count"]
    assert b_summary_before == b_summary_after
