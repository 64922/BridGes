"""Issue 12：Career 模型运行锁 SQLite 合同测试。

覆盖：同 run 多锁、阶段/序号、幂等重放、真正重执行的调用序号、跨账户
隔离、投影前崩溃与重启后查询、模型 ID 来自 Issue 09 单一事实源、修复
序号守门失败关闭。临时 SQLite 建库，不触碰任何生产数据。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from bridges.ai import CapabilityRegistry, ModelGateway
from bridges.ai.fixed_models import MODEL_BY_CAPABILITY
from bridges.ai.ports import ModelRunLockRecorder, RecordRequest
from bridges.ai.sqlite_recorder import SqliteModelRunLockRecorder
from bridges.career.service import (
    CAREER_LOCK_OBJECT_TYPE,
    CAREER_OPERATION_GENERATION,
    CAREER_OPERATION_REPAIR,
    CareerPlannerService,
)
from bridges.contracts.ai import (
    CapabilityKind,
    CapabilityRecord,
    CapabilityStatus,
    ModelCallStatus,
    RetryPolicy,
)
from bridges.contracts.career import CareerPlanningStatus
from bridges.contracts.workflows import RunContextEnvelope
from bridges.learning import InMemoryLearningRepository, LearningService
from bridges.observability.service import ObservabilityService
from bridges.storage import BridgesDatabase
from tests.career.test_career_service import (
    _good_output,
    _invalid_output,
    _ProgrammableStructuredAdapter,
    _web_search_projection,
)

NOW = datetime.now(UTC)

_FIXED_STRUCTURED_MODEL = MODEL_BY_CAPABILITY["qwen_structured_output"]


def _run_context(
    run_id: str, account_id: str, project_id: str = "project-1"
) -> RunContextEnvelope:
    return RunContextEnvelope(
        run_id=run_id,
        account_id=account_id,
        project_id=project_id,
        workflow_name="chat",
        workflow_version="1",
        submitted_at=NOW,
    )


def _structured_capability(model_id: str = _FIXED_STRUCTURED_MODEL) -> CapabilityRecord:
    return CapabilityRecord(
        name="qwen_structured_output",
        version="1",
        kind=CapabilityKind.MODEL,
        vendor="qwen",
        region="cn-beijing",
        model_id=model_id,
        input_schema_version="structured-messages-v1",
        output_schema_version="json-schema-v1",
        status=CapabilityStatus.VERIFIED,
        retry_policy=RetryPolicy(max_attempts=2, backoff_seconds=0.01),
    )


def _gateway_with(
    adapter: _ProgrammableStructuredAdapter, model_id: str
) -> ModelGateway:
    registry = CapabilityRegistry()
    registry.register(_structured_capability(model_id))
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_structured_output", "1", adapter)
    return gateway


def _service(
    database: BridgesDatabase,
    adapter: _ProgrammableStructuredAdapter,
    *,
    recorder: ModelRunLockRecorder | None = None,
    model_id: str = _FIXED_STRUCTURED_MODEL,
) -> CareerPlannerService:
    return CareerPlannerService(
        gateway=_gateway_with(adapter, model_id),
        learning_service=LearningService(repository=InMemoryLearningRepository()),
        observability_service=ObservabilityService(),
        run_lock_recorder=recorder or SqliteModelRunLockRecorder(database),
    )


def _run(
    service: CareerPlannerService,
    *,
    account_id: str = "account-1",
    conversation_id: str = "conv-1",
    assistant_message_id: str = "assistant-1",
    run_context: RunContextEnvelope | None = None,
) -> list[Any]:
    return list(
        service.run_task(
            account_id,
            conversation_id,
            assistant_message_id,
            "生涯规划：数据分析方向怎么安排",
            mode="companion",
            run_context=run_context or _run_context("run-career-1", account_id),
            profile_enabled=False,
            profile_used=False,
            profile_items=[],
            web_search_projection=_web_search_projection(),
        )
    )


@pytest.fixture
def database(tmp_path: Path) -> BridgesDatabase:
    db = BridgesDatabase(tmp_path / "bridges.db")
    db.initialize()
    return db


def test_same_run_multi_locks_survive_restart(database: BridgesDatabase) -> None:
    """首次无效 + 修复成功：两条锁（generation、repair）按序号排序；
    进程重启后（新 service/recorder 实例）按 run 与业务引用均可查。"""
    adapter = _ProgrammableStructuredAdapter(outputs=[_invalid_output(), _good_output()])
    service = _service(database, adapter)
    events = _run(service)
    assert events[-1].result is not None
    assert events[-1].result.status == CareerPlanningStatus.DONE
    assert adapter.call_count == 2

    # 重启：全新实例读取同一 SQLite 文件
    restarted = SqliteModelRunLockRecorder(database)
    by_run = restarted.list_locks_by_run("account-1", "run-career-1")
    assert len(by_run) == 2
    plan_ordinals = [
        ref.attempt_ordinal
        for lock in by_run
        for ref in lock.business_refs
        if ref.object_type == CAREER_LOCK_OBJECT_TYPE
    ]
    assert plan_ordinals == [1, 2]
    operations = [
        ref.operation
        for lock in by_run
        for ref in lock.business_refs
        if ref.object_type == CAREER_LOCK_OBJECT_TYPE
    ]
    assert operations == [CAREER_OPERATION_GENERATION, CAREER_OPERATION_REPAIR]

    by_ref = restarted.list_locks_by_business_ref(
        "account-1", CAREER_LOCK_OBJECT_TYPE, "assistant-1"
    )
    assert len(by_ref) == 2
    assert by_ref[0].lock_id == by_run[0].lock_id
    # 每条锁的会话关联也在重启后可查
    for lock in by_ref:
        conv_refs = [
            ref
            for ref in lock.business_refs
            if ref.object_type == "conversation" and ref.object_id == "conv-1"
        ]
        assert len(conv_refs) == 1


def test_idempotent_replay_same_lock_keeps_one_row(
    database: BridgesDatabase,
) -> None:
    """recorder 重复提交相同锁幂等：同一锁（同 lock_id + 同业务关联）
    重放 N 次只保留一行与一组关联。"""
    adapter = _ProgrammableStructuredAdapter(outputs=[_good_output()])
    service = _service(database, adapter)
    events = _run(service)
    projection = events[-1].result
    assert projection is not None
    assert len(projection.run_lock_refs) == 1
    lock_id = projection.run_lock_refs[0].lock_id

    recorder = SqliteModelRunLockRecorder(database)
    lock = recorder.get_lock(lock_id, "account-1")
    assert lock is not None
    # 原样重放同一锁与业务关联（模拟幂等重放/重复消费）；record 端口
    # 契约接受不可变 ModelRunLock，重放时还原为纯锁视图。
    from bridges.contracts.ai import ModelRunLock

    plain = ModelRunLock(
        **lock.model_dump(
            exclude={"business_refs", "legacy", "legacy_missing_fields"}
        )
    )
    for _ in range(3):
        for ref in lock.business_refs:
            recorder.record(plain, business_ref=ref)

    after = recorder.list_locks_by_business_ref(
        "account-1", CAREER_LOCK_OBJECT_TYPE, "assistant-1"
    )
    assert len(after) == 1, "重复提交相同锁不得产生重复行"
    assert after[0].lock_id == lock_id
    assert len(after[0].business_refs) == len(lock.business_refs)


def test_reexecution_saves_new_ordinal_without_overwriting_old_lock(
    database: BridgesDatabase,
) -> None:
    """真正重新执行模型请求：新锁带自己的调用序号，旧锁原样保留。"""
    adapter = _ProgrammableStructuredAdapter(outputs=[_good_output()])
    service = _service(database, adapter)
    first = _run(service)
    first_id = first[-1].result.run_lock_refs[0].lock_id

    # 同一业务对象第二次执行（新网关锁）
    adapter2 = _ProgrammableStructuredAdapter(outputs=[_good_output()])
    service2 = _service(database, adapter2)
    second = _run(service2)
    second_id = second[-1].result.run_lock_refs[0].lock_id

    assert first_id != second_id
    recorder = SqliteModelRunLockRecorder(database)
    locks = recorder.list_locks_by_business_ref(
        "account-1", CAREER_LOCK_OBJECT_TYPE, "assistant-1"
    )
    assert len(locks) == 2
    ids = {lock.lock_id for lock in locks}
    assert ids == {first_id, second_id}
    # 旧锁内容未被改写
    old = recorder.get_lock(first_id, "account-1")
    assert old is not None
    assert old.status == ModelCallStatus.SUCCESS
    ordinals = {
        ref.attempt_ordinal
        for lock in locks
        for ref in lock.business_refs
        if ref.object_type == CAREER_LOCK_OBJECT_TYPE
    }
    assert ordinals == {1}


def test_cross_account_isolation_with_colliding_object_ids(
    database: BridgesDatabase,
) -> None:
    """两账户并发规划：锁与投影按账户隔离；相同会话/助手消息 ID 不跨
    账户关联；共享 Key 不构成跨账户查询依据。"""
    from concurrent.futures import ThreadPoolExecutor

    def run_account(account_id: str, run_id: str) -> list[Any]:
        adapter = _ProgrammableStructuredAdapter(outputs=[_good_output()])
        service = _service(database, adapter)
        return _run(
            service,
            account_id=account_id,
            conversation_id="same-conv",
            assistant_message_id="same-message",
            run_context=_run_context(run_id, account_id),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        future_a = executor.submit(run_account, "account-a", "run-a")
        future_b = executor.submit(run_account, "account-b", "run-b")
        events_a = future_a.result()
        events_b = future_b.result()

    assert events_a[-1].result is not None
    assert events_a[-1].result.status == CareerPlanningStatus.DONE
    assert events_b[-1].result is not None
    assert events_b[-1].result.status == CareerPlanningStatus.DONE

    recorder = SqliteModelRunLockRecorder(database)
    # 业务对象 ID 冲突但不越权
    assert len(
        recorder.list_locks_by_business_ref(
            "account-a", CAREER_LOCK_OBJECT_TYPE, "same-message"
        )
    ) == 1
    assert len(
        recorder.list_locks_by_business_ref(
            "account-b", CAREER_LOCK_OBJECT_TYPE, "same-message"
        )
    ) == 1
    # run 隔离
    assert len(recorder.list_locks_by_run("account-a", "run-a")) == 1
    assert len(recorder.list_locks_by_run("account-b", "run-b")) == 1
    # 账户 A 无法读取账户 B 的锁（同 run_id 也读不到——run_id 不跨账户）
    assert recorder.list_locks_by_run("account-a", "run-b") == []
    lock_a = recorder.list_locks_by_run("account-a", "run-a")[0]
    assert lock_a.account_id == "account-a"
    plan_refs_a = [
        ref for ref in lock_a.business_refs if ref.object_type == CAREER_LOCK_OBJECT_TYPE
    ]
    assert all(ref.object_id == "same-message" for ref in plan_refs_a)
    conv_refs_a = [
        ref for ref in lock_a.business_refs if ref.object_type == "conversation"
    ]
    assert all(ref.object_id == "same-conv" for ref in conv_refs_a)


def test_crash_before_projection_keeps_persisted_locks(
    database: BridgesDatabase,
) -> None:
    """投影前崩溃（锁已持久化、投影尚未构造）：重启后锁仍可查，不丢失
    已发起的供应商调用证据。"""
    adapter = _ProgrammableStructuredAdapter(outputs=[_invalid_output(), _good_output()])
    service = _service(database, adapter)
    events = service.run_task(
        "account-1",
        "conv-1",
        "assistant-1",
        "生涯规划：数据分析方向怎么安排",
        mode="companion",
        run_context=_run_context("run-career-1", "account-1"),
        profile_enabled=False,
        profile_used=False,
        profile_items=[],
        web_search_projection=_web_search_projection(),
    )
    # 在复核阶段事件之后立刻停止消费（模拟进程在投影构造前崩溃；
    # 此时首次生成与有界修复的真实调用锁都已持久化）
    consumed = 0
    for event in events:
        consumed += 1
        if event.kind == "process" and "复核证据与边界" in event.step_label:
            break
    assert consumed >= 3
    assert adapter.call_count == 2, "崩溃前两次真实调用都已发起"

    # 重启查询：已经发起的调用（含修复）都有持久化锁
    recorder = SqliteModelRunLockRecorder(database)
    locks = recorder.list_locks_by_run("account-1", "run-career-1")
    assert len(locks) >= 1
    assert all(lock.account_id == "account-1" for lock in locks)
    # 两条锁分别对应两次真实调用（生成 + 修复）
    assert len(locks) == 2
    assert all(lock.status == ModelCallStatus.SUCCESS for lock in locks)


def test_recorded_model_id_matches_issue09_single_source(
    database: BridgesDatabase,
) -> None:
    """运行锁的实际模型 ID 必须来自 Issue 09 单一事实源
    （fixed_models 批准矩阵），且 capability 固定为 qwen_structured_output。"""
    assert _FIXED_STRUCTURED_MODEL  # 单一事实源常量非空
    adapter = _ProgrammableStructuredAdapter(outputs=[_good_output()])
    service = _service(database, adapter)
    events = _run(service)
    assert events[-1].result is not None
    assert events[-1].result.status == CareerPlanningStatus.DONE

    recorder = SqliteModelRunLockRecorder(database)
    locks = recorder.list_locks_by_run("account-1", "run-career-1")
    assert len(locks) == 1
    assert locks[0].capability_name == "qwen_structured_output"
    assert locks[0].capability_version == "1"
    assert locks[0].actual_model_id == _FIXED_STRUCTURED_MODEL


class _BlindSequenceRecorder(ModelRunLockRecorder):
    """持久化正常但业务引用查询恒为空：触发修复序号守门。"""

    def __init__(self, inner: SqliteModelRunLockRecorder) -> None:
        self._inner = inner

    def record(self, lock: Any, *, business_ref: Any) -> Any:
        return self._inner.record(lock, business_ref=business_ref)

    def record_many(self, requests: list[RecordRequest]) -> list[Any]:
        return self._inner.record_many(requests)

    def get_lock(self, lock_id: str, account_id: str) -> Any:
        return self._inner.get_lock(lock_id, account_id)

    def list_locks_by_run(self, account_id: str, run_id: str) -> list[Any]:
        return self._inner.list_locks_by_run(account_id, run_id)

    def list_locks_by_business_ref(
        self, account_id: str, object_type: str, object_id: str
    ) -> list[Any]:
        return []


def test_repair_sequence_guard_fails_closed_when_generation_lock_missing(
    database: BridgesDatabase,
) -> None:
    """缺少首次生成锁时不得发起修复调用：career_call_sequence_mismatch
    失败关闭，只有 generation 锁落库，adapter 只被调用一次。"""
    adapter = _ProgrammableStructuredAdapter(outputs=[_invalid_output(), _good_output()])
    service = _service(
        database,
        adapter,
        recorder=_BlindSequenceRecorder(SqliteModelRunLockRecorder(database)),
    )
    events = _run(service)
    projection = events[-1].result
    assert projection is not None
    assert projection.status == CareerPlanningStatus.ERROR
    assert projection.error_code == "career_call_sequence_mismatch"
    assert adapter.call_count == 1, "序号守门失败时不得发起第二次真实调用"

    recorder = SqliteModelRunLockRecorder(database)
    locks = recorder.list_locks_by_business_ref(
        "account-1", CAREER_LOCK_OBJECT_TYPE, "assistant-1"
    )
    assert len(locks) == 1
    refs = [
        ref
        for ref in locks[0].business_refs
        if ref.object_type == CAREER_LOCK_OBJECT_TYPE
    ]
    assert refs[0].operation == CAREER_OPERATION_GENERATION
    assert refs[0].attempt_ordinal == 1
