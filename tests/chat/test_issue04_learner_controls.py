"""Issue 04：直接讲解、跳过验证、暂停/继续与阶段小结。

覆盖验收项：
1. 明确要求完整讲解得到完整内容，解除等待，不强制继续追问；
2. 跳过解除正确的问题等待，未验证不算错答、掌握或缺口；
3. 未验证不是强制门禁，可以继续下一步；
4. 暂停位置可恢复；停止生成与暂停学习分开；
5. 小结仅使用实际证据，未核验项保持待确认，不输出掌握百分比；
6. 单次答对仅描述本次表现，收尾不自动增加考试；
7. 小结失败独立重试，不重判、不重复保存、不覆盖历史；
8. 旧控制消息重试不影响新待答问题。
"""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi.testclient import TestClient

from bridges.ai.adapters import RateLimitError
from bridges.contracts.study_controls import CONTROLS_CONTRACT_VERSION
from bridges.study.controls import StudyControlRepository
from bridges.study.verification import StudyVerificationRepository
from tests.chat.test_issue02_text_tutor import _TextTutorAdapter
from tests.chat.test_issue03_embedded_verification import (
    _Q1,
    _Q2,
    _gateway_with_verification,
    _plan,
    _plan_evaluate,
    _plan_pose,
    _PlanAdapter,
)
from tests.chat.test_v2_05_photo_attachments import _app, _register

_Q3 = "再检查一下：独立事件满足什么等式？"

_SUMMARY_MARKER = "【本轮阶段小结（必须原样包含）】"
_SUMMARY_SENTINEL = "不要追加新的测验或要求作答的问题"


def _extract_summary_block(payload: dict[str, Any]) -> str | None:
    """按生成载荷中的指令提取小结全文（模拟合规模型的原样交付）。"""

    system = "\n".join(
        str(message.get("content") or "")
        for message in payload.get("messages", [])
        if message.get("role") == "system"
    )
    if _SUMMARY_MARKER not in system:
        return None
    tail = system.split(_SUMMARY_MARKER, 1)[1]
    lines = tail.splitlines()
    start = next(
        (index for index, line in enumerate(lines) if line.startswith("【本阶段小结】")),
        None,
    )
    if start is None:
        return None
    block: list[str] = []
    for line in lines[start:]:
        if line.startswith(_SUMMARY_SENTINEL):
            break
        block.append(line)
    text = "\n".join(block).strip()
    return text or None


class _SummaryAwareTutor(_TextTutorAdapter):
    """合规替身：系统指令要求小结时原样返回小结全文，其余按固定回答。

    ``follow_summary`` 关闭时模拟无视指令的回复（用于覆盖 partial 状态）。
    """

    def __init__(self, answer: str) -> None:
        super().__init__(answer)
        self.follow_summary = True

    def stream_call(
        self, capability: Any, run_context: Any, payload: dict[str, Any]
    ) -> Any:
        if self.follow_summary:
            block = _extract_summary_block(payload)
            if block is not None:
                self.answer = "好，我把这次进展收个尾。\n" + block
        return super().stream_call(capability, run_context, payload)


def _start_study(client: TestClient, content: str, key: str) -> str:
    response = client.post(
        "/chat/first-turn",
        json={"mode": "study", "content": content, "idempotency_key": key},
    )
    assert response.status_code == 201, response.text
    return response.json()["conversation"]["conversation_id"]


def _send(
    client: TestClient, conversation_id: str, content: str, key: str
) -> str:
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": content, "idempotency_key": key},
    )
    assert response.status_code == 200, response.text
    return response.json()["assistant_message"]["message_id"]


def _close(app: Any) -> None:
    app.state.bridges_database.close()
    app.state.state_store.close()


def _controls(app: Any, account_id: str, conversation_id: str) -> StudyControlRepository:
    return StudyControlRepository(app.state.bridges_database)


def _rows(app: Any, sql: str, params: tuple[Any, ...] = ()) -> list[Any]:
    return app.state.bridges_database.connection.execute(sql, params).fetchall()


