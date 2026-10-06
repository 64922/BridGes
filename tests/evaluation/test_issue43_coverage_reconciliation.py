"""工单 43：覆盖对账（验收标准 1）与放行裁决台账验收。

对账必须以来源文档逐行解析为准：COVERAGE.md 的 127 条需求映射加 42 的
39 个固定工作流场景共 166 项，全部登记负责票与可执行证据；39 的
``not_released`` 结论不因合并改写。
"""

from __future__ import annotations

from pathlib import Path

from bridges.evaluation.integration_coverage import (
    build_coverage_report,
    coverage_items,
    expected_coverage_ids,
)
from bridges.evaluation.integration_coverage_types import CoverageStatus


def test_reconciliation_covers_every_mapped_requirement_and_scenario() -> None:
    report = build_coverage_report()

    assert report.problems == []
    assert report.passed is True
    assert report.total == 166
    assert len(expected_coverage_ids()) == 166
    assert report.status_counts["verified"] >= 19
    assert report.status_counts["degraded"] >= 9


def test_document_ids_parse_to_exactly_127_requirements() -> None:
    document_ids = {
        item.item_id
        for item in coverage_items()
        if item.item_id[0] not in {"A", "L", "R"}
    }
    assert len(document_ids) == 127


def test_ticket_39_stays_not_released_and_others_released() -> None:
    by_id = {item.item_id: item for item in coverage_items()}

    assert by_id["H-D6"].status is CoverageStatus.NOT_RELEASED
    assert by_id["H-18"].status is CoverageStatus.NOT_RELEASED
    released = {"C-D1", "C-20", "P-15", "W-31", "W-34", "A01"}
    for item_id in released:
        assert by_id[item_id].status is CoverageStatus.VERIFIED
    assert all(
        item.status is CoverageStatus.DEGRADED
        for item in coverage_items()
        if item.item_id in {"A14", "A18", "W-36", "W-37", "W-39", "W-51"}
    )


def test_every_item_has_owner_and_existing_evidence_anchor() -> None:
    root = Path(__file__).resolve().parents[2]
    for item in coverage_items():
        assert item.owner_tickets, item.item_id
        assert min(item.owner_tickets) >= 1
        assert max(item.owner_tickets) <= 43
        for ref in item.evidence:
            path_part = ref.partition("::")[0]
            assert (root / path_part).exists(), f"{item.item_id}: {ref}"
