"""独立验收：故障注入不能被生产节点的自报结论掩盖。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from bridges.github.kernel import (
    GITHUB_GATE_HANDLERS,
    NODE_EVALUATE,
    NODE_MATCH,
    NODE_PARSE,
    NODE_READ,
    NODE_VERIFY,
    GithubNodeFlow,
    build_github_recipe,
)
from bridges.github.ranking import match_features, rank_candidates
from bridges.kernel.contracts import (
    ArtifactTrust,
    NodeArtifact,
    NodeInvocation,
    QualityVerdict,
    RecipeInputs,
)
from tests.github.test_github_module_flow import _FakeReader, _FakeSearchPort
from tests.github.test_github_requirement_matrix import _analysis_with, _readme_evidence


def _artifact(node: str, payload: dict[str, Any]) -> NodeArtifact:
    return NodeArtifact.build(
        account_id="账户", conversation_id="会话", run_id="运行",
        task_id=None, task_version=None,
        recipe_id="github-project-recommendation", recipe_version="github-recipe-v1",
        node=node, artifact_type=node, capability_version="v1",
        trust_state=ArtifactTrust.EVIDENCE_BOUND, input_key=node,
        input_deps=(), source_refs=(), read_scope="验收证据",
        requirement_coverage=(), unconfirmed=(), error=None, payload=payload,
        now=datetime(2026, 10, 3, tzinfo=UTC),
    )


def _verify(
    *, mutation: str | None = None, missing_source: str | None = None,
) -> dict[str, Any]:
    analysis = _analysis_with(features=["发布书籍", "搜索书籍"])
    evidence = _readme_evidence()
    recommendations = rank_candidates(analysis, [evidence]).recommendations
    recommendation = recommendations[0].model_dump(mode="json")
    rows = [row.model_dump(mode="json") for row in match_features(analysis, evidence)]
    if mutation == "whole_with_gap":
        analysis.features.append("语音聊天")
        rows = [row.model_dump(mode="json") for row in match_features(analysis, evidence)]
        recommendation["feature_matches"] = rows
        recommendation["coverage"] = "whole"
    elif mutation == "unconfirmed_matched":
        recommendation["feature_matches"][0]["matched"] = True
        recommendation["feature_matches"][0]["support_level"] = "unconfirmed"
    elif mutation == "missing_required":
        recommendation["feature_matches"] = recommendation["feature_matches"][:1]
    elif mutation == "runtime_verified":
        recommendation["runtime_verified"] = True
    elif mutation == "license_undisclosed":
        recommendation["limitations"] = []
    elif mutation == "license_forged":
        recommendation["license"]["detected"] = True
        recommendation["license"]["file_read"] = True
    elif mutation == "version_forged":
        recommendation["version"] = {"commit_sha": "伪造的提交"}
    if missing_source:
        source = recommendation["feature_matches"][0]["sources"][0]
        source[missing_source] = None
        if missing_source == "obtained_at":
            source["commit_sha"] = None
    artifacts = {
        NODE_PARSE: _artifact(NODE_PARSE, {"analysis": analysis.model_dump(mode="json")}),
        NODE_READ: _artifact(NODE_READ, {"evidence": [evidence.model_dump(mode="json")]}),
        NODE_MATCH: _artifact(NODE_MATCH, {"matrix": {evidence.full_name: rows}}),
        NODE_EVALUATE: _artifact(NODE_EVALUATE, {"ranked": {"recommendations": [recommendation]}}),
    }
    inputs = RecipeInputs(
        account_id="账户", conversation_id="会话", run_id="运行",
        user_message_id="消息", user_content="推荐校园二手书项目",
        task_id=None, task_version=None, wait_identity=None, artifacts=artifacts,
    )
    invocation = NodeInvocation(
        spec=build_github_recipe().node(NODE_VERIFY), inputs=inputs,
        dependencies=artifacts, remaining_budget_ms=30000,
    )
    flow = GithubNodeFlow(search=_FakeSearchPort(), reader=_FakeReader())
    execution = flow.run_node(invocation)
    assert execution.verdict is QualityVerdict.PASS
    return {
        name: gate(invocation, execution)
        for name, gate in GITHUB_GATE_HANDLERS.items()
    }


def test_actual_supported_recommendation_passes_all_gates() -> None:
    assert all(result.passed for result in _verify().values())


@pytest.mark.parametrize("mutation", [
    "whole_with_gap", "unconfirmed_matched", "missing_required", "runtime_verified",
])
def test_required_gate_rejects_wrong_actual_recommendation(mutation: str) -> None:
    assert not _verify(mutation=mutation)["github.required_matrix"].passed


@pytest.mark.parametrize("field", ["locator", "read_range", "obtained_at"])
def test_source_gate_requires_location_range_and_version(field: str) -> None:
    assert not _verify(missing_source=field)["github.version_sources"].passed


def test_source_gate_rejects_version_not_read() -> None:
    assert not _verify(mutation="version_forged")["github.version_sources"].passed


@pytest.mark.parametrize("mutation", ["license_undisclosed", "license_forged"])
def test_license_gate_checks_actual_disclosure_and_evidence(mutation: str) -> None:
    assert not _verify(mutation=mutation)["github.license_disclosure"].passed
