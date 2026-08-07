"""Issue 16：聊天附件的安全上传边界。"""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zipfile import ZipFile

from fastapi.testclient import TestClient

from bridges.api.main import create_app
from bridges.config import get_settings


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


def _create_conversation(client: TestClient) -> str:
    response = client.post("/chat/conversations", json={})
    assert response.status_code == 201, response.text
    return response.json()["conversation_id"]


def _upload(
    client: TestClient,
    conversation_id: str,
    filename: str,
    content: bytes,
    *,
    upload_id: str = "upload-1",
    content_type: str = "application/octet-stream",
) -> Any:
    return client.post(
        f"/chat/conversations/{conversation_id}/attachments",
        content=content,
        headers={
            "Content-Type": content_type,
            "X-Bridges-Filename": quote(filename, safe=""),
            "X-Bridges-Upload-Id": upload_id,
        },
    )


def test_upload_uses_content_sniffing_and_retries_idempotently(
    tmp_path: Path, monkeypatch: Any
) -> None:
    client = TestClient(_app(tmp_path, monkeypatch))
    _register(client)
    conversation_id = _create_conversation(client)

    pdf = b"%PDF-1.7\nminimal test document"
    uploaded = _upload(
        client,
        conversation_id,
        "课程资料.pdf",
        pdf,
        content_type="application/pdf",
    )
    assert uploaded.status_code == 201, uploaded.text
    projection = uploaded.json()
    assert projection["original_filename"] == "课程资料.pdf"
    assert projection["media_type"] == "application/pdf"
    assert projection["content_length"] == len(pdf)
    assert projection["content_hash"]
    assert projection["message_id"] is None
    assert str(tmp_path) not in uploaded.text

    cancelled = _upload(
        client,
        conversation_id,
        "cancelled.pdf",
        pdf,
        upload_id="upload-cancelled",
        content_type="application/pdf",
    )
    assert cancelled.status_code == 201, cancelled.text
    cancelled_object_id = cancelled.json()["object_id"]
    cancel_response = client.delete(
        f"/chat/conversations/{conversation_id}/attachments/by-upload/upload-cancelled"
    )
    assert cancel_response.status_code == 204, cancel_response.text
    assert (
        client.get(
            f"/chat/conversations/{conversation_id}/attachments/{cancelled_object_id}/download"
        ).status_code
        == 404
    )
    pre_cancel = client.delete(
        f"/chat/conversations/{conversation_id}/attachments/by-upload/cancel-before-upload"
    )
    assert pre_cancel.status_code == 204, pre_cancel.text
    late_upload = _upload(
        client,
        conversation_id,
        "late-cancel.pdf",
        pdf,
        upload_id="cancel-before-upload",
        content_type="application/pdf",
    )
    assert late_upload.status_code == 409, late_upload.text

    retry = _upload(
        client,
        conversation_id,
        "课程资料.pdf",
        pdf,
        content_type="application/pdf",
    )
    assert retry.status_code == 200, retry.text
    assert retry.json()["object_id"] == projection["object_id"]

    spoofed = _upload(
        client,
        conversation_id,
        "伪装.pdf",
        b"this is not a PDF",
        upload_id="upload-spoofed",
        content_type="application/pdf",
    )
    assert spoofed.status_code == 400, spoofed.text
    assert "文件类型" in spoofed.json()["detail"]["message"]

    traversal = _upload(
        client,
        conversation_id,
        "..\\secret.txt",
        b"safe-looking text",
        upload_id="upload-traversal",
        content_type="text/plain",
    )
    assert traversal.status_code == 400, traversal.text
    assert "文件名" in traversal.json()["detail"]["message"]

    office = BytesIO()
    with ZipFile(office, "w") as archive:
        archive.writestr("word/document.xml", "<document/>")
        archive.writestr("word/vbaProject.bin", b"macro")
    macro_doc = _upload(
        client,
        conversation_id,
        "含宏.docx",
        office.getvalue(),
        upload_id="upload-macro",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert macro_doc.status_code == 400, macro_doc.text
    assert "文件类型" in macro_doc.json()["detail"]["message"]


def test_attachment_binds_to_message_downloads_and_isolated_delete(
    tmp_path: Path, monkeypatch: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    alice_client = TestClient(app)
    _register(alice_client, "alice_attachment")
    conversation_id = _create_conversation(alice_client)

    content = b"%PDF-1.7\nprivate study notes"
    uploaded = _upload(
        alice_client,
        conversation_id,
        "private-notes.pdf",
        content,
        upload_id="bind-upload",
        content_type="application/pdf",
    )
    assert uploaded.status_code == 201, uploaded.text
    object_id = uploaded.json()["object_id"]

    sent = alice_client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": "请阅读这个附件", "attachment_ids": [object_id]},
    )
    assert sent.status_code == 200, sent.text
    assert any(name == "done" for name, _ in _parse_sse(sent.text))

    history = alice_client.get(f"/chat/conversations/{conversation_id}")
    assert history.status_code == 200, history.text
    user_message = next(
        item for item in history.json()["messages"] if item["role"] == "user"
    )
    attachment = user_message["attachments"][0]
    assert attachment["object_id"] == object_id
    assert attachment["message_id"] == user_message["message_id"]
    assert attachment["original_filename"] == "private-notes.pdf"

    download = alice_client.get(
        f"/chat/conversations/{conversation_id}/attachments/{object_id}/download"
    )
    assert download.status_code == 200, download.text
    assert download.content == content
    assert download.headers["content-type"] == "application/pdf"
    assert str(tmp_path) not in download.headers.get("content-disposition", "")

    bob_client = TestClient(app)
    _register(bob_client, "bob_attachment")
    bob_conversation_id = _create_conversation(bob_client)
    assert (
        bob_client.get(
            f"/chat/conversations/{conversation_id}/attachments/{object_id}/download"
        ).status_code
        == 404
    )
    assert (
        bob_client.delete(
            f"/chat/conversations/{conversation_id}/messages/{user_message['message_id']}"
            f"/attachments/{object_id}"
        ).status_code
        == 404
    )
    cross_account_bind = bob_client.post(
        f"/chat/conversations/{bob_conversation_id}/messages",
        json={"content": "不应绑定别人的附件", "attachment_ids": [object_id]},
    )
    assert cross_account_bind.status_code == 404, cross_account_bind.text

    deleted = alice_client.delete(
        f"/chat/conversations/{conversation_id}/messages/{user_message['message_id']}"
        f"/attachments/{object_id}"
    )
    assert deleted.status_code == 204, deleted.text
    assert (
        alice_client.get(
            f"/chat/conversations/{conversation_id}/attachments/{object_id}/download"
        ).status_code
        == 404
    )
    remaining = alice_client.get(f"/chat/conversations/{conversation_id}").json()
    assert next(
        item for item in remaining["messages"] if item["role"] == "user"
    )["attachments"] == []
    assert app.state.bridges_database.connection.execute(
        "SELECT COUNT(*) AS count FROM chat_attachments WHERE object_id = ?",
        (object_id,),
    ).fetchone()["count"] == 0


def test_attachment_survives_app_restart(
    tmp_path: Path, monkeypatch: Any
) -> None:
    first_app = _app(tmp_path, monkeypatch)
    first_client = TestClient(first_app)
    _register(first_client, "restart_attachment")
    conversation_id = _create_conversation(first_client)
    content = b"%PDF-1.7\npersistent attachment"
    uploaded = _upload(
        first_client,
        conversation_id,
        "restart.pdf",
        content,
        upload_id="restart-upload",
        content_type="application/pdf",
    )
    assert uploaded.status_code == 201, uploaded.text
    object_id = uploaded.json()["object_id"]
    session = first_client.cookies.get("bridges_session")
    assert session

    second_app = _app(tmp_path, monkeypatch)
    second_client = TestClient(second_app)
    second_client.cookies.set("bridges_session", session)
    history = second_client.get(f"/chat/conversations/{conversation_id}")
    assert history.status_code == 200, history.text
    download = second_client.get(
        f"/chat/conversations/{conversation_id}/attachments/{object_id}/download"
    )
    assert download.status_code == 200, download.text
    assert download.content == content
