"""Issue 09：发布门报告的分类、脱敏与真实探针显式开关。"""

from __future__ import annotations

import json
from pathlib import Path

from bridges.closeout.release_gate import (
    CheckEvidence,
    FailureClass,
    ProviderProbeEvidence,
    ReleaseGateReport,
    classify_failure,
    write_report,
)


def test_failure_classification_distinguishes_product_external_and_environment() -> None:
    assert classify_failure("pytest", 1, "assertion failed") == FailureClass.PRODUCT
    assert classify_failure("ddg-probe", 1, "timeout") == FailureClass.EXTERNAL
    assert classify_failure("playwright", 1, "executable doesn't exist") == FailureClass.ENVIRONMENT
    assert classify_failure("playwright", 1, "executable_missing") == FailureClass.ENVIRONMENT
    assert classify_failure("pytest", 0, "") is None


def test_report_is_sanitized_and_records_only_release_evidence(tmp_path: Path) -> None:
    report = ReleaseGateReport(
        code_version="abc1234",
        config_category="deterministic-test",
        schema_version=44,
        deterministic_checks=[
            CheckEvidence(
                name="python-tests",
                status="passed",
                duration_ms=12,
            )
        ],
        real_probes=[
            ProviderProbeEvidence(
                provider="duckduckgo",
                status="failed",
                semantic_health="unavailable",
                duration_ms=88,
                error_category="web_search_timeout",
                failure_class=FailureClass.EXTERNAL,
                worker_cleanup=True,
            )
        ],
        risks=["real_provider_probe_failed"],
        release_ready=False,
    )
    target = tmp_path / "report.json"
    write_report(report, target)
    payload = json.loads(target.read_text(encoding="utf-8"))

    assert payload["release_ready"] is False
    assert payload["schema_version"] == 44
    assert payload["real_probes"][0]["error_category"] == "web_search_timeout"
    serialized = target.read_text(encoding="utf-8")
    assert "用户原始问题" not in serialized
    assert "secret" not in serialized.lower()
