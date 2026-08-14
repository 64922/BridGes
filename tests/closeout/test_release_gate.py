"""Issue 09/04/01：发布门报告的分类、脱敏、真实探针显式开关与 Tavily 合同校验。

Issue 01：真实发布探针只接受 Tavily 可解析结果且 provider/version/
错误语义符合合同；缺 Key、网络未授权或外部暂时不可达报告 ``inconclusive``
并非零退出；备用提供方 Key/开关是配置漂移；mock/fixture 无法让真实门变绿。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from bridges.closeout.release_gate import (
    CheckEvidence,
    CheckStatus,
    FailureClass,
    ProviderProbeEvidence,
    ReleaseGateReport,
    _probe_web,
    _provider_drift_check,
    classify_failure,
    diagnose_web_health,
    main,
    write_report,
)
from bridges.web_search.client import WebSearchError
from bridges.web_search.contracts import (
    WebSearchResult,
    WebSearchVerification,
)
from bridges.web_search.providers import BRAVE_SEARCH_PROVIDER_VERSION
from bridges.web_search.tavily import (
    TAVILY_SEARCH_PROVIDER_VERSION,
)


def _result(
    result_id: str = "probe-1",
    *,
    provider: str = "tavily",
    provider_version: str = TAVILY_SEARCH_PROVIDER_VERSION,
    title: str = "Transformer architecture 公开资料",
    snippet: str = "Transformer architecture uses attention mechanisms.",
) -> WebSearchResult:
    return WebSearchResult(
        result_id=result_id,
        title=title,
        site="example.com",
        url=f"https://example.com/{result_id}",
        snippet=snippet,
        accessed_at=datetime.now(UTC),
        provider=provider,
        provider_version=provider_version,
        verification=WebSearchVerification.VERIFIED,
    )


class _FakeTavilyClient:
    """探针级替身：模拟 release_gate.TavilySearchClient 的构造与 search。"""

    def __init__(self, outcome: object) -> None:
        self.outcome = outcome
        self.constructed: list[tuple[float, bool]] = []

    def __call__(
        self,
        *,
        api_key: object,
        http_client: httpx.Client,
        timeout: float,
        fetch_sources: bool,
    ) -> _FakeTavilyClient:
        del api_key, http_client
        self.constructed.append((timeout, fetch_sources))
        return self

    def search(self, query: str, *, timeout: float | None = None):
        del timeout
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def _install_fake_probe(
    monkeypatch: pytest.MonkeyPatch, outcome: object
) -> _FakeTavilyClient:
    fake = _FakeTavilyClient(outcome)
    monkeypatch.setenv("BRIDGES_TAVILY_API_KEY", "tvly-probe-test-key")
    monkeypatch.setattr("bridges.closeout.release_gate.TavilySearchClient", fake)
    return fake


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
                provider="tavily",
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


def test_report_records_provider_probe_fields(tmp_path: Path) -> None:
    """Issue 04/01：报告包含探针时间、版本、结果数与脱敏错误码。"""
    report = ReleaseGateReport(
        code_version="abc1234",
        config_category="real-provider-probe",
        schema_version=44,
        real_probes=[
            ProviderProbeEvidence(
                provider="tavily",
                status="passed",
                semantic_health="ready",
                checked_at="2026-08-13T12:00:00+00:00",
                duration_ms=123,
                provider_version=TAVILY_SEARCH_PROVIDER_VERSION,
                result_count=5,
                worker_cleanup=True,
            )
        ],
        release_ready=True,
    )
    target = tmp_path / "report.json"
    write_report(report, target)
    payload = json.loads(target.read_text(encoding="utf-8"))

    probe = payload["real_probes"][0]
    assert probe["checked_at"] == "2026-08-13T12:00:00+00:00"
    assert probe["provider_version"] == TAVILY_SEARCH_PROVIDER_VERSION
    assert probe["result_count"] == 5
    assert probe["status"] == "passed"


def test_provider_drift_check_fails_on_fallback_key() -> None:
    evidence = _provider_drift_check(
        {
            "BRIDGES_BRAVE_SEARCH_API_KEY": "brave-secret",
        }
    )

    assert evidence.status == CheckStatus.FAILED
    assert evidence.error_category == "unexpected_search_provider"
    assert evidence.failure_class == FailureClass.PRODUCT


def test_provider_drift_check_fails_on_fallback_switch() -> None:
    evidence = _provider_drift_check(
        {
            "BRIDGES_PUBLIC_SEARCH_FALLBACK_ENABLED": "true",
        }
    )

    assert evidence.status == CheckStatus.FAILED
    assert evidence.error_category == "unexpected_search_provider"


def test_provider_drift_check_passes_with_tavily_primary_key() -> None:
    """Issue 01：Tavily Key 是生产主提供方凭据，不再是配置漂移。"""
    evidence = _provider_drift_check(
        {
            "BRIDGES_ENVIRONMENT": "production",
            "BRIDGES_QWEN_API_KEY": "qwen-only",
            "BRIDGES_TAVILY_API_KEY": "tvly-primary-key",
        }
    )

    assert evidence.status == CheckStatus.PASSED
    assert evidence.error_category is None


def test_provider_drift_check_passes_on_clean_environment() -> None:
    evidence = _provider_drift_check(
        {
            "BRIDGES_ENVIRONMENT": "production",
            "BRIDGES_QWEN_API_KEY": "qwen-only",
        }
    )

    assert evidence.status == CheckStatus.PASSED
    assert evidence.error_category is None


def test_web_probe_passes_only_with_contract_matching_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _install_fake_probe(monkeypatch, [_result()])

    with httpx.Client() as http_client:
        probe = _probe_web(http_client)

    assert probe.status == CheckStatus.PASSED
    assert probe.semantic_health == "ready"
    assert probe.provider == "tavily"
    assert probe.provider_version == TAVILY_SEARCH_PROVIDER_VERSION
    assert probe.result_count == 1
    assert probe.checked_at
    assert probe.error_category is None
    assert probe.failure_class is None
    assert fake.constructed == [(8.0, False)]


def test_web_probe_rejects_fixture_impostor_with_wrong_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fixture/closeout 替身（版本不符）不能冒充真实 Tavily 合同。"""
    _install_fake_probe(
        monkeypatch,
        [
            _result(
                provider="tavily",
                provider_version="closeout-web-fixture-v1",
            )
        ],
    )

    with httpx.Client() as http_client:
        probe = _probe_web(http_client)

    assert probe.status == CheckStatus.FAILED
    assert probe.error_category == "parse_contract_drift"
    assert probe.failure_class == FailureClass.PRODUCT


