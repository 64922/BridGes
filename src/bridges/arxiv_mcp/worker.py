"""arXiv MCP worker：只读 JSONL 协议，不读取宿主配置或用户材料。

协议固定为 UTF-8（issue 05）：进程启动时显式把标准流重新配置为
UTF-8，不依赖系统代码页（Windows 默认为 GBK/CP936）或 UTF-8 模式，
随后先输出一行 ``ready`` 握手，再进入请求循环。父进程只在收到
合法 ``ready`` 后才发送搜索请求。
"""

from __future__ import annotations

import json
import sys
from contextlib import suppress
from dataclasses import asdict

from bridges.arxiv_mcp.client import ArxivMcpClient, ArxivMcpError

#: 握手协议版本：父进程校验此版本后才接受响应。
HANDSHAKE_VERSION = 1


def main() -> None:
    _configure_utf8_stdio()
    sys.stdout.write(
        json.dumps({"type": "ready", "version": HANDSHAKE_VERSION}) + "\n"
    )
    sys.stdout.flush()
    client = ArxivMcpClient()
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ArxivMcpError("arxiv_request", "arXiv 请求格式无效，请重试。")
            query = request.get("query")
            max_results = request.get("max_results", 5)
            if not isinstance(query, str) or not isinstance(max_results, int):
                raise ArxivMcpError("arxiv_request", "arXiv 请求格式无效，请重试。")
            papers = client.search(query, max_results=max_results)
            payload = {
                "ok": True,
                "papers": [
                    {
                        **asdict(paper),
                        "published_at": paper.published_at.isoformat(),
                    }
                    for paper in papers
                ],
            }
        except ArxivMcpError as exc:
            payload = {
                "ok": False,
                "code": exc.code,
                "message": exc.message,
            }
        except (json.JSONDecodeError, TypeError, ValueError):
            payload = {
                "ok": False,
                "code": "arxiv_request",
                "message": "arXiv 请求格式无效，请重试。",
            }
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def _configure_utf8_stdio() -> None:
    """把标准流显式固定为 UTF-8，不依赖系统代码页或 UTF-8 模式。"""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        with suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]


if __name__ == "__main__":
    main()
