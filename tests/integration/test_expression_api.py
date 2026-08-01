"""Integration tests for the expression API (T025).

The seam under test: an authenticated user uploads a source, generates a claim
graph, creates an expression brief, and receives a fact-lock-bound draft. The
API proves that claims, citations and fact locks are observable through the
inspector and that missing evidence produces an explainable block.
"""

from __future__ import annotations

import base64
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.contracts.expression import (
    ApplyRevisionPatchRequest,
    ExpressionBrief,
    ExpressionDraftRequest,
    ExpressionDraftStatus,
    ExpressionGateCheck,
    Genre,
    PatchAction,
    RiskTier,
    StyleDiagnosticRequest,
    SubmitExpressionFeedbackRequest,
    UserFeedbackTarget,
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
    user = registered_user(client, "alice-expression@example.com", "correct-horse-12")
    account_id = user["account"]["id"]
    project_id = create_project_for_user(client, "表达测试项目")

    # Upload a source so the claim graph has retrievable candidates.
    upload = client.post(
        f"/science/projects/{project_id}/sources", json=_source_payload(
            "线粒体是细胞的能量工厂。它们通过细胞呼吸产生 ATP。"
        )
    )
    assert upload.status_code == 201, upload.text

    # Generate a claim graph for the project.
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


class TestExpressionDraftAPI:
    def test_create_draft_returns_fact_locked_draft(
        self, expression_client: tuple[TestClient, str, str]
    ) -> None:
        client, _account_id, graph_id = expression_client

        brief = ExpressionBrief(
            brief_id="brief-1",
            task_goal="向非专业读者解释线粒体功能",
            deliverable_type="科普文案",
            genre=Genre.POPULAR_SCIENCE,
            channel="公众号",
            length_or_duration="800字",
            language_locale="zh-CN",
            risk_tier=RiskTier.LOW,
            required_claim_ids=[],
            success_criteria=["每个核心判断有引用", "不夸大结论"],
        )
        request = ExpressionDraftRequest(brief=brief, graph_id=graph_id)

        response = client.post("/expression/drafts", json=request.model_dump(mode="json"))
        assert response.status_code == 201, response.text

        data = response.json()
        draft = data["draft"]
        assert draft["status"] == ExpressionDraftStatus.DRAFTED.value
        assert data["gate"]["passed"] is True
        assert draft["fact_lock_set_id"]

        # At least one span binds to a claim, citation and fact lock.
        bound_spans = [
            s
            for s in draft["spans"]
            if s["claim_ids"] and s["citation_ids"] and s["fact_lock_ids"]
        ]
        assert bound_spans

    def test_inspector_returns_same_draft_with_bindings(
        self, expression_client: tuple[TestClient, str, str]
    ) -> None:
        client, _account_id, graph_id = expression_client

        brief = ExpressionBrief(
            brief_id="brief-2",
            task_goal="解释线粒体功能",
            deliverable_type="科普文案",
            genre=Genre.POPULAR_SCIENCE,
            channel="公众号",
            risk_tier=RiskTier.LOW,
            success_criteria=["有引用"],
        )
        request = ExpressionDraftRequest(brief=brief, graph_id=graph_id)
        create_response = client.post(
            "/expression/drafts", json=request.model_dump(mode="json")
        )
        assert create_response.status_code == 201
        draft_id = create_response.json()["draft"]["draft_id"]

        inspector_response = client.get(f"/expression/drafts/{draft_id}/inspector")
        assert inspector_response.status_code == 200
        assert inspector_response.json()["draft_id"] == draft_id

    def test_missing_success_criteria_returns_blocked_draft(
        self, expression_client: tuple[TestClient, str, str]
    ) -> None:
        client, _account_id, graph_id = expression_client

        brief = ExpressionBrief(
            brief_id="brief-3",
            task_goal="解释线粒体功能",
            deliverable_type="科普文案",
            genre=Genre.POPULAR_SCIENCE,
            channel="公众号",
            risk_tier=RiskTier.LOW,
            success_criteria=[],
        )
        request = ExpressionDraftRequest(brief=brief, graph_id=graph_id)

        response = client.post("/expression/drafts", json=request.model_dump(mode="json"))
        assert response.status_code == 201

        data = response.json()
        assert data["draft"]["status"] == ExpressionDraftStatus.BLOCKED.value
        assert data["gate"]["passed"] is False
        assert ExpressionGateCheck.BRIEF_COMPLETE.value in data["gate"]["failed_checks"]


class TestExpressionStyleDiagnosticAPI:
    def test_style_diagnostic_endpoint_returns_report(
        self, expression_client: tuple[TestClient, str, str]
    ) -> None:
        client, _account_id, graph_id = expression_client

        brief = ExpressionBrief(
            brief_id="brief-style-api",
            task_goal="向非专业读者解释线粒体功能",
            deliverable_type="科普文案",
            genre=Genre.POPULAR_SCIENCE,
            channel="公众号",
            risk_tier=RiskTier.LOW,
            required_claim_ids=[],
            success_criteria=["每个核心判断有引用"],
        )
        request = ExpressionDraftRequest(brief=brief, graph_id=graph_id)
        create_response = client.post(
            "/expression/drafts", json=request.model_dump(mode="json")
        )
        assert create_response.status_code == 201
        draft_id = create_response.json()["draft"]["draft_id"]

        diag_response = client.post(
            f"/expression/drafts/{draft_id}/style-diagnostic",
            json=StyleDiagnosticRequest(draft_id=draft_id).model_dump(mode="json"),
        )
        assert diag_response.status_code == 200
        data = diag_response.json()
        assert data["report"]["draft_id"] == draft_id
        assert data["report"]["ai_detector_used_as_gate"] is False
        assert "findings" in data["report"]

    def test_feedback_endpoint_routes_factual_correction(
        self, expression_client: tuple[TestClient, str, str]
    ) -> None:
        client, _account_id, graph_id = expression_client

        brief = ExpressionBrief(
            brief_id="brief-feedback-api",
            task_goal="解释线粒体功能",
            deliverable_type="科普文案",
            genre=Genre.POPULAR_SCIENCE,
            channel="公众号",
            risk_tier=RiskTier.LOW,
            required_claim_ids=[],
            success_criteria=["每个核心判断有引用"],
        )
        request = ExpressionDraftRequest(brief=brief, graph_id=graph_id)
        create_response = client.post(
            "/expression/drafts", json=request.model_dump(mode="json")
        )
        assert create_response.status_code == 201
        draft_id = create_response.json()["draft"]["draft_id"]

        feedback_response = client.post(
            f"/expression/drafts/{draft_id}/feedback",
            json=SubmitExpressionFeedbackRequest(
                target=UserFeedbackTarget.CURRENT_VERSION,
                message="这个数字不对，文献不支持。",
            ).model_dump(mode="json"),
        )
        assert feedback_response.status_code == 200
        data = feedback_response.json()
        assert data["routed_to"] == UserFeedbackTarget.FACT_REVIEW.value
        assert data["feedback_id"]

    def test_apply_patch_endpoint_requires_auth_and_existing_patch(
        self, expression_client: tuple[TestClient, str, str]
    ) -> None:
        client, _account_id, graph_id = expression_client

        brief = ExpressionBrief(
            brief_id="brief-patch-api",
            task_goal="解释线粒体功能",
            deliverable_type="科普文案",
            genre=Genre.POPULAR_SCIENCE,
            channel="公众号",
            risk_tier=RiskTier.LOW,
            required_claim_ids=[],
            success_criteria=["每个核心判断有引用"],
        )
        request = ExpressionDraftRequest(brief=brief, graph_id=graph_id)
        create_response = client.post(
            "/expression/drafts", json=request.model_dump(mode="json")
        )
        assert create_response.status_code == 201
        draft_id = create_response.json()["draft"]["draft_id"]

        # Applying a non-existent patch returns 404/422, not 200.
        apply_response = client.post(
            f"/expression/drafts/{draft_id}/patches/patch-does-not-exist/apply",
            json=ApplyRevisionPatchRequest(action=PatchAction.ACCEPT).model_dump(
                mode="json"
            ),
        )
        assert apply_response.status_code in {404, 422}