# ---------------------------------------------------------------------------
# 完整讲解与继续不受未验证门禁阻塞
# ---------------------------------------------------------------------------


def test_full_explanation_releases_wait_and_never_forces_quiz(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _TextTutorAdapter(f"先检查一下。{_Q1}")
    plan = _PlanAdapter(default=_plan_pose(_Q1))
    app.state.chat_service._gateway = _gateway_with_verification(text, plan)

    try:
        with TestClient(app) as client:
            account = _register(client, "issue04-explain")
            database = app.state.bridges_database
            conversation_id = _start_study(client, "互斥是什么？", "i04-explain-1")
            generation_helpers["drive"](app)
            assert client.get(
                f"/chat/conversations/{conversation_id}"
            ).json()["study_pending_check"] is not None

            # 「直接完整讲解，我不想答题」：完整说明 + 解除等待，不追加测验。
            # 规划器即使给出新题，也必须被抑制。
            plan.default = _plan_pose(_Q2)
            plan.default["intent"] = "full_explanation"
            plan.default["action"] = "help"
            text.answer = (
                "完整说明：互斥指两个事件不能同时发生，交集为空；"
                "独立指一个事件发生不影响另一个的概率。两者含义不同，"
                "互斥通常不独立（除非概率为零）。"
            )
            assistant_message_id = _send(
                client, conversation_id, "直接完整讲解，我不想答题。", "i04-explain-2"
            )
            generation_helpers["drive"](app)

            detail = client.get(f"/chat/conversations/{conversation_id}").json()
            assert detail["study_pending_check"] is None
            assert detail["messages"][-1]["content"] == text.answer
            # 回复中不包含被抑制的新问题/强制测验指令。
            directive = str(text.payloads[-1]["messages"][0]["content"])
            assert "必须原样包含以下需要学生作答的问题" not in directive
            assert "直接完整讲解" in directive

            repository = StudyVerificationRepository(database)
            evidence = repository.list_evidence(account["id"], conversation_id)
            assert len(evidence) == 1
            assert evidence[0].question == _Q1
            assert evidence[0].semantics.value == "unverified"
            assert evidence[0].answered is False
            assert evidence[0].outcome.value == "no_answer"
            # 未验证不写长期掌握或能力标签。
            assert database.connection.execute(
                "SELECT COUNT(*) FROM study_states WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()[0] == 0

            control = detail["study_last_control"]
            assert control is not None
            assert control["kind"] == "full_explanation"
            assert control["question"] == _Q1
            assert control["released"] is True
            # 控制状态归到真正承载该结果的助手回合，供逐条消息定位。
            assert control["assistant_message_id"] == assistant_message_id

            # 未验证不是门禁：可以继续下一步并获得新检查。
            plan.default = _plan_pose(_Q2)
            text.answer = f"那我们看下一步。{_Q2}"
            _send(client, conversation_id, "继续下一步。", "i04-explain-3")
            generation_helpers["drive"](app)
            pending = client.get(
                f"/chat/conversations/{conversation_id}"
            ).json()["study_pending_check"]
            assert pending is not None and pending["question"] == _Q2
    finally:
        _close(app)


def test_skip_releases_only_bound_check_and_records_unverified(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _TextTutorAdapter(f"先检查一下。{_Q1}")
    plan = _PlanAdapter(default=_plan_pose(_Q1))
    app.state.chat_service._gateway = _gateway_with_verification(text, plan)

    try:
        with TestClient(app) as client:
            account = _register(client, "issue04-skip")
            database = app.state.bridges_database
            conversation_id = _start_study(client, "互斥是什么？", "i04-skip-1")
            generation_helpers["drive"](app)

            plan.default = _plan(action="help", intent="skip")
            text.answer = f"好，这题先放一放，保持未验证。{_Q2}"
            skip_assistant_id = _send(
                client, conversation_id, "这题跳过吧。", "i04-skip-2"
            )
            generation_helpers["drive"](app)

            detail = client.get(f"/chat/conversations/{conversation_id}").json()
            assert detail["study_pending_check"] is None
            control = detail["study_last_control"]
            assert control["kind"] == "skip"
            assert control["released"] is True
            assert control["question"] == _Q1
            assert control["assistant_message_id"] == skip_assistant_id

            evidence = StudyVerificationRepository(database).list_evidence(
                account["id"], conversation_id
            )
            assert len(evidence) == 1
            assert evidence[0].semantics.value == "unverified"
            assert evidence[0].answered is False
            assert evidence[0].outcome.value == "no_answer"

            # 没有待答问题时跳过只记录控制，不产生任何证据或错误判定。
            plan.default = _plan(action="help", intent="skip")
            text.answer = "现在没有待答问题，我们继续讲。"
            _send(client, conversation_id, "继续讲吧。", "i04-skip-3")
            generation_helpers["drive"](app)
            assert len(
                StudyVerificationRepository(database).list_evidence(
                    account["id"], conversation_id
                )
            ) == 1
            latest = _controls(app, account["id"], conversation_id).latest_control(
                account["id"], conversation_id
            )
            assert latest is not None and latest.released is False
    finally:
        _close(app)


# ---------------------------------------------------------------------------
# 暂停、继续与停止生成
# ---------------------------------------------------------------------------


def test_pause_saves_position_and_resume_clears_it(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _SummaryAwareTutor(f"先检查一下。{_Q1}")
    plan = _PlanAdapter(default=_plan_pose(_Q1))
    app.state.chat_service._gateway = _gateway_with_verification(text, plan)

    try:
        with TestClient(app) as client:
            account = _register(client, "issue04-pause")
            database = app.state.bridges_database
            conversation_id = _start_study(client, "互斥是什么？", "i04-pause-1")
            generation_helpers["drive"](app)

            plan.default = _plan(action="help", intent="pause")
            _send(client, conversation_id, "今天先到这里吧。", "i04-pause-2")
            generation_helpers["drive"](app)

            detail = client.get(f"/chat/conversations/{conversation_id}").json()
            pause = detail["study_pause"]
            assert pause is not None
            assert pause["position"] == "今天先到这里吧。"
            assert pause["pending_question"] == _Q1
            assert pause["progress_version"] >= 2
            # 暂停不解除待答问题：问题与未验证状态如实保留。
            assert detail["study_pending_check"]["question"] == _Q1
            # 暂停附带简短小结，触发来源为 pause，且原文完整交付。
            summary = detail["study_summary"]
            assert summary is not None
            assert summary["trigger"] == "pause"
            assert summary["status"] == "complete"
            assert "【本阶段小结】" in summary["content"]
            assert summary["content"] in detail["messages"][-1]["content"]

            rows = _rows(
                app,
                "SELECT kind, released FROM study_session_controls"
                " WHERE conversation_id = ?",
                (conversation_id,),
            )
            assert [(row["kind"], row["released"]) for row in rows] == [("pause", 0)]

            # 刷新后暂停位置与小结仍在；继续后暂停被解除，问题与证据不变。
            refreshed = client.get(f"/chat/conversations/{conversation_id}").json()
            assert refreshed["study_pause"] == pause
            before_evidence = StudyVerificationRepository(database).list_evidence(
                account["id"], conversation_id
            )

            plan.default = _plan(action="none", intent="resume")
            text.answer = "好，我们接着上次的进度继续。"
            _send(client, conversation_id, "继续学吧。", "i04-pause-3")
            generation_helpers["drive"](app)

            detail = client.get(f"/chat/conversations/{conversation_id}").json()
            assert detail["study_pause"] is None
            assert detail["study_pending_check"]["question"] == _Q1
            assert detail["study_last_control"]["kind"] == "resume"
            assert StudyVerificationRepository(database).list_evidence(
                account["id"], conversation_id
            ) == before_evidence
            assert database.connection.execute(
                "SELECT COUNT(*) FROM study_session_pauses WHERE resumed_at IS NULL"
                " AND conversation_id = ?",
                (conversation_id,),
            ).fetchone()[0] == 0
    finally:
        _close(app)


def test_stop_generation_is_not_pause(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _SummaryAwareTutor(f"先检查一下。{_Q1}")
    plan = _PlanAdapter(default=_plan_pose(_Q1))
    app.state.chat_service._gateway = _gateway_with_verification(text, plan)

    try:
        with TestClient(app) as client:
            _register(client, "issue04-stop")
            database = app.state.bridges_database
            conversation_id = _start_study(client, "互斥是什么？", "i04-stop-1")
            generation_helpers["drive"](app)

            # 暂停意图的回复正在生成时用户停止：传输中断不是学习控制。
            plan.default = _plan(action="help", intent="pause")
            text.answer = "好，我们收个尾并保存进展。" * 30
            text.slow = True
            stop_executor, executor_thread = generation_helpers["executor_thread"](app)
            control_client = TestClient(app)
            control_client.cookies.set(
                "bridges_session", client.cookies.get("bridges_session")
            )
            try:
                sent = client.post(
                    f"/chat/conversations/{conversation_id}/messages",
                    json={"content": "今天先到这里。", "idempotency_key": "i04-stop-2"},
                )
                assert sent.status_code == 200, sent.text
                message_id = sent.json()["assistant_message"]["message_id"]
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    message = control_client.get(
                        f"/chat/conversations/{conversation_id}"
                    ).json()["messages"][-1]
                    if message["content"]:
                        break
                    time.sleep(0.02)
                stopped = control_client.post(
                    f"/chat/conversations/{conversation_id}/messages/{message_id}/stop"
                )
                assert stopped.status_code == 200, stopped.text
                assert stopped.json()["message"]["status"] == "stopped"
                detail = control_client.get(
                    f"/chat/conversations/{conversation_id}"
                ).json()
                assert detail["study_pause"] is None
                assert detail["study_summary"] is None
                assert detail["study_last_control"] is None
                assert detail["study_pending_check"]["question"] == _Q1
                assert detail["study_local_progress"]["version"] == 1
                assert database.connection.execute(
                    "SELECT COUNT(*) FROM study_session_pauses"
                ).fetchone()[0] == 0
                assert database.connection.execute(
                    "SELECT COUNT(*) FROM study_session_controls"
                ).fetchone()[0] == 0
            finally:
                stop_executor.set()
                executor_thread.join(timeout=5)
                control_client.close()
    finally:
        _close(app)


# ---------------------------------------------------------------------------
# 阶段小结：实际证据、真实状态与独立重试
# ---------------------------------------------------------------------------


def test_summary_uses_actual_evidence_without_mastery_claims(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _SummaryAwareTutor(f"先检查一下。{_Q1}")
    plan = _PlanAdapter(default=_plan_pose(_Q1))
    app.state.chat_service._gateway = _gateway_with_verification(text, plan)

    try:
        with TestClient(app) as client:
            account = _register(client, "issue04-summary")
            database = app.state.bridges_database
            conversation_id = _start_study(client, "互斥是什么？", "i04-summary-1")
            generation_helpers["drive"](app)

            plan.default = _plan_evaluate()
            text.answer = "因为不能同时发生，所以交集为空。"
            _send(client, conversation_id, "互斥时交集为空，所以 P(A∩B)=0。", "i04-summary-2")
            generation_helpers["drive"](app)

            plan.default = _plan(action="none", intent="summary_request")
            _send(client, conversation_id, "帮我小结一下这次学习。", "i04-summary-3")
            generation_helpers["drive"](app)

            detail = client.get(f"/chat/conversations/{conversation_id}").json()
            summary = detail["study_summary"]
            assert summary is not None
            assert summary["trigger"] == "explicit_request"
            assert summary["status"] == "complete"
            content = summary["content"]
            assert "本次验证（即时反馈）" in content
            assert "本次独立完成" in content
            assert "独立诊断尚未提供" in content
            assert "不代表长期掌握" in content
            # 不输出掌握百分比或长期能力结论。
            assert "%" not in content and "％" not in content
            assert "掌握度" not in content and "掌握率" not in content
            # 原文完整随回复交付。
            assert content in detail["messages"][-1]["content"]

            # 依据引用全部指向实际存在的证据；正式诊断未提供时保持待确认。
            row = database.connection.execute(
                "SELECT basis_json, progress_version FROM study_session_summaries"
                " WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            basis = json.loads(row["basis_json"])
            evidence_ids = {
                item.evidence_id
                for item in StudyVerificationRepository(database).list_evidence(
                    account["id"], conversation_id
                )
            }
            assert {item["evidence_id"] for item in basis["items"]} == evidence_ids
            assert basis["items"][0]["verification"] == "immediate_feedback"
            assert basis["formal_diagnosis"]["status"] == "not_available"
            assert basis["progress"]["version"] == 2
            assert row["progress_version"] == 3
            assert basis["controls_contract_version"] == CONTROLS_CONTRACT_VERSION

            # 后续学习不覆盖历史小结文字：新的一轮后小结原文不变。
            plan.default = _plan()
            text.answer = "我们继续。"
            _send(client, conversation_id, "继续。", "i04-summary-4")
            generation_helpers["drive"](app)
            again = client.get(
                f"/chat/conversations/{conversation_id}"
            ).json()["study_summary"]
            assert again["summary_id"] == summary["summary_id"]
            assert again["content"] == content
    finally:
        _close(app)


def test_natural_completion_summary_does_not_add_exam(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _SummaryAwareTutor(f"先检查一下。{_Q1}")
    plan = _PlanAdapter(default=_plan_pose(_Q1))
    app.state.chat_service._gateway = _gateway_with_verification(text, plan)

    try:
        with TestClient(app) as client:
            account = _register(client, "issue04-natural")
            database = app.state.bridges_database
            conversation_id = _start_study(client, "互斥是什么？", "i04-natural-1")
            generation_helpers["drive"](app)

            # 作答正确且规划自然收尾；同轮即使给出新题也必须被抑制。
            follow_up = _plan_evaluate()
            follow_up.update(
                wrap_up=True,
                check_kind="formal_check",
                check_question=_Q2,
                check_knowledge_context="互斥与独立",
                evaluation_points=["交集为空"],
                accepted_equivalents=["交集为空"],
                key_misconceptions=[],
                assumptions=[],
                expected_answer="0",
            )
            plan.default = follow_up
            _send(client, conversation_id, "互斥时交集为空，所以 P(A∩B)=0。", "i04-natural-2")
            generation_helpers["drive"](app)

            detail = client.get(f"/chat/conversations/{conversation_id}").json()
            assert detail["study_pending_check"] is None
            summary = detail["study_summary"]
            assert summary is not None
            assert summary["trigger"] == "natural_completion"
            assert summary["status"] == "complete"
            assert "本次独立完成" in summary["content"]
            # 收尾不自动增加考试：不出现新问题，也不要求继续作答。
            directive = str(text.payloads[-1]["messages"][0]["content"])
            assert "必须原样包含以下需要学生作答的问题" not in directive
            assert _Q2 not in directive
            assert "不要追加新的测验或要求作答的问题" in directive

            evidence = StudyVerificationRepository(database).list_evidence(
                account["id"], conversation_id
            )
            assert len(evidence) == 1
            assert evidence[0].semantics.value == "independent_completion"
    finally:
        _close(app)


def test_summary_failure_retries_only_summary_without_rejudging(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _SummaryAwareTutor("先讲解。" + _Q1)
    plan = _PlanAdapter(default=_plan_pose(_Q1))
    app.state.chat_service._gateway = _gateway_with_verification(text, plan)

    try:
        with TestClient(app) as client:
            account = _register(client, "issue04-retry")
            database = app.state.bridges_database
            conversation_id = _start_study(client, "互斥是什么？", "i04-retry-1")
            generation_helpers["drive"](app)

            plan.default = _plan(action="none", intent="summary_request")
            text.failure = _temporary_failure()
            failed_id = _send(client, conversation_id, "帮我小结。", "i04-retry-2")
            generation_helpers["drive"](app)
            assert client.get(
                f"/chat/conversations/{conversation_id}"
            ).json()["messages"][-1]["status"] == "error"
            assert database.connection.execute(
                "SELECT COUNT(*) FROM study_session_summaries"
            ).fetchone()[0] == 0
            plan_calls = len(plan.payloads)

            # 重试只补小结：重放冻结快照（不重新规划/重判），只保存一条。
            text.failure = None
            retried = client.post(
                f"/chat/conversations/{conversation_id}/messages/{failed_id}/retry"
            )
            assert retried.status_code == 200, retried.text
            generation_helpers["drive"](app)
            assert len(plan.payloads) == plan_calls

            detail = client.get(f"/chat/conversations/{conversation_id}").json()
            assert detail["messages"][-1]["status"] == "done"
            summary = detail["study_summary"]
            assert summary is not None and summary["status"] == "complete"
            assert database.connection.execute(
                "SELECT COUNT(*) FROM study_session_summaries"
            ).fetchone()[0] == 1
            assert StudyVerificationRepository(database).list_evidence(
                account["id"], conversation_id
            ) == []
    finally:
        _close(app)


def test_partial_summary_records_true_status_and_retry_upgrades(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _SummaryAwareTutor(f"先检查一下。{_Q1}")
    text.follow_summary = False
    plan = _PlanAdapter(default=_plan_pose(_Q1))
    app.state.chat_service._gateway = _gateway_with_verification(text, plan)

    try:
        with TestClient(app) as client:
            _register(client, "issue04-partial")
            database = app.state.bridges_database
            conversation_id = _start_study(client, "互斥是什么？", "i04-partial-1")
            generation_helpers["drive"](app)

            # 回复没有包含规范小结原文：如实记录 partial，不虚报成功。
            plan.default = _plan(action="none", intent="summary_request")
            message_id = _send(client, conversation_id, "帮我小结。", "i04-partial-2")
            generation_helpers["drive"](app)

            detail = client.get(f"/chat/conversations/{conversation_id}").json()
            summary = detail["study_summary"]
            assert summary is not None
            assert summary["status"] == "partial"
            assert "【本阶段小结】" in summary["content"]
            row = database.connection.execute(
                "SELECT content FROM study_session_summaries"
            ).fetchone()
            canonical = row["content"]

            # 合规重试补全同一条小结：状态升级，行不重复、原文不覆盖。
            text.follow_summary = True
            retried = client.post(
                f"/chat/conversations/{conversation_id}/messages/{message_id}/retry"
            )
            assert retried.status_code == 200, retried.text
            generation_helpers["drive"](app)

            detail = client.get(f"/chat/conversations/{conversation_id}").json()
            assert detail["study_summary"]["status"] == "complete"
            assert database.connection.execute(
                "SELECT COUNT(*) FROM study_session_summaries"
            ).fetchone()[0] == 1
            after = database.connection.execute(
                "SELECT content FROM study_session_summaries"
            ).fetchone()
            assert after["content"] == canonical
    finally:
        _close(app)


def test_old_control_retry_does_not_touch_new_pending_check(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _TextTutorAdapter(f"先检查一下。{_Q1}")
    plan = _PlanAdapter(default=_plan_pose(_Q1))
    app.state.chat_service._gateway = _gateway_with_verification(text, plan)

    try:
        with TestClient(app) as client:
            account = _register(client, "issue04-old-control")
            database = app.state.bridges_database
            conversation_id = _start_study(client, "互斥是什么？", "i04-old-1")
            generation_helpers["drive"](app)

            # 跳过 Q1 并给出 Q2。
            plan.default = _plan_pose(_Q2)
            plan.default["intent"] = "skip"
            text.answer = f"好，看下一个。{_Q2}"
            _send(client, conversation_id, "跳过吧，换个问题。", "i04-old-2")
            generation_helpers["drive"](app)
            skip_message_id = client.get(
                f"/chat/conversations/{conversation_id}"
            ).json()["messages"][-1]["message_id"]

            # 作答 Q2 并给出 Q3。
            plan.default = _plan_evaluate()
            text.answer = "因为不能同时发生，所以交集为空。"
            _send(client, conversation_id, "互斥时交集为空。", "i04-old-3")
            generation_helpers["drive"](app)
            plan.default = _plan_pose(_Q3)
            plan.default["intent"] = "other"
            text.answer = f"再看一个。{_Q3}"
            _send(client, conversation_id, "继续。", "i04-old-4")
            generation_helpers["drive"](app)
            detail = client.get(f"/chat/conversations/{conversation_id}").json()
            assert detail["study_pending_check"]["question"] == _Q3
            evidence_before = StudyVerificationRepository(database).list_evidence(
                account["id"], conversation_id
            )
            assert [item.semantics.value for item in evidence_before] == [
                "unverified",
                "independent_completion",
            ]

            # 重试旧跳过消息：重放旧快照，不解除新问题、不重复记账。
            plan_calls = len(plan.payloads)
            retried = client.post(
                f"/chat/conversations/{conversation_id}/messages/{skip_message_id}/retry"
            )
            assert retried.status_code == 200, retried.text
            generation_helpers["drive"](app)
            assert len(plan.payloads) == plan_calls

            detail = client.get(f"/chat/conversations/{conversation_id}").json()
            assert detail["study_pending_check"]["question"] == _Q3
            evidence_after = StudyVerificationRepository(database).list_evidence(
                account["id"], conversation_id
            )
            assert len(evidence_after) == len(evidence_before)
            assert database.connection.execute(
                "SELECT COUNT(*) FROM study_session_controls WHERE kind = 'skip'"
            ).fetchone()[0] == 1
    finally:
        _close(app)


# ---------------------------------------------------------------------------
# 桌面旅程：待答 → 直接讲解 → 继续 → 暂停 → 继续
# ---------------------------------------------------------------------------


def test_desktop_journey_from_pending_to_pause_and_resume(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _SummaryAwareTutor(f"先检查一下。{_Q1}")
    plan = _PlanAdapter(default=_plan_pose(_Q1))
    app.state.chat_service._gateway = _gateway_with_verification(text, plan)

    try:
        with TestClient(app) as client:
            _register(client, "issue04-journey")
            conversation_id = _start_study(client, "互斥是什么？", "i04-journey-1")
            generation_helpers["drive"](app)
            detail = client.get(f"/chat/conversations/{conversation_id}").json()
            assert detail["study_pending_check"]["question"] == _Q1

            plan.default = _plan(action="help", intent="full_explanation")
            text.answer = "完整讲解：互斥与独立分别是两个不同的条件……"
            _send(client, conversation_id, "直接完整讲解，我不想答题。", "i04-journey-2")
            generation_helpers["drive"](app)
            detail = client.get(f"/chat/conversations/{conversation_id}").json()
            assert detail["study_pending_check"] is None
            assert detail["study_last_control"]["kind"] == "full_explanation"

            plan.default = _plan_pose(_Q2)
            text.answer = f"那我们看下一步。{_Q2}"
            _send(client, conversation_id, "继续下一步。", "i04-journey-3")
            generation_helpers["drive"](app)

            plan.default = _plan(action="help", intent="pause")
            _send(client, conversation_id, "今天先到这里。", "i04-journey-4")
            generation_helpers["drive"](app)
            detail = client.get(f"/chat/conversations/{conversation_id}").json()
            assert detail["study_pause"] is not None
            assert detail["study_pause"]["pending_question"] == _Q2
            assert detail["study_summary"]["trigger"] == "pause"
            paused_version = detail["study_local_progress"]["version"]

            # 刷新后暂停位置与进展保持；继续后恢复可学状态。
            refreshed = client.get(f"/chat/conversations/{conversation_id}").json()
            assert refreshed["study_pause"] == detail["study_pause"]
            assert refreshed["study_local_progress"]["version"] == paused_version

            plan.default = _plan(action="none", intent="resume")
            text.answer = "好，我们接着学。"
            _send(client, conversation_id, "继续。", "i04-journey-5")
            generation_helpers["drive"](app)
            detail = client.get(f"/chat/conversations/{conversation_id}").json()
            assert detail["study_pause"] is None
            assert detail["study_pending_check"]["question"] == _Q2
            assert detail["study_last_control"]["kind"] == "resume"
    finally:
        _close(app)


def _temporary_failure() -> Exception:
    return RateLimitError("temporary network interruption")
