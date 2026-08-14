"""运行 Issue 17 全功能 Qwen 真实性发布门（独立命令）。

与 ``python scripts/release_gate.py --real-probes --qwen-authenticity``
等价（后者同时运行 Issue 09 收尾门）。真实探针必须显式 ``--real-probes``
opt-in；缺少安装级全局 Qwen Key、真实网络或供应商权限时整体状态为
失败/``inconclusive``，命令非零退出，不得通过发布门。
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from bridges.closeout.authenticity_gate import main  # noqa: E402, I001

if __name__ == "__main__":
    raise SystemExit(main())
