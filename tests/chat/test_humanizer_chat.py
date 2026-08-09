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
    """可编程结构化适配器（人味化编排使用）。

    ``sequence``（Issue 07）按调用次数依次返回输出或抛错误（末项复用），
    用于「初始产出 → 至多一次软门修复」的调用序列。
    """

    def __init__(
        self,
        output: dict[str, Any] | None = None,
        error: AdapterError | None = None,
        sequence: list[Any] | None = None,
    ) -> None:
        self._output = output
        self._error = error
        self._sequence = list(sequence or [])
        self.calls = 0

    def call(
        self,
        capability: CapabilityRecord,
        run_context: Any,
        payload: dict[str, Any],
    ) -> AdapterResult:
        self.calls += 1
        if self._sequence:
            item = self._sequence[min(self.calls - 1, len(self._sequence) - 1)]
            if isinstance(item, AdapterError):
                raise item
            return AdapterResult(actual_model_id=capability.model_id, output=item)
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


def _send_humanizer(
    client: TestClient, conversation_id: str, *, use_knowledge_base: bool = False
) -> dict[str, Any]:
    """Issue 02：发送 SKILL 消息 → 创建响应（运行已入队，无 SSE 流）。

    Issue 07：改写默认关闭全局知识库（与前端对话框默认一致）；显式开启
    时检索轮次进入改写证据合同（仅作补充，不替代原文）。
    """
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={
            "content": "文章人味化：改写光合作用科普段落",
            "skill_id": _SKILL_PAYLOAD["skill_id"],
            "skill_input": _SKILL_PAYLOAD,
            "use_knowledge_base": use_knowledge_base,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_humanizer_message_flows_through_real_message_stream(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    _register(client)
    _swap_gateways(sqlite_app, _ProgrammableStructuredAdapter(output=_good_output()))
    conversation_id = _create_conversation(client)

    created = _send_humanizer(client, conversation_id)
    generation_helpers["drive"](sqlite_app)
    events = generation_helpers["subscribe"](
        client, conversation_id, created["assistant_message"]["message_id"]
    )
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
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    _register(client)
    _swap_gateways(sqlite_app, _ProgrammableStructuredAdapter(output=_good_output()))
    conversation_id = _create_conversation(client)

    created = _send_humanizer(client, conversation_id)
    generation_helpers["drive"](sqlite_app)
    events = generation_helpers["subscribe"](
        client, conversation_id, created["assistant_message"]["message_id"]
    )
    humanizer_events = [data for name, data in events if name == "humanizer"]
    assert humanizer_events
    first = humanizer_events[0]
    assert first["state"] == "loading"
    assert first["step_label"]  # 中文步骤说明
    assert all(step for step in first["progress_steps"] if step)


def test_retry_preserves_original_task_input(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    _register(client)
    _swap_gateways(
        sqlite_app, _ProgrammableStructuredAdapter(error=RateLimitError("slow"))
    )
    conversation_id = _create_conversation(client)

    created = _send_humanizer(client, conversation_id)
    generation_helpers["drive"](sqlite_app)
    events = generation_helpers["subscribe"](
        client, conversation_id, created["assistant_message"]["message_id"]
    )
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
    retried = retry.json()
    generation_helpers["drive"](sqlite_app)
    retry_events = generation_helpers["subscribe"](
        client, conversation_id, retried["assistant_message"]["message_id"]
    )
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
    _register(client)
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
    assert response.status_code == 410, response.text
    assert response.json()["detail"]["error"] == "user_extensions_retired"


def test_invalid_skill_payload_rejected(
    sqlite_app: Any, client: TestClient
) -> None:
    _register(client)
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
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    _register(client)
    violating = _good_output()
    violating["final_text"] = violating["final_text"].replace(
        "25 μmol·m⁻²·s⁻¹", "30 μmol·m⁻²·s⁻¹"
    )
    _swap_gateways(sqlite_app, _ProgrammableStructuredAdapter(output=violating))
    conversation_id = _create_conversation(client)

    created = _send_humanizer(client, conversation_id)
    generation_helpers["drive"](sqlite_app)
    events = generation_helpers["subscribe"](
        client, conversation_id, created["assistant_message"]["message_id"]
    )
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
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    _register(client, tag="1")
    _swap_gateways(sqlite_app, _ProgrammableStructuredAdapter(output=_good_output()))
    conversation_id = _create_conversation(client)
    created = _send_humanizer(client, conversation_id)
    assert created["run_id"]
    generation_helpers["drive"](sqlite_app)
    events = generation_helpers["subscribe"](
        client, conversation_id, created["assistant_message"]["message_id"]
    )
    assert events[-1][0] == "done"

    _register(client, tag="2")
    other = client.get(f"/chat/conversations/{conversation_id}")
    assert other.status_code == 404
    assert (
        client.get(
            f"/chat/conversations/{conversation_id}/messages/"
            f"{created['assistant_message']['message_id']}/events"
        ).status_code
        == 404
    )


# ---------------------------------------------------------------------------
# Issue 07：改写默认不检索知识库；草稿持久化（硬门失败也保留正文）；
# 软门交付正文 + 具体警告（至多一次修复）。
# ---------------------------------------------------------------------------

def test_rewrite_skips_knowledge_base_retrieval(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """改写路径：进入本地检索阶段但关闭知识库来源（知识库材料绝不检索/引用）。

    Issue 07 意图是「默认不检索全局知识库」而非「跳过整个检索阶段」——
    附件是改写原文，附件层必须披露（Issue 04 绑定契约），关闭仅作用于
    知识库层（use_knowledge_base=false 时检索服务不查知识库来源）。
    """
    _register(client)
    _swap_gateways(sqlite_app, _ProgrammableStructuredAdapter(output=_good_output()))
    conversation_id = _create_conversation(client)

    created = _send_humanizer(client, conversation_id)
    generation_helpers["drive"](sqlite_app)
    events = generation_helpers["subscribe"](
        client, conversation_id, created["assistant_message"]["message_id"]
    )
    stages = [data for name, data in events if name == "stage"]
    retrieval = [s for s in stages if s["stage"] == "local_retrieval"]
    assert retrieval and retrieval[0]["status"] == "active"
    assert events[-1][0] == "done"


def test_hard_gate_conflict_keeps_draft_content(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """硬门冲突：阻止标记最终稿（error 事件 + 冲突说明），草稿仍保留。

    验收：切换会话/刷新后可见草稿、阶段与结果；硬门说明冲突与恢复方式。
    """
    _register(client)
    violating = _good_output()
    violating["final_text"] = violating["final_text"].replace(
        "25 μmol·m⁻²·s⁻¹", "30 μmol·m⁻²·s⁻¹"
    )
    _swap_gateways(sqlite_app, _ProgrammableStructuredAdapter(output=violating))
    conversation_id = _create_conversation(client)

    created = _send_humanizer(client, conversation_id)
    generation_helpers["drive"](sqlite_app)
    events = generation_helpers["subscribe"](
        client, conversation_id, created["assistant_message"]["message_id"]
    )
    assert events[-1][0] == "error"
    error_data = events[-1][1]
    assert error_data["error"]["code"] == "fact_lock_conflict"
    assert "恢复方式" in error_data["error"]["message"]

    # 草稿持久化：消息正文保留模型产出（未标记最终稿，由结果卡说明冲突）
    history = client.get(f"/chat/conversations/{conversation_id}").json()
    assistant = history["messages"][1]
    assert assistant["status"] == "error"
    assert assistant["content"] == violating["final_text"]
    humanizer = assistant["humanizer"]
    assert humanizer["status"] == "error"
    assert humanizer["error_code"] == "fact_lock_conflict"
    assert humanizer["fact_lock_check"]["blocking_conflicts"]


def test_soft_gate_delivers_text_with_warnings(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """软门失败且修复后仍不过：交付正文 + 未完全满足项，不扣留正文。"""
    _register(client)
    bad = _good_output()
    # 只删除类比/边界/行动相关性句（保持全部事实锁 → 只触发软门）
    bad["final_text"] = bad["final_text"].replace(
        "你可以把光合作用比作植物的充电过程，但比喻到此为止，真正的机制"
        "是叶绿素吸收光子；对你说来，这意味着理解温室栽培需要先了解这些"
        "条件。",
        "",
    )
    _swap_gateways(sqlite_app, _ProgrammableStructuredAdapter(sequence=[bad, bad]))
    conversation_id = _create_conversation(client)

    created = _send_humanizer(client, conversation_id)
    generation_helpers["drive"](sqlite_app)
    events = generation_helpers["subscribe"](
        client, conversation_id, created["assistant_message"]["message_id"]
    )
    assert events[-1][0] == "done"
    done_data = events[-1][1]
    message = done_data["message"]
    assert message["status"] == "done"
    humanizer = message["humanizer"]
    assert humanizer["status"] == "needs_human"
    assert humanizer["output"]["final_text"] == bad["final_text"]
    assert humanizer["output"]["quality_status"] == "warn"
    assert humanizer["quality_warnings"]
    assert humanizer["repair_attempts"] == 1  # 恰好一次修复，不循环
    assert message["content"] == bad["final_text"]  # 正文照常交付


def test_rewrite_explicit_knowledge_base_runs_retrieval(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """改写 + 显式开启补充检索：本地检索阶段执行（默认关闭仍跳过）。"""
    _register(client)
    _swap_gateways(sqlite_app, _ProgrammableStructuredAdapter(output=_good_output()))
    conversation_id = _create_conversation(client)

    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={
            "content": "文章人味化：改写光合作用科普段落",
            "skill_id": _SKILL_PAYLOAD["skill_id"],
            "skill_input": _SKILL_PAYLOAD,
            "use_knowledge_base": True,
        },
    )
    assert response.status_code == 200, response.text
    created = response.json()
    generation_helpers["drive"](sqlite_app)
    events = generation_helpers["subscribe"](
        client, conversation_id, created["assistant_message"]["message_id"]
    )
    stages = [data for name, data in events if name == "stage"]
    retrieval = [s for s in stages if s["stage"] == "local_retrieval"]
    assert retrieval and retrieval[0]["status"] in ("active", "done")
    assert events[-1][0] == "done"


def test_natural_language_message_uses_the_existing_humanizer_lifecycle(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """普通聊天消息自动路由，并保存可重试的路由/合同快照。"""
    _register(client)
    adapter = _ProgrammableStructuredAdapter(output=_good_output())
    _swap_gateways(sqlite_app, adapter)
    conversation_id = _create_conversation(client)

    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={
            "content": (
                "请帮我润色这篇科普文章，面向高中生，长度不超过 800 字，"
                "数字不能改：光合作用是植物把光能转化为化学能的过程。"
            )
        },
    )
    assert response.status_code == 200, response.text
    created = response.json()
    skill = created["user_message"]["skill"]
    assert skill["skill_id"] == "bridges-humanizer"
    assert skill["route"]["source"] == "natural_language"
    assert skill["route"]["version"] == "humanizer-route-v1"
    assert skill["contract"]["audience"] == "高中生"
    assert skill["contract"]["length_target"] == "不超过 800 字"
    assert skill["contract"]["source_text"].startswith("光合作用")

    generation_helpers["drive"](sqlite_app)
    events = generation_helpers["subscribe"](
        client, conversation_id, created["assistant_message"]["message_id"]
    )
    assert events[-1][0] == "done"
    assert events[-1][1]["message"]["humanizer"]["output"]["final_text"]
    assert adapter.calls == 1


def test_natural_language_route_without_source_does_not_call_model(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    _register(client)
    adapter = _ProgrammableStructuredAdapter(output=_good_output())
    _swap_gateways(sqlite_app, adapter)
    conversation_id = _create_conversation(client)

    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": "请帮我润色这篇文章，面向普通读者。"},
    )
    assert response.status_code == 200, response.text
    created = response.json()
    assert created["user_message"]["skill"]["contract"]["source_text"] is None

    generation_helpers["drive"](sqlite_app)
    events = generation_helpers["subscribe"](
        client, conversation_id, created["assistant_message"]["message_id"]
    )
    assert events[-1][0] == "error"
    assert events[-1][1]["error"]["code"] == "empty_source"
    assert adapter.calls == 0


def test_natural_language_first_turn_is_idempotent(
    sqlite_app: Any, client: TestClient
) -> None:
    _register(client)
    _swap_gateways(sqlite_app, _ProgrammableStructuredAdapter(output=_good_output()))
    payload = {
        "content": "给我润色这篇文章：光合作用是植物把光能转化为化学能。",
        "idempotency_key": "natural-humanizer-first-turn-1",
    }

    first = client.post("/chat/first-turn", json=payload)
    second = client.post("/chat/first-turn", json=payload)

    assert first.status_code == 201, first.text
    assert second.status_code == 200, second.text
    first_body = first.json()
    second_body = second.json()
    assert second_body["idempotent_replay"] is True
    assert (
        second_body["user_message"]["message_id"]
        == first_body["user_message"]["message_id"]
    )
    assert second_body["user_message"]["skill"]["route"]["source"] == (
        "natural_language"
    )
