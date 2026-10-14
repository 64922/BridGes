"""Issue 06：档案纠正、删除与迟到结果保护。

覆盖验收项：
1. 用户能查看真实依据，来源不可用时准确显示；
2. 纠正有主动来源与记录版本，自动诊断不能无条件覆盖；
3. 删除对真实持久数据生效，刷新后不恢复，迟到结果被拒绝；
4. 同证据迟到任务不能经任何入口复活已撤回判断；
5. 真正的新作答形成新观察，不与旧历史混淆；
6. 用户自评不被声明为已核验掌握，冲突与不确定性保留；
7. 并发纠正/删除、重复提交与版本冲突反馈正确，失败保留原记录；
8. 他人无法查看、纠正或删除记录及其来源；
9. 有效读取边界、导出与账户隔离贯通。
"""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi.testclient import TestClient

from bridges.ai.adapters import TransientError
from bridges.contracts.study_diagnosis import StudyRecordState
from tests.chat.test_issue02_text_tutor import _TextTutorAdapter
from tests.chat.test_issue03_embedded_verification import (
    _plan_evaluate,
    _plan_pose,
    _PlanAdapter,
)
from tests.chat.test_issue05_durable_diagnosis import (
    _ANSWER,
    _Q1,
    _Q2,
    _close,
    _detail,
    _diagnosis_output,
    _record_version,
    _send,
    _start_study,
    _StructuredAdapter,
    _table_count,
    _wire,
)
from tests.chat.test_v2_05_photo_attachments import _app, _register

_Q3 = "再换一题：互斥事件的和事件概率怎么算？"


def _record_for(detail: dict[str, Any], record_id: str) -> dict[str, Any]:
    return next(
        item
        for item in detail["study_learning_record"]
        if item["record_id"] == record_id
    )


def _seed_record(
    app: Any,
    client: TestClient,
    generation_helpers: Any,
    text: _TextTutorAdapter,
    plan: _PlanAdapter,
    tag: str,
) -> str:
    """形成一条已核验档案与一个待核验的第二证据（第二题诊断失败）。

    返回对话标识；档案标识由 ``record_id`` 查询获得。
    """

    conversation_id = _start_study(client, "互斥是不是独立？", f"{tag}-1")
    generation_helpers["drive"](app)
    plan.default = _plan_evaluate()
    text.answer = "互斥时交集为空，所以 P(A∩B)=0。"
    _send(client, conversation_id, _ANSWER, f"{tag}-2")
    generation_helpers["drive"](app)
    return conversation_id


