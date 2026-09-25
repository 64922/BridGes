"""LangGraph 检查点的事务 SQLite 适配器（V2 Issue 02）。

ADR-0030/V2 架构合同：LangGraph 检查点以 ``(account_id, conversation_id,
run_id)`` 关联现有 SQLite 运行、消息和 SSE 事件；优先为现有事务仓库提供
持久化检查点适配器，避免图自身写入与消息事务互相失配。

映射关系（固定，不接受前端指定）：
- ``thread_id`` 固定映射会话 ID；
- ``checkpoint_ns`` 固定映射运行 ID（同一会话的每次运行是独立检查点谱系，
  新运行不会从上一运行的检查点续跑）；
- 全部读写经 :class:`~bridges.storage.database.BridgesDatabase` 的
  ``scoped(account_id)`` 作用域连接强制账户隔离——跨账户恢复请求在 SQL
  层不可见，而非仅靠调用方约定。

存储形状与官方 ``langgraph-checkpoint-sqlite`` 一致：整个 checkpoint 字典
（含 channel_values）经 BaseCheckpointSaver 自带 serde 序列化为单个 blob，
pending writes 单独一行一写。
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from typing import Any

from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    ChannelVersions,
)
from langchain_core.runnables import RunnableConfig

from bridges.storage.database import BridgesDatabase


class RepositoryCheckpointSaver(BaseCheckpointSaver):
    """把 LangGraph 检查点写入 bridges.db 的持久化保存器（同步执行面）。

    执行器线程同步驱动图，只需实现同步方法；异步方法由基类默认委托到
    同步实现。构造时绑定 ``(account_id, conversation_id, run_id)``，每次
    读写都经账户作用域连接执行——图代码不存在绕开账户隔离的通道。
    """

    def __init__(
        self,
        database: BridgesDatabase,
        *,
        account_id: str,
        conversation_id: str,
        run_id: str,
    ) -> None:
        super().__init__()
        self._db = database
        self._account_id = account_id
        self._thread_id = conversation_id
        self._run_id = run_id

    # ------------------------------------------------------------------
    # 配置构造
    # ------------------------------------------------------------------

    def run_config(self) -> RunnableConfig:
        """本运行的检查点配置（thread=会话，ns=运行）。"""
        return {
            "configurable": {
                "thread_id": self._thread_id,
                "checkpoint_ns": self._run_id,
            }
        }

    def _config_with(self, checkpoint_id: str) -> RunnableConfig:
        return {
            "configurable": {
                "thread_id": self._thread_id,
                "checkpoint_ns": self._run_id,
                "checkpoint_id": checkpoint_id,
            }
        }

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        requested_id = config["configurable"].get("checkpoint_id")
        if requested_id is not None:
            row = self._db.scoped(self._account_id).execute(
                "SELECT thread_id, checkpoint_id, parent_checkpoint_id, type,"
                " checkpoint, metadata FROM graph_checkpoints"
                " WHERE account_id = ? AND thread_id = ? AND checkpoint_ns = ?"
                " AND checkpoint_id = ?",
                (self._account_id, self._thread_id, self._run_id, requested_id),
            ).fetchone()
        else:
            row = self._db.scoped(self._account_id).execute(
                "SELECT thread_id, checkpoint_id, parent_checkpoint_id, type,"
                " checkpoint, metadata FROM graph_checkpoints"
                " WHERE account_id = ? AND thread_id = ? AND checkpoint_ns = ?"
                " ORDER BY checkpoint_id DESC LIMIT 1",
                (self._account_id, self._thread_id, self._run_id),
            ).fetchone()
        if row is None:
            return None
        (
            _thread_id,
            checkpoint_id,
            parent_checkpoint_id,
            type_,
            checkpoint_blob,
            metadata_blob,
        ) = row
        writes = self._db.scoped(self._account_id).execute(
            "SELECT task_id, task_path, idx, channel, type, value"
            " FROM graph_checkpoint_writes"
            " WHERE account_id = ? AND thread_id = ? AND checkpoint_ns = ?"
            " AND checkpoint_id = ? ORDER BY task_id, task_path, idx",
            (self._account_id, self._thread_id, self._run_id, checkpoint_id),
        ).fetchall()
        return CheckpointTuple(
            self._config_with(str(checkpoint_id)),
            self.serde.loads_typed((str(type_), bytes(checkpoint_blob))),
            json.loads(bytes(metadata_blob)) if metadata_blob is not None else {},
            (
                self._config_with(str(parent_checkpoint_id))
                if parent_checkpoint_id is not None
                else None
            ),
            [
                (
                    str(task_id),
                    str(channel),
                    self.serde.loads_typed((str(type_), bytes(value)))
                    if value is not None
                    else None,
                )
                for task_id, task_path, idx, channel, type_, value in writes
            ],
        )

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        """按最新在前列出本运行谱系的检查点（本适配器仅限当前运行）。"""
        rows = self._db.scoped(self._account_id).execute(
            "SELECT checkpoint_id, parent_checkpoint_id, type, checkpoint, metadata"
            " FROM graph_checkpoints"
            " WHERE account_id = ? AND thread_id = ? AND checkpoint_ns = ?"
            " ORDER BY checkpoint_id DESC",
            (self._account_id, self._thread_id, self._run_id),
        ).fetchall()
        del filter, before, limit  # 本适配器的谱系固定为单运行，无需过滤
        for row in rows:
            checkpoint_id, parent_checkpoint_id, type_, blob, metadata_blob = row
            writes = self._db.scoped(self._account_id).execute(
                "SELECT task_id, task_path, idx, channel, type, value"
                " FROM graph_checkpoint_writes"
                " WHERE account_id = ? AND thread_id = ? AND checkpoint_ns = ?"
                " AND checkpoint_id = ? ORDER BY task_id, task_path, idx",
                (self._account_id, self._thread_id, self._run_id, checkpoint_id),
            ).fetchall()
            yield CheckpointTuple(
                self._config_with(str(checkpoint_id)),
                self.serde.loads_typed((str(type_), bytes(blob))),
                json.loads(bytes(metadata_blob)) if metadata_blob is not None else {},
                (
                    self._config_with(str(parent_checkpoint_id))
                    if parent_checkpoint_id is not None
                    else None
                ),
                [
                    (
                        str(task_id),
                        str(channel),
                        self.serde.loads_typed((str(type_), bytes(value)))
                        if value is not None
                        else None,
                    )
                    for task_id, task_path, idx, channel, type_, value in writes
                ],
            )

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        del new_versions  # 官方 sqlite 保存器同样只存 checkpoint 整体
        type_, checkpoint_blob = self.serde.dumps_typed(checkpoint)
        metadata_blob = json.dumps(
            metadata, ensure_ascii=False, sort_keys=True
        ).encode("utf-8", "ignore")
        parent_id = config["configurable"].get("checkpoint_id")
        with self._db.transaction():
            self._db.scoped(self._account_id).execute(
                "INSERT OR REPLACE INTO graph_checkpoints"
                "(thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id,"
                " account_id, conversation_id, run_id, type, checkpoint, metadata,"
                " created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    self._thread_id,
                    self._run_id,
                    checkpoint["id"],
                    parent_id,
                    self._account_id,
                    self._thread_id,
                    self._run_id,
                    type_,
                    checkpoint_blob,
                    metadata_blob,
                    checkpoint.get("ts"),
                ),
            )
        return self._config_with(str(checkpoint["id"]))

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        checkpoint_id = str(config["configurable"]["checkpoint_id"])
        rows = [
            (
                self._thread_id,
                self._run_id,
                checkpoint_id,
                task_id,
                task_path,
                WRITES_IDX_MAP.get(channel, idx),
                channel,
                *self.serde.dumps_typed(value),
                self._account_id,
            )
            for idx, (channel, value) in enumerate(writes)
        ]
        query = (
            "INSERT OR REPLACE INTO graph_checkpoint_writes"
            "(thread_id, checkpoint_ns, checkpoint_id, task_id, task_path, idx,"
            " channel, type, value, account_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            if all(w[0] in WRITES_IDX_MAP for w in writes)
            else "INSERT OR IGNORE INTO graph_checkpoint_writes"
            "(thread_id, checkpoint_ns, checkpoint_id, task_id, task_path, idx,"
            " channel, type, value, account_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        with self._db.transaction():
            for row in rows:
                self._db.scoped(self._account_id).execute(query, row)

    def delete_thread(self, thread_id: str) -> None:
        """删除一个会话的全部检查点（会话删除编排使用；账户限定）。"""
        with self._db.transaction():
            self._db.scoped(self._account_id).execute(
                "DELETE FROM graph_checkpoints"
                " WHERE account_id = ? AND thread_id = ?",
                (self._account_id, thread_id),
            )
            self._db.scoped(self._account_id).execute(
                "DELETE FROM graph_checkpoint_writes"
                " WHERE account_id = ? AND thread_id = ?",
                (self._account_id, thread_id),
            )
