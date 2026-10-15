"""工单 04：自然自述经语义分支形成可恢复长期记忆的集成测试。

覆盖：T01–T03/T02 从调度入口写入、一条消息多项事实、证据核验（原话定位、
否定、时间、条件、取值锚定、主体守卫）、重复事件幂等、重启恢复、取消、
有界重试、账户级背压与真实模型探测的确定性替身路径。

网关替身返回真实形制运行锁：Qwen 分支的来源守卫要求每次真实尝试至少
一条锁，替身不绕过该合同。
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import ValidationError

from bridges.ai import ModelGateway
from bridges.contracts.ai import ModelCallStatus, ModelRunLock
from bridges.contracts.profile_extraction import (
    ProfileExtractionOutcome,
    ProfileExtractionOutput,
    ProfileExtractionSource,
    ProfileExtractionStatus,
)
from bridges.contracts.profiles import FourDimension
from bridges.profiles import (
    AtomicProfileService,
    AutomaticProfileService,
    FourDimensionProfileService,
    GatewayAutomaticProfileExtractor,
    InMemoryAtomicProfileRepository,
    InMemoryAutomaticProfileRepository,
    InMemoryFourDimensionProfileRepository,
    SqliteAtomicProfileRepository,
    SqliteAutomaticProfileRepository,
    SqliteFourDimensionProfileRepository,
)
from bridges.profiles import automatic as automatic_module
from bridges.profiles.automatic import AUTOMATIC_PRIVACY_NOTICE_TEXT
from bridges.storage import BridgesDatabase

ACCOUNT = "account-issue04"
_TRAILING_PUNCTUATION = "，。！？!?,.;；：:、 \t\r\n"
_EVIDENCE_REF_IN_PROMPT = re.compile(r'evidence_ref 固定填写 "([^"]+)"')


def _message_id_from(payload: dict[str, Any]) -> str:
    for message in payload.get("messages") or []:
        if str(message.get("role")) != "system":
            continue
        match = _EVIDENCE_REF_IN_PROMPT.search(str(message.get("content") or ""))
        if match is not None:
            return match.group(1)
    return ""


def _current_content(payload: dict[str, Any]) -> str:
    messages = payload.get("messages") or []
    return str(messages[-1].get("content") or "").strip() if messages else ""


def _echo_item(
    payload: dict[str, Any],
    *,
    value: str | None = None,
    fact_text: str | None = None,
    quote: str | None = None,
    dimension: FourDimension | None = None,
    action: str = "create",
) -> dict[str, Any]:
    current = _current_content(payload)
    resolved_value = value if value is not None else current.strip(_TRAILING_PUNCTUATION)
    item: dict[str, Any] = {
        "normalized_value": resolved_value,
        "fact_text": fact_text if fact_text is not None else resolved_value,
        "evidence_ref": _message_id_from(payload),
        "evidence_quote": quote if quote is not None else current,
        "reliability": 0.99,
        "action": action,
    }
    if dimension is not None:
        item["dimension"] = dimension.value
    return item


def _echo_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    item = _echo_item(payload)
    return [item] if item["normalized_value"] else []


def _lock(
    call: int,
    *,
    status: ModelCallStatus = ModelCallStatus.SUCCESS,
    error_code: str | None = None,
) -> ModelRunLock:
    return ModelRunLock(
        lock_id=f"fake-lock-{call}",
        run_id="run-issue04",
        account_id=ACCOUNT,
        project_id="conversation-1",
        capability_name="qwen_profile_extraction",
        capability_version="1",
        actual_model_id="qwen3.7-plus-2026-05-26",
        region="cn-beijing",
        parameters={"temperature": 0, "max_tokens": 512},
        prompt_version="profile-extraction-prompt-v5",
        input_output_contract=(
            "qwen_profile_extraction:profile-message-v3->profile-extraction-v3"
        ),
        status=status,
        error_code=error_code,
        created_at=datetime.now(UTC),
    )


class _RespondingGateway:
    """按 responder 生成候选并返回真实形制锁的确定性假网关。"""

    def __init__(
        self,
        responder: Any = _echo_items,
        *,
        fail_once: str | None = None,
    ) -> None:
        self.calls = 0
        self.payloads: list[dict[str, Any]] = []
        self._responder = responder
        self._fail_once = fail_once

    def invoke(self, *args: object, **kwargs: object) -> SimpleNamespace:
        del args
        self.calls += 1
        payload = kwargs.get("payload")
        payload_dict = payload if isinstance(payload, dict) else {}
        self.payloads.append(payload_dict)
        if self._fail_once and self.calls == 1:
            return SimpleNamespace(
                status=ModelCallStatus.RETRYABLE_FAIL,
                output=None,
                error_code=self._fail_once,
                error_message="transient failure",
                lock=_lock(
                    self.calls,
                    status=ModelCallStatus.RETRYABLE_FAIL,
                    error_code=self._fail_once,
                ),
            )
        return SimpleNamespace(
            status=ModelCallStatus.SUCCESS,
            output={"items": self._responder(payload_dict)},
            error_code=None,
            error_message=None,
            lock=_lock(self.calls),
        )


def _memory_service(
    gateway: _RespondingGateway,
) -> tuple[AutomaticProfileService, AtomicProfileService]:
    four = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=InMemoryFourDimensionProfileRepository(),
    )
    atomic = AtomicProfileService(four, InMemoryAtomicProfileRepository())
    service = AutomaticProfileService(
        four_dimension_service=four,
        repository=InMemoryAutomaticProfileRepository(),
        extractor=GatewayAutomaticProfileExtractor(cast(ModelGateway, gateway)),
        atomic_profile_service=atomic,
    )
    return service, atomic


def _sqlite_service(
    database: BridgesDatabase, gateway: _RespondingGateway
) -> tuple[AutomaticProfileService, AtomicProfileService]:
    four = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=SqliteFourDimensionProfileRepository(database),
    )
    atomic = AtomicProfileService(
        four, SqliteAtomicProfileRepository(database)
    )
    service = AutomaticProfileService(
        four_dimension_service=four,
        repository=SqliteAutomaticProfileRepository(database),
        extractor=GatewayAutomaticProfileExtractor(cast(ModelGateway, gateway)),
        atomic_profile_service=atomic,
    )
    return service, atomic


def _schedule(
    service: AutomaticProfileService, message_id: str, content: str
):  # noqa: ANN202 - 返回 run 或 None
    return service.schedule_message_extraction(
        ACCOUNT,
        conversation_id="conversation-1",
        message_id=message_id,
        content=content,
        run_id=f"run-{message_id}",
    )


def _drain(service: AutomaticProfileService, *, max_ticks: int = 8) -> list[str]:
    results: list[str] = []
    for _ in range(max_ticks):
        result = service.run_retry_tick()
        results.append(result)
        if "无待处理任务" in result:
            break
    return results


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


# ---------------------------------------------------------------------------
# T01–T03 / T02：从调度入口形成长期记忆
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("content", "needle"),
    [
        ("我叫小林。", "小林"),
        ("我养了一只猫叫团子。", "团子"),
        ("周末一般会去爬山。", "爬山"),
        ("我喜欢心理学。", "心理学"),
        ("我在学线性代数，每晚只能抽出半小时。", "半小时"),
    ],
)
def test_natural_self_descriptions_are_saved_from_scheduling_entry(
    content: str, needle: str
) -> None:
    gateway = _RespondingGateway()
    service, atomic = _memory_service(gateway)

    run = _run_once(service, "message-1", content)

    assert gateway.calls == 1
    assert run.status == ProfileExtractionStatus.SUCCEEDED
    assert run.source == ProfileExtractionSource.QWEN_MODEL
    assert run.outcome == ProfileExtractionOutcome.SUCCEEDED_WRITTEN
    items = atomic.list_items(ACCOUNT)
    assert any(needle in item.text for item in items)
    assert any(item.evidence_quote for item in items)


def test_one_message_supports_multiple_facts() -> None:
    content = "我叫小林，我养了一只猫叫团子。"

    def responder(payload: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            _echo_item(payload, value="我叫小林", quote="我叫小林"),
            _echo_item(payload, value="我养了一只猫叫团子", quote="我养了一只猫叫团子"),
        ]

    gateway = _RespondingGateway(responder)
    service, atomic = _memory_service(gateway)

    run = _run_once(service, "message-1", content)

    assert run.outcome == ProfileExtractionOutcome.SUCCEEDED_WRITTEN
    texts = {item.text for item in atomic.list_items(ACCOUNT)}
    assert texts == {"我叫小林", "我养了一只猫叫团子"}


def test_repeated_quote_without_disambiguation_is_deferred() -> None:
    """工单 05：原话重复且无消歧区间时暂缓（零写入、不失败、不随意取位置）。"""

    content = "我叫小林。我叫小林。"

    def responder(payload: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            _echo_item(payload, value="我叫小林", quote="我叫小林。"),
        ]

    gateway = _RespondingGateway(responder)
    service, atomic = _memory_service(gateway)

    run = _run_once(service, "message-1", content)

    assert run.status == ProfileExtractionStatus.SUCCEEDED
    assert run.outcome == ProfileExtractionOutcome.SUCCEEDED_EMPTY
    assert atomic.list_items(ACCOUNT) == []


def test_dimension_is_an_optional_hint_in_contract() -> None:
    open_fact = {
        "normalized_value": "团子",
        "evidence_ref": "message-1",
        "reliability": 0.9,
        "action": "create",
    }
    output = ProfileExtractionOutput.model_validate({"items": [open_fact]})
    assert output.items[0].dimension is None
    with pytest.raises(ValidationError):
        ProfileExtractionOutput.model_validate(
            {"items": [dict(open_fact, dimension="星座")]}
        )
    with pytest.raises(ValidationError):
        ProfileExtractionOutput.model_validate(
            {"items": [dict(open_fact, normalized_value=f"值{index}") for index in range(7)]}
        )


# ---------------------------------------------------------------------------
# 边界守卫：琐事、行为推测、敏感自述与短期情绪不自动长期化
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content",
    [
        "我朋友说我喜欢跑步。",
        "刚才的助手总结说我在准备考研。",
        "我最近很焦虑。",
    ],
)
def test_hard_forbidden_messages_never_schedule_or_call_gateway(content: str) -> None:
    gateway = _RespondingGateway()
    service, atomic = _memory_service(gateway)

    run = _schedule(service, "message-1", content)

    assert run is None
    assert gateway.calls == 0
    assert atomic.list_items(ACCOUNT) == []


@pytest.mark.parametrize(
    "content",
    [
        "今天好累。",
        "今天天气不错。",
    ],
)
def test_transient_and_trivia_candidates_stay_zero_write(content: str) -> None:
    gateway = _RespondingGateway()
    service, atomic = _memory_service(gateway)

    run = _run_once(service, "message-1", content)

    # 语义替身提出整句候选，但主体/证据守卫拒绝：零写入、无可记事实。
    assert gateway.calls == 1
    assert run.status == ProfileExtractionStatus.SUCCEEDED
    assert run.outcome == ProfileExtractionOutcome.SUCCEEDED_EMPTY
    assert atomic.list_items(ACCOUNT) == []


def test_ambiguous_span_candidate_is_downgraded_to_observation() -> None:
    content = "我可能想学习摄影。"

    def responder(payload: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            _echo_item(
                payload,
                value="摄影",
                fact_text="想学习摄影",
                quote=content,
                dimension=FourDimension.KNOWLEDGE_INTEREST,
            )
        ]

    gateway = _RespondingGateway(responder)
    service, atomic = _memory_service(gateway)

    run = _run_once(service, "message-1", content)

    assert run.outcome == ProfileExtractionOutcome.SUCCEEDED_OBSERVED
    assert run.observed_count == 1
    assert atomic.list_items(ACCOUNT) == []


def test_third_party_clause_inside_self_message_stays_zero_write() -> None:
    content = "以后先给结论，老王在研究量子计算。"

    def responder(payload: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            _echo_item(
                payload,
                value="量子计算",
                fact_text="老王在研究量子计算",
                quote="老王在研究量子计算",
            )
        ]

    gateway = _RespondingGateway(responder)
    service, atomic = _memory_service(gateway)

    run = _run_once(service, "message-1", content)

    assert run.outcome == ProfileExtractionOutcome.SUCCEEDED_EMPTY
    assert atomic.list_items(ACCOUNT) == []


# ---------------------------------------------------------------------------
# 证据核验：原话定位、否定、时间、条件与取值锚定
# ---------------------------------------------------------------------------


def test_quote_not_found_is_a_permanent_contract_failure() -> None:
    content = "我叫小林。"

    def responder(payload: dict[str, Any]) -> list[dict[str, Any]]:
        return [_echo_item(payload, value="小林", quote="完全不存在的原话")]

    gateway = _RespondingGateway(responder)
    service, atomic = _memory_service(gateway)

    run = _run_once(service, "message-1", content)

    assert run.status == ProfileExtractionStatus.EXHAUSTED
    assert run.last_error == "profile_extraction_evidence_mismatch"
    assert run.outcome == ProfileExtractionOutcome.PERMANENT_FAILURE
    assert atomic.list_items(ACCOUNT) == []


def test_time_constraint_must_be_preserved_in_candidate() -> None:
    content = "我在学线性代数，每晚只能抽出半小时。"

    def responder(payload: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            _echo_item(
                payload,
                value="线性代数",
                fact_text="正在学习线性代数",
                quote="每晚只能抽出半小时",
            )
        ]

    gateway = _RespondingGateway(responder)
    service, atomic = _memory_service(gateway)

    run = _run_once(service, "message-1", content)

    assert run.outcome == ProfileExtractionOutcome.SUCCEEDED_EMPTY
    assert atomic.list_items(ACCOUNT) == []


def test_duration_equivalence_accepts_half_hour_as_thirty_minutes() -> None:
    content = "我在学线性代数，每晚只能抽出半小时。"

    def responder(payload: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            _echo_item(
                payload,
                value="每晚只能抽出半小时",
                fact_text="每晚只能学习线性代数约 30 分钟",
                quote=content,
            )
        ]

    gateway = _RespondingGateway(responder)
    service, atomic = _memory_service(gateway)

    run = _run_once(service, "message-1", content)

    assert run.outcome == ProfileExtractionOutcome.SUCCEEDED_WRITTEN
    assert any("30 分钟" in item.text for item in atomic.list_items(ACCOUNT))


def test_candidate_cannot_add_negation_absent_from_evidence() -> None:
    content = "我喜欢跑步。"

    def responder(payload: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            _echo_item(
                payload,
                value="不喜欢跑步",
                fact_text="我不喜欢跑步",
                quote="我喜欢跑步",
            )
        ]

    gateway = _RespondingGateway(responder)
    service, atomic = _memory_service(gateway)

    run = _run_once(service, "message-1", content)

    assert run.outcome == ProfileExtractionOutcome.SUCCEEDED_EMPTY
    assert atomic.list_items(ACCOUNT) == []


def test_condition_clause_must_be_preserved_in_candidate() -> None:
    content = "只要不下雨，我每周五都去跑步。"

    def responder(payload: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            _echo_item(
                payload,
                value="每周五都去跑步",
                fact_text="每周五都去跑步",
                quote=content,
            )
        ]

    gateway = _RespondingGateway(responder)
    service, atomic = _memory_service(gateway)

    run = _run_once(service, "message-1", content)

    assert run.outcome == ProfileExtractionOutcome.SUCCEEDED_EMPTY
    assert atomic.list_items(ACCOUNT) == []


# ---------------------------------------------------------------------------
# 恢复、幂等、取消、背压
# ---------------------------------------------------------------------------


def test_transient_failure_retries_bounded_and_does_not_duplicate() -> None:
    gateway = _RespondingGateway(fail_once="network_error")
    service, atomic = _memory_service(gateway)

    run = _run_once(service, "message-1", "我叫小林。")

    assert gateway.calls == 2
    assert run.status == ProfileExtractionStatus.SUCCEEDED
    assert run.outcome == ProfileExtractionOutcome.SUCCEEDED_WRITTEN
    assert len(atomic.list_items(ACCOUNT)) == 1

    # 重复完成事件：幂等返回既有 run，不重复调用、不新增副本。
    replay = _schedule(service, "message-1", "我叫小林。")
    assert replay is not None
    assert replay.extraction_id == run.extraction_id
    assert gateway.calls == 2
    assert len(atomic.list_items(ACCOUNT)) == 1


def test_restart_replays_idempotently_without_loss_or_duplication(tmp_path) -> None:
    database_path = tmp_path / "issue04-restart.db"
    first_database = BridgesDatabase(database_path)
    first_database.initialize()
    first_gateway = _RespondingGateway()
    first_service, first_atomic = _sqlite_service(first_database, first_gateway)
    _run_once(first_service, "message-1", "我叫小林。")
    assert len(first_atomic.list_items(ACCOUNT)) == 1
    first_database.close()

    second_database = BridgesDatabase(database_path)
    second_gateway = _RespondingGateway()
    second_service, second_atomic = _sqlite_service(second_database, second_gateway)
    replay = _schedule(second_service, "message-1", "我叫小林。")

    assert replay is not None
    assert replay.status == ProfileExtractionStatus.SUCCEEDED
    assert second_gateway.calls == 0
    assert len(second_atomic.list_items(ACCOUNT)) == 1
    second_database.close()


def test_tombstone_before_processing_prevents_late_write() -> None:
    gateway = _RespondingGateway()
    service, atomic = _memory_service(gateway)

    run = _schedule(service, "message-1", "我叫小林。")
    assert run is not None
    service.mark_message_tombstone(ACCOUNT, "message-1")
    _drain(service)

    refreshed = service._repository.get_run(  # type: ignore[attr-defined]
        ACCOUNT, "message-1", service.extractor_version, run.source_hash
    )
    assert refreshed is not None
    assert refreshed.status == ProfileExtractionStatus.EXHAUSTED
    assert refreshed.last_error == "profile_extraction_source_invalidated"
    assert gateway.calls == 0
    assert atomic.list_items(ACCOUNT) == []


def test_user_delete_is_not_revived_by_replay() -> None:
    gateway = _RespondingGateway()
    service, atomic = _memory_service(gateway)

    _run_once(service, "message-1", "我叫小林。")
    items = atomic.list_items(ACCOUNT)
    assert len(items) == 1
    atomic.delete_item(ACCOUNT, items[0].profile_item_id, items[0].version)

    replay = _schedule(service, "message-1", "我叫小林。")

    assert replay is not None
    assert gateway.calls == 1
    assert atomic.list_items(ACCOUNT) == []


def test_account_backpressure_skips_new_scheduling(monkeypatch) -> None:
    monkeypatch.setattr(
        automatic_module, "PROFILE_EXTRACTION_MAX_PENDING_PER_ACCOUNT", 1
    )
    gateway = _RespondingGateway()
    service, atomic = _memory_service(gateway)

    first = _schedule(service, "message-1", "我叫小林。")
    assert first is not None
    blocked = _schedule(service, "message-2", "我养了一只猫叫团子。")
    assert blocked is None

    _drain(service)
    third = _schedule(service, "message-3", "周末一般会去爬山。")
    assert third is not None

    _drain(service)
    texts = {item.text for item in atomic.list_items(ACCOUNT)}
    assert any("小林" in text for text in texts)
    assert any("爬山" in text for text in texts)


def test_privacy_notice_discloses_semantic_branch_honestly() -> None:
    assert "本机规则" in AUTOMATIC_PRIVACY_NOTICE_TEXT
    assert "Qwen" in AUTOMATIC_PRIVACY_NOTICE_TEXT
    assert "语义" in AUTOMATIC_PRIVACY_NOTICE_TEXT
    assert "明确的自我描述、学习目标、行为观察和更正由本机规则识别" not in (
        AUTOMATIC_PRIVACY_NOTICE_TEXT
    )
