"""用户 SKILL、插件与通用 MCP 的退役边界。"""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, NoReturn

from fastapi import HTTPException, Request, status

from bridges import __version__
from bridges.contracts.retirement import RetiredCapabilityError
from bridges.mcp.runtime import McpRuntime
from bridges.storage.database import BridgesDatabase

COMPATIBILITY_SERVICE_VERSION = __version__
_PROBE_VALUES = frozenset({"1", "true", "yes", "probe"})
_RETIRED_MESSAGE = "用户 SKILL、插件与通用 MCP 已退役，请返回聊天或知识库。"
_PENDING_MCP_STATES = frozenset(
    {"pending", "running", "claimed", "sensitive_pending", "awaiting_confirmation"}
)


class CompatibilityObserver:
    """只按稳定端点、版本和流量类别计数，不保存请求身份或正文。"""

    def __init__(self) -> None:
        self._counts: dict[str, dict[str, int]] = {}
        self._lock = threading.RLock()

    def record(self, endpoint: str, *, traffic_class: str) -> None:
        with self._lock:
            counts = self._counts.setdefault(endpoint, {"real": 0, "probe": 0})
            counts[traffic_class] += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "service_version": COMPATIBILITY_SERVICE_VERSION,
                "routes": {
                    endpoint: dict(counts)
                    for endpoint, counts in sorted(self._counts.items())
                },
            }


compatibility_observer = CompatibilityObserver()


def _traffic_class(request: Request) -> str:
    value = request.headers.get("x-bridges-compatibility-probe", "").lower()
    return "probe" if value in _PROBE_VALUES else "real"


def _observer_for(request: Request) -> CompatibilityObserver:
    return getattr(request.app.state, "compatibility_observer", compatibility_observer)


def raise_retired_capability(
    request: Request,
    *,
    endpoint: str,
    replacement_path: str = "/templates/chat",
) -> NoReturn:
    """记录一次旧接口调用并抛出不读取请求正文的 410。"""
    traffic_class = _traffic_class(request)
    record_compatibility_observation(request, endpoint)
    detail = RetiredCapabilityError(
        error="user_extensions_retired",
        message=_RETIRED_MESSAGE,
        replacement_path=replacement_path,
        endpoint=endpoint,
        service_version=COMPATIBILITY_SERVICE_VERSION,
        traffic_class=traffic_class,
    ).model_dump()
    raise HTTPException(status_code=status.HTTP_410_GONE, detail=detail)


def record_compatibility_observation(request: Request, endpoint: str) -> None:
    """记录不含请求正文的兼容性流量观察。"""
    _observer_for(request).record(endpoint, traffic_class=_traffic_class(request))


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


def _retire_message_payload(value: str) -> tuple[str, bool]:
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
            "error_message": _RETIRED_MESSAGE,
        }
    )
    return json.dumps(updated, ensure_ascii=False, separators=(",", ":")), True


def retire_user_extensions(
    database: BridgesDatabase,
    *,
    runtime: McpRuntime | None = None,
) -> dict[str, int | str]:
    """幂等地禁用历史外部能力，并终止待处理调用但保留审计数据。"""
    if runtime is not None:
        runtime.reap_orphans()
        runtime.stop_all()

    timestamp = _now()
    disabled_skills = 0
    disabled_servers = 0
    terminated_calls = 0
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
            (_RETIRED_MESSAGE, timestamp),
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
            (_RETIRED_MESSAGE, timestamp),
        )
        database.connection.execute(
            "UPDATE conversations SET plugin_selection = NULL "
            "WHERE plugin_selection IS NOT NULL"
        )

        rows = database.connection.execute(
            "SELECT message_id, mcp_call, skill FROM messages "
            "WHERE mcp_call IS NOT NULL OR skill IS NOT NULL"
        ).fetchall()
        for row in rows:
            updated, changed = _retire_message_payload(row["mcp_call"])
            skill_updated = row["skill"]
            skill_changed = False
            if row["skill"]:
                try:
                    skill_payload = json.loads(row["skill"])
                except (TypeError, json.JSONDecodeError):
                    skill_payload = None
                if (
                    isinstance(skill_payload, Mapping)
                    and "skill_id" in skill_payload
                    and skill_payload.get("status") not in {"done", "failed", "retired"}
                ):
                    skill_payload = dict(skill_payload)
                    skill_payload.update(
                        {
                            "status": "failed",
                            "error_code": "user_extensions_retired",
                            "error_message": _RETIRED_MESSAGE,
                        }
                    )
                    skill_updated = json.dumps(
                        skill_payload, ensure_ascii=False, separators=(",", ":")
                    )
                    skill_changed = True
            if not changed and not skill_changed:
                continue
            database.connection.execute(
                "UPDATE messages SET mcp_call = ?, skill = ?, updated_at = ? "
                "WHERE message_id = ?",
                (updated if changed else row["mcp_call"], skill_updated, timestamp, row["message_id"]),
            )
            terminated_calls += int(changed)

        pending_statuses = ", ".join("?" for _ in _PENDING_MCP_STATES)
        pending_values = tuple(sorted(_PENDING_MCP_STATES))
        database.connection.execute(
            f"UPDATE mcp_calls SET status = 'failed', error_code = ?, "
            f"error_message = ? WHERE status IN ({pending_statuses})",
            ("user_extensions_retired", _RETIRED_MESSAGE, *pending_values),
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
    "COMPATIBILITY_SERVICE_VERSION",
    "CompatibilityObserver",
    "compatibility_observer",
    "mcp_endpoint",
    "plugin_endpoint",
    "raise_retired_capability",
    "record_compatibility_observation",
    "retire_user_extensions",
]
