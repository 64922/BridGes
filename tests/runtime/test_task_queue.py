"""Issue 43：统一「领取型任务」契约（runtime/queue.py）单元测试。

覆盖验收标准要求的全部语义：领取原子性（并发不重复）、租约过期重领
（崩溃恢复唯一规则）、三种退避公式、complete/requeue 幂等、TaskWorker
的 claim → handler → complete/requeue 薄封装。
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest

from bridges.runtime.queue import (
    Claim,
    RetryKind,
    TaskPermanentError,
    TaskQueue,
    TaskRetryError,
    TaskWorker,
)
from bridges.storage.database import BridgesDatabase


@pytest.fixture()
def queue() -> TaskQueue:
    database = BridgesDatabase(":memory:")
    database.initialize()
    return TaskQueue(database, default_lease_seconds=300.0)


@pytest.fixture()
def db(queue: TaskQueue) -> BridgesDatabase:
    return queue._database  # noqa: SLF001 - 测试读取内部状态


# ----------------------------------------------------------------------
# 入队与领取
# ----------------------------------------------------------------------


def test_enqueue_and_claim_first_attempt(queue: TaskQueue) -> None:
    queue.enqueue("ingestion", "ingestion:doc-1", {"object_id": "obj-1"})
    claim = queue.claim_next("ingestion", "worker-1")
    assert claim is not None
    assert claim.task_key == "ingestion:doc-1"
    assert claim.attempt == 0  # 首次领取：尚未失败过
    assert claim.payload == {"object_id": "obj-1"}
    assert claim.lease_expires_at > datetime.now(UTC)
    # 已领取：同一 worker 不再领到同一条。
    assert queue.claim_next("ingestion", "worker-1") is None


def test_enqueue_same_key_resets_scheduling(queue: TaskQueue) -> None:
    queue.enqueue("q", "k")
    claim = queue.claim_next("q", "w")
    assert claim is not None
    queue.requeue(claim, retry_kind=RetryKind.EXPONENTIAL, reason="boom")
    # 手动重试：重新入队后立即可以领取，attempt 归零重新计数；同一
    # key 只保留一条记录（幂等），claim_id 不变。
    queue.enqueue("q", "k")
    claim2 = queue.claim_next("q", "w")
    assert claim2 is not None
    assert claim2.claim_id == claim.claim_id
    assert claim2.attempt == 0


def test_claim_while_claimed_lease_active_returns_none(queue: TaskQueue) -> None:
    queue.enqueue("q", "k")
    assert queue.claim_next("q", "w1") is not None
    assert queue.claim_next("q", "w2") is None


def test_lease_expired_claim_can_be_reclaimed(queue: TaskQueue) -> None:
    """租约过期重领是崩溃恢复的唯一规则，且不消耗重试计数。"""
    queue.enqueue("q", "k")
    first = queue.claim_next("q", "w1", lease_seconds=1)
    assert first is not None
    # 直接篡改租约过期（模拟进程崩溃后时间流逝）。
    queue._database.connection.execute(  # noqa: SLF001
        "UPDATE task_claims SET lease_expires_at = ? WHERE claim_id = ?",
        (
            (datetime.now(UTC) - timedelta(seconds=1)).isoformat(timespec="seconds"),
            first.claim_id,
        ),
    )
    recovered = queue.claim_next("q", "w2")
    assert recovered is not None
    assert recovered.task_key == "k"
    # 恢复重领不递增 attempt（恢复不算重试）。
    assert recovered.attempt == first.attempt


def test_concurrent_claim_is_atomic(queue: TaskQueue) -> None:
    """两个线程同时领取 10 条任务，每条恰好被领取一次。"""
    for i in range(10):
        queue.enqueue("q", f"task-{i}")
    claimed: list[Claim] = []
    lock = threading.Lock()

    def worker_fn() -> None:
        while True:
            claim = queue.claim_next("q", "worker")
            if claim is None:
                return
            with lock:
                claimed.append(claim)

    threads = [threading.Thread(target=worker_fn) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    keys = sorted(c.task_key for c in claimed)
    assert keys == [f"task-{i}" for i in range(10)]
    assert len({c.claim_id for c in claimed}) == 10


def test_fifo_order(queue: TaskQueue) -> None:
    queue.enqueue("q", "first")
    queue.enqueue("q", "second")
    assert queue.claim_next("q", "w").task_key == "first"
    assert queue.claim_next("q", "w").task_key == "second"


def test_complete_idempotent(queue: TaskQueue) -> None:
    queue.enqueue("q", "k")
    claim = queue.claim_next("q", "w")
    assert claim is not None
    queue.complete(claim, result={"ok": True})
    queue.complete(claim, result={"ok": True})  # 重复调用无副作用
    assert queue.claim_next("q", "w") is None
    row = queue._database.connection.execute(  # noqa: SLF001
        "SELECT status FROM task_claims WHERE claim_id = ?", (claim.claim_id,)
    ).fetchone()
    assert row["status"] == "completed"


def test_requeue_idempotent_after_complete(queue: TaskQueue) -> None:
    queue.enqueue("q", "k")
    claim = queue.claim_next("q", "w")
    assert claim is not None
    queue.complete(claim)
    # 对已完成 claim 的 requeue 无效（幂等），不会复活任务。
    queue.requeue(claim, retry_kind=RetryKind.FIXED, reason="late")
    assert queue.claim_next("q", "w") is None


# ----------------------------------------------------------------------
# 退避公式
# ----------------------------------------------------------------------


def test_backoff_fixed(queue: TaskQueue) -> None:
    queue.enqueue("q", "k")
    claim = queue.claim_next("q", "w")
    assert claim is not None
    queue.requeue(claim, retry_kind=RetryKind.FIXED, reason="r")
    # FIXED 默认 0 秒：下一轮立即重试。
    assert queue.claim_next("q", "w") is not None


def test_backoff_linear(queue: TaskQueue) -> None:
    queue.enqueue("q", "k")
    claim = queue.claim_next("q", "w")
    assert claim is not None
    queue.requeue(claim, retry_kind=RetryKind.LINEAR, reason="r")
    row = queue._database.connection.execute(  # noqa: SLF001
        "SELECT next_retry_at FROM task_claims WHERE claim_id = ?",
        (claim.claim_id,),
    ).fetchone()
    expected = datetime.now(UTC) + timedelta(seconds=30 * 1)
    actual = datetime.fromisoformat(str(row["next_retry_at"]))
    assert abs((actual - expected).total_seconds()) < 5


def test_backoff_exponential_caps(queue: TaskQueue) -> None:
    for attempt in (1, 2, 3, 4):
        assert TaskQueue.compute_backoff(
            RetryKind.EXPONENTIAL, attempt, base_seconds=30, cap_seconds=300
        ) == min(30 * 2 ** (attempt - 1), 300)


def test_requeue_exhausts_max_attempts(queue: TaskQueue) -> None:
    """重试预算耗尽后不再自动调度，等子系统手动重新入队。"""
    queue.enqueue("q", "k")
    for failures in (0, 1, 2):
        claim = queue.claim_next("q", "w")
        assert claim is not None
        assert claim.attempt == failures
        exhausted = queue.requeue(
            claim, retry_kind=RetryKind.FIXED, reason="r", max_attempts=3
        )
        assert exhausted == (failures == 2)
    assert queue.claim_next("q", "w") is None
    assert queue.pending_count("q") == 1  # 耗尽驻留（可被手动重新入队复活）
    queue.enqueue("q", "k")
    assert queue.claim_next("q", "w") is not None


def test_requeue_after_exhaustion_returns_true(queue: TaskQueue) -> None:
    queue.enqueue("q", "k")
    claim = queue.claim_next("q", "w")
    assert claim is not None
    assert claim.attempt == 0
    exhausted = queue.requeue(
        claim, retry_kind=RetryKind.FIXED, reason="r", max_attempts=1
    )
    assert exhausted is True
    assert queue.claim_next("q", "w") is None


# ----------------------------------------------------------------------
# 崩溃恢复（跨实例重建）
# ----------------------------------------------------------------------


def test_crash_recovery_reclaims_expired_leases(queue: TaskQueue) -> None:
    """进程崩溃后：未完成的领取（租约过期）在重建实例后可被重领。"""
    database = BridgesDatabase(":memory:")
    database.initialize()
    queue_a = TaskQueue(database, default_lease_seconds=5)
    queue_a.enqueue("q", "k")
    claim = queue_a.claim_next("q", "worker-a")
    assert claim is not None
    # 模拟崩溃：不 complete 也不 requeue，租约自然过期。
    database.connection.execute(
        "UPDATE task_claims SET lease_expires_at = ? WHERE claim_id = ?",
        (
            (datetime.now(UTC) - timedelta(seconds=1)).isoformat(timespec="seconds"),
            claim.claim_id,
        ),
    )
    # 新进程重建队列实例，恢复并处理同一任务。
    queue_b = TaskQueue(database, default_lease_seconds=5)
    recovered = queue_b.claim_next("q", "worker-b")
    assert recovered is not None
    assert recovered.task_key == "k"
    queue_b.complete(recovered)
    assert queue_b.claim_next("q", "worker-b") is None


def test_recovery_skips_tasks_in_progress_under_lease(queue: TaskQueue) -> None:
    """租约未过期的领取不被新实例打扰（幂等守护）。"""
    database = BridgesDatabase(":memory:")
    database.initialize()
    queue_a = TaskQueue(database, default_lease_seconds=600)
    queue_a.enqueue("q", "k")
    assert queue_a.claim_next("q", "worker-a") is not None
    queue_b = TaskQueue(database, default_lease_seconds=600)
    assert queue_b.claim_next("q", "worker-b") is None


# ----------------------------------------------------------------------
# TaskWorker 薄封装
# ----------------------------------------------------------------------


def test_worker_completes_on_none_return(queue: TaskQueue) -> None:
    queue.enqueue("q", "k")
    worker = TaskWorker(queue, "q", "w", lambda claim: None)
    assert worker.run_once() is True
    assert queue.pending_count("q") == 0


def test_worker_completes_with_result_dict(queue: TaskQueue) -> None:
    queue.enqueue("q", "k")

    def handler(claim: Claim) -> dict[str, object]:
        return {"processed": claim.task_key}

    worker = TaskWorker(queue, "q", "w", handler)
    assert worker.run_once() is True
    row = queue._database.connection.execute(  # noqa: SLF001
        "SELECT result_json FROM task_claims WHERE task_key = 'k'"
    ).fetchone()
    assert row["result_json"] == '{"processed": "k"}'


def test_worker_requeues_on_retry_later(queue: TaskQueue) -> None:
    queue.enqueue("q", "k")

    def handler(claim: Claim) -> None:
        raise TaskRetryError("smtp down", retry_kind=RetryKind.EXPONENTIAL)

    worker = TaskWorker(queue, "q", "w", handler)
    assert worker.run_once() is True
    assert queue.claim_next("q", "w") is None  # 退避中，本轮到不了
    row = queue._database.connection.execute(  # noqa: SLF001
        "SELECT next_retry_at FROM task_claims WHERE task_key = 'k'"
    ).fetchone()
    assert row["next_retry_at"] is not None


def test_worker_fails_permanently(queue: TaskQueue) -> None:
    queue.enqueue("q", "k")

    def handler(claim: Claim) -> None:
        raise TaskPermanentError("文件损坏，无法解析")

    worker = TaskWorker(queue, "q", "w", handler)
    assert worker.run_once() is True
    assert queue.claim_next("q", "w") is None
    row = queue._database.connection.execute(  # noqa: SLF001
        "SELECT next_retry_at FROM task_claims WHERE task_key = 'k'"
    ).fetchone()
    assert row["next_retry_at"] is None  # 永久失败不设置退避


def test_worker_requeues_on_unexpected_exception(queue: TaskQueue) -> None:
    queue.enqueue("q", "k")

    def handler(claim: Claim) -> None:
        raise RuntimeError("unexpected")

    worker = TaskWorker(queue, "q", "w", handler, default_max_attempts=3)
    assert worker.run_once() is True
    assert queue.claim_next("q", "w") is None


def test_worker_drain_handles_all_pending(queue: TaskQueue) -> None:
    """drain 每任务每轮至多一次：成功任务完成，FIXED 重试留给下一轮。"""
    for i in range(5):
        queue.enqueue("q", f"task-{i}")

    def handler(claim: Claim) -> None:
        if claim.task_key == "task-1":
            raise TaskRetryError("transient", retry_kind=RetryKind.FIXED)

    worker = TaskWorker(queue, "q", "w", handler)
    handled, failures = worker.drain()
    # 每个任务本轮至多处理一次；task-1 失败后短退避（留给下一轮），
    # 不阻塞 task-2/3/4 完成。
    assert handled == 5
    assert failures == 1
    assert queue.claim_next("q", "w") is None  # task-1 处于 1 秒退避
    assert queue.pending_count("q") == 1
    # 后续 drain：task-1 持续驻留（1 秒退避内），不复活不消失。
    worker.drain()
    assert queue.pending_count("q") == 1


def test_worker_drain_empty(queue: TaskQueue) -> None:
    worker = TaskWorker(queue, "q", "w", lambda claim: None)
    assert worker.drain() == (0, 0)


# ----------------------------------------------------------------------
# 租约配置
# ----------------------------------------------------------------------


def test_lease_expiry_seconds_registry(queue: TaskQueue) -> None:
    assert queue.lease_expiry_seconds("unknown") == 300.0
    queue.set_lease_seconds("ingestion", 1800.0)
    assert queue.lease_expiry_seconds("ingestion") == 1800.0


def test_touch_renews_lease(queue: TaskQueue) -> None:
    queue.enqueue("q", "k")
    claim = queue.claim_next("q", "w", lease_seconds=60)
    assert claim is not None
    queue.touch(claim, lease_seconds=600)
    row = queue._database.connection.execute(  # noqa: SLF001
        "SELECT lease_expires_at FROM task_claims WHERE claim_id = ?",
        (claim.claim_id,),
    ).fetchone()
    remaining = datetime.fromisoformat(str(row["lease_expires_at"])) - datetime.now(UTC)
    assert remaining > timedelta(minutes=8)
