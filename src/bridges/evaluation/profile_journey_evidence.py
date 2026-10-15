"""工单 01：画像闭环基线的比较、运行锁与证据文件写入。

本模块是 ``scripts/run_issue01_profile_baseline.py`` 的证据层：按预先固定
的确定性检查项比较目标与实测、汇总实测成本与延迟、构建不可变运行锁，
并把证据写成 JSON/Markdown。运行驱动与采集见 ``profile_journey_runner.py``。
"""

from __future__ import annotations

import hashlib
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bridges.evaluation.learning_journey_runner import (
    CredentialPresence,
    write_json,
)
from bridges.evaluation.profile_journey_runner import (
    JOURNEY_TURN_TIMEOUT_SECONDS,
    RecordingProfileExtractionAdapter,
)
from bridges.evaluation.profile_journeys import (
    PROFILE_JOURNEY_CORPUS_VERSION,
    BaselineJourney,
    corpus_digest,
)


def _journey_texts(journey_evidence: dict[str, Any]) -> str:
    snapshot = journey_evidence["profile_snapshot"]
    parts: list[str] = []
    for record in snapshot["four_dimensions"]:
        parts.append(str(record.get("content") or ""))
    for item in snapshot["items"]:
        parts.append(str(item.get("text") or ""))
    for observation in snapshot["observations"]:
        parts.append(str(observation.get("normalized_value") or ""))
    for turn in journey_evidence["turns"]:
        for run in turn.get("extraction_runs") or []:
            parts.extend(
                str(value or "") for value in run.get("committed_record_ids") or []
            )
    return " ".join(parts)


def _contains_any(texts: str, needles: tuple[str, ...]) -> bool:
    return any(needle in texts for needle in needles)


def _extraction_statuses(journey_evidence: dict[str, Any]) -> tuple[str, ...]:
    statuses: list[str] = []
    for turn in journey_evidence["turns"]:
        for run in turn.get("extraction_runs") or []:
            statuses.append(str(run.get("status") or ""))
    return tuple(statuses)


