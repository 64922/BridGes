"""Regression tests for the configured durable application state seam."""

import sqlite3
from pathlib import Path

from bridges.contracts.identity import AccountRegistration, LoginCredential
from bridges.contracts.projects import ProjectCreateRequest
from bridges.identity import IdentityService
from bridges.persistence import SqliteStateStore, build_state_store
from bridges.projects import ProjectService
from bridges.sync import SyncService
from tests.sync.test_sync_service import _exchange, _operation


def test_identity_and_projects_survive_service_reconstruction(tmp_path: Path) -> None:
    store = SqliteStateStore(tmp_path / "bridges.db")
    first_identity = IdentityService(state_store=store)
    registered = first_identity.register(
        AccountRegistration(
            email="durable@example.com",
            password="correct-horse-12",
            agreed_to_terms=True,
        )
    )
    first_projects = ProjectService(state_store=store)
    project = first_projects.create_project(
        registered.account.id,
        ProjectCreateRequest(name="持久化项目"),
    )

    second_identity = IdentityService(
        state_store=SqliteStateStore(tmp_path / "bridges.db")
    )
    authenticated = second_identity.authenticate(
        LoginCredential(email="durable@example.com", password="correct-horse-12")
    )
    second_projects = ProjectService(
        state_store=SqliteStateStore(tmp_path / "bridges.db")
    )

    assert authenticated.account.id == registered.account.id
    assert second_projects.get_project(registered.account.id, project.id).name == "持久化项目"


def test_database_url_requires_an_explicit_supported_adapter(tmp_path: Path) -> None:
    store = build_state_store(
        f"sqlite:///{tmp_path / 'state.db'}",
        encryption_key="test-state-key",
    )

    assert store is not None
    assert store.health_check()


def test_configured_state_store_encrypts_payload_at_rest(tmp_path: Path) -> None:
    database = tmp_path / "encrypted-state.db"
    store = build_state_store(
        f"sqlite:///{database}",
        encryption_key="test-state-key",
    )
    assert store is not None
    store.save("private", {"content": "不可直接读取"})

    with sqlite3.connect(database) as connection:
        payload = connection.execute(
            "SELECT payload FROM application_state WHERE namespace = 'private'"
        ).fetchone()[0]

    assert "不可直接读取" not in payload
    reloaded = build_state_store(
        f"sqlite:///{database}",
        encryption_key="test-state-key",
    )
    assert reloaded is not None
    assert reloaded.load("private") == {"content": "不可直接读取"}


def test_sync_tombstones_survive_service_reconstruction(tmp_path: Path) -> None:
    database = tmp_path / "sync-state.db"
    first = SyncService(state_store=SqliteStateStore(database))
    first.register_device("alice", "laptop", "epoch-1")
    _exchange(first, _operation())
    _exchange(
        first,
        _operation(
            operation_id="delete-1",
            operation_type="delete",
            base_version=1,
            payload={},
        ),
    )

    second = SyncService(state_store=SqliteStateStore(database))
    stale = _exchange(
        second,
        _operation(
            operation_id="offline-resurrection",
            base_version=1,
            payload={"title": "不能复活"},
        ),
    )

    assert stale.accepted_operations == []
    assert stale.quarantined_operations
