"""Application wiring tests for the configured durable state store."""

from pathlib import Path

from fastapi.testclient import TestClient

from science_companion.api.main import create_app
from science_companion.persistence import SqliteStateStore


def test_recreated_app_reads_identity_and_project_from_sqlite(tmp_path: Path) -> None:
    database = tmp_path / "api-state.db"
    first_app = create_app(state_store=SqliteStateStore(database))
    first_client = TestClient(first_app)
    registered = first_client.post(
        "/auth/register",
        json={
            "email": "recreated-app@example.com",
            "password": "correct-horse-12",
            "agreed_to_terms": True,
        },
    )
    assert registered.status_code == 201
    account_id = registered.json()["account"]["id"]
    created = first_client.post("/projects", json={"name": "重启后仍存在"})
    assert created.status_code == 201
    project_id = created.json()["id"]

    second_app = create_app(state_store=SqliteStateStore(database))
    second_client = TestClient(second_app)
    login = second_client.post(
        "/auth/login",
        json={"email": "recreated-app@example.com", "password": "correct-horse-12"},
    )
    assert login.status_code == 200
    assert login.json()["account"]["id"] == account_id
    fetched = second_client.get(f"/projects/{project_id}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == project_id
