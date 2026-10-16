"""画像工单 14：记忆页学习引用的真实 L06/L08 端到端验证。

学习档案由真实导师流程产生（诊断经真实后台执行器，复用工单 06 助手），
目标经 L08 仓库接口读写。覆盖验收项：

1. 记忆页引用区与用户自述分栏：自述条目不冒充已核验学情，阅读/单次
   答对不构成长期掌握证明；
2. 纠正后不再核验、来源不可用如实标注；删除立即生效，迟到结果不复活；
3. 页面暂停经学习领域执行，聊天侧续学承接原目标与原路线，不双写；
4. 跨账户隔离：他人目标与记录不进入本人引用，也不能操作本人条目。
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient

from bridges.ai.adapters import TransientError
from bridges.contracts.study_goals import StudyGoalProjection
from bridges.profiles.commit import ProfileFactSubmission
from bridges.study.goals import StudyGoalRepository
from tests.chat.test_issue03_embedded_verification import (
    _plan_evaluate,
    _plan_pose,
)
from tests.chat.test_issue05_durable_diagnosis import (
    _Q2,
    _close,
    _detail,
    _diagnosis_output,
    _record_version,
    _send,
    _start_study,
    _table_count,
)
from tests.chat.test_issue06_learning_record_controls import _seed_record
from tests.chat.test_v2_05_photo_attachments import _app, _register
from tests.profiles.test_issue14_learning_integration import (
    GOAL_ITEM_TEXT,
    _link,
    _seed_goal,
    _wire_tutor,
)

SELF_REPORT_TEXT = "我已经掌握了互斥与独立的判断"


def _learning_references(client: TestClient) -> dict[str, Any]:
    response = client.get("/profiles/learning-references")
    assert response.status_code == 200, response.text
    return dict(response.json())


def test_memory_view_separates_self_report_and_verified_learning(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text, plan, structured = _wire_tutor(app)
    try:
        with TestClient(app) as client:
            account_id = _register(client, "issue14-memory")["id"]
            atomic = app.state.atomic_profile_service
            submitted = atomic.commit_fact(
                account_id,
                ProfileFactSubmission(
                    text=SELF_REPORT_TEXT,
                    evidence_message_id="message-self-report-14",
                ),
            )
            assert submitted is not None

            conversation_id = _seed_record(
                app, client, generation_helpers, text, plan, "issue14-memory-a"
            )
            record = _detail(client, conversation_id)["study_learning_record"][0]
            record_id = record["record_id"]

            # 引用区：真实作答核验的档案；自述条目不在其中。
            body = _learning_references(client)
            assert body["owner"] == "study"
            assert body["contract_version"] == "learning-references-v1"
            assert body["goals"] == []
            assert len(body["evidences"]) == 1
            evidence = body["evidences"][0]
            assert evidence["record_id"] == record_id
            assert evidence["verdict"] == "independent_completion"
            assert evidence["state"] == "active"
            assert evidence["verified"] is True
            assert evidence["pending_confirmation"] is False
            assert evidence["source_available"] is True
            assert evidence["title"]
            raw = json.dumps(body, ensure_ascii=False)
            assert SELF_REPORT_TEXT not in raw
            items = client.get("/profiles/items").json()
            assert any(item["text"] == SELF_REPORT_TEXT for item in items)

            # 纠正（真实 L06 路由）：立即不再核验，用户自评如实透传。
            version = evidence["record_version"]
            corrected = client.post(
                f"/chat/conversations/{conversation_id}/study/records/"
                f"{record_id}/corrections",
                json={
                    "note": "我当时只是看懂了，还没真正做对。",
                    "expected_version": version,
                },
            )
            assert corrected.status_code == 200, corrected.text
            body = _learning_references(client)
            evidence = body["evidences"][0]
            assert evidence["state"] == "corrected"
            assert evidence["verified"] is False
            assert evidence["record_version"] == version + 1
            assert evidence["user_note"].startswith("我当时只是看懂了")
            assert evidence["verdict"] == "independent_completion"

            # 来源不可用（删除唯一相关对话后）：不显示为已核实。
            removed = client.delete(f"/chat/conversations/{conversation_id}")
            assert removed.status_code == 204, removed.text
            body = _learning_references(client)
            evidence = body["evidences"][0]
            assert evidence["source_available"] is False
            assert evidence["verified"] is False
    finally:
        _close(app)


def test_record_deletion_and_late_results_do_not_revive_memory_view(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text, plan, structured = _wire_tutor(app)
    try:
        with TestClient(app) as client:
            _register(client, "issue14-delete")
            database = app.state.bridges_database
            conversation_id = _seed_record(
                app, client, generation_helpers, text, plan, "issue14-delete-a"
            )
            record_id = _detail(client, conversation_id)["study_learning_record"][0][
                "record_id"
            ]
            version = _record_version(database)
            assert len(_learning_references(client)["evidences"]) == 1

            # 第二条证据的后台诊断尚未完成时用户删除记录。
            structured.diagnosis_outputs[_Q2] = TransientError("diagnosis unavailable")
            plan.default = _plan_pose(_Q2)
            text.answer = f"再检查一题。{_Q2}"
            _send(client, conversation_id, "继续检查。", "issue14-delete-b1")
            generation_helpers["drive"](app)
            plan.default = _plan_evaluate()
            text.answer = "独立时 P(A∩B)=P(A)P(B)。"
            _send(
                client,
                conversation_id,
                "独立时 P(A∩B)=P(A)P(B)。",
                "issue14-delete-b2",
            )
            generation_helpers["drive"](app)
            results_before = _table_count(database, "study_diagnosis_results")

            removed = client.request(
                "DELETE",
                f"/chat/conversations/{conversation_id}/study/records/{record_id}",
                json={"expected_version": version},
            )
            assert removed.status_code == 204, removed.text
            # 删除立即生效：引用区不再列出该项。
            assert _learning_references(client)["evidences"] == []

            # 放回迟到结果：提交被拒，记录不复活，引用区保持为空。
            structured.diagnosis_outputs[_Q2] = _diagnosis_output(
                performance="迟到结论不应恢复记录。"
            )
            time.sleep(1.05)
            generation_helpers["drive"](app)
            late_task = database.connection.execute(
                "SELECT status FROM study_diagnosis_tasks WHERE question = ?",
                (_Q2,),
            ).fetchone()
            assert late_task["status"] == "invalidated"
            assert _table_count(database, "study_diagnosis_results") == results_before
            row = database.connection.execute(
                "SELECT state FROM study_learning_records"
            ).fetchone()
            assert row["state"] == "deleted"
            assert _learning_references(client)["evidences"] == []
    finally:
        _close(app)


def test_page_pause_and_chat_resume_stay_consistent_through_domain(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text, plan, structured = _wire_tutor(app)
    try:
        with TestClient(app) as client:
            account_id = _register(client, "issue14-resume")["id"]
            atomic = app.state.atomic_profile_service
            database = app.state.bridges_database

            conversation_id = _start_study(client, "互斥是不是独立？", "issue14-resume-1")
            generation_helpers["drive"](app)

            _seed_goal(app, account_id, "goal-14-resume")
            item = atomic.remember(
                account_id, GOAL_ITEM_TEXT, source_message_id="message-goal-resume"
            )
            _link(app, account_id, item.profile_item_id, "goal-14-resume")
            item = atomic.get_item(account_id, item.profile_item_id)

            # 页面暂停：经学习领域执行，领域版本推进，画像条目不动。
            paused = client.post(
                f"/profiles/ongoing/{item.profile_item_id}/state",
                json={
                    "action": "pause",
                    "version": item.version,
                    "reference_version": 1,
                },
            )
            assert paused.status_code == 200, paused.text
            body = paused.json()
            assert body["items"][0]["goal"]["goal_state"] == "paused"
            assert body["items"][0]["domain_references"][0]["version"] == 2

            # 聊天侧续学（L08 会话关联接口）：恢复原目标，承接原路线。
            now = datetime.now(UTC)
            with database.transaction():
                linked = (
                    StudyGoalRepository(database).link_existing_goal_in_transaction(
                        account_id=account_id,
                        conversation_id=conversation_id,
                        goal_id="goal-14-resume",
                        expected_version=2,
                        now=now,
                    )
                )
            assert linked is True
            repository = StudyGoalRepository(database)
            goal = repository.get_linked_goal(account_id, conversation_id)
            assert goal is not None and isinstance(goal, StudyGoalProjection)
            assert goal.status == "active"
            assert goal.version == 3
            assert goal.route is not None
            assert goal.route.current_node_id == "node-1"

            resumed = client.get("/profiles/ongoing").json()
            reference = resumed["items"][0]["domain_references"][0]
            assert reference["state_code"] == "active"
            assert reference["version"] == 3
            assert resumed["items"][0]["goal"]["goal_state"] == "active"

            # 页面读到的领域版本不再是当前版本：按冲突拒绝，不覆盖。
            stale = client.post(
                f"/profiles/ongoing/{item.profile_item_id}/state",
                json={
                    "action": "pause",
                    "version": item.version,
                    "reference_version": 2,
                },
            )
            assert stale.status_code == 409, stale.text
            assert (
                stale.json()["detail"]["error"]
                == "ongoing_domain_reference_conflict"
            )
            assert repository.get_goal(account_id, "goal-14-resume").version == 3

            # 全程没有在画像侧写第二份状态。
            stored = atomic.get_item(account_id, item.profile_item_id)
            assert stored.version == item.version
            assert stored.goal_state.value == "active"

            # 跨账户：他人看不见该目标与记录，也不能操作本人条目。
            _register(client, "issue14-resume-b")
            other = _learning_references(client)
            assert other["goals"] == []
            assert other["evidences"] == []
            denied_state = client.post(
                f"/profiles/ongoing/{item.profile_item_id}/state",
                json={"action": "pause", "version": item.version},
            )
            assert denied_state.status_code == 404, denied_state.text
            denied_reference = client.put(
                f"/profiles/ongoing/{item.profile_item_id}/domain-reference",
                json={
                    "reference": {"domain": "study", "reference_id": "goal-14"},
                    "version": item.version,
                },
            )
            assert denied_reference.status_code == 404, denied_reference.text
    finally:
        _close(app)
