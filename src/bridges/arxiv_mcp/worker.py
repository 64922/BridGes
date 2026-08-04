"""arXiv MCP worker：只读 JSONL 协议，不读取宿主配置或用户材料。"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict

from bridges.arxiv_mcp.client import ArxivMcpClient, ArxivMcpError


def main() -> None:
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


if __name__ == "__main__":
    main()
