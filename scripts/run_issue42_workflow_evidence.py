"""工单 42：执行 39 场景确定性证据并生成报告。

用法（在仓库根或工作树根运行）：

    python scripts/run_issue42_workflow_evidence.py

脚本按清单执行全部场景与零容忍守卫的测试节点，解析 JUnit XML，
把「真实执行且通过」的证据写入 ``.scratch/2/validation/42-workflow-evaluation``。
存在缺失/失败/跳过的节点时以非零退出码结束。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from bridges.evaluation.workflow_evidence import (  # noqa: E402
    build_evidence_report,
    manifest_nodes,
    render_markdown,
)

DEFAULT_OUTPUT_DIR = REPO_ROOT / ".scratch" / "2" / "validation" / "42-workflow-evaluation"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="执行工单 42 场景证据并报告。")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="报告输出目录（相对路径按仓库根解析）。",
    )
    parser.add_argument(
        "--junit",
        type=Path,
        default=None,
        help="复用既有 JUnit XML（提供时不再重新执行 pytest）。",
    )
    parser.add_argument("--timeout", type=float, default=2400.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    output_dir = (
        args.output_dir if args.output_dir.is_absolute() else REPO_ROOT / args.output_dir
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (REPO_ROOT / ".tmp").mkdir(exist_ok=True)

    exit_code: int | None = None
    command = ""
    if args.junit is not None:
        junit_path = args.junit if args.junit.is_absolute() else REPO_ROOT / args.junit
        if not junit_path.exists():
            print(f"JUnit XML 不存在：{junit_path}", file=sys.stderr)
            return 2
    else:
        junit_path = output_dir / "pytest-junit.xml"
        nodes = list(manifest_nodes())
        command_args = [
            sys.executable,
            "-m",
            "pytest",
            *nodes,
            "-p",
            "no:cacheprovider",
            "-q",
            f"--junitxml={junit_path}",
        ]
        command = " ".join(command_args)
        environment = dict(os.environ, PYTHONIOENCODING="utf-8")
        completed = subprocess.run(
            command_args,
            cwd=str(REPO_ROOT),
            env=environment,
            check=False,
            timeout=args.timeout,
        )
        exit_code = completed.returncode

    report = build_evidence_report(
        junit_path.read_text(encoding="utf-8"),
        pytest_exit_code=exit_code,
        pytest_command=command,
    )
    report_path = output_dir / "workflow-evidence.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    markdown_path = output_dir / "workflow-evidence.md"
    markdown_path.write_text(render_markdown(report), encoding="utf-8")

    summary = report["summary"]
    print(
        f"证据报告：场景 {summary['scenarios_ok']}/{report['scenario_count']} 通过，"
        f"问题 {summary['problems']} 个 -> {report_path}"
    )
    for problem in report["problems"]:
        print(f"  - {problem}")
    return 1 if report["problems"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
