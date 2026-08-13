"""Integration tests for retiring legacy expression write routes (Issue 07).

All legacy POST routes under ``/expression`` must return ``410 Gone`` with a
stable error code, Chinese retirement message and modern chat replacement path.
They must not invoke the expression service, deterministic generator, model
gateway or Qwen client, and must not create drafts, versions, feedback,
approval, publish events or model run locks.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from bridges.ai import CapabilityRegistryError
from bridges.contracts.workflows import WorkflowRunStatus, WorkOrder

_RETIRED_ENDPOINTS: list[tuple[str, str, str, dict[str, str]]] = [
    ("POST", "/expression/drafts", "expression.drafts.create", {}),
    (
        "POST",
        "/expression/drafts/{draft_id}/style-diagnostic",
        "expression.drafts.style_diagnostic",
        {"draft_id": "draft-retired"},
    ),
    (
        "POST",
        "/expression/drafts/{draft_id}/patches/{patch_id}/apply",
        "expression.drafts.apply_patch",
        {"draft_id": "draft-retired", "patch_id": "patch-retired"},
    ),
    (
        "POST",
        "/expression/drafts/{draft_id}/feedback",
        "expression.drafts.feedback",
        {"draft_id": "draft-retired"},
    ),
    (
        "POST",
        "/expression/drafts/{draft_id}/approve",
        "expression.drafts.approve",
        {"draft_id": "draft-retired"},
    ),
    (
        "POST",
        "/expression/drafts/{draft_id}/publish",
        "expression.drafts.publish",
        {"draft_id": "draft-retired"},
    ),
    ("POST", "/expression/drafts/compare", "expression.drafts.compare", {}),
]


def _retirement_detail(response: Any) -> dict[str, Any]:
    """Extract the retired-capability detail from a FastAPI HTTPException body."""
    body = response.json()
    detail = body.get("detail", body)
    assert isinstance(detail, dict)
    return detail


@pytest.fixture
def authenticated_client(
    client: TestClient, registered_user: Any
) -> TestClient:
    """Return a client authenticated as a registered user."""
    registered_user(client, "alice-retire", "100007@qq.com", "correct-horse-12")
    return client


@pytest.fixture
def spied_expression_service(authenticated_client: TestClient) -> MagicMock:
    """Wrap the application's expression service so invocations are observable."""
    real_service = authenticated_client.app.state.expression_service
    spy = MagicMock(wraps=real_service)
    authenticated_client.app.state.expression_service = spy
    return spy


@pytest.fixture
def spied_model_gateway(authenticated_client: TestClient) -> MagicMock:
    """Wrap the application's model gateway so invocations are observable."""
    real_gateway = authenticated_client.app.state.model_gateway
    spy = MagicMock(wraps=real_gateway)
    authenticated_client.app.state.model_gateway = spy
    return spy


