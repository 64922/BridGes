"""重新生成 openapi.json（契约同步工具；提交前运行）。

在 test 环境生成：与 pytest 的契约同步测试（create_app 在 test 环境）
一致，`/_test/` 端点（恢复令牌/能力标记）包含在 spec 中。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("BRIDGES_ENVIRONMENT", "test")

from bridges.api.main import create_app

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "openapi.json"

if __name__ == "__main__":
    spec = create_app().openapi()
    with TARGET.open("w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False, indent=2)
    print(f"openapi.json regenerated: {len(spec['paths'])} paths")
