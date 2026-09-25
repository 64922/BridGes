"""高德适配器合同：缺凭据降级、三种方式各用各的接口、失败分类与有界调用。

Issue 12 验收项要求「步行、自行车、电动车分别使用对应高德结果…不串用其他方式
耗时」「无凭据、地点解析失败或路线服务失败有可见降级」。这些用例用录制结构
（httpx ``MockTransport``）驱动真实解析与分类代码，不依赖外网与真实凭据。
"""

from __future__ import annotations

import json
import threading
from typing import Any

import httpx
import pytest

from bridges.commute.contracts import MODE_ENDPOINTS, CommuteMode
from bridges.commute.sources import (
    AMAP_SOURCE_PLACE,
    AMAP_SOURCE_ROUTE,
    AmapRouteClient,
    coordinates_distance_meters,
)
from bridges.contracts.modules import ModuleQueryStatus

PLACE_PAYLOAD: dict[str, Any] = {
    "status": "1",
    "info": "OK",
    "infocode": "10000",
    "count": "2",
    "pois": [
        {
            "id": "B001",
            "name": "华东交通大学图书馆",
            "location": "115.870000,28.750000",
            "address": "双港东大街808号",
            "adname": "青山湖区",
            "cityname": "南昌市",
        },
        {
            "id": "B002",
            "name": "华东交通大学南区图书馆",
            "location": "115.871000,28.750500",
            "address": "双港东大街808号南区",
            "adname": "青山湖区",
            "cityname": "南昌市",
        },
    ],
}


