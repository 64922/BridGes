"""工单 42：外部探针的离线验证（MockTransport，不发真实请求）。"""

from __future__ import annotations

import json

import httpx

from bridges.evaluation import external_probe_contracts as contracts
from bridges.evaluation import external_probes as probes
from bridges.evaluation.external_probe_contracts import (
    AvailabilityLevel,
    ProbeContext,
    ProbeResult,
    ProbeStatus,
)
from bridges.evaluation.external_probes import (
    claim_consistency_problems,
    run_all_probes,
    run_probe,
)
from bridges.evaluation.workflow_scenario_contracts import ExternalGate

_QWEN_KEY = "test-qwen-key"


def _context(handler: object, **overrides: object) -> ProbeContext:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    client = httpx.Client(transport=transport, follow_redirects=True)
    return ProbeContext(http=client, **overrides)  # type: ignore[arg-type]


def test_missing_credentials_stay_inconclusive_without_requests() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"缺少凭据时不应发起请求：{request.url.host}")

    context = _context(handler)
    gates = (
        ExternalGate.AMAP_CAMPUS_ROUTES,
        ExternalGate.TIEBA_REPLIES,
        ExternalGate.PUBLIC_JOBS,
        ExternalGate.VIDEO_INTRO,
        ExternalGate.MODEL_CAPABILITIES,
        ExternalGate.WEB_SEARCH,
    )
    results = run_all_probes(context, gates)
    assert {result.gate for result in results} == set(gates)
    for result in results:
        assert result.status is ProbeStatus.INCONCLUSIVE
        assert result.level is AvailabilityLevel.CONFIGURED_UNVERIFIED


def test_tavily_probe_passes_and_records_verified_sources() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.tavily.com"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "华东交通大学图书馆开放时间",
                        "url": "https://www.ecjtu.edu.cn/library",
                        "content": "周一至周日 8:00-22:00",
                    }
                ]
            },
        )

    result = run_probe(ExternalGate.WEB_SEARCH, _context(handler, tavily_key="tavily-test-key"))
    assert result.status is ProbeStatus.PASSED
    assert result.level is AvailabilityLevel.FULL
    assert result.measurements["result_count"] == 1
    assert result.to_dict()["declared_level"] == "full"


def test_amap_probe_measures_each_mode_on_its_own_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v3/place/text":
            return httpx.Response(
                200,
                json={
                    "status": "1",
                    "pois": [
                        {
                            "name": "华东交通大学南区",
                            "location": "115.860,28.680",
                            "address": "双港东大街",
                            "id": "B0FF001",
                            "adname": "新建区",
                        }
                    ],
                },
            )
        assert path in {
            "/v5/direction/walking",
            "/v5/direction/bicycling",
            "/v5/direction/electrobike",
        }
        return httpx.Response(
            200,
            json={
                "status": "1",
                "route": {
                    "origin": "115.860,28.680",
                    "destination": "115.865,28.685",
                    "paths": [
                        {
                            "distance": "1200",
                            "cost": {"duration": "900"},
                            "steps": [
                                {
                                    "instruction": "向北步行",
                                    "road_name": "学府大道",
                                    "step_distance": "500",
                                }
                            ],
                            "polyline": "115.860,28.680;115.865,28.685",
                        }
                    ],
                },
            },
        )

    result = run_probe(ExternalGate.AMAP_CAMPUS_ROUTES, _context(handler, amap_key="amap-test-key"))
    assert result.status is ProbeStatus.PASSED
    assert result.level is AvailabilityLevel.FULL
    modes = result.measurements["modes"]
    assert set(modes) == {"walking", "bicycling", "electrobike"}
    assert all(item["ok"] and item["polyline_points"] == 2 for item in modes.values())