class TestExpressionRetirement:
    @pytest.mark.parametrize("method, path_template, endpoint_id, path_params", _RETIRED_ENDPOINTS)
    def test_retired_endpoint_returns_410(
        self,
        authenticated_client: TestClient,
        method: str,
        path_template: str,
        endpoint_id: str,
        path_params: dict[str, str],
    ) -> None:
        path = path_template.format(**path_params)
        response = authenticated_client.request(method, path)

        assert response.status_code == 410, response.text
        detail = _retirement_detail(response)
        assert detail["error"] == "legacy_expression_retired"
        assert "已退役" in detail["message"]
        assert detail["replacement_path"] == "/chat"
        assert detail["endpoint"] == endpoint_id
        assert "service_version" in detail

    @pytest.mark.parametrize("method, path_template, endpoint_id, path_params", _RETIRED_ENDPOINTS)
    def test_retired_endpoint_ignores_valid_body(
        self,
        authenticated_client: TestClient,
        method: str,
        path_template: str,
        endpoint_id: str,
        path_params: dict[str, str],
    ) -> None:
        path = path_template.format(**path_params)
        response = authenticated_client.request(
            method, path, json={"brief_id": "ignored", "draft_id": "ignored"}
        )

        assert response.status_code == 410
        detail = _retirement_detail(response)
        assert detail["error"] == "legacy_expression_retired"
        assert detail["endpoint"] == endpoint_id

    @pytest.mark.parametrize("method, path_template, _endpoint_id, path_params", _RETIRED_ENDPOINTS)
    def test_retired_endpoint_ignores_malformed_body(
        self,
        authenticated_client: TestClient,
        method: str,
        path_template: str,
        _endpoint_id: str,
        path_params: dict[str, str],
    ) -> None:
        path = path_template.format(**path_params)
        response = authenticated_client.request(
            method,
            path,
            content=b"not-json",
            headers={"content-type": "application/json"},
        )

        assert response.status_code == 410

    @pytest.mark.parametrize("method, path_template, _endpoint_id, path_params", _RETIRED_ENDPOINTS)
    def test_retired_endpoint_ignores_empty_body(
        self,
        authenticated_client: TestClient,
        method: str,
        path_template: str,
        _endpoint_id: str,
        path_params: dict[str, str],
    ) -> None:
        path = path_template.format(**path_params)
        response = authenticated_client.request(method, path, content=b"")

        assert response.status_code == 410

    @pytest.mark.parametrize("method, path_template, endpoint_id, path_params", _RETIRED_ENDPOINTS)
    def test_retired_endpoint_is_idempotent(
        self,
        authenticated_client: TestClient,
        method: str,
        path_template: str,
        endpoint_id: str,
        path_params: dict[str, str],
    ) -> None:
        path = path_template.format(**path_params)
        first = authenticated_client.request(method, path)
        second = authenticated_client.request(method, path)

        assert first.status_code == second.status_code == 410
        assert _retirement_detail(first)["endpoint"] == endpoint_id
        assert first.json() == second.json()

    def test_retired_endpoints_do_not_invoke_expression_service_or_gateway(
        self,
        authenticated_client: TestClient,
        spied_expression_service: MagicMock,
        spied_model_gateway: MagicMock,
    ) -> None:
        for _method, path_template, _endpoint_id, path_params in _RETIRED_ENDPOINTS:
            path = path_template.format(**path_params)
            response = authenticated_client.post(path)
            assert response.status_code == 410

        spied_expression_service.assert_not_called()
        spied_model_gateway.invoke.assert_not_called()

    def test_retired_endpoints_do_not_create_model_run_locks(
        self,
        authenticated_client: TestClient,
    ) -> None:
        db = getattr(authenticated_client.app.state, "bridges_database", None)
        before = 0
        if db is not None:
            row = db.connection.execute(
                "SELECT COUNT(*) AS count FROM model_run_locks"
            ).fetchone()
            before = int(row["count"])

        for _method, path_template, _endpoint_id, path_params in _RETIRED_ENDPOINTS:
            path = path_template.format(**path_params)
            response = authenticated_client.post(path)
            assert response.status_code == 410

        after = 0
        if db is not None:
            row = db.connection.execute(
                "SELECT COUNT(*) AS count FROM model_run_locks"
            ).fetchone()
            after = int(row["count"])

        assert after == before

    def test_unauthenticated_retired_endpoint_returns_401(
        self, client: TestClient
    ) -> None:
        response = client.post("/expression/drafts")
        assert response.status_code == 401

    def test_real_and_probe_traffic_are_counted_separately(
        self, authenticated_client: TestClient
    ) -> None:
        metrics = authenticated_client.app.state.compatibility_metrics
        endpoint = "expression.drafts.create"

        real_before = metrics.snapshot().get("routes", {}).get(endpoint, {}).get("real", 0)
        probe_before = metrics.snapshot().get("routes", {}).get(endpoint, {}).get("probe", 0)

        authenticated_client.post("/expression/drafts")
        authenticated_client.post(
            "/expression/drafts",
            headers={"x-bridges-compatibility-probe": "true"},
        )

        snapshot = metrics.snapshot()
        assert snapshot["routes"][endpoint]["real"] == real_before + 1
        assert snapshot["routes"][endpoint]["probe"] == probe_before + 1


class TestExpressionCapabilityRetirement:
    def test_expression_draft_generation_not_in_registry(
        self, authenticated_client: TestClient
    ) -> None:
        registry = authenticated_client.app.state.capability_registry

        with pytest.raises(CapabilityRegistryError):
            registry.get("expression_draft_generation", "1")

        active_names = {c.name for c in registry.list_active()}
        assert "expression_draft_generation" not in active_names

    def test_expression_draft_generation_cannot_be_invoked_by_workflow(
        self,
        authenticated_client: TestClient,
        registered_user: Any,
    ) -> None:
        user = registered_user(
            authenticated_client, "workflow-retire", "1000077@qq.com", "correct-horse-12"
        )
        account_id = user["account"]["id"]
        workflow_service = authenticated_client.app.state.workflow_service

        workflow_service.register_workflow(
            name="legacy_expression_task",
            version="1",
            nodes=[
                {
                    "node_id": "generate_draft",
                    "node_name": "生成表达草稿",
                    "human_gate": False,
                    "capability_name": "expression_draft_generation",
                    "capability_version": "1",
                }
            ],
            terminal_states=["succeeded", "blocked", "cancelled"],
        )

        order = WorkOrder(
            project_id="project-retire",
            workflow_name="legacy_expression_task",
            workflow_version="1",
            objective="使用已退役能力",
            success_criteria="不应成功",
            risk_statement="低风险",
        )
        work_order = workflow_service.submit_work_order(account_id, order)
        workflow_service.confirm_work_order(account_id, work_order.run_id)

        projection = workflow_service.advance_run(account_id, work_order.run_id)
        assert projection.run_status == WorkflowRunStatus.BLOCKED
        assert len(projection.model_run_locks) == 1
        lock = projection.model_run_locks[0]
        assert lock.error_code == "unregistered_capability"
