"""学习复习安排旧 API 的退役兼容合同测试。"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient


def test_all_legacy_review_routes_return_stable_410(
    client: TestClient, registered_user: Any
) -> None:
    registered_user(client, "retired-review-user", "100407@qq.com", "correct-horse-12")
    requests = [
        ("post", "/learning/missions/mission-1/review-schedule"),
        ("get", "/learning/missions/mission-1/review-schedule"),
        ("get", "/learning/missions/mission-1/review-tasks"),
        ("get", "/learning/review-tasks/task-1"),
        ("post", "/learning/review-tasks/task-1/postpone"),
        ("post", "/learning/review-tasks/task-1/adjust"),
        ("post", "/learning/review-tasks/task-1/cancel"),
        ("post", "/learning/review-tasks/task-1/complete"),
        ("post", "/learning/review-tasks/task-1/work-order"),
    ]
    for method, path in requests:
        response = client.request(method.upper(), path, content=b"{malformed")
        assert response.status_code == 410, (method, path, response.text)
        detail = response.json()["detail"]
        assert detail["error"] == "learning_review_retired"
        assert detail["message"] == "该能力已退役，请在学习模式聊天中继续。"
        assert detail["replacement_path"] == "/"
        assert detail["endpoint"].startswith("learning.")
        assert detail["service_version"]

    assert not hasattr(client.app.state, "review_scheduling_service")
