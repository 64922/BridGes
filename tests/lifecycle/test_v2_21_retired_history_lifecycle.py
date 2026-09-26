"""Issue 21 AC2/AC3：退役能力历史结果的导出/删除/恢复归属回归。

覆盖三条收口断言：

1. 历史生成结果（图片/视频任务、资产与版本）按账户导出，跨账户隔离；
2. V2 新增的图检查点、OCR 解析缓存、附件草稿、画像墓碑等按各自归属
   参与账户删除与恢复（删除后归零、其他账户不受影响、恢复后回来）；
3. 目录守卫：库里任何带 ``account_id`` 的表都必须登记在
   ``ACCOUNT_TABLES``（``account_deletions`` 为文档化的系统状态例外），
   防止新表静默逃过删除。
"""

from __future__ import annotations

import json

from harness import SMTP_CANARY, Harness

from bridges.lifecycle.catalog import ACCOUNT_TABLES, EXPORT_CATEGORIES

PASSPHRASE = "收口回归口令-21"

#: Issue 21 新登记的账户表（删除/恢复必须覆盖）。
NEWLY_CATALOGUED_TABLES = (
    "study_states",
    "generation_runs",
    "generation_events",
    "graph_checkpoints",
    "graph_checkpoint_writes",
    "retrieval_decisions",
    "smtp_verification_attempts",
    "workflow_runs",
)

#: 退役能力的生成结果表 + V2 新增状态表（回归重点）。
LEGACY_MEDIA_TABLES = (
    "image_tasks",
    "image_versions",
    "image_assets",
    "video_tasks",
    "video_assets",
)

V2_STATE_TABLES = (
    "graph_checkpoints",
    "graph_checkpoint_writes",
    "document_parse_cache",
    "chat_attachment_drafts",
    "profile_extraction_tombstones",
    "study_states",
)


def _count(harness: Harness, account_id: str, table: str) -> int:
    row = harness.database.connection.execute(
        f"SELECT COUNT(*) AS count FROM {table} WHERE account_id = ?", (account_id,)
    ).fetchone()
    return int(row["count"])


def _counts(harness: Harness, account_id: str, tables: tuple[str, ...]) -> dict[str, int]:
    return {table: _count(harness, account_id, table) for table in tables}


def _export_document(harness: Harness, account_id: str) -> dict:
    _, payload = harness.export.export_data(account_id)
    return json.loads(payload)


def test_legacy_generated_results_are_exported_per_account(tmp_path) -> None:
    harness = Harness(tmp_path)
    harness.seed_retired_and_v2_state(harness.acc1)
    harness.seed_retired_and_v2_state(harness.acc2)

    preview = {item.category: item for item in harness.export.preview(harness.acc1).categories}
    # 图片任务/资产/版本 + 视频任务/资产各一行。
    assert preview["legacy_media"].item_count == 5
    assert preview["legacy_media"].estimated_bytes > 0

    document = _export_document(harness, harness.acc1)
    items = document["categories"]["legacy_media"]["items"]
    # 每行都保留原始列（含提示词/模型/状态），足以审阅历史生成结果。
    prompts = {str(row["prompt"]) for row in items if "prompt" in row}
    assert f"历史图片任务：{harness.acc1}" in prompts
    assert f"历史视频任务：{harness.acc1}" in prompts
    # 每行只属于导出账户：B 账户的历史结果与正文不在 A 的导出里。
    assert {str(row["account_id"]) for row in items} == {harness.acc1}
    serialized = json.dumps(document, ensure_ascii=False)
    assert f"历史图片任务：{harness.acc2}" not in serialized
    assert f"历史图片替代文本：{harness.acc2}" not in serialized
    # 导出不含凭据金丝雀。
    assert SMTP_CANARY not in serialized


def test_export_categories_cover_retired_media_and_retrieval_decisions() -> None:
    """类别键稳定：退役媒体与检索决策（含跳过决策）都在导出范围内。"""
    by_key = {category.key: category for category in EXPORT_CATEGORIES}
    assert set(by_key["legacy_media"].tables) == set(LEGACY_MEDIA_TABLES)
    assert "retrieval_decisions" in by_key["retrieval"].tables


def test_deletion_purges_new_state_tables_and_restore_brings_them_back(tmp_path) -> None:
    harness = Harness(tmp_path)
    harness.seed_retired_and_v2_state(harness.acc1)
    harness.seed_retired_and_v2_state(harness.acc2)
    _, backup_bytes = harness.backup.create_backup(PASSPHRASE)
    before = _counts(harness, harness.acc1, V2_STATE_TABLES)
    assert all(count > 0 for count in before.values()), before

    harness.delete_account(harness.acc1)
    for table in V2_STATE_TABLES:
        assert _count(harness, harness.acc1, table) == 0, table
        # 其他账户的同名数据不被删除。
        assert _count(harness, harness.acc2, table) > 0, table

    preview = harness.backup.restore_backup(PASSPHRASE, backup_bytes, confirmation="恢复")
    assert preview.ok, preview.reasons
    # 恢复后新状态类别整体回来（含图检查点/OCR 缓存/草稿/墓碑）。
    assert _counts(harness, harness.acc1, V2_STATE_TABLES) == before


def test_deletion_removes_retired_media_results_and_new_runtime_tables(tmp_path) -> None:
    harness = Harness(tmp_path)
    harness.seed_retired_and_v2_state(harness.acc1)
    harness.seed_retired_and_v2_state(harness.acc2)
    covered = LEGACY_MEDIA_TABLES + NEWLY_CATALOGUED_TABLES
    before = _counts(harness, harness.acc1, covered)
    assert all(count > 0 for count in before.values()), before
    _, backup_bytes = harness.backup.create_backup(PASSPHRASE)

    harness.delete_account(harness.acc1)
    for table in covered:
        assert _count(harness, harness.acc1, table) == 0, table
        assert _count(harness, harness.acc2, table) > 0, table
    # 对话与消息一并删除：历史结果不再属于任何可读会话。
    assert _count(harness, harness.acc1, "messages") == 0

    preview = harness.backup.restore_backup(PASSPHRASE, backup_bytes, confirmation="恢复")
    assert preview.ok, preview.reasons
    assert _counts(harness, harness.acc1, covered) == before


def test_account_catalog_covers_every_live_account_scoped_table(tmp_path) -> None:
    """守卫：新增账户表必须登记，否则删除/导出会静默漏表。"""
    harness = Harness(tmp_path)
    live_tables: list[str] = []
    for table in harness.database.schema_table_names():
        columns = harness.database.connection.execute(f"PRAGMA table_info({table})").fetchall()
        if any(str(column["name"]) == "account_id" for column in columns):
            live_tables.append(table)
    # 系统级删除状态表是唯一文档化例外（不作为账户数据处理）。
    assert set(live_tables) - set(ACCOUNT_TABLES) == {"account_deletions"}
    assert set(NEWLY_CATALOGUED_TABLES) <= set(ACCOUNT_TABLES)
    assert set(LEGACY_MEDIA_TABLES) <= set(ACCOUNT_TABLES)
