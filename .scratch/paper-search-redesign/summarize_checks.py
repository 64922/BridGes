"""按失败节点、错误消息和规则比较固定main基准，忽略行号漂移。"""

import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).parent
LOGS = ROOT / "acceptance-logs"


def read(name: str) -> str:
    return (LOGS / name).read_text(encoding="utf-8", errors="replace")


def pytest(name: str) -> dict[str, object]:
    log = read(name)
    failures = sorted(set(re.findall(r"^FAILED (\S+)", log, re.MULTILINE)))
    errors = sorted(set(re.findall(r"^ERROR (\S+)", log, re.MULTILINE)))
    summaries = [line for line in log.splitlines() if re.search(r"\d+ passed.*in \d", line)]
    return {"summary": summaries[-1] if summaries else "未完成", "failed": failures,
            "errors": errors}


def mypy(name: str) -> Counter[tuple[str, str]]:
    return Counter((path.replace("\\", "/"), message) for path, message in re.findall(
        r"^(.+?):\d+(?::\d+)?: error: (.+)$", read(name), re.MULTILINE))


def ruff(name: str) -> Counter[tuple[str, str, str]]:
    values = json.loads(read(name))
    return Counter((value["filename"].replace("\\", "/").split("/src/")[-1]
                    if "/src/" in value["filename"].replace("\\", "/") else
                    "tests/" + value["filename"].replace("\\", "/").split("/tests/")[-1],
                    value["code"], value["message"]) for value in values)


if __name__ == "__main__":
    baseline, snapshot = pytest("pytest-baseline-final.log"), pytest("pytest-paper-snapshot.log")
    report = {"baseline": baseline, "paper_snapshot": snapshot,
              "new_failed_nodes": sorted(set(snapshot["failed"]) - set(baseline["failed"])),
              "resolved_failed_nodes": sorted(set(baseline["failed"]) - set(snapshot["failed"])),
              "new_error_nodes": sorted(set(snapshot["errors"]) - set(baseline["errors"]))}
    for tool, parser, suffix in (("mypy", mypy, "log"), ("ruff", ruff, "json")):
        old, new = parser(f"{tool}-baseline.{suffix}"), parser(f"{tool}-snapshot.{suffix}")
        report[tool] = {"baseline_count": sum(old.values()), "snapshot_count": sum(new.values()),
                        "new": [list(key) + [count] for key, count in (new - old).items()],
                        "resolved": [list(key) + [count] for key, count in (old - new).items()]}
    (ROOT / "CHECK-COMPARISON.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("main:", baseline["summary"])
    print("论文副本:", snapshot["summary"])
    print("新增失败:", report["new_failed_nodes"], "新增错误:", report["new_error_nodes"])
    for tool in ("mypy", "ruff"):
        print(tool, report[tool]["baseline_count"], "→", report[tool]["snapshot_count"],
              "新增", len(report[tool]["new"]))
