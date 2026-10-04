"""工单 40 上下文连续性与成本评测入口：同模型同预算新旧配对 + 量表 + 成本校准。

用法（conda agent 环境）::

    # 新旧配对（默认：本工作树 与 .worktrees/40-baseline-7818c34）
    python scripts/run_issue40_context_continuity_evaluation.py --repeats 2

    # 只跑一侧（内部子进程模式）
    python scripts/run_issue40_context_continuity_evaluation.py \
        --run-tree <tree> --result-json <file> --repeats 2

报告写入 ``.scratch/2/validation/40-context-continuity/``：每侧原始 JSON +
配对 Markdown/JSON。真实调用有界（固定原创多轮集 6 个场景 × repeats）；
密钥绝不写入报告或日志；缺凭据/适配器时如实记录 ``inconclusive`` 并以非零退出。

固定原创多轮集（真实模型执行部分）覆盖：

- ``tail-constraint``：末尾条件跨轮保持（constraint_retention）；
- ``mid-correction``：中途纠正覆盖旧值（correction_override）；
- ``task-roundtrip``：无关话题往返后回到最新有效条件（两个维度）；
- ``ordinal-list``：单一列表「第二个」唯一解析（object_resolution/reference_correctness）；
- ``missing-reference``：缺来源指代如实澄清不臆造
  （honest_clarification_gap/reference_correctness）；
- ``cross-account-isolation``：跨账户历史互不可见（isolation）；
- ``long-history-summary``：预置长聊后由真实摘要/回退恢复末尾条件
  （constraint_retention，记录摘要额外调用与 token）。

确定性机制覆盖（照片、附件页码/末尾段、多列表投影歧义、摘要失败、来源删除、
任务材料主题续接）由 ``tests/chat/test_improvement40_context_boundaries.py`` 与
``tests/chat/test_improvement40_continuity_corpus.py`` 承担；本脚本只做真实语义
配对与成本/校准统计。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OLD_TREE = REPO_ROOT.parent / "40-baseline-7818c34"
DEFAULT_OUTPUT_DIR = REPO_ROOT / ".scratch" / "2" / "validation" / "40-context-continuity"

DIMENSIONS = (
    "constraint_retention",
    "correction_override",
    "object_resolution",
    "reference_correctness",
    "honest_clarification_gap",
    "isolation",
)


@dataclass(frozen=True)
class Turn:
    content: str
    must_include: tuple[str, ...] = ()
    must_include_any: tuple[tuple[str, ...], ...] = ()
    must_exclude: tuple[str, ...] = ()
    must_ask: bool = False


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    dimensions: tuple[str, ...]
    turns: tuple[Turn, ...]
    #: 在指定轮次（1 起）之前切换到全新账户/会话，用于跨账户隔离场景。
    fresh_account_before_turn: int | None = None
    #: 发送前直接落库的历史对（预置长聊，不消耗模型调用）。
    seed_pairs: int = 0
    #: 预置历史首条的约束正文，用于真实长聊条件恢复。
    seed_constraint: str | None = None
    #: 本场景临时激活的（窗口, 最大输入）预算，用后还原。
    activate_window: tuple[int, int] | None = None


FILLER = "这里补充一些背景信息，仅用于占用上下文长度。" * 20

SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        "tail-constraint",
        ("constraint_retention",),
        (
            Turn(
                "我在准备一个社团活动方案。"
                + "内容说明：" * 40
                + FILLER
                + "最后补充一条硬性条件：总预算不得超过两千五百元。"
            ),
            Turn("先继续把活动流程大致说一遍。"),
            Turn(
                "等一下，我们之前说过的总预算上限是多少？只回答预算数字。",
                must_include_any=(("两千五百", "2500"),),
                must_exclude=("三千", "五千", "5000"),
            ),
        ),
    ),
    Scenario(
        "mid-correction",
        ("correction_override",),
        (
            Turn("帮我记一条装修预算：先按五千元准备。"),
            Turn("更正一下：预算改成三千元，最终按这个数执行。"),
            Turn(
                "只回答当前这条装修预算的数字。",
                must_include_any=(("三千", "3000"),),
                must_exclude=("五千", "5000"),
            ),
        ),
    ),
    Scenario(
        "task-roundtrip",
        ("constraint_retention", "correction_override"),
        (
            Turn("帮我规划周末徒步，参加人数四人。"),
            Turn("先聊点别的：推荐一首适合散步时听的轻音乐。"),
            Turn("回到徒步规划：参加人数改成六人，需要安排两辆车。"),
            Turn(
                "当前的人数与车辆数分别是多少？用『人数X、车辆Y』格式回答。",
                must_include_any=(("六", "6"), ("两辆", "2辆", "二辆", "车辆2")),
                must_exclude=("四人", "4人"),
            ),
        ),
    ),
    Scenario(
        "ordinal-list",
        ("object_resolution", "reference_correctness"),
        (
            Turn("请把这三个方向按编号列出：1. 数据分析；2. 网页开发；3. 自动化脚本。"
                 "每个方向只写一行标题，不要展开。"),
            Turn("第二个方向通常用来做什么项目？",
                 must_include_any=(("网页", "网站", "Web", "web", "前端"),)),
        ),
    ),
    Scenario(
        "missing-reference",
        ("honest_clarification_gap", "reference_correctness"),
        (
            Turn("第二个方案的具体步骤是什么？", must_ask=True),
        ),
    ),
    Scenario(
        "cross-account-isolation",
        ("isolation",),
        (
            Turn("记住一个口令：蓝鲸七号。收到后回复『已记住』即可。"),
            Turn("刚才我说过的口令是什么？", must_exclude=("蓝鲸", "七号")),
        ),
        fresh_account_before_turn=2,
    ),
    Scenario(
        "long-history-summary",
        ("constraint_retention",),
        (
            Turn(
                "只回答：我之前说的社团活动总预算上限是多少？",
                must_include_any=(("三千", "3000"),),
                must_exclude=("两千五百", "2500"),
            ),
        ),
        seed_pairs=40,
        seed_constraint="先记一条：社团活动总预算上限是三千元，之后都按这个数准备。",
        activate_window=(3000, 3000),
    ),
)

_SECRET_PATTERNS = (
    r"password",
    r"api[_-]?key",
    r"secret",
    r"authorization",
    r"sk-[A-Za-z0-9]{16,}",
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _local_app_data() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    return (Path(base) if base else Path.home() / "AppData/Local") / "BridGes" / "data"


def _git_commit(tree: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=tree,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return completed.stdout.strip() or None


def _check_turn(answer: str, turn: Turn) -> dict[str, Any]:
    text = answer or ""
    lowered = text.lower()
    checks: dict[str, Any] = {
        "must_include": all(token.lower() in lowered for token in turn.must_include),
        "must_include_any": all(
            any(token.lower() in lowered for token in group)
            for group in turn.must_include_any
        ),
        "must_exclude": all(
            token.lower() not in lowered for token in turn.must_exclude
        ),
        "must_ask": (not turn.must_ask) or ("？" in text or "?" in text),
    }
    checks["passed"] = all(
        value for key, value in checks.items() if key != "passed"
    )
    return checks


def _scenario_by_id(scenario_id: str) -> Scenario:
    for scenario in SCENARIOS:
        if scenario.scenario_id == scenario_id:
            return scenario
    raise KeyError(scenario_id)


def _register(client: Any, *, tag: str) -> str:
    response = client.post(
        "/auth/register",
        json={
            "username": f"issue40_eval_{tag}",
            "qq_email": f"{int(tag):012d}@qq.com",
            "password": "Passw0rd123!",
        },
    )
    if response.status_code != 201:
        raise RuntimeError(f"register_failed:{response.status_code}")
    return str(response.json()["account"]["id"])


def _login(client: Any, *, tag: str) -> None:
    """恢复客户端会话到本轮账户（跨账户用例会顶掉会话 Cookie）。"""
    response = client.post(
        "/auth/login",
        json={
            "identifier": f"issue40_eval_{tag}",
            "password": "Passw0rd123!",
        },
    )
    if response.status_code != 200:
        raise RuntimeError(f"login_failed:{response.status_code}:{response.text[:200]}")


def _send_turn(
    client: Any,
    app: Any,
    account_id: str,
    conversation_id: str,
    content: str,
    *,
    timeout: float,
) -> dict[str, Any]:
    started = time.monotonic()
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": content},
    )
    if response.status_code != 200:
        raise RuntimeError(f"send_failed:{response.status_code}:{response.text[:200]}")
    body = response.json()
    assistant_id = str(body["assistant_message"]["message_id"])
    run_id = str(body["run_id"])
    repo = app.state.chat_service._repo  # noqa: SLF001 - 评测读正式持久化
    deadline = started + timeout
    record = None
    while time.monotonic() < deadline:
        app.state.generation_executor.run_tick()
        for item in repo.list_messages(account_id, conversation_id):
            if item.message_id == assistant_id:
                record = item
                break
        if record is not None and record.status.value in {"done", "error", "stopped"}:
            break
        time.sleep(0.05)
    # 让摘要后台队列跑空（有界），真实计入额外调用与等待。
    executor = app.state.generation_executor
    if hasattr(executor, "_last_summary"):
        drain_deadline = min(deadline, time.monotonic() + 60.0)
        while time.monotonic() < drain_deadline:
            executor.run_tick()
            if "无待处理任务" in str(getattr(executor, "_last_summary", "")):
                break
            time.sleep(0.05)
    elapsed = round(time.monotonic() - started, 2)
    if record is None:
        raise RuntimeError("assistant_message_missing")
    return {
        "run_id": run_id,
        "status": record.status.value,
        "error_code": record.error_code,
        "model_id": record.model_id,
        "answer": (record.content or "")[:600],
        "elapsed_s": elapsed,
    }


def _empty_cost() -> dict[str, int]:
    return {
        "calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "chat_calls": 0,
        "chat_prompt_tokens": 0,
        "chat_completion_tokens": 0,
    }


def _lock_costs(app: Any, cursor: int) -> tuple[int, dict[str, dict[str, int]]]:
    rows = app.state.bridges_database.connection.execute(
        "SELECT rowid, run_id, capability_name, usage FROM model_run_locks"
        " WHERE rowid > ? ORDER BY rowid",
        (cursor,),
    ).fetchall()
    costs: dict[str, dict[str, int]] = {}
    for rowid, run_id, capability_name, usage in rows:
        cursor = max(cursor, int(rowid))
        if not run_id:
            continue
        entry = costs.setdefault(str(run_id), _empty_cost())
        prompt = 0
        completion = 0
        if usage:
            try:
                payload = json.loads(usage)
            except (TypeError, ValueError):
                payload = None
            if isinstance(payload, dict):
                prompt = int(payload.get("prompt_tokens") or 0)
                completion = int(payload.get("completion_tokens") or 0)
        entry["calls"] += 1
        entry["prompt_tokens"] += prompt
        entry["completion_tokens"] += completion
        if capability_name == "qwen_text_chat":
            entry["chat_calls"] += 1
            entry["chat_prompt_tokens"] += prompt
            entry["chat_completion_tokens"] += completion
    return cursor, costs


def _payload_gate_records(
    app: Any, action: Any, start: int, estimate_key: str
) -> tuple[int, list[int], list[int]]:
    """收集最终载荷门的编译估算与（新树）调用后补记的实测用量。"""
    events = app.state.observability_service.list_audit_events(action=action)
    estimates: list[int] = []
    actuals: list[int] = []
    for event in events[start:]:
        details = event.details
        if details.get("record_phase") != "post_call":
            estimate = details.get(estimate_key)
            if isinstance(estimate, int) and estimate > 0:
                estimates.append(estimate)
        actual = details.get("actual_input_tokens")
        if isinstance(actual, int) and actual > 0:
            actuals.append(actual)
    return len(events), estimates, actuals


def _constants() -> dict[str, Any]:
    constants: dict[str, Any] = {}
    try:
        from bridges.ai.payload_budget import (  # noqa: PLC0415
            IMAGE_PART_COST_TOKENS,
            PAYLOAD_BUDGET_VERSION,
            PAYLOAD_SAFETY_MARGIN_TOKENS,
            TOKEN_ESTIMATE_VERSION,
        )

        constants["payload_budget"] = {
            "version": PAYLOAD_BUDGET_VERSION,
            "token_estimate_version": TOKEN_ESTIMATE_VERSION,
            "image_part_cost_tokens": IMAGE_PART_COST_TOKENS,
            "safety_margin_tokens": PAYLOAD_SAFETY_MARGIN_TOKENS,
        }
    except ImportError:
        constants["payload_budget"] = None
    try:
        from bridges.contracts import summaries  # noqa: PLC0415

        constants["summaries"] = {
            "text_max_chars": summaries.SUMMARY_TEXT_MAX_CHARS,
            "max_source_tokens": summaries.SUMMARY_MAX_SOURCE_TOKENS,
            "output_tokens": summaries.SUMMARY_OUTPUT_TOKENS,
            "sync_timeout_ms": summaries.SUMMARY_SYNC_TIMEOUT_MS,
            "background_timeout_ms": summaries.SUMMARY_BACKGROUND_TIMEOUT_MS,
            "max_attempts": summaries.SUMMARY_MAX_ATTEMPTS,
            "max_calls_per_task": summaries.SUMMARY_MAX_CALLS_PER_TASK,
            "min_new_messages": summaries.SUMMARY_MIN_NEW_MESSAGES,
        }
    except ImportError:
        constants["summaries"] = None
    return constants


def _seed_history(
    app: Any,
    account_id: str,
    conversation_id: str,
    scenario: Scenario,
) -> None:
    """直接落库预置多轮历史并锁定模式；不消耗模型调用。"""
    from bridges.chat.repository import MessageRecord  # noqa: PLC0415
    from bridges.contracts.chat import (  # noqa: PLC0415
        ChatMessageRole,
        ChatMessageStatus,
    )

    records: list[tuple[Any, str]] = []
    if scenario.seed_constraint:
        records.append((ChatMessageRole.USER, scenario.seed_constraint))
        records.append((ChatMessageRole.ASSISTANT, "好的，已记下。"))
    for index in range(scenario.seed_pairs):
        records.append(
            (
                ChatMessageRole.USER,
                f"历史背景{index}：" + "补充说明。" * 60,
            )
        )
        records.append((ChatMessageRole.ASSISTANT, "收到。"))
    repo = app.state.chat_service._repo  # noqa: SLF001
    base = datetime.now(UTC)
    for offset, (role, content) in enumerate(records):
        created = base + timedelta(seconds=offset)
        repo.insert_message(
            MessageRecord(
                message_id=f"eval40-{conversation_id[-8:]}-{offset:04d}",
                conversation_id=conversation_id,
                account_id=account_id,
                role=role,
                attempt_number=1,
                status=ChatMessageStatus.DONE,
                content=content,
                thinking=None,
                error_code=None,
                error_message=None,
                duration_ms=None,
                model_id=None,
                run_lock_id=None,
                created_at=created,
                updated_at=created,
            )
        )
    database = app.state.bridges_database
    with database.transaction():
        database.scoped(account_id).execute(
            "UPDATE conversations SET mode_locked = 1"
            " WHERE conversation_id = ? AND account_id = ?",
            (conversation_id, account_id),
        )


def _activate_window(app: Any, window: tuple[int, int]) -> Any:
    """临时激活较小窗口预算，返回可还原的先前快照。"""
    provider = app.state.run_model_config_provider
    previous = provider.snapshot()
    provider.activate(
        model_id=previous.model_id,
        capabilities=previous.capabilities,
        context_window=window[0],
        max_input_tokens=window[1],
        validated_at=previous.validated_at,
        metadata_version=previous.metadata_version,
    )
    return previous


def _restore_config(app: Any, previous: Any) -> None:
    app.state.run_model_config_provider.activate(
        model_id=previous.model_id,
        capabilities=previous.capabilities,
        context_window=previous.context_window,
        max_input_tokens=previous.max_input_tokens,
        validated_at=previous.validated_at,
        metadata_version=previous.metadata_version,
    )


def _run_case(
    client: Any,
    app: Any,
    account_id: str,
    conversation_id: str,
    scenario: Scenario,
    *,
    repeat: int,
    timeout: float,
    gate_action: Any,
    gate_estimate_key: str,
    summary_action: Any,
    compiled_action: Any,
    model_ids: set[str],
) -> dict[str, Any]:
    lock_cursor = app.state.bridges_database.connection.execute(
        "SELECT COALESCE(MAX(rowid), 0) FROM model_run_locks"
    ).fetchone()[0]
    gate_start = len(
        app.state.observability_service.list_audit_events(action=gate_action)
    )
    summary_start = (
        len(app.state.observability_service.list_audit_events(action=summary_action))
        if summary_action is not None
        else 0
    )
    compiled_start = len(
        app.state.observability_service.list_audit_events(action=compiled_action)
    )
    started = time.monotonic()
    turn_results: list[dict[str, Any]] = []
    case_run_ids: list[str] = []
    previous = (
        _activate_window(app, scenario.activate_window)
        if scenario.activate_window is not None
        else None
    )
    if scenario.seed_pairs:
        _seed_history(app, account_id, conversation_id, scenario)
    try:
        for index, turn in enumerate(scenario.turns, start=1):
            if scenario.fresh_account_before_turn == index:
                tag = str(int(datetime.now(UTC).timestamp() * 1_000_000))
                account_id = _register(client, tag=tag)
                conversation_id = app.state.chat_service.create_conversation(
                    account_id
                ).conversation_id
            outcome = _send_turn(
                client,
                app,
                account_id,
                conversation_id,
                turn.content,
                timeout=timeout,
            )
            case_run_ids.append(outcome["run_id"])
            checks = _check_turn(outcome["answer"], turn)
            turn_results.append(
                {
                    "index": index,
                    "status": outcome["status"],
                    "error_code": outcome["error_code"],
                    "model_id": outcome["model_id"],
                    "checks": checks,
                    "answer": outcome["answer"],
                }
            )
            if outcome["model_id"]:
                model_ids.add(str(outcome["model_id"]))
    finally:
        if previous is not None:
            _restore_config(app, previous)
    elapsed = round(time.monotonic() - started, 2)
    lock_cursor, costs = _lock_costs(app, int(lock_cursor))
    case_cost = _empty_cost()
    for run_id in case_run_ids:
        entry = costs.get(run_id)
        if entry is None:
            continue
        for key in case_cost:
            case_cost[key] += entry[key]
    gate_start, estimates, actuals = _payload_gate_records(
        app, gate_action, gate_start, gate_estimate_key
    )
    summary_events = (
        [
            {
                "result": getattr(event.result, "value", str(event.result)),
                "reason": event.details.get("reason"),
                "mode": event.details.get("mode"),
            }
            for event in app.state.observability_service.list_audit_events(
                action=summary_action
            )[summary_start:]
        ]
        if summary_action is not None
        else []
    )
    compiled_events = app.state.observability_service.list_audit_events(
        action=compiled_action
    )[compiled_start:]
    last_compiled = compiled_events[-1].details if compiled_events else {}
    compiled_summary = {
        "summary_source_range": last_compiled.get("summary_source_range"),
        "summary_cache_hit": last_compiled.get("summary_cache_hit"),
        "summary_fallback_entries": last_compiled.get("summary_fallback_entries"),
        "unresolved_reference": last_compiled.get("unresolved_reference"),
    }
    content_passed = all(item["checks"]["passed"] for item in turn_results)
    run_passed = all(item["status"] == "done" for item in turn_results)
    gated = [
        item["error_code"]
        for item in turn_results
        if item["status"] != "done" and (item["answer"] or "").strip()
    ]
    return {
        "scenario_id": scenario.scenario_id,
        "repeat": repeat,
        "dimensions": list(scenario.dimensions),
        "content_passed": content_passed,
        "passed": run_passed and content_passed,
        "quality_gated": gated,
        "summary_events": summary_events,
        "compiled_summary": compiled_summary,
        "turns": turn_results,
        "cost": {**case_cost, "elapsed_s": elapsed},
        "calibration": {
            "gate_estimate_tokens": estimates,
            "gate_actual_tokens": actuals,
            "chat_prompt_tokens": case_cost["chat_prompt_tokens"],
        },
    }


def run_tree(
    tree: Path,
    *,
    result_path: Path,
    repeats: int,
    scenario_ids: list[str] | None,
    timeout: float,
) -> dict[str, Any]:
    tree = tree.resolve()
    tmp_dir = Path(tempfile.mkdtemp(prefix="issue40-eval-"))
    os.environ["BRIDGES_ENVIRONMENT"] = "test"
    os.environ["BRIDGES_DATABASE_URL"] = f"sqlite:///{tmp_dir / 'bridges.db'}"
    os.environ["BRIDGES_SECRET_KEY"] = f"issue40-eval-{tmp_dir.name}"
    for key in list(os.environ):
        if key.startswith("BRIDGES_QWEN_API_KEY"):
            os.environ.pop(key, None)
    sys.path.insert(0, str(tree / "src"))
    os.chdir(tree)

    from bridges.config import get_settings  # noqa: PLC0415

    get_settings.cache_clear()

    from fastapi.testclient import TestClient  # noqa: PLC0415

    from bridges.api.main import create_app  # noqa: PLC0415
    from bridges.contracts.observability import AuditAction  # noqa: PLC0415
    from bridges.credentials.store import OsCredentialStore  # noqa: PLC0415

    # 基线审查树尚无最终载荷门审计：退回编译审计的估算字段。
    gate_action = getattr(
        AuditAction, "PAYLOAD_BUDGET_EVALUATED", AuditAction.CONTEXT_COMPILED
    )
    gate_estimate_key = (
        "input_token_estimate"
        if gate_action is AuditAction.CONTEXT_COMPILED
        else "estimated_input_tokens"
    )
    # 基线审查树尚无摘要准备审计：缺失时跳过摘要事件采集。
    summary_action = getattr(AuditAction, "HISTORY_SUMMARY_PREPARED", None)
    store = OsCredentialStore(data_dir=_local_app_data(), namespace="runtime")
    app = create_app(runtime_credential_store=store)
    result: dict[str, Any] = {
        "tree": str(tree),
        "label": tree.name,
        "commit": _git_commit(tree),
        "started_at": _now_iso(),
        "repeats": repeats,
        "scenarios": [item.scenario_id for item in SCENARIOS],
        "constants": _constants(),
        "inconclusive": False,
        "reason": None,
        "model_ids": [],
        "cases": [],
        "totals": {},
    }

    selected = (
        [item for item in SCENARIOS if item.scenario_id in scenario_ids]
        if scenario_ids
        else list(SCENARIOS)
    )

    with TestClient(app) as client:
        adapter = app.state.model_gateway.get_adapter("qwen_text_chat", "1")
        if adapter is None:
            result["inconclusive"] = True
            result["reason"] = "qwen_text_chat 适配器缺失（凭据不可用）"
            result_path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return result
        model_ids: set[str] = set()
        for repeat in range(1, repeats + 1):
            base_tag = int(datetime.now().strftime("%H%M%S")) * 100 + repeat
            account_id = _register(client, tag=str(base_tag))
            conversations: dict[str, str] = {}
            for scenario in selected:
                conversation = app.state.chat_service.create_conversation(account_id)
                conversations[scenario.scenario_id] = conversation.conversation_id
            for scenario in selected:
                conversation_id = conversations[scenario.scenario_id]
                started = time.monotonic()
                try:
                    _login(client, tag=str(base_tag))
                    case = _run_case(
                        client,
                        app,
                        account_id,
                        conversation_id,
                        scenario,
                        repeat=repeat,
                        timeout=timeout,
                        gate_action=gate_action,
                        gate_estimate_key=gate_estimate_key,
                        summary_action=summary_action,
                        compiled_action=AuditAction.CONTEXT_COMPILED,
                        model_ids=model_ids,
                    )
                except Exception as exc:  # noqa: BLE001 - 单场景失败如实记录
                    case = {
                        "scenario_id": scenario.scenario_id,
                        "repeat": repeat,
                        "dimensions": list(scenario.dimensions),
                        "content_passed": False,
                        "passed": False,
                        "quality_gated": [],
                        "summary_events": [],
                        "compiled_summary": {},
                        "error": f"{type(exc).__name__}: {exc}",
                        "turns": [],
                        "cost": {
                            **_empty_cost(),
                            "elapsed_s": round(time.monotonic() - started, 2),
                        },
                        "calibration": {
                            "gate_estimate_tokens": [],
                            "gate_actual_tokens": [],
                            "chat_prompt_tokens": 0,
                        },
                    }
                result["cases"].append(case)
        result["model_ids"] = sorted(model_ids)

    by_dimension: dict[str, dict[str, int]] = {
        name: {"passed": 0, "total": 0} for name in DIMENSIONS
    }
    total_cost: dict[str, float] = {**_empty_cost(), "elapsed_s": 0.0}
    for case in result["cases"]:
        for dimension in case["dimensions"]:
            by_dimension[dimension]["total"] += 1
            if case.get("content_passed"):
                by_dimension[dimension]["passed"] += 1
        for key in _empty_cost():
            total_cost[key] += case["cost"].get(key, 0)
        total_cost["elapsed_s"] += case["cost"]["elapsed_s"]
    result["totals"] = {
        "by_dimension": by_dimension,
        "cost": {**total_cost, "elapsed_s": round(total_cost["elapsed_s"], 2)},
    }
    result["finished_at"] = _now_iso()
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def _case_content_passed(case: dict[str, Any]) -> bool:
    if case.get("content_passed") is not None:
        return bool(case["content_passed"])
    turns = case.get("turns") or []
    return bool(turns) and all(turn["checks"]["passed"] for turn in turns)


def _case_gated(case: dict[str, Any]) -> list[str]:
    if case.get("quality_gated"):
        return list(case["quality_gated"])
    return [
        turn["error_code"] or "error"
        for turn in case.get("turns") or []
        if turn["status"] != "done" and (turn.get("answer") or "").strip()
    ]


def _format_pairing_markdown(old: dict[str, Any], new: dict[str, Any]) -> str:
    lines: list[str] = ["# 工单 40 上下文连续性与成本：新旧配对报告", ""]
    lines.append(f"生成时间：{_now_iso()}")
    lines.append("")
    lines.append("| 项目 | 旧（审查基线） | 新（本分支） |")
    lines.append("| --- | --- | --- |")
    lines.append(f"| 工作树 | `{old['tree']}` | `{new['tree']}` |")
    lines.append(f"| 提交 | `{old.get('commit')}` | `{new.get('commit')}` |")
    lines.append(
        f"| 实际模型 | `{','.join(old.get('model_ids') or [])}` "
        f"| `{','.join(new.get('model_ids') or [])}` |"
    )
    lines.append(f"| repeats | {old.get('repeats')} | {new.get('repeats')} |")
    lines.append("")
    if old.get("inconclusive") or new.get("inconclusive"):
        lines.append(
            f"> 配对不完整：旧={old.get('reason')}；新={new.get('reason')}。"
        )
        lines.append("")

    lines.append("## 量表通过率（维度 × 侧，按回答内容判定）")
    lines.append("")
    lines.append(
        "> 维度通过以回答内容是否满足连续性检查为准；"
        "运行终态质量门拦截在下方单独列出，不计入连续性维度。"
    )
    lines.append("")
    lines.append("| 维度 | 旧 通过/总数 | 新 通过/总数 |")
    lines.append("| --- | --- | --- |")
    for dimension in DIMENSIONS:
        marks = []
        for side in (old, new):
            cases = [
                case
                for case in side.get("cases", [])
                if dimension in case.get("dimensions", [])
            ]
            passed = sum(1 for case in cases if _case_content_passed(case))
            marks.append(f"{passed}/{len(cases)}")
        lines.append(f"| {dimension} | {marks[0]} | {marks[1]} |")
    lines.append("")

    lines.append("## 场景 × repeat 波动")
    lines.append("")
    lines.append("| 场景 | 旧（按 repeat） | 新（按 repeat） |")
    lines.append("| --- | --- | --- |")
    scenario_ids = list(dict.fromkeys(
        [case["scenario_id"] for case in old.get("cases", [])]
        + [case["scenario_id"] for case in new.get("cases", [])]
    ))
    for scenario_id in scenario_ids:
        old_marks = [
            ("P" if _case_content_passed(case) else "F")
            for case in old.get("cases", [])
            if case["scenario_id"] == scenario_id
        ]
        new_marks = [
            ("P" if _case_content_passed(case) else "F")
            for case in new.get("cases", [])
            if case["scenario_id"] == scenario_id
        ]
        lines.append(
            f"| {scenario_id} | {'/'.join(old_marks) or '-'} "
            f"| {'/'.join(new_marks) or '-'} |"
        )
    lines.append("")
    for side_name, side in (("旧", old), ("新", new)):
        gated = [
            f"{case['scenario_id']}#{case['repeat']}：{'、'.join(sorted(set(_case_gated(case))))}"
            for case in side.get("cases", [])
            if _case_gated(case)
        ]
        lines.append(
            f"- {side_name}侧质量门拦截（内容已产出但终态 error）："
            + ("；".join(gated) if gated else "无")
        )
    lines.append("")

    lines.append("## 成本与耗时（实测）")
    lines.append("")
    lines.append("| 指标 | 旧 | 新 | 差值（新-旧） |")
    lines.append("| --- | --- | --- | --- |")
    old_cost = old.get("totals", {}).get("cost", {})
    new_cost = new.get("totals", {}).get("cost", {})
    for key, label in (
        ("calls", "模型调用次数（含摘要）"),
        ("prompt_tokens", "输入 tokens（含摘要）"),
        ("completion_tokens", "输出 tokens（含摘要）"),
        ("chat_calls", "其中聊天轮调用次数"),
        ("chat_prompt_tokens", "其中聊天轮输入 tokens"),
        ("chat_completion_tokens", "其中聊天轮输出 tokens"),
        ("elapsed_s", "墙钟秒数"),
    ):
        delta = round(float(new_cost.get(key, 0)) - float(old_cost.get(key, 0)), 2)
        lines.append(
            f"| {label} | {old_cost.get(key, 0)} | {new_cost.get(key, 0)} | {delta} |"
        )
    lines.append("")
    for side_name, side in (("旧", old), ("新", new)):
        counters: dict[str, int] = {}
        for case in side.get("cases", []):
            for event in case.get("summary_events", []):
                key = (
                    f"{event.get('result')}:{event.get('reason')}:{event.get('mode')}"
                )
                counters[key] = counters.get(key, 0) + 1
        detail = "、".join(
            f"{key}×{count}" for key, count in sorted(counters.items())
        )
        compressed = sum(
            1
            for case in side.get("cases", [])
            if case.get("compiled_summary", {}).get("summary_source_range")
        )
        lines.append(
            f"- {side_name}侧摘要准备事件：{detail or '无'}；"
            f"发生摘要边界编译的场景数：{compressed}"
        )
    lines.append("")

    lines.append("## 估算校准（网关最终载荷估算 vs 调用后补记实测）")
    lines.append("")
    lines.append(
        "| 侧 | 网关估算合计 | 实测补记合计 | 实测/估算比 "
        "| 单次最大超出 | 余量256覆盖 | 场景聊天输入/估算 |"
    )
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for side_name, side in (("旧", old), ("新", new)):
        estimates: list[int] = []
        actuals: list[int] = []
        prompts = 0
        for case in side.get("cases", []):
            calibration = case.get("calibration", {})
            estimates.extend(calibration.get("gate_estimate_tokens", []))
            actuals.extend(calibration.get("gate_actual_tokens", []))
            prompts += int(calibration.get("chat_prompt_tokens", 0))
        estimate_total = sum(estimates)
        actual_total = sum(actuals)
        ratio = (
            round(actual_total / estimate_total, 3)
            if estimate_total and actual_total
            else "—"
        )
        peak = (
            max(
                actual - estimate
                for estimate, actual in zip(estimates, actuals, strict=False)
            )
            if actuals
            else None
        )
        margin = (
            "—"
            if peak is None
            else ("是" if peak <= 256 else f"否（差 {peak}）")
        )
        aggregate_ratio = (
            round(prompts / estimate_total, 3) if estimate_total else None
        )
        lines.append(
            f"| {side_name} | {estimate_total}（{len(estimates)} 次） "
            f"| {actual_total if actuals else '无（基线未补记）'} | {ratio} "
            f"| {peak if peak is not None else '—'} | {margin} | {aggregate_ratio} |"
        )
    lines.append("")
    lines.append(
        "- 实测/估算比来自工单 40 新增的调用后补记（``actual_input_tokens``）；"
        "基线只有调用前估算，故以场景聊天输入 tokens 近似对照。"
    )
    lines.append("")

    lines.append("## 常数快照")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(new.get("constants"), ensure_ascii=False, indent=2))
    lines.append("```")
    lines.append("")
    lines.append("## 限制")
    lines.append("")
    lines.append(
        "- 真实语义只覆盖纯聊天固定语料；照片/附件读取、多列表投影歧义、摘要失败、"
        "来源删除与任务材料主题续接由确定性正式图测试承担，不做真实配对。"
    )
    lines.append(
        "- 新树估算校准为网关每次调用「编译估算 vs 调用后补记实测」配对"
        "（工单 40 新增补记）；基线无补记，退化为编译估算与场景聊天输入的聚合对照。"
    )
    lines.append(
        "- 首轮真实配对在推荐类追问上触发主树质量门 ``web_search_citation_invalid``；"
        "语料已调整为不含外部事实声明的连续性场景，避免把其它质量门噪声计入连续性量表。"
    )
    lines.append(
        "- 长聊场景预置历史后由真实模型回答；若编译在恢复/回退内即满足预算则不发生摘要"
        "模型调用（以 ``summary_events`` 与 ``compiled_summary`` 如实记录），"
        "摘要边界与失败路径由确定性正式图测试覆盖。"
    )
    return "\n".join(lines) + "\n"


def _ensure_redacted(payload: Any) -> None:
    import re  # noqa: PLC0415

    serialized = json.dumps(payload, ensure_ascii=True)
    for pattern in _SECRET_PATTERNS:
        if re.search(pattern, serialized, flags=re.IGNORECASE):
            raise RuntimeError(f"报告疑似包含敏感内容：{pattern}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="工单 40 上下文连续性与成本评测")
    parser.add_argument("--run-tree", type=Path, help="内部模式：只跑该工作树")
    parser.add_argument("--result-json", type=Path, help="内部模式：原始结果输出路径")
    parser.add_argument("--old-tree", type=Path, default=DEFAULT_OLD_TREE)
    parser.add_argument("--new-tree", type=Path, default=REPO_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--scenario", action="append", dest="scenarios")
    parser.add_argument("--timeout", type=float, default=240.0)
    args = parser.parse_args(argv)

    if args.run_tree is not None:
        if args.result_json is None:
            parser.error("--run-tree 需要 --result-json")
        result = run_tree(
            args.run_tree,
            result_path=args.result_json,
            repeats=args.repeats,
            scenario_ids=args.scenarios,
            timeout=args.timeout,
        )
        print(
            json.dumps(
                {
                    "tree": result["tree"],
                    "inconclusive": result["inconclusive"],
                    "cases": len(result["cases"]),
                    "cost": result["totals"].get("cost"),
                },
                ensure_ascii=False,
            )
        )
        return 2 if result["inconclusive"] else 0

    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    old_result = args.output_dir / f"tree-{args.old_tree.name}-{stamp}.json"
    new_result = args.output_dir / f"tree-{args.new_tree.name}-{stamp}.json"
    command_base = [sys.executable, str(Path(__file__).resolve())]
    scenario_args = [
        value
        for scenario in (args.scenarios or [])
        for value in ("--scenario", scenario)
    ]
    failures = 0
    for tree, result_path in (
        (args.old_tree, old_result),
        (args.new_tree, new_result),
    ):
        if not (tree / "src" / "bridges").exists():
            print(f"[issue40] 跳过不存在的对照树：{tree}", file=sys.stderr)
            failures += 1
            continue
        completed = subprocess.run(
            [
                *command_base,
                "--run-tree",
                str(tree),
                "--result-json",
                str(result_path),
                "--repeats",
                str(args.repeats),
                "--timeout",
                str(args.timeout),
                *scenario_args,
            ],
            cwd=str(tree),
            check=False,
        )
        if completed.returncode != 0 or not result_path.exists():
            failures += 1

    if not old_result.exists() or not new_result.exists():
        print("[issue40] 配对不完整：缺一侧结果。", file=sys.stderr)
        return failures or 2

    old_payload = json.loads(old_result.read_text(encoding="utf-8"))
    new_payload = json.loads(new_result.read_text(encoding="utf-8"))
    _ensure_redacted(old_payload)
    _ensure_redacted(new_payload)
    markdown = _format_pairing_markdown(old_payload, new_payload)
    report_path = args.output_dir / f"pairing-{stamp}.md"
    report_json = args.output_dir / f"pairing-{stamp}.json"
    report_path.write_text(markdown, encoding="utf-8")
    report_json.write_text(
        json.dumps(
            {"old": old_payload, "new": new_payload, "markdown": markdown},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"[issue40] 报告：{report_path}")
    print(f"[issue40] 原始：{old_result}")
    print(f"[issue40] 原始：{new_result}")
    if old_payload.get("inconclusive") or new_payload.get("inconclusive"):
        return 2
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
