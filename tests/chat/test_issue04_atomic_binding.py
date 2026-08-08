"""Issue 04 反馈环：消息与附件绑定的原子性故障注入。

真实 HTTP + 真实 SQLite（同一请求内完成创建与绑定）。在绑定前（消息
INSERT 失败）与绑定后（COMMIT 失败）注入故障，断言消息、运行与附件
绑定要么同时提交、要么同时回滚——绝不出现「消息提交成功但附件仍未
绑定」或「绑定成功但消息缺失」的中间成功态。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from bridges.api.main import create_app
from bridges.config import get_settings


def _app(tmp_path: Path, monkeypatch: Any) -> Any:
    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "issue04-binding-test-secret")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    return create_app()


def _register(client: TestClient, tag: str) -> dict[str, Any]:
    response = client.post(
        "/auth/register",
        json={
            "username": f"{tag}_user",
            "qq_email": f"987654{len(tag):02d}@qq.com",
            "password": "Passw0rd123!",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["account"]


def _create_conversation(client: TestClient) -> str:
    response = client.post("/chat/conversations", json={})
    assert response.status_code == 201, response.text
    return response.json()["conversation_id"]


def _upload(client: TestClient, conversation_id: str) -> str:
    response = client.post(
        f"/chat/conversations/{conversation_id}/attachments",
        headers={
            "X-Bridges-Filename": "issue04-notes.txt",
            "X-Bridges-Upload-Id": "issue04-upload-1",
        },
        content="Issue 04 原子绑定测试正文（仅测试内容）。".encode(),
    )
    assert response.status_code == 201, response.text
    return response.json()["object_id"]


def _send(client: TestClient, conversation_id: str, object_id: str) -> Any:
    return client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": "带附件的消息", "attachment_ids": [object_id]},
    )


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


def _install_fault(
    app: Any, fail_on: Callable[[str], bool]
) -> tuple[Any, _FaultConnection]:
    database = app.state.bridges_database
    proxy = _FaultConnection(database._connection, fail_on)  # noqa: SLF001 - 测试直连
    database._connection = proxy  # noqa: SLF001
    return database, proxy


def _row_counts(database: Any, conversation_id: str) -> dict[str, int]:
    return {
        "messages": database.connection.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()["n"],
        "runs": database.connection.execute(
            "SELECT COUNT(*) AS n FROM generation_runs"
            " WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()["n"],
    }


def _attachment_state(database: Any, object_id: str) -> tuple[str | None, str]:
    row = database.connection.execute(
        "SELECT message_id, status FROM chat_attachments WHERE object_id = ?",
        (object_id,),
    ).fetchone()
    assert row is not None, "附件绑定行必须存在（上传已成功）"
    return row["message_id"], row["status"]


# ---------------------------------------------------------------------------
# 绑定前故障：消息 INSERT 失败 → 消息、运行与绑定全部回滚
# ---------------------------------------------------------------------------

def test_failure_before_binding_rolls_back_message_run_and_attachment(
    tmp_path: Path, monkeypatch: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    # 故障注入使服务端抛 StorageError：TestClient 不代为抛出，按真实
    # 客户端视角收到 500 响应。
    with TestClient(app, raise_server_exceptions=False) as client:
        _register(client, "pre")
        conversation_id = _create_conversation(client)
        object_id = _upload(client, conversation_id)
        database, _ = _install_fault(
            app,
            lambda sql: sql.lstrip().startswith("INSERT INTO messages"),
        )

        response = _send(client, conversation_id, object_id)
        assert response.status_code == 500, response.text

        # 事务整体回滚：没有消息、没有运行，附件仍保持「已上传未绑定」。
        assert _row_counts(database, conversation_id) == {"messages": 0, "runs": 0}
        message_id, status = _attachment_state(database, object_id)
        assert message_id is None
        assert status == "uploaded"


# ---------------------------------------------------------------------------
# 绑定后故障：COMMIT 失败 → 已执行的绑定与消息一起回滚
# ---------------------------------------------------------------------------

def test_failure_after_binding_rolls_back_message_and_binding(
    tmp_path: Path, monkeypatch: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    with TestClient(app, raise_server_exceptions=False) as client:
        _register(client, "post")
        conversation_id = _create_conversation(client)
        object_id = _upload(client, conversation_id)
        database, proxy = _install_fault(
            app, lambda sql: sql.strip().upper() == "COMMIT"
        )

        response = _send(client, conversation_id, object_id)
        assert response.status_code == 500, response.text
        # 故障使事务保持打开（COMMIT 未执行）：显式回滚后事务内全部语句
        # （含已执行的绑定 UPDATE）一并撤销，不留半状态。
        proxy._real.execute("ROLLBACK")  # noqa: SLF001

        assert _row_counts(database, conversation_id) == {"messages": 0, "runs": 0}
        message_id, status = _attachment_state(database, object_id)
        assert message_id is None
        assert status == "uploaded"


# ---------------------------------------------------------------------------
# 绑定语句本身失败：绑定 UPDATE 抛错 → 消息、运行与绑定全部回滚
# ---------------------------------------------------------------------------

def test_failure_at_binding_statement_rolls_back_message_and_attachment(
    tmp_path: Path, monkeypatch: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    with TestClient(app, raise_server_exceptions=False) as client:
        _register(client, "bind")
        conversation_id = _create_conversation(client)
        object_id = _upload(client, conversation_id)
        database, _ = _install_fault(
            app,
            lambda sql: sql.lstrip().startswith("UPDATE chat_attachments"),
        )

        response = _send(client, conversation_id, object_id)
        assert response.status_code == 500, response.text

        assert _row_counts(database, conversation_id) == {"messages": 0, "runs": 0}
        message_id, status = _attachment_state(database, object_id)
        assert message_id is None
        assert status == "uploaded"


# ---------------------------------------------------------------------------
# 正向对照：无故障时消息与绑定同时提交，不存在中间成功态
# ---------------------------------------------------------------------------

def test_binding_commits_together_with_message(tmp_path: Path, monkeypatch: Any) -> None:
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        _register(client, "ok")
        conversation_id = _create_conversation(client)
        object_id = _upload(client, conversation_id)

        response = _send(client, conversation_id, object_id)
        assert response.status_code == 200, response.text

        database = app.state.bridges_database
        assert _row_counts(database, conversation_id)["messages"] == 2  # 用户+助手占位
        message_id, status = _attachment_state(database, object_id)
        assert message_id is not None
        assert status == "bound"
