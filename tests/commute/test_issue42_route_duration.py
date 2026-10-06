"""工单 42：骑行真实响应的 path.duration 不应被误报为无路线。"""

from __future__ import annotations

import httpx
import pytest

from bridges.commute.contracts import MODE_ENDPOINTS, CommuteMode
from bridges.commute.sources import AmapRouteClient
from bridges.contracts.modules import ModuleQueryStatus


@pytest.mark.parametrize(
    ("mode", "duration", "expected"),
    [
        (CommuteMode.BICYCLING, "300", 300),
        (CommuteMode.ELECTROBIKE, "240", 240),
        (CommuteMode.WALKING, "700", None),
        (CommuteMode.BICYCLING, "bad", None),
        (CommuteMode.ELECTROBIKE, True, None),
    ],
)
def test_path_duration_is_only_accepted_for_the_requested_riding_mode(
    mode: CommuteMode, duration: object, expected: int | None
) -> None:
    """同一响应字段只适用于骑行方式；非法时长仍保持可见失败。"""
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "status": "1",
                "infocode": "10000",
                "route": {
                    "paths": [
                        {
                            "distance": "1200",
                            "duration": duration,
                            "steps": [
                                {
                                    "instruction": "沿道路向北",
                                    "polyline": "115.87,28.75;115.88,28.76",
                                }
                            ],
                        }
                    ]
                },
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = AmapRouteClient(key_provider=lambda: "test-key", client=http)
        result = client.route(
            "account-1",
            mode,
            origin="115.87,28.75",
            destination="115.88,28.76",
            origin_name="图书馆",
            destination_name="北门",
        )
    assert requested == [MODE_ENDPOINTS[mode]]
    assert result.record is not None
    if expected is None:
        assert result.path is None
        assert result.record.error_code == "amap_route_unusable"
    else:
        assert result.record.status is ModuleQueryStatus.SUCCESS
        assert result.path is not None
        assert result.path.duration_seconds == expected
        assert result.path.distance_m == 1200
        assert result.path.polyline == ("115.87,28.75", "115.88,28.76")
