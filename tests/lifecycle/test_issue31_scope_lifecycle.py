"""工单 31：新旧范围、预习与来源真实导出、备份恢复和隔离删除。"""

import json

from tests.lifecycle.harness import Harness


def _seed_study_state(
    harness: Harness, account_id: str, conversation_id: str, payload: dict
) -> None:
    harness.database.scoped(account_id).execute(
        "INSERT INTO study_states (account_id, conversation_id, state_json, updated_at)"
        " VALUES (?, ?, ?, ?)",
        (
            account_id,
            conversation_id,
            json.dumps(payload, ensure_ascii=False),
            "2026-10-03T00:00:00+00:00",
        ),
    )


def _read_state(harness: Harness, account_id: str, conversation_id: str):
    from bridges.study.history import StudyHistoryReader

    return StudyHistoryReader(harness.database).read(account_id, conversation_id)


def test_scope_history_backup_restore_export_and_account_isolation(tmp_path) -> None:
    from bridges.contracts.study import StudyState, upgrade_legacy_study_state

    harness = Harness(tmp_path)
    a = harness.create_conversation(harness.acc1, "甲的教材", 0)
    b = harness.create_conversation(harness.acc2, "乙的教材", 0)
    legacy = StudyState.model_validate({
        "subsection_id": a, "stage": "tutoring",
        "units": [{"title": "温度", "fragment_ids": ["photo:1"]}],
        "questions": [{"question": "温度是什么？", "unit_titles": ["温度"]}],
    })
    # 原始 v1 行与升级后 v4 行共存，读取经正式只读路径解释。
    _seed_study_state(harness, harness.acc1, a, json.loads(legacy.model_dump_json()))
    upgraded = upgrade_legacy_study_state(legacy)
    upgraded.subsection_id = b
    upgraded.scope_history = [upgraded.scope.model_copy(update={"scope_version_id": "old-version"})]
    upgraded.scope = upgraded.scope.model_copy(update={
        "scope_version_id": "scope-new", "protocol_version": "study-scope-v2",
        "material_hash": "new-material-hash", "legacy": False,
    })
    upgraded.questions[0].scope_version_id = "scope-new"
    _seed_study_state(harness, harness.acc2, b, json.loads(upgraded.model_dump_json()))
    before_a = _read_state(harness, harness.acc1, a).study.model_dump(mode="json")
    before_b = _read_state(harness, harness.acc2, b).study.model_dump(mode="json")
    _, exported = harness.export.export_data(harness.acc2)
    items = json.loads(exported)["categories"]["study"]["items"]
    assert len(items) == 1
    assert "old-version" in json.dumps(items)
    _, backup = harness.backup.create_backup("范围恢复口令")
    harness.database.connection.execute("DELETE FROM study_states")
    result = harness.backup.restore_backup("范围恢复口令", backup, confirmation="恢复")
    assert result.ok, result.reasons
    assert _read_state(harness, harness.acc1, a).study.model_dump(mode="json") == before_a
    assert _read_state(harness, harness.acc2, b).study.model_dump(mode="json") == before_b
    assert _read_state(harness, harness.acc1, b).study is None
    harness.delete_account(harness.acc1)
    assert _read_state(harness, harness.acc1, a).study is None
    assert _read_state(harness, harness.acc2, b).study.model_dump(mode="json") == before_b
