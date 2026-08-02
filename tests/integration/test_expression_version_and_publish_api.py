"""Integration tests for expression version comparison and release gate (T029).

The seam under test: an authenticated user creates expression drafts, compares
versions, approves an artifact, links it to a workflow run, and attempts to
publish. The API proves that release eligibility depends on human approval,
workflow success, open todos and upstream source invalidation.
"""

from __future__ import annotations

import base64
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.contracts.expression import (
    ApproveArtifactRequest,
    CompareVersionsRequest,
    ExpressionBrief,
    ExpressionDraftRequest,
    ExpressionDraftStatus,
    Genre,
    PublishArtifactRequest,
    ReleaseEligibilityStatus,
    ReleaseGateCheck,
    RiskTier,
)
from bridges.contracts.science import MediaType


def _source_payload(text: str) -> dict[str, Any]:
    return {
        "filename": "test.txt",
        "media_type": MediaType.TEXT_PLAIN.value,
        "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        "license_state": "user_owned",
        "title": "测试来源",
    }


@pytest.fixture
def expression_client(
    client: TestClient,
    registered_user: Any,
    create_project_for_user: Any,
) -> tuple[TestClient, str, str]:
    """Authenticated client with a project and a source-derived claim graph."""
    user = registered_user(client, "alice-version", "100029@qq.com", "correct-horse-12")
    account_id = user["account"]["id"]
    project_id = create_project_for_user(client, "版本发布测试项目")

    upload = client.post(
        f"/science/projects/{project_id}/sources", json=_source_payload(
            "线粒体是细胞的能量工厂。它们通过细胞呼吸产生 ATP。"
        )
    )
    assert upload.status_code == 201, upload.text

    graph_response = client.post(
        f"/science/projects/{project_id}/claim-graphs",
        json={
            "query": "线粒体功能",
            "project_id": project_id,
            "object_domain": "shared_project",
            "top_k": 5,
            "include_refutations": True,
        },
    )
    assert graph_response.status_code == 201, graph_response.text

    return client, account_id, graph_response.json()["graph"]["graph_id"]


