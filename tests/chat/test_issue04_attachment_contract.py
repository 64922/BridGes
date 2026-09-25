"""附件契约一致性、草稿恢复与孤儿清理规则（V2 Issue 05 更新）。

原 Issue 04 的会话级草稿已退役为账户级草稿域（``chat_attachment_drafts``）；
本文件沿守同样的服务端语义：
- 已上传未发送草稿可经列表接口恢复，跨账户不泄漏；
- 超过安全期限的孤儿被清理，期限内的保留，已绑定对象绝不误删；
- 数据库约束（v32 触发器）杜绝「bound 但无 message_id」的写入；
- 附件集合校验（不存在/越权）在发送前给出可理解错误。
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.api.main import create_app
from bridges.config import get_settings

PNG_BYTES = b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR-contract-test"


def _app(tmp_path: Path, monkeypatch: Any) -> Any:
    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "issue04-contract-test-secret")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    return create_app()


def _register(client: TestClient, tag: str) -> dict[str, Any]:
    response = client.post(
        "/auth/register",
        json={
            "username": f"{tag}_user",
            "qq_email": f"987654{sum(bytearray(tag.encode('utf-8'))) % 10 ** 8:08d}@qq.com",
            "password": "Passw0rd123!",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["account"]


def _start_conversation(client: TestClient, app: Any, content: str = "先聊两句") -> str:
    response = client.post(
        "/chat/first-turn",
        json={
            "content": content,
            "idempotency_key": f"first-{content[:8]}-{id(client) % 10 ** 8}",
        },
    )
    assert response.status_code == 201, response.text
    app.state.generation_executor.run_tick()
    return response.json()["conversation"]["conversation_id"]


def _upload(
    client: TestClient, filename: str = "notes.png", upload_id: str = "upload-1"
) -> str:
    from urllib.parse import quote

    response = client.post(
        "/chat/attachment-drafts",
        headers={
            "X-Bridges-Filename": quote(filename),
            "X-Bridges-Upload-Id": upload_id,
        },
        content=PNG_BYTES,
    )
    assert response.status_code == 201, response.text
    return response.json()["object_id"]


# ---------------------------------------------------------------------------
# 草稿恢复（列表接口，账户级）
# ---------------------------------------------------------------------------

def test_drafts_listed_for_recovery_and_isolated(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """上传未发送 → 列表可见可恢复；跨账户返回空集不泄漏。"""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        _register(client, "reca")
        object_id = _upload(client, upload_id="upload-a")

        listing = client.get("/chat/attachment-drafts")
        assert listing.status_code == 200, listing.text
        items = listing.json()
        assert [item["object_id"] for item in items] == [object_id]

    with TestClient(app) as client:
        _register(client, "recb")
        bob_listing = client.get("/chat/attachment-drafts")
        assert bob_listing.status_code == 200
        assert bob_listing.json() == []


# ---------------------------------------------------------------------------
# 孤儿清理规则（草稿域 + 旧未绑定行）
# ---------------------------------------------------------------------------

def test_sweep_respects_ttl_and_never_touches_bound(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """超过期限的草稿被清理；期限内的保留；已绑定对象绝不误删。"""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        _register(client, "sweep")
        stale_id = _upload(client, "stale.png", "upload-stale")
        fresh_id = _upload(client, "fresh.png", "upload-fresh")
        bound_id = _upload(client, "bound.png", "upload-bound")
        conversation_id = _start_conversation(client, app)

        response = client.post(
            f"/chat/conversations/{conversation_id}/messages",
            json={"content": "绑定附件", "attachment_ids": [bound_id]},
        )
        assert response.status_code == 200, response.text
        app.state.generation_executor.run_tick()

        database = app.state.bridges_database
        database.connection.execute(
            "UPDATE chat_attachment_drafts SET updated_at = ? WHERE object_id = ?",
            (
                (datetime.now(UTC) - timedelta(days=8)).isoformat(),
                stale_id,
            ),
        )

        service = app.state.chat_attachment_service
        swept = service.sweep_drafts(datetime.now(UTC) - timedelta(hours=24 * 7))
        assert swept == 1

        remaining = database.connection.execute(
            "SELECT object_id FROM chat_attachment_drafts"
        ).fetchall()
        assert {str(row["object_id"]) for row in remaining} == {fresh_id}
        bound = database.connection.execute(
            "SELECT status, message_id FROM chat_attachments WHERE object_id = ?",
            (bound_id,),
        ).fetchone()
        assert bound is not None
        assert bound["status"] == "bound"
        assert bound["message_id"] is not None


# ---------------------------------------------------------------------------
# 数据库约束（v32）：bound 必须携带 message_id
# ---------------------------------------------------------------------------

def test_database_rejects_bound_without_message_id(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """v32 触发器：status='bound' 但 message_id 为空 → 写入被拒绝。"""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        _register(client, "trig")
        object_id = _upload(client, upload_id="upload-trig")

        database = app.state.bridges_database
        with database.transaction(), pytest.raises(sqlite3.IntegrityError):
            database.connection.execute(
                "INSERT INTO chat_attachments"
                " (object_id, account_id, conversation_id, message_id, upload_id,"
                "  media_type, status, created_at, updated_at)"
                " VALUES (?, 'x', 'x', NULL, 'upload-x', 'image/png', 'bound',"
                "  '2026-09-25T00:00:00+00:00', '2026-09-25T00:00:00+00:00')",
                (object_id,),
            )


# ---------------------------------------------------------------------------
# 发送集合校验：未知附件 404，不静默降级
# ---------------------------------------------------------------------------

def test_send_with_unknown_attachment_rejected_without_partial_bind(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """合同引用未知附件 → 404，不创建消息、已有草稿保持可绑定。"""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        _register(client, "mism")
        object_id = _upload(client, upload_id="upload-mism")
        conversation_id = _start_conversation(client, app)

        response = client.post(
            f"/chat/conversations/{conversation_id}/messages",
            json={"content": "带附件", "attachment_ids": [object_id, "ghost-id"]},
        )
        assert response.status_code == 404, response.text
        assert response.json()["detail"]["error"] == "attachment_not_found"
        # 没有静默降级：不产生消息，草稿仍可重新绑定。
        database = app.state.bridges_database
        count = database.connection.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()["n"]
        assert count == 2  # 首轮的用户+助手占位，无新消息
        draft_rows = database.connection.execute(
            "SELECT COUNT(*) AS n FROM chat_attachment_drafts WHERE object_id = ?",
            (object_id,),
        ).fetchone()["n"]
        assert draft_rows == 1


def test_attachment_cannot_be_bound_to_a_second_message(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """双击/网络重试不会把同一附件绑定到两条消息：已绑定后再次发送 404。"""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        _register(client, "twice")
        object_id = _upload(client, upload_id="upload-twice")
        conversation_id = _start_conversation(client, app)

        first = client.post(
            f"/chat/conversations/{conversation_id}/messages",
            json={"content": "第一次发送", "attachment_ids": [object_id]},
        )
        assert first.status_code == 200, first.text
        assert (
            first.json()["user_message"]["attachments"][0]["object_id"] == object_id
        )
        # 收敛第一轮（确定性替身秒回），让附件校验成为第二次发送的门。
        app.state.generation_executor.run_tick()

        # 第二次发送同一附件：服务端拒绝（不产生第二条绑定），且消息未创建。
        second = client.post(
            f"/chat/conversations/{conversation_id}/messages",
            json={"content": "重复发送", "attachment_ids": [object_id]},
        )
        assert second.status_code == 404, second.text
        assert second.json()["detail"]["error"] == "attachment_not_found"

        database = app.state.bridges_database
        bound_rows = database.connection.execute(
            "SELECT COUNT(*) AS n FROM chat_attachments"
            " WHERE object_id = ? AND message_id IS NOT NULL",
            (object_id,),
        ).fetchone()["n"]
        assert bound_rows == 1  # 恰好绑定一条消息
