"""跨内容统一桌面搜索的只读仓库（Issue 24）。

所有查询都通过 ``db.scoped(account_id)`` 执行：SQL 层强制账户过滤，
跨账户内容从"约定"升级为"不可能"。全部为实时 SQL（无新表、无缓存），
改名/删除/项目移动后下一次查询即反映。

正文检索取舍：文档与图片元数据分块使用参数化 LIKE 子串匹配（含 ``%``、
``_``、``\\`` 转义），而不是复用 FTS5 trigram 索引。原因：FTS5 trigram
要求查询至少 3 个字符且依赖活跃索引版本，统一搜索需要与标题/文件名
一致的任意长度子串语义，并在索引重建期间仍给出可用的名称级结果；
桌面单用户数据规模下 LIKE 全表扫描性能足够。
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from bridges.storage import BridgesDatabase

#: LIKE 转义符（配合 ``ESCAPE '\'`` 子句）。
_LIKE_ESCAPE = "\\"
#: ISO 时间文本的秒级前缀长度（'YYYY-MM-DDTHH:MM:SS'），时间筛选按此前缀比较。
_SECONDS_PREFIX_LEN = 19


def like_pattern(query: str) -> str:
    """把用户查询转成安全的 LIKE 子串模式：转义通配符后两端加 ``%``。"""
    escaped = (
        query.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
        .replace("%", _LIKE_ESCAPE + "%")
        .replace("_", _LIKE_ESCAPE + "_")
    )
    return f"%{escaped}%"


def _iso(value: datetime) -> str:
    """把 incoming 筛选时间规范化为 UTC 秒级前缀（'YYYY-MM-DDTHH:MM:SS'）。

    已核查库存 ``updated_at`` 的全部写入路径：``storage/repository`` 与
    ``ingestion`` 写 ``isoformat(timespec="seconds")``，``chat/repository``
    与 ``learning_projects`` 写 ``isoformat()``（带微秒）——格式并不恒定，
    但全部写入都是 ``datetime.now(UTC)``（``+00:00`` 偏移）。因此两侧统一
    截断到秒级前缀再按字典序比较（此时字典序即时间序），避免 incoming
    带微秒（``.000``）、``Z`` 结尾或不同偏移时字典序偏离时间序、边界秒的
    记录被误排除/误包含。无偏移输入按 UTC 解释；同秒记录对 from/to 均按
    包含处理（秒级粒度筛选语义）。
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="seconds")[:_SECONDS_PREFIX_LEN]


