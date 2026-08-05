"""恶意夹具：调用时崩溃退出（Issue 35 Verification 3 进程崩溃）。

启动握手成功后，收到 invoke 请求即异常退出，验证宿主把进程崩溃
检测为失败状态并给出中文原因。
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from protocol import send  # noqa: E402

send({"ok": True, "result": {"name": "zombie-fixture", "version": "0.0.1-test"}})
for line in sys.stdin:
    if not line:
        break
    try:
        message = json.loads(line)
    except json.JSONDecodeError:
        continue
    if message.get("method") == "invoke":
        os._exit(7)  # noqa: SLF001 - 夹具刻意崩溃


if __name__ == "__main__":
    pass
