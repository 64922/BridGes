"""Issue 03：学习安排与 QQ SMTP 提醒的退役回归测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from pydantic import SecretStr

from bridges.credentials.store import InMemoryCredentialStore
from bridges.retirement import run_reminder_retirement
from bridges.storage import BridgesDatabase


def test_old_learning_and_reminder_routes_are_stable_410(
    client: TestClient, registered_user: Any
) -> None:
    registered_user(client, "retired-user", "100403@qq.com", "correct-horse-12")
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
        assert detail["error"] in {"learning_review_retired", "reminders_retired"}
        assert "请在学习模式聊天中继续" in detail["message"]
        assert detail["replacement_path"] == "/"
        assert detail["endpoint"]
        assert detail["service_version"]

    repeated = client.post("/reminders", json={"same": "request"})
    assert repeated.status_code == 410
    metrics = client.app.state.compatibility_metrics.snapshot()
    assert metrics["routes"]["reminders.create"] == {"real": 2, "probe": 0}
    assert not hasattr(client.app.state, "reminder_service")
    assert not hasattr(client.app.state, "review_scheduling_service")


def test_compatibility_probe_is_counted_without_private_context(
    client: TestClient, registered_user: Any
) -> None:
    registered_user(client, "probe-user", "100404@qq.com", "correct-horse-12")
    response = client.get(
        "/reminders/rem-guess",
        headers={"X-Bridges-Compatibility-Probe": "true"},
    )
    assert response.status_code == 410
    detail = response.json()["detail"]
    assert "rem-guess" not in str(detail)
    assert "probe-user" not in str(detail)
    assert client.app.state.compatibility_metrics.snapshot()["routes"][
        "reminders.detail"
    ] == {"real": 0, "probe": 1}


def test_reminder_retirement_is_account_scoped_idempotent_and_clears_credentials(
    tmp_path: Path,
) -> None:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    account_a = "account-a"
    account_b = "account-b"
    now = "2026-08-09T00:00:00+00:00"
    schedule_json = (
        '{"first_run_at":"2026-08-09T00:00:00Z","repeat":"once",'
        '"repeat_weekdays":null,"repeat_month_day":null,'
        '"timezone":"Asia/Shanghai"}'
    )
    with database.transaction():
        database.connection.execute(
            "INSERT INTO reminder_settings(account_id) VALUES (?)", (account_a,)
        )
        database.connection.execute(
            "INSERT INTO reminder_settings(account_id) VALUES (?)", (account_b,)
        )
        database.connection.execute(
            """
            INSERT INTO task_claims(
                claim_id, queue_name, task_key, status, created_at, updated_at
            ) VALUES (?, 'reminder', ?, 'queued', ?, ?)
            """,
            ("claim-rem-a", "reminder:rem-a", now, now),
        )
        database.connection.execute(
            """
            INSERT INTO reminders(
                reminder_id, account_id, qq_email, timezone, raw_text,
                schedule_json, subject, body, status, next_run_at,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'enabled', ?, ?, ?)
            """,
            (
                "rem-a",
                account_a,
                "100405@qq.com",
                "Asia/Shanghai",
                "旧提醒",
                schedule_json,
                "旧提醒",
                "旧正文",
                now,
                now,
                now,
            ),
        )

    credentials = InMemoryCredentialStore(namespace="smtp")
    credentials.save(account_a, SecretStr("AUTH-CODE-MUST-NOT-LEAK"))
    report = run_reminder_retirement(
        database=database,
        credential_store=credentials,
        now=datetime(2026, 8, 9, tzinfo=UTC),
    )

    row = database.connection.execute(
        "SELECT status, next_run_at, next_retry_at FROM reminders "
        "WHERE reminder_id = 'rem-a'"
    ).fetchone()
    assert row["status"] == "retired"
    assert row["next_run_at"] is None
    assert row["next_retry_at"] is None
    assert credentials.get(account_a) is None
    assert report["status"] == "completed"
    assert report["reminders_retired"] == 1
    assert report["queued_reminders_retired"] == 1
    assert report["account_count"] == 2
    assert "AUTH-CODE-MUST-NOT-LEAK" not in str(report)
    claim = database.connection.execute(
        "SELECT status, next_retry_at, result_json FROM task_claims "
        "WHERE claim_id = 'claim-rem-a'"
    ).fetchone()
    assert claim["status"] == "completed"
    assert claim["next_retry_at"] is None
    assert claim["result_json"] == '{"status":"retired"}'

    second = run_reminder_retirement(
        database=database,
        credential_store=credentials,
        now=datetime(2026, 8, 9, 0, 1, tzinfo=UTC),
    )
    assert second["reminders_retired"] == 0
    assert second["queued_reminders_retired"] == 0
    assert second["credentials_cleared"] == 2
