"""Issue 08：跨会话续学、歧义澄清与目标切换（真实服务链路）。"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from bridges.ai import ModelGateway
from bridges.ai.adapters import AdapterResult, StreamChunk
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.contracts.ai import (
    CapabilityKind,
    CapabilityRecord,
    CapabilityStatus,
    RetryPolicy,
)
from bridges.study.goal_events import StudyGoalEventRepository
from bridges.study.goal_planning import StudyGoalPlan, StudyGoalPlanner
from bridges.study.goal_turn import StudyGoalTurn, StudyGoalTurnService
from bridges.study.goals import StudyGoalRepository
from bridges.study.verification import (
    STUDY_VERIFICATION_PLAN_JSON_SCHEMA,
    StudyVerificationRepository,
)
from tests.chat.test_v2_05_photo_attachments import _app, _register

GOAL_LINEAR = "我想学线性代数，主要为了理解机器学习里的矩阵"
GOAL_PROBABILITY = "我想系统学概率论，为了做数据分析"
GOAL_FRENCH = "我改学法语，目标是一年后能读原版小说"

NODES_LINEAR = [
    {"node_id": "n1", "title": "向量与线性组合", "prerequisites": [], "depth": "基础"},
    {
        "node_id": "n2",
        "title": "矩阵与矩阵乘法",
        "prerequisites": ["n1"],
        "depth": "基础",
    },
]
NODES_PROBABILITY = [
    {"node_id": "p1", "title": "随机变量", "prerequisites": [], "depth": "基础"},
    {
        "node_id": "p2",
        "title": "常见分布",
        "prerequisites": ["p1"],
        "depth": "基础",
    },
]
NODES_FRENCH = [
    {"node_id": "f1", "title": "发音与拼读", "prerequisites": [], "depth": "基础"},
    {"node_id": "f2", "title": "高频动词", "prerequisites": ["f1"], "depth": "基础"},
]

CHECK_QUESTION = "互斥事件的概率加法公式是什么？"


def _route_json(
    nodes: list[dict[str, Any]], current_node_id: str, revision_reason: str = ""
) -> dict[str, Any]:
    return {
        "nodes": nodes,
        "current_node_id": current_node_id,
        "revision_reason": revision_reason,
    }


def _plan_json(**overrides: Any) -> str:
    payload: dict[str, Any] = {
        "kind": "systematic_goal",
        "target_goal_id": "",
        "observable_result": "",
        "scope": "",
        "constraints": "",
        "clarification_question": "",
        "route": None,
        "note": "",
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


def _verification_none() -> dict[str, Any]:
    return {
        "action": "none",
        "intent": "other",
        "outcome": "",
        "equivalent_accepted": False,
        "reasoning_note": "",
        "supporting": [],
        "missing": [],
        "feedback_direction": "",
        "check_kind": "",
        "check_question": "",
        "check_knowledge_context": "",
        "evaluation_points": [],
        "accepted_equivalents": [],
        "key_misconceptions": [],
        "assumptions": [],
        "expected_answer": "",
        "teaching_direction": "",
    }


class _TutorAdapter:
    """目标规划、验证规划与流式回复的确定性替身。"""

    def __init__(self, answer: str = "好的，我们继续。") -> None:
        self.answer = answer
        self.plan_responses: list[str] = []
        self.verification_responses: list[str] = []
        self.plan_payloads: list[dict[str, Any]] = []
        self.verification_payloads: list[dict[str, Any]] = []
        self.chat_payloads: list[dict[str, Any]] = []
        self.slow = False

    def call(
        self, capability: CapabilityRecord, run_context: Any, payload: dict[str, Any]
    ) -> AdapterResult:
        if payload.get("task") == "study.goal_planning":
            self.plan_payloads.append(payload)
            raw = (
                self.plan_responses.pop(0)
                if self.plan_responses
                else _plan_json(kind="local_question")
            )
            return AdapterResult(
                actual_model_id=capability.model_id, output=json.loads(raw)
            )
        if payload.get("json_schema") == STUDY_VERIFICATION_PLAN_JSON_SCHEMA:
            self.verification_payloads.append(payload)
            if self.verification_responses:
                output = json.loads(self.verification_responses.pop(0))
            else:
                output = _verification_none()
            return AdapterResult(actual_model_id=capability.model_id, output=output)
        return AdapterResult(actual_model_id=capability.model_id, output=_verification_none())

    def stream_call(
        self, capability: CapabilityRecord, run_context: Any, payload: dict[str, Any]
    ) -> Any:
        self.chat_payloads.append(payload)
        if self.slow:
            for character in self.answer:
                time.sleep(0.01)
                yield StreamChunk(kind="delta", delta=character)
        else:
            yield StreamChunk(kind="delta", delta=self.answer)
        yield StreamChunk(kind="done", actual_model_id=capability.model_id)


def _capability(name: str) -> CapabilityRecord:
    return CapabilityRecord(
        name=name,
        version="1",
        kind=CapabilityKind.MODEL,
        vendor="qwen",
        region="cn-beijing",
        model_id="qwen3.7-plus-2026-05-26",
        input_schema_version="chat-messages-v1",
        output_schema_version="chat-completion-v1",
        status=CapabilityStatus.VERIFIED,
        retry_policy=RetryPolicy(max_attempts=1, backoff_seconds=0),
    )


def _gateway(adapter: _TutorAdapter) -> ModelGateway:
    registry = CapabilityRegistry()
    registry.register(_capability("qwen_text_chat"))
    registry.register(_capability("qwen_structured_output"))
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", adapter)
    gateway.register_adapter("qwen_structured_output", "1", adapter)
    return gateway


def _start_study_turn(
    client: TestClient, content: str, key: str
) -> dict[str, Any]:
    response = client.post(
        "/chat/first-turn",
        json={"mode": "study", "content": content, "idempotency_key": key},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _send(client: TestClient, conversation_id: str, content: str, key: str) -> None:
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": content, "idempotency_key": key},
    )
    assert response.status_code == 200, response.text


def _detail(client: TestClient, conversation_id: str) -> dict[str, Any]:
    response = client.get(f"/chat/conversations/{conversation_id}")
    assert response.status_code == 200, response.text
    return response.json()


def _goals(client: TestClient) -> dict[str, Any]:
    response = client.get("/chat/study/goals")
    assert response.status_code == 200, response.text
    return response.json()


def _counts(app: Any) -> tuple[int, int, int]:
    connection = app.state.bridges_database.connection
    return (
        connection.execute("SELECT COUNT(*) FROM study_goals").fetchone()[0],
        connection.execute("SELECT COUNT(*) FROM study_goal_routes").fetchone()[0],
        connection.execute(
            "SELECT COUNT(*) FROM study_conversation_goals"
        ).fetchone()[0],
    )


def _linked_goal_id(app: Any, account_id: str, conversation_id: str) -> str | None:
    goal = StudyGoalRepository(app.state.bridges_database).get_linked_goal(
        account_id, conversation_id
    )
    return None if goal is None else goal.goal_id


def test_resume_existing_goal_across_conversations(
    tmp_path: Path, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    adapter = _TutorAdapter(answer="我们从向量与线性组合继续。")
    adapter.plan_responses.append(
        _plan_json(route=_route_json(NODES_LINEAR, "n1", "首次形成路线"))
    )
    app.state.chat_service._gateway = _gateway(adapter)  # noqa: SLF001

    try:
        with TestClient(app) as client:
            account = _register(client, "issue08-resume")
            first = _start_study_turn(client, GOAL_LINEAR, "issue08-resume-first")
            conversation_a = first["conversation"]["conversation_id"]
            generation_helpers["drive"](app)

            repository = StudyGoalRepository(app.state.bridges_database)
            goal = repository.get_linked_goal(account["id"], conversation_a)
            assert goal is not None

            # 新会话明确要求继续：规划选中既有目标，读取已保存位置。
            adapter.plan_responses.append(
                _plan_json(kind="resume", target_goal_id=goal.goal_id)
            )
            second = _start_study_turn(
                client, "继续学线性代数", "issue08-resume-second"
            )
            conversation_b = second["conversation"]["conversation_id"]
            generation_helpers["drive"](app)

            detail = _detail(client, conversation_b)
            assert detail["study_goal"]["goal_id"] == goal.goal_id
            assert detail["study_goal"]["statement"] == GOAL_LINEAR
            notice = detail["study_goal_notice"]
            assert notice is not None
            assert notice["kind"] == "restored"
            assert GOAL_LINEAR in notice["message"]
            assert detail["messages"][-1]["status"] == "done"

            # 规划提示词带候选目标；导师提示词带恢复区块（真实位置）。
            resume_payload = adapter.plan_payloads[-1]
            resume_system = resume_payload["messages"][0]["content"]
            assert f"target_goal_id={goal.goal_id}" in resume_system
            assert "继续某个既有学习目标" in resume_system
            chat_system = adapter.chat_payloads[-1]["messages"][0]["content"]
            assert "已按账户内目标引用恢复目标" in chat_system
            assert "上次所在的知识点：向量与线性组合" in chat_system
            assert "不宣称跳过或未验证内容已掌握" in chat_system

            # 事件：第二个会话记录 resume 关联；目标数量不变。
            assert _counts(app) == (1, 1, 2)
            assert (
                StudyGoalEventRepository(app.state.bridges_database).has_transition_for_message(
                    account["id"], conversation_b, second["user_message"]["message_id"]
                )
                is True
            )

            # 目标列表投影：本人目标可见，其他账户不可见。
            goals = _goals(client)
            assert len(goals["goals"]) == 1
            assert goals["goals"][0]["goal_id"] == goal.goal_id
            assert goals["goals"][0]["status"] == "active"
            assert goals["goals"][0]["current_node_title"] == "向量与线性组合"
            assert goals["goals"][0]["route_version"] == 1
            with TestClient(app) as other_client:
                _register(other_client, "issue08-resume-other")
                assert _goals(other_client)["goals"] == []
    finally:
        app.state.bridges_database.close()
        app.state.state_store.close()


def test_ambiguous_resume_asks_one_question_then_links(
    tmp_path: Path, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    adapter = _TutorAdapter(answer="请确认你要继续哪一个目标。")
    adapter.plan_responses.append(
        _plan_json(route=_route_json(NODES_LINEAR, "n1", "首次形成路线"))
    )
    adapter.plan_responses.append(
        _plan_json(
            route=_route_json(NODES_PROBABILITY, "p1", "首次形成路线"),
        )
    )
    app.state.chat_service._gateway = _gateway(adapter)  # noqa: SLF001

    try:
        with TestClient(app) as client:
            account = _register(client, "issue08-clarify")
            first = _start_study_turn(client, GOAL_LINEAR, "issue08-clarify-a")
            generation_helpers["drive"](app)
            second = _start_study_turn(
                client, GOAL_PROBABILITY, "issue08-clarify-b"
            )
            generation_helpers["drive"](app)

            repository = StudyGoalRepository(app.state.bridges_database)
            goal_linear = repository.get_linked_goal(
                account["id"], first["conversation"]["conversation_id"]
            )
            goal_probability = repository.get_linked_goal(
                account["id"], second["conversation"]["conversation_id"]
            )
            assert goal_linear is not None and goal_probability is not None

            # 两个候选都可能：只问一个必要问题，不默认选择。
            adapter.plan_responses.append(
                _plan_json(
                    kind="resume",
                    clarification_question="你要继续的是哪一个学习目标？",
                )
            )
            third = _start_study_turn(
                client, "继续之前的学习", "issue08-clarify-c"
            )
            conversation_id = third["conversation"]["conversation_id"]
            generation_helpers["drive"](app)

            detail = _detail(client, conversation_id)
            assert detail["study_goal"] is None
            notice = detail["study_goal_notice"]
            assert notice is not None
            assert notice["kind"] == "clarification"
            assert detail["messages"][-1]["status"] == "done"
            clarify_system = adapter.chat_payloads[-1]["messages"][0]["content"]
            assert "续学目标待确认" in clarify_system
            assert "只问一个让用户选择目标的问题" in clarify_system
            plan_system = adapter.plan_payloads[-1]["messages"][0]["content"]
            assert f"target_goal_id={goal_linear.goal_id}" in plan_system
            assert f"target_goal_id={goal_probability.goal_id}" in plan_system

            # 用户明确选择后关联该目标，不再追问。
            adapter.plan_responses.append(
                _plan_json(kind="resume", target_goal_id=goal_probability.goal_id)
            )
            _send(client, conversation_id, "继续学概率论", "issue08-clarify-resolve")
            generation_helpers["drive"](app)

            detail = _detail(client, conversation_id)
            assert detail["study_goal"]["goal_id"] == goal_probability.goal_id
            assert detail["study_goal"]["statement"] == GOAL_PROBABILITY
            assert detail["study_goal_notice"]["kind"] == "restored"
            assert _counts(app) == (2, 2, 3)
    finally:
        app.state.bridges_database.close()
        app.state.state_store.close()


def test_unresolved_clarification_lapses_and_does_not_ask_again(
    tmp_path: Path, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    adapter = _TutorAdapter(answer="我们先解释随机变量。")
    adapter.plan_responses.append(
        _plan_json(route=_route_json(NODES_LINEAR, "n1", "首次形成路线"))
    )
    adapter.plan_responses.append(
        _plan_json(route=_route_json(NODES_PROBABILITY, "p1", "首次形成路线"))
    )
    app.state.chat_service._gateway = _gateway(adapter)  # noqa: SLF001

    try:
        with TestClient(app) as client:
            account = _register(client, "issue08-lapse")
            first = _start_study_turn(client, GOAL_LINEAR, "issue08-lapse-a")
            generation_helpers["drive"](app)
            _start_study_turn(client, GOAL_PROBABILITY, "issue08-lapse-b")
            generation_helpers["drive"](app)
            repository = StudyGoalRepository(app.state.bridges_database)
            goal_linear = repository.get_linked_goal(
                account["id"], first["conversation"]["conversation_id"]
            )
            assert goal_linear is not None

            adapter.plan_responses.append(
                _plan_json(
                    kind="resume",
                    clarification_question="你要继续的是哪一个学习目标？",
                )
            )
            third = _start_study_turn(
                client, "继续之前的学习", "issue08-lapse-c"
            )
            conversation_id = third["conversation"]["conversation_id"]
            generation_helpers["drive"](app)
            assert _detail(client, conversation_id)["study_goal_notice"]["kind"] == (
                "clarification"
            )

            # 第二条消息未解决歧义：记失效、按普通教学继续，不再追问。
            adapter.plan_responses.append(_plan_json(kind="local_question"))
            _send(client, conversation_id, "什么是随机变量？", "issue08-lapse-plain")
            generation_helpers["drive"](app)

            detail = _detail(client, conversation_id)
            assert detail["study_goal"] is None
            assert detail["study_goal_notice"] is None
            assert detail["messages"][-1]["status"] == "done"
            lapsed_system = adapter.chat_payloads[-1]["messages"][0]["content"]
            assert "续学目标待确认" not in lapsed_system
            connection = app.state.bridges_database.connection
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM study_goal_events"
                    " WHERE conversation_id = ? AND kind = 'clarification_lapsed'",
                    (conversation_id,),
                ).fetchone()[0]
                == 1
            )

            # 之后仍可明确续学（失效只取消追问，不冻结功能）。
            adapter.plan_responses.append(
                _plan_json(kind="resume", target_goal_id=goal_linear.goal_id)
            )
            _send(client, conversation_id, "继续学线性代数", "issue08-lapse-again")
            generation_helpers["drive"](app)
            detail = _detail(client, conversation_id)
            assert detail["study_goal"]["goal_id"] == goal_linear.goal_id
            assert detail["study_goal_notice"]["kind"] == "restored"
    finally:
        app.state.bridges_database.close()
        app.state.state_store.close()


def test_switch_to_existing_goal_pauses_old_and_restores_paused_target(
    tmp_path: Path, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    adapter = _TutorAdapter(answer="好的，按当前目标继续。")
    adapter.plan_responses.append(
        _plan_json(route=_route_json(NODES_LINEAR, "n1", "首次形成路线"))
    )
    adapter.plan_responses.append(
        _plan_json(route=_route_json(NODES_PROBABILITY, "p1", "首次形成路线"))
    )
    app.state.chat_service._gateway = _gateway(adapter)  # noqa: SLF001

    try:
        with TestClient(app) as client:
            account = _register(client, "issue08-switch")
            first = _start_study_turn(client, GOAL_LINEAR, "issue08-switch-a")
            conversation_a = first["conversation"]["conversation_id"]
            generation_helpers["drive"](app)
            second = _start_study_turn(
                client, GOAL_PROBABILITY, "issue08-switch-b"
            )
            generation_helpers["drive"](app)

            repository = StudyGoalRepository(app.state.bridges_database)
            goal_linear = repository.get_linked_goal(account["id"], conversation_a)
            goal_probability = repository.get_linked_goal(
                account["id"], second["conversation"]["conversation_id"]
            )
            assert goal_linear is not None and goal_probability is not None

            # 明确改学既有目标：原目标暂停，会话引用原子切换。
            adapter.plan_responses.append(
                _plan_json(kind="switch", target_goal_id=goal_probability.goal_id)
            )
            _send(
                client,
                conversation_a,
                "我不想学线性代数了，我改学概率论",
                "issue08-switch-turn",
            )
            generation_helpers["drive"](app)

            detail = _detail(client, conversation_a)
            assert detail["study_goal"]["goal_id"] == goal_probability.goal_id
            assert detail["study_goal"]["statement"] == GOAL_PROBABILITY
            assert detail["study_goal_notice"]["kind"] == "switched"
            switch_system = adapter.chat_payloads[-1]["messages"][0]["content"]
            assert "已保存并暂停原目标，切换回既有目标" in switch_system
            assert "不得声称原目标已完成或已掌握" in switch_system
            paused = repository.get_goal(account["id"], goal_linear.goal_id)
            assert paused is not None
            assert paused.status == "paused"
            assert paused.version == goal_linear.version + 1
            # 会话进展照常保存（切换不影响消息交付与进展记录）。
            assert detail["study_local_progress"]["version"] == 2

            goals = _goals(client)
            statuses = {item["goal_id"]: item["status"] for item in goals["goals"]}
            assert statuses[goal_linear.goal_id] == "paused"
            assert statuses[goal_probability.goal_id] == "active"

            # 明确改学回暂停中的原目标：恢复为 active 并递增版本。
            adapter.plan_responses.append(
                _plan_json(kind="switch", target_goal_id=goal_linear.goal_id)
            )
            _send(
                client,
                conversation_a,
                "我改学回线性代数，还是想理解矩阵",
                "issue08-switch-back",
            )
            generation_helpers["drive"](app)

            detail = _detail(client, conversation_a)
            assert detail["study_goal"]["goal_id"] == goal_linear.goal_id
            assert detail["study_goal_notice"]["kind"] == "switched"
            restored = repository.get_goal(account["id"], goal_linear.goal_id)
            assert restored is not None
            assert restored.status == "active"
            assert restored.version == paused.version + 1
            now_paused = repository.get_goal(account["id"], goal_probability.goal_id)
            assert now_paused is not None
            assert now_paused.status == "paused"
            assert _counts(app) == (2, 2, 2)

            # 会话 B 仍关联被其他会话暂停的目标：明确继续按最新版本恢复。
            conversation_b = second["conversation"]["conversation_id"]
            detail_b = _detail(client, conversation_b)
            assert detail_b["study_goal"]["goal_id"] == goal_probability.goal_id
            assert detail_b["study_goal"]["status"] == "paused"
            assert detail_b["study_goal_notice"] is None
            _send(client, conversation_b, "继续学概率论", "issue08-switch-reactivate")
            generation_helpers["drive"](app)
            detail_b = _detail(client, conversation_b)
            assert detail_b["study_goal"]["status"] == "active"
            assert detail_b["study_goal_notice"]["kind"] == "restored"
            restored_again = repository.get_goal(
                account["id"], goal_probability.goal_id
            )
            assert restored_again is not None
            assert restored_again.status == "active"
            assert restored_again.version == now_paused.version + 1
            statuses = {
                item["goal_id"]: item["status"] for item in _goals(client)["goals"]
            }
            assert statuses[goal_probability.goal_id] == "active"
            assert statuses[goal_linear.goal_id] == "active"
    finally:
        app.state.bridges_database.close()
        app.state.state_store.close()


def test_switch_to_new_goal_closes_pending_check_unverified(
    tmp_path: Path, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    adapter = _TutorAdapter(answer=f"先检验一下。请回答：{CHECK_QUESTION}")
    adapter.plan_responses.append(
        _plan_json(route=_route_json(NODES_LINEAR, "n1", "首次形成路线"))
    )
    app.state.chat_service._gateway = _gateway(adapter)  # noqa: SLF001

    try:
        with TestClient(app) as client:
            account = _register(client, "issue08-newgoal")
            first = _start_study_turn(client, GOAL_LINEAR, "issue08-newgoal-a")
            conversation_id = first["conversation"]["conversation_id"]
            generation_helpers["drive"](app)
            repository = StudyGoalRepository(app.state.bridges_database)
            old_goal = repository.get_linked_goal(account["id"], conversation_id)
            assert old_goal is not None

            # 提出一个待答问题（展示在回复正文中才会绑定）。
            adapter.verification_responses.append(
                json.dumps(
                    {
                        "action": "pose_check",
                        "intent": "other",
                        "check_kind": "formal_check",
                        "check_question": CHECK_QUESTION,
                        "check_knowledge_context": "互斥事件",
                        "evaluation_points": ["写出加法公式"],
                        "expected_answer": "P(A∪B)=P(A)+P(B)",
                        "teaching_direction": "",
                    },
                    ensure_ascii=False,
                )
            )
            _send(client, conversation_id, "继续吧", "issue08-newgoal-pose")
            generation_helpers["drive"](app)

            detail = _detail(client, conversation_id)
            pending = detail["study_pending_check"]
            assert pending is not None
            assert pending["question"] == CHECK_QUESTION
            connection = app.state.bridges_database.connection
            bound_goal = connection.execute(
                "SELECT goal_id FROM study_pending_checks"
                " WHERE account_id = ? AND conversation_id = ?",
                (account["id"], conversation_id),
            ).fetchone()
            assert bound_goal is not None
            assert bound_goal["goal_id"] == old_goal.goal_id
            old_check_id = pending["check_id"]

            # 明确改学新目标：关闭旧待答并如实记为未验证（归属旧目标）。
            adapter.plan_responses.append(
                _plan_json(kind="switch", route=_route_json(NODES_FRENCH, "f1"))
            )
            _send(
                client,
                conversation_id,
                "我改学法语，目标是一年后能读原版小说",
                "issue08-newgoal-switch",
            )
            generation_helpers["drive"](app)

            detail = _detail(client, conversation_id)
            assert detail["study_goal"]["statement"] == GOAL_FRENCH
            assert detail["study_goal"]["route"]["current_node_id"] == "f1"
            assert detail["study_pending_check"] is None
            assert detail["study_goal_notice"]["kind"] == "switched"

            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM study_pending_checks"
                    " WHERE account_id = ? AND conversation_id = ?",
                    (account["id"], conversation_id),
                ).fetchone()[0]
                == 0
            )
            evidence = connection.execute(
                "SELECT question, semantics, goal_id FROM study_evidence"
                " WHERE account_id = ? AND conversation_id = ?"
                " AND semantics = 'unverified'",
                (account["id"], conversation_id),
            ).fetchall()
            assert len(evidence) == 1
            assert evidence[0]["question"] == CHECK_QUESTION
            assert evidence[0]["goal_id"] == old_goal.goal_id
            switch_out = connection.execute(
                "SELECT pending_check_id FROM study_goal_events"
                " WHERE account_id = ? AND conversation_id = ?"
                " AND kind = 'switch_out'",
                (account["id"], conversation_id),
            ).fetchone()
            assert switch_out is not None
            assert switch_out["pending_check_id"] == old_check_id
            assert _counts(app) == (2, 2, 1)
    finally:
        app.state.bridges_database.close()
        app.state.state_store.close()


def test_stale_switch_snapshot_records_conflict_without_half_switch(
    tmp_path: Path, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    adapter = _TutorAdapter(answer="我们先从向量开始。")
    adapter.plan_responses.append(
        _plan_json(route=_route_json(NODES_LINEAR, "n1", "首次形成路线"))
    )
    app.state.chat_service._gateway = _gateway(adapter)  # noqa: SLF001

    try:
        with TestClient(app) as client:
            account = _register(client, "issue08-conflict")
            first = _start_study_turn(client, GOAL_LINEAR, "issue08-conflict-a")
            conversation_id = first["conversation"]["conversation_id"]
            generation_helpers["drive"](app)

            database = app.state.bridges_database
            repository = StudyGoalRepository(database)
            events = StudyGoalEventRepository(database)
            stale = repository.get_linked_goal(account["id"], conversation_id)
            assert stale is not None

            detail = _detail(client, conversation_id)
            run = app.state.chat_service._repo.get_run_by_message(  # noqa: SLF001
                account["id"], detail["messages"][-1]["message_id"]
            )
            user_message = app.state.chat_service._repo.get_message(  # noqa: SLF001
                account["id"], run.user_message_id
            )

            # 另一设备先暂停了目标：规划快照已过期。
            with database.transaction():
                assert repository.pause_goal_in_transaction(
                    account_id=account["id"],
                    goal_id=stale.goal_id,
                    expected_version=stale.version,
                    now=datetime.now(UTC),
                )
            service = StudyGoalTurnService(
                repository,
                StudyGoalPlanner(_gateway(adapter)),
                StudyVerificationRepository(database),
                events,
            )
            turn = StudyGoalTurn(
                mode="switch",
                plan=StudyGoalPlan(kind="switch", route=None),
                goal=stale,
                expected_route_version=1,
                prompt_block="",
            )
            with database.transaction():
                service.commit(turn, run, user_message)

            # 冲突如实可见：不半提交，不凭空建目标，引用保持原样。
            latest = events.latest_event(account["id"], conversation_id)
            assert latest is not None
            assert latest["kind"] == "conflict"
            assert latest["detail"] == "goal_changed"
            notice = events.latest_notice(account["id"], conversation_id)
            assert notice is not None
            assert notice.kind == "conflict"
            current = repository.get_goal(account["id"], stale.goal_id)
            assert current is not None
            assert current.status == "paused"
            assert current.version == stale.version + 1
            assert _linked_goal_id(app, account["id"], conversation_id) == stale.goal_id
            assert _counts(app) == (1, 1, 1)
    finally:
        app.state.bridges_database.close()
        app.state.state_store.close()
