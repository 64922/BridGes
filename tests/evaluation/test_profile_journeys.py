"""工单 01：T01–T20 合成语料与六类基线旅程的结构校验。

只校验语料与旅程资产的固定契约（输入、预期、负责票、变体、划分与
状态语义）；实际行为比较见 ``test_profile_baseline_journey_runner.py``。
"""

from __future__ import annotations

from bridges.evaluation.profile_journey_types import (
    SCENARIO_STATUS_BASELINE_DEFECT,
    SCENARIO_STATUS_PLANNED,
    SPLIT_DEV,
    SPLIT_HOLDOUT,
)
from bridges.evaluation.profile_journeys import (
    BASELINE_JOURNEYS,
    BASELINE_JOURNEYS_BY_ID,
    PROFILE_JOURNEY_CORPUS_VERSION,
    SCENARIOS,
    SCENARIOS_BY_ID,
    VARIANT_KINDS,
    corpus_digest,
    variant_kinds_by_scenario,
)

_EXPECTED_IDS = tuple(f"T{index:02d}" for index in range(1, 21))
_VALID_SPLITS = {SPLIT_DEV, SPLIT_HOLDOUT}
_VALID_STATUSES = {SCENARIO_STATUS_BASELINE_DEFECT, SCENARIO_STATUS_PLANNED}


def test_corpus_version_is_frozen_label() -> None:
    assert PROFILE_JOURNEY_CORPUS_VERSION == "profile-loop-corpus-v2"


def test_scenarios_cover_t01_to_t20_in_order() -> None:
    assert tuple(scenario.scenario_id for scenario in SCENARIOS) == _EXPECTED_IDS
    assert set(SCENARIOS_BY_ID) == set(_EXPECTED_IDS)


def test_every_scenario_has_fixed_inputs_expectations_and_owner() -> None:
    for scenario in SCENARIOS:
        assert scenario.turns, scenario.scenario_id
        assert any(turn.speaker == "user" for turn in scenario.turns), scenario.scenario_id
        assert all(turn.source_time and turn.content for turn in scenario.turns)
        assert scenario.expectation.answer, scenario.scenario_id
        assert scenario.expectation.baseline, scenario.scenario_id
        # 负向场景（如 T06 孤立「同意」）没有预期事实/变更，由回答表现承载。
        assert (
            scenario.expectation.facts
            or scenario.expectation.changes
            or "不" in scenario.expectation.answer
        ), scenario.scenario_id
        assert scenario.responsible_tickets, scenario.scenario_id
        assert scenario.contracts, scenario.scenario_id
        assert scenario.split in _VALID_SPLITS, scenario.scenario_id
        assert scenario.status in _VALID_STATUSES, scenario.scenario_id


def test_planned_scenarios_are_not_claimed_as_current_defects() -> None:
    for scenario in SCENARIOS:
        if scenario.status == SCENARIO_STATUS_PLANNED:
            assert "不记为现有缺陷" in scenario.expectation.baseline, scenario.scenario_id
        if scenario.status == SCENARIO_STATUS_BASELINE_DEFECT:
            assert "不记为现有缺陷" not in scenario.expectation.baseline, scenario.scenario_id
            assert "缺陷" in scenario.expectation.baseline, scenario.scenario_id


def test_discovered_defects_match_runnable_journeys() -> None:
    defect_ids = {
        scenario.scenario_id
        for scenario in SCENARIOS
        if scenario.status == SCENARIO_STATUS_BASELINE_DEFECT
    }
    assert defect_ids == {"T01", "T02", "T03", "T04"}
    for journey in BASELINE_JOURNEYS:
        assert journey.scenario_id in SCENARIOS_BY_ID
        assert journey.user_turns
        assert journey.target
        assert journey.baseline_observation
        assert journey.baseline_status in {"pass", "known_failing"}


def test_every_scenario_has_a_holdout_variant_and_variant_labels_are_known() -> None:
    kinds_by_scenario = variant_kinds_by_scenario()
    for scenario in SCENARIOS:
        assert "holdout" in kinds_by_scenario[scenario.scenario_id], scenario.scenario_id
        for variant in scenario.variants:
            assert variant.kind in VARIANT_KINDS, (scenario.scenario_id, variant.kind)
            assert variant.content and variant.note


def test_corpus_covers_negation_mixed_subject_and_synonym_variants() -> None:
    all_kinds = {
        variant.kind for scenario in SCENARIOS for variant in scenario.variants
    }
    assert {"negation", "mixed_subject", "synonym", "holdout"} <= all_kinds


def test_corpus_digest_is_sha256_hex() -> None:
    digest = corpus_digest()
    assert len(digest) == 64
    int(digest, 16)
    assert digest == corpus_digest()


def test_baseline_journey_ids_are_unique_and_lookup_consistent() -> None:
    ids = [journey.journey_id for journey in BASELINE_JOURNEYS]
    assert len(ids) == len(set(ids))
    assert set(BASELINE_JOURNEYS_BY_ID) == set(ids)
    problem_classes = {journey.problem_class for journey in BASELINE_JOURNEYS}
    assert {
        "name",
        "pet",
        "habit",
        "sensitive_keyword",
        "time_constraint",
        "neighbor_messages",
        "positive_control",
        "recall_control",
    } <= problem_classes
