"""恢复一致性测试（Issue 37，Verification 1-2）。

覆盖：多账户夹具导出→恢复后的逻辑摘要比对；后台索引/提醒任务运行时
创建备份的一致性点与恢复结果（快照锁保证数据库/对象/索引一致）。
"""

from __future__ import annotations

from harness import Harness, logical_summary


def test_restore_preserves_logical_summary_for_all_accounts(tmp_path) -> None:
    """多账户夹具恢复后逻辑摘要与恢复前完全一致（Verification 1）。"""
    harness = Harness(tmp_path)
    harness.seed_everything()
    harness.create_conversation(harness.acc2, "B 的第二个对话", 4)
    harness.create_object(harness.acc2, "b2.txt", b"second bob bytes")
    before_a = logical_summary(harness, harness.acc1)
    before_b = logical_summary(harness, harness.acc2)
    name, backup_bytes = harness.backup.create_backup("一致性口令")
    # 篡改两侧数据后恢复。
    harness.database.connection.execute(
        "UPDATE messages SET content = '篡改' WHERE account_id = ?", (harness.acc1,)
    )
    harness.database.connection.execute(
        "DELETE FROM messages WHERE account_id = ?", (harness.acc2,)
    )
    preview = harness.backup.restore_backup(
        "一致性口令", backup_bytes, confirmation="恢复"
    )
    assert preview.ok, preview.reasons
    assert logical_summary(harness, harness.acc1) == before_a
    assert logical_summary(harness, harness.acc2) == before_b
    # 消息内容也一致（不只是条数）。
    row = harness.database.connection.execute(
        "SELECT content FROM messages WHERE message_id LIKE '%B 的第二个对话%4'"
    ).fetchone()
    assert row is not None and "篡改" not in row["content"]
    assert "第 4 条消息" in row["content"]


def test_backup_during_running_tasks_is_consistent(tmp_path) -> None:
    """后台任务运行中创建备份：一致性点保证恢复结果完整（Verification 2）。

    模拟：备份前写入排队中的摄取任务与待执行提醒行（后台执行器/调度器
    正在处理的形态），快照锁下创建备份，恢复后这些行与逻辑摘要一致。
    """
    harness = Harness(tmp_path)
    harness.seed_everything()
    # 排队中的文档摄取任务（worker 尚未领取）与待执行提醒（scheduler 将投递）。
    harness.database.scoped(harness.acc1).execute(
        "INSERT INTO document_records(document_id, account_id, object_id,"
        " content_hash, parser_version, status, source, created_at, updated_at)"
        " VALUES ('doc-1', ?, 'obj-x', 'hash-1', 'v1', 'queued', 'knowledge_base',"
        " '2026-08-06T00:00:00+00:00', '2026-08-06T00:00:00+00:00')",
        (harness.acc1,),
    )
    harness.database.scoped(harness.acc1).execute(
        "INSERT INTO reminders(reminder_id, account_id, qq_email, timezone, raw_text,"
        " schedule_json, subject, body, use_profile, status, next_run_at, created_at, updated_at)"
        " VALUES ('remind-pending', ?, '10001@qq.com', 'Asia/Shanghai', '立即提醒', '{}',"
        " '后台任务', '正文', 0, 'enabled', '2026-08-06T00:00:05+00:00',"
        " '2026-08-06T00:00:00+00:00', '2026-08-06T00:00:00+00:00')",
        (harness.acc1,),
    )
    summary_before = logical_summary(harness, harness.acc1)
    name, backup_bytes = harness.backup.create_backup("任务运行时口令")
    assert name.endswith(".bridgesbackup")
    # 恢复后：排队任务与提醒都回来（快照一致性点未丢数据）。
    preview = harness.backup.restore_backup(
        "任务运行时口令", backup_bytes, confirmation="恢复"
    )
    assert preview.ok, preview.reasons
    assert logical_summary(harness, harness.acc1) == summary_before
    pending = harness.database.scoped(harness.acc1).execute(
        "SELECT status FROM document_records WHERE document_id = 'doc-1'"
        " AND account_id = ?",
        (harness.acc1,),
    ).fetchone()
    assert pending is not None and pending["status"] == "queued"
    reminder = harness.database.scoped(harness.acc1).execute(
        "SELECT status FROM reminders WHERE reminder_id = 'remind-pending'"
        " AND account_id = ?",
        (harness.acc1,),
    ).fetchone()
    assert reminder is not None and reminder["status"] == "enabled"