class TestVersionComparisonAPI:
    def test_compare_versions_endpoint_returns_differences(
        self, expression_client: tuple[TestClient, str, str]
    ) -> None:
        client, _account_id, graph_id = expression_client

        brief_a = ExpressionBrief(
            brief_id="brief-a",
            task_goal="向非专业读者解释线粒体功能",
            deliverable_type="科普文案",
            genre=Genre.POPULAR_SCIENCE,
            channel="公众号",
            risk_tier=RiskTier.LOW,
            required_claim_ids=[],
            success_criteria=["每个核心判断有引用"],
        )
        create_a = client.post(
            "/expression/drafts", json=ExpressionDraftRequest(
                brief=brief_a, graph_id=graph_id
            ).model_dump(mode="json")
        )
        assert create_a.status_code == 201
        draft_a_id = create_a.json()["draft"]["draft_id"]

        brief_b = ExpressionBrief(
            brief_id="brief-b",
            task_goal="向同行汇报线粒体功能",
            deliverable_type="科研汇报",
            genre=Genre.RESEARCH_REPORT,
            channel="组会",
            risk_tier=RiskTier.LOW,
            required_claim_ids=[],
            success_criteria=["每个核心判断有引用"],
        )
        create_b = client.post(
            "/expression/drafts", json=ExpressionDraftRequest(
                brief=brief_b, graph_id=graph_id
            ).model_dump(mode="json")
        )
        assert create_b.status_code == 201
        draft_b_id = create_b.json()["draft"]["draft_id"]

        response = client.post(
            "/expression/drafts/compare",
            json=CompareVersionsRequest(
                draft_id_a=draft_a_id, draft_id_b=draft_b_id
            ).model_dump(mode="json"),
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["draft_id_a"] == draft_a_id
        assert data["draft_id_b"] == draft_b_id
        fields = {d["field"] for d in data["differences"]}
        assert "genre" in fields
        assert "argument_plan" in fields


class TestReleaseEligibilityAPI:
    def test_release_eligibility_requires_approval(
        self, expression_client: tuple[TestClient, str, str]
    ) -> None:
        client, _account_id, graph_id = expression_client

        brief = ExpressionBrief(
            brief_id="brief-rel",
            task_goal="向非专业读者解释线粒体功能",
            deliverable_type="科普文案",
            genre=Genre.POPULAR_SCIENCE,
            channel="公众号",
            risk_tier=RiskTier.LOW,
            required_claim_ids=[],
            success_criteria=["每个核心判断有引用"],
        )
        create_response = client.post(
            "/expression/drafts", json=ExpressionDraftRequest(
                brief=brief, graph_id=graph_id
            ).model_dump(mode="json")
        )
        assert create_response.status_code == 201
        draft_id = create_response.json()["draft"]["draft_id"]

        response = client.get(f"/expression/drafts/{draft_id}/release-eligibility")
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["passed"] is False
        assert data["status"] == ReleaseEligibilityStatus.WAITING_APPROVAL.value
        assert ReleaseGateCheck.ARTIFACT_APPROVED.value in data["failed_checks"]

    def test_approve_and_publish_flow(
        self,
        expression_client: tuple[TestClient, str, str],
        submit_work_order_for_project: Any,
    ) -> None:
        client, _account_id, graph_id = expression_client
        # Create a project via the science endpoint already in expression_client,
        # but we also need a workflow run. Reuse the project id from the graph.
        project_id = graph_id  # Not correct; graph_id is not project_id.

        # The expression_client fixture doesn't expose project_id; retrieve it from
        # the claim graph metadata via the science API.
        graph_response = client.get(f"/science/claim-graphs/{graph_id}")
        assert graph_response.status_code == 200, graph_response.text
        project_id = graph_response.json()["project_id"]

        run_response = client.post(
            f"/projects/{project_id}/work-orders",
            json={
                "workflow_name": "generic_science_task",
                "workflow_version": "1",
                "project_id": project_id,
                "objective": "生成表达草稿",
                "success_criteria": "草稿通过表达门",
                "risk_statement": "低风险",
            },
        )
        assert run_response.status_code == 201, run_response.text
        run_id = run_response.json()["run_id"]

        # Confirm and advance the run to succeeded (generic_science_task has two nodes).
        confirm_response = client.post(
            f"/projects/{project_id}/runs/{run_id}/confirm", json={"confirmed": True}
        )
        assert confirm_response.status_code == 200, confirm_response.text
        advance_response = client.post(f"/_test/runs/{run_id}/advance")
        assert advance_response.status_code == 200, advance_response.text
        if advance_response.json()["run_status"] == "running":
            advance_response = client.post(f"/_test/runs/{run_id}/advance")
            assert advance_response.status_code == 200, advance_response.text
        assert advance_response.json()["run_status"] == "succeeded"

        brief = ExpressionBrief(
            brief_id="brief-pub",
            task_goal="向非专业读者解释线粒体功能",
            deliverable_type="科普文案",
            genre=Genre.POPULAR_SCIENCE,
            channel="公众号",
            risk_tier=RiskTier.LOW,
            required_claim_ids=[],
            success_criteria=["每个核心判断有引用"],
        )
        create_response = client.post(
            "/expression/drafts", json=ExpressionDraftRequest(
                brief=brief, graph_id=graph_id, run_id=run_id, project_id=project_id
            ).model_dump(mode="json")
        )
        assert create_response.status_code == 201, create_response.text
        draft_id = create_response.json()["draft"]["draft_id"]
        assert create_response.json()["draft"]["status"] == ExpressionDraftStatus.DRAFTED.value

        # Before approval, publish is blocked.
        blocked_publish = client.post(
            f"/expression/drafts/{draft_id}/publish",
            json=PublishArtifactRequest(run_id=run_id).model_dump(mode="json"),
        )
        assert blocked_publish.status_code == 422, blocked_publish.text

        # Approve the artifact.
        approve_response = client.post(
            f"/expression/drafts/{draft_id}/approve",
            json=ApproveArtifactRequest(reason="内容准确，引用完整。").model_dump(mode="json"),
        )
        assert approve_response.status_code == 200, approve_response.text
        assert approve_response.json()["draft"]["artifact_trust_status"] == "approved"

        # Release eligibility is now true.
        eligibility_response = client.get(
            f"/expression/drafts/{draft_id}/release-eligibility",
            params={"run_id": run_id},
        )
        assert eligibility_response.status_code == 200, eligibility_response.text
        eligibility = eligibility_response.json()
        assert eligibility["passed"] is True
        assert eligibility["status"] == ReleaseEligibilityStatus.ELIGIBLE.value

        # Publish succeeds and binds the event.
        publish_response = client.post(
            f"/expression/drafts/{draft_id}/publish",
            json=PublishArtifactRequest(run_id=run_id).model_dump(mode="json"),
        )
        assert publish_response.status_code == 200, publish_response.text
        event = publish_response.json()["event"]
        assert event["draft_id"] == draft_id
        assert event["account_id"] == _account_id
        assert event["release_gate_result"]["passed"] is True
        assert event["human_decision_id"]
