"""工单 42：在隔离代码树和临时数据库内采集真实对话。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.issue42_pairing_budget import (
    _budget_entry_counts,
    _budget_ledger,
    _external_peak,
    _paper_observation,
)
from scripts.issue42_pairing_cases import (
    SCENARIOS,
    TERMINAL_STATUSES,
    CaseSpec,
    TurnSpec,
    corpus_sha256,
    evaluate_case_checks,
    projection_empty,
)
from scripts.issue42_pairing_lock import source_lock

REPO_ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "Passw0rd123!"


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
    data_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "BridGes" / "data"
    result: dict[str, Any] = {
        "tree": str(tree),
        "commit": _git(tree, "rev-parse", "HEAD"),
        "dirty": bool(_git(tree, "status", "--porcelain")),
        "source_lock": source_lock(tree, Path(__file__).parent),
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
        "SELECT capability_name, usage FROM model_run_locks WHERE rowid > ? ORDER BY rowid",
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
        message for message in projection["messages"] if message["message_id"] == message_id
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
    first_progress = next((event for event in events if event.kind in {"node", "stage"}), None)
    first_result = next(
        (
            event
            for event in events
            if event.kind == "result"
            and isinstance(event.payload, dict)
            and event.payload.get("result", {}).get("delivered")
        ),
        None,
    )
    completed = next((event for event in events if event.kind == "done"), None)
    usable = first_result or (completed if record.status.value == "done" else None)
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
        "route": getattr(record, "route", None),
        "elapsed_s": round(time.monotonic() - started, 2),
        "answer_elapsed_ms": run.duration_ms,
        "queue_to_first_token_ms": (
            round((first_delta.created_at - run.created_at).total_seconds() * 1000)
            if first_delta
            else None
        ),
        "queue_to_first_progress_ms": (
            round((first_progress.created_at - run.created_at).total_seconds() * 1000)
            if first_progress
            else None
        ),
        "queue_to_first_usable_ms": (
            round((usable.created_at - run.created_at).total_seconds() * 1000) if usable else None
        ),
        "queue_to_complete_ms": (
            round((completed.created_at - run.created_at).total_seconds() * 1000)
            if completed and record.status.value == "done"
            else None
        ),
        "phase_timings": [
            {
                "kind": event.kind,
                "offset_ms": round((event.created_at - run.created_at).total_seconds() * 1000),
            }
            for event in events
            if event.kind in {"node", "stage", "result", "done", "error"}
        ],
        "capability_calls": by_capability,
        "paper_search_empty": projection_empty(projected.get("paper_search")),
        "budget": _budget_ledger(app, run_id),
        "budget_entries": _budget_entry_counts(app, run_id),
        "external_peak": _external_peak(app, run_id),
        "paper_observation": _paper_observation(projected),
    }


def _list_tasks(client: Any, conversation_id: str) -> list[Any] | None:
    response = client.get(f"/tasks/conversations/{conversation_id}")
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise RuntimeError(f"tasks_failed:{response.status_code}")
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