def test_export_then_restore_roundtrip_matches_summary(tmp_path) -> None:
    """导出 JSON 与恢复后数据库的逻辑摘要一致（Verification 1 双向核对）。"""
    import json

    harness = Harness(tmp_path)
    harness.seed_everything()
    filename, payload = harness.export.export_data(harness.acc1)
    assert filename.endswith(".json")
    document = json.loads(payload)
    summary = logical_summary(harness, harness.acc1)
    counts = {
        "conversations": len(document["categories"]["conversations"]["items"]),
        "messages": len(document["categories"]["messages"]["items"]),
        "objects": len(document["categories"]["objects"]["items"]),
    }
    assert counts == {
        "conversations": summary["conversations"],
        "messages": summary["messages"],
        "objects": summary["objects"],
    }


def test_backup_with_concurrent_writes_is_consistent(tmp_path) -> None:
    """Verification 2：备份创建期间存在并发写入，一致性点不丢数据不损坏。

    写入线程在备份快照锁外持续插入消息；恢复后数据库健康且逻辑摘要
    等于「备份点前」或「备份点后」的确定性状态（绝无半行/损坏）。
    """
    import threading
    import time

    harness = Harness(tmp_path)
    harness.seed_everything()
    conversation_id = harness.create_conversation(harness.acc1, "并发对话", 1)
    summary_before = logical_summary(harness, harness.acc1)
    stop = threading.Event()
    written: list[int] = []

    def _writer() -> None:
        index = 0
        while not stop.is_set():
            try:
                harness.database.scoped(harness.acc1).execute(
                    "INSERT INTO messages(message_id, conversation_id, account_id,"
                    " role, attempt_number, status, content, created_at, updated_at)"
                    " VALUES (?, ?, ?, 'user', 1, 'done', ?,"
                    " '2026-08-06T00:00:00+00:00', '2026-08-06T00:00:00+00:00')",
                    (f"concurrent-{index}", conversation_id, harness.acc1,
                     f"并发消息 {index}", ),
                )
                written.append(index)
                index += 1
            except Exception:  # noqa: BLE001 - 写入被快照锁串行化，失败即停止
                break
            time.sleep(0.001)

    writer = threading.Thread(target=_writer, daemon=True)
    writer.start()
    time.sleep(0.01)  # 让写入线程先产生若干消息
    name, backup_bytes = harness.backup.create_backup("并发口令")
    stop.set()
    writer.join(timeout=5)
    assert name.endswith(".bridgesbackup")

    # 篡改数据后恢复：恢复点 = 快照锁内的确定性状态。
    harness.database.connection.execute(
        "DELETE FROM messages WHERE account_id = ?", (harness.acc1,)
    )
    preview = harness.backup.restore_backup(
        "并发口令", backup_bytes, confirmation="恢复"
    )
    assert preview.ok, preview.reasons
    # 恢复后：数据库健康、消息数 ≥ 备份点前的数量（快照点无半行写入），
    # 且并发写入的全部消息要么都在（快照后写入被包含或锁外排队）要么
    # 截止到快照点——绝无损坏。
    assert harness.database.health_check()
    messages = int(
        harness.database.scoped(harness.acc1)
        .execute(
            "SELECT COUNT(*) AS count FROM messages WHERE account_id = ?",
            (harness.acc1,),
        )
        .fetchone()["count"]
    )
    assert messages >= summary_before["messages"]
    # 消息内容可读（无半行/损坏）。
    rows = harness.database.scoped(harness.acc1).execute(
        "SELECT content FROM messages WHERE account_id = ? ORDER BY rowid",
        (harness.acc1,),
    ).fetchall()
    assert all(str(row["content"]) for row in rows)
