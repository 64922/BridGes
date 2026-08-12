"""运行 Issue 09 三条旅程收尾发布门。"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from bridges.closeout.release_gate import main  # noqa: E402, I001

if __name__ == "__main__":
    raise SystemExit(main())