class SearchRepository:
    """账户作用域的只读搜索查询集合。"""

    def __init__(self, database: BridgesDatabase) -> None:
        self._database = database

    # ------------------------------------------------------------------
    # 聊天：会话标题 + 消息正文
    # ------------------------------------------------------------------

    def conversation_title_hits(
        self,
        account_id: str,
        pattern: str,
        *,
        project_id: str | None,
        from_: datetime | None,
        to: datetime | None,
    ) -> list[sqlite3.Row]:
        sql = (
            "SELECT conversation_id, title, project_id, updated_at"
            " FROM conversations"
            " WHERE account_id = ? AND title LIKE ? ESCAPE '\\'"
        )
        params: list[object] = [account_id, pattern]
        sql, params = _apply_filters(sql, params, "project_id", "updated_at", project_id, from_, to)
        return self._database.scoped(account_id).execute(
            f"{sql} ORDER BY updated_at DESC, conversation_id", params
        ).fetchall()

    def message_hits(
        self,
        account_id: str,
        pattern: str,
        *,
        project_id: str | None,
        from_: datetime | None,
        to: datetime | None,
    ) -> list[sqlite3.Row]:
        sql = (
            "SELECT m.message_id, m.conversation_id, m.content, m.updated_at,"
            " c.title AS conversation_title, c.project_id AS project_id"
            " FROM messages m"
            " JOIN conversations c ON c.conversation_id = m.conversation_id"
            " WHERE m.account_id = ? AND m.content LIKE ? ESCAPE '\\'"
        )
        params: list[object] = [account_id, pattern]
        sql, params = _apply_filters(
            sql, params, "c.project_id", "m.updated_at", project_id, from_, to
        )
        return self._database.scoped(account_id).execute(
            f"{sql} ORDER BY m.updated_at DESC, m.message_id", params
        ).fetchall()

    # ------------------------------------------------------------------
    # 文档：名称 + 正文分块（排除图片对象，图片单独成类）
    # ------------------------------------------------------------------

    def document_name_hits(
        self,
        account_id: str,
        pattern: str,
        *,
        project_id: str | None,
        from_: datetime | None,
        to: datetime | None,
    ) -> list[sqlite3.Row]:
        sql = (
            "SELECT r.document_id, r.object_id, r.title, r.updated_at, r.project_id,"
            " o.original_filename"
            " FROM document_records r"
            " JOIN objects o ON o.object_id = r.object_id"
            " WHERE r.account_id = ? AND o.status = 'active'"
            " AND o.media_type NOT LIKE 'image/%'"
            " AND (r.title LIKE ? ESCAPE '\\' OR o.original_filename LIKE ? ESCAPE '\\')"
        )
        params: list[object] = [account_id, pattern, pattern]
        sql, params = _apply_filters(
            sql, params, "r.project_id", "r.updated_at", project_id, from_, to
        )
        return self._database.scoped(account_id).execute(
            f"{sql} ORDER BY r.updated_at DESC, r.document_id", params
        ).fetchall()

    def document_chunk_hits(
        self,
        account_id: str,
        pattern: str,
        *,
        project_id: str | None,
        from_: datetime | None,
        to: datetime | None,
    ) -> list[sqlite3.Row]:
        # 按文档与分块序排列，服务层每份文档取第一处命中分块作锚点。
        sql = (
            "SELECT r.document_id, r.object_id, r.title, r.updated_at, r.project_id,"
            " o.original_filename, c.content, c.page_number, c.section_title"
            " FROM document_chunks c"
            " JOIN document_records r ON r.document_id = c.document_id"
            " JOIN objects o ON o.object_id = r.object_id"
            " WHERE c.account_id = ? AND r.status = 'ready' AND o.status = 'active'"
            " AND o.media_type NOT LIKE 'image/%'"
            " AND c.content LIKE ? ESCAPE '\\'"
        )
        params: list[object] = [account_id, pattern]
        sql, params = _apply_filters(
            sql, params, "r.project_id", "r.updated_at", project_id, from_, to
        )
        return self._database.scoped(account_id).execute(
            f"{sql} ORDER BY r.document_id, c.chunk_index", params
        ).fetchall()

    # ------------------------------------------------------------------
    # 图片：文件名 + 摄取元数据分块（图像生成功能未交付，检索面即上传图片）
    # ------------------------------------------------------------------

    def image_name_hits(
        self,
        account_id: str,
        pattern: str,
        *,
        project_id: str | None,
        from_: datetime | None,
        to: datetime | None,
    ) -> list[sqlite3.Row]:
        sql = (
            "SELECT o.object_id, o.original_filename, o.updated_at"
            " FROM objects o"
            " WHERE o.account_id = ? AND o.status = 'active'"
            " AND o.media_type LIKE 'image/%'"
            " AND o.original_filename LIKE ? ESCAPE '\\'"
        )
        params: list[object] = [account_id, pattern]
        sql, params = _apply_filters(sql, params, None, "o.updated_at", project_id, from_, to)
        if project_id is not None:
            # 图片对象本身不直接归属项目；项目筛选只保留经摄取记录归属该项目的图片。
            sql += (
                " AND EXISTS (SELECT 1 FROM document_records dr"
                " WHERE dr.account_id = o.account_id AND dr.object_id = o.object_id"
                " AND dr.project_id = ?)"
            )
            params.append(project_id)
        return self._database.scoped(account_id).execute(
            f"{sql} ORDER BY o.updated_at DESC, o.object_id", params
        ).fetchall()

    def image_chunk_hits(
        self,
        account_id: str,
        pattern: str,
        *,
        project_id: str | None,
        from_: datetime | None,
        to: datetime | None,
    ) -> list[sqlite3.Row]:
        sql = (
            "SELECT o.object_id, o.original_filename, o.updated_at, c.content,"
            " r.project_id"
            " FROM document_chunks c"
            " JOIN document_records r ON r.document_id = c.document_id"
            " JOIN objects o ON o.object_id = r.object_id"
            " WHERE c.account_id = ? AND r.status = 'ready' AND o.status = 'active'"
            " AND o.media_type LIKE 'image/%'"
            " AND c.content LIKE ? ESCAPE '\\'"
        )
        params: list[object] = [account_id, pattern]
        sql, params = _apply_filters(
            sql, params, "r.project_id", "r.updated_at", project_id, from_, to
        )
        return self._database.scoped(account_id).execute(
            f"{sql} ORDER BY o.object_id, c.chunk_index", params
        ).fetchall()

    # ------------------------------------------------------------------
    # 学习项目：名称 + 描述
    # ------------------------------------------------------------------

    def project_hits(
        self,
        account_id: str,
        pattern: str,
        *,
        project_id: str | None,
        from_: datetime | None,
        to: datetime | None,
    ) -> list[sqlite3.Row]:
        # 项目筛选对项目类结果同样生效：只保留该项目自身（若名称/描述命中）。
        sql = (
            "SELECT project_id, name, description, updated_at"
            " FROM learning_projects"
            " WHERE account_id = ?"
            " AND (name LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\')"
        )
        params: list[object] = [account_id, pattern, pattern]
        sql, params = _apply_filters(
            sql, params, "project_id", "updated_at", project_id, from_, to
        )
        return self._database.scoped(account_id).execute(
            f"{sql} ORDER BY updated_at DESC, project_id", params
        ).fetchall()

    # ------------------------------------------------------------------
    # 归属校验与索引就绪信号
    # ------------------------------------------------------------------

    def project_exists(self, account_id: str, project_id: str) -> bool:
        """项目是否属于当前账户（跨账户与不存在同样为 False，不泄漏归属）。"""
        row = self._database.scoped(account_id).execute(
            "SELECT 1 AS ok FROM learning_projects"
            " WHERE account_id = ? AND project_id = ?",
            (account_id, project_id),
        ).fetchone()
        return row is not None

    def index_ready(self, account_id: str) -> bool:
        """账户索引是否就绪：无待处理/处理中文档、无构建中版本，且已有
        就绪文档时存在活跃索引版本（无任何文档的账户视为就绪）。"""
        scoped = self._database.scoped(account_id)
        pending = scoped.execute(
            "SELECT COUNT(*) AS count FROM document_records"
            " WHERE account_id = ? AND status IN ('queued', 'parsing', 'processing')",
            (account_id,),
        ).fetchone()
        building = scoped.execute(
            "SELECT COUNT(*) AS count FROM index_versions"
            " WHERE account_id = ? AND status = 'building'",
            (account_id,),
        ).fetchone()
        ready_docs = scoped.execute(
            "SELECT COUNT(*) AS count FROM document_records"
            " WHERE account_id = ? AND status = 'ready'",
            (account_id,),
        ).fetchone()
        active = scoped.execute(
            "SELECT 1 AS ok FROM index_active WHERE account_id = ? LIMIT 1",
            (account_id,),
        ).fetchone()
        assert pending is not None and building is not None and ready_docs is not None
        if int(pending["count"]) > 0 or int(building["count"]) > 0:
            return False
        return int(ready_docs["count"]) == 0 or active is not None


def _apply_filters(
    sql: str,
    params: list[object],
    project_column: str | None,
    time_column: str,
    project_id: str | None,
    from_: datetime | None,
    to: datetime | None,
) -> tuple[str, list[object]]:
    """拼接项目归属与时间范围筛选（参数化，列名由调用方固定给出）。

    时间比较两侧都截断到秒级前缀：库存时间格式不恒定（部分表带微秒），
    但全部为 UTC，秒级前缀的字典序即时间序（见 :func:`_iso` 注释）。
    """
    if project_id is not None and project_column is not None:
        sql += f" AND {project_column} = ?"
        params.append(project_id)
    if from_ is not None:
        sql += f" AND substr({time_column}, 1, {_SECONDS_PREFIX_LEN}) >= ?"
        params.append(_iso(from_))
    if to is not None:
        sql += f" AND substr({time_column}, 1, {_SECONDS_PREFIX_LEN}) <= ?"
        params.append(_iso(to))
    return sql, params
