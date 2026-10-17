"""画像工单 19：切换恢复时的旧抽取队列边界。

以切换前备份为界，备份中已经存在且仍活动的抽取运行/任务属于旧代队列，
恢复后不得继续执行；切换后新增的活动行则必须保留。外部旧队列消息若仍被
领取，会因任务行不存在而直接完成，不再写入新恢复库。
"""

from __future__ import annotations

import sqlite3

_ACTIVE_STATES = ("pending", "running")

#: 画像域队列名；``task_claims`` 是系统级表，不含 account_id。
PROFILE_QUEUE_NAMES = (
    "profile-extraction",
    "profile-replay-v2",
    "profile-history-rebuild",
)


def active_extraction_ids(
    connection: sqlite3.Connection,
    account_id: str,
) -> tuple[set[str], set[str]]:
    """返回账户下仍在活动的抽取运行与任务标识。"""

    placeholders = ", ".join("?" for _ in _ACTIVE_STATES)
    run_rows = connection.execute(
        f"SELECT extraction_id FROM profile_extraction_runs "
        f"WHERE account_id = ? AND status IN ({placeholders})",
        (account_id, *_ACTIVE_STATES),
    ).fetchall()
    task_rows = connection.execute(
        f"SELECT task_id FROM profile_extraction_tasks "
        f"WHERE account_id = ? AND status IN ({placeholders})",
        (account_id, *_ACTIVE_STATES),
    ).fetchall()
    return (
        {str(row[0]) for row in run_rows},
        {str(row[0]) for row in task_rows},
    )


def all_extraction_ids(
    connection: sqlite3.Connection,
    account_id: str,
) -> tuple[set[str], set[str]]:
    """返回账户下备份快照中存在的抽取运行与任务标识。"""

    run_rows = connection.execute(
        "SELECT extraction_id FROM profile_extraction_runs WHERE account_id = ?",
        (account_id,),
    ).fetchall()
    task_rows = connection.execute(
        "SELECT task_id FROM profile_extraction_tasks WHERE account_id = ?",
        (account_id,),
    ).fetchall()
    return {str(row[0]) for row in run_rows}, {str(row[0]) for row in task_rows}


def drop_stale_extraction_rows(
    target: sqlite3.Connection,
    backup: sqlite3.Connection,
    account_id: str,
) -> None:
    """删除备份中已存在且仍活动的抽取运行/任务。

    切换后的当前库以备份为底，因此只看“当前库独有”无法识别旧任务；必须
    以切换快照为界。切换后新增的活动运行/任务不在备份中，保持不变。
    """

    backup_runs, backup_tasks = all_extraction_ids(backup, account_id)
    target_runs, target_tasks = active_extraction_ids(target, account_id)
    for extraction_id in sorted(target_runs & backup_runs):
        target.execute(
            "DELETE FROM profile_extraction_runs WHERE account_id = ? AND extraction_id = ?",
            (account_id, extraction_id),
        )
    for task_id in sorted(target_tasks & backup_tasks):
        target.execute(
            "DELETE FROM profile_extraction_tasks WHERE account_id = ? AND task_id = ?",
            (account_id, task_id),
        )


def profile_claim_task_keys(connection: sqlite3.Connection) -> set[str]:
    """返回画像域队列中仍保留的领取任务键。"""

    placeholders = ", ".join("?" for _ in PROFILE_QUEUE_NAMES)
    rows = connection.execute(
        f"SELECT task_key FROM task_claims WHERE queue_name IN ({placeholders})",
        PROFILE_QUEUE_NAMES,
    ).fetchall()
    return {str(row[0]) for row in rows}


def replace_profile_task_claims(
    target: sqlite3.Connection,
    current: sqlite3.Connection,
    expected_task_ids: set[str],
) -> None:
    """用切换后仍活动的画像任务重建画像队列领取行。

    旧队列的领取/租约行不能随备份带入恢复库；当前库中属于切换前任务的
    领取行也已由 :func:`drop_stale_extraction_rows` 对应的任务边界排除。
    """

    placeholders = ", ".join("?" for _ in PROFILE_QUEUE_NAMES)
    target.execute(
        f"DELETE FROM task_claims WHERE queue_name IN ({placeholders})",
        PROFILE_QUEUE_NAMES,
    )
    if not expected_task_ids:
        return
    columns = tuple(
        str(row[1]) for row in target.execute("PRAGMA table_info(task_claims)").fetchall()
    )
    quote = ", ".join(f'"{column}"' for column in columns)
    rows = current.execute(
        f"SELECT {quote} FROM task_claims WHERE queue_name IN ({placeholders})",
        PROFILE_QUEUE_NAMES,
    ).fetchall()
    selected = [
        tuple(row) for row in rows if str(row[columns.index("task_key")]) in expected_task_ids
    ]
    if not selected:
        return
    row_placeholders = ", ".join("?" for _ in columns)
    target.executemany(
        f"INSERT OR REPLACE INTO task_claims ({quote}) VALUES ({row_placeholders})",
        selected,
    )


__all__ = [
    "PROFILE_QUEUE_NAMES",
    "active_extraction_ids",
    "all_extraction_ids",
    "drop_stale_extraction_rows",
    "profile_claim_task_keys",
    "replace_profile_task_claims",
]