def test_web_probe_rejects_brave_provider_results_as_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_probe(
        monkeypatch,
        [
            _result(
                provider="brave_search",
                provider_version=BRAVE_SEARCH_PROVIDER_VERSION,
            )
        ],
    )

    with httpx.Client() as http_client:
        probe = _probe_web(http_client)

    assert probe.status == CheckStatus.FAILED
    assert probe.error_category == "parse_contract_drift"
    assert probe.result_count == 1


def test_web_probe_rejects_unparseable_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unrelated = _result(
        title="完全无关的主题",
        snippet="这是与本次探针查询无关的内容。",
    )
    _install_fake_probe(monkeypatch, [unrelated])

    with httpx.Client() as http_client:
        probe = _probe_web(http_client)

    assert probe.status == CheckStatus.FAILED
    assert probe.error_category == "parse_contract_drift"


@pytest.mark.parametrize(
    ("error_code", "expected_status"),
    [
        ("web_search_credentials", CheckStatus.INCONCLUSIVE),
        ("web_search_configuration", CheckStatus.INCONCLUSIVE),
        ("web_search_timeout", CheckStatus.INCONCLUSIVE),
        ("web_search_connect", CheckStatus.INCONCLUSIVE),
        ("web_search_dns", CheckStatus.INCONCLUSIVE),
        ("web_search_offline", CheckStatus.INCONCLUSIVE),
        ("web_search_permission", CheckStatus.INCONCLUSIVE),
        ("web_search_rate_limit", CheckStatus.INCONCLUSIVE),
        ("web_search_provider", CheckStatus.INCONCLUSIVE),
        ("web_search_provider_challenge", CheckStatus.INCONCLUSIVE),
        ("web_search_contract", CheckStatus.FAILED),
        ("web_search_parse", CheckStatus.FAILED),
        ("web_search_redirect", CheckStatus.FAILED),
        ("web_search_response_too_large", CheckStatus.FAILED),
        ("web_search_request", CheckStatus.FAILED),
    ],
)
def test_web_probe_maps_error_codes_to_inconclusive_or_failed(
    monkeypatch: pytest.MonkeyPatch,
    error_code: str,
    expected_status: CheckStatus,
) -> None:
    _install_fake_probe(
        monkeypatch,
        WebSearchError(error_code, "探针失败", permission=error_code == "web_search_permission"),
    )

    with httpx.Client() as http_client:
        probe = _probe_web(http_client)

    assert probe.status == expected_status
    assert probe.error_category == error_code
    assert probe.semantic_health == "unavailable"
    if expected_status == CheckStatus.INCONCLUSIVE:
        if error_code in {"web_search_credentials", "web_search_configuration"}:
            assert probe.failure_class == FailureClass.ENVIRONMENT
        else:
            assert probe.failure_class == FailureClass.EXTERNAL
    else:
        assert probe.failure_class == FailureClass.PRODUCT


