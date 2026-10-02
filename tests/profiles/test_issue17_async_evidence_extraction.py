"""改进工单 17：回答后异步提取、精确证据与逐候选写入门禁。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from bridges.chat.run_executor import GenerationRunExecutor
from bridges.chat.service import ChatService
from bridges.contracts.ai import ModelCallStatus
from bridges.contracts.chat import ChatMessageStatus
from bridges.contracts.profile_extraction import (
    ProfileExtractionOutput,
    ProfileExtractionStatus,
)
from bridges.contracts.profiles import FourDimension
from bridges.profiles import (
    AutomaticProfileService,
    FourDimensionProfileService,
    GatewayAutomaticProfileExtractor,
    InMemoryAutomaticProfileRepository,
    InMemoryFourDimensionProfileRepository,
    RuleBasedAutomaticProfileExtractor,
    SqliteAutomaticProfileRepository,
)
from bridges.storage import BridgesDatabase

ACCOUNT = "account-issue17"


def _output(items: list[dict[str, Any]]) -> ProfileExtractionOutput:
    return ProfileExtractionOutput.model_validate({"items": items})


def _item(
    *,
    message_id: str,
    value: str,
    start: int | None = None,
    end: int | None = None,
    fact_text: str | None = None,
    dimension: FourDimension = FourDimension.KNOWLEDGE_INTEREST,
    action: str = "create",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "dimension": dimension.value,
        "normalized_value": value,
        "evidence_ref": message_id,
        "reliability": 0.99,
        "action": action,
    }
    if fact_text is not None:
        payload["fact_text"] = fact_text
    if start is not None and end is not None:
        payload["evidence_start"] = start
        payload["evidence_end"] = end
    return payload


class _RecordingExtractor:
    version = "issue17-recording-v1"

    def __init__(self, output: ProfileExtractionOutput) -> None:
        self.output = output
        self.calls = 0
        self.last_content: str | None = None

    def extract(self, **kwargs: Any) -> ProfileExtractionOutput:
        self.calls += 1
        self.last_content = str(kwargs.get("content"))
        return self.output


def _services(
    extractor: Any,
) -> tuple[FourDimensionProfileService, AutomaticProfileService]:
    dimensions = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=InMemoryFourDimensionProfileRepository(),
    )
    return dimensions, AutomaticProfileService(
        four_dimension_service=dimensions,
        repository=InMemoryAutomaticProfileRepository(),
        extractor=extractor,
    )


def test_schedule_is_idempotent_and_runs_only_in_the_worker() -> None:
    """登记不调用模型；同键重复登记不重复计数；worker 领取后才执行一次。"""

    extractor = _RecordingExtractor(_output([]))
    _, service = _services(extractor)

    first = service.schedule_message_extraction(
        ACCOUNT,
        conversation_id="conversation-1",
        message_id="message-1",
        content="我的目标是今年通过雅思考试",
        run_id="run-1",
    )
    second = service.schedule_message_extraction(
        ACCOUNT,
        conversation_id="conversation-1",
        message_id="message-1",
        content="我的目标是今年通过雅思考试",
        run_id="run-1",
    )

    assert first is not None and first.status == ProfileExtractionStatus.PENDING
    assert second is not None and second.extraction_id == first.extraction_id
    assert len(service.list_retry_tasks(ACCOUNT)) == 1
    assert extractor.calls == 0

    service.run_retry_tick()

    assert extractor.calls == 1
    assert [
        task.status for task in service.list_retry_tasks(ACCOUNT)
    ] == [ProfileExtractionStatus.SUCCEEDED]

    service.run_retry_tick()
    assert extractor.calls == 1


def test_model_call_happens_outside_the_write_transaction(tmp_path: Any) -> None:
    """模型调用发生在写事务之外（R07），最终写入另起短事务。"""

    database = BridgesDatabase(tmp_path / "issue17-tx.db")
    database.initialize()
    seen_in_transaction: list[bool] = []

    class _TransactionProbeExtractor:
        version = "issue17-tx-probe-v1"

        def extract(self, *, message_id: str = "", **_: Any) -> ProfileExtractionOutput:
            seen_in_transaction.append(bool(database.connection.in_transaction))
            return _output([])

    dimensions = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=InMemoryFourDimensionProfileRepository(),
    )
    service = AutomaticProfileService(
        four_dimension_service=dimensions,
        repository=SqliteAutomaticProfileRepository(database),
        extractor=_TransactionProbeExtractor(),
        message_reader=lambda account_id, message_id: SimpleNamespace(
            content="我想学习 Transformer"
        ),
    )

    service.preprocess_message(
        ACCOUNT,
        conversation_id="conversation-1",
        message_id="message-1",
        content="我想学习 Transformer",
        run_id="run-1",
        mode="companion",
    )

    assert seen_in_transaction == [False]
    database.close()


def test_async_span_commit_keeps_exact_clean_fragment_only() -> None:
    """异步提交只写合规片段，引用原话是该片段的精确区间。"""

    dimensions = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=InMemoryFourDimensionProfileRepository(),
    )
    service = AutomaticProfileService(
        four_dimension_service=dimensions,
        repository=InMemoryAutomaticProfileRepository(),
        extractor=RuleBasedAutomaticProfileExtractor(),
    )
    content = "我最近焦虑，想学习 Transformer"
    run = service.schedule_message_extraction(
        ACCOUNT,
        conversation_id="conversation-1",
        message_id="message-1",
        content=content,
        run_id="run-1",
    )
    assert run is not None
    assert dimensions.list_records(ACCOUNT) == []

    service.run_retry_tick()

    records = dimensions.list_records(ACCOUNT)
    assert [(record.dimension, record.content) for record in records] == [
        (FourDimension.KNOWLEDGE_INTEREST, "Transformer")
    ]
    assert records[0].evidence_quote == "想学习 Transformer"


def test_mixed_message_keeps_clean_multi_facts_and_negation() -> None:
    """混合消息：多个爱好分别成事实；否定偏好保留否定；敏感片段零写入。"""

    dimensions = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=InMemoryFourDimensionProfileRepository(),
    )
    service = AutomaticProfileService(
        four_dimension_service=dimensions,
        repository=InMemoryAutomaticProfileRepository(),
        extractor=RuleBasedAutomaticProfileExtractor(),
    )
    content = (
        "我最近焦虑，想学习 Transformer；我喜欢跑步，也喜欢爬山。"
        "我不喜欢长篇回答，以后先结论，每天 30 分钟。"
    )

    result = service.preprocess_message(
        ACCOUNT,
        conversation_id="conversation-1",
        message_id="message-1",
        content=content,
        run_id="run-1",
        mode="companion",
    )

    assert result.run.status == ProfileExtractionStatus.SUCCEEDED
    records = dimensions.list_records(ACCOUNT)
    written = {(record.dimension, record.content) for record in records}
    assert written == {
        (FourDimension.HOBBY, "跑步"),
        (FourDimension.HOBBY, "爬山"),
        (FourDimension.KNOWLEDGE_INTEREST, "Transformer"),
        (FourDimension.KNOWLEDGE_INTEREST, "我不喜欢长篇回答"),
    }
    assert all("焦虑" not in (record.evidence_quote or "") for record in records)


def test_third_party_and_elided_self_continuation_keeps_user_fragment() -> None:
    """用户+朋友混合：只保存承前省略主语的用户自己的爱好（R05）。"""

    dimensions = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=InMemoryFourDimensionProfileRepository(),
    )
    service = AutomaticProfileService(
        four_dimension_service=dimensions,
        repository=InMemoryAutomaticProfileRepository(),
        extractor=RuleBasedAutomaticProfileExtractor(),
    )

    result = service.preprocess_message(
        ACCOUNT,
        conversation_id="conversation-1",
        message_id="message-1",
        content="我朋友喜欢爬山，我也喜欢爬山",
        run_id="run-1",
        mode="companion",
    )

    assert result.run.status == ProfileExtractionStatus.SUCCEEDED
    records = dimensions.list_records(ACCOUNT)
    assert [(record.dimension, record.content) for record in records] == [
        (FourDimension.HOBBY, "爬山")
    ]
    assert records[0].evidence_quote == "我也喜欢爬山"


def test_value_not_supported_by_span_is_not_written() -> None:
    """证据区间必须支持将要写入的值：无关原话上的伪造对象零写入（R04）。"""

    content = "我喜欢跑步"
    extractor = _RecordingExtractor(
        _output(
            [
                _item(
                    message_id="message-1",
                    value="摄影",
                    start=0,
                    end=len(content),
                )
            ]
        )
    )
    dimensions, service = _services(extractor)

    result = service.preprocess_message(
        ACCOUNT,
        conversation_id="conversation-1",
        message_id="message-1",
        content=content,
        run_id="run-1",
        mode="companion",
    )

    assert result.run.status == ProfileExtractionStatus.SUCCEEDED
    assert dimensions.list_records(ACCOUNT) == []


def test_extractor_version_change_before_commit_drops_late_result() -> None:
    """模型调用期间抽取器版本升级：最终短事务复核后丢弃迟到结果（R07）。"""

    class _VersionFlipExtractor:
        def __init__(self) -> None:
            self.version = "issue17-version-flip-v1"

        def extract(self, **kwargs: Any) -> ProfileExtractionOutput:
            self.version = "issue17-version-flip-v2"
            return _output(
                [
                    _item(
                        message_id="message-1",
                        value="Transformer",
                        start=1,
                        end=16,
                        fact_text="想学习 Transformer",
                    )
                ]
            )

    extractor = _VersionFlipExtractor()
    dimensions, service = _services(extractor)
    run = service.schedule_message_extraction(
        ACCOUNT,
        conversation_id="conversation-1",
        message_id="message-1",
        content="我想学习 Transformer",
        run_id="run-1",
    )
    assert run is not None

    service.run_retry_tick()

    assert dimensions.list_records(ACCOUNT) == []
    tasks = service.list_retry_tasks(ACCOUNT)
    assert [task.status for task in tasks] == [ProfileExtractionStatus.EXHAUSTED]
    assert tasks[0].last_error == "profile_extraction_source_invalidated"


def test_negated_span_never_becomes_a_positive_fact() -> None:
    """区间是否定原话而候选丢掉否定时，不写入肯定事实（R04/R05）。"""

    content = "我不喜欢长篇回答"
    extractor = _RecordingExtractor(
        _output(
            [
                _item(
                    message_id="message-1",
                    value="长篇回答",
                    start=0,
                    end=len(content),
                )
            ]
        )
    )
    dimensions, service = _services(extractor)

    result = service.preprocess_message(
        ACCOUNT,
        conversation_id="conversation-1",
        message_id="message-1",
        content=content,
        run_id="run-1",
        mode="companion",
    )

    assert dimensions.list_records(ACCOUNT) == []
    assert result.run.status == ProfileExtractionStatus.SUCCEEDED
    assert result.observed_count == 0


def test_dropped_time_constraint_is_not_written_as_a_stable_fact() -> None:
    """原文明示时间（每天 30 分钟）未保留时，不写入为长期事实。"""

    content = "我每天学习 30 分钟"
    extractor = _RecordingExtractor(
        _output(
            [
                _item(
                    message_id="message-1",
                    value="30 分钟学习",
                    start=0,
                    end=len(content),
                )
            ]
        )
    )
    dimensions, service = _services(extractor)

    service.preprocess_message(
        ACCOUNT,
        conversation_id="conversation-1",
        message_id="message-1",
        content=content,
        run_id="run-1",
        mode="companion",
    )

    assert dimensions.list_records(ACCOUNT) == []


def test_semantic_preference_candidate_is_written_with_precise_span() -> None:
    """「以后先给结论」本地词表未命中：精确区间支持的语义候选可提交（任务 5/8）。"""

    content = "以后先给结论"
    extractor = _RecordingExtractor(
        _output(
            [
                _item(
                    message_id="message-1",
                    value="先给结论",
                    start=0,
                    end=len(content),
                    fact_text="以后先给结论",
                )
            ]
        )
    )
    dimensions, service = _services(extractor)

    result = service.preprocess_message(
        ACCOUNT,
        conversation_id="conversation-1",
        message_id="message-1",
        content=content,
        run_id="run-1",
        mode="companion",
    )

    assert result.run.status == ProfileExtractionStatus.SUCCEEDED
    records = dimensions.list_records(ACCOUNT)
    assert [(record.dimension, record.content) for record in records] == [
        (FourDimension.KNOWLEDGE_INTEREST, "先给结论")
    ]
    assert records[0].evidence_quote == "以后先给结论"


def test_semantic_time_constraint_candidate_keeps_explicit_time() -> None:
    """「每天 30 分钟」语义候选保留原文明示时间时可写入（任务 5）。"""

    content = "我每天学习 30 分钟"
    extractor = _RecordingExtractor(
        _output(
            [
                _item(
                    message_id="message-1",
                    value="每天学习 30 分钟",
                    start=0,
                    end=len(content),
                    fact_text="每天学习 30 分钟",
                )
            ]
        )
    )
    dimensions, service = _services(extractor)

    result = service.preprocess_message(
        ACCOUNT,
        conversation_id="conversation-1",
        message_id="message-1",
        content=content,
        run_id="run-1",
        mode="companion",
    )

    assert result.run.status == ProfileExtractionStatus.SUCCEEDED
    assert [record.content for record in dimensions.list_records(ACCOUNT)] == [
        "每天学习 30 分钟"
    ]


def test_semantic_candidate_unrelated_value_is_not_written() -> None:
    """语义候选的规范值必须真实出现在精确区间，无关值零写入。"""

    content = "以后先给结论"
    extractor = _RecordingExtractor(
        _output(
            [
                _item(
                    message_id="message-1",
                    value="摄影",
                    start=0,
                    end=len(content),
                    fact_text="以后给结论加一张摄影图",
                )
            ]
        )
    )
    dimensions, service = _services(extractor)

    result = service.preprocess_message(
        ACCOUNT,
        conversation_id="conversation-1",
        message_id="message-1",
        content=content,
        run_id="run-1",
        mode="companion",
    )

    assert result.run.status == ProfileExtractionStatus.SUCCEEDED
    assert dimensions.list_records(ACCOUNT) == []


def test_semantic_candidate_on_unrelated_fragment_is_not_written() -> None:
    """混合消息里语义候选落在与偏好无关的片段上时零写入（主体守卫）。"""

    content = "以后先给结论，老王在研究量子计算"
    fragment = "老王在研究量子计算"
    start = content.index(fragment)
    extractor = _RecordingExtractor(
        _output(
            [
                _item(
                    message_id="message-1",
                    value="量子计算",
                    start=start,
                    end=start + len(fragment),
                    fact_text=fragment,
                )
            ]
        )
    )
    dimensions, service = _services(extractor)

    result = service.preprocess_message(
        ACCOUNT,
        conversation_id="conversation-1",
        message_id="message-1",
        content=content,
        run_id="run-1",
        mode="companion",
    )

    assert result.run.status == ProfileExtractionStatus.SUCCEEDED
    assert dimensions.list_records(ACCOUNT) == []


def test_fabricated_span_is_a_non_retryable_contract_failure() -> None:
    """区间越界/无真实片段是合同错误，失败关闭而不是凭空写入。"""

    content = "我想学习 Transformer"
    extractor = _RecordingExtractor(
        _output(
            [
                _item(
                    message_id="message-1",
                    value="Transformer",
                    start=0,
                    end=len(content) + 50,
                )
            ]
        )
    )
    dimensions, service = _services(extractor)

    result = service.preprocess_message(
        ACCOUNT,
        conversation_id="conversation-1",
        message_id="message-1",
        content=content,
        run_id="run-1",
        mode="companion",
    )

    assert result.run.status == ProfileExtractionStatus.EXHAUSTED
    assert result.run.last_error == "profile_extraction_evidence_mismatch"
    assert dimensions.list_records(ACCOUNT) == []


def test_observation_candidate_with_precise_span_stays_observation() -> None:
    """行为观察候选只留观察，不因整条消息旧分类失败关闭（R08）。"""

    content = "找Transformer论文"
    extractor = _RecordingExtractor(
        _output(
            [
                _item(
                    message_id="message-1",
                    value="Transformer",
                    start=1,
                    end=len(content),
                    action="observe",
                )
            ]
        )
    )
    dimensions, service = _services(extractor)

    result = service.preprocess_message(
        ACCOUNT,
        conversation_id="conversation-1",
        message_id="message-1",
        content=content,
        run_id="run-1",
        mode="companion",
    )

    assert result.run.status == ProfileExtractionStatus.SUCCEEDED
    assert result.observed_count == 1
    assert dimensions.list_records(ACCOUNT) == []


class _CapturingGateway:
    """捕获画像抽取调用载荷的最小网关替身。"""

    def __init__(self, output: dict[str, Any]) -> None:
        self.payloads: list[dict[str, Any]] = []
        self._output = output

    def invoke(self, *args: Any, **kwargs: Any) -> Any:
        self.payloads.append(kwargs["payload"])
        return SimpleNamespace(
            lock=None,
            status=ModelCallStatus.SUCCESS,
            output=self._output,
            error_code=None,
            error_message=None,
        )


def test_gateway_neighbors_are_reference_only() -> None:
    """邻近用户原文只作回指线索，与当前消息分开标注（任务 3）。"""

    gateway = _CapturingGateway({"items": []})
    extractor = GatewayAutomaticProfileExtractor(
        cast(Any, gateway),
        neighbor_reader=lambda account_id, conversation_id, message_id: ["我喜欢跑步"],
    )

    extractor.extract(
        account_id=ACCOUNT,
        conversation_id="conversation-1",
        message_id="message-1",
        content="这个专业怎么样",
        run_id="run-1",
    )

    messages = gateway.payloads[0]["messages"]
    assert messages[-1] == {"role": "user", "content": "这个专业怎么样"}
    reference = messages[1]
    assert "不得作为事实证据" in reference["content"]
    assert "我喜欢跑步" in reference["content"]


def test_executor_schedules_only_after_done_terminal() -> None:
    """执行器只在回答正常完成时登记普通提取；停止/失败终态不提取（R03）。"""

    calls: list[str] = []

    class _RecordingProfileService:
        def schedule_message_extraction(self, account_id: str, **kwargs: Any) -> None:
            calls.append(account_id)

    repository = SimpleNamespace(
        get_message=lambda account_id, message_id: SimpleNamespace(
            content="我想学习 Transformer"
        )
    )
    executor = SimpleNamespace(
        _profile_extraction=_RecordingProfileService(),
        _repo=repository,
        _last_summary="",
    )
    run = SimpleNamespace(
        account_id=ACCOUNT,
        conversation_id="conversation-1",
        user_message_id="message-1",
        run_id="run-1",
    )
    schedule = GenerationRunExecutor._schedule_profile_extraction

    for status in (
        ChatMessageStatus.STOPPED,
        ChatMessageStatus.ERROR,
        ChatMessageStatus.STREAMING,
    ):
        commit = SimpleNamespace(outcome=SimpleNamespace(status=status))
        schedule(cast(GenerationRunExecutor, executor), ACCOUNT, run, commit)
    assert calls == []

    schedule(
        cast(GenerationRunExecutor, executor),
        ACCOUNT,
        run,
        SimpleNamespace(outcome=SimpleNamespace(status=ChatMessageStatus.DONE)),
    )
    assert calls == [ACCOUNT]


def test_chat_service_after_turn_schedules_only_for_done_message() -> None:
    """直接编排路径同样只在回答 DONE 后登记，失败消息不创建提取。"""

    calls: list[str] = []
    fake_profile = SimpleNamespace(
        schedule_message_extraction=lambda account_id, **kwargs: calls.append(account_id)
    )
    run = SimpleNamespace(
        user_message_id="message-1",
        run_id="run-1",
        conversation_id="conversation-1",
    )
    repository = SimpleNamespace(
        get_message=lambda account_id, message_id: SimpleNamespace(
            content="我想学习 Transformer",
            status=ChatMessageStatus.ERROR
            if message_id == "assistant-error"
            else ChatMessageStatus.DONE,
        ),
        get_run_by_message=lambda account_id, message_id: run,
    )
    service = SimpleNamespace(
        _automatic_profiles=fake_profile,
        _repo=repository,
    )
    schedule = ChatService._schedule_profile_extraction_after_turn

    schedule(cast(ChatService, service), ACCOUNT, "assistant-error")
    assert calls == []

    schedule(cast(ChatService, service), ACCOUNT, "assistant-done")
    assert calls == [ACCOUNT]
