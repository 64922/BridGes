"""BridGes JSONL 协议 MCP 服务器共享框架（Issue 35）。

协议（服务器角度，与宿主双向 JSONL）：

- 启动后立即输出初始化握手：``{"ok": true, "result": {...}}``；
- 收到 ``invoke`` 请求后以生成器驱动调用：处理函数可 ``yield`` 工具
  请求（``{"tool_call_id", "tool", "arguments"}``），框架发送
  ``tool_call`` 并挂起，收到宿主 ``tool_result`` 后恢复生成器；
- 敏感操作被宿主挂起时，框架收到 ``sensitive_confirm``：approved 时
  重发同一工具调用，denied 时以拒绝结果终止本次调用；
- 最终以 ``result`` 消息结束本次调用。

服务器程序不直接读取文件、联网或执行命令；一切 I/O 都经宿主工具
调用，由宿主按允许清单强制校验。协议输入任何损坏都以中文失败
结果响应，不向宿主泄漏原始异常。
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Generator
from typing import Any

#: 处理函数：接收（tool, input, data_slice, permissions），yield 工具
#: 请求 dict（tool_call_id/tool/arguments），被恢复时收到宿主响应 dict
#: （ok/result/error/denied），最终 return 调用结果。
InvokeHandler = Callable[
    [str, dict[str, Any], dict[str, Any], dict[str, Any]],
    Generator[dict[str, Any], dict[str, Any] | None, Any],
]


class McpServerFramework:
    """JSONL 服务器框架：握手 + 主循环 + invoke 生成器状态机。"""

    def __init__(self, *, name: str, version: str, handler: InvokeHandler) -> None:
        self._name = name
        self._version = version
        self._handler = handler
        self._invokes: dict[str, _InvokeState] = {}

    def run(self) -> None:
        sys.stdout.write(
            json.dumps(
                {"ok": True, "result": {"name": self._name, "version": self._version}},
                ensure_ascii=False,
            )
            + "\n"
        )
        sys.stdout.flush()
        for line in sys.stdin:
            try:
                message = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                self._respond_error(None, "protocol_error", "请求格式无效，请重试。")
                continue
            if not isinstance(message, dict):
                self._respond_error(None, "protocol_error", "请求格式无效，请重试。")
                continue
            try:
                if message.get("method") == "invoke":
                    self._start_invoke(message)
                elif message.get("type") == "tool_result":
                    self._deliver_tool_result(message)
                elif message.get("type") == "sensitive_confirm":
                    self._deliver_confirm(message)
                else:
                    self._respond_error(
                        message.get("id"),
                        "protocol_error",
                        "不支持的消息类型，请重试。",
                    )
            except (KeyError, TypeError, ValueError):
                self._respond_error(message.get("id"), "server_error", "服务器处理失败，请重试。")

    # ------------------------------------------------------------------

    def _start_invoke(self, message: dict[str, Any]) -> None:
        request_id = message.get("id")
        if not isinstance(request_id, str):
            self._respond_error(None, "protocol_error", "请求标识无效，请重试。")
            return
        params = message.get("params")
        if not isinstance(params, dict):
            self._respond_error(request_id, "invalid_request", "调用参数无效，请重试。")
            return
        tool = params.get("tool")
        if not isinstance(tool, str) or not tool:
            self._respond_error(request_id, "invalid_request", "缺少工具名，请重试。")
            return
        result = self._handler(
            tool,
            params.get("input") or {},
            params.get("data_slice") or {},
            params.get("permissions") or {},
        )
        if not hasattr(result, "send"):
            # 普通函数直接完成（无工具请求的服务器，如回显）。
            self._respond(request_id, "result", ok=True, result=result)
            return
        state = _InvokeState(request_id=request_id, generator=result)
        self._invokes[request_id] = state
        self._advance(state)

    def _deliver_tool_result(self, message: dict[str, Any]) -> None:
        message_id = message.get("id")
        if not isinstance(message_id, str):
            return
        state = self._invokes.get(message_id)
        if state is None:
            return  # 迟到结果：无挂起调用，忽略。
        if state.pending_call_id != message.get("tool_call_id"):
            return  # 标识不匹配：忽略（防御协议错乱）。
        state.pending_call_id = None
        state.host_response = message
        self._advance(state)

    def _deliver_confirm(self, message: dict[str, Any]) -> None:
        message_id = message.get("id")
        if not isinstance(message_id, str):
            return
        state = self._invokes.get(message_id)
        if state is None or state.pending_call_id != message.get("tool_call_id"):
            return
        if message.get("approved") is True:
            # 用户确认：保留待重发标识，重发同一工具调用。
            self._send_tool_call(state)
        else:
            # 用户拒绝：以拒绝结果终止本次调用（安全终止）。
            state.denied = True
            state.pending_call_id = None
            self._advance(state)

    def _advance(self, state: _InvokeState) -> None:
        """推进生成器一步：工具请求则发送并挂起，完成则输出结果。"""
        try:
            if state.denied:
                request = state.generator.send({"denied": True})
            elif state.host_response is not None:
                request = state.generator.send(state.host_response)
                state.host_response = None
            else:
                request = state.generator.send(None)
        except StopIteration as stop:
            self._invokes.pop(state.request_id, None)
            self._respond(state.request_id, "result", ok=True, result=stop.value)
            return
        except SensitiveRejected:
            self._invokes.pop(state.request_id, None)
            self._respond_error(state.request_id, "sensitive_denied", "用户拒绝了该敏感操作。")
            return
        except Exception as exc:
            # 处理函数内部错误：转失败结果，进程保持存活（不崩溃）；
            # 携带处理函数的失败原因（内置服务器为可读中文说明）。
            self._invokes.pop(state.request_id, None)
            self._respond_error(
                state.request_id,
                "server_error",
                str(exc) or "服务器处理失败，请重试。",
            )
            return
        if not isinstance(request, dict):
            self._invokes.pop(state.request_id, None)
            self._respond_error(state.request_id, "server_error", "服务器工具请求无效，请重试。")
            return
        call_id = request.get("tool_call_id")
        tool = request.get("tool")
        arguments = request.get("arguments")
        if (
            not isinstance(call_id, int)
            or not isinstance(tool, str)
            or not isinstance(arguments, dict)
        ):
            self._invokes.pop(state.request_id, None)
            self._respond_error(state.request_id, "server_error", "服务器工具请求无效，请重试。")
            return
        state.pending_call_id = call_id
        state.requested_tool = tool
        state.requested_arguments = arguments
        self._send_tool_call(state)

    def _send_tool_call(self, state: _InvokeState) -> None:
        self._respond(
            state.request_id,
            "tool_call",
            tool_call_id=state.pending_call_id,
            tool=state.requested_tool,
            arguments=state.requested_arguments,
        )

    # ------------------------------------------------------------------

    def _respond(self, request_id: Any, message_type: str, **payload: Any) -> None:
        body = {"id": request_id, "type": message_type, **payload}
        sys.stdout.write(json.dumps(body, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    def _respond_error(self, request_id: Any, code: str, message: str) -> None:
        self._respond(request_id, "result", ok=False, error={"code": code, "message": message})


class _InvokeState:
    """一次 invoke 的生成器状态机。"""

    def __init__(self, *, request_id: Any, generator: Generator[dict[str, Any], Any, Any]) -> None:
        self.request_id = request_id
        self.generator = generator
        self.pending_call_id: int | None = None
        self.requested_tool: str = ""
        self.requested_arguments: dict[str, Any] = {}
        self.host_response: dict[str, Any] | None = None
        self.denied = False


class SensitiveRejected(Exception):  # noqa: N818 - 协议内部控制流异常
    """用户拒绝敏感操作后，服务器端终止调用。"""


def request_tool(tool_call_id: int, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """在生成器处理函数中发起工具请求（yield 该返回值）。"""
    return {"tool_call_id": tool_call_id, "tool": tool, "arguments": arguments}


def run_server(server: McpServerFramework) -> None:
    server.run()


__all__ = ["InvokeHandler", "McpServerFramework", "SensitiveRejected", "request_tool", "run_server"]
