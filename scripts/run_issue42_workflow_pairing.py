"""工单 42：旧 V2 与新编排的同任务真实模型配对入口。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
# 命令入口按仓库包导入评测职责，子进程生产模块仍从目标树延迟导入。
sys.path.insert(0, str(REPO_ROOT))
from scripts.issue42_pairing_report import (  # noqa: E402 - 独立命令入口先定位仓库
    BASELINE_COMMIT,
    render_markdown,
    summarize_pairing,
)
from scripts.issue42_pairing_runtime import run_tree  # noqa: E402

DEFAULT_OLD_TREE = REPO_ROOT.parent / "42-baseline-7818c34"
DEFAULT_OUTPUT_DIR = REPO_ROOT / ".scratch" / "2" / "validation" / "42-workflow-evaluation"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="工单 42 旧/新树真实模型配对。")
    parser.add_argument("--old-tree", type=Path, default=DEFAULT_OLD_TREE)
    parser.add_argument("--new-tree", type=Path, default=REPO_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=240.0)
    parser.add_argument("--proxy", default=None)
    parser.add_argument("--run-tree", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--result-json", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument(
        "--no-create-old-tree", action="store_true", help="旧树缺失时不自动创建 worktree。"
    )
    return parser.parse_args(argv)


def _ensure_old_tree(tree: Path, *, allow_create: bool) -> None:
    if (tree / "src" / "bridges").exists():
        return
    if not allow_create:
        raise RuntimeError(f"旧树不存在：{tree}")
    completed = subprocess.run(
        ["git", "worktree", "add", "--detach", str(tree), BASELINE_COMMIT],
        cwd=str(REPO_ROOT),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0 or not (tree / "src" / "bridges").exists():
        raise RuntimeError(f"创建旧树 worktree 失败：{completed.stderr.strip()}")


def _run_child(
    tree: Path,
    result_path: Path,
    *,
    repeats: int,
    timeout: float,
    proxy: str | None,
    script: Path,
) -> int:
    command = [
        sys.executable,
        str(script),
        "--run-tree",
        str(tree),
        "--result-json",
        str(result_path),
        "--repeats",
        str(repeats),
        "--timeout",
        str(timeout),
    ]
    if proxy:
        command.extend(["--proxy", proxy])
    completed = subprocess.run(command, cwd=str(tree), check=False)
    return completed.returncode


def _budget_snapshot() -> dict[str, Any]:
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from bridges.chat.run_budget_ledger import (  # noqa: PLC0415
        RUN_BUDGET_CONTRACT_VERSION,
        RUN_BUDGET_INITIALS,
    )

    return {
        "contract_version": RUN_BUDGET_CONTRACT_VERSION,
        "classes": {
            budget_class.value: {
                "total_budget_ms": initials.total_budget_ms,
                "verify_deliver_reserve_ms": initials.verify_deliver_reserve_ms,
                "model_call_limit": initials.model_call_limit,
                "external_parallel_max": initials.external_parallel_max,
                "candidate_screen_max": initials.candidate_screen_max,
                "deep_read_max": initials.deep_read_max,
                "adjustment_rounds_max": initials.adjustment_rounds_max,
                "transient_retry_max": initials.transient_retry_max,
            }
            for budget_class, initials in RUN_BUDGET_INITIALS.items()
        },
    }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.run_tree is not None:
        if args.result_json is None:
            print("--run-tree 需要 --result-json", file=sys.stderr)
            return 2
        return run_tree(
            args.run_tree,
            args.result_json,
            repeats=args.repeats,
            timeout=args.timeout,
            proxy=args.proxy,
        )

    old_tree = args.old_tree if args.old_tree.is_absolute() else REPO_ROOT / args.old_tree
    new_tree = args.new_tree if args.new_tree.is_absolute() else REPO_ROOT / args.new_tree
    output_dir = args.output_dir if args.output_dir.is_absolute() else REPO_ROOT / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        _ensure_old_tree(old_tree, allow_create=not args.no_create_old_tree)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    script = Path(__file__).resolve()
    old_result_path = output_dir / f"workflow-pairing-old-{stamp}.json"
    new_result_path = output_dir / f"workflow-pairing-new-{stamp}.json"
    old_code = _run_child(
        old_tree,
        old_result_path,
        repeats=args.repeats,
        timeout=args.timeout,
        proxy=args.proxy,
        script=script,
    )
    new_code = _run_child(
        new_tree,
        new_result_path,
        repeats=args.repeats,
        timeout=args.timeout,
        proxy=args.proxy,
        script=script,
    )
    if not old_result_path.exists() or not new_result_path.exists():
        print("一侧未产出结果文件。", file=sys.stderr)
        return 2
    old_result = json.loads(old_result_path.read_text(encoding="utf-8"))
    new_result = json.loads(new_result_path.read_text(encoding="utf-8"))
    if old_result.get("inconclusive") or new_result.get("inconclusive"):
        print(
            f"存在不可判定的一侧：old={old_result.get('reason')} new={new_result.get('reason')}",
            file=sys.stderr,
        )
        return 2

    budgets_payload = _budget_snapshot()
    report = summarize_pairing(old_result, new_result)
    report["budgets"] = budgets_payload
    report_path = output_dir / f"workflow-pairing-{stamp}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path = output_dir / f"workflow-pairing-{stamp}.md"
    markdown_path.write_text(render_markdown(report, budgets_payload["classes"]), encoding="utf-8")

    print(f"配对报告：{report_path}\n旧侧退出码 {old_code}，新侧退出码 {new_code}")
    for problem in report["problems"]:
        print(f"  [问题] {problem}")
    return 1 if report["problems"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
