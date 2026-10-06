"""工单 42：配对质量、成本、延迟与预算证据汇总。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from scripts.issue42_pairing_budget import (
    _budget_problems,
    _budget_summary,
    _p50p95,
    _status_text,
    percentile,
)
from scripts.issue42_pairing_cases import SCENARIOS, corpus_sha256

BASELINE_COMMIT = "7818c34"


def _quality_summary(cases: list[dict[str, Any]]) -> dict[str, Any]:
    per_check: dict[str, dict[str, int]] = {}
    for case in cases:
        for check, value in case.get("checks", {}).items():
            bucket = per_check.setdefault(check, {"passed": 0, "applicable": 0})
            if value is None:
                continue
            bucket["applicable"] += 1
            if value:
                bucket["passed"] += 1
    applicable = sum(item["applicable"] for item in per_check.values())
    passed = sum(item["passed"] for item in per_check.values())
    return {
        "checks": per_check,
        "passed": passed,
        "applicable": applicable,
        "pass_rate": round(passed / applicable, 4) if applicable else None,
    }


def _cost_summary(cases: list[dict[str, Any]]) -> dict[str, int]:
    totals = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
    for case in cases:
        cost = case.get("cost", {})
        for key in totals:
            totals[key] += int(cost.get(key, 0))
    return totals


def _latency_summary(cases: list[dict[str, Any]]) -> dict[str, int | None]:
    answers: list[int] = []
    first_tokens: list[int] = []
    failures = 0
    progress: list[int] = []
    usable: list[int] = []
    complete: list[int] = []
    for case in cases:
        for turn in case.get("turns", []):
            if turn.get("status") != "done":
                failures += 1
                continue
            value = turn.get("answer_elapsed_ms")
            if isinstance(value, int) and value > 0:
                answers.append(value)
            first = turn.get("queue_to_first_token_ms")
            if isinstance(first, int) and first >= 0:
                first_tokens.append(first)
            first_progress = turn.get("queue_to_first_progress_ms")
            if isinstance(first_progress, int) and first_progress >= 0:
                progress.append(first_progress)
            for field, bucket in (
                ("queue_to_first_usable_ms", usable),
                ("queue_to_complete_ms", complete),
            ):
                value = turn.get(field)
                if isinstance(value, int) and value >= 0:
                    bucket.append(value)
    return {
        "answer_p50_ms": percentile(answers, 0.5),
        "answer_p95_ms": percentile(answers, 0.95),
        "answer_samples": len(answers),
        "first_token_p50_ms": percentile(first_tokens, 0.5),
        "first_token_p95_ms": percentile(first_tokens, 0.95),
        "first_token_samples": len(first_tokens),
        "failed_or_stopped_turns": failures,
        "first_progress_p50_ms": percentile(progress, 0.5),
        "first_progress_p95_ms": percentile(progress, 0.95),
        "first_progress_samples": len(progress),
        "first_usable_p50_ms": percentile(usable, 0.5),
        "first_usable_p95_ms": percentile(usable, 0.95),
        "first_usable_samples": len(usable),
        "complete_p50_ms": percentile(complete, 0.5),
        "complete_p95_ms": percentile(complete, 0.95),
        "complete_samples": len(complete),
    }


def summarize_pairing(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """对比两侧结果，生成质量/成本/延迟与等价比对报告（纯函数）。"""
    problems: list[str] = []
    if old.get("commit") is None or new.get("commit") is None:
        problems.append("存在未取得代码提交号的一侧。")
    if old.get("corpus_sha256") != new.get("corpus_sha256"):
        problems.append("两侧配对任务数据摘要不一致。")
    if old.get("corpus_sha256") != corpus_sha256():
        problems.append("运行时的任务数据摘要与当前脚本定义不一致。")
    if sorted(old.get("model_ids", [])) != sorted(new.get("model_ids", [])):
        problems.append(f"两侧生效模型不一致：{old.get('model_ids')} vs {new.get('model_ids')}")
    if old.get("repeats") != new.get("repeats"):
        problems.append("两侧重复次数不一致。")

    case_entries: list[dict[str, Any]] = []
    for spec in SCENARIOS:
        old_cases = [c for c in old.get("cases", []) if c["case_id"] == spec.case_id]
        new_cases = [c for c in new.get("cases", []) if c["case_id"] == spec.case_id]
        for label, samples, tree in (("旧侧", old_cases, old), ("新侧", new_cases, new)):
            repeats = tree.get("repeats")
            if not isinstance(repeats, int) or repeats < 1 or len(samples) != repeats:
                problems.append(f"{spec.case_id} {label}执行次数不符合锁定重复次数。")
            if len({sample.get("repeat") for sample in samples}) != len(samples):
                problems.append(f"{spec.case_id} {label}重复序号重复。")
        old_errors = [c["error"] for c in old_cases if c.get("error")]
        new_errors = [c["error"] for c in new_cases if c.get("error")]
        if old_errors or new_errors:
            problems.append(
                f"{spec.case_id} 执行错误：旧侧 {len(old_errors)}，新侧 {len(new_errors)}。"
            )
        for sample in new_cases:
            for check in spec.checks:
                if sample.get("checks", {}).get(check) is not True:
                    problems.append(
                        f"{spec.case_id} 新侧第 {sample.get('repeat')} 次未通过 {check}。"
                    )
            for turn in sample.get("turns", []):
                expected_block = (
                    spec.scenario_id == "A11"
                    and turn.get("error_code") == "network_not_allowed"
                    and sample.get("checks", {}).get("hard_condition_blocked") is True
                )
                if turn.get("status") == "error" and not expected_block:
                    problems.append(f"{spec.case_id} 新侧应用错误：{turn.get('error_code')}。")
        case_entries.append(
            {
                "case_id": spec.case_id,
                "scenario_id": spec.scenario_id,
                "budget_class": spec.budget_class,
                "turns": len(spec.turns),
                "old": {
                    "quality": _quality_summary(old_cases),
                    "cost": _cost_summary(old_cases),
                    "latency": _latency_summary(old_cases),
                },
                "new": {
                    "quality": _quality_summary(new_cases),
                    "cost": _cost_summary(new_cases),
                    "latency": _latency_summary(new_cases),
                },
                "check_results": {
                    check: {
                        "old": _check_values(old_cases, check),
                        "new": _check_values(new_cases, check),
                    }
                    for check in spec.checks
                },
            }
        )
    old_all = old.get("cases", [])
    new_all = new.get("cases", [])
    new_budget = _budget_summary(new_all)
    problems.extend(_budget_problems(new_budget))
    return {
        "kind": "workflow-pairing",
        "generated_at": datetime.now(UTC).isoformat(),
        "corpus_sha256": corpus_sha256(),
        "baseline_commit": BASELINE_COMMIT,
        "old": {
            "tree": old.get("tree"),
            "commit": old.get("commit"),
            "dirty": old.get("dirty"),
            "source_lock": old.get("source_lock"),
            "model_ids": old.get("model_ids"),
            "started_at": old.get("started_at"),
            "finished_at": old.get("finished_at"),
        },
        "new": {
            "tree": new.get("tree"),
            "commit": new.get("commit"),
            "dirty": new.get("dirty"),
            "source_lock": new.get("source_lock"),
            "model_ids": new.get("model_ids"),
            "started_at": new.get("started_at"),
            "finished_at": new.get("finished_at"),
        },
        "cases": case_entries,
        "old_totals": {
            "quality": _quality_summary(old_all),
            "cost": _cost_summary(old_all),
            "latency": _latency_summary(old_all),
            "error_codes": _error_codes(old_all),
        },
        "new_totals": {
            "quality": _quality_summary(new_all),
            "cost": _cost_summary(new_all),
            "latency": _latency_summary(new_all),
            "error_codes": _error_codes(new_all),
        },
        "new_budget": new_budget,
        "problems": problems,
    }


def _error_codes(cases: list[dict[str, Any]]) -> dict[str, int]:
    codes: dict[str, int] = {}
    for case in cases:
        for turn in case.get("turns", []):
            code = turn.get("error_code")
            if code:
                codes[str(code)] = codes.get(str(code), 0) + 1
    return codes


def _check_values(cases: list[dict[str, Any]], check: str) -> list[bool | None]:
    return [case.get("checks", {}).get(check) for case in cases]


def render_markdown(report: dict[str, Any], budgets: dict[str, Any] | None = None) -> str:
    """渲染配对报告；``budgets`` 为 09 预算初值（可选）。"""
    lines = [
        "# 工单 42 旧/新树真实模型配对报告",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 任务数据摘要：`{report['corpus_sha256']}`",
        f"- 旧树：`{report['old']['tree']}` @ `{report['old']['commit']}`",
        f"- 新树：`{report['new']['tree']}` @ `{report['new']['commit']}`",
        f"- 生效模型：{report['new']['model_ids']}",
        "",
        "## 质量检查点（同任务数据，两侧同一模型配置）",
        "",
        "| 场景 | 检查点 | 旧树通过 | 新树通过 |",
        "| --- | --- | --- | --- |",
    ]
    for case in report["cases"]:
        for check, values in case["check_results"].items():
            old_text = _format_values(values["old"])
            new_text = _format_values(values["new"])
            lines.append(f"| {case['scenario_id']} | {check} | {old_text} | {new_text} |")
    lines.extend(
        [
            "",
            "## 汇总（质量 / 成本 / 延迟）",
            "",
            "| 侧 | 检查点通过率 | 模型调用 | prompt tokens | completion tokens "
            "| 整答 P50/P95 (ms) | 首字 P50/P95 (ms) |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for label, totals in (("旧树", report["old_totals"]), ("新树", report["new_totals"])):
        quality = totals["quality"]
        cost = totals["cost"]
        latency = totals["latency"]
        rate = f"{quality['passed']}/{quality['applicable']}" if quality["applicable"] else "n/a"
        lines.append(
            f"| {label} | {rate} | {cost['calls']} | {cost['prompt_tokens']} | "
            f"{cost['completion_tokens']} | "
            f"{latency['answer_p50_ms']}/{latency['answer_p95_ms']} | "
            f"{latency['first_token_p50_ms']}/{latency['first_token_p95_ms']} |"
        )
    if budgets:
        budget_summary = report.get("new_budget") or {}
        lines.extend(["", "## 新树按预算类别的真实延迟基线（任务分组）", ""])
        if not budget_summary:
            lines.append("- 无预算账本样本；本类别不宣称延迟或消耗已校准。")
        else:
            lines.extend(
                [
                    "| 预算类别 | 样本 | 首真实进度 P50/P95 (ms) | 首可用结果 P50/P95 (ms) "
                    "| 完整结果 P50/P95 (ms) | 总预算 ms | 完整 P95 余量 ms | 账本终态 |",
                    "| --- | --- | --- | --- | --- | --- | --- | --- |",
                ]
            )
            for name, group in budget_summary.items():
                limit = budgets.get(name) or {}
                total = group["limits"].get(
                    "total_budget_ms", limit.get("total_budget_ms")
                )
                complete = group["latency_p95"].get("complete")
                margin = (
                    total - complete
                    if isinstance(total, int) and isinstance(complete, int)
                    else None
                )
                lines.append(
                    f"| {name} | {group['samples']} | {_p50p95(group, 'first_progress')} | "
                    f"{_p50p95(group, 'first_usable')} | {_p50p95(group, 'complete')} | "
                    f"{total if isinstance(total, int) else '—'} | "
                    f"{margin if margin is not None else '—'} | {_status_text(group)} |"
                )
        lines.extend(["", "## 预算初值与实测消耗对照（新树）", ""])
        lines.extend(
            [
                "| 预算类别 | 模型调用 max/初值 | 外部调用累计 max/并发峰值 (初值) "
                "| 传输重试 max/初值 | 补证轮 max/初值 | 候选初筛 max/初值 | 深读 max/初值 "
                "| prompt/output tokens max |",
                "| --- | --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for name, group in budget_summary.items():
            observed = group["observed_max"]
            limits = {**(budgets.get(name) or {}), **group["limits"]}
            lines.append(
                f"| {name} | {observed.get('model_calls_used', 0)}/"
                f"{limits.get('model_call_limit', '—')} | "
                f"{observed.get('external_calls_used', 0)}/"
                f"{observed.get('external_peak', 0)} "
                f"({limits.get('external_parallel_max', '—')}) | "
                f"{observed.get('transient_retries_used', 0)}/"
                f"{limits.get('transient_retry_max', '—')} | "
                f"{observed.get('adjustment_rounds_used', 0)}/"
                f"{limits.get('adjustment_rounds_max', '—')} | "
                f"{group['paper'].get('candidate_screens', 0)}/"
                f"{limits.get('candidate_screen_max', '—')} | "
                f"{group['paper'].get('deep_reads', 0)}/"
                f"{limits.get('deep_read_max', '—')} | "
                f"{observed.get('input_tokens_used', 0)}/{observed.get('output_tokens_used', 0)} |"
            )
        lines.extend(
            [
                "",
                "## 语义与延迟阈值基线",
                "",
                "- 延迟阈值以本表各类真实 P95 为基线；完整结果 P95 超出类别总预算即列入问题。",
                "- 语义阈值由生产量表全项检查定义；真实模型基线与逐项证据见 semantics-final 报告，"
                "本报告不重复宣称。",
                "- 无样本的类别与限额保持初值并明确标注，不宣称已校准；调整初值必须同时更新"
                "配对证据与锁定测试。",
                "- 并发峰值由账本流水重放；复合运行中姊妹分支可能占满 external_parallel_max，"
                "被拒分支按合同如实降级（不重试、不越限），本表只记录峰值，不承诺并发饱和下的成功率。",
            ]
        )
    lines.extend(["", "## 执行错误码", ""])
    lines.append("| 侧 | 错误码计数 |")
    lines.append("| --- | --- |")
    for label, totals in (("旧树", report["old_totals"]), ("新树", report["new_totals"])):
        codes = totals.get("error_codes") or {}
        text = "、".join(f"{code}×{count}" for code, count in sorted(codes.items()))
        lines.append(f"| {label} | {text or '无'} |")
    lines.extend(["", "## 配对等价与问题", ""])
    if report["problems"]:
        lines.extend(f"- {problem}" for problem in report["problems"])
    else:
        lines.append("- 两侧任务数据、模型与重复次数一致，新侧所测行为通过。")
    return "\n".join(lines) + "\n"


def _format_values(values: list[bool | None]) -> str:
    labels = {True: "✓", False: "✗", None: "n/a"}
    return " ".join(labels[value] for value in values) if values else "—"