def _route_payload(distance: int, duration: int, *, with_polyline: bool = True) -> dict[str, Any]:
    step: dict[str, Any] = {
        "instruction": "沿双港东大街向西步行",
        "road_name": "双港东大街",
        "step_distance": str(distance // 2),
    }
    second: dict[str, Any] = {
        "instruction": "到达目的地",
        "road_name": "",
        "step_distance": str(distance - distance // 2),
    }
    if with_polyline:
        step["polyline"] = "115.870000,28.750000;115.869500,28.750100"
        second["polyline"] = "115.869500,28.750100;115.869000,28.750200"
    return {
        "status": "1",
        "info": "OK",
        "infocode": "10000",
        "count": "1",
        "route": {
            "origin": "115.870000,28.750000",
            "destination": "115.869000,28.750200",
            "paths": [
                {
                    "distance": str(distance),
                    "cost": {"duration": str(duration)},
                    "steps": [step, second],
                }
            ],
        },
    }


def _failure(infocode: str, info: str) -> dict[str, Any]:
    return {"status": "0", "info": info, "infocode": infocode}


class _Recorder:
    """记录每次上游请求的路径与参数（用于断言方式与接口一一对应）。"""

    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.requests: list[tuple[str, dict[str, str]]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        self.requests.append((request.url.path, params))
        payload = self.responses.get(request.url.path, {})
        if isinstance(payload, Exception):
            raise payload
        return httpx.Response(200, json=payload)


def _client(recorder: _Recorder, key: str | None = "test-key") -> AmapRouteClient:
    return AmapRouteClient(
        key_provider=lambda: key,
        client=httpx.Client(transport=httpx.MockTransport(recorder.handler)),
    )


def test_missing_key_degrades_without_any_external_call() -> None:
    recorder = _Recorder({})
    client = _client(recorder, key=None)

    place = client.search_place("account-1", "华东交通大学图书馆")
    route = client.route(
        "account-1",
        CommuteMode.WALKING,
        origin="115.87,28.75",
        destination="115.88,28.76",
        origin_name="图书馆",
        destination_name="北门",
    )

    assert recorder.requests == [], "缺少凭据时绝不发出任何外部请求"
    assert place.record is not None
    assert place.record.error_code == "amap_not_configured"
    assert place.record.retryable is False
    assert "设置页" in (place.record.error_message or "")
    assert route.record is not None
    assert route.record.error_code == "amap_not_configured"
    assert route.path is None


def test_place_search_returns_real_pois_with_city_limited_query() -> None:
    recorder = _Recorder({"/v3/place/text": PLACE_PAYLOAD})
    outcome = _client(recorder).search_place("account-1", "华东交通大学图书馆")

    assert outcome.record is not None
    assert outcome.record.source == AMAP_SOURCE_PLACE
    assert outcome.record.status is ModuleQueryStatus.SUCCESS
    assert outcome.record.evidence_count == 2
    assert [poi.name for poi in outcome.pois] == [
        "华东交通大学图书馆",
        "华东交通大学南区图书馆",
    ]
    path, params = recorder.requests[0]
    assert path == "/v3/place/text"
    assert params["city"] == "南昌"
    assert params["citylimit"] == "true"
    assert params["keywords"] == "华东交通大学图书馆"


def test_place_search_empty_is_reported_as_empty_not_as_failure() -> None:
    recorder = _Recorder({"/v3/place/text": {"status": "1", "infocode": "10000", "pois": []}})
    outcome = _client(recorder).search_place("account-1", "华东交通大学不存在楼")

    assert outcome.record is not None
    assert outcome.record.status is ModuleQueryStatus.EMPTY
    assert outcome.record.evidence_count == 0
    assert outcome.pois == []


@pytest.mark.parametrize(
    ("mode", "distance", "duration"),
    [
        (CommuteMode.WALKING, 900, 700),
        (CommuteMode.BICYCLING, 1200, 300),
        (CommuteMode.ELECTROBIKE, 1200, 240),
    ],
)
def test_each_mode_uses_its_own_endpoint_and_its_own_duration(
    mode: CommuteMode, distance: int, duration: int
) -> None:
    """三种方式各自调用对应接口，并各自取自己的距离与耗时（不串用）。"""
    endpoint = MODE_ENDPOINTS[mode]
    recorder = _Recorder({endpoint: _route_payload(distance, duration)})
    outcome = _client(recorder).route(
        "account-1",
        mode,
        origin="115.870000,28.750000",
        destination="115.869000,28.750200",
        origin_name="图书馆",
        destination_name="北门",
    )

    assert outcome.path is not None
    assert outcome.path.distance_m == distance
    assert outcome.path.duration_seconds == duration
    path, params = recorder.requests[0]
    assert path == endpoint
    assert params["show_fields"] == "cost,navi,polyline"
    assert params["origin"] == "115.870000,28.750000"
    assert params["destination"] == "115.869000,28.750200"
    assert outcome.record is not None
    assert outcome.record.source == AMAP_SOURCE_ROUTE
    assert "路径点 4 个" in (outcome.record.detail or "")


def test_route_steps_and_polyline_come_from_the_response() -> None:
    recorder = _Recorder({"/v5/direction/walking": _route_payload(900, 700)})
    outcome = _client(recorder).route(
        "account-1",
        CommuteMode.WALKING,
        origin="115.870000,28.750000",
        destination="115.869000,28.750200",
        origin_name="图书馆",
        destination_name="北门",
    )

    assert outcome.path is not None
    assert [step.instruction for step in outcome.path.steps] == [
        "沿双港东大街向西步行",
        "到达目的地",
    ]
    assert outcome.path.steps[0].road_name == "双港东大街"
    assert outcome.path.steps[0].distance_m == 450
    assert len(outcome.path.polyline) == 4
    assert outcome.path.polyline[0] == "115.870000,28.750000"
    assert outcome.path.snapped_origin == "115.870000,28.750000"
    assert outcome.path.plan_count == 1


def test_route_without_polyline_reports_zero_points_but_keeps_distance() -> None:
    recorder = _Recorder({"/v5/direction/walking": _route_payload(900, 700, with_polyline=False)})
    outcome = _client(recorder).route(
        "account-1",
        CommuteMode.WALKING,
        origin="115.870000,28.750000",
        destination="115.869000,28.750200",
        origin_name="图书馆",
        destination_name="北门",
    )

    assert outcome.path is not None
    assert outcome.path.polyline == ()
    assert outcome.path.distance_m == 900
    assert outcome.record is not None
    assert "路径点 0 个" in (outcome.record.detail or "")


def test_route_without_any_plan_is_honest_failure_for_that_mode() -> None:
    payload = {
        "status": "1",
        "infocode": "10000",
        "route": {"origin": "", "destination": "", "paths": []},
    }
    recorder = _Recorder({"/v5/direction/electrobike": payload})
    outcome = _client(recorder).route(
        "account-1",
        CommuteMode.ELECTROBIKE,
        origin="115.870000,28.750000",
        destination="115.869000,28.750200",
        origin_name="图书馆",
        destination_name="北门",
    )

    assert outcome.path is None
    assert outcome.record is not None
    assert outcome.record.status is ModuleQueryStatus.EMPTY
    assert outcome.record.error_code == "amap_route_unavailable"
    assert "不拿其他方式" in (outcome.record.error_message or "")
    assert outcome.record.retryable is False


def test_route_missing_distance_is_rejected_instead_of_shown() -> None:
    payload = {
        "status": "1",
        "infocode": "10000",
        "route": {"paths": [{"cost": {"duration": "300"}, "steps": []}]},
    }
    recorder = _Recorder({"/v5/direction/bicycling": payload})
    outcome = _client(recorder).route(
        "account-1",
        CommuteMode.BICYCLING,
        origin="115.870000,28.750000",
        destination="115.869000,28.750200",
        origin_name="图书馆",
        destination_name="北门",
    )

    assert outcome.path is None
    assert outcome.record is not None
    assert outcome.record.error_code == "amap_route_unusable"
    assert outcome.record.retryable is True


@pytest.mark.parametrize(
    ("infocode", "info", "expected_code", "retryable", "status"),
    [
        ("10001", "INVALID_USER_KEY", "amap_key_rejected", False, ModuleQueryStatus.ERROR),
        ("10012", "INSUFFICIENT_PRIVILEGES", "amap_key_rejected", False, ModuleQueryStatus.ERROR),
        (
            "10003",
            "DAILY_QUERY_OVER_LIMIT",
            "amap_daily_quota_exceeded",
            False,
            ModuleQueryStatus.ERROR,
        ),
        ("10004", "ACCESS_TOO_FREQUENT", "amap_rate_limited", True, ModuleQueryStatus.RATE_LIMITED),
        ("20801", "起点在无路区域", "amap_route_unavailable", False, ModuleQueryStatus.ERROR),
        ("20003", "参数错误", "amap_bad_request", False, ModuleQueryStatus.ERROR),
        ("99999", "未知原因", "amap_error", True, ModuleQueryStatus.ERROR),
    ],
)
def test_amap_failures_are_classified_with_raw_code_kept(
    infocode: str, info: str, expected_code: str, retryable: bool, status: ModuleQueryStatus
) -> None:
    recorder = _Recorder({"/v5/direction/walking": _failure(infocode, info)})
    outcome = _client(recorder).route(
        "account-1",
        CommuteMode.WALKING,
        origin="115.870000,28.750000",
        destination="115.869000,28.750200",
        origin_name="图书馆",
        destination_name="北门",
    )

    assert outcome.record is not None
    assert outcome.record.error_code == expected_code
    assert outcome.record.retryable is retryable
    assert outcome.record.status is status
    assert infocode in (outcome.record.detail or "")
    assert info in (outcome.record.error_message or ""), "原始 info 必须如实转述，不臆断语义"


def test_timeout_is_reported_as_timeout() -> None:
    recorder = _Recorder({"/v5/direction/walking": httpx.TimeoutException("boom")})
    outcome = _client(recorder).route(
        "account-1",
        CommuteMode.WALKING,
        origin="115.870000,28.750000",
        destination="115.869000,28.750200",
        origin_name="图书馆",
        destination_name="北门",
    )

    assert outcome.record is not None
    assert outcome.record.status is ModuleQueryStatus.TIMEOUT
    assert outcome.record.error_code == "amap_timeout"
    assert outcome.record.retryable is True


def test_transport_error_is_retried_at_most_twice() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        raise httpx.ConnectError("boom", request=request)

    client = AmapRouteClient(
        key_provider=lambda: "test-key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleeper=lambda _seconds: None,
    )
    outcome = client.route(
        "account-1",
        CommuteMode.WALKING,
        origin="115.870000,28.750000",
        destination="115.869000,28.750200",
        origin_name="图书馆",
        destination_name="北门",
    )

    assert calls["count"] == 2, "网络层失败只重试一次（共两次尝试）"
    assert outcome.record is not None
    assert outcome.record.error_code == "amap_transport_error"
    assert "上游请求 2 次" in (outcome.record.detail or "")


def test_non_json_body_is_reported_as_unparseable_not_unreachable() -> None:
    """响应体不是 JSON：按「返回内容无法解析」分类，不报成连不上高德。"""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, text="<html>gateway error</html>")

    client = AmapRouteClient(
        key_provider=lambda: "test-key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleeper=lambda _seconds: None,
    )

    outcome = client.search_place("account-1", "华东交通大学图书馆")

    assert outcome.pois == []
    assert outcome.record is not None
    assert outcome.record.error_code == "amap_bad_response"
    assert "无法解析" in (outcome.record.error_message or "")
    assert calls == ["/v3/place/text"], "内容无法解析不是网络问题，不重试"


def test_stop_request_cancels_before_any_request() -> None:
    recorder = _Recorder({"/v5/direction/walking": _route_payload(900, 700)})
    stop_event = threading.Event()
    stop_event.set()

    outcome = _client(recorder).route(
        "account-1",
        CommuteMode.WALKING,
        origin="115.870000,28.750000",
        destination="115.869000,28.750200",
        origin_name="图书馆",
        destination_name="北门",
        stop_event=stop_event,
    )

    assert recorder.requests == []
    assert outcome.record is not None
    assert outcome.record.status is ModuleQueryStatus.CANCELLED


def test_oversized_response_is_rejected() -> None:
    big = {"status": "1", "infocode": "10000", "pois": [], "padding": "x" * 300_000}
    recorder = _Recorder({"/v3/place/text": big})
    outcome = _client(recorder).search_place("account-1", "华东交通大学图书馆")

    assert outcome.record is not None
    assert outcome.record.error_code == "amap_response_too_large"
    assert outcome.pois == []


def test_query_records_never_include_credentials() -> None:
    recorder = _Recorder({"/v3/place/text": PLACE_PAYLOAD})
    outcome = _client(recorder, key="super-secret-key").search_place(
        "account-1", "华东交通大学图书馆"
    )

    assert outcome.record is not None
    dumped = json.dumps(outcome.record.model_dump(mode="json"), ensure_ascii=False)
    assert "super-secret-key" not in dumped


def test_snap_distance_helper_flags_far_snapping() -> None:
    near = coordinates_distance_meters("115.870000,28.750000", "115.870100,28.750000")
    far = coordinates_distance_meters("115.870000,28.750000", "115.880000,28.750000")

    assert near is not None and near < 20
    assert far is not None and far > 900
    assert coordinates_distance_meters("bad", "115.87,28.75") is None
    assert coordinates_distance_meters(None, "115.87,28.75") is None
