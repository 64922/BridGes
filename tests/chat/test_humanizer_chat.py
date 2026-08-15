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


def test_humanizer_fidelity_gate_conflict_error_event(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """硬门冲突（数字改动）：来源账本保真硬门优先于事实锁停止交付。"""
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
    assert error_data["error"]["code"] == "fidelity_gate_conflict"
    # 冲突停止交付：消息无违规正文，但保留错误说明
    message = error_data["message_id"]
    history = client.get(f"/chat/conversations/{conversation_id}").json()
    failed = next(m for m in history["messages"] if m["message_id"] == message)
    assert failed["status"] == "error"
    assert failed["error_code"] == "fidelity_gate_conflict"
    codes = [
        f["code"]
        for f in failed["humanizer"]["fidelity_check"]["blocking_failures"]
    ]
    assert "number_changed" in codes, codes


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
    assert error_data["error"]["code"] == "fidelity_gate_conflict"
    assert "恢复方式" in error_data["error"]["message"]

    # 草稿持久化：消息正文保留模型产出（未标记最终稿，由结果卡说明冲突）
    history = client.get(f"/chat/conversations/{conversation_id}").json()
    assistant = history["messages"][1]
    assert assistant["status"] == "error"
    assert assistant["content"] == violating["final_text"]
    humanizer = assistant["humanizer"]
    assert humanizer["status"] == "error"
    assert humanizer["error_code"] == "fidelity_gate_conflict"
    assert humanizer["fidelity_check"]["blocking_failures"]


def test_soft_gate_delivers_text_with_warnings(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """软门失败且修复后仍不过：交付正文 + 未完全满足项，不扣留正文。"""
    _register(client)
    bad = _good_output()
    # 只触发软门：科普文案禁止「综上所述」论文腔套话（保留全部事实锁）
    bad["final_text"] = "综上所述，" + bad["final_text"]
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
    """普通聊天消息自动路由，并保存可重试的路由/合同快照。

    Issue 01：候选正文必须与账本一致（候选不新增无来源 claim），
    沿用原文事实的合规改写才能通过保真硬门。
    """
    _register(client)
    compliant = {
        "final_text": "光合作用指的是植物把光能转化为化学能的过程。"
    }
    # 定向修订可能触发一次：序列两项都合规，终态稳定为 done
    adapter = _ProgrammableStructuredAdapter(sequence=[compliant, compliant])
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
    assert 1 <= adapter.calls <= 2


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


def test_natural_language_assumption_permission_passes_fidelity_gate(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """Issue 01 缺陷 a：授权假设的请求，prompt 权限与硬门判定一致。

    用户显式「可以假设」时，表达契约的 hypothetical_permission 必须映射进
    旧契约 allow_assumptions——合规标注的假设内容不再被 ASSUMPTION_NOT_ALLOWED
    拦截（首稿 → 硬门通过 → 交付）。
    """
    _register(client)
    source = "光合作用是植物把光能转化为化学能的过程。"
    compliant = (
        "光合作用是植物把光能转化为化学能的过程。"
        "比如，假设在强光下，光合速率可能会更高。"
    )
    adapter = _ProgrammableStructuredAdapter(output={"final_text": compliant})
    _swap_gateways(sqlite_app, adapter)
    conversation_id = _create_conversation(client)

    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": f"请帮我润色这篇科普文章，可以假设：{source}"},
    )
    assert response.status_code == 200, response.text
    created = response.json()
    skill = created["user_message"]["skill"]
    # 契约快照：表达契约与旧契约的权限真值一致（同一份授权）
    assert skill["expression_contract"]["hypothetical_permission"] is True
    assert skill["contract"]["allow_assumptions"] is True

    generation_helpers["drive"](sqlite_app)
    events = generation_helpers["subscribe"](
        client, conversation_id, created["assistant_message"]["message_id"]
    )
    assert events[-1][0] == "done", events[-1]
    message = events[-1][1]["message"]
    assert message["humanizer"]["status"] == "done"
    assert message["humanizer"]["output"]["final_text"] == compliant
    assert adapter.calls == 1


def test_natural_language_without_assumption_permission_still_blocks(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """Issue 01 缺陷 a：未授权假设时拦截语义不变（ASSUMPTION_NOT_ALLOWED）。"""
    _register(client)
    source = "光合作用是植物把光能转化为化学能的过程。"
    violating = (
        "光合作用是植物把光能转化为化学能的过程。"
        "比如，假设在强光下，光合速率可能会更高。"
    )
    adapter = _ProgrammableStructuredAdapter(
        sequence=[{"final_text": violating}, {"final_text": violating}]
    )
    _swap_gateways(sqlite_app, adapter)
    conversation_id = _create_conversation(client)

    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": f"请帮我润色这篇科普文章：{source}"},
    )
    assert response.status_code == 200, response.text
    created = response.json()
    skill = created["user_message"]["skill"]
    assert skill["contract"]["allow_assumptions"] is False

    generation_helpers["drive"](sqlite_app)
    events = generation_helpers["subscribe"](
        client, conversation_id, created["assistant_message"]["message_id"]
    )
    assert events[-1][0] == "error"
    error_data = events[-1][1]
    assert error_data["error"]["code"] == "fidelity_gate_conflict"
    # 投影保留保真失败明细：假设未授权是拦截原因
    history = client.get(f"/chat/conversations/{conversation_id}").json()
    assistant = next(
        m
        for m in history["messages"]
        if m["role"] == "assistant"
        and m["message_id"] == created["assistant_message"]["message_id"]
    )
    codes = [
        f["code"]
        for f in assistant["humanizer"]["fidelity_check"]["blocking_failures"]
    ]
    assert "assumption_not_allowed" in codes, codes


# ---------------------------------------------------------------------------
# Issue 05：文章定向二次修订（真实消息流：触发、检查点与重试恢复）
# ---------------------------------------------------------------------------


def _templated_draft() -> dict[str, Any]:
    return {
        "final_text": (
            "本助手认为，总而言之，番茄工作法把时间切成 25 分钟的工作块"
            "和 5 分钟的休息块，四个工作块后休息 15 分钟。未来可期。"
        )
    }


def _fixed_draft() -> dict[str, Any]:
    return {
        "final_text": (
            "把时间切成 25 分钟的工作块和 5 分钟的休息块，四个工作块后"
            "休息 15 分钟，这就是番茄工作法的大致框架。"
        )
    }


def test_natural_language_revision_triggers_second_call_and_checkpoint(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """高置信表达问题触发一次定向修订：两次写作调用、检查点随投影落库。"""
    _register(client)
    adapter = _ProgrammableStructuredAdapter(
        sequence=[_templated_draft(), _fixed_draft()]
    )
    _swap_gateways(sqlite_app, adapter)
    conversation_id = _create_conversation(client)

    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={
            "content": (
                "请帮我润色这篇科普文章：番茄工作法把时间切成 25 分钟的"
                "工作块和 5 分钟的休息块。四个工作块后休息 15 分钟。"
            )
        },
    )
    assert response.status_code == 200, response.text
    created = response.json()
    generation_helpers["drive"](sqlite_app)
    events = generation_helpers["subscribe"](
        client, conversation_id, created["assistant_message"]["message_id"]
    )
    assert events[-1][0] == "done"
    message = events[-1][1]["message"]
    humanizer = message["humanizer"]
    assert humanizer["status"] == "done"
    assert humanizer["writing_call_count"] == 2
    assert humanizer["revision"]["triggered"] is True
    assert humanizer["revision"]["final_state"] == "deliver_revised"
    assert adapter.calls == 2
    # 正文 = 修订稿（最小修改保留首稿已通过部分）
    assert message["content"] == _fixed_draft()["final_text"]
    # 刷新投影：计数与修订审计随结果投影持久化（重试恢复的依据）
    history = client.get(f"/chat/conversations/{conversation_id}").json()
    assistant = next(
        m
        for m in history["messages"]
        if m["role"] == "assistant"
        and m["message_id"] == created["assistant_message"]["message_id"]
    )
    assert assistant["humanizer"]["writing_call_count"] == 2


def test_retry_after_quota_exhausted_starts_fresh_budget(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """Issue 01 缺陷 b：2 次写作用尽仍保真失败后，手动重试 = 新一轮预算。

    重试不再撞 writing_call_limit_reached：模型被真实再调用（新预算），
    单轮内 2 次上限不变。
    """
    _register(client)
    violating = {"final_text": "番茄工作法把时间切成 35 分钟的工作块和 5 分钟的休息块。"}
    adapter = _ProgrammableStructuredAdapter(sequence=[violating, violating])
    _swap_gateways(sqlite_app, adapter)
    conversation_id = _create_conversation(client)

    created = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={
            "content": (
                "请帮我润色这篇科普文章：番茄工作法把时间切成 25 分钟的"
                "工作块和 5 分钟的休息块。四个工作块后休息 15 分钟。"
            )
        },
    ).json()
    generation_helpers["drive"](sqlite_app)
    events = generation_helpers["subscribe"](
        client, conversation_id, created["assistant_message"]["message_id"]
    )
    assert events[-1][0] == "error"
    assert adapter.calls == 2  # 首稿 + 修订，均破坏事实 → 停止交付
    failed_message_id = events[-1][1]["message_id"]

    # 重试：新一轮写作预算 → 重新起草（adapter2 被真实调用一次），不再
    # 直接报 writing_call_limit_reached
    adapter2 = _ProgrammableStructuredAdapter(output={"final_text": _fixed_draft()["final_text"]})
    _swap_gateways(sqlite_app, adapter2)
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
    assert adapter2.calls == 1
    assert retry_events[-1][0] == "done", retry_events[-1]
    retry_message = retry_events[-1][1]["message"]
    assert retry_message["humanizer"]["status"] == "done"
    assert retry_message["humanizer"]["writing_call_count"] == 1
    assert retry_message["humanizer"]["error_code"] is None


def test_retry_after_quota_exhausted_toggle_off_keeps_old_semantics(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """回滚开关：RETRY_RESETS_WRITING_BUDGET=False 时保留旧的计数沿用语义。"""
    monkeypatch.setattr(
        "bridges.chat.service.RETRY_RESETS_WRITING_BUDGET", False
    )
    _register(client, tag="3")
    violating = {"final_text": "番茄工作法把时间切成 35 分钟的工作块和 5 分钟的休息块。"}
    adapter = _ProgrammableStructuredAdapter(sequence=[violating, violating])
    _swap_gateways(sqlite_app, adapter)
    conversation_id = _create_conversation(client)

    created = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={
            "content": (
                "请帮我润色这篇科普文章：番茄工作法把时间切成 25 分钟的"
                "工作块和 5 分钟的休息块。四个工作块后休息 15 分钟。"
            )
        },
    ).json()
    generation_helpers["drive"](sqlite_app)
    events = generation_helpers["subscribe"](
        client, conversation_id, created["assistant_message"]["message_id"]
    )
    assert events[-1][0] == "error"
    failed_message_id = events[-1][1]["message_id"]

    # 旧语义：重试沿用计数 2，不再调用模型（无第三次写作调用）
    adapter2 = _ProgrammableStructuredAdapter(output={"final_text": "不应被调用"})
    _swap_gateways(sqlite_app, adapter2)
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
    assert adapter2.calls == 0
    assert retry_events[-1][0] == "error"
    assert retry_events[-1][1]["error"]["code"] == "writing_call_limit_reached"
    # 新尝试的结果投影记录了明确错误（无第三次写作调用）
    history = client.get(f"/chat/conversations/{conversation_id}").json()
    retry_assistant = next(
        m
        for m in history["messages"]
        if m["role"] == "assistant"
        and m["message_id"] == retried["assistant_message"]["message_id"]
    )
    assert retry_assistant["humanizer"]["error_code"] == "writing_call_limit_reached"
    assert retry_assistant["humanizer"]["writing_call_count"] == 2


def test_retry_fresh_round_still_capped_at_two_calls(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """Issue 01 缺陷 b：重试获得新预算，但新一轮单轮 2 次上限仍生效。"""
    _register(client, tag="4")
    violating = {"final_text": "番茄工作法把时间切成 35 分钟的工作块和 5 分钟的休息块。"}
    adapter = _ProgrammableStructuredAdapter(sequence=[violating, violating])
    _swap_gateways(sqlite_app, adapter)
    conversation_id = _create_conversation(client)

    created = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={
            "content": (
                "请帮我润色这篇科普文章：番茄工作法把时间切成 25 分钟的"
                "工作块和 5 分钟的休息块。四个工作块后休息 15 分钟。"
            )
        },
    ).json()
    generation_helpers["drive"](sqlite_app)
    events = generation_helpers["subscribe"](
        client, conversation_id, created["assistant_message"]["message_id"]
    )
    assert events[-1][0] == "error"
    failed_message_id = events[-1][1]["message_id"]

    # 重试获得新预算：新一轮内首稿 + 修订仍恰好 2 次调用，之后保真失败停止
    adapter2 = _ProgrammableStructuredAdapter(sequence=[violating, violating])
    _swap_gateways(sqlite_app, adapter2)
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
    assert adapter2.calls == 2  # 新一轮内仍至多 2 次写作调用
    assert retry_events[-1][0] == "error"
    # 新一轮用尽后是保真失败（不是立即的 writing_call_limit_reached）
    assert retry_events[-1][1]["error"]["code"] == "fidelity_gate_conflict"


def test_retry_starts_fresh_round_after_revision_failure(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """修订模型失败后手动重试：新一轮写作预算，重新起草（不再沿用计数）。"""
    _register(client)
    adapter = _ProgrammableStructuredAdapter(
        sequence=[_templated_draft(), AdapterError(code="transient", message="慢")]
    )
    _swap_gateways(sqlite_app, adapter)
    conversation_id = _create_conversation(client)

    created = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={
            "content": (
                "请帮我润色这篇科普文章：番茄工作法把时间切成 25 分钟的"
                "工作块和 5 分钟的休息块。四个工作块后休息 15 分钟。"
            )
        },
    ).json()
    generation_helpers["drive"](sqlite_app)
    events = generation_helpers["subscribe"](
        client, conversation_id, created["assistant_message"]["message_id"]
    )
    # 修订失败 → 交付首稿的真实软审稿状态（WARN），不把未通过稿标为成功
    assert events[-1][0] == "done"
    first_message = events[-1][1]["message"]
    assert first_message["humanizer"]["writing_call_count"] == 1
    assert first_message["humanizer"]["revision"]["skipped_reason"].startswith(
        "model_error:"
    )
    assert adapter.calls == 2
    first_message_id = first_message["message_id"]

    # 重试：新一轮写作预算 → 重新起草（adapter2 先被调用一次用于首稿），
    # 不再沿用旧计数 1 与旧正文
    adapter2 = _ProgrammableStructuredAdapter(sequence=[_fixed_draft()])
    _swap_gateways(sqlite_app, adapter2)
    retry = client.post(
        f"/chat/conversations/{conversation_id}/messages/{first_message_id}/retry",
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
    assert retry_message["humanizer"]["status"] == "done"
    assert retry_message["humanizer"]["writing_call_count"] == 1
    assert retry_message["humanizer"]["error_code"] is None
    # 重新起草：adapter2 的首稿调用已发生（新预算），正文 = 新首稿
    assert adapter2.calls >= 1
    assert retry_message["content"] == _fixed_draft()["final_text"]


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
