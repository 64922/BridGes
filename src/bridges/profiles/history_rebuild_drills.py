"""画像工单 17：对账演练（账户清理 / 导出 / 恢复），全部在一次性副本上进行。

三个演练都复用 ``bridges.lifecycle.catalog`` 的登记表与真实实现（删除、
导出、备份恢复），只把计数、标识与摘要写进对账报告；失败如实计入
``errors``，绝不输出消息或条目正文。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from bridges.lifecycle.catalog import (
    ACCOUNT_TABLES,
    EXPORT_CATEGORIES,
    category_preview,
    delete_account_rows,
    export_rows,
    logical_summary,
)
from bridges.profiles.history_backup import (
    create_history_backup,
    verify_history_restore,
)
from bridges.profiles.history_contracts import HistoryPreviewManifest
from bridges.profiles.history_preview import preview_history
from bridges.profiles.history_rebuild_runtime import (
    RebuildWorkspace,
    open_working_database,
)
from bridges.profiles.replay import inspect_database
from bridges.storage.database import BridgesDatabase


def deletion_drill(
    workspace: RebuildWorkspace,
    account_id: str,
    drill_root: Path,
    errors: list[dict[str, object]],
) -> dict[str, object]:
    """在一次性副本上执行真实账户删除并核对全部账户表清空（含台账）。"""

    target_dir = drill_root / "deletion"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "bridges.db"
    if target.exists():
        target.unlink()
    shutil.copyfile(workspace.working_db_path, target)
    database = BridgesDatabase(target)
    residual: dict[str, int] = {}
    account_left = True
    try:
        database.initialize()
        delete_account_rows(database, account_id)
        for table in ACCOUNT_TABLES:
            row = database.connection.execute(
                f"SELECT COUNT(*) AS total FROM {table} WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            count = 0 if row is None else int(row["total"])
            if count:
                residual[table] = count
        account_row = database.connection.execute(
            "SELECT COUNT(*) AS total FROM accounts WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        account_left = account_row is not None and int(account_row["total"]) > 0
    finally:
        database.close()
    if residual or account_left:
        errors.append(
            {
                "check": "account_deletion",
                "detail": "账户删除演练后仍有残留数据",
                "residual": residual,
                "account_row_left": account_left,
            }
        )
    return {
        "executed": True,
        "tables_checked": len(ACCOUNT_TABLES),
        "residual": residual,
        "account_row_left": account_left,
    }


def export_section(
    database: BridgesDatabase,
    account_id: str,
    errors: list[dict[str, object]],
) -> dict[str, object]:
    """逐类别逐表执行导出读取，并核对台账审计类别已登记。"""

    preview = category_preview(database, account_id)
    exported_tables: dict[str, int] = {}
    failures: list[str] = []
    for category in EXPORT_CATEGORIES:
        for table in category.tables:
            try:
                rows = export_rows(database, account_id, table)
            except Exception as exc:  # noqa: BLE001 - 导出失败如实计入
                failures.append(f"{category.key}:{table}:{exc.__class__.__name__}")
                continue
            exported_tables[table] = len(rows)
    if failures:
        errors.append(
            {
                "check": "export_rows",
                "detail": "存在无法导出的类别/表",
                "failures": failures[:20],
            }
        )
    ledger_category = next(
        (
            (category, total, estimated)
            for category, total, estimated in preview
            if category.key == "profile_history_rebuild"
        ),
        None,
    )
    if ledger_category is None:
        errors.append(
            {
                "check": "export_ledger_registered",
                "detail": "导出类别缺少画像历史重整审计",
            }
        )
    return {
        "categories": len(EXPORT_CATEGORIES),
        "tables": len(exported_tables),
        "ledger_rows": {
            "runs": exported_tables.get("profile_rebuild_runs"),
            "items": exported_tables.get("profile_rebuild_items"),
            "preview_total": None if ledger_category is None else ledger_category[1],
        },
        "failures": failures[:20],
    }


def restore_drill(
    workspace: RebuildWorkspace,
    account_id: str,
    drill_root: Path,
    errors: list[dict[str, object]],
) -> dict[str, object]:
    """重新预览工作副本 → 创建并核对新备份 → 二次恢复开演 → 逻辑摘要比对。"""

    target_dir = drill_root / "restore"
    target_dir.mkdir(parents=True, exist_ok=True)
    identity = inspect_database(workspace.working_db_path)
    manifest: HistoryPreviewManifest = preview_history(identity, account_id=account_id)
    backup_path = target_dir / "post-rebuild.db"
    for suffix in ("", ".manifest.json", ".history-record.json"):
        candidate = Path(f"{backup_path}{suffix}")
        if candidate.exists():
            candidate.unlink()
    record = create_history_backup(
        identity, backup_path, preview=manifest, work_dir=target_dir
    )
    restore = verify_history_restore(backup_path, preview=manifest, work_dir=target_dir)
    if not record.verified:
        errors.append(
            {
                "check": "post_rebuild_backup",
                "detail": "重整后的备份未通过创建时隔离恢复核对",
            }
        )

    # 逻辑摘要比对：在工作副本与恢复副本上重算并逐表一致。
    source = open_working_database(workspace)
    try:
        expected_summary = logical_summary(source, account_id)
    finally:
        source.close()
    restored_copy = target_dir / "restored-summary.db"
    shutil.copyfile(backup_path, restored_copy)
    restored = BridgesDatabase(restored_copy)
    try:
        restored.initialize()
        restored_summary = logical_summary(restored, account_id)
    finally:
        restored.close()
    mismatched = {
        key: {"expected": expected_summary.get(key), "restored": restored_summary.get(key)}
        for key in sorted(set(expected_summary) | set(restored_summary))
        if expected_summary.get(key) != restored_summary.get(key)
    }
    if mismatched:
        errors.append(
            {
                "check": "restore_logical_summary",
                "detail": "恢复副本与工作副本的逻辑摘要不一致",
                "mismatches": mismatched,
            }
        )
    return {
        "preview_state_digest": manifest.state_digest,
        "backup_verified": record.verified,
        "restore_verified": restore.verified,
        "restore_reconciliation_passed": bool(
            restore.restore.get("reconciliation_passed")
        ),
        "logical_summary_tables": len(expected_summary),
        "logical_summary_mismatches": mismatched,
    }


__all__ = [
    "deletion_drill",
    "export_section",
    "restore_drill",
]
