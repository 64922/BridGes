"""对照独立验收的 main 与 Issue JUnit 结果，输出差异节点。"""
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def results(path):
    root = ET.parse(path).getroot()
    counts = {kind: 0 for kind in ("passed", "failed", "error", "skipped")}
    failures = {}
    for test in root.iter("testcase"):
        outcome = "passed"
        for tag, kind in (("failure", "failed"), ("error", "error"), ("skipped", "skipped")):
            if test.find(tag) is not None:
                outcome = kind
                break
        counts[outcome] += 1
        if outcome in ("failed", "error"):
            name = test.attrib["classname"].replace(".", "/") + ".py::" + test.attrib["name"]
            failures[name] = outcome
    return counts, failures


branch_dir = Path(__file__).resolve().parent
main_dir = Path(sys.argv[1])
branch, branch_failures = results(branch_dir / "branch-full.xml")
main, main_failures = results(main_dir / "main-full.xml")
comparison = {
    "branch": branch,
    "main": main,
    "common_failures": sorted(branch_failures.keys() & main_failures.keys()),
    "branch_only": sorted(branch_failures.keys() - main_failures.keys()),
    "main_only": sorted(main_failures.keys() - branch_failures.keys()),
}
(branch_dir / "comparison.json").write_text(
    json.dumps(comparison, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
print(json.dumps({key: value for key, value in comparison.items() if key != "common_failures"}, indent=2))
