"""工单 35：版本 4 历史的迁移、导出、备份恢复、删除与账户隔离。"""

import json

from bridges.contracts.study import StudyState
from tests.lifecycle.harness import Harness


def _seed_study_state(
    harness: Harness, account_id: str, conversation_id: str, payload: dict
) -> None:
    harness.database.scoped(account_id).execute(
        "INSERT INTO study_states(account_id, conversation_id, state_json, updated_at)"
        " VALUES (?, ?, ?, ?)",
        (
            account_id,
            conversation_id,
            json.dumps(payload, ensure_ascii=False),
            "2026-10-05T00:00:00Z",
        ),
    )


def _read_state(harness: Harness, account_id: str, conversation_id: str):
    from bridges.study.history import StudyHistoryReader

    return StudyHistoryReader(harness.database).read(account_id, conversation_id)


def test_version_history_survives_lifecycle_and_legacy_read(tmp_path):
    harness = Harness(tmp_path)
    conversation = harness.create_conversation(harness.acc1, "版本历史", 0)
    foreign = harness.create_conversation(harness.acc2, "另一账户", 0)
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
    _seed_study_state(harness, harness.acc1, conversation, json.loads(state.model_dump_json()))
    # 直接写入旧 v3 JSON，确认读取只补默认值，不猜测改写旧总结和判定。
    legacy = {"subsection_id": foreign, "state_version": 3, "stage": "summary",
              "summary": {"points": [{"kind": "learned", "text": "旧总结乙",
                                      "fragment_ids": ["foreign:1"], "question_ids": []}]}}
    _seed_study_state(harness, harness.acc2, foreign, legacy)
    current = _read_state(harness, harness.acc1, conversation).study.model_dump(mode="json")
    old = _read_state(harness, harness.acc2, foreign).study.model_dump(mode="json")
    assert old["state_version"] == 4 and old["summary_history"] == []
    # 工单 36 追加总结范围版本字段：旧总结原文不变，仅补默认空值。
    assert old["summary"] == {**legacy["summary"], "scope_version_id": ""}
    for account, own, other in ((harness.acc1, "原总结历史甲", "旧总结乙"),
                                (harness.acc2, "旧总结乙", "原总结历史甲")):
        _, payload = harness.export.export_data(account)
        exported = json.dumps(json.loads(payload)["categories"]["study"], ensure_ascii=False)
        assert own in exported and other not in exported
        if account == harness.acc1:
            row = json.loads(payload)["categories"]["study"]["items"][0]
            stored = {key: value for key, value in current.items() if key != "materials"}
            assert json.loads(row["state_json"]) == stored
    _, backup = harness.backup.create_backup("版本历史恢复口令")
    harness.database.connection.execute("DELETE FROM study_states")
    restored = harness.backup.restore_backup("版本历史恢复口令", backup, confirmation="恢复")
    assert restored.ok, restored.reasons
    assert _read_state(harness, harness.acc1, conversation).study.model_dump(mode="json") == current
    assert _read_state(harness, harness.acc2, foreign).study.model_dump(mode="json") == old
    assert _read_state(harness, harness.acc2, conversation).study is None
    assert _read_state(harness, harness.acc1, foreign).study is None
    harness.delete_account(harness.acc1)
    assert _read_state(harness, harness.acc1, conversation).study is None
    assert _read_state(harness, harness.acc2, foreign).study.model_dump(mode="json") == old
    assert "export_create" in harness.audit_actions()
