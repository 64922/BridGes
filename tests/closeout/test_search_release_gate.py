"""Issue 07：搜索提供方收口与发布门组成项测试（ADR-0029）。

覆盖 `.scratch/7/issues/07-search-provider-release-gate.md` Test plan
1–2 的组成项：

- 能力清单：通用网页搜索恰好分类一次且为 ``external_non_qwen``；
- 生产组合断言：提供方清单恰好只有 ``tavily``，备用配置失败关闭；
- 密钥泄漏硬门：``tvly-`` 形态扫描命中/白名单/发布报告进程内复核
  失败关闭；
- 三态 smoke：真实正文获取（Extract 单 URL）的 passed/failed/
  inconclusive 判定与合并语义；
- 发布门总装：金标路由（Issue 03）与降级语义（Issue 02）测试文件
  纳入 ``RELEASE_GATE_PYTHON_TESTS``，密钥扫描与报告复核成为门禁检查项。
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from bridges.closeout.manifest import (
    CAPABILITY_MANIFEST_VERSION,
    PRODUCTION_CAPABILITY_MANIFEST,
    CapabilityCategory,
    validate_manifest,
)
from bridges.closeout.release_gate import (
    RELEASE_GATE_PYTHON_TESTS,
    CheckEvidence,
    CheckStatus,
    FailureClass,
    _load_artifact_scan,
    _probe_web,
    _probe_web_body_fetch,
    _production_provider_check,
    _scan_report_for_secrets,
    run_gate,
)
from bridges.web_search.providers import build_web_search_provider
from bridges.web_search.tavily import TavilySearchClient

_FAKE_KEY = "tvly-probe-test-key"


# ---------------------------------------------------------------------------
# 1. 能力清单分类收口
# ---------------------------------------------------------------------------


def test_web_search_classified_exactly_once_as_external_non_qwen() -> None:
    """通用网页搜索恰好分类一次且为 external_non_qwen（带自有凭据）。"""
    claimed = [
        entry
        for entry in PRODUCTION_CAPABILITY_MANIFEST
        if "web_search" in entry.chat_actions
    ]
    assert len(claimed) == 1, "通用网页搜索必须恰好分类一次"
    entry = claimed[0]
    assert entry.id == "tavily_web_search"
    assert entry.category == CapabilityCategory.EXTERNAL_NON_QWEN
    assert entry.model_capability is None, "external_non_qwen 不得绑定 Qwen 能力"
    assert validate_manifest(PRODUCTION_CAPABILITY_MANIFEST) == []


def test_manifest_version_bumped_for_issue07() -> None:
    """Issue 07 收口使清单版本受控递增到 v2。"""
    assert CAPABILITY_MANIFEST_VERSION == 2


# ---------------------------------------------------------------------------
# 2. 生产组合断言（提供方清单恰好只有 tavily）
# ---------------------------------------------------------------------------


def test_production_composition_registers_only_tavily() -> None:
    from bridges.web_search.contracts import PRIMARY_WEB_SEARCH_PROVIDER

    provider = build_web_search_provider(None)
    assert isinstance(provider, TavilySearchClient)
    assert provider.provider_name == "tavily"
    assert PRIMARY_WEB_SEARCH_PROVIDER == "tavily"


def test_production_provider_check_passes_with_only_tavily(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BRIDGES_PUBLIC_SEARCH_FALLBACK_ENABLED", raising=False)
    monkeypatch.delenv("BRIDGES_BRAVE_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("BRIDGES_BRAVE_SEARCH_API_KEY_FILE", raising=False)

    evidence = _production_provider_check()

    assert evidence.status == CheckStatus.PASSED
    assert evidence.error_category is None


def test_production_provider_check_fails_on_fallback_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """备用提供方开关/Key 出现即失败关闭（unexpected_search_provider）。"""
    monkeypatch.setenv("BRIDGES_PUBLIC_SEARCH_FALLBACK_ENABLED", "true")
    monkeypatch.setenv("BRIDGES_BRAVE_SEARCH_API_KEY", "brave-secret-key")

    evidence = _production_provider_check()

    assert evidence.status == CheckStatus.FAILED
    assert evidence.error_category == "unexpected_search_provider"
    assert evidence.failure_class == FailureClass.PRODUCT


# ---------------------------------------------------------------------------
# 3. 密钥泄漏硬门（tvly- 形态）
# ---------------------------------------------------------------------------


def _scanner() -> object:
    scanner = _load_artifact_scan()
    assert scanner is not None, "artifact_secret_scan 必须可被发布门装载"
    return scanner


def test_secret_scan_detects_tavily_key_shape() -> None:
    # 运行时拼接构造真实形态的假 Key：源码不含连续字面量，避免仓库级
    # 秘密扫描把测试夹具当作提交的秘密。
    fake_key = "tvly-" + "abcdefghijklmnopqrstuvwxyz123456"
    hits = _scanner().scan_text(  # type: ignore[attr-defined]
        f"Authorization: Bearer {fake_key}\n"
    )
    assert ("tavily_key", 1) in hits


def test_secret_scan_ignores_fake_markers_and_short_values() -> None:
    text = "\n".join(
        [
            "tvly-test-key-123",
            "tvly-probe-test-key",
            "tvly-short",
            "tvly-primary-key",
            "tvly-secret-value",
        ]
    )
    assert _scanner().scan_text(text) == []  # type: ignore[attr-defined]


def test_report_scan_fails_closed_on_leaked_report(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    # 运行时拼接（理由同 test_secret_scan_detects_tavily_key_shape）。
    leaked = "tvly-" + "abcdefghijklmnop1234567890"
    report.write_text(
        '{"provider": "tavily", "status": "passed", "credential": "' + leaked + '"}',
        encoding="utf-8",
    )

    evidence = _scan_report_for_secrets(report)

    assert evidence.status == CheckStatus.FAILED
    assert evidence.error_category == "secret_leak_in_report"
    assert evidence.failure_class == FailureClass.PRODUCT


def test_report_scan_passes_on_clean_report(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    report.write_text(
        '{"provider": "tavily", "status": "passed", "result_count": 5}',
        encoding="utf-8",
    )

    evidence = _scan_report_for_secrets(report)

    assert evidence.status == CheckStatus.PASSED
    assert evidence.error_category is None


# ---------------------------------------------------------------------------
# 4. 三态 smoke：真实正文获取判定
# ---------------------------------------------------------------------------


def _extract_response(
    status_code: int = 200, raw_content: str | None = "正文内容"
) -> httpx.Response:
    if status_code != 200:
        return httpx.Response(status_code, json={"error": "probe"})
    return httpx.Response(
        200,
        json={
            "results": [
                {
                    "url": "https://example.com/1",
                    "raw_content": raw_content,
                }
            ]
        },
    )


def test_body_fetch_passed_with_raw_content() -> None:
    transport = httpx.MockTransport(lambda request: _extract_response())
    with httpx.Client(transport=transport) as http_client:
        status, error = _probe_web_body_fetch(
            http_client, SecretStr(_FAKE_KEY), "https://example.com/1"
        )
    assert status == CheckStatus.PASSED
    assert error is None


def test_body_fetch_inconclusive_on_configuration_error() -> None:
    transport = httpx.MockTransport(lambda request: _extract_response(status_code=401))
    with httpx.Client(transport=transport) as http_client:
        status, error = _probe_web_body_fetch(
            http_client, SecretStr(_FAKE_KEY), "https://example.com/1"
        )
    assert status == CheckStatus.INCONCLUSIVE
    assert error == "web_search_configuration"


def test_body_fetch_inconclusive_on_rate_limit() -> None:
    transport = httpx.MockTransport(lambda request: _extract_response(status_code=429))
    with httpx.Client(transport=transport) as http_client:
        status, error = _probe_web_body_fetch(
            http_client, SecretStr(_FAKE_KEY), "https://example.com/1"
        )
    assert status == CheckStatus.INCONCLUSIVE
    assert error == "web_search_rate_limit"


def test_body_fetch_failed_on_contract_mismatch() -> None:
    transport = httpx.MockTransport(
        lambda request: _extract_response(raw_content=None)
    )
    with httpx.Client(transport=transport) as http_client:
        status, error = _probe_web_body_fetch(
            http_client, SecretStr(_FAKE_KEY), "https://example.com/1"
        )
    assert status == CheckStatus.FAILED
    assert error == "web_search_contract"


def _search_transport(
    *, extract_status: int = 200, extract_raw: str | None = "正文内容"
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "title": "Transformer architecture 公开资料",
                            "url": "https://example.com/1",
                            "content": (
                                "Transformer architecture uses attention mechanisms."
                            ),
                        }
                    ]
                },
            )
        if request.url.path == "/extract":
            return _extract_response(status_code=extract_status, raw_content=extract_raw)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_web_probe_passes_only_when_search_and_body_fetch_both_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRIDGES_TAVILY_API_KEY", _FAKE_KEY)

    with httpx.Client(transport=_search_transport()) as http_client:
        probe = _probe_web(http_client)

    assert probe.status == CheckStatus.PASSED
    assert probe.body_fetch == "passed"
    assert probe.result_count == 1
    assert probe.error_category is None


def test_web_probe_inconclusive_when_body_fetch_credentials_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRIDGES_TAVILY_API_KEY", _FAKE_KEY)

    with httpx.Client(transport=_search_transport(extract_status=401)) as http_client:
        probe = _probe_web(http_client)

    assert probe.status == CheckStatus.INCONCLUSIVE
    assert probe.error_category == "web_search_configuration"
    assert probe.failure_class == FailureClass.ENVIRONMENT
    assert probe.body_fetch == "inconclusive"


def test_web_probe_failed_when_body_fetch_contract_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRIDGES_TAVILY_API_KEY", _FAKE_KEY)

    with httpx.Client(transport=_search_transport(extract_raw=None)) as http_client:
        probe = _probe_web(http_client)

    assert probe.status == CheckStatus.FAILED
    assert probe.error_category == "web_search_contract"
    assert probe.failure_class == FailureClass.PRODUCT
    assert probe.body_fetch == "failed"


def test_web_probe_without_key_marks_body_fetch_not_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BRIDGES_TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("BRIDGES_TAVILY_API_KEY_FILE", raising=False)

    with httpx.Client(transport=_search_transport()) as http_client:
        probe = _probe_web(http_client)

    assert probe.status == CheckStatus.INCONCLUSIVE
    assert probe.error_category == "web_search_credentials"
    assert probe.body_fetch == "not_run"


# ---------------------------------------------------------------------------
# 5. 发布门总装：金标路由/降级语义纳入门禁、密钥扫描成为检查项
# ---------------------------------------------------------------------------


def test_release_gate_includes_golden_routes_and_degradation_semantics() -> None:
    tests = set(RELEASE_GATE_PYTHON_TESTS)
    assert "tests/chat/test_golden_intent_routes.py" in tests, (
        "Issue 03 金标路由必须作为发布门组成部分被执行"
    )
    assert "tests/learning/test_teaching_gate.py" in tests, (
        "Issue 02 降级语义（本地不足 + 搜索失败 → 带标注降级）必须纳入门禁"
    )
    assert "tests/chat/test_issue03_learning_evidence_chat.py" in tests
    assert "tests/closeout/test_search_release_gate.py" in tests


def test_release_gate_assembles_issue07_checks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """单命令总装：生产提供方断言、产物密钥扫描与报告复核都是硬门检查项。"""
    recorded: list[str] = []

    def fake_run_command(
        *,
        name: str,
        command: list[str],
        cwd: Path,
        environment: dict[str, str],
        timeout_seconds: float,
    ) -> CheckEvidence:
        recorded.append(name)
        return CheckEvidence(name=name, status=CheckStatus.PASSED)

    monkeypatch.setattr(
        "bridges.closeout.release_gate._run_command", fake_run_command
    )
    monkeypatch.setattr(
        "bridges.closeout.release_gate.run_real_provider_probes", lambda: []
    )
    report_path = tmp_path / "report.json"

    report = run_gate(repo_root=tmp_path, report_path=report_path)

    assert "python-tests" in recorded
    assert "artifact-secret-scan" in recorded
    names = [check.name for check in report.deterministic_checks]
    assert "production-search-provider" in names
    assert "release-report-secret-scan" in names
    assert report.status == "passed"
    assert report_path.is_file()
