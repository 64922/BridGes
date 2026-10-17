"""工单 18：真实模型画像闭环评测运行器。

用法（在仓库根目录、``conda agent`` 环境）：

    python scripts/run_issue18_profile_loop_evaluation.py \
        --mode real --proxy http://127.0.0.1:7890 \
        --output .scratch/profile-memory-redesign/validation/18-real-model-loop-evaluation/evidence

- ``real``：development 环境 + 操作系统凭据库中的真实主模型；不注册任何
  替身。主模型适配器缺失时写 ``inconclusive`` 并以退出码 2 结束。
- ``scripted``：test 环境 + 确定性替身，只用于冒烟与故障注入，结果不得
  冒充真实模型结论（响应标记为 stub）。
- 场景、变体、对照实验与硬边界判定在 ``profile_loop_eval/scenes.py``
  运行前冻结；本运行器不因实测结果调整判定。
- 每次运行使用仓库 ``.tmp`` 下的隔离数据目录与独立数据库；不接触正式
  数据库、不记录任何凭据正文。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

#: 不可被平均分抵消的硬边界判定前缀（隔离、删除、越权、遗忘、控制、
#: 手改权威、幂等与恢复；凭据泄漏另按 forbidden 关键词单列）。
_HARD_CHECK_PREFIXES = (
    "boundary:",
    "isolation:",
    "forget:",
    "controls:",
    "authority:",
    "idempotency:",
    "recovery:",
)
_HARD_CHECK_IDS = {"forbidden:123456", "forbidden:银行卡密码"}


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("real", "scripted"), default="real")
    parser.add_argument("--scenarios", default="", help="逗号分隔的 T 场景子集；默认全部 T01–T20")
    parser.add_argument("--skip-paired", action="store_true", help="跳过画像对照实验")
    parser.add_argument("--skip-boundaries", action="store_true", help="跳过补充硬边界场景")
    parser.add_argument(
        "--skip-baseline-journeys",
        action="store_true",
        help="不运行 01 运行器的候选旅程对照（真实模式默认运行）",
    )
    parser.add_argument("--proxy", default="", help="HTTP/HTTPS 代理，如 http://127.0.0.1:7890")
    parser.add_argument("--strict", action="store_true", help="硬边界失败时以退出码 4 结束")
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT
        / ".scratch"
        / "profile-memory-redesign"
        / "validation"
        / "18-real-model-loop-evaluation"
        / "evidence",
    )
    return parser.parse_args(argv)


def _hard_failures(results: list[Any]) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    for result in results:
        for check in result.checks:
            if check.passed:
                continue
            if check.check_id.startswith(_HARD_CHECK_PREFIXES) or (
                check.check_id in _HARD_CHECK_IDS
            ):
                failures.append(
                    {
                        "scene_id": result.record.scene_id,
                        "check_id": check.check_id,
                        "detail": check.detail,
                    }
                )
    return failures


def _run_baseline_journeys(evidence_dir: Path, proxy: str) -> None:
    """用 01 运行器重放候选旅程（同一检查口径），结果供基线对照。"""

    target = evidence_dir / "01-journeys"
    target.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "run_issue01_profile_baseline.py"),
        "--mode",
        "real",
        "--output",
        str(target),
    ]
    if proxy:
        command += ["--proxy", proxy]
    print("[18] 运行 01 候选旅程对照 ...", flush=True)
    completed = subprocess.run(command, cwd=REPO_ROOT, timeout=10800)
    print(f"[18] 01 候选旅程退出码：{completed.returncode}", flush=True)


def _run_mode(mode: str, args: argparse.Namespace, output_dir: Path, stamp: str) -> int:
    from bridges.evaluation.profile_loop_eval import evidence as evidence_mod
    from bridges.evaluation.profile_loop_eval import metrics
    from bridges.evaluation.profile_loop_eval.environment import LoopEnvironment
    from bridges.evaluation.profile_loop_eval.runner import (
        LoopEvaluationContext,
        collect_resource_evidence,
        run_boundary_suite,
        run_paired_suite,
        run_scenario_suite,
        scenario_ids_for,
    )

    response_mode = "real" if mode == "real" else "stub"
    selected = scenario_ids_for(args.scenarios)
    run_dir = REPO_ROOT / ".tmp" / f"issue18-{mode}-{stamp}-{uuid.uuid4().hex[:6]}"
    environment = LoopEnvironment(repo_root=REPO_ROOT, run_dir=run_dir, mode=mode, proxy=args.proxy)
    payload: dict[str, Any] = {
        "kind": "profile-loop-evaluation",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "run_dir": str(run_dir),
        "results": [],
        "inconclusive": False,
        "reason": None,
        "notice": (
            "本文件由脚本生成：判定标准运行前冻结；真实与替身分开标记；"
            "缺少价格依据时只报用量。未达标项与局限见 "
            "18-real-model-loop-evaluation/verification-record.md。"
        ),
    }

    def _write_inconclusive(reason: str) -> int:
        payload["inconclusive"] = True
        payload["reason"] = reason
        payload["summary"] = {"matrix": [], "check_totals": {}, "sample_size": {}}
        payload["resources"] = {
            "resources": metrics.summarize_resources([]),
            "latency": metrics.latency_summary([]),
            "tasks_by_status": {},
            "queue_depth_max_observed": 0,
            "queue_depth_samples": 0,
        }
        payload["run_lock"] = evidence_mod.build_run_lock(
            environment, REPO_ROOT, scenarios=selected, response_mode=response_mode
        )
        payload["baseline_comparison"] = {"available": False, "reason": reason}
        evidence_mod.write_evidence(
            output_dir,
            stamp=stamp,
            payload=payload,
            markdown=evidence_mod.render_markdown(payload),
        )
        environment.close()
        print(f"[inconclusive] {reason}")
        return 2

    environment.start()
    started_monotonic = time.monotonic()
    if mode == "real":
        gateway = environment.app.state.model_gateway
        missing = [
            name
            for name, version in (
                ("qwen_text_chat", "1"),
                ("qwen_profile_extraction", "1"),
            )
            if gateway.get_adapter(name, version) is None
        ]
        if missing:
            return _write_inconclusive(f"主模型适配器缺失：{', '.join(missing)}")

    ctx = LoopEvaluationContext(environment)
    results: list[Any] = []
    results.extend(run_scenario_suite(ctx, selection=selected))
    if not args.skip_paired:
        results.extend(run_paired_suite(ctx))
    if not args.skip_boundaries:
        results.extend(run_boundary_suite(ctx))

    payload["results"] = evidence_mod.results_payload(results)
    payload["summary"] = metrics.summarize_results(results)
    payload["resources"] = collect_resource_evidence(
        environment, results, started_monotonic=started_monotonic
    )
    payload["run_lock"] = evidence_mod.build_run_lock(
        environment, REPO_ROOT, scenarios=selected, response_mode=response_mode
    )

    baseline: dict[str, Any] = {
        "available": False,
        "reason": "未运行 01 候选旅程对照。",
    }
    if mode == "real" and not args.skip_baseline_journeys:
        environment.close()
        _run_baseline_journeys(output_dir, args.proxy)
        candidate = evidence_mod.load_baseline_candidate(output_dir / "01-journeys")
        frozen = evidence_mod.load_frozen_baseline(REPO_ROOT)
        baseline = evidence_mod.compare_baseline(frozen, candidate)
    payload["baseline_comparison"] = baseline

    hard = _hard_failures(results)
    payload["hard_failures"] = hard
    json_path, md_path = evidence_mod.write_evidence(
        output_dir,
        stamp=stamp,
        payload=payload,
        markdown=evidence_mod.render_markdown(payload),
    )
    if mode == "real" and environment._app is not None:  # noqa: SLF001
        environment.app.state.bridges_database.close()
        environment.app.state.state_store.close()
    elif mode != "real":
        environment.close()

    summary = payload["summary"]
    failed_scenes = [row for row in summary["matrix"] if row["status"] != "pass"]
    print(
        f"[done] 场景 {len(results)} 条；未全通过对的 {len(failed_scenes)} 条；"
        f"硬边界失败 {len(hard)} 条。"
    )
    for row in failed_scenes:
        print(
            f"  - {row['scene_id']}（{row['status']}）："
            f"{', '.join(row['failed_checks']) or '无失败判定'}"
        )
    print(f"[done] evidence written to {json_path} and {md_path}")
    if args.strict and hard:
        return 4
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    output_dir: Path = args.output if args.output.is_absolute() else REPO_ROOT / args.output
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return _run_mode(args.mode, args, output_dir, stamp)


if __name__ == "__main__":
    raise SystemExit(main())
