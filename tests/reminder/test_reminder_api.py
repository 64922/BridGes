"""提醒 API 的退役兼容合同测试。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from bridges.api.main import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_all_legacy_reminder_routes_return_stable_410(client: TestClient) -> None:
    registered = client.post(
        "/auth/register",
        json={
            "username": "retired-reminder-user",
            "qq_email": "100406@qq.com",
            "password": "correct-horse-12",
        },
    )
    assert registered.status_code == 201, registered.text

    requests = [
        ("get", "/reminders/smtp"),
        ("put", "/reminders/smtp"),
        ("post", "/reminders/smtp/verify"),
        ("delete", "/reminders/smtp"),
        ("get", "/reminders/settings"),
        ("put", "/reminders/settings"),
        ("post", "/reminders/parse"),
        ("post", "/reminders"),
        ("get", "/reminders"),
        ("get", "/reminders/rem-1"),
        ("put", "/reminders/rem-1"),
        ("post", "/reminders/rem-1/pause"),
        ("post", "/reminders/rem-1/resume"),
        ("post", "/reminders/rem-1/send-now"),
        ("delete", "/reminders/rem-1"),
        ("get", "/reminders/rem-1/deliveries"),
    ]
    for method, path in requests:
        response = client.request(method.upper(), path, content=b"{malformed")
        assert response.status_code == 410, (method, path, response.text)
        detail = response.json()["detail"]
        assert detail["error"] == "reminders_retired"
        assert detail["message"] == "该能力已退役，请在学习模式聊天中继续。"
        assert detail["replacement_path"] == "/"
        assert detail["endpoint"].startswith("reminders.")
        assert detail["service_version"]

    assert not hasattr(client.app.state, "reminder_service")
    assert client.app.state.compatibility_metrics.snapshot()["routes"][
        "reminders.create"
    ] == {"real": 1, "probe": 0}
