"""按测试标识及诊断内容比较 Issue39 与固定 main 的独立结果。"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
MAIN = Path("D:/BridGes")


def failures(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    tree = ET.parse(path)
    cases = {}
    failed = {}
    for case in tree.iter("testcase"):
        key = f"{case.get('classname')}::{case.get('name')}"
        kind = next((name for name in ("failure", "error", "skipped")
                     if case.find(name) is not None), "passed")
        cases[key] = kind
        if kind in {"failure", "error"}:
            failed[key] = kind
    return cases, failed


def ruff(path: Path, root: Path) -> Counter[tuple[str, str, str, int, int]]:
    # PowerShell 合并流可能把 conda 的非零退出提示插入 JSON 最后的 ] 前。
    text = "\n".join(line for line in path.read_text(encoding="utf-8-sig").splitlines()
                     if not line.startswith("ERROR conda.cli.main_run:execute"))
    payload, _ = json.JSONDecoder().raw_decode(text.lstrip())
    return Counter((Path(row["filename"]).relative_to(root).as_posix(), row["code"],
                    row["message"], row["location"]["row"], row["location"]["column"])
                   for row in payload)


def mypy(path: Path) -> Counter[str]:
    return Counter(line.strip().replace("\\", "/")
                   for line in path.read_text(encoding="utf-8-sig").splitlines()
                   if ": error:" in line)


def main() -> None:
    branch_ruff = ruff(HERE / "ruff.json", ROOT)
    main_ruff = ruff(HERE / "main-ruff.json", MAIN)
    branch_mypy = mypy(HERE / "mypy.txt")
    main_mypy = mypy(HERE / "main-mypy.txt")
    result = {
        "fixed_main": "1c6f9edda00165de1b0bb19e02912037e65e5b54",
        "ruff": {"main_count": sum(main_ruff.values()),
                 "issue_count": sum(branch_ruff.values()),
                 "new": list((branch_ruff - main_ruff).elements()),
                 "removed": list((main_ruff - branch_ruff).elements())},
        "mypy": {"main_count": sum(main_mypy.values()),
                 "issue_count": sum(branch_mypy.values()),
                 "new": list((branch_mypy - main_mypy).elements()),
                 "removed": list((main_mypy - branch_mypy).elements())},
    }
    for label, branch, baseline in (
        ("full_pytest", HERE / "full.xml" if (HERE / "full.xml").exists()
         else ROOT / ".scratch/2/validation/39-full.xml",
         HERE / "main-full.xml" if (HERE / "main-full.xml").exists()
         else MAIN / ".scratch/2/validation/39-full.xml"),
        ("evaluation_pytest", HERE / "evaluation.xml",
         HERE / "main-evaluation.xml" if (HERE / "main-evaluation.xml").exists()
         else MAIN / ".scratch/2/validation/39-main-baseline.xml"),
    ):
        if branch.exists() and baseline.exists():
            cases, failed = failures(branch)
            baseline_cases, baseline_failed = failures(baseline)
            result[label] = {
                "main": dict(Counter(baseline_cases.values())),
                "issue": dict(Counter(cases.values())),
                "new_failures": sorted(set(failed) - set(baseline_failed)),
                "removed_failures": sorted(set(baseline_failed) - set(failed)),
                "shared_failures": sorted(set(failed) & set(baseline_failed)),
                "new_tests": sorted(set(cases) - set(baseline_cases)),
            }
    (HERE / "baseline-comparison.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({key: {name: value for name, value in item.items()
                           if name not in {"new_tests", "shared_failures"}}
                      if isinstance(item, dict) else item for key, item in result.items()},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
