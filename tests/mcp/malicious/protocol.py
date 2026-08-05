"""恶意 MCP 夹具共享 JSONL 协议（测试专用，与生产代码无关）。

恶意夹具按 BridGes JSONL 协议启动（握手 + invoke 处理 + 工具请求），
用于验证平台强制层：读取秘密、访问未授权路径、连接未声明域名、启动
未声明命令与跨账户读取都必须失败闭锁。夹具只通过工具请求尝试越权，
实际 I/O 由宿主执行并拒绝。
"""

from __future__ import annotations

import json
import sys
from typing import Any


def send(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def read_line() -> dict[str, Any] | None:
    line = sys.stdin.readline()
    if not line:
        return None
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def handshake(name: str) -> None:
    send({"ok": True, "result": {"name": name, "version": "0.0.1-test"}})


def request_tool(
    request_id: Any, tool_call_id: int, tool: str, arguments: dict[str, Any]
) -> dict[str, Any] | None:
    """请求宿主执行工具并等待 tool_result（返回宿主响应）。"""
    send(
        {
            "id": request_id,
            "type": "tool_call",
            "tool_call_id": tool_call_id,
            "tool": tool,
            "arguments": arguments,
        }
    )
    while True:
        message = read_line()
        if message is None:
            return None
        if message.get("type") == "tool_result" and message.get("tool_call_id") == tool_call_id:
            return message
        # 其余消息（如敏感挂起提示）忽略；夹具只关心结果。


def finish(request_id: Any, result: Any) -> None:
    send({"id": request_id, "type": "result", "ok": True, "result": result})


def serve_invoke(handler) -> None:
    """主循环：握手后逐条处理 invoke 请求。"""
    handshake("malicious-fixture")
    while True:
        message = read_line()
        if message is None:
            return
        if message.get("method") != "invoke":
            continue
        request_id = message.get("id")
        try:
            result = handler(request_id, message.get("params") or {})
            finish(request_id, result)
        except Exception as exc:  # noqa: BLE001 - 夹具失败也要返回结果
            send(
                {
                    "id": request_id,
                    "type": "result",
                    "ok": False,
                    "error": {"code": "fixture_error", "message": str(exc)},
                }
            )
