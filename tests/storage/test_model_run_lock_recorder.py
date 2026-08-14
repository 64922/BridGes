"""Issue 10：ModelRunLockRecorder 持久化、幂等、并发与账户隔离测试。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bridges.ai.errors import (
    ModelRunLockConflictError,
    ModelRunLockPersistError,
    ModelRunLockSecurityError,
)
from bridges.ai.ports import RecordRequest
from bridges.ai.sqlite_recorder import SqliteModelRunLockRecorder
from bridges.contracts.ai import (
    BusinessRef,
    ModelCallStatus,
    ModelRunLock,
)
from bridges.storage import BridgesDatabase


def _make_lock(
    *,
    lock_id: str,
    account_id: str = "acc-1",
    run_id: str = "run-1",
    capability_name: str = "chat",
    status: ModelCallStatus = ModelCallStatus.SUCCESS,
    retry_count: int = 0,
    parameters: dict | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    usage: dict | None = None,
    cost_estimate: dict | None = None,
) -> ModelRunLock:
    return ModelRunLock(
        lock_id=lock_id,
        run_id=run_id,
        account_id=account_id,
        project_id="proj-1",
        capability_name=capability_name,
        capability_version="1",
        actual_model_id="qwen-plus",
        region="cn-beijing",
        parameters=parameters or {"temperature": 0.7},
        prompt_version="1",
        input_output_contract="contract-1",
        fallback_path=["chat@1"],
        status=status,
        retry_count=retry_count,
        degradation_reason=None,
        error_code=error_code,
        error_message=error_message,
        created_at=datetime.now(UTC),
        usage=usage or {"input_tokens": 10, "output_tokens": 20},
        cost_estimate=cost_estimate if cost_estimate is not None else {"usd": 0.001},
    )


def _make_ref(object_id: str = "msg-1", attempt_ordinal: int = 1) -> BusinessRef:
    return BusinessRef(
        object_type="message",
        object_id=object_id,
        operation="generate",
        attempt_ordinal=attempt_ordinal,
        is_primary=True,
    )


@pytest.fixture
def database(tmp_path: Path) -> BridgesDatabase:
    db = BridgesDatabase(tmp_path / "bridges.db")
    db.initialize()
    return db


@pytest.fixture
def recorder(database: BridgesDatabase) -> SqliteModelRunLockRecorder:
    return SqliteModelRunLockRecorder(database)


def test_record_persists_lock_and_link(
    database: BridgesDatabase, recorder: SqliteModelRunLockRecorder
) -> None:
    lock = _make_lock(lock_id="lock-1")
    ref = _make_ref(object_id="msg-1")

    with database.transaction():
        persisted = recorder.record(lock, business_ref=ref)

    assert persisted.lock_id == "lock-1"
    assert persisted.business_refs == [ref]
    assert persisted.legacy is False

    read_back = recorder.get_lock("lock-1", "acc-1")
    assert read_back is not None
    assert read_back.lock_id == "lock-1"
    assert read_back.run_id == "run-1"
    assert read_back.project_id == "proj-1"
    assert read_back.capability_name == "chat"
    assert read_back.status == ModelCallStatus.SUCCESS
    assert read_back.business_refs[0].object_id == "msg-1"


def test_record_many_persists_in_order(
    database: BridgesDatabase, recorder: SqliteModelRunLockRecorder
) -> None:
    requests = [
        RecordRequest(
            lock=_make_lock(lock_id="lock-a", capability_name="chat", retry_count=0),
            business_ref=_make_ref(object_id="msg-2", attempt_ordinal=1),
        ),
        RecordRequest(
            lock=_make_lock(lock_id="lock-b", capability_name="chat", retry_count=1),
            business_ref=_make_ref(object_id="msg-2", attempt_ordinal=2),
        ),
    ]

    with database.transaction():
        results = recorder.record_many(requests)

    assert len(results) == 2
    by_run = recorder.list_locks_by_run("acc-1", "run-1")
    assert [r.lock_id for r in by_run] == ["lock-a", "lock-b"]

    by_object = recorder.list_locks_by_business_ref("acc-1", "message", "msg-2")
    assert [r.lock_id for r in by_object] == ["lock-a", "lock-b"]


def test_record_is_idempotent_for_same_lock_id_and_content(
    database: BridgesDatabase, recorder: SqliteModelRunLockRecorder
) -> None:
    lock = _make_lock(lock_id="lock-idem")
    ref = _make_ref(object_id="msg-idem")

    with database.transaction():
        first = recorder.record(lock, business_ref=ref)
        second = recorder.record(lock, business_ref=ref)

    assert first.lock_id == second.lock_id
    rows = database.scoped("acc-1").execute(
        "SELECT COUNT(*) AS n FROM model_run_locks WHERE account_id = ?",
        ("acc-1",),
    ).fetchone()
    assert int(rows["n"]) == 1

    link_rows = database.scoped("acc-1").execute(
        "SELECT COUNT(*) AS n FROM model_run_lock_links WHERE account_id = ?",
        ("acc-1",),
    ).fetchone()
    assert int(link_rows["n"]) == 1


def test_record_conflicts_for_same_lock_id_different_content(
    database: BridgesDatabase, recorder: SqliteModelRunLockRecorder
) -> None:
    lock_a = _make_lock(lock_id="lock-conflict", capability_name="chat")
    lock_b = _make_lock(lock_id="lock-conflict", capability_name="chat-v2")
    ref = _make_ref(object_id="msg-conflict")

    with database.transaction():
        recorder.record(lock_a, business_ref=ref)
        with pytest.raises(ModelRunLockConflictError):
            recorder.record(lock_b, business_ref=ref)

    # Original row must remain untouched.
    persisted = recorder.get_lock("lock-conflict", "acc-1")
    assert persisted is not None
    assert persisted.capability_name == "chat"


def test_record_many_rolls_back_with_surrounding_transaction(
    database: BridgesDatabase, recorder: SqliteModelRunLockRecorder
) -> None:
    requests = [
        RecordRequest(
            lock=_make_lock(lock_id="lock-rollback-1", capability_name="chat"),
            business_ref=_make_ref(object_id="msg-rollback"),
        ),
        RecordRequest(
            # Same lock_id but different canonical content -> conflict.
            lock=_make_lock(lock_id="lock-rollback-1", capability_name="chat-v2"),
            business_ref=_make_ref(object_id="msg-rollback"),
        ),
    ]

    with pytest.raises(ModelRunLockConflictError), database.transaction():
        recorder.record_many(requests)

    assert recorder.get_lock("lock-rollback-1", "acc-1") is None


def test_account_isolation(database: BridgesDatabase, recorder: SqliteModelRunLockRecorder) -> None:
    lock_a = _make_lock(lock_id="lock-iso-a", account_id="acc-a")
    lock_b = _make_lock(lock_id="lock-iso-b", account_id="acc-b")

    with database.transaction():
        recorder.record(lock_a, business_ref=_make_ref(object_id="msg-a"))
        recorder.record(lock_b, business_ref=_make_ref(object_id="msg-b"))

    assert recorder.get_lock("lock-iso-a", "acc-a") is not None
    assert recorder.get_lock("lock-iso-b", "acc-b") is not None
    # Cross-account reads must return None.
    assert recorder.get_lock("lock-iso-a", "acc-b") is None
    assert recorder.get_lock("lock-iso-b", "acc-a") is None
    # Business refs do not leak across accounts even with same object id.
    results_a = recorder.list_locks_by_business_ref("acc-a", "message", "msg-a")
    assert len(results_a) == 1
    results_b = recorder.list_locks_by_business_ref("acc-b", "message", "msg-a")
    assert len(results_b) == 0


def test_rejects_secret_in_parameters(
    database: BridgesDatabase, recorder: SqliteModelRunLockRecorder
) -> None:
    lock = _make_lock(
        lock_id="lock-secret",
        parameters={"api_key": "sk-test"},
    )
    with pytest.raises(ModelRunLockSecurityError), database.transaction():
        recorder.record(lock, business_ref=_make_ref())


def test_rejects_prompt_in_parameters(
    database: BridgesDatabase, recorder: SqliteModelRunLockRecorder
) -> None:
    lock = _make_lock(
        lock_id="lock-prompt",
        parameters={"nested": {"prompt": "hello"}},
    )
    with pytest.raises(ModelRunLockSecurityError), database.transaction():
        recorder.record(lock, business_ref=_make_ref())


def test_rejects_error_message_with_secret(
    database: BridgesDatabase, recorder: SqliteModelRunLockRecorder
) -> None:
    lock = _make_lock(
        lock_id="lock-err-secret",
        status=ModelCallStatus.RETRYABLE_FAIL,
        error_code="provider_error",
        error_message="Authorization header was sk-12345",
    )
    with pytest.raises(ModelRunLockSecurityError), database.transaction():
        recorder.record(lock, business_ref=_make_ref())


def test_concurrent_writers_do_not_duplicate_locks(
    database: BridgesDatabase, recorder: SqliteModelRunLockRecorder
) -> None:
    lock = _make_lock(lock_id="lock-concurrent")
    ref = _make_ref(object_id="msg-concurrent")
    errors: list[Exception] = []

    def worker() -> None:
        try:
            with database.transaction():
                recorder.record(lock, business_ref=ref)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=4) as executor:
        [executor.submit(worker) for _ in range(10)]

    rows = database.scoped("acc-1").execute(
        "SELECT COUNT(*) AS n FROM model_run_locks WHERE account_id = ? AND lock_id = ?",
        ("acc-1", "lock-concurrent"),
    ).fetchone()
    assert int(rows["n"]) == 1
    # Concurrent duplicate inserts may raise conflict errors only if content
    # differed; identical content must be idempotent.
    assert all(isinstance(e, ModelRunLockConflictError) for e in errors) or not errors


def test_sqlite_file_does_not_contain_secrets(
    tmp_path: Path, database: BridgesDatabase, recorder: SqliteModelRunLockRecorder
) -> None:
    lock = _make_lock(lock_id="lock-no-secret", parameters={"temperature": 0.5})
    with database.transaction():
        recorder.record(lock, business_ref=_make_ref(object_id="msg-safe"))

    database.close()
    raw = (tmp_path / "bridges.db").read_bytes()
    assert b"sk-test" not in raw
    assert b"api_key" not in raw
    assert b"Authorization" not in raw


def test_record_is_atomic_without_surrounding_transaction(
    database: BridgesDatabase, recorder: SqliteModelRunLockRecorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """关联写入失败时不得留下孤儿锁行（recorder 自带事务边界）。"""

    def _explode_link(*args: object, **kwargs: object) -> None:
        raise RuntimeError("模拟关联写入中断")

    monkeypatch.setattr(recorder, "_insert_link_row", _explode_link)
    with pytest.raises(RuntimeError):
        recorder.record(_make_lock(lock_id="lock-orphan"), business_ref=_make_ref())

    assert recorder.get_lock("lock-orphan", "acc-1") is None
    rows = database.scoped("acc-1").execute(
        "SELECT COUNT(*) AS n FROM model_run_locks"
        " WHERE account_id = ? AND lock_id = ?",
        ("acc-1", "lock-orphan"),
    ).fetchone()
    assert int(rows["n"]) == 0


def test_record_many_is_atomic_without_surrounding_transaction(
    database: BridgesDatabase, recorder: SqliteModelRunLockRecorder
) -> None:
    """无外部事务时，批量中途冲突也不得留下部分写入。"""
    requests = [
        RecordRequest(
            lock=_make_lock(lock_id="lock-batch-1", capability_name="chat"),
            business_ref=_make_ref(object_id="msg-batch"),
        ),
        RecordRequest(
            # 与第一条同 lock_id 但内容不同 → 冲突。
            lock=_make_lock(lock_id="lock-batch-1", capability_name="chat-v2"),
            business_ref=_make_ref(object_id="msg-batch"),
        ),
    ]

    with pytest.raises(ModelRunLockConflictError):
        recorder.record_many(requests)

    rows = database.scoped("acc-1").execute(
        "SELECT COUNT(*) AS n FROM model_run_locks"
        " WHERE account_id = ? AND lock_id = ?",
        ("acc-1", "lock-batch-1"),
    ).fetchone()
    assert int(rows["n"]) == 0


def test_list_locks_by_run_orders_by_attempt_ordinal(
    database: BridgesDatabase, recorder: SqliteModelRunLockRecorder
) -> None:
    """run 级查询按业务调用序号排序；legacy（无关联）行排最后。"""
    first = _make_lock(lock_id="lock-ord-2", capability_name="chat", run_id="run-ord")
    second = _make_lock(lock_id="lock-ord-1", capability_name="chat", run_id="run-ord")
    with database.transaction():
        recorder.record(first, business_ref=_make_ref(object_id="msg-ord", attempt_ordinal=2))
        recorder.record(second, business_ref=_make_ref(object_id="msg-ord", attempt_ordinal=1))

    ordered = recorder.list_locks_by_run("acc-1", "run-ord")
    assert [lock.lock_id for lock in ordered] == ["lock-ord-1", "lock-ord-2"]

    # legacy 行（无 links）排序在最后。
    database.scoped("acc-1").execute(
        "INSERT INTO model_run_locks"
        " (lock_id, account_id, run_id, project_id, capability_name,"
        " capability_version, actual_model_id, region, parameters,"
        " prompt_version, input_output_contract, fallback_path_json,"
        " status, retry_count, degradation_reason, error_code,"
        " error_message, usage, cost_estimate_json, canonical_hash,"
        " created_at)"
        " VALUES ('lock-legacy-ord', 'acc-1', 'run-ord', 'legacy-unknown',"
        " 'chat', '1', NULL, 'cn-beijing', '{}', 'legacy', 'legacy', '[]',"
        " 'success', 0, NULL, NULL, NULL, NULL, NULL, 'legacy-lock-legacy-ord',"
        " '2026-01-01T00:00:00Z')",
        (),
    )
    ordered = recorder.list_locks_by_run("acc-1", "run-ord")
    assert [lock.lock_id for lock in ordered] == [
        "lock-ord-1",
        "lock-ord-2",
        "lock-legacy-ord",
    ]


def test_rejects_secret_in_usage(
    database: BridgesDatabase, recorder: SqliteModelRunLockRecorder
) -> None:
    lock = _make_lock(
        lock_id="lock-usage-secret",
        usage={"authorization": "Bearer sk-12345"},
    )
    with pytest.raises(ModelRunLockSecurityError), database.transaction():
        recorder.record(lock, business_ref=_make_ref())


def test_rejects_secret_value_under_benign_key(
    database: BridgesDatabase, recorder: SqliteModelRunLockRecorder
) -> None:
    """无害字段名下携带凭据形态的值（如 sk-...）同样拒绝持久化。"""
    lock = _make_lock(
        lock_id="lock-value-secret",
        parameters={"temperature": "sk-abcdefghijklmnop123456"},
    )
    with pytest.raises(ModelRunLockSecurityError), database.transaction():
        recorder.record(lock, business_ref=_make_ref())


def test_cross_account_same_lock_id_reports_persist_error_not_conflict(
    database: BridgesDatabase, recorder: SqliteModelRunLockRecorder
) -> None:
    """相同 lock_id 出现在其他账户时不是内容冲突，而是明确的持久化失败。"""
    with database.transaction():
        recorder.record(
            _make_lock(lock_id="lock-shared", account_id="acc-a"),
            business_ref=_make_ref(object_id="msg-a"),
        )
        with pytest.raises(ModelRunLockPersistError):
            recorder.record(
                _make_lock(lock_id="lock-shared", account_id="acc-b"),
                business_ref=_make_ref(object_id="msg-b"),
            )
    # 原账户行不受影响，B 账户无该锁。
    assert recorder.get_lock("lock-shared", "acc-a") is not None
    assert recorder.get_lock("lock-shared", "acc-b") is None


def test_accepts_legitimate_numeric_usage(
    database: BridgesDatabase, recorder: SqliteModelRunLockRecorder
) -> None:
    """真实适配器的 usage 键（prompt_tokens 等）是数值，必须允许持久化。"""
    lock = _make_lock(
        lock_id="lock-usage-ok",
        usage={
            "prompt_tokens": 123,
            "completion_tokens": 45,
            "total_tokens": 168,
            "cache_read_tokens": 0,
        },
        cost_estimate={"usd": 0.0012},
    )
    with database.transaction():
        persisted = recorder.record(lock, business_ref=_make_ref())
    assert persisted.usage is not None
    assert persisted.usage["prompt_tokens"] == 123
    assert persisted.cost_estimate == {"usd": 0.0012}


def test_metrics_emission(
    database: BridgesDatabase, recorder: SqliteModelRunLockRecorder, tmp_path: Path
) -> None:
    from bridges.ai.metrics import InMemoryModelRunLockMetrics

    metrics = InMemoryModelRunLockMetrics()
    instrumented = SqliteModelRunLockRecorder(database, metrics=metrics)

    with database.transaction():
        instrumented.record(
            _make_lock(lock_id="lock-m1", capability_name="chat"),
            business_ref=_make_ref(),
        )
        instrumented.record(
            _make_lock(
                lock_id="lock-m2",
                capability_name="chat",
                status=ModelCallStatus.RETRYABLE_FAIL,
            ),
            business_ref=_make_ref(object_id="msg-m2"),
        )

    snapshot = metrics.snapshot()
    assert snapshot.get("model_lock_record_total:chat:success") == 1
    assert snapshot.get("model_lock_record_total:chat:retryable_fail") == 1

    # 冲突计数。
    with pytest.raises(ModelRunLockConflictError), database.transaction():
        instrumented.record(
            _make_lock(lock_id="lock-m1", capability_name="chat-v2"),
            business_ref=_make_ref(),
        )
    snapshot = metrics.snapshot()
    assert snapshot.get("model_lock_record_conflict_total:chat-v2") == 1

    # legacy 行读取 → 缺业务关联指标。
    database.scoped("acc-1").execute(
        "INSERT INTO model_run_locks"
        " (lock_id, account_id, run_id, project_id, capability_name,"
        " capability_version, actual_model_id, region, parameters,"
        " prompt_version, input_output_contract, fallback_path_json,"
        " status, retry_count, degradation_reason, error_code,"
        " error_message, usage, cost_estimate_json, canonical_hash,"
        " created_at)"
        " VALUES ('lock-legacy-m', 'acc-1', 'legacy-unknown', 'legacy-unknown',"
        " 'chat', '1', NULL, 'cn-beijing', '{}', 'legacy', 'legacy', '[]',"
        " 'success', 0, NULL, NULL, NULL, NULL, NULL, 'legacy-lock-legacy-m',"
        " '2026-01-01T00:00:00Z')",
        (),
    )
    instrumented.get_lock("lock-legacy-m", "acc-1")
    snapshot = metrics.snapshot()
    assert snapshot.get("model_lock_missing_business_ref_total:chat") == 1
