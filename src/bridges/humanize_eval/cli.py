"""人味评测可复制命令（Issue 01 tracer bullet）。

一条命令完成：案例校验 → 真实运行 → 保真检查 → 系统裁判调用 →
裁判包导出，并生成不含私人正文的执行摘要。

真实提供方运行必须显式启用（--allow-real）；普通调用只做协议校验与
缺项报告，绝不假装成功。缺少全局凭据或 Humanizer-zh 快照时结论为
inconclusive 并列出缺项。

用法示例：
    python -m bridges.humanize_eval.cli run --outdir .scratch/人味化改进/runs
    python -m bridges.humanize_eval.cli run --outdir <outdir> --allow-real
"""

from __future__ import annotations

import argparse
from pathlib import Path

from bridges.humanize_eval.cases import validate_cases


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m bridges.humanize_eval.cli",
        description="真实人味评测 tracer bullet 命令。",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="执行完整评测运行并导出裁判包与摘要。")
    run.add_argument(
        "--outdir",
        type=Path,
        required=True,
        help="输出根目录（runs/ 与摘要存放于此，append-only）。",
    )
    run.add_argument(
        "--snapshot",
        type=str,
        default=None,
        help="Humanizer-zh 快照目录（默认使用机器上的本地参考路径）。",
    )
    run.add_argument("--anon-seed", type=int, default=2026, help="匿名化种子。")
    run.add_argument(
        "--allow-real",
        action="store_true",
        help="显式允许真实模型提供方调用（普通 CI 不得启用）。",
    )
    run.add_argument(
        "--judges",
        type=int,
        default=3,
        help="系统裁判实例数（默认 3；少于 3 时结论固定为 inconclusive）。",
    )

    sub.add_parser("validate", help="只校验案例定义与哈希。")
    return parser


def _run_command(args: argparse.Namespace) -> int:
    from bridges.humanize_eval.generation import QwenGenerationPort
    from bridges.humanize_eval.judges import build_judges
    from bridges.humanize_eval.runner import HumanizeRunError, HumanizeRunner

    if not args.allow_real:
        print(
            "提示：未启用 --allow-real，本次调用不会访问真实模型提供方；"
            "缺少凭据时结论为 inconclusive（不会假成功）。"
        )
    port = QwenGenerationPort()
    judges = build_judges(port)[: max(1, args.judges)]
    runner = HumanizeRunner(
        outdir=args.outdir,
        workspace=Path.cwd(),
        snapshot_dir=args.snapshot,
        port=port,
        judges=judges,
        anon_seed=args.anon_seed,
        allow_real=args.allow_real,
    )
    try:
        summary = runner.run()
    except HumanizeRunError as exc:
        print(f"运行被拒绝（结论为 inconclusive）：{exc}")
        return 1
    print(f"运行完成：{summary.run_id}")
    print(f"结论：{summary.verdict}")
    for reason in summary.reasons:
        print(f"  - {reason}")
    if summary.packet_path:
        print(f"裁判包：{summary.packet_path}")
        print(f"organizer mapping：{summary.mapping_path}")
    summary_path = args.outdir / "runs" / summary.run_id / "summary.json"
    print(f"脱敏执行摘要：{summary_path}")
    return 0


def _validate_command(args: argparse.Namespace) -> int:
    problems = validate_cases()
    if problems:
        print("案例校验失败：")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("案例校验通过（哈希一致、必填字段完整）。")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "run":
        return _run_command(args)
    if args.command == "validate":
        return _validate_command(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
