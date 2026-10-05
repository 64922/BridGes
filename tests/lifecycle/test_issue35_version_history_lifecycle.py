"""工单 35：版本 4 历史的迁移、导出、备份恢复、删除与账户隔离。"""

import importlib
import json

from bridges.contracts.study import StudyState
from tests.lifecycle.harness import Harness


def test_version_history_survives_lifecycle_and_legacy_read(tmp_path):
    # 初始化既有聊天组合根，避免学习/聊天服务的既有循环导入依赖收集顺序。
    importlib.import_module("bridges.chat")
    from bridges.study.service import StudyRepository

    harness = Harness(tmp_path)
    conversation = harness.create_conversation(harness.acc1, "版本历史", 0)
    foreign = harness.create_conversation(harness.acc2, "另一账户", 0)
    repository = StudyRepository(harness.database)
    state = StudyState.model_validate({
        "subsection_id": conversation, "state_version": 4, "stage": "tutoring",
        "pages": [{
            "object_id": "new-photo", "ordinal": 1, "content_hash": "new-hash",
            "model_id": "test-model", "fragments": [],
            "superseded_fragments": [{
                "fragment_id": "old-photo:1", "kind": "text", "position": "正文",
                "text": "原识别历史甲", "confidence": 0.9,
            }],
        }],
        "review": {"questions": [{
            "question_id": "paused", "question": "已呈现未答", "asked": True,
            "unanswered": True, "coverage_units": ["unit"], "scope_version_id": "old-scope",
            "fragment_ids": ["old-photo:1"],
        }]},
        "summary_history": [{
            "scope_version_id": "old-scope", "superseded_reason": "补拍更新",
            "summary": {"points": [{
                "kind": "learned", "text": "原总结历史甲", "fragment_ids": ["old-photo:1"],
            }]},
        }],
    })
    repository.save(harness.acc1, conversation, state)
    # 直接写入旧 v3 JSON，确认读取只补默认值，不猜测改写旧总结和判定。
    legacy = {"subsection_id": foreign, "state_version": 3, "stage": "summary",
              "summary": {"points": [{"kind": "learned", "text": "旧总结乙",
                                      "fragment_ids": ["foreign:1"], "question_ids": []}]}}
    harness.database.scoped(harness.acc2).execute(
        "INSERT INTO study_states(account_id, conversation_id, state_json, updated_at)"
        " VALUES (?, ?, ?, ?)",
        (harness.acc2, foreign, json.dumps(legacy, ensure_ascii=False), "2026-10-05T00:00:00Z"),
    )
    current = repository.get(harness.acc1, conversation).model_dump()
    old = repository.get(harness.acc2, foreign).model_dump()
    assert old["state_version"] == 4 and old["summary_history"] == []
    assert old["summary"] == legacy["summary"]
    for account, own, other in ((harness.acc1, "原总结历史甲", "旧总结乙"),
                                (harness.acc2, "旧总结乙", "原总结历史甲")):
        _, payload = harness.export.export_data(account)
        exported = json.dumps(json.loads(payload)["categories"]["study"], ensure_ascii=False)
        assert own in exported and other not in exported
        if account == harness.acc1:
            row = json.loads(payload)["categories"]["study"]["items"][0]
            assert json.loads(row["state_json"]) == current
    _, backup = harness.backup.create_backup("版本历史恢复口令")
    harness.database.connection.execute("DELETE FROM study_states")
    restored = harness.backup.restore_backup("版本历史恢复口令", backup, confirmation="恢复")
    assert restored.ok, restored.reasons
    assert repository.get(harness.acc1, conversation).model_dump() == current
    assert repository.get(harness.acc2, foreign).model_dump() == old
    assert repository.get(harness.acc2, conversation) is None
    assert repository.get(harness.acc1, foreign) is None
    harness.delete_account(harness.acc1)
    assert repository.get(harness.acc1, conversation) is None
    assert repository.get(harness.acc2, foreign).model_dump() == old
    assert "export_create" in harness.audit_actions()
