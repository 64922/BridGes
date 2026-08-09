"""检索轮次与引用的持久化（Issue 20）。

``retrieval_rounds`` 固化每轮检索的作用域与充足性信号；``message_citations``
固化最终引用的展示数据（文件名/页码/章节/片段），生成后不随索引重建
漂移。所有查询经账户作用域面，跨账户访问在 SQL 层被强制拒绝。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import cast

from bridges.storage.database import BridgesDatabase

#: 轮次表列顺序（插入与读取共享，保持单一来源）。
_ROUND_COLUMNS = (
    "round_id, message_id, user_message_id, conversation_id, account_id,"
    " use_knowledge_base, sufficiency, index_version_id, layers_json, note, created_at"
)
#: 引用表列顺序。
_CITATION_COLUMNS = (
    "citation_id, round_id, message_id, conversation_id, account_id,"
    " source_layer, object_id, filename, media_type, page_number, section_title,"
    " snippet, chunk_id, rank, created_at"
)


class RetrievalRepository:
    """检索轮次与引用的账户作用域读写。"""

    def __init__(self, database: BridgesDatabase) -> None:
        self._database = database

    # ------------------------------------------------------------------
    # Issue 12：决策快照
    # ------------------------------------------------------------------

    @staticmethod
    def _decision_select() -> str:
        return (
            "SELECT decision_id, assistant_message_id, user_message_id,"
            " conversation_id, action, reason, rules_version, capability_route,"
            " mode, query_fingerprint, created_at FROM retrieval_decisions"
        )

    def insert_decision(
        self,
        *,
        account_id: str,
        decision_id: str,
        assistant_message_id: str,
        user_message_id: str | None,
        conversation_id: str,
        action: str,
        reason: str,
        rules_version: str,
        capability_route: str,
        mode: str,
        query_fingerprint: str,
        created_at: datetime,
    ) -> None:
        """写入一份回合决策；同一用户回合由数据库唯一约束去重。"""
        self._database.scoped(account_id).execute(
            "INSERT INTO retrieval_decisions"
            " (decision_id, assistant_message_id, user_message_id, conversation_id,"
            " account_id, action, reason, rules_version, capability_route, mode,"
            " query_fingerprint, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                decision_id,
                assistant_message_id,
                user_message_id,
                conversation_id,
                account_id,
                action,
                reason,
                rules_version,
                capability_route,
                mode,
                query_fingerprint,
                created_at.isoformat(timespec="seconds"),
            ),
        )

    def decision_row(
        self,
        account_id: str,
        *,
        assistant_message_id: str | None = None,
        user_message_id: str | None = None,
    ) -> sqlite3.Row | None:
        """按当前账户读取决策；用户消息键优先用于重试复用。"""
        clauses = ["account_id = ?"]
        params: list[object] = [account_id]
        if user_message_id is not None:
            clauses.append("user_message_id = ?")
            params.append(user_message_id)
        elif assistant_message_id is not None:
            clauses.append("assistant_message_id = ?")
            params.append(assistant_message_id)
        else:
            return None
        row = self._database.scoped(account_id).execute(
            self._decision_select() + " WHERE " + " AND ".join(clauses), params
        ).fetchone()
        return cast(sqlite3.Row | None, row)

    def decision_row_for_message(
        self, account_id: str, assistant_message_id: str
    ) -> sqlite3.Row | None:
        """读取助手尝试对应的决策，重试时沿 generation_runs 回溯用户回合。"""
        row = self.decision_row(
            account_id, assistant_message_id=assistant_message_id
        )
        if row is not None:
            return row
        row = self._database.scoped(account_id).execute(
            "SELECT retrieval_decisions.decision_id,"
            " retrieval_decisions.assistant_message_id,"
            " retrieval_decisions.user_message_id,"
            " retrieval_decisions.conversation_id, retrieval_decisions.action,"
            " retrieval_decisions.reason, retrieval_decisions.rules_version,"
            " retrieval_decisions.capability_route, retrieval_decisions.mode,"
            " retrieval_decisions.query_fingerprint, retrieval_decisions.created_at"
            " FROM retrieval_decisions"
            + " JOIN generation_runs g ON g.account_id = retrieval_decisions.account_id"
            " AND g.user_message_id = retrieval_decisions.user_message_id"
            " WHERE retrieval_decisions.account_id = ?"
            " AND g.assistant_message_id = ? LIMIT 1",
            (account_id, assistant_message_id),
        ).fetchone()
        return cast(sqlite3.Row | None, row)

    def insert_round(
        self,
        *,
        account_id: str,
        round_id: str,
        message_id: str,
        user_message_id: str | None,
        conversation_id: str,
        use_knowledge_base: bool,
        sufficiency: str,
        index_version_id: str | None,
        layers: list[dict[str, object]],
        note: str | None,
        created_at: datetime,
    ) -> None:
        """写入一轮检索（调用方须在事务内）。"""
        self._database.scoped(account_id).execute(
            "INSERT INTO retrieval_rounds"
            f" ({_ROUND_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                round_id,
                message_id,
                user_message_id,
                conversation_id,
                account_id,
                1 if use_knowledge_base else 0,
                sufficiency,
                index_version_id,
                json.dumps(layers, ensure_ascii=False),
                note,
                created_at.isoformat(timespec="seconds"),
            ),
        )

    def insert_citation(
        self,
        *,
        account_id: str,
        citation_id: str,
        round_id: str,
        message_id: str,
        conversation_id: str,
        source_layer: str,
        object_id: str,
        filename: str,
        media_type: str,
        page_number: int | None,
        section_title: str | None,
        snippet: str,
        chunk_id: str,
        rank: int,
        created_at: datetime,
    ) -> None:
        """写入一条最终引用（调用方须在事务内）。"""
        self._database.scoped(account_id).execute(
            "INSERT INTO message_citations"
            f" ({_CITATION_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                citation_id,
                round_id,
                message_id,
                conversation_id,
                account_id,
                source_layer,
                object_id,
                filename,
                media_type,
                page_number,
                section_title,
                snippet,
                chunk_id,
                rank,
                created_at.isoformat(timespec="seconds"),
            ),
        )

    def round_row(self, account_id: str, message_id: str) -> sqlite3.Row | None:
        """返回消息绑定的检索轮次行；无轮次返回 None。"""
        row = self._database.scoped(account_id).execute(
            "SELECT round_id, message_id, user_message_id, conversation_id,"
            " use_knowledge_base, sufficiency, index_version_id, layers_json, note,"
            " created_at"
            " FROM retrieval_rounds WHERE account_id = ? AND message_id = ?",
            (account_id, message_id),
        ).fetchone()
        return cast(sqlite3.Row | None, row)

    def round_row_for_user_message(
        self, account_id: str, user_message_id: str
    ) -> sqlite3.Row | None:
        """返回同一用户回合已有的检索轮次，供重试幂等复用。"""
        row = self._database.scoped(account_id).execute(
            "SELECT round_id, message_id, user_message_id, conversation_id,"
            " use_knowledge_base, sufficiency, index_version_id, layers_json, note,"
            " created_at FROM retrieval_rounds"
            " WHERE account_id = ? AND user_message_id = ?"
            " ORDER BY created_at, round_id LIMIT 1",
            (account_id, user_message_id),
        ).fetchone()
        return cast(sqlite3.Row | None, row)

    def generation_user_message_id(
        self, account_id: str, assistant_message_id: str
    ) -> str | None:
        """返回助手尝试所属的用户消息，供重试引用授权校验。"""
        row = self._database.scoped(account_id).execute(
            "SELECT user_message_id FROM generation_runs"
            " WHERE account_id = ? AND assistant_message_id = ? LIMIT 1",
            (account_id, assistant_message_id),
        ).fetchone()
        return str(row["user_message_id"]) if row is not None else None

    def citation_rows(self, account_id: str, round_id: str) -> list[sqlite3.Row]:
        """返回轮次的全部引用行（按融合位次升序）。"""
        return list(
            self._database.scoped(account_id).execute(
                "SELECT citation_id, source_layer, object_id, filename, media_type,"
                " page_number, section_title, snippet, rank"
                " FROM message_citations"
                " WHERE account_id = ? AND round_id = ?"
                " ORDER BY rank, citation_id",
                (account_id, round_id),
            ).fetchall()
        )

    def citation_row(
        self, account_id: str, citation_id: str
    ) -> sqlite3.Row | None:
        """返回单条引用行；跨账户或不存在返回 None。"""
        row = self._database.scoped(account_id).execute(
            "SELECT citation_id, round_id, message_id, conversation_id, source_layer,"
            " object_id, filename, media_type, page_number, section_title, snippet, rank"
            " FROM message_citations WHERE account_id = ? AND citation_id = ?",
            (account_id, citation_id),
        ).fetchone()
        return cast(sqlite3.Row | None, row)

    # ------------------------------------------------------------------
    # 检索域读面（Issue 45 收编：索引/文档/对象的只读查询收进属主仓库）
    # ------------------------------------------------------------------

    def active_version(self, account_id: str) -> sqlite3.Row | None:
        """返回当前账户已激活的索引版本行；无激活版本返回 None。"""
        row = self._database.scoped(account_id).execute(
            "SELECT v.version_id, v.status FROM index_active a"
            " JOIN index_versions v ON v.version_id = a.version_id"
            " WHERE a.account_id = ? AND v.status = 'active'",
            (account_id,),
        ).fetchone()
        return cast(sqlite3.Row | None, row)

    def ready_document_ids(
        self,
        account_id: str,
        *,
        source: str,
        object_ids: list[str] | None = None,
        project_id: str | None = None,
    ) -> list[str]:
        """返回某层已就绪且对象仍活跃的文档标识（只查当前账户资源）。

        ``document_records`` 属检索/摄取域；``objects``（storage 域）仅
        在 JOIN 中作活跃性过滤，不触及其写路径。
        """
        sql = (
            "SELECT r.document_id FROM document_records r"
            " JOIN objects o ON o.object_id = r.object_id"
            " WHERE r.account_id = ? AND r.source = ? AND r.status = 'ready'"
            " AND o.account_id = ? AND o.status = 'active'"
        )
        params: list[object] = [account_id, source, account_id]
        if object_ids is not None:
            placeholders = ",".join("?" for _ in object_ids)
            sql += f" AND r.object_id IN ({placeholders})"
            params.extend(object_ids)
        if project_id is not None:
            sql += " AND r.project_id = ?"
            params.append(project_id)
        rows = self._database.scoped(account_id).execute(sql, params).fetchall()
        return [str(row["document_id"]) for row in rows]

    def document_statuses(
        self,
        account_id: str,
        *,
        source: str,
        object_ids: list[str] | None = None,
        project_id: str | None = None,
    ) -> set[str]:
        """读取当前账户候选材料的摄取状态，用于区分等待与损坏。"""
        clauses = ["r.account_id = ?", "r.source = ?"]
        params: list[object] = [account_id, source]
        if object_ids is not None:
            if not object_ids:
                return set()
            placeholders = ",".join("?" for _ in object_ids)
            clauses.append(f"r.object_id IN ({placeholders})")
            params.extend(object_ids)
        if project_id is not None:
            clauses.append("r.project_id = ?")
            params.append(project_id)
        rows = self._database.scoped(account_id).execute(
            "SELECT DISTINCT r.status FROM document_records r WHERE "
            + " AND ".join(clauses),
            params,
        ).fetchall()
        return {str(row["status"]) for row in rows}

    def document_metadata(
        self,
        account_id: str,
        document_ids: list[str],
        *,
        source: str,
    ) -> list[dict[str, str]]:
        """只读取当前账户候选文件的元数据，不触碰正文或向量。"""
        if not document_ids:
            return []
        placeholders = ",".join("?" for _ in document_ids)
        rows = self._database.scoped(account_id).execute(
            "SELECT r.document_id, r.object_id, o.original_filename, o.media_type"
            " FROM document_records r JOIN objects o ON o.object_id = r.object_id"
            " WHERE r.account_id = ? AND r.source = ? AND r.status = 'ready'"
            " AND o.account_id = ? AND o.status = 'active'"
            f" AND r.document_id IN ({placeholders})"
            " ORDER BY r.created_at, r.document_id",
            (account_id, source, account_id, *document_ids),
        ).fetchall()
        return [
            {
                "document_id": str(row["document_id"]),
                "object_id": str(row["object_id"]),
                "filename": str(row["original_filename"]),
                "media_type": str(row["media_type"]),
            }
            for row in rows
        ]

    def has_stale_documents(
        self,
        account_id: str,
        *,
        source: str,
        object_ids: list[str] | None = None,
        project_id: str | None = None,
    ) -> bool:
        """返回已就绪但等待索引重建的文档状态，供教学证据门闭锁。"""
        sql = (
            "SELECT 1 FROM document_records r"
            " JOIN objects o ON o.object_id = r.object_id"
            " WHERE r.account_id = ? AND r.source = ? AND r.status = 'ready'"
            " AND r.rebuild_requested = 1 AND o.account_id = ? AND o.status = 'active'"
        )
        params: list[object] = [account_id, source, account_id]
        if object_ids is not None:
            placeholders = ",".join("?" for _ in object_ids)
            sql += f" AND r.object_id IN ({placeholders})"
            params.extend(object_ids)
        if project_id is not None:
            sql += " AND r.project_id = ?"
            params.append(project_id)
        return (
            self._database.scoped(account_id).execute(sql + " LIMIT 1", params).fetchone()
            is not None
        )

    def vector_rows(
        self,
        account_id: str,
        version_id: str,
        document_ids: list[str],
    ) -> list[dict[str, object]]:
        """返回指定版本与文档集合的向量分块行（关键词/向量融合候选）。"""
        placeholders = ",".join("?" for _ in document_ids)
        rows = self._database.scoped(account_id).execute(
            "SELECT c.chunk_id, c.document_id, c.content, c.section_title,"
            " c.page_number, r.object_id, r.content_hash,"
            " c.content_hash AS chunk_content_hash, v.vector_json"
            " FROM index_vectors v"
            " JOIN document_chunks c ON c.chunk_id = v.chunk_id"
            " JOIN document_records r ON r.document_id = c.document_id"
            " WHERE v.version_id = ? AND r.account_id = ? AND r.status = 'ready'"
            f" AND r.document_id IN ({placeholders})",
            (version_id, account_id) + tuple(document_ids),
        ).fetchall()
        return [dict(row) for row in rows]

    def round_user_message_id(
        self, account_id: str, round_id: str
    ) -> str | None:
        """返回检索轮次绑定的用户消息标识（附件层授权校验用）。"""
        row = self._database.scoped(account_id).execute(
            "SELECT user_message_id FROM retrieval_rounds"
            " WHERE account_id = ? AND round_id = ?",
            (account_id, round_id),
        ).fetchone()
        if row is None or row["user_message_id"] is None:
            return None
        return str(row["user_message_id"])

    def document_record_exists(
        self,
        account_id: str,
        object_id: str,
        *,
        source: str,
        project_id: str | None = None,
    ) -> bool:
        """文档记录是否存在（引用打开时的授权校验；跨账户返回 False）。"""
        sql = (
            "SELECT 1 FROM document_records WHERE account_id = ?"
            " AND object_id = ? AND source = ?"
        )
        params: list[object] = [account_id, object_id, source]
        if project_id is not None:
            sql += " AND project_id = ?"
            params.append(project_id)
        row = self._database.scoped(account_id).execute(
            sql + " LIMIT 1", params
        ).fetchone()
        return row is not None
