"""Issue 11：Humanizer 真实 Qwen 模型运行锁审计闭环测试。

用可编程假适配器 + 临时 SQLite 统一锁仓库（``SqliteModelRunLockRecorder``）
驱动完整文章服务，断言每次真实模型调用都有独立、持久化、可关联的运行锁：

- 单次首稿恰好一条锁；首稿 + 修订恰好两条不同锁，顺序稳定、重启后可查；
- 修订未触发/停止/预算不足只保留首稿锁；修订失败保留首稿成功锁与修订
  失败锁；空输出与本地复核失败都保留已发生的供应商调用锁；
- 锁状态与业务终态互相独立，不互相冒充；
- 同一记录事件幂等重放不产生重复锁；恢复发起的新调用以新锁保存；
- 跨账户隔离；recorder 写失败/缺锁/业务关联不符一律失败关闭；
- 锁只保存脱敏白名单字段，不保存 prompt/正文/Key。
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bridges.ai import CapabilityRegistry, ModelGateway
from bridges.ai.adapters import AdapterError, AdapterResult, RateLimitError
from bridges.ai.errors import ModelRunLockPersistError
from bridges.ai.fixed_models import CHAT_MODEL_ID
from bridges.ai.ports import ModelRunLockRecorder, RecordRequest
from bridges.ai.sqlite_recorder import SqliteModelRunLockRecorder
from bridges.chat.budget import RunBudget
from bridges.contracts.ai import (
    BusinessRef,
    CapabilityKind,
    CapabilityRecord,
    CapabilityStatus,
    ModelCallStatus,
    ModelRunLock,
    PersistedModelRunLock,
    RetryPolicy,
)
from bridges.contracts.humanizer import (
    HumanizerPath,
    HumanizerProcessState,
    HumanizerResultStatus,
    HumanizerSkillInput,
    HumanizerTaskContract,
)
from bridges.contracts.workflows import ObjectDomain, RunContextEnvelope
from bridges.skills.humanizer.draft_compiler import DRAFT_MAX_TOKENS
from bridges.skills.humanizer.intent import route_humanizer_message
from bridges.skills.humanizer.revision_prompt import REVISION_MAX_TOKENS
from bridges.skills.humanizer.service import (
    HUMANIZER_LOCK_BUSINESS_MISMATCH,
    HUMANIZER_LOCK_PERSIST_FAILED,
    HUMANIZER_MISSING_RUN_LOCK,
    HUMANIZER_STAGE_DRAFT,
    HUMANIZER_STAGE_REVISION,
    HumanizerService,
)
from bridges.skills.registry import create_builtin_registry
from bridges.storage import BridgesDatabase

_SOURCE = (
    "番茄工作法把时间切成 25 分钟的工作块和 5 分钟的休息块。"
    "四个工作块后休息 15 分钟。"
)
_TEMPLATED = (
    "本助手认为，总而言之，番茄工作法把时间切成 25 分钟的工作块"
    "和 5 分钟的休息块，四个工作块后休息 15 分钟。未来可期。"
)
_FIXED = (
    "把时间切成 25 分钟的工作块和 5 分钟的休息块，四个工作块后"
    "休息 15 分钟，这就是番茄工作法的大致框架。"
)


def _structured_capability() -> CapabilityRecord:
    # 固定模型 ID 只来自 bridges.ai.fixed_models 单一事实源（Issue 09）。
    return CapabilityRecord(
        name="qwen_structured_output",
        version="1",
        kind=CapabilityKind.MODEL,
        vendor="qwen",
        region="cn-beijing",
        model_id=CHAT_MODEL_ID,
        input_schema_version="structured-messages-v1",
        output_schema_version="json-schema-v1",
        status=CapabilityStatus.VERIFIED,
        retry_policy=RetryPolicy(max_attempts=1, backoff_seconds=0),
    )


class _SequencedAdapter:
    """按序返回输出/抛错的可编程适配器：记录调用次数与载荷。"""

    def __init__(
        self,
        outputs: list[dict[str, Any] | Exception],
        usage: dict[str, Any] | None = None,
    ) -> None:
        self._outputs = list(outputs)
        self._usage = usage
        self.calls = 0
        self.payloads: list[dict[str, Any]] = []

    def call(
        self,
        capability: CapabilityRecord,
        run_context: Any,
        payload: dict[str, Any],
    ) -> AdapterResult:
        self.calls += 1
        self.payloads.append(payload)
        step = self._outputs[min(self.calls - 1, len(self._outputs) - 1)]
        if isinstance(step, Exception):
            raise step
        return AdapterResult(
            actual_model_id=capability.model_id, output=step, usage=self._usage
        )


def _gateway_with(adapter: Any) -> ModelGateway:
    registry = CapabilityRegistry()
    registry.register(_structured_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_structured_output", "1", adapter)
    return gateway


def _run_context(
    run_id: str = "run-test", account_id: str = "acc-test"
) -> RunContextEnvelope:
    return RunContextEnvelope(
        run_id=run_id,
        account_id=account_id,
        project_id="conv-test",
        workflow_name="chat",
        workflow_version="1",
        object_domain=ObjectDomain.PERSONAL_VAULT,
        submitted_at=datetime.now(UTC),
    )


def _expression_input() -> Any:
    routed = route_humanizer_message(f"帮我改写这段话：{_SOURCE}")
    assert routed is not None
    return routed.skill_input


def _legacy_generate_input() -> HumanizerSkillInput:
    return HumanizerSkillInput(
        skill_id="bridges-humanizer",
        contract=HumanizerTaskContract(
            path=HumanizerPath.GENERATE,
            topic="时间管理",
        ),
    )


def _run(
    service: HumanizerService,
    skill_input: Any,
    *,
    account_id: str = "acc-test",
    run_id: str = "run-test",
    writing_call_count: int = 0,
    recovered_draft: str | None = None,
    stop_event: threading.Event | None = None,
    budget: RunBudget | None = None,
) -> tuple[list[Any], Any]:
    events = list(
        service.run_task(
            account_id,
            "conv-test",
            "msg-test",
            skill_input,
            _run_context(run_id=run_id, account_id=account_id),
            writing_call_count=writing_call_count,
            recovered_draft=recovered_draft,
            stop_event=stop_event,
            budget=budget,
        )
    )
    result = next(e.result for e in events if e.kind == "result")
    return events, result


def _draft(final: str) -> dict[str, Any]:
    return {"final_text": final}


@pytest.fixture
def database(tmp_path: Path) -> BridgesDatabase:
    db = BridgesDatabase(tmp_path / "bridges.db")
    db.initialize()
    return db


@pytest.fixture
def recorder(database: BridgesDatabase) -> SqliteModelRunLockRecorder:
    return SqliteModelRunLockRecorder(database)


def _make_service(adapter: Any, recorder: ModelRunLockRecorder | None) -> HumanizerService:
    return HumanizerService(
        registry=create_builtin_registry(),
        gateway=_gateway_with(adapter),
        run_lock_recorder=recorder,
    )


# ---------------------------------------------------------------------------
# 首稿与修订的锁数量、阶段、顺序与关联
# ---------------------------------------------------------------------------


def test_single_draft_persists_exactly_one_lock(
    recorder: SqliteModelRunLockRecorder,
) -> None:
    adapter = _SequencedAdapter([_draft(_FIXED)])
    _, result = _run(_make_service(adapter, recorder), _expression_input())
    assert adapter.calls == 1
    assert result.status == HumanizerResultStatus.DONE

    locks = recorder.list_locks_by_run("acc-test", "run-test")
    assert len(locks) == 1
    lock = locks[0]
    assert lock.capability_name == "qwen_structured_output"
    assert lock.capability_version == "1"
    # 固定模型 ID 来自单一事实源
    assert lock.actual_model_id == CHAT_MODEL_ID
    assert lock.status == ModelCallStatus.SUCCESS
    # 阶段与调用序号：humanizer_draft:1
    message_refs = [
        ref for ref in lock.business_refs if ref.object_type == "message"
    ]
    conversation_refs = [
        ref for ref in lock.business_refs if ref.object_type == "conversation"
    ]
    assert len(message_refs) == 1
    assert message_refs[0].object_id == "msg-test"
    assert message_refs[0].operation == HUMANIZER_STAGE_DRAFT
    assert message_refs[0].attempt_ordinal == 1
    assert message_refs[0].is_primary is True
    assert len(conversation_refs) == 1
    assert conversation_refs[0].object_id == "conv-test"
    assert conversation_refs[0].operation == HUMANIZER_STAGE_DRAFT
    assert conversation_refs[0].attempt_ordinal == 1
    # 锁记录 run 标识：按 account_id + run_id 可查
    assert lock.run_id == "run-test"
    assert lock.account_id == "acc-test"
    # 投影只保存主要锁引用与业务 run 引用
    assert result.run_lock_id == lock.lock_id
    assert result.model_run_id == "run-test"
    # 参数只含白名单字段（temperature/max_tokens），无 prompt/正文
    assert lock.parameters == {"temperature": 0.4, "max_tokens": DRAFT_MAX_TOKENS}


def test_draft_and_revision_persist_two_distinct_locks(
    database: BridgesDatabase, recorder: SqliteModelRunLockRecorder
) -> None:
    usage = {"prompt_tokens": 120, "completion_tokens": 60}
    adapter = _SequencedAdapter([_draft(_TEMPLATED), _draft(_FIXED)], usage=usage)
    _, result = _run(_make_service(adapter, recorder), _expression_input())
    assert adapter.calls == 2
    assert result.status == HumanizerResultStatus.DONE
    assert result.output is not None and result.output.final_text == _FIXED
    assert result.writing_call_count == 2

    locks = recorder.list_locks_by_run("acc-test", "run-test")
    assert len(locks) == 2
    assert locks[0].lock_id != locks[1].lock_id
    # 顺序稳定：首稿（ordinal 1）在前，修订（ordinal 2）在后
    assert locks[0].business_refs[0].operation == HUMANIZER_STAGE_DRAFT
    assert locks[0].business_refs[0].attempt_ordinal == 1
    assert locks[1].business_refs[0].operation == HUMANIZER_STAGE_REVISION
    assert locks[1].business_refs[0].attempt_ordinal == 2
    assert locks[0].status == ModelCallStatus.SUCCESS
    assert locks[1].status == ModelCallStatus.SUCCESS
    # 修订锁携带 usage 与修订参数
    assert locks[1].usage == usage
    assert locks[1].parameters == {"temperature": 0.3, "max_tokens": REVISION_MAX_TOKENS}
    # 终态投影引用首稿锁，不覆盖、不删除首稿锁
    assert result.run_lock_id == locks[0].lock_id
    assert result.model_run_id == "run-test"
    # 同一 run 恰好两条锁（不折叠为一条）
    rows = database.scoped("acc-test").execute(
        "SELECT COUNT(*) AS n FROM model_run_locks WHERE account_id = ?",
        ("acc-test",),
    ).fetchone()
    assert int(rows["n"]) == 2


def test_no_revision_trigger_keeps_only_draft_lock(
    recorder: SqliteModelRunLockRecorder,
) -> None:
    adapter = _SequencedAdapter([_draft(_FIXED)])
    _, result = _run(_make_service(adapter, recorder), _expression_input())
    assert adapter.calls == 1
    assert result.revision is not None and result.revision.triggered is False
    locks = recorder.list_locks_by_run("acc-test", "run-test")
    assert len(locks) == 1
    assert locks[0].business_refs[0].operation == HUMANIZER_STAGE_DRAFT


def test_user_stopped_before_revision_keeps_only_draft_lock(
    recorder: SqliteModelRunLockRecorder,
) -> None:
    stop_event = threading.Event()
    stop_event.set()
    adapter = _SequencedAdapter([_draft(_TEMPLATED)])
    _, result = _run(
        _make_service(adapter, recorder), _expression_input(), stop_event=stop_event
    )
    assert adapter.calls == 1
    assert result.revision is not None
    assert result.revision.skipped_reason == "user_stopped"
    locks = recorder.list_locks_by_run("acc-test", "run-test")
    assert len(locks) == 1
    assert locks[0].business_refs[0].operation == HUMANIZER_STAGE_DRAFT


def test_budget_insufficient_keeps_only_draft_lock(
    recorder: SqliteModelRunLockRecorder,
) -> None:
    budget = RunBudget("run-budget", total_ms=1_000)
    adapter = _SequencedAdapter([_draft(_TEMPLATED)])
    _, result = _run(
        _make_service(adapter, recorder), _expression_input(), budget=budget
    )
    assert adapter.calls == 1
    assert result.revision is not None
    assert result.revision.skipped_reason == "budget_insufficient"
    locks = recorder.list_locks_by_run("acc-test", "run-test")
    assert len(locks) == 1
    # 不伪造修订锁
    assert locks[0].business_refs[0].operation == HUMANIZER_STAGE_DRAFT


# ---------------------------------------------------------------------------
# 失败/空输出/本地复核失败也必须保留真实调用锁
# ---------------------------------------------------------------------------


def test_draft_failure_persists_failed_lock(
    recorder: SqliteModelRunLockRecorder,
) -> None:
    adapter = _SequencedAdapter([RateLimitError("临时失败")])
    _, result = _run(_make_service(adapter, recorder), _expression_input())
    assert result.status == HumanizerResultStatus.ERROR
    assert result.error_code == "rate_limit"
    locks = recorder.list_locks_by_run("acc-test", "run-test")
    assert len(locks) == 1
    assert locks[0].status == ModelCallStatus.RETRYABLE_FAIL
    assert locks[0].error_code == "rate_limit"
    assert locks[0].business_refs[0].operation == HUMANIZER_STAGE_DRAFT


def test_draft_permanent_failure_records_blocked_lock(
    recorder: SqliteModelRunLockRecorder,
) -> None:
    adapter = _SequencedAdapter([AdapterError(code="auth_error", message="凭据无效")])
    _, result = _run(_make_service(adapter, recorder), _expression_input())
    assert result.status == HumanizerResultStatus.ERROR
    assert result.error_code == "auth_error"
    locks = recorder.list_locks_by_run("acc-test", "run-test")
    assert len(locks) == 1
    assert locks[0].status == ModelCallStatus.BLOCKED
    assert locks[0].error_code == "auth_error"


def test_revision_failure_keeps_draft_success_and_revision_failed_locks(
    recorder: SqliteModelRunLockRecorder,
) -> None:
    adapter = _SequencedAdapter([_draft(_TEMPLATED), RateLimitError("临时失败")])
    _, result = _run(_make_service(adapter, recorder), _expression_input())
    assert adapter.calls == 2
    # 交付语义不变：修订失败 → 交付首稿的真实检查状态
    assert result.status == HumanizerResultStatus.DONE
    assert result.output is not None
    assert result.output.final_text == _TEMPLATED
    assert result.revision is not None
    assert result.revision.skipped_reason == "model_error:rate_limit"
    locks = recorder.list_locks_by_run("acc-test", "run-test")
    assert len(locks) == 2
    assert locks[0].status == ModelCallStatus.SUCCESS
    assert locks[0].business_refs[0].operation == HUMANIZER_STAGE_DRAFT
    assert locks[1].status == ModelCallStatus.RETRYABLE_FAIL
    assert locks[1].error_code == "rate_limit"
    assert locks[1].business_refs[0].operation == HUMANIZER_STAGE_REVISION
    # 失败锁的错误信息不含正文
    assert _SOURCE not in (locks[1].error_message or "")
    assert _TEMPLATED not in (locks[1].error_message or "")


def test_empty_output_keeps_lock_with_success_status(
    recorder: SqliteModelRunLockRecorder,
) -> None:
    adapter = _SequencedAdapter([{}])
    _, result = _run(_make_service(adapter, recorder), _expression_input())
    assert result.status == HumanizerResultStatus.ERROR
    assert result.error_code == "empty_output"
    locks = recorder.list_locks_by_run("acc-test", "run-test")
    assert len(locks) == 1
    # 供应商调用真实发生（网关 SUCCESS），空输出是业务层判定——
    # 锁状态（SUCCESS）与业务终态（ERROR）互不冒充
    assert locks[0].status == ModelCallStatus.SUCCESS


class _DegradedGateway:
    """网关替身：把成功结果改写为 DEGRADED（锁与输出保留）。"""

    def __init__(self, inner: ModelGateway) -> None:
        self._inner = inner

    def invoke(self, *args: Any, **kwargs: Any) -> Any:
        result = self._inner.invoke(*args, **kwargs)
        if result.status != ModelCallStatus.SUCCESS:
            return result
        degraded_lock = result.lock.model_copy(
            update={"status": ModelCallStatus.DEGRADED}
        )
        return result.model_copy(
            update={"status": ModelCallStatus.DEGRADED, "lock": degraded_lock}
        )


def test_degraded_draft_keeps_degraded_lock(
    recorder: SqliteModelRunLockRecorder,
) -> None:
    """降级成功同样保留锁：锁状态如实为 degraded，业务按既有语义交付。"""
    adapter = _SequencedAdapter([_draft(_FIXED)])
    gateway = _DegradedGateway(_gateway_with(adapter))
    service = HumanizerService(
        registry=create_builtin_registry(),
        gateway=gateway,
        run_lock_recorder=recorder,
    )
    _, result = _run(service, _expression_input())
    assert result.status == HumanizerResultStatus.DONE
    locks = recorder.list_locks_by_run("acc-test", "run-test")
    assert len(locks) == 1
    assert locks[0].status == ModelCallStatus.DEGRADED
    assert locks[0].business_refs[0].operation == HUMANIZER_STAGE_DRAFT


def test_parse_failure_keeps_lock_of_real_call(
    recorder: SqliteModelRunLockRecorder,
) -> None:
    """结构校验失败（输出合同不完整）：已发生的供应商调用仍有对应锁。

    网关只接受 dict 输出（合同边界），服务层的解析失败形态是结构校验
    不通过——legacy 路径下 malformed edits 被收窄为空清单，输出合同
    完整性门以 output_contract_incomplete 拒绝，但锁在解析前已落库。
    """
    adapter = _SequencedAdapter(
        [
            {
                "final_text": "时间管理的关键是先列清单，再排优先级。",
                "edits": "not-a-list",
                "fact_check": [],
                "open_questions": [],
            }
        ]
    )
    _, result = _run(_make_service(adapter, recorder), _legacy_generate_input())
    assert adapter.calls == 1
    assert result.status == HumanizerResultStatus.ERROR
    assert result.error_code == "output_contract_incomplete"
    locks = recorder.list_locks_by_run("acc-test", "run-test")
    assert len(locks) == 1
    # 供应商调用真实发生（网关 SUCCESS），业务层结构校验失败不抹除锁
    assert locks[0].status == ModelCallStatus.SUCCESS
    assert locks[0].business_refs[0].operation == HUMANIZER_STAGE_DRAFT


def test_local_review_failure_keeps_locks_distinct_from_business_terminal(
    recorder: SqliteModelRunLockRecorder,
) -> None:
    violating = _draft(_SOURCE.replace("25 分钟", "35 分钟"))
    adapter = _SequencedAdapter([violating, violating])
    _, result = _run(_make_service(adapter, recorder), _expression_input())
    assert adapter.calls == 2
    # 本地保真硬门失败：业务终态 ERROR/停止交付
    assert result.status == HumanizerResultStatus.ERROR
    assert result.error_code == "fidelity_gate_conflict"
    assert result.output is None
    # 两次真实调用都保留锁（锁是调用证据，不是业务结论）
    locks = recorder.list_locks_by_run("acc-test", "run-test")
    assert len(locks) == 2
    assert all(lock.status == ModelCallStatus.SUCCESS for lock in locks)
    assert locks[0].business_refs[0].operation == HUMANIZER_STAGE_DRAFT
    assert locks[1].business_refs[0].operation == HUMANIZER_STAGE_REVISION


# ---------------------------------------------------------------------------
# 幂等、恢复与重启
# ---------------------------------------------------------------------------


def test_locks_survive_process_restart(tmp_path: Path) -> None:
    """锁落在 SQLite 文件：模拟「模型成功后、结果投影前崩溃」，重启后可查。"""
    db_path = tmp_path / "bridges.db"
    first_db = BridgesDatabase(db_path)
    first_db.initialize()
    first_recorder = SqliteModelRunLockRecorder(first_db)
    adapter = _SequencedAdapter([_draft(_FIXED)])
    _, result = _run(_make_service(adapter, first_recorder), _expression_input())
    assert result.status == HumanizerResultStatus.DONE

    # 进程重启：全新数据库实例读取同一文件
    second_db = BridgesDatabase(db_path)
    second_db.initialize()
    second_recorder = SqliteModelRunLockRecorder(second_db)
    locks = second_recorder.list_locks_by_run("acc-test", "run-test")
    assert len(locks) == 1
    assert locks[0].status == ModelCallStatus.SUCCESS
    assert locks[0].business_refs[0].operation == HUMANIZER_STAGE_DRAFT
    assert second_recorder.get_lock(locks[0].lock_id, "acc-test") is not None


def test_recovery_new_call_saves_new_lock_not_overwrite(
    recorder: SqliteModelRunLockRecorder,
) -> None:
    """恢复执行若确实发起新模型调用：以新 lock_id 保存新锁，不覆盖旧锁。"""
    adapter = _SequencedAdapter([_draft(_TEMPLATED), _draft(_FIXED)])
    service = _make_service(adapter, recorder)
    _, first = _run(service, _expression_input())
    assert first.writing_call_count == 2
    draft_lock_id = first.run_lock_id
    assert draft_lock_id is not None

    # 跨进程恢复：计数 1 + 恢复首稿 → 只发起一次修订调用（同 run 标识）
    adapter2 = _SequencedAdapter([_draft(_FIXED)])
    service2 = _make_service(adapter2, recorder)
    _, second = _run(
        service2,
        _expression_input(),
        writing_call_count=1,
        recovered_draft=_TEMPLATED,
    )
    assert adapter2.calls == 1
    assert second.writing_call_count == 2

    locks = recorder.list_locks_by_run("acc-test", "run-test")
    assert len(locks) == 3
    lock_ids = [lock.lock_id for lock in locks]
    assert len(set(lock_ids)) == 3
    # 首稿锁未被覆盖或删除
    assert draft_lock_id in lock_ids
    # 恢复执行的修订是新锁（不是重放第一次修订的锁）
    assert [lock.business_refs[0].attempt_ordinal for lock in locks] == [1, 2, 2]
    assert [lock.business_refs[0].operation for lock in locks] == [
        HUMANIZER_STAGE_DRAFT,
        HUMANIZER_STAGE_REVISION,
        HUMANIZER_STAGE_REVISION,
    ]


def test_seam_idempotent_replay_no_duplicate(
    database: BridgesDatabase, recorder: SqliteModelRunLockRecorder
) -> None:
    """重复投递同一记录事件（同一 lock_id + 相同内容）只保留一行锁。"""
    service = _make_service(_SequencedAdapter([_draft(_FIXED)]), recorder)
    lock = ModelRunLock(
        lock_id="lock-replay",
        run_id="run-test",
        account_id="acc-test",
        project_id="conv-test",
        capability_name="qwen_structured_output",
        capability_version="1",
        actual_model_id=CHAT_MODEL_ID,
        region="cn-beijing",
        parameters={"temperature": 0.4, "max_tokens": DRAFT_MAX_TOKENS},
        prompt_version="1",
        input_output_contract="structured-messages-v1:1->json-schema-v1:1",
        fallback_path=["qwen_structured_output@1"],
        status=ModelCallStatus.SUCCESS,
        retry_count=0,
        created_at=datetime.now(UTC),
    )
    call_result = SimpleNamespace(lock=lock)
    service._record_model_run_lock(  # noqa: SLF001 - 接缝合同测试
        call_result,
        account_id="acc-test",
        conversation_id="conv-test",
        assistant_message_id="msg-test",
        run_context=_run_context(),
        operation=HUMANIZER_STAGE_DRAFT,
        attempt_ordinal=1,
        lock_evidence=None,
    )
    service._record_model_run_lock(  # noqa: SLF001 - 接缝合同测试（重放）
        call_result,
        account_id="acc-test",
        conversation_id="conv-test",
        assistant_message_id="msg-test",
        run_context=_run_context(),
        operation=HUMANIZER_STAGE_DRAFT,
        attempt_ordinal=1,
        lock_evidence=None,
    )
    rows = database.scoped("acc-test").execute(
        "SELECT COUNT(*) AS n FROM model_run_locks WHERE account_id = ?",
        ("acc-test",),
    ).fetchone()
    assert int(rows["n"]) == 1
    links = database.scoped("acc-test").execute(
        "SELECT COUNT(*) AS n FROM model_run_lock_links WHERE account_id = ?",
        ("acc-test",),
    ).fetchone()
    assert int(links["n"]) == 2  # 消息 + 会话各一条关联，无重复


# ---------------------------------------------------------------------------
# 跨账户隔离
# ---------------------------------------------------------------------------


def test_cross_account_isolation(recorder: SqliteModelRunLockRecorder) -> None:
    """两个账户并发执行：锁查询严格按账户隔离，业务 ID 冲突不串户。"""
    for account_id, tag in (("acc-a", "a"), ("acc-b", "b")):
        adapter = _SequencedAdapter([_draft(f"{tag}：" + _FIXED)])
        service = _make_service(adapter, recorder)
        _, result = _run(
            service, _expression_input(), account_id=account_id, run_id="run-shared"
        )
        assert result.status == HumanizerResultStatus.DONE

    a_locks = recorder.list_locks_by_run("acc-a", "run-shared")
    b_locks = recorder.list_locks_by_run("acc-b", "run-shared")
    assert len(a_locks) == 1
    assert len(b_locks) == 1
    assert a_locks[0].account_id == "acc-a"
    assert b_locks[0].account_id == "acc-b"
    # 相同业务对象 ID（msg-test/conv-test）不得越权关联
    a_by_msg = recorder.list_locks_by_business_ref("acc-a", "message", "msg-test")
    b_by_msg = recorder.list_locks_by_business_ref("acc-b", "message", "msg-test")
    assert [lock.lock_id for lock in a_by_msg] == [a_locks[0].lock_id]
    assert [lock.lock_id for lock in b_by_msg] == [b_locks[0].lock_id]
    assert a_locks[0].lock_id != b_locks[0].lock_id


# ---------------------------------------------------------------------------
# 失败关闭：recorder 写失败 / 缺锁 / 业务关联不符
# ---------------------------------------------------------------------------


class _FailingRecorder(ModelRunLockRecorder):
    """record_many 恒失败的测试替身（模拟持久化故障注入）。"""

    def record(self, lock: ModelRunLock, *, business_ref: BusinessRef) -> PersistedModelRunLock:
        raise ModelRunLockPersistError("注入失败")

    def record_many(self, requests: list[RecordRequest]) -> list[PersistedModelRunLock]:
        raise ModelRunLockPersistError("注入失败")

    def get_lock(self, lock_id: str, account_id: str) -> PersistedModelRunLock | None:
        return None

    def list_locks_by_run(self, account_id: str, run_id: str) -> list[PersistedModelRunLock]:
        return []

    def list_locks_by_business_ref(
        self, account_id: str, object_type: str, object_id: str
    ) -> list[PersistedModelRunLock]:
        return []


class _FailOnSecondRecordRecorder(_FailingRecorder):
    """第一次 record_many 成功、之后失败：模拟修订锁写入失败。"""

    def __init__(self, inner: SqliteModelRunLockRecorder) -> None:
        self._inner = inner
        self._calls = 0

    def record_many(self, requests: list[RecordRequest]) -> list[PersistedModelRunLock]:
        self._calls += 1
        if self._calls > 1:
            raise ModelRunLockPersistError("注入失败（修订锁）")
        return self._inner.record_many(requests)


def test_recorder_write_failure_fails_closed() -> None:
    """recorder 写入失败：不能把无审计的真实模型输出提升为完成态。"""
    adapter = _SequencedAdapter([_draft(_FIXED)])
    _, result = _run(
        _make_service(adapter, _FailingRecorder()), _expression_input()
    )
    assert adapter.calls == 1  # 真实调用已发生
    assert result.status == HumanizerResultStatus.ERROR
    assert result.error_code == HUMANIZER_LOCK_PERSIST_FAILED
    assert result.output is None
    assert result.process_state == HumanizerProcessState.RECOVERY


def test_revision_lock_write_failure_fails_closed(
    recorder: SqliteModelRunLockRecorder,
) -> None:
    """修订锁写入失败：不得静默降级为「交付首稿」，任务整体失败关闭。"""
    adapter = _SequencedAdapter([_draft(_TEMPLATED), _draft(_FIXED)])
    _, result = _run(
        _make_service(adapter, _FailOnSecondRecordRecorder(recorder)),
        _expression_input(),
    )
    assert adapter.calls == 2
    assert result.status == HumanizerResultStatus.ERROR
    assert result.error_code == HUMANIZER_LOCK_PERSIST_FAILED
    assert result.output is None
    # 首稿锁已持久化，修订锁未写入（无半审计交付）
    locks = recorder.list_locks_by_run("acc-test", "run-test")
    assert len(locks) == 1
    assert locks[0].business_refs[0].operation == HUMANIZER_STAGE_DRAFT


class _LocklessGateway:
    """网关替身：调用真实网关后丢弃锁（模拟网关契约违约）。"""

    def __init__(self, inner: ModelGateway) -> None:
        self._inner = inner

    def invoke(self, *args: Any, **kwargs: Any) -> Any:
        result = self._inner.invoke(*args, **kwargs)
        return result.model_copy(update={"lock": None})


def test_missing_lock_fails_closed(recorder: SqliteModelRunLockRecorder) -> None:
    adapter = _SequencedAdapter([_draft(_FIXED)])
    gateway = _LocklessGateway(_gateway_with(adapter))
    service = HumanizerService(
        registry=create_builtin_registry(),
        gateway=gateway,
        run_lock_recorder=recorder,
    )
    _, result = _run(service, _expression_input())
    assert result.status == HumanizerResultStatus.ERROR
    assert result.error_code == HUMANIZER_MISSING_RUN_LOCK
    assert result.output is None
    assert recorder.list_locks_by_run("acc-test", "run-test") == []


class _MismatchGateway:
    """网关替身：把锁的账户改写为其他账户（模拟业务关联不符）。"""

    def __init__(self, inner: ModelGateway) -> None:
        self._inner = inner

    def invoke(self, *args: Any, **kwargs: Any) -> Any:
        result = self._inner.invoke(*args, **kwargs)
        if result.lock is None:
            return result
        forged = result.lock.model_copy(update={"account_id": "acc-other"})
        return result.model_copy(update={"lock": forged})


def test_business_mismatch_fails_closed(recorder: SqliteModelRunLockRecorder) -> None:
    adapter = _SequencedAdapter([_draft(_FIXED)])
    gateway = _MismatchGateway(_gateway_with(adapter))
    service = HumanizerService(
        registry=create_builtin_registry(),
        gateway=gateway,
        run_lock_recorder=recorder,
    )
    _, result = _run(service, _expression_input())
    assert result.status == HumanizerResultStatus.ERROR
    assert result.error_code == HUMANIZER_LOCK_BUSINESS_MISMATCH
    assert result.output is None
    # 不写错账：其他账户的锁没有进入本账户作用域
    assert recorder.list_locks_by_run("acc-test", "run-test") == []


# ---------------------------------------------------------------------------
# 证据安全修订（Issue 06）同样走统一记录接缝
# ---------------------------------------------------------------------------

_EVIDENCE_SOURCE = (
    "调查显示，使用该产品与满意度相关，用户满意度因此显著提升。该产品售价 199 元。"
)
_EVIDENCE_REVISED = (
    "调查显示，使用该产品与满意度相关，用户满意度因此有所提升。该产品售价 199 元。"
)


def _evidence_safe_input() -> Any:
    routed = route_humanizer_message(f"帮我改写这段话，可以调整结论强度：{_EVIDENCE_SOURCE}")
    assert routed is not None
    return routed.skill_input


def test_evidence_safe_revision_records_two_stage_locks(
    recorder: SqliteModelRunLockRecorder,
) -> None:
    """证据安全修订应用：首稿 + 证据安全修订两条锁，第二条为修订阶段。"""
    adapter = _SequencedAdapter([_draft(_EVIDENCE_SOURCE), _draft(_EVIDENCE_REVISED)])
    _, result = _run(_make_service(adapter, recorder), _evidence_safe_input())
    assert adapter.calls == 2
    assert result.status == HumanizerResultStatus.DONE
    locks = recorder.list_locks_by_run("acc-test", "run-test")
    assert len(locks) == 2
    assert locks[0].business_refs[0].operation == HUMANIZER_STAGE_DRAFT
    assert locks[0].business_refs[0].attempt_ordinal == 1
    assert locks[1].business_refs[0].operation == HUMANIZER_STAGE_REVISION
    assert locks[1].business_refs[0].attempt_ordinal == 2
    assert result.run_lock_id == locks[0].lock_id


def test_evidence_safe_revision_failure_keeps_both_locks(
    recorder: SqliteModelRunLockRecorder,
) -> None:
    """证据安全修订调用失败：保留首稿成功锁与修订失败锁，正文保持首稿。"""
    adapter = _SequencedAdapter([_draft(_EVIDENCE_SOURCE), RateLimitError("临时失败")])
    _, result = _run(_make_service(adapter, recorder), _evidence_safe_input())
    assert adapter.calls == 2
    # 交付语义不变：修订失败 → 保持首稿原文 + hold_for_user
    assert result.status == HumanizerResultStatus.DONE
    assert result.output is not None
    assert result.output.final_text == _EVIDENCE_SOURCE
    locks = recorder.list_locks_by_run("acc-test", "run-test")
    assert len(locks) == 2
    assert locks[0].status == ModelCallStatus.SUCCESS
    assert locks[0].business_refs[0].operation == HUMANIZER_STAGE_DRAFT
    assert locks[1].status == ModelCallStatus.RETRYABLE_FAIL
    assert locks[1].business_refs[0].operation == HUMANIZER_STAGE_REVISION


def test_evidence_safe_revision_held_by_local_gate_keeps_locks(
    recorder: SqliteModelRunLockRecorder,
) -> None:
    """修订稿未通过本地来源硬门：锁保留（真实调用已发生），业务保持首稿。"""
    contaminated = (
        "调查显示，使用该产品与满意度相关，用户满意度因此有所提升。"
        "该产品售价 199 元。本研究纳入 5000 名参与者，采用配对 t 检验。"
    )
    adapter = _SequencedAdapter([_draft(_EVIDENCE_SOURCE), _draft(contaminated)])
    _, result = _run(_make_service(adapter, recorder), _evidence_safe_input())
    assert adapter.calls == 2
    assert result.status == HumanizerResultStatus.DONE
    assert result.output is not None
    assert result.output.final_text == _EVIDENCE_SOURCE
    locks = recorder.list_locks_by_run("acc-test", "run-test")
    assert len(locks) == 2
    # 锁状态与业务终态独立：供应商调用成功（SUCCESS），本地复核未应用
    assert all(lock.status == ModelCallStatus.SUCCESS for lock in locks)


# ---------------------------------------------------------------------------
# 旧兼容路径（无表达契约）同样走记录接缝
# ---------------------------------------------------------------------------

#: 与 test_humanizer_service.py 同一光合作用语料与合规改写（已证明可过
#: 保真硬门与体裁软门），保证旧路径修复测试聚焦锁行为而非检查逻辑。
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
_LEGACY_SOURCE = _CORPUS.split("## 原文")[1].split("## 事实锁清单")[0].strip()
_COMPLIANT_FINAL = (
    "光合作用指的是植物把光能转化为化学能的过程。研究显示，在光照充足"
    "的条件下，水稻叶片的净光合速率约为 25 μmol·m⁻²·s⁻¹；当温度超过 "
    "35°C 时，速率会显著下降（Zhang et al., 2021）。核心反应可以写成 "
    "6CO₂ + 6H₂O → C₆H₁₂O₆ + 6O₂。每固定 1 mol CO₂ 大约需要 8-10 个"
    "光量子；有研究表明，在 25°C 条件下 Rubisco 的周转速率约为每秒 3 "
    "次，仅相当于部分 C4 植物的一半。需要特别说明的是，数据仅适用于受控"
    "温室条件下的栽培品种，不能直接外推到田间。目前只能说初步结果支持"
    "「高温会抑制光合效率」这一判断，尚不能证明其普遍适用（参见 Smith, "
    "2020）。你可以把光合作用比作植物的充电过程，但比喻到此为止，真正的"
    "机制是叶绿素吸收光子；对你说来，这意味着理解温室栽培需要先了解这些"
    "条件。"
)


def _legacy_good_output(final_text: str = _COMPLIANT_FINAL) -> dict[str, Any]:
    return {
        "final_text": final_text,
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


def _legacy_bad_genre_output() -> dict[str, Any]:
    """保持全部事实锁但含科普禁止模式（论文腔套话）的正文（体裁软门不过）。"""
    return _legacy_good_output("本文将介绍光合作用。" + _COMPLIANT_FINAL)


def _legacy_rewrite_input() -> HumanizerSkillInput:
    from bridges.contracts.humanizer import Genre

    return HumanizerSkillInput(
        skill_id="bridges-humanizer",
        contract=HumanizerTaskContract(
            path=HumanizerPath.REWRITE,
            genre=Genre.POPULAR_SCIENCE,
            source_text=_LEGACY_SOURCE,
            audience="普通读者",
            channel="公众号",
            length_target="800 字",
        ),
    )


def test_legacy_draft_records_one_lock(recorder: SqliteModelRunLockRecorder) -> None:
    # 旧路径要求完整输出合同（edits/fact_check/open_questions 非空）
    adapter = _SequencedAdapter(
        [_legacy_good_output("时间管理的关键是先列清单，再排优先级。")]
    )
    _, result = _run(_make_service(adapter, recorder), _legacy_generate_input())
    assert adapter.calls == 1
    assert result.status == HumanizerResultStatus.DONE
    locks = recorder.list_locks_by_run("acc-test", "run-test")
    assert len(locks) == 1
    assert locks[0].business_refs[0].operation == HUMANIZER_STAGE_DRAFT
    assert locks[0].business_refs[0].attempt_ordinal == 1
    assert result.run_lock_id == locks[0].lock_id


def test_legacy_repair_records_second_revision_stage_lock(
    recorder: SqliteModelRunLockRecorder,
) -> None:
    """旧路径软门定向修复：与首稿共享预算，锁阶段为修订、序号 2。"""
    adapter = _SequencedAdapter([_legacy_bad_genre_output(), _legacy_good_output()])
    budget = RunBudget("run-budget", total_ms=120_000)
    _, result = _run(
        _make_service(adapter, recorder), _legacy_rewrite_input(), budget=budget
    )
    assert adapter.calls == 2
    assert result.repair_attempts == 1
    assert result.status == HumanizerResultStatus.DONE
    locks = recorder.list_locks_by_run("acc-test", "run-test")
    assert len(locks) == 2
    assert locks[0].business_refs[0].operation == HUMANIZER_STAGE_DRAFT
    assert locks[0].business_refs[0].attempt_ordinal == 1
    assert locks[1].business_refs[0].operation == HUMANIZER_STAGE_REVISION
    assert locks[1].business_refs[0].attempt_ordinal == 2
    assert result.run_lock_id == locks[0].lock_id


# ---------------------------------------------------------------------------
# 脱敏：锁与日志只保存白名单字段
# ---------------------------------------------------------------------------


def test_locks_never_contain_sensitive_payload(
    recorder: SqliteModelRunLockRecorder,
) -> None:
    usage = {"prompt_tokens": 10, "completion_tokens": 5}
    adapter = _SequencedAdapter([_draft(_TEMPLATED), _draft(_FIXED)], usage=usage)
    _, result = _run(_make_service(adapter, recorder), _expression_input())
    assert result.status == HumanizerResultStatus.DONE

    for lock in recorder.list_locks_by_run("acc-test", "run-test"):
        dumped = lock.model_dump()
        # 参数只有白名单数值键，绝无 prompt/messages/content/正文
        assert set(lock.parameters.keys()) <= {"temperature", "max_tokens"}
        assert all(
            isinstance(value, (int, float)) for value in lock.parameters.values()
        )
        # usage 只含数值（不允许文本载荷混入）
        assert all(
            isinstance(value, (int, float)) for value in (lock.usage or {}).values()
        )
        # 自由文本字段不携带正文
        for field in ("error_message", "error_code", "degradation_reason"):
            value = getattr(lock, field)
            if value:
                assert _SOURCE not in str(value)
                assert _TEMPLATED not in str(value)
                assert _FIXED not in str(value)
        # 锁不复制正文（正文只存在于业务投影的最终文本，不属于锁）
        assert _TEMPLATED not in str(dumped)
        assert _FIXED not in str(dumped)
    # 业务投影本身也只引用锁 ID 与 run ID，不内嵌锁内容
    assert result.run_lock_id is not None
    assert result.model_run_id == "run-test"
    assert _TEMPLATED not in str(result.model_dump())
