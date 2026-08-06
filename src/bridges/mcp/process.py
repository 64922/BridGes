"""MCP 受限 worker 进程客户端（Issue 35）。

MCP 服务器程序是遵循 BridGes JSONL 协议的外部命令（stdio 模式，与
arXiv worker 同风格）。本客户端负责：

- 以白名单环境启动命令（只保留 PYTHONPATH 与必要 PATH，绝不继承百炼
  密钥、QQ SMTP 授权码、内部加密密钥或其余环境秘密）；
- ``initialize`` 握手（启动超时内完成即健康，超时按启动失败处理）；
- ``invoke`` 请求往返：服务器可经 ``tool_call`` 请求工具（文件/网络/
  外部命令/数据），宿主工具处理器校验允许清单后返回结果或敏感挂起；
- 敏感确认恢复（``confirm_sensitive`` → ``resume`` 完成挂起调用）；
- 进程崩溃检测、响应超时与 terminate/kill 回收。

所有 I/O 都经宿主工具处理器；本客户端不直接执行任何 MCP 请求。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from bridges.contracts.mcp import (
    McpPermissionManifest,
    McpSensitiveConfirmation,
    McpSensitiveKind,
)

#: initialize 握手超时（秒）：超时视为启动失败。
STARTUP_TIMEOUT_SECONDS = 8.0
#: 单次工具调用往返超时（秒）。
TOOL_TIMEOUT_SECONDS = 20.0


class McpProcessError(Exception):
    """MCP 进程域错误；message 为面向用户的中文说明。"""

    def __init__(self, code: str, message: str, *, permission: bool = False) -> None:
        self.code = code
        self.message = message
        self.permission = permission
        super().__init__(message)


@dataclass(frozen=True)
class ToolCallOutcome:
    """宿主对一次工具调用的处理结果。"""

    ok: bool
    result: Any | None = None
    error_code: str | None = None
    error_message: str | None = None
    sensitive: McpSensitiveConfirmation | None = None


@dataclass
class _PendingConfirmation:
    """宿主侧挂起的敏感确认上下文。"""

    request_id: str
    tool_call_id: int
    tool: str
    arguments: dict[str, Any]
    confirmation: McpSensitiveConfirmation
    confirmed: bool = False


#: 宿主工具处理器：校验允许清单并执行（或挂起敏感确认）。
#: ``force=True`` 表示该敏感工具调用已获用户确认，必须直接执行。
ToolHandler = Callable[[str, dict[str, Any], bool], ToolCallOutcome]


class McpProcessClient:
    """通过 JSONL 与受限 worker 通信的进程客户端。"""

    def __init__(
        self,
        *,
        command: list[str],
        python_executable: str | None = None,
        startup_timeout: float = STARTUP_TIMEOUT_SECONDS,
        tool_timeout: float = TOOL_TIMEOUT_SECONDS,
        cwd: str | None = None,
    ) -> None:
        self._command = list(command)
        self._python_executable = python_executable or sys.executable
        self._startup_timeout = startup_timeout
        self._tool_timeout = tool_timeout
        #: 受限工作目录（Issue 39 AC8）：不继承父进程工作目录，
        #: 防止 MCP 进程在宿主工作区读写任意文件。
        self._cwd = cwd
        self._process: subprocess.Popen[str] | None = None
        self._write_lock = threading.Lock()
        #: 同进程调用串行锁：协议是单请求流，宿主侧用该锁串行并发调用。
        self.call_lock = threading.Lock()
        self._pending: _PendingConfirmation | None = None

    @property
    def pid(self) -> int | None:
        process = self._process
        return process.pid if process is not None else None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def start(self) -> None:
        """以白名单环境启动命令并完成 initialize 握手（同步等待）。"""
        if self.is_alive():
            return
        # 只保留导入内置包所需的 PYTHONPATH 与最小 PATH；绝不继承账户
        # Key、SMTP 码、内部加密密钥或其余环境变量（AC5 秘密隔离）。
        clean_env = {
            "PYTHONPATH": os.pathsep.join(sys.path),
            "PATH": os.environ.get("PATH", ""),
            "PYTHONIOENCODING": "utf-8",
        }
        try:
            self._process = subprocess.Popen(
                self._command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                env=clean_env,
                cwd=self._cwd,
            )
        except (OSError, ValueError) as exc:
            self._process = None
            raise McpProcessError(
                "start_failed", f"MCP 服务器启动失败：{exc}", permission=True
            ) from exc
        # initialize 握手：超时未应答即启动失败（AC3 启动超时闭锁）。
        response = self._read_line(timeout=self._startup_timeout)
        if response is None:
            self.terminate()
            raise McpProcessError("start_timeout", "MCP 服务器启动超时，已停止并回收进程。")
        if response.get("ok") is not True:
            self.terminate()
            error = response.get("error")
            code = error.get("code") if isinstance(error, dict) else None
            message = error.get("message") if isinstance(error, dict) else None
            raise McpProcessError(
                str(code or "start_failed"),
                str(message or "MCP 服务器启动失败，请重试。"),
                permission=code == "permission_denied",
            )

    def is_alive(self) -> bool:
        process = self._process
        return process is not None and process.poll() is None

    def terminate(self) -> None:
        """停止进程：优先 SIGTERM，超时后强杀（不遗留僵尸进程）。"""
        process = self._process
        self._process = None
        self._pending = None
        if process is None:
            return
        if process.stdin is not None:
            with suppress(OSError, ValueError):
                process.stdin.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                with suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=3)


    def close(self) -> None:
        self.terminate()

    # ------------------------------------------------------------------
    # 调用与敏感确认恢复
    # ------------------------------------------------------------------

    def invoke(
        self,
        *,
        request_id: str,
        tool: str,
        input_data: dict[str, Any],
        data_slice: dict[str, Any],
        permissions: McpPermissionManifest,
        handler: ToolHandler,
    ) -> tuple[str, Any]:
        """执行一次调用并返回（状态, 结果）。

        状态为 ``success``/``failed``/``sensitive_pending``。敏感挂起时
        本调用返回 ``sensitive_pending`` 与挂起上下文，等待外部确认后
        经 :meth:`resume` 完成。
        """
        request = {
            "id": request_id,
            "method": "invoke",
            "params": {
                "tool": tool,
                "input": input_data,
                "data_slice": data_slice,
                "permissions": permissions.model_dump(),
            },
        }
        with self._write_lock:
            self._write_line(request)
        return self._read_response_loop(request_id, handler)

    def resume(
        self, *, request_id: str, tool_call_id: int, approved: bool, handler: ToolHandler
    ) -> tuple[str, Any]:
        """确认敏感操作后恢复挂起调用（确认仅对本次调用有效）。

        确认前先把批准标记记入挂起上下文，再转发给服务器；服务器重发
        同标识的工具调用时直接执行（或按拒绝终止）。
        """
        pending = self._pending
        if (
            pending is None
            or pending.request_id != request_id
            or pending.tool_call_id != tool_call_id
        ):
            raise McpProcessError("confirmation_stale", "该确认已过期，请重新发起调用。")
        if not self.is_alive():
            raise McpProcessError("not_running", "MCP 服务器未运行，无法确认。")
        pending.confirmed = approved
        payload = {
            "id": request_id,
            "type": "sensitive_confirm",
            "tool_call_id": tool_call_id,
            "approved": approved,
        }
        with self._write_lock:
            self._write_line(payload)
        return self._read_response_loop(request_id, handler)

    def _read_response_loop(self, request_id: str, handler: ToolHandler) -> tuple[str, Any]:
        """读取并处理服务器响应流，直到 result 或敏感挂起。"""
        while True:
            message = self._read_line(timeout=self._tool_timeout)
            if message is None:
                self.terminate()
                raise McpProcessError("call_timeout", "MCP 服务器响应超时，已停止进程。")
            if message.get("id") != request_id:
                # 迟到/错乱消息：忽略并继续（进程按协议串行，防御性处理）。
                continue
            message_type = message.get("type")
            if message_type == "tool_call":
                outcome = self._handle_tool_call(request_id, message, handler)
                if outcome is not None:
                    return outcome
            elif message_type == "result":
                ok = message.get("ok") is True
                if ok:
                    return "success", message.get("result")
                error = message.get("error")
                code = error.get("code") if isinstance(error, dict) else None
                text = error.get("message") if isinstance(error, dict) else None
                raise McpProcessError(
                    str(code or "call_failed"),
                    str(text or "MCP 服务器调用失败，请重试。"),
                )
            else:
                self.terminate()
                raise McpProcessError("protocol_error", "MCP 服务器协议错误：未知消息类型。")

    def _handle_tool_call(
        self, request_id: str, message: dict[str, Any], handler: ToolHandler
    ) -> tuple[str, Any] | None:
        """处理一次工具调用；敏感未确认时挂起返回 sensitive_pending。"""
        tool_call_id = message.get("tool_call_id")
        tool_name = message.get("tool")
        arguments = message.get("arguments") or {}
        if not isinstance(tool_call_id, int) or not isinstance(tool_name, str):
            self.terminate()
            raise McpProcessError("protocol_error", "MCP 服务器协议错误：工具调用标识无效。")
        pending = self._pending
        already_confirmed = (
            pending is not None
            and pending.request_id == request_id
            and pending.tool_call_id == tool_call_id
            and pending.confirmed
        )
        outcome = handler(str(tool_name), arguments, already_confirmed)
        if outcome.sensitive is not None and not already_confirmed:
            self._pending = _PendingConfirmation(
                request_id=request_id,
                tool_call_id=tool_call_id,
                tool=str(tool_name),
                arguments=dict(arguments),
                confirmation=outcome.sensitive,
            )
            return "sensitive_pending", {
                "tool_call_id": tool_call_id,
                "confirmation": outcome.sensitive.model_dump(mode="json"),
            }
        if outcome.sensitive is not None:
            # 已确认但处理器仍要求敏感（矛盾状态）：按拒绝安全终止。
            self._write_tool_result(
                request_id,
                tool_call_id,
                False,
                "sensitive_denied",
                "该敏感操作未获确认，调用已安全终止。",
            )
            return None
        if not outcome.ok:
            self._write_tool_result(
                request_id,
                tool_call_id,
                False,
                outcome.error_code or "permission_denied",
                outcome.error_message or "权限不足，该操作未执行。",
            )
            return None
        self._write_tool_result(request_id, tool_call_id, True, None, None, outcome.result)
        return None

    def _write_tool_result(
        self,
        request_id: str,
        tool_call_id: int,
        ok: bool,
        error_code: str | None,
        error_message: str | None,
        result: Any | None = None,
    ) -> None:
        response: dict[str, Any] = {
            "id": request_id,
            "type": "tool_result",
            "tool_call_id": tool_call_id,
            "ok": ok,
        }
        if ok:
            response["result"] = result
        else:
            response["error"] = {
                "code": error_code or "permission_denied",
                "message": error_message or "权限不足，该操作未执行。",
            }
        with self._write_lock:
            self._write_line(response)

    # ------------------------------------------------------------------
    # 低层 JSONL
    # ------------------------------------------------------------------

    def _write_line(self, payload: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise McpProcessError("not_running", "MCP 服务器未运行，无法调用。")
        try:
            process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            process.stdin.flush()
        except (OSError, ValueError) as exc:
            self.terminate()
            raise McpProcessError("call_failed", "MCP 服务器通信中断，已停止进程。") from exc

    def _read_line(self, *, timeout: float) -> dict[str, Any] | None:
        """带超时读取一行 JSON（超时/EOF/损坏返回 None 或抛错）。"""
        process = self._process
        if process is None or process.stdout is None:
            return None
        holder: list[str | None] = []
        error_holder: list[McpProcessError] = []
        stdout = process.stdout

        def _read() -> None:
            try:
                holder.append(stdout.readline())
            except (OSError, ValueError):
                error_holder.append(
                    McpProcessError("call_failed", "MCP 服务器通信中断，已停止进程。")
                )

        reader = threading.Thread(target=_read, daemon=True)
        reader.start()
        reader.join(timeout)
        if reader.is_alive():
            # 超时：放弃读取线程（调用方随后终止进程使 readline 返回）。
            return None
        if error_holder:
            self.terminate()
            raise error_holder[0]
        line = holder[0]
        if not line:
            # EOF：进程已退出。短暂轮询 poll 获取退出码（Windows 上
            # 管道 EOF 与进程回收状态存在极小延迟）。
            code = process.poll()
            for _ in range(20):
                if code is not None:
                    break
                time.sleep(0.05)
                code = process.poll()
            self.terminate()
            if code is not None:
                # 不携带 stderr 内容：服务器输出可能含私人正文，绝不落库
                # （Verification 4 秘密与完整私人正文不进入日志/审计/错误响应）。
                raise McpProcessError(
                    "process_crashed",
                    f"MCP 服务器进程已退出（退出码 {code}），调用失败。",
                )
            raise McpProcessError("call_failed", "MCP 服务器通信中断，已停止进程。")
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            self.terminate()
            raise McpProcessError("protocol_error", "MCP 服务器返回内容损坏，已停止进程。") from exc
        if not isinstance(payload, dict):
            self.terminate()
            raise McpProcessError("protocol_error", "MCP 服务器返回内容损坏，已停止进程。")
        return payload


def describe_sensitive_operation(
    kind: McpSensitiveKind, tool: str, arguments: dict[str, Any]
) -> tuple[str, str]:
    """生成敏感操作的目标与影响中文说明（展示给用户确认）。"""
    if kind == McpSensitiveKind.WRITE_FILE:
        target = str(arguments.get("path") or arguments.get("file") or "未指定路径")
        return (
            f"写入文件：{target}",
            f"将把 MCP 的写入结果保存到「{target}」，该目录在安装时已声明为可写。",
        )
    if kind == McpSensitiveKind.RUN_COMMAND:
        target = str(arguments.get("command") or arguments.get("program") or "未指定命令")
        return (
            f"执行外部命令：{target}",
            f"将在受限环境中执行「{target}」，仅允许安装时声明的命令集合。",
        )
    if kind == McpSensitiveKind.SEND_EXTERNAL:
        target = str(arguments.get("url") or arguments.get("host") or "未指定目标")
        return (
            f"向外部服务提交：{target}",
            f"将把本次调用的数据切片提交到「{target}」，仅允许安装时声明的网络域名。",
        )
    return ("执行敏感操作", "该操作将访问本机资源或向外部提交数据。")


__all__ = [
    "STARTUP_TIMEOUT_SECONDS",
    "TOOL_TIMEOUT_SECONDS",
    "McpProcessClient",
    "McpProcessError",
    "ToolCallOutcome",
    "ToolHandler",
    "describe_sensitive_operation",
]