def evaluate_journey(journey: BaselineJourney, evidence: dict[str, Any]) -> dict[str, Any]:
    """按固定检查项比较目标与实测；返回逐项结论与汇总状态。

    检查项是预先固定的确定性断言（见测量方法文档），不因实测结果调整。
    """

    texts = _journey_texts(evidence)
    statuses = _extraction_statuses(evidence)
    checks: list[dict[str, Any]] = []

    def add(check_id: str, ok: bool, detail: str) -> None:
        checks.append({"check_id": check_id, "passed": bool(ok), "detail": detail})

    if journey.journey_id == "t01-name":
        add("fact_captured", _contains_any(texts, ("小林",)), f"texts={texts!r}")
    elif journey.journey_id == "t01-pet":
        add("fact_captured", _contains_any(texts, ("团子",)), f"texts={texts!r}")
    elif journey.journey_id == "t01-habit":
        add("fact_captured", _contains_any(texts, ("爬山", "徒步")), f"texts={texts!r}")
    elif journey.journey_id == "t02-psychology":
        add(
            "interest_captured_not_blocked",
            "succeeded" in statuses and _contains_any(texts, ("心理学", "认知科学")),
            f"statuses={statuses} texts={texts!r}",
        )
    elif journey.journey_id == "t03-time-limit":
        add(
            "content_captured",
            _contains_any(texts, ("线性代数", "线代")),
            f"texts={texts!r}",
        )
        add(
            "time_constraint_captured",
            _contains_any(
                texts, ("半小时", "30 分钟", "30分钟", "一小时", "1 小时", "1小时")
            ),
            f"texts={texts!r}",
        )
    elif journey.journey_id == "t04-cross-turn":
        calls = [
            call
            for turn in evidence["turns"]
            for call in turn.get("recording_calls") or []
        ]
        probe = evidence.get("neighbor_reader_probe")
        if calls:
            source = f"calls={calls!r}"
            hint_ok = any(call.get("neighbor_hint_present") for call in calls)
            context_ok = any(
                call.get("project_id") and call.get("project_id") != "retry"
                for call in calls
            )
        else:
            # real 模式没有替身录制：以应用级探测（真实会话 ID + 真实
            # 邻居读取函数）的返回作为邻近上下文证据；空即缺失。
            source = f"probe={probe!r}"
            hint_ok = bool(probe)
            context_ok = bool(probe)
        add("neighbor_context_present", hint_ok, source)
        add("real_conversation_context_passed", context_ok, source)
        add(
            "preference_captured",
            _contains_any(texts, ("先给结论", "先给重点", "先讲重点")),
            f"texts={texts!r}",
        )
    elif journey.journey_id == "positive-academic-control":
        add("extraction_succeeded", "succeeded" in statuses, f"statuses={statuses}")
        # 工单 03 起原子条目是长期信息唯一权威：四维记录不再参与写入，
        # 该检查项随 03 落地移除（写入证据改由 atomic_item_written 承担）。
        add("atomic_item_written", bool(evidence["profile_snapshot"]["items"]), "")
        add(
            "fact_active_in_snapshot",
            any(
                item.get("status") == "active"
                and _contains_any(str(item.get("text") or ""), ("计算机", "大二"))
                for item in evidence["profile_snapshot"]["items"]
            ),
            "",
        )
    elif journey.journey_id == "recall-academic-control":
        add("extraction_succeeded", "succeeded" in statuses, f"statuses={statuses}")
        last_run = evidence["turns"][-1].get("generation_run") or {}
        slice_payload = last_run.get("adopted_profile_slice") or {}
        adopted_facts = [
            str(item.get("fact_text") or "")
            for item in slice_payload.get("adopted_items") or []
        ]
        excluded = [
            str(item.get("exclusion_reason") or "")
            for item in slice_payload.get("excluded_items") or []
        ]
        # 只认下一轮（最后一轮）采用切片里的该事实；首轮切片的包含不算。
        add(
            "adopted_in_next_turn",
            any(_contains_any(fact, ("计算机", "大二", "专业")) for fact in adopted_facts),
            f"adopted={adopted_facts!r} excluded={excluded!r}",
        )
    else:  # pragma: no cover - 旅程集合固定，防御性分支
        add("known_journey", False, f"未定义检查项：{journey.journey_id}")

    passed = sum(1 for check in checks if check["passed"])
    if passed == len(checks):
        observed = "pass"
    elif passed == 0:
        observed = "known_failing"
    else:
        observed = "partial"
    return {
        "checks": checks,
        "observed_status": observed,
        "baseline_match": observed == journey.baseline_status
        or (journey.baseline_status == "known_failing" and observed == "partial"),
    }


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


def _python_version() -> str:
    import sys

    return sys.version.split()[0]


