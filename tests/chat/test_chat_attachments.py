"""V2 Issue 05：聊天附件草稿的安全上传边界与历史附件读取兼容。

V2 中发送前的附件是账户级草稿（``/chat/attachment-drafts``），本轮仅
接受照片类型；已绑定附件继续经会话下载端点读取（历史消息只读兼容）。
文件类型（PDF/Office 等）自 V2 Issue 06 接入。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi.testclient import TestClient

from bridges.api.main import create_app
from bridges.chat.attachments import sniff_media_type
from bridges.config import get_settings

PNG_BYTES = b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR-restart-persistent"


def _app(tmp_path: Path, monkeypatch: Any) -> Any:
    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "attachment-test-secret-key")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    return create_app()


def _register(client: TestClient, tag: str = "attachment") -> dict[str, Any]:
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


def _parse_sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for block in text.split("\n\n"):
        event_name = None
        data: list[str] = []
        for line in block.split("\n"):
            if line.startswith("event:"):
                event_name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data.append(line[len("data:") :].strip())
        if event_name and data:
            events.append((event_name, json.loads("\n".join(data))))
    return events


def _start_conversation(client: TestClient, content: str = "先聊两句") -> str:
    response = client.post(
        "/chat/first-turn",
        json={
            "content": content,
            "idempotency_key": f"first-{content[:8]}-{id(client) % 10 ** 8}",
        },
    )
    assert response.status_code == 201, response.text
    client.app.state.generation_executor.run_tick()
    return response.json()["conversation"]["conversation_id"]


def _upload_draft(
    client: TestClient,
    filename: str,
    content: bytes,
    *,
    upload_id: str = "upload-1",
) -> Any:
    return client.post(
        "/chat/attachment-drafts",
        content=content,
        headers={
            "X-Bridges-Filename": quote(filename, safe=""),
            "X-Bridges-Upload-Id": upload_id,
        },
    )


def test_upload_uses_content_sniffing_and_retries_idempotently(
    tmp_path: Path, monkeypatch: Any
) -> None:
    client = TestClient(_app(tmp_path, monkeypatch))
    _register(client)

    uploaded = _upload_draft(client, "书页1.png", PNG_BYTES, upload_id="upload-1")
    assert uploaded.status_code == 201, uploaded.text
    projection = uploaded.json()
    assert projection["original_filename"] == "书页1.png"
    assert projection["media_type"] == "image/png"
    assert projection["content_length"] == len(PNG_BYTES)
    assert projection["content_hash"]
    assert str(tmp_path) not in uploaded.text

    retry = _upload_draft(client, "书页1.png", PNG_BYTES, upload_id="upload-1")
    assert retry.status_code == 200, retry.text
    assert retry.json()["object_id"] == projection["object_id"]

    spoofed = _upload_draft(
        client, "伪装.png", b"this is not a PNG", upload_id="upload-spoofed"
    )
    assert spoofed.status_code == 400, spoofed.text
    assert "文件类型" in spoofed.json()["detail"]["message"]

    traversal = _upload_draft(
        client, "..\\secret.png", PNG_BYTES, upload_id="upload-traversal"
    )
    assert traversal.status_code == 400, traversal.text
    assert "文件名" in traversal.json()["detail"]["message"]


def test_sniff_accepts_markdown_extension() -> None:
    content = "# 标题\n\n正文内容。\n".encode()
    assert sniff_media_type("笔记.markdown", content) == "text/markdown"
    assert sniff_media_type("笔记.md", content) == "text/markdown"


def test_bound_attachment_downloads_and_is_isolated(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """绑定后的照片经会话下载端点读取；跨账户不可见、不可绑定。"""
    app = _app(tmp_path, monkeypatch)
    alice_client = TestClient(app)
    _register(alice_client, "alice_attachment")

    uploaded = _upload_draft(
        alice_client, "photo.png", PNG_BYTES, upload_id="bind-upload"
    )
    assert uploaded.status_code == 201, uploaded.text
    object_id = uploaded.json()["object_id"]
    conversation_id = _start_conversation(alice_client)

    sent = alice_client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": "请看这张照片", "attachment_ids": [object_id]},
    )
    assert sent.status_code == 200, sent.text
    created = sent.json()
    assert created["run_id"]
    app.state.generation_executor.run_tick()
    with alice_client.stream(
        "GET",
        f"/chat/conversations/{conversation_id}/messages/"
        f"{created['assistant_message']['message_id']}/events",
        params={"cursor": 0},
    ) as stream:
        assert any(name == "done" for name, _ in _parse_sse("\n".join(stream.iter_lines())))

    history = alice_client.get(f"/chat/conversations/{conversation_id}")
    assert history.status_code == 200, history.text
    user_message = [
        item for item in history.json()["messages"] if item["role"] == "user"
    ][-1]
    attachment = user_message["attachments"][0]
    assert attachment["object_id"] == object_id
    assert attachment["message_id"] == user_message["message_id"]
    assert attachment["ordinal"] == 1

    download = alice_client.get(
        f"/chat/conversations/{conversation_id}/attachments/{object_id}/download"
    )
    assert download.status_code == 200, download.text
    assert download.content == PNG_BYTES
    assert download.headers["content-type"] == "image/png"
    assert str(tmp_path) not in download.headers.get("content-disposition", "")

    bob_client = TestClient(app)
    _register(bob_client, "bob_attachment")
    bob_conversation_id = _start_conversation(bob_client, "bob 的会话")
    assert (
        bob_client.get(
            f"/chat/conversations/{conversation_id}/attachments/{object_id}/download"
        ).status_code
        == 404
    )
    cross_account_bind = bob_client.post(
        f"/chat/conversations/{bob_conversation_id}/messages",
        json={"content": "不应绑定别人的附件", "attachment_ids": [object_id]},
    )
    assert cross_account_bind.status_code == 404, cross_account_bind.text


def test_draft_survives_app_restart(tmp_path: Path, monkeypatch: Any) -> None:
    """上传草稿 → 重启应用 → 草稿列表与内容可恢复（页面重开场景）。"""
    first_app = _app(tmp_path, monkeypatch)
    first_client = TestClient(first_app)
    _register(first_client, "restart_draft")
    uploaded = _upload_draft(
        first_client, "restart.png", PNG_BYTES, upload_id="restart-upload"
    )
    assert uploaded.status_code == 201, uploaded.text
    object_id = uploaded.json()["object_id"]
    session = first_client.cookies.get("bridges_session")
    assert session

    second_app = _app(tmp_path, monkeypatch)
    second_client = TestClient(second_app)
    second_client.cookies.set("bridges_session", session)
    drafts = second_client.get("/chat/attachment-drafts")
    assert drafts.status_code == 200, drafts.text
    assert [item["object_id"] for item in drafts.json()] == [object_id]
    download = second_client.get(f"/chat/attachment-drafts/{object_id}/content")
    assert download.status_code == 200, download.text
    assert download.content == PNG_BYTES
