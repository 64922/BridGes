"""重新生成 openapi.json（契约同步工具；提交前运行）。"""

from __future__ import annotations

import json
from pathlib import Path

from bridges.api.main import create_app

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "openapi.json"

if __name__ == "__main__":
    spec = create_app().openapi()
    with TARGET.open("w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False, indent=2)
    print(f"openapi.json regenerated: {len(spec['paths'])} paths")
