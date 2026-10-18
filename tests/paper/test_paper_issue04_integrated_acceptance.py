"""工单 04：原始用户旅程的集成验收。

覆盖「帮我找几篇机器学习入门论文」的确定性完整链路，以及单源失败、
全部来源失败、全部/部分语义证据无效、未知需求标识、取消、失败后重试
和重载恢复。全部使用隔离 SQLite、合成账户、确定性模型网关与来源替身，
真实经过父图派发、论文内核、终态提交、消息投影、SSE 与查询记录。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.contracts.modules import ModuleQueryStatus
from tests.chat.test_chat_api import _parse_sse, _register
from tests.paper.issue04_support import (
    GOOD_ARXIV,
    GOOD_OPENALEX,
    PARTIAL_OPENALEX,
    UNKNOWN_OPENALEX,
    _artifacts,
    _journey,
    _PaperGateway,
)
from tests.paper.test_paper_module_flow import _FakePaperSource


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


def test_original_user_journey_delivers_verified_papers(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    gateway = _PaperGateway()
    arxiv = _FakePaperSource(candidates=[GOOD_ARXIV], source="arxiv")
    openalex = _FakePaperSource(candidates=[GOOD_OPENALEX], source="openalex")
    assistant, _created, conversation_id, message_id = _journey(
        sqlite_app,
        client,
        generation_helpers["drive"],
        gateway=gateway,
        arxiv=arxiv,
        openalex=openalex,
    )

    assert assistant["status"] == "done"
    assert assistant["error_code"] is None
    paper = assistant["paper_search"]
    assert paper["status"] == "success"
    assert paper["selected_sources"] == ["arxiv", "openalex"]
    assert [item["source"] for item in paper["queries"]] == ["arxiv", "openalex"]
    assert all(item["status"] == "success" for item in paper["queries"])
    assert paper["papers"]
    assert {item["title"] for item in paper["papers"]} == {
        GOOD_ARXIV.title,
        GOOD_OPENALEX.title,
    }
    candidates = {item.title: item for item in (GOOD_ARXIV, GOOD_OPENALEX)}
    for recommendation in paper["papers"]:
        source_text = candidates[recommendation["title"]]
        requirements = {
            item["requirement"] for item in recommendation["match_evidence"]
        }
        assert requirements == {"Machine Learning", "Introduction"}
        for evidence in recommendation["match_evidence"]:
            haystack = {
                "title": source_text.title,
                "abstract": source_text.abstract,
            }[evidence["source"]]
            assert evidence["quote"] in haystack, (
                "推荐证据必须逐字来自本轮取得的标题或摘要"
            )
        assert recommendation["read_scope"] == "abstract"
        assert any("未通读全文" in item for item in recommendation["unverified"])
        assert recommendation["source_abstract"]
        assert "全文" not in str(recommendation.get("read_evidence") or [])

    assert gateway.calls[:3] == ["intent", "plan", "judge"]
    assert gateway.calls.count("judge") == 1

    events = _parse_sse(
        client.get(
            f"/chat/conversations/{conversation_id}/messages/{message_id}/events"
        ).text
    )
    started = next(payload for name, payload in events if name == "started")
    done = [payload for name, payload in events if name == "done"][-1]
    assert started.get("arxiv_search") is None, "论文轮不得预写旧的 arXiv loading 卡"
    assert done["message"]["paper_search"]["status"] == "success"
    assert done["message"]["paper_search"]["selected_sources"] == ["arxiv", "openalex"]

    resumed = _parse_sse(
        client.get(
            f"/chat/conversations/{conversation_id}/messages/{message_id}/events",
            params={"cursor": 10},
        ).text
    )
    assert resumed == events[10:], "断线后按游标恢复只能回放缺口之后的事件"

    refreshed = client.get(f"/chat/conversations/{conversation_id}").json()
    stored = [
        item
        for item in refreshed["messages"]
        if item["message_id"] == message_id
    ][0]
    assert stored["paper_search"] == paper
    assert stored["status"] == "done"


def test_display_name_case_does_not_break_identifier_mapping(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    gateway = _PaperGateway(display_name="machine learning")
    arxiv = _FakePaperSource(candidates=[GOOD_ARXIV], source="arxiv")
    openalex = _FakePaperSource(candidates=[GOOD_OPENALEX], source="openalex")
    assistant, _created, _conversation_id, _message_id = _journey(
        sqlite_app,
        client,
        generation_helpers["drive"],
        gateway=gateway,
        arxiv=arxiv,
        openalex=openalex,
    )

    paper = assistant["paper_search"]
    assert assistant["status"] == "done"
    assert paper["status"] == "success"
    assert paper["papers"]
    assert {
        item["requirement"] for item in paper["papers"][0]["match_evidence"]
    } == {"machine learning", "Introduction"}


def test_arxiv_failure_still_delivers_openalex_papers(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    gateway = _PaperGateway()
    arxiv = _FakePaperSource(
        status=ModuleQueryStatus.ERROR,
        error_code="arxiv_request",
        error_message="arXiv 未接受本次查询参数（HTTP 400）；本轮未使用该来源。",
        retryable=False,
        source="arxiv",
    )
    openalex = _FakePaperSource(candidates=[GOOD_OPENALEX], source="openalex")
    assistant, _created, conversation_id, message_id = _journey(
        sqlite_app,
        client,
        generation_helpers["drive"],
        gateway=gateway,
        arxiv=arxiv,
        openalex=openalex,
    )

    assert assistant["status"] == "done", assistant.get("error_code")
    paper = assistant["paper_search"]
    assert paper["status"] == "success"
    assert paper["selected_sources"] == ["arxiv", "openalex"]
    records = {item["source"]: item for item in paper["queries"]}
    assert records["arxiv"]["status"] == "error"
    assert records["arxiv"]["error_code"] == "arxiv_request"
    assert records["openalex"]["status"] == "success"
    assert [item["title"] for item in paper["papers"]] == [GOOD_OPENALEX.title]
    assert any("arxiv 本轮失败" in note for note in paper["evidence_notes"]), paper[
        "evidence_notes"
    ]

    refreshed = client.get(f"/chat/conversations/{conversation_id}").json()
    stored = next(
        item for item in refreshed["messages"] if item["message_id"] == message_id
    )
    assert stored["paper_search"] == paper


def test_all_sources_failing_keeps_terminal_state_and_actual_sources(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    gateway = _PaperGateway()
    arxiv = _FakePaperSource(
        status=ModuleQueryStatus.ERROR,
        error_code="arxiv_request",
        error_message="arXiv 未接受本次查询参数（HTTP 400）。",
        retryable=False,
        source="arxiv",
    )
    openalex = _FakePaperSource(
        status=ModuleQueryStatus.TIMEOUT,
        error_code="openalex_timeout",
        error_message="OpenAlex 检索超时。",
        retryable=True,
        source="openalex",
    )
    assistant, _created, conversation_id, message_id = _journey(
        sqlite_app,
        client,
        generation_helpers["drive"],
        gateway=gateway,
        arxiv=arxiv,
        openalex=openalex,
    )

    assert assistant["status"] == "error"
    assert assistant["error_code"] == "paper_search_failed"
    paper = assistant["paper_search"]
    assert paper["status"] == "error"
    assert paper["retryable"] is True
    assert paper["selected_sources"] == ["arxiv", "openalex"]
    assert paper["papers"] == []
    assert paper["status"] != "searching"
    records = {item["source"]: item for item in paper["queries"]}
    assert records["arxiv"]["error_code"] == "arxiv_request"
    assert records["openalex"]["error_code"] == "openalex_timeout"
    events = _parse_sse(
        client.get(
            f"/chat/conversations/{conversation_id}/messages/{message_id}/events"
        ).text
    )
    error = [payload for name, payload in events if name == "error"][-1]
    assert error["error"]["code"] == "paper_search_failed"
    assert error["error"]["retryable"] is True
    assert error["paper_search"]["status"] == "error"
    assert error["paper_search"]["selected_sources"] == ["arxiv", "openalex"]


def test_all_invalid_evidence_fails_honestly_without_recommendations(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    gateway = _PaperGateway(judge_mode="all_invalid")
    arxiv = _FakePaperSource(candidates=[GOOD_ARXIV], source="arxiv")
    openalex = _FakePaperSource(candidates=[GOOD_OPENALEX], source="openalex")
    assistant, _created, conversation_id, _message_id = _journey(
        sqlite_app,
        client,
        generation_helpers["drive"],
        gateway=gateway,
        arxiv=arxiv,
        openalex=openalex,
    )

    assert assistant["status"] == "error"
    assert assistant["error_code"] == "paper_semantic_evaluation_unavailable"
    paper = assistant["paper_search"]
    assert paper["status"] == "error"
    assert paper["retryable"] is True
    assert paper["papers"] == []
    assert {item["status"] for item in paper["queries"]} == {"success"}

    account = client.app.state.identity_service.resolve_session(
        client.cookies.get("bridges_session")
    ).subject
    artifacts = _artifacts(sqlite_app, account.account_id, conversation_id)
    screen = artifacts["paper.screen"]
    assert screen.payload["semantic_rejection_counts"] == {
        "semantic_quote_not_found": 2
    }
    assert screen.payload["screened"] == []
    assert all(
        item["reason"] == "semantic_quote_not_found"
        for item in screen.payload["excluded"]
    )


def test_partial_evidence_and_unknown_identifier_are_separated(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    gateway = _PaperGateway(judge_mode="mixed")
    arxiv = _FakePaperSource(candidates=[GOOD_ARXIV], source="arxiv")
    openalex = _FakePaperSource(
        candidates=[PARTIAL_OPENALEX, UNKNOWN_OPENALEX], source="openalex"
    )
    assistant, _created, conversation_id, _message_id = _journey(
        sqlite_app,
        client,
        generation_helpers["drive"],
        gateway=gateway,
        arxiv=arxiv,
        openalex=openalex,
    )

    assert assistant["status"] == "done", assistant.get("error_code")
    paper = assistant["paper_search"]
    assert paper["status"] == "success"
    titles = {item["title"] for item in paper["papers"]}
    assert GOOD_ARXIV.title in titles
    assert PARTIAL_OPENALEX.title in titles, "一条有效证据应保留该候选"
    assert UNKNOWN_OPENALEX.title not in titles
    partial = next(
        item for item in paper["papers"] if item["title"] == PARTIAL_OPENALEX.title
    )
    assert {item["requirement"] for item in partial["match_evidence"]} == {
        "Machine Learning"
    }
    assert any("未作为已核验推荐交付" in note for note in paper["evidence_notes"])
    assert any("未知需求标识" in note for note in paper["evidence_notes"])


def test_unknown_requirement_only_cannot_become_recommendation(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    gateway = _PaperGateway(judge_mode="unknown_requirement")
    arxiv = _FakePaperSource(candidates=[GOOD_ARXIV], source="arxiv")
    openalex = _FakePaperSource(candidates=[GOOD_OPENALEX], source="openalex")
    assistant, _created, conversation_id, _message_id = _journey(
        sqlite_app,
        client,
        generation_helpers["drive"],
        gateway=gateway,
        arxiv=arxiv,
        openalex=openalex,
    )

    assert assistant["status"] == "error"
    assert assistant["error_code"] == "paper_semantic_evaluation_unavailable"
    assert assistant["paper_search"]["papers"] == []
    account = client.app.state.identity_service.resolve_session(
        client.cookies.get("bridges_session")
    ).subject
    screen = _artifacts(sqlite_app, account.account_id, conversation_id)["paper.screen"]
    assert screen.payload["semantic_rejection_counts"] == {
        "semantic_requirement_unknown": 2
    }


def test_cancel_preserves_actual_sources_without_failure_recovery(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    gateway = _PaperGateway()
    arxiv = _FakePaperSource(
        candidates=[GOOD_ARXIV], source="arxiv", stop_on_search=True
    )
    openalex = _FakePaperSource(candidates=[GOOD_OPENALEX], source="openalex")
    assistant, _created, conversation_id, message_id = _journey(
        sqlite_app,
        client,
        generation_helpers["drive"],
        gateway=gateway,
        arxiv=arxiv,
        openalex=openalex,
    )

    assert assistant["status"] == "stopped"
    paper = assistant["paper_search"]
    assert paper["status"] == "stopped"
    assert paper["selected_sources"] == ["arxiv", "openalex"]
    assert paper["papers"] == []
    assert all(
        item["status"] in {"success", "cancelled"} for item in paper["queries"]
    )
    assert assistant["turn_result"]["outcome"] == "cancelled"
    assert assistant["turn_result"]["recovery"] is None
    events = _parse_sse(
        client.get(
            f"/chat/conversations/{conversation_id}/messages/{message_id}/events"
        ).text
    )
    error = [payload for name, payload in events if name == "error"][-1]
    assert error["error"]["code"] == "stopped"
    assert error["paper_search"]["status"] == "stopped"
    assert error["paper_search"]["selected_sources"] == ["arxiv", "openalex"]

    arxiv.stop_on_search = False
    retried = client.post(
        f"/chat/conversations/{conversation_id}/messages/{message_id}/retry",
        json={},
    )
    assert retried.status_code == 200, retried.text
    generation_helpers["drive"](sqlite_app)
    refreshed = client.get(f"/chat/conversations/{conversation_id}").json()
    by_id = {item["message_id"]: item for item in refreshed["messages"]}
    assert by_id[message_id]["status"] == "stopped"
    assert by_id[retried.json()["assistant_message"]["message_id"]]["status"] == "done"


def test_failure_retry_creates_new_attempt_without_old_state_override(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    gateway = _PaperGateway(judge_mode="all_invalid")
    arxiv = _FakePaperSource(candidates=[GOOD_ARXIV], source="arxiv")
    openalex = _FakePaperSource(candidates=[GOOD_OPENALEX], source="openalex")
    assistant, created, conversation_id, failed_message_id = _journey(
        sqlite_app,
        client,
        generation_helpers["drive"],
        gateway=gateway,
        arxiv=arxiv,
        openalex=openalex,
    )
    assert assistant["status"] == "error"

    gateway.judge_mode = "valid"
    retried = client.post(
        f"/chat/conversations/{conversation_id}/messages/{failed_message_id}/retry",
        json={},
    )
    assert retried.status_code == 200, retried.text
    new_message_id = retried.json()["assistant_message"]["message_id"]
    assert new_message_id != failed_message_id
    generation_helpers["drive"](sqlite_app)

    refreshed = client.get(f"/chat/conversations/{conversation_id}").json()
    by_id = {item["message_id"]: item for item in refreshed["messages"]}
    assert by_id[failed_message_id]["status"] == "error"
    assert by_id[failed_message_id]["paper_search"]["status"] == "error"
    assert by_id[new_message_id]["status"] == "done"
    assert by_id[new_message_id]["paper_search"]["status"] == "success"
    assert by_id[new_message_id]["paper_search"]["papers"]

    old_events = _parse_sse(
        client.get(
            f"/chat/conversations/{conversation_id}/messages/"
            f"{failed_message_id}/events"
        ).text
    )
    assert not [name for name, _payload in old_events if name == "done"]
    new_events = _parse_sse(
        client.get(
            f"/chat/conversations/{conversation_id}/messages/"
            f"{new_message_id}/events"
        ).text
    )
    done = [payload for name, payload in new_events if name == "done"][-1]
    assert done["message"]["paper_search"]["status"] == "success"
    assert done["message"]["paper_search"]["selected_sources"] == ["arxiv", "openalex"]


def test_reload_and_cross_account_do_not_reuse_old_or_foreign_state(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    gateway = _PaperGateway()
    arxiv = _FakePaperSource(candidates=[GOOD_ARXIV], source="arxiv")
    openalex = _FakePaperSource(candidates=[GOOD_OPENALEX], source="openalex")
    assistant, _created, conversation_id, message_id = _journey(
        sqlite_app,
        client,
        generation_helpers["drive"],
        gateway=gateway,
        arxiv=arxiv,
        openalex=openalex,
    )
    assert assistant["paper_search"]["status"] == "success"
    first = client.get(f"/chat/conversations/{conversation_id}").json()
    second = client.get(f"/chat/conversations/{conversation_id}").json()
    assert first == second
    stored = next(item for item in first["messages"] if item["message_id"] == message_id)
    assert stored["paper_search"] == assistant["paper_search"]
    assert stored["paper_search"]["status"] != "searching"

    other = TestClient(sqlite_app)
    _register(other, tag="2")
    assert other.get(f"/chat/conversations/{conversation_id}").status_code == 404
    assert (
        other.get(
            f"/chat/conversations/{conversation_id}/messages/{message_id}/events"
        ).status_code
        == 404
    )
