"""工单 18：运行锁、证据文件与对照基线比较。"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bridges.evaluation.profile_loop_eval.scenes import PAIRED_TASKS, scenario_payload
from bridges.evaluation.profile_loop_eval.types import PROFILE_LOOP_EVAL_VERSION, SceneResult


def _git(repo_root: Path, *args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _hash_files(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _canonical_digest(payload: Any) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def eval_assets_digest() -> str:
    return _canonical_digest(
        {
            "eval_version": PROFILE_LOOP_EVAL_VERSION,
            "scenarios": scenario_payload(),
            "paired_tasks": [
                {
                    "task_id": task.task_id,
                    "correct_seed": task.correct_seed,
                    "wrong_seed": task.wrong_seed,
                    "question": task.question,
                    "correction_text": task.correction_text,
                    "recurrence_question": task.recurrence_question,
                }
                for task in PAIRED_TASKS
            ],
        }
    )


def build_run_lock(
    environment: Any,
    repo_root: Path,
    *,
    scenarios: tuple[str, ...],
    response_mode: str,
) -> dict[str, Any]:
    from bridges.evaluation.profile_journey_runner import JOURNEY_TURN_TIMEOUT_SECONDS
    from bridges.profiles.atomic import ATOMIC_PROFILE_MIGRATION_VERSION
    from bridges.profiles.automatic import (
        AUTOMATIC_EXTRACTOR_VERSION,
        PROFILE_EXTRACTION_CONTEXT_VERSION,
        PROFILE_EXTRACTION_MAX_NEIGHBORS,
        PROFILE_EXTRACTION_MAX_RETRIES,
        PROFILE_EXTRACTION_PROMPT_VERSION,
        PROFILE_EXTRACTION_QUALITY_POLICY_VERSION,
        PROFILE_EXTRACTION_RECIPE_VERSION,
    )
    from bridges.profiles.signals import PROFILE_SIGNAL_CLASSIFIER_VERSION

    pipeline_files = sorted((repo_root / "src" / "bridges" / "profiles").glob("*.py"))
    pipeline_files += [
        repo_root / "src" / "bridges" / "api" / "app_setup_models.py",
        repo_root / "src" / "bridges" / "api" / "app_support.py",
        repo_root / "src" / "bridges" / "chat" / "turn.py",
    ]
    evaluation_files = sorted(
        (repo_root / "src" / "bridges" / "evaluation" / "profile_loop_eval").glob("*.py")
    )
    evaluation_files += [
        repo_root / "scripts" / "run_issue18_profile_loop_evaluation.py",
    ]
    model_config = None
    provider = getattr(environment.app.state, "run_model_config_provider", None)
    if provider is not None:
        model_config = provider.snapshot().model_dump(mode="json")
    credentials = _credential_presence(environment)
    return {
        "kind": "profile-loop-evaluation-run-lock",
        "eval_version": PROFILE_LOOP_EVAL_VERSION,
        "eval_assets_sha256": eval_assets_digest(),
        "scenarios": list(scenarios),
        "response_mode": response_mode,
        "git_commit": _git(repo_root, "rev-parse", "HEAD"),
        "git_dirty": bool(_git(repo_root, "status", "--porcelain", "--untracked-files=no")),
        "pipeline_snapshot_sha256": _hash_files(pipeline_files),
        "evaluation_sha256": _hash_files(evaluation_files),
        "python_version": sys.version.split()[0],
        "model_config": model_config,
        "contract_versions": {
            "automatic_extractor": AUTOMATIC_EXTRACTOR_VERSION,
            "signal_classifier": PROFILE_SIGNAL_CLASSIFIER_VERSION,
            "prompt": PROFILE_EXTRACTION_PROMPT_VERSION,
            "recipe": PROFILE_EXTRACTION_RECIPE_VERSION,
            "context": PROFILE_EXTRACTION_CONTEXT_VERSION,
            "quality_policy": PROFILE_EXTRACTION_QUALITY_POLICY_VERSION,
            "atomic_migration": ATOMIC_PROFILE_MIGRATION_VERSION,
        },
        "budget_constants": {
            "turn_timeout_seconds": JOURNEY_TURN_TIMEOUT_SECONDS,
            "profile_extraction_max_retries": PROFILE_EXTRACTION_MAX_RETRIES,
            "profile_extraction_max_neighbors": PROFILE_EXTRACTION_MAX_NEIGHBORS,
        },
        "credentials": credentials,
        "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def _credential_presence(environment: Any) -> dict[str, Any]:
    from bridges.evaluation.learning_journey_runner import resolve_credential_presence

    store = getattr(environment.app.state, "runtime_credential_store", None)
    settings = getattr(environment.app.state, "settings", None)
    presence = resolve_credential_presence(store, settings)
    return presence.to_dict()


def results_payload(results: list[SceneResult]) -> list[dict[str, Any]]:
    return [result.to_dict() for result in results]


def write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return path


def write_evidence(
    output_dir: Path, *, stamp: str, payload: dict[str, Any], markdown: str
) -> tuple[Path, Path]:
    mode = str(payload["run_lock"]["response_mode"])
    json_path = output_dir / f"loop-eval-{mode}-{stamp}.json"
    md_path = output_dir / f"loop-eval-{mode}-{stamp}.md"
    write_json(json_path, payload)
    write_json(output_dir / f"run-lock-{mode}-{stamp}.json", payload["run_lock"])
    md_path.write_text(markdown, encoding="utf-8")
    return json_path, md_path


def load_baseline_candidate(
    search_dir: Path,
) -> dict[str, Any] | None:
    """读取 01 运行器在本目录最新的候选旅程证据。"""

    files = sorted(search_dir.glob("baseline-real-*.json"))
    if not files:
        return None
    payload: Any = json.loads(files[-1].read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else None


def load_frozen_baseline(repo_root: Path) -> dict[str, Any] | None:
    path = (
        repo_root
        / ".scratch"
        / "profile-memory-redesign"
        / "validation"
        / "01-baseline"
        / "baseline-real-20261015T061452Z.json"
    )
    if not path.is_file():
        return None
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else None


def compare_baseline(
    frozen: dict[str, Any] | None, candidate: dict[str, Any] | None
) -> dict[str, Any]:
    """基线/候选逐旅程对照（同输入、同检查口径；模型相同）。"""

    if frozen is None or candidate is None:
        return {
            "available": False,
            "reason": "缺少 01 冻结基线或候选旅程证据（未运行 01 运行器）。",
        }
    frozen_by_id = {journey["journey_id"]: journey for journey in frozen.get("journeys", [])}
    rows: list[dict[str, Any]] = []
    for journey in candidate.get("journeys", []):
        journey_id = journey["journey_id"]
        baseline_journey = frozen_by_id.get(journey_id) or {}
        baseline_eval = baseline_journey.get("evaluation") or {}
        candidate_eval = journey.get("evaluation") or {}
        rows.append(
            {
                "journey_id": journey_id,
                "baseline_observed": baseline_eval.get("observed_status"),
                "candidate_observed": candidate_eval.get("observed_status"),
                "candidate_expected": journey.get("expected_baseline_status"),
                "candidate_match": candidate_eval.get("baseline_match"),
            }
        )
    return {
        "available": True,
        "frozen_generated_from": {
            "commit": (frozen.get("run_lock") or {}).get("git_commit"),
            "model": ((frozen.get("run_lock") or {}).get("model_config") or {}).get("model_id"),
            "corpus": (frozen.get("run_lock") or {}).get("corpus_version"),
        },
        "candidate": {
            "commit": (candidate.get("run_lock") or {}).get("git_commit"),
            "model": ((candidate.get("run_lock") or {}).get("model_config") or {}).get("model_id"),
            "corpus": (candidate.get("run_lock") or {}).get("corpus_version"),
        },
        "rows": rows,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lock = payload["run_lock"]
    summary = payload["summary"]
    lines: list[str] = []
    lines.append(f"# 画像闭环真实评测证据（{lock['response_mode']}）")
    lines.append("")
    lines.append(f"- 生成时间：{payload['generated_at']}")
    lines.append(f"- 评测版本：{lock['eval_version']}")
    lines.append(f"- 评测资产摘要：{lock['eval_assets_sha256']}")
    lines.append(f"- 代码提交：{lock['git_commit']}（dirty={lock['git_dirty']}）")
    model_config = lock.get("model_config") or {}
    lines.append(f"- 主模型：{model_config.get('model_id')}")
    lines.append(f"- 凭据：{lock['credentials']}")
    lines.append("")
    lines.append("## 场景矩阵")
    lines.append("")
    lines.append("| 场景 | 类别 | 状态 | 通过/检查 | 失败判定 |")
    lines.append("| --- | --- | --- | --- | --- |")
    for row in summary["matrix"]:
        failed = ", ".join(row["failed_checks"]) or "—"
        lines.append(
            f"| {row['scene_id']} | {row['kind']} | {row['status']} | "
            f"{row['passed']}/{row['checks']} | {failed} |"
        )
    lines.append("")
    lines.append("## 判定汇总")
    lines.append("")
    lines.append("| 口径 | 通过 | 失败 | 通过率 |")
    lines.append("| --- | --- | --- | --- |")
    for scope, bucket in summary["check_totals"].items():
        lines.append(f"| {scope} | {bucket['passed']} | {bucket['failed']} | {bucket['rate']} |")
    resources = payload["resources"]["resources"]
    lines.append("")
    lines.append("## 资源与延迟")
    lines.append("")
    lines.append(
        f"- 模型调用 {resources['model_calls']} 次；输入 {resources['input_tokens']} / "
        f"输出 {resources['output_tokens']} token（{resources['note']}）"
    )
    latency = payload["resources"]["latency"]
    lines.append(f"- 回答延迟样本：{latency['generation_duration_ms']}")
    lines.append(f"- 提取延迟样本：{latency['extraction_duration_ms']}")
    lines.append(
        f"- 提取任务状态：{payload['resources']['tasks_by_status']}；"
        f"观测队列峰值：{payload['resources']['queue_depth_max_observed']}"
    )
    baseline = payload.get("baseline_comparison") or {}
    if baseline.get("available"):
        lines.append("")
        lines.append("## 基线/候选对照（01 冻结基线 vs 本候选）")
        lines.append("")
        lines.append("| 旅程 | 01 基线实测 | 本候选实测 | 候选期望 | 一致 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for row in baseline["rows"]:
            lines.append(
                f"| {row['journey_id']} | {row['baseline_observed']} | "
                f"{row['candidate_observed']} | {row['candidate_expected']} | "
                f"{row['candidate_match']} |"
            )
    lines.append("")
    lines.append(payload["notice"])
    lines.append("")
    return "\n".join(lines)


__all__ = [
    "build_run_lock",
    "eval_assets_digest",
    "results_payload",
    "write_json",
    "write_evidence",
    "load_baseline_candidate",
    "load_frozen_baseline",
    "compare_baseline",
    "render_markdown",
]
