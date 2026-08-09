"""Issue 11：全局知识库是唯一的新文件输入来源。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from bridges.api.main import create_app
from bridges.contracts.chat import ChatMessageCreateRequest, ImageRequestPayload
from bridges.contracts.humanizer import HumanizerTaskContract


def test_new_chat_message_contract_has_no_attachment_field() -> None:
    with pytest.raises(ValueError):
        ChatMessageCreateRequest.model_validate(
            {"content": "不能再从聊天附件摄取", "attachment_ids": ["legacy-object"]}
        )


def test_capability_contracts_reject_legacy_file_inputs() -> None:
    with pytest.raises(ValueError):
        HumanizerTaskContract.model_validate(
            {
                "path": "rewrite",
                "genre": "popular_science",
                "attachment_ids": ["legacy-object"],
            }
        )
    with pytest.raises(ValueError):
        ImageRequestPayload.model_validate(
            {
                "kind": "edit",
                "prompt": "改背景",
                "source_version_id": "legacy-version",
            }
        )


def test_legacy_file_write_routes_return_410_without_reading_body() -> None:
    client = TestClient(create_app())
    registered = client.post(
        "/auth/register",
        json={
            "username": "issue11-routes",
            "qq_email": "110011@qq.com",
            "password": "correct-horse-12",
        },
    )
    assert registered.status_code == 201, registered.text

    checks = [
        ("post", "/learning-projects", {"json": {"name": "legacy"}}),
        (
            "post",
            "/chat/conversations/legacy/attachments",
            {"content": b"must not be consumed", "headers": {"x-bridges-filename": "x.txt"}},
        ),
        (
            "post",
            "/chat/conversations/legacy/attachments/object/ingestion/retry",
            {},
        ),
        ("delete", "/chat/conversations/legacy/attachments/object", {}),
    ]
    for method, path, kwargs in checks:
        response = getattr(client, method)(path, **kwargs)
        assert response.status_code == 410, (path, response.text)
        assert response.json()["detail"]["error"] == "legacy_file_source_retired"
