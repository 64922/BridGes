"""贴吧页面结构化响应的捕获与搜索页解析。"""

from __future__ import annotations

import json
import threading
import time

from playwright.sync_api import Page, Response

from bridges.tieba.browser_structured import (
    StructuredResponseError,
    parse_search_payload,
)


class StructuredCapture:
    """在页面导航期间收集结构化响应；不读取 Cookie 或会话字段。"""

    def __init__(self, page: Page, path: str) -> None:
        self._page = page
        self._path = path
        self.payloads: list[dict[str, object]] = []

    def start(self) -> StructuredCapture:
        register = getattr(self._page, "on", None)
        if callable(register):
            register("response", self._capture)
        return self

    def stop(self) -> None:
        unregister = getattr(self._page, "remove_listener", None)
        if callable(unregister):
            unregister("response", self._capture)

    def __enter__(self) -> StructuredCapture:
        return self.start()

    def __exit__(self, *_: object) -> None:
        self.stop()

    def _capture(self, response: Response) -> None:
        if self._path not in response.url:
            return
        try:
            body = json.loads(response.text())
        except Exception:  # noqa: BLE001 - 单个响应解析失败不终止页面
            return
        if isinstance(body, dict):
            self.payloads.append(body)


def wait_for_payloads(
    payloads: list[dict[str, object]],
    page: Page,
    closed: threading.Event,
    deadline: float | None,
    max_ms: int,
) -> None:
    stop = time.monotonic() + max_ms / 1000
    if deadline is not None:
        stop = min(stop, deadline)
    while not payloads and not closed.is_set() and time.monotonic() < stop:
        page.wait_for_timeout(100)


def search_structured(
    capture: StructuredCapture, tab: str
) -> list[dict[str, object]] | None:
    hits: list[dict[str, object]] = []
    parsed = False
    failure: StructuredResponseError | None = None
    for payload in capture.payloads:
        try:
            hits.extend(
                parse_search_payload(
                    payload,
                    discovery_type="thread" if tab == "1" else "reply",
                )
            )
            parsed = True
        except StructuredResponseError as error:
            failure = failure or error
    if parsed:
        return hits
    if failure is not None:
        raise failure
    return None
