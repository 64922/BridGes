"""结构化搜索与帖子响应的回归（夹具来自真实响应，字段已脱敏）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bridges.tieba.browser_structured import (
    BROWSER_PROTOCOL_VERSION,
    StructuredResponseError,
    parse_search_payload,
    parse_thread_payload,
)

FIXTURE = Path(__file__).parent / "fixtures" / "tieba_structured_payloads.json"


@pytest.fixture
def payloads() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_search_payload_only_exposes_candidates_not_session_fields(
    payloads: dict[str, object],
) -> None:
    hits = parse_search_payload(payloads["search"], discovery_type="thread")

    assert [hit["url"] for hit in hits] == [
        "https://tieba.baidu.com/p/9100000001",
        "https://tieba.baidu.com/p/9100000002",
    ]
    assert hits[0]["title"] == "宿舍热水与查寝情况"
    assert "示例发言人甲" not in json.dumps(hits, ensure_ascii=False)
    assert "tbs" not in json.dumps(hits, ensure_ascii=False)


def test_search_payload_marks_reply_discovery(payloads: dict[str, object]) -> None:
    hits = parse_search_payload(payloads["search"], discovery_type="reply")
    assert hits[0]["discovery_type"] == "reply"
    assert hits[0]["matched_reply"] == "想问问北区宿舍的热水和周末查寝情况。"


def test_thread_payload_keeps_identity_time_floor_and_parent(
    payloads: dict[str, object],
) -> None:
    result = parse_thread_payload(
        payloads["thread"],
        url="https://tieba.baidu.com/p/9100000001?fr=pb",
        floors={"910000000102": 2, "910000000103": 3},
    )

    assert result["forum"] == "华东交通大学吧"
    assert result["title"] == "宿舍热水与查寝情况"
    assert result["thread_id"] == "9100000001"
    assert result["author"] == "示例发言人甲"
    assert result["posted_at"] == "2026-10-16 15:21"
    assert result["original_floor"] == 1
    assert result["total_replies"] == 4
    assert result["total_pages"] == 2
    assert result["response_valid"] is True
    assert result["protocol_version"] == BROWSER_PROTOCOL_VERSION

    replies = result["replies"]
    assert [reply["author"] for reply in replies] == [
        "示例发言人乙",
        "示例发言人丙",
        "示例发言人乙",
        None,
    ]
    assert [reply["floor"] for reply in replies] == [2, 3, 3, None]
    assert replies[2]["parent_reply_id"] == "910000000103"
    assert replies[2]["nested"] is True
    assert replies[3]["author"] is None


def test_thread_payload_leaves_floor_and_author_unknown_without_dom(
    payloads: dict[str, object],
) -> None:
    result = parse_thread_payload(payloads["thread"], url="https://tieba.baidu.com/p/9100000001")
    replies = result["replies"]

    assert [reply["floor"] for reply in replies[:2]] == [None, None]
    assert replies[-1]["author"] is None


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"no": 1, "error": "failed", "data": {"post_list": []}},
        {"no": 0, "error": "success", "data": {}},
        {"no": 0, "error": "success", "data": {"post_list": [{"title": "缺少 tid"}]}},
    ],
)
def test_invalid_search_payload_is_explicit(payload: object) -> None:
    with pytest.raises(StructuredResponseError):
        parse_search_payload(payload, discovery_type="thread")


def test_empty_search_payload_is_a_valid_empty_result(payloads: dict[str, object]) -> None:
    payload = {
        "no": 0,
        "error": "success",
        "data": {"post_list": [], "has_more": 0, "current_page": 1},
    }
    assert parse_search_payload(payload, discovery_type="thread") == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.pop("forum"),
        lambda value: value.pop("first_floor"),
        lambda value: value.pop("post_list"),
        lambda value: value.update({"error_msg": "帖子不存在"}),
        lambda value: value.update({"error_code": 336}),
    ],
)
def test_invalid_thread_payload_is_explicit(
    payloads: dict[str, object], mutate: object
) -> None:
    payload = json.loads(json.dumps(payloads["thread"]))
    mutate(payload)  # type: ignore[operator]
    with pytest.raises(StructuredResponseError):
        parse_thread_payload(payload, url="https://tieba.baidu.com/p/9100000001")
