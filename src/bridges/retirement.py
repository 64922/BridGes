"""退役能力的兼容门与账户级清理。

本模块集中承载 Issue 03 的两个不可混淆的边界：旧客户端得到稳定的
``410 Gone``，而遗留提醒在任何进程重启或调度扫描中都不会再次投递。
计数器只按稳定端点、服务版本和探针/真实流量分类，不保存账户、路径
参数、查询串或请求正文。
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, NoReturn

from fastapi import HTTPException, Request, status

from bridges import __version__
from bridges.contracts.retirement import RetiredCapabilityError
from bridges.credentials.store import CredentialStoreError, CredentialStorePort
from bridges.persistence import StateStore
from bridges.storage import BridgesDatabase

COMPATIBILITY_SERVICE_VERSION = __version__
COMPATIBILITY_METRICS_NAMESPACE = "compatibility_gate"
REMINDER_RETIREMENT_NAMESPACE = "reminder_retirement"

_REPLACEMENT_PATH = "/"
_RETIRED_MESSAGE = "该能力已退役，请在学习模式聊天中继续。"
_PROBE_HEADERS = frozenset({"1", "true", "yes"})


class CompatibilityMetrics:
    """按稳定端点聚合兼容调用，并可持久化到状态存储。"""

    def __init__(self, state_store: StateStore | None = None) -> None:
        self._state_store = state_store
        self._lock = threading.RLock()
        self._counts: dict[str, dict[str, int]] = {}
        if state_store is not None:
            stored = state_store.load(COMPATIBILITY_METRICS_NAMESPACE) or {}
            routes = stored.get("routes")
            if isinstance(routes, Mapping):
                self._counts = {
                    str(endpoint): {
                        "real": int(values.get("real", 0)),
                        "probe": int(values.get("probe", 0)),
                    }
                    for endpoint, values in routes.items()
                    if isinstance(values, Mapping)
                }

    def record(self, endpoint: str, *, probe: bool) -> None:
        kind = "probe" if probe else "real"
        with self._lock:
            counts = self._counts.setdefault(endpoint, {"real": 0, "probe": 0})
            counts[kind] += 1
            if self._state_store is not None:
                self._state_store.save(
                    COMPATIBILITY_METRICS_NAMESPACE,
                    {
                        "service_version": COMPATIBILITY_SERVICE_VERSION,
                        "routes": self._counts,
                    },
                )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "service_version": COMPATIBILITY_SERVICE_VERSION,
                "routes": {
                    endpoint: dict(counts)
                    for endpoint, counts in sorted(self._counts.items())
                },
            }


_default_metrics = CompatibilityMetrics()


def compatibility_metrics_for(request: Request) -> CompatibilityMetrics:
    """返回当前应用的计数器；独立路由测试使用进程内回退实例。"""

    return getattr(request.app.state, "compatibility_metrics", _default_metrics)


def raise_retired_capability(
    request: Request,
    *,
    endpoint: str,
    error: str,
    replacement_path: str = _REPLACEMENT_PATH,
) -> NoReturn:
    """记录一次兼容调用并抛出不带敏感输入的 410。"""

    probe = request.headers.get("x-bridges-compatibility-probe", "").lower() in _PROBE_HEADERS
    compatibility_metrics_for(request).record(endpoint, probe=probe)
    detail = RetiredCapabilityError(
        error=error,
        message=_RETIRED_MESSAGE,
        replacement_path=replacement_path,
        endpoint=endpoint,
        service_version=COMPATIBILITY_SERVICE_VERSION,
    ).model_dump()
    raise HTTPException(status_code=status.HTTP_410_GONE, detail=detail)


def _retirement_accounts(database: BridgesDatabase) -> list[str]:
    """枚举提醒兼容面账户，不把账户标识写入迁移报告。"""

    rows = database.connection.execute(
        """
        SELECT account_id FROM reminders
        UNION
        SELECT account_id FROM reminder_settings
        UNION
        SELECT account_id FROM smtp_verification_attempts
        ORDER BY account_id
        """
    ).fetchall()
    return [str(row["account_id"]) for row in rows]


def run_reminder_retirement(
    *,
    database: BridgesDatabase,
    credential_store: CredentialStorePort,
    state_store: StateStore | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """幂等取消遗留提醒并清除每个账户的 SMTP 授权码。

    只保存数量、状态和时间。凭据删除失败会保留为可重试结果；无论有无
    遗留项都写入一份报告，保证空环境也有零迁移证据。
    """

    effective_now = (now or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="seconds")
    accounts = _retirement_accounts(database)
    retired_count = 0
    superseded_attempt_count = 0
    credential_failures = 0

    with database.transaction():
        retired_count = int(
            database.connection.execute(
                """
                SELECT COUNT(*) AS count FROM reminders
                WHERE status IN ('enabled', 'paused')
                """
            ).fetchone()["count"]
        )
        database.connection.execute(
            """
            UPDATE reminders
            SET status = 'retired',
                pause_reason = '学习提醒能力已退役，历史仅通过账户导出保留。',
                next_run_at = NULL,
                next_retry_at = NULL,
                retry_count = 0,
                updated_at = ?
            WHERE status IN ('enabled', 'paused')
            """,
            (effective_now,),
        )
        superseded_attempt_count = int(
            database.connection.execute(
                """
                SELECT COUNT(*) AS count FROM smtp_verification_attempts
                WHERE state IN ('smtp_connecting', 'mail_sent', 'waiting_receipt')
                """
            ).fetchone()["count"]
        )
        database.connection.execute(
            """
            UPDATE smtp_verification_attempts
            SET state = 'superseded', updated_at = ?
            WHERE state IN ('smtp_connecting', 'mail_sent', 'waiting_receipt')
            """,
            (effective_now,),
        )
        database.connection.execute(
            """
            UPDATE reminder_settings
            SET smtp_status = 'unconfigured',
                smtp_verified_at = NULL,
                smtp_error_code = NULL,
                smtp_error_message = NULL,
                smtp_attempt_id = NULL,
                smtp_updated_at = ?
            """,
            (effective_now,),
        )

    for account_id in accounts:
        try:
            credential_store.delete(account_id)
        except CredentialStoreError:
            credential_failures += 1

    report: dict[str, Any] = {
        "version": 1,
        "status": "retryable" if credential_failures else "completed",
        "completed_at": effective_now,
        "account_count": len(accounts),
        "reminders_retired": retired_count,
        "verification_attempts_superseded": superseded_attempt_count,
        "credentials_cleared": len(accounts) - credential_failures,
        "credential_failures": credential_failures,
        "historical_deliveries_preserved": True,
    }
    if state_store is not None:
        state_store.save(REMINDER_RETIREMENT_NAMESPACE, report)
    return report


__all__ = [
    "COMPATIBILITY_METRICS_NAMESPACE",
    "COMPATIBILITY_SERVICE_VERSION",
    "CompatibilityMetrics",
    "REMINDER_RETIREMENT_NAMESPACE",
    "raise_retired_capability",
    "run_reminder_retirement",
]
