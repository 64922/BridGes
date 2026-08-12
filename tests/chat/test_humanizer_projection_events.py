"""文章结果投影前端事件端点测试（人味化改造 Issue 08）。

验证 POST /chat/humanizer/events 只接受投影版本/状态/风险类型/事件名/
legacy 标志（请求体 Schema 无正文字段，正文无法进入审计）；事件只落
审计，不影响用户操作。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.api.main import create_app
from bridges.config import get_settings


@pytest.fixture
def sqlite_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """构建挂载 sqlite bridges.db 的应用（与 test_chat_api 一致）。"""
    monkeypatch.setenv(
        "BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}"
    )
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "api-test-secret-key")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    return create_app()


@pytest.fixture
def client(sqlite_app: Any) -> TestClient:
    return TestClient(sqlite_app)


def _register(client: TestClient, tag: str = "1") -> None:
    response = client.post(
        "/auth/register",
        json={
            "username": f"proj_user_{tag}",
            "qq_email": f"12345678{tag}@qq.com",
            "password": "Passw0rd123!",
        },
    )
    assert response.status_code == 201, response.text


def test_expand_event_recorded_without_body_text(
    client: TestClient, sqlite_app: Any
) -> None:
    _register(client)
    response = client.post(
        "/chat/humanizer/events",
        json={
            "event": "expand",
            "projection_version": "1",
            "delivery_status": "delivered",
            "risk_types": ["fidelity_blocking"],
            "legacy": False,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True}


def test_copy_event_and_legacy_read_accepted(
    client: TestClient, sqlite_app: Any
) -> None:
    _register(client, "2")
    for payload in (
        {"event": "copy", "projection_version": "1", "delivery_status": "delivered"},
        {
            "event": "legacy_read",
            "projection_version": None,
            "delivery_status": "done",
            "legacy": True,
        },
    ):
        response = client.post("/chat/humanizer/events", json=payload)
        assert response.status_code == 200, response.text


def test_request_body_rejects_private_text_fields(
    client: TestClient, sqlite_app: Any
) -> None:
    """请求体 Schema 无正文字段：正文/引语/diff 内容必须 422（AC 9）。"""
    _register(client, "3")
    response = client.post(
        "/chat/humanizer/events",
        json={
            "event": "copy",
            "projection_version": "1",
            "delivery_status": "delivered",
            "final_text": "私人正文不得进入前端遥测。",
            "original_span": "原文片段",
            "diff": "逐项 diff 内容",
        },
    )
    assert response.status_code == 422, response.text


def test_unknown_event_rejected(client: TestClient, sqlite_app: Any) -> None:
    _register(client, "4")
    response = client.post(
        "/chat/humanizer/events",
        json={"event": "delete_message", "projection_version": "1"},
    )
    assert response.status_code == 422, response.text


def test_unauthenticated_event_rejected(client: TestClient, sqlite_app: Any) -> None:
    response = client.post(
        "/chat/humanizer/events",
        json={"event": "expand", "projection_version": "1"},
    )
    assert response.status_code == 401, response.text
