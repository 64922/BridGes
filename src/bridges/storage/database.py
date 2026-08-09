"""BridGes 权威数据库：版本化事务 SQLite。

空数据目录首次启动会事务化创建带版本记录的 ``bridges.db``（``schema_meta``
记录模式版本）；重复启动只补齐缺失的迁移，绝不重复执行或原位改写旧数据。
所有写入通过显式事务边界完成，外键与 WAL 全程启用。
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from bridges.storage.errors import StorageError

logger = logging.getLogger(__name__)

#: 当前支持的数据模式版本。新增迁移时在此递增并在 ``MIGRATIONS`` 补充脚本。
SCHEMA_VERSION = 34

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
    13: [
        """
        ALTER TABLE messages ADD COLUMN teaching TEXT
        """,
    ],
    # Issue 26：画像观察-候选-断言与许可/通知的持久化。账户列统一命名为
    # account_id，仓库层使用 scoped() 查询面让跨账户访问在 SQL 层被强制
    # 拒绝（后台任务不能串号）。通知表承载自动写入通知与一键撤回入口。
    14: [
        """
        CREATE TABLE profile_observations (
            observation_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            project_id TEXT,
            source_type TEXT NOT NULL,
            source_ref TEXT NOT NULL,
            source_span_or_event TEXT NOT NULL,
            scene TEXT NOT NULL,
            purpose TEXT NOT NULL,
            observed_content TEXT NOT NULL,
            signal_kind TEXT NOT NULL,
            extractor_and_version TEXT NOT NULL,
            model_rationale TEXT,
            reliability_factors_json TEXT NOT NULL DEFAULT '[]',
            sensitivity_class TEXT NOT NULL,
            retention_policy TEXT NOT NULL,
            authorization_version TEXT NOT NULL DEFAULT 'authz-1.0',
            content_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX idx_profile_observations_account
        ON profile_observations(account_id, created_at DESC)
        """,
        """
        CREATE INDEX idx_profile_observations_blocker
        ON profile_observations(account_id, content_hash, status)
        """,
        """
        CREATE TABLE profile_candidates (
            candidate_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            canonical_dimension TEXT NOT NULL,
            value_or_rule TEXT NOT NULL,
            applicable_scenes_json TEXT NOT NULL DEFAULT '[]',
            non_applicable_scenes_json TEXT NOT NULL DEFAULT '[]',
            supporting_observation_ids_json TEXT NOT NULL DEFAULT '[]',
            contradicting_observation_ids_json TEXT NOT NULL DEFAULT '[]',
            evidence_summary TEXT NOT NULL DEFAULT '',
            authorization_scope TEXT NOT NULL DEFAULT 'general',
            promotion_policy_version TEXT NOT NULL DEFAULT 'promotion-1.0',
            review_status TEXT NOT NULL,
            stability_state TEXT NOT NULL DEFAULT 'candidate',
            sensitivity_class TEXT NOT NULL DEFAULT 'preference',
            proposed_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            expires_at TEXT,
            human_decision_json TEXT
        )
        """,
        """
        CREATE INDEX idx_profile_candidates_account
        ON profile_candidates(account_id, proposed_at DESC)
        """,
        """
        CREATE TABLE profile_assertions (
            assertion_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            canonical_dimension TEXT NOT NULL,
            value_or_rule TEXT NOT NULL,
            applicable_scenes_json TEXT NOT NULL DEFAULT '[]',
            supporting_observation_ids_json TEXT NOT NULL DEFAULT '[]',
            contradicting_observation_ids_json TEXT NOT NULL DEFAULT '[]',
            authorization_scope TEXT NOT NULL DEFAULT 'general',
            status TEXT NOT NULL,
            sensitivity_class TEXT NOT NULL DEFAULT 'preference',
            expires_at TEXT,
            promoted_from_candidate_id TEXT,
            version INTEGER NOT NULL DEFAULT 1,
            last_used_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX idx_profile_assertions_account
        ON profile_assertions(account_id, created_at DESC)
        """,
        """
        CREATE INDEX idx_profile_assertions_dedup
        ON profile_assertions(account_id, canonical_dimension, status)
        """,
        """
        CREATE TABLE profile_assertion_versions (
            version_id TEXT PRIMARY KEY,
            assertion_id TEXT NOT NULL,
            account_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            canonical_dimension TEXT NOT NULL,
            value_or_rule TEXT NOT NULL,
            applicable_scenes_json TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL,
            sensitivity_class TEXT NOT NULL DEFAULT 'preference',
            promoted_from_candidate_id TEXT,
            content_hash TEXT NOT NULL,
            changed_at TEXT NOT NULL,
            changed_by TEXT NOT NULL,
            change_reason TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX idx_profile_assertion_versions
        ON profile_assertion_versions(account_id, assertion_id, version)
        """,
        """
        CREATE TABLE profile_slices (
            slice_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            purpose TEXT NOT NULL,
            project_id TEXT,
            included_items_json TEXT NOT NULL DEFAULT '[]',
            unused_items_json TEXT NOT NULL DEFAULT '[]',
            excluded_candidate_ids_json TEXT NOT NULL DEFAULT '[]',
            exclusion_reasons_json TEXT NOT NULL DEFAULT '{}',
            rejected_items_json TEXT NOT NULL DEFAULT '[]',
            authorization_snapshot TEXT NOT NULL DEFAULT 'authz-1.0',
            key_epoch TEXT NOT NULL DEFAULT 'epoch-0',
            expires_at TEXT,
            sensitivity_classes_allowed_json TEXT NOT NULL DEFAULT '[]',
            compiled_policy_version TEXT NOT NULL DEFAULT 'slice-1.0',
            status TEXT NOT NULL DEFAULT 'active',
            invalidated_at TEXT,
            invalidation_reason TEXT,
            compiled_at TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX idx_profile_slices_account_run
        ON profile_slices(account_id, run_id, compiled_at DESC)
        """,
        """
        CREATE TABLE profile_permissions (
            account_id TEXT NOT NULL,
            dimension TEXT NOT NULL,
            scene TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (account_id, dimension, scene)
        )
        """,
        """
        CREATE TABLE profile_notifications (
            notification_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            source_ref TEXT NOT NULL,
            source_text TEXT NOT NULL,
            dimension TEXT,
            scene TEXT,
            assertion_id TEXT,
            candidate_id TEXT,
            recallable INTEGER NOT NULL DEFAULT 0,
            recalled_at TEXT,
            read_at TEXT,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX idx_profile_notifications_account
        ON profile_notifications(account_id, created_at DESC)
        """,
    ],
    # Issue 27：上下文说明披露固化在助手消息上（快照含当时版本与状态，
    # 修正后历史回答仍可回放差异）；answer_feedback 记录「回答不合适」与
    # 「画像有误」反馈，按账户隔离、幂等去重、失败不丢反馈。
    15: [
        """
        ALTER TABLE messages ADD COLUMN context_note TEXT
        """,
        """
        CREATE TABLE answer_feedback (
            feedback_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            feedback_text TEXT NOT NULL,
            preference TEXT,
            assertion_id TEXT,
            status TEXT NOT NULL DEFAULT 'submitted',
            resolution_note TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX idx_answer_feedback_account
        ON answer_feedback(account_id, created_at DESC)
        """,
        """
        CREATE INDEX idx_answer_feedback_dedup
        ON answer_feedback(account_id, message_id, kind, assertion_id)
        """,
    ],
    # Issue 28：内置 bridges-humanizer SKILL 走真实消息流程。用户消息
    # 携带 skill JSON（标识+任务契约快照，重试沿用）；助手消息的人味化
    # 结果投影（输出合同/事实锁/引用/五态过程）同样固化在 skill 列。
    16: [
        """
        ALTER TABLE messages ADD COLUMN skill TEXT
        """,
    ],
    # Issue 29: 生涯规划助手结果投影（六类输出/证据/复核/过程状态），
    # 与 skill 列同语义：助手消息携带规划结果快照，重试沿用原输入；
    # 回答反馈增加 career_item_ref 列支持逐项反馈定位。
    17: [
        """
        ALTER TABLE messages ADD COLUMN career_planning TEXT
        """,
        """
        ALTER TABLE answer_feedback ADD COLUMN career_item_ref TEXT
        """,
    ],
    # Issue 30：单条回答朗读状态快照固化在助手消息上。快照含状态
    # （not_generated/ready/failed）、实际固定 TTS 模型标识、账户对象库
    # 音频引用与失败语义；刷新后可从同一快照重新请求播放。听写音频
    # 不落盘（请求体内存直传 ASR），无持久化表。
    18: [
        """
        ALTER TABLE messages ADD COLUMN read_aloud TEXT
        """,
    ],
    # Issue 31：图片生成与编辑纵向链路。image_tasks 是可恢复异步任务
    # 状态机（后台执行器按租约领取并轮询 DashScope 云端任务，取消后
    # 迟到结果不发布）；image_assets/image_versions 是账户隔离的版本化
    # 资产（编辑创建新版本并保留来源/提示/模型快照/时间关系，不覆盖
    # 原图）；messages.image 列保存助手消息上的任务/资产状态快照，
    # 刷新与重启后可恢复查询。
    19: [
        """
        CREATE TABLE IF NOT EXISTS image_tasks (
            task_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            message_id TEXT,
            kind TEXT NOT NULL,
            prompt TEXT NOT NULL,
            source_version_id TEXT,
            source_object_id TEXT,
            cloud_task_id TEXT,
            status TEXT NOT NULL DEFAULT 'queued',
            lease_expires_at TEXT,
            claimed_at TEXT,
            poll_count INTEGER NOT NULL DEFAULT 0,
            retry_count INTEGER NOT NULL DEFAULT 0,
            error_code TEXT,
            error_message TEXT,
            model_id TEXT,
            asset_id TEXT,
            result_version_id TEXT,
            cancelled_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_image_tasks_account_status
            ON image_tasks(account_id, status)
        """,
        """
        CREATE TABLE IF NOT EXISTS image_assets (
            asset_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            alt_text TEXT NOT NULL DEFAULT '',
            alt_text_source TEXT NOT NULL DEFAULT 'fallback',
            current_version_id TEXT,
            deleted INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS image_versions (
            version_id TEXT PRIMARY KEY,
            asset_id TEXT NOT NULL,
            account_id TEXT NOT NULL,
            parent_version_id TEXT,
            kind TEXT NOT NULL,
            prompt TEXT NOT NULL,
            model_id TEXT,
            object_id TEXT NOT NULL,
            media_type TEXT NOT NULL,
            content_length INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_image_versions_asset
            ON image_versions(asset_id, created_at)
        """,
        """
        ALTER TABLE messages ADD COLUMN image TEXT
        """,
    ],
    # Issue 32：文生视频纵向链路（ADR-0007：Wan 是矩阵唯一非 Qwen 系列
    # 例外）。video_tasks 是可恢复异步任务状态机（后台执行器按租约领取并
    # 轮询 DashScope 云端任务；取消后迟到结果经条件发布隔离，不进入对话
    # 或资产库）；video_assets 是账户隔离的本地资产（提示/模型/供应商
    # 任务标识/说明文字快照，单个对象无版本链——视频生成不提供编辑）；
    # messages.video 列保存助手消息上的任务/资产状态快照，刷新与重启后
    # 可恢复查询。
    20: [
        """
        CREATE TABLE IF NOT EXISTS video_tasks (
            task_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            message_id TEXT,
            prompt TEXT NOT NULL,
            cloud_task_id TEXT,
            status TEXT NOT NULL DEFAULT 'queued',
            lease_expires_at TEXT,
            claimed_at TEXT,
            poll_count INTEGER NOT NULL DEFAULT 0,
            retry_count INTEGER NOT NULL DEFAULT 0,
            error_code TEXT,
            error_message TEXT,
            model_id TEXT,
            asset_id TEXT,
            result_object_id TEXT,
            cancelled_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_video_tasks_account_status
            ON video_tasks(account_id, status)
        """,
        """
        CREATE TABLE IF NOT EXISTS video_assets (
            asset_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            description_source TEXT NOT NULL DEFAULT 'prompt',
            object_id TEXT NOT NULL,
            prompt TEXT NOT NULL,
            model_id TEXT,
            cloud_task_id TEXT,
            media_type TEXT NOT NULL,
            content_length INTEGER NOT NULL DEFAULT 0,
            deleted INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        ALTER TABLE messages ADD COLUMN video TEXT
        """,
    ],
    # Issue 33：QQ SMTP 任务提醒纵向链路。reminders 是账户级提醒（带时区
    # 结构化日程 + 冻结的画像措辞快照 + 调度字段：next_run_at/退避重试）；
    # reminder_deliveries 是投递记录（区分发送/失败/跳过/补发/手动重试，
    # 补发带 delayed 标记）；reminder_settings 是账户提醒设置（时区 +
    # SMTP 授权码验证状态机：unconfigured/verifying/verified/failed）。
    # 授权码绝不进入本库：只存在于凭据存储（smtp 命名空间）。
    21: [
        """
        CREATE TABLE IF NOT EXISTS reminders (
            reminder_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            qq_email TEXT NOT NULL,
            timezone TEXT NOT NULL,
            raw_text TEXT NOT NULL,
            schedule_json TEXT NOT NULL,
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            use_profile INTEGER NOT NULL DEFAULT 0,
            profile_slice_id TEXT,
            profile_categories TEXT NOT NULL DEFAULT '[]',
            profile_item_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'enabled',
            pause_reason TEXT,
            next_run_at TEXT,
            next_retry_at TEXT,
            retry_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_reminders_account_status
            ON reminders(account_id, status)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_reminders_due
            ON reminders(status, next_run_at)
        """,
        """
        CREATE TABLE IF NOT EXISTS reminder_deliveries (
            delivery_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            reminder_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            outcome TEXT NOT NULL,
            scheduled_for TEXT NOT NULL,
            attempted_at TEXT,
            delayed INTEGER NOT NULL DEFAULT 0,
            error_code TEXT,
            error_message TEXT,
            message_id TEXT,
            FOREIGN KEY (reminder_id) REFERENCES reminders(reminder_id)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_reminder_deliveries_reminder
            ON reminder_deliveries(account_id, reminder_id, attempted_at)
        """,
        """
        CREATE TABLE IF NOT EXISTS reminder_settings (
            account_id TEXT PRIMARY KEY,
            timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
            smtp_status TEXT NOT NULL DEFAULT 'unconfigured',
            smtp_verified_at TEXT,
            smtp_error_code TEXT,
            smtp_error_message TEXT,
            smtp_updated_at TEXT
        )
        """,
    ],
    # Issue 34：SKILL 插件中心。skill_packages 是用户上传声明式包的
    # 安装记录（按账户隔离，UNIQUE(account_id, plugin_id)；install_failed
    # 是可恢复失败态，不进入运行注册表）；account_skill_states 是内置
    # 只读插件的账户级启停状态（内置清单本体来自 plugins/registry，不
    # 入库，版本固定随应用发布）。zip 包字节存加密对象库（账户作用域），
    # 本库只存元数据与状态，绝不复制包内容。
    22: [
        """
        CREATE TABLE IF NOT EXISTS skill_packages (
            package_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            plugin_id TEXT NOT NULL,
            version TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            source TEXT,
            license TEXT,
            capabilities TEXT NOT NULL DEFAULT '[]',
            data_categories TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'install_failed',
            object_id TEXT,
            file_count INTEGER NOT NULL DEFAULT 0,
            content_length INTEGER NOT NULL DEFAULT 0,
            failure_reason TEXT,
            installed_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (account_id, plugin_id)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_skill_packages_account
            ON skill_packages(account_id, status)
        """,
        """
        CREATE TABLE IF NOT EXISTS account_skill_states (
            account_id TEXT NOT NULL,
            plugin_id TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (account_id, plugin_id)
        )
        """,
    ],
    # Issue 35: 显式授权 MCP 插件管理。mcp_servers 按账户保存固定版本
    # 安装描述（命令/权限清单/完整性锁定哈希）与运行状态；mcp_calls
    # 保存真实调用记录（次数/最近结果/失败原因），不含输入正文与秘密。
    23: [
        """
        CREATE TABLE IF NOT EXISTS mcp_servers (
            mcp_id TEXT NOT NULL,
            account_id TEXT NOT NULL,
            name TEXT NOT NULL,
            version TEXT NOT NULL,
            description TEXT,
            source TEXT NOT NULL,
            integrity TEXT,
            integrity_sha256 TEXT NOT NULL,
            command TEXT NOT NULL,
            permissions TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'healthy',
            enabled INTEGER NOT NULL DEFAULT 1,
            object_id TEXT,
            failure_reason TEXT,
            installed_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (account_id, mcp_id)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_mcp_servers_account
            ON mcp_servers(account_id, status)
        """,
        """
        CREATE TABLE IF NOT EXISTS mcp_calls (
            call_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            mcp_id TEXT NOT NULL,
            tool TEXT NOT NULL,
            status TEXT NOT NULL,
            error_code TEXT,
            error_message TEXT,
            latency_ms INTEGER NOT NULL DEFAULT 0,
            sensitive_ops INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_mcp_calls_account
            ON mcp_calls(account_id, mcp_id, created_at DESC)
        """,
    ],
    # Issue 36: 对话级插件选择（SKILL/MCP，JSON 列表随对话持久化）与
    # 消息内 MCP 调用结果投影（真实调用链路，刷新可恢复）。两列均为
    # 可空 JSON 文本，存量行不受影响。
    24: [
        """
        ALTER TABLE conversations ADD COLUMN plugin_selection TEXT
        """,
        """
        ALTER TABLE messages ADD COLUMN mcp_call TEXT
        """,
    ],
    # Issue 37: 账户删除状态机——部分失败时保留可观察、可重试的清理状态
    # 与最小非敏感审计。不设指向 accounts 的外键：删除事务会先行移除
    # accounts 行，状态记录必须独立存活到清理完成；pending_hashes 保存
    # 事务删除前收集的对象内容哈希清单，供文件系统清理失败后重试。
    25: [
        """
        CREATE TABLE IF NOT EXISTS account_deletions (
            deletion_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'deleting',
            retry_count INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            pending_hashes TEXT,
            started_at TEXT NOT NULL,
            completed_at TEXT
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_account_deletions_status
            ON account_deletions(status, started_at)
        """,
    ],
    # Issue 40：可复现 A/B 科学评测资产。评测套件、运行锁、案例结果、
    # 报告与盲评集是评测域资产而非用户数据：不进入账户作用域表（账户
    # 删除不影响评测记录），但随整库备份恢复。结果与报告 append-only：
    # 同一运行锁的重放产生新行，旧行永不覆盖或删除。
    26: [
        """
        CREATE TABLE IF NOT EXISTS eval_suites (
            suite_id TEXT NOT NULL,
            version TEXT NOT NULL,
            digest TEXT NOT NULL,
            definition_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            PRIMARY KEY (suite_id, version)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS eval_run_locks (
            lock_id TEXT PRIMARY KEY,
            suite_id TEXT NOT NULL,
            suite_version TEXT NOT NULL,
            suite_digest TEXT NOT NULL,
            lock_json TEXT NOT NULL,
            lock_digest TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS eval_case_results (
            case_result_id TEXT PRIMARY KEY,
            lock_id TEXT NOT NULL,
            sut_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            case_id TEXT NOT NULL,
            seed INTEGER NOT NULL,
            execution_index INTEGER NOT NULL,
            result_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_eval_results_lock
            ON eval_case_results(lock_id, sut_id, created_at)
        """,
        """
        CREATE TABLE IF NOT EXISTS eval_reports (
            report_id TEXT NOT NULL,
            report_version TEXT NOT NULL,
            lock_id TEXT NOT NULL,
            report_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (report_id, report_version)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS eval_blind_reviews (
            review_set_id TEXT PRIMARY KEY,
            lock_id TEXT NOT NULL,
            set_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_eval_reviews_lock
            ON eval_blind_reviews(lock_id, created_at)
        """,
    ],
    # Issue 43：统一「领取型任务」契约（runtime/queue.py 深 module）。
    # task_claims 只做领取与调度（租约/退避/崩溃恢复），子系统业务表
    # 仍是其自身状态的权威来源，不迁移存量 schema。表为系统级（跨账户
    # 调度），不含 account_id，不经账户作用域写入。
    27: [
        """
        CREATE TABLE IF NOT EXISTS task_claims (
            claim_id TEXT PRIMARY KEY,
            queue_name TEXT NOT NULL,
            task_key TEXT NOT NULL,
            worker TEXT,
            attempt INTEGER NOT NULL DEFAULT 0,
            claimed_at TEXT,
            lease_expires_at TEXT,
            next_retry_at TEXT,
            payload_json TEXT,
            result_json TEXT,
            status TEXT NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued', 'claimed', 'completed', 'failed')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (queue_name, task_key)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_task_claims_queue
            ON task_claims(queue_name, status, created_at)
        """,
        # 存量任务回填：升级前处于可处理状态的业务行进入调度表，避免
        # 迁移后孤儿化（旧实现每轮全表扫描会重试它们，新实现只处理
        # 队列行）。回填为 queued 立即重领，等价于旧「重启后恢复」语义；
        # 已耗尽/永久失败（retry_count 达上限、error_code 永久码）不回
        # 填，等子系统手动重试时重新入队。INSERT OR IGNORE 保证重复
        # 升级不重复回填。
        """
        INSERT OR IGNORE INTO task_claims(claim_id, queue_name, task_key, attempt,
            status, created_at, updated_at)
        SELECT 'cl-backfill-ing-' || document_id, 'ingestion',
               'ingestion:' || account_id || ':' || object_id, 0, 'queued',
               COALESCE(created_at, '1970-01-01T00:00:00+00:00'),
               COALESCE(updated_at, created_at, '1970-01-01T00:00:00+00:00')
        FROM document_records
        WHERE status IN ('queued', 'parsing', 'processing')
           OR (status = 'error' AND retry_count < 3)
        """,
        """
        INSERT OR IGNORE INTO task_claims(claim_id, queue_name, task_key, attempt,
            status, created_at, updated_at)
        SELECT 'cl-backfill-img-' || task_id, 'image',
               'image:' || account_id || ':' || task_id, 0, 'queued',
               COALESCE(created_at, '1970-01-01T00:00:00+00:00'),
               COALESCE(updated_at, created_at, '1970-01-01T00:00:00+00:00')
        FROM image_tasks
        WHERE status IN ('queued', 'running')
           OR (status = 'failed' AND retry_count < 3)
        """,
        """
        INSERT OR IGNORE INTO task_claims(claim_id, queue_name, task_key, attempt,
            status, created_at, updated_at)
        SELECT 'cl-backfill-vid-' || task_id, 'video',
               'video:' || account_id || ':' || task_id, 0, 'queued',
               COALESCE(created_at, '1970-01-01T00:00:00+00:00'),
               COALESCE(updated_at, created_at, '1970-01-01T00:00:00+00:00')
        FROM video_tasks
        WHERE status IN ('queued', 'submitting', 'generating', 'cancelling')
           OR (status = 'failed' AND retry_count < 3
               AND error_code NOT IN ('empty_result'))
        """,
        """
        INSERT OR IGNORE INTO task_claims(claim_id, queue_name, task_key, attempt,
            status, created_at, updated_at)
        SELECT 'cl-backfill-del-' || deletion_id, 'deletion',
               'deletion:' || deletion_id, 0, 'queued',
               COALESCE(started_at, '1970-01-01T00:00:00+00:00'),
               COALESCE(completed_at, started_at, '1970-01-01T00:00:00+00:00')
        FROM account_deletions
        WHERE status = 'failed'
        """,
    ],
    # Issue 43：workflows 运行状态持久化——内存 _RunRecord 落 SQLite，
    # 崩溃后进程重建可从本表恢复运行（与 lifecycle/deletion 的持久化
    # 恢复语义一致）。run 由 JSON 快照承载（pydantic 序列化），状态
    # 列冗余主状态便于运维查询。
    28: [
        """
        CREATE TABLE IF NOT EXISTS workflow_runs (
            run_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            context_json TEXT NOT NULL,
            work_order_json TEXT NOT NULL,
            status TEXT NOT NULL,
            artifact_trust_status TEXT NOT NULL,
            nodes_json TEXT NOT NULL DEFAULT '[]',
            human_todos_json TEXT NOT NULL DEFAULT '[]',
            model_run_locks_json TEXT NOT NULL DEFAULT '[]',
            current_node_index INTEGER,
            run_started_at TEXT,
            run_ended_at TEXT,
            cancel_reason TEXT,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_workflow_runs_account
            ON workflow_runs(account_id, updated_at)
        """,
    ],
    # Issue 02：持久化后台生成运行（ADR-0013 的"生成运行"落地）。回复
    # 生成从页面/SSE 生命周期迁移到持久化运行：generation_runs 保存每次
    # 运行的最小状态机（queued → running → done | failed | stopped）——
    # run ID、会话/消息归属、当前阶段、租约（崩溃恢复）、尝试号、终态
    # 原因与脱敏耗时；generation_events 按 run 追加单调递增游标事件
    # （SSE 订阅回放与恢复的唯一真相源，重启后仍可恢复）。generation
    # 队列行入 task_claims（queue_name='generation'）走统一领取契约。
    29: [
        """
        CREATE TABLE IF NOT EXISTS generation_runs (
            run_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            user_message_id TEXT NOT NULL,
            assistant_message_id TEXT NOT NULL,
            attempt_number INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued', 'running', 'done', 'failed', 'stopped')),
            stage TEXT,
            config_json TEXT,
            lease_owner TEXT,
            lease_expires_at TEXT,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            stop_requested INTEGER NOT NULL DEFAULT 0,
            error_code TEXT,
            error_message TEXT,
            duration_ms INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_generation_runs_account_status
            ON generation_runs(account_id, status)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_generation_runs_message
            ON generation_runs(assistant_message_id)
        """,
        """
        CREATE TABLE IF NOT EXISTS generation_events (
            run_id TEXT NOT NULL REFERENCES generation_runs(run_id),
            seq INTEGER NOT NULL,
            account_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (run_id, seq)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_generation_events_run
            ON generation_events(run_id, seq)
        """,
        # 存量 streaming 遗留消息（无后台运行）：由读取路径的陈旧收敛
        # 兜底（保持既有 stream_interrupted 语义），不再回填运行。
    ],
    # Issue 10：QQ 验证 attempt 持久化状态机。smtp_verification_attempts
    # 保存每次自发自收验证的唯一 attempt（不含授权码正文）：阶段
    # smtp_connecting/mail_sent/waiting_receipt/verified/failed/superseded、
    # 收件截止时间、更新时间与错误码；验证令牌（随机 hex，非秘密）用于
    # 重启后按主题/Message-ID 恢复轮询。reminder_settings.smtp_attempt_id
    # 指向当前 attempt：只有当前 attempt 可以提交账户 SMTP 终态，旧
    # attempt 的迟到成功/失败一律标 superseded 并丢弃（更换/删除授权码
    # 立即 supersede 旧 attempt）。
    30: [
        """
        CREATE TABLE IF NOT EXISTS smtp_verification_attempts (
            attempt_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            state TEXT NOT NULL,
            message_token TEXT NOT NULL,
            deadline_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            error_code TEXT,
            error_message TEXT,
            session_id TEXT
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_smtp_attempts_account_state
            ON smtp_verification_attempts(account_id, state)
        """,
        """
        ALTER TABLE reminder_settings ADD COLUMN smtp_attempt_id TEXT
        """,
    ],
    # Issue 03：原子首轮幂等键。conversations 新增 idempotency_key——
    # 首页「创建会话 + 首条消息 + 启动运行」在同一事务内完成，键值用于
    # 双击/网络重放去重（同账户同键只产生一份会话/消息/运行）。非首轮
    # 创建的会话该列为 NULL，SQLite 唯一索引允许多个 NULL，互不干扰。
    31: [
        """
        ALTER TABLE conversations ADD COLUMN idempotency_key TEXT
        """,
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_conversations_account_idempotency
            ON conversations(account_id, idempotency_key)
        """,
    ],
    # Issue 04：附件绑定一致性数据库约束——status='bound' 必须携带
    # message_id。事务语义由仓库层保障（消息与绑定同事务提交/回滚），
    # 这里作为最后防线，杜绝任何「绑定成功但消息缺失」的中间态写入。
    32: [
        """
        CREATE TRIGGER IF NOT EXISTS trg_attachments_bound_has_message_insert
        BEFORE INSERT ON chat_attachments
        FOR EACH ROW WHEN NEW.status = 'bound' AND NEW.message_id IS NULL
        BEGIN
            SELECT RAISE(ABORT, 'bound 状态必须携带 message_id');
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS trg_attachments_bound_has_message_update
        BEFORE UPDATE ON chat_attachments
        FOR EACH ROW WHEN NEW.status = 'bound' AND NEW.message_id IS NULL
        BEGIN
            SELECT RAISE(ABORT, 'bound 状态必须携带 message_id');
        END
        """,
    ],
    # Issue 02：学习项目文件迁移台账。旧 project_file 记录和对象不删除，
    # 迁移记录保存稳定来源、目标知识库记录、摄取版本、状态与失败原因；
    # 对话解除项目归属前保存一份只读关联审计。目标删除墓碑按来源文件
    # 持久化，阻止重跑、恢复或迟到任务重新创建目标。
    33: [
        """
        CREATE TABLE learning_project_migration_runs (
            run_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            status TEXT NOT NULL
                CHECK (status IN ('running', 'completed', 'failed')),
            compatibility_window TEXT NOT NULL DEFAULT 'issue-02-v1',
            total_bytes INTEGER NOT NULL DEFAULT 0,
            completed_bytes INTEGER NOT NULL DEFAULT 0,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            updated_at TEXT NOT NULL,
            UNIQUE (account_id)
        )
        """,
        """
        CREATE INDEX idx_learning_project_migration_runs_status
        ON learning_project_migration_runs(account_id, status)
        """,
        """
        CREATE TABLE learning_project_migrations (
            migration_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            account_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            project_name TEXT NOT NULL,
            source_document_id TEXT NOT NULL,
            source_object_id TEXT NOT NULL,
            source_filename TEXT NOT NULL,
            source_media_type TEXT NOT NULL,
            source_content_hash TEXT NOT NULL,
            source_content_length INTEGER NOT NULL,
            source_created_at TEXT NOT NULL,
            target_document_id TEXT,
            target_object_id TEXT,
            target_filename TEXT,
            ingestion_version TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN (
                    'pending', 'processing', 'waiting_ingestion', 'completed',
                    'failed', 'source_deleted', 'target_deleted'
                )),
            failure_stage TEXT,
            failure_reason TEXT,
            retry_count INTEGER NOT NULL DEFAULT 0,
            last_attempt_at TEXT,
            completed_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (account_id, source_document_id),
            UNIQUE (account_id, source_object_id)
        )
        """,
        """
        CREATE INDEX idx_learning_project_migrations_run_status
        ON learning_project_migrations(account_id, run_id, status)
        """,
        """
        CREATE INDEX idx_learning_project_migrations_target
        ON learning_project_migrations(account_id, target_object_id)
        """,
        """
        CREATE TABLE learning_project_migration_conversations (
            audit_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            account_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            project_name TEXT NOT NULL,
            detached_at TEXT NOT NULL,
            UNIQUE (account_id, conversation_id)
        )
        """,
        """
        CREATE INDEX idx_learning_project_migration_conversations_account
        ON learning_project_migration_conversations(account_id, project_id)
        """,
        """
        CREATE TABLE learning_project_migration_tombstones (
            tombstone_id TEXT PRIMARY KEY,
            migration_id TEXT NOT NULL,
            account_id TEXT NOT NULL,
            source_document_id TEXT NOT NULL,
            source_content_hash TEXT NOT NULL,
            target_document_id TEXT,
            target_object_id TEXT,
            deleted_at TEXT NOT NULL,
            reason TEXT NOT NULL,
            UNIQUE (account_id, source_document_id)
        )
        """,
        """
        UPDATE skill_packages
        SET status = 'disabled',
            failure_reason = COALESCE(failure_reason, 'user extensions retired'),
            updated_at = datetime('now')
        WHERE status <> 'disabled'
        """,
        """
        UPDATE mcp_servers
        SET status = 'disabled',
            enabled = 0,
            failure_reason = COALESCE(failure_reason, 'user extensions retired'),
            updated_at = datetime('now')
        WHERE status <> 'disabled' OR enabled <> 0
        """,
        """
        UPDATE conversations SET plugin_selection = NULL
        WHERE plugin_selection IS NOT NULL
        """,
        """
        UPDATE mcp_calls
        SET status = 'failed',
            error_code = 'user_extensions_retired',
            error_message = 'user extensions retired'
        WHERE status IN ('pending', 'running', 'claimed', 'sensitive_pending', 'awaiting_confirmation')
        """,
        """
        CREATE TABLE IF NOT EXISTS extension_retirement (
            retirement_id INTEGER PRIMARY KEY CHECK (retirement_id = 1),
            status TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        INSERT INTO extension_retirement(retirement_id, status, updated_at)
        VALUES (1, 'completed', datetime('now'))
        ON CONFLICT(retirement_id) DO UPDATE SET
            status = excluded.status,
            updated_at = excluded.updated_at
        """,
    # Issue 14：四维画像 expand/migrate 投影。旧画像表、旧枚举和旧读写
    # 路径保留；新表只承载四维目标、教学域内部交接、legacy 封存与无正文
    # 迁移报告，所有账户域查询由仓库层通过 scoped() 强制隔离。
        """
        CREATE TABLE profile_four_dimension_records (
            record_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            dimension TEXT NOT NULL,
            label TEXT NOT NULL,
            content TEXT NOT NULL,
            first_stable_recorded_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'active',
            source_record_id TEXT NOT NULL,
            source_version INTEGER NOT NULL,
            content_hash TEXT NOT NULL,
            write_origin TEXT NOT NULL,
            migration_version TEXT NOT NULL,
            UNIQUE (account_id, source_record_id, dimension)
        )
        """,
        """
        CREATE INDEX idx_profile_four_dimension_account
        ON profile_four_dimension_records(account_id, dimension, first_stable_recorded_at)
        """,
        """
        CREATE TABLE profile_four_dimension_learning_records (
            record_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            source_record_id TEXT NOT NULL,
            source_version INTEGER NOT NULL,
            content_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (account_id, source_record_id)
        )
        """,
        """
        CREATE INDEX idx_profile_four_dimension_learning_account
        ON profile_four_dimension_learning_records(account_id, created_at DESC)
        """,
        """
        CREATE TABLE profile_four_dimension_legacy (
            archive_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            source_record_id TEXT NOT NULL,
            source_dimension TEXT NOT NULL,
            content TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (account_id, source_record_id)
        )
        """,
        """
        CREATE INDEX idx_profile_four_dimension_legacy_account
        ON profile_four_dimension_legacy(account_id, created_at DESC)
        """,
        """
        CREATE TABLE profile_four_dimension_migrations (
            report_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            migration_version TEXT NOT NULL,
            status TEXT NOT NULL,
            four_dimension_migrated INTEGER NOT NULL,
            teaching_records_migrated INTEGER NOT NULL,
            legacy_preserved INTEGER NOT NULL,
            skipped INTEGER NOT NULL,
            failed INTEGER NOT NULL,
            stable_record_ids_json TEXT NOT NULL DEFAULT '[]',
            failure_codes_json TEXT NOT NULL DEFAULT '[]',
            retryable INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX idx_profile_four_dimension_migrations_account
        ON profile_four_dimension_migrations(account_id, created_at DESC)
        """,
    # Issue 05：模式在首条用户消息提交时锁定。新建空会话保留
    # mode_locked=0；升级时所有存量会话保留当前 mode 并标记已锁定，历史
    # mode_events 只读保留，后续写路径不再产生新的模式事件。
    ],
    34: [
        """
        ALTER TABLE conversations ADD COLUMN mode_locked INTEGER NOT NULL DEFAULT 0
        """,
        """
        UPDATE conversations SET mode_locked = 1
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
                    if version == 34:
                        self._validate_conversation_modes_for_lock()
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

    def _validate_conversation_modes_for_lock(self) -> None:
        """拒绝非法历史模式，避免迁移时猜测人格合同。"""
        rows = self._connection.execute(
            "SELECT DISTINCT mode FROM conversations"
        ).fetchall()
        invalid_modes = sorted(
            str(row["mode"])
            for row in rows
            if str(row["mode"]) not in {"companion", "study"}
        )
        if invalid_modes:
            logger.error(
                "conversation_mode_migration_failed",
                extra={"invalid_mode_count": len(invalid_modes)},
            )
            raise StorageError(
                "会话模式迁移失败：发现非法历史模式，请先修复数据后重试。"
            )

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

    @contextmanager
    def snapshot_lock(self) -> Iterator[None]:
        """受控一致性点：持有数据库锁供快照与对象文件复制使用。

        SQLite 在线备份 API 不能运行在任何事务内，因此这里只持有 RLock
        串行化全部连接操作（含事务），不打开事务本身；调用方在锁内完成
        :meth:`snapshot_to` 与对象文件复制，即可获得数据库、对象与索引
        之间的一致快照点（Issue 37 备份合同）。
        """
        with self._lock:
            yield

    def snapshot_to(self, target: str | Path) -> None:
        """把当前数据库的一致性快照写入 ``target``（含全部表与索引）。

        在锁内（调用方持有 :meth:`snapshot_lock`）用 SQLite 在线备份 API
        复制：WAL 模式下读取一致性视图，FTS 与向量索引随库一并复制；
        ``target`` 为全新文件路径，绝不改写源数据库。
        """
        try:
            target_connection = sqlite3.connect(str(target))
            try:
                self._connection.backup(target_connection)
            finally:
                target_connection.close()
        except sqlite3.Error as exc:
            raise StorageError(
                "数据库快照创建失败，请检查目标路径与磁盘空间。"
            ) from exc

    def reopen(self) -> None:
        """关闭并重开底层连接（恢复流程替换文件后重建连接）。

        恢复用原子替换把快照移入正式路径，既有连接句柄仍指向已改名的
        旧文件；重开后连接指向新文件并重新应用运行期 PRAGMA，外部调用方
        持有的 ``scoped``/``connection`` 引用在下次使用时访问新文件。
        """
        with self._lock:
            self._connection.close()
            try:
                self._connection = sqlite3.connect(
                    self.path,
                    timeout=10.0,
                    check_same_thread=False,
                    isolation_level=None,
                )
            except sqlite3.Error as exc:
                raise StorageError(
                    "恢复后无法重新打开数据库文件，请检查数据目录权限。"
                ) from exc
            self._connection.row_factory = sqlite3.Row
            self._configure()

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
        # SQLite 连接在应用内共享；串行化 execute，避免读请求与提交事务并发触发
        # sqlite3.InterfaceError。事务内部可重入此锁。
        with self._db.snapshot_lock():
            return self._db.connection.execute(sql, params)