def test_web_probe_without_key_is_inconclusive_environment_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BRIDGES_TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("BRIDGES_TAVILY_API_KEY_FILE", raising=False)

    with httpx.Client() as http_client:
        probe = _probe_web(http_client)

    assert probe.status == CheckStatus.INCONCLUSIVE
    assert probe.error_category == "web_search_credentials"
    assert probe.failure_class == FailureClass.ENVIRONMENT


def test_report_is_blocked_by_inconclusive_probe() -> None:
    report = ReleaseGateReport(
        code_version="abc1234",
        config_category="real-provider-probe",
        schema_version=44,
        real_probes=[
            ProviderProbeEvidence(
                provider="tavily",
                status=CheckStatus.INCONCLUSIVE,
                semantic_health="unavailable",
                error_category="web_search_timeout",
                failure_class=FailureClass.EXTERNAL,
            )
        ],
        release_ready=False,
    )

    assert report.status == "blocked"
    assert report.release_ready is False


def test_diagnose_web_health_exit_codes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    passed = ProviderProbeEvidence(
        provider="tavily",
        status=CheckStatus.PASSED,
        semantic_health="ready",
        duration_ms=42,
        provider_version=TAVILY_SEARCH_PROVIDER_VERSION,
        result_count=5,
    )
    inconclusive = ProviderProbeEvidence(
        provider="tavily",
        status=CheckStatus.INCONCLUSIVE,
        semantic_health="unavailable",
        duration_ms=42,
        error_category="web_search_timeout",
        failure_class=FailureClass.EXTERNAL,
    )
    failed = ProviderProbeEvidence(
        provider="tavily",
        status=CheckStatus.FAILED,
        semantic_health="contract_mismatch",
        duration_ms=42,
        error_category="parse_contract_drift",
        failure_class=FailureClass.PRODUCT,
    )

    for probe, expected_code in (
        (passed, 0),
        (inconclusive, 2),
        (failed, 1),
    ):
        monkeypatch.setattr(
            "bridges.closeout.release_gate._probe_web", lambda _http, _probe=probe: _probe
        )
        assert diagnose_web_health() == expected_code
        captured = capsys.readouterr()
        lines = captured.out.splitlines()
        doc_end = next(i for i, line in enumerate(lines) if line == "}")
        payload = json.loads("\n".join(lines[: doc_end + 1]))
        assert payload["status"] == probe.status.value
        assert payload["provider_version"] == probe.provider_version
        assert "Tavily" in captured.out


def test_main_web_health_flag_routes_to_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "bridges.closeout.release_gate.diagnose_web_health",
        lambda: 0,
    )
    assert main(["--web-health"]) == 0
