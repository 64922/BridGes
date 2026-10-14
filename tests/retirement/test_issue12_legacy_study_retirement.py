"""工单 12：旧学习只读退出与主动续学（迁移 v79）。

固定两件事的对外行为：

- 旧五阶段学习会话只读：发送、首轮预建、重试与后台执行全部收敛为
  稳定 410/error（``study_legacy_retired``），历史消息与材料仍可读取；
- 主动续学由用户发起：只列出仍可访问的旧材料与本人目标，采用材料为
  新对象并标注来源会话/来源对象，创建新模式会话；同键重放复用同一
  会话，失败不改变旧历史、不留下孤立草稿。
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from bridges.api.auth import SESSION_COOKIE_NAME
from tests.chat.test_issue08_resume_and_goal_switch import _gateway, _TutorAdapter

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
LEGACY_GRAPH_VERSION = "study-tutoring-review-v6"


@pytest.fixture
def sqlite_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    from bridges.api.main import create_app
    from bridges.config import get_settings

    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "issue12-test-secret-key")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    return create_app()


@pytest.fixture
def client(sqlite_app: Any) -> TestClient:
    return TestClient(sqlite_app)


def _register(client: TestClient, tag: str) -> str:
    response = client.post(
        "/auth/register",
        json={
            "username": f"legacy_study_{tag}",
            "qq_email": f"1357{sum(bytearray(tag.encode('utf-8'))) % 10 ** 8:08d}@qq.com",
            "password": "Passw0rd123!",
        },
    )
    assert response.status_code == 201, response.text
    token = client.cookies.get(SESSION_COOKIE_NAME)
    assert token is not None
    return str(
        client.app.state.identity_service.resolve_session(token).subject.account_id
    )


def _upload_draft(client: TestClient, name: str) -> str:
    response = client.post(
        "/chat/attachment-drafts",
        headers={
            "X-Bridges-Filename": quote(name),
            "X-Bridges-Upload-Id": f"upload-{secrets.token_urlsafe(8)}",
        },
        content=PNG_BYTES,
    )
    assert response.status_code == 201, response.text
    return str(response.json()["object_id"])


def _conversation_detail(
    client: TestClient, conversation_id: str
) -> dict[str, Any]:
    response = client.get(f"/chat/conversations/{conversation_id}")
    assert response.status_code == 200, response.text
    return response.json()


def _bound_materials(detail: dict[str, Any]) -> list[dict[str, Any]]:
    materials: list[dict[str, Any]] = []
    for message in detail["messages"]:
        materials.extend(message.get("attachments") or [])
    return materials


def _prepare_legacy_with_material(
    client: TestClient, app: Any, tag: str
) -> tuple[str, str, str]:
    """创建一个含绑定材料的旧流程学习会话；返回 (账户, 会话, 材料对象)。"""
    account_id = _register(client, tag)
    object_id = _upload_draft(client, "旧教材.png")
    first = client.post(
        "/chat/first-turn",
        json={
            "content": "开始一次学习",
            "mode": "study",
            "attachment_ids": [object_id],
            "idempotency_key": f"legacy-first-{tag}",
        },
    )
    assert first.status_code == 201, first.text
    conversation_id = str(first.json()["conversation"]["conversation_id"])
    with app.state.bridges_database.transaction():
        app.state.bridges_database.scoped(account_id).execute(
            "UPDATE generation_runs SET graph_version = ?"
            " WHERE conversation_id = ? AND account_id = ?",
            (LEGACY_GRAPH_VERSION, conversation_id, account_id),
        )
    return account_id, conversation_id, object_id


def _seed_goal(
    app: Any, account_id: str, statement: str = "掌握线性代数", status: str = "active"
) -> str:
    goal_id = secrets.token_urlsafe(16)
    now = datetime.now(UTC).isoformat()
    with app.state.bridges_database.transaction():
        app.state.bridges_database.scoped(account_id).execute(
            "INSERT INTO study_goals"
            " (goal_id, account_id, statement, observable_result, scope,"
            "  constraints, status, source_message_id, version, created_at, updated_at)"
            " VALUES (?, ?, ?, '', '', '', ?, 'seed-message', 1, ?, ?)",
            (goal_id, account_id, statement, status, now, now),
        )
    return goal_id


def _count(app: Any, table: str, where: str = "", params: tuple[Any, ...] = ()) -> int:
    sql = f"SELECT COUNT(*) AS n FROM {table}"
    if where:
        sql += f" WHERE {where}"
    row = app.state.bridges_database.connection.execute(sql, params).fetchone()
    assert row is not None
    return int(row["n"])


def _drive_all(app: Any, *, timeout: float = 10.0) -> None:
    """驱动后台执行器直到生成队列清空（多运行分批串行处理）。"""
    executor = app.state.generation_executor
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        executor.run_tick()
        if "无待处理运行" in executor._last_summary:  # noqa: SLF001 - 测试读摘要
            return
        time.sleep(0.02)
    raise AssertionError("执行器未在超时前清空生成队列。")


def test_legacy_study_sends_and_retries_are_refused(client: TestClient, sqlite_app: Any) -> None:
    _, conversation_id, _ = _prepare_legacy_with_material(client, sqlite_app, "refuse")

    sent = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": "继续旧流程"},
    )
    assert sent.status_code == 410, sent.text
    assert sent.json()["detail"]["error"] == "study_legacy_retired"

    first_turn = client.post(
        "/chat/first-turn",
        json={
            "content": "从旧会话继续",
            "mode": "study",
            "conversation_id": conversation_id,
            "idempotency_key": "legacy-confirm-first",
        },
    )
    assert first_turn.status_code == 410, first_turn.text
    assert first_turn.json()["detail"]["error"] == "study_legacy_retired"

    detail = _conversation_detail(client, conversation_id)
    assistant = next(
        message for message in detail["messages"] if message["role"] == "assistant"
    )
    retried = client.post(
        f"/chat/conversations/{conversation_id}/messages/{assistant['message_id']}/retry"
    )
    assert retried.status_code == 410, retried.text
    assert retried.json()["detail"]["error"] == "study_legacy_retired"

    # 历史只读不变：消息、绑定材料与旧运行都未被改写。
    detail = _conversation_detail(client, conversation_id)
    assert len(detail["messages"]) == 2
    materials = _bound_materials(detail)
    assert len(materials) == 1
    assert materials[0]["adopted_from_conversation_id"] == ""


def test_executor_converges_legacy_runs_without_executing_them(
    client: TestClient, sqlite_app: Any
) -> None:
    account_id, conversation_id, _ = _prepare_legacy_with_material(
        client, sqlite_app, "converge"
    )
    sqlite_app.state.generation_executor.run_tick()

    rows = sqlite_app.state.bridges_database.scoped(account_id).execute(
        "SELECT status, error_code FROM generation_runs"
        " WHERE conversation_id = ? AND account_id = ?",
        (conversation_id, account_id),
    ).fetchall()
    assert rows
    assert all(str(row["status"]) == "failed" for row in rows)
    assert all(str(row["error_code"]) == "study_legacy_retired" for row in rows)

    detail = _conversation_detail(client, conversation_id)
    assistants = [
        message for message in detail["messages"] if message["role"] == "assistant"
    ]
    assert all(message["status"] == "error" for message in assistants)
    assert all(
        message["error_code"] == "study_legacy_retired" for message in assistants
    )


def test_restart_options_list_only_accessible_materials(
    client: TestClient, sqlite_app: Any
) -> None:
    account_id, conversation_id, object_id = _prepare_legacy_with_material(
        client, sqlite_app, "options"
    )
    goal_id = _seed_goal(sqlite_app, account_id)

    options = client.get(
        f"/chat/conversations/{conversation_id}/study/restart-options"
    )
    assert options.status_code == 200, options.text
    body = options.json()
    assert body["can_restart"] is True
    assert [goal["goal_id"] for goal in body["goals"]] == [goal_id]
    assert [item["object_id"] for item in body["materials"]] == [object_id]
    assert body["materials"][0]["original_filename"] == "旧教材.png"

    # 非旧流程会话不提供续学选项；混淆目标时如实拒绝。
    other = client.post(
        "/chat/first-turn",
        json={"content": "普通对话", "idempotency_key": "options-other"},
    )
    assert other.status_code == 201, other.text
    other_id = other.json()["conversation"]["conversation_id"]
    refused = client.get(f"/chat/conversations/{other_id}/study/restart-options")
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["error"] == "study_restart_not_legacy"


def test_restart_adopts_material_with_provenance_and_replays(
    client: TestClient, sqlite_app: Any
) -> None:
    account_id, conversation_id, object_id = _prepare_legacy_with_material(
        client, sqlite_app, "adopt"
    )
    payload = {
        "idempotency_key": "restart-adopt-key",
        "object_ids": [object_id],
        "message": "接着这份旧教材继续",
    }
    created = client.post(
        f"/chat/conversations/{conversation_id}/study/restart", json=payload
    )
    assert created.status_code == 201, created.text
    first = created.json()
    new_id = first["conversation"]["conversation_id"]
    assert new_id != conversation_id
    assert first["idempotent_replay"] is False
    assert first["conversation"]["mode"] == "study"

    detail = _conversation_detail(client, new_id)
    materials = _bound_materials(detail)
    assert len(materials) == 1
    adopted = materials[0]
    assert adopted["object_id"] != object_id
    assert adopted["adopted_from_conversation_id"] == conversation_id
    assert adopted["adopted_from_object_id"] == object_id

    # 原会话不受影响：原材料仍绑定原消息、无来源标注、可读。
    source_detail = _conversation_detail(client, conversation_id)
    source_material = _bound_materials(source_detail)[0]
    assert source_material["object_id"] == object_id
    assert source_material["adopted_from_conversation_id"] == ""

    # 同键重放复用同一会话，不产生第二个会话，也不留下本次采用的草稿。
    replay = client.post(
        f"/chat/conversations/{conversation_id}/study/restart", json=payload
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["conversation"]["conversation_id"] == new_id
    assert replay.json()["idempotent_replay"] is True
    assert (
        _count(
            sqlite_app,
            "conversations",
            "account_id = ? AND mode = 'study'",
            (account_id,),
        )
        == 2
    )
    assert _count(sqlite_app, "chat_attachment_drafts", "account_id = ?", (account_id,)) == 0

    # 来源会话撤回后，新会话的材料仍可读取（副本+来源仅审计）。
    sqlite_app.state.generation_executor.run_tick()
    deleted = client.delete(f"/chat/conversations/{conversation_id}")
    assert deleted.status_code == 204, deleted.text
    downloaded = client.get(
        f"/chat/conversations/{new_id}/attachments/{adopted['object_id']}/download"
    )
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.content == PNG_BYTES


def test_restart_with_goal_links_goal_and_freezes_run_config(
    client: TestClient, sqlite_app: Any
) -> None:
    account_id, conversation_id, _ = _prepare_legacy_with_material(
        client, sqlite_app, "goal"
    )
    goal_id = _seed_goal(sqlite_app, account_id, status="paused")
    adapter = _TutorAdapter(answer="我们继续这个目标。")
    sqlite_app.state.chat_service._gateway = _gateway(adapter)  # noqa: SLF001

    response = client.post(
        f"/chat/conversations/{conversation_id}/study/restart",
        json={
            "idempotency_key": "restart-goal-key",
            "goal_id": goal_id,
            "message": "继续这个目标",
        },
    )
    assert response.status_code == 201, response.text
    new_id = response.json()["conversation"]["conversation_id"]

    run_row = sqlite_app.state.bridges_database.scoped(account_id).execute(
        "SELECT config_json FROM generation_runs"
        " WHERE conversation_id = ? AND account_id = ?",
        (new_id, account_id),
    ).fetchone()
    assert run_row is not None
    config = json.loads(str(run_row["config_json"]))
    assert config["study_resume_goal_id"] == goal_id

    # 用户已在入口选定目标：恢复是确定性的，不再调用规划角色。
    _drive_all(sqlite_app)
    assert adapter.plan_payloads == []

    link = sqlite_app.state.bridges_database.scoped(account_id).execute(
        "SELECT goal_id FROM study_conversation_goals"
        " WHERE conversation_id = ? AND account_id = ?",
        (new_id, account_id),
    ).fetchone()
    assert link is not None and str(link["goal_id"]) == goal_id
    goal_row = sqlite_app.state.bridges_database.scoped(account_id).execute(
        "SELECT status FROM study_goals WHERE goal_id = ? AND account_id = ?",
        (goal_id, account_id),
    ).fetchone()
    assert goal_row is not None and str(goal_row["status"]) == "active"


def test_restart_validation_refuses_incomplete_or_foreign_selections(
    client: TestClient, sqlite_app: Any
) -> None:
    account_id, conversation_id, object_id = _prepare_legacy_with_material(
        client, sqlite_app, "validate"
    )
    endpoint = f"/chat/conversations/{conversation_id}/study/restart"

    empty = client.post(endpoint, json={"idempotency_key": "restart-empty-key"})
    assert empty.status_code == 422, empty.text
    assert empty.json()["detail"]["error"] == "study_restart_empty_selection"

    valid_goal = _seed_goal(sqlite_app, account_id, statement="掌握概率论")
    goal_only = client.post(
        endpoint,
        json={"idempotency_key": "restart-goal-only", "goal_id": valid_goal},
    )
    assert goal_only.status_code == 422, goal_only.text
    assert goal_only.json()["detail"]["error"] == "study_restart_message_required"

    missing_goal = client.post(
        endpoint,
        json={"idempotency_key": "restart-missing-goal", "goal_id": "missing-goal"},
    )
    assert missing_goal.status_code == 422, missing_goal.text
    assert missing_goal.json()["detail"]["error"] == "study_restart_goal_unavailable"

    no_message = client.post(
        endpoint,
        json={"idempotency_key": "restart-no-message", "object_ids": [object_id]},
    )
    # 有材料时消息可为空（纯附件首轮），不触发 message_required。
    assert no_message.status_code == 201, no_message.text

    duplicate = client.post(
        endpoint,
        json={
            "idempotency_key": "restart-duplicate-key",
            "object_ids": [object_id, object_id],
            "message": "继续",
        },
    )
    assert duplicate.status_code == 422, duplicate.text
    assert duplicate.json()["detail"]["error"] == "study_restart_duplicate_material"

    missing = client.post(
        endpoint,
        json={
            "idempotency_key": "restart-missing-key",
            "object_ids": ["object-does-not-exist"],
            "message": "继续",
        },
    )
    assert missing.status_code == 404, missing.text
    assert missing.json()["detail"]["error"] == "attachment_not_found"

    # 他人账户的对象按不存在处理，不泄漏存在性。
    other_client = TestClient(sqlite_app)
    _register(other_client, "validate-other")
    foreign_object = _upload_draft(other_client, "他人材料.png")
    foreign = client.post(
        endpoint,
        json={
            "idempotency_key": "restart-foreign-key",
            "object_ids": [foreign_object],
            "message": "继续",
        },
    )
    assert foreign.status_code == 404, foreign.text
    assert foreign.json()["detail"]["error"] == "attachment_not_found"

    # 未绑定的草稿不是旧材料，不能采用。
    unbound = _upload_draft(client, "未绑定.png")
    unbound_attempt = client.post(
        endpoint,
        json={
            "idempotency_key": "restart-unbound-key",
            "object_ids": [unbound],
            "message": "继续",
        },
    )
    assert unbound_attempt.status_code == 404, unbound_attempt.text


class _FaultConnection:
    """故障注入连接代理：命中谓词的 SQL 抛 sqlite3 错误，其余原样转发。"""

    def __init__(
        self, real: sqlite3.Connection, fail_on: Callable[[str], bool]
    ) -> None:
        self._real = real
        self._fail_on = fail_on

    def execute(self, sql: str, params: Any = None) -> sqlite3.Cursor:
        if self._fail_on(sql):
            raise sqlite3.OperationalError("注入的绑定阶段故障")
        if params is None:
            return self._real.execute(sql)
        return self._real.execute(sql, params)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


def test_restart_binding_failure_leaves_no_orphans(sqlite_app: Any) -> None:
    # 故障注入使服务端抛 StorageError：按真实客户端视角收到 500 响应。
    client = TestClient(sqlite_app, raise_server_exceptions=False)
    account_id, conversation_id, object_id = _prepare_legacy_with_material(
        client, sqlite_app, "rollback"
    )
    objects_before = _count(sqlite_app, "objects", "account_id = ?", (account_id,))
    database = sqlite_app.state.bridges_database
    proxy = _FaultConnection(
        database._connection,  # noqa: SLF001 - 测试直连注入
        lambda sql: sql.lstrip().startswith("INSERT INTO chat_attachments"),
    )
    database._connection = proxy  # noqa: SLF001
    try:
        response = client.post(
            f"/chat/conversations/{conversation_id}/study/restart",
            json={
                "idempotency_key": "restart-rollback-key",
                "object_ids": [object_id],
                "message": "继续",
            },
        )
        assert response.status_code == 500, response.text
    finally:
        database._connection = proxy._real  # noqa: SLF001

    # 新会话整体回滚；本次采用的草稿已回收，克隆对象标记待清理。
    assert (
        _count(
            sqlite_app,
            "conversations",
            "account_id = ? AND mode = 'study'",
            (account_id,),
        )
        == 1
    )
    assert _count(sqlite_app, "chat_attachment_drafts", "account_id = ?", (account_id,)) == 0
    assert (
        _count(sqlite_app, "objects", "account_id = ?", (account_id,))
        == objects_before
    )
    # 旧历史不变。
    source_detail = _conversation_detail(client, conversation_id)
    assert len(_bound_materials(source_detail)) == 1
