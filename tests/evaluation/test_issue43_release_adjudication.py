"""工单 43：放行裁决台账验收（任务 6 / 验收标准 6）。"""

from __future__ import annotations

from bridges.evaluation.release_adjudication import (
    DECISION_NOT_RELEASED,
    DECISION_RELEASED,
    DECISION_RELEASED_WITH_DEGRADATION,
    DECISION_RELEASED_WITH_LIMITS,
    build_release_adjudication,
)


def test_adjudication_passed_and_not_released_for_ticket_39() -> None:
    report = build_release_adjudication()

    assert report.problems == ()
    assert report.passed is True
    assert report.by_ticket(39).decision == DECISION_NOT_RELEASED
    assert "不开放" in report.by_ticket(39).open_scope


def test_adjudication_matches_each_ticket_evidence() -> None:
    report = build_release_adjudication()

    assert report.by_ticket(40).decision == DECISION_RELEASED
    assert report.by_ticket(41).decision == DECISION_RELEASED_WITH_LIMITS
    assert report.by_ticket(42).decision == DECISION_RELEASED_WITH_DEGRADATION
    for decision in report.decisions:
        assert decision.evidence
        assert decision.open_scope


def test_degraded_tickets_record_external_limits() -> None:
    report = build_release_adjudication()

    joined = "；".join(report.by_ticket(42).limitations)
    assert "tieba" in joined
    assert "GitHub" in joined
    joined = "；".join(report.by_ticket(41).limitations)
    assert "方向" in joined
