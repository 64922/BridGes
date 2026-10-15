"""Issue 02：定位归一化后仍存在的字段差异（路径级 diff）。

用法（仓库根目录、conda ``agent`` 环境）：

    python validation/02-write-boundary/tools/diff_profile_baseline_fields.py \
        "<左侧证据 glob>" "<右侧证据 glob>"

输出第一个到第 40 个字段级差异路径；无差异时输出
``no field-level differences``。本工具依赖同目录的
``compare_profile_baseline.normalize``，用于证明比较器归一化口径本身
可靠（例如 main 同一版本两次运行之间的对照）。
"""

from __future__ import annotations

import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from compare_profile_baseline import normalize  # noqa: E402


def walk(left, right, path, report, limit=40):
    if len(report) >= limit:
        return
    if type(left) is not type(right):
        report.append(f"{path}: type {type(left).__name__} != {type(right).__name__}")
        return
    if isinstance(left, dict):
        for key in sorted(set(left) | set(right)):
            if key not in left:
                report.append(f"{path}.{key}: missing on left")
            elif key not in right:
                report.append(f"{path}.{key}: missing on right")
            else:
                walk(left[key], right[key], f"{path}.{key}", report, limit)
    elif isinstance(left, list):
        if len(left) != len(right):
            report.append(f"{path}: len {len(left)} != {len(right)}")
        for index, (a, b) in enumerate(zip(left, right)):
            walk(a, b, f"{path}[{index}]", report, limit)
    elif left != right:
        report.append(f"{path}: {left!r} != {right!r}")


def main() -> int:
    left = normalize(glob.glob(sys.argv[1])[0])
    right = normalize(glob.glob(sys.argv[2])[0])
    report: list[str] = []
    walk(left, right, "$", report)
    print("\n".join(report) if report else "no field-level differences")
    return 1 if report else 0


if __name__ == "__main__":
    raise SystemExit(main())
