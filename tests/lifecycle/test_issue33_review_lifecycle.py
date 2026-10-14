"""工单 33：冻结评分与旧判定的导出、备份恢复、删除和账户隔离。"""

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
            "2026-10-04T00:00:00+00:00",
        ),
    )


def _read_state(harness: Harness, account_id: str, conversation_id: str):
    from bridges.study.history import StudyHistoryReader

    return StudyHistoryReader(harness.database).read(account_id, conversation_id)


def test_frozen_and_legacy_rubrics_survive_lifecycle_without_cross_account_leak(tmp_path):
    harness = Harness(tmp_path)
    first = harness.create_conversation(harness.acc1, "新评分", 0)
    second = harness.create_conversation(harness.acc2, "旧评分", 0)
    current = StudyState.model_validate({
        "subsection_id": first, "state_version": 3, "stage": "review",
        "review": {"scope_version_id": "frozen-scope", "protocol_version": "study-review-v2",
                   "active_question_id": "new-rq", "questions": [{
                       "question_id": "new-rq", "question": "温度是什么？", "asked": True,
                       "scope_version_id": "frozen-scope", "coverage_units": ["unit-1"],
                       "fragment_ids": ["fragment-1"], "canonical_answer": "私有答案甲",
                       "core_points": ["私有要点甲"], "equivalents": ["私有等价甲"],
                       "key_misconceptions": ["私有误解甲"], "incomplete_basis": "缺要点",
                       "incorrect_basis": "与原文冲突",
                       "answer": "学生答案甲", "user_message_id": "answer-source",
                       "judgement": "correct", "feedback": "已提交反馈甲",
                       "grade_record": {"protocol_version": "study-grade-v4",
                                        "recheck_status": "revised",
                                        "recheck_detail": "独立核实甲",
                                        "point_checks": [{"point": "私有要点甲", "status": "hit",
                                                          "fragment_ids": ["fragment-1"]}]},
                       "verification": {
                           "status": "consistent", "question_matches_knowledge": True,
                           "rubric_supported": True, "answer_consistent": True,
                       },
                   }]},
    })
    _seed_study_state(harness, harness.acc1, first, json.loads(current.model_dump_json()))
    legacy = {
        "subsection_id": second, "state_version": 2, "stage": "summary",
        "review": {"questions": [{"question_id": "old-rq", "question": "旧题",
                                  "coverage_units": ["legacy-unit"],
                                  "fragment_ids": ["legacy-fragment"],
                                  "asked": True, "judgement": "correct",
                                  "canonical_answer": "历史答案乙", "explanation": "旧判定"}]},
    }
    _seed_study_state(harness, harness.acc2, second, legacy)
    before_first = _read_state(harness, harness.acc1, first).study.model_dump(mode="json")
    before_second = _read_state(harness, harness.acc2, second).study.model_dump(mode="json")
    assert before_second["review"]["questions"][0]["legacy"] is True
    for account, own, foreign in (
        (harness.acc1, "私有答案甲", "历史答案乙"),
        (harness.acc2, "历史答案乙", "私有答案甲"),
    ):
        _, payload = harness.export.export_data(account)
        study = json.dumps(json.loads(payload)["categories"]["study"], ensure_ascii=False)
        assert own in study and foreign not in study
        if account == harness.acc1:
            assert "已提交反馈甲" in study and "独立核实甲" in study
    _, backup = harness.backup.create_backup("评分依据恢复口令")
    harness.database.connection.execute("DELETE FROM study_states")
    restored = harness.backup.restore_backup("评分依据恢复口令", backup, confirmation="恢复")
    assert restored.ok, restored.reasons
    assert _read_state(harness, harness.acc1, first).study.model_dump(mode="json") == before_first
    assert _read_state(harness, harness.acc2, second).study.model_dump(mode="json") == before_second
    assert _read_state(harness, harness.acc1, second).study is None
    assert _read_state(harness, harness.acc2, first).study is None
    harness.delete_account(harness.acc1)
    assert _read_state(harness, harness.acc1, first).study is None
    assert _read_state(harness, harness.acc2, second).study.model_dump(mode="json") == before_second
    assert "export_create" in harness.audit_actions()
