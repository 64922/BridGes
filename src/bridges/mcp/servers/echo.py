"""内置受控回显 MCP 服务器（Issue 35）。

通过 ``python -m bridges.mcp.servers.echo`` 启动。只回显调用参数与
授权数据切片，不读取文件、不联网、不执行命令；供安装描述引用、单元
测试与 E2E 演示（真实调用往返）。
"""

from __future__ import annotations

from typing import Any

from bridges.mcp.servers.base import McpServerFramework, run_server


def _echo_handler(
    tool: str,
    input_data: dict[str, Any],
    data_slice: dict[str, Any],
    permissions: dict[str, Any],
) -> Any:
    if tool != "echo":
        raise ValueError(f"不支持的工具：{tool}")
    # 回显调用参数与授权切片（不含权限清单本身）。
    return {
        "tool": tool,
        "echo": data_slice.get("text", ""),
        "input": input_data,
        "attachment_count": len(data_slice.get("attachments") or []),
    }


def main() -> None:
    server = McpServerFramework(
        name="bridges-echo",
        version="1.0.0",
        handler=_echo_handler,
    )
    run_server(server)


if __name__ == "__main__":
    main()
