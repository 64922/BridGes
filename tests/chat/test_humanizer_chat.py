"""bridges-humanizer 聊天集成测试（Issue 28）。

SKILL 载荷走真实消息流程：started → humanizer 过程事件 → done（含结果
投影与最终文本）；重试沿用原任务输入；未注册 SKILL 拒绝发送。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.ai import CapabilityRegistry, ModelGateway
from bridges.ai.adapters import AdapterError, AdapterResult, RateLimitError
from bridges.contracts.ai import (
    CapabilityKind,
    CapabilityRecord,
    CapabilityStatus,
    RetryPolicy,
)

_CORPUS = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "bridges"
    / "skills"
    / "humanizer"
    / "skill"
    / "fixtures"
    / "rewrite_corpus.md"
).read_text(encoding="utf-8")
_SOURCE = _CORPUS.split("## 原文")[1].split("## 事实锁清单")[0].strip()

_SKILL_PAYLOAD = {
    "skill_id": "bridges-humanizer",
    "contract": {
        "path": "rewrite",
        "genre": "popular_science",
        "source_text": _SOURCE,
        "audience": "普通读者",
        "channel": "公众号",
        "length_target": "800 字",
    },
}


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
    """可编程结构化适配器（人味化编排使用）。"""

    def __init__(
        self,
        output: dict[str, Any] | None = None,
        error: AdapterError | None = None,
    ) -> None:
        self._output = output
        self._error = error

    def call(
        self,
        capability: CapabilityRecord,
        run_context: Any,
        payload: dict[str, Any],
    ) -> AdapterResult:
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
    final = (
        "光合作用指的是植物把光能转化为化学能的过程。研究显示，在光照"
        "充足的条件下，水稻叶片的净光合速率约为 25 μmol·m⁻²·s⁻¹；当"
        "温度超过 35°C 时，速率会显著下降（Zhang et al., 2021）。核心"
        "反应可以写成 6CO₂ + 6H₂O → C₆H₁₂O₆ + 6O₂。每固定 1 mol CO₂ "
        "大约需要 8-10 个光量子；有研究表明，在 25°C 条件下 Rubisco 的"
        "周转速率约为每秒 3 次，仅相当于部分 C4 植物的一半。需要特别说明"
        "的是，数据仅适用于受控温室条件下的栽培品种，不能直接外推到田间。"
        "你可以把光合作用比作植物的充电过程，但比喻到此为止，真正的机制"
        "是叶绿素吸收光子；对你说来，这意味着理解温室栽培需要先了解这些"
        "条件。目前只能说初步结果支持「高温会抑制光合效率」这一判断，尚"
        "不能证明其普遍适用（参见 Smith, 2020）。"
    )
    return {
        "final_text": final,
        "edits": [
            {
                "original": "研究显示",
                "revised": "研究显示",
                "kind": "no_change",
                "reason": "原文已符合科普文案表达规则。",
            }
        ],
        "fact_check": [
            {"item": "25 μmol·m⁻²·s⁻¹", "result": "已核实", "evidence": "与原文一致。"}
        ],
        "open_questions": ["田间高温胁迫下的实际速率仍待验证。"],
    }


@pytest.fixture
def sqlite_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """构建挂载 sqlite bridges.db 的应用（真实聊天服务 + 人味化编排）。"""
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
            "username": f"humanizer_user_{tag}",
            "qq_email": f"12345670{tag}@qq.com",
            "password": "Passw0rd123!",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["account"]


def _swap_gateways(sqlite_app: Any, structured: Any) -> None:
    gateway = _gateway_with(structured)
    sqlite_app.state.chat_service._gateway = gateway
    sqlite_app.state.humanizer_service._gateway = gateway


def _create_conversation(client: TestClient) -> str:
    response = client.post("/chat/conversations", json={})
    assert response.status_code == 201, response.text
    return response.json()["conversation_id"]


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


def _send_humanizer(client: TestClient, conversation_id: str) -> tuple[int, str]:
    return client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={
            "content": "文章人味化：改写光合作用科普段落",
            "skill_id": _SKILL_PAYLOAD["skill_id"],
            "skill_input": _SKILL_PAYLOAD,
        },
    ), ""


def test_humanizer_message_flows_through_real_message_stream(
    sqlite_app: Any, client: TestClient
) -> None:
    account = _register(client)
    _swap_gateways(sqlite_app, _ProgrammableStructuredAdapter(output=_good_output()))
    conversation_id = _create_conversation(client)

    response, _ = _send_humanizer(client, conversation_id)
    assert response.status_code == 200, response.text
    events = _parse_sse(response.text)
    names = [name for name, _ in events]
    assert names[0] == "started"
    assert "humanizer" in names  # 过程卡事件（loading 步骤）
    assert names[-1] == "done"

    done_data = events[-1][1]
    message = done_data["message"]
    assert message["status"] == "done"
    assert message["humanizer"] is not None
    humanizer = message["humanizer"]
    assert humanizer["skill_id"] == "bridges-humanizer"
    assert humanizer["skill_version"] == "1.0.0"
    assert humanizer["status"] == "done"
    assert humanizer["output"]["final_text"]
    assert humanizer["output"]["edits"]
    assert humanizer["output"]["fact_check"]
    assert "open_questions" in humanizer["output"]
    # 助手消息正文 = 最终文本（真实消息流程）
    assert message["content"] == humanizer["output"]["final_text"]

    # 历史中用户消息携带 SKILL 载荷快照（重试沿用）
    history = client.get(f"/chat/conversations/{conversation_id}").json()
    user_message = history["messages"][0]
    assert user_message["role"] == "user"
    assert user_message["skill"]["skill_id"] == "bridges-humanizer"


def test_humanizer_process_events_carry_chinese_states(
    sqlite_app: Any, client: TestClient
) -> None:
    account = _register(client)
    _swap_gateways(sqlite_app, _ProgrammableStructuredAdapter(output=_good_output()))
    conversation_id = _create_conversation(client)

    response, _ = _send_humanizer(client, conversation_id)
    events = _parse_sse(response.text)
    humanizer_events = [data for name, data in events if name == "humanizer"]
    assert humanizer_events
    first = humanizer_events[0]
    assert first["state"] == "loading"
    assert first["step_label"]  # 中文步骤说明
    assert all(step for step in first["progress_steps"] if step)


def test_retry_preserves_original_task_input(
    sqlite_app: Any, client: TestClient
) -> None:
    account = _register(client)
    _swap_gateways(
        sqlite_app, _ProgrammableStructuredAdapter(error=RateLimitError("slow"))
    )
    conversation_id = _create_conversation(client)

    response, _ = _send_humanizer(client, conversation_id)
    events = _parse_sse(response.text)
    assert events[-1][0] == "error"
    error_data = events[-1][1]
    assert error_data["error"]["retryable"] is True
    failed_message_id = error_data["message_id"]

    # 重试：同一轮新尝试，沿用原任务契约（输入不丢失）
    _swap_gateways(sqlite_app, _ProgrammableStructuredAdapter(output=_good_output()))
    retry = client.post(
        f"/chat/conversations/{conversation_id}/messages/{failed_message_id}/retry",
        json={},
    )
    assert retry.status_code == 200, retry.text
    retry_events = _parse_sse(retry.text)
    assert retry_events[-1][0] == "done"
    retry_message = retry_events[-1][1]["message"]
    assert retry_message["attempt_number"] == 2
    assert retry_message["humanizer"]["status"] == "done"

    history = client.get(f"/chat/conversations/{conversation_id}").json()
    user_message = next(m for m in history["messages"] if m["role"] == "user")
    assert user_message["skill"]["contract"]["source_text"] == _SOURCE


def test_unregistered_skill_rejected(
    sqlite_app: Any, client: TestClient
) -> None:
    account = _register(client)
    _swap_gateways(sqlite_app, _ProgrammableStructuredAdapter(output=_good_output()))
    conversation_id = _create_conversation(client)

    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={
            "content": "文章人味化任务",
            "skill_id": "not-a-real-skill",
            "skill_input": {
                "skill_id": "not-a-real-skill",
                "contract": {"path": "rewrite", "genre": "popular_science"},
            },
        },
    )
    assert response.status_code == 404, response.text
    assert response.json()["detail"]["error"] == "skill_not_found"


def test_invalid_skill_payload_rejected(
    sqlite_app: Any, client: TestClient
) -> None:
    account = _register(client)
    _swap_gateways(sqlite_app, _ProgrammableStructuredAdapter(output=_good_output()))
    conversation_id = _create_conversation(client)

    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={
            "content": "文章人味化任务",
            "skill_id": "bridges-humanizer",
            "skill_input": {
                "skill_id": "bridges-humanizer",
                "contract": {"path": "unknown-path"},
            },
        },
    )
    assert response.status_code == 422, response.text

    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": "文章人味化任务", "skill_id": "bridges-humanizer"},
    )
    assert response.status_code == 422, response.text


def test_humanizer_fact_lock_conflict_error_event(
    sqlite_app: Any, client: TestClient
) -> None:
    _register(client)
    violating = _good_output()
    violating["final_text"] = violating["final_text"].replace(
        "25 μmol·m⁻²·s⁻¹", "30 μmol·m⁻²·s⁻¹"
    )
    _swap_gateways(sqlite_app, _ProgrammableStructuredAdapter(output=violating))
    conversation_id = _create_conversation(client)

    response, _ = _send_humanizer(client, conversation_id)
    events = _parse_sse(response.text)
    assert events[-1][0] == "error"
    error_data = events[-1][1]
    assert error_data["error"]["code"] == "fact_lock_conflict"
    # 冲突停止交付：消息无违规正文，但保留错误说明
    message = error_data["message_id"]
    history = client.get(f"/chat/conversations/{conversation_id}").json()
    failed = next(m for m in history["messages"] if m["message_id"] == message)
    assert failed["status"] == "error"
    assert failed["error_code"] == "fact_lock_conflict"
    assert failed["humanizer"]["fact_lock_check"]["blocking_conflicts"]


def test_second_account_cannot_see_humanizer_results(
    sqlite_app: Any, client: TestClient
) -> None:
    _register(client, tag="1")
    _swap_gateways(sqlite_app, _ProgrammableStructuredAdapter(output=_good_output()))
    conversation_id = _create_conversation(client)
    response, _ = _send_humanizer(client, conversation_id)
    assert response.status_code == 200

    _register(client, tag="2")
    other = client.get(f"/chat/conversations/{conversation_id}")
    assert other.status_code == 404
