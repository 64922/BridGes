"""工单 05：跨轮省略理解、多片段证据与逐来源原话的集成测试。

覆盖：①邻近窗口按位置读取（含助手消息、严格不含未来、按 K 截断）；
②真实会话标识随抽取传递（不再传字面量 retry）；③跨轮片段在提交期完成
定位、主体与取值核验后写入来源与原话记录，并在依据投影中逐来源展示；
④助手消息、窗口外引用、第三人/重复/无法定位的片段一律零写入且不失败；
⑤原话只出现在邻近消息、重复且无消歧区间时候选暂缓；⑥来源变化或删除
后逐来源副本不再展示。语义质量结论仍以真实模型与留出变体为准。
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import ValidationError

from bridges.ai import ModelGateway
from bridges.api.app_support import _profile_neighbor_reader
from bridges.contracts.ai import ModelCallStatus, ModelRunLock
from bridges.contracts.atomic_profile import (
    AtomicProfileEvidenceQuoteStatus,
    AtomicProfileEvidenceSourceStatus,
)
from bridges.contracts.profile_extraction import (
    ProfileExtractionOutcome,
    ProfileExtractionOutput,
    ProfileExtractionStatus,
)
from bridges.profiles import (
    AtomicProfileService,
    AutomaticProfileService,
    FourDimensionProfileService,
    GatewayAutomaticProfileExtractor,
    InMemoryAtomicProfileRepository,
    InMemoryAutomaticProfileRepository,
    InMemoryFourDimensionProfileRepository,
    ProfileNeighborMessage,
    ProfileSourceMessage,
)

ACCOUNT = "account-issue05"
CONVERSATION = "conversation-1"


def _lock(call: int) -> ModelRunLock:
    return ModelRunLock(
        lock_id=f"fake-lock-{call}",
        run_id="run-issue05",
        account_id=ACCOUNT,
        project_id=CONVERSATION,
        capability_name="qwen_profile_extraction",
        capability_version="1",
        actual_model_id="qwen3.7-plus-2026-05-26",
        region="cn-beijing",
        parameters={"temperature": 0, "max_tokens": 512},
        prompt_version="profile-extraction-prompt-v6",
        input_output_contract=(
            "qwen_profile_extraction:profile-message-v3->profile-extraction-v4"
        ),
        status=ModelCallStatus.SUCCESS,
        created_at=datetime.now(UTC),
    )


class _RecordingGateway:
    """录制最终载荷与会话标识的确定性假网关。"""

    def __init__(self, responder: Any) -> None:
        self.calls: list[dict[str, Any]] = []
        self.payloads: list[dict[str, Any]] = []
        self._responder = responder

    def invoke(self, *args: object, **kwargs: object) -> SimpleNamespace:
        # 网关合同：第 3 个位置参数是运行上下文（project_id = 会话标识）。
        context = args[2] if len(args) > 2 else None
        payload = kwargs.get("payload")
        payload_dict = payload if isinstance(payload, dict) else {}
        self.calls.append(
            {"project_id": getattr(context, "project_id", None)}
        )
        self.payloads.append(payload_dict)
        return SimpleNamespace(
            status=ModelCallStatus.SUCCESS,
            output={"items": self._responder(payload_dict)},
            error_code=None,
            error_message=None,
            lock=_lock(len(self.calls)),
        )


def _source(
    message_id: str, content: str, *, role: str = "user", status: str = "done"
) -> ProfileSourceMessage:
    return ProfileSourceMessage(
        message_id=message_id,
        conversation_id=CONVERSATION,
        role=role,
        status=status,
        content=content,
        created_at=datetime.now(UTC),
    )


def _build_service(
    responder: Any,
    *,
    window: list[ProfileNeighborMessage] | None = None,
    source_messages: dict[str, ProfileSourceMessage] | None = None,
    message_reader: Any | None = None,
) -> tuple[AutomaticProfileService, AtomicProfileService, _RecordingGateway]:
    four = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=InMemoryFourDimensionProfileRepository(),
    )
    # 直接引用调用方的消息字典：测试可以在写入后模拟来源变化或删除。
    messages = source_messages if source_messages is not None else {}

    def evidence_reader(account_id: str, message_id: str):
        del account_id
        return messages.get(message_id)
    atomic = AtomicProfileService(
        four, InMemoryAtomicProfileRepository(), message_reader=evidence_reader
    )
    gateway = _RecordingGateway(responder)
    neighbor_reader = (
        (lambda account_id, conversation_id, message_id: list(window))
        if window is not None
        else None
    )
    extractor = GatewayAutomaticProfileExtractor(
        cast(ModelGateway, gateway), neighbor_reader=neighbor_reader
    )
    service = AutomaticProfileService(
        four_dimension_service=four,
        repository=InMemoryAutomaticProfileRepository(),
        extractor=extractor,
        atomic_profile_service=atomic,
        message_reader=message_reader,
        neighbor_reader=neighbor_reader,
    )
    return service, atomic, gateway


def _schedule(
    service: AutomaticProfileService, message_id: str, content: str
):  # noqa: ANN202 - 返回 run 或 None
    return service.schedule_message_extraction(
        ACCOUNT,
        conversation_id=CONVERSATION,
        message_id=message_id,
        content=content,
        run_id=f"run-{message_id}",
    )


def _drain(service: AutomaticProfileService, *, max_ticks: int = 8) -> None:
    for _ in range(max_ticks):
        if "无待处理任务" in service.run_retry_tick():
            break


def _run_once(
    service: AutomaticProfileService, message_id: str, content: str
):  # noqa: ANN202 - 返回终态 run
    run = _schedule(service, message_id, content)
    assert run is not None
    _drain(service)
    refreshed = service._repository.get_run(  # type: ignore[attr-defined]
        ACCOUNT, message_id, service.extractor_version, run.source_hash
    )
    assert refreshed is not None
    return refreshed


def _fragment(
    *,
    ref: str,
    quote: str,
    value: str = "继续读研",
    fact_text: str = "想继续读研",
    quote_current: str = "这个专业我想继续读研",
) -> dict[str, Any]:
    return {
        "normalized_value": value,
        "fact_text": fact_text,
        "evidence_ref": "message-1",
        "evidence_quote": quote_current,
        "evidence_fragments": [{"evidence_ref": ref, "evidence_quote": quote}],
        "reliability": 0.99,
        "action": "create",
    }


# ---------------------------------------------------------------------------
# ① 邻近窗口：按位置、含助手、无未来、有界
# ---------------------------------------------------------------------------


def _fake_conversation_repository(
    messages: list[SimpleNamespace],
) -> Any:
    return SimpleNamespace(list_messages=lambda account_id, conversation_id: messages)


def _message(message_id: str, role: str, content: str) -> SimpleNamespace:
    return SimpleNamespace(message_id=message_id, role=role, content=content)


def test_neighbor_reader_takes_position_prefix_including_assistant() -> None:
    repository = _fake_conversation_repository(
        [
            _message("message-1", "user", "第一条"),
            _message("message-2", "assistant", "第一条回复"),
            _message("message-3", "user", "第二条"),
            _message("message-4", "assistant", "第二条回复"),
            _message("message-5", "user", "第三条"),
            _message("message-6", "user", "当前消息"),
            _message("message-7", "user", "未来消息"),
        ]
    )
    reader = _profile_neighbor_reader(repository)

    window = reader(ACCOUNT, CONVERSATION, "message-6")

    # K=4：取当前消息之前的最后 4 条，按时序，含助手消息，不含未来。
    assert [entry.message_id for entry in window] == [
        "message-2",
        "message-3",
        "message-4",
        "message-5",
    ]
    assert [entry.role for entry in window] == [
        "assistant",
        "user",
        "assistant",
        "user",
    ]
    assert all(entry.message_id != "message-7" for entry in window)
    # 第一条消息之前没有前文；找不到当前消息时同样不伪造窗口。
    assert reader(ACCOUNT, CONVERSATION, "message-1") == []
    assert reader(ACCOUNT, CONVERSATION, "message-404") == []


# ---------------------------------------------------------------------------
# ② 真实会话标识随抽取传递
# ---------------------------------------------------------------------------


def test_extraction_call_carries_real_conversation_id() -> None:
    message = SimpleNamespace(
        conversation_id="conversation-42",
        content="我叫小林。",
        created_at=datetime.now(UTC),
    )
    service, atomic, gateway = _build_service(
        lambda payload: [], message_reader=lambda account_id, message_id: message
    )

    run = _run_once(service, "message-1", "我叫小林。")

    assert run.status == ProfileExtractionStatus.SUCCEEDED
    assert gateway.calls == [{"project_id": "conversation-42"}]


# ---------------------------------------------------------------------------
# ③ 跨轮片段：写入、来源与原话记录、依据投影
# ---------------------------------------------------------------------------


def test_cross_turn_fragment_is_verified_written_and_displayed() -> None:
    window = [
        ProfileNeighborMessage(
            message_id="message-0", role="user", content="我在读计算机专业，大二。"
        ),
        ProfileNeighborMessage(
            message_id="message-0a", role="assistant", content="大二开始接触核心课了。"
        ),
    ]
    messages = {
        "message-1": _source("message-1", "这个专业我想继续读研。"),
        "message-0": _source("message-0", "我在读计算机专业，大二。"),
    }
    service, atomic, _gateway = _build_service(
        lambda payload: [
            _fragment(ref="message-0", quote="计算机专业"),
        ],
        window=window,
        source_messages=messages,
    )

    run = _run_once(service, "message-1", "这个专业我想继续读研。")

    assert run.outcome == ProfileExtractionOutcome.SUCCEEDED_WRITTEN
    items = atomic.list_items(ACCOUNT)
    assert len(items) == 1
    item = items[0]
    # 主来源在前、片段来源在后；逐来源原话各归其位。
    assert item.source_message_ids == ["message-1", "message-0"]
    assert item.source_quotes == {
        "message-1": "这个专业我想继续读研",
        "message-0": "计算机专业",
    }
    projection = atomic.item_evidence(ACCOUNT, item.profile_item_id)
    by_id = {source.message_id: source for source in projection.sources}
    assert by_id["message-1"].quote == "这个专业我想继续读研"
    assert (
        by_id["message-1"].quote_status
        is AtomicProfileEvidenceQuoteStatus.RECORDED
    )
    assert by_id["message-0"].quote == "计算机专业"
    assert by_id["message-0"].quote_status is AtomicProfileEvidenceQuoteStatus.RECORDED


def test_fragment_only_candidate_is_deferred() -> None:
    """只有片段、没有主引用的候选暂缓：结论主体必须以当前消息为准。"""

    window = [
        ProfileNeighborMessage(
            message_id="message-0", role="user", content="我在读计算机专业，大二。"
        )
    ]
    service, atomic, _gateway = _build_service(
        lambda payload: [
            {
                "normalized_value": "计算机专业",
                "fact_text": "在读计算机专业",
                "evidence_ref": "message-1",
                "evidence_fragments": [
                    {"evidence_ref": "message-0", "evidence_quote": "计算机专业"}
                ],
                "reliability": 0.99,
                "action": "create",
            }
        ],
        window=window,
    )

    run = _run_once(service, "message-1", "这个专业我想继续读研。")

    assert run.status == ProfileExtractionStatus.SUCCEEDED
    assert run.outcome == ProfileExtractionOutcome.SUCCEEDED_EMPTY
    assert atomic.list_items(ACCOUNT) == []


# ---------------------------------------------------------------------------
# ④ 片段守卫：助手消息、窗口外引用、第三人、重复与无法定位
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("window", "fragment"),
    [
        (
            [
                ProfileNeighborMessage(
                    message_id="message-0a",
                    role="assistant",
                    content="我建议你制定一个每天刷 20 道题的学习计划。",
                )
            ],
            _fragment(ref="message-0a", quote="每天刷 20 道题"),
        ),
        (
            [],
            _fragment(ref="message-999", quote="我在读计算机专业"),
        ),
        (
            [
                ProfileNeighborMessage(
                    message_id="message-0", role="user", content="我在读计算机专业，大二。"
                )
            ],
            _fragment(ref="message-0", quote="我在读考古专业"),
        ),
        (
            [
                ProfileNeighborMessage(
                    message_id="message-0",
                    role="user",
                    content="我喜欢羽毛球。我喜欢羽毛球。",
                )
            ],
            _fragment(ref="message-0", quote="我喜欢羽毛球。"),
        ),
        (
            [
                ProfileNeighborMessage(
                    message_id="message-0", role="user", content="我朋友说我喜欢跑步。"
                )
            ],
            _fragment(ref="message-0", quote="我朋友说我喜欢跑步"),
        ),
    ],
    ids=[
        "assistant-message",
        "window-outside-reference",
        "quote-not-in-source",
        "duplicate-quote",
        "third-party",
    ],
)
def test_guarded_fragments_are_deferred_with_zero_write(
    window: list[ProfileNeighborMessage], fragment: dict[str, Any]
) -> None:
    service, atomic, _gateway = _build_service(
        lambda payload: [fragment], window=window
    )

    run = _run_once(service, "message-1", "这个专业我想继续读研。")

    assert run.status == ProfileExtractionStatus.SUCCEEDED
    assert run.outcome == ProfileExtractionOutcome.SUCCEEDED_EMPTY
    assert atomic.list_items(ACCOUNT) == []


# ---------------------------------------------------------------------------
# ⑤ 主引用的邻句/重复消歧
# ---------------------------------------------------------------------------


def test_primary_quote_only_in_neighbor_is_deferred_not_failed() -> None:
    window = [
        ProfileNeighborMessage(
            message_id="message-0", role="user", content="我在读计算机专业，大二。"
        )
    ]
    service, atomic, _gateway = _build_service(
        lambda payload: [
            {
                "normalized_value": "继续读研",
                "fact_text": "想继续读研",
                "evidence_ref": "message-1",
                "evidence_quote": "我在读计算机专业，大二。",
                "reliability": 0.99,
                "action": "create",
            }
        ],
        window=window,
    )

    run = _run_once(service, "message-1", "这个专业我想继续读研。")

    # 暂缓：零写入、不失败、不把邻句原话当成当前消息的证据。
    assert run.status == ProfileExtractionStatus.SUCCEEDED
    assert run.outcome == ProfileExtractionOutcome.SUCCEEDED_EMPTY
    assert atomic.list_items(ACCOUNT) == []


def test_duplicate_primary_quote_with_matching_span_is_written() -> None:
    content = "我叫小林。我叫小林。"

    def responder(payload: dict[str, Any]) -> list[dict[str, Any]]:
        start = content.rfind("我叫小林。")
        return [
            {
                "normalized_value": "我叫小林",
                "fact_text": "我叫小林",
                "evidence_ref": "message-1",
                "evidence_quote": "我叫小林。",
                "evidence_start": start,
                "evidence_end": start + len("我叫小林。"),
                "reliability": 0.99,
                "action": "create",
            }
        ]

    service, atomic, _gateway = _build_service(responder)

    run = _run_once(service, "message-1", content)

    assert run.outcome == ProfileExtractionOutcome.SUCCEEDED_WRITTEN
    items = atomic.list_items(ACCOUNT)
    assert len(items) == 1
    assert items[0].evidence_quote == "我叫小林。"


def test_duplicate_primary_quote_with_mismatched_span_is_deferred() -> None:
    content = "我叫小林。我叫小林。"

    def responder(payload: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "normalized_value": "我叫小林",
                "fact_text": "我叫小林",
                "evidence_ref": "message-1",
                "evidence_quote": "我叫小林。",
                "evidence_start": 0,
                "evidence_end": 4,
                "reliability": 0.99,
                "action": "create",
            }
        ]

    service, atomic, _gateway = _build_service(responder)

    run = _run_once(service, "message-1", content)

    assert run.status == ProfileExtractionStatus.SUCCEEDED
    assert run.outcome == ProfileExtractionOutcome.SUCCEEDED_EMPTY
    assert atomic.list_items(ACCOUNT) == []


# ---------------------------------------------------------------------------
# ⑥ 来源变化/删除后逐来源副本不再展示
# ---------------------------------------------------------------------------


def test_source_quotes_stop_displaying_when_message_changes_or_disappears() -> None:
    window = [
        ProfileNeighborMessage(
            message_id="message-0", role="user", content="我在读计算机专业，大二。"
        )
    ]
    messages = {
        "message-1": _source("message-1", "这个专业我想继续读研。"),
        "message-0": _source("message-0", "我在读计算机专业，大二。"),
    }
    service, atomic, _gateway = _build_service(
        lambda payload: [_fragment(ref="message-0", quote="计算机专业")],
        window=window,
        source_messages=messages,
    )
    run = _run_once(service, "message-1", "这个专业我想继续读研。")
    assert run.outcome == ProfileExtractionOutcome.SUCCEEDED_WRITTEN
    item = atomic.list_items(ACCOUNT)[0]

    messages["message-0"] = _source("message-0", "换了个说法而已。")
    projection = atomic.item_evidence(ACCOUNT, item.profile_item_id)
    changed = next(s for s in projection.sources if s.message_id == "message-0")
    assert changed.status is AtomicProfileEvidenceSourceStatus.AVAILABLE
    assert changed.quote is None
    assert (
        changed.quote_status
        is AtomicProfileEvidenceQuoteStatus.SOURCE_UNAVAILABLE
    )

    messages.pop("message-0")
    projection = atomic.item_evidence(ACCOUNT, item.profile_item_id)
    deleted = next(s for s in projection.sources if s.message_id == "message-0")
    assert deleted.status is AtomicProfileEvidenceSourceStatus.DELETED
    assert deleted.quote is None
    assert (
        deleted.quote_status
        is AtomicProfileEvidenceQuoteStatus.SOURCE_UNAVAILABLE
    )


# ---------------------------------------------------------------------------
# 合同：跨轮片段形状与上限
# ---------------------------------------------------------------------------


def test_fragment_contract_shape_and_limits() -> None:
    base = {
        "normalized_value": "计算机专业",
        "evidence_ref": "message-1",
        "reliability": 0.9,
        "action": "create",
        "evidence_fragments": [
            {"evidence_ref": "message-0", "evidence_quote": "计算机专业"}
        ],
    }
    output = ProfileExtractionOutput.model_validate({"items": [base]})
    assert output.items[0].evidence_fragments is not None
    assert output.items[0].evidence_fragments[0].evidence_quote == "计算机专业"
    # 上限 3 条；超出、空引用、额外字段与空原话都判为无效输出。
    with pytest.raises(ValidationError):
        ProfileExtractionOutput.model_validate(
            {
                "items": [
                    dict(
                        base,
                        evidence_fragments=[
                            {"evidence_ref": f"message-{index}", "evidence_quote": "q"}
                            for index in range(4)
                        ],
                    )
                ]
            }
        )
    with pytest.raises(ValidationError):
        ProfileExtractionOutput.model_validate(
            {
                "items": [
                    dict(
                        base,
                        evidence_fragments=[
                            {"evidence_ref": "", "evidence_quote": "计算机专业"}
                        ],
                    )
                ]
            }
        )
    with pytest.raises(ValidationError):
        ProfileExtractionOutput.model_validate(
            {
                "items": [
                    dict(
                        base,
                        evidence_fragments=[
                            {
                                "evidence_ref": "message-0",
                                "evidence_quote": "计算机专业",
                                "start": 0,
                            }
                        ],
                    )
                ]
            }
        )
    with pytest.raises(ValidationError):
        ProfileExtractionOutput.model_validate(
            {
                "items": [
                    dict(
                        base,
                        evidence_fragments=[
                            {"evidence_ref": "message-0", "evidence_quote": ""}
                        ],
                    )
                ]
            }
        )
