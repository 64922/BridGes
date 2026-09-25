"""``route.parse`` 单元合同：三项提取、只问缺项、指代不猜坐标、并列方式。

Issue 12 编排合同要求解析保留原话、缺哪项只问哪项、无法定位「我这里」时不猜
坐标。这些用例逐条固定该行为，避免后续改动把「猜测」重新引入解析层。
"""

from __future__ import annotations

from datetime import UTC, datetime

from bridges.commute.contracts import (
    MISSING_DESTINATION,
    MISSING_DESTINATION_CHOICE,
    MISSING_MODE,
    MISSING_ORIGIN,
    MISSING_ORIGIN_CHOICE,
    MISSING_ORIGIN_UNLOCATABLE,
    CommuteMode,
    CommutePlaceCandidate,
    CommutePlaceRole,
)
from bridges.commute.parsing import (
    match_candidate,
    parse_commute_request,
    pending_payload,
)
from bridges.contracts.modules import ModuleWaitState

NOW = datetime(2026, 9, 25, 3, 0, tzinfo=UTC)


def _pending(
    context: dict[str, object],
    *,
    question: str = "请问？",
    kind: str = "clarification",
) -> ModuleWaitState:
    return ModuleWaitState(
        module_id="commute",
        kind=kind,
        question=question,
        origin_message_id="assistant-1",
        context=context,
        created_at=NOW,
    )


def test_full_request_parses_three_items_without_clarification() -> None:
    analysis = parse_commute_request("从南区骑车到图书馆要多久", now=NOW)

    assert analysis.mode is CommuteMode.BICYCLING
    assert analysis.mode_phrase == "骑车"
    assert analysis.origin_phrase == "南区"
    assert analysis.destination_phrase == "图书馆"
    assert analysis.clarification is None
    assert analysis.confidence >= 0.9


def test_missing_origin_asks_only_origin() -> None:
    analysis = parse_commute_request("去图书馆怎么走", now=NOW)

    assert analysis.destination_phrase == "图书馆"
    assert analysis.origin_phrase is None
    assert analysis.clarification is not None
    assert analysis.clarification.missing == MISSING_ORIGIN
    assert analysis.clarification.role is CommutePlaceRole.ORIGIN
    assert analysis.clarification.question.count("？") == 1


def test_missing_mode_asks_only_mode_and_bare_walk_verb_is_not_a_mode() -> None:
    """「怎么走」里的「走」不是方式选择：缺方式时仍然追问，不默认步行。"""
    analysis = parse_commute_request("从南区到北区怎么走", now=NOW)

    assert analysis.mode is None
    assert analysis.origin_phrase == "南区"
    assert analysis.destination_phrase == "北区"
    assert analysis.clarification is not None
    assert analysis.clarification.missing == MISSING_MODE


def test_missing_destination_asks_only_destination() -> None:
    analysis = parse_commute_request("从图书馆出发", now=NOW)

    assert analysis.origin_phrase == "图书馆"
    assert analysis.destination_phrase is None
    assert analysis.clarification is not None
    assert analysis.clarification.missing == MISSING_DESTINATION


def test_unlocatable_origin_asks_for_a_concrete_place_without_guessing() -> None:
    analysis = parse_commute_request("从我这里到北门怎么走", now=NOW)

    assert analysis.origin_unlocatable is True
    assert analysis.clarification is not None
    assert analysis.clarification.missing == MISSING_ORIGIN_UNLOCATABLE
    assert "我这里" in analysis.clarification.question
    assert "猜" in analysis.clarification.question


def test_two_modes_in_one_sentence_ask_which_one() -> None:
    analysis = parse_commute_request("走路还是骑车去图书馆", now=NOW)

    assert analysis.mode is None
    assert analysis.mode_candidates == [CommuteMode.WALKING, CommuteMode.BICYCLING]
    assert analysis.clarification is not None
    assert analysis.clarification.missing == MISSING_MODE
    assert "步行" in analysis.clarification.question
    assert "自行车" in analysis.clarification.question


def test_missing_places_are_backfilled_from_prior_context() -> None:
    analysis = parse_commute_request(
        "骑车怎么走", prior_context=["从图书馆到食堂怎么走"], now=NOW
    )

    assert analysis.origin_phrase == "图书馆"
    assert analysis.destination_phrase == "食堂"
    assert analysis.mode is CommuteMode.BICYCLING
    assert analysis.clarification is None


