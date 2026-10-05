"""按测试标识比较 main 与工单 35 的实际 JUnit 结果。"""

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def results(path):
    root = ET.parse(path).getroot()
    tests = {}
    for case in root.iter("testcase"):
        node = case.attrib["classname"] + "::" + case.attrib["name"]
        status = "passed"
        for kind in ("failure", "error", "skipped"):
            if case.find(kind) is not None:
                status = kind
                break
        tests[node] = status
    counts = {kind: sum(value == kind for value in tests.values())
              for kind in ("passed", "failure", "error", "skipped")}
    return tests, counts


main, main_counts = results(sys.argv[1])
branch, branch_counts = results(sys.argv[2])
failed = {"failure", "error"}
report = {
    "main": main_counts,
    "branch": branch_counts,
    "branch_only_failures": sorted(node for node, status in branch.items()
                                   if status in failed and main.get(node) not in failed),
    "main_only_failures": sorted(node for node, status in main.items()
                                 if status in failed and branch.get(node) not in failed),
    "new_tests": {node: branch[node] for node in sorted(branch.keys() - main.keys())},
    "removed_tests": sorted(main.keys() - branch.keys()),
}
Path(sys.argv[3]).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
