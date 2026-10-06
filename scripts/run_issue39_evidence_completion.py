"""工单 39 验收证据补齐入口：真实消融、正式路径执行与部署限额派生。

用法（conda `agent` 环境）::

    python scripts/run_issue39_evidence_completion.py --execute-ablations
    python scripts/run_issue39_evidence_completion.py --execute-paths
    python scripts/run_issue39_evidence_completion.py --derive-report
    python scripts/run_issue39_evidence_completion.py --all

``--derive-report`` 只从冻结主报告派生验证范围与证据引用（深拷贝后仅改
``validation_scope`` 并新增 ``evidence_completion``），盲评材料、运行锁、
真实测量与解盲映射一律保持原样，绝不重新生成主配对。证据文件校验不过时
对应标记保持 False，退出码 1；缺少证据文件或凭据时非零退出并说明。
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from bridges.evaluation.expression_ablation_run import (  # noqa: E402
    run_real_ablations,
    verify_ablation_evidence,
)
from bridges.evaluation.expression_deployment_thresholds import (  # noqa: E402
    evaluate_deployment_thresholds,
)
from bridges.evaluation.expression_frozen_review import (  # noqa: E402
    load_frozen_report,
)
from bridges.evaluation.expression_model_path_receipts import (  # noqa: E402
    run_long_task_receipts,
    run_model_path_receipts,
)
from bridges.evaluation.expression_path_receipts import (  # noqa: E402
    run_deterministic_path_receipts,
    run_fixed_copy_receipts,
    verify_path_receipts,
)
from bridges.evaluation.expression_provenance import (  # noqa: E402
    SUITE_ID,
    SUITE_VERSION,
    code_commit,
)
from bridges.evaluation.expression_real_gateway import (  # noqa: E402
    RetryingStructuredGateway,
    build_eval_quota,
    build_real_gateway,
)

DEFAULT_OUTPUT_DIR = REPO_ROOT / ".scratch" / "2" / "validation" / "39-independent"
DEFAULT_REPORT = (
    REPO_ROOT / ".scratch" / "2" / "validation" / "39-human-expression" / "real-report.json"
)
_FORBIDDEN_PATTERN = re.compile(
    r"(?i)(password|api[_-]?key|secret|authorization)[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9_\-]{8,}"
)


def _local_app_data() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) if base else Path.home() / "AppData/Local"


DEFAULT_DATA_DIR = _local_app_data() / "BridGes/data"


def _digest_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _digest_file(path: Path) -> str:
    return _digest_text(path.read_text(encoding="utf-8"))


def _ensure_redacted(payload: Any) -> None:
    if _FORBIDDEN_PATTERN.search(json.dumps(payload, ensure_ascii=False)):
        raise SystemExit("证据疑似包含秘密字段，拒绝写入；请检查输入。")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    _ensure_redacted(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _composition(data_dir: Path) -> Any:
    composition, _resolver = build_real_gateway(data_dir)
    return composition


def execute_ablations(gateway: Any, output_dir: Path) -> int:
    payload = run_real_ablations(gateway)
    problems = verify_ablation_evidence(payload)
    payload["verification_problems"] = problems
    _write_json(output_dir / "ablation-report.json", payload)
    print(
        f"[39] 真实消融完成：{len(payload['ablations'])} 项，"
        f"模型调用 {payload['model_call_count']} 次，"
        f"校验 {'通过' if not problems else '不通过'}。"
    )
    for problem in problems:
        print(f"  - {problem}")
    return 1 if problems else 0


def execute_paths(composition: Any, output_dir: Path) -> int:
    retrying = RetryingStructuredGateway(composition.gateway, max_attempts=10)
    quota = build_eval_quota(composition)
    receipts = [
        *run_deterministic_path_receipts(),
        run_fixed_copy_receipts(),
        *run_model_path_receipts(retrying, quota),
    ]
    long_runs = run_long_task_receipts(composition.gateway)
    payload: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "code_commit": code_commit(),
        "suite": {"suite_id": SUITE_ID, "suite_version": SUITE_VERSION},
        "paths": receipts,
        "long_task_runs": long_runs,
        "model_lock_count": sum(len(entry.get("model_locks") or []) for entry in receipts),
        "gateway_retries": retrying.retry_log,
    }
    problems = verify_path_receipts(payload)
    payload["verification_problems"] = problems
    _write_json(output_dir / "formal-path-receipts.json", payload)
    print(
        f"[39] 正式路径收据完成：{len(receipts)} 条路径、"
        f"长任务 {len(long_runs)} 条，模型锁 {payload['model_lock_count']} 个，"
        f"校验 {'通过' if not problems else '不通过'}。"
    )
    for problem in problems:
        print(f"  - {problem}")
    return 1 if problems else 0


def derive_report(output_dir: Path, report_path: Path) -> int:
    ablation_path = output_dir / "ablation-report.json"
    receipts_path = output_dir / "formal-path-receipts.json"
    for path in (ablation_path, receipts_path):
        if not path.exists():
            print(f"[39] 缺少证据文件 {path}，拒绝派生报告。")
            return 2
    if not report_path.exists():
        print(f"[39] 缺少冻结主报告 {report_path}，拒绝派生报告。")
        return 2

    main_report = load_frozen_report(report_path)
    ablation = json.loads(ablation_path.read_text(encoding="utf-8"))
    receipts = json.loads(receipts_path.read_text(encoding="utf-8"))
    ablation_problems = verify_ablation_evidence(ablation)
    path_problems = verify_path_receipts(receipts)
    thresholds = evaluate_deployment_thresholds(main_report)
    _write_json(output_dir / "deployment-thresholds.json", thresholds)

    derived = copy.deepcopy(main_report)
    scope = dict(derived.get("validation_scope") or {})
    scope["real_ablations_executed"] = not ablation_problems
    scope["formal_paths_executed"] = not path_problems
    scope["deployment_thresholds_defined"] = thresholds["passed"]
    derived["validation_scope"] = scope
    derived["evidence_completion"] = {
        "derived_at": datetime.now(UTC).isoformat(),
        "code_commit": code_commit(),
        "note": (
            "仅补齐验证范围与证据引用；盲评材料、解盲映射、运行锁与真实测量"
            "与原冻结报告逐字一致。"
        ),
        "from_report": {
            "path": report_path.relative_to(REPO_ROOT).as_posix(),
            "digest": _digest_file(report_path),
        },
        "ablation_report": {
            "path": ablation_path.relative_to(REPO_ROOT).as_posix(),
            "digest": _digest_file(ablation_path),
            "problems": ablation_problems,
        },
        "formal_path_receipts": {
            "path": receipts_path.relative_to(REPO_ROOT).as_posix(),
            "digest": _digest_file(receipts_path),
            "problems": path_problems,
        },
        "deployment_thresholds": {
            "path": (output_dir / "deployment-thresholds.json")
            .relative_to(REPO_ROOT)
            .as_posix(),
            "passed": thresholds["passed"],
            "problems": thresholds["problems"],
        },
    }
    _write_json(output_dir / "derived-report" / "report-v2.json", derived)
    print(
        "[39] 派生报告完成："
        f"真实消融 {'是' if scope['real_ablations_executed'] else '否'}、"
        f"正式路径 {'是' if scope['formal_paths_executed'] else '否'}、"
        f"部署限额 {'是' if scope['deployment_thresholds_defined'] else '否'}。"
    )
    for problem in (*ablation_problems, *path_problems, *thresholds["problems"]):
        print(f"  - {problem}")
    flagged = (
        scope["real_ablations_executed"]
        and scope["formal_paths_executed"]
        and scope["deployment_thresholds_defined"]
    )
    return 0 if flagged else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="工单 39 验收证据补齐")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--execute-ablations", action="store_true")
    parser.add_argument("--execute-paths", action="store_true")
    parser.add_argument("--derive-report", action="store_true")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args(argv)

    if not (args.all or args.execute_ablations or args.execute_paths or args.derive_report):
        parser.print_help()
        return 2

    args.output_dir.mkdir(parents=True, exist_ok=True)
    exit_code = 0
    if args.all or args.execute_ablations or args.execute_paths:
        try:
            composition = _composition(args.data_dir)
        except RuntimeError as exc:
            print(f"[39] 真实证据未执行：缺少可用凭据或适配器（{exc}）。")
            return 3
        if args.all or args.execute_ablations:
            exit_code = max(
                exit_code, execute_ablations(composition.gateway, args.output_dir)
            )
        if args.all or args.execute_paths:
            exit_code = max(exit_code, execute_paths(composition, args.output_dir))
    if args.all or args.derive_report:
        exit_code = max(exit_code, derive_report(args.output_dir, args.report))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
