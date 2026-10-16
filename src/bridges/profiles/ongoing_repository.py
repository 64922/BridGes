"""近期工作上下文的持久化（画像工单 13）。

上下文是近期事项投影的带版本派生视图，本模块只负责它的**存储与守卫**，
不做任何投影计算，也不持有独立事实：

- **失效即递增世代（epoch）**：条目写入、来源删除或控制变化后调用
  :meth:`OngoingContextStore.invalidate`，世代加一、标记失效原因；旧载荷
  保留仅供排障观察，读取路径必须按「已失效」处理，绝不继续当作当前内容。
- **重建以捕获的世代做 CAS 提交**：重建方先捕获世代、再计算载荷，最后用
  :meth:`OngoingContextStore.commit_rebuild` 提交；期间发生过任何失效时
  提交失败（返回 ``False``），迟到重建不能覆盖新状态，只能重读依赖重试。
- **按账户隔离**：全部读写带 ``account_id``；不存在跨账户行。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol

from bridges.profiles.transactions import joined_transaction
from bridges.storage.database import BridgesDatabase


@dataclass(frozen=True)
class StoredOngoingContext:
    """一行近期工作上下文：世代、依赖指纹与冻结载荷。"""

    account_id: str
    context_version: str
    epoch: int
    dependency_fingerprint: str
    payload_json: str
    built_at: datetime | None
    invalidated_at: datetime | None
    invalidated_reason: str | None

    @property
    def is_current(self) -> bool:
        """是否未被失效：被失效的行永不直接服务读取。"""

        return self.invalidated_at is None


class OngoingContextStore(Protocol):
    """上下文存储端口（SQLite 与内存替身共用同一语义）。"""

    def read(self, account_id: str) -> StoredOngoingContext | None:
        """读取当前行；从未建立时返回 ``None``。"""

    def invalidate(self, account_id: str, *, reason: str, now: datetime) -> None:
        """立即失效：世代加一；行不存在时从世代 1 起建立失效行。"""

    def commit_rebuild(
        self,
        account_id: str,
        *,
        captured_epoch: int,
        context_version: str,
        dependency_fingerprint: str,
        payload_json: str,
        now: datetime,
    ) -> bool:
        """世代 CAS 提交重建；返回是否赢得提交（丢失竞争时为 ``False``）。"""


class InMemoryOngoingContextStore:
    """进程内替身（仅测试/E2E；语义与 SQLite 实现一致）。"""

    def __init__(self) -> None:
        self._rows: dict[str, StoredOngoingContext] = {}

    def read(self, account_id: str) -> StoredOngoingContext | None:
        return self._rows.get(account_id)

    def invalidate(self, account_id: str, *, reason: str, now: datetime) -> None:
        row = self._rows.get(account_id)
        if row is None:
            self._rows[account_id] = StoredOngoingContext(
                account_id=account_id,
                context_version="",
                epoch=1,
                dependency_fingerprint="",
                payload_json="[]",
                built_at=None,
                invalidated_at=now,
                invalidated_reason=reason,
            )
            return
        self._rows[account_id] = replace(
            row,
            epoch=row.epoch + 1,
            invalidated_at=now,
            invalidated_reason=reason,
        )

    def commit_rebuild(
        self,
        account_id: str,
        *,
        captured_epoch: int,
        context_version: str,
        dependency_fingerprint: str,
        payload_json: str,
        now: datetime,
    ) -> bool:
        row = self._rows.get(account_id)
        current = 0 if row is None else row.epoch
        if current != captured_epoch:
            return False
        self._rows[account_id] = StoredOngoingContext(
            account_id=account_id,
            context_version=context_version,
            epoch=captured_epoch + 1,
            dependency_fingerprint=dependency_fingerprint,
            payload_json=payload_json,
            built_at=now,
            invalidated_at=None,
            invalidated_reason=None,
        )
        return True


class SqliteOngoingContextStore:
    """``profile_ongoing_context`` 表实现（随账户删除，不进导出）。"""

    def __init__(self, database: BridgesDatabase) -> None:
        self._database = database

    def read(self, account_id: str) -> StoredOngoingContext | None:
        row = self._database.connection.execute(
            "SELECT * FROM profile_ongoing_context WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        if row is None:
            return None
        return StoredOngoingContext(
            account_id=str(row["account_id"]),
            context_version=str(row["context_version"]),
            epoch=int(row["epoch"]),
            dependency_fingerprint=str(row["dependency_fingerprint"]),
            payload_json=str(row["payload_json"]),
            built_at=_parse_time(row["built_at"]),
            invalidated_at=_parse_time(row["invalidated_at"]),
            invalidated_reason=(
                str(row["invalidated_reason"])
                if row["invalidated_reason"] is not None
                else None
            ),
        )

    def invalidate(self, account_id: str, *, reason: str, now: datetime) -> None:
        timestamp = now.isoformat(timespec="seconds")
        with joined_transaction(self._database):
            self._database.connection.execute(
                "INSERT INTO profile_ongoing_context"
                " (account_id, context_version, epoch, dependency_fingerprint,"
                "  payload_json, built_at, invalidated_at, invalidated_reason)"
                " VALUES (?, '', 1, '', '[]', NULL, ?, ?)"
                " ON CONFLICT(account_id) DO UPDATE SET"
                " epoch = profile_ongoing_context.epoch + 1,"
                " invalidated_at = excluded.invalidated_at,"
                " invalidated_reason = excluded.invalidated_reason",
                (account_id, timestamp, reason),
            )

    def commit_rebuild(
        self,
        account_id: str,
        *,
        captured_epoch: int,
        context_version: str,
        dependency_fingerprint: str,
        payload_json: str,
        now: datetime,
    ) -> bool:
        timestamp = now.isoformat(timespec="seconds")
        with joined_transaction(self._database):
            cursor = self._database.connection.execute(
                "INSERT INTO profile_ongoing_context"
                " (account_id, context_version, epoch, dependency_fingerprint,"
                "  payload_json, built_at, invalidated_at, invalidated_reason)"
                " VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)"
                " ON CONFLICT(account_id) DO UPDATE SET"
                " context_version = excluded.context_version,"
                " epoch = excluded.epoch,"
                " dependency_fingerprint = excluded.dependency_fingerprint,"
                " payload_json = excluded.payload_json,"
                " built_at = excluded.built_at,"
                " invalidated_at = NULL,"
                " invalidated_reason = NULL"
                " WHERE profile_ongoing_context.epoch = ?",
                (
                    account_id,
                    context_version,
                    captured_epoch + 1,
                    dependency_fingerprint,
                    payload_json,
                    timestamp,
                    captured_epoch,
                ),
            )
        return cursor.rowcount == 1


def _parse_time(value: object) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


__all__ = [
    "InMemoryOngoingContextStore",
    "OngoingContextStore",
    "SqliteOngoingContextStore",
    "StoredOngoingContext",
]
