"""Issue 04: 人味化轮次 × 最小画像切片注入与披露的聊天级集成测试。

四种矩阵（有画像 / 无画像 / use_profile=False / 画像服务异常）走真实消息
流：断言模型请求注入切片（捕获结构化载荷）、助手消息携带与普通/生涯路径
一致的「本次上下文说明」披露（刷新后一致）、主流程正常完成；扫描式断言
运行锁、SSE 事件与人味化产物中不出现画像原文，披露只含条数与类别。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.ai import CapabilityRegistry, ModelGateway
from bridges.ai.adapters import AdapterResult
from bridges.contracts.ai import (
    CapabilityKind,
    CapabilityRecord,
    CapabilityStatus,
    RetryPolicy,
)
from bridges.contracts.workflows import ObjectDomain, RunContextEnvelope

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

_PROFILE_VALUE = "喜欢数据分析与可视化"


def _context(
    run_id: str = "run-profile-slice", account_id: str = "profile-account"
) -> RunContextEnvelope:
    return RunContextEnvelope(
        run_id=run_id,
        account_id=account_id,
        project_id="conversation-1",
        workflow_name="chat",
        workflow_version="1",
        object_domain=ObjectDomain.PERSONAL_VAULT,
        submitted_at=datetime.now(UTC),
    )


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


class _CapturingStructuredAdapter:
    """捕获人味化模型载荷的结构化适配器（断言切片注入与不泄漏）。"""

    def __init__(self, output: dict[str, Any] | None = None) -> None:
        self._output = output or {}
        self.requests: list[dict[str, Any]] = []

    def call(
        self,
        capability: CapabilityRecord,
        run_context: Any,
        payload: dict[str, Any],
    ) -> AdapterResult:
        self.requests.append(payload)
        return AdapterResult(actual_model_id=capability.model_id, output=self._output)


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
                "revised": "数据显示",
                "kind": "word_choice",
                "reason": "科普文案规则：使用面向读者的表述。",
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
            "username": f"humanizer_profile_user_{tag}",
            "qq_email": f"1234567{tag}0@qq.com",
            "password": "Passw0rd123!",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["account"]


def _create_conversation(client: TestClient) -> str:
    response = client.post("/chat/conversations", json={})
    assert response.status_code == 201, response.text
    return response.json()["conversation_id"]


def _swap_gateways(sqlite_app: Any, structured: Any) -> None:
    gateway = _gateway_with(structured)
    sqlite_app.state.chat_service._gateway = gateway  # noqa: SLF001
    sqlite_app.state.humanizer_service._gateway = gateway  # noqa: SLF001


def _seed_assertion(sqlite_app: Any, account_id: str) -> str:
    """播种一条可进入日常模式切片的高把握画像记录（四维直接写入）。

    直接经四维服务以 HIGH 把握度落库（迁移路径会把旧断言标为 LOW、
    不可召回），保证切片编译器把该记录纳入本轮最小切片。
    """
    from bridges.contracts.profiles import FourDimension, FourDimensionConfidence

    record = sqlite_app.state.four_dimension_profile_service.upsert_automatic_record(
        account_id,
        dimension=FourDimension.KNOWLEDGE_INTEREST,
        content=_PROFILE_VALUE,
        action="create",
        confidence=FourDimensionConfidence.HIGH,
        change_note="Issue 04 测试播种",
    )
    return record.record_id


def _send_humanizer(
    client: TestClient, conversation_id: str
) -> dict[str, Any]:
    """发送显式 SKILL 人味化消息（旧兼容路径 + 新建路径共用入口）。"""
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={
            "content": "文章人味化：改写光合作用科普段落",
            "skill_id": _SKILL_PAYLOAD["skill_id"],
            "skill_input": _SKILL_PAYLOAD,
            "use_knowledge_base": False,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _prompt_text(adapter: _CapturingStructuredAdapter) -> str:
    """把最近一次模型请求的全部消息拼成纯文本（断言切片注入）。"""
    assert adapter.requests, "人味化轮次必须调用结构化模型"
    payload = adapter.requests[-1]
    return "\n".join(
        str(message.get("content") or "")
        for message in payload.get("messages", [])
    )


def _run_locks_text(sqlite_app: Any) -> str:
    """运行锁表的全量文本（扫描式断言：画像原文不得进入运行锁）。"""
    rows = sqlite_app.state.bridges_database.connection.execute(
        "SELECT * FROM model_run_locks"
    ).fetchall()
    return str(rows)


def _sse_text(events: list[tuple[str, dict[str, Any]]]) -> str:
    return json.dumps([data for _, data in events], ensure_ascii=False)


def test_humanizer_with_profile_injects_slice_and_discloses(
    sqlite_app: Any,
    client: TestClient,
    generation_helpers: dict[str, Any],
) -> None:
    """有画像：人味化 prompt 注入切片，消息披露 ready 且刷新后一致。"""
    account = _register(client)
    adapter = _CapturingStructuredAdapter(output=_good_output())
    _swap_gateways(sqlite_app, adapter)
    conversation_id = _create_conversation(client)
    _seed_assertion(sqlite_app, account["id"])

    created = _send_humanizer(client, conversation_id)
    generation_helpers["drive"](sqlite_app)
    events = generation_helpers["subscribe"](
        client, conversation_id, created["assistant_message"]["message_id"]
    )
    assert events[-1][0] == "done"

    # 注入：模型请求的系统提示包含画像切片（「风格与背景偏好」用途）
    prompt = _prompt_text(adapter)
    assert "风格与背景偏好" in prompt
    assert _PROFILE_VALUE in prompt
    # 不改变证据与来源合同：切片不进入材料区与输出合同
    assert "以下是本轮为你参考的已授权信息" in prompt

    done_data = events[-1][1]
    message = done_data["message"]
    assert message["status"] == "done"
    # 披露：与普通/生涯路径一致的「本次上下文说明」（条数与授权说明）
    note = message["context_note"]
    assert note is not None
    assert note["state"] == "ready"
    assert note["profile_enabled"] is True
    assert note["profile_item_count"] == 1
    assert "你已授权的用户背景信息" in note["note"]
    # 披露只含条数与类别，不含画像原文
    assert _PROFILE_VALUE not in note["note"]

    # 刷新后一致：重新拉取历史消息，披露仍保留
    history = client.get(f"/chat/conversations/{conversation_id}").json()
    assistant = next(
        m
        for m in history["messages"]
        if m["role"] == "assistant"
        and m["message_id"] == created["assistant_message"]["message_id"]
    )
    assert assistant["context_note"]["state"] == "ready"
    assert assistant["context_note"]["profile_item_count"] == 1

    # 审计：人味化轮次记录 profile_used 与条数（不含画像原文）
    from bridges.contracts.observability import AuditAction

    humanizer_audits = sqlite_app.state.observability_service.list_audit_events(
        account_id=account["id"], action=AuditAction.HUMANIZER_GENERATE
    )
    assert humanizer_audits
    latest = humanizer_audits[-1]
    assert latest.details["profile_used"] is True
    assert latest.details["profile_item_count"] == 1
    assert _PROFILE_VALUE not in str(latest.model_dump())


def test_humanizer_no_profile_completes_without_profile_content(
    sqlite_app: Any,
    client: TestClient,
    generation_helpers: dict[str, Any],
) -> None:
    """无画像：人味化正常完成，披露为空态且模型请求不含画像内容。"""
    _register(client)
    adapter = _CapturingStructuredAdapter(output=_good_output())
    _swap_gateways(sqlite_app, adapter)
    conversation_id = _create_conversation(client)

    created = _send_humanizer(client, conversation_id)
    generation_helpers["drive"](sqlite_app)
    events = generation_helpers["subscribe"](
        client, conversation_id, created["assistant_message"]["message_id"]
    )
    assert events[-1][0] == "done"
    assert "风格与背景偏好" not in _prompt_text(adapter)

    message = events[-1][1]["message"]
    assert message["status"] == "done"
    assert message["humanizer"]["status"] == "done"
    note = message["context_note"]
    assert note is not None
    assert note["state"] == "empty"
    assert note["profile_item_count"] == 0


def test_humanizer_use_profile_false_skips_compile_and_disclosure(
    sqlite_app: Any, client: TestClient
) -> None:
    """use_profile=False：零画像编译、零画像披露，人味化主流程正常。"""
    account = _register(client)
    adapter = _CapturingStructuredAdapter(output=_good_output())
    _swap_gateways(sqlite_app, adapter)
    conversation_id = _create_conversation(client)
    _seed_assertion(sqlite_app, account["id"])

    # 服务层直驱（API 载荷不暴露 use_profile，开关经服务层传递）：
    # 关闭画像后本轮不编译、不注入、不披露任何画像内容。
    chat = sqlite_app.state.chat_service
    _, assistant = chat.start_generation(
        account["id"],
        conversation_id,
        "文章人味化：改写光合作用科普段落",
        skill_id=_SKILL_PAYLOAD["skill_id"],
        skill_input=_SKILL_PAYLOAD,
        use_knowledge_base=False,
        use_profile=False,
    )
    run = chat._repo.get_run_by_message(  # noqa: SLF001
        account["id"], assistant.message_id
    )
    assert run is not None
    list(
        chat.stream_generation(
            account["id"],
            conversation_id,
            assistant.message_id,
            _context(run_id=run.run_id, account_id=account["id"]),
            use_profile=False,
        )
    )
    final = chat.message_projection(account["id"], assistant.message_id)
    assert final is not None and final.status.value == "done"

    # 零注入：模型请求不含任何画像内容
    assert "风格与背景偏好" not in _prompt_text(adapter)
    assert _PROFILE_VALUE not in _prompt_text(adapter)
    # 零画像披露：off 态只说明已关闭，不含画像原文
    note = final.context_note
    assert note is not None
    assert note.state.value == "off"
    assert note.profile_enabled is False
    assert note.profile_item_count == 0
    assert _PROFILE_VALUE not in note.note


def test_humanizer_profile_service_error_degrades_gracefully(
    sqlite_app: Any,
    client: TestClient,
    generation_helpers: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """画像服务异常：披露 error 态准确降级，人味化照常完成而非失败。"""
    account = _register(client)
    adapter = _CapturingStructuredAdapter(output=_good_output())
    _swap_gateways(sqlite_app, adapter)
    conversation_id = _create_conversation(client)
    _seed_assertion(sqlite_app, account["id"])

    def _broken_compile(*args: object, **kwargs: object):
        raise RuntimeError("profile store broken")

    monkeypatch.setattr(
        sqlite_app.state.four_dimension_profile_service,
        "compile_chat_slice",
        _broken_compile,
    )

    created = _send_humanizer(client, conversation_id)
    generation_helpers["drive"](sqlite_app)
    events = generation_helpers["subscribe"](
        client, conversation_id, created["assistant_message"]["message_id"]
    )
    # 主流程正常完成（准确降级而非失败）
    assert events[-1][0] == "done"
    message = events[-1][1]["message"]
    assert message["status"] == "done"
    assert message["humanizer"]["status"] == "done"
    # 降级披露：error 态，不注入任何画像内容
    note = message["context_note"]
    assert note is not None
    assert note["state"] == "error"
    assert note["profile_item_count"] == 0
    assert "风格与背景偏好" not in _prompt_text(adapter)
    assert _PROFILE_VALUE not in _prompt_text(adapter)


def test_humanizer_profile_text_never_enters_locks_sse_or_artifacts(
    sqlite_app: Any,
    client: TestClient,
    generation_helpers: dict[str, Any],
) -> None:
    """扫描式断言：画像原文不进入运行锁、SSE 事件与人味化产物。"""
    account = _register(client)
    adapter = _CapturingStructuredAdapter(output=_good_output())
    _swap_gateways(sqlite_app, adapter)
    conversation_id = _create_conversation(client)
    _seed_assertion(sqlite_app, account["id"])

    created = _send_humanizer(client, conversation_id)
    generation_helpers["drive"](sqlite_app)
    events = generation_helpers["subscribe"](
        client, conversation_id, created["assistant_message"]["message_id"]
    )
    assert events[-1][0] == "done"

    # 运行锁：锁只含调用元数据，绝不复制 prompt/画像原文
    assert _PROFILE_VALUE not in _run_locks_text(sqlite_app)
    # SSE 事件：不含画像原文（披露只含条数与类别）
    sse_text = _sse_text(events)
    assert _PROFILE_VALUE not in sse_text
    # 人味化产物（投影 JSON）：不含画像原文
    message = events[-1][1]["message"]
    assert _PROFILE_VALUE not in json.dumps(message["humanizer"], ensure_ascii=False)
    # 画像原文只允许出现在模型请求（注入）与画像服务内部，绝不落库消息
    assert _PROFILE_VALUE not in json.dumps(message, ensure_ascii=False)


def test_humanizer_natural_language_route_injects_profile_slice(
    sqlite_app: Any,
    client: TestClient,
    generation_helpers: dict[str, Any],
) -> None:
    """自然语言路由（表达任务新流程）同样注入切片并披露。"""
    account = _register(client)
    # 首稿 + 定向修订两次调用；两个 prompt 都应包含画像切片
    templated = {
        "final_text": (
            "本助手认为，总而言之，番茄工作法把时间切成 25 分钟的工作块"
            "和 5 分钟的休息块，四个工作块后休息 15 分钟。未来可期。"
        )
    }
    fixed = {
        "final_text": (
            "把时间切成 25 分钟的工作块和 5 分钟的休息块，四个工作块后"
            "休息 15 分钟，这就是番茄工作法的大致框架。"
        )
    }

    class _SequenceAdapter(_CapturingStructuredAdapter):
        def __init__(self) -> None:
            super().__init__()
            self._sequence = [templated, fixed]

        def call(
            self,
            capability: CapabilityRecord,
            run_context: Any,
            payload: dict[str, Any],
        ) -> AdapterResult:
            self.requests.append(payload)
            index = min(len(self.requests) - 1, len(self._sequence) - 1)
            return AdapterResult(
                actual_model_id=capability.model_id,
                output=self._sequence[index],
            )

    adapter = _SequenceAdapter()
    _swap_gateways(sqlite_app, adapter)
    conversation_id = _create_conversation(client)
    _seed_assertion(sqlite_app, account["id"])

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

    # 首稿与定向修订 prompt 都注入画像切片
    assert len(adapter.requests) == 2
    for request in adapter.requests:
        prompt = "\n".join(
            str(message.get("content") or "")
            for message in request.get("messages", [])
        )
        assert "风格与背景偏好" in prompt
        assert _PROFILE_VALUE in prompt

    message = events[-1][1]["message"]
    assert message["status"] == "done"
    note = message["context_note"]
    assert note is not None
    assert note["state"] == "ready"
    assert note["profile_item_count"] == 1
    assert _PROFILE_VALUE not in note["note"]
