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
SCHEMA_VERSION = 12

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
    # Issue 17：文档摄取与版本化全文/向量索引。document_records 是持久化摄取
    # 状态机（含失败阶段与中文原因）；document_parse_cache 做账户内同内容
    # 解析复用；document_chunks 记录可追溯到原文范围的哈希分块；index_versions
    # / index_active / index_vectors / fts_chunks 组成不可混写的版本化索引
    # （合同变化全量重建、校验后原子切换，旧版可回滚）。存量已上传附件在
    # 迁移时按当前解析器版本回填为 queued，由后台执行器接管。
    7: [
        """
        CREATE TABLE document_records (
            document_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            object_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            parser_version TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued', 'parsing', 'processing', 'ready', 'empty', 'error')),
            failure_stage TEXT,
            failure_reason TEXT,
            retry_count INTEGER NOT NULL DEFAULT 0,
            title TEXT,
            page_count INTEGER NOT NULL DEFAULT 0,
            section_count INTEGER NOT NULL DEFAULT 0,
            chunk_count INTEGER NOT NULL DEFAULT 0,
            vector_enabled INTEGER NOT NULL DEFAULT 0,
            vector_indexed INTEGER NOT NULL DEFAULT 0,
            claimed_at TEXT,
            lease_expires_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE UNIQUE INDEX idx_document_records_object
        ON document_records(account_id, object_id)
        """,
        """
        CREATE INDEX idx_document_records_account_status
        ON document_records(account_id, status)
        """,
        """
        CREATE TABLE document_parse_cache (
            account_id TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            parser_version TEXT NOT NULL,
            parsed_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (account_id, content_hash)
        )
        """,
        """
        CREATE TABLE document_chunks (
            chunk_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL REFERENCES document_records(document_id),
            account_id TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            content TEXT NOT NULL,
            section_title TEXT,
            page_number INTEGER,
            start_offset INTEGER NOT NULL,
            end_offset INTEGER NOT NULL,
            content_hash TEXT NOT NULL,
            vector_status TEXT NOT NULL DEFAULT 'pending'
                CHECK (vector_status IN ('pending', 'indexed', 'unavailable')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (document_id, chunk_index)
        )
        """,
        """
        CREATE INDEX idx_document_chunks_document
        ON document_chunks(account_id, document_id)
        """,
        """
        CREATE TABLE index_versions (
            version_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            contract_json TEXT NOT NULL,
            contract_hash TEXT NOT NULL,
            status TEXT NOT NULL
                CHECK (status IN ('building', 'active', 'obsolete', 'failed')),
            expected_chunk_count INTEGER NOT NULL DEFAULT 0,
            chunk_count INTEGER NOT NULL DEFAULT 0,
            vector_count INTEGER NOT NULL DEFAULT 0,
            error_message TEXT,
            built_at TEXT,
            switched_at TEXT,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX idx_index_versions_account
        ON index_versions(account_id, status)
        """,
        """
        CREATE TABLE index_active (
            account_id TEXT PRIMARY KEY,
            version_id TEXT NOT NULL REFERENCES index_versions(version_id)
        )
        """,
        """
        CREATE TABLE index_vectors (
            vector_id TEXT PRIMARY KEY,
            version_id TEXT NOT NULL REFERENCES index_versions(version_id),
            account_id TEXT NOT NULL,
            chunk_id TEXT NOT NULL REFERENCES document_chunks(chunk_id),
            vector_json TEXT NOT NULL,
            dimension_count INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX idx_index_vectors_version
        ON index_vectors(version_id)
        """,
        """
        CREATE VIRTUAL TABLE fts_chunks USING fts5(
            version_id UNINDEXED,
            account_id UNINDEXED,
            chunk_id UNINDEXED,
            content,
            tokenize = 'trigram'
        )
        """,
        # 存量附件回填：只入队摄取支持的媒体类型（与 enqueue 行为一致），
        # 由后台执行器摄取（幂等，重复执行会被 object_id 唯一索引拒绝）。
        """
        INSERT INTO document_records
            (document_id, account_id, object_id, conversation_id, content_hash,
             parser_version, status, created_at, updated_at)
        SELECT 'doc-' || a.object_id, a.account_id, a.object_id, a.conversation_id,
               o.content_hash, 'unknown-v0', 'queued',
               a.created_at, a.created_at
        FROM chat_attachments a
        JOIN objects o ON o.object_id = a.object_id
        WHERE o.status = 'active'
          AND o.media_type IN (
              'application/pdf',
              'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
              'text/plain',
              'text/markdown',
              'image/png',
              'image/jpeg',
              'image/gif',
              'image/webp'
          )
        """,
    ],
    # Issue 18：全局本地知识库。document_records.conversation_id 放开为可空
    # （全局知识库材料不绑定对话），并新增 source 列区分聊天附件
    # （chat_attachment，存量行默认值）与知识库材料（knowledge_base）；
    # rebuild_requested 标记用户显式请求的版本化重建，由后台执行器在
    # 文档重新就绪后做全量重建（产生新索引版本）。SQLite 不能修改列约束，
    # 按表重建迁移；外键全程开启时不能删除仍被引用的父表，因此按
    # document_chunks / index_vectors（子表先备份再删）→ document_records
    # 重建 → 子表原样恢复的顺序执行，全部数据保留。
    8: [
        """
        CREATE TABLE document_records_v8 (
            document_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            object_id TEXT NOT NULL,
            conversation_id TEXT,
            content_hash TEXT NOT NULL,
            parser_version TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued', 'parsing', 'processing', 'ready', 'empty', 'error')),
            failure_stage TEXT,
            failure_reason TEXT,
            retry_count INTEGER NOT NULL DEFAULT 0,
            title TEXT,
            page_count INTEGER NOT NULL DEFAULT 0,
            section_count INTEGER NOT NULL DEFAULT 0,
            chunk_count INTEGER NOT NULL DEFAULT 0,
            vector_enabled INTEGER NOT NULL DEFAULT 0,
            vector_indexed INTEGER NOT NULL DEFAULT 0,
            claimed_at TEXT,
            lease_expires_at TEXT,
            source TEXT NOT NULL DEFAULT 'chat_attachment'
                CHECK (source IN ('chat_attachment', 'knowledge_base')),
            rebuild_requested INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        INSERT INTO document_records_v8
            (document_id, account_id, object_id, conversation_id, content_hash,
             parser_version, status, failure_stage, failure_reason, retry_count,
             title, page_count, section_count, chunk_count, vector_enabled,
             vector_indexed, claimed_at, lease_expires_at, created_at, updated_at)
        SELECT document_id, account_id, object_id, conversation_id, content_hash,
               parser_version, status, failure_stage, failure_reason, retry_count,
               title, page_count, section_count, chunk_count, vector_enabled,
               vector_indexed, claimed_at, lease_expires_at, created_at, updated_at
        FROM document_records
        """,
        # 子表备份（纯数据表，无外键），随后按子→父顺序删除，父表才能安全重建。
        """
        CREATE TABLE document_chunks_backup AS SELECT * FROM document_chunks
        """,
        """
        CREATE TABLE index_vectors_backup AS SELECT * FROM index_vectors
        """,
        """
        DROP TABLE index_vectors
        """,
        """
        DROP TABLE document_chunks
        """,
        """
        DROP TABLE document_records
        """,
        """
        ALTER TABLE document_records_v8 RENAME TO document_records
        """,
        # 子表按原 v7 模式恢复并回填数据，外键在提交时校验通过。
        """
        CREATE TABLE document_chunks (
            chunk_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL REFERENCES document_records(document_id),
            account_id TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            content TEXT NOT NULL,
            section_title TEXT,
            page_number INTEGER,
            start_offset INTEGER NOT NULL,
            end_offset INTEGER NOT NULL,
            content_hash TEXT NOT NULL,
            vector_status TEXT NOT NULL DEFAULT 'pending'
                CHECK (vector_status IN ('pending', 'indexed', 'unavailable')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (document_id, chunk_index)
        )
        """,
        """
        INSERT INTO document_chunks SELECT * FROM document_chunks_backup
        """,
        """
        CREATE TABLE index_vectors (
            vector_id TEXT PRIMARY KEY,
            version_id TEXT NOT NULL REFERENCES index_versions(version_id),
            account_id TEXT NOT NULL,
            chunk_id TEXT NOT NULL REFERENCES document_chunks(chunk_id),
            vector_json TEXT NOT NULL,
            dimension_count INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        """
        INSERT INTO index_vectors SELECT * FROM index_vectors_backup
        """,
        """
        DROP TABLE document_chunks_backup
        """,
        """
        DROP TABLE index_vectors_backup
        """,
        """
        CREATE UNIQUE INDEX idx_document_records_object
        ON document_records(account_id, object_id)
        """,
        """
        CREATE INDEX idx_document_records_account_status
        ON document_records(account_id, status)
        """,
        """
        CREATE INDEX idx_document_records_account_source
        ON document_records(account_id, source, created_at DESC)
        """,
        """
        CREATE INDEX idx_document_chunks_document
        ON document_chunks(account_id, document_id)
        """,
        """
        CREATE INDEX idx_index_vectors_version
        ON index_vectors(version_id)
        """,
    ],
    # Issue 19：文件夹式学习项目。learning_projects 是账户内的项目文件夹
    # （对话可选归属、项目级文件）；document_records 的 source 放开
    # project_file（项目文件与知识库材料、聊天附件共用同一摄取状态机），
    # 并新增可空 project_id 记录项目归属（服务层校验归属，不设外键）。
    # SQLite 不能修改列约束，沿用 v8 的表重建迁移顺序：子表先备份再删 →
    # document_records 重建 → 子表原样恢复，全部数据保留。
    9: [
        """
        CREATE TABLE learning_projects (
            project_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL REFERENCES accounts(account_id)
                ON DELETE CASCADE,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX idx_learning_projects_account
        ON learning_projects(account_id, updated_at DESC)
        """,
        """
        CREATE TABLE document_records_v9 (
            document_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            object_id TEXT NOT NULL,
            conversation_id TEXT,
            content_hash TEXT NOT NULL,
            parser_version TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued', 'parsing', 'processing', 'ready', 'empty', 'error')),
            failure_stage TEXT,
            failure_reason TEXT,
            retry_count INTEGER NOT NULL DEFAULT 0,
            title TEXT,
            page_count INTEGER NOT NULL DEFAULT 0,
            section_count INTEGER NOT NULL DEFAULT 0,
            chunk_count INTEGER NOT NULL DEFAULT 0,
            vector_enabled INTEGER NOT NULL DEFAULT 0,
            vector_indexed INTEGER NOT NULL DEFAULT 0,
            claimed_at TEXT,
            lease_expires_at TEXT,
            source TEXT NOT NULL DEFAULT 'chat_attachment'
                CHECK (source IN ('chat_attachment', 'knowledge_base', 'project_file')),
            rebuild_requested INTEGER NOT NULL DEFAULT 0,
            project_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        INSERT INTO document_records_v9
            (document_id, account_id, object_id, conversation_id, content_hash,
             parser_version, status, failure_stage, failure_reason, retry_count,
             title, page_count, section_count, chunk_count, vector_enabled,
             vector_indexed, claimed_at, lease_expires_at, source,
             rebuild_requested, created_at, updated_at)
        SELECT document_id, account_id, object_id, conversation_id, content_hash,
               parser_version, status, failure_stage, failure_reason, retry_count,
               title, page_count, section_count, chunk_count, vector_enabled,
               vector_indexed, claimed_at, lease_expires_at, source,
               rebuild_requested, created_at, updated_at
        FROM document_records
        """,
        # 子表备份（纯数据表，无外键），随后按子→父顺序删除，父表才能安全重建。
        """
        CREATE TABLE document_chunks_backup AS SELECT * FROM document_chunks
        """,
        """
        CREATE TABLE index_vectors_backup AS SELECT * FROM index_vectors
        """,
        """
        DROP TABLE index_vectors
        """,
        """
        DROP TABLE document_chunks
        """,
        """
        DROP TABLE document_records
        """,
        """
        ALTER TABLE document_records_v9 RENAME TO document_records
        """,
        # 子表按原模式恢复并回填数据，外键在提交时校验通过。
        """
        CREATE TABLE document_chunks (
            chunk_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL REFERENCES document_records(document_id),
            account_id TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            content TEXT NOT NULL,
            section_title TEXT,
            page_number INTEGER,
            start_offset INTEGER NOT NULL,
            end_offset INTEGER NOT NULL,
            content_hash TEXT NOT NULL,
            vector_status TEXT NOT NULL DEFAULT 'pending'
                CHECK (vector_status IN ('pending', 'indexed', 'unavailable')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (document_id, chunk_index)
        )
        """,
        """
        INSERT INTO document_chunks SELECT * FROM document_chunks_backup
        """,
        """
        CREATE TABLE index_vectors (
            vector_id TEXT PRIMARY KEY,
            version_id TEXT NOT NULL REFERENCES index_versions(version_id),
            account_id TEXT NOT NULL,
            chunk_id TEXT NOT NULL REFERENCES document_chunks(chunk_id),
            vector_json TEXT NOT NULL,
            dimension_count INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        """
        INSERT INTO index_vectors SELECT * FROM index_vectors_backup
        """,
        """
        DROP TABLE document_chunks_backup
        """,
        """
        DROP TABLE index_vectors_backup
        """,
        """
        CREATE UNIQUE INDEX idx_document_records_object
        ON document_records(account_id, object_id)
        """,
        """
        CREATE INDEX idx_document_records_account_status
        ON document_records(account_id, status)
        """,
        """
        CREATE INDEX idx_document_records_account_source
        ON document_records(account_id, source, created_at DESC)
        """,
        """
        CREATE INDEX idx_document_chunks_document
        ON document_chunks(account_id, document_id)
        """,
        """
        CREATE INDEX idx_index_vectors_version
        ON index_vectors(version_id)
        """,
    ],
    # Issue 20：分层本地检索、融合排序与引用。retrieval_rounds 记录每轮
    # 检索的作用域（是否使用知识库）、充足性信号、索引版本与每层状态；
    # message_citations 固化最终引用的展示数据（文件名/页码/章节/片段），
    # 索引重建或原文变化不会让历史引用静默漂移，点击时按对象实时校验授权。
    10: [
        """
        CREATE TABLE retrieval_rounds (
            round_id TEXT PRIMARY KEY,
            message_id TEXT NOT NULL,
            user_message_id TEXT,
            conversation_id TEXT NOT NULL,
            account_id TEXT NOT NULL,
            use_knowledge_base INTEGER NOT NULL DEFAULT 1,
            sufficiency TEXT NOT NULL,
            index_version_id TEXT,
            layers_json TEXT NOT NULL DEFAULT '[]',
            note TEXT,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX idx_retrieval_rounds_message
        ON retrieval_rounds(account_id, message_id)
        """,
        """
        CREATE TABLE message_citations (
            citation_id TEXT PRIMARY KEY,
            round_id TEXT NOT NULL REFERENCES retrieval_rounds(round_id),
            message_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            account_id TEXT NOT NULL,
            source_layer TEXT NOT NULL,
            object_id TEXT NOT NULL,
            filename TEXT NOT NULL,
            media_type TEXT NOT NULL,
            page_number INTEGER,
            section_title TEXT,
            snippet TEXT NOT NULL,
            chunk_id TEXT NOT NULL,
            rank INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX idx_message_citations_round
        ON message_citations(account_id, round_id)
        """,
    ],
    # Issue 21：将公网搜索状态固化在助手消息上，保证刷新/重试/失败与引用
    # 展示都读取同一份结果，不把私有查询正文写入审计或消息之外的记录。
    11: [
        """
        ALTER TABLE messages ADD COLUMN web_search TEXT
        """,
    ],
    # Issue 22：将 arXiv MCP 搜索状态与真实论文结果固化到助手消息，
    # 刷新、失败、取消与重试都从同一份真实来源投影恢复。
    12: [
        """
        ALTER TABLE messages ADD COLUMN arxiv_search TEXT
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
                # 升级路径：版本行已存在（旧版本号），必须覆盖而非新增，
                # 否则 UNIQUE 约束使既有库永远无法升级。
                self._connection.execute(
                    "INSERT INTO schema_meta(key, value) VALUES ('version', ?)"
                    " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
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
