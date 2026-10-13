"""Issue 07：系统性学习目标与简短学习路线（真实服务链路）。"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from bridges.ai import ModelGateway
from bridges.ai.adapters import AdapterResult, RateLimitError, StreamChunk
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.chat.run_budget_ledger import RunBudgetLedgerRepository
from bridges.contracts.ai import (
    CapabilityKind,
    CapabilityRecord,
    CapabilityStatus,
    RetryPolicy,
)
from bridges.contracts.chat import ChatMode
from tests.chat.test_v2_05_photo_attachments import _app, _register

GOAL_TEXT = "我想学线性代数，主要为了理解机器学习里的矩阵"

NODES_V1 = [
    {"node_id": "n1", "title": "向量与线性组合", "prerequisites": [], "depth": "基础"},
    {
        "node_id": "n2",
        "title": "矩阵与矩阵乘法",
        "prerequisites": ["n1"],
        "depth": "基础",
    },
    {
        "node_id": "n3",
        "title": "特征值与特征向量",
        "prerequisites": ["n2"],
        "depth": "深入",
    },
]


def _route_json(
    nodes: list[dict[str, Any]],
    current_node_id: str,
    revision_reason: str = "",
) -> dict[str, Any]:
    return {
        "nodes": nodes,
        "current_node_id": current_node_id,
        "revision_reason": revision_reason,
    }


def _plan_json(**overrides: Any) -> str:
    payload: dict[str, Any] = {
        "kind": "systematic_goal",
        "observable_result": "",
        "scope": "",
        "constraints": "",
        "clarification_question": "",
        "route": None,
        "note": "",
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


class _GoalTutorAdapter:
    """规划结构化调用与流式导师回复的确定性替身。

    Issue 03 的验证规划与目标规划共用 ``qwen_structured_output``：本替身
    只脚本化 ``study.goal_planning``，对验证规划返回「不验证」的合法输出，
    保持目标/路线断言不被验证子系统干扰。
    """

    def __init__(self, answer: str = "我们先从向量与线性组合开始。") -> None:
        self.answer = answer
        self.plan_responses: list[str] = []
        self.plan_payloads: list[dict[str, Any]] = []
        self.chat_payloads: list[dict[str, Any]] = []
        self.plan_failure: Exception | None = None
        self.stream_failure: Exception | None = None
        self.slow = False

    def call(
        self, capability: CapabilityRecord, run_context: Any, payload: dict[str, Any]
    ) -> AdapterResult:
        if payload.get("task") != "study.goal_planning":
            return AdapterResult(
                actual_model_id=capability.model_id,
                output={
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
                },
            )
        self.plan_payloads.append(payload)
        if self.plan_failure is not None:
            raise self.plan_failure
        raw = (
            self.plan_responses.pop(0)
            if self.plan_responses
            else _plan_json(kind="local_question")
        )
        return AdapterResult(
            actual_model_id=capability.model_id, output=json.loads(raw)
        )

    def stream_call(
        self, capability: CapabilityRecord, run_context: Any, payload: dict[str, Any]
    ) -> Any:
        self.chat_payloads.append(payload)
        if self.stream_failure is not None:
            raise self.stream_failure
        if self.slow:
            for character in self.answer:
                time.sleep(0.01)
                yield StreamChunk(kind="delta", delta=character)
        else:
            yield StreamChunk(kind="delta", delta=self.answer)
        yield StreamChunk(kind="done", actual_model_id=capability.model_id)


def _chat_capability() -> CapabilityRecord:
    return CapabilityRecord(
        name="qwen_text_chat",
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


def _structured_capability() -> CapabilityRecord:
    return CapabilityRecord(
        name="qwen_structured_output",
        version="1",
        kind=CapabilityKind.MODEL,
        vendor="qwen",
        region="cn-beijing",
        model_id="qwen3.7-plus-2026-05-26",
        input_schema_version="structured-messages-v1",
        output_schema_version="json-schema-v1",
        status=CapabilityStatus.VERIFIED,
        retry_policy=RetryPolicy(max_attempts=1, backoff_seconds=0),
    )


def _goal_gateway(adapter: _GoalTutorAdapter) -> ModelGateway:
    registry = CapabilityRegistry()
    registry.register(_chat_capability())
    registry.register(_structured_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", adapter)
    gateway.register_adapter("qwen_structured_output", "1", adapter)
    return gateway


def _start_study_turn(
    client: TestClient,
    content: str,
    key: str,
) -> dict[str, Any]:
    response = client.post(
        "/chat/first-turn",
        json={
            "mode": ChatMode.STUDY.value,
            "content": content,
            "idempotency_key": key,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _send(client: TestClient, conversation_id: str, content: str, key: str) -> dict[str, Any]:
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": content, "idempotency_key": key},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _detail(client: TestClient, conversation_id: str) -> dict[str, Any]:
    response = client.get(f"/chat/conversations/{conversation_id}")
    assert response.status_code == 200, response.text
    return response.json()


def _goal_counts(app: Any) -> tuple[int, int, int]:
    connection = app.state.bridges_database.connection
    return (
        connection.execute("SELECT COUNT(*) FROM study_goals").fetchone()[0],
        connection.execute("SELECT COUNT(*) FROM study_goal_routes").fetchone()[0],
        connection.execute(
            "SELECT COUNT(*) FROM study_conversation_goals"
        ).fetchone()[0],
    )


def test_systematic_goal_creates_goal_route_and_stays_lightweight_for_questions(
    tmp_path: Path, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    adapter = _GoalTutorAdapter()
    adapter.plan_responses.append(
        _plan_json(
            observable_result="能用矩阵运算理解机器学习里的矩阵",
            scope="线性代数基础与机器学习常用矩阵运算",
            constraints="不要布置作业",
            route=_route_json(NODES_V1, "n1", "首次形成路线：从矩阵用途倒推基础顺序"),
        )
    )
    app.state.chat_service._gateway = _goal_gateway(adapter)  # noqa: SLF001

    try:
        with TestClient(app) as client:
            account = _register(client, "issue07-goal")
            first = _start_study_turn(client, GOAL_TEXT, "issue07-goal-first")
            conversation_id = first["conversation"]["conversation_id"]

            replay = client.post(
                "/chat/first-turn",
                json={
                    "mode": ChatMode.STUDY.value,
                    "content": GOAL_TEXT,
                    "idempotency_key": "issue07-goal-first",
                },
            )
            assert replay.status_code == 200, replay.text
            assert replay.json()["idempotent_replay"] is True

            generation_helpers["drive"](app)

            detail = _detail(client, conversation_id)
            goal = detail["study_goal"]
            assert goal["statement"] == GOAL_TEXT
            assert goal["observable_result"] == "能用矩阵运算理解机器学习里的矩阵"
            assert goal["scope"] == "线性代数基础与机器学习常用矩阵运算"
            assert goal["constraints"] == "不要布置作业"
            assert goal["source_message_id"]
            assert goal["status"] == "active"
            assert goal["version"] == 1
            route = goal["route"]
            assert route["version"] == 1
            assert route["origin"] == "assistant_proposal"
            assert route["current_node_id"] == "n1"
            assert [node["title"] for node in route["nodes"]] == [
                "向量与线性组合",
                "矩阵与矩阵乘法",
                "特征值与特征向量",
            ]
            assert route["nodes"][1]["prerequisites"] == ["n1"]
            assert route["revision_reason"] == "首次形成路线：从矩阵用途倒推基础顺序"
            assert set(route["nodes"][0]) == {
                "node_id",
                "title",
                "prerequisites",
                "depth",
            }

            # 重复首轮与幂等重放不重复建目标。
            assert _goal_counts(app) == (1, 1, 1)
            assert detail["study_local_progress"]["version"] == 1

            # 规划调用一次；提示词包含目标原话、路线与不可虚报掌握的约束。
            assert len(adapter.plan_payloads) == 1
            plan_payload = adapter.plan_payloads[0]
            assert plan_payload["task"] == "study.goal_planning"
            assert plan_payload["messages"][0]["role"] == "system"
            assert (
                "用户的学习目的与明确限制是权威事实"
                in plan_payload["messages"][0]["content"]
            )
            chat_system = adapter.chat_payloads[-1]["messages"][0]
            assert chat_system["role"] == "system"
            assert GOAL_TEXT in chat_system["content"]
            assert "向量与线性组合" in chat_system["content"]
            assert "未经验证的内容不得称为已掌握" in chat_system["content"]

            # 角色调用计入同一运行预算账本：目标规划 1 次 + 验证规划 1 次
            # （Issue 03 在每次学习导师轮执行）+ 流式 1 次。
            run = app.state.chat_service._repo.get_run_by_message(
                account["id"], detail["messages"][-1]["message_id"]
            )
            snapshot = RunBudgetLedgerRepository(
                app.state.bridges_database
            ).load(account["id"], run.run_id)
            assert snapshot is not None
            assert snapshot.model_calls_used == 3

            # 普通提问不触发完整规划，路线与目标保持不变。
            _send(client, conversation_id, "什么是特征值？", "issue07-question")
            generation_helpers["drive"](app)
            detail = _detail(client, conversation_id)
            assert len(adapter.plan_payloads) == 1
            assert detail["study_goal"]["route"]["version"] == 1
            assert detail["study_goal"]["statement"] == GOAL_TEXT
            assert detail["study_local_progress"]["version"] == 2

            # 跨账户访问拒绝：目标与会话均按账户隔离。
            with TestClient(app) as other_client:
                _register(other_client, "issue07-other")
                assert other_client.get(
                    f"/chat/conversations/{conversation_id}"
                ).status_code == 404
    finally:
        app.state.bridges_database.close()
        app.state.state_store.close()


def test_local_question_does_not_create_goal_or_run_planning(
    tmp_path: Path, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    adapter = _GoalTutorAdapter(answer="互斥表示不能同时发生。")
    app.state.chat_service._gateway = _goal_gateway(adapter)  # noqa: SLF001

    try:
        with TestClient(app) as client:
            _register(client, "issue07-local")
            first = _start_study_turn(client, "互斥是不是独立？", "issue07-local-first")
            conversation_id = first["conversation"]["conversation_id"]
            generation_helpers["drive"](app)

            detail = _detail(client, conversation_id)
            assert detail["study_goal"] is None
            assert detail["study_local_progress"] is not None
            assert adapter.plan_payloads == []
            assert _goal_counts(app) == (0, 0, 0)
    finally:
        app.state.bridges_database.close()
        app.state.state_store.close()


def test_missing_foundation_asks_one_question_then_builds_route(
    tmp_path: Path, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    adapter = _GoalTutorAdapter()
    adapter.plan_responses.append(
        _plan_json(
            clarification_question="你之前接触过线性代数的哪些内容？",
            route=None,
        )
    )
    adapter.plan_responses.append(
        _plan_json(route=_route_json(NODES_V1, "n1", "依据用户基础补充形成路线"))
    )
    app.state.chat_service._gateway = _goal_gateway(adapter)  # noqa: SLF001

    try:
        with TestClient(app) as client:
            _register(client, "issue07-clarify")
            first = _start_study_turn(client, "带我学线性代数", "issue07-clarify-first")
            conversation_id = first["conversation"]["conversation_id"]
            generation_helpers["drive"](app)

            detail = _detail(client, conversation_id)
            goal = detail["study_goal"]
            assert goal is not None
            assert goal["statement"] == "带我学线性代数"
            assert goal["route"] is None
            assert goal["observable_result"] == ""
            assert len(adapter.plan_payloads) == 1
            first_system = adapter.chat_payloads[-1]["messages"][0]["content"]
            assert "你之前接触过线性代数的哪些内容？" in first_system
            assert "只问这一个必要问题" in first_system

            _send(client, conversation_id, "只学过一点矩阵乘法", "issue07-clarify-answer")
            generation_helpers["drive"](app)

            detail = _detail(client, conversation_id)
            assert detail["study_goal"]["route"]["version"] == 1
            assert len(adapter.plan_payloads) == 2
            assert (
                "用户此前已表达系统性学习目标"
                in adapter.plan_payloads[1]["messages"][0]["content"]
            )
            assert _goal_counts(app) == (1, 1, 1)
    finally:
        app.state.bridges_database.close()
        app.state.state_store.close()


def test_route_adjustment_and_node_jump_create_revisions_without_mastery_claims(
    tmp_path: Path, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    adapter = _GoalTutorAdapter()
    adapter.plan_responses.append(
        _plan_json(route=_route_json(NODES_V1, "n1", "首次形成路线"))
    )
    adapter.plan_responses.append(
        _plan_json(
            route=_route_json(
                [
                    NODES_V1[0],
                    NODES_V1[2],
                    NODES_V1[1],
                ],
                "n3",
                "按用户要求调整顺序：先学特征值",
            )
        )
    )
    adapter.plan_responses.append(
        _plan_json(
            route=_route_json(
                [
                    *NODES_V1,
                    {
                        "node_id": "n4",
                        "title": "奇异值分解",
                        "prerequisites": ["n2"],
                        "depth": "深入",
                    },
                ],
                "n4",
                "按用户要求跳转到奇异值分解",
            )
        )
    )
    app.state.chat_service._gateway = _goal_gateway(adapter)  # noqa: SLF001

    try:
        with TestClient(app) as client:
            _register(client, "issue07-revise")
            first = _start_study_turn(client, GOAL_TEXT, "issue07-revise-first")
            conversation_id = first["conversation"]["conversation_id"]
            generation_helpers["drive"](app)

            _send(
                client,
                conversation_id,
                "调整顺序：先学特征值再学矩阵分解",
                "issue07-revise-order",
            )
            generation_helpers["drive"](app)

            detail = _detail(client, conversation_id)
            route = detail["study_goal"]["route"]
            assert route["version"] == 2
            assert route["origin"] == "user_request"
            assert route["revision_reason"] == "按用户要求调整顺序：先学特征值"
            assert [node["node_id"] for node in route["nodes"]] == ["n1", "n3", "n2"]
            assert route["current_node_id"] == "n3"
            # 原学习目的不被路线修订改写。
            assert detail["study_goal"]["statement"] == GOAL_TEXT
            assert detail["study_goal"]["version"] == 1

            _send(client, conversation_id, "直接跳到奇异值分解", "issue07-revise-jump")
            generation_helpers["drive"](app)

            detail = _detail(client, conversation_id)
            route = detail["study_goal"]["route"]
            assert route["version"] == 3
            assert route["current_node_id"] == "n4"
            # 跳节点不表示前面已掌握：投影不含任何掌握状态字段。
            for node in route["nodes"]:
                assert set(node) == {"node_id", "title", "prerequisites", "depth"}
            jump_system = adapter.chat_payloads[-1]["messages"][0]["content"]
            assert "跳到某个节点不代表前面的知识点已完成" in jump_system
            assert len(adapter.plan_payloads) == 3
            assert _goal_counts(app) == (1, 3, 1)
    finally:
        app.state.bridges_database.close()
        app.state.state_store.close()


def test_planning_failure_keeps_goal_truthful_and_recovers_next_turn(
    tmp_path: Path, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    adapter = _GoalTutorAdapter()
    adapter.plan_failure = RateLimitError("planning unavailable")
    app.state.chat_service._gateway = _goal_gateway(adapter)  # noqa: SLF001

    try:
        with TestClient(app) as client:
            _register(client, "issue07-planfail")
            first = _start_study_turn(client, GOAL_TEXT, "issue07-planfail-first")
            conversation_id = first["conversation"]["conversation_id"]
            generation_helpers["drive"](app)

            detail = _detail(client, conversation_id)
            assert detail["messages"][-1]["status"] == "done"
            goal = detail["study_goal"]
            assert goal is not None
            assert goal["statement"] == GOAL_TEXT
            assert goal["route"] is None
            system_prompt = adapter.chat_payloads[-1]["messages"][0]["content"]
            assert "本次未能形成学习路线" in system_prompt
            assert "不要声称已保存路线" in system_prompt
            assert _goal_counts(app) == (1, 0, 1)

            # 恢复后下一轮补建路线，不重复建目标。
            adapter.plan_failure = None
            adapter.plan_responses.append(
                _plan_json(route=_route_json(NODES_V1, "n1", "补充信息后形成路线"))
            )
            _send(client, conversation_id, "我学过一点矩阵", "issue07-planfail-retry")
            generation_helpers["drive"](app)

            detail = _detail(client, conversation_id)
            assert detail["study_goal"]["route"]["version"] == 1
            assert _goal_counts(app) == (1, 1, 1)
    finally:
        app.state.bridges_database.close()
        app.state.state_store.close()


def test_failed_turn_retry_commits_goal_once(
    tmp_path: Path, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    adapter = _GoalTutorAdapter()
    adapter.stream_failure = RateLimitError("temporary stream failure")
    app.state.chat_service._gateway = _goal_gateway(adapter)  # noqa: SLF001

    try:
        with TestClient(app) as client:
            _register(client, "issue07-retry")
            adapter.plan_responses.append(
                _plan_json(route=_route_json(NODES_V1, "n1", "首次形成路线"))
            )
            adapter.plan_responses.append(
                _plan_json(route=_route_json(NODES_V1, "n1", "首次形成路线"))
            )
            first = _start_study_turn(client, GOAL_TEXT, "issue07-retry-first")
            conversation_id = first["conversation"]["conversation_id"]
            generation_helpers["drive"](app)

            detail = _detail(client, conversation_id)
            assert detail["messages"][-1]["status"] == "error"
            assert detail["study_goal"] is None
            assert _goal_counts(app) == (0, 0, 0)
            failed_message_id = detail["messages"][-1]["message_id"]

            adapter.stream_failure = None
            retried = client.post(
                f"/chat/conversations/{conversation_id}/messages/{failed_message_id}/retry"
            )
            assert retried.status_code == 200, retried.text
            generation_helpers["drive"](app)

            detail = _detail(client, conversation_id)
            assert detail["messages"][-1]["status"] == "done"
            assert detail["study_goal"]["route"]["version"] == 1
            assert _goal_counts(app) == (1, 1, 1)
    finally:
        app.state.bridges_database.close()
        app.state.state_store.close()


def test_stopped_first_turn_claims_nothing(
    tmp_path: Path, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    adapter = _GoalTutorAdapter(answer="正在逐字生成的回答。" * 20)
    adapter.slow = True
    adapter.plan_responses.append(
        _plan_json(route=_route_json(NODES_V1, "n1", "首次形成路线"))
    )
    app.state.chat_service._gateway = _goal_gateway(adapter)  # noqa: SLF001

    try:
        with TestClient(app) as client:
            _register(client, "issue07-stop")
            first = _start_study_turn(client, GOAL_TEXT, "issue07-stop-first")
            conversation_id = first["conversation"]["conversation_id"]
            message_id = first["assistant_message"]["message_id"]

            stop_executor, executor_thread = generation_helpers["executor_thread"](app)
            control_client = TestClient(app)
            control_client.cookies.set(
                "bridges_session", client.cookies.get("bridges_session")
            )
            try:
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
            finally:
                stop_executor.set()
                executor_thread.join(timeout=5)
                control_client.close()

            detail = _detail(client, conversation_id)
            assert detail["study_goal"] is None
            assert _goal_counts(app) == (0, 0, 0)
            assert (
                app.state.bridges_database.connection.execute(
                    "SELECT COUNT(*) FROM study_local_progress"
                ).fetchone()[0]
                == 0
            )
    finally:
        app.state.bridges_database.close()
        app.state.state_store.close()


def test_route_revision_conflict_does_not_overwrite_newer_version(
    tmp_path: Path, monkeypatch: Any, generation_helpers: Any
) -> None:
    from bridges.contracts.study_goals import StudyRouteNode, StudyRouteProjection
    from bridges.study.goals import StudyGoalRepository

    app = _app(tmp_path, monkeypatch)
    adapter = _GoalTutorAdapter()
    adapter.plan_responses.append(
        _plan_json(route=_route_json(NODES_V1, "n1", "首次形成路线"))
    )
    app.state.chat_service._gateway = _goal_gateway(adapter)  # noqa: SLF001

    try:
        with TestClient(app) as client:
            account = _register(client, "issue07-conflict")
            first = _start_study_turn(client, GOAL_TEXT, "issue07-conflict-first")
            conversation_id = first["conversation"]["conversation_id"]
            generation_helpers["drive"](app)

            database = app.state.bridges_database
            repository = StudyGoalRepository(database)
            goal = repository.get_linked_goal(account["id"], conversation_id)
            assert goal is not None
            draft_route = StudyRouteProjection(
                version=2,
                nodes=[StudyRouteNode(node_id="n1", title="向量与线性组合")],
                current_node_id="n1",
                revision_reason="过期规划结果",
                origin="assistant_proposal",
                created_at="2026-10-13T00:00:00+00:00",
            )
            with database.transaction():
                # 规划时看到的是 v1；并发方已提交 v2 时过期提交必须被拒绝。
                stale_accepted = repository.save_route_revision_in_transaction(
                    account_id=account["id"],
                    goal_id=goal.goal_id,
                    route=draft_route,
                    expected_version=0,
                )
            assert stale_accepted is False
            assert repository.current_route(account["id"], goal.goal_id).version == 1

            fresh_route = draft_route.model_copy(
                update={"revision_reason": "按用户要求调整深度"}
            )
            with database.transaction():
                fresh_accepted = repository.save_route_revision_in_transaction(
                    account_id=account["id"],
                    goal_id=goal.goal_id,
                    route=fresh_route,
                    expected_version=1,
                )
            assert fresh_accepted is True
            assert repository.current_route(account["id"], goal.goal_id).version == 2
            assert [
                item.version
                for item in repository.list_route_revisions(account["id"], goal.goal_id)
            ] == [1, 2]
    finally:
        app.state.bridges_database.close()
        app.state.state_store.close()
