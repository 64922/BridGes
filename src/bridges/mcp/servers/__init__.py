"""内置受控演示 MCP 服务器（Issue 35）。

这些服务器是遵循 BridGes JSONL 协议的外部命令（``python -m
bridges.mcp.servers.echo`` 等），供安装描述引用、测试与 E2E 演示：
它们不直接读取文件、联网或执行命令，一切 I/O 都经宿主工具调用并由
宿主按允许清单强制校验。
"""

from bridges.mcp.servers.base import McpServerFramework, run_server

__all__ = ["McpServerFramework", "run_server"]
