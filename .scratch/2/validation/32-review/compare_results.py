"""独立验收：比较 JUnit 失败集合与 mypy 错误，保存脱敏摘要。

用法（在 Issue32 工作树或合并后的 main 仓库根运行）：
    python .scratch/2/validation/32-review/compare_results.py final-confirmed.xml

main 基线证据保存在同目录 baseline/ 下，可随仓库携带。
"""

import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def failures(path):
    root = ET.parse(path).getroot()
    failed = {}
    for item in root.iter("testcase"):
        error = item.find("failure")
        if error is None:
            error = item.find("error")
        if error is not None:
            failed[item.attrib["classname"] + "::" + item.attrib["name"]] = error.get("message", "")
    return failed


current = Path(__file__).resolve().parent
base = current / "baseline"
main = failures(base / "main-isolated.xml")
issue = failures(current / (sys.argv[1] if len(sys.argv) > 1 else "issue-isolated.xml"))
summary = {
    "main_failures": len(main), "issue_failures": len(issue),
    "issue_only": sorted(issue.keys() - main.keys()),
    "main_only": sorted(main.keys() - issue.keys()),
    "common": sorted(main.keys() & issue.keys()),
}
print(json.dumps(summary, ensure_ascii=False, indent=2))
(current / "failure-comparison.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
)


def type_errors(path):
    return {re.sub(r":\d+: error:", ": error:", line) for line in
            path.read_text(encoding="utf-8").splitlines() if ": error:" in line}


main_errors = type_errors(base / "mypy-main.txt")
issue_errors = type_errors(current / "mypy-issue.txt")
print("类型错误：", len(main_errors), len(issue_errors),
      "新增：", sorted(issue_errors - main_errors))
