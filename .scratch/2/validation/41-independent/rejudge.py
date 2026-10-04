"""对保存的真实回答离线复评；不调用模型，不改写原始证据。"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from bridges.evaluation.profile_pairing import PairingResponse, run_pairing, write_reports

DIRECTORY = Path(__file__).resolve().parent
source = DIRECTORY / "final-real/pairing-report.json"
payload = json.loads(source.read_text("utf-8"))
responses = {(run["task_id"], run["condition"]): run for run in payload["runs"]}


def sender(task, condition):
    run = responses[task.task_id, condition.value]
    return PairingResponse(**{name: run.get(name) for name in (
        "status", "answer", "latency_ms", "model_id", "input_tokens", "output_tokens",
        "cost_estimate", "profile_item_count", "error_code",
    )})


commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
report = run_pairing(sender, environment={
    "mode": "offline-rejudge-no-model-calls",
    "generation_environment": payload["environment"],
    "judging_code_commit": commit,
    "raw_report_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    "judge_source_sha256": hashlib.sha256(
        (ROOT / "src/bridges/evaluation/profile_pairing.py").read_bytes()
    ).hexdigest(),
})
write_reports(report, DIRECTORY / "rejudged")
print(json.dumps({"passed": report.passed, "runs": len(report.runs),
                  "new_model_calls": 0, "judge_commit": commit}, ensure_ascii=False))
raise SystemExit(0 if report.passed else 1)
