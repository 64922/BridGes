"""Integration tests for the retired expression write API (T025 / Issue 07).

Legacy write routes under ``/expression`` are retired and return ``410 Gone``
during the ADR-0026 compatibility window. Only read-only inspection routes
remain usable.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def expression_client(
    client: TestClient,
    registered_user: Any,
    create_project_for_user: Any,
) -> tuple[TestClient, str, str]:
    """Authenticated client with a project and a source-derived claim graph."""
    user = registered_user(client, "alice-expression", "100010@qq.com", "correct-horse-12")
    account_id = user["account"]["id"]
    project_id = create_project_for_user(client, "表达测试项目")

    return client, account_id, project_id


class TestExpressionDraftAPI:
    def test_create_draft_returns_410(
        self, expression_client: tuple[TestClient, str, str]
    ) -> None:
        client, _account_id, _project_id = expression_client
        response = client.post("/expression/drafts", json={"ignored": True})

        assert response.status_code == 410, response.text
        detail = response.json()["detail"]
        assert detail["error"] == "legacy_expression_retired"
        assert detail["replacement_path"] == "/chat"

    def test_style_diagnostic_returns_410(
        self, expression_client: tuple[TestClient, str, str]
    ) -> None:
        client, _account_id, _project_id = expression_client
        response = client.post(
            "/expression/drafts/draft-does-not-exist/style-diagnostic",
            json={"ignored": True},
        )
        assert response.status_code == 410, response.text
        assert response.json()["detail"]["error"] == "legacy_expression_retired"

    def test_feedback_returns_410(
        self, expression_client: tuple[TestClient, str, str]
    ) -> None:
        client, _account_id, _project_id = expression_client
        response = client.post(
            "/expression/drafts/draft-does-not-exist/feedback",
            json={"ignored": True},
        )
        assert response.status_code == 410, response.text
        assert response.json()["detail"]["error"] == "legacy_expression_retired"

    def test_apply_patch_returns_410(
        self, expression_client: tuple[TestClient, str, str]
    ) -> None:
        client, _account_id, _project_id = expression_client
        response = client.post(
            "/expression/drafts/draft-does-not-exist/patches/patch-does-not-exist/apply",
            json={"ignored": True},
        )
        assert response.status_code == 410, response.text
        assert response.json()["detail"]["error"] == "legacy_expression_retired"


class TestExpressionReadOnlyRoutes:
    def test_inspector_returns_404_for_unknown_draft(
        self, expression_client: tuple[TestClient, str, str]
    ) -> None:
        client, _account_id, _project_id = expression_client
        response = client.get("/expression/drafts/draft-does-not-exist/inspector")
        assert response.status_code == 404, response.text
