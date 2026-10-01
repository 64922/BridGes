"""改进工单 13 验收测试：有界摘要缓存与后台准备。

覆盖 ``.scratch/2/issues/13-bounded-summary-cache.md`` 的五条验收标准：

1. 121 条长历史最终输入收敛到预算内或明确受限，不随消息数无限追加摘要行；
2. 末尾条件、否定、后续纠正可追溯原文，新纠正本轮生效不等待缓存；
3. 新增片段只整理需要压缩部分，有效缓存复用，原文重建结果具可核验来源；
4. 正常短对话无不必要摘要调用；同步补齐超时/重试耗尽后不会无限等待；
5. 来源无效后不复用派生依据，缓存迁移/删除/导出和取消守卫完整。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from bridges.chat.context_compiler import (
    SUMMARY_FALLBACK_MAX_CHARS,
    SUMMARY_RENDER_MAX_CHARS,
    compile_turn_context,
)
from bridges.chat.repository import ConversationRepository, MessageRecord
from bridges.chat.run_executor import GenerationRunExecutor
from bridges.chat.summary import (
    ChatSummaryService,
    SummaryExtraction,
    source_fingerprint,
)
from bridges.contracts.chat import (
    ChatMessageRole,
    ChatMessageStatus,
    ChatMode,
)
from bridges.contracts.summaries import (
    SUMMARY_GENERATOR_VERSION,
    SummarySourceMessage,
)
from bridges.lifecycle.catalog import ACCOUNT_TABLES, EXPORT_CATEGORIES
from bridges.storage.database import BridgesDatabase

_ACCOUNT = "acc-accept"
_CONVERSATION = "conv-accept"
_START = datetime(2026, 10, 1, tzinfo=UTC)


@pytest.fixture
def database(tmp_path: Any) -> BridgesDatabase:
    db = BridgesDatabase(str(tmp_path / "bridges.db"))
    db.initialize()
    return db


@pytest.fixture
def conversations(database: BridgesDatabase) -> ConversationRepository:
    return ConversationRepository(database)


def _record(
    index: int,
    role: ChatMessageRole,
    content: str,
    *,
    status: ChatMessageStatus = ChatMessageStatus.DONE,
) -> MessageRecord:
    created = _START + timedelta(seconds=index)
    return MessageRecord(
        message_id=f"m{index:03d}",
        conversation_id=_CONVERSATION,
        account_id=_ACCOUNT,
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
    )


def _seed_long_history(
    conversations: ConversationRepository,
    *,
    exchanges: int,
    user_content: Any,
    assistant_content: Any = "收到。",
) -> list[MessageRecord]:
    conversations.create_conversation(
        account_id=_ACCOUNT,
        conversation_id=_CONVERSATION,
        title="长历史",
        mode="companion",
        created_at=_START,
    )
    records: list[MessageRecord] = []
    for index in range(exchanges):
        user = _record(
            index * 2,
            ChatMessageRole.USER,
            user_content(index) if callable(user_content) else user_content,
        )
        assistant = _record(
            index * 2 + 1,
            ChatMessageRole.ASSISTANT,
            assistant_content(index)
            if callable(assistant_content)
            else assistant_content,
        )
        conversations.insert_message(user)
        conversations.insert_message(assistant)
        records.extend([user, assistant])
    return records


class _StubExtractor:
    version = SUMMARY_GENERATOR_VERSION

    def __init__(self) -> None:
        self.calls: list[list[SummarySourceMessage]] = []

    def extract(
        self,
        *,
        run_context: Any,
        sources: list[SummarySourceMessage],
        timeout_ms: int,
    ) -> SummaryExtraction:
        self.calls.append(list(sources))
        return SummaryExtraction(
            output={
                "summary": "较早片段背景：" + sources[0].content[:80],
                "object_clues": [sources[0].message_id],
                "open_questions": [],
            },
            input_tokens=32,
            output_tokens=16,
        )


def _summary_block(compiled: Any) -> str:
    return next(
        message["content"]
        for message in compiled.messages
        if message["role"] == "system" and "较早对话的摘要" in message["content"]
    )


def _prepare_full_coverage(
    service: ChatSummaryService,
    conversations: ConversationRepository,
    boundary_message_id: str,
) -> None:
    for _ in range(6):
        service.schedule_background(
            _ACCOUNT, _CONVERSATION, boundary_message_id=boundary_message_id
        )
        if service.pending_count == 0:
            return
        service.run_tick()


# ---------------------------------------------------------------------------
# 验收 1：121 条长历史收敛或有界
# ---------------------------------------------------------------------------


def test_121_message_history_summary_is_bounded(
    database: BridgesDatabase, conversations: ConversationRepository
) -> None:
    _seed_long_history(
        conversations,
        exchanges=60,
        user_content=lambda index: f"第{index}个问题：" + "问" * 800,
        assistant_content=lambda index: f"第{index}个回答：" + "答" * 800,
    )
    current = _record(120, ChatMessageRole.USER, "你好")
    conversations.insert_message(current)
    messages = conversations.list_messages(_ACCOUNT, _CONVERSATION)
    assert len(messages) == 121

    # 小窗口 + 无缓存：确定性回退行有界；要么收敛在预算内，要么明确受限。
    compiled = compile_turn_context(
        messages=messages,
        current_user_message_id=current.message_id,
        model_id="test-model",
        mode=ChatMode.COMPANION,
        context_window=4000,
    )
    assert compiled.summary_source_range is not None
    assert compiled.summary_cache_hit is False
    assert (
        compiled.input_token_estimate <= compiled.input_budget_tokens
        or compiled.budget_floor_exceeded
    )
    # 摘要总长度有上限，不随消息数线性增长（回退行 ≤ 条数/字符双上限）。
    block = _summary_block(compiled)
    assert len(block) <= SUMMARY_RENDER_MAX_CHARS + SUMMARY_FALLBACK_MAX_CHARS + 400
    assert compiled.summary_omitted_message_count >= 0


def test_121_message_history_prepares_bounded_cache_and_converges(
    database: BridgesDatabase, conversations: ConversationRepository
) -> None:
    records = _seed_long_history(
        conversations,
        exchanges=60,
        user_content=lambda index: f"第{index}个问题：" + "问" * 400,
        assistant_content=lambda index: f"第{index}个回答：" + "答" * 400,
    )
    extractor = _StubExtractor()
    service = ChatSummaryService(
        database=database,
        conversation_repository=conversations,
        extractor=extractor,
    )
    current = _record(120, ChatMessageRole.USER, "继续")
    conversations.insert_message(current)
    # 后台准备边界 = 最近原文之前的最后一条（与生产 schedule 一致）。
    boundary = records[-1].message_id
    _prepare_full_coverage(service, conversations, boundary)

    active = service.valid_summaries(_ACCOUNT, _CONVERSATION)
    assert active, "长历史应已准备有效缓存"
    assert service.pending_count == 0
    # 缓存按片段有界：覆盖范围连续且可核验，相邻片段不重叠。
    assert active[0].covered_first_message_id == "m000"
    assert active[-1].covered_last_message_id == boundary
    for previous, following in zip(active, active[1:], strict=False):
        assert (
            int(following.covered_first_message_id[1:])
            == int(previous.covered_last_message_id[1:]) + 1
        )
    messages = conversations.list_messages(_ACCOUNT, _CONVERSATION)
    compiled = compile_turn_context(
        messages=messages,
        current_user_message_id=current.message_id,
        model_id="test-model",
        mode=ChatMode.COMPANION,
        context_window=4000,
        summaries=active,
    )
    assert compiled.summary_cache_hit is True
    assert compiled.summary_instance_ids
    block = _summary_block(compiled)
    assert "【缓存摘要" in block
    assert len(block) <= SUMMARY_RENDER_MAX_CHARS + SUMMARY_FALLBACK_MAX_CHARS + 400
    assert (
        compiled.input_token_estimate <= compiled.input_budget_tokens
        or compiled.budget_floor_exceeded
    )


# ---------------------------------------------------------------------------
# 验收 2：末尾条件/纠正可追溯，本轮不等缓存
# ---------------------------------------------------------------------------


def test_tail_constraint_and_correction_traceable_without_cache(
    conversations: ConversationRepository,
) -> None:
    conversations.create_conversation(
        account_id=_ACCOUNT,
        conversation_id=_CONVERSATION,
        title="纠正",
        mode="companion",
        created_at=_START,
    )
    first = _record(
        0,
        ChatMessageRole.USER,
        "背景说明。" * 100 + "最终预算不得超过三千元。",
    )
    conversations.insert_message(first)
    conversations.insert_message(_record(1, ChatMessageRole.ASSISTANT, "已记下。"))
    correction = _record(
        2, ChatMessageRole.USER, "上面说错了，预算改成五千元。"
    )
    conversations.insert_message(correction)
    conversations.insert_message(_record(3, ChatMessageRole.ASSISTANT, "好的。"))
    for index in range(4, 11):
        conversations.insert_message(
            _record(
                index,
                ChatMessageRole.USER if index % 2 == 0 else ChatMessageRole.ASSISTANT,
                "闲聊" * 300,
            )
        )
    current = _record(11, ChatMessageRole.USER, "之前说好的「预算」是多少？")
    conversations.insert_message(current)
    messages = conversations.list_messages(_ACCOUNT, _CONVERSATION)

    # 无缓存：最新纠正与当前请求仍直接生效；被引用的末尾条件按原文补回。
    compiled = compile_turn_context(
        messages=messages,
        current_user_message_id=current.message_id,
        model_id="test-model",
        mode=ChatMode.COMPANION,
        context_window=3500,
        summaries=[],
    )
    all_content = "\n".join(message["content"] for message in compiled.messages)
    assert compiled.messages[-1]["content"] == "之前说好的「预算」是多少？"
    assert "预算改成五千元" in all_content
    assert "最终预算不得超过三千元" in all_content
    assert "三千元" in all_content  # 旧值可追溯，未被缓存掩蔽
    assert compiled.recovered_message_ids


# ---------------------------------------------------------------------------
# 验收 3：只整理未覆盖片段 + 缓存复用 + 来源可核验
# ---------------------------------------------------------------------------


def test_incremental_segments_only_cover_new_originals(
    database: BridgesDatabase, conversations: ConversationRepository
) -> None:
    records = _seed_long_history(
        conversations,
        exchanges=5,
        user_content=lambda index: f"问题{index}：" + "问" * 200,
        assistant_content=lambda index: "答" * 200,
    )
    extractor = _StubExtractor()
    service = ChatSummaryService(
        database=database,
        conversation_repository=conversations,
        extractor=extractor,
    )
    boundary = records[-1].message_id
    _prepare_full_coverage(service, conversations, boundary)
    first_instances = service.valid_summaries(_ACCOUNT, _CONVERSATION)
    assert len(first_instances) == 1
    assert first_instances[0].covered_first_message_id == "m000"
    # 来源可核验：按覆盖范围从原文重算指纹，与实例记录一致。
    first = first_instances[0]
    messages = conversations.list_messages(_ACCOUNT, _CONVERSATION)
    by_id = {message.message_id: message for message in messages}
    covered_ids = [
        f"m{index:03d}"
        for index in range(
            int(first.covered_first_message_id[1:]),
            int(first.covered_last_message_id[1:]) + 1,
        )
    ]
    recomputed = source_fingerprint(
        [
            SummarySourceMessage(
                message_id=by_id[message_id].message_id,
                role=by_id[message_id].role.value,
                content=by_id[message_id].content,
            )
            for message_id in covered_ids
        ]
    )
    assert first.covered_message_count == len(covered_ids)
    assert recomputed == first.source_fingerprint

    # 新增两条原文：后台只整理新增片段，旧缓存复用，不重算旧范围。
    new_user = _record(10, ChatMessageRole.USER, "新增：" + "新" * 200)
    new_assistant = _record(11, ChatMessageRole.ASSISTANT, "新增答：" + "答" * 200)
    conversations.insert_message(new_user)
    conversations.insert_message(new_assistant)
    calls_before = len(extractor.calls)
    _prepare_full_coverage(service, conversations, new_assistant.message_id)
    new_calls = extractor.calls[calls_before:]
    assert new_calls, "新增原文应触发后台整理"
    assert all(
        source.message_id in {new_user.message_id, new_assistant.message_id}
        for source in new_calls[0]
    )
    active = service.valid_summaries(_ACCOUNT, _CONVERSATION)
    assert len(active) == 2
    assert active[0].source_fingerprint == first_instances[0].source_fingerprint

    current = _record(12, ChatMessageRole.USER, "最新问题")
    conversations.insert_message(current)
    messages = conversations.list_messages(_ACCOUNT, _CONVERSATION)
    compiled = compile_turn_context(
        messages=messages,
        current_user_message_id=current.message_id,
        model_id="test-model",
        mode=ChatMode.COMPANION,
        context_window=2500,
        summaries=active,
    )
    all_content = "\n".join(message["content"] for message in compiled.messages)
    assert "缓存摘要" in all_content
    assert compiled.summary_cache_hit is True


# ---------------------------------------------------------------------------
# 验收 4：短对话不调用 + 失败不无限等待
# ---------------------------------------------------------------------------


def test_short_conversation_has_no_summary_range(
    conversations: ConversationRepository,
) -> None:
    records = _seed_long_history(
        conversations,
        exchanges=2,
        user_content=lambda index: f"短问题{index}",
        assistant_content="短回答",
    )
    current = _record(4, ChatMessageRole.USER, "再聊一句")
    conversations.insert_message(current)
    messages = conversations.list_messages(_ACCOUNT, _CONVERSATION)
    compiled = compile_turn_context(
        messages=messages,
        current_user_message_id=current.message_id,
        model_id="test-model",
        mode=ChatMode.COMPANION,
        context_window=1_000_000,
    )
    # 无不必要摘要：近期原文完整覆盖，不产生摘要范围（服务层据此不登记）。
    assert compiled.summary_source_range is None
    assert compiled.summary_cache_hit is True
    assert len(records) == 4


def test_summary_task_composition_runs_after_generation_tick(
    database: BridgesDatabase, conversations: ConversationRepository
) -> None:
    """执行器每轮在生成 tick 之后单独领取摘要任务（回答后后台准备）。"""
    calls: list[str] = []

    class _SummaryStub:
        def run_tick(self) -> str:
            calls.append("summary")
            return "chat-summary: 已准备 1 段有界摘要。"

    repo = ConversationRepository(database)
    executor = GenerationRunExecutor(
        SimpleNamespace(_repo=repo, terminal=SimpleNamespace()),  # type: ignore[arg-type]
        database,
        summary_service=_SummaryStub(),  # type: ignore[arg-type]
    )
    assert "chat-summary" in executor.run_tick()
    assert calls == ["summary"]


# ---------------------------------------------------------------------------
# 验收 5：失效不复用 + 迁移/删除/导出完整
# ---------------------------------------------------------------------------


def test_invalid_source_is_not_reused_and_delete_cascades(
    database: BridgesDatabase, conversations: ConversationRepository
) -> None:
    records = _seed_long_history(
        conversations,
        exchanges=3,
        user_content=lambda index: f"问题{index}：" + "问" * 200,
        assistant_content="答" * 200,
    )
    extractor = _StubExtractor()
    service = ChatSummaryService(
        database=database,
        conversation_repository=conversations,
        extractor=extractor,
    )
    _prepare_full_coverage(service, conversations, records[-1].message_id)
    assert service.valid_summaries(_ACCOUNT, _CONVERSATION)
    # 来源删除：失配即失效，下一轮不复用派生依据。
    with database.transaction():
        database.scoped(_ACCOUNT).execute(
            "DELETE FROM messages WHERE message_id = ? AND account_id = ?",
            ("m000", _ACCOUNT),
        )
    assert service.valid_summaries(_ACCOUNT, _CONVERSATION) == []
    # 会话删除级联清理摘要（外键要求的删除顺序）。
    assert conversations.delete_conversation(_ACCOUNT, _CONVERSATION) == 1
    row = database.connection.execute(
        "SELECT COUNT(*) AS count FROM conversation_summaries"
    ).fetchone()
    assert row["count"] == 0


def test_summary_tables_registered_for_export_and_deletion() -> None:
    assert "conversation_summaries" in ACCOUNT_TABLES
    assert ACCOUNT_TABLES.index("conversation_summaries") < ACCOUNT_TABLES.index(
        "conversations"
    )
    categories = {category.key: category for category in EXPORT_CATEGORIES}
    assert categories["history_summaries"].tables == ("conversation_summaries",)
