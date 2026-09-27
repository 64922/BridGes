"""``route.parse`` 单元合同：三项提取、只问缺项、指代不猜坐标、并列方式。

Issue 12 编排合同要求解析保留原话、缺哪项只问哪项、无法定位「我这里」时不猜
坐标。这些用例逐条固定该行为，避免后续改动把「猜测」重新引入解析层。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

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
    analysis = parse_commute_request("从南区骑车到图书馆要多久")

    assert analysis.mode is CommuteMode.BICYCLING
    assert analysis.mode_phrase == "骑车"
    assert analysis.origin_phrase == "南区"
    assert analysis.destination_phrase == "图书馆"
    assert analysis.clarification is None
    assert analysis.confidence >= 0.9


def test_missing_origin_asks_only_origin() -> None:
    analysis = parse_commute_request("去图书馆怎么走")

    assert analysis.destination_phrase == "图书馆"
    assert analysis.origin_phrase is None
    assert analysis.clarification is not None
    assert analysis.clarification.missing == MISSING_ORIGIN
    assert analysis.clarification.role is CommutePlaceRole.ORIGIN
    assert analysis.clarification.question.count("？") == 1


def test_missing_mode_asks_only_mode_and_bare_walk_verb_is_not_a_mode() -> None:
    """「怎么走」里的「走」不是方式选择：缺方式时仍然追问，不默认步行。"""
    analysis = parse_commute_request("从南区到北区怎么走")

    assert analysis.mode is None
    assert analysis.origin_phrase == "南区"
    assert analysis.destination_phrase == "北区"
    assert analysis.clarification is not None
    assert analysis.clarification.missing == MISSING_MODE


def test_missing_destination_asks_only_destination() -> None:
    analysis = parse_commute_request("从图书馆出发")

    assert analysis.origin_phrase == "图书馆"
    assert analysis.destination_phrase is None
    assert analysis.clarification is not None
    assert analysis.clarification.missing == MISSING_DESTINATION


def test_unlocatable_origin_asks_for_a_concrete_place_without_guessing() -> None:
    analysis = parse_commute_request("从我这里到北门怎么走")

    assert analysis.origin_unlocatable is True
    assert analysis.clarification is not None
    assert analysis.clarification.missing == MISSING_ORIGIN_UNLOCATABLE
    assert "我这里" in analysis.clarification.question
    assert "猜" in analysis.clarification.question


def test_two_modes_in_one_sentence_ask_which_one() -> None:
    analysis = parse_commute_request("走路还是骑车去图书馆")

    assert analysis.mode is None
    assert analysis.mode_candidates == [CommuteMode.WALKING, CommuteMode.BICYCLING]
    assert analysis.clarification is not None
    assert analysis.clarification.missing == MISSING_MODE
    assert "步行" in analysis.clarification.question
    assert "自行车" in analysis.clarification.question


def test_missing_places_are_backfilled_from_prior_context() -> None:
    analysis = parse_commute_request(
        "骑车怎么走", prior_context=["从图书馆到食堂怎么走"]
    )

    assert analysis.origin_phrase == "图书馆"
    assert analysis.destination_phrase == "食堂"
    assert analysis.mode is CommuteMode.BICYCLING
    assert analysis.clarification is None


def test_resume_fills_awaited_origin_then_asks_mode() -> None:
    first = parse_commute_request("去图书馆怎么走")
    assert first.clarification is not None
    pending = _pending(pending_payload(first, awaiting=first.clarification.missing))

    second = parse_commute_request("南区", pending=pending)

    assert second.origin_phrase == "南区"
    assert second.destination_phrase == "图书馆"
    assert second.clarification is not None
    assert second.clarification.missing == MISSING_MODE
    assert second.mode is None


def test_resume_fills_awaited_mode() -> None:
    first = parse_commute_request("从南区到北区怎么走")
    assert first.clarification is not None
    pending = _pending(pending_payload(first, awaiting=first.clarification.missing))

    second = parse_commute_request("骑电动车", pending=pending)

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

    resumed = parse_commute_request("2", pending=pending)

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

    resumed = parse_commute_request("北门", pending=pending)

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

    resumed = parse_commute_request("不是这个，是体育场", pending=pending)

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


def test_resume_lets_the_new_wording_override_stored_places() -> None:
    """等待状态只补空缺：用户在回答里写出新地点时以新话为准。"""
    first = parse_commute_request("从南区到图书馆怎么走")
    assert first.clarification is not None
    pending = _pending(pending_payload(first, awaiting=first.clarification.missing))

    second = parse_commute_request("从宿舍到食堂", pending=pending)

    assert second.origin_phrase == "宿舍"
    assert second.destination_phrase == "食堂"
    # 方式仍然缺失：只问方式，不沿用上一轮的起终点
    assert second.clarification is not None
    assert second.clarification.missing == MISSING_MODE


def test_resume_treats_a_complete_restatement_as_a_new_request() -> None:
    """完整的重新表述按全新请求解析，旧等待随之作废。"""
    first = parse_commute_request("从南区到图书馆怎么走")
    assert first.clarification is not None
    pending = _pending(pending_payload(first, awaiting=first.clarification.missing))

    second = parse_commute_request("从宿舍骑车到食堂怎么走", pending=pending)

    assert second.origin_phrase == "宿舍"
    assert second.destination_phrase == "食堂"
    assert second.mode is CommuteMode.BICYCLING
    assert second.clarification is None


def test_candidate_choice_keeps_the_query_that_produced_it() -> None:
    """沿用候选时保留当时真正发送的检索词，证据里不出现没发过的词。"""
    candidates = [
        CommutePlaceCandidate(name="华东交通大学图书馆", location="115.87,28.75", campus=True),
        CommutePlaceCandidate(name="华东交通大学南区图书馆", location="115.88,28.76", campus=True),
    ]
    pending = _pending(
        {
            "awaiting": MISSING_ORIGIN_CHOICE,
            "destination_phrase": "北门",
            "origin_candidates": [item.model_dump(mode="json") for item in candidates],
            "origin_candidate_query": "华东交通大学图书馆",
        }
    )

    resumed = parse_commute_request("1", pending=pending)

    assert resumed.origin_place is not None
    assert resumed.origin_place.query == "华东交通大学图书馆"


# ---------------------------------------------------------------------------
# 句首口语前缀（Issue 03）：前缀不是地点，地点文字与数字一字不动
# ---------------------------------------------------------------------------

#: 同一趟出行的等价说法：句首口语前缀（主语／时间／意愿动词）任意连写。
EQUIVALENT_SENTENCES: tuple[tuple[str, CommuteMode, str], ...] = (
    ("从42栋步行到南区25栋", CommuteMode.WALKING, "步行"),
    ("42栋到南区25栋，步行", CommuteMode.WALKING, "步行"),
    ("我想从42栋到南区25栋，步行", CommuteMode.WALKING, "步行"),
    ("我现在想从42栋步行到南区25栋", CommuteMode.WALKING, "步行"),
    ("我现在想走路从42栋到南区25栋", CommuteMode.WALKING, "走路"),
    ("帮我看看从42栋骑车到南区25栋", CommuteMode.BICYCLING, "骑车"),
    ("我打算今天从42栋骑电动车到南区25栋", CommuteMode.ELECTROBIKE, "骑电动车"),
)


@pytest.mark.parametrize(("sentence", "mode", "mode_phrase"), EQUIVALENT_SENTENCES)
def test_colloquial_prefix_leaves_places_mode_and_digits_intact(
    sentence: str, mode: CommuteMode, mode_phrase: str
) -> None:
    """原句「我现在想从42栋步行到南区25栋」与各等价说法解析结果一致。"""
    analysis = parse_commute_request(sentence)

    assert analysis.origin_phrase == "42栋"
    assert analysis.destination_phrase == "南区25栋"
    assert analysis.mode is mode
    assert analysis.mode_phrase == mode_phrase
    assert analysis.clarification is None


def test_intent_words_never_survive_inside_a_place_phrase() -> None:
    """前缀只能被剥掉，不能换一种形式留在地点里（如「现在想从42栋」）。"""
    analysis = parse_commute_request("我现在想从42栋步行到南区25栋")

    for phrase in (analysis.origin_phrase, analysis.destination_phrase):
        assert phrase is not None
        assert not any(
            word in phrase for word in ("我", "现在", "想", "从", "步行")
        ), f"地点短语里混入了前缀：{phrase!r}"


def test_zone_words_and_digits_are_not_trimmed_as_intent() -> None:
    """校区词与数字是地点的一部分，不因剥离前缀被删掉。"""
    analysis = parse_commute_request("我从南区25栋步行到北区3号楼")

    assert analysis.origin_phrase == "南区25栋"
    assert analysis.destination_phrase == "北区3号楼"


def test_colloquial_prefix_with_only_a_destination_still_asks_origin() -> None:
    """「我现在想去图书馆」缺的是起点：前缀不得被当成起点。"""
    analysis = parse_commute_request("我现在想去图书馆")

    assert analysis.destination_phrase == "图书馆"
    assert analysis.origin_phrase is None
    assert analysis.clarification is not None
    assert analysis.clarification.missing == MISSING_ORIGIN


@pytest.mark.parametrize(
    "sentence",
    ("请问从南区到北区怎么走", "我们要从南区到北区", "现在从南区到北区"),
)
def test_prefix_does_not_become_a_place_and_missing_mode_is_still_asked(sentence: str) -> None:
    """礼貌／主语／时间前缀同样只剥离、不进地点；缺方式仍只问方式。"""
    analysis = parse_commute_request(sentence)

    assert analysis.origin_phrase == "南区"
    assert analysis.destination_phrase == "北区"
    assert analysis.clarification is not None
    assert analysis.clarification.missing == MISSING_MODE


def test_question_tail_words_are_not_treated_as_an_intent_prefix() -> None:
    """「要多久…」是问句尾巴而不是意图前缀：只有终点时仍只问起点。"""
    analysis = parse_commute_request("要多久到图书馆")

    assert analysis.destination_phrase == "图书馆"
    assert analysis.origin_phrase is None
    assert analysis.clarification is not None
    assert analysis.clarification.missing == MISSING_ORIGIN


@pytest.mark.parametrize(
    ("sentence", "origin", "destination"),
    (
        ("先骕楼到北门怎么走", "先骕楼", "北门"),
        ("明天广场步行到南区", "明天广场", "南区"),
    ),
)
def test_place_names_that_begin_with_intent_words_are_kept_intact(
    sentence: str, origin: str, destination: str
) -> None:
    """「先骕楼」的「先」、「明天广场」的「明天」是地名首字，不得当前缀剥掉。"""
    analysis = parse_commute_request(sentence)

    assert analysis.origin_phrase == origin
    assert analysis.destination_phrase == destination


@pytest.mark.parametrize(
    "sentence",
    ("我现在的位置到北门怎么走", "请问我现在的位置到北门怎么走"),
)
def test_locational_reference_still_asks_instead_of_becoming_a_place(sentence: str) -> None:
    """「我现在的位置」是必须追问的指代，带不带礼貌前缀都不能当可检索地点。"""
    analysis = parse_commute_request(sentence)

    assert analysis.origin_unlocatable is True
    assert analysis.clarification is not None
    assert analysis.clarification.missing == MISSING_ORIGIN_UNLOCATABLE
    assert "现在的位置" in analysis.clarification.question
