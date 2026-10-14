"""Issue 05：后台独立诊断与学习档案。

覆盖验收项：
1. 作答后回复先交付，完整诊断在后台独立执行；
2. 任务与回复/证据原子提交，失败、停止与非作答不产生无依据缺口；
3. 提示程度、实际来源、概念上下文与不确定性可追溯；
4. 单次完成不晋升长期掌握，同名不同上下文不合并；
5. 重复调度、租约恢复、迟到完成与重复完成只记一次；
6. 乱序归并、来源失效与版本冲突不覆盖更新证据；
7. 失败保留原档案，有界重试耗尽后手动重试有真实状态；
8. 应用重启恢复有效任务，账户隔离与私有字段成立。
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from bridges.ai import ModelGateway
from bridges.ai.adapters import AdapterResult, RateLimitError, TransientError
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.contracts.ai import CapabilityRecord
from bridges.study.diagnosis_prompt import STUDY_DIAGNOSIS_SYSTEM_PROMPT
from bridges.study.diagnosis_repository import (
    StudyDiagnosisCandidate,
    evidence_input_version,
    knowledge_key,
)
from bridges.study.verification import StudyVerificationRepository
from tests.chat.test_issue02_text_tutor import _TextTutorAdapter
from tests.chat.test_issue03_embedded_verification import (
    _plan,
    _plan_evaluate,
    _plan_pose,
    _PlanAdapter,
    _structured_capability,
)
from tests.chat.test_v2_05_photo_attachments import (
    _app,
    _chat_capability,
    _register,
)

#: 只应存在于服务端私有评价依据中的哨兵串，用于证明私有依据不泄漏。
_BASIS_SENTINEL = "PRIVATE-BASIS-42"

_Q1 = "如果 A、B 互斥，P(A∩B) 是多少？为什么？"
_Q2 = "如果 A、B 独立，P(A∩B) 与 P(A)、P(B) 的关系是什么？"
_ANSWER = "因为互斥表示不能同时发生，所以 P(A∩B)=0。"


def _diagnosis_output(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "subject": "概率论",
        "knowledge_title": "互斥与独立",
        "usage_context": "理解事件关系",
        "verdict": "independent_completion",
        "performance": "能说明互斥不等于独立。",
        "supporting": ["给出了正确的反例"],
        "refuting": [],
        "uncertainty": "只在单题上验证过。",
        "next_validation": "换一个条件概率题再验证。",
    }
    base.update(overrides)
    return base


class _StructuredAdapter:
    """共用 ``qwen_structured_output`` 的确定性替身。

    诊断调用按题目分发脚本（支持注入重试失败）；其余结构化调用（03
    验证规划、07 目标规划）交给各自的替身，避免脚本互相消费。
    """

    def __init__(self, planner: _PlanAdapter | None = None) -> None:
        self.planner = planner
        self.diagnosis_outputs: dict[str, Any] = {}
        self.diagnosis_default: Any = _diagnosis_output()
        self.diagnosis_payloads: list[dict[str, Any]] = []

    def call(
        self, capability: CapabilityRecord, run_context: Any, payload: dict[str, Any]
    ) -> AdapterResult:
        messages = payload.get("messages") or []
        system = str(messages[0].get("content") or "") if messages else ""
        if system == STUDY_DIAGNOSIS_SYSTEM_PROMPT:
            self.diagnosis_payloads.append(payload)
            request = json.loads(str(messages[1]["content"]))
            item = self.diagnosis_outputs.get(
                str(request.get("question") or ""), self.diagnosis_default
            )
            if isinstance(item, Exception):
                raise item
            return AdapterResult(actual_model_id=capability.model_id, output=item)
        if payload.get("task") == "study.goal_planning":
            # 局部问题不建目标、不规划路线（与 07 的合同一致）。
            return AdapterResult(
                actual_model_id=capability.model_id,
                output={
                    "kind": "local_question",
                    "observable_result": "",
                    "scope": "",
                    "constraints": "",
                    "clarification_question": "",
                    "route": None,
                    "note": "",
                },
            )
        assert self.planner is not None, "未配置验证规划替身"
        return self.planner.call(capability, run_context, payload)

    def stream_call(
        self, capability: CapabilityRecord, run_context: Any, payload: dict[str, Any]
    ) -> Any:
        raise AssertionError("结构化替身不应走流式调用")


def _gateway_with_diagnosis(
    text: _TextTutorAdapter, structured: _StructuredAdapter
) -> ModelGateway:
    registry = CapabilityRegistry()
    registry.register(_chat_capability())
    registry.register(_structured_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", text)
    gateway.register_adapter("qwen_structured_output", "1", structured)
    return gateway


def _wire(
    app: Any, text: _TextTutorAdapter, structured: _StructuredAdapter
) -> ModelGateway:
    gateway = _gateway_with_diagnosis(text, structured)
    app.state.chat_service._gateway = gateway
    app.state.study_diagnosis_service._gateway = gateway
    return gateway


def _start_study(client: TestClient, content: str, key: str) -> str:
    response = client.post(
        "/chat/first-turn",
        json={"mode": "study", "content": content, "idempotency_key": key},
    )
    assert response.status_code == 201, response.text
    return response.json()["conversation"]["conversation_id"]


def _send(client: TestClient, conversation_id: str, content: str, key: str) -> str:
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": content, "idempotency_key": key},
    )
    assert response.status_code == 200, response.text
    return response.json()["assistant_message"]["message_id"]


def _detail(client: TestClient, conversation_id: str) -> dict[str, Any]:
    response = client.get(f"/chat/conversations/{conversation_id}")
    assert response.status_code == 200, response.text
    return response.json()


def _close(app: Any) -> None:
    app.state.bridges_database.close()
    app.state.state_store.close()


def _table_count(database: Any, table: str) -> int:
    return int(
        database.connection.execute(
            f"SELECT COUNT(*) AS count FROM {table}"
        ).fetchone()["count"]
    )


def _record_version(database: Any) -> int:
    return int(
        database.connection.execute(
            "SELECT record_version FROM study_learning_records"
        ).fetchone()["record_version"]
    )


# ---------------------------------------------------------------------------
# 回复先交付、后台独立诊断与私有依据隔离
# ---------------------------------------------------------------------------


def test_reply_delivers_before_background_diagnosis_and_private_basis_stays_server_side(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _TextTutorAdapter(f"我们先检查一个关键点。{_Q1}")
    plan = _PlanAdapter([_plan_pose(_Q1)])
    structured = _StructuredAdapter(plan)
    _wire(app, text, structured)
    # 后台诊断暂时不可用：回复仍须先交付，任务如实停留在待核验。
    structured.diagnosis_outputs[_Q1] = TransientError("diagnosis unavailable")

    try:
        with TestClient(app) as client:
            _register(client, "issue05-background")
            database = app.state.bridges_database
            conversation_id = _start_study(client, "互斥是不是独立？", "issue05-bg-1")
            generation_helpers["drive"](app)
            assert _table_count(database, "study_diagnosis_tasks") == 0

            plan.default = _plan_evaluate()
            text.answer = "互斥时交集为空，所以 P(A∩B)=0。"
            _send(client, conversation_id, _ANSWER, "issue05-bg-2")
            generation_helpers["drive"](app)

            detail = _detail(client, conversation_id)
            assert detail["messages"][-1]["status"] == "done"
            tasks = detail["study_diagnosis_tasks"]
            assert len(tasks) == 1
            assert tasks[0]["status"] == "pending"
            assert tasks[0]["question"] == _Q1
            assert tasks[0]["knowledge_context"] == "互斥与独立"
            assert tasks[0]["attempts"] == 1
            assert detail["study_learning_record"] == []
            # 私有评价依据与完整提示词不进入用户投影。
            detail_text = json.dumps(detail, ensure_ascii=False)
            assert _BASIS_SENTINEL not in detail_text
            assert STUDY_DIAGNOSIS_SYSTEM_PROMPT[:30] not in detail_text
            stored = database.connection.execute(
                "SELECT basis_json FROM study_diagnosis_tasks"
            ).fetchone()
            assert _BASIS_SENTINEL in stored["basis_json"]

            # 诊断载荷核对：题目、实际回答、提示程度、上下文与冻结依据。
            request = json.loads(
                structured.diagnosis_payloads[-1]["messages"][1]["content"]
            )
            assert request["question"] == _Q1
            assert request["student_answer"] == _ANSWER
            assert request["assistance"] == "independent"
            assert request["hint_count"] == 0
            assert request["knowledge_context"] == "互斥与独立"
            assert (
                request["evaluation_basis"]["evaluation_points"][0]
                == _BASIS_SENTINEL
            )
            assert request["protocol_version"] == "study-diagnosis-v1"

            # 诊断能力恢复：自动重试成功，档案只记一次。
            structured.diagnosis_outputs.pop(_Q1)
            time.sleep(1.05)
            generation_helpers["drive"](app)

            detail = _detail(client, conversation_id)
            assert detail["study_diagnosis_tasks"][0]["status"] == "succeeded"
            records = detail["study_learning_record"]
            assert len(records) == 1
            record = records[0]
            assert record["subject"] == "概率论"
            assert record["title"] == "互斥与独立"
            assert record["usage_context"] == "理解事件关系"
            assert record["verdict"] == "independent_completion"
            assert record["performance"] == "能说明互斥不等于独立。"
            assert record["uncertainty"] == "只在单题上验证过。"
            assert record["evidence_count"] == 1
            assert record["evidences"][0]["assistance"] == "independent"
            assert record["evidences"][0]["question"] == _Q1
            assert _table_count(database, "study_diagnosis_results") == 1
            baseline_version = _record_version(database)

            # 重复调度同一证据：再次驱动不重复记账、不再次改学情。
            account_id = database.connection.execute(
                "SELECT account_id FROM study_diagnosis_tasks"
            ).fetchone()["account_id"]
            evidence_id = database.connection.execute(
                "SELECT evidence_id FROM study_diagnosis_tasks"
            ).fetchone()["evidence_id"]
            evidence = StudyVerificationRepository(database).get_evidence(
                account_id, evidence_id
            )
            assert evidence is not None
            candidate = StudyDiagnosisCandidate(
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
            app.state.study_diagnosis_service.schedule_in_transaction(candidate)
            generation_helpers["drive"](app)
            assert _table_count(database, "study_diagnosis_results") == 1
            assert _record_version(database) == baseline_version
            assert len(_detail(client, conversation_id)["study_diagnosis_tasks"]) == 1
    finally:
        _close(app)


# ---------------------------------------------------------------------------
# 非作答、失败与停止不产生无依据缺口
# ---------------------------------------------------------------------------


def test_non_answer_and_failed_turn_do_not_create_diagnosis_or_gap(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _TextTutorAdapter(f"我们先检查一个关键点。{_Q1}")
    plan = _PlanAdapter(default=_plan_pose(_Q1))
    structured = _StructuredAdapter(plan)
    _wire(app, text, structured)

    try:
        with TestClient(app) as client:
            _register(client, "issue05-non-answer")
            database = app.state.bridges_database
            conversation_id = _start_study(client, "互斥是什么？", "issue05-na-1")
            generation_helpers["drive"](app)

            # 纯提问不是作答：不产生证据，也不生成掌握/缺口判断。
            plan.default = _plan(action="none", intent="question")
            text.answer = "互斥表示两个事件不能同时发生。"
            _send(client, conversation_id, "能再解释一下互斥吗？", "issue05-na-2")
            generation_helpers["drive"](app)
            assert _table_count(database, "study_evidence") == 0
            assert _table_count(database, "study_diagnosis_tasks") == 0
            assert _table_count(database, "study_learning_records") == 0

            # 生成失败：不提交证据、不登记诊断任务，也不判学生错。
            plan.default = _plan_evaluate()
            text.failure = RateLimitError("temporary network interruption")
            _send(client, conversation_id, _ANSWER, "issue05-na-3")
            generation_helpers["drive"](app)
            detail = _detail(client, conversation_id)
            assert detail["messages"][-1]["status"] == "error"
            assert _table_count(database, "study_diagnosis_tasks") == 0
            assert _table_count(database, "study_learning_records") == 0
    finally:
        _close(app)


def test_stopped_turn_does_not_commit_diagnosis(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _TextTutorAdapter(f"我们先检查一个关键点。{_Q1}")
    plan = _PlanAdapter(default=_plan_pose(_Q1))
    structured = _StructuredAdapter(plan)
    _wire(app, text, structured)

    try:
        with TestClient(app) as client:
            _register(client, "issue05-stop")
            database = app.state.bridges_database
            conversation_id = _start_study(client, "互斥是什么？", "issue05-s-1")
            generation_helpers["drive"](app)

            plan.default = _plan_evaluate()
            text.answer = "互斥时交集为空，所以 P(A∩B)=0。"
            assistant_id = _send(client, conversation_id, _ANSWER, "issue05-s-2")
            stopped = client.post(
                f"/chat/conversations/{conversation_id}/messages/{assistant_id}/stop"
            )
            assert stopped.status_code == 200, stopped.text
            generation_helpers["drive"](app)

            detail = _detail(client, conversation_id)
            assert detail["messages"][-1]["status"] != "done"
            assert _table_count(database, "study_evidence") == 0
            assert _table_count(database, "study_diagnosis_tasks") == 0
            assert _table_count(database, "study_learning_records") == 0
    finally:
        _close(app)


# ---------------------------------------------------------------------------
# 提示程度、实际来源与概念上下文可追溯；同名不同概念不合并
# ---------------------------------------------------------------------------


def test_hint_level_source_and_context_are_traceable_and_same_title_stays_separate(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _TextTutorAdapter(f"先检查一下。{_Q1}")
    plan = _PlanAdapter(default=_plan_pose(_Q1))
    structured = _StructuredAdapter(plan)
    _wire(app, text, structured)
    structured.diagnosis_outputs[_Q1] = _diagnosis_output(
        verdict="hinted_completion",
        performance="在提示后能说明交集为空。",
    )

    try:
        with TestClient(app) as client:
            _register(client, "issue05-hint-trace")
            database = app.state.bridges_database
            conversation_id = _start_study(client, "互斥是什么？", "issue05-h-1")
            generation_helpers["drive"](app)

            # 先要提示再作答：提示不被判错，诊断必须看到真实提示程度。
            plan.default = _plan(action="help", intent="hint_request")
            text.answer = "提示：先想想互斥对交集意味着什么。"
            _send(client, conversation_id, "给我一点提示。", "issue05-h-2")
            generation_helpers["drive"](app)
            plan.default = _plan_evaluate()
            text.answer = "互斥时交集为空，所以 P(A∩B)=0。"
            _send(client, conversation_id, _ANSWER, "issue05-h-3")
            generation_helpers["drive"](app)

            request = json.loads(
                structured.diagnosis_payloads[-1]["messages"][1]["content"]
            )
            assert request["assistance"] == "after_hint"
            assert request["hint_count"] == 1
            assert request["recent_hints"] == ["提示：先想想互斥对交集意味着什么。"]

            detail = _detail(client, conversation_id)
            record = detail["study_learning_record"][0]
            assert record["verdict"] == "hinted_completion"
            assert record["performance"] == "在提示后能说明交集为空。"
            assert record["evidences"][0]["assistance"] == "after_hint"
            assert record["evidences"][0]["answer_message_id"]
            assert record["evidences"][0]["feedback_message_id"]
            assert "PRIVATE-BASIS-42" not in json.dumps(detail, ensure_ascii=False)

            # 同一标题、不同用途上下文：另一个对话形成独立档案条目。
            structured.diagnosis_outputs[_Q2] = _diagnosis_output(
                usage_context="写出完整证明",
                performance="证明步骤完整。",
            )
            plan.default = _plan_pose(_Q2)
            text.answer = f"再检查一题。{_Q2}"
            other = _start_study(client, "独立性怎么判断？", "issue05-h-4")
            generation_helpers["drive"](app)
            plan.default = _plan_evaluate()
            text.answer = "独立时 P(A∩B)=P(A)P(B)。"
            _send(client, other, "独立时 P(A∩B)=P(A)P(B)。", "issue05-h-5")
            generation_helpers["drive"](app)

            rows = database.connection.execute(
                "SELECT title, usage_context, knowledge_key"
                " FROM study_learning_records ORDER BY rowid"
            ).fetchall()
            assert len(rows) == 2
            assert rows[0]["title"] == rows[1]["title"] == "互斥与独立"
            assert rows[0]["usage_context"] == "理解事件关系"
            assert rows[1]["usage_context"] == "写出完整证明"
            assert rows[0]["knowledge_key"] != rows[1]["knowledge_key"]
            assert rows[0]["knowledge_key"] == knowledge_key(
                "概率论", "互斥与独立", "理解事件关系"
            )
            assert len(_detail(client, other)["study_learning_record"]) == 1
    finally:
        _close(app)


# ---------------------------------------------------------------------------
# 乱序归并、冲突待确认与迟到完成
# ---------------------------------------------------------------------------


def test_late_older_result_does_not_overwrite_newer_evidence_and_marks_conflict(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _TextTutorAdapter(f"先检查第一题。{_Q1}")
    plan = _PlanAdapter(default=_plan_pose(_Q1))
    structured = _StructuredAdapter(plan)
    _wire(app, text, structured)
    # 旧证据的诊断持续失败，新证据的诊断先完成；随后旧任务迟到完成。
    structured.diagnosis_outputs[_Q1] = TransientError("diagnosis unavailable")
    structured.diagnosis_outputs[_Q2] = _diagnosis_output(
        verdict="independent_completion",
        performance="能推导独立时的乘法公式。",
    )

    try:
        with TestClient(app) as client:
            _register(client, "issue05-out-of-order")
            database = app.state.bridges_database
            conversation_id = _start_study(client, "互斥是什么？", "issue05-oo-1")
            generation_helpers["drive"](app)

            plan.default = _plan_evaluate()
            text.answer = "互斥时交集为空，所以 P(A∩B)=0。"
            _send(client, conversation_id, _ANSWER, "issue05-oo-2")
            generation_helpers["drive"](app)
            old_evidence = database.connection.execute(
                "SELECT evidence_id, created_at FROM study_evidence ORDER BY rowid"
            ).fetchone()
            assert old_evidence is not None

            # 旧证据的诊断在后台重试；新证据（较新发生）的诊断先完成归并。
            time.sleep(1.05)
            plan.default = _plan_pose(_Q2)
            text.answer = f"再检查一题。{_Q2}"
            _send(client, conversation_id, "继续检查。", "issue05-oo-3")
            generation_helpers["drive"](app)
            time.sleep(1.05)
            plan.default = _plan_evaluate()
            text.answer = "独立时 P(A∩B)=P(A)P(B)。"
            _send(client, conversation_id, "独立时 P(A∩B)=P(A)P(B)。", "issue05-oo-4")
            generation_helpers["drive"](app)

            detail = _detail(client, conversation_id)
            record = detail["study_learning_record"][0]
            assert record["verdict"] == "independent_completion"
            assert record["performance"] == "能推导独立时的乘法公式。"
            assert record["pending_confirmation"] is False
            assert record["evidence_count"] == 1
            old_task = next(
                item
                for item in detail["study_diagnosis_tasks"]
                if item["question"] == _Q1
            )
            assert old_task["status"] == "failed"
            assert old_task["retryable"] is True

            # 旧证据的诊断迟到完成：不覆盖新判断，但方向冲突如实待确认。
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
            assert record["verdict"] == "independent_completion"
            assert record["performance"] == "能推导独立时的乘法公式。"
            assert record["pending_confirmation"] is True
            assert record["evidence_count"] == 2
            # 证据按发生顺序排列：旧在前、新在后。
            evidences = record["evidences"]
            assert evidences[0]["evidence_id"] == old_evidence["evidence_id"]
            assert evidences[0]["verdict"] == "misconception"
            assert evidences[1]["verdict"] == "independent_completion"
            assert _table_count(database, "study_diagnosis_results") == 2
    finally:
        _close(app)


# ---------------------------------------------------------------------------
# 来源失效与版本冲突不写入档案
# ---------------------------------------------------------------------------


def test_invalidated_source_and_version_mismatch_do_not_write_record(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _TextTutorAdapter(f"先检查一个关键点。{_Q1}")
    plan = _PlanAdapter(default=_plan_pose(_Q1))
    structured = _StructuredAdapter(plan)
    _wire(app, text, structured)
    structured.diagnosis_outputs[_Q1] = TransientError("diagnosis unavailable")
    structured.diagnosis_outputs[_Q2] = TransientError("diagnosis unavailable")

    try:
        with TestClient(app) as client:
            _register(client, "issue05-invalidated")
            database = app.state.bridges_database
            conversation_id = _start_study(client, "互斥是什么？", "issue05-inv-1")
            generation_helpers["drive"](app)
            plan.default = _plan_evaluate()
            text.answer = "互斥时交集为空，所以 P(A∩B)=0。"
            _send(client, conversation_id, _ANSWER, "issue05-inv-2")
            generation_helpers["drive"](app)
            first = database.connection.execute(
                "SELECT task_id, evidence_id, status FROM study_diagnosis_tasks"
            ).fetchone()
            assert first["status"] == "pending"

            # 删除来源证据后继续驱动：任务标记失效，不生成诊断、不写档案。
            database.connection.execute(
                "DELETE FROM study_evidence WHERE evidence_id = ?",
                (first["evidence_id"],),
            )
            time.sleep(1.05)
            generation_helpers["drive"](app)
            row = database.connection.execute(
                "SELECT status, failure_reason FROM study_diagnosis_tasks"
            ).fetchone()
            assert row["status"] == "invalidated"
            assert "来源证据已失效" in row["failure_reason"]

            # 输入版本冲突（证据检查版本变化）同样不写入。
            plan.default = _plan_pose(_Q2)
            text.answer = f"再检查一题。{_Q2}"
            _send(client, conversation_id, "继续检查。", "issue05-inv-3")
            generation_helpers["drive"](app)
            plan.default = _plan_evaluate()
            text.answer = "独立时 P(A∩B)=P(A)P(B)。"
            _send(client, conversation_id, "独立时 P(A∩B)=P(A)P(B)。", "issue05-inv-4")
            generation_helpers["drive"](app)
            second = database.connection.execute(
                "SELECT task_id, evidence_id, status FROM study_diagnosis_tasks"
                " WHERE task_id != ?",
                (first["task_id"],),
            ).fetchone()
            assert second["status"] == "pending"
            database.connection.execute(
                "UPDATE study_evidence SET check_version = check_version + 1"
                " WHERE evidence_id = ?",
                (second["evidence_id"],),
            )
            time.sleep(1.05)
            generation_helpers["drive"](app)

            rows = database.connection.execute(
                "SELECT status, failure_reason FROM study_diagnosis_tasks"
                " ORDER BY rowid"
            ).fetchall()
            assert rows[0]["status"] == "invalidated"
            assert rows[1]["status"] == "invalidated"
            assert "证据输入版本已变化" in rows[1]["failure_reason"]
            assert _table_count(database, "study_diagnosis_results") == 0
            assert _table_count(database, "study_learning_records") == 0
            detail = _detail(client, conversation_id)
            assert detail["study_learning_record"] == []
            assert {
                item["status"] for item in detail["study_diagnosis_tasks"]
            } == {"invalidated"}
    finally:
        _close(app)


# ---------------------------------------------------------------------------
# 租约恢复、失败保留与手动重试
# ---------------------------------------------------------------------------


def test_lease_recovery_and_manual_retry_preserve_record_until_success(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _TextTutorAdapter(f"先检查一个关键点。{_Q1}")
    plan = _PlanAdapter(default=_plan_pose(_Q1))
    structured = _StructuredAdapter(plan)
    _wire(app, text, structured)

    try:
        with TestClient(app) as client:
            _register(client, "issue05-retry")
            database = app.state.bridges_database
            conversation_id = _start_study(client, "互斥是什么？", "issue05-r-1")
            generation_helpers["drive"](app)

            # 第一条证据成功：档案形成。
            plan.default = _plan_evaluate()
            text.answer = "互斥时交集为空，所以 P(A∩B)=0。"
            _send(client, conversation_id, _ANSWER, "issue05-r-2")
            generation_helpers["drive"](app)
            baseline_version = _record_version(database)

            # 第二条证据：诊断持续失败，有界重试耗尽后任务进入失败态。
            structured.diagnosis_outputs[_Q2] = TransientError(
                "diagnosis unavailable"
            )
            plan.default = _plan_pose(_Q2)
            text.answer = f"再检查一题。{_Q2}"
            _send(client, conversation_id, "继续检查。", "issue05-r-3")
            generation_helpers["drive"](app)
            plan.default = _plan_evaluate()
            text.answer = "独立时 P(A∩B)=P(A)P(B)。"
            _send(client, conversation_id, "独立时 P(A∩B)=P(A)P(B)。", "issue05-r-4")
            generation_helpers["drive"](app)
            for _ in range(2):
                time.sleep(1.05)
                generation_helpers["drive"](app)

            detail = _detail(client, conversation_id)
            failed = next(
                item
                for item in detail["study_diagnosis_tasks"]
                if item["status"] == "failed"
            )
            assert failed["retryable"] is True
            assert failed["attempts"] == 3
            assert "诊断服务暂时不可用" in failed["failure_reason"]
            # 失败保留原档案：不判学生错、不覆盖已有判断。
            record = detail["study_learning_record"][0]
            assert record["verdict"] == "independent_completion"
            assert record["performance"] == "能说明互斥不等于独立。"
            assert record["evidence_count"] == 1
            assert _record_version(database) == baseline_version

            # 已成功任务不可手动重试；不存在任务返回 404。
            succeeded = next(
                item
                for item in detail["study_diagnosis_tasks"]
                if item["status"] == "succeeded"
            )
            conflict = client.post(
                f"/chat/conversations/{conversation_id}/study/diagnosis/"
                f"{succeeded['task_id']}/retry"
            )
            assert conflict.status_code == 409
            missing = client.post(
                f"/chat/conversations/{conversation_id}/study/diagnosis/dx-missing/retry"
            )
            assert missing.status_code == 404

            # 手动重试（同一任务、同一幂等单元）：能力恢复后成功且只记一次。
            structured.diagnosis_outputs[_Q2] = _diagnosis_output(
                performance="能推导独立时的乘法公式。",
            )
            retried = client.post(
                f"/chat/conversations/{conversation_id}/study/diagnosis/"
                f"{failed['task_id']}/retry"
            )
            assert retried.status_code == 200, retried.text
            assert retried.json()["status"] == "pending"
            generation_helpers["drive"](app)

            detail = _detail(client, conversation_id)
            record = detail["study_learning_record"][0]
            assert record["verdict"] == "independent_completion"
            assert record["performance"] == "能推导独立时的乘法公式。"
            assert record["evidence_count"] == 2
            assert record["pending_confirmation"] is False
            assert _table_count(database, "study_diagnosis_results") == 2
            assert _table_count(database, "study_diagnosis_tasks") == 2

            # 租约恢复：模拟进程崩溃留下的已领取任务，只能被完成一次。
            structured.diagnosis_outputs[_Q2] = TransientError(
                "diagnosis unavailable"
            )
            plan.default = _plan_pose(_Q2)
            text.answer = f"再看一题。{_Q2}"
            _send(client, conversation_id, "再看一题。", "issue05-r-5")
            generation_helpers["drive"](app)
            plan.default = _plan_evaluate()
            text.answer = "独立时 P(A∩B)=P(A)P(B)。"
            _send(client, conversation_id, "独立时 P(A∩B)=P(A)P(B)。", "issue05-r-6")
            generation_helpers["drive"](app)
            pending_task = database.connection.execute(
                "SELECT task_id FROM study_diagnosis_tasks"
                " WHERE status = 'pending' ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            assert pending_task is not None
            database.connection.execute(
                "UPDATE task_claims SET status = 'claimed', worker = 'crashed',"
                " lease_expires_at = ? WHERE queue_name = 'study-diagnosis'"
                " AND task_key = ?",
                (
                    (datetime.now(UTC) - timedelta(seconds=30)).isoformat(),
                    pending_task["task_id"],
                ),
            )
            structured.diagnosis_outputs[_Q2] = _diagnosis_output(
                performance="再次验证通过。",
            )
            generation_helpers["drive"](app)
            assert _table_count(database, "study_diagnosis_results") == 3
            assert (
                database.connection.execute(
                    "SELECT COUNT(*) AS count FROM study_diagnosis_results"
                    " WHERE task_id = ?",
                    (pending_task["task_id"],),
                ).fetchone()["count"]
                == 1
            )
            record = _detail(client, conversation_id)["study_learning_record"][0]
            assert record["performance"] == "再次验证通过。"
            assert record["evidence_count"] == 3
    finally:
        _close(app)


def test_permanent_failure_has_real_state_and_can_be_retried(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _TextTutorAdapter(f"先检查一个关键点。{_Q1}")
    plan = _PlanAdapter(default=_plan_pose(_Q1))
    structured = _StructuredAdapter(plan)
    _wire(app, text, structured)

    class _PermanentGateway:
        def invoke(self, *args: Any, **kwargs: Any) -> Any:
            return SimpleNamespace(
                status=SimpleNamespace(value="permanent_fail"), output=None
            )

    try:
        with TestClient(app) as client:
            _register(client, "issue05-permanent")
            database = app.state.bridges_database
            conversation_id = _start_study(client, "互斥是什么？", "issue05-p-1")
            generation_helpers["drive"](app)
            app.state.study_diagnosis_service._gateway = _PermanentGateway()

            plan.default = _plan_evaluate()
            text.answer = "互斥时交集为空，所以 P(A∩B)=0。"
            _send(client, conversation_id, _ANSWER, "issue05-p-2")
            generation_helpers["drive"](app)
            row = database.connection.execute(
                "SELECT status, failure_reason FROM study_diagnosis_tasks"
            ).fetchone()
            assert row["status"] == "failed"
            assert "诊断能力未配置或不可用" in row["failure_reason"]

            detail = _detail(client, conversation_id)
            failed = detail["study_diagnosis_tasks"][0]
            assert failed["status"] == "failed"
            assert failed["retryable"] is True
            assert detail["study_learning_record"] == []

            # 恢复能力后手动重试：同一任务产生唯一结果。
            app.state.study_diagnosis_service._gateway = _gateway_with_diagnosis(
                text, structured
            )
            retried = client.post(
                f"/chat/conversations/{conversation_id}/study/diagnosis/"
                f"{failed['task_id']}/retry"
            )
            assert retried.status_code == 200, retried.text
            generation_helpers["drive"](app)
            detail = _detail(client, conversation_id)
            assert detail["study_diagnosis_tasks"][0]["status"] == "succeeded"
            assert len(detail["study_learning_record"]) == 1
    finally:
        _close(app)


# ---------------------------------------------------------------------------
# 应用重启恢复与账户隔离
# ---------------------------------------------------------------------------


def test_restart_recovers_pending_task_and_accounts_stay_isolated(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _TextTutorAdapter(f"先检查一个关键点。{_Q1}")
    plan = _PlanAdapter(default=_plan_pose(_Q1))
    structured = _StructuredAdapter(plan)
    _wire(app, text, structured)
    structured.diagnosis_outputs[_Q1] = TransientError("diagnosis unavailable")
    conversation_id: str | None = None

    with TestClient(app) as client:
        _register(client, "issue05-restart")
        database = app.state.bridges_database
        conversation_id = _start_study(client, "互斥是什么？", "issue05-rs-1")
        generation_helpers["drive"](app)
        plan.default = _plan_evaluate()
        text.answer = "互斥时交集为空，所以 P(A∩B)=0。"
        _send(client, conversation_id, _ANSWER, "issue05-rs-2")
        generation_helpers["drive"](app)
        row = database.connection.execute(
            "SELECT status FROM study_diagnosis_tasks"
        ).fetchone()
        assert row["status"] == "pending"
    _close(app)

    # 应用重启（同一数据库、新的进程内对象）：有效任务按持久化状态恢复。
    restarted = _app(tmp_path, monkeypatch)
    text2 = _TextTutorAdapter(f"先检查一个关键点。{_Q1}")
    plan2 = _PlanAdapter(default=_plan_pose(_Q1))
    structured2 = _StructuredAdapter(plan2)
    _wire(restarted, text2, structured2)

    try:
        with TestClient(restarted) as client:
            login = client.post(
                "/auth/login",
                json={"identifier": "issue05-restart_user", "password": "Passw0rd123!"},
            )
            assert login.status_code == 200, login.text
            # 上次失败留下 1 秒退避：等待到期后由新进程执行器领取。
            time.sleep(1.2)
            generation_helpers["drive"](restarted)

            assert (
                _table_count(
                    restarted.state.bridges_database, "study_learning_records"
                )
                == 1
            )
            detail = _detail(client, conversation_id or "")
            assert detail["study_diagnosis_tasks"][0]["status"] == "succeeded"
            record = detail["study_learning_record"][0]
            assert record["verdict"] == "independent_completion"

            # 账户隔离：另一账户看不到任务、档案与重试入口。
            with TestClient(restarted) as other_client:
                _register(other_client, "issue05-restart-other")
                blocked = other_client.get(f"/chat/conversations/{conversation_id}")
                assert blocked.status_code == 404
                task_id = detail["study_diagnosis_tasks"][0]["task_id"]
                denied = other_client.post(
                    f"/chat/conversations/{conversation_id}/study/diagnosis/"
                    f"{task_id}/retry"
                )
                assert denied.status_code == 404
    finally:
        _close(restarted)
