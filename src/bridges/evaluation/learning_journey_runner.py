"""工单 13：学习模式真实主模型旅程的证据采集与运行锁。

本模块是 ``scripts/run_issue13_learning_journeys.py`` 的实现层：把固定
情境（``learning_journeys.SCENARIOS``）通过真实 API 路由（TestClient 驱动
正式应用装配）发送给主模型，并采集：

- 每轮导师回复正文、状态与前后台延迟（首个 delta 与完成事件来自
  ``generation_events``，诊断耗时来自 ``study_diagnosis_tasks``）；
- 学习状态事实：局部进展、待答问题、用户控制、诊断任务、学习档案、
  采用记录与公开查证记录；
- 调用与成本：``run_budget_ledger`` 的调用/输入/输出计数与
  ``model_run_locks`` 的逐次调用模型、状态、重试与用量；
- 运行锁：代码提交与源码快照、语料与量表摘要、模型配置快照、合同与
  提示词版本、预算常量。

只记录结构化事实与固定语料正文；不记录凭据、不导出内部提示词。
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bridges.contracts.study_adaptation import ADAPTATION_CONTRACT_VERSION
from bridges.contracts.study_diagnosis import DIAGNOSIS_CONTRACT_VERSION
from bridges.contracts.study_source_verification import (
    STUDY_SOURCE_VERIFICATION_VERSION,
)
from bridges.evaluation.learning_journeys import (
    LEARNING_JOURNEY_CORPUS_VERSION,
    SCENARIOS_BY_ID,
    corpus_digest,
    independent_ground_truth,
)
from bridges.study.versions import STUDY_GRAPH_VERSION, STUDY_TUTOR_GRAPH_VERSION

#: 单轮生成 + 诊断的等待上限（秒）；真实主模型长回复与诊断重试需要余量。
JOURNEY_TURN_TIMEOUT_SECONDS = 900.0

#: 诊断失败后的重试退避需要多次 tick；空闲判定间隔。
_IDLE_POLL_SECONDS = 0.5


@dataclass(frozen=True, slots=True)
class CredentialPresence:
    """凭据可用性的脱敏记录（只记布尔与来源名，绝不记录正文）。"""

    qwen_configured: bool
    qwen_source: str
    tavily_configured: bool
    tavily_source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "qwen_configured": self.qwen_configured,
            "qwen_source": self.qwen_source,
            "tavily_configured": self.tavily_configured,
            "tavily_source": self.tavily_source,
        }


def resolve_credential_presence(store: Any, settings: Any) -> CredentialPresence:
    """经正式解析器读取凭据存在性；不取出、不记录任何秘密正文。"""

    from bridges.credentials.runtime_resolver import RuntimeCredentialResolver

    resolver = RuntimeCredentialResolver(settings=settings, credential_store=store)
    qwen = resolver.qwen_api_key()
    tavily = resolver.tavily_api_key()
    return CredentialPresence(
        qwen_configured=bool(qwen.configured),
        qwen_source=str(qwen.source),
        tavily_configured=bool(tavily.configured),
        tavily_source=str(tavily.source),
    )


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
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


def _rows(database: Any, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    return [dict(row) for row in database.connection.execute(sql, params).fetchall()]


class JourneyEvidenceCollector:
    """按账户/会话采集真实旅程证据。"""

    def __init__(
        self,
        *,
        app: Any,
        client: Any,
        database: Any,
        account_id: str,
    ) -> None:
        self._app = app
        self._client = client
        self._database = database
        self._account_id = account_id

    @property
    def database(self) -> Any:
        return self._database

    @property
    def account_id(self) -> str:
        return self._account_id

    def latest_run(self, conversation_id: str) -> dict[str, Any]:
        """最近一次生成的运行与助手消息标识（首轮创建路径使用）。"""

        rows = _rows(
            self._database,
            "SELECT run_id, assistant_message_id FROM generation_runs"
            " WHERE account_id = ? AND conversation_id = ?"
            " ORDER BY created_at DESC LIMIT 1",
            (self._account_id, conversation_id),
        )
        return rows[0] if rows else {}

    # ------------------------------------------------------------------
    # 运行推进
    # ------------------------------------------------------------------

    def _pending(self, conversation_id: str) -> tuple[int, int]:
        generation = int(
            self._database.connection.execute(
                "SELECT COUNT(*) AS count FROM generation_runs"
                " WHERE account_id = ? AND conversation_id = ?"
                " AND status IN ('queued', 'running')",
                (self._account_id, conversation_id),
            ).fetchone()["count"]
        )
        diagnosis = int(
            self._database.connection.execute(
                "SELECT COUNT(*) AS count FROM study_diagnosis_tasks"
                " WHERE account_id = ? AND conversation_id = ?"
                " AND status IN ('pending', 'running')",
                (self._account_id, conversation_id),
            ).fetchone()["count"]
        )
        return generation, diagnosis

    def drive_until_idle(
        self, conversation_id: str, *, timeout: float = JOURNEY_TURN_TIMEOUT_SECONDS
    ) -> dict[str, Any]:
        """同步推进执行器直到该会话生成与诊断都空闲；返回耗时统计。"""

        executor = self._app.state.generation_executor
        started = time.monotonic()
        ticks = 0
        deadline = started + timeout
        while time.monotonic() < deadline:
            executor.run_tick()
            ticks += 1
            generation, diagnosis = self._pending(conversation_id)
            if generation == 0 and diagnosis == 0:
                return {
                    "ticks": ticks,
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                }
            time.sleep(_IDLE_POLL_SECONDS)
        raise TimeoutError(
            f"会话 {conversation_id} 在 {timeout:.0f}s 内未完成生成与诊断。"
        )

    def _drive_stopped(self, conversation_id: str) -> None:
        """把该会话生成推进到终态；诊断不一定立即完成。"""

        executor = self._app.state.generation_executor
        deadline = time.monotonic() + JOURNEY_TURN_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            executor.run_tick()
            generation, _ = self._pending(conversation_id)
            if generation == 0:
                return
            time.sleep(_IDLE_POLL_SECONDS)
        raise TimeoutError(f"会话 {conversation_id} 生成未在时限内收敛。")

    # ------------------------------------------------------------------
    # 对话操作
    # ------------------------------------------------------------------

    def start_study(self, content: str, key: str) -> str:
        response = self._client.post(
            "/chat/first-turn",
            json={"mode": "study", "content": content, "idempotency_key": key},
        )
        assert response.status_code == 201, response.text
        return str(response.json()["conversation"]["conversation_id"])

    def send_turn(self, conversation_id: str, content: str, key: str) -> dict[str, Any]:
        response = self._client.post(
            f"/chat/conversations/{conversation_id}/messages",
            json={"content": content, "idempotency_key": key},
        )
        body = response.json()
        assert response.status_code == 200, response.text
        return {
            "run_id": str(body.get("run_id") or ""),
            "cursor": body.get("cursor"),
            "user_message_id": body["user_message"]["message_id"],
            "assistant_message_id": body["assistant_message"]["message_id"],
        }

    def conversation_detail(self, conversation_id: str) -> dict[str, Any]:
        response = self._client.get(f"/chat/conversations/{conversation_id}")
        assert response.status_code == 200, response.text
        payload: dict[str, Any] = dict(response.json())
        return payload

    # ------------------------------------------------------------------
    # 证据采集
    # ------------------------------------------------------------------

    def _run_timing(self, run_id: str) -> dict[str, Any]:
        runs = _rows(
            self._database,
            "SELECT status, error_code, duration_ms, created_at, updated_at"
            " FROM generation_runs WHERE account_id = ? AND run_id = ?",
            (self._account_id, run_id),
        )
        events = _rows(
            self._database,
            "SELECT kind, seq, created_at FROM generation_events"
            " WHERE account_id = ? AND run_id = ? ORDER BY seq",
            (self._account_id, run_id),
        )
        delta_events = [event for event in events if event["kind"] == "delta"]
        first_delta_at = delta_events[0]["created_at"] if delta_events else None
        completed_at = events[-1]["created_at"] if events else None
        run = runs[0] if runs else {}
        return {
            "run_id": run_id,
            "status": run.get("status"),
            "error_code": run.get("error_code"),
            "duration_ms": run.get("duration_ms"),
            "event_count": len(events),
            "delta_count": len(delta_events),
            "first_delta_ms": _delta_ms(run.get("created_at"), first_delta_at),
            "front_stage_completion_ms": _delta_ms(
                run.get("created_at"), completed_at or run.get("updated_at")
            ),
        }

    def _budget(self, run_id: str) -> dict[str, Any] | None:
        rows = _rows(
            self._database,
            "SELECT model_calls_used, external_calls_used, input_tokens_used,"
            " output_tokens_used, status, exhausted_reason,"
            " total_budget_ms, model_call_limit, token_budget"
            " FROM run_budget_ledger WHERE account_id = ? AND run_id = ?",
            (self._account_id, run_id),
        )
        return rows[0] if rows else None

    def _model_calls(self, run_id: str) -> list[dict[str, Any]]:
        rows = _rows(
            self._database,
            "SELECT capability_name, actual_model_id, status, retry_count,"
            " error_code, usage, created_at FROM model_run_locks"
            " WHERE account_id = ? AND run_id = ? ORDER BY created_at",
            (self._account_id, run_id),
        )
        calls = []
        for row in rows:
            usage = row.get("usage")
            try:
                usage_payload = json.loads(usage) if usage else None
            except json.JSONDecodeError:
                usage_payload = {"unparsable": True}
            calls.append(
                {
                    "capability": row["capability_name"],
                    "actual_model_id": row["actual_model_id"],
                    "status": row["status"],
                    "retry_count": row["retry_count"],
                    "error_code": row["error_code"],
                    "usage": usage_payload,
                }
            )
        return calls

    def _diagnosis_tasks(self, conversation_id: str) -> list[dict[str, Any]]:
        rows = _rows(
            self._database,
            "SELECT task_id, evidence_id, question, knowledge_context, status,"
            " attempt, failure_reason, result_version, created_at, completed_at,"
            " updated_at FROM study_diagnosis_tasks"
            " WHERE account_id = ? AND conversation_id = ? ORDER BY created_at",
            (self._account_id, conversation_id),
        )
        for row in rows:
            row["latency_ms"] = _delta_ms(row.get("created_at"), row.get("completed_at"))
        return rows

    def diagnosis_tasks(self, conversation_id: str) -> list[dict[str, Any]]:
        return self._diagnosis_tasks(conversation_id)

    def records(self) -> list[dict[str, Any]]:
        return self._records()

    def adoptions(self, conversation_id: str) -> list[dict[str, Any]]:
        return self._adoptions(conversation_id)

    def _records(self) -> list[dict[str, Any]]:
        return _rows(
            self._database,
            "SELECT record_id, subject, title, usage_context, verdict, state,"
            " pending_confirmation, record_version, evidence_ids_json,"
            " created_at, updated_at FROM study_learning_records"
            " WHERE account_id = ? ORDER BY updated_at",
            (self._account_id,),
        )

    def _adoptions(self, conversation_id: str) -> list[dict[str, Any]]:
        rows = _rows(
            self._database,
            "SELECT adoption_id, run_id, assistant_message_id, goal_id,"
            " goal_version, route_version, status, invalid_reason, entries_json,"
            " uncertainty_note, contract_version, created_at"
            " FROM study_record_adoptions WHERE account_id = ?"
            " AND conversation_id = ? ORDER BY created_at",
            (self._account_id, conversation_id),
        )
        for row in rows:
            try:
                row["entries"] = json.loads(row.pop("entries_json") or "[]")
            except json.JSONDecodeError:
                row["entries"] = []
        return rows

    def _controls(self, conversation_id: str) -> list[dict[str, Any]]:
        return _rows(
            self._database,
            "SELECT kind, bound_check_id, bound_question, released, created_at"
            " FROM study_session_controls WHERE account_id = ?"
            " AND conversation_id = ? ORDER BY created_at",
            (self._account_id, conversation_id),
        )

    def _goal_links(self, conversation_id: str) -> list[dict[str, Any]]:
        return _rows(
            self._database,
            "SELECT goal_id, created_at, updated_at FROM study_conversation_goals"
            " WHERE account_id = ? AND conversation_id = ?",
            (self._account_id, conversation_id),
        )

    def _source_verifications(self, assistant_message_id: str) -> list[dict[str, Any]]:
        rows = _rows(
            self._database,
            "SELECT verification_id, purpose, status, query_summary,"
            " sources_json, adopted_source_ids_json, has_conflicts, gap_note,"
            " can_retry, contract_version, created_at"
            " FROM study_source_verifications WHERE account_id = ?"
            " AND assistant_message_id = ?",
            (self._account_id, assistant_message_id),
        )
        for row in rows:
            try:
                sources = json.loads(row.pop("sources_json") or "[]")
                adopted = json.loads(row.pop("adopted_source_ids_json") or "[]")
            except json.JSONDecodeError:
                sources, adopted = [], []
            row["source_count"] = len(sources)
            row["adopted_source_ids"] = adopted
            row["sources"] = sources
        return rows

    def capture_turn(
        self,
        conversation_id: str,
        sent: dict[str, Any],
        *,
        user_content: str,
        expectation: str,
    ) -> dict[str, Any]:
        detail = self.conversation_detail(conversation_id)
        assistant = next(
            (
                message
                for message in detail["messages"]
                if message["message_id"] == sent["assistant_message_id"]
            ),
            None,
        )
        user_message_id = sent.get("user_message_id") or next(
            (
                message["message_id"]
                for message in reversed(detail["messages"])
                if message["role"] == "user"
            ),
            "",
        )
        run_id = sent.get("run_id") or ""
        if not run_id:
            runs = _rows(
                self._database,
                "SELECT run_id FROM generation_runs WHERE account_id = ?"
                " AND assistant_message_id = ? ORDER BY created_at DESC LIMIT 1",
                (self._account_id, sent["assistant_message_id"]),
            )
            run_id = runs[0]["run_id"] if runs else ""
        return {
            "user_content": user_content,
            "expectation": expectation,
            "user_message_id": user_message_id,
            "assistant_message_id": sent["assistant_message_id"],
            "assistant_status": (assistant or {}).get("status"),
            "assistant_content": (assistant or {}).get("content") or "",
            "assistant_error_code": (assistant or {}).get("error_code"),
            "timing": self._run_timing(run_id) if run_id else {},
            "budget": self._budget(run_id) if run_id else None,
            "model_calls": self._model_calls(run_id) if run_id else [],
            "source_verifications": self._source_verifications(
                sent["assistant_message_id"]
            ),
        }

    def conversation_snapshot(self, conversation_id: str) -> dict[str, Any]:
        detail = self.conversation_detail(conversation_id)
        return {
            "conversation_id": conversation_id,
            "mode": detail.get("mode"),
            "mode_locked": detail.get("mode_locked"),
            "study_local_progress": detail.get("study_local_progress"),
            "study_goal": detail.get("study_goal"),
            "study_goal_notice": detail.get("study_goal_notice"),
            "study_pending_check": detail.get("study_pending_check"),
            "study_last_control": detail.get("study_last_control"),
            "study_pause": detail.get("study_pause"),
            "study_summary": detail.get("study_summary"),
            "study_diagnosis_tasks": detail.get("study_diagnosis_tasks"),
            "study_learning_record": detail.get("study_learning_record"),
            "study_record_adoptions": detail.get("study_record_adoptions"),
            "goal_links": self._goal_links(conversation_id),
            "controls": self._controls(conversation_id),
        }

    # ------------------------------------------------------------------
    # 用户控制示范（纠正/删除）
    # ------------------------------------------------------------------

    def exercise_record_controls(self, conversation_id: str) -> dict[str, Any]:
        """对真实档案执行一次纠正与一次删除，记录真实状态变化。"""

        result: dict[str, Any] = {"corrections": [], "deletions": [], "problems": []}
        records = self._records()
        if not records:
            result["problems"].append("档案为空，未执行纠正/删除示范。")
            return result
        first = records[0]
        correction = self._client.post(
            f"/chat/conversations/{conversation_id}/study/records/"
            f"{first['record_id']}/corrections",
            json={
                "note": "用户纠正：当前判断不适用，我先按自己的理解继续，请勿据此调整教学。",
                "expected_version": int(first["record_version"]),
            },
        )
        result["corrections"].append(
            {
                "record_id": first["record_id"],
                "status_code": correction.status_code,
                "body": correction.json() if correction.status_code < 300 else None,
            }
        )
        records_after = self._records()
        deleted_id = None
        for record in records_after:
            if record["record_id"] != first["record_id"] and record["state"] == "active":
                deletion = self._client.request(
                    "DELETE",
                    f"/chat/conversations/{conversation_id}/study/records/"
                    f"{record['record_id']}",
                    json={"expected_version": int(record["record_version"])},
                )
                result["deletions"].append(
                    {
                        "record_id": record["record_id"],
                        "status_code": deletion.status_code,
                        "body": deletion.json() if deletion.status_code < 300 else None,
                    }
                )
                deleted_id = record["record_id"]
                break
        if deleted_id is None:
            result["problems"].append("没有第二条活动记录可删除；删除示范未执行。")
        result["records_after"] = self._records()
        return result


def build_run_lock(
    repo_root: Path,
    *,
    app: Any,
    scenario_ids: list[str],
    credentials: CredentialPresence,
    injected_models: bool,
) -> dict[str, Any]:
    """冻结本次评测的运行锁（代码/语料/模型配置/合同/提示词/预算）。"""

    scenario_module = repo_root / "src" / "bridges" / "evaluation" / "learning_journeys.py"
    runner_module = (
        repo_root / "src" / "bridges" / "evaluation" / "learning_journey_runner.py"
    )
    entry_script = repo_root / "scripts" / "run_issue13_learning_journeys.py"
    prompt_files = [
        repo_root / "src" / "bridges" / "study" / "tutor_prompt.py",
        repo_root / "src" / "bridges" / "study" / "diagnosis_prompt.py",
    ]
    model_config = app.state.run_model_config_provider.snapshot()
    return {
        "kind": "study-journey-run-lock",
        "corpus_version": LEARNING_JOURNEY_CORPUS_VERSION,
        "corpus_sha256": corpus_digest(),
        "scenarios": scenario_ids,
        "git_commit": _git(repo_root, "rev-parse", "HEAD"),
        "git_dirty": bool(
            _git(repo_root, "status", "--porcelain", "--untracked-files=no")
        ),
        "source_snapshot_sha256": _hash_files(
            sorted((repo_root / "src" / "bridges").rglob("*.py"))
        ),
        "evaluation_sha256": _hash_files(
            [scenario_module, runner_module, entry_script]
        ),
        "prompt_sha256": _hash_files(prompt_files),
        "python_version": _python_version(),
        "model_config": model_config.model_dump(mode="json"),
        "injected_models_overridden": injected_models,
        "contract_versions": {
            "diagnosis": DIAGNOSIS_CONTRACT_VERSION,
            "adaptation": ADAPTATION_CONTRACT_VERSION,
            "source_verification": STUDY_SOURCE_VERIFICATION_VERSION,
            "graph_version": STUDY_TUTOR_GRAPH_VERSION,
            "legacy_graph_version": STUDY_GRAPH_VERSION,
        },
        "budget_constants": {
            "turn_timeout_seconds": JOURNEY_TURN_TIMEOUT_SECONDS,
        },
        "credentials": credentials.to_dict(),
        "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def _python_version() -> str:
    import sys

    return sys.version.split()[0]


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
        if not path.is_file():
            continue
        digest.update(path.read_bytes())
    return digest.hexdigest()


def assert_no_secret_like(payload: str) -> None:
    """证据文件写入前的粗筛：拒绝明显凭据形态的文本。"""

    import re

    patterns = (
        r"sk-[A-Za-z0-9]{20,}",
        r"Bearer\s+[A-Za-z0-9\-_.]{20,}",
        r"tvly-[A-Za-z0-9]{16,}",
    )
    for pattern in patterns:
        if re.search(pattern, payload):
            raise ValueError(f"证据内容疑似包含凭据形态文本：{pattern}")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    assert_no_secret_like(rendered)
    path.write_text(rendered, encoding="utf-8")


def ground_truth_payload() -> dict[str, Any]:
    """独立核对基准的公开入口（脚本与证据文件共用）。"""

    return independent_ground_truth()


def scenario_lookup(scenario_id: str) -> Any:
    scenario = SCENARIOS_BY_ID.get(scenario_id)
    if scenario is None:
        raise KeyError(f"未知情境：{scenario_id}")
    return scenario
