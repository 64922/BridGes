"""Issue 11：按需公开查证与来源失败降级。

覆盖验收项：
1. 基础概念与数学计算无需统一联网，零外呼；
2. 论文、时效、精确外部数据、争议与明确核实请求走真实查证分支；
3. 公开查询只含最小公开概念，不发送私人原文、档案或凭据；
4. 引用与实际来源/读取范围一致，摘要不冒充全文；
5. 用户来源限制不可绕过；失败不判学生错、不生成伪评价依据；
6. 停止、重试与预算耗尽不重复外呼、不覆盖有效状态。
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient

from bridges.web_search.client import WebSearchError
from bridges.web_search.contracts import (
    WebSearchResult,
    WebSearchVerification,
)
from bridges.web_search.service import WebSearchService
from tests.chat.test_issue02_text_tutor import _TextTutorAdapter
from tests.chat.test_issue03_embedded_verification import (
    _gateway_with_verification,
    _plan_evaluate,
    _plan_pose,
    _PlanAdapter,
)
from tests.chat.test_v2_05_photo_attachments import (
    _app,
    _register,
)

_Q1 = "这篇论文报告的准确率是多少？依据是什么？"
_Q2 = "如果把学习率调小，训练会发生什么变化？请说明理由。"
_ANSWER = "学习率过小会让收敛变慢，但一般不会导致发散。"
_VERIFY_TEXT = (
    "请核实一下这篇论文关于注意力机制的最新进展，我的邮箱是 priv@example.com，简历在附件里。"
)


class _RecordingSearchClient:
    """记录实际外发查询的确定性替身；可配置失败与来源证据状态。"""

    def __init__(
        self,
        *,
        fail: bool = False,
        verification: WebSearchVerification = WebSearchVerification.VERIFIED,
    ) -> None:
        self.queries: list[str] = []
        self.fail = fail
        self.verification = verification

    def search(self, query: str, **kwargs: Any) -> list[WebSearchResult]:
        self.queries.append(query)
        if self.fail:
            raise WebSearchError("web_search_timeout", "联网搜索超时", retryable=False)
        return [
            WebSearchResult(
                result_id="web-1",
                title="注意力机制综述",
                site="example.org",
                url="https://example.org/attention",
                snippet="年度综述摘要。",
                accessed_at=datetime.now(UTC),
                content_summary="综述正文摘要。",
                verification=self.verification,
            )
        ]


def _install_web_search(app: Any, client: _RecordingSearchClient) -> None:
    app.state.chat_service._turn._web_search = WebSearchService(  # noqa: SLF001
        client=client, sleeper=lambda _: None
    )


def _start_study(client: TestClient, content: str, key: str) -> str:
    response = client.post(
        "/chat/first-turn",
        json={"mode": "study", "content": content, "idempotency_key": key},
    )
    assert response.status_code == 201, response.text
    return response.json()["conversation"]["conversation_id"]


def _send(client: TestClient, conversation_id: str, content: str, key: str) -> str:
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": content, "idempotency_key": key},
    )
    assert response.status_code == 200, response.text
    return response.json()["assistant_message"]["message_id"]


def _close(app: Any) -> None:
    app.state.bridges_database.close()
    app.state.state_store.close()


def _verification_rows(app: Any) -> list[Any]:
    return app.state.bridges_database.connection.execute(
        "SELECT * FROM study_source_verifications ORDER BY rowid"
    ).fetchall()


# ---------------------------------------------------------------------------
# 基础概念与数学计算：零外呼
# ---------------------------------------------------------------------------


def test_basic_concept_turn_never_calls_public_search(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _TextTutorAdapter("互斥表示不能同时发生，和独立不是一回事。")
    app.state.chat_service._gateway = _gateway_with_verification(text, _PlanAdapter())
    search = _RecordingSearchClient()
    _install_web_search(app, search)

    try:
        with TestClient(app) as client:
            _register(client, "issue11-basic")
            conversation_id = _start_study(client, "什么是互斥事件？", "issue11-basic-1")
            generation_helpers["drive"](app)

            assert search.queries == []
            detail = client.get(f"/chat/conversations/{conversation_id}").json()
            assistant = detail["messages"][-1]
            assert assistant["status"] == "done"
            assert assistant["web_search"] is None
            assert _verification_rows(app) == []
    finally:
        _close(app)


def test_math_computation_with_numbers_does_not_trigger_search(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _TextTutorAdapter("12.5 × 3 = 37.5，按乘法分配律直接计算。")
    app.state.chat_service._gateway = _gateway_with_verification(text, _PlanAdapter())
    search = _RecordingSearchClient()
    _install_web_search(app, search)

    try:
        with TestClient(app) as client:
            _register(client, "issue11-math")
            conversation_id = _start_study(client, "请计算 12.5 × 3 的结果。", "issue11-math-1")
            generation_helpers["drive"](app)

            assert search.queries == []
            detail = client.get(f"/chat/conversations/{conversation_id}").json()
            assert detail["messages"][-1]["web_search"] is None
            assert _verification_rows(app) == []
    finally:
        _close(app)


# ---------------------------------------------------------------------------
# 明确核实：真实查证、脱敏外发、来源版本进入冻结合同
# ---------------------------------------------------------------------------


def test_explicit_verification_searches_with_scrubbed_query_and_freezes_sources(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _TextTutorAdapter(f"我们核实一下这篇论文。{_Q1}")
    plan = _PlanAdapter([_plan_pose(_Q1)])
    app.state.chat_service._gateway = _gateway_with_verification(text, plan)
    search = _RecordingSearchClient()
    _install_web_search(app, search)

    try:
        with TestClient(app) as client:
            _register(client, "issue11-verify")
            database = app.state.bridges_database
            conversation_id = _start_study(client, _VERIFY_TEXT, "issue11-verify-1")
            generation_helpers["drive"](app)

            # 1) 真实外呼发生过，且外发查询只含最小公开概念。
            assert search.queries
            for query in search.queries:
                assert "priv@example.com" not in query
                assert "简历" not in query
                assert "附件" not in query

            # 2) 消息投影回报真实来源与成功状态（界面按真实事件显示）。
            detail = client.get(f"/chat/conversations/{conversation_id}").json()
            assistant = detail["messages"][-1]
            assert assistant["status"] == "done"
            assert assistant["web_search"]["status"] in {"success", "partial"}
            assert assistant["web_search"]["results"][0]["url"] == ("https://example.org/attention")

            # 3) 查证产物冻结目的/来源/读取范围/采用关系与版本。
            rows = _verification_rows(app)
            assert len(rows) == 1
            row = rows[0]
            assert row["assistant_message_id"] == assistant["message_id"]
            assert row["status"] == "success"
            assert row["purpose"] in {"explicit_request", "paper", "freshness"}
            sources = json.loads(row["sources_json"])
            assert sources[0]["url"] == "https://example.org/attention"
            assert sources[0]["read_scope"] == "full_text"
            assert sources[0]["adoption"] == "adopted"
            assert json.loads(row["adopted_source_ids_json"]) == ["web-1"]
            assert row["contract_version"] == "study-source-verification-v1"

            # 4) 来源版本接入 03 的冻结合同（正式练习评价依据）。
            pending = detail["study_pending_check"]
            assert pending is not None and pending["kind"] == "formal_check"
            stored = database.connection.execute(
                "SELECT basis_json FROM study_pending_checks"
            ).fetchone()
            basis = json.loads(stored["basis_json"])
            assert basis["source_refs"][0]["url"] == "https://example.org/attention"
            assert basis["source_refs"][0]["provider"] == "tavily"
            assert basis["evidence_incomplete"] is False

            # 5) 模型收到查证合同：只能引用这些来源、搜索成功≠已核实。
            model_context = str(text.payloads[-1])
            assert "本轮公开查证合同" in model_context
            assert "https://example.org/attention" in model_context
            assert "搜索成功不等于结论已核实" in model_context
    finally:
        _close(app)


# ---------------------------------------------------------------------------
# 失败降级：不生成伪标准答案，也不判学生错
# ---------------------------------------------------------------------------


def test_failed_verification_downgrades_check_and_never_marks_wrong(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _TextTutorAdapter(f"先看一个检查点。{_Q2}")
    plan = _PlanAdapter([_plan_pose(_Q2)])
    app.state.chat_service._gateway = _gateway_with_verification(text, plan)
    search = _RecordingSearchClient(fail=True)
    _install_web_search(app, search)

    try:
        with TestClient(app) as client:
            _register(client, "issue11-fail")
            database = app.state.bridges_database
            conversation_id = _start_study(client, _VERIFY_TEXT, "issue11-fail-1")
            generation_helpers["drive"](app)

            # 1) 失败不阻断回答，也不伪造引用：状态如实、无采用来源。
            detail = client.get(f"/chat/conversations/{conversation_id}").json()
            assistant = detail["messages"][-1]
            assert assistant["status"] == "done"
            assert assistant["web_search"]["status"] == "error"
            rows = _verification_rows(app)
            assert len(rows) == 1
            assert rows[0]["status"] == "error"
            assert json.loads(rows[0]["adopted_source_ids_json"]) == []
            assert rows[0]["gap_note"]

            # 2) 正式练习降级为开放探查：没有伪标准答案，依据标记不完整。
            pending = detail["study_pending_check"]
            assert pending is not None
            assert pending["kind"] == "open_probe"
            stored = database.connection.execute(
                "SELECT basis_json FROM study_pending_checks"
            ).fetchone()
            basis = json.loads(stored["basis_json"])
            assert basis["expected_answer"] == ""
            assert basis["source_refs"] == []
            assert basis["evidence_incomplete"] is True

            # 3) 作答被判「误解」也不落地：如实记录无法判断，不判学生错。
            plan.default = _plan_evaluate("misconception")
            text.answer = "收到你的回答，我们换个角度看。"
            _send(client, conversation_id, _ANSWER, "issue11-fail-2")
            generation_helpers["drive"](app)

            evidence = database.connection.execute(
                "SELECT outcome, semantics, check_kind FROM study_evidence ORDER BY rowid DESC"
            ).fetchone()
            assert evidence["outcome"] == "undetermined"
            assert evidence["check_kind"] == "open_probe"
    finally:
        _close(app)


def test_conflicting_sources_are_excluded_and_marked(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _TextTutorAdapter("分别说明各方说法。")
    app.state.chat_service._gateway = _gateway_with_verification(text, _PlanAdapter())
    search = _RecordingSearchClient(verification=WebSearchVerification.CONFLICTING)
    _install_web_search(app, search)

    try:
        with TestClient(app) as client:
            _register(client, "issue11-conflict")
            _start_study(client, _VERIFY_TEXT, "issue11-conflict-1")
            generation_helpers["drive"](app)

            rows = _verification_rows(app)
            assert len(rows) == 1
            assert rows[0]["status"] == "conflict"
            assert rows[0]["has_conflicts"] == 1
            sources = json.loads(rows[0]["sources_json"])
            assert sources[0]["adoption"] == "excluded"
            assert json.loads(rows[0]["adopted_source_ids_json"]) == []
            # 冲突不合并为单一结论：指令要求分别说明各方说法。
            assert "分别说明" in str(text.payloads[-1])
    finally:
        _close(app)


def test_summary_only_sources_stay_summary_level(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _TextTutorAdapter("这是摘要级说明。")
    app.state.chat_service._gateway = _gateway_with_verification(text, _PlanAdapter())
    search = _RecordingSearchClient(verification=WebSearchVerification.SUMMARY_ONLY)
    _install_web_search(app, search)

    try:
        with TestClient(app) as client:
            _register(client, "issue11-summary")
            _start_study(client, _VERIFY_TEXT, "issue11-summary-1")
            generation_helpers["drive"](app)

            rows = _verification_rows(app)
            assert len(rows) == 1
            assert rows[0]["status"] == "insufficient"
            sources = json.loads(rows[0]["sources_json"])
            assert sources[0]["read_scope"] == "abstract"
            assert sources[0]["adoption"] == "summary_only"
            assert json.loads(rows[0]["adopted_source_ids_json"]) == []
            # 摘要不冒充全文：模型合同明确只能作摘要级判断。
            assert "仅搜索摘要" in str(text.payloads[-1])
    finally:
        _close(app)


# ---------------------------------------------------------------------------
# 用户限制与恢复
# ---------------------------------------------------------------------------


def test_user_network_restriction_is_not_bypassed(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _TextTutorAdapter("按你的要求，这轮不联网核实。")
    app.state.chat_service._gateway = _gateway_with_verification(text, _PlanAdapter())
    search = _RecordingSearchClient()
    _install_web_search(app, search)

    try:
        with TestClient(app) as client:
            _register(client, "issue11-blocked")
            conversation_id = _start_study(
                client,
                "不要联网，帮我核实一下最新的汇率数据。",
                "issue11-blocked-1",
            )
            generation_helpers["drive"](app)

            assert search.queries == []
            detail = client.get(f"/chat/conversations/{conversation_id}").json()
            assert detail["messages"][-1]["web_search"] is None
            rows = _verification_rows(app)
            assert len(rows) == 1
            assert rows[0]["status"] == "blocked"
            assert rows[0]["can_retry"] == 0
            assert "公开查证" in rows[0]["gap_note"]
    finally:
        _close(app)


# ---------------------------------------------------------------------------
# 重试复用：不重复外呼、不覆盖有效状态
# ---------------------------------------------------------------------------


def test_retry_reuses_frozen_sources_without_new_external_calls(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _TextTutorAdapter("根据公开来源回答。")
    plan = _PlanAdapter()
    app.state.chat_service._gateway = _gateway_with_verification(text, plan)
    search = _RecordingSearchClient()
    _install_web_search(app, search)

    try:
        with TestClient(app) as client:
            _register(client, "issue11-retry")
            conversation_id = _start_study(client, _VERIFY_TEXT, "issue11-retry-1")
            generation_helpers["drive"](app)

            detail = client.get(f"/chat/conversations/{conversation_id}").json()
            first_message_id = detail["messages"][-1]["message_id"]
            calls_before = len(search.queries)
            assert calls_before >= 1

            retried = client.post(
                f"/chat/conversations/{conversation_id}/messages/{first_message_id}/retry"
            )
            assert retried.status_code == 200, retried.text
            generation_helpers["drive"](app)

            # 重试不重复外呼：沿用已冻结的有效搜索产物。
            assert len(search.queries) == calls_before
            rows = _verification_rows(app)
            assert len(rows) == 2
            assert all(row["status"] == "success" for row in rows)
            # 历史记录的来源与读取含义不被重写。
            first_row = next(row for row in rows if row["assistant_message_id"] == first_message_id)
            assert json.loads(first_row["sources_json"])[0]["read_scope"] == "full_text"
            assert json.loads(rows[0]["sources_json"]) == json.loads(rows[1]["sources_json"])
    finally:
        _close(app)


# ---------------------------------------------------------------------------
# 停止与预算耗尽：不提交、不外呼
# ---------------------------------------------------------------------------


def test_stopped_turn_commits_no_verification_record(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _TextTutorAdapter("好的，我先讲基础概念。")
    app.state.chat_service._gateway = _gateway_with_verification(text, _PlanAdapter())
    search = _RecordingSearchClient()
    _install_web_search(app, search)

    try:
        with TestClient(app) as client:
            _register(client, "issue11-stop")
            conversation_id = _start_study(client, "互斥是什么？", "issue11-stop-1")
            generation_helpers["drive"](app)

            text.answer = f"正在逐字生成的回答。{_Q1}" * 20
            text.slow = True
            stop_executor, executor_thread = generation_helpers["executor_thread"](app)
            control_client = TestClient(app)
            control_client.cookies.set("bridges_session", client.cookies.get("bridges_session"))
            try:
                sent = client.post(
                    f"/chat/conversations/{conversation_id}/messages",
                    json={"content": _VERIFY_TEXT, "idempotency_key": "issue11-stop-2"},
                )
                assert sent.status_code == 200, sent.text
                message_id = sent.json()["assistant_message"]["message_id"]
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    message = control_client.get(f"/chat/conversations/{conversation_id}").json()[
                        "messages"
                    ][-1]
                    if message["content"]:
                        break
                    time.sleep(0.02)
                stopped = control_client.post(
                    f"/chat/conversations/{conversation_id}/messages/{message_id}/stop"
                )
                assert stopped.status_code == 200, stopped.text
                assert stopped.json()["message"]["status"] == "stopped"
            finally:
                stop_executor.set()
                executor_thread.join(timeout=5)
                control_client.close()

            # 停止的轮次不提交查证记录；搜索外呼即使已发生也不落库。
            assert _verification_rows(app) == []
    finally:
        _close(app)


def test_exhausted_budget_skips_search_and_records_gap(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _TextTutorAdapter("按未核实的一轮如实回答。")
    app.state.chat_service._gateway = _gateway_with_verification(text, _PlanAdapter())
    search = _RecordingSearchClient()
    _install_web_search(app, search)

    try:
        with TestClient(app) as client:
            _register(client, "issue11-budget")
            database = app.state.bridges_database
            first = client.post(
                "/chat/first-turn",
                json={
                    "mode": "study",
                    "content": _VERIFY_TEXT,
                    "idempotency_key": "issue11-budget-1",
                },
            )
            assert first.status_code == 201, first.text
            run_id = first.json()["run_id"]
            deadline = (datetime.now(UTC) + timedelta(milliseconds=1)).isoformat()
            with database.transaction():
                database.connection.execute(
                    "UPDATE run_budget_ledger SET total_budget_ms = ?, deadline_at = ?"
                    " WHERE run_id = ?",
                    (1, deadline, run_id),
                )
            generation_helpers["drive"](app)

            # 预算耗尽：不进入公开搜索、不外呼；快照如实保留缺口。
            assert search.queries == []
            config_row = database.connection.execute(
                "SELECT config_json FROM generation_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            config = json.loads(config_row["config_json"] or "{}")
            snapshot = config.get("study_source_verification")
            assert isinstance(snapshot, dict)
            assert snapshot["status"] == "error"
            assert "预算" in snapshot["gap_note"]
    finally:
        _close(app)
