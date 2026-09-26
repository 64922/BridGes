"""Issue 16 端到端合同：显式派发、检索、证据读取、排序、等待与失败。

全部走真实 HTTP + SQLite + 后台执行器（假检索与假读取边界，无外网）：

- 只有逐消息 ``module_id=github`` 或点击建议才启动，未接入模块仍被拒绝；
- 只推荐真实取得证据的仓库，逐条给功能匹配、覆盖面、维护与许可证据；
- 只凭 README 自述命中的整体项目候选必须与 idea 有词汇重合，否则明确剔除；
- 限流／空结果／匹配不足时如实降级并写明实际查询词与局限；
- 澄清等待跨轮次恢复；指向前文的请求把前文原词带进检索并可追溯；
- 移除模块后的普通聊天不启动 GitHub 检索。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.ai.adapters import StreamChunk
from bridges.contracts.modules import ModuleQueryStatus
from bridges.github.contracts import (
    GithubFileRead,
    GithubLicenseCheck,
    GithubReadmeStatus,
    GithubRepositoryCandidate,
    GithubRepositoryEvidence,
)
from bridges.github.inspecting import InspectionOutcome
from bridges.github.searching import SearchOutcome, query_record
from bridges.github.service import GithubProjectsService
from tests.chat.test_chat_api import _create_conversation, _gateway_with, _register

WHOLE_IDEA = "我想做一个校园二手书交换平台，学生可以发布想卖的书，搜索想要的书，线下交换"
WHOLE_QUERY = "校园二手书交换"


@pytest.fixture
def sqlite_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    from bridges.api.main import create_app
    from bridges.config import get_settings

    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "api-test-secret-key")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    return create_app()


@pytest.fixture
def client(sqlite_app: Any) -> TestClient:
    return TestClient(sqlite_app)


class _SilentAdapter:
    """普通聊天用的静默流式适配器；GitHub 轮不该调用模型。"""

    def __init__(self) -> None:
        self.calls = 0

    def stream_call(
        self, capability: Any, run_context: Any, payload: dict[str, Any]
    ) -> Any:
        del capability, run_context, payload
        self.calls += 1
        yield StreamChunk(kind="delta", delta="好的。")


class _FakeSearchPort:
    """检索替身：按查询词返回脚本化候选，并记录收到的查询词。"""

    def __init__(
        self,
        *,
        per_query: dict[str, list[GithubRepositoryCandidate]] | None = None,
        status: ModuleQueryStatus = ModuleQueryStatus.SUCCESS,
        error_code: str | None = None,
        error_message: str | None = None,
        retryable: bool = True,
    ) -> None:
        self.queries: list[str] = []
        self._per_query = per_query or {}
        self._status = status
        self._error_code = error_code
        self._error_message = error_message
        self._retryable = retryable

    def search_repositories(
        self,
        account_id: str,
        *,
        query: str,
        reason: str,
        stop_event: object | None = None,
        deadline: float | None = None,
    ) -> SearchOutcome:
        del account_id, reason, stop_event, deadline
        self.queries.append(query)
        candidates = list(self._per_query.get(query, []))
        record = query_record(
            query=query,
            status=self._status if not candidates else ModuleQueryStatus.SUCCESS,
            evidence_count=len(candidates),
            error_code=self._error_code,
            error_message=self._error_message,
            retryable=self._retryable,
        )
        return SearchOutcome(query=query, candidates=candidates, record=record)


class _FakeReader:
    """读取替身：按仓库名返回脚本化证据，并记录读取过的仓库。"""

    def __init__(
        self,
        evidences: dict[str, GithubRepositoryEvidence] | None = None,
        *,
        rate_limited: bool = False,
    ) -> None:
        self.inspected: list[str] = []
        self._evidences = evidences or {}
        self._rate_limited = rate_limited

    def inspect_candidates(
        self,
        account_id: str,
        candidates: list[GithubRepositoryCandidate],
        *,
        stop_event: object | None = None,
        deadline: float | None = None,
    ) -> InspectionOutcome:
        del account_id, stop_event, deadline
        evidence: list[GithubRepositoryEvidence] = []
        records: list[Any] = []
        for candidate in candidates[:3]:
            self.inspected.append(candidate.full_name)
            found = self._evidences.get(candidate.full_name)
            if found is not None:
                evidence.append(found)
            records.append(
                query_record(
                    source="github_repository",
                    query=candidate.full_name,
                    status=(
                        ModuleQueryStatus.SUCCESS if found else ModuleQueryStatus.EMPTY
                    ),
                    evidence_count=1 if found else 0,
                )
            )
        return InspectionOutcome(
            evidence=evidence, records=records, rate_limited=self._rate_limited
        )


def _candidate(
    full_name: str,
    *,
    description: str | None,
    topics: list[str] | None = None,
    stars: int = 5,
) -> GithubRepositoryCandidate:
    return GithubRepositoryCandidate(
        full_name=full_name,
        html_url=f"https://github.com/{full_name}",
        description=description,
        topics=topics or [],
        language="Python",
        stars=stars,
        pushed_at=datetime(2026, 9, 1, tzinfo=UTC),
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
        matched_query=WHOLE_QUERY,
        source="github_search",
    )


def _evidence(
    full_name: str,
    *,
    description: str | None = "校园二手书交换平台",
    topics: list[str] | None = None,
    readme_text: str | None = "学生可以发布想卖的书，也可以搜索想要的书，然后线下交换。",
    readme_status: GithubReadmeStatus = GithubReadmeStatus.READ,
    files_read: list[GithubFileRead] | None = None,
    license: GithubLicenseCheck | None = None,
    stars: int = 5,
) -> GithubRepositoryEvidence:
    return GithubRepositoryEvidence(
        full_name=full_name,
        html_url=f"https://github.com/{full_name}",
        description=description,
        topics=topics or [],
        language="Python",
        stars=stars,
        pushed_at=datetime(2026, 9, 1, tzinfo=UTC),
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
        license=license
        or GithubLicenseCheck(
            detected=True,
            spdx_id="MIT",
            name="MIT License",
            path="LICENSE",
            file_read=True,
            excerpt="MIT License",
            note="已读取仓库中的许可文件 LICENSE。",
        ),
        readme_status=readme_status,
        readme_url=f"https://github.com/{full_name}/blob/main/README.md",
        readme_text=readme_text,
        files_read=files_read or [],
        matched_query=WHOLE_QUERY,
        retrieved_at=datetime(2026, 9, 26, tzinfo=UTC),
    )


def _install_github_service(
    app: Any,
    *,
    port: _FakeSearchPort,
    reader: _FakeReader | None = None,
) -> GithubProjectsService:
    """把 GitHub 模块的检索与读取边界换成替身（编排与父图派发保持真实）。"""
    service = GithubProjectsService(search=port, reader=reader or _FakeReader())
    app.state.github_projects_service = service
    app.state.chat_service._github_projects = service  # noqa: SLF001
    return service


def _send(
    client: TestClient,
    conversation_id: str,
    content: str,
    *,
    module_id: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"content": content}
    if module_id is not None:
        body["module_id"] = module_id
    response = client.post(f"/chat/conversations/{conversation_id}/messages", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def _run_and_read(
    app: Any, client: TestClient, drive: Any, conversation_id: str
) -> dict[str, Any]:
    drive(app)
    projection = client.get(f"/chat/conversations/{conversation_id}").json()
    assistant = [
        message for message in projection["messages"] if message["role"] == "assistant"
    ][-1]
    return assistant


def test_explicit_dispatch_recommends_grounded_repositories(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """显式选择 GitHub 模块：只推荐有真实证据的仓库，逐项给出功能匹配与证据。"""
    _register(client)
    port = _FakeSearchPort(
        per_query={
            WHOLE_QUERY: [
                _candidate("253936563/huanshu", description="校园二手书交换"),
                _candidate("demo/other", description="别的东西"),
            ]
        }
    )
    reader = _FakeReader(
        {
            "253936563/huanshu": _evidence(
                "253936563/huanshu",
                files_read=[
                    GithubFileRead(
                        path="src/books/publish.py",
                        kind="file",
                        excerpt="def publish_book(request):  # 发布想卖的书",
                    )
                ],
            ),
            "demo/other": _evidence(
                "demo/other",
                description="别的东西",
                readme_text="与本轮要点无关的自述。",
            ),
        }
    )
    _install_github_service(sqlite_app, port=port, reader=reader)
    adapter = _SilentAdapter()
    sqlite_app.state.chat_service._gateway = _gateway_with(adapter)  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, WHOLE_IDEA, module_id="github")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

    assert assistant["status"] == "done"
    projects = assistant["github_projects"]
    assert projects["status"] == "success"
    assert projects["scenario"] == "校园二手书交换平台"
    assert port.queries[0] == WHOLE_QUERY
    assert reader.inspected == ["253936563/huanshu", "demo/other"]

    recommendation = projects["recommendations"][0]
    assert recommendation["full_name"] == "253936563/huanshu"
    assert recommendation["html_url"] == "https://github.com/253936563/huanshu"
    assert recommendation["coverage"] == "whole"
    assert recommendation["license"]["file_read"] is True
    matched = {
        item["feature"]: item for item in recommendation["feature_matches"]
    }
    assert matched["学生可以发布想卖的书"]["matched"] is True
    assert matched["学生可以发布想卖的书"]["evidence_kind"] == "implementation"
    # 命中片段按原词合并展示（相邻片段合成完整词，不再罗列「发布想、布想卖」）。
    assert any("发布" in term for term in matched["学生可以发布想卖的书"]["matched_terms"])

    # 与 idea 毫无词汇重合的候选被明确剔除，理由写进投影。
    rejected = {item["full_name"]: item["reason"] for item in projects["rejected"]}
    assert "demo/other" in rejected
    assert "名称、简介与话题里都没有出现你整体想法的关键词" in rejected["demo/other"]

    # 正文如实呈现：实际查询词只列检索词，读取记录不混进来。
    assert f"实际查询词：{WHOLE_QUERY}" in assistant["content"]
    assert "253936563/huanshu" in assistant["content"]
    assert "链接：https://github.com/253936563/huanshu" in assistant["content"]
    assert "不对内部架构" in assistant["content"]
    assert adapter.calls == 0  # GitHub 轮不调用模型


def test_metadata_only_degradation_when_rate_limited(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """限流：保留已取得的证据，状态降级并给出可重试与额度说明。"""
    _register(client)
    port = _FakeSearchPort(
        per_query={WHOLE_QUERY: [_candidate("demo/a", description="校园二手书交换")]}
    )
    reader = _FakeReader({}, rate_limited=True)
    _install_github_service(sqlite_app, port=port, reader=reader)
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, WHOLE_IDEA, module_id="github")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

    projects = assistant["github_projects"]
    assert projects["status"] in {"metadata_only", "empty"}
    assert projects["rate_limit"]["limited"] is True
    assert projects["retryable"] is True
    assert "限流" in assistant["content"] or "额度" in assistant["content"]


def test_empty_search_reports_the_real_queries(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """上游没有候选：如实说没有，并写出真正发过的查询词。"""
    _register(client)
    port = _FakeSearchPort(per_query={})
    _install_github_service(sqlite_app, port=port)
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, WHOLE_IDEA, module_id="github")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

    projects = assistant["github_projects"]
    assert projects["status"] == "empty"
    assert projects["recommendations"] == []
    assert projects["empty_reason"]
    assert WHOLE_QUERY in assistant["content"]
    assert "没有可推荐" in assistant["content"] or "没有返回" in assistant["content"]


def test_search_failure_marks_the_message_with_a_stable_code(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """检索失败：同一条消息收敛为失败态并留下稳定错误码。"""
    _register(client)
    port = _FakeSearchPort(
        status=ModuleQueryStatus.ERROR,
        error_code="github_unavailable",
        error_message="GitHub 接口暂时不可用。",
        retryable=True,
    )
    _install_github_service(sqlite_app, port=port)
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, WHOLE_IDEA, module_id="github")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

    projects = assistant["github_projects"]
    assert projects["status"] == "error"
    assert projects["error_code"] == "github_unavailable"
    assert projects["retryable"] is True
    assert "GitHub 接口暂时不可用。" in assistant["content"]


def test_clarification_waits_and_resumes_with_the_same_module(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """说不清找什么时先问一句；下一条回复带着模块继续，并沿用首次请求。"""
    account = _register(client)
    port = _FakeSearchPort(
        per_query={WHOLE_QUERY: [_candidate("demo/a", description="校园二手书交换")]}
    )
    _install_github_service(
        sqlite_app,
        port=port,
        reader=_FakeReader(
            {"demo/a": _evidence("demo/a", description="校园二手书交换平台")}
        ),
    )
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, "有没有人做过类似的项目", module_id="github")
    first = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

    assert first["github_projects"]["status"] == "clarification"
    pending = first["github_projects"]["pending"]
    assert pending["module_id"] == "github"
    assert pending["question"] in first["content"]
    # 等待状态写进了运行表：跨轮次恢复的依据。
    run = sqlite_app.state.chat_service._repo.get_run_by_message(  # noqa: SLF001
        account["id"], first["message_id"]
    )
    assert run is not None
    assert run.wait_reason == "github_clarification"
    assert port.queries == []  # 提问阶段不发起任何外部检索

    # 用户下一句给出主题（同一模块），检索才开始。
    _send(client, conversation_id, WHOLE_IDEA, module_id="github")
    second = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

    projects = second["github_projects"]
    assert projects["status"] == "success"
    assert port.queries[0] == WHOLE_QUERY
    # 恢复轮沿用首次请求：澄清问题与用户首次原话都留在投影里。
    assert projects["original_request"] == "有没有人做过类似的项目"
    assert projects["pending"] is None


def test_reference_to_prior_paper_turn_uses_its_original_phrase(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """「找实现它的项目」指向论文搜索的前文原词，并可追溯到具体消息。"""
    _register(client)
    port = _FakeSearchPort(
        per_query={
            "联邦学习": [
                _candidate("demo/fedavg", description="联邦学习算法实现")
            ]
        }
    )
    _install_github_service(
        sqlite_app,
        port=port,
        reader=_FakeReader(
            {
                "demo/fedavg": _evidence(
                    "demo/fedavg",
                    description="联邦学习算法实现",
                    readme_text="本项目实现联邦学习中的 FedAvg 算法。",
                )
            }
        ),
    )
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    # 前一轮：论文搜索（真实模块路径，不用假服务）。
    _send(client, conversation_id, "联邦学习", module_id="paper")
    paper_turn = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )
    paper_message_id = paper_turn["message_id"]
    assert paper_turn["paper_search"] is not None

    # 这一轮：用「找实现它的项目」指向它。
    _send(client, conversation_id, "帮我找实现它的项目", module_id="github")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

    projects = assistant["github_projects"]
    assert projects["status"] == "success"
    assert projects["scenario"] == "联邦学习"
    assert port.queries == ["联邦学习"]
    # 前文依据可追溯到承载它的那条助手消息。
    assert projects["context_source"]["phrase"] == "联邦学习"
    assert projects["context_source"]["message_id"] == paper_message_id
    assert "前文依据：上一轮论文搜索的原始词「联邦学习」" in assistant["content"]


def test_plain_chat_without_the_module_never_starts_github(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """普通聊天：只给一键建议，不启动检索；移除模块后仍不启动。"""
    _register(client)
    port = _FakeSearchPort(per_query={WHOLE_QUERY: []})
    _install_github_service(sqlite_app, port=port)
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, "有没有人做过类似的项目")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

    assert assistant.get("github_projects") is None
    assert port.queries == []
    suggestion = assistant["module_suggestion"]
    assert suggestion["module_id"] == "github"
    assert suggestion["needs_disambiguation"] is True
