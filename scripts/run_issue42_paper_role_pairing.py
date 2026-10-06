"""实际运行旧V2生产筛选，与保存的真实新语义筛选收据配对；不发额外网络请求。"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()


def run_old(tree: Path, input_path: Path, result_path: Path, repeats: int) -> None:
    # 此子进程尚未导入bridges；首先只装载指定旧树生产包，禁止当前树污染。
    sys.path.insert(0, str(tree / "src"))
    from bridges.paper.parsing import parse_paper_request
    from bridges.paper.planning import plan_queries
    from bridges.paper.ranking import rank_candidates
    from bridges.paper.sources import PaperCandidate

    request = json.loads(input_path.read_text(encoding="utf-8"))
    candidates = [
        PaperCandidate(**{**row, "published_at": datetime.fromisoformat(row["published_at"])})
        for row in request["candidates"]
    ]
    runs = []
    for _ in range(repeats):
        started = time.perf_counter()
        analysis = parse_paper_request(request["request"], now=datetime(2026, 10, 6))
        plan = plan_queries(analysis)[0]
        outcome = rank_candidates(analysis, plan, candidates, {}, target_count=len(candidates))
        runs.append(
            {
                "analysis": analysis.model_dump(mode="json"),
                "plan": plan.model_dump(mode="json"),
                "selected": [item.arxiv_id for item in outcome.recommendations],
                "notes": outcome.notes,
                "topic_mismatch": outcome.topic_mismatch,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            }
        )
    modules = (parse_paper_request, plan_queries, rank_candidates)
    imported = {fn.__module__: sys.modules[fn.__module__].__file__ for fn in modules}
    for path in imported.values():
        if not Path(path).resolve().is_relative_to((tree / "src").resolve()):
            raise ValueError("旧侧生产包导入来自错误工作树。")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tree, text=True).strip()
    result = {
        "commit": commit,
        "paired_input_sha256": _digest(request),
        "runs": runs,
        "imported_modules": imported,
        "source_sha256": {
            name: hashlib.sha256(Path(path).read_bytes()).hexdigest()
            for name, path in imported.items()
        },
        "cost_basis": "旧生产解析/计划/排序纯函数，未装配模型或外部读取；调用数0",
    }
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-tree", type=Path, required=True)
    parser.add_argument("--real-artifact", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--run-old", action="store_true")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--repeats", type=int, default=2)
    args = parser.parse_args()
    if args.run_old:
        run_old(args.old_tree.resolve(), args.input, args.result, args.repeats)
        return 0
    if args.real_artifact is None or args.output_dir is None:
        parser.error("父进程需要真实收据和输出目录。")
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from bridges.evaluation.paper_role_pairing import (
        build_role_pairing,
        paired_input,
        real_relevance_calls,
    )

    artifact = json.loads(args.real_artifact.read_text(encoding="utf-8"))
    calls = real_relevance_calls(artifact)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    input_path = args.output_dir / "paired-input.json"
    result_path = args.output_dir / "old-production-result.json"
    input_path.write_text(
        json.dumps(paired_input(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--run-old",
            "--old-tree",
            str(args.old_tree.resolve()),
            "--input",
            str(input_path.resolve()),
            "--result",
            str(result_path.resolve()),
            "--repeats",
            str(len(calls)),
        ],
        check=True,
        cwd=args.old_tree.resolve(),
    )
    old = json.loads(result_path.read_text(encoding="utf-8"))
    expected = subprocess.check_output(
        ["git", "rev-parse", "7818c34"], cwd=REPO_ROOT, text=True
    ).strip()
    if old["commit"] != expected:
        raise ValueError("旧侧实际代码不是锁定V2基线。")
    report = build_role_pairing(old, artifact)
    report["real_artifact_sha256"] = hashlib.sha256(args.real_artifact.read_bytes()).hexdigest()
    report["real_artifact_path"] = str(args.real_artifact.resolve())
    target = args.output_dir / "paper-role-pairing.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"专业筛选配对：收益={report['observable_gain']}，新侧证据通过={report['new_passed']}；{target}"
    )
    return 0 if report["observable_gain"] and report["new_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
