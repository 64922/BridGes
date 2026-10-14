"""Issue 13：学习模式生命周期整合演练（跨票串联）。

与各票的独立测试不同，本文件在一条连续时间线上串联跨票机制，验证
它们在真实组合条件下互不破坏：

1. 关闭页面（进程退出）不阻塞后台诊断，重启后任务自动恢复；
2. 重复事件与重复调度只记一次（幂等）；
3. 用户在诊断进行中删除记录：删除持久、迟到结果失效、不复活记录；
4. 删除后继续学习能形成新的独立观察，旧记录保持删除状态；
5. 乱序完成不覆盖较新证据，用户纠正后迟到结果仍不能改写用户操作。
"""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi.testclient import TestClient

from bridges.ai.adapters import TransientError
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


def test_drill_close_restart_duplicate_events_deletion_and_late_result(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _TextTutorAdapter(f"先检查一个关键点。{_Q1}")
    plan = _PlanAdapter(default=_plan_pose(_Q1))
    structured = _StructuredAdapter(plan)
    _wire(app, text, structured)
    structured.diagnosis_outputs[_Q1] = TransientError("diagnosis unavailable")

    with TestClient(app) as client:
        _register(client, "issue13-drill-restart")
        conversation_id = _start_study(client, "互斥是不是独立？", "issue13-drill-r1")
        generation_helpers["drive"](app)
        plan.default = _plan_evaluate()
        text.answer = "互斥时交集为空，所以 P(A∩B)=0。"
        _send(client, conversation_id, _ANSWER, "issue13-drill-r2")
        generation_helpers["drive"](app)

        detail = _detail(client, conversation_id)
        # 前台回复先交付；诊断暂时失败，任务如实停留在待核验。
        assert detail["messages"][-1]["status"] == "done"
        assert detail["study_diagnosis_tasks"][0]["status"] == "pending"
        assert detail["study_learning_record"] == []
    # 关闭页面：进程内对象销毁，数据库保留真实状态。
    _close(app)

    # 重启（同一数据库、新的进程内对象）：有效任务按持久化状态恢复。
    restarted = _app(tmp_path, monkeypatch)
    text2 = _TextTutorAdapter(f"先检查一个关键点。{_Q1}")
    plan2 = _PlanAdapter(default=_plan_pose(_Q1))
    structured2 = _StructuredAdapter(plan2)
    _wire(restarted, text2, structured2)

    try:
        with TestClient(restarted) as client:
            login = client.post(
                "/auth/login",
                json={
                    "identifier": "issue13-drill-restart_user",
                    "password": "Passw0rd123!",
                },
            )
            assert login.status_code == 200, login.text
            database = restarted.state.bridges_database
            # 上次失败留下 1 秒退避：等待到期后由新进程执行器领取。
            time.sleep(1.2)
            generation_helpers["drive"](restarted)

            detail = _detail(client, conversation_id)
            assert detail["study_diagnosis_tasks"][0]["status"] == "succeeded"
            record = detail["study_learning_record"][0]
            assert record["verdict"] == "independent_completion"

            # 重复事件与重复调度：再次驱动不重复记账。
            baseline_version = _record_version(database)
            generation_helpers["drive"](restarted)
            generation_helpers["drive"](restarted)
            assert _table_count(database, "study_diagnosis_results") == 1
            assert _table_count(database, "study_diagnosis_tasks") == 1
            assert _table_count(database, "study_evidence") == 1
            assert _record_version(database) == baseline_version

            # 第二条证据的诊断在后台运行时用户删除记录。
            structured2.diagnosis_outputs[_Q2] = TransientError(
                "diagnosis unavailable"
            )
            plan2.default = _plan_pose(_Q2)
            text2.answer = f"再检查一题。{_Q2}"
            _send(client, conversation_id, "继续检查。", "issue13-drill-r3")
            generation_helpers["drive"](restarted)
            plan2.default = _plan_evaluate()
            text2.answer = "独立时 P(A∩B)=P(A)P(B)。"
            _send(
                client,
                conversation_id,
                "独立时 P(A∩B)=P(A)P(B)。",
                "issue13-drill-r4",
            )
            generation_helpers["drive"](restarted)
            pending = next(
                item
                for item in _detail(client, conversation_id)["study_diagnosis_tasks"]
                if item["question"] == _Q2
            )
            assert pending["status"] == "pending"

            record_id = record["record_id"]
            version = _record_version(database)
            first_evidence_id = database.connection.execute(
                "SELECT evidence_id FROM study_evidence ORDER BY rowid"
            ).fetchone()["evidence_id"]
            removed = client.request(
                "DELETE",
                f"/chat/conversations/{conversation_id}/study/records/{record_id}",
                json={"expected_version": version},
            )
            assert removed.status_code == 204, removed.text
            assert _detail(client, conversation_id)["study_learning_record"] == []
            row = database.connection.execute(
                "SELECT state, retracted_evidence_ids_json, record_version"
                " FROM study_learning_records"
            ).fetchone()
            assert row["state"] == "deleted"
            assert first_evidence_id in json.loads(row["retracted_evidence_ids_json"])
            assert row["record_version"] == version + 1

            # 迟到结果：诊断恢复后提交被拒，任务如实失效，记录不被复活。
            structured2.diagnosis_outputs[_Q2] = _diagnosis_output(
                performance="迟到结论不应恢复记录。"
            )
            time.sleep(1.05)
            generation_helpers["drive"](restarted)
            late_task = database.connection.execute(
                "SELECT status, failure_reason FROM study_diagnosis_tasks"
                " WHERE question = ?",
                (_Q2,),
            ).fetchone()
            assert late_task["status"] == "invalidated"
            assert "撤回" in late_task["failure_reason"]
            assert _table_count(database, "study_diagnosis_results") == 1
            assert (
                database.connection.execute(
                    "SELECT state FROM study_learning_records"
                ).fetchone()["state"]
                == "deleted"
            )

            # 继续学习：删除不阻塞后续，新作答形成新的独立观察。
            structured2.diagnosis_default = _diagnosis_output()
            plan2.default = _plan_pose(_Q1)
            text2.answer = f"我们再做一题。{_Q1}"
            _send(client, conversation_id, "我想再练一题。", "issue13-drill-r5")
            generation_helpers["drive"](restarted)
            plan2.default = _plan_evaluate()
            text2.answer = _ANSWER
            _send(client, conversation_id, _ANSWER, "issue13-drill-r6")
            generation_helpers["drive"](restarted)

            detail = _detail(client, conversation_id)
            records = detail["study_learning_record"]
            assert len(records) == 1
            # 同知识键的新观察复用同一档案行：删除状态恢复为有效，
            # 旧证据保持撤回、不因新观察回归。
            assert records[0]["record_id"] == record_id
            assert records[0]["state"] == "active"
            assert records[0]["record_version"] == version + 2
            assert records[0]["evidence_count"] == 1
            retracted = json.loads(
                database.connection.execute(
                    "SELECT retracted_evidence_ids_json FROM study_learning_records"
                ).fetchone()["retracted_evidence_ids_json"]
            )
            assert first_evidence_id in retracted
    finally:
        _close(restarted)


def test_drill_out_of_order_result_then_correction_holds(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _TextTutorAdapter(f"先检查第一题。{_Q1}")
    plan = _PlanAdapter(default=_plan_pose(_Q1))
    structured = _StructuredAdapter(plan)
    _wire(app, text, structured)
    # 旧证据（第一题）的诊断持续失败；新证据（第二题）先完成归并。
    structured.diagnosis_outputs[_Q1] = TransientError("diagnosis unavailable")
    structured.diagnosis_outputs[_Q2] = _diagnosis_output(
        verdict="independent_completion",
        performance="能推导独立时的乘法公式。",
    )

    try:
        with TestClient(app) as client:
            _register(client, "issue13-drill-merge")
            database = app.state.bridges_database
            conversation_id = _start_study(client, "互斥是什么？", "issue13-drill-m1")
            generation_helpers["drive"](app)

            plan.default = _plan_evaluate()
            text.answer = "互斥时交集为空，所以 P(A∩B)=0。"
            _send(client, conversation_id, _ANSWER, "issue13-drill-m2")
            generation_helpers["drive"](app)

            # 第一题诊断重试仍失败并进入更长退避；期间第二题先完成。
            time.sleep(1.05)
            plan.default = _plan_pose(_Q2)
            text.answer = f"再检查一题。{_Q2}"
            _send(client, conversation_id, "继续检查。", "issue13-drill-m3")
            generation_helpers["drive"](app)
            time.sleep(1.05)
            plan.default = _plan_evaluate()
            text.answer = "独立时 P(A∩B)=P(A)P(B)。"
            _send(
                client,
                conversation_id,
                "独立时 P(A∩B)=P(A)P(B)。",
                "issue13-drill-m4",
            )
            generation_helpers["drive"](app)

            detail = _detail(client, conversation_id)
            record = detail["study_learning_record"][0]
            record_id = record["record_id"]
            assert record["verdict"] == "independent_completion"
            assert record["evidence_count"] == 1
            assert record["pending_confirmation"] is False
            old_task = next(
                item
                for item in detail["study_diagnosis_tasks"]
                if item["question"] == _Q1
            )
            assert old_task["status"] == "failed"
            assert old_task["retryable"] is True

            # 用户在旧诊断仍在重试时纠正当前判断。
            version = _record_version(database)
            note = "我当时其实理解了互斥的差别。"
            corrected = client.post(
                f"/chat/conversations/{conversation_id}/study/records/"
                f"{record_id}/corrections",
                json={"note": note, "expected_version": version},
            )
            assert corrected.status_code == 200, corrected.text
            assert corrected.json()["state"] == "corrected"
            assert corrected.json()["record_version"] == version + 1

            # 旧证据诊断迟到完成：方向冲突如实待确认，不覆盖较新判断，
            # 也不改写用户操作。
            structured.diagnosis_outputs[_Q1] = _diagnosis_output(
                verdict="misconception",
                performance="把互斥理解成了互不影响。",
                refuting=["回答把独立与互斥混为一谈"],
            )
            retried = client.post(
                f"/chat/conversations/{conversation_id}/study/diagnosis/"
                f"{old_task['task_id']}/retry"
            )
            assert retried.status_code == 200, retried.text
            generation_helpers["drive"](app)

            detail = _detail(client, conversation_id)
            record = detail["study_learning_record"][0]
            assert record["record_id"] == record_id
            assert record["state"] == "corrected"
            assert record["user_note"] == note
            assert record["superseded_verdict"] == "independent_completion"
            assert record["verdict"] == "independent_completion"
            assert record["performance"] == "能推导独立时的乘法公式。"
            assert record["pending_confirmation"] is True
            assert record["evidence_count"] == 2
            assert _table_count(database, "study_diagnosis_results") == 2
    finally:
        _close(app)
