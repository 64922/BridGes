"""改进工单 13 单元测试：有界摘要缓存的生成、校验、复用、失效与后台准备。

覆盖：

- ``validate_summary_output`` 的确定性校验：结构/长度上限、数值保真
  （捏造数值拒绝）、否定保真（来源没有的否定词拒绝）。
- ``ConversationSummaryRepository`` 的保存/失效/删除与账户隔离。
- ``ChatSummaryService``：短对话不调用、后台准备只整理未覆盖片段、
  有效缓存复用、来源变化失效、取消守卫、超时/重试次数上限。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest

from bridges.ai.ports import ModelRunLockRecorder
from bridges.chat.context_compiler import compile_turn_context
from bridges.chat.repository import (
    ConversationRepository,
    MessageRecord,
)
from bridges.chat.summary import (
    ChatSummaryService,
    ConversationSummaryRepository,
    GatewaySummaryExtractor,
    SummaryExtraction,
    SummaryRejectedError,
    SummaryUnavailableError,
    source_fingerprint,
    validate_summary_output,
)
from bridges.contracts.ai import BusinessRef, ModelRunLock
from bridges.contracts.chat import (
    ChatMessageRole,
    ChatMessageStatus,
    ChatMode,
)
from bridges.contracts.summaries import (
    SUMMARY_CONTRACT_VERSION,
    SUMMARY_GENERATOR_VERSION,
    SUMMARY_MAX_CALLS_PER_TASK,
    SUMMARY_MIN_NEW_MESSAGES,
    SUMMARY_TEXT_MAX_CHARS,
    HistorySummary,
    SummarySourceMessage,
)
from bridges.storage.database import BridgesDatabase

_ACCOUNT = "acc-13"
_CONVERSATION = "conv-13"
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
    conversation_id: str = _CONVERSATION,
    account_id: str = _ACCOUNT,
) -> MessageRecord:
    created = _START + timedelta(seconds=index)
    return MessageRecord(
        message_id=f"m{index:03d}",
        conversation_id=conversation_id,
        account_id=account_id,
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


def _seed(
    conversations: ConversationRepository,
    *,
    account_id: str = _ACCOUNT,
    conversation_id: str = _CONVERSATION,
    exchanges: int = 3,
    content_prefix: str = "闲聊",
    content_length: int = 40,
) -> list[MessageRecord]:
    conversations.create_conversation(
        account_id=account_id,
        conversation_id=conversation_id,
        title="摘要测试",
        mode="companion",
        created_at=_START,
    )
    records: list[MessageRecord] = []
    for index in range(exchanges):
        user = _record(
            index * 2,
            ChatMessageRole.USER,
            f"{content_prefix}问题{index}：" + content_prefix * content_length,
            conversation_id=conversation_id,
            account_id=account_id,
        )
        assistant = _record(
            index * 2 + 1,
            ChatMessageRole.ASSISTANT,
            f"{content_prefix}回答{index}：" + content_prefix * content_length,
            conversation_id=conversation_id,
            account_id=account_id,
        )
        conversations.insert_message(user)
        conversations.insert_message(assistant)
        records.extend([user, assistant])
    return records


def _rewrite_message(
    database: BridgesDatabase, message_id: str, content: str
) -> None:
    """测试用：改写一条已完成消息正文（生产路径不编辑已完成原文）。"""
    with database.transaction():
        database.scoped(_ACCOUNT).execute(
            "UPDATE messages SET content = ? WHERE message_id = ? AND account_id = ?",
            (content, message_id, _ACCOUNT),
        )


def _summary(
    *,
    first: str,
    last: str,
    count: int,
    fingerprint: str,
    text: str = "背景线索。",
    account_id: str = _ACCOUNT,
    conversation_id: str = _CONVERSATION,
    instance_version: str = SUMMARY_GENERATOR_VERSION,
) -> HistorySummary:
    now = datetime.now(UTC)
    return HistorySummary(
        summary_id=f"sum-{first}-{last}",
        account_id=account_id,
        conversation_id=conversation_id,
        contract_version=SUMMARY_CONTRACT_VERSION,
        instance_version=instance_version,
        covered_first_message_id=first,
        covered_last_message_id=last,
        covered_message_count=count,
        source_fingerprint=fingerprint,
        text=text,
        created_at=now,
        updated_at=now,
    )


class _StubExtractor:
    """确定性摘要抽取替身：记录每次调用的来源，可注入失败/来源变更。"""

    version = SUMMARY_GENERATOR_VERSION

    def __init__(
        self,
        *,
        fail: Exception | None = None,
        on_extract: Any | None = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self._fail = fail
        self._on_extract = on_extract

    def extract(
        self,
        *,
        run_context: Any,
        sources: list[SummarySourceMessage],
        timeout_ms: int,
    ) -> SummaryExtraction:
        self.calls.append(
            {
                "run_context": run_context,
                "sources": list(sources),
                "timeout_ms": timeout_ms,
            }
        )
        if self._on_extract is not None:
            self._on_extract()
        if self._fail is not None:
            raise self._fail
        first = sources[0].content[:120]
        return SummaryExtraction(
            output={
                "summary": f"较早片段背景：{first}",
                "object_clues": [sources[0].message_id],
                "open_questions": [],
            },
            input_tokens=32,
            output_tokens=16,
        )


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def test_validate_accepts_grounded_output() -> None:
    draft = validate_summary_output(
        {
            "summary": "预算约定是 3000 元，没有使用 5000 元。",
            "object_clues": ["预算"],
            "open_questions": ["是否再改"],
        },
        "用户：预算改成 3000 元，不使用 5000。",
    )
    assert draft.text == "预算约定是 3000 元，没有使用 5000 元。"
    assert draft.object_clues == ("预算",)


def test_validate_rejects_fabricated_number() -> None:
    with pytest.raises(SummaryRejectedError, match="数值"):
        validate_summary_output(
            {"summary": "预算改成 9000 元。", "object_clues": [], "open_questions": []},
            "用户：预算改成 3000 元。",
        )


def test_validate_rejects_fabricated_negation() -> None:
    with pytest.raises(SummaryRejectedError, match="否定"):
        validate_summary_output(
            {"summary": "用户没有接受该方案。", "object_clues": [], "open_questions": []},
            "用户：我接受该方案。",
        )


def test_validate_rejects_numeric_substring_and_dropped_negation() -> None:
    with pytest.raises(SummaryRejectedError, match="数值"):
        validate_summary_output({"summary": "预算是30元"}, "预算是3000元")
    with pytest.raises(SummaryRejectedError, match="否定"):
        validate_summary_output({"summary": "可以发送邮件"}, "不要发送邮件")


def test_inflight_invalidation_cannot_restore_active_cache(
    database: BridgesDatabase, conversations: ConversationRepository
) -> None:
    records = _seed(conversations)
    service = ChatSummaryService(
        database=database,
        conversation_repository=conversations,
        extractor=_StubExtractor(
            on_extract=lambda: service.invalidate_conversation(
                _ACCOUNT, _CONVERSATION, reason="permission_revoked"
            )
        ),
    )
    service.schedule_background(_ACCOUNT, _CONVERSATION, boundary_message_id=records[-1].message_id)
    service.run_tick()
    assert service.valid_summaries(_ACCOUNT, _CONVERSATION) == []
    assert ConversationSummaryRepository(database).generation(_ACCOUNT, _CONVERSATION) == 1
    conversations.delete_conversation(_ACCOUNT, _CONVERSATION)
    assert (
        database.connection.execute(
            "SELECT COUNT(*) FROM conversation_summary_generations"
        ).fetchone()[0]
        == 0
    )


def test_single_original_and_permanent_failure_do_not_retry(
    database: BridgesDatabase, conversations: ConversationRepository
) -> None:
    records = _seed(conversations)
    extractor = _StubExtractor(fail=SummaryUnavailableError("invalid", "永久失败", retryable=False))
    service = ChatSummaryService(
        database=database, conversation_repository=conversations, extractor=extractor
    )
    assert (
        service.prepare_sync(
            _ACCOUNT, _CONVERSATION, boundary_message_id=records[0].message_id, run_id="one"
        )
        == []
    )
    assert extractor.calls == []
    service.schedule_background(_ACCOUNT, _CONVERSATION, boundary_message_id=records[-1].message_id)
    service.run_tick()
    service.run_tick()
    assert len(extractor.calls) == 1
    service.schedule_background(
        _ACCOUNT, _CONVERSATION, boundary_message_id=records[-1].message_id
    )
    service.run_tick()
    assert len(extractor.calls) == 1  # 重登记相同来源也不恢复永久失败任务。


def test_sync_attempt_survives_service_restart_and_cancel(
    database: BridgesDatabase, conversations: ConversationRepository
) -> None:
    from bridges.chat.run_budget_ledger import RunBudgetClass, RunBudgetLedgerRepository

    records = _seed(conversations)
    ledger = RunBudgetLedgerRepository(database)
    ledger.ensure_for_run(
        account_id=_ACCOUNT,
        run_id="sync-run",
        conversation_id=_CONVERSATION,
        budget_class=RunBudgetClass.LIGHTWEIGHT,
        run_created_at=datetime.now(UTC),
        now=datetime.now(UTC),
    )
    extractor = _StubExtractor(fail=SummaryUnavailableError("timeout", "超时", retryable=True))
    for _ in range(2):
        service = ChatSummaryService(
            database=database, conversation_repository=conversations, extractor=extractor
        )
        service.prepare_sync(
            _ACCOUNT, _CONVERSATION, boundary_message_id=records[-1].message_id, run_id="sync-run"
        )
    assert len(extractor.calls) == 1
    ledger.close(account_id=_ACCOUNT, run_id="sync-run", now=datetime.now(UTC))
    assert not ConversationSummaryRepository(database).reserve_sync_attempt(
        _ACCOUNT, _CONVERSATION, "sync-run"
    )
    ledger.ensure_for_run(
        account_id=_ACCOUNT, run_id="cancel-during-summary", conversation_id=_CONVERSATION,
        budget_class=RunBudgetClass.LIGHTWEIGHT,
        run_created_at=datetime.now(UTC), now=datetime.now(UTC),
    )
    service = ChatSummaryService(
        database=database, conversation_repository=conversations,
        extractor=_StubExtractor(on_extract=lambda: ledger.close(
            account_id=_ACCOUNT, run_id="cancel-during-summary", now=datetime.now(UTC)
        )),
    )
    assert service.prepare_sync(
        _ACCOUNT, _CONVERSATION, boundary_message_id=records[-1].message_id,
        run_id="cancel-during-summary",
    ) == []
    assert service.valid_summaries(_ACCOUNT, _CONVERSATION) == []


def test_gateway_sync_uses_frozen_deadline_and_shared_call_tokens(
    database: BridgesDatabase, conversations: ConversationRepository
) -> None:
    from bridges.chat.run_budget_ledger import RunBudgetClass, RunBudgetLedgerRepository
    from bridges.contracts.ai import ModelCallStatus
    from bridges.contracts.projects import ObjectDomain
    from bridges.contracts.workflows import RunContextEnvelope

    _seed(conversations)
    ledger = RunBudgetLedgerRepository(database)
    snapshot = ledger.ensure_for_run(
        account_id=_ACCOUNT,
        run_id="gateway-sync",
        conversation_id=_CONVERSATION,
        budget_class=RunBudgetClass.LIGHTWEIGHT,
        run_created_at=datetime.now(UTC) - timedelta(seconds=119),
        now=datetime.now(UTC),
    )

    class Gateway:
        def invoke(self, *args: Any, **kwargs: Any) -> Any:
            budget = kwargs["budget"]
            assert budget.has_ledger
            assert 0 < budget.remaining_ms() <= 1000
            assert budget.register_model_call("summary-test", purpose="history_summary")
            budget.record_model_call_result("summary-test", input_tokens=12, output_tokens=3)
            return SimpleNamespace(
                status=ModelCallStatus.SUCCESS, output={"summary": "背景"}, lock=None
            )

    extractor = GatewaySummaryExtractor(Gateway(), database=database)  # type: ignore[arg-type]
    extractor._resolve_quota = lambda: SimpleNamespace(  # type: ignore[method-assign,return-value]
        is_verified=True, quota_version="test"
    )
    context = RunContextEnvelope(
        run_id="gateway-sync",
        account_id=_ACCOUNT,
        project_id=_CONVERSATION,
        workflow_name="history-summary-sync",
        workflow_version="1",
        object_domain=ObjectDomain.PERSONAL_VAULT,
        submitted_at=datetime.now(UTC),
    )
    extractor.extract(run_context=context, sources=[], timeout_ms=8000)
    after = ledger.load(_ACCOUNT, context.run_id)
    assert after is not None
    assert after.plan.deadline_at == snapshot.plan.deadline_at
    assert (after.model_calls_used, after.input_tokens_used, after.output_tokens_used) == (1, 12, 3)


def test_validate_rejects_oversized_or_malformed_output() -> None:
    source = "任意来源正文。"
    with pytest.raises(SummaryRejectedError):
        validate_summary_output({"open_questions": []}, source)
    with pytest.raises(SummaryRejectedError):
        validate_summary_output(
            {"summary": "长" * (SUMMARY_TEXT_MAX_CHARS + 1)}, source
        )
    with pytest.raises(SummaryRejectedError):
        validate_summary_output(
            {"summary": "正常", "object_clues": [1]}, source
        )


def test_source_fingerprint_changes_with_content_or_membership() -> None:
    base = [SummarySourceMessage(message_id="m1", role="user", content="原文")]
    assert source_fingerprint(base) == source_fingerprint(list(base))
    changed = [SummarySourceMessage(message_id="m1", role="user", content="改过")]
    assert source_fingerprint(base) != source_fingerprint(changed)
    extended = base + [
        SummarySourceMessage(message_id="m2", role="assistant", content="回答")
    ]
    assert source_fingerprint(base) != source_fingerprint(extended)


# ---------------------------------------------------------------------------
# 仓库：保存 / 失效 / 删除 / 取消守卫
# ---------------------------------------------------------------------------


def test_repository_saves_and_invalidates_by_source_change(
    database: BridgesDatabase, conversations: ConversationRepository
) -> None:
    records = _seed(conversations, exchanges=2)
    repo = ConversationSummaryRepository(database)
    fingerprint = source_fingerprint(
        [
            SummarySourceMessage(
                message_id=item.message_id,
                role=item.role.value,
                content=item.content,
            )
            for item in records
        ]
    )
    item = _summary(first="m000", last="m003", count=4, fingerprint=fingerprint)
    assert repo.save_if_sources_unchanged(item) is True
    assert [s.summary_id for s in repo.list_active(_ACCOUNT, _CONVERSATION)] == [
        item.summary_id
    ]
    # 来源被修改后保存守卫拒绝新实例（旧实例由读取侧失效）。
    _rewrite_message(database, "m000", "被改写的正文")
    changed = _summary(
        first="m000", last="m003", count=4, fingerprint=fingerprint, text="旧指纹"
    )
    changed = changed.model_copy(update={"summary_id": "sum-changed"})
    assert repo.save_if_sources_unchanged(changed) is False
    assert len(repo.list_active(_ACCOUNT, _CONVERSATION)) == 1


def test_repository_invalidate_and_delete_are_account_scoped(
    database: BridgesDatabase, conversations: ConversationRepository
) -> None:
    records = _seed(conversations, exchanges=1)
    repo = ConversationSummaryRepository(database)
    fingerprint = source_fingerprint(
        [
            SummarySourceMessage(
                message_id=item.message_id,
                role=item.role.value,
                content=item.content,
            )
            for item in records
        ]
    )
    item = _summary(first="m000", last="m001", count=2, fingerprint=fingerprint)
    assert repo.save_if_sources_unchanged(item)
    # 跨账户失效/删除不可见。
    assert (
        repo.invalidate_conversation(
            "other", _CONVERSATION, reason="x", now=datetime.now(UTC)
        )
        == 0
    )
    assert repo.delete_for_conversation("other", _CONVERSATION) == 0
    assert repo.invalidate_conversation(
        _ACCOUNT, _CONVERSATION, reason="fact_suppressed", now=datetime.now(UTC)
    ) == 1
    assert repo.list_active(_ACCOUNT, _CONVERSATION) == []
    assert repo.delete_for_conversation(_ACCOUNT, _CONVERSATION) == 1


# ---------------------------------------------------------------------------
# 服务：短对话不调用 / 分段准备 / 缓存复用 / 失效 / 守卫 / 上限
# ---------------------------------------------------------------------------


def test_schedule_background_waits_for_enough_new_originals(
    database: BridgesDatabase, conversations: ConversationRepository
) -> None:
    """新增未覆盖原文不足下限时不登记任务（不给正常短对话加调用）。"""
    records = _seed(conversations, exchanges=2)
    extractor = _StubExtractor()
    service = ChatSummaryService(
        database=database,
        conversation_repository=conversations,
        extractor=extractor,
    )
    # 只有 1 条未覆盖消息：不满足最小新增量。
    service.schedule_background(
        _ACCOUNT, _CONVERSATION, boundary_message_id=records[0].message_id
    )
    assert service.pending_count == 0
    assert extractor.calls == []
    # 达到下限后登记；后台 tick 领取。
    service.schedule_background(
        _ACCOUNT, _CONVERSATION, boundary_message_id=records[1].message_id
    )
    assert service.pending_count == 1
    service.run_tick()
    assert len(extractor.calls) == 1
    # 已覆盖同一范围后不再重复登记。
    service.schedule_background(
        _ACCOUNT, _CONVERSATION, boundary_message_id=records[1].message_id
    )
    assert service.pending_count == 0
    assert len(extractor.calls) == 1


def test_background_prepares_only_uncovered_segment(
    database: BridgesDatabase, conversations: ConversationRepository
) -> None:
    records = _seed(conversations, exchanges=3, content_length=80)
    extractor = _StubExtractor()
    service = ChatSummaryService(
        database=database,
        conversation_repository=conversations,
        extractor=extractor,
    )
    boundary = records[-1].message_id
    service.schedule_background(
        _ACCOUNT, _CONVERSATION, boundary_message_id=boundary
    )
    assert service.pending_count == 1
    message = service.run_tick()
    assert "已准备" in message
    assert len(extractor.calls) == 1
    first_call_sources = extractor.calls[0]["sources"]
    assert first_call_sources[0].message_id == "m000"
    active = service.valid_summaries(_ACCOUNT, _CONVERSATION)
    assert len(active) == 1
    assert active[0].covered_first_message_id == "m000"
    assert active[0].covered_last_message_id == boundary

    # 再追加两条新原文后再次准备：只整理新增未覆盖片段，缓存其余复用。
    new_user = _record(6, ChatMessageRole.USER, "新增问题：" + "新" * 120)
    new_assistant = _record(
        7, ChatMessageRole.ASSISTANT, "新增回答：" + "答" * 120
    )
    conversations.insert_message(new_user)
    conversations.insert_message(new_assistant)
    service.schedule_background(
        _ACCOUNT, _CONVERSATION, boundary_message_id=new_assistant.message_id
    )
    assert service.pending_count == 1
    service.run_tick()
    assert len(extractor.calls) == 2
    second_call_sources = extractor.calls[1]["sources"]
    assert second_call_sources[0].message_id == new_user.message_id
    assert all(
        source.message_id not in {"m000", "m004"}
        for source in second_call_sources
    )
    active = service.valid_summaries(_ACCOUNT, _CONVERSATION)
    assert [item.covered_first_message_id for item in active] == ["m000", "m006"]


def test_source_change_invalidates_cache_and_blocks_reuse(
    database: BridgesDatabase, conversations: ConversationRepository
) -> None:
    records = _seed(conversations, exchanges=3, content_length=80)
    extractor = _StubExtractor()
    service = ChatSummaryService(
        database=database,
        conversation_repository=conversations,
        extractor=extractor,
    )
    service.schedule_background(
        _ACCOUNT, _CONVERSATION, boundary_message_id=records[-1].message_id
    )
    service.run_tick()
    assert len(service.valid_summaries(_ACCOUNT, _CONVERSATION)) == 1
    # 来源修改：缓存失效，不再复用派生依据。
    _rewrite_message(database, "m000", "改写后的正文 " * 10)
    assert service.valid_summaries(_ACCOUNT, _CONVERSATION) == []
    # 再次准备时从被失效的起点重建（回到原文，不压缩旧摘要）。
    service.schedule_background(
        _ACCOUNT, _CONVERSATION, boundary_message_id=records[-1].message_id
    )
    assert service.pending_count == 1
    service.run_tick()
    assert extractor.calls[-1]["sources"][0].content.startswith("改写后的正文")


def test_generation_version_change_invalidates_cache(
    database: BridgesDatabase, conversations: ConversationRepository
) -> None:
    records = _seed(conversations, exchanges=2)
    extractor = _StubExtractor()
    service = ChatSummaryService(
        database=database,
        conversation_repository=conversations,
        extractor=extractor,
    )
    repo = ConversationSummaryRepository(database)
    fingerprint = source_fingerprint(
        [
            SummarySourceMessage(
                message_id=item.message_id,
                role=item.role.value,
                content=item.content,
            )
            for item in records
        ]
    )
    repo.save_if_sources_unchanged(
        _summary(
            first="m000",
            last="m003",
            count=4,
            fingerprint=fingerprint,
            instance_version="summary-gen-old",
        )
    )
    assert service.valid_summaries(_ACCOUNT, _CONVERSATION) == []


def test_cancellation_guard_drops_summary_when_source_changes_mid_generation(
    database: BridgesDatabase, conversations: ConversationRepository
) -> None:
    records = _seed(conversations, exchanges=3, content_length=80)
    state = {"done": False}

    def _mutate() -> None:
        if state["done"]:
            return
        state["done"] = True
        _rewrite_message(database, "m000", "生成期间被改写 " * 10)

    extractor = _StubExtractor(on_extract=_mutate)
    service = ChatSummaryService(
        database=database,
        conversation_repository=conversations,
        extractor=extractor,
    )
    service.schedule_background(
        _ACCOUNT, _CONVERSATION, boundary_message_id=records[-1].message_id
    )
    service.run_tick()
    # 生成期间来源变化：取消守卫拒绝写入，缓存为空。
    assert service.valid_summaries(_ACCOUNT, _CONVERSATION) == []


def test_sync_fill_is_single_attempt_and_failure_is_bounded(
    database: BridgesDatabase, conversations: ConversationRepository
) -> None:
    records = _seed(conversations, exchanges=3, content_length=80)
    extractor = _StubExtractor(
        fail=SummaryUnavailableError("summary_timeout", "超时", retryable=True)
    )
    service = ChatSummaryService(
        database=database,
        conversation_repository=conversations,
        extractor=extractor,
    )
    prepared = service.prepare_sync(
        _ACCOUNT,
        _CONVERSATION,
        boundary_message_id=records[-1].message_id,
        run_id="run-1",
    )
    assert prepared == []
    assert len(extractor.calls) == 1  # 同步补齐只有一次调用，不无限重试


def test_background_retries_are_bounded(
    database: BridgesDatabase, conversations: ConversationRepository
) -> None:
    records = _seed(conversations, exchanges=3, content_length=80)
    extractor = _StubExtractor(
        fail=SummaryUnavailableError("summary_transient", "临时失败", retryable=True)
    )
    service = ChatSummaryService(
        database=database,
        conversation_repository=conversations,
        extractor=extractor,
    )
    service.schedule_background(
        _ACCOUNT, _CONVERSATION, boundary_message_id=records[-1].message_id
    )
    attempts = 0
    while True:
        message = service.run_tick()
        if "无待处理任务" in message:
            break
        attempts += 1
        assert attempts <= SUMMARY_MIN_NEW_MESSAGES + 10
        # 退避窗口：手动把**尚未耗尽**的失败行调回可领取（确定性控制，
        # 不依赖真实睡眠；已耗尽行 next_retry_at 为 NULL，不得重新激活）。
        database.connection.execute(
            "UPDATE task_claims SET next_retry_at = '2000-01-01T00:00:00+00:00'"
            " WHERE queue_name = 'chat_summary' AND next_retry_at IS NOT NULL"
        )
    assert attempts == 3  # SUMMARY_MAX_ATTEMPTS：重试有上限，恰好失败三次


def test_background_calls_are_capped_per_task(
    database: BridgesDatabase, conversations: ConversationRepository
) -> None:
    # 每条原文都超过单次来源 token 上限的十分之一，迫使分多段。
    records = _seed(conversations, exchanges=8, content_length=900)
    extractor = _StubExtractor()
    service = ChatSummaryService(
        database=database,
        conversation_repository=conversations,
        extractor=extractor,
    )
    service.schedule_background(
        _ACCOUNT, _CONVERSATION, boundary_message_id=records[-1].message_id
    )
    service.run_tick()
    assert len(extractor.calls) <= SUMMARY_MAX_CALLS_PER_TASK


def test_compile_reuses_cached_summary_block(
    database: BridgesDatabase, conversations: ConversationRepository
) -> None:
    records = _seed(conversations, exchanges=6, content_length=120)
    extractor = _StubExtractor()
    service = ChatSummaryService(
        database=database,
        conversation_repository=conversations,
        extractor=extractor,
    )
    # 先准备完整覆盖（后台任务可按上限分多段；循环驱动到无待处理）。
    service.schedule_background(
        _ACCOUNT, _CONVERSATION, boundary_message_id=records[-1].message_id
    )
    for _ in range(SUMMARY_MAX_CALLS_PER_TASK + 1):
        if service.run_tick().startswith("chat-summary: 无待处理任务"):
            break
        service.schedule_background(
            _ACCOUNT, _CONVERSATION, boundary_message_id=records[-1].message_id
        )
    current = _record(12, ChatMessageRole.USER, "继续推荐设备")
    conversations.insert_message(current)
    messages = conversations.list_messages(_ACCOUNT, _CONVERSATION)
    cached = service.valid_summaries(_ACCOUNT, _CONVERSATION)
    assert cached, "后台准备后应有有效缓存"
    compiled = compile_turn_context(
        messages=messages,
        current_user_message_id=current.message_id,
        model_id="test-model",
        mode=ChatMode.COMPANION,
        context_window=2500,
        summaries=cached,
    )
    assert compiled.summary_source_range is not None
    assert compiled.summary_instance_ids
    assert compiled.summary_cache_hit is True
    assert "缓存摘要" in "\n".join(
        message["content"] for message in compiled.messages
    )


def test_gateway_extractor_records_lock_with_business_ref() -> None:
    """真实调用路径把不可变锁交给记录器时必须携带 business_ref（端口契约）。"""

    class _Recorder:
        def __init__(self) -> None:
            self.calls: list[tuple[ModelRunLock, BusinessRef]] = []

        def record(
            self, lock: ModelRunLock, *, business_ref: BusinessRef
        ) -> None:
            self.calls.append((lock, business_ref))

    recorder = _Recorder()
    extractor = GatewaySummaryExtractor(
        gateway=cast(Any, object()),
        lock_recorder=cast(ModelRunLockRecorder, recorder),
    )
    extractor._record_lock(
        cast(ModelRunLock, SimpleNamespace(lock_id="lock-1")),
        cast(Any, SimpleNamespace(project_id=_CONVERSATION)),
    )
    assert len(recorder.calls) == 1
    _, business_ref = recorder.calls[0]
    assert business_ref.object_type == "conversation"
    assert business_ref.object_id == _CONVERSATION
    assert business_ref.operation == "summarize_history"
