"""对照工单 33 最终全量与 main 基线的 JUnit 失败集合。

用法：python 33-independent-compare-final.py <issue.xml> <main.xml> [输出.json]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from xml.etree import ElementTree as ET


def load(path: Path) -> dict[str, bool]:
    root = ET.parse(path).getroot()
    cases: dict[str, bool] = {}
    for case in root.iter("testcase"):
        key = f"{case.get('classname')}::{case.get('name')}"
        failed = case.find("failure") is not None or case.find("error") is not None
        cases[key] = failed
    return cases


def main() -> None:
    issue = load(Path(sys.argv[1]))
    baseline = load(Path(sys.argv[2]))
    issue_failures = {key for key, failed in issue.items() if failed}
    main_failures = {key for key, failed in baseline.items() if failed}
    report = {
        "issue_total": len(issue),
        "issue_failed": len(issue_failures),
        "main_total": len(baseline),
        "main_failed": len(main_failures),
        "issue_pass": len(issue) - len(issue_failures),
        "main_pass": len(baseline) - len(main_failures),
        "issue_only_failures": sorted(issue_failures - main_failures),
        "main_only_failures": sorted(main_failures - issue_failures),
        "failure_sets_equal": issue_failures == main_failures,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if len(sys.argv) > 3:
        Path(sys.argv[3]).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )


if __name__ == "__main__":
    main()
