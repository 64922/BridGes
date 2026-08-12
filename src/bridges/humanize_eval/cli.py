"""人味评测可复制命令（Issue 01 tracer bullet + Issue 09 分层语料）。

一条命令完成：案例校验 → 真实运行 → 保真检查 → 系统裁判调用 →
裁判包导出，并生成不含私人正文的执行摘要。聊天与文章分别报告；
holdout 默认冻结（未解封时只运行 development 分区）。

真实提供方运行必须显式启用（--allow-real）；普通调用只做协议校验与
缺项报告，绝不假装成功。缺少全局凭据或 Humanizer-zh 快照时结论为
inconclusive 并列出缺项。

用法示例：
    python -m bridges.humanize_eval.cli validate
    python -m bridges.humanize_eval.cli datacard --out .scratch/人味化改进/corpus
    python -m bridges.humanize_eval.cli freeze-holdout
    python -m bridges.humanize_eval.cli run --outdir .scratch/人味化改进/runs
    python -m bridges.humanize_eval.cli run --outdir <outdir> --allow-real
    python -m bridges.humanize_eval.cli run --outdir <outdir> --surface article
"""

from __future__ import annotations

import argparse
import json
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
    run.add_argument(
        "--surface",
        type=str,
        choices=("chat", "article"),
        default=None,
        help="只运行指定语料面（默认全部）。",
    )
    run.add_argument(
        "--allow-holdout",
        action="store_true",
        help="允许运行 holdout 案例（仅已解封时有效；未解封一律拒绝并审计）。",
    )

    validate = sub.add_parser("validate", help="只校验案例定义、哈希与来源清洁。")
    validate.add_argument(
        "--datacard",
        type=Path,
        default=None,
        help="同时输出数据卡 JSON 到指定文件。",
    )

    datacard = sub.add_parser("datacard", help="生成语料数据卡（脱敏统计）。")
    datacard.add_argument("--out", type=Path, required=True, help="输出 JSON 路径。")

    freeze = sub.add_parser("freeze-holdout", help="冻结当前 holdout 案例哈希（首次）。")
    return parser


def _run_command(args: argparse.Namespace) -> int:
    from bridges.humanize_eval.generation import QwenGenerationPort
    from bridges.humanize_eval.holdout import HoldoutController, HoldoutError
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
        holdout=HoldoutController(Path.cwd()),
        surface=args.surface,
        allow_holdout=args.allow_holdout,
    )
    try:
        summary = runner.run()
    except (HumanizeRunError, HoldoutError) as exc:
        print(f"运行被拒绝（结论为 inconclusive）：{exc}")
        return 1
    print(f"运行完成：{summary.run_id}")
    print(f"结论：{summary.verdict}")
    if summary.holdout_excluded:
        print(f"holdout 冻结排除案例数：{summary.holdout_excluded}")
    for name, verdict in summary.surface_verdicts.items():
        print(f"  [{name}] {verdict}（{summary.surface_cases.get(name, 0)}/"
              f"{summary.surface_case_total.get(name, 0)} 成功）")
    if summary.canary_passed:
        canary_line = "；".join(
            f"{judge_id}:{'通过' if passed else '未通过'}"
            for judge_id, passed in summary.canary_passed.items()
        )
        print(f"裁判 canary 硬门：{canary_line}")
    if summary.panel_issues:
        for issue in summary.panel_issues:
            print(f"  - {issue}")
    for reason in summary.reasons:
        print(f"  - {reason}")
    if summary.packet_path:
        print(f"裁判包：{summary.packet_path}")
        print(f"sealed mapping：{summary.mapping_path}")
    if summary.judge_count:
        print("注：系统自动裁判结果，未经真实用户或人工验证。")
    summary_path = args.outdir / "runs" / summary.run_id / "summary.json"
    print(f"脱敏执行摘要：{summary_path}")
    # Issue 11：发布质量门（chat/article 分别裁决）。候选未通过时返回
    # 非零退出状态与稳定中文原因；普通非人味评测不受本门控影响。
    gate = summary.gate_report
    if gate is not None:
        from bridges.humanize_eval.release_gate import format_gate_blockers

        print(
            "发布质量门（automated_system_judges_only=true，"
            "human_validated=false）："
        )
        for name, verdict in gate.surface_verdicts.items():
            print(f"  [{name}] {verdict}")
        blockers = format_gate_blockers(gate)
        for blocker in blockers:
            print(f"  - {blocker}")
        if gate.passed:
            print("发布门通过（全部语料面 passed）。")
            return 0
        print("发布门未通过：candidate 不得发布（含 inconclusive 不得视为通过）。")
        return 1
    # 无门报告（生成失败/裁判全部不可用等）但结论不是 passed：
    # 不得沿用自动成功语义，同样返回非零。
    if summary.verdict != "passed":
        print("无发布门报告且运行结论不是 passed，candidate 不得发布。")
        return 1
    return 0


def _validate_command(args: argparse.Namespace) -> int:
    from bridges.humanize_eval.cases import HUMANIZE_CASES
    from bridges.humanize_eval.cleanliness import run_cleanliness_scan

    problems = validate_cases()
    if problems:
        print("案例校验失败：")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    scan = run_cleanliness_scan(HUMANIZE_CASES)
    print("案例校验通过（哈希一致、必填字段完整）。")
    print(f"来源清洁扫描：{scan.checked_cases} 案例，净室声明 "
          f"{scan.license_complete}/{scan.checked_cases}，"
          f"跨分区重复 {len(scan.duplicate_pairs)}，"
          f"保护项违规 {len(scan.protected_violations)}。")
    if not scan.clean:
        print("来源清洁扫描未通过：")
        for issue in scan.duplicate_pairs + scan.protected_violations:
            print(f"  - {issue}")
        return 1
    if args.datacard:
        _write_datacard(args.datacard)
    print("来源清洁扫描通过（净室边界与许可证清单合规）。")
    return 0


def _write_datacard(path: Path) -> None:
    from bridges.humanize_eval.cases import HUMANIZE_CASES
    from bridges.humanize_eval.cleanliness import generate_datacard

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = generate_datacard(HUMANIZE_CASES)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"数据卡已写入：{path}")


def _datacard_command(args: argparse.Namespace) -> int:
    _write_datacard(args.out)
    return 0


def _freeze_holdout_command(args: argparse.Namespace) -> int:
    from bridges.humanize_eval.cases import CORPUS_VERSION, HUMANIZE_CASES, case_hashes
    from bridges.humanize_eval.holdout import HoldoutController, HoldoutError

    controller = HoldoutController(Path.cwd())
    try:
        sealed = {
            case.case_id: case.ensure_hash()
            for case in HUMANIZE_CASES
            if case.partition.value == "holdout"
        }
        manifest = controller.build_manifest(
            sealed, corpus_version=CORPUS_VERSION
        )
        controller.save_manifest(manifest)
    except HoldoutError as exc:
        print(f"冻结失败：{exc}")
        return 1
    print(
        f"holdout 已冻结：{len(sealed)} 案例，提交 {manifest.frozen_commit}，"
        f"清单 {controller.manifest_path}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "run":
        return _run_command(args)
    if args.command == "validate":
        return _validate_command(args)
    if args.command == "datacard":
        return _datacard_command(args)
    if args.command == "freeze-holdout":
        return _freeze_holdout_command(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
