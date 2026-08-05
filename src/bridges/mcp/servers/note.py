"""内置受控笔记 MCP 服务器（Issue 35）。

通过 ``python -m bridges.mcp.servers.note`` 启动。演示敏感操作确认
纵向路径：调用时请求宿主 ``write_file`` 工具把授权数据切片写入安装
时声明的可写目录；宿主按允许清单校验路径并挂起敏感确认，确认通过后
才执行写入。服务器自身不直接写文件，一切 I/O 都经宿主工具调用。
"""

from __future__ import annotations

from typing import Any

from bridges.mcp.servers.base import (
    McpServerFramework,
    SensitiveRejected,
    request_tool,
    run_server,
)


def _note_handler(
    tool: str,
    input_data: dict[str, Any],
    data_slice: dict[str, Any],
    permissions: dict[str, Any],
) -> Any:
    if tool != "note":
        raise ValueError(f"不支持的工具：{tool}")
    path = input_data.get("path")
    if not isinstance(path, str) or not path:
        raise ValueError("缺少目标路径（input.path）。")
    content = str(data_slice.get("text", ""))
    response = yield request_tool(0, "write_file", {"path": path, "content": content})
    if response is None or response.get("denied") is True:
        raise SensitiveRejected("用户拒绝了该敏感操作。")
    if response.get("ok") is not True:
        error = response.get("error") if isinstance(response, dict) else None
        message = error.get("message") if isinstance(error, dict) else None
        raise RuntimeError(str(message or "写入被拒绝，调用失败。"))
    return {
        "written": True,
        "path": path,
        "chars": len(content),
    }


def main() -> None:
    server = McpServerFramework(
        name="bridges-note",
        version="1.0.0",
        handler=_note_handler,
    )
    run_server(server)


if __name__ == "__main__":
    main()
