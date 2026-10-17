"""画像工单 19：以已验证备份为底的切换恢复与重升级。

恢复流程固定为：

1. 校验备份身份、文件摘要和 schema；
2. 复制备份到新目标库，绝不覆盖当前库；
3. 把当前库中切换后新增的原子事实、人工修订、删除墓碑、控制、反馈与
   迁移台账合并到目标库；
4. 清空概览、事项上下文与召回索引等可重建视图，并以备份为界移除切换前
   仍在活动的抽取运行/任务，防止旧队列在新库上继续执行；
5. 逐账户核对后才返回报告；任一项失败删除目标库并抛稳定错误码。

恢复目标是整库副本，因此即使调用方只关心一个账户，也会合并当前库的全部
账户；账户集合在切换窗口内必须与备份一致，窗口内新建或删除账户会被拒绝。
本模块不合并聊天、文件、学习记录等领域数据，这些数据仍由整实例备份负责。
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from typing import Any

from bridges.profiles.cutover import require_current_database
from bridges.profiles.cutover_contracts import (
    CutoverCheck,
    ProfileCutoverRecovery,
    ProfileCutoverRecoverySet,
)
from bridges.profiles.cutover_extraction import (
    active_extraction_ids,
    all_extraction_ids,
    drop_stale_extraction_rows,
)
from bridges.profiles.replay import (
    ProfileReplayError,
    VerifiedBackup,
    _check_integrity,
    _load_verified_backup,
    _readonly_connection,
)
from bridges.storage.database import BridgesDatabase

#: 当前库优先、同时保留备份独有行的账户域表。条目表另做身份冲突处理。
_UNION_TABLES = (
    "profile_item_feedback",
    "profile_item_migrations",
    "profile_item_migration_records",
    "profile_privacy_disclosures",
    "profile_extraction_tombstones",
    "profile_extraction_runs",
    "profile_extraction_tasks",
    "profile_rebuild_runs",
    "profile_rebuild_items",
)

#: 当前库完全权威的账户域表：备份中的同账户行必须先删除。
_EXACT_ACCOUNT_TABLES = (
    "profile_account_controls",
    "profile_extraction_privacy_blocks",
)

#: 可重建派生视图；恢复后必须清空，避免旧代数覆盖新事实。
_DERIVED_TABLES = (
    "profile_overview",
    "profile_ongoing_context",
    "profile_recall_index_versions",
    "profile_recall_index_entries",
)


def _account_ids(connection: sqlite3.Connection) -> set[str]:
    return {str(row[0]) for row in connection.execute("SELECT account_id FROM accounts").fetchall()}


def _columns(connection: sqlite3.Connection, table: str) -> tuple[str, ...]:
    rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return tuple(str(row[1]) for row in rows)


def _rows(
    connection: sqlite3.Connection,
    table: str,
    account_id: str,
    *,
    columns: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    selected = columns or _columns(connection, table)
    quote = ", ".join(f'"{column}"' for column in selected)
    rows = connection.execute(
        f"SELECT {quote} FROM {table} WHERE account_id = ?", (account_id,)
    ).fetchall()
    return [dict(zip(selected, tuple(row), strict=True)) for row in rows]


def _upsert_rows(
    target: sqlite3.Connection,
    table: str,
    columns: tuple[str, ...],
    rows: list[dict[str, Any]],
) -> None:
    if not rows:
        return
    quote = ", ".join(f'"{column}"' for column in columns)
    placeholders = ", ".join("?" for _ in columns)
    target.executemany(
        f"INSERT OR REPLACE INTO {table} ({quote}) VALUES ({placeholders})",
        [tuple(row[column] for column in columns) for row in rows],
    )


def _merge_profile_items(
    target: sqlite3.Connection,
    current: sqlite3.Connection,
    account_id: str,
) -> None:
    columns = _columns(target, "profile_items")
    rows = _rows(current, "profile_items", account_id, columns=columns)
    for row in rows:
        # 身份键唯一：当前库同身份行的标识可能已因迁移/重建变化，先删除
        # 备份里的同身份行，确保当前状态而不是旧备份决定活动事实。
        target.execute(
            "DELETE FROM profile_items WHERE account_id = ? AND identity_key = ?",
            (account_id, row["identity_key"]),
        )
    _upsert_rows(target, "profile_items", columns, rows)


def _merge_state(
    target: sqlite3.Connection,
    current: sqlite3.Connection,
    backup: sqlite3.Connection,
    account_id: str,
) -> None:
    _merge_profile_items(target, current, account_id)

    for table in _UNION_TABLES:
        columns = _columns(target, table)
        _upsert_rows(
            target,
            table,
            columns,
            _rows(current, table, account_id, columns=columns),
        )

    for table in _EXACT_ACCOUNT_TABLES:
        target.execute(f"DELETE FROM {table} WHERE account_id = ?", (account_id,))
        columns = _columns(target, table)
        _upsert_rows(
            target,
            table,
            columns,
            _rows(current, table, account_id, columns=columns),
        )

    for table in _DERIVED_TABLES:
        target.execute(f"DELETE FROM {table} WHERE account_id = ?", (account_id,))

    drop_stale_extraction_rows(target, backup, account_id)


def _row_map(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    return {str(row[key]): row for row in rows}


def _check(check_id: str, passed: bool, detail: str) -> CutoverCheck:
    return CutoverCheck(check_id=check_id, passed=passed, detail=detail)


def _verify_recovery(
    target: sqlite3.Connection,
    current: sqlite3.Connection,
    backup: sqlite3.Connection,
    *,
    account_id: str,
    backup_only_items: set[str],
    added_since_backup: set[str],
    changed_since_backup: set[str],
) -> tuple[tuple[CutoverCheck, ...], dict[str, int]]:
    columns = _columns(target, "profile_items")
    current_items = _row_map(
        _rows(current, "profile_items", account_id, columns=columns),
        "profile_item_id",
    )
    target_items = _row_map(
        _rows(target, "profile_items", account_id, columns=columns),
        "profile_item_id",
    )
    missing = sorted(set(current_items) - set(target_items))
    mismatched = sorted(
        item_id
        for item_id in set(current_items) & set(target_items)
        if current_items[item_id] != target_items[item_id]
    )
    backup_only_missing = sorted(backup_only_items - set(target_items))

    current_controls = _rows(current, "profile_account_controls", account_id)
    target_controls = _rows(target, "profile_account_controls", account_id)
    current_blocks = _rows(current, "profile_extraction_privacy_blocks", account_id)
    target_blocks = _rows(target, "profile_extraction_privacy_blocks", account_id)
    current_tombstones = {
        (row["account_id"], row["message_id"])
        for row in _rows(current, "profile_extraction_tombstones", account_id)
    }
    target_tombstones = {
        (row["account_id"], row["message_id"])
        for row in _rows(target, "profile_extraction_tombstones", account_id)
    }
    current_runs = {row["run_id"] for row in _rows(current, "profile_item_migrations", account_id)}
    target_runs = {row["run_id"] for row in _rows(target, "profile_item_migrations", account_id)}

    backup_run_ids, backup_task_ids = all_extraction_ids(backup, account_id)
    current_active_runs, current_active_tasks = active_extraction_ids(current, account_id)
    target_active_runs, target_active_tasks = active_extraction_ids(target, account_id)
    post_cutover_active_runs = current_active_runs - backup_run_ids
    post_cutover_active_tasks = current_active_tasks - backup_task_ids
    stale_extraction = (target_active_runs & backup_run_ids) | (
        target_active_tasks & backup_task_ids
    )
    missing_extraction = (post_cutover_active_runs - target_active_runs) | (
        post_cutover_active_tasks - target_active_tasks
    )

    derived_counts = {
        table: int(
            target.execute(
                f"SELECT COUNT(*) FROM {table} WHERE account_id = ?",
                (account_id,),
            ).fetchone()[0]
        )
        for table in _DERIVED_TABLES
    }

    checks = (
        _check(
            "current_items_preserved",
            not missing and not mismatched,
            f"缺失 {len(missing)} 条，内容不一致 {len(mismatched)} 条",
        ),
        _check(
            "backup_only_items_retained",
            not backup_only_missing,
            f"备份独有条目缺失 {len(backup_only_missing)} 条",
        ),
        _check(
            "controls_match_current",
            current_controls == target_controls,
            "当前账户的画像使用控制已覆盖备份状态",
        ),
        _check(
            "privacy_blocks_match_current",
            current_blocks == target_blocks,
            "当前账户的停止记录屏障已覆盖备份状态",
        ),
        _check(
            "tombstones_preserved",
            current_tombstones <= target_tombstones,
            f"当前墓碑 {len(current_tombstones)} 条，目标中包含 "
            f"{len(current_tombstones & target_tombstones)} 条",
        ),
        _check(
            "migration_ledger_preserved",
            current_runs <= target_runs,
            f"当前迁移批次 {len(current_runs)} 个，目标中包含 {len(current_runs & target_runs)} 个",
        ),
        _check(
            "extraction_state_matches_current",
            not stale_extraction and not missing_extraction,
            f"旧活动行残留 {len(stale_extraction)} 条，当前活动行缺失 {len(missing_extraction)} 条",
        ),
        _check(
            "derived_views_invalidated",
            all(count == 0 for count in derived_counts.values()),
            "概览、事项上下文与召回索引已清空，等待当前代码重建",
        ),
    )
    counts = {
        "items_added_since_backup": len(added_since_backup),
        "changed_items_replaced": len(changed_since_backup),
        "backup_only_items_retained": len(set(backup_only_items) & set(target_items)),
        "target_items": len(target_items),
        "stale_extraction_rows_remaining": len(stale_extraction),
        "missing_current_extraction_rows": len(missing_extraction),
        "derived_rows_after_recovery": sum(derived_counts.values()),
    }
    return checks, counts


def _remove_database_files(path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)


def _validate_paths(
    backup: VerifiedBackup | str | Path,
    current_path: str | Path,
    target_path: str | Path,
) -> tuple[VerifiedBackup, Path, Path]:
    verified = _load_verified_backup(backup)
    current = Path(current_path).resolve()
    target = Path(target_path).resolve()
    if not current.exists():
        raise ProfileReplayError("current_database_missing", f"当前数据库不存在：{current}")
    if target.exists():
        raise ProfileReplayError("target_exists", "恢复目标已存在；不会覆盖已有数据库。")
    if target == current or target == verified.path:
        raise ProfileReplayError("target_conflict", "恢复目标不能是当前库或备份文件本身。")
    if not target.parent.exists():
        raise ProfileReplayError("target_parent_missing", "恢复目标目录不存在；请先显式创建。")
    return verified, current, target


def _run_recovery(
    *,
    backup: VerifiedBackup | str | Path,
    current_path: str | Path,
    target_path: str | Path,
) -> tuple[ProfileCutoverRecovery, ...]:
    verified, current, target = _validate_paths(backup, current_path, target_path)
    current_connection = _readonly_connection(current)
    backup_connection = _readonly_connection(verified.path)
    target_database: BridgesDatabase | None = None
    created_target = False
    try:
        _check_integrity(current_connection)
        current_accounts = _account_ids(current_connection)
        if not current_accounts:
            raise ProfileReplayError("account_missing", "当前数据库没有可恢复的账户。")
        for account_id in sorted(current_accounts):
            require_current_database(current_connection, account_id=account_id)
        backup_accounts = _account_ids(backup_connection)
        if current_accounts != backup_accounts:
            raise ProfileReplayError(
                "account_set_changed",
                "切换窗口内账户集合发生变化；请恢复该账户或重新制作备份。",
            )

        pending: list[tuple[str, set[str], set[str], set[str]]] = []
        for account_id in sorted(current_accounts):
            backup_items = _row_map(
                _rows(backup_connection, "profile_items", account_id),
                "profile_item_id",
            )
            current_items = _row_map(
                _rows(current_connection, "profile_items", account_id),
                "profile_item_id",
            )
            pending.append(
                (
                    account_id,
                    set(current_items) - set(backup_items),
                    {
                        item_id
                        for item_id in set(backup_items) & set(current_items)
                        if backup_items[item_id] != current_items[item_id]
                    },
                    set(backup_items) - set(current_items),
                )
            )

        shutil.copy2(verified.path, target)
        created_target = True
        target_database = BridgesDatabase(target)
        target_database.initialize()
        with target_database.transaction():
            for account_id, *_ in pending:
                _merge_state(
                    target_database.connection,
                    current_connection,
                    backup_connection,
                    account_id,
                )

        reports: list[ProfileCutoverRecovery] = []
        for account_id, added, changed, backup_only in pending:
            checks, counts = _verify_recovery(
                target_database.connection,
                current_connection,
                backup_connection,
                account_id=account_id,
                backup_only_items=backup_only,
                added_since_backup=added,
                changed_since_backup=changed,
            )
            reports.append(
                ProfileCutoverRecovery(
                    account_id=account_id,
                    backup_id=verified.backup_id,
                    backup_digest=verified.backup_digest,
                    source_digest=verified.source_digest,
                    schema_version=verified.schema_version,
                    checks=checks,
                    counts=counts,
                )
            )
        if not all(report.passed for report in reports):
            raise ProfileReplayError(
                "cutover_recovery_unverified",
                "恢复库未通过核对，已删除未验证目标库。",
            )
        return tuple(reports)
    except Exception:
        if created_target:
            if target_database is not None:
                target_database.close()
                target_database = None
            _remove_database_files(target)
        raise
    finally:
        if target_database is not None:
            target_database.close()
        backup_connection.close()
        current_connection.close()


def recover_cutover_state(
    *,
    backup: VerifiedBackup | str | Path,
    current_path: str | Path,
    target_path: str | Path,
    account_id: str,
) -> ProfileCutoverRecovery:
    """恢复整库副本，并返回指定账户的核对报告。

    实际合并覆盖当前库全部账户；``account_id`` 只决定返回哪一份报告。这样
    单账户操作不会把其他账户切回旧状态。
    """

    current = Path(current_path).resolve()
    if current.exists():
        connection = _readonly_connection(current)
        try:
            require_current_database(connection, account_id=account_id)
        finally:
            connection.close()
    reports = _run_recovery(
        backup=backup,
        current_path=current_path,
        target_path=target_path,
    )
    for report in reports:
        if report.account_id == account_id:
            return report
    raise ProfileReplayError("account_missing", f"恢复目标中不存在账户：{account_id}")


def recover_all_cutover_state(
    *,
    backup: VerifiedBackup | str | Path,
    current_path: str | Path,
    target_path: str | Path,
) -> ProfileCutoverRecoverySet:
    """恢复整库副本并返回全部账户的核对报告。"""

    reports = _run_recovery(
        backup=backup,
        current_path=current_path,
        target_path=target_path,
    )
    return ProfileCutoverRecoverySet(
        account_ids=tuple(report.account_id for report in reports),
        recoveries=reports,
    )


__all__ = [
    "recover_all_cutover_state",
    "recover_cutover_state",
]
