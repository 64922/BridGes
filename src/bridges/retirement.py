"""学习任务安排与提醒退役的兼容边界和账户级清理。"""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, NoReturn

from fastapi import HTTPException, Request, status

from bridges import __version__
from bridges.contracts.retirement import RetiredCapabilityError
from bridges.credentials.store import CredentialStoreError, CredentialStorePort
from bridges.mcp.runtime import McpRuntime
from bridges.persistence import StateStore
from bridges.storage import BridgesDatabase

COMPATIBILITY_SERVICE_VERSION = __version__
COMPATIBILITY_METRICS_NAMESPACE = "compatibility_gate"
REMINDER_RETIREMENT_NAMESPACE = "reminder_retirement"

_REPLACEMENT_PATH = "/"
_RETIRED_MESSAGE = "该能力已退役，请在学习模式聊天中继续。"
_PROBE_HEADERS = frozenset({"1", "true", "yes", "probe"})
_PENDING_MCP_STATES = frozenset(
    {"pending", "running", "claimed", "sensitive_pending", "awaiting_confirmation"}
)


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
    error: str = "user_extensions_retired",
    replacement_path: str = _REPLACEMENT_PATH,
    message: str = _RETIRED_MESSAGE,
) -> NoReturn:
    """记录一次兼容调用并抛出不带敏感输入的 410。"""

    probe = (
        request.headers.get("x-bridges-compatibility-probe", "").lower()
        in _PROBE_HEADERS
    )
    compatibility_metrics_for(request).record(endpoint, probe=probe)
    observability = getattr(request.app.state, "observability_service", None)
    if observability is not None:
        observability.record_compatibility_410(
            endpoint_id=endpoint,
            service_version=COMPATIBILITY_SERVICE_VERSION,
            traffic_class="probe" if probe else "real",
        )
    detail = RetiredCapabilityError(
        error=error,
        message=message,
        replacement_path=replacement_path,
        endpoint=endpoint,
        service_version=COMPATIBILITY_SERVICE_VERSION,
    ).model_dump()
    raise HTTPException(status_code=status.HTTP_410_GONE, detail=detail)


def raise_retired_file_source(request: Request, *, endpoint: str) -> NoReturn:
    """迁移闸门通过后拒绝项目/聊天文件写入口。"""

    database = getattr(request.app.state, "bridges_database", None)
    if database is not None:
        # 延迟导入避免退役观测模块与迁移服务之间形成导入环。
        from bridges.learning_projects.migration import contraction_gate_report

        report = contraction_gate_report(database)
        if report["status"] != "passed":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "migration_gate_blocked",
                    "message": report["summary"],
                    "gate_status": report["status"],
                    "unmanaged_count": report["unmanaged_count"],
                    "pending_count": report["pending_count"],
                    "failed_count": report["failed_count"],
                },
            )
    raise_retired_capability(
        request,
        endpoint=endpoint,
        error="legacy_file_source_retired",
        replacement_path="/knowledge-base",
        message="项目文件和聊天附件已退役，请使用全局知识库。",
    )


def record_compatibility_observation(request: Request, endpoint: str) -> None:
    probe = (
        request.headers.get("x-bridges-compatibility-probe", "").lower()
        in _PROBE_HEADERS
    )
    compatibility_metrics_for(request).record(endpoint, probe=probe)


