"""Issue 04：附件契约一致性、未绑定草稿恢复与孤儿清理规则。

覆盖实施步骤 3/4/6 的服务端语义：
- 技能合同引用的附件必须与消息绑定集合一致，不一致返回可理解错误，
  绝不静默回退到知识库或其他材料；
- 已上传未绑定附件可经列表接口恢复（草稿），跨账户不泄漏；
- 超过安全期限的孤儿被清理，期限内的保留，已绑定对象绝不误删；
- 数据库约束（v32 触发器）杜绝「bound 但无 message_id」的写入。
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
            "qq_email": f"987654{sum(bytearray(tag.encode('utf-8'))):08d}@qq.com",
            "password": "Passw0rd123!",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["account"]


def _create_conversation(client: TestClient) -> str:
    response = client.post("/chat/conversations", json={})
    assert response.status_code == 201, response.text
    return response.json()["conversation_id"]


def _upload(client: TestClient, conversation_id: str, tag: str = "issue04") -> str:
    response = client.post(
        f"/chat/conversations/{conversation_id}/attachments",
        headers={
            "X-Bridges-Filename": f"{tag}-notes.txt",
            "X-Bridges-Upload-Id": f"{tag}-upload-1",
        },
        content="Issue 04 附件契约测试正文（仅测试内容）。".encode(),
    )
    assert response.status_code == 201, response.text
    return response.json()["object_id"]


# ---------------------------------------------------------------------------
# 技能合同附件一致性
# ---------------------------------------------------------------------------

def test_skill_contract_mismatch_is_rejected_without_fallback(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """合同引用附件但消息绑定为空 → 422 可理解错误，不创建任何消息。"""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        _register(client, "mism")
        conversation_id = _create_conversation(client)
        object_id = _upload(client, conversation_id)

        response = client.post(
            f"/chat/conversations/{conversation_id}/messages",
            json={
                "content": "文章人味化（科普文案）：改写《测试》",
                "attachment_ids": [],
                "skill_id": "bridges-humanizer",
                "skill_input": {
                    "skill_id": "bridges-humanizer",
                    "contract": {
                        "path": "rewrite",
                        "genre": "popular_science",
                        "attachment_ids": [object_id],
                    },
                },
            },
        )
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["error"] == "attachment_contract_mismatch"
        # 没有静默降级：不产生消息、附件仍可重新绑定。
        database = app.state.bridges_database
        count = database.connection.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()["n"]
        assert count == 0


def test_skill_contract_matches_message_binding_succeeds(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """合同引用与消息绑定一致 → 正常创建并绑定。"""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        _register(client, "match")
        conversation_id = _create_conversation(client)
        object_id = _upload(client, conversation_id)

        response = client.post(
            f"/chat/conversations/{conversation_id}/messages",
            json={
                "content": "文章人味化（科普文案）：改写《测试》",
                "attachment_ids": [object_id],
                "skill_id": "bridges-humanizer",
                "skill_input": {
                    "skill_id": "bridges-humanizer",
                    "contract": {
                        "path": "rewrite",
                        "genre": "popular_science",
                        "attachment_ids": [object_id],
                    },
                },
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["user_message"]["attachments"][0]["object_id"] == object_id
        assert body["user_message"]["attachments"][0]["status"] == "bound"


def test_attachment_cannot_be_bound_to_a_second_message(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """双击/网络重试不会把同一附件绑定到两条消息：已绑定后再次发送 404。"""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        _register(client, "twice")
        conversation_id = _create_conversation(client)
        object_id = _upload(client, conversation_id)

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


# ---------------------------------------------------------------------------
# 未绑定草稿恢复（列表接口）
# ---------------------------------------------------------------------------

def test_unbound_attachments_listed_for_recovery_and_isolated(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """上传未发送 → 列表可见可恢复；跨账户/跨会话返回空集不泄漏。"""
    app = _app(tmp_path, monkeypatch)
    alice_client = TestClient(app)
    _register(alice_client, "reca")
    conversation_a = _create_conversation(alice_client)
    conversation_b = _create_conversation(alice_client)
    object_id = _upload(alice_client, conversation_a)

    listing = alice_client.get(f"/chat/conversations/{conversation_a}/attachments")
    assert listing.status_code == 200, listing.text
    items = listing.json()
    assert [item["object_id"] for item in items] == [object_id]
    assert items[0]["status"] == "uploaded"
    assert items[0]["message_id"] is None

    # 跨会话：同一账户的其他会话不可见（不泄漏存在性）。
    other = alice_client.get(f"/chat/conversations/{conversation_b}/attachments")
    assert other.status_code == 200
    assert other.json() == []

    # 跨账户：Bob 看不到 Alice 的未绑定附件。
    bob_client = TestClient(app)
    _register(bob_client, "recb")
    bob_listing = bob_client.get(f"/chat/conversations/{conversation_a}/attachments")
    assert bob_listing.status_code == 200
    assert bob_listing.json() == []


# ---------------------------------------------------------------------------
# 孤儿清理规则
# ---------------------------------------------------------------------------

def test_sweep_unbound_respects_ttl_and_never_touches_bound(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """超过期限的孤儿被清理；期限内的保留；已绑定对象绝不误删。"""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        _register(client, "sweep")
        conversation_id = _create_conversation(client)
        stale_id = _upload(client, conversation_id, "stale")
        fresh_id = _upload(client, conversation_id, "fresh")
        bound_id = _upload(client, conversation_id, "bound")

        # 发送一条消息绑定 bound_id。
        response = client.post(
            f"/chat/conversations/{conversation_id}/messages",
            json={"content": "绑定附件", "attachment_ids": [bound_id]},
        )
        assert response.status_code == 200, response.text

        database = app.state.bridges_database
        service = app.state.chat_attachment_service
        # 把 stale 的 updated_at 回拨到安全期限之外。
        database.connection.execute(
            "UPDATE chat_attachments SET updated_at = ? WHERE object_id = ?",
            (
                (datetime.now(UTC) - timedelta(days=8)).isoformat(),
                stale_id,
            ),
        )

        swept = service.sweep_unbound(datetime.now(UTC) - timedelta(hours=24 * 7))
        assert swept == 1

        remaining = database.connection.execute(
            "SELECT object_id, status, message_id FROM chat_attachments"
            " ORDER BY object_id"
        ).fetchall()
        ids = {str(row["object_id"]) for row in remaining}
        assert ids == {fresh_id, bound_id}  # 期限内的保留，已绑定的保留
        bound = next(row for row in remaining if str(row["object_id"]) == bound_id)
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
        conversation_id = _create_conversation(client)
        object_id = _upload(client, conversation_id)

        database = app.state.bridges_database
        with database.transaction(), pytest.raises(sqlite3.IntegrityError):
            database.connection.execute(
                "UPDATE chat_attachments SET status = 'bound'"
                " WHERE object_id = ?",
                (object_id,),
            )
        # 行未被污染：仍是 uploaded 且无 message_id。
        row = database.connection.execute(
            "SELECT status, message_id FROM chat_attachments WHERE object_id = ?",
            (object_id,),
        ).fetchone()
        assert row["status"] == "uploaded"
        assert row["message_id"] is None