def test_view_evidence_locates_real_sources_and_marks_unavailable(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _TextTutorAdapter(f"先检查一个关键点。{_Q1}")
    plan = _PlanAdapter(default=_plan_pose(_Q1))
    structured = _StructuredAdapter(plan)
    _wire(app, text, structured)

    try:
        with TestClient(app) as client:
            _register(client, "issue06-view")
            database = app.state.bridges_database
            conversation_a = _seed_record(
                app, client, generation_helpers, text, plan, "issue06-view-a"
            )
            detail = _detail(client, conversation_a)
            record = detail["study_learning_record"][0]
            assert record["state"] == "active"
            assert record["record_version"] >= 1
            assert record["source_available"] is True
            evidence = record["evidences"][0]
            assert evidence["question"] == _Q1
            assert evidence["knowledge_context"] == "互斥与独立"
            assert evidence["assistance"] == "independent"
            assert evidence["answer_message_id"]
            assert evidence["feedback_message_id"]
            assert evidence["verdict"] == "independent_completion"
            assert evidence["source_available"] is True
            assert evidence["source_note"] == ""
            record_id = record["record_id"]

            # 第二段对话共享同一知识身份：记录跨会话可见并归并第二证据。
            plan.default = _plan_pose(_Q2)
            text.answer = f"再检查一题。{_Q2}"
            conversation_b = _start_study(client, "独立性怎么判断？", "issue06-view-b1")
            generation_helpers["drive"](app)
            plan.default = _plan_evaluate()
            text.answer = "独立时 P(A∩B)=P(A)P(B)。"
            _send(client, conversation_b, "独立时 P(A∩B)=P(A)P(B)。", "issue06-view-b2")
            generation_helpers["drive"](app)
            record = _record_for(_detail(client, conversation_b), record_id)
            assert record["evidence_count"] == 2

            # 删除原对话：来源消息与证据随之删除，记录保留并如实标注不可用。
            deleted = client.delete(f"/chat/conversations/{conversation_a}")
            assert deleted.status_code == 204, deleted.text
            detail_b = _detail(client, conversation_b)
            record = _record_for(detail_b, record_id)
            assert record["source_available"] is False
            unavailable = [
                item for item in record["evidences"] if not item["source_available"]
            ]
            readable = [
                item for item in record["evidences"] if item["source_available"]
            ]
            assert len(unavailable) == 1
            assert unavailable[0]["question"] == ""
            assert "不可读" in unavailable[0]["source_note"]
            assert unavailable[0]["occurred_at"] is None
            assert len(readable) == 1
            assert readable[0]["question"] == _Q2
            # 私有评价依据与提示词不进入用户投影。
            detail_text = json.dumps(detail_b, ensure_ascii=False)
            assert "PRIVATE-BASIS-42" not in detail_text

            # 有效读取边界：来源不可用时不再视为已核验，但仍可读取。
            boundary = app.state.study_record_control_service.record_boundary(
                _account_id(database), record_id
            )
            assert boundary is not None
            assert boundary.effective is True
            assert boundary.verified is False
            assert boundary.reason == "source_unavailable"
    finally:
        _close(app)


def test_user_correction_keeps_source_and_late_diagnosis_cannot_overwrite(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _TextTutorAdapter(f"先检查一个关键点。{_Q1}")
    plan = _PlanAdapter(default=_plan_pose(_Q1))
    structured = _StructuredAdapter(plan)
    _wire(app, text, structured)

    try:
        with TestClient(app) as client:
            _register(client, "issue06-correct")
            database = app.state.bridges_database
            conversation_id = _seed_record(
                app, client, generation_helpers, text, plan, "issue06-correct-a"
            )
            record_id = _detail(client, conversation_id)["study_learning_record"][0][
                "record_id"
            ]
            version = _record_version(database)

            # 后台诊断正在运行时（第二题任务未完成）用户纠正第一条判断。
            structured.diagnosis_outputs[_Q2] = TransientError("diagnosis unavailable")
            plan.default = _plan_pose(_Q2)
            text.answer = f"再检查一题。{_Q2}"
            _send(client, conversation_id, "继续检查。", "issue06-correct-b1")
            generation_helpers["drive"](app)
            plan.default = _plan_evaluate()
            text.answer = "独立时 P(A∩B)=P(A)P(B)。"
            _send(
                client, conversation_id, "独立时 P(A∩B)=P(A)P(B)。", "issue06-correct-b2"
            )
            generation_helpers["drive"](app)
            pending = next(
                item
                for item in _detail(client, conversation_id)["study_diagnosis_tasks"]
                if item["question"] == _Q2
            )
            assert pending["status"] == "pending"

            corrected = client.post(
                f"/chat/conversations/{conversation_id}/study/records/"
                f"{record_id}/corrections",
                json={
                    "note": "我当时其实理解了互斥的差别，只是题目答错了细节。",
                    "expected_version": version,
                },
            )
            assert corrected.status_code == 200, corrected.text
            body = corrected.json()
            assert body["state"] == "corrected"
            assert body["user_note"] == "我当时其实理解了互斥的差别，只是题目答错了细节。"
            assert body["superseded_verdict"] == "independent_completion"
            assert body["superseded_performance"] == "能说明互斥不等于独立。"
            assert body["record_version"] == version + 1
            assert body["corrected_at"]
            # 用户自评不是已核验掌握：证据条数不变，纠正不自动新增完成判断。
            assert body["evidence_count"] == 1
            # 事件表保留主动来源、被取代结论与操作版本。
            event = database.connection.execute(
                "SELECT kind, user_note, superseded_verdict,"
                " superseded_record_version, record_version"
                " FROM study_record_controls"
            ).fetchone()
            assert event["kind"] == "correction"
            assert event["user_note"].startswith("我当时其实")
            assert event["superseded_verdict"] == "independent_completion"
            assert event["superseded_record_version"] == version
            assert event["record_version"] == version + 1

            # 放回迟到结果（纠正前作答的第二条证据）：不覆盖用户操作。
            structured.diagnosis_outputs[_Q2] = _diagnosis_output(
                performance="迟到结论：能推导独立乘法公式。",
            )
            time.sleep(1.05)
            generation_helpers["drive"](app)
            detail = _detail(client, conversation_id)
            record = _record_for(detail, record_id)
            assert record["state"] == "corrected"
            assert record["user_note"].startswith("我当时其实")
            assert record["superseded_verdict"] == "independent_completion"
            # 自动层不被迟到结果改写为用户纠正之前的样子。
            assert record["performance"] == "能说明互斥不等于独立。"
            assert record["evidence_count"] == 2
            assert record["pending_confirmation"] is True
            task = next(
                item
                for item in detail["study_diagnosis_tasks"]
                if item["question"] == _Q2
            )
            assert task["status"] == "succeeded"
            # 迟到结果作为观察被保留（双方依据并存），但不冒充当前判断。
            appended = [
                item
                for item in record["evidences"]
                if item["question"] == _Q2
            ]
            assert len(appended) == 1

            # 有效边界：纠正记录不是已核验学情。
            boundary = app.state.study_record_control_service.record_boundary(
                _account_id(database), record_id
            )
            assert boundary is not None
            assert boundary.effective is True
            assert boundary.verified is False
            assert boundary.reason == "user_corrected"
            assert boundary.user_note.startswith("我当时其实")
            assert boundary.record_version == record["record_version"]
    finally:
        _close(app)


def test_deletion_persists_and_late_results_cannot_revive(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _TextTutorAdapter(f"先检查一个关键点。{_Q1}")
    plan = _PlanAdapter(default=_plan_pose(_Q1))
    structured = _StructuredAdapter(plan)
    _wire(app, text, structured)

    try:
        with TestClient(app) as client:
            _register(client, "issue06-delete")
            database = app.state.bridges_database
            conversation_id = _seed_record(
                app, client, generation_helpers, text, plan, "issue06-delete-a"
            )
            detail = _detail(client, conversation_id)
            record_id = detail["study_learning_record"][0]["record_id"]
            version = _record_version(database)
            first_evidence_id = database.connection.execute(
                "SELECT evidence_id FROM study_evidence ORDER BY rowid"
            ).fetchone()["evidence_id"]
            results_before = _table_count(database, "study_diagnosis_results")

            # 第二条证据的后台诊断尚未完成时用户删除记录。
            structured.diagnosis_outputs[_Q2] = TransientError("diagnosis unavailable")
            plan.default = _plan_pose(_Q2)
            text.answer = f"再检查一题。{_Q2}"
            _send(client, conversation_id, "继续检查。", "issue06-delete-b1")
            generation_helpers["drive"](app)
            plan.default = _plan_evaluate()
            text.answer = "独立时 P(A∩B)=P(A)P(B)。"
            _send(
                client, conversation_id, "独立时 P(A∩B)=P(A)P(B)。", "issue06-delete-b2"
            )
            generation_helpers["drive"](app)

            removed = client.request(
                "DELETE",
                f"/chat/conversations/{conversation_id}/study/records/{record_id}",
                json={"expected_version": version},
            )
            assert removed.status_code == 204, removed.text
            # 刷新后记录不再出现。
            assert _detail(client, conversation_id)["study_learning_record"] == []
            row = database.connection.execute(
                "SELECT state, deleted_at, retracted_evidence_ids_json,"
                " record_version FROM study_learning_records"
            ).fetchone()
            assert row["state"] == "deleted"
            assert row["deleted_at"]
            assert first_evidence_id in json.loads(row["retracted_evidence_ids_json"])
            assert row["record_version"] == version + 1
            deletion_event = database.connection.execute(
                "SELECT kind, superseded_record_version FROM study_record_controls"
            ).fetchone()
            assert deletion_event["kind"] == "deletion"
            assert deletion_event["superseded_record_version"] == version

            # 放回迟到结果：提交被拒，任务如实失效，结果与记录都不被改写。
            structured.diagnosis_outputs[_Q2] = _diagnosis_output(
                performance="迟到结论不应恢复记录。"
            )
            time.sleep(1.05)
            generation_helpers["drive"](app)
            late_task = database.connection.execute(
                "SELECT status, failure_reason FROM study_diagnosis_tasks"
                " WHERE question = ?",
                (_Q2,),
            ).fetchone()
            assert late_task["status"] == "invalidated"
            assert "撤回" in late_task["failure_reason"]
            assert _table_count(database, "study_diagnosis_results") == results_before
            row = database.connection.execute(
                "SELECT state, record_version FROM study_learning_records"
            ).fetchone()
            assert row["state"] == "deleted"
            assert row["record_version"] == version + 1
            assert _detail(client, conversation_id)["study_learning_record"] == []

            # 同证据再次登记也不能复活：旧任务的幂等与撤回范围共同守住。
            app.state.study_diagnosis_service.schedule_in_transaction(
                _candidate_for(database, conversation_id, first_evidence_id)
            )
            generation_helpers["drive"](app)
            assert _table_count(database, "study_diagnosis_results") == results_before
            assert (
                database.connection.execute(
                    "SELECT state FROM study_learning_records"
                ).fetchone()["state"]
                == "deleted"
            )

            # 重复提交与版本冲突反馈正确，失败不改变已删除状态。
            repeat = client.request(
                "DELETE",
                f"/chat/conversations/{conversation_id}/study/records/{record_id}",
                json={"expected_version": version + 1},
            )
            assert repeat.status_code == 409
            assert "已删除" in repeat.json()["detail"]["message"]
            stale = client.post(
                f"/chat/conversations/{conversation_id}/study/records/"
                f"{record_id}/corrections",
                json={"note": "仍想纠正。", "expected_version": version},
            )
            assert stale.status_code == 409
            deleted_correction = client.post(
                f"/chat/conversations/{conversation_id}/study/records/"
                f"{record_id}/corrections",
                json={"note": "仍想纠正。", "expected_version": version + 1},
            )
            assert deleted_correction.status_code == 409
            assert "已删除" in deleted_correction.json()["detail"]["message"]

            # 有效读取边界：已删除、不可采用，版本与原因如实返回。
            boundary = app.state.study_record_control_service.record_boundary(
                _account_id(database), record_id
            )
            assert boundary is not None
            assert boundary.effective is False
            assert boundary.verified is False
            assert boundary.reason == "user_deleted"
            assert boundary.state == StudyRecordState.DELETED
            assert boundary.record_version == version + 1
    finally:
        _close(app)


def test_new_answer_after_deletion_forms_new_observation(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _TextTutorAdapter(f"先检查一个关键点。{_Q1}")
    plan = _PlanAdapter(default=_plan_pose(_Q1))
    structured = _StructuredAdapter(plan)
    _wire(app, text, structured)

    try:
        with TestClient(app) as client:
            _register(client, "issue06-new")
            database = app.state.bridges_database
            conversation_id = _seed_record(
                app, client, generation_helpers, text, plan, "issue06-new-a"
            )
            record_id = _detail(client, conversation_id)["study_learning_record"][0][
                "record_id"
            ]
            old_evidence_id = database.connection.execute(
                "SELECT evidence_id FROM study_evidence ORDER BY rowid"
            ).fetchone()["evidence_id"]
            version = _record_version(database)
            removed = client.request(
                "DELETE",
                f"/chat/conversations/{conversation_id}/study/records/{record_id}",
                json={"expected_version": version},
            )
            assert removed.status_code == 204, removed.text

            # 删除之后真正的新作答：形成新观察，旧证据不回归。
            plan.default = _plan_pose(_Q3)
            text.answer = f"再检查一题。{_Q3}"
            _send(client, conversation_id, "继续。", "issue06-new-b1")
            generation_helpers["drive"](app)
            plan.default = _plan_evaluate()
            text.answer = "互斥时 P(A∪B)=P(A)+P(B)。"
            structured.diagnosis_outputs[_Q3] = _diagnosis_output(
                performance="能写出互斥时的加法公式。",
            )
            _send(client, conversation_id, "互斥时 P(A∪B)=P(A)+P(B)。", "issue06-new-b2")
            generation_helpers["drive"](app)

            detail = _detail(client, conversation_id)
            record = _record_for(detail, record_id)
            assert record["state"] == "active"
            assert record["verdict"] == "independent_completion"
            assert record["performance"] == "能写出互斥时的加法公式。"
            assert record["record_version"] == version + 2
            assert record["evidence_count"] == 1
            evidence_ids = [item["evidence_id"] for item in record["evidences"]]
            assert len(evidence_ids) == 1
            assert evidence_ids[0] != old_evidence_id
            # 撤回标记保留：旧证据不会因为新观察被重新采用。
            retracted = json.loads(
                database.connection.execute(
                    "SELECT retracted_evidence_ids_json FROM study_learning_records"
                ).fetchone()["retracted_evidence_ids_json"]
            )
            assert old_evidence_id in retracted

            # 有效边界：新观察恢复为可正式采用的已核验记录。
            boundary = app.state.study_record_control_service.record_boundary(
                _account_id(database), record_id
            )
            assert boundary is not None
            assert boundary.effective is True
            assert boundary.verified is True
            assert boundary.reason == ""
            assert boundary.record_version == version + 2
    finally:
        _close(app)


def test_corrected_record_new_observation_coexists_with_user_note(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _TextTutorAdapter(f"先检查一个关键点。{_Q1}")
    plan = _PlanAdapter(default=_plan_pose(_Q1))
    structured = _StructuredAdapter(plan)
    _wire(app, text, structured)

    try:
        with TestClient(app) as client:
            _register(client, "issue06-coexist")
            database = app.state.bridges_database
            conversation_id = _seed_record(
                app, client, generation_helpers, text, plan, "issue06-coexist-a"
            )
            detail = _detail(client, conversation_id)
            record_id = detail["study_learning_record"][0]["record_id"]
            version = _record_version(database)
            corrected = client.post(
                f"/chat/conversations/{conversation_id}/study/records/"
                f"{record_id}/corrections",
                json={
                    "note": "我更清楚我自己会不会，这个判断不准确。",
                    "expected_version": version,
                },
            )
            assert corrected.status_code == 200, corrected.text

            # 纠正之后真正的新作答：自动观察与用户纠正并存，不互相覆盖。
            plan.default = _plan_pose(_Q2)
            text.answer = f"再检查一题。{_Q2}"
            _send(client, conversation_id, "继续检查。", "issue06-coexist-b1")
            generation_helpers["drive"](app)
            plan.default = _plan_evaluate()
            text.answer = "独立时 P(A∩B)=P(A)P(B)。"
            structured.diagnosis_outputs[_Q2] = _diagnosis_output(
                verdict="misconception",
                performance="新观察指认了乘法公式上的误解。",
                refuting=["把独立与互斥混用"],
            )
            _send(
                client, conversation_id, "独立时 P(A∩B)=P(A)P(B)。", "issue06-coexist-b2"
            )
            generation_helpers["drive"](app)

            detail = _detail(client, conversation_id)
            record = _record_for(detail, record_id)
            assert record["state"] == "corrected"
            assert record["user_note"] == "我更清楚我自己会不会，这个判断不准确。"
            assert record["superseded_verdict"] == "independent_completion"
            # 新观察真实更新了自动层，但纠正与待确认同时保留。
            assert record["verdict"] == "misconception"
            assert record["performance"] == "新观察指认了乘法公式上的误解。"
            assert record["pending_confirmation"] is True
            assert record["evidence_count"] == 2
            assert record["record_version"] == version + 2
            # 有效边界：用户纠正的存在让自动结论不能作为已核验学情。
            boundary = app.state.study_record_control_service.record_boundary(
                _account_id(database), record_id
            )
            assert boundary is not None
            assert boundary.verified is False
            assert boundary.reason == "user_corrected"
    finally:
        _close(app)


def test_version_conflict_duplicate_and_account_isolation(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _TextTutorAdapter(f"先检查一个关键点。{_Q1}")
    plan = _PlanAdapter(default=_plan_pose(_Q1))
    structured = _StructuredAdapter(plan)
    _wire(app, text, structured)

    try:
        with TestClient(app) as client:
            _register(client, "issue06-conflict")
            database = app.state.bridges_database
            conversation_id = _seed_record(
                app, client, generation_helpers, text, plan, "issue06-conflict-a"
            )
            detail = _detail(client, conversation_id)
            record_id = detail["study_learning_record"][0]["record_id"]
            version = _record_version(database)
            path = (
                f"/chat/conversations/{conversation_id}/study/records/{record_id}"
            )

            # 版本不一致：拒绝覆盖，原记录与输入都保持。
            stale = client.post(
                f"{path}/corrections",
                json={"note": "旧版本提交。", "expected_version": version + 7},
            )
            assert stale.status_code == 409
            assert "刷新后重试" in stale.json()["detail"]["message"]
            record = _record_for(_detail(client, conversation_id), record_id)
            assert record["state"] == "active"
            assert record["user_note"] == ""
            assert record["record_version"] == version

            # 空内容纠正如实拒绝（422），不改档案。
            blank = client.post(
                f"{path}/corrections",
                json={"note": "   ", "expected_version": version},
            )
            assert blank.status_code == 422

            # 正确版本保存；重复提交同一旧版本被判冲突，不丢失已有纠正。
            first = client.post(
                f"{path}/corrections",
                json={"note": "第一次纠正。", "expected_version": version},
            )
            assert first.status_code == 200, first.text
            assert first.json()["record_version"] == version + 1
            repeat = client.post(
                f"{path}/corrections",
                json={"note": "重复提交。", "expected_version": version},
            )
            assert repeat.status_code == 409
            second = client.post(
                f"{path}/corrections",
                json={"note": "第二次纠正（更新说法）。", "expected_version": version + 1},
            )
            assert second.status_code == 200, second.text
            assert second.json()["user_note"] == "第二次纠正（更新说法）。"
            assert second.json()["record_version"] == version + 2
            events = database.connection.execute(
                "SELECT COUNT(*) AS count FROM study_record_controls"
                " WHERE kind = 'correction'"
            ).fetchone()
            assert events["count"] == 2

            # 删除的版本冲突与重复删除反馈正确。
            stale_delete = client.request(
                "DELETE", path, json={"expected_version": version}
            )
            assert stale_delete.status_code == 409
            removed = client.request(
                "DELETE", path, json={"expected_version": version + 2}
            )
            assert removed.status_code == 204
            repeat_delete = client.request(
                "DELETE", path, json={"expected_version": version + 3}
            )
            assert repeat_delete.status_code == 409

            # 他人无法查看、纠正或删除该记录及其来源。
            with TestClient(app) as other_client:
                _register(other_client, "issue06-conflict-other")
                blocked = other_client.get(f"/chat/conversations/{conversation_id}")
                assert blocked.status_code == 404
                denied = other_client.post(
                    f"{path}/corrections",
                    json={"note": "越权纠正。", "expected_version": 1},
                )
                assert denied.status_code == 404
                denied_delete = other_client.request(
                    "DELETE", path, json={"expected_version": 1}
                )
                assert denied_delete.status_code == 404
    finally:
        _close(app)


def _candidate_for(
    database: Any, conversation_id: str, evidence_id: str
) -> Any:
    from bridges.study.diagnosis_repository import (
        StudyDiagnosisCandidate,
        evidence_input_version,
    )
    from bridges.study.verification import StudyVerificationRepository

    account_id = _account_id(database)
    evidence = StudyVerificationRepository(database).get_evidence(
        account_id, evidence_id
    )
    assert evidence is not None
    return StudyDiagnosisCandidate(
        account_id=account_id,
        conversation_id=conversation_id,
        evidence_id=evidence.evidence_id,
        question=evidence.question,
        knowledge_context=evidence.knowledge_context,
        input_version=evidence_input_version(evidence),
        basis={
            "evaluation_points": [],
            "accepted_equivalents": [],
            "key_misconceptions": [],
            "assumptions": [],
            "expected_answer": "",
            "contract_version": "study-verification-v1",
        },
        assistance=evidence.assistance.value,
        answer_message_id=evidence.answer_message_id or "",
        check_kind=evidence.check_kind.value,
    )


def _account_id(database: Any) -> str:
    return str(
        database.connection.execute(
            "SELECT account_id FROM conversations ORDER BY rowid LIMIT 1"
        ).fetchone()["account_id"]
    )