def build_run_lock(
    repo_root: Path,
    *,
    app: Any,
    journey_ids: list[str],
    response_mode: str,
    credentials: CredentialPresence,
    stub_profile_extraction: bool,
    stub_chat: bool,
) -> dict[str, Any]:
    """冻结本次评测的运行锁（代码/语料/模型/合同/提示词/预算）。"""

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
    evaluation_files = [
        repo_root / "src" / "bridges" / "evaluation" / "profile_journey_types.py",
        repo_root / "src" / "bridges" / "evaluation" / "profile_journey_scenarios_baseline.py",
        repo_root / "src" / "bridges" / "evaluation" / "profile_journey_scenarios_planned.py",
        repo_root / "src" / "bridges" / "evaluation" / "profile_journeys.py",
        repo_root / "src" / "bridges" / "evaluation" / "profile_journey_runner.py",
        repo_root / "src" / "bridges" / "evaluation" / "profile_journey_evidence.py",
        repo_root / "scripts" / "run_issue01_profile_baseline.py",
    ]
    model_config = None
    provider = getattr(app.state, "run_model_config_provider", None)
    if provider is not None:
        model_config = provider.snapshot().model_dump(mode="json")
    return {
        "kind": "profile-baseline-run-lock",
        "corpus_version": PROFILE_JOURNEY_CORPUS_VERSION,
        "corpus_sha256": corpus_digest(),
        "journeys": journey_ids,
        "response_mode": response_mode,
        "injected_models_overridden": stub_profile_extraction or stub_chat,
        "stub_profile_extraction": stub_profile_extraction,
        "stub_chat": stub_chat,
        "git_commit": _git(repo_root, "rev-parse", "HEAD"),
        "git_dirty": bool(
            _git(repo_root, "status", "--porcelain", "--untracked-files=no")
        ),
        "pipeline_snapshot_sha256": _hash_files(pipeline_files),
        "evaluation_sha256": _hash_files(evaluation_files),
        "python_version": _python_version(),
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
        "credentials": credentials.to_dict(),
        "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def attach_recording_calls(
    journeys: list[dict[str, Any]], adapter: RecordingProfileExtractionAdapter | None
) -> None:
    """把替身适配器录到的调用载荷附到对应旅程（按 extraction_id 关联）。"""

    if adapter is None:
        for journey in journeys:
            for turn in journey.get("turns", []):
                turn.setdefault("recording_calls", [])
        return
    calls_by_run = {str(call.get("run_id")): call for call in adapter.calls}
    for journey in journeys:
        for turn in journey.get("turns", []):
            turn["recording_calls"] = [
                calls_by_run[str(run.get("extraction_id"))]
                for run in turn.get("extraction_runs") or []
                if str(run.get("extraction_id")) in calls_by_run
            ]
        journey["all_recording_calls"] = [
            call
            for call in adapter.calls
            if call.get("run_id")
            in {
                str(run.get("extraction_id"))
                for turn in journey.get("turns", [])
                for run in turn.get("extraction_runs") or []
            }
        ]


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _delta_ms(start: Any, end: Any) -> int | None:
    first, second = _parse_ts(start), _parse_ts(end)
    if first is None or second is None:
        return None
    return max(0, int((second - first).total_seconds() * 1000))


def journey_report_totals(journeys: list[dict[str, Any]]) -> dict[str, Any]:
    """汇总实测调用、token 与延迟样本；只报实测，不编造。"""

    model_calls = 0
    input_tokens = 0
    output_tokens = 0
    completion_ms: list[int] = []
    extraction_ms: list[int] = []
    for journey in journeys:
        for turn in journey.get("turns", []):
            model_calls += len(turn.get("model_calls") or [])
            for call in turn.get("model_calls") or []:
                usage = call.get("usage") or {}
                input_tokens += int(usage.get("prompt_tokens") or 0)
                output_tokens += int(usage.get("completion_tokens") or 0)
            run = turn.get("generation_run") or {}
            if isinstance(run.get("duration_ms"), int):
                completion_ms.append(run["duration_ms"])
            for extraction in turn.get("extraction_runs") or []:
                latency = _delta_ms(
                    extraction.get("created_at"), extraction.get("updated_at")
                )
                if latency is not None:
                    extraction_ms.append(latency)
    return {
        "model_calls": model_calls,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "generation_duration_ms_samples": completion_ms,
        "extraction_duration_ms_samples": extraction_ms,
        "note": "只报告本次实测；替身模式的 token 为 0 不代表真实成本。",
    }


def write_journey_evidence(
    output_dir: Path, *, stamp: str, payload: dict[str, Any], markdown: str
) -> tuple[Path, Path]:
    """写入证据 JSON 与 Markdown；JSON 经凭据形态粗筛。"""

    mode = str(payload["run_lock"]["response_mode"])
    json_path = output_dir / f"baseline-{mode}-{stamp}.json"
    md_path = output_dir / f"baseline-{mode}-{stamp}.md"
    write_json(json_path, payload)
    md_path.write_text(markdown, encoding="utf-8")
    return json_path, md_path


def write_run_lock(output_dir: Path, *, stamp: str, payload: dict[str, Any]) -> Path:
    mode = str(payload["run_lock"]["response_mode"])
    path = output_dir / f"run-lock-{mode}-{stamp}.json"
    write_json(path, payload["run_lock"])
    return path


__all__ = [
    "evaluate_journey",
    "build_run_lock",
    "attach_recording_calls",
    "journey_report_totals",
    "write_journey_evidence",
    "write_run_lock",
]
