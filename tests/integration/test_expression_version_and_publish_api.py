"""Integration tests for expression version comparison and release gate (T029).

Legacy write routes (create, compare, approve, publish) are retired and return
``410 Gone`` during the ADR-0026 compatibility window. Only the read-only
release-eligibility check remains usable.
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
    """Authenticated client with a project."""
    user = registered_user(client, "alice-version", "100029@qq.com", "correct-horse-12")
    account_id = user["account"]["id"]
    project_id = create_project_for_user(client, "版本发布测试项目")
    return client, account_id, project_id


class TestVersionComparisonAPI:
    def test_compare_versions_returns_410(
        self, expression_client: tuple[TestClient, str, str]
    ) -> None:
        client, _account_id, _project_id = expression_client
        response = client.post(
            "/expression/drafts/compare", json={"ignored": True}
        )
        assert response.status_code == 410, response.text
        detail = response.json()["detail"]
        assert detail["error"] == "legacy_expression_retired"
        assert detail["replacement_path"] == "/chat"


class TestReleaseEligibilityAPI:
    def test_release_eligibility_returns_404_for_unknown_draft(
        self, expression_client: tuple[TestClient, str, str]
    ) -> None:
        client, _account_id, _project_id = expression_client
        response = client.get(
            "/expression/drafts/draft-does-not-exist/release-eligibility"
        )
        assert response.status_code == 404, response.text
        assert response.json()["detail"]["error"] == "draft_not_found"


class TestApproveAndPublishAPI:
    def test_approve_returns_410(
        self, expression_client: tuple[TestClient, str, str]
    ) -> None:
        client, _account_id, _project_id = expression_client
        response = client.post(
            "/expression/drafts/draft-does-not-exist/approve",
            json={"ignored": True},
        )
        assert response.status_code == 410, response.text
        assert response.json()["detail"]["error"] == "legacy_expression_retired"

    def test_publish_returns_410(
        self, expression_client: tuple[TestClient, str, str]
    ) -> None:
        client, _account_id, _project_id = expression_client
        response = client.post(
            "/expression/drafts/draft-does-not-exist/publish",
            json={"ignored": True},
        )
        assert response.status_code == 410, response.text
        assert response.json()["detail"]["error"] == "legacy_expression_retired"
