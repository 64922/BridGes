"""画像工单 19：四维旧路径退出审计。

:func:`audit_profile_retirement` 只读检查一个账户的切换前状态，确认
schema 当前、原子条目身份完整、删除墓碑不保留正文，并报告旧四维存量、
原子条目和待重试队列数量。恢复与回滚属于
:mod:`bridges.profiles.cutover_recovery`。

本模块不执行真实用户库切换、不停止运行实例；审计通过也不等于生产部署完成。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from bridges.profiles.cutover_contracts import (
    CutoverCheck,
    ProfileCutoverAudit,
)
from bridges.profiles.replay import (
    ProfileReplayError,
    _check_integrity,
    _readonly_connection,
    _schema_version,
)
from bridges.storage.database import SCHEMA_VERSION


def _account_exists(connection: sqlite3.Connection, account_id: str) -> bool:
    return (
        connection.execute("SELECT 1 FROM accounts WHERE account_id = ?", (account_id,)).fetchone()
        is not None
    )


def require_current_database(connection: sqlite3.Connection, *, account_id: str) -> int:
    """校验 schema 与账户存在；失败时抛稳定错误码。"""

    version = _schema_version(connection)
    if version != SCHEMA_VERSION:
        raise ProfileReplayError(
            "schema_version_mismatch",
            f"数据库 schema 为 {version}，当前程序需要 {SCHEMA_VERSION}。",
        )
    if not _account_exists(connection, account_id):
        raise ProfileReplayError("account_missing", f"数据库中不存在账户：{account_id}")
    return version


def _count(connection: sqlite3.Connection, sql: str, parameters: tuple[Any, ...]) -> int:
    row = connection.execute(sql, parameters).fetchone()
    return int(row[0]) if row is not None else 0


def _audit_counts(connection: sqlite3.Connection, account_id: str) -> dict[str, int]:
    return {
        "legacy_records": _count(
            connection,
            "SELECT COUNT(*) FROM profile_four_dimension_records WHERE account_id = ?",
            (account_id,),
        ),
        "legacy_active_records": _count(
            connection,
            "SELECT COUNT(*) FROM profile_four_dimension_records "
            "WHERE account_id = ? AND status = 'active'",
            (account_id,),
        ),
        "atomic_items": _count(
            connection,
            "SELECT COUNT(*) FROM profile_items WHERE account_id = ?",
            (account_id,),
        ),
        "atomic_active_items": _count(
            connection,
            "SELECT COUNT(*) FROM profile_items WHERE account_id = ? AND status = 'active'",
            (account_id,),
        ),
        "atomic_tombstones": _count(
            connection,
            "SELECT COUNT(*) FROM profile_items WHERE account_id = ? AND status = 'withdrawn'",
            (account_id,),
        ),
        "withdrawn_with_body": _count(
            connection,
            "SELECT COUNT(*) FROM profile_items "
            "WHERE account_id = ? AND status = 'withdrawn' AND text <> ''",
            (account_id,),
        ),
        "items_without_identity": _count(
            connection,
            "SELECT COUNT(*) FROM profile_items "
            "WHERE account_id = ? AND (identity_key IS NULL OR identity_key = '')",
            (account_id,),
        ),
        "pending_retry_tasks": _count(
            connection,
            "SELECT COUNT(*) FROM profile_extraction_tasks "
            "WHERE account_id = ? AND status IN ('pending', 'running')",
            (account_id,),
        ),
        "migration_runs": _count(
            connection,
            "SELECT COUNT(*) FROM profile_item_migrations WHERE account_id = ?",
            (account_id,),
        ),
    }


def audit_profile_retirement(database_path: str | Path, *, account_id: str) -> ProfileCutoverAudit:
    """只读审计一个账户是否满足四维路径退出后的数据前提。"""

    path = Path(database_path).resolve()
    if not path.exists():
        raise ProfileReplayError("database_missing", f"数据库不存在：{path}")
    connection = _readonly_connection(path)
    try:
        _check_integrity(connection)
        version = require_current_database(connection, account_id=account_id)
        counts = _audit_counts(connection, account_id)
        checks = (
            CutoverCheck("sqlite_integrity", True, "PRAGMA integrity_check=ok"),
            CutoverCheck("schema_current", True, f"schema={version}"),
            CutoverCheck("account_present", True, "账户存在"),
            CutoverCheck(
                "withdrawn_items_have_no_body",
                counts["withdrawn_with_body"] == 0,
                f"墓碑中保留正文的条目数：{counts['withdrawn_with_body']}",
            ),
            CutoverCheck(
                "atomic_identity_complete",
                counts["items_without_identity"] == 0,
                f"缺少身份键的条目数：{counts['items_without_identity']}",
            ),
        )
        return ProfileCutoverAudit(
            account_id=account_id,
            schema_version=version,
            checks=checks,
            counts=counts,
        )
    finally:
        connection.close()


__all__ = ["audit_profile_retirement", "require_current_database"]
