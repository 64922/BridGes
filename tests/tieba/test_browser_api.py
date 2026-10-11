"""专用窗口操作必须来自认证会话，不能由请求体选择另一账户。"""

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.api.main import create_app
from bridges.closeout.manifest import discover_api_routes
from bridges.config import get_settings
from bridges.contracts.modules import ModuleQueryStatus
from bridges.tieba.contracts import ReadStatus, TiebaReply, TiebaSearchHit
from bridges.tieba.parsing import parse_tieba_request
from bridges.tieba.searching import HitDiagnostics, SearchOutcome, plan_queries, query_record
from tests.chat.test_chat_api import _register


def test_legacy_manual_browser_setup_routes_return_410(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRIDGES_DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "browser-api-test-secret")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    app = create_app()
    paths = {route.path for route in discover_api_routes(app)}
    assert "/tieba/browser/open" in paths
    assert "/tieba/browser/complete" in paths
    client = TestClient(app)
    _register(client, "802")
    for path in ("/tieba/browser/open", "/tieba/browser/complete"):
        response = client.post(path)
        assert response.status_code == 410
        assert response.json()["detail"]["error"] == "tieba_browser_controls_retired"


def test_original_question_uses_account_bound_browser_through_chat(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    generation_helpers: dict[str, Any],
) -> None:
    from bridges.tieba.browser import BrowserTiebaSearch, BrowserTiebaThreadReader
    from bridges.tieba.reading import HttpTiebaThreadReader
    from bridges.tieba.service import TiebaResearchService
    from tests.chat.test_chat_api import _create_conversation
    from tests.tieba.test_tieba_module_flow import _run_and_read, _send

    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'browser-chat.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "browser-chat-test-secret")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    app = create_app()
    client = TestClient(app)
    _register(client, "803")
    account_id = client.get("/me").json()["account_id"]

    class RecordedBrowser:
        # 内容取自人工登录后真正读到的页面；此测试复核编排，不冒充在线验收。
        def request(self, account: str, payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
            assert account == account_id
            if payload["action"] == "search":
                return {
                    "hits": [
                        {
                            "url": "https://tieba.baidu.com/p/11023199670",
                            "title": "论该丝的中秋国庆假期通知",
                            "snippet": "华东交通大学吧",
                        }
                    ]
                }
            return {
                "forum": "华东交通大学吧",
                "title": "论该丝的中秋国庆假期通知",
                "original": "啊啊啊啊啊啊啊怎么现在还没下来啊😫，我就想订个票我容易吗我",
                "posted_at": "09-14",
                "replies": [{"content": "同问", "posted_at": "09-14"}],
            }

    class UnusedPublic:
        def search_public(self, *args: Any, **kwargs: Any) -> Any:
            raise AssertionError("本轮原生搜索已返回帖子，不应调用公开索引")

    browser: Any = RecordedBrowser()
    service = TiebaResearchService(
        search=BrowserTiebaSearch(browser, UnusedPublic()),
        reader=HttpTiebaThreadReader(),
        reader_for_account=lambda account: BrowserTiebaThreadReader(browser, account),
    )
    app.state.tieba_research_service = service
    app.state.chat_service._tieba_research = service  # noqa: SLF001
    conversation = _create_conversation(client)
    _send(client, conversation, "我想了解国庆节假期的讨论", module_id="tieba")
    assistant = _run_and_read(app, client, generation_helpers["drive"], conversation)
    assert assistant["status"] == "done"
    research = assistant["tieba_research"]
    assert research["status"] == "success"
    assert research["queries"][0]["source"] == "tieba_browser"
    assert "订个票" in research["confirmed_posts"][0]["replies"][0]["content"]
    assert research["confirmed_posts"][0]["read_status"] == "partial"
    service.close()


def test_continue_retries_auth_and_reuses_completed_read_evidence(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    generation_helpers: dict[str, Any],
) -> None:
    from tests.chat.test_chat_api import _create_conversation
    from tests.tieba.test_tieba_module_flow import (
        _FakeReader,
        _install_tieba_service,
        _read_result,
        _run_and_read,
        _send,
    )

    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'read-auth.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "browser-read-auth-test-secret")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    app = create_app()
    client = TestClient(app)
    _register(client, "804")
    original_question = "华东交通大学吧 宿舍条件"
    initial_url = "https://tieba.baidu.com/p/10745250786"
    auth_url = "https://tieba.baidu.com/p/10745250787"
    added_url = "https://tieba.baidu.com/p/10745250788"
    analysis = parse_tieba_request(original_question)
    queries = plan_queries(analysis)
    assert len(queries) >= 2
    initial_hits = [
        TiebaSearchHit(url=initial_url, title="宿舍条件讨论", snippet=""),
        TiebaSearchHit(url=auth_url, title="宿舍条件讨论", snippet=""),
    ]
    added_hit = TiebaSearchHit(url=added_url, title="宿舍条件补充", snippet="")

    class ResumingSearchPort:
        signed_in = False
        calls: list[str] = []

        def search_public(self, account_id: str, *, query: str, **kwargs: Any) -> SearchOutcome:
            del account_id, kwargs
            self.calls.append(query)
            if query == queries[0]:
                hits = tuple(initial_hits)
                status = ModuleQueryStatus.SUCCESS
                error_code = None
                error_message = None
            elif not self.signed_in:
                hits = ()
                status = ModuleQueryStatus.ERROR
                error_code = "tieba_browser_auth_required"
                error_message = "需要登录"
            elif query == queries[1]:
                hits = (added_hit,)
                status = ModuleQueryStatus.SUCCESS
                error_code = None
                error_message = None
            else:
                hits = ()
                status = ModuleQueryStatus.SUCCESS
                error_code = None
                error_message = None
            return SearchOutcome(
                record=query_record(
                    query=query,
                    status=status,
                    evidence_count=len(hits),
                    error_code=error_code,
                    error_message=error_message,
                    retryable=error_code is not None,
                ),
                hits=hits,
                candidates=hits,
                rejected=(),
                diagnostics=HitDiagnostics(raw_hits=len(hits), usable=len(hits)),
            )

    auth_result = _read_result(url=auth_url, status=ReadStatus.ACCESS_RESTRICTED).model_copy(
        update={
            "error_code": "tieba_browser_auth_required",
            "error_message": "需要登录",
            "retrieved_at": datetime.now(UTC),
        }
    )
    reader_results = {
        initial_url: _read_result(
            url=initial_url,
            status=ReadStatus.READ,
            title="宿舍条件讨论",
            replies=(TiebaReply(floor=1, content="宿舍热水条件还可以。"),),
        ),
        auth_url: auth_result,
        added_url: _read_result(
            url=added_url,
            status=ReadStatus.READ,
            title="宿舍条件补充",
            replies=(TiebaReply(floor=1, content="宿舍洗衣房需要排队。"),),
        ),
    }
    reader = _FakeReader(reader_results)
    port = ResumingSearchPort()
    _install_tieba_service(app, port=port, reader=reader)
    conversation = _create_conversation(client)
    _send(client, conversation, original_question, module_id="tieba")
    assistant = _run_and_read(app, client, generation_helpers["drive"], conversation)

    research = assistant["tieba_research"]
    assert research is not None, assistant
    assert research["status"] == "waiting_auth"
    assert research["pending"]["context"]["original_question"] == original_question
    assert reader.read_urls == [initial_url, auth_url]

    port.signed_in = True
    reader_results[auth_url] = _read_result(
        url=auth_url,
        status=ReadStatus.READ,
        title="宿舍条件讨论",
        replies=(TiebaReply(floor=2, content="宿舍热水晚上比较稳定。"),),
    )
    _send(client, conversation, "继续", module_id="tieba")
    resumed = _run_and_read(app, client, generation_helpers["drive"], conversation)

    resumed_research = resumed["tieba_research"]
    assert resumed_research is not None, (
        resumed.get("status"), resumed.get("error_code"), resumed.get("error_message")
    )
    assert resumed_research["original_question"] == original_question
    assert {post["url"] for post in resumed_research["confirmed_posts"]} == {
        initial_url,
        auth_url,
        added_url,
    }
    assert reader.read_urls == [initial_url, auth_url, auth_url, added_url]
    assert port.calls.count(queries[0]) == 1
    assert port.calls.count(queries[1]) == 2
