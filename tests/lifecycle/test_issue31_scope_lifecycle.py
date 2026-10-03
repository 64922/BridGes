"""工单 31：新旧范围、预习与来源真实导出、备份恢复和隔离删除。"""

import json

from tests.lifecycle.harness import Harness


def test_scope_history_backup_restore_export_and_account_isolation(tmp_path) -> None:
    from bridges.chat.service import ChatService  # noqa: F401
    from bridges.contracts.study import StudyState
    from bridges.study.service import StudyRepository

    harness = Harness(tmp_path)
    a = harness.create_conversation(harness.acc1, "甲的教材", 0)
    b = harness.create_conversation(harness.acc2, "乙的教材", 0)
    repository = StudyRepository(harness.database)
    legacy = StudyState.model_validate({
        "subsection_id": a, "stage": "tutoring",
        "units": [{"title": "温度", "fragment_ids": ["photo:1"]}],
        "questions": [{"question": "温度是什么？", "unit_titles": ["温度"]}],
    })
    # 原始 v1 行与 v2 行共存，恢复后均经正式读取路径解释。
    harness.database.scoped(harness.acc1).execute(
        "INSERT INTO study_states (account_id, conversation_id, state_json, updated_at)"
        " VALUES (?, ?, ?, ?)",
        (harness.acc1, a, legacy.model_dump_json(), "2026-10-03T00:00:00+00:00"),
    )
    upgraded = repository.get(harness.acc1, a)
    upgraded.subsection_id = b
    upgraded.scope_history = [upgraded.scope.model_copy(update={"scope_version_id": "old-version"})]
    upgraded.scope = upgraded.scope.model_copy(update={
        "scope_version_id": "scope-new", "protocol_version": "study-scope-v2",
        "material_hash": "new-material-hash", "legacy": False,
    })
    upgraded.questions[0].scope_version_id = "scope-new"
    repository.save(harness.acc2, b, upgraded)
    before_a = repository.get(harness.acc1, a).model_dump()
    before_b = repository.get(harness.acc2, b).model_dump()
    _, exported = harness.export.export_data(harness.acc2)
    items = json.loads(exported)["categories"]["study"]["items"]
    assert len(items) == 1
    assert "old-version" in json.dumps(items)
    _, backup = harness.backup.create_backup("范围恢复口令")
    harness.database.connection.execute("DELETE FROM study_states")
    result = harness.backup.restore_backup("范围恢复口令", backup, confirmation="恢复")
    assert result.ok, result.reasons
    assert repository.get(harness.acc1, a).model_dump() == before_a
    assert repository.get(harness.acc2, b).model_dump() == before_b
    assert repository.get(harness.acc1, b) is None
    harness.delete_account(harness.acc1)
    assert repository.get(harness.acc1, a) is None
    assert repository.get(harness.acc2, b).model_dump() == before_b