def test_model_probe_uses_effective_model_and_context_window() -> None:
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        payload = json.loads(request.content.decode("utf-8"))
        seen.append(payload)
        if "tools" in payload:
            message = {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "record_ping", "arguments": "{}"},
                    }
                ],
            }
        elif "response_format" in payload:
            message = {"content": '{"pong": true}'}
        elif "开头标记" in str(payload["messages"]):
            message = {"content": '["bridge-alpha", "bridge-beta", "bridge-gamma"]'}
        else:
            message = {"content": "pong"}
        return httpx.Response(
            200, json={"choices": [{"message": message, "finish_reason": "stop"}]}
        )

    result = run_probe(
        ExternalGate.MODEL_CAPABILITIES,
        _context(
            handler,
            qwen_key=_QWEN_KEY,
            effective_model_id="qwen-test-model",
            model_capabilities=("text", "image", "tool_calling", "structured_output"),
            model_context_window=131072,
            model_config_source="settings",
        ),
    )
    assert result.status is ProbeStatus.PASSED
    assert result.level is AvailabilityLevel.FULL
    assert result.measurements["model_id"] == "qwen-test-model"
    assert result.measurements["context_window"] == 131072
    assert result.measurements["config_source"] == "settings"
    assert len(seen) == 5
    assert result.measurements["context_sample"]["ok"]


def test_claim_consistency_flags_only_gates_without_degradation_contract() -> None:
    model_shortfall = ProbeResult(
        gate=ExternalGate.MODEL_CAPABILITIES,
        status=ProbeStatus.FAILED,
        level=AvailabilityLevel.DEGRADED,
        summary="图片能力未通过",
    )
    amap_degraded = ProbeResult(
        gate=ExternalGate.AMAP_CAMPUS_ROUTES,
        status=ProbeStatus.FAILED,
        level=AvailabilityLevel.UNAVAILABLE,
        summary="三种方式都失败",
    )
    problems = claim_consistency_problems([model_shortfall])
    assert len(problems) == 1
    assert "model.configured_capabilities" in problems[0]
    assert claim_consistency_problems([amap_degraded]) == []


def test_probe_failure_never_leaks_secret_material() -> None:
    secret = "SECRET-TAVILY-KEY-123"

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"connect failed for {secret}")

    result = run_probe(ExternalGate.WEB_SEARCH, _context(handler, tavily_key=secret))
    assert result.status is ProbeStatus.INCONCLUSIVE
    assert result.level is AvailabilityLevel.CONFIGURED_UNVERIFIED
    assert secret not in json.dumps(result.to_dict(), ensure_ascii=False)


def test_probe_registry_is_complete_and_levels_are_ordered() -> None:
    assert set(probes.PROBE_REGISTRY) == set(contracts.PRODUCT_CLAIMS)
    assert (
        contracts.LEVEL_ORDER[AvailabilityLevel.FULL]
        > contracts.LEVEL_ORDER[AvailabilityLevel.PARTIAL]
        > contracts.LEVEL_ORDER[AvailabilityLevel.DEGRADED]
        > contracts.LEVEL_ORDER[AvailabilityLevel.UNAVAILABLE]
        > contracts.LEVEL_ORDER[AvailabilityLevel.CONFIGURED_UNVERIFIED]
    )


def test_public_source_filter_checks_hostname_and_path() -> None:
    from bridges.evaluation.external_probes_public_pages import _matches_public_source

    assert _matches_public_source("https://www.bilibili.com/video/BV123", "bilibili.com/video/BV")
    assert _matches_public_source("https://jobs.zhipin.com/job/123", "zhipin.com")
    for url in (
        "https://bilibili.com.evil.example/video/BV123",
        "https://evil.example/?next=bilibili.com/video/BV123",
        "https://www.bilibili.com/other/video/BV123",
        "https://user@www.bilibili.com/video/BV123",
        "file://www.bilibili.com/video/BV123",
        "https://[invalid/video/BV123",
    ):
        assert not _matches_public_source(url, "bilibili.com/video/BV"), url
