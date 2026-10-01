"""改进工单 10 迁移 v65：持久节点产物/收据/事件的表、索引与账户生命周期。

断言新表真实存在、进入启动完整性清单，旧版本库升级后既有数据保留；
核对账户删除、导出与逻辑摘要登记（单一事实源），并做一次真实快照恢复
与账户隔离验证（写入只经内核仓库）。
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from bridges.kernel.contracts import (
    ArtifactTrust,
    NodeArtifact,
    NodeReceipt,
    NodeReceiptStatus,
    PendingNodeEvent,
    QualityVerdict,
)
from bridges.kernel.repository import NodeKernelRepository
from bridges.lifecycle.catalog import (
    ACCOUNT_TABLES,
    DELETION_ORDER,
    EXPORT_CATEGORIES,
    category_preview,
    delete_account_rows,
    export_rows,
    logical_summary,
)
from bridges.storage.database import (
    MIGRATIONS,
    REQUIRED_TABLES,
    SCHEMA_VERSION,
    BridgesDatabase,
)

KERNEL_TABLES = ("node_artifacts", "node_receipts", "node_outbox")

LEGACY_VERSION = 64

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _table_names(path: Path) -> set[str]:
    with sqlite3.connect(path) as connection:
        return {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }


def _index_names(path: Path) -> set[str]:
    with sqlite3.connect(path) as connection:
        return {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }


def _build_artifact(account_id: str, conversation_id: str, run_id: str) -> NodeArtifact:
    return NodeArtifact.build(
        account_id=account_id,
        conversation_id=conversation_id,
        run_id=run_id,
        task_id=None,
        task_version=None,
        recipe_id="campus-commute",
        recipe_version="campus-commute-recipe-v1",
        node="route.parse",
        artifact_type="commute.request_analysis",
        capability_version="commute-parse-v1",
        trust_state=ArtifactTrust.DRAFT,
        input_key="parse:key-1",
        input_deps=(),
        source_refs=(),
        read_scope="确定性解析",
        requirement_coverage=(),
        unconfirmed=(),
        error=None,
        payload={"analysis": {"raw_text": "从南区步行到图书馆"}},
        now=NOW,
    )


def _seed_kernel_rows(database: BridgesDatabase, account_id: str, suffix: str) -> None:
    conversation_id = f"conv-{suffix}"
    run_id = f"run-{suffix}"
    with database.transaction():
        database.connection.execute(
            "INSERT INTO conversations"
            "(conversation_id, account_id, title, mode, created_at, updated_at)"
            " VALUES (?, ?, '', 'companion', ?, ?)",
            (conversation_id, account_id, NOW.isoformat(), NOW.isoformat()),
        )
    repository = NodeKernelRepository(database)
    artifact = _build_artifact(account_id, conversation_id, run_id)
    with repository.transaction():
        repository.save_artifact(artifact)
        repository.save_receipt(
            NodeReceipt(
                receipt_id=f"rcp-{suffix}",
                account_id=account_id,
                conversation_id=conversation_id,
                run_id=run_id,
                node="route.parse",
                input_key=artifact.input_key,
                output_key="output-1",
                artifact_id=artifact.artifact_id,
                status=NodeReceiptStatus.COMPLETED,
                quality_verdict=QualityVerdict.PASS,
                attempt=1,
                lease_owner="worker-1",
                detail={"duration_ms": 5},
                committed_at=NOW,
            )
        )
        repository.enqueue_events(
            receipt_id=f"rcp-{suffix}",
            account_id=account_id,
            run_id=run_id,
            node="route.parse",
            events=(PendingNodeEvent(kind="node_completed", payload={"duration_ms": 5}),),
            now=NOW,
        )


def test_fresh_database_has_node_kernel_tables(tmp_path: Path) -> None:
    path = tmp_path / "bridges.db"
    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION
    assert SCHEMA_VERSION >= LEGACY_VERSION + 1
    names = _table_names(path)
    for table in KERNEL_TABLES:
        assert table in names
    indexes = _index_names(path)
    assert "idx_node_artifacts_identity" in indexes
    assert "idx_node_receipts_identity" in indexes
    assert "idx_node_outbox_pending" in indexes


def test_node_kernel_tables_are_required_at_startup() -> None:
    for table in KERNEL_TABLES:
        assert table in REQUIRED_TABLES


def test_upgrade_from_v64_preserves_data_and_adds_tables(tmp_path: Path) -> None:
    path = tmp_path / "bridges.db"
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        for version in range(1, LEGACY_VERSION + 1):
            for statement in MIGRATIONS[version]:
                connection.execute(statement)
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_meta"
            " (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_meta(key, value) VALUES ('version', ?)",
            (str(LEGACY_VERSION),),
        )
        connection.execute(
            "INSERT INTO conversations"
            "(conversation_id, account_id, title, mode, created_at, updated_at)"
            " VALUES ('legacy-conv', 'acc-legacy', '旧会话', 'companion',"
            " '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
        )
        connection.commit()
    finally:
        connection.close()

    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION
    names = _table_names(path)
    for table in KERNEL_TABLES:
        assert table in names
    with sqlite3.connect(path) as verify:
        row = verify.execute(
            "SELECT title FROM conversations WHERE conversation_id = 'legacy-conv'"
        ).fetchone()
        assert row is not None and row[0] == "旧会话"
        for table in KERNEL_TABLES:
            assert verify.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0] == 0


def test_node_kernel_tables_registered_for_deletion_and_export() -> None:
    """账户删除顺序与导出目录共用同一清单：新表随账户删除并进入导出。"""
    for table in KERNEL_TABLES:
        assert table in ACCOUNT_TABLES
        assert table in DELETION_ORDER
    category = next(item for item in EXPORT_CATEGORIES if item.key == "node_kernel")
    for table in KERNEL_TABLES:
        assert table in category.tables


def test_account_isolation_delete_export_and_snapshot_recovery(tmp_path: Path) -> None:
    path = tmp_path / "bridges.db"
    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION
    _seed_kernel_rows(database, "acc-1", "one")
    _seed_kernel_rows(database, "acc-2", "two")

    repository = NodeKernelRepository(database)
    assert repository.list_artifacts("acc-1", "conv-one")
    assert repository.list_artifacts("acc-2", "conv-one") == [], "跨账户不可见"
    assert repository.find_artifact("acc-2", "conv-one", "route.parse", "parse:key-1") is None

    preview = {
        category.key: count for category, count, _ in category_preview(database, "acc-1")
    }
    assert preview["node_kernel"] == 3
    exported = export_rows(database, "acc-1", "node_artifacts")
    assert len(exported) == 1 and exported[0]["account_id"] == "acc-1"
    summary = logical_summary(database, "acc-1")
    assert summary["node_artifacts"] == 1
    assert summary["node_receipts"] == 1
    assert summary["node_outbox"] == 1

    # 快照恢复：备份文件重开后节点产物/收据/事件仍在且账户摘要一致。
    backup_path = tmp_path / "bridges-backup.db"
    with database.snapshot_lock():
        database.snapshot_to(backup_path)
    restored = BridgesDatabase(backup_path)
    assert restored.initialize() == SCHEMA_VERSION
    restored_summary = logical_summary(restored, "acc-1")
    assert restored_summary["node_artifacts"] == 1
    assert restored_summary["node_receipts"] == 1
    assert restored_summary["node_outbox"] == 1

    # 账户删除：acc-1 的节点行全部清除，acc-2 不受影响。
    delete_account_rows(database, "acc-1")
    for table in KERNEL_TABLES:
        remaining = database.connection.execute(
            f"SELECT COUNT(*) AS total FROM {table} WHERE account_id = 'acc-1'"
        ).fetchone()
        assert int(remaining["total"]) == 0
        kept = database.connection.execute(
            f"SELECT COUNT(*) AS total FROM {table} WHERE account_id = 'acc-2'"
        ).fetchone()
        assert int(kept["total"]) == 1
