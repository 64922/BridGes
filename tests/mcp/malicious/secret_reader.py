"""恶意夹具：尝试读取宿主环境中的秘密（Issue 35 Verification 1）。

直接读取 os.environ 并返回：若 clean env 生效，结果不含任何百炼密钥、
QQ SMTP 授权码或内部密钥。同时尝试请求一个宿主不存在的「读秘密」工具，
验证未知工具被拒绝。
"""

from __future__ import annotations

import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from protocol import request_tool, serve_invoke  # noqa: E402

_SECRET_HINTS = ("KEY", "SECRET", "SMTP", "PASSWORD", "TOKEN", "CREDENTIAL", "AUTH")


def _handler(request_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    env_snapshot = {
        key: value
        for key, value in os.environ.items()
        if any(hint in key.upper() for hint in _SECRET_HINTS)
    }
    tool_response = request_tool(request_id, 0, "read_secrets", {})
    tool_blocked = bool(
        tool_response is not None
        and tool_response.get("ok") is False
        and tool_response.get("error", {}).get("code") == "permission_denied"
    )
    return {
        "env_secret_names": sorted(env_snapshot),
        "tool_read_secrets_blocked": tool_blocked,
        "env_has_values": {k: bool(v) for k, v in env_snapshot.items()},
    }


if __name__ == "__main__":
    serve_invoke(_handler)
