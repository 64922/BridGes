"""生涯规划聊天集成测试（Issue 29）。

真实消息流程：send（明确生涯规划意图）→ started → career 过程事件 →
done（六类投影 + 正文）；重试沿用原输入；刷新/退出重登后由同一账户从
历史恢复，其他账户不可读；无画像/拒绝画像/敏感排除/模型失败/边界违反
各场景保持校准、不越权、不输出模板化假成功；逐项反馈进入既有反馈闭环。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.ai import CapabilityRegistry, ModelGateway
from bridges.ai.adapters import AdapterResult, RateLimitError
from bridges.contracts.ai import (
    CapabilityKind,
    CapabilityRecord,
    CapabilityStatus,
    RetryPolicy,
)

_CAREER_INTENT = "生涯规划助手：我大二在读计算机科学，喜欢数据分析，怎么规划接下来的方向"


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
        retry_policy=RetryPolicy(max_attempts=3, backoff_seconds=0.01),
    )


def _structured_capability() -> CapabilityRecord:
    return CapabilityRecord(
        name="qwen_structured_output",
        version="1",
        kind=CapabilityKind.MODEL,
        vendor="qwen",
        region="cn-beijing",
        model_id="qwen3.6-flash",
        input_schema_version="structured-messages-v1",
        output_schema_version="json-schema-v1",
        status=CapabilityStatus.VERIFIED,
        retry_policy=RetryPolicy(max_attempts=2, backoff_seconds=0.01),
    )


class _ProgrammableStructuredAdapter:
    """可编程结构化适配器（生涯规划编排使用；记录最后一次请求载荷）。"""

    def __init__(
        self,
        output: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._output = output
        self._error = error
        self.requests: list[dict[str, Any]] = []

    def call(
        self,
        capability: CapabilityRecord,
        run_context: Any,
        payload: dict[str, Any],
    ) -> AdapterResult:
        self.requests.append(payload)
        if self._error is not None:
            raise self._error
        return AdapterResult(
            actual_model_id=capability.model_id,
            output=self._output or {},
        )


def _gateway_with(structured: Any) -> ModelGateway:
    registry = CapabilityRegistry()
    registry.register(_chat_capability())
    registry.register(_structured_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_structured_output", "1", structured)
    return gateway


def _good_output() -> dict[str, Any]:
    return {
        "final_text": (
            "综合你的阶段与目标，数据分析是值得考虑的方向，"
            "可以从选课与实习两条线并行推进。"
        ),
        "facts": [
            {
                "content": "数据分析相关岗位需求在近三年持续增长。",
                "evidence_refs": ["web:0"],
                "note": "来源：行业报告。",
            }
        ],
        "assumptions": [
            {
                "content": "你可能适合偏业务的数据分析岗。",
                "evidence_refs": [],
                "note": None,
                "verification_next_step": "与从业者交流或做一次实习验证。",
            }
        ],
        "options": [
            {
                "content": "数据分析方向（业务侧）。",
                "evidence_refs": ["web:0"],
                "note": None,
                "rationale": "与你的兴趣与课程背景匹配。",
            }
        ],
        "risks": [
            {
                "content": "岗位竞争加剧。",
                "evidence_refs": ["web:0"],
                "note": None,
                "trigger": "应届求职季人数增加。",
            }
        ],
        "path": [
            {
                "content": "先补齐统计与 SQL 基础。",
                "evidence_refs": ["statement:current"],
                "note": None,
                "timeline": "第 1-3 个月",
            }
        ],
        "suggestions": [
            {
                "content": "完成一个端到端数据分析小项目。",
                "evidence_refs": ["statement:current"],
                "note": None,
                "verification": "项目上线后可验证兴趣与能力。",
            }
        ],
        "boundary_statement": "本规划不构成就业、薪酬或录取保证，也不替代持证职业顾问。",
        "open_questions": ["行业报告的统计口径未完全披露，建议进一步核查。"],
    }


@pytest.fixture
def sqlite_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """构建挂载 sqlite bridges.db 的应用（真实聊天服务 + 生涯规划编排）。"""
    from bridges.api.main import create_app
    from bridges.config import get_settings

    monkeypatch.setenv(
        "BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}"
    )
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "api-test-secret-key")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    return create_app()


@pytest.fixture
def client(sqlite_app: Any) -> TestClient:
    return TestClient(sqlite_app)


def _register(client: TestClient, tag: str = "1") -> dict[str, Any]:
    response = client.post(
        "/auth/register",
        json={
            "username": f"career_user_{tag}",
            "qq_email": f"12345671{tag}@qq.com",
            "password": "Passw0rd123!",
        },
    )
    assert response.status_code == 201, response.text
    account: dict[str, Any] = response.json()["account"]
    return account


def _swap_gateways(sqlite_app: Any, structured: Any) -> None:
    gateway = _gateway_with(structured)
    sqlite_app.state.chat_service._gateway = gateway
    sqlite_app.state.career_planner_service._gateway = gateway


def _create_conversation(client: TestClient) -> str:
    response = client.post("/chat/conversations", json={})
    assert response.status_code == 201, response.text
    conversation_id: str = response.json()["conversation_id"]
    return conversation_id


def _seed_assertion(
    client: TestClient,
    dimension: str,
    value: str,
    *,
    sensitivity: str = "preference",
) -> str:
    response = client.post(
        "/profiles/assertions/manual",
        json={
            "dimension": dimension,
            "value_or_rule": value,
            "applicable_scenes": ["companion", "study"],
            "sensitivity_class": sensitivity,
            "authorization_scope": "general",
            "source_note": "测试播种",
        },
    )
    assert response.status_code == 201, response.text
    assertion_id: str = response.json()["assertion_id"]
    return assertion_id


def _seed_learning_mission(sqlite_app: Any, account_id: str) -> None:
    from bridges.contracts.learning import LearningMissionCreateRequest

    sqlite_app.state.learning_service.create_mission(
        account_id=account_id,
        request=LearningMissionCreateRequest(
            title="数据分析学习",
            goal="掌握 SQL 与统计基础，完成一个端到端项目。",
            scope_concepts=["SQL", "统计"],
            constraints=[],
            success_criteria=["项目上线"],
        ),
    )


def _parse_sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for block in text.split("\n\n"):
        lines = [line for line in block.split("\n") if line]
        event_name = None
        data: list[str] = []
        for line in lines:
            if line.startswith("event:"):
                event_name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data.append(line[len("data:"):].strip())
        if event_name and data:
            events.append((event_name, json.loads("\n".join(data))))
    return events


def _send_career(
    client: TestClient, conversation_id: str, *, use_profile: bool = True
) -> dict[str, Any]:
    """Issue 02：发送生涯规划消息 → 创建响应（运行已入队，无 SSE 流）。"""
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": _CAREER_INTENT, "use_profile": use_profile},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_career_message_flows_through_real_message_stream(
    sqlite_app: Any, client: TestClient,
    generation_helpers: dict[str, Any],
) -> None:
    """六类输出经真实消息流交付：started → career 过程事件 → done 投影。"""
    _register(client)
    adapter = _ProgrammableStructuredAdapter(output=_good_output())
    _swap_gateways(sqlite_app, adapter)
    conversation_id = _create_conversation(client)

    created = _send_career(client, conversation_id)
    generation_helpers["drive"](sqlite_app)
    events = generation_helpers["subscribe"](
        client, conversation_id, created["assistant_message"]["message_id"]
    )
    names = [name for name, _ in events]
    assert names[0] == "started"
    assert "career" in names  # 过程卡事件（loading 步骤）
    assert names[-1] == "done"

    done_data = events[-1][1]
    message = done_data["message"]
    assert message["status"] == "done"
    assert message["career_planning"] is not None
    career = message["career_planning"]
    assert career["status"] == "done"
    assert career["process_state"] == "done"
    output = career["output"]
    assert output["final_text"]
    # 六类输出齐全
    assert output["facts"]
    assert output["assumptions"]
    assert output["options"]
    assert output["risks"]
    assert output["path"]
    assert output["suggestions"]
    assert output["boundary_statement"]
    assert output["open_questions"]
    # 助手消息正文 = 规划正文（真实消息流程）
    assert message["content"] == output["final_text"]
    # 证据带核查时间与来源
    assert career["evidence_sources"]
    assert all("accessed_at" in source for source in career["evidence_sources"])


def test_career_process_events_carry_chinese_states(
    sqlite_app: Any, client: TestClient,
    generation_helpers: dict[str, Any],
) -> None:
    _register(client)
    _swap_gateways(sqlite_app, _ProgrammableStructuredAdapter(output=_good_output()))
    conversation_id = _create_conversation(client)

    created = _send_career(client, conversation_id)
    generation_helpers["drive"](sqlite_app)
    career_events = [
        data
        for name, data in generation_helpers["subscribe"](
            client, conversation_id, created["assistant_message"]["message_id"]
        )
        if name == "career"
    ]
    assert career_events
    first = career_events[0]
    assert first["state"] == "loading"
    assert first["step_label"]  # 中文步骤说明
    assert all(step for step in first["progress_steps"] if step)


def test_career_persists_and_restores_for_same_account_only(
    sqlite_app: Any, client: TestClient,
    generation_helpers: dict[str, Any],
) -> None:
    """刷新/退出重登后同一账户可恢复规划结果；其他账户不可读。"""
    account_a = _register(client, tag="1")
    _swap_gateways(sqlite_app, _ProgrammableStructuredAdapter(output=_good_output()))
    conversation_id = _create_conversation(client)
    _send_career(client, conversation_id)
    generation_helpers["drive"](sqlite_app)

    # 刷新（重新 GET 历史）：同一账户可恢复完整规划投影
    history = client.get(f"/chat/conversations/{conversation_id}").json()
    assistant = next(m for m in history["messages"] if m["role"] == "assistant")
    assert assistant["career_planning"]["status"] == "done"
    assert assistant["career_planning"]["output"]["facts"]

    # 退出重登后仍可恢复
    client.post("/auth/logout")
    login = client.post(
        "/auth/login",
        json={
            "identifier": "career_user_1",
            "password": "Passw0rd123!",
        },
    )
    assert login.status_code == 200, login.text
    history = client.get(f"/chat/conversations/{conversation_id}").json()
    assistant = next(m for m in history["messages"] if m["role"] == "assistant")
    assert assistant["career_planning"] is not None

    # 其他账户不可读（不存在性不泄漏）
    account_b = _register(client, tag="2")
    assert account_b["id"] != account_a["id"]
    other = client.get(f"/chat/conversations/{conversation_id}")
    assert other.status_code == 404


def test_career_uses_minimal_authorized_profile_slice(
    sqlite_app: Any, client: TestClient,
    generation_helpers: dict[str, Any],
) -> None:
    """只使用当前账户授权的最小画像切片；敏感记录绝不进入模型请求。"""
    _register(client)
    adapter = _ProgrammableStructuredAdapter(output=_good_output())
    _swap_gateways(sqlite_app, adapter)
    conversation_id = _create_conversation(client)

    _seed_assertion(client, "interest_preference", "喜欢数据分析与可视化。")
    # 敏感断言（如重要经历）必须被切片编译器排除
    _seed_assertion(
        client,
        "important_experience",
        "曾因方向选择焦虑休学半年。",
        sensitivity="sensitive",
    )

    created = _send_career(client, conversation_id)
    generation_helpers["drive"](sqlite_app)
    done_data = generation_helpers["subscribe"](
        client, conversation_id, created["assistant_message"]["message_id"]
    )[-1][1]
    message = done_data["message"]
    # 披露：READY 态且只含 1 条授权切片（敏感记录被排除）
    context_note = message["context_note"]
    assert context_note["state"] == "ready"
    assert len(context_note["profile_items"]) == 1
    assert context_note["excluded_count"] >= 1
    # 模型请求只含最小切片，不含敏感记录正文
    assert adapter.requests, "生涯规划必须调用结构化模型"
    payload_text = json.dumps(adapter.requests[0], ensure_ascii=False)
    assert "喜欢数据分析与可视化" in payload_text
    assert "焦虑休学" not in payload_text
    # 规划投影披露画像使用状态
    career = message["career_planning"]
    assert career["profile_enabled"] is True
    assert career["profile_used"] is True


def test_career_with_profile_disabled_uses_nothing(
    sqlite_app: Any, client: TestClient,
    generation_helpers: dict[str, Any],
) -> None:
    """发送前关闭画像：请求、披露与规划投影均不含画像内容。"""
    _register(client)
    adapter = _ProgrammableStructuredAdapter(output=_good_output())
    _swap_gateways(sqlite_app, adapter)
    conversation_id = _create_conversation(client)
    _seed_assertion(client, "interest_preference", "喜欢数据分析与可视化。")

    created = _send_career(client, conversation_id, use_profile=False)
    generation_helpers["drive"](sqlite_app)
    done_data = generation_helpers["subscribe"](
        client, conversation_id, created["assistant_message"]["message_id"]
    )[-1][1]
    message = done_data["message"]
    context_note = message["context_note"]
    assert context_note["state"] == "off"
    assert context_note["profile_items"] == []
    payload_text = json.dumps(adapter.requests[0], ensure_ascii=False)
    assert "喜欢数据分析与可视化" not in payload_text
    career = message["career_planning"]
    assert career["profile_enabled"] is False
    assert career["profile_used"] is False


def test_career_without_profile_has_empty_disclosure(
    sqlite_app: Any, client: TestClient,
    generation_helpers: dict[str, Any],
) -> None:
    """启用画像但无相关记录：合法空态（empty 披露，回答照常）。"""
    _register(client)
    _swap_gateways(sqlite_app, _ProgrammableStructuredAdapter(output=_good_output()))
    conversation_id = _create_conversation(client)

    created = _send_career(client, conversation_id)
    generation_helpers["drive"](sqlite_app)
    done_data = generation_helpers["subscribe"](
        client, conversation_id, created["assistant_message"]["message_id"]
    )[-1][1]
    message = done_data["message"]
    assert message["context_note"]["state"] == "empty"
    assert message["career_planning"]["status"] == "done"


def test_career_learning_records_enter_evidence(
    sqlite_app: Any, client: TestClient,
    generation_helpers: dict[str, Any],
) -> None:
    account = _register(client)
    adapter = _ProgrammableStructuredAdapter(output=_good_output())
    _swap_gateways(sqlite_app, adapter)
    _seed_learning_mission(sqlite_app, account["id"])
    conversation_id = _create_conversation(client)

    created = _send_career(client, conversation_id)
    generation_helpers["drive"](sqlite_app)
    done_data = generation_helpers["subscribe"](
        client, conversation_id, created["assistant_message"]["message_id"]
    )[-1][1]
    career = done_data["message"]["career_planning"]
    learning = [
        source for source in career["evidence_sources"]
        if source["kind"] == "learning_record"
    ]
    assert learning, "学习记录必须进入生涯规划证据"
    assert any("数据分析学习" in source["title"] for source in learning)


def test_career_model_failure_is_recoverable_and_retry_keeps_input(
    sqlite_app: Any, client: TestClient,
    generation_helpers: dict[str, Any],
) -> None:
    _register(client)
    failing = _ProgrammableStructuredAdapter(error=RateLimitError("slow"))
    _swap_gateways(sqlite_app, failing)
    conversation_id = _create_conversation(client)

    created = _send_career(client, conversation_id)
    generation_helpers["drive"](sqlite_app)
    events = generation_helpers["subscribe"](
        client, conversation_id, created["assistant_message"]["message_id"]
    )
    assert events[-1][0] == "error"
    error_data = events[-1][1]
    assert error_data["error"]["retryable"] is True
    failed_message_id = error_data["message_id"]
    # 失败不输出模板化假成功：无规划投影交付
    history = client.get(f"/chat/conversations/{conversation_id}").json()
    failed = next(m for m in history["messages"] if m["message_id"] == failed_message_id)
    assert failed["status"] == "error"
    assert failed["career_planning"]["status"] == "error"
    assert failed["career_planning"]["process_state"] == "recovery"
    assert failed["career_planning"]["output"] is None

    # 重试：同一轮新尝试，沿用原问题（意图不丢失），成功后交付六类输出
    _swap_gateways(sqlite_app, _ProgrammableStructuredAdapter(output=_good_output()))
    retry = client.post(
        f"/chat/conversations/{conversation_id}/messages/{failed_message_id}/retry",
        json={},
    )
    assert retry.status_code == 200, retry.text
    retried = retry.json()
    generation_helpers["drive"](sqlite_app)
    retry_events = generation_helpers["subscribe"](
        client, conversation_id, retried["assistant_message"]["message_id"]
    )
    assert retry_events[-1][0] == "done"
    retry_message = retry_events[-1][1]["message"]
    assert retry_message["attempt_number"] == 2
    assert retry_message["career_planning"]["status"] == "done"
    assert retry_message["career_planning"]["output"]["facts"]


def test_career_boundary_violation_blocks_delivery(
    sqlite_app: Any, client: TestClient,
    generation_helpers: dict[str, Any],
) -> None:
    """承诺词（就业/薪酬/录取保证）触发阻断：不交付规划正文。"""
    _register(client)
    violating = _good_output()
    violating["final_text"] = "选这条路，包就业。"
    _swap_gateways(sqlite_app, _ProgrammableStructuredAdapter(output=violating))
    conversation_id = _create_conversation(client)

    created = _send_career(client, conversation_id)
    generation_helpers["drive"](sqlite_app)
    events = generation_helpers["subscribe"](
        client, conversation_id, created["assistant_message"]["message_id"]
    )
    assert events[-1][0] == "error"
    error_data = events[-1][1]
    assert error_data["error"]["code"] == "career_boundary_violation"
    message_id = error_data["message_id"]
    history = client.get(f"/chat/conversations/{conversation_id}").json()
    failed = next(m for m in history["messages"] if m["message_id"] == message_id)
    assert failed["status"] == "error"
    assert failed["error_code"] == "career_boundary_violation"
    assert failed["career_planning"]["output"] is None
    # 正文不被污染：不落库违规承诺文本
    assert "包就业" not in failed["content"]


def test_career_item_feedback_is_idempotent_and_scoped(
    sqlite_app: Any, client: TestClient,
    generation_helpers: dict[str, Any],
) -> None:
    """逐项反馈：定位到具体条目（career_item_ref），幂等，不串号。"""
    _register(client)
    _swap_gateways(sqlite_app, _ProgrammableStructuredAdapter(output=_good_output()))
    conversation_id = _create_conversation(client)
    created = _send_career(client, conversation_id)
    generation_helpers["drive"](sqlite_app)
    events = generation_helpers["subscribe"](
        client, conversation_id, created["assistant_message"]["message_id"]
    )
    message_id = events[-1][1]["message"]["message_id"]

    feedback = {
        "kind": "answer_inappropriate",
        "feedback_text": "这条事实过时了。",
        "career_item_ref": "fact:1",
    }
    first = client.post(
        f"/chat/conversations/{conversation_id}/messages/{message_id}/feedback",
        json=feedback,
    )
    assert first.status_code == 200, first.text
    # 幂等：同一反馈重试返回同一记录
    second = client.post(
        f"/chat/conversations/{conversation_id}/messages/{message_id}/feedback",
        json=feedback,
    )
    assert second.status_code == 200
    assert first.json()["feedback_id"] == second.json()["feedback_id"]
    assert first.json()["career_item_ref"] == "fact:1"

    # 反馈按账户隔离：另一账户看不到任何反馈内容（空列表，不泄漏存在性）
    _register(client, tag="2")
    other = client.get(f"/chat/conversations/{conversation_id}/feedback")
    assert other.status_code == 200
    assert other.json() == []


def test_career_item_ref_requires_career_message(
    sqlite_app: Any, client: TestClient,
    generation_helpers: dict[str, Any],
) -> None:
    _register(client)
    _swap_gateways(sqlite_app, _ProgrammableStructuredAdapter(output=_good_output()))
    conversation_id = _create_conversation(client)

    # 普通消息（非规划）不能带 career_item_ref
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": "今天天气怎么样"},
    )
    assert response.status_code == 200
    history = client.get(f"/chat/conversations/{conversation_id}").json()
    assistant = next(m for m in history["messages"] if m["role"] == "assistant")
    feedback = client.post(
        f"/chat/conversations/{conversation_id}/messages/{assistant['message_id']}/feedback",
        json={
            "kind": "answer_inappropriate",
            "feedback_text": "内容有误。",
            "career_item_ref": "fact:1",
        },
    )
    assert feedback.status_code == 422
    assert feedback.json()["detail"]["error"] == "career_item_not_found"


def test_career_audit_does_not_leak_body(
    sqlite_app: Any, client: TestClient,
    generation_helpers: dict[str, Any],
) -> None:
    """CAREER_PLANNING_GENERATED 审计只记条目数/证据数/画像引用，不含正文。"""
    account = _register(client)
    _swap_gateways(sqlite_app, _ProgrammableStructuredAdapter(output=_good_output()))
    conversation_id = _create_conversation(client)
    _send_career(client, conversation_id)
    generation_helpers["drive"](sqlite_app)

    from bridges.contracts.observability import AuditAction

    events = sqlite_app.state.observability_service.list_audit_events(
        account_id=account["id"], action=AuditAction.CAREER_PLANNING_GENERATED
    )
    assert len(events) == 1
    details = events[0].details
    assert details["item_counts"]["facts"] == 1
    assert details["item_counts"]["suggestions"] == 1
    assert "final_text" not in details
    serialized = str(details)
    assert "数据分析是值得考虑的方向" not in serialized


def test_second_account_planning_is_isolated_from_first(
    sqlite_app: Any, client: TestClient,
    generation_helpers: dict[str, Any],
) -> None:
    """两个账户执行相同问题：只受各自授权画像影响，缓存/引用不串号。"""
    _register(client)
    adapter_a = _ProgrammableStructuredAdapter(output=_good_output())
    _swap_gateways(sqlite_app, adapter_a)
    conversation_a = _create_conversation(client)
    _seed_assertion(client, "interest_preference", "账户 A 明确偏好金融行业。")
    _send_career(client, conversation_a)
    generation_helpers["drive"](sqlite_app)
    assert "金融行业" in json.dumps(adapter_a.requests[0], ensure_ascii=False)

    # 账户 B 无任何画像：同一问题其请求不含账户 A 的画像内容
    _register(client, tag="2")
    adapter_b = _ProgrammableStructuredAdapter(output=_good_output())
    _swap_gateways(sqlite_app, adapter_b)
    conversation_b = _create_conversation(client)
    _send_career(client, conversation_b)
    generation_helpers["drive"](sqlite_app)
    assert adapter_b.requests
    payload_b = json.dumps(adapter_b.requests[0], ensure_ascii=False)
    assert "金融行业" not in payload_b
