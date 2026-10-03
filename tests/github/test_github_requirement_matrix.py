"""改进工单 26：必要功能证据矩阵、约束与类型化需求（确定性合同）。

覆盖验收标准的确定性部分（真实 API/文件可得性由工单 42 实测）：

- 必要功能未支持时不因高比例、可选项或 stars 认定整体适配；
- 文档自述/静态实现/运行未验证在输出中明确区分，许可未知不宣称自由复用；
- 实现文件定位到版本与读取范围；
- 类型化需求（选定论文/岗位）身份未确认时不宣称对应实现；
- 内核收据恢复：重试复用已完成节点，不重复检索与读取。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from bridges.github.contracts import (
    GithubConstraintSet,
    GithubCoverage,
    GithubDeepCheckStatus,
    GithubFileRead,
    GithubIdeaAnalysis,
    GithubLicenseCheck,
    GithubReadmeStatus,
    GithubRepositoryEvidence,
    GithubRequirementInput,
    GithubRequirementKind,
    GithubSourceKind,
    GithubSupportLevel,
    GithubVersionEvidence,
)
from bridges.github.lexicon import extract_excluded_terms, extract_license_terms
from bridges.github.parsing import parse_github_request
from bridges.github.presenting import InsightOutcome, _feature_line
from bridges.github.ranking import match_features, rank_candidates
from bridges.github.service import _identity_note, _insights_of
from tests.chat.test_chat_api import _create_conversation, _register
from tests.github.test_github_module_flow import (
    WHOLE_IDEA,
    WHOLE_QUERY,
    _candidate,
    _FakeReader,
    _FakeSearchPort,
    _install_github_service,
    _run_and_read,
    _send,
)


def _analysis_with(
    *,
    features: list[str],
    optional: list[str] | None = None,
    constraints: dict[str, list[str]] | None = None,
    whole: bool = True,
    requirement: GithubRequirementInput | None = None,
) -> GithubIdeaAnalysis:
    return GithubIdeaAnalysis(
        original_request="校园二手书交换平台",
        scenario="校园二手书交换平台",
        features=features,
        optional_features=optional or [],
        tech_terms=[],
        constraints=GithubConstraintSet(**(constraints or {})),
        whole_idea=whole,
        component_terms=[] if whole else ["校园二手书交换平台"],
        requirement_source=requirement,
    )


def _readme_evidence(
    *,
    full_name: str = "demo/bookswap",
    readme_text: str | None = "发布书籍、搜索书籍、云同步都支持。",
    stars: int = 5,
    license: GithubLicenseCheck | None = None,
    files_read: list[GithubFileRead] | None = None,
    version: GithubVersionEvidence | None = None,
) -> GithubRepositoryEvidence:
    return GithubRepositoryEvidence(
        full_name=full_name,
        html_url=f"https://github.com/{full_name}",
        description="校园二手书交换平台",
        topics=[],
        language="Python",
        stars=stars,
        pushed_at=datetime(2026, 9, 1, tzinfo=UTC),
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
        license=license
        or GithubLicenseCheck(detected=False, note="上游元数据没有标注许可。"),
        readme_status=GithubReadmeStatus.READ,
        readme_url=f"https://github.com/{full_name}/blob/main/README.md",
        readme_text=readme_text,
        files_read=files_read or [],
        matched_query=WHOLE_QUERY,
        retrieved_at=datetime(2026, 9, 26, tzinfo=UTC),
        deep_checks=GithubDeepCheckStatus.DONE,
        version=version,
    )


def test_required_unsupported_is_not_whole_even_with_ratio_stars_and_optionals() -> None:
    """必要功能缺一项 + 可选项全中 + 万星，也只能按组件呈现。"""
    analysis = _analysis_with(
        features=["发布书籍", "搜索书籍", "线下交换"],
        optional=["云同步", "聊天"],
    )
    evidence = _readme_evidence(
        readme_text="发布书籍、搜索书籍、云同步、聊天都支持。",
        stars=99999,
    )

    outcome = rank_candidates(analysis, [evidence])
    recommendation = outcome.recommendations[0]

    assert recommendation.coverage is GithubCoverage.COMPONENT
    assert recommendation.required_supported_count == 2
    assert recommendation.required_feature_count == 3
    assert recommendation.matched_feature_count == 2
    required_rows = [
        row
        for row in recommendation.feature_matches
        if row.kind is GithubRequirementKind.REQUIRED
    ]
    gaps = [row for row in required_rows if row.support_level is not GithubSupportLevel.DOCUMENTED]
    assert [row.feature for row in gaps] == ["线下交换"]
    assert gaps[0].support_level is GithubSupportLevel.UNCONFIRMED
    assert "缺口" in recommendation.coverage_note


def test_optional_rows_are_listed_but_do_not_offset_required_gaps() -> None:
    """可选项进入矩阵供展示，但不计入必要功能的覆盖结论。"""
    analysis = _analysis_with(
        features=["发布书籍", "线下交换"],
        optional=["云同步"],
    )
    evidence = _readme_evidence(readme_text="发布书籍、线下交换、云同步都支持。")

    rows = match_features(analysis, evidence)
    optional = [row for row in rows if row.kind is GithubRequirementKind.OPTIONAL]

    assert [row.feature for row in optional] == ["云同步"]
    assert optional[0].matched is True

    outcome = rank_candidates(analysis, [evidence])
    assert outcome.recommendations[0].coverage is GithubCoverage.WHOLE
    assert outcome.recommendations[0].required_supported_count == 2


def test_runtime_requirement_is_never_reported_as_runnable() -> None:
    """用户要求能跑：静态读到清单也只标未确认，绝不声称已运行。"""
    analysis = _analysis_with(
        features=["发布书籍"],
        constraints={"runtime": ["能跑起来"]},
    )
    evidence = _readme_evidence(
        readme_text="发布书籍。",
        files_read=[
            GithubFileRead(
                path="package.json",
                kind="file",
                excerpt='{"scripts": {"start": "node server.js"}}',
            )
        ],
    )

    rows = match_features(analysis, evidence)
    runtime_rows = [
        row
        for row in rows
        if row.kind is GithubRequirementKind.CONSTRAINT and row.feature == "能跑起来"
    ]
    assert runtime_rows
    assert runtime_rows[0].support_level is GithubSupportLevel.UNCONFIRMED
    assert runtime_rows[0].runtime_required is True

    outcome = rank_candidates(analysis, [evidence])
    recommendation = outcome.recommendations[0]
    assert recommendation.runtime_verified is False
    assert recommendation.coverage is GithubCoverage.COMPONENT
    assert "不声称能跑" in recommendation.coverage_note


def test_unknown_license_never_claims_free_reuse() -> None:
    """许可不可得：约束行保持未确认，局限明说未知。"""
    analysis = _analysis_with(
        features=["发布书籍"],
        constraints={"license": ["可商用"]},
    )
    evidence = _readme_evidence(readme_text="发布书籍。")

    rows = match_features(analysis, evidence)
    license_row = next(
        row for row in rows if row.kind is GithubRequirementKind.CONSTRAINT
    )
    assert license_row.support_level is GithubSupportLevel.UNCONFIRMED
    assert "未知" in license_row.evidence

    outcome = rank_candidates(analysis, [evidence])
    recommendation = outcome.recommendations[0]
    assert recommendation.coverage is GithubCoverage.COMPONENT
    assert any("不声称" in item for item in recommendation.limitations)


def test_conflicting_license_is_rejected_with_a_stated_reason() -> None:
    """用户点名 MIT 而仓库是 GPL：许可条件不符，剔除并说明理由。"""
    analysis = _analysis_with(
        features=["发布书籍"],
        constraints={"license": ["MIT"]},
    )
    evidence = _readme_evidence(
        readme_text="发布书籍。",
        license=GithubLicenseCheck(
            detected=True,
            spdx_id="GPL-3.0",
            name="GNU General Public License v3.0",
            path="LICENSE",
            file_read=True,
            excerpt="GNU GENERAL PUBLIC LICENSE",
            note="已读取许可文件 LICENSE。",
        ),
    )

    outcome = rank_candidates(analysis, [evidence])

    assert outcome.recommendations == []
    assert any("许可条件不符" in item.reason for item in outcome.rejected)


def _license(spdx_id: str) -> GithubLicenseCheck:
    return GithubLicenseCheck(
        detected=True,
        spdx_id=spdx_id,
        name=f"{spdx_id} License",
        path="LICENSE",
        file_read=True,
        excerpt=f"{spdx_id} License",
        note="已读取许可文件 LICENSE。",
    )


def test_lgpl_and_agpl_are_not_mistaken_for_gpl() -> None:
    """LGPL/AGPL 里含 GPL 子串：用户点名 GPL 时不能判成满足条件。"""
    assert extract_license_terms("希望是 LGPL 许可") == ["LGPL"]
    assert extract_license_terms("AGPL 也行") == ["AGPL"]
    assert extract_license_terms("MIT 或 Apache-2.0") == ["MIT", "Apache-2.0"]

    analysis = _analysis_with(features=["发布书籍"], constraints={"license": ["GPL"]})
    for spdx_id in ("LGPL-3.0", "AGPL-3.0"):
        outcome = rank_candidates(
            analysis, [_readme_evidence(readme_text="发布书籍。", license=_license(spdx_id))]
        )
        assert outcome.recommendations == [], f"{spdx_id} 不能算满足 GPL 条件"
        assert any("许可条件不符" in item.reason for item in outcome.rejected)

    # 点名 LGPL 时，LGPL-3.0 才算一致。
    lgpl_analysis = _analysis_with(
        features=["发布书籍"], constraints={"license": ["LGPL"]}
    )
    lgpl_outcome = rank_candidates(
        lgpl_analysis, [_readme_evidence(readme_text="发布书籍。", license=_license("LGPL-3.0"))]
    )
    row = next(
        item
        for item in lgpl_outcome.recommendations[0].feature_matches
        if item.kind is GithubRequirementKind.CONSTRAINT
    )
    assert row.matched is True
    assert row.support_level is GithubSupportLevel.STATIC_IMPLEMENTATION


def test_non_essential_is_optional_not_an_exclusion() -> None:
    """「非必需」是可选标记，不能把中间的「必需」当成排除词。"""
    assert extract_excluded_terms("最好有云同步，非必需") == []
    assert extract_excluded_terms("不是必须") == []
    assert extract_excluded_terms("不要 Java，非必须") == ["Java"]


def test_tech_constraint_hit_carries_a_source() -> None:
    """命中的技术条件行也要能指回来源（元数据主要语言）。"""
    analysis = _analysis_with(
        features=["发布书籍"],
        constraints={"technical": ["Python"]},
    )
    evidence = _readme_evidence(readme_text="发布书籍。")

    rows = match_features(analysis, evidence)
    tech_row = next(
        row for row in rows if row.kind is GithubRequirementKind.CONSTRAINT
    )
    assert tech_row.matched is True
    assert tech_row.support_level is GithubSupportLevel.DOCUMENTED
    assert tech_row.sources
    assert tech_row.sources[0].kind is GithubSourceKind.METADATA
    assert tech_row.sources[0].read_range == "API 元数据（主要语言）"


def test_component_coverage_states_the_integration_work() -> None:
    """任务 4：组件路径要明说集成工作不在仓库范围内。"""
    analysis = _analysis_with(features=["发布书籍"], whole=False)
    evidence = _readme_evidence(readme_text="发布书籍。")

    outcome = rank_candidates(analysis, [evidence])

    note = outcome.recommendations[0].coverage_note
    assert outcome.recommendations[0].coverage is GithubCoverage.COMPONENT
    assert "集成工作" in note


def test_fixed_copy_renders_support_levels_and_runtime_gap() -> None:
    """固定正文渲染器要按支持层次与类别展示，不能退回「已覆盖」。"""
    documented = _feature_line(
        match_features(
            _analysis_with(features=["发布书籍"]),
            _readme_evidence(readme_text="发布书籍。"),
        )[0]
    )
    assert documented.startswith("[必要功能] 发布书籍：文档自述（")
    assert "来源：" in documented and "README 正文" in documented

    runtime = _feature_line(
        match_features(
            _analysis_with(features=["能跑起来"]),
            _readme_evidence(readme_text="能跑起来。"),
        )[0]
    )
    assert "未确认（未运行，不声称能跑）" in runtime


def test_reused_present_artifact_restores_insights() -> None:
    """重试复用 present 产物时，借鉴角度从产物回填而不是丢失。"""
    from types import SimpleNamespace

    from bridges.github.kernel import NODE_PRESENT

    class _StubResult:
        def __init__(self, payload: dict[str, Any]) -> None:
            self._payload = payload

        def artifact(self, node: str) -> Any:
            if node != NODE_PRESENT:
                return None
            return SimpleNamespace(payload=self._payload)

    payload = {
        "insights": {"demo/bookswap": "先看它的职责划分。"},
        "note": "解读是模型在证据范围内的归纳。",
    }
    flow = SimpleNamespace(last_insight=InsightOutcome())
    restored = _insights_of(_StubResult(payload), flow)
    assert restored.insights == {"demo/bookswap": "先看它的职责划分。"}
    assert restored.note == "解读是模型在证据范围内的归纳。"

    fresh = InsightOutcome(insights={"demo/bookswap": "本轮新解读。"})
    flow = SimpleNamespace(last_insight=fresh)
    assert _insights_of(_StubResult(payload), flow) is fresh


def test_implementation_sources_carry_version_and_read_range() -> None:
    """实现证据必须能指回提交版本与读取范围，选用理由带上版本。"""
    analysis = _analysis_with(features=["发布书籍"])
    version = GithubVersionEvidence(
        commit_sha="abcdef1234567890",
        ref="main",
        source="commit_api",
        obtained_at=datetime(2026, 9, 26, tzinfo=UTC),
        note="证据定位到默认分支 main 的提交 abcdef1（上游提交接口）。",
    )
    evidence = _readme_evidence(
        readme_text="项目说明见源码。",
        files_read=[
            GithubFileRead(
                path="src/books/publish.py",
                kind="file",
                excerpt=(
                    "# 发布书籍：学生发布想卖的书\n"
                    "def publish_book(request):\n    return request['book']"
                ),
                sha="1234567890abcdef",
            )
        ],
        version=version,
    )

    rows = match_features(analysis, evidence)
    required = [row for row in rows if row.kind is GithubRequirementKind.REQUIRED]
    assert required[0].support_level is GithubSupportLevel.STATIC_IMPLEMENTATION
    source = required[0].sources[0]
    assert source.kind is GithubSourceKind.IMPLEMENTATION_FILE
    assert source.commit_sha == "abcdef1234567890"
    assert source.read_range and "文件片段" in source.read_range

    outcome = rank_candidates(analysis, [evidence])
    recommendation = outcome.recommendations[0]
    assert recommendation.version is not None
    assert "abcdef1" in recommendation.reason_zh


def test_typed_requirement_without_confirmed_identity_is_not_overclaimed() -> None:
    """类型化需求来源未确认身份：只按原词检索，投影明确「不宣称对应实现」。"""
    requirement = GithubRequirementInput(
        kind="paper",
        label="选定的论文",
        identifier="2401.00001",
        phrase="联邦学习聚合",
        identity_confirmed=False,
        source_ref="paper:msg-9",
    )
    analysis = parse_github_request("找实现它的项目", requirement=requirement)

    assert analysis.clarification is None
    assert analysis.scenario == "联邦学习聚合"
    assert analysis.features == ["联邦学习聚合"]
    assert analysis.requirement_source == requirement
    note = _identity_note(analysis)
    assert note is not None and "不把仓库断言为它的实现" in note

    confirmed = requirement.model_copy(update={"identity_confirmed": True})
    confirmed_analysis = parse_github_request("找实现它的项目", requirement=confirmed)
    confirmed_note = _identity_note(confirmed_analysis)
    assert confirmed_note is not None
    assert "已确认" in confirmed_note
    assert "2401.00001" in confirmed_note


def _retry(client: TestClient, conversation_id: str, message_id: str) -> str:
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages/{message_id}/retry",
        json={},
    )
    assert response.status_code == 200, response.text
    return str(response.json()["assistant_message"]["message_id"])


def test_retry_reuses_completed_nodes_by_receipt(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """重试同一请求：解析/检索/读取/交付按收据复用，不重复外发请求。"""
    _register(client)
    port = _FakeSearchPort(
        per_query={WHOLE_QUERY: [_candidate("demo/a", description="校园二手书交换")]}
    )
    reader = _FakeReader(
        {
            "demo/a": _readme_evidence(
                full_name="demo/a",
                readme_text="学生可以发布想卖的书，也可以搜索想要的书，然后线下交换。",
            )
        }
    )
    _install_github_service(sqlite_app, port=port, reader=reader)
    conversation_id = _create_conversation(client)
    _send(client, conversation_id, WHOLE_IDEA, module_id="github")
    first = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)
    assert first["status"] == "done"
    assert first["github_projects"]["status"] == "success"

    search_queries = list(port.queries)
    inspected = list(reader.inspected)
    assert search_queries and inspected

    _retry(client, conversation_id, first["message_id"])
    generation_helpers["drive"](sqlite_app)
    final = client.get(f"/chat/conversations/{conversation_id}").json()
    latest = [m for m in final["messages"] if m["role"] == "assistant"][-1]
    assert latest["github_projects"]["status"] == "success"

    assert port.queries == search_queries, "重试必须复用检索收据，不重复外发"
    assert reader.inspected == inspected, "重试必须复用读取收据，不重复外发"

    database = sqlite_app.state.bridges_database
    rows = database.connection.execute(
        "SELECT node, COUNT(*) AS total FROM node_receipts GROUP BY node"
    ).fetchall()
    counts = {str(row["node"]): int(row["total"]) for row in rows}
    for node in ("github.parse", "github.search", "github.read", "github.present"):
        assert counts.get(node, 0) >= 1, f"{node} 必须有完成收据"


def test_actual_github_artifacts_export_backup_and_delete(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any], tmp_path: Path,
) -> None:
    """实际 GitHub 节点产物进入账户导出与备份，并随会话删除。"""
    import json
    import sqlite3
    import zipfile
    from io import BytesIO

    from bridges.lifecycle.backup import BACKUP_MAGIC, _fernet_for

    _register(client)
    port = _FakeSearchPort(
        per_query={WHOLE_QUERY: [_candidate("demo/a", description="校园二手书交换")]}
    )
    reader = _FakeReader({"demo/a": _readme_evidence(full_name="demo/a")})
    _install_github_service(sqlite_app, port=port, reader=reader)
    conversation_id = _create_conversation(client)
    _send(client, conversation_id, WHOLE_IDEA, module_id="github")
    result = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)
    assert result["status"] == "done"
    database = sqlite_app.state.bridges_database
    row = database.connection.execute(
        "SELECT account_id FROM conversations WHERE conversation_id = ?", (conversation_id,)
    ).fetchone()
    _, exported = sqlite_app.state.export_service.export_data(str(row["account_id"]))
    node_items = json.loads(exported)["categories"]["node_kernel"]["items"]
    assert "github.repository_evidence" in json.dumps(node_items)
    passphrase = "工单26备份验证口令"
    _, backup = sqlite_app.state.backup_service.create_backup(passphrase)
    manifest_bytes, _, payload = backup[len(BACKUP_MAGIC):].partition(b"\n")
    manifest = json.loads(manifest_bytes)
    archive_bytes = _fernet_for(passphrase, bytes.fromhex(manifest["salt"])).decrypt(payload)
    with zipfile.ZipFile(BytesIO(archive_bytes)) as archive:
        snapshot = tmp_path / "github-snapshot.db"
        snapshot.write_bytes(archive.read("bridges.db"))
    with sqlite3.connect(snapshot) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM node_artifacts WHERE node LIKE 'github.%'"
        ).fetchone()[0] >= 8
    from bridges.kernel.repository import NodeKernelRepository

    NodeKernelRepository(database).delete_for_conversation("other-account", conversation_id)
    assert database.connection.execute(
        "SELECT COUNT(*) FROM node_artifacts WHERE conversation_id = ?", (conversation_id,)
    ).fetchone()[0] >= 8
    response = client.delete(f"/chat/conversations/{conversation_id}")
    assert response.status_code == 204, response.text
    assert database.connection.execute("SELECT COUNT(*) FROM node_outbox").fetchone()[0] == 0
    for table in ("node_artifacts", "node_receipts"):
        assert database.connection.execute(
            f"SELECT COUNT(*) FROM {table} WHERE conversation_id = ?", (conversation_id,)
        ).fetchone()[0] == 0
