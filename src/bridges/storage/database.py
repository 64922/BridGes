"""BridGes 权威数据库：版本化事务 SQLite。

空数据目录首次启动会事务化创建带版本记录的 ``bridges.db``（``schema_meta``
记录模式版本）；重复启动只补齐缺失的迁移，绝不重复执行或原位改写旧数据。
所有写入通过显式事务边界完成，外键与 WAL 全程启用。
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from bridges.storage.errors import StorageError

#: 当前支持的数据模式版本。新增迁移时在此递增并在 ``MIGRATIONS`` 补充脚本。
SCHEMA_VERSION = 6

#: 每个版本对应的迁移脚本，按版本号从小到大依次执行。
MIGRATIONS: dict[int, list[str]] = {
    1: [
        """
        CREATE TABLE accounts (
            account_id TEXT PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE objects (
            object_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL REFERENCES accounts(account_id),
            content_hash TEXT NOT NULL,
            original_filename TEXT NOT NULL,
            content_length INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            cleanup_retry_count INTEGER NOT NULL DEFAULT 0,
            last_cleanup_error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX idx_objects_account ON objects(account_id)
        """,
        """
        CREATE INDEX idx_objects_status ON objects(status)
        """,
    ],
    # Issue 11: 持久化对话、消息（含助手尝试）与模型运行锁。所有表都绑定
    # 稳定账户 ID（account_id 列），查询一律按 account_id 过滤，跨账户访问
    # 视为不存在。accounts 表只随头像等对象创建时填充（ensure_account），
    # 因此本域不设到 accounts 的外键，账户隔离由仓库查询层强制。
    2: [
        """
        CREATE TABLE conversations (
            conversation_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            mode TEXT NOT NULL DEFAULT 'companion',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX idx_conversations_account_updated
        ON conversations(account_id, updated_at DESC)
        """,
        """
        CREATE TABLE model_run_locks (
            lock_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            capability_name TEXT NOT NULL,
            capability_version TEXT NOT NULL,
            actual_model_id TEXT,
            region TEXT NOT NULL,
            status TEXT NOT NULL,
            error_code TEXT,
            error_message TEXT,
            usage TEXT,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX idx_run_locks_account_created
        ON model_run_locks(account_id, created_at DESC)
        """,
        """
        CREATE TABLE messages (
            message_id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),
            account_id TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
            attempt_number INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'done'
                CHECK (status IN ('streaming', 'done', 'error', 'stopped')),
            content TEXT NOT NULL DEFAULT '',
            error_code TEXT,
            error_message TEXT,
            duration_ms INTEGER,
            model_id TEXT,
            run_lock_id TEXT REFERENCES model_run_locks(lock_id),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX idx_messages_conversation_created
        ON messages(conversation_id, created_at, message_id)
        """,
        """
        CREATE INDEX idx_messages_account ON messages(account_id)
        """,
    ],
    # Issue 14: 双模式与可折叠思考摘要。messages 增加可公开思考摘要
    # （JSON 文本，绝不存原始思维链）；mode_events 记录写入消息流的可见
    # 模式切换事件（与消息同序渲染）。既有行通过 DEFAULT NULL 自然兼容。
    3: [
        """
        ALTER TABLE messages ADD COLUMN thinking TEXT
        """,
        """
        CREATE TABLE mode_events (
            event_id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),
            account_id TEXT NOT NULL,
            from_mode TEXT NOT NULL,
            to_mode TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX idx_mode_events_conversation_created
        ON mode_events(conversation_id, created_at)
        """,
        """
        CREATE INDEX idx_mode_events_account ON mode_events(account_id)
        """,
    ],
    # Issue 15: 最近会话生命周期。置顶、可选学习项目归属与旧数据库兼容。
    4: [
        """
        ALTER TABLE conversations ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0
        """,
        """
        ALTER TABLE conversations ADD COLUMN project_id TEXT
        """,
        """
        CREATE INDEX idx_conversations_account_pinned_updated
        ON conversations(account_id, pinned DESC, updated_at DESC, created_at DESC)
        """,
    ],
    # Issue 16：安全聊天附件。
    5: [
        """
        ALTER TABLE objects ADD COLUMN media_type TEXT NOT NULL
            DEFAULT 'application/octet-stream'
        """,
        """
        CREATE TABLE chat_attachments (
            object_id TEXT PRIMARY KEY REFERENCES objects(object_id),
            account_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            message_id TEXT,
            upload_id TEXT NOT NULL UNIQUE,
            media_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'uploaded'
                CHECK (status IN ('uploaded', 'bound')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX idx_chat_attachments_message
        ON chat_attachments(account_id, conversation_id, message_id)
        """,
        """
        CREATE INDEX idx_chat_attachments_upload
        ON chat_attachments(account_id, conversation_id, upload_id)
        """,
    ],
    # Issue 16：记录先到达的取消请求，避免上传事务随后落库形成孤儿对象。
    6: [
        """
        CREATE TABLE chat_attachment_cancellations (
            account_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            upload_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (account_id, conversation_id, upload_id)
        )
        """,
    ],
}


class BridgesDatabase:
    """版本化事务 SQLite 数据库连接。

    单连接 + 显式事务边界；``initialize`` 幂等，可安全重复调用。
    """

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        try:
            self._connection = sqlite3.connect(
                self.path,
                timeout=10.0,
                check_same_thread=False,
                isolation_level=None,
            )
        except sqlite3.Error as exc:
            raise StorageError(
                "无法打开数据目录下的数据库文件，请检查数据目录权限与磁盘空间。"
            ) from exc
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._configure()

    @property
    def connection(self) -> sqlite3.Connection:
        """数据库连接；写入必须包在 ``transaction()`` 中。

        账户域仓库不应直接使用裸连接：改用 :meth:`scoped` 获得账户作用域
        查询面，让跨账户查询在 SQL 层被强制拒绝。
        """
        return self._connection

    def scoped(self, account_id: str) -> ScopedConnection:
        """返回绑定账户的作用域查询面。

        作用域强制：INSERT 必须包含 ``account_id`` 列、其余语句的 WHERE
        必须包含 ``account_id`` 过滤；违反即拒绝执行，跨账户访问从
        "约定"升级为"不可能"。
        """
        return ScopedConnection(self, account_id)

    def _configure(self) -> None:
        """启用 WAL、完整同步与外键约束。"""
        try:
            if self.path != ":memory:":
                self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=FULL")
            self._connection.execute("PRAGMA foreign_keys=ON")
        except sqlite3.Error as exc:
            raise StorageError(
                "数据库初始化失败，请检查数据目录是否可写。"
            ) from exc

    def initialize(self) -> int:
        """事务化创建或升级数据库模式，返回当前版本；重复调用安全。

        迁移脚本与版本号在单个事务内完成：中途失败整体回滚，不会留下
        半迁移状态；已是最新版本时不做任何写操作。
        """
        try:
            with self.transaction():
                self._connection.execute(
                    "CREATE TABLE IF NOT EXISTS schema_meta ("
                    " key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                )
                row = self._connection.execute(
                    "SELECT value FROM schema_meta WHERE key = 'version'"
                ).fetchone()
                if row is not None:
                    try:
                        current = int(str(row["value"]))
                    except ValueError as exc:
                        raise StorageError(
                            "数据库迁移版本记录损坏，请检查数据目录。"
                        ) from exc
                else:
                    current = 0
                if current > SCHEMA_VERSION:
                    raise StorageError(
                        f"数据库迁移版本（{current}）高于当前程序支持的版本"
                        f"（{SCHEMA_VERSION}），请升级程序后再启动。"
                    )
                if current == SCHEMA_VERSION:
                    return current
                for version in range(current + 1, SCHEMA_VERSION + 1):
                    for statement in MIGRATIONS[version]:
                        self._connection.execute(statement)
                self._connection.execute(
                    "INSERT INTO schema_meta(key, value) VALUES ('version', ?)",
                    (str(SCHEMA_VERSION),),
                )
                return SCHEMA_VERSION
        except sqlite3.DatabaseError as exc:
            raise StorageError(
                "数据库文件已损坏或不是有效的 SQLite 数据库，请检查数据目录。"
            ) from exc

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """显式事务边界：失败整体回滚，成功提交。

        BEGIN 失败（数据库繁忙或文件不可写）时报中文运维错误，不向调用方
        泄漏原始 sqlite 异常。
        """
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
            except sqlite3.Error as exc:
                raise StorageError(
                    "数据库当前不可写，请稍后重试或检查数据目录权限。"
                ) from exc
            try:
                yield
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise
            self._connection.execute("COMMIT")

    def health_check(self) -> bool:
        """返回数据库是否可查询。"""
        try:
            with self._lock:
                row = self._connection.execute("SELECT 1 AS ok").fetchone()
            return row is not None and int(row["ok"]) == 1
        except sqlite3.Error:
            return False

    def close(self) -> None:
        """关闭底层连接。"""
        with self._lock:
            self._connection.close()


class ScopedConnection:
    """账户作用域查询面：SQL 级强制账户隔离。

    ``execute`` 在运行前校验 SQL 是否显式引用账户：
    - INSERT 语句的列清单（VALUES 之前）必须包含 ``account_id``；
    - 其余语句的 WHERE 子句必须包含 ``account_id`` 过滤；
    - 违反时抛 :class:`StorageError`，不做任何数据库访问。

    这样"忘记写账户过滤"不再是静默跨账户泄漏，而是立即失败；
    账户域仓库通过 :meth:`BridgesDatabase.scoped` 获取本对象。
    """

    def __init__(self, database: BridgesDatabase, account_id: str) -> None:
        self._db = database
        self._account_id = account_id

    @property
    def account_id(self) -> str:
        """本作用域绑定的账户标识。"""
        return self._account_id

    def _assert_account_bound(self, sql: str) -> None:
        stripped = sql.lstrip()
        lowered = stripped.lower()
        if lowered.startswith("insert"):
            # INSERT 的强制点：列清单必须包含 account_id（检查 VALUES 之前的片段）
            head = lowered.split("values", 1)[0]
            if "account_id" not in head:
                raise StorageError(
                    "账户作用域 INSERT 必须包含 account_id 列，禁止写入无归属记录。"
                )
            return
        # SELECT / UPDATE / DELETE 的强制点：WHERE 子句必须包含 account_id 过滤
        where_pos = lowered.find("where")
        if where_pos < 0 or "account_id" not in lowered[where_pos:]:
            raise StorageError(
                "账户作用域查询必须通过 WHERE account_id 过滤限定账户范围。"
            )

    def execute(
        self, sql: str, params: Any = None
    ) -> sqlite3.Cursor:
        """在账户作用域内执行一条 SQL；写入必须包在 ``transaction()`` 中。"""
        self._assert_account_bound(sql)
        return self._db.connection.execute(sql, params)
