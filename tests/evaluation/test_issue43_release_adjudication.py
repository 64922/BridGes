"""工单 43：放行裁决台账验收（任务 6 / 验收标准 6）。"""

from __future__ import annotations

import json
from pathlib import Path
from shutil import copyfile

import pytest

from bridges.evaluation.release_adjudication import (
    DECISION_NOT_RELEASED,
    DECISION_RELEASED,
    DECISION_RELEASED_WITH_DEGRADATION,
    DECISION_RELEASED_WITH_LIMITS,
    RELEASE_DECISIONS,
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


@pytest.fixture
def evidence_root(tmp_path: Path) -> Path:
    """复制脱敏证据，用反例污染副本，保留原始真实报告。"""
    root = Path(__file__).resolve().parents[2]
    for decision in RELEASE_DECISIONS:
        for ref in decision.evidence:
            destination = tmp_path / ref
            destination.parent.mkdir(parents=True, exist_ok=True)
            copyfile(root / ref, destination)
    return tmp_path


def _report_path(root: Path, ticket: int, suffix: str) -> Path:
    decision = next(item for item in RELEASE_DECISIONS if item.ticket == ticket)
    return root / next(ref for ref in decision.evidence if ref.endswith(suffix))


def test_text_markers_and_summary_counts_cannot_prove_release(tmp_path: Path) -> None:
    for decision in RELEASE_DECISIONS:
        for ref in decision.evidence:
            path = tmp_path / ref
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = ("not_released 不放行 36/36 8/8" if path.suffix == ".md"
                       else '{"scenario_count":39,"summary":{"problems":0}}')
            path.write_text(payload, encoding="utf-8")
    report = build_release_adjudication(tmp_path)
    assert not report.passed
    assert all(item.decision == DECISION_NOT_RELEASED for item in report.decisions)


@pytest.mark.parametrize(("ticket", "suffix"), [
    (39, "review-result.json"),
    (40, "tree-40-context-continuity-and-cost-evaluation-20261005T075002Z.json"),
    (41, "rejudged/pairing-report.json"),
    (42, "workflow-evidence.json"),
])
def test_missing_structured_evidence_closes_release(
    evidence_root: Path, ticket: int, suffix: str,
) -> None:
    _report_path(evidence_root, ticket, suffix).unlink()
    report = build_release_adjudication(evidence_root)
    assert not report.passed
    assert report.by_ticket(ticket).decision == DECISION_NOT_RELEASED
    assert report.by_ticket(ticket).limitations


@pytest.mark.parametrize(("ticket", "suffix", "mutation"), [
    (39, "review-result.json", "manual_gate"),
    (40, "tree-40-context-continuity-and-cost-evaluation-20261005T075002Z.json", "hard_gate"),
    (40, "tree-40-context-continuity-and-cost-evaluation-20261005T075002Z.json", "calls"),
    (41, "rejudged/pairing-report.json", "fingerprint"),
    (41, "rejudged/pairing-report.json", "checkpoints"),
    (42, "workflow-evidence.json", "scenarios"),
    (42, "workflow-evidence.json", "guard"),
    (42, "external-probes.json", "probes"),
    (42, "external-probes.json", "claim"),
    (42, "workflow-pairing.json", "pair_check"),
    (42, "workflow-semantics.json", "semantic_call"),
    (42, "workflow-semantics.json", "semantic_check"),
])
def test_failed_or_partial_real_evidence_cannot_inherit_release(
    evidence_root: Path, ticket: int, suffix: str, mutation: str,
) -> None:
    path = _report_path(evidence_root, ticket, suffix)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if mutation == "manual_gate":
        payload["manual_hard_gate_failures"] = []
    elif mutation == "hard_gate":
        payload["cases"][0]["hard_checks"]["provider_actual_bounds"] = False
    elif mutation == "calls":
        payload["cases"][0]["provider_calls"] = []
    elif mutation == "fingerprint":
        payload["environment"]["raw_report_sha256"] = "失配"
    elif mutation == "checkpoints":
        payload["runs"][0]["checkpoints"][0]["passed"] = False
    elif mutation == "scenarios":
        payload["scenarios"].pop()
    elif mutation == "guard":
        payload["zero_tolerance"] = []
    elif mutation == "probes":
        payload["probes"] = []
    elif mutation == "claim":
        payload["claims"]["tieba.replies"]["declared_level"] = "full"
    elif mutation == "pair_check":
        payload["cases"][0]["check_results"]["no_unnecessary_search"]["new"][0] = False
    elif mutation == "semantic_call":
        payload["calls"][0]["finish_reason"] = "length"
    else:
        payload["reports"][0]["cases"][0]["checks"]["semantic_decision"] = False
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    report = build_release_adjudication(evidence_root)
    assert not report.passed
    assert report.by_ticket(ticket).decision == DECISION_NOT_RELEASED
    assert "证据校验失败" in report.by_ticket(ticket).open_scope


@pytest.mark.parametrize("invalid", ["[]", "null", "{坏 JSON"])
def test_malformed_evidence_is_reported_without_crashing(
    evidence_root: Path, invalid: str,
) -> None:
    _report_path(evidence_root, 42, "external-probes.json").write_text(invalid, encoding="utf-8")
    report = build_release_adjudication(evidence_root)
    assert not report.passed
    assert report.by_ticket(42).decision == DECISION_NOT_RELEASED
