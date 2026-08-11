"""账户数据目录（Issue 37）：导出类别、删除顺序与逻辑摘要。

「账户数据」= bridges.db 中全部带 ``account_id`` 列的业务表（38 张，
Issue 05-36 各域交付）；派生数据（FTS/向量/解析缓存/索引版本）由
``document_records`` 状态重建，不参与导出但参与删除与备份快照。
本模块是导出、删除、备份与恢复四者的单一事实源：新增表时只需在此
登记，四处行为自动一致。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bridges.storage.database import BridgesDatabase
from bridges.storage.errors import StorageError

#: 全部账户数据表 = 删除顺序表（同一清单，单一事实源）。
#: 不含系统级 schema_meta 与 account_deletions 状态表。
ACCOUNT_TABLES: tuple[str, ...] = (
    "web_search_cache",
    "learning_progress",
    "teaching_plan_adjustments",
    "teaching_next_actions",
    "teaching_assessments",
    "teaching_attempts",
    "teaching_quizzes",
    "teaching_lessons",
    "teaching_plans",
    "fts_chunks",
    "index_vectors",
    "index_active",
    "document_chunks",
    "learning_project_migration_tombstones",
    "learning_project_migrations",
    "learning_project_migration_conversations",
    "learning_project_migration_runs",
    "document_records",
    "index_versions",
    "document_parse_cache",
    "message_citations",
    "retrieval_rounds",
    "chat_attachment_cancellations",
    "chat_attachments",
    "mode_events",
    "answer_feedback",
    "image_tasks",
    "image_versions",
    "image_assets",
    "video_tasks",
    "video_assets",
    "reminder_deliveries",
    "reminder_settings",
    "reminders",
    "skill_packages",
    "account_skill_states",
    "mcp_calls",
    "mcp_servers",
    "profile_observations",
    "profile_candidates",
    "profile_assertion_versions",
    "profile_assertions",
    "profile_slices",
    "profile_permissions",
    "profile_notifications",
    "profile_four_dimension_records",
    "profile_four_dimension_learning_records",
    "profile_four_dimension_legacy",
    "profile_four_dimension_migrations",
    "profile_extraction_observations",
    "profile_extraction_tasks",
    "profile_extraction_runs",
    "profile_privacy_disclosures",
    "profile_extraction_tombstones",
    "messages",
    "conversations",
    "model_run_locks",
    "learning_projects",
    "objects",
    "accounts",
)

#: 删除顺序：子表先于父表（外键无 CASCADE，learning_projects 例外，
#: 但统一显式删除保证不依赖 CASCADE）；ftts/索引/缓存等派生表先行。
#: 与 ACCOUNT_TABLES 为同一清单（保持单一事实源）。
DELETION_ORDER = ACCOUNT_TABLES

#: 导出类别：每个类别聚合若干表，逐类提供中文名与每行预计字节
#: （预览用估算；派生索引/缓存/审计不入导出——数据可读且机器可处理，
#: 对象只出元数据清单）。
#: 行估计为保守常量（文本列与 JSON 列主导），仅供「确认前可见预计大小」。
@dataclass(frozen=True)
class ExportCategory:
    key: str
    label: str
    tables: tuple[str, ...]
    row_estimate_bytes: int


EXPORT_CATEGORIES: tuple[ExportCategory, ...] = (
    ExportCategory("conversations", "对话", ("conversations",), 256),
    ExportCategory("messages", "消息", ("messages",), 1024),
    ExportCategory("mode_events", "模式切换事件", ("mode_events",), 128),
    ExportCategory("model_run_locks", "模型运行记录", ("model_run_locks",), 512),
    ExportCategory("chat_attachments", "对话附件", ("chat_attachments",), 256),
    ExportCategory("answer_feedback", "回答反馈", ("answer_feedback",), 512),
    ExportCategory(
        "teaching_progress",
        "对话教学进度",
        (
            "learning_progress",
            "teaching_plans",
            "teaching_lessons",
            "teaching_quizzes",
            "teaching_attempts",
            "teaching_assessments",
            "teaching_next_actions",
            "teaching_plan_adjustments",
        ),
        1024,
    ),
    ExportCategory(
        "profile",
        "画像与版本",
        (
            "profile_observations",
            "profile_candidates",
            "profile_assertions",
            "profile_assertion_versions",
            "profile_slices",
            "profile_permissions",
            "profile_notifications",
            "profile_four_dimension_records",
            "profile_four_dimension_learning_records",
            "profile_four_dimension_legacy",
            "profile_four_dimension_migrations",
            "profile_extraction_observations",
            "profile_extraction_tasks",
            "profile_extraction_runs",
            "profile_privacy_disclosures",
            "profile_extraction_tombstones",
        ),
        512,
    ),
    ExportCategory("learning_projects", "历史归档材料", ("learning_projects",), 256),
    ExportCategory(
        "learning_project_migration",
        "迁移审计",
        (
            "learning_project_migration_runs",
            "learning_project_migrations",
            "learning_project_migration_conversations",
            "learning_project_migration_tombstones",
        ),
        512,
    ),
    ExportCategory(
        "reminders",
        "历史投递审计",
        ("reminders", "reminder_deliveries", "reminder_settings"),
        512,
    ),
    ExportCategory(
        "plugins", "扩展停用审计", ("skill_packages", "account_skill_states"), 512
    ),
    ExportCategory("mcp", "扩展调用审计", ("mcp_servers", "mcp_calls"), 512),
    ExportCategory(
        "documents", "文档与检索内容", ("document_records", "document_chunks"), 1024
    ),
    ExportCategory("objects", "资产清单", ("objects",), 256),
    ExportCategory(
        "retrieval", "检索与引用", ("retrieval_rounds", "message_citations"), 256
    ),
)


def _scoped_row_counts(
    database: BridgesDatabase, account_id: str, tables: tuple[str, ...]
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in tables:
        row = database.scoped(account_id).execute(
            f"SELECT COUNT(*) AS count FROM {table} WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        counts[table] = int(row["count"])
    return counts


def category_preview(
    database: BridgesDatabase, account_id: str
) -> list[tuple[ExportCategory, int, int]]:
    """返回 [(类别, 条数, 预计字节)]：预览确认前可见范围与预计大小。"""
    result: list[tuple[ExportCategory, int, int]] = []
    for category in EXPORT_CATEGORIES:
        counts = _scoped_row_counts(database, account_id, category.tables)
        total = sum(counts.values())
        estimated = sum(
            counts[table] * category.row_estimate_bytes for table in category.tables
        )
        result.append((category, total, estimated))
    return result


def export_rows(database: BridgesDatabase, account_id: str, table: str) -> list[dict[str, Any]]:
    """导出一张表的全部账户行（sqlite3.Row → dict，列名稳定）。"""
    rows = database.scoped(account_id).execute(
        f"SELECT * FROM {table} WHERE account_id = ? ORDER BY rowid",
        (account_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def delete_account_rows(database: BridgesDatabase, account_id: str) -> None:
    """在单个事务内按依赖顺序删除账户的全部数据行（含 accounts 行）。

    事务失败整体回滚（零副作用），成功即「已撤权数据不可被聊天、搜索或
    插件读取」——所有读取都经 scoped 账户过滤，账户行消失后一律为空。
    """
    try:
        with database.transaction():
            for table in DELETION_ORDER:
                database.scoped(account_id).execute(
                    f"DELETE FROM {table} WHERE account_id = ?", (account_id,)
                )
    except StorageError:
        raise
    except Exception as exc:  # noqa: BLE001 - 统一中文错误
        raise StorageError(
            "账户数据删除失败，数据库未发生任何改动。"
        ) from exc


def logical_summary(
    database: BridgesDatabase, account_id: str
) -> dict[str, int]:
    """账户数据的逻辑摘要（各类别条数），用于导出/恢复一致性比对。

    只统计业务表（不含派生索引/缓存与系统状态），摘要键 = 表名。
    """
    tables = (
        "conversations",
        "messages",
        "mode_events",
        "model_run_locks",
        "chat_attachments",
        "answer_feedback",
        "learning_progress",
        "teaching_plans",
        "teaching_lessons",
        "teaching_quizzes",
        "teaching_attempts",
        "teaching_assessments",
        "teaching_next_actions",
        "teaching_plan_adjustments",
        "profile_observations",
        "profile_candidates",
        "profile_assertions",
        "profile_assertion_versions",
        "profile_slices",
        "profile_permissions",
        "profile_notifications",
        "profile_four_dimension_records",
        "profile_four_dimension_learning_records",
        "profile_four_dimension_legacy",
        "profile_four_dimension_migrations",
        "profile_extraction_observations",
        "profile_extraction_tasks",
        "profile_extraction_runs",
        "profile_privacy_disclosures",
        "profile_extraction_tombstones",
        "learning_projects",
        "learning_project_migration_runs",
        "learning_project_migrations",
        "learning_project_migration_conversations",
        "learning_project_migration_tombstones",
        "reminders",
        "reminder_deliveries",
        "reminder_settings",
        "skill_packages",
        "account_skill_states",
        "mcp_servers",
        "mcp_calls",
        "document_records",
        "document_chunks",
        "objects",
        "retrieval_rounds",
        "message_citations",
    )
    counts = _scoped_row_counts(database, account_id, tables)
    return {table: counts[table] for table in tables}


def global_stats(database: BridgesDatabase) -> dict[str, int]:
    """全库数据统计（备份清单用）：账户数/对话数/消息数/对象数等。"""
    stats: dict[str, int] = {}
    for table in ("accounts", "conversations", "messages", "objects"):
        row = database.connection.execute(
            f"SELECT COUNT(*) AS count FROM {table}"
        ).fetchone()
        stats[table] = int(row["count"])
    return stats
