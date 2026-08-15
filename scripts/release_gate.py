"""运行 Issue 09 三条旅程收尾发布门、Issue 07 搜索提供方发布门与
Issue 17 全功能 Qwen 真实性门。

用法：

```powershell
python scripts/release_gate.py                          # 离线收尾门
python scripts/release_gate.py --real-probes            # 收尾门 + 真实 Tavily/arXiv 探针
python scripts/release_gate.py --real-probes --qwen-authenticity
    # 追加 Issue 17 真实性门（能力清单/静态扫描/组合/live suite/重启锁复查）
```

Issue 07（ADR-0029）组成项随收尾门始终运行：生产提供方清单断言（恰好
只有 tavily）、金标路由（Issue 03）与降级语义（Issue 02）测试、产物
密钥扫描（``tvly-``/Qwen Key 形态）与发布报告进程内复核；``--real-probes``
下的 Tavily 探针包含最小成本真实搜索 + 正文获取的三态 smoke。

Issue 17 门禁只在该标志显式给出时运行；任一子门失败都以非零退出。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from bridges.closeout.release_gate import main as closeout_main  # noqa: E402, I001


def main(argv: list[str] | None = None) -> int:
    """把 ``--qwen-authenticity`` 从收尾门参数中剥离并组合两个门的结果。"""
    args = list(sys.argv[1:] if argv is None else argv)
    if "--qwen-authenticity" not in args:
        return closeout_main(args)
    args.remove("--qwen-authenticity")

    from bridges.closeout.authenticity_gate import run_authenticity_gate

    real_probes = "--real-probes" in args
    report = run_authenticity_gate(real_probes=real_probes)
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    closeout_exit = closeout_main(args)
    if report.status == "blocked" or closeout_exit != 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
