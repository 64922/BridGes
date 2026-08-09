"""显式授权 MCP 插件管理服务（Issue 35）。

按账户安装与治理 MCP 的完整纵向链路：安装描述经安全闭锁（版本锁/来源/
完整性/权限清单）后持久化并锁定描述哈希；运行在独立受限进程中（白名单
环境、惰性启动、崩溃检测、重启孤儿回收）；文件/网络/外部命令/数据访问
全部经宿主工具处理器校验允许清单，未声明或未同意权限一律不可用；每次
调用只接收当前消息明确授权的数据切片；敏感操作（写文件/运行外部命令/
外发）每次调用独立再次确认，拒绝后调用安全终止；撤权会停止新调用并终止
仍依赖该权限的运行；调用统计与审计按账户隔离，不保存秘密与完整私人正文。
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, cast

from bridges.contracts.mcp import (
    McpCallRecord,
    McpCallRequest,
    McpCallResult,
    McpCheckResult,
    McpDataSlice,
    McpError,
    McpListProjection,
    McpPermissionManifest,
    McpSensitiveConfirmation,
    McpSensitiveKind,
    McpServerProjection,
    McpStatus,
)
from bridges.contracts.observability import AuditAction, AuditResult
from bridges.contracts.projects import ObjectDomain, ObjectRef
from bridges.contracts.scope import ScopeAction, ScopeIsolationError
from bridges.mcp.checker import McpDescriptorChecker, require_checker_error
from bridges.mcp.manifest import validate_permissions
from bridges.mcp.process import (
    McpProcessError,
    ToolCallOutcome,
    describe_sensitive_operation,
)
from bridges.mcp.runtime import McpRuntime
from bridges.scope import ScopeEnforcer
from bridges.storage.database import BridgesDatabase
from bridges.storage.repository import BridgesObjectRepository

#: 工具执行限制：读文件/写文件/网络响应大小上限。
_MAX_TOOL_BYTES = 1024 * 1024
_MAX_HTTP_BYTES = 256 * 1024
_HTTP_TIMEOUT_SECONDS = 10.0
_COMMAND_TIMEOUT_SECONDS = 15.0

#: 进程级故障码：崩溃/启动失败/超时/协议错误才标记 MCP failed 状态；
#: 调用级失败（工具被拒/服务器失败/敏感拒绝）进程仍健康，只记录调用。
_PROCESS_LEVEL_CODES = frozenset(
    {"process_crashed", "start_failed", "start_timeout", "call_timeout", "protocol_error"}
)

# 审计 details 白名单：只记录标识、版本、工具名、类别与原因，绝不携带
# 数据切片正文、工具参数正文或秘密。
_AUDIT_DETAIL_KEYS = frozenset(
    {
        "mcp_id",
        "version",
        "tool",
        "data_categories",
        "kind",
        "reason",
        "code",
        "latency_ms",
        "sensitive_ops",
        "status",
    }
)

_MCP_COLUMNS = (
    "mcp_id",
    "account_id",
    "name",
    "version",
    "description",
    "source",
    "integrity",
    "integrity_sha256",
    "command",
    "permissions",
    "status",
    "enabled",
    "object_id",
    "failure_reason",
    "installed_at",
    "updated_at",
)
_MCP_SELECT = ", ".join(_MCP_COLUMNS)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _safe_details(details: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in details.items() if k in _AUDIT_DETAIL_KEYS}


class _ConfirmationEntry:
    """宿主侧挂起的敏感确认条目。"""

    def __init__(
        self,
        *,
        confirmation_id: str,
        account_id: str,
        mcp_id: str,
        request_id: str,
        tool_call_id: int,
        tool: str,
        kind: McpSensitiveKind,
        target: str,
        impact: str,
        data_categories: list[str],
        version: str,
    ) -> None:
        self.confirmation_id = confirmation_id
        self.account_id = account_id
        self.mcp_id = mcp_id
        self.request_id = request_id
        self.tool_call_id = tool_call_id
        self.tool = tool
        self.kind = kind
        self.target = target
        self.impact = impact
        self.data_categories = data_categories
        self.version = version
        self.approved: bool | None = None
        self.started_at = time.monotonic()


class McpService:
    """MCP 插件中心的账户作用域编排服务。"""

    def __init__(
        self,
        *,
        database: BridgesDatabase,
        object_repository: BridgesObjectRepository,
        observability_service: Any,
        runtime: McpRuntime | None = None,
        checker: McpDescriptorChecker | None = None,
        clock: Any = None,
        scope_enforcer: ScopeEnforcer | None = None,
    ) -> None:
        self._database = database
        self._objects = object_repository
        self._observability = observability_service
        self._runtime = runtime or McpRuntime()
        self._checker = checker or McpDescriptorChecker()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._scope_enforcer = scope_enforcer
        self._confirmations: dict[str, _ConfirmationEntry] = {}
        self._confirmations_lock = threading.Lock()
        # Issue 39 AC8：外部命令的受限工作目录——每次服务实例独立随机目录，
        # 固定路径不可被本地进程预置符号链接（目录本身也可能被伪装）。
        self._command_workdir = tempfile.mkdtemp(prefix="bridges-mcp-cmd-")

    def _ensure_not_retired(self) -> None:
        raise McpError(
            "user_extensions_retired",
            "用户 SKILL、插件与通用 MCP 已退役，请返回聊天或知识库。",
            status_code=410,
        )

    # ------------------------------------------------------------------
    # 安装检查与安装
    # ------------------------------------------------------------------

    def check(self, filename: str, content: bytes) -> McpCheckResult:
        """安装前检查：纯函数，不落库、不落对象（取消无残留）。"""
        self._ensure_not_retired()
        if not filename.lower().endswith((".yaml", ".yml")):
            raise McpError(
                "invalid_descriptor",
                "请上传 .yaml/.yml 格式的 MCP 安装描述。",
                status_code=422,
            )
        return self._checker.check(content)

    def install(self, account_id: str, filename: str, content: bytes) -> McpServerProjection:
        """确认安装：重跑安全闭锁，锁定描述哈希后按账户持久化并审计。"""
        self._ensure_not_retired()
        if not filename.lower().endswith((".yaml", ".yml")):
            raise McpError(
                "invalid_descriptor",
                "请上传 .yaml/.yml 格式的 MCP 安装描述。",
                status_code=422,
            )
        result = self._checker.check(content)
        if not result.ok or result.mcp_id is None:
            require_checker_error(result)
        assert (
            result.version is not None
            and result.permissions is not None
            and result.mcp_id is not None
        )
        stored = self._objects.create_object(
            account_id, filename, content, media_type="application/yaml"
        )
        try:
            scoped = self._database.scoped(account_id)
            existing = scoped.execute(
                "SELECT mcp_id, status FROM mcp_servers WHERE account_id = ? AND mcp_id = ?",
                (account_id, result.mcp_id),
            ).fetchone()
            if existing is not None and existing[1] != McpStatus.FAILED.value:
                self._objects.delete_object(account_id, stored.object_id)
                raise McpError(
                    "mcp_conflict",
                    f"已安装同标识 MCP {result.mcp_id}（版本 {result.version}）；"
                    "如需替换请先卸载现有版本。",
                    status_code=409,
                )
            # 失败态覆盖重装：更新既有行，并回收旧描述对象（防孤儿）。
            if existing is not None:
                old_object = scoped.execute(
                    "SELECT object_id FROM mcp_servers WHERE account_id = ? AND mcp_id = ?",
                    (account_id, result.mcp_id),
                ).fetchone()
                if old_object is not None and old_object[0]:
                    with suppress(Exception):
                        self._objects.delete_object(account_id, old_object[0])
                scoped.execute(
                    "UPDATE mcp_servers SET name = ?, version = ?, description = ?,"
                    " source = ?, integrity = ?, integrity_sha256 = ?, command = ?,"
                    " permissions = ?, status = 'healthy', enabled = 1,"
                    " object_id = ?, failure_reason = NULL, updated_at = ?"
                    " WHERE account_id = ? AND mcp_id = ?",
                    (
                        result.name,
                        result.version,
                        result.description,
                        result.source,
                        result.integrity,
                        result.integrity_sha256,
                        json.dumps(result.command, ensure_ascii=False),
                        result.permissions.model_dump_json(),
                        stored.object_id,
                        _now(),
                        account_id,
                        result.mcp_id,
                    ),
                )
            else:
                scoped.execute(
                    "INSERT INTO mcp_servers ("
                    " mcp_id, account_id, name, version, description, source,"
                    " integrity, integrity_sha256, command, permissions, status,"
                    " enabled, object_id, failure_reason, installed_at, updated_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'healthy', 1, ?,"
                    " NULL, ?, ?)",
                    (
                        result.mcp_id,
                        account_id,
                        result.name,
                        result.version,
                        result.description,
                        result.source,
                        result.integrity,
                        result.integrity_sha256,
                        json.dumps(result.command, ensure_ascii=False),
                        result.permissions.model_dump_json(),
                        stored.object_id,
                        _now(),
                        _now(),
                    ),
                )
        except McpError:
            raise
        except Exception as exc:
            self._objects.delete_object(account_id, stored.object_id)
            raise McpError(
                "install_failed",
                f"安装失败：{exc}",
                status_code=503,
                retryable=True,
            ) from exc
        self._audit(
            account_id,
            AuditAction.MCP_INSTALL,
            AuditResult.SUCCESS,
            {
                "mcp_id": result.mcp_id,
                "version": result.version,
                "data_categories": result.permissions.data_categories,
            },
        )
        return self._row_to_projection(account_id, result.mcp_id)

    # ------------------------------------------------------------------
    # 列表 / 启停 / 卸载 / 撤权
    # ------------------------------------------------------------------

    def list_servers(self, account_id: str) -> McpListProjection:
        scoped = self._database.scoped(account_id)
        rows = scoped.execute(
            f"SELECT {_MCP_SELECT} FROM mcp_servers WHERE account_id = ?"
            " ORDER BY installed_at DESC",
            (account_id,),
        ).fetchall()
        servers = []
        for row in rows:
            mcp_id = row[0]
            if isinstance(mcp_id, str):
                servers.append(self._row_to_projection(account_id, mcp_id))
        return McpListProjection(servers=servers)

    def get_calls(self, account_id: str, mcp_id: str) -> list[McpCallRecord]:
        """返回最近调用记录（最多 50 条，不含输入与正文）。"""
        self._require_server(account_id, mcp_id)
        scoped = self._database.scoped(account_id)
        rows = scoped.execute(
            "SELECT call_id, mcp_id, tool, status, error_code, error_message,"
            " latency_ms, sensitive_ops, created_at FROM mcp_calls"
            " WHERE account_id = ? AND mcp_id = ?"
            " ORDER BY created_at DESC, rowid DESC LIMIT 50",
            (account_id, mcp_id),
        ).fetchall()
        return [
            McpCallRecord(
                call_id=row[0],
                mcp_id=row[1],
                tool=row[2],
                status=row[3],
                error_code=row[4],
                error_message=row[5],
                latency_ms=int(row[6]),
                sensitive_ops=int(row[7]),
                created_at=datetime.fromisoformat(row[8]),
            )
            for row in rows
        ]

    def set_enabled(self, account_id: str, mcp_id: str, enabled: bool) -> None:
        self._ensure_not_retired()
        row = self._require_server(account_id, mcp_id)
        if row[10] == McpStatus.FAILED.value and enabled:
            raise McpError(
                "mcp_failed",
                "该 MCP 处于失败状态，请先重新安装或修正后重试。",
                status_code=409,
                retryable=True,
            )
        status = McpStatus.HEALTHY.value if enabled else McpStatus.DISABLED.value
        scoped = self._database.scoped(account_id)
        scoped.execute(
            "UPDATE mcp_servers SET status = ?, enabled = ?, updated_at = ?"
            " WHERE account_id = ? AND mcp_id = ?",
            (status, 1 if enabled else 0, _now(), account_id, mcp_id),
        )
        if not enabled:
            self._runtime.stop(account_id, mcp_id)
        self._audit(
            account_id,
            AuditAction.MCP_ENABLE if enabled else AuditAction.MCP_DISABLE,
            AuditResult.SUCCESS,
            {"mcp_id": mcp_id, "version": row[3]},
        )

    def uninstall(self, account_id: str, mcp_id: str) -> None:
        self._ensure_not_retired()
        row = self._require_server(account_id, mcp_id)
        scoped = self._database.scoped(account_id)
        scoped.execute(
            "DELETE FROM mcp_servers WHERE account_id = ? AND mcp_id = ?",
            (account_id, mcp_id),
        )
        self._runtime.stop(account_id, mcp_id)
        if row[12]:
            with suppress(Exception):
                self._objects.delete_object(account_id, row[12])
        self._audit(
            account_id,
            AuditAction.MCP_UNINSTALL,
            AuditResult.SUCCESS,
            {"mcp_id": mcp_id, "version": row[3]},
        )

    def revoke_permissions(
        self, account_id: str, mcp_id: str, manifest: McpPermissionManifest
    ) -> McpServerProjection:
        """撤权：以新清单替换；移除敏感权限时终止仍依赖该权限的运行。"""
        self._ensure_not_retired()
        row = self._require_server(account_id, mcp_id)
        reasons: list[str] = []
        validate_permissions(manifest, reasons)
        if reasons:
            raise McpError(
                "invalid_descriptor",
                "；".join(reasons),
                status_code=422,
            )
        old_manifest = McpPermissionManifest.model_validate(json.loads(row[9]))
        # 撤走敏感权限（写目录/外部命令/敏感操作类别）时终止运行中的进程：
        # 新调用立即用新清单，旧进程不再持有已撤回的能力。
        sensitive_removed = bool(
            (set(old_manifest.filesystem_write) - set(manifest.filesystem_write))
            or (set(old_manifest.external_commands) - set(manifest.external_commands))
            or (set(old_manifest.sensitive_operations) - set(manifest.sensitive_operations))
        )
        if sensitive_removed:
            # 终止仍依赖已撤权限的运行，状态进入可观察的 stopped；
            # 下次调用以新清单惰性重启（AC3 停止状态）。
            self._runtime.stop(account_id, mcp_id)
        scoped = self._database.scoped(account_id)
        scoped.execute(
            "UPDATE mcp_servers SET permissions = ?, status = ?, updated_at = ?"
            " WHERE account_id = ? AND mcp_id = ?",
            (
                manifest.model_dump_json(),
                McpStatus.STOPPED.value if sensitive_removed else McpStatus.HEALTHY.value,
                _now(),
                account_id,
                mcp_id,
            ),
        )
        self._audit(
            account_id,
            AuditAction.MCP_PERMISSIONS_REVOKE,
            AuditResult.SUCCESS,
            {
                "mcp_id": mcp_id,
                "version": row[3],
                "data_categories": manifest.data_categories,
                "reason": "sensitive_removed" if sensitive_removed else "update",
            },
        )
        return self._row_to_projection(account_id, mcp_id)

    # ------------------------------------------------------------------
    # 调用与敏感确认
    # ------------------------------------------------------------------

    def invoke(self, account_id: str, mcp_id: str, request: McpCallRequest) -> McpCallResult:
        """执行一次真实调用；只接收清单声明的数据切片。"""
        self._ensure_not_retired()
        row = self._require_server(account_id, mcp_id)
        if row[10] == McpStatus.FAILED.value:
            raise McpError(
                "mcp_failed",
                f"MCP 服务器不可用：{row[13] or '启动失败'}",
                status_code=409,
                retryable=True,
            )
        if not row[11] or row[10] == McpStatus.DISABLED.value:
            raise McpError(
                "mcp_disabled",
                "该 MCP 已停用，请先启用后再调用。",
                status_code=409,
            )
        if row[10] == McpStatus.STOPPED.value:
            # 撤权后的停止态：配置合法，惰性重启并回到 healthy。
            scoped = self._database.scoped(account_id)
            scoped.execute(
                "UPDATE mcp_servers SET status = 'healthy', updated_at = ?"
                " WHERE account_id = ? AND mcp_id = ?",
                (_now(), account_id, mcp_id),
            )
        manifest = McpPermissionManifest.model_validate(json.loads(row[9]))
        # AC6 数据切片校验：只传当前消息明确授权的类别，未声明即拒绝。
        denied_reason = _validate_data_slice(request.data_slice, manifest)
        if denied_reason is not None:
            self._audit(
                account_id,
                AuditAction.MCP_INVOKE_DENIED,
                AuditResult.DENIED,
                {
                    "mcp_id": mcp_id,
                    "version": row[3],
                    "tool": request.tool,
                    "reason": denied_reason,
                },
            )
            raise McpError("data_slice_denied", denied_reason, status_code=403)

        command = _load_command(row[8])
        try:
            client = self._runtime.get_or_start(account_id, mcp_id, command)
        except McpProcessError as exc:
            self._mark_start_failed(account_id, mcp_id, row[3], exc)
            raise McpError(
                "mcp_start_failed",
                exc.message,
                status_code=503,
                retryable=exc.code == "start_timeout",
            ) from exc
        request_id = f"invoke-{uuid.uuid4().hex[:12]}"
        started = time.monotonic()
        try:
            with client.call_lock:
                status, payload = client.invoke(
                    request_id=request_id,
                    tool=request.tool,
                    input_data=request.input,
                    data_slice=request.data_slice.model_dump(mode="json"),
                    permissions=manifest,
                    handler=lambda tool, args, force: self._tool_handler(
                        account_id, mcp_id, row[3], manifest, tool, args, force
                    ),
                )
        except McpProcessError as exc:
            # 进程级故障（崩溃/超时/协议错误）标记 failed；调用级失败
            # （工具被拒/服务器失败/敏感拒绝）进程仍健康，只记录调用。
            if _PROCESS_LEVEL_CODES.intersection({exc.code}):
                self._mark_start_failed(account_id, mcp_id, row[3], exc)
            self._record_call(
                account_id,
                mcp_id,
                request.tool,
                "failed",
                exc.code,
                exc.message,
                int((time.monotonic() - started) * 1000),
            )
            self._audit(
                account_id,
                AuditAction.MCP_INVOKE,
                AuditResult.DENIED
                if exc.code == "sensitive_denied"
                else AuditResult.RETRYABLE_FAIL,
                {
                    "mcp_id": mcp_id,
                    "version": row[3],
                    "tool": request.tool,
                    "code": exc.code,
                    "reason": exc.message,
                },
            )
            return McpCallResult(
                status="failed",
                error_code=exc.code,
                error_message=exc.message,
            )
        if status == "sensitive_pending":
            confirmation = self._register_confirmation(
                account_id,
                mcp_id,
                row[3],
                request_id,
                payload["tool_call_id"],
                payload["confirmation"],
                request.tool,
                request.data_slice,
            )
            return McpCallResult(
                status="sensitive_pending",
                confirmation=confirmation,
            )
        latency = int((time.monotonic() - started) * 1000)
        self._record_call(account_id, mcp_id, request.tool, "success", None, None, latency)
        self._audit(
            account_id,
            AuditAction.MCP_INVOKE,
            AuditResult.SUCCESS,
            {
                "mcp_id": mcp_id,
                "version": row[3],
                "tool": request.tool,
                "latency_ms": latency,
            },
        )
        return McpCallResult(status="success", result=payload)

    def approve(self, account_id: str, mcp_id: str, confirmation_id: str) -> McpCallResult:
        """确认敏感操作后恢复调用（确认仅对本次调用有效）。"""
        self._ensure_not_retired()
        return self._resolve_confirmation(account_id, mcp_id, confirmation_id, approved=True)

    def deny(self, account_id: str, mcp_id: str, confirmation_id: str) -> McpCallResult:
        """拒绝敏感操作：调用安全终止，不执行任何操作。"""
        self._ensure_not_retired()
        return self._resolve_confirmation(account_id, mcp_id, confirmation_id, approved=False)

    def _resolve_confirmation(
        self, account_id: str, mcp_id: str, confirmation_id: str, *, approved: bool
    ) -> McpCallResult:
        with self._confirmations_lock:
            entry = self._confirmations.get(confirmation_id)
        if entry is None or entry.account_id != account_id or entry.mcp_id != mcp_id:
            raise McpError(
                "confirmation_not_found",
                "未找到该确认请求，可能已过期或不属于当前账户。",
                status_code=404,
            )
        client = self._runtime.get_running(account_id, mcp_id)
        if client is None:
            with self._confirmations_lock:
                self._confirmations.pop(confirmation_id, None)
            raise McpError(
                "confirmation_stale",
                "MCP 服务器已停止，该确认已失效。",
                status_code=409,
            )
        entry.approved = approved
        manifest = self._manifest_for(account_id, mcp_id)
        started = time.monotonic()
        try:
            with client.call_lock:
                status, payload = client.resume(
                    request_id=entry.request_id,
                    tool_call_id=entry.tool_call_id,
                    approved=approved,
                    handler=lambda tool, args, force: self._tool_handler(
                        account_id, mcp_id, entry.version, manifest, tool, args, force
                    ),
                )
        except McpProcessError as exc:
            with self._confirmations_lock:
                self._confirmations.pop(confirmation_id, None)
            if exc.code != "sensitive_denied":
                self._mark_start_failed(account_id, mcp_id, entry.version, exc)
            latency = int((time.monotonic() - started) * 1000)
            call_status = "denied" if exc.code == "sensitive_denied" else "failed"
            self._record_call(
                account_id, mcp_id, entry.tool, call_status, exc.code, exc.message, latency
            )
            self._audit(
                account_id,
                AuditAction.MCP_SENSITIVE_DENY
                if not approved
                else AuditAction.MCP_SENSITIVE_APPROVE,
                AuditResult.BLOCKED
                if exc.code == "sensitive_denied"
                else AuditResult.RETRYABLE_FAIL,
                {
                    "mcp_id": mcp_id,
                    "version": entry.version,
                    "tool": entry.tool,
                    "kind": entry.kind.value,
                    "reason": exc.message,
                },
            )
            return McpCallResult(
                status="failed",
                error_code=exc.code,
                error_message=exc.message,
            )
        with self._confirmations_lock:
            self._confirmations.pop(confirmation_id, None)
        latency = int((time.monotonic() - started) * 1000)
        if status == "sensitive_pending":
            # 同一调用内的下一次敏感操作：登记新确认并返回挂起，
            # 而不是把挂起误判为拒绝（多敏感操作调用链）。
            confirmation = self._register_confirmation(
                account_id,
                mcp_id,
                entry.version,
                entry.request_id,
                payload["tool_call_id"],
                payload["confirmation"],
                entry.tool,
                McpDataSlice(),
            )
            return McpCallResult(
                status="sensitive_pending",
                confirmation=confirmation,
            )
        if status == "success":
            self._record_call(
                account_id, mcp_id, entry.tool, "success", None, None, latency, sensitive_ops=1
            )
            self._audit(
                account_id,
                AuditAction.MCP_SENSITIVE_APPROVE,
                AuditResult.SUCCESS,
                {
                    "mcp_id": mcp_id,
                    "version": entry.version,
                    "tool": entry.tool,
                    "kind": entry.kind.value,
                    "latency_ms": latency,
                },
            )
            return McpCallResult(status="success", result=payload)
        # denied（或失败）路径：服务器以失败结果结束。
        error = payload if isinstance(payload, dict) else {}
        code = str(error.get("code") or "sensitive_denied")
        message = str(error.get("message") or ("用户拒绝了该敏感操作，调用已安全终止。"))
        self._record_call(account_id, mcp_id, entry.tool, "denied", code, message, latency)
        self._audit(
            account_id,
            AuditAction.MCP_SENSITIVE_DENY,
            AuditResult.BLOCKED,
            {
                "mcp_id": mcp_id,
                "version": entry.version,
                "tool": entry.tool,
                "kind": entry.kind.value,
                "reason": message,
            },
        )
        return McpCallResult(
            status="failed",
            error_code=code,
            error_message=message,
        )

    # ------------------------------------------------------------------
    # 工具处理器（宿主强制层）
    # ------------------------------------------------------------------

    def _tool_handler(
        self,
        account_id: str,
        mcp_id: str,
        version: str,
        manifest: McpPermissionManifest,
        tool: str,
        arguments: dict[str, Any],
        force: bool,
    ) -> ToolCallOutcome:
        """校验允许清单并执行工具；敏感操作未确认时返回挂起。"""
        if tool == "read_file":
            path = str(arguments.get("path") or "")
            if not _path_allowed(path, manifest.filesystem_read):
                self._denied_audit(account_id, mcp_id, version, tool, "未授权读取路径")
                return ToolCallOutcome(
                    False,
                    error_code="permission_denied",
                    error_message=f"读取路径「{path}」不在允许清单内。",
                )
            try:
                with open(path, "rb") as file:
                    raw = file.read(_MAX_TOOL_BYTES + 1)
                if len(raw) > _MAX_TOOL_BYTES:
                    return ToolCallOutcome(
                        False,
                        error_code="tool_limited",
                        error_message=f"文件超过 {_MAX_TOOL_BYTES // 1024} KB 读取上限。",
                    )
                return ToolCallOutcome(True, result={"path": path, "content": _decode_text(raw)})
            except OSError as exc:
                return ToolCallOutcome(
                    False, error_code="tool_error", error_message=f"读取失败：{exc}"
                )
        if tool == "write_file":
            path = str(arguments.get("path") or "")
            content = str(arguments.get("content") or "")
            if not _path_allowed(path, manifest.filesystem_write):
                self._denied_audit(account_id, mcp_id, version, tool, "未授权写入路径")
                return ToolCallOutcome(
                    False,
                    error_code="permission_denied",
                    error_message=f"写入路径「{path}」不在允许清单内。",
                )
            confirmation = self._sensitive_confirmation(
                account_id, mcp_id, version, tool, McpSensitiveKind.WRITE_FILE, arguments
            )
            if not force:
                return ToolCallOutcome(False, sensitive=confirmation)
            try:
                directory = os.path.dirname(path)
                if directory:
                    os.makedirs(directory, exist_ok=True)
                with open(path, "w", encoding="utf-8") as file:
                    file.write(content[:_MAX_TOOL_BYTES])
                return ToolCallOutcome(
                    True, result={"path": path, "bytes": min(len(content), _MAX_TOOL_BYTES)}
                )
            except OSError as exc:
                return ToolCallOutcome(
                    False, error_code="tool_error", error_message=f"写入失败：{exc}"
                )
        if tool == "http_get":
            url = str(arguments.get("url") or "")
            if not _url_allowed(url, manifest.network_domains):
                self._denied_audit(account_id, mcp_id, version, tool, "未声明网络域名")
                return ToolCallOutcome(
                    False,
                    error_code="permission_denied",
                    error_message=f"目标域名「{_hostname(url)}」不在允许清单内。",
                )
            confirmation = self._sensitive_confirmation(
                account_id, mcp_id, version, tool, McpSensitiveKind.SEND_EXTERNAL, arguments
            )
            if not force:
                return ToolCallOutcome(False, sensitive=confirmation)
            try:
                # 禁止重定向跟随（Issue 39 AC8）：允许域名内的 URL 可被
                # 302 重定向到未授权域名或内网地址，重定向即视为拒绝。
                with _no_redirect_opener().open(url, timeout=_HTTP_TIMEOUT_SECONDS) as response:
                    raw = response.read(_MAX_HTTP_BYTES + 1)
                if len(raw) > _MAX_HTTP_BYTES:
                    return ToolCallOutcome(
                        False,
                        error_code="tool_limited",
                        error_message=f"响应超过 {_MAX_HTTP_BYTES // 1024} KB 上限。",
                    )
                return ToolCallOutcome(True, result={"url": url, "content": _decode_text(raw)})
            except urllib.error.HTTPError as exc:
                if exc.code in (301, 302, 303, 307, 308):
                    self._denied_audit(
                        account_id, mcp_id, version, tool, "重定向目标未授权"
                    )
                    return ToolCallOutcome(
                        False,
                        error_code="permission_denied",
                        error_message="目标地址发生重定向，已拒绝跟随（重定向目标不在允许清单内）。",
                    )
                return ToolCallOutcome(
                    False, error_code="tool_error", error_message=f"请求失败（HTTP {exc.code}）。"
                )
            except (urllib.error.URLError, OSError, ValueError) as exc:
                return ToolCallOutcome(
                    False, error_code="tool_error", error_message=f"请求失败：{exc}"
                )
        if tool == "run_command":
            raw_command = str(arguments.get("command") or "")
            if not raw_command:
                return ToolCallOutcome(
                    False, error_code="permission_denied", error_message="缺少要执行的命令。"
                )
            program = shlex.split(raw_command, posix=False)[0] if raw_command.strip() else ""
            if not program or program not in manifest.external_commands:
                self._denied_audit(account_id, mcp_id, version, tool, "未声明外部命令")
                return ToolCallOutcome(
                    False,
                    error_code="permission_denied",
                    error_message=f"外部命令「{program}」不在允许清单内。",
                )
            confirmation = self._sensitive_confirmation(
                account_id, mcp_id, version, tool, McpSensitiveKind.RUN_COMMAND, arguments
            )
            if not force:
                return ToolCallOutcome(False, sensitive=confirmation)
            clean_env = {
                "PYTHONPATH": os.pathsep.join(sys.path),
                "PATH": os.environ.get("PATH", ""),
                "PYTHONIOENCODING": "utf-8",
            }
            try:
                completed = subprocess.run(
                    shlex.split(raw_command, posix=False),
                    capture_output=True,
                    text=True,
                    timeout=_COMMAND_TIMEOUT_SECONDS,
                    shell=False,
                    env=clean_env,
                    cwd=self._command_workdir,
                )
            except (OSError, ValueError) as exc:
                return ToolCallOutcome(
                    False, error_code="tool_error", error_message=f"命令执行失败：{exc}"
                )
            except subprocess.TimeoutExpired:
                return ToolCallOutcome(
                    False,
                    error_code="tool_timeout",
                    error_message=f"命令执行超过 {_COMMAND_TIMEOUT_SECONDS:.0f} 秒，已终止。",
                )
            return ToolCallOutcome(
                True,
                result={
                    "returncode": completed.returncode,
                    "stdout": completed.stdout[:_MAX_TOOL_BYTES],
                    "stderr": completed.stderr[:_MAX_TOOL_BYTES],
                },
            )
        self._denied_audit(account_id, mcp_id, version, tool, "未声明工具")
        return ToolCallOutcome(
            False,
            error_code="permission_denied",
            error_message=f"工具「{tool}」不在允许清单内。",
        )

    def _sensitive_confirmation(
        self,
        account_id: str,
        mcp_id: str,
        version: str,
        tool: str,
        kind: McpSensitiveKind,
        arguments: dict[str, Any],
    ) -> McpSensitiveConfirmation:
        target, impact = describe_sensitive_operation(kind, tool, arguments)
        return McpSensitiveConfirmation(
            confirmation_id=f"conf-{uuid.uuid4().hex[:12]}",
            mcp_id=mcp_id,
            kind=kind,
            tool=tool,
            target=target,
            impact=impact,
            status="pending",
        )

    def _register_confirmation(
        self,
        account_id: str,
        mcp_id: str,
        version: str,
        request_id: str,
        tool_call_id: int,
        raw: dict[str, Any],
        tool: str,
        data_slice: McpDataSlice,
    ) -> McpSensitiveConfirmation:
        confirmation = McpSensitiveConfirmation.model_validate(raw)
        entry = _ConfirmationEntry(
            confirmation_id=confirmation.confirmation_id,
            account_id=account_id,
            mcp_id=mcp_id,
            request_id=request_id,
            tool_call_id=tool_call_id,
            tool=tool,
            kind=confirmation.kind,
            target=confirmation.target,
            impact=confirmation.impact,
            data_categories=_slice_categories(data_slice),
            version=version,
        )
        with self._confirmations_lock:
            self._confirmations[confirmation.confirmation_id] = entry
        self._audit(
            account_id,
            AuditAction.MCP_INVOKE,
            AuditResult.DEGRADED,
            {
                "mcp_id": mcp_id,
                "version": version,
                "tool": tool,
                "reason": "sensitive_pending",
            },
        )
        return confirmation

    def _manifest_for(self, account_id: str, mcp_id: str) -> McpPermissionManifest:
        row = self._require_server(account_id, mcp_id)
        return McpPermissionManifest.model_validate(json.loads(row[9]))

    def shutdown(self) -> None:
        """应用关闭时停止全部 MCP 进程（不遗留子进程）。"""
        self._runtime.stop_all()

    def reap_orphans(self) -> int:
        """回收上次异常退出遗留的孤儿 MCP 进程（应用启动时调用）。"""
        return self._runtime.reap_orphans()

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _require_server(self, account_id: str, mcp_id: str) -> tuple[Any, ...]:
        # Issue 44：MCP 对象读取与 vault/media/workflows 回答同一问题——先取
        # 对象真实所有者交给 scope enforcer 判定，再以账户作用域查询取行
        # （scoped 仍是纵深防御）。跨账户/不存在统一 404，不泄漏存在性。
        if self._scope_enforcer is not None:
            # 同一 mcp_id 可被多账户各自安装（表仅 UNIQUE(account_id, mcp_id)），
            # 必须对全部真实所有者逐一判定，避免把合法所有者误判为跨账户。
            owners = self._database.connection.execute(
                "SELECT account_id FROM mcp_servers WHERE mcp_id = ?",
                (mcp_id,),
            ).fetchall()
            if owners:
                subject = ScopeEnforcer.service_subject(account_id, "mcp")
                authorized = False
                last_error: ScopeIsolationError | None = None
                for (owner,) in owners:
                    ref = ObjectRef(
                        domain=ObjectDomain.PERSONAL_VAULT,
                        owner_id=str(owner),
                        object_id=mcp_id,
                        version=1,
                    )
                    try:
                        self._scope_enforcer.authorize(subject, ScopeAction.READ, ref)
                    except ScopeIsolationError as exc:
                        last_error = exc
                        continue
                    authorized = True
                    break
                if not authorized:
                    raise McpError(
                        "mcp_not_found",
                        "未找到该 MCP，可能已卸载或不属于当前账户。",
                        status_code=404,
                    ) from last_error
        scoped = self._database.scoped(account_id)
        row = scoped.execute(
            f"SELECT {_MCP_SELECT} FROM mcp_servers WHERE account_id = ? AND mcp_id = ?",
            (account_id, mcp_id),
        ).fetchone()
        if row is None:
            raise McpError(
                "mcp_not_found",
                "未找到该 MCP，可能已卸载或不属于当前账户。",
                status_code=404,
            )
        assert row is not None
        return cast(tuple[Any, ...], row)

    def _mark_start_failed(
        self, account_id: str, mcp_id: str, version: str, exc: McpProcessError
    ) -> None:
        """启动失败/崩溃：持久化 failed 状态并审计（下次调用恢复尝试）。"""
        self._runtime.stop(account_id, mcp_id)
        scoped = self._database.scoped(account_id)
        scoped.execute(
            "UPDATE mcp_servers SET status = 'failed', failure_reason = ?,"
            " updated_at = ? WHERE account_id = ? AND mcp_id = ?",
            (exc.message, _now(), account_id, mcp_id),
        )
        self._audit(
            account_id,
            AuditAction.MCP_START_FAILED,
            AuditResult.RETRYABLE_FAIL,
            {
                "mcp_id": mcp_id,
                "version": version,
                "code": exc.code,
                "reason": exc.message,
            },
        )

    def _denied_audit(
        self, account_id: str, mcp_id: str, version: str, tool: str, reason: str
    ) -> None:
        """工具权限校验失败：拒绝并审计（直接/间接越权尝试均被记录）。"""
        self._audit(
            account_id,
            AuditAction.MCP_INVOKE_DENIED,
            AuditResult.DENIED,
            {"mcp_id": mcp_id, "version": version, "tool": tool, "reason": reason},
        )

    def _record_call(
        self,
        account_id: str,
        mcp_id: str,
        tool: str,
        status: str,
        error_code: str | None,
        error_message: str | None,
        latency_ms: int,
        sensitive_ops: int = 0,
    ) -> None:
        scoped = self._database.scoped(account_id)
        scoped.execute(
            "INSERT INTO mcp_calls (call_id, account_id, mcp_id, tool, status,"
            " error_code, error_message, latency_ms, sensitive_ops, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                account_id,
                mcp_id,
                tool,
                status,
                error_code,
                error_message,
                latency_ms,
                sensitive_ops,
                _now(),
            ),
        )

    def _row_to_projection(self, account_id: str, mcp_id: str) -> McpServerProjection:
        row = self._require_server(account_id, mcp_id)
        (
            row_mcp_id,
            _row_account,
            name,
            version,
            description,
            source,
            integrity,
            integrity_sha256,
            command,
            permissions,
            status,
            enabled,
            _object_id,
            failure_reason,
            installed_at,
            updated_at,
        ) = row
        call_stats = self._call_stats(account_id, mcp_id)
        return McpServerProjection(
            mcp_id=row_mcp_id,
            name=name,
            version=version,
            description=description,
            source=source,
            integrity=integrity
            or (f"sha256:{integrity_sha256[:16]}…" if integrity_sha256 else None),
            command=_load_command(command),
            permissions=McpPermissionManifest.model_validate(json.loads(permissions)),
            status=McpStatus(status),
            enabled=bool(enabled),
            failure_reason=failure_reason,
            installed_at=datetime.fromisoformat(installed_at),
            updated_at=datetime.fromisoformat(updated_at),
            call_count=call_stats[0],
            last_call_status=call_stats[1],
            last_call_error=call_stats[2],
            last_call_at=call_stats[3],
        )

    def _call_stats(self, account_id: str, mcp_id: str) -> tuple[int, str | None, str | None, Any]:
        scoped = self._database.scoped(account_id)
        row = scoped.execute(
            "SELECT COUNT(*),"
            " (SELECT status FROM mcp_calls c2 WHERE c2.account_id = ? AND"
            "  c2.mcp_id = ? ORDER BY c2.created_at DESC, c2.rowid DESC LIMIT 1),"
            " (SELECT error_message FROM mcp_calls c3 WHERE c3.account_id = ? AND"
            "  c3.mcp_id = ? ORDER BY c3.created_at DESC, c3.rowid DESC LIMIT 1),"
            " (SELECT created_at FROM mcp_calls c4 WHERE c4.account_id = ? AND"
            "  c4.mcp_id = ? ORDER BY c4.created_at DESC, c4.rowid DESC LIMIT 1)"
            " FROM mcp_calls WHERE account_id = ? AND mcp_id = ?",
            (account_id, mcp_id, account_id, mcp_id, account_id, mcp_id, account_id, mcp_id),
        ).fetchone()
        last_at = row[3]
        return (
            int(row[0]),
            row[1],
            row[2],
            datetime.fromisoformat(last_at) if last_at else None,
        )

    def _audit(
        self,
        account_id: str,
        action: AuditAction,
        result: AuditResult,
        details: dict[str, Any],
    ) -> None:
        self._observability.log_audit(
            actor_account_id=account_id,
            action=action,
            result=result,
            reason=None,
            details=_safe_details(details),
        )


def _load_command(raw: str) -> list[str]:
    try:
        value = json.loads(raw)
        if isinstance(value, list):
            return [str(item) for item in value]
    except (ValueError, TypeError):
        pass
    return []


def _decode_text(raw: bytes) -> str:
    for encoding in ("utf-8", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _path_allowed(path: str, directories: list[str]) -> bool:
    """路径必须在声明目录之内（规范化前缀匹配，跨盘符安全）。

    Issue 39 AC8：先解析符号链接再匹配，防止允许目录内放置指向外部的
    链接逃逸读取/写入；解析失败（不存在/无权限）一律拒绝。
    """
    if not directories or not path:
        return False
    try:
        norm = os.path.normcase(os.path.normpath(path))
    except (TypeError, ValueError):
        return False
    if not os.path.isabs(norm):
        return False
    # 符号链接逃逸防护（Issue 39 AC8）：只比较真实路径。
    # 词法路径在允许目录内不代表安全——允许目录内的链接可指向外部；
    # 必须验证「解析符号链接后的真实目标」落在「允许目录的真实路径」内，
    # 否则链接逃逸（read/write 越界）会被错误放行。
    try:
        real = os.path.normcase(os.path.realpath(path))
    except (TypeError, ValueError, OSError):
        return False
    if not real:
        return False
    for directory in directories:
        try:
            real_dir = os.path.normcase(os.path.realpath(directory))
        except (TypeError, ValueError, OSError):
            continue
        if not real_dir:
            continue
        if real == real_dir or real.startswith(real_dir.rstrip(os.sep) + os.sep):
            return True
    return False


def _no_redirect_opener() -> Any:
    """构造不跟随重定向的 urlopen opener（Issue 39 AC8 重定向闭锁）。"""
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args: Any, **kwargs: Any) -> None:
            raise urllib.error.HTTPError(
                args[0], 302, "重定向已禁止", None, None
            )

    return urllib.request.build_opener(_NoRedirect)


def _url_allowed(url: str, domains: list[str]) -> bool:
    if not domains or not url:
        return False
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return False
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    return parsed.hostname.lower() in {domain.lower() for domain in domains}


def _hostname(url: str) -> str:
    try:
        parsed = urllib.parse.urlparse(url)
        return str(parsed.hostname or url)
    except ValueError:
        return url


def _validate_data_slice(data_slice: McpDataSlice, manifest: McpPermissionManifest) -> str | None:
    """数据切片与清单类别校验：只接收当前调用明确授权的数据。"""
    declared = set(manifest.data_categories)
    if data_slice.text and "current_message_text" not in declared:
        return "本次调用未声明接收当前消息文本（current_message_text），已拒绝传递。"
    if data_slice.attachments and "attachment_files" not in declared:
        return "本次调用未声明接收附件数据（attachment_files），已拒绝传递。"
    return None


def _slice_categories(data_slice: McpDataSlice) -> list[str]:
    categories: list[str] = []
    if data_slice.text:
        categories.append("current_message_text")
    if data_slice.attachments:
        categories.append("attachment_files")
    return categories


__all__ = ["McpService"]
