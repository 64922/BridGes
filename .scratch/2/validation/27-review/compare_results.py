"""将独立验收的 JUnit 与静态诊断压缩为可逐项复核的证据。"""

import json
import re
from collections import Counter
from pathlib import Path
import xml.etree.ElementTree as ET


BASELINE = Path(r"D:\BridGes\.scratch\2\validation\27-review")
CURRENT = Path(__file__).resolve().parent


def junit(path: Path) -> dict:
    root = ET.parse(path).getroot()
    cases = root.findall(".//testcase")
    failed = sorted(
        f"{case.attrib['classname']}::{case.attrib['name']}"
        for case in cases if case.find("failure") is not None or case.find("error") is not None
    )
    skipped = sum(case.find("skipped") is not None for case in cases)
    return {"file": path.name, "tests": len(cases), "passed": len(cases) - len(failed) - skipped,
            "failed": len(failed), "skipped": skipped, "failures": failed}


def comparison(baseline: dict, current: dict) -> dict:
    return {"main": baseline, "issue": current,
            "introduced_failures": sorted(set(current["failures"]) - set(baseline["failures"])),
            "resolved_failures": sorted(set(baseline["failures"]) - set(current["failures"]))}


def mypy(path: Path) -> Counter:
    return Counter(re.sub(r":\d+:", ":LINE:", line) for line in path.read_text(encoding="utf-8-sig").splitlines() if "error:" in line)


def ruff(path: Path) -> Counter:
    text = path.read_text(encoding="utf-8-sig")
    cut = text.find("\nERROR ")
    if cut != -1:
        text = text[:cut].rstrip()
        if not text.endswith("]"):
            text += "]"
    return Counter((Path(item["filename"]).name, item["code"], item["message"])
                   for item in json.loads(text))


main_types = mypy(BASELINE / "mypy-main.log")
issue_types = mypy(CURRENT / "mypy-issue-final.log")
main_ruff = ruff(BASELINE / "ruff-main.json")
issue_ruff = ruff(CURRENT / "ruff-issue.json")
summary = {
    "baseline_commit": "68357a208f3e22b6fdea0445377ea383a80a9fcb",
    "focused": comparison(junit(BASELINE / "main-focused.xml"), junit(CURRENT / "final-focused.xml")),
    "regressions": junit(CURRENT / "final-regressions.xml"),
    "integration": comparison(junit(BASELINE / "main-integration-isolated.xml"), junit(CURRENT / "issue-integration-isolated.xml")),
    "mypy": {"main_errors": sum(main_types.values()), "issue_errors": sum(issue_types.values()),
             "introduced": list((issue_types - main_types).elements()), "resolved": list((main_types - issue_types).elements())},
    "ruff_shared_files": {"main_diagnostics": sum(main_ruff.values()), "issue_diagnostics": sum(issue_ruff.values()),
                          "introduced": list((issue_ruff - main_ruff).elements())},
    "limitations": ["首次集成运行被固定 pytest 临时目录文件锁干扰，已用独立 basetemp 重跑。",
                    "确定性替身只证明机制；真实回复、官网可得性和模型体验待工单42。"]
}
(CURRENT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({key: value for key, value in summary.items() if key not in ("focused", "integration")}, ensure_ascii=False))
print("新增专项失败：", summary["focused"]["introduced_failures"])
print("新增集成失败：", summary["integration"]["introduced_failures"])
