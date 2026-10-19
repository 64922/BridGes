"""结构化响应捕获与搜索页解析的隔离测试。"""

from __future__ import annotations

import json
from typing import Any

import pytest

from bridges.tieba.browser_capture import StructuredCapture, search_structured
from bridges.tieba.browser_structured import StructuredResponseError
from bridges.tieba.browser_worker import _error_payload


class _Response:
    def __init__(self, url: str, body: dict[str, Any] | str) -> None:
        self.url = url
        self._body = body

    def text(self) -> str:
        return self._body if isinstance(self._body, str) else json.dumps(self._body)


class _Page:
    def __init__(self) -> None:
        self.handlers: dict[str, Any] = {}

    def on(self, event: str, handler: Any) -> None:
        self.handlers[event] = handler

    def remove_listener(self, event: str, _handler: Any) -> None:
        self.handlers.pop(event, None)

    def fire(self, response: _Response) -> None:
        self.handlers["response"](response)


def _search_body() -> dict[str, Any]:
    return {
        "no": 0,
        "data": {
            "post_list": [
                {
                    "tid": "123",
                    "title": "宿舍条件",
                    "content": "北区宿舍热水怎么样？",
                    "forum_name": "华东交通大学吧",
                    "pb_url": "https://tieba.baidu.com/p/123",
                    "user": {"show_nickname": "楼主甲"},
                    "time": 1760600000,
                }
            ]
        },
    }


def test_capture_only_keeps_dict_payloads_from_expected_path() -> None:
    page = _Page()
    capture = StructuredCapture(page, "/mo/q/search/thread").start()
    try:
        page.fire(_Response("https://tieba.baidu.com/other", _search_body()))
        page.fire(_Response("https://tieba.baidu.com/mo/q/search/thread?x=1", "not-json"))
        page.fire(
            _Response(
                "https://tieba.baidu.com/mo/q/search/thread?x=2",
                _search_body(),
            )
        )
    finally:
        capture.stop()

    assert len(capture.payloads) == 1
    assert capture.payloads[0]["no"] == 0
    assert page.handlers == {}


def test_search_structured_uses_valid_payload_and_ignores_malformed_one() -> None:
    page = _Page()
    capture = StructuredCapture(page, "/mo/q/search/thread").start()
    page.fire(
        _Response(
            "https://tieba.baidu.com/mo/q/search/thread?x=1",
            {"no": 0, "data": {}},
        )
    )
    page.fire(
        _Response(
            "https://tieba.baidu.com/mo/q/search/thread?x=2",
            _search_body(),
        )
    )
    capture.stop()

    hits = search_structured(capture, tab="1")
    assert hits == [
        {
            "url": "https://tieba.baidu.com/p/123",
            "title": "宿舍条件",
            "snippet": "华东交通大学吧\n北区宿舍热水怎么样？",
            "discovery_type": "thread",
            "matched_reply": None,
        }
    ]


def test_search_structured_raises_when_every_payload_is_malformed() -> None:
    page = _Page()
    capture = StructuredCapture(page, "/mo/q/search/thread").start()
    page.fire(
        _Response(
            "https://tieba.baidu.com/mo/q/search/thread?x=1",
            {"no": 0, "data": {}},
        )
    )
    capture.stop()

    with pytest.raises(StructuredResponseError):
        search_structured(capture, tab="1")


def test_structured_failure_maps_to_stable_browser_error() -> None:
    payload = _error_payload(StructuredResponseError("字段缺失"))
    assert payload["error"] == "tieba_browser_response_invalid"
    assert "结构校验失败" in payload["message"]
