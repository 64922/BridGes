"""新增结果快照沿用消息的隔离、导出、加密备份恢复、删除及审计。"""

import json

from tests.lifecycle.harness import Harness


def test_result_snapshot_survives_backup_and_is_deleted_only_with_owner(tmp_path):
    harness = Harness(tmp_path)
    for account, label in ((harness.acc1, "结果甲"), (harness.acc2, "结果乙")):
        harness.create_conversation(account, label, 1)
        harness.database.scoped(account).execute(
            "UPDATE messages SET turn_result = ? WHERE account_id = ?",
            (json.dumps({"version": "turn-result-v1", "outcome": "partial",
                         "outcome_label": label}, ensure_ascii=False), account),
        )
        _, payload = harness.export.export_data(account)
        items = json.loads(payload)["categories"]["messages"]["items"]
        assert len(items) == 1
        assert json.loads(items[0]["turn_result"])["outcome_label"] == label

    _, backup = harness.backup.create_backup("工单38快照恢复口令")
    harness.database.connection.execute("UPDATE messages SET turn_result = NULL")
    restored = harness.backup.restore_backup("工单38快照恢复口令", backup, confirmation="恢复")
    assert restored.ok, restored.reasons
    harness.delete_account(harness.acc1)
    assert not harness.database.scoped(harness.acc1).execute(
        "SELECT turn_result FROM messages WHERE account_id = ?", (harness.acc1,),
    ).fetchall()
    remaining = harness.database.scoped(harness.acc2).execute(
        "SELECT turn_result FROM messages WHERE account_id = ?", (harness.acc2,),
    ).fetchone()
    assert json.loads(remaining["turn_result"])["outcome_label"] == "结果乙"
    assert {"export_create", "backup_create", "restore_complete"} <= set(harness.audit_actions())
