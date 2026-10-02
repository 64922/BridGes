"""改进工单 21：按任务、边界与前文适配有分寸表达。

覆盖验收标准（``.scratch/2/issues/21-context-sensitive-companion-expression.md``）：

1. 5 公里直接给 5000 米；倾诉不强制建议；概念焦虑/翻译引语不推断用户情绪；
2. 混合意图完成排查/建议主请求，用户感谢已解决时不机械追问；
3. 跨轮继续和纠正接回遗漏任务，详细推导/两千字任务不会被默认短答规则抵消；
4. 真实工具超时/部分/成功信号决定准确说明，失败不伪装成功；
5. 新任务用新策略，正常重试保留策略快照；异常降级可完成任务；
6. 无人味专属第二次生成或分类调用，规则/夹具原创；事实保护与预算验证通过。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bridges.ai import ModelGateway
from bridges.ai.adapters import StreamChunk
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.chat.global_writing_policy import (
    SAFE_BASELINE_POLICY_VERSION,
    GlobalWritingPolicyCompiler,
)
from bridges.chat.lightweight_policy import (
    CONSTRAINT_RULES,
    DEFAULT_OUTPUT_TOKENS,
    EXTENDED_OUTPUT_TOKENS,
    GLOBAL_CHAT_LIGHTWEIGHT_VERSION,
    ChatResponseForm,
    ToolOutcome,
    continuation_source_text,
    detect_response_form,
    detect_turn_constraints,
    output_tokens_for_request,
)
from bridges.chat.repository import ConversationRepository
from bridges.chat.service import ChatService
from bridges.contracts.ai import CapabilityKind, CapabilityRecord
from bridges.contracts.chat import ChatMessageRole, ChatMode
from bridges.contracts.projects import ObjectDomain
from bridges.contracts.workflows import RunContextEnvelope
from bridges.storage.database import BridgesDatabase
from tests.chat.test_issue02_durable_generation import (  # noqa: F401 - 复用应用夹具
    client as client,
)
from tests.chat.test_issue02_durable_generation import (
    sqlite_app as sqlite_app,
)

# ---------------------------------------------------------------------------
# 形态路由：任务、边界与引语（验收 1、2、3）
# ---------------------------------------------------------------------------


def test_short_fact_question_stays_direct() -> None:
    assert (
        detect_response_form("5 公里是多少米？", ChatMode.COMPANION)
        == ChatResponseForm.SHORT_ANSWER
    )


def test_concept_anxiety_is_explanation_not_user_state() -> None:
    assert (
        detect_response_form("解释焦虑的生理机制", ChatMode.COMPANION)
        == ChatResponseForm.EXPLANATION
    )


def test_quoted_emotion_translation_is_direct_task() -> None:
    """待翻译引语中的情绪不是用户当前状态。"""
    assert (
        detect_response_form("他说‘我很焦虑’，帮我翻译成英文", ChatMode.COMPANION)
        == ChatResponseForm.DIRECT_TASK
    )


def test_quoted_emotion_in_material_is_not_user_state() -> None:
    assert (
        detect_response_form("这段话里的‘焦虑’是什么意思", ChatMode.COMPANION)
        == ChatResponseForm.EXPLANATION
    )


def test_advice_request_with_emotion_completes_main_request() -> None:
    """焦虑又请求排查：主请求是建议/排查，不是单向情绪承接。"""
    assert (
        detect_response_form("我很焦虑，怎么办", ChatMode.COMPANION)
        == ChatResponseForm.ADVICE
    )


def test_no_advice_boundary_suppresses_advice_route() -> None:
    text = "我今天很难过，不想听建议，只想聊聊"
    assert detect_response_form(text, ChatMode.COMPANION) == ChatResponseForm.EMPATHY
    assert "no_advice" in detect_turn_constraints(text)


def test_no_comfort_boundary_keeps_advice_task() -> None:
    text = "不要安慰我，直接告诉我怎么办"
    assert detect_response_form(text, ChatMode.COMPANION) == ChatResponseForm.ADVICE
    assert detect_turn_constraints(text) == ("no_comfort",)


def test_answer_only_boundary_shortens_answer() -> None:
    text = "只给答案，别解释"
    assert detect_response_form(text, ChatMode.COMPANION) == ChatResponseForm.SHORT_ANSWER
    assert "answer_only" in detect_turn_constraints(text)


@pytest.mark.parametrize(
    "text",
    ["请详细推导一下这个公式", "写一篇两千字的故事", "请完整展开讲讲这个实验"],
)
def test_explicit_detail_requests_are_direct_tasks(text: str) -> None:
    assert detect_response_form(text, ChatMode.COMPANION) == ChatResponseForm.DIRECT_TASK
    assert "detail_requested" in detect_turn_constraints(text)


@pytest.mark.parametrize(
    "text",
    ["不用详细讲，简单说一下", "别讲得太详细，给个大概", "不要展开，一句话带过"],
)
def test_negated_detail_is_not_a_long_request(text: str) -> None:
    """否定句式里的“详细/展开”不触发长文形态与扩展额度。"""
    constraints = detect_turn_constraints(text)
    assert "detail_requested" not in constraints
    assert detect_response_form(text, ChatMode.COMPANION) != ChatResponseForm.DIRECT_TASK
    assert output_tokens_for_request(text) == DEFAULT_OUTPUT_TOKENS


def test_positive_detail_after_unrelated_negation_still_counts() -> None:
    assert "detail_requested" in detect_turn_constraints("不用担心，详细说说原理")


def test_quoted_boundaries_are_not_turn_constraints() -> None:
    """引语中的“别追问/不想听建议”不是本轮用户限制。"""
    assert "no_follow_up" not in detect_turn_constraints(
        "他说‘别追问我了’，这句话是什么意思"
    )
    assert detect_response_form(
        "他说‘别追问我了’，这句话是什么意思", ChatMode.COMPANION
    ) == ChatResponseForm.EXPLANATION
    assert "no_advice" not in detect_turn_constraints(
        "他写道‘我不想听建议’，帮我翻译成英文"
    )
    assert detect_response_form(
        "他写道‘我不想听建议’，帮我翻译成英文", ChatMode.COMPANION
    ) == ChatResponseForm.DIRECT_TASK


def test_short_translation_keeps_default_budget() -> None:
    assert output_tokens_for_request("帮我翻译这句话") == DEFAULT_OUTPUT_TOKENS


def test_no_follow_up_boundary_is_recorded() -> None:
    assert "no_follow_up" in detect_turn_constraints("别追问我了")
    # 用户明确不要追问时不进入澄清追问形态。
    assert (
        detect_response_form("帮我看看，别追问我了", ChatMode.COMPANION)
        != ChatResponseForm.CLARIFICATION
    )


def test_thanks_after_resolution_is_natural_closing() -> None:
    text = "谢谢你，刚才的方法帮我解决了"
    assert detect_response_form(text, ChatMode.COMPANION) == ChatResponseForm.COMPACT_DEFAULT
    assert "closing" in detect_turn_constraints(text)


def test_continuation_adopts_prior_task_form() -> None:
    assert (
        detect_response_form(
            "继续", ChatMode.COMPANION, continuation_text="请详细推导这个公式"
        )
        == ChatResponseForm.DIRECT_TASK
    )
    constraints = detect_turn_constraints(
        "继续", continuation_text="请详细推导这个公式"
    )
    assert "detail_requested" in constraints


def test_missed_part_adopts_real_request() -> None:
    assert (
        detect_response_form(
            "你漏答了，继续", ChatMode.COMPANION, continuation_text="第二个问题是什么"
        )
        == ChatResponseForm.EXPLANATION
    )
    constraints = detect_turn_constraints("你漏答了，继续")
    assert "missed_part" in constraints


def test_tool_outcomes_override_text_form() -> None:
    assert (
        detect_response_form("你好", ChatMode.COMPANION, tool_outcome=ToolOutcome.ERROR)
        == ChatResponseForm.ERROR_REFUSAL
    )
    assert (
        detect_response_form("你好", ChatMode.COMPANION, tool_outcome=ToolOutcome.PARTIAL)
        == ChatResponseForm.TOOL_RESULT
    )
    assert (
        detect_response_form("你好", ChatMode.COMPANION, tool_outcome=ToolOutcome.SUCCESS)
        == ChatResponseForm.TOOL_RESULT
    )


def test_continuation_source_skips_pure_continuations() -> None:
    messages = [
        SimpleNamespace(
            message_id="u1", role=ChatMessageRole.USER, content="写一篇两千字的故事"
        ),
        SimpleNamespace(message_id="a1", role=ChatMessageRole.ASSISTANT, content="好的"),
        SimpleNamespace(message_id="u2", role=ChatMessageRole.USER, content="继续"),
        SimpleNamespace(message_id="a2", role=ChatMessageRole.ASSISTANT, content="继续写"),
        SimpleNamespace(message_id="u3", role=ChatMessageRole.USER, content="继续"),
    ]
    assert continuation_source_text(messages, "u3") == "写一篇两千字的故事"


# ---------------------------------------------------------------------------
# 策略编译：显式边界、规则与固定收尾（验收 1、2、3、5）
# ---------------------------------------------------------------------------


def test_no_advice_snapshot_compiles_boundary_rules() -> None:
    snapshot = GlobalWritingPolicyCompiler().compile(
        ChatMode.COMPANION, user_text="我今天很难过，不想听建议，只想聊聊"
    )

    assert snapshot.version == GLOBAL_CHAT_LIGHTWEIGHT_VERSION
    assert snapshot.form == ChatResponseForm.EMPATHY
    assert snapshot.constraints == ("no_advice",)
    assert "no-unsolicited-advice" in snapshot.rule_ids
    assert "先给出明确取舍" not in snapshot.system_block
    assert "不提供计划、步骤或行动建议" in snapshot.system_block


def test_empathy_rules_drop_forced_action() -> None:
    snapshot = GlobalWritingPolicyCompiler().compile(
        ChatMode.COMPANION, user_text="今天好难过"
    )

    assert snapshot.form == ChatResponseForm.EMPATHY
    assert "empathy-with-action" not in snapshot.rule_ids
    assert "stay-without-forcing" in snapshot.rule_ids
    assert "honest-disagreement" in snapshot.rule_ids
    block = snapshot.system_block
    assert "必须带来具体判断" not in block
    assert "聊聊还是建议" not in block
    assert "不编造共同经历、亲密关系或自己的经历" in block


def test_explanation_rules_drop_forced_confirmation() -> None:
    snapshot = GlobalWritingPolicyCompiler().compile(
        ChatMode.COMPANION, user_text="什么是黑洞？讲讲原理"
    )

    assert snapshot.form == ChatResponseForm.EXPLANATION
    assert "check-understanding" not in snapshot.rule_ids
    assert "no-forced-check" in snapshot.rule_ids
    assert "结尾用一句话确认是否解决" not in snapshot.system_block


def test_tool_result_states_real_status() -> None:
    snapshot = GlobalWritingPolicyCompiler().compile(
        ChatMode.COMPANION,
        user_text="查一下这个结果",
        tool_outcome=ToolOutcome.PARTIAL,
    )

    assert snapshot.form == ChatResponseForm.TOOL_RESULT
    assert "status-accurate" in snapshot.rule_ids
    assert "partial_results" in snapshot.constraints
    assert "partial-results-stated" in snapshot.rule_ids
    assert "不得说成完整成功" in snapshot.system_block


def test_tool_failure_requires_truthful_status() -> None:
    snapshot = GlobalWritingPolicyCompiler().compile(
        ChatMode.COMPANION,
        user_text="查一下这个结果",
        tool_outcome=ToolOutcome.ERROR,
    )

    assert snapshot.form == ChatResponseForm.ERROR_REFUSAL
    assert "failure-not-feigned" in snapshot.rule_ids
    assert "不把未核实内容说成工具已成功" in snapshot.system_block


def test_refusal_shapes_use_error_refusal_rules() -> None:
    snapshot = GlobalWritingPolicyCompiler().compile(
        ChatMode.COMPANION, user_text="查一下", refusal=True
    )

    assert snapshot.form == ChatResponseForm.ERROR_REFUSAL
    assert "reason-direct" in snapshot.rule_ids


def test_closing_snapshot_does_not_reopen_task() -> None:
    snapshot = GlobalWritingPolicyCompiler().compile(
        ChatMode.COMPANION, user_text="谢谢你，刚才的方法帮我解决了"
    )

    assert snapshot.form == ChatResponseForm.COMPACT_DEFAULT
    assert snapshot.constraints == ("closing",)
    assert "natural-closing" in snapshot.rule_ids
    assert "不重启话题、不追问新需求" in snapshot.system_block


def test_direct_task_rules_follow_real_task_length() -> None:
    snapshot = GlobalWritingPolicyCompiler().compile(
        ChatMode.COMPANION, user_text="写一篇两千字的故事"
    )

    assert snapshot.form == ChatResponseForm.DIRECT_TASK
    assert "length-follows-request" in snapshot.rule_ids
    assert "不因默认简短而压缩必要内容" in snapshot.system_block
    assert "不追加无关建议、情绪承接或固定收尾" in snapshot.system_block


def test_constraint_cap_matches_prompt_and_metadata() -> None:
    """提示词渲染与审计快照只保留同一批前三条约束，形态/额度用全部信号。"""
    snapshot = GlobalWritingPolicyCompiler().compile(
        ChatMode.COMPANION,
        user_text="不要安慰我，只给答案，别追问，请详细推导这个公式",
    )

    constraint_rule_ids = {rule_id for rule_id, _ in CONSTRAINT_RULES.values()}
    rendered = [
        rule_id for rule_id in snapshot.rule_ids if rule_id in constraint_rule_ids
    ]
    assert len(snapshot.constraints) == 3
    assert rendered == [CONSTRAINT_RULES[name][0] for name in snapshot.constraints]
    assert snapshot.form == ChatResponseForm.DIRECT_TASK
    assert snapshot.output_tokens == EXTENDED_OUTPUT_TOKENS


def test_policy_priority_puts_task_and_boundaries_first() -> None:
    snapshot = GlobalWritingPolicyCompiler().compile(
        ChatMode.COMPANION, user_text="只给答案，别解释"
    )

    block = snapshot.system_block
    assert block.index("用户本轮明确要求") < block.index("默认表达规则")
    assert "优先级低于用户本轮明确表达的语气与篇幅要求" in block
    assert "事实准确性、权限边界" in block


def test_existing_snapshot_reuse_keeps_constraints_and_budget() -> None:
    compiler = GlobalWritingPolicyCompiler()
    first = compiler.compile(ChatMode.COMPANION, user_text="写一篇两千字的故事")
    reused = compiler.compile(
        ChatMode.COMPANION,
        user_text="换一句不同的话",
        existing_snapshot=first,
    )

    assert reused.version == first.version
    assert reused.form == first.form
    assert reused.constraints == first.constraints
    assert reused.output_tokens == first.output_tokens == EXTENDED_OUTPUT_TOKENS


def test_metadata_records_constraints_without_body_text() -> None:
    snapshot = GlobalWritingPolicyCompiler().compile(
        ChatMode.COMPANION, user_text="不想听建议，只想聊聊今天的事"
    )

    metadata = snapshot.metadata()
    assert metadata["constraints"] == ["no_advice"]
    assert metadata["output_tokens"] == DEFAULT_OUTPUT_TOKENS
    assert "不想听建议" not in str(metadata)


def test_safe_baseline_honors_explicit_boundaries() -> None:
    snapshot = GlobalWritingPolicyCompiler(resource=None).compile(
        ChatMode.COMPANION, user_text="不想听建议"
    )

    assert snapshot.version == SAFE_BASELINE_POLICY_VERSION
    block = snapshot.system_block
    assert "用户本轮明确限制" in block
    assert "不追加建议、安慰或追问" in block
    assert "必须带来具体判断" not in block


# ---------------------------------------------------------------------------
# 输出额度：显式长文用有界任务上限（验收 3、04 预算）
# ---------------------------------------------------------------------------


def test_default_output_budget_is_unchanged() -> None:
    assert output_tokens_for_request("5 公里是多少米？") == DEFAULT_OUTPUT_TOKENS
    snapshot = GlobalWritingPolicyCompiler().compile(
        ChatMode.COMPANION, user_text="5 公里是多少米？"
    )
    assert snapshot.output_tokens == DEFAULT_OUTPUT_TOKENS


def test_explicit_detail_uses_extended_task_ceiling() -> None:
    assert (
        output_tokens_for_request("写一篇两千字的故事") == EXTENDED_OUTPUT_TOKENS
    )
    assert (
        output_tokens_for_request("继续", continuation_text="请详细推导这个公式")
        == EXTENDED_OUTPUT_TOKENS
    )
    snapshot = GlobalWritingPolicyCompiler().compile(
        ChatMode.COMPANION, user_text="写一篇两千字的故事"
    )
    assert snapshot.output_tokens == EXTENDED_OUTPUT_TOKENS


# ---------------------------------------------------------------------------
# 正式生成链：一次调用、续接与工具状态（验收 3、4、6）
# ---------------------------------------------------------------------------


class _CountingStreamAdapter:
    """可编程流式适配器：记录 payload，统计调用次数。"""

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []
        self.stream_calls = 0

    def stream_call(
        self,
        capability: Any,
        run_context: Any,
        payload: dict[str, Any],
    ):
        self.stream_calls += 1
        self.payloads.append(payload)
        yield StreamChunk(kind="delta", delta="普通回答")
        yield StreamChunk(kind="done")


def _context() -> RunContextEnvelope:
    return RunContextEnvelope(
        run_id="run-21",
        account_id="alice",
        project_id="conversation-1",
        workflow_name="chat",
        workflow_version="1",
        object_domain=ObjectDomain.PERSONAL_VAULT,
        submitted_at=datetime.now(UTC),
    )


def _allow_web_search(service: ChatService, account_id: str, message_id: str) -> None:
    """固定助手消息的路由快照为允许公网搜索。

    V2 直连服务路径下 ``start_generation`` 的默认路由快照把
    ``web_search_allowed`` 固定为 False；生产由决策层在检索前写入。
    本测试只关心搜索失败后的表达策略接线，因此直接按决策层的语义
    固定该字段，不依赖决策层自身的实现。
    """

    record = service._repo.get_message(account_id, message_id)  # noqa: SLF001
    assert record is not None
    route = dict(record.route or {})
    route["web_search_allowed"] = True
    with service._repo.database.transaction():  # noqa: SLF001
        service._repo._persist_message_route(  # noqa: SLF001
            replace(record, route=route)
        )


def _counting_service(tmp_path: Path) -> tuple[ChatService, _CountingStreamAdapter]:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    repository = ConversationRepository(database)
    registry = CapabilityRegistry()
    registry.register(
        CapabilityRecord(
            name="qwen_text_chat",
            version="1",
            kind=CapabilityKind.MODEL,
            vendor="qwen",
            region="cn-beijing",
            model_id="qwen3.7-plus-2026-05-26",
            input_schema_version="chat-messages-v1",
            output_schema_version="chat-completion-v1",
        )
    )
    adapter = _CountingStreamAdapter()
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", adapter)
    return ChatService(repository=repository, gateway=gateway), adapter


def test_long_request_uses_extended_payload_budget_in_generation(
    tmp_path: Path,
) -> None:
    service, adapter = _counting_service(tmp_path)
    created = service.create_conversation("alice")
    user, assistant, _ = service.start_generation(
        "alice", created.conversation_id, "写一篇两千字的故事"
    )
    list(
        service.stream_generation(
            "alice",
            created.conversation_id,
            assistant.message_id,
            _context(),
            until_user_message_id=user.message_id,
        )
    )

    assert adapter.stream_calls == 1
    payload = adapter.payloads[0]
    assert payload["max_tokens"] == EXTENDED_OUTPUT_TOKENS
    metadata = payload["global_writing_policy"]
    assert metadata["version"] == GLOBAL_CHAT_LIGHTWEIGHT_VERSION
    assert metadata["form"] == ChatResponseForm.DIRECT_TASK.value
    assert metadata["output_tokens"] == EXTENDED_OUTPUT_TOKENS
    assert "detail_requested" in metadata["constraints"]


def test_continuation_uses_previous_request_without_copying_history(
    tmp_path: Path,
) -> None:
    service, adapter = _counting_service(tmp_path)
    created = service.create_conversation("alice")
    first_user, first, _ = service.start_generation(
        "alice", created.conversation_id, "写一篇两千字的故事"
    )
    list(
        service.stream_generation(
            "alice",
            created.conversation_id,
            first.message_id,
            _context(),
            until_user_message_id=first_user.message_id,
        )
    )
    second_user, second, _ = service.start_generation(
        "alice", created.conversation_id, "继续"
    )
    list(
        service.stream_generation(
            "alice",
            created.conversation_id,
            second.message_id,
            _context(),
            until_user_message_id=second_user.message_id,
        )
    )

    assert adapter.stream_calls == 2  # 无分类或润色触发的额外调用
    metadata = adapter.payloads[1]["global_writing_policy"]
    assert metadata["form"] == ChatResponseForm.DIRECT_TASK.value
    assert "detail_requested" in metadata["constraints"]
    # 不复制完整历史另建情绪块：策略块里只有规则，不含上一轮正文。
    system_blocks = [
        message["content"]
        for message in adapter.payloads[1]["messages"]
        if message["role"] == "system"
    ]
    assert any("按需求量展开必要步骤与推导" in block for block in system_blocks)
    assert all("写一篇两千字的故事" not in block for block in system_blocks)


def test_retry_reuses_issue21_snapshot(tmp_path: Path) -> None:
    service, adapter = _counting_service(tmp_path)
    created = service.create_conversation("alice")
    user, assistant, _ = service.start_generation(
        "alice", created.conversation_id, "不想听建议，只想聊聊"
    )
    list(
        service.stream_generation(
            "alice",
            created.conversation_id,
            assistant.message_id,
            _context(),
            until_user_message_id=user.message_id,
        )
    )
    stored_first = service._repo.get_run_by_message(  # noqa: SLF001 - 运行配置断言
        "alice", assistant.message_id
    ).config["global_writing_policy"]

    owner, retried, _ = service.retry_generation(
        "alice", created.conversation_id, assistant.message_id
    )
    list(
        service.stream_generation(
            "alice",
            created.conversation_id,
            retried.message_id,
            _context(),
            until_user_message_id=owner.message_id,
        )
    )
    stored_second = service._repo.get_run_by_message(  # noqa: SLF001 - 运行配置断言
        "alice", retried.message_id
    ).config["global_writing_policy"]

    assert adapter.stream_calls == 2
    assert stored_second["version"] == stored_first["version"]
    assert stored_second["form"] == stored_first["form"]
    assert stored_second["constraints"] == stored_first["constraints"]


def test_tool_failure_is_wired_into_expression_policy(tmp_path: Path) -> None:
    from bridges.web_search.client import WebSearchError
    from bridges.web_search.contracts import WebSearchResult
    from bridges.web_search.service import WebSearchService

    class _FailingClient:
        def search(self, query: str) -> list[WebSearchResult]:
            raise WebSearchError("web_search_timeout", "联网搜索超时，请重试。")

    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    repository = ConversationRepository(database)
    registry = CapabilityRegistry()
    registry.register(
        CapabilityRecord(
            name="qwen_text_chat",
            version="1",
            kind=CapabilityKind.MODEL,
            vendor="qwen",
            region="cn-beijing",
            model_id="qwen3.7-plus-2026-05-26",
            input_schema_version="chat-messages-v1",
            output_schema_version="chat-completion-v1",
        )
    )
    adapter = _CountingStreamAdapter()
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", adapter)
    service = ChatService(
        repository=repository,
        gateway=gateway,
        web_search_service=WebSearchService(client=_FailingClient()),
    )
    created = service.create_conversation("alice")
    user, assistant, _ = service.start_generation(
        "alice", created.conversation_id, "请联网核实这个说法是否属实"
    )
    _allow_web_search(service, "alice", assistant.message_id)
    list(
        service.stream_generation(
            "alice",
            created.conversation_id,
            assistant.message_id,
            _context(),
            until_user_message_id=user.message_id,
        )
    )

    assert adapter.stream_calls == 1
    metadata = adapter.payloads[0]["global_writing_policy"]
    assert metadata["form"] == ChatResponseForm.ERROR_REFUSAL.value
    system_blocks = [
        message["content"]
        for message in adapter.payloads[0]["messages"]
        if message["role"] == "system"
    ]
    assert any("不把未核实内容说成工具已成功" in block for block in system_blocks)


def test_parent_graph_end_to_end_uses_v3_policy(
    sqlite_app: Any, client: Any, generation_helpers: dict[str, Any]
) -> None:
    from tests.chat.test_chat_api import (
        _create_conversation,
        _gateway_with,
        _register,
    )

    adapter = _CountingStreamAdapter()
    sqlite_app.state.chat_service._gateway = _gateway_with(adapter)  # noqa: SLF001
    account = _register(client)
    conversation_id = _create_conversation(client)
    created = generation_helpers["send"](
        client, conversation_id, content="我今天很难过，不想听建议，只想聊聊"
    )
    generation_helpers["drive"](sqlite_app)
    assistant_id = created["assistant_message"]["message_id"]

    service = sqlite_app.state.chat_service
    run = service._repo.get_run_by_message(  # noqa: SLF001 - 运行配置断言
        account["id"], assistant_id
    )
    stored = (run.config or {})["global_writing_policy"]
    assert stored["version"] == GLOBAL_CHAT_LIGHTWEIGHT_VERSION
    assert stored["form"] == ChatResponseForm.EMPATHY.value
    assert "no_advice" in stored["constraints"]
    assert "no-unsolicited-advice" in stored["rule_ids"]
    assert adapter.stream_calls == 1
    system_blocks = [
        message["content"]
        for message in adapter.payloads[0]["messages"]
        if message["role"] == "system"
    ]
    assert any("不提供计划、步骤或行动建议" in block for block in system_blocks)
    assert all("必须带来具体判断" not in block for block in system_blocks)
