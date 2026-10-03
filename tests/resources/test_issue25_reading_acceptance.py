"""工单 25：图书读取请求受运行剩余预算约束。"""

from unittest.mock import patch

import httpx
import pytest

from bridges.resources.reading import OpenLibraryBookInsightReader
from bridges.resources.sources import BilibiliVideoVerifier, OpenLibraryBookSource


@pytest.mark.parametrize(
    ("deadline", "expected_timeout"), [(100.25, 0.25), (120.0, 8.0), (None, 8.0)]
)
def test_read_timeout_is_limited_by_remaining_deadline(
    deadline: float | None, expected_timeout: float
) -> None:
    timeouts: list[float] = []

    def respond(request: httpx.Request) -> httpx.Response:
        timeouts.append(request.extensions["timeout"]["read"])
        return httpx.Response(200, json={"description": "机器学习入门"})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        reader = OpenLibraryBookInsightReader(client=client)
        with patch("bridges.resources.reading.time.monotonic", return_value=100.0):
            evidence = reader.read(
                "https://openlibrary.org/works/OL1W",
                "openlibrary",
                account_id="test-account",
                deadline=deadline,
            )
    assert evidence.description == "机器学习入门"
    assert timeouts == [expected_timeout]


def test_expired_deadline_does_not_send_read_request() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        reader = OpenLibraryBookInsightReader(client=client)
        with patch("bridges.resources.reading.time.monotonic", return_value=100.0):
            evidence = reader.read(
                "https://openlibrary.org/works/OL1W",
                "openlibrary",
                account_id="test-account",
                deadline=100.0,
            )
    assert evidence.error == "openlibrary_deadline"
    assert requests == []


@pytest.mark.parametrize("source", ["book", "video"])
@pytest.mark.parametrize("remaining", [0.25, 0.0])
def test_metadata_source_respects_remaining_request_budget(source: str, remaining: float) -> None:
    timeouts: list[float] = []

    def respond(request: httpx.Request) -> httpx.Response:
        timeouts.append(request.extensions["timeout"]["read"])
        return httpx.Response(200, json={"docs": [], "code": 0, "data": {}})

    with (
        httpx.Client(transport=httpx.MockTransport(respond)) as client,
        patch("bridges.resources.sources.time.monotonic", return_value=100.0),
    ):
        if source == "book":
            OpenLibraryBookSource(client=client).search(
                "account", "深度学习", limit=3, deadline=100.0 + remaining
            )
        else:
            BilibiliVideoVerifier(client=client).verify(
                ["https://www.bilibili.com/video/BV1pu411o7BE"],
                account_id="account",
                deadline=100.0 + remaining,
            )
    assert timeouts == ([remaining] if remaining else [])