def test_resume_fills_awaited_origin_then_asks_mode() -> None:
    first = parse_commute_request("去图书馆怎么走", now=NOW)
    assert first.clarification is not None
    pending = _pending(pending_payload(first, awaiting=first.clarification.missing))

    second = parse_commute_request("南区", pending=pending, now=NOW)

    assert second.origin_phrase == "南区"
    assert second.destination_phrase == "图书馆"
    assert second.clarification is not None
    assert second.clarification.missing == MISSING_MODE
    assert second.mode is None


def test_resume_fills_awaited_mode() -> None:
    first = parse_commute_request("从南区到北区怎么走", now=NOW)
    assert first.clarification is not None
    pending = _pending(pending_payload(first, awaiting=first.clarification.missing))

    second = parse_commute_request("骑电动车", pending=pending, now=NOW)

    assert second.mode is CommuteMode.ELECTROBIKE
    assert second.origin_phrase == "南区"
    assert second.destination_phrase == "北区"
    assert second.clarification is None


def test_resume_with_candidate_index_pins_the_chosen_poi() -> None:
    candidates = [
        CommutePlaceCandidate(
            name="华东交通大学图书馆",
            location="115.870000,28.750000",
            address="双港东大街808号",
            campus=True,
        ),
        CommutePlaceCandidate(
            name="华东交通大学南区图书馆",
            location="115.871000,28.750500",
            address="双港东大街808号南区",
            campus=True,
        ),
    ]
    pending = _pending(
        {
            "awaiting": MISSING_ORIGIN_CHOICE,
            "origin_phrase": None,
            "destination_phrase": "北门",
            "origin_candidates": [item.model_dump(mode="json") for item in candidates],
        }
    )

    resumed = parse_commute_request("2", pending=pending, now=NOW)

    assert resumed.origin_place is not None
    assert resumed.origin_place.name == "华东交通大学南区图书馆"
    assert resumed.origin_place.location == "115.871000,28.750500"
    assert resumed.clarification is not None
    assert resumed.clarification.missing == MISSING_MODE


def test_resume_with_candidate_name_matches_by_name() -> None:
    candidates = [
        CommutePlaceCandidate(name="华东交通大学南门", location="115.87,28.75", campus=True),
        CommutePlaceCandidate(name="华东交通大学北门", location="115.88,28.76", campus=True),
    ]
    pending = _pending(
        {
            "awaiting": MISSING_ORIGIN_CHOICE,
            "destination_phrase": "图书馆",
            "origin_candidates": [item.model_dump(mode="json") for item in candidates],
        }
    )

    resumed = parse_commute_request("北门", pending=pending, now=NOW)

    assert resumed.origin_place is not None
    assert resumed.origin_place.name == "华东交通大学北门"


def test_resume_with_unmatched_answer_reparses_that_side() -> None:
    """回答没对上候选时按新地点重新解析这一侧，而不是把整句当地点。"""
    candidates = [
        CommutePlaceCandidate(name="华东交通大学南门", location="115.87,28.75", campus=True)
    ]
    pending = _pending(
        {
            "awaiting": MISSING_DESTINATION_CHOICE,
            "origin_phrase": "图书馆",
            "destination_candidates": [item.model_dump(mode="json") for item in candidates],
        }
    )

    resumed = parse_commute_request("不是这个，是体育场", pending=pending, now=NOW)

    assert resumed.destination_place is None
    assert resumed.destination_phrase is not None
    assert "体育场" in resumed.destination_phrase
    assert resumed.origin_phrase == "图书馆"


def test_match_candidate_supports_index_and_name() -> None:
    candidates = [
        CommutePlaceCandidate(name="华东交通大学食堂", campus=True),
        CommutePlaceCandidate(name="华东交通大学南区食堂", campus=True),
    ]

    assert match_candidate("第2个", candidates) is candidates[1]
    assert match_candidate("1", candidates) is candidates[0]
    assert match_candidate("南区食堂", candidates) is candidates[1]
    assert match_candidate("无关内容", candidates) is None
    assert match_candidate("1", []) is None
