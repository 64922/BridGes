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
