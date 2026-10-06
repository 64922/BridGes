"""独立复核已提交材料和 Git 来源；只读历史证据，输出单独审计 JSON。"""

from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from bridges.contracts.evaluation_suite import SuiteRunLock  # noqa: E402
from bridges.evaluation.expression_corpus import SCENARIOS  # noqa: E402
from bridges.evaluation.expression_gates import evaluate_hard_gates  # noqa: E402
from bridges.evaluation.expression_review import (  # noqa: E402
    DEFAULT_COMPARISONS,
    BlindPairItem,
    ScenarioTranscript,
    TranscriptTurn,
    build_blind_review,
)


def git_blob(ref: str, path: str) -> bytes:
    return subprocess.check_output(["git", "show", f"{ref}:{path}"], cwd=ROOT)


def main() -> None:
    directory = ROOT / ".scratch/2/validation/39-human-expression"
    report = json.loads((directory / "real-report.json").read_text(encoding="utf-8"))
    lock_payload = json.loads((directory / "run-lock.json").read_text(encoding="utf-8"))
    lock = SuiteRunLock.model_validate(lock_payload)
    commit = report["environment"]["code_commit"]
    mismatches = []
    for path, digest in report["environment"]["source_hashes"].items():
        source = git_blob(commit, path)
        # 报告哈希来自 Windows 工作区；同时核对 Git LF 与检出后的 CRLF。
        hashes = {hashlib.sha256(value).hexdigest() for value in
                  (source, source.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))}
        if digest not in hashes:
            mismatches.append(path)
    scenarios = {scenario.scenario_id: scenario for scenario in SCENARIOS}
    transcripts = {}
    current_gate_failures = []
    for run in report["runs"]:
        scenario = scenarios[run["scenario_id"]]
        transcripts[(scenario.scenario_id, run["arm"])] = ScenarioTranscript(
            scenario_id=scenario.scenario_id, title=scenario.title,
            category=scenario.category.value, formal_path=scenario.formal_path,
            arm_id=run["arm"], turns=tuple(TranscriptTurn(**turn) for turn in run["turns"]),
        )
        for index, turn in enumerate(run["turns"], 1):
            for gate in evaluate_hard_gates(
                scenario, turn_index=index, answer=turn["assistant"], status=turn["status"]
            ):
                if not gate.passed:
                    current_gate_failures.append({"arm": run["arm"],
                                                  "scenario_id": scenario.scenario_id,
                                                  **gate.to_dict()})
    items = [BlindPairItem(**item) for item in report["blind_review"]["items"]]
    rebuilt_items, rebuilt_mapping = build_blind_review(
        items[0].review_set_id, transcripts, order_seed=items[0].order_seed
    )
    material = (directory / "blind-review.md").read_text(encoding="utf-8")
    forbidden = ["current-v4", "legacy-v2", "concise-baseline",
                 *[comparison.comparison_id for comparison in DEFAULT_COMPARISONS],
                 *scenarios]
    old = ast.parse(git_blob("079faab6", "src/bridges/chat/lightweight_policy.py"))
    replay = ast.parse((ROOT / "src/bridges/evaluation/legacy_v2_policy.py").read_text(
        encoding="utf-8"
    ))

    def tables(tree: ast.Module) -> dict[str, str]:
        return {node.target.id: ast.dump(node.value)
                for node in tree.body if isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id in {"FORM_RULES", "GLOBAL_DEFAULT_RULES", "FORM_LABELS"}}

    output = {
        "source_commit": commit, "source_file_count": len(report["environment"]["source_hashes"]),
        "source_hash_mismatches": mismatches,
        "lock_matches_report": lock_payload == report["run_lock"],
        "lock_digest_matches": lock.digest() == report["run_lock_digest"],
        "observed_digest_matches": lock.observed_digest() == report["observed_locks_digest"],
        "models": dict(Counter(item.actual_model_id for item in lock.model_run_locks)),
        "max_tokens": dict(Counter(item.parameters["max_tokens"] for item in lock.model_run_locks)),
        "temperature": dict(Counter(
            item.parameters["temperature"] for item in lock.model_run_locks
        )),
        "original_reviewer_count": report["blind_review"]["reviewer_count"],
        "executed_scenarios": sorted({run["scenario_id"] for run in report["runs"]}),
        "executed_distribution": dict(Counter(
            scenarios[scenario_id].category.value for scenario_id in
            {run["scenario_id"] for run in report["runs"]}
        )),
        "replayed_blind_items_equal": rebuilt_items == items,
        "replayed_blind_mapping_equal": rebuilt_mapping == report["blind_review"]["mapping"],
        "blind_material_identity_leaks": [word for word in forbidden if word in material],
        "legacy_rule_table_ast_equal": tables(old) == tables(replay) and bool(tables(old)),
        "current_diagnostic_gate_failures_on_frozen_answers": current_gate_failures,
        "limits": ["运行锁是仓库产物，未向供应商查询独立账单。",
                   "机器门只作诊断，不能代替语义硬失败的人工核查。",
                   "未重新调用模型；不将冻结样本当作修复后新真实运行。"],
    }
    destination = Path(__file__).with_name("evidence-audit.json")
    destination.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in output.items()
                      if key not in {"executed_scenarios",
                                     "current_diagnostic_gate_failures_on_frozen_answers"}},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