def _retirement_accounts(database: BridgesDatabase) -> list[str]:
    """枚举提醒相关账户，仅用于按账户清理，不写入迁移报告。"""

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
    """幂等停止遗留提醒并清除每个账户的 SMTP 授权码。"""

    effective_now = (now or datetime.now(UTC)).astimezone(UTC).isoformat(
        timespec="seconds"
    )
    accounts = _retirement_accounts(database)
    with database.transaction():
        queued_reminders_retired = int(
            database.connection.execute(
                """
                SELECT COUNT(*) AS count FROM task_claims
                WHERE queue_name IN ('reminder', 'reminders')
                  AND status IN ('queued', 'claimed', 'failed')
                """
            ).fetchone()["count"]
        )
        database.connection.execute(
            """
            UPDATE task_claims
            SET status = 'completed',
                worker = NULL,
                claimed_at = NULL,
                lease_expires_at = NULL,
                next_retry_at = NULL,
                result_json = '{"status":"retired"}',
                updated_at = ?
            WHERE queue_name IN ('reminder', 'reminders')
              AND status IN ('queued', 'claimed', 'failed')
            """,
            (effective_now,),
        )
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
        superseded_count = int(
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

    credential_failures = 0
    for account_id in accounts:
        try:
            credential_store.delete(account_id)
        except (CredentialStoreError, OSError):
            credential_failures += 1

    report: dict[str, Any] = {
        "version": 1,
        "status": "retryable" if credential_failures else "completed",
        "completed_at": effective_now,
        "account_count": len(accounts),
        "reminders_retired": retired_count,
        "verification_attempts_superseded": superseded_count,
        "queued_reminders_retired": queued_reminders_retired,
        "credentials_cleared": len(accounts) - credential_failures,
        "credential_failures": credential_failures,
        "historical_deliveries_preserved": True,
    }
    if state_store is not None:
        state_store.save(REMINDER_RETIREMENT_NAMESPACE, report)
    return report


def plugin_endpoint(method: str, path: str) -> str:
    normalized = path.strip("/")
    if not normalized:
        return "legacy.plugins.list"
    if normalized in {"check", "install"}:
        return f"legacy.plugins.{normalized}"
    if normalized.endswith("/enable"):
        return "legacy.plugins.enable"
    if normalized.endswith("/disable"):
        return "legacy.plugins.disable"
    if normalized.endswith("/demo"):
        return "legacy.plugins.demo"
    if method == "DELETE":
        return "legacy.plugins.uninstall"
    return "legacy.plugins.unknown"


def mcp_endpoint(method: str, path: str) -> str:
    normalized = path.strip("/")
    if not normalized:
        return "legacy.mcp.list"
    if normalized in {"check", "install"}:
        return f"legacy.mcp.{normalized}"
    if "/confirmations/" in normalized and normalized.endswith("/approve"):
        return "legacy.mcp.confirmation.approve"
    if "/confirmations/" in normalized and normalized.endswith("/deny"):
        return "legacy.mcp.confirmation.deny"
    if normalized.endswith("/invoke"):
        return "legacy.mcp.invoke"
    if normalized.endswith("/calls"):
        return "legacy.mcp.calls"
    if normalized.endswith("/permissions"):
        return "legacy.mcp.permissions"
    if normalized.endswith("/enable"):
        return "legacy.mcp.enable"
    if normalized.endswith("/disable"):
        return "legacy.mcp.disable"
    if method == "DELETE":
        return "legacy.mcp.uninstall"
    return "legacy.mcp.unknown"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _retire_message_payload(value: str | None) -> tuple[str | None, bool]:
    if value is None:
        return value, False
    try:
        payload = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value, False
    if not isinstance(payload, Mapping):
        return value, False
    payload_status = str(payload.get("status", ""))
    is_unstarted_user_call = (
        "status" not in payload and "mcp_id" in payload and "tool" in payload
    )
    if payload_status not in _PENDING_MCP_STATES and not is_unstarted_user_call:
        return value, False
    updated = dict(payload)
    updated.update(
        {
            "status": "failed",
            "error_code": "user_extensions_retired",
            "error_message": "user extensions retired",
        }
    )
    return json.dumps(updated, separators=(",", ":")), True


def retire_user_extensions(
    database: BridgesDatabase,
    *,
    runtime: McpRuntime | None = None,
) -> dict[str, int | str]:
    if runtime is not None:
        runtime.reap_orphans()
        runtime.stop_all()

    timestamp = _now()
    with database.transaction():
        disabled_skills = int(
            database.connection.execute(
                "SELECT COUNT(*) AS count FROM skill_packages "
                "WHERE status <> 'disabled'"
            ).fetchone()["count"]
        )
        database.connection.execute(
            "UPDATE skill_packages SET status = 'disabled', "
            "failure_reason = COALESCE(failure_reason, ?), updated_at = ? "
            "WHERE status <> 'disabled'",
            ("user extensions retired", timestamp),
        )
        disabled_servers = int(
            database.connection.execute(
                "SELECT COUNT(*) AS count FROM mcp_servers "
                "WHERE status <> 'disabled' OR enabled <> 0"
            ).fetchone()["count"]
        )
        database.connection.execute(
            "UPDATE mcp_servers SET status = 'disabled', enabled = 0, "
            "failure_reason = COALESCE(failure_reason, ?), updated_at = ? "
            "WHERE status <> 'disabled' OR enabled <> 0",
            ("user extensions retired", timestamp),
        )
        database.connection.execute(
            "UPDATE conversations SET plugin_selection = NULL "
            "WHERE plugin_selection IS NOT NULL"
        )

        terminated_calls = 0
        rows = database.connection.execute(
            "SELECT message_id, mcp_call, skill FROM messages "
            "WHERE mcp_call IS NOT NULL OR skill IS NOT NULL"
        ).fetchall()
        for row in rows:
            updated_call, call_changed = _retire_message_payload(row["mcp_call"])
            updated_skill = row["skill"]
            skill_changed = False
            if row["skill"]:
                try:
                    skill_payload = json.loads(row["skill"])
                except (TypeError, json.JSONDecodeError):
                    skill_payload = None
                if (
                    isinstance(skill_payload, Mapping)
                    and "skill_id" in skill_payload
                    and skill_payload.get("status")
                    not in {"done", "failed", "retired"}
                ):
                    updated_skill_payload = dict(skill_payload)
                    updated_skill_payload.update(
                        {
                            "status": "failed",
                            "error_code": "user_extensions_retired",
                            "error_message": "user extensions retired",
                        }
                    )
                    updated_skill = json.dumps(
                        updated_skill_payload, separators=(",", ":")
                    )
                    skill_changed = True
            if not call_changed and not skill_changed:
                continue
            database.connection.execute(
                "UPDATE messages SET mcp_call = ?, skill = ?, updated_at = ? "
                "WHERE message_id = ?",
                (
                    updated_call if call_changed else row["mcp_call"],
                    updated_skill,
                    timestamp,
                    row["message_id"],
                ),
            )
            terminated_calls += int(call_changed)

        placeholders = ", ".join("?" for _ in _PENDING_MCP_STATES)
        database.connection.execute(
            f"UPDATE mcp_calls SET status = 'failed', "
            f"error_code = ?, error_message = ? WHERE status IN ({placeholders})",
            ("user_extensions_retired", "user extensions retired", *_PENDING_MCP_STATES),
        )
        database.connection.execute(
            "CREATE TABLE IF NOT EXISTS extension_retirement ("
            "retirement_id INTEGER PRIMARY KEY CHECK (retirement_id = 1), "
            "status TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        database.connection.execute(
            "INSERT INTO extension_retirement(retirement_id, status, updated_at) "
            "VALUES (1, 'completed', ?) ON CONFLICT(retirement_id) DO UPDATE SET "
            "status = excluded.status, updated_at = excluded.updated_at",
            (timestamp,),
        )

    return {
        "status": "completed",
        "skill_packages_disabled": disabled_skills,
        "mcp_servers_disabled": disabled_servers,
        "pending_calls_terminated": terminated_calls,
    }


__all__ = [
    "COMPATIBILITY_METRICS_NAMESPACE",
    "COMPATIBILITY_SERVICE_VERSION",
    "CompatibilityMetrics",
    "REMINDER_RETIREMENT_NAMESPACE",
    "mcp_endpoint",
    "plugin_endpoint",
    "raise_retired_capability",
    "raise_retired_file_source",
    "record_compatibility_observation",
    "retire_user_extensions",
    "run_reminder_retirement",
]
