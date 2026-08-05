"""恶意夹具：尝试跨账户读取（Issue 35 Verification 1）。

请求宿主 read_file 工具访问其他账户的目录（数据目录/对象库），验证
宿主按允许清单拒绝；同时检查宿主传入的数据切片不含其他账户内容。
"""

from __future__ import annotations

import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from protocol import request_tool, serve_invoke  # noqa: E402


def _handler(request_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    data_slice = params.get("data_slice") or {}
    attempts = params.get("input", {}).get("paths") or [
        "/home/other-account/private",
        "C:\\Users\\other\\AppData\\BridGes\\objects",
    ]
    outcomes: list[dict[str, Any]] = []
    for index, path in enumerate(attempts):
        response = request_tool(request_id, index, "read_file", {"path": str(path)})
        outcomes.append(
            {
                "path": str(path),
                "blocked": bool(
                    response is not None
                    and response.get("ok") is False
                    and response.get("error", {}).get("code") == "permission_denied"
                ),
            }
        )
    return {
        "attempts": outcomes,
        "slice_keys": sorted(data_slice.keys()),
        "slice_text_length": len(str(data_slice.get("text", ""))),
    }


if __name__ == "__main__":
    serve_invoke(_handler)
