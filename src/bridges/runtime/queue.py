"""统一「领取型任务」契约（Issue 43）：租约 + 轮询 + 重启恢复只写一次。

后台任务「领取 → 执行 → 完成/重试」的机械规则此前在 ingestion、image、
video、deletion、reminder 各自实现（列名、退避公式、跳过/补发语义互不
相同）。本模块把这段契约收拢为一个深 module：``TaskQueue`` 负责原子领取、
租约续期、退避调度与崩溃恢复（唯一规则：租约过期的领取可被重领），
``TaskWorker`` 提供「claim → handler → complete/requeue」的薄封装。

边界：子系统业务表仍是其自身的权威状态（状态机、进度、补发窗口等业务
语义不进队列）；``task_claims`` 表只做领取与调度。队列的并发安全依赖
SQLite 单连接 + ``BEGIN IMMEDIATE`` 事务（写锁天然串行），适用于单机
进程内后台执行器与提醒调度器（ADR-0013 不冲突）。
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from bridges.storage.database import BridgesDatabase
from bridges.storage.errors import StorageError

#: 队列行状态：queued 等待领取；claimed 已领取（租约内）；completed 已
#: 完成；failed 退避等待重试（next_retry_at 到期可重领）或已耗尽（永久
#: 停摆，等子系统手动重新入队）。
_QUEUED = "queued"
_CLAIMED = "claimed"
_COMPLETED = "completed"
_FAILED = "failed"


class RetryKind(StrEnum):
    """退避公式（唯一实现处，子系统不得自带公式）。"""

    FIXED = "fixed"  # 固定间隔（默认 0 秒 = 下一轮立即）
    LINEAR = "linear"  # base * attempt
    EXPONENTIAL = "exponential"  # min(base * 2**(attempt-1), cap)


def _encode(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Claim:
    """领取到的一件活儿（不可变）。"""

    claim_id: str
    queue_name: str
    task_key: str  # 子系统+对象标识（如 "ingestion:doc-42"）
    attempt: int  # 已失败的次数（requeue 时递增，0 起；续轮不计数）
    lease_expires_at: datetime
    payload: dict[str, Any] | None = None


class TaskQueueError(Exception):
    """队列契约错误（handler 抛出后由 worker 转换）。"""


class TaskPermanentError(TaskQueueError):
    """handler 抛出：任务永久失败，不再自动重试（等子系统手动重试）。"""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class TaskRetryError(TaskQueueError):
    """handler 抛出：任务失败，需按退避公式稍后重试。

    ``count_attempt=False`` 用于「正常进度续轮」而非失败（如云端任务
    轮询）：不消耗重试预算，attempt 不递增。
    """

    def __init__(
        self,
        reason: str,
        *,
        retry_kind: RetryKind = RetryKind.EXPONENTIAL,
        max_attempts: int = 0,
        backoff_seconds: float | None = None,
        count_attempt: bool = True,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retry_kind = retry_kind
        self.max_attempts = max_attempts  # 0 = 不设上限（使用 worker 默认）
        self.backoff_seconds = backoff_seconds
        self.count_attempt = count_attempt


#: handler 处理一件活儿：返回 dict（作为 result）或 None 表示成功；
#: 抛 ``TaskRetryError``/``TaskPermanentError`` 表达失败语义。
Handler = Callable[[Claim], dict[str, Any] | None]


class TaskQueue:
    """深 module：租约 / 退避 / 崩溃恢复全部在此，子系统不自行实现。"""

    def __init__(
        self,
        database: BridgesDatabase,
        *,
        default_lease_seconds: float = 300.0,
    ) -> None:
        self._database = database
        self._default_lease_seconds = default_lease_seconds
        self._leases: dict[str, float] = {}

    # ------------------------------------------------------------------
    # 配置
    # ------------------------------------------------------------------

    def set_lease_seconds(self, queue_name: str, seconds: float) -> None:
        """注册队列的租约时长（默认租约在构造时指定）。"""
        self._leases[queue_name] = seconds

    def lease_expiry_seconds(self, queue_name: str) -> float:
        """返回队列的租约时长；未注册时返回默认值。"""
        return self._leases.get(queue_name, self._default_lease_seconds)

    @staticmethod
    def compute_backoff(
        retry_kind: RetryKind,
        attempt: int,
        *,
        base_seconds: float = 30.0,
        cap_seconds: float = 300.0,
    ) -> float:
        """退避公式唯一实现：attempt 为第几次尝试（从 1 起）。"""
        if retry_kind is RetryKind.FIXED:
            return 0.0
        if retry_kind is RetryKind.LINEAR:
            return base_seconds * attempt
        return min(base_seconds * 2 ** (attempt - 1), cap_seconds)

    # ------------------------------------------------------------------
    # 入队 / 领取
    # ------------------------------------------------------------------

    def enqueue(
        self,
        queue_name: str,
        task_key: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """登记一件活儿（幂等：同队列同 key 只保留一条，重复入队重置调度）。

        已有行正在被处理（claimed 且租约未过期）时不打断它；否则重置
        回 queued 并清零退避计数（用户手动重试、子系统重新入队都走
        这里）。调用方已处于事务中时加入该事务（业务状态行与队列行
        原子一致），否则自开事务。
        """
        if self._database.connection.in_transaction:
            self._enqueue_now(queue_name, task_key, payload)
        else:
            with self._database.transaction():
                self._enqueue_now(queue_name, task_key, payload)

    def _enqueue_now(
        self,
        queue_name: str,
        task_key: str,
        payload: dict[str, Any] | None,
    ) -> None:
        now = _now()
        self._database.connection.execute(
            "INSERT INTO task_claims(claim_id, queue_name, task_key, attempt,"
            " status, payload_json, created_at, updated_at)"
            " VALUES (?, ?, ?, 0, ?, ?, ?, ?)"
            " ON CONFLICT(queue_name, task_key) DO UPDATE SET"
            " payload_json = excluded.payload_json, updated_at = excluded.updated_at,"
            " status = 'queued', attempt = 0, next_retry_at = NULL"
            " WHERE task_claims.status != 'claimed'",
            (
                f"cl-{secrets.token_urlsafe(16)}",
                queue_name,
                task_key,
                _QUEUED,
                json.dumps(payload) if payload is not None else None,
                _encode(now),
                _encode(now),
            ),
        )

    def claim_next(
        self,
        queue_name: str,
        worker: str,
        *,
        lease_seconds: float | None = None,
    ) -> Claim | None:
        """原子领取一件可执行的任务；无任务返回 None。

        领取条件（调度唯一入口）：queued；或 failed 且退避到期；或
        claimed 且租约过期（崩溃恢复的唯一规则）。领取在 ``BEGIN
        IMMEDIATE`` 事务内完成，SQLite 写锁天然串行，多线程并发安全。
        租约过期重领不递增 attempt（恢复不算重试）。
        """
        now = _now()
        now_text = _encode(now)
        lease = now + timedelta(
            seconds=lease_seconds or self.lease_expiry_seconds(queue_name)
        )
        lease_text = _encode(lease)
        # failed 行的 next_retry_at 非 NULL 才是「退避等待重试」；NULL
        # 表示已耗尽或永久失败（fail()/requeue 耗尽后置 NULL），不再领取。
        claimable = (
            "status = 'queued'"
            " OR (status = 'failed' AND next_retry_at IS NOT NULL"
            "     AND next_retry_at <= ?)"
            " OR (status = 'claimed' AND lease_expires_at IS NOT NULL"
            "     AND lease_expires_at < ?)"
        )
        try:
            with self._database.transaction():
                row = self._database.connection.execute(
                    "SELECT claim_id FROM task_claims"
                    " WHERE queue_name = ? AND (" + claimable + ")"
                    " ORDER BY created_at, rowid LIMIT 1",
                    (queue_name, now_text, now_text),
                ).fetchone()
                if row is None:
                    return None
                claim_id = str(row["claim_id"])
                self._database.connection.execute(
                    "UPDATE task_claims SET status = 'claimed', worker = ?,"
                    " claimed_at = ?, lease_expires_at = ?, updated_at = ?"
                    " WHERE claim_id = ?",
                    (worker, now_text, lease_text, now_text, claim_id),
                )
                full = self._database.connection.execute(
                    "SELECT * FROM task_claims WHERE claim_id = ?",
                    (claim_id,),
                ).fetchone()
            if full is None:
                return None
            payload = (
                json.loads(str(full["payload_json"]))
                if full["payload_json"] is not None
                else None
            )
            return Claim(
                claim_id=claim_id,
                queue_name=queue_name,
                task_key=str(full["task_key"]),
                attempt=int(full["attempt"]),
                lease_expires_at=datetime.fromisoformat(
                    str(full["lease_expires_at"])
                ),
                payload=payload,
            )
        except (StorageError, sqlite3.Error, ValueError, TypeError):
            # 领取失败（库忙/数据损坏）不阻断 worker 循环，下轮再试。
            return None

    # ------------------------------------------------------------------
    # 结果回写（幂等：仅当行仍处于 claimed 状态时生效）
    # ------------------------------------------------------------------

    def complete(self, claim: Claim, result: dict[str, Any] | None = None) -> None:
        """标记完成；重复调用（如崩溃后重放）无副作用。"""
        with self._database.transaction():
            self._database.connection.execute(
                "UPDATE task_claims SET status = 'completed', result_json = ?,"
                " lease_expires_at = NULL, next_retry_at = NULL, updated_at = ?"
                " WHERE claim_id = ? AND status = 'claimed'",
                (
                    json.dumps(result) if result is not None else None,
                    _encode(_now()),
                    claim.claim_id,
                ),
            )

    def requeue(
        self,
        claim: Claim,
        *,
        retry_kind: RetryKind,
        reason: str,
        max_attempts: int = 0,
        backoff_seconds: float | None = None,
        count_attempt: bool = True,
    ) -> bool:
        """失败重排：消耗一次 attempt 并按退避公式计算下一次可领取时间。

        ``count_attempt=False``（进度续轮/轮次边界释放租约）不消耗
        重试预算。返回 True 表示预算已耗尽（不再自动调度，等子系统
        手动重新入队）；此时 ``claim_next`` 不再领取该任务。
        """
        now = _now()
        attempt = claim.attempt + (1 if count_attempt else 0)
        exhausted = max_attempts > 0 and attempt >= max_attempts
        next_retry: datetime | None = None
        if not exhausted:
            backoff = (
                backoff_seconds
                if backoff_seconds is not None
                else self.compute_backoff(retry_kind, attempt)
            )
            next_retry = now + timedelta(seconds=backoff)
        with self._database.transaction():
            self._database.connection.execute(
                "UPDATE task_claims SET status = 'failed', next_retry_at = ?,"
                " result_json = ?, lease_expires_at = NULL, updated_at = ?,"
                " attempt = ?"
                " WHERE claim_id = ? AND status = 'claimed'",
                (
                    _encode(next_retry) if next_retry is not None else None,
                    json.dumps(
                        {"retry_kind": retry_kind.value, "reason": reason[:1000]}
                    ),
                    _encode(now),
                    attempt,
                    claim.claim_id,
                ),
            )
        return exhausted

    def fail(self, claim: Claim, reason: str) -> None:
        """永久失败：不再自动调度，等待子系统手动重新入队。"""
        with self._database.transaction():
            self._database.connection.execute(
                "UPDATE task_claims SET status = 'failed', next_retry_at = NULL,"
                " result_json = ?, lease_expires_at = NULL, updated_at = ?"
                " WHERE claim_id = ? AND status = 'claimed'",
                (
                    json.dumps({"reason": reason[:1000]}),
                    _encode(_now()),
                    claim.claim_id,
                ),
            )

    def touch(self, claim: Claim, *, lease_seconds: float | None = None) -> None:
        """续租（handler 长任务保活）；仅对仍处于 claimed 的行生效。"""
        seconds = (
            lease_seconds
            if lease_seconds is not None
            else self.lease_expiry_seconds(claim.queue_name)
        )
        with self._database.transaction():
            self._database.connection.execute(
                "UPDATE task_claims SET lease_expires_at = ?, updated_at = ?"
                " WHERE claim_id = ? AND status = 'claimed'",
                (_encode(_now() + timedelta(seconds=seconds)), _encode(_now()), claim.claim_id),
            )

    # ------------------------------------------------------------------
    # 观测
    # ------------------------------------------------------------------

    def pending_count(self, queue_name: str) -> int:
        """返回队列中待处理（含退避等待）的任务数，供测试与摘要。"""
        row = self._database.connection.execute(
            "SELECT COUNT(*) AS count FROM task_claims"
            " WHERE queue_name = ? AND status IN ('queued', 'claimed', 'failed')",
            (queue_name,),
        ).fetchone()
        return int(row["count"]) if row is not None else 0

    def has_runnable(self, queue_name: str) -> bool:
        """判断队列是否仍有可被 worker 领取的任务。"""
        now = _encode(_now())
        row = self._database.connection.execute(
            "SELECT 1 FROM task_claims WHERE queue_name = ? "
            "AND (status IN ('queued', 'claimed') "
            "OR (status = 'failed' AND next_retry_at IS NOT NULL "
            "AND next_retry_at <= ?)) LIMIT 1",
            (queue_name, now),
        ).fetchone()
        return row is not None


class TaskWorker:
    """薄封装：claim → handler → complete/requeue，异常兜底。

    ``handler`` 只管「干这一件活儿」；重试/退避/崩溃恢复的机械规则
    全部由队列承担。``run_once`` 处理一件，``drain`` 循环到无任务。
    """

    def __init__(
        self,
        queue: TaskQueue,
        queue_name: str,
        worker: str,
        handler: Handler,
        *,
        default_max_attempts: int = 5,
    ) -> None:
        self._queue = queue
        self._queue_name = queue_name
        self._worker = worker
        self._handler = handler
        self._default_max_attempts = default_max_attempts
        self._handled = 0
        self._failures = 0

    def _run_claim(self, claim: Claim) -> None:
        """执行一件活儿并回写队列状态；统计 handled/failures。"""
        self._handled += 1
        try:
            result = self._handler(claim)
        except TaskPermanentError as exc:
            self._queue.fail(claim, exc.reason)
            self._failures += 1
        except TaskRetryError as exc:
            self._queue.requeue(
                claim,
                retry_kind=exc.retry_kind,
                reason=exc.reason,
                max_attempts=exc.max_attempts or self._default_max_attempts,
                backoff_seconds=exc.backoff_seconds,
                count_attempt=exc.count_attempt,
            )
            if exc.count_attempt:
                self._failures += 1
        except Exception as exc:  # noqa: BLE001 - 单任务异常兜底重试
            self._queue.requeue(
                claim,
                retry_kind=RetryKind.EXPONENTIAL,
                reason=f"处理异常：{exc}",
                max_attempts=self._default_max_attempts,
            )
            self._failures += 1
        else:
            self._queue.complete(claim, result=result)

    def run_once(self) -> bool:
        """处理一件活儿，返回是否处理了一件；失败已转成队列状态。"""
        claim = self._queue.claim_next(self._queue_name, self._worker)
        if claim is None:
            return False
        self._run_claim(claim)
        return True

    def drain(self, *, max_steps: int | None = None) -> tuple[int, int]:
        """每轮每任务至多处理一次，返回 (处理件数, 失败件数)。

        FIXED 0 秒退避的任务（如轮询续轮、删除重试）在本轮处理一次
        后释放租约（不消耗 attempt、+1 秒退避），留给下一轮——避免
        单轮内无限重试同一任务，同时不阻塞队列中其他任务。
        ``max_steps`` 限制单轮处理件数（控制单轮时长），剩余任务留待
        下一轮。
        """
        self._handled = 0
        self._failures = 0
        seen: set[str] = set()
        while True:
            if max_steps is not None and self._handled >= max_steps:
                break
            claim = self._queue.claim_next(self._queue_name, self._worker)
            if claim is None:
                break
            if claim.claim_id in seen:
                # 本轮已处理过：释放租约（不计数、短退避）并继续处理
                # 队列中其他任务，该任务留给下一轮。
                self._queue.requeue(
                    claim,
                    retry_kind=RetryKind.FIXED,
                    reason="轮次边界释放",
                    backoff_seconds=1.0,
                    count_attempt=False,
                )
                continue
            seen.add(claim.claim_id)
            self._run_claim(claim)
        return self._handled, self._failures
