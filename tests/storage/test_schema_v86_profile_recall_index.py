"""画像工单 11：v86 迁移新增按任务召回的派生向量索引。

派生索引只保存条目向量的副本（可重建、不持有事实真相）：随账户删除与
备份快照，但不进入导出类别与逻辑摘要。从 v85 升级后新表可用、重复
初始化幂等。（画像工单 10 先行并入 main 使用了 85，本迁移顺延为 86。）
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from bridges.ai.ports import EmbeddingContext
from bridges.lifecycle.catalog import (
    ACCOUNT_TABLES,
    EXPORT_CATEGORIES,
    delete_account_rows,
    logical_summary,
)
from bridges.profiles.adapters import InMemoryProfileRepository
from bridges.profiles.atomic import (
    AtomicProfileService,
    SqliteAtomicProfileRepository,
)
from bridges.profiles.four_dimensions import (
    FourDimensionProfileService,
    InMemoryFourDimensionProfileRepository,
)
from bridges.profiles.purpose import build_purpose
from bridges.profiles.recall import ProfileRecallIndex
from bridges.storage.database import MIGRATIONS, SCHEMA_VERSION, BridgesDatabase

ALICE = "account-alice"
RECALL_TABLES = (
    "profile_recall_index_entries",
    "profile_recall_index_versions",
)


class _StubEmbeddingPort:
    """固定向量替身：本文件只关心存储与生命周期，不测语义质量。"""

    def embed(
        self,
        account_id: str,
        texts: list[str],
        *,
        context: EmbeddingContext | None = None,
    ) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]


def _build_v85_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        for version in range(1, 86):
            for statement in MIGRATIONS[version]:
                connection.execute(statement)
        connection.execute(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO schema_meta VALUES ('version', '85')")


def test_upgrade_from_v85_adds_recall_index_tables(tmp_path: Path) -> None:
    path = tmp_path / "upgrade-85.db"
    _build_v85_database(path)

    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION
    assert SCHEMA_VERSION == 86

    tables = {
        str(row["name"])
        for row in database.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert set(RECALL_TABLES) <= tables
    indexes = {
        str(row["name"])
        for row in database.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        ).fetchall()
    }
    assert {
        "idx_profile_recall_index_versions_account",
        "idx_profile_recall_index_entries_account",
    } <= indexes

    columns = {
        str(item["name"])
        for item in database.connection.execute(
            "PRAGMA table_info(profile_recall_index_entries)"
        ).fetchall()
    }
    assert {
        "version_id",
        "account_id",
        "profile_item_id",
        "item_version",
        "text_hash",
        "vector_json",
        "updated_at",
    } <= columns
    version_columns = {
        str(item["name"])
        for item in database.connection.execute(
            "PRAGMA table_info(profile_recall_index_versions)"
        ).fetchall()
    }
    assert {
        "version_id",
        "account_id",
        "contract_json",
        "contract_hash",
        "status",
        "item_count",
        "vector_count",
        "created_at",
        "built_at",
    } <= version_columns

    # 升级幂等：重复初始化不重复执行迁移。
    assert database.initialize() == SCHEMA_VERSION
    database.close()


def test_recall_index_is_derived_account_scoped_and_backup_visible(
    tmp_path: Path,
) -> None:
    """派生索引参与删除与备份、不进导出与逻辑摘要，删除顺序在事实表之前。"""

    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    four = FourDimensionProfileService(
        InMemoryProfileRepository(), InMemoryFourDimensionProfileRepository()
    )
    repository = SqliteAtomicProfileRepository(database, initialize=False)
    index = ProfileRecallIndex(database, _StubEmbeddingPort())
    service = AtomicProfileService(four, repository, recall_index=index)
    service.remember(ALICE, "我在学线性代数")
    service.compile_adopted_slice(
        ALICE,
        purpose=build_purpose(mode="companion", query="帮我安排学习"),
        run_id="run-index",
    )

    for table in RECALL_TABLES:
        count = database.connection.execute(
            f"SELECT COUNT(*) AS count FROM {table} WHERE account_id = ?",
            (ALICE,),
        ).fetchone()
        assert int(count["count"]) >= 1

    # 导出边界：派生数据不入导出类别与逻辑摘要（见 lifecycle.catalog 文档）。
    exported_tables = {
        table for category in EXPORT_CATEGORIES for table in category.tables
    }
    assert exported_tables.isdisjoint(RECALL_TABLES)
    assert logical_summary(database, ALICE).keys().isdisjoint(RECALL_TABLES)

    # 删除顺序：派生索引先于 profile_items（同清单单一事实源）。
    assert ACCOUNT_TABLES.index("profile_recall_index_entries") < (
        ACCOUNT_TABLES.index("profile_items")
    )
    assert ACCOUNT_TABLES.index("profile_recall_index_versions") < (
        ACCOUNT_TABLES.index("profile_items")
    )

    # 备份快照包含派生索引行（可随恢复一起回来）。
    backup_path = tmp_path / "bridges-backup.db"
    with database.snapshot_lock():
        database.snapshot_to(backup_path)
    restored = BridgesDatabase(backup_path)
    assert restored.initialize() == SCHEMA_VERSION
    restored_count = restored.connection.execute(
        "SELECT COUNT(*) AS count FROM profile_recall_index_entries"
        " WHERE account_id = ?",
        (ALICE,),
    ).fetchone()
    assert int(restored_count["count"]) >= 1
    restored.close()

    # 账户删除清空派生索引行。
    delete_account_rows(database, ALICE)
    for table in RECALL_TABLES:
        count = database.connection.execute(
            f"SELECT COUNT(*) AS count FROM {table} WHERE account_id = ?",
            (ALICE,),
        ).fetchone()
        assert int(count["count"]) == 0
    database.close()
