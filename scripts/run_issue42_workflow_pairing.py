"""工单 42：旧 V2 与新编排的同任务真实模型配对评测。

对每个场景在两侧代码树各跑一份真实模型对话，保持相同任务数据、模型运行
配置与重复次数，分别报告质量检查点、成本（模型调用与 token）与延迟
（首字/整答 P50、P95），并给出与 09 预算初值的对照。

harness 的子进程分树执行方式沿用 40 工单已验证做法：父进程对每棵树用同一
脚本自重启（``--run-tree``），子进程内把目标树 ``src`` 插入 ``sys.path`` 后
才导入 ``bridges``，避免跨树代码混用；结果经 ``--result-json`` 落盘交换。

用法：

    python scripts/run_issue42_workflow_pairing.py --proxy http://127.0.0.1:7890

旧树默认取提交 ``7818c34`` 的 detached worktree；缺失时自动创建。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OLD_TREE = REPO_ROOT.parent / "42-baseline-7818c34"
DEFAULT_OUTPUT_DIR = REPO_ROOT / ".scratch" / "2" / "validation" / "42-workflow-evaluation"
BASELINE_COMMIT = "7818c34"
TERMINAL_STATUSES = {"done", "error", "stopped"}
PASSWORD = "Passw0rd123!"


@dataclass(frozen=True)
class TurnSpec:
    """一次用户发言；R07 可在发送后立即请求停止。"""

    content: str
    module_id: str | None = None
    stop_after_send: bool = False


@dataclass(frozen=True)
class CaseSpec:
    """一个配对场景：轮次与质量检查点。"""

    case_id: str
    scenario_id: str
    turns: tuple[TurnSpec, ...]
    checks: tuple[str, ...]
    budget_class: str


SCENARIOS: tuple[CaseSpec, ...] = (
    CaseSpec(
        case_id="A01.lightweight",
        scenario_id="A01",
        turns=(TurnSpec("你好，今天想随便聊两句，你最近怎么样？"),),
        checks=(
            "answer_nonempty",
            "single_chat_call",
            "no_external_model_capability",
            "no_task_created",
            "no_unnecessary_search",
        ),
        budget_class="lightweight",
    ),
    CaseSpec(
        case_id="A02.hint_override",
        scenario_id="A02",
        turns=(TurnSpec("从南区宿舍走到图书馆大概要多久？走路。", module_id="paper"),),
        checks=("answer_nonempty", "no_paper_results"),
        budget_class="normal",
    ),
    CaseSpec(
        case_id="A03.ambiguous",
        scenario_id="A03",
        turns=(TurnSpec("帮我找一下 transformer 相关的资料。"),),
        checks=("answer_nonempty", "asks_one_clarification", "no_paper_results"),
        budget_class="normal",
    ),
    CaseSpec(
        case_id="A11.hard_condition",
        scenario_id="A11",
        turns=(
            TurnSpec("帮我找几篇 Transformer 的论文，只要 2020 年以后的，不要联网搜索。"),
        ),
        checks=("no_paper_results", "hard_condition_blocked"),
        budget_class="normal",
    ),
    CaseSpec(
        case_id="R07.stop_continue",
        scenario_id="R07",
        turns=(
            TurnSpec("推荐几本 Python 入门教材。"),
            TurnSpec("继续推荐更多 Python 教程。", stop_after_send=True),
            TurnSpec("继续刚才的任务，接着推荐。"),
        ),
        checks=(
            "answer_nonempty",
            "stop_no_auto_continue",
            "continue_creates_new_run",
        ),
        budget_class="normal",
    ),
)


def corpus_sha256() -> str:
    """锁定配对任务数据内容。"""
    payload = json.dumps(
        [
            {
                "case_id": case.case_id,
                "scenario_id": case.scenario_id,
                "turns": [asdict(turn) for turn in case.turns],
                "checks": list(case.checks),
            }
            for case in SCENARIOS
        ],
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def percentile(values: list[int], fraction: float) -> int | None:
    """最近秩法分位数（与工作流性能摘要口径一致）。"""
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[index]


def projection_empty(value: Any) -> bool:
    """会话投影中某模块是否没有产出（None/空列表/空字典都算空）。"""
    if value is None:
        return True
    if isinstance(value, list | dict):
        return len(value) == 0
    return False


def evaluate_case_checks(case: dict[str, Any]) -> dict[str, bool | None]:
    """由一个用例的采集结果计算质量检查点（纯函数）。"""
    spec = next(item for item in SCENARIOS if item.case_id == case["case_id"])
    turns = case.get("turns", [])
    computed: dict[str, bool | None] = {}
    outstanding = [
        turn for turn in turns if str(turn.get("status") or "") != "stopped"
    ]
    computed["answer_nonempty"] = (
        all(str(turn.get("answer") or "").strip() for turn in outstanding)
        if outstanding
        else None
    )
    chat_calls = sum(
        int(turn.get("capability_calls", {}).get("qwen_text_chat", 0))
        for turn in turns
    )
    computed["single_chat_call"] = chat_calls == 1
    computed["no_external_model_capability"] = all(
        set(turn.get("capability_calls", {})) <= {"qwen_text_chat"} for turn in turns
    )
    tasks = case.get("tasks")
    computed["no_task_created"] = None if tasks is None else len(tasks) == 0
    computed["no_unnecessary_search"] = not any(
        bool(turn.get("search_planned")) for turn in turns
    )
    computed["no_paper_results"] = all(
        bool(turn.get("paper_search_empty", True)) for turn in turns
    )
    last_answer = str(turns[-1].get("answer") or "") if turns else ""
    computed["asks_one_clarification"] = "？" in last_answer or "?" in last_answer
    computed["hard_condition_blocked"] = any(
        turn.get("error_code") == "network_not_allowed"
        or "联网" in str(turn.get("error_message") or "")
        or "联网" in str(turn.get("answer") or "")
        for turn in turns
    )
    stopped_run_id = case.get("stopped_run_id")
    completed_runs = {
        str(turn.get("run_id")) for turn in turns[:2]
    } if case["case_id"] == "R07.stop_continue" else set()
    computed["stop_no_auto_continue"] = (
        stopped_run_id is not None
        and int(case.get("idle_new_runs", -1)) == 0
        and case.get("stopped_run_status") in TERMINAL_STATUSES
    )
    computed["continue_creates_new_run"] = (
        case.get("continued_run_id") is not None
        and str(case.get("continued_run_id")) not in completed_runs
    )
    return {check: computed.get(check) for check in spec.checks}


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
    for case in cases:
        for turn in case.get("turns", []):
            value = turn.get("answer_elapsed_ms")
            if isinstance(value, int) and value > 0:
                answers.append(value)
            first = turn.get("queue_to_first_token_ms")
            if isinstance(first, int) and first >= 0:
                first_tokens.append(first)
    return {
        "answer_p50_ms": percentile(answers, 0.5),
        "answer_p95_ms": percentile(answers, 0.95),
        "answer_samples": len(answers),
        "first_token_p50_ms": percentile(first_tokens, 0.5),
        "first_token_p95_ms": percentile(first_tokens, 0.95),
        "first_token_samples": len(first_tokens),
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
        problems.append(
            f"两侧生效模型不一致：{old.get('model_ids')} vs {new.get('model_ids')}"
        )
    if old.get("repeats") != new.get("repeats"):
        problems.append("两侧重复次数不一致。")

    case_entries: list[dict[str, Any]] = []
    for spec in SCENARIOS:
        old_cases = [c for c in old.get("cases", []) if c["case_id"] == spec.case_id]
        new_cases = [c for c in new.get("cases", []) if c["case_id"] == spec.case_id]
        if not old_cases or not new_cases:
            problems.append(f"{spec.case_id} 在某一侧没有执行结果。")
        old_errors = [c["error"] for c in old_cases if c.get("error")]
        new_errors = [c["error"] for c in new_cases if c.get("error")]
        if old_errors or new_errors:
            problems.append(
                f"{spec.case_id} 执行错误：旧侧 {len(old_errors)}，新侧 {len(new_errors)}。"
            )
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
    return {
        "kind": "workflow-pairing",
        "generated_at": datetime.now(UTC).isoformat(),
        "corpus_sha256": corpus_sha256(),
        "baseline_commit": BASELINE_COMMIT,
        "old": {
            "tree": old.get("tree"),
            "commit": old.get("commit"),
            "dirty": old.get("dirty"),
            "model_ids": old.get("model_ids"),
            "started_at": old.get("started_at"),
            "finished_at": old.get("finished_at"),
        },
        "new": {
            "tree": new.get("tree"),
            "commit": new.get("commit"),
            "dirty": new.get("dirty"),
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
        rate = (
            f"{quality['passed']}/{quality['applicable']}"
            if quality["applicable"]
            else "n/a"
        )
        lines.append(
            f"| {label} | {rate} | {cost['calls']} | {cost['prompt_tokens']} | "
            f"{cost['completion_tokens']} | "
            f"{latency['answer_p50_ms']}/{latency['answer_p95_ms']} | "
            f"{latency['first_token_p50_ms']}/{latency['first_token_p95_ms']} |"
        )
    if budgets:
        lines.extend(["", "## 与 09 预算初值对照（新树）", ""])
        lines.append("| 预算类别 | 总预算 ms | 预留 ms | 实测整答 P95 ms | 结论 |")
        lines.append("| --- | --- | --- | --- | --- |")
        latency_by_class = _latency_by_budget_class(report)
        for name, values in budgets.items():
            measured = latency_by_class.get(name)
            conclusion = _calibration_conclusion(values, measured)
            lines.append(
                f"| {name} | {values['total_budget_ms']} | "
                f"{values['verify_deliver_reserve_ms']} | {measured} | {conclusion} |"
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
        lines.append("- 两侧任务数据、模型与重复次数一致，无执行错误。")
    return "\n".join(lines) + "\n"


def _format_values(values: list[bool | None]) -> str:
    labels = {True: "✓", False: "✗", None: "n/a"}
    return " ".join(labels[value] for value in values) if values else "—"


def _latency_by_budget_class(report: dict[str, Any]) -> dict[str, int | None]:
    buckets: dict[str, list[int]] = {"lightweight": [], "normal": []}
    for case in report["cases"]:
        bucket = buckets.setdefault(case["budget_class"], [])
        latency = case["new"]["latency"]
        p95 = latency.get("answer_p95_ms")
        if p95 is not None:
            bucket.append(p95)
    return {
        name: (max(values) if values else None) for name, values in buckets.items()
    }


def _calibration_conclusion(budget: dict[str, int], measured: int | None) -> str:
    if measured is None:
        return "无样本"
    margin = budget["total_budget_ms"] - budget["verify_deliver_reserve_ms"] - measured
    if margin >= 0:
        return f"保留（余量 {margin} ms）"
    return f"超出 {abs(margin)} ms，需复核"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="工单 42 旧/新树真实模型配对。")
    parser.add_argument("--old-tree", type=Path, default=DEFAULT_OLD_TREE)
    parser.add_argument("--new-tree", type=Path, default=REPO_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=240.0)
    parser.add_argument("--proxy", default=None)
    parser.add_argument("--run-tree", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--result-json", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument(
        "--no-create-old-tree", action="store_true", help="旧树缺失时不自动创建 worktree。"
    )
    return parser.parse_args(argv)


def _git(tree: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(tree),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _ensure_old_tree(tree: Path, *, allow_create: bool) -> None:
    if (tree / "src" / "bridges").exists():
        return
    if not allow_create:
        raise RuntimeError(f"旧树不存在：{tree}")
    completed = subprocess.run(
        ["git", "worktree", "add", "--detach", str(tree), BASELINE_COMMIT],
        cwd=str(REPO_ROOT),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0 or not (tree / "src" / "bridges").exists():
        raise RuntimeError(f"创建旧树 worktree 失败：{completed.stderr.strip()}")


# ---------------------------------------------------------------------------
# 子进程：在单棵树内跑真实模型配对
# ---------------------------------------------------------------------------


def run_tree(
    tree: Path, result_path: Path, *, repeats: int, timeout: float, proxy: str | None
) -> int:
    """在目标树内执行全部场景；结果写入 ``result_path``。返回进程退出码。"""
    tree = tree.resolve()
    tmp_dir = Path(tempfile.mkdtemp(prefix="issue42-pairing-"))
    os.environ["BRIDGES_ENVIRONMENT"] = "test"
    os.environ["BRIDGES_DATABASE_URL"] = f"sqlite:///{tmp_dir / 'bridges.db'}"
    os.environ["BRIDGES_SECRET_KEY"] = f"issue42-pairing-{tmp_dir.name}"
    for key in list(os.environ):
        if key.startswith("BRIDGES_QWEN_API_KEY"):
            os.environ.pop(key, None)
    if proxy:
        os.environ["HTTP_PROXY"] = proxy
        os.environ["HTTPS_PROXY"] = proxy
    sys.path.insert(0, str(tree / "src"))
    os.chdir(tree)

    from fastapi.testclient import TestClient  # noqa: PLC0415 - 必须目标树导入

    from bridges.api.main import create_app  # noqa: PLC0415
    from bridges.credentials.store import OsCredentialStore  # noqa: PLC0415

    started_at = datetime.now(UTC).isoformat()
    data_dir = (
        Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "BridGes" / "data"
    )
    result: dict[str, Any] = {
        "tree": str(tree),
        "commit": _git(tree, "rev-parse", "HEAD"),
        "dirty": bool(_git(tree, "status", "--porcelain")),
        "corpus_sha256": corpus_sha256(),
        "repeats": repeats,
        "started_at": started_at,
        "cases": [],
        "inconclusive": False,
        "reason": None,
    }
    try:
        store = OsCredentialStore(data_dir=data_dir, namespace="runtime")
        app = create_app(runtime_credential_store=store)
    except Exception as exc:  # noqa: BLE001 - 报告凭据/启动不可用而不是崩溃
        result["inconclusive"] = True
        result["reason"] = f"app_start_failed:{type(exc).__name__}"
        _write_json(result_path, result)
        return 2

    model_ids: set[str] = set()
    notes: list[str] = []
    with TestClient(app) as client:
        for repeat in range(repeats):
            tag = int(time.strftime("%H%M%S")) * 100 + repeat
            account_id = _register(client, tag=tag)
            for spec in SCENARIOS:
                case = _run_case(
                    client,
                    app,
                    account_id,
                    spec,
                    repeat=repeat,
                    timeout=timeout,
                    model_ids=model_ids,
                    notes=notes,
                )
                case["checks"] = evaluate_case_checks(case)
                result["cases"].append(case)

    result["model_ids"] = sorted(model_ids)
    result["notes"] = notes
    result["finished_at"] = datetime.now(UTC).isoformat()
    _write_json(result_path, result)
    errors = [case for case in result["cases"] if case.get("error")]
    return 1 if errors else 0


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _register(client: Any, *, tag: int) -> str:
    username = f"issue42_eval_{tag}"
    response = client.post(
        "/auth/register",
        json={
            "username": username,
            "qq_email": f"{tag:012d}@qq.com",
            "password": PASSWORD,
        },
    )
    if response.status_code not in {200, 201}:
        raise RuntimeError(f"register_failed:{response.status_code}:{response.text[:200]}")
    login = client.post(
        "/auth/login",
        json={"identifier": username, "password": PASSWORD},
    )
    if login.status_code not in {200, 201}:
        raise RuntimeError(f"login_failed:{login.status_code}:{login.text[:200]}")
    return str(response.json()["account"]["id"])


def _conversation_runs(app: Any, account_id: str, conversation_id: str) -> list[Any]:
    return app.state.bridges_database.connection.execute(
        "SELECT run_id, status FROM generation_runs"
        " WHERE conversation_id = ? AND account_id = ?"
        " ORDER BY created_at, run_id",
        (conversation_id, account_id),
    ).fetchall()


def _run_lock_cursor(app: Any) -> int:
    row = app.state.bridges_database.connection.execute(
        "SELECT COALESCE(MAX(rowid), 0) FROM model_run_locks"
    ).fetchone()
    return int(row[0])


def _run_lock_rows(app: Any, cursor: int) -> list[Any]:
    return app.state.bridges_database.connection.execute(
        "SELECT capability_name, usage FROM model_run_locks"
        " WHERE rowid > ? ORDER BY rowid",
        (cursor,),
    ).fetchall()


def _cost_from_rows(rows: list[Any]) -> tuple[dict[str, int], dict[str, int]]:
    cost = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
    by_capability: dict[str, int] = {}
    for capability, usage_raw in rows:
        usage: dict[str, Any] = {}
        if usage_raw:
            try:
                parsed = json.loads(usage_raw)
                usage = parsed if isinstance(parsed, dict) else {}
            except (TypeError, ValueError):
                usage = {}
        calls = int(usage.get("calls") or 0)
        if calls == 0:
            calls = 1
        cost["calls"] += calls
        cost["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
        cost["completion_tokens"] += int(usage.get("completion_tokens") or 0)
        name = str(capability)
        by_capability[name] = by_capability.get(name, 0) + calls
    return cost, by_capability


def _start_turn(
    client: Any,
    app: Any,
    account_id: str,
    conversation_id: str,
    spec: TurnSpec,
    notes: list[str],
) -> tuple[str, str, float]:
    payload: dict[str, Any] = {"content": spec.content, "attachment_ids": []}
    if spec.module_id:
        payload["module_id"] = spec.module_id
    url = f"/chat/conversations/{conversation_id}/messages"
    response = client.post(url, json=payload)
    if response.status_code == 422 and spec.module_id:
        notes.append("module_hint_rejected_by_tree")
        payload.pop("module_id")
        response = client.post(url, json=payload)
    if response.status_code != 200:
        raise RuntimeError(f"send_failed:{response.status_code}:{response.text[:200]}")
    body = response.json()
    return (
        str(body["assistant_message"]["message_id"]),
        str(body["run_id"]),
        time.monotonic(),
    )


def _wait_terminal(
    client: Any,
    app: Any,
    account_id: str,
    conversation_id: str,
    message_id: str,
    *,
    timeout: float,
    started: float,
    drain_summary: bool,
) -> Any:
    repo = app.state.chat_service._repo  # noqa: SLF001 - 评测读正式持久化
    deadline = started + timeout
    record = None
    while time.monotonic() < deadline:
        app.state.generation_executor.run_tick()
        for item in repo.list_messages(account_id, conversation_id):
            if item.message_id == message_id:
                record = item
                break
        if record is not None and record.status.value in TERMINAL_STATUSES:
            break
        time.sleep(0.05)
    if drain_summary:
        summary_service = getattr(app.state, "chat_summary_service", None)
        if summary_service is not None:
            drain_deadline = min(deadline, time.monotonic() + 60.0)
            while time.monotonic() < drain_deadline:
                if "无待处理任务" in summary_service.run_tick():
                    break
                time.sleep(0.05)
    if record is None:
        raise RuntimeError("assistant_message_missing")
    return record


def _collect_turn(
    client: Any,
    app: Any,
    account_id: str,
    conversation_id: str,
    message_id: str,
    run_id: str,
    *,
    started: float,
) -> dict[str, Any]:
    repo = app.state.chat_service._repo  # noqa: SLF001
    projection = client.get(f"/chat/conversations/{conversation_id}").json()
    projected = next(
        message
        for message in projection["messages"]
        if message["message_id"] == message_id
    )
    record = next(
        item
        for item in repo.list_messages(account_id, conversation_id)
        if item.message_id == message_id
    )
    lock_rows = app.state.bridges_database.connection.execute(
        "SELECT capability_name, usage FROM model_run_locks WHERE run_id = ?",
        (run_id,),
    ).fetchall()
    _, by_capability = _cost_from_rows(lock_rows)
    events = repo.list_generation_events(account_id, run_id, 0)
    run = repo.get_generation_run(account_id, run_id)
    first_delta = next((event for event in events if event.kind == "delta"), None)
    started_event = next((event for event in events if event.kind == "started"), None)
    search_planned = bool(
        started_event
        and isinstance(started_event.payload, dict)
        and started_event.payload.get("web_search")
    )
    return {
        "run_id": run_id,
        "status": record.status.value,
        "error_code": record.error_code,
        "error_message": record.error_message,
        "search_planned": search_planned,
        "answer": record.content or "",
        "elapsed_s": round(time.monotonic() - started, 2),
        "answer_elapsed_ms": run.duration_ms,
        "queue_to_first_token_ms": (
            round((first_delta.created_at - run.created_at).total_seconds() * 1000)
            if first_delta
            else None
        ),
        "capability_calls": by_capability,
        "paper_search_empty": projection_empty(projected.get("paper_search")),
    }


def _list_tasks(client: Any, conversation_id: str) -> list[Any] | None:
    response = client.get(f"/tasks/conversations/{conversation_id}")
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        return None
    payload = response.json()
    return payload if isinstance(payload, list) else None


def _run_case(
    client: Any,
    app: Any,
    account_id: str,
    spec: CaseSpec,
    *,
    repeat: int,
    timeout: float,
    model_ids: set[str],
    notes: list[str],
) -> dict[str, Any]:
    case: dict[str, Any] = {
        "case_id": spec.case_id,
        "scenario_id": spec.scenario_id,
        "repeat": repeat,
        "error": None,
        "turns": [],
        "tasks": None,
        "stopped_run_id": None,
        "stopped_run_status": None,
        "continued_run_id": None,
        "idle_new_runs": 0,
        "cost": {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0},
    }
    cursor = _run_lock_cursor(app)
    try:
        conversation = app.state.chat_service.create_conversation(account_id)
        conversation_id = conversation.conversation_id
        for index, turn in enumerate(spec.turns):
            message_id, run_id, started = _start_turn(
                client, app, account_id, conversation_id, turn, notes
            )
            if turn.stop_after_send:
                runs_before = len(_conversation_runs(app, account_id, conversation_id))
                stop_response = client.post(
                    f"/chat/conversations/{conversation_id}/messages/{message_id}/stop"
                )
                if stop_response.status_code != 200:
                    notes.append(f"stop_status_{stop_response.status_code}")
                record = _wait_terminal(
                    client,
                    app,
                    account_id,
                    conversation_id,
                    message_id,
                    timeout=timeout,
                    started=started,
                    drain_summary=False,
                )
                for _ in range(10):
                    app.state.generation_executor.run_tick()
                    time.sleep(0.05)
                runs_after = _conversation_runs(app, account_id, conversation_id)
                case["stopped_run_id"] = run_id
                case["stopped_run_status"] = record.status.value
                case["idle_new_runs"] = max(0, len(runs_after) - runs_before)
            else:
                record = _wait_terminal(
                    client,
                    app,
                    account_id,
                    conversation_id,
                    message_id,
                    timeout=timeout,
                    started=started,
                    drain_summary=True,
                )
            turn_result = _collect_turn(
                client,
                app,
                account_id,
                conversation_id,
                message_id,
                run_id,
                started=started,
            )
            if index == len(spec.turns) - 1 and spec.case_id == "R07.stop_continue":
                case["continued_run_id"] = run_id
            case["turns"].append(turn_result)
            if turn_result.get("status") not in TERMINAL_STATUSES:
                raise RuntimeError(f"turn_not_terminal:{turn_result.get('status')}")
            model_id = getattr(record, "model_id", None)
            if isinstance(model_id, str) and model_id:
                model_ids.add(model_id)
            lock_models = app.state.bridges_database.connection.execute(
                "SELECT DISTINCT actual_model_id FROM model_run_locks"
                " WHERE run_id = ? AND actual_model_id IS NOT NULL",
                (run_id,),
            ).fetchall()
            for (actual_model_id,) in lock_models:
                if isinstance(actual_model_id, str) and actual_model_id:
                    model_ids.add(actual_model_id)
        case["tasks"] = _list_tasks(client, conversation_id)
    except Exception as exc:  # noqa: BLE001 - 单用例失败不拖垮整树
        case["error"] = f"{type(exc).__name__}:{exc}"
    rows = _run_lock_rows(app, cursor)
    cost, _ = _cost_from_rows(rows)
    case["cost"] = cost
    return case


# ---------------------------------------------------------------------------
# 父进程：调度两侧、生成配对报告
# ---------------------------------------------------------------------------


def _run_child(
    tree: Path,
    result_path: Path,
    *,
    repeats: int,
    timeout: float,
    proxy: str | None,
    script: Path,
) -> int:
    command = [
        sys.executable,
        str(script),
        "--run-tree",
        str(tree),
        "--result-json",
        str(result_path),
        "--repeats",
        str(repeats),
        "--timeout",
        str(timeout),
    ]
    if proxy:
        command.extend(["--proxy", proxy])
    completed = subprocess.run(command, cwd=str(tree), check=False)
    return completed.returncode


def _budget_snapshot() -> dict[str, Any]:
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from bridges.chat.run_budget_ledger import (  # noqa: PLC0415
        RUN_BUDGET_CONTRACT_VERSION,
        RUN_BUDGET_INITIALS,
    )

    return {
        "contract_version": RUN_BUDGET_CONTRACT_VERSION,
        "classes": {
            budget_class.value: {
                "total_budget_ms": initials.total_budget_ms,
                "verify_deliver_reserve_ms": initials.verify_deliver_reserve_ms,
                "model_call_limit": initials.model_call_limit,
                "external_parallel_max": initials.external_parallel_max,
                "candidate_screen_max": initials.candidate_screen_max,
                "deep_read_max": initials.deep_read_max,
                "adjustment_rounds_max": initials.adjustment_rounds_max,
                "transient_retry_max": initials.transient_retry_max,
            }
            for budget_class, initials in RUN_BUDGET_INITIALS.items()
        },
    }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.run_tree is not None:
        if args.result_json is None:
            print("--run-tree 需要 --result-json", file=sys.stderr)
            return 2
        return run_tree(
            args.run_tree,
            args.result_json,
            repeats=args.repeats,
            timeout=args.timeout,
            proxy=args.proxy,
        )

    old_tree = args.old_tree if args.old_tree.is_absolute() else REPO_ROOT / args.old_tree
    new_tree = args.new_tree if args.new_tree.is_absolute() else REPO_ROOT / args.new_tree
    output_dir = (
        args.output_dir if args.output_dir.is_absolute() else REPO_ROOT / args.output_dir
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        _ensure_old_tree(old_tree, allow_create=not args.no_create_old_tree)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    script = Path(__file__).resolve()
    old_result_path = output_dir / f"workflow-pairing-old-{stamp}.json"
    new_result_path = output_dir / f"workflow-pairing-new-{stamp}.json"
    old_code = _run_child(
        old_tree,
        old_result_path,
        repeats=args.repeats,
        timeout=args.timeout,
        proxy=args.proxy,
        script=script,
    )
    new_code = _run_child(
        new_tree,
        new_result_path,
        repeats=args.repeats,
        timeout=args.timeout,
        proxy=args.proxy,
        script=script,
    )
    if not old_result_path.exists() or not new_result_path.exists():
        print("一侧未产出结果文件。", file=sys.stderr)
        return 2
    old_result = json.loads(old_result_path.read_text(encoding="utf-8"))
    new_result = json.loads(new_result_path.read_text(encoding="utf-8"))
    if old_result.get("inconclusive") or new_result.get("inconclusive"):
        print(
            f"存在不可判定的一侧：old={old_result.get('reason')} "
            f"new={new_result.get('reason')}",
            file=sys.stderr,
        )
        return 2

    budgets_payload = _budget_snapshot()
    report = summarize_pairing(old_result, new_result)
    report["budgets"] = budgets_payload
    report_path = output_dir / f"workflow-pairing-{stamp}.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    markdown_path = output_dir / f"workflow-pairing-{stamp}.md"
    markdown_path.write_text(
        render_markdown(report, budgets_payload["classes"]), encoding="utf-8"
    )

    print(
        f"配对报告：{report_path}\n旧侧退出码 {old_code}，新侧退出码 {new_code}"
    )
    for problem in report["problems"]:
        print(f"  [问题] {problem}")
    return 1 if report["problems"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
