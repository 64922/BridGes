"""T011 integration test: invalidation service wiring through the app.

The seam under test: the application creates a shared InvalidationService,
registers generic downstream resolvers, and passes it to both WorkflowService and
VaultService. A revoked object is blocked by new runs and new vault reads.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from science_companion.api.main import create_app
from science_companion.contracts.identity import AuthMethod, SubjectContext
from science_companion.contracts.invalidation import InvalidationEventType, InvalidationState
from science_companion.contracts.projects import ObjectDomain, ObjectRef
from science_companion.contracts.workflows import WorkOrder


def _register(client: TestClient, email: str, password: str) -> dict[str, Any]:
    response = client.post(
        "/auth/register",
        json={"email": email, "password": password, "agreed_to_terms": True},
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


def _create_project(client: TestClient, name: str) -> str:
    response = client.post("/projects", json={"name": name})
    assert response.status_code == 201, response.text
    return cast(str, response.json()["id"])


def _create_vault_object(client: TestClient, account_id: str, content: bytes) -> str:
    import base64

    response = client.post(
        "/vault/objects",
        json={
            "owner_account_id": account_id,
            "content": base64.b64encode(content).decode("ascii"),
            "content_authority": "server_replica",
            "purpose": "test",
        },
    )
    assert response.status_code == 201, response.text
    return cast(str, response.json()["ref"]["object_id"])


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_app_wires_invalidation_service_to_workflow_and_vault(
    client: TestClient,
) -> None:
    alice_client = TestClient(client.app)
    registered = _register(alice_client, "alice-invalidation@example.com", "correct-horse-12")
    account_id = cast(str, registered["account"]["id"])
    project_id = _create_project(alice_client, "失效测试项目")
    object_id = _create_vault_object(alice_client, account_id, b"object to invalidate")

    invalidation_service = client.app.state.invalidation_service  # type: ignore[attr-defined]
    workflow_service = client.app.state.workflow_service  # type: ignore[attr-defined]
    vault_service = client.app.state.vault_service  # type: ignore[attr-defined]

    subject = SubjectContext(
        account_id=account_id,
        session_id="session-test",
        auth_method=AuthMethod.PASSWORD,
    )
    object_ref = ObjectRef(
        domain=ObjectDomain.PERSONAL_VAULT,
        owner_id=account_id,
        object_id=object_id,
        version=1,
    )

    # Revoke the object and build a plan.
    event = invalidation_service.record_invalidation_event(
        subject, object_ref, InvalidationEventType.REVOKE, "撤权"
    )
    plan = invalidation_service.plan_invalidation(event.event_id)

    # Generic resolvers produce cache, index and run downstreams.
    types = {d.downstream_type for d in plan.impact_set.affected_downstreams}
    assert "cache" in types
    assert "index_projection" in types
    assert "workflow_run" in types

    # A new run referencing the revoked object is blocked.
    order = WorkOrder(
        workflow_name="generic_science_task",
        workflow_version="1",
        project_id=project_id,
        objective="使用已失效对象",
        success_criteria="被阻止",
        risk_statement="无",
        object_refs=[object_id],
    )
    from science_companion.workflows import WorkflowError

    with pytest.raises(WorkflowError, match="对象已失效"):
        workflow_service.submit_work_order(account_id, order)

    # A new vault read is blocked.
    from science_companion.vault import VaultError

    with pytest.raises(VaultError, match="对象已失效"):
        vault_service.get_object(account_id, object_id)

    # The state is visible and immutable.
    state = invalidation_service.check_state(object_ref)
    assert state.state == InvalidationState.REVOKED
    assert state.effective_event_id == event.event_id
