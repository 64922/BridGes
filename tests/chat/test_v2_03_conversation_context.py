"""V2 Issue 03 验收测试：长对话上下文（会话上下文编译器）。

覆盖验收标准（``.scratch/bridges-v2/issues/03-conversation-context.md``）：
1. 每轮按锁定模型的已验证窗口编译当前请求、近期消息、旧消息摘要及必要
   原文，保留输出与工具预算；原始消息完整保存；
2. 动态预算明确预留系统规则、输出、工具及图片成本；编译记录模型 ID、
   预算版本、摘要版本和实际采用的原文消息 ID；
3. 摘要记录其来源消息范围；用户引用早先实体或约定时，能补回相关原文并
   记录本轮使用的消息 ID；
4. 预算不足时先裁掉低相关材料，不丢当前请求或已取得的关键证据；找不到
   旧原文时承认不确定并询问；
5. 私人会话材料不会整体发送到公开检索服务，跨账户历史不可召回；模型
   切换后下一轮重新计算预算。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient

from bridges.ai.adapters import StreamChunk
from bridges.ai.fixed_models import (
    CHAT_MODEL_ID,
    FACTORY_MAIN_MODEL_CONTEXT_WINDOW,
    FACTORY_MAIN_MODEL_MAX_INPUT_TOKENS,
)
from bridges.chat.context_compiler import (
    CONTEXT_BUDGET_VERSION,
    DEFAULT_CONTEXT_WINDOW,
    IMAGE_COST_TOKENS,
    OUTPUT_RESERVE_MARGIN_TOKENS,
    OUTPUT_RESERVE_TOKENS,
    SUMMARY_VERSION,
    TOOL_RESERVE_TOKENS,
    compile_turn_context,
    estimate_tokens,
    verified_context_window,
)
from bridges.chat.repository import MessageRecord
from bridges.contracts.chat import ChatMessageRole, ChatMessageStatus, ChatMode
from bridges.contracts.observability import AuditAction
from tests.chat.test_chat_api import (
    _create_conversation,
    _gateway_with,
    _register,
)
from tests.chat.test_issue02_durable_generation import (
    client,  # noqa: F401 - 复用客户端夹具
    sqlite_app,  # noqa: F401 - 复用应用夹具
)

# ---------------------------------------------------------------------------
# 测试材料构造
# ---------------------------------------------------------------------------

_CONV = "conv-1"
_ACC = "acc-1"


def _record(
    message_id: str,
    role: ChatMessageRole,
    content: str,
    *,
    status: ChatMessageStatus = ChatMessageStatus.DONE,
    image: dict[str, Any] | None = None,
    minutes: int = 0,
) -> MessageRecord:
    created = datetime.now(UTC) + timedelta(minutes=minutes)
    return MessageRecord(
        message_id=message_id,
        conversation_id=_CONV,
        account_id=_ACC,
        role=role,
        attempt_number=1,
        status=status,
        content=content,
        thinking=None,
        error_code=None,
        error_message=None,
        duration_ms=None,
        model_id=None,
        run_lock_id=None,
        created_at=created,
        updated_at=created,
        image=image,
    )


def _exchange(index: int, content: str, reply: str) -> list[MessageRecord]:
    return [
        _record(f"u{index}", ChatMessageRole.USER, content, minutes=index * 2),
        _record(f"a{index}", ChatMessageRole.ASSISTANT, reply, minutes=index * 2 + 1),
    ]


def _user_contents(messages: list[dict[str, str]]) -> list[str]:
    return [m["content"] for m in messages if m["role"] == "user"]


def _system_blocks(compiled: Any) -> list[str]:
    return [m["content"] for m in compiled.messages if m["role"] == "system"]


def _summary_block(compiled: Any) -> str:
    return next(
        block
        for block in _system_blocks(compiled)
        if "较早对话的摘要" in block
    )


def _recovered_block(compiled: Any) -> str:
    return next(
        block
        for block in _system_blocks(compiled)
        if "找回的相关原文" in block
    )


# ---------------------------------------------------------------------------
# 验收 1：窗口内编译 + 原始消息完整保存
# ---------------------------------------------------------------------------


def test_compile_includes_request_recent_verbatim_and_older_summary() -> None:
    """验收 1：当前请求与近期消息保留原文，较早片段进带范围的摘要。"""
    records: list[MessageRecord] = []
    for index in range(1, 6):
        records.extend(
            _exchange(
                index,
                f"第{index}个问题正文：" + "问" * 300,
                f"第{index}个回答正文：" + "答" * 300,
            )
        )
    records.append(_record("u6", ChatMessageRole.USER, "第六个问题当前请求", minutes=11))

    compiled = compile_turn_context(
        messages=records,
        current_user_message_id="u6",
        model_id=CHAT_MODEL_ID,
        mode=ChatMode.COMPANION,
        context_window=5000,
    )

    # 首条为模式合同（系统规则），当前请求逐字保留在最后。
    assert compiled.messages[0]["role"] == "system"
    assert compiled.messages[-1] == {"role": "user", "content": "第六个问题当前请求"}
    all_content = "\n".join(m["content"] for m in compiled.messages)
    # 近期原文逐字保留（含最新完成的助手回答）。
    assert f"第5个回答正文：{'答' * 300}" in all_content
    # 较早片段只以带 ID 与范围的摘要出现，不整体携带原文。
    summary = _summary_block(compiled)
    assert SUMMARY_VERSION in summary
    assert "[u1]" in summary
    assert compiled.summary_source_range == ("u1", "u3")
    assert f"第1个问题正文：{'问' * 300}" not in all_content
    assert "…" in summary
    # 「采用原文」只含近期原文消息；摘要-only 的较早消息不计入。
    assert "u5" in compiled.adopted_message_ids
    assert "a5" in compiled.adopted_message_ids
    assert "u6" in compiled.adopted_message_ids
    assert "u1" not in compiled.adopted_message_ids
    # 编译在窗口预算内完成。
    assert compiled.input_token_estimate <= compiled.input_budget_tokens
    # 编译是纯函数：原始消息记录原样保留（原始消息完整保存）。
    assert records[0].content == f"第1个问题正文：{'问' * 300}"


def test_older_summary_shrinks_under_small_window() -> None:
    """验收 1：小窗口下较早片段整体进摘要，近期原文仍保留。"""
    records: list[MessageRecord] = []
    for index in range(1, 7):
        records.extend(_exchange(index, f"问题{index}：" + "长" * 200, "答" * 200))
    records.append(_record("u7", ChatMessageRole.USER, "当前请求", minutes=14))

    compiled = compile_turn_context(
        messages=records,
        current_user_message_id="u7",
        model_id=CHAT_MODEL_ID,
        mode=ChatMode.COMPANION,
        context_window=4000,
    )

    verbatim_users = _user_contents(compiled.messages)
    assert verbatim_users[-1] == "当前请求"
    # 小窗口下多数较早原文被摘要覆盖，近期原文只保留尾部几条。
    assert len(verbatim_users) < 7
    assert compiled.summary_source_range is not None
    assert compiled.summary_source_range[0] == "u1"
    assert compiled.input_token_estimate <= compiled.input_budget_tokens


# ---------------------------------------------------------------------------
# 验收 2：动态预算预留与编译记录
# ---------------------------------------------------------------------------


def test_dynamic_budget_reserves_and_compilation_record() -> None:
    """验收 2：输出/工具/图片/系统规则预留；编译记录含模型与版本字段。"""
    records = [
        *_exchange(1, "带图之前的问题", "回答"),
        _record(
            "u2",
            ChatMessageRole.USER,
            "当前带图请求",
            image={"kind": "generate"},
            minutes=5,
        ),
    ]

    compiled = compile_turn_context(
        messages=records,
        current_user_message_id="u2",
        model_id=CHAT_MODEL_ID,
        mode=ChatMode.COMPANION,
    )

    expected_budget = (
        FACTORY_MAIN_MODEL_CONTEXT_WINDOW
        - OUTPUT_RESERVE_TOKENS
        - OUTPUT_RESERVE_MARGIN_TOKENS
    )
    expected_budget -= TOOL_RESERVE_TOKENS
    assert compiled.context_window == FACTORY_MAIN_MODEL_CONTEXT_WINDOW
    assert compiled.input_budget_tokens == expected_budget
    assert compiled.reserves.output_tokens == (
        OUTPUT_RESERVE_TOKENS + OUTPUT_RESERVE_MARGIN_TOKENS
    )
    assert compiled.reserves.tool_tokens == TOOL_RESERVE_TOKENS
    assert compiled.reserves.image_tokens == IMAGE_COST_TOKENS
    assert compiled.reserves.system_tokens > 0
    assert compiled.input_token_estimate <= compiled.input_budget_tokens
    # 编译记录字段：模型 ID、预算版本、摘要版本、估算版本、采用的原文 ID。
    record = compiled.to_record()
    assert record["model_id"] == CHAT_MODEL_ID
    assert record["budget_version"] == CONTEXT_BUDGET_VERSION
    assert record["summary_version"] == SUMMARY_VERSION
    assert record["token_estimate_version"] == "token-estimate-v1"
    assert record["adopted_message_ids"] == ["u1", "a1", "u2"]
    # 已验证窗口登记表：出厂主模型回退到出厂快照验证值；未知模型回退
    # 保守缺省窗口。
    assert verified_context_window(CHAT_MODEL_ID) == FACTORY_MAIN_MODEL_CONTEXT_WINDOW
    assert verified_context_window("unknown-model") == DEFAULT_CONTEXT_WINDOW


def test_image_cost_absent_without_image_payload() -> None:
    """验收 2：无图片载荷的普通文本轮不计图片成本。"""
    records = [_record("u1", ChatMessageRole.USER, "纯文本请求", minutes=1)]
    compiled = compile_turn_context(
        messages=records,
        current_user_message_id="u1",
        model_id=CHAT_MODEL_ID,
        mode=ChatMode.COMPANION,
    )
    assert compiled.reserves.image_tokens == 0


# ---------------------------------------------------------------------------
# 验收 3：摘要来源范围 + 早期实体/约定补回原文
# ---------------------------------------------------------------------------


def test_recovery_of_earlier_agreement_with_source_ids() -> None:
    """验收 3：引用早先约定时补回原文，记录本轮使用的消息 ID。

    用小窗口让早先消息进入摘要区（大窗口下全部近期原文已在上下文内，
    无需补回），验证按需补回语义。
    """
    records = [
        *_exchange(1, "我们约定周五下午三点在图书馆开组会", "好的，已记下这个安排。"),
        *_exchange(2, "今天天气不错", "是呀，适合出门走走。"),
        _record(
            "u3", ChatMessageRole.USER, "之前说好的「开组会」是周几来着？", minutes=6
        ),
    ]

    compiled = compile_turn_context(
        messages=records,
        current_user_message_id="u3",
        model_id=CHAT_MODEL_ID,
        mode=ChatMode.COMPANION,
        context_window=2000,
    )

    # 补回块携带完整原文与来源消息 ID；本轮使用记录包含该消息。
    recovered = _recovered_block(compiled)
    assert "[u1]" in recovered
    assert "我们约定周五下午三点在图书馆开组会" in recovered
    assert compiled.recovered_message_ids == ["u1"]
    assert "u1" in compiled.adopted_message_ids
    assert compiled.unresolved_reference is False
    # 摘要仍覆盖较早片段，范围完整。
    assert compiled.summary_source_range == ("u1", "a2")


def test_recovery_prefers_quoted_span() -> None:
    """验收 3：引号原文优先作为检索线索（含中文弯引号；小窗口进摘要区）。"""
    records = [
        *_exchange(1, "Transformer 的注意力机制是核心", "好的，这个话题我记下了。"),
        _record("u2", ChatMessageRole.USER, "再讲讲“注意力机制”？", minutes=4),
    ]
    compiled = compile_turn_context(
        messages=records,
        current_user_message_id="u2",
        model_id=CHAT_MODEL_ID,
        mode=ChatMode.COMPANION,
        context_window=2000,
    )
    assert compiled.recovered_message_ids == ["u1"]
    assert compiled.unresolved_reference is False


# ---------------------------------------------------------------------------
# 验收 4：预算裁剪顺序 + 承认不确定
# ---------------------------------------------------------------------------


def test_trim_keeps_request_and_recovered_evidence_at_floor() -> None:
    """验收 4：预算触底时当前请求与补回的关键证据不丢。"""
    records: list[MessageRecord] = []
    for index in range(1, 6):
        records.extend(_exchange(index, f"闲聊{index}：" + "聊" * 120, "嗯" * 120))
    records.append(
        _record("e1", ChatMessageRole.USER, "关键约定：周五下午三点交作业", minutes=11)
    )
    records.append(
        _record("u6", ChatMessageRole.USER, "之前说好的「交作业」时间？", minutes=12)
    )

    compiled = compile_turn_context(
        messages=records,
        current_user_message_id="u6",
        model_id=CHAT_MODEL_ID,
        mode=ChatMode.COMPANION,
        context_window=800,
    )

    all_content = "\n".join(m["content"] for m in compiled.messages)
    # 当前请求逐字保留；补回的关键证据保留完整原文。
    assert "之前说好的「交作业」时间？" in all_content
    assert "关键约定：周五下午三点交作业" in all_content
    assert compiled.recovered_message_ids == ["e1"]
    # 近期原文已被收缩（闲聊原文多数降级为摘要），摘要已加重压缩。
    assert len(_user_contents(compiled.messages)) < 6
    assert "…" in _summary_block(compiled)
    # 窗口极小：触底如实记录，而不是丢当前请求或证据。
    assert compiled.budget_floor_exceeded is True
    assert compiled.input_token_estimate > compiled.input_budget_tokens


def test_unresolved_reference_admits_uncertainty() -> None:
    """验收 4：找不到旧原文时附「承认不确定并询问」规则，不编造。"""
    records = [
        *_exchange(1, "我喜欢蓝色的笔记本", "收到。"),
        _record("u2", ChatMessageRole.USER, "之前说好的约定具体是什么？", minutes=4),
    ]

    compiled = compile_turn_context(
        messages=records,
        current_user_message_id="u2",
        model_id=CHAT_MODEL_ID,
        mode=ChatMode.COMPANION,
        context_window=2000,
    )

    assert compiled.unresolved_reference is True
    assert compiled.recovered_message_ids == []
    uncertainty_blocks = [
        block
        for block in _system_blocks(compiled)
        if "未能在会话历史中找到" in block
    ]
    assert len(uncertainty_blocks) == 1
    assert "询问" in uncertainty_blocks[0]


def test_no_reference_flags_without_backward_markers() -> None:
    """验收 4：普通新问题不触发补回，也不误报不确定。"""
    records = [
        *_exchange(1, "我喜欢蓝色的笔记本", "收到。"),
        _record("u2", ChatMessageRole.USER, "帮我看看今天有什么安排", minutes=4),
    ]
    compiled = compile_turn_context(
        messages=records,
        current_user_message_id="u2",
        model_id=CHAT_MODEL_ID,
        mode=ChatMode.COMPANION,
    )
    assert compiled.unresolved_reference is False
    assert compiled.recovered_message_ids == []


# ---------------------------------------------------------------------------
# 验收 5：隐私边界 + 模型切换重算预算
# ---------------------------------------------------------------------------


def test_audit_record_leaks_no_private_content() -> None:
    """验收 5：编译审计记录只含 ID/版本/计数，不含任何会话正文。"""
    secret = "私人材料：我的银行账号是 6222 开头的长串数字"
    records = [
        *_exchange(1, secret, "好的"),
        _record("u2", ChatMessageRole.USER, "再看看之前的安排", minutes=4),
    ]
    compiled = compile_turn_context(
        messages=records,
        current_user_message_id="u2",
        model_id=CHAT_MODEL_ID,
        mode=ChatMode.COMPANION,
    )
    dumped = json.dumps(compiled.to_record(), ensure_ascii=False)
    assert "6222" not in dumped
    assert "银行账号" not in dumped


def test_model_switch_recomputes_budget_next_turn() -> None:
    """验收 5：同一会话换模型/窗口后，下一轮按新窗口重算预算。"""
    records: list[MessageRecord] = []
    for index in range(1, 5):
        records.extend(_exchange(index, f"历史问题{index}：" + "史" * 160, "答" * 160))
    records.append(_record("u5", ChatMessageRole.USER, "当前请求", minutes=10))

    small = compile_turn_context(
        messages=records,
        current_user_message_id="u5",
        model_id="small-context-model",
        mode=ChatMode.COMPANION,
        context_window=2500,
    )
    large = compile_turn_context(
        messages=records,
        current_user_message_id="u5",
        model_id=CHAT_MODEL_ID,
        mode=ChatMode.COMPANION,
    )

    assert small.context_window == 2500
    assert large.context_window == FACTORY_MAIN_MODEL_CONTEXT_WINDOW
    assert small.input_budget_tokens < large.input_budget_tokens
    # 小窗口触发较早原文摘要化；大窗口下近期原文保留更多。
    assert len(_user_contents(small.messages)) <= len(_user_contents(large.messages))
    assert small.summary_source_range is not None


def test_cross_account_history_not_recallable(
    sqlite_app: Any, client: TestClient
) -> None:
    """验收 5：跨账户历史不可召回（仓库按账户隔离，编译只见本账户消息）。"""
    account_a = _register(client, tag="91")["id"]
    conv_a = _create_conversation(client)
    account_b = _register(client, tag="92")["id"]
    conv_b = _create_conversation(client)

    repo = sqlite_app.state.chat_service._repo  # noqa: SLF001 - 测试验证隔离边界
    # 会话与消息对其他账户不可见（跨账户 ID 表现为不存在）。
    assert repo.list_messages(account_b, conv_a) == []
    assert repo.get_conversation(account_b, conv_a) is None
    # 各账户只能看到自己作用域内的消息。
    messages_a = repo.list_messages(account_a, conv_a)
    assert all(record.account_id == account_a for record in messages_a)
    # 账户 B 轮次的编译材料只能来自 B 作用域内的消息，A 的材料无从进入。
    messages_b = repo.list_messages(account_b, conv_b)
    assert all(record.account_id == account_b for record in messages_b)


# ---------------------------------------------------------------------------
# 集成：日常父图整链 —— 编译产物进入模型载荷并落审计
# ---------------------------------------------------------------------------


class _CapturingAdapter:
    """捕获模型载荷的流式适配器（断言编译产物真实进入模型输入）。"""

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def stream_call(
        self, capability: Any, run_context: Any, payload: dict[str, Any]
    ):
        self.payloads.append(payload)
        yield StreamChunk(kind="delta", delta="已收到")


def test_graph_turn_delivers_compiled_context_and_audit(
    sqlite_app: Any,
    client: TestClient,
    generation_helpers: dict[str, Any],
) -> None:
    """集成：父图 compile_context 产物流入模型载荷；审计记录编译元数据。"""
    _register(client)
    adapter = _CapturingAdapter()
    sqlite_app.state.chat_service._gateway = _gateway_with(adapter)  # noqa: SLF001
    conversation_id = _create_conversation(client)
    generation_helpers["send"](
        client, conversation_id, content="第一轮：我们约定周五下午三点开组会"
    )
    generation_helpers["drive"](sqlite_app)
    generation_helpers["send"](
        client, conversation_id, content="第二轮：之前说好的「开组会」是几点？"
    )
    generation_helpers["drive"](sqlite_app)
    projection = client.get(f"/chat/conversations/{conversation_id}").json()
    assistants = [m for m in projection["messages"] if m["role"] == "assistant"]
    assert [m["status"] for m in assistants] == ["done", "done"]

    assert len(adapter.payloads) == 2
    second_messages = adapter.payloads[1]["messages"]
    # 首条为模式合同；第二轮载荷包含第一轮原文（近期原文或补回原文）。
    assert second_messages[0]["role"] == "system"
    second_content = "\n".join(m["content"] for m in second_messages)
    assert "我们约定周五下午三点开组会" in second_content
    # 当前请求是最后一条消息。
    assert second_messages[-1]["content"] == "第二轮：之前说好的「开组会」是几点？"

    # 编译审计：每轮一条 CONTEXT_COMPILED，记录模型 ID/版本/采用消息 ID。
    observability = sqlite_app.state.observability_service
    events = observability.list_audit_events(action=AuditAction.CONTEXT_COMPILED)
    assert len(events) == 2
    record = events[-1].details
    assert record["model_id"] == CHAT_MODEL_ID
    assert record["budget_version"] == CONTEXT_BUDGET_VERSION
    assert record["summary_version"] == SUMMARY_VERSION
    assert record["adopted_message_ids"]
    # 运行配置快照（V2 Issue 09）提供已验证窗口与最大输入额度，编译取
    # 两者较小值作为输入上界。
    assert record["context_window"] == min(
        FACTORY_MAIN_MODEL_CONTEXT_WINDOW, FACTORY_MAIN_MODEL_MAX_INPUT_TOKENS
    )


# ---------------------------------------------------------------------------
# token 估算
# ---------------------------------------------------------------------------


def test_estimate_tokens_is_deterministic_and_conservative() -> None:
    """CJK 计 1 token/字，拉丁按 4 字符 1 token；同输入结果稳定。"""
    text = "你好世界abc"
    assert estimate_tokens(text) == estimate_tokens(text)
    assert estimate_tokens(text) == 4 + 1  # 4 个汉字 + 3 个拉丁字符（ceil(3/4)）
    assert estimate_tokens("") == 0
    # 全角标点按 1 token 计（保守）。
    assert estimate_tokens("。！") == 2
