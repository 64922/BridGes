"""专用窗口操作必须来自认证会话，不能由请求体选择另一账户。"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.api.main import create_app
from bridges.closeout.manifest import check_route_coverage, discover_api_routes
from bridges.config import get_settings
from tests.chat.test_chat_api import _register


def test_browser_setup_is_authenticated_and_account_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRIDGES_DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "browser-api-test-secret")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    app = create_app()
    browser_routes = [
        route for route in discover_api_routes(app) if route.path.startswith("/tieba/browser/")
    ]
    assert len(browser_routes) == 2 and check_route_coverage(browser_routes) == []

    class Runtime:
        calls: list[tuple[str, str]] = []

        def open(self, account_id: str) -> None:
            self.calls.append(("open", account_id))

        def complete(self, account_id: str) -> None:
            self.calls.append(("complete", account_id))

    runtime = Runtime()
    app.state.tieba_browser_runtime = runtime
    first, second = TestClient(app), TestClient(app)
    assert first.post("/tieba/browser/open").status_code == 401
    _register(first, "801")
    _register(second, "802")
    account_a: Any = first.get("/me").json()["account_id"]
    account_b: Any = second.get("/me").json()["account_id"]
    assert first.post("/tieba/browser/open", json={"account_id": account_b}).status_code == 200
    assert second.post("/tieba/browser/open").status_code == 200
    assert first.post("/tieba/browser/complete").status_code == 200
    assert runtime.calls == [("open", account_a), ("open", account_b), ("complete", account_a)]
    assert (
        first.post("/tieba/browser/open", headers={"Origin": "https://evil.example"}).status_code
        == 403
    )


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
                assert payload["terms"] == "国庆 假期"
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
