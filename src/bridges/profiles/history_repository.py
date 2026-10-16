"""画像工单 16：历史重整预览的只读读取边界。

本模块只做只读查询与记录视图：不复用会初始化或写入的原子仓库，也不把
条目正文、消息正文写进输出。账户级分类与摘要计算在
:mod:`bridges.profiles.history_snapshot`；预览清单与备份恢复演练共用同一份
计算，保证两侧核对的是同一合同。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass


@dataclass(frozen=True)
class HistoryItemRecord:
    """一条原子条目的只读视图；``text`` 只在内存中用于确定性分类。"""

    profile_item_id: str
    status: str
    write_origin: str
    user_edited_at: str | None
    version: int
    created_at: str
    updated_at: str
    identity_key: str
    fact_key: str
    usage_purpose: str | None
    source_record_id: str | None
    migration_run_id: str | None
    text: str
    source_message_ids: tuple[str, ...]
    source_quotes: dict[str, str]


@dataclass(frozen=True)
class HistoryMessageRecord:
    """一条消息的只读视图；正文只在内存中参与摘要与门控判断。"""

    message_id: str
    conversation_id: str
    role: str
    status: str
    created_at: str
    content: str


@dataclass(frozen=True)
class HistoryControlsRecord:
    """账户控制开关与删除屏障的只读视图。"""

    recording_enabled: bool
    usage_enabled: bool
    usage_control_version: int
    content_blocks: tuple[str, ...]
    tombstoned_message_ids: tuple[str, ...]


@dataclass(frozen=True)
class HistoryFeedbackRecord:
    """一条画像反馈的领域引用（只作对账，不含正文）。"""

    feedback_id: str
    profile_item_id: str
    kind: str
    item_version: int
    created_at: str


def _json_str_tuple(raw: object) -> tuple[str, ...]:
    """解析 JSON 标识列表；损坏数据按空处理（证据不足方向，绝不伪造）。"""

    if not isinstance(raw, str) or not raw.strip():
        return ()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return ()
    if not isinstance(parsed, list):
        return ()
    return tuple(str(value) for value in parsed)


def _json_str_map(raw: object) -> dict[str, str]:
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(key): str(value) for key, value in parsed.items()}


class ProfileHistorySnapshotReader:
    """在给定连接上读取一个账户的只读历史快照。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def account_exists(self, account_id: str) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM accounts WHERE account_id = ?", (account_id,)
        ).fetchone()
        return row is not None

    def list_items(self, account_id: str) -> list[HistoryItemRecord]:
        rows = self._connection.execute(
            "SELECT profile_item_id, status, write_origin, user_edited_at, version, "
            "created_at, updated_at, identity_key, fact_key, usage_purpose, "
            "source_record_id, migration_run_id, text, source_message_ids_json, "
            "source_quotes_json "
            "FROM profile_items WHERE account_id = ? "
            "ORDER BY profile_item_id",
            (account_id,),
        ).fetchall()
        return [
            HistoryItemRecord(
                profile_item_id=str(row["profile_item_id"]),
                status=str(row["status"]),
                write_origin=str(row["write_origin"]),
                user_edited_at=(
                    str(row["user_edited_at"])
                    if row["user_edited_at"] is not None
                    else None
                ),
                version=int(row["version"]),
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
                identity_key=str(row["identity_key"]),
                fact_key=str(row["fact_key"]),
                usage_purpose=(
                    str(row["usage_purpose"])
                    if row["usage_purpose"] is not None
                    else None
                ),
                source_record_id=(
                    str(row["source_record_id"])
                    if row["source_record_id"] is not None
                    else None
                ),
                migration_run_id=(
                    str(row["migration_run_id"])
                    if row["migration_run_id"] is not None
                    else None
                ),
                text=str(row["text"]),
                source_message_ids=_json_str_tuple(row["source_message_ids_json"]),
                source_quotes=_json_str_map(row["source_quotes_json"]),
            )
            for row in rows
        ]

    def iter_messages(self, account_id: str) -> Iterator[HistoryMessageRecord]:
        cursor = self._connection.execute(
            "SELECT message_id, conversation_id, role, status, created_at, content "
            "FROM messages WHERE account_id = ? ORDER BY message_id",
            (account_id,),
        )
        for row in cursor:
            yield HistoryMessageRecord(
                message_id=str(row["message_id"]),
                conversation_id=str(row["conversation_id"]),
                role=str(row["role"]),
                status=str(row["status"]),
                created_at=str(row["created_at"]),
                content=str(row["content"]),
            )

    def read_controls(self, account_id: str) -> HistoryControlsRecord:
        recording_row = self._connection.execute(
            "SELECT 1 FROM profile_extraction_privacy_blocks "
            "WHERE account_id = ? AND scope = 'account' LIMIT 1",
            (account_id,),
        ).fetchone()
        block_rows = self._connection.execute(
            "SELECT normalized_value FROM profile_extraction_privacy_blocks "
            "WHERE account_id = ? AND scope = 'content' "
            "ORDER BY normalized_value",
            (account_id,),
        ).fetchall()
        tombstone_rows = self._connection.execute(
            "SELECT message_id FROM profile_extraction_tombstones "
            "WHERE account_id = ? ORDER BY message_id",
            (account_id,),
        ).fetchall()
        usage_row = self._connection.execute(
            "SELECT profile_usage_enabled, usage_control_version "
            "FROM profile_account_controls WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        return HistoryControlsRecord(
            recording_enabled=recording_row is None,
            usage_enabled=(
                True if usage_row is None else bool(usage_row["profile_usage_enabled"])
            ),
            usage_control_version=(
                0 if usage_row is None else int(usage_row["usage_control_version"])
            ),
            content_blocks=tuple(str(row["normalized_value"]) for row in block_rows),
            tombstoned_message_ids=tuple(
                str(row["message_id"]) for row in tombstone_rows
            ),
        )

    def list_feedback(self, account_id: str) -> list[HistoryFeedbackRecord]:
        rows = self._connection.execute(
            "SELECT feedback_id, profile_item_id, kind, item_version, created_at "
            "FROM profile_item_feedback WHERE account_id = ? "
            "ORDER BY feedback_id",
            (account_id,),
        ).fetchall()
        return [
            HistoryFeedbackRecord(
                feedback_id=str(row["feedback_id"]),
                profile_item_id=str(row["profile_item_id"]),
                kind=str(row["kind"]),
                item_version=int(row["item_version"]),
                created_at=str(row["created_at"]),
            )
            for row in rows
        ]

    def count_migration_reports(self, account_id: str) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) AS total FROM profile_item_migrations "
            "WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        return 0 if row is None else int(row["total"])

    def count_unresolved_source_record_refs(
        self, account_id: str, items: list[HistoryItemRecord]
    ) -> int:
        """统计指向已不存在的旧四维记录的条目数（领域引用对账）。"""

        refs = sorted(
            {item.source_record_id for item in items if item.source_record_id}
        )
        if not refs:
            return 0
        placeholders = ",".join("?" for _ in refs)
        rows = self._connection.execute(
            "SELECT record_id FROM profile_four_dimension_records "
            f"WHERE account_id = ? AND record_id IN ({placeholders})",
            (account_id, *refs),
        ).fetchall()
        existing = {str(row["record_id"]) for row in rows}
        return sum(1 for item in items if item.source_record_id not in existing)


__all__ = [
    "HistoryControlsRecord",
    "HistoryFeedbackRecord",
    "HistoryItemRecord",
    "HistoryMessageRecord",
    "ProfileHistorySnapshotReader",
]
