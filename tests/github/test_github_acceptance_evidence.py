"""工单26独立验收回归：需求保留、来源真实性与读取计划。"""

from __future__ import annotations

import base64
from typing import Any

import httpx
import pytest

from bridges.github.client import GithubApiClient
from bridges.github.contracts import (
    GithubCoverage,
    GithubFileRead,
    GithubImplementationCheck,
    GithubReadmeStatus,
    GithubRequirementInput,
    GithubSupportLevel,
)
from bridges.github.inspecting import GithubRepositoryReader
from bridges.github.parsing import parse_github_request
from bridges.github.ranking import match_features, rank_candidates
from tests.github.test_github_module_flow import _candidate
from tests.github.test_github_requirement_matrix import _analysis_with, _readme_evidence


def test_optional_and_seventh_required_survive_real_parser() -> None:
    analysis = parse_github_request(
        "校园二手书平台，发布书籍，搜索书籍，线下交换，收藏书籍，聊天消息，价格提醒，学校认证，最好有云同步"
    )
    assert "学校认证" in analysis.features
    assert "最好有云同步" not in analysis.features
    assert analysis.optional_features == ["最好有云同步"]


def test_typed_multiple_requirements_and_current_constraints_are_preserved() -> None:
    requirement = GithubRequirementInput(
        kind="job",
        label="岗位需求",
        phrase="发布书籍；搜索书籍；学校认证",
        source_ref="job:1",
        identity_confirmed=True,
    )
    analysis = parse_github_request(
        "找实现它的项目，必须用 Python，不要 Java", requirement=requirement
    )
    # 当前约束不能把明确的来源需求替换为「Python」场景。
    assert analysis.features == ["发布书籍", "搜索书籍", "学校认证"]
    assert analysis.requirement_source == requirement
    assert analysis.constraints.technical == ["Python"]


def test_directory_and_path_names_do_not_prove_implementation() -> None:
    evidence = _readme_evidence(
        readme_text="相关结构见目录。",
        files_read=[GithubFileRead(path="发布书籍", kind="dir", entries=["搜索书籍.py"])],
    )
    evidence.implementation_checks = [
        GithubImplementationCheck(
            claim="存在目录",
            path="发布书籍",
            status="confirmed",
            evidence="根目录存在。",
        )
    ]
    assert (
        match_features(_analysis_with(features=["发布书籍"]), evidence)[0].support_level
        is GithubSupportLevel.UNCONFIRMED
    )


@pytest.mark.parametrize("readme", [None, "只有发布书籍文档。"])
def test_partial_or_missing_readme_is_not_negative_proof(readme: str | None) -> None:
    evidence = _readme_evidence(readme_text=readme)
    if readme is None:
        evidence.readme_status = GithubReadmeStatus.NOT_FOUND
    row = match_features(_analysis_with(features=["学校认证"]), evidence)[0]
    assert row.support_level is GithubSupportLevel.UNCONFIRMED


def test_each_implementation_row_points_to_its_own_file() -> None:
    evidence = _readme_evidence(
        files_read=[
            GithubFileRead(path="src/publish.py", kind="file", excerpt="发布书籍实现"),
            GithubFileRead(path="src/search.py", kind="file", excerpt="搜索书籍实现"),
        ]
    )
    rows = match_features(_analysis_with(features=["发布书籍", "搜索书籍"]), evidence)
    assert rows[0].sources[0].locator == "src/publish.py"
    assert rows[1].sources[0].locator == "src/search.py"
    assert "搜索书籍" in (rows[1].sources[0].excerpt or "")


def test_readme_blob_is_not_a_commit_sha() -> None:
    evidence = _readme_evidence()
    evidence.readme_sha = "blob123"
    row = match_features(_analysis_with(features=["发布书籍"]), evidence)[0]
    assert row.sources[0].commit_sha is None
    assert row.sources[0].obtained_at == evidence.retrieved_at


def test_explicit_denial_cannot_be_whole() -> None:
    evidence = _readme_evidence(readme_text="发布书籍已支持。不支持搜索书籍。")
    outcome = rank_candidates(_analysis_with(features=["发布书籍", "搜索书籍"]), [evidence])
    assert outcome.recommendations[0].coverage is GithubCoverage.COMPONENT
    assert (
        outcome.recommendations[0].feature_matches[1].support_level
        is GithubSupportLevel.UNSUPPORTED
    )


def test_requested_implementation_is_not_satisfied_by_readme() -> None:
    analysis = _analysis_with(features=["发布书籍"])
    analysis.implementation_required = True
    outcome = rank_candidates(analysis, [_readme_evidence()])
    assert outcome.recommendations[0].coverage is GithubCoverage.COMPONENT
    assert (
        outcome.recommendations[0].feature_matches[0].support_level
        is GithubSupportLevel.UNCONFIRMED
    )


def test_reader_selects_actual_entry_without_readme_path_and_keeps_time_version() -> None:
    seen: list[str] = []

    def content(text: str) -> dict[str, Any]:
        return {
            "content": base64.b64encode(text.encode()).decode(),
            "encoding": "base64",
            "sha": "blob",
        }

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path.endswith("/readme"):
            return httpx.Response(200, json=content("校园书籍项目。"))
        if request.url.path.endswith("/contents"):
            return httpx.Response(200, json=[{"name": "main.py", "type": "file"}])
        if request.url.path.endswith("/contents/main.py"):
            return httpx.Response(200, json=content("def publish_book():\n    return '发布书籍'"))
        return httpx.Response(404, json={})

    reader = GithubRepositoryReader(
        GithubApiClient(client=httpx.Client(transport=httpx.MockTransport(handler)))
    )
    result = reader.inspect_candidates(
        "account", [_candidate("demo/books", description="校园书籍项目")], requirements=["发布书籍"]
    )
    evidence = result.evidence[0]
    assert [file.path for file in evidence.files_read] == ["main.py"]
    assert evidence.version is not None and evidence.version.commit_sha is None
    assert all(not path.endswith("/commits") for path in seen)
    assert (
        match_features(_analysis_with(features=["发布书籍"]), evidence)[0].support_level
        is GithubSupportLevel.STATIC_IMPLEMENTATION
    )


@pytest.mark.parametrize(
    "excerpt",
    ["# 发布书籍", "def publish_book(): ...", "# 发布书籍\ndef publish_book():\n    pass"],
)
def test_comment_or_stub_does_not_prove_static_implementation(excerpt: str) -> None:
    evidence = _readme_evidence(
        readme_text="项目说明。",
        files_read=[
            GithubFileRead(path="src/publish.py", kind="file", excerpt=excerpt),
        ],
    )
    row = match_features(_analysis_with(features=["发布书籍"]), evidence)[0]
    assert row.support_level is GithubSupportLevel.UNCONFIRMED


def test_whole_candidates_sort_before_components_with_same_supported_count() -> None:
    analysis = _analysis_with(features=["发布书籍"])
    whole = _readme_evidence(full_name="demo/whole", stars=1, readme_text="发布书籍")
    component = _readme_evidence(full_name="demo/component", stars=99999, readme_text="发布书籍")
    component.description = "独立发布模块"
    component.matched_query = "发布书籍"
    outcome = rank_candidates(analysis, [component, whole])
    assert [item.full_name for item in outcome.recommendations] == ["demo/whole", "demo/component"]
    assert outcome.recommendations[1].coverage is GithubCoverage.COMPONENT
