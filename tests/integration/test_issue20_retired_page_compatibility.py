"""Issue 20：退役独立页面的兼容观测合同。"""

from fastapi.testclient import TestClient

from bridges.api.main import create_app


def test_retired_page_observation_counts_stable_redirect_routes_separately() -> None:
    client = TestClient(create_app())

    real = client.post("/compatibility/pages/legacy.pages.tasks")
    probe = client.post(
        "/compatibility/pages/legacy.pages.tasks",
        headers={"X-Bridges-Compatibility-Probe": "1"},
    )

    assert real.status_code == 204
    assert probe.status_code == 204
    assert client.app.state.compatibility_metrics.snapshot()["routes"][
        "legacy.pages.tasks"
    ] == {"real": 1, "probe": 1}
    assert client.app.state.observability_service.compatibility_gate_snapshot() == [
        {
            "endpoint_id": "legacy.pages.tasks",
            "service_version": "0.1.0",
            "traffic_class": "real",
            "status_code": 307,
            "count": 1,
        },
        {
            "endpoint_id": "legacy.pages.tasks",
            "service_version": "0.1.0",
            "traffic_class": "probe",
            "status_code": 307,
            "count": 1,
        },
    ]


def test_retired_page_observation_rejects_unknown_route_ids() -> None:
    client = TestClient(create_app())

    response = client.post("/compatibility/pages/legacy.pages.unknown")

    assert response.status_code == 404
    assert client.app.state.compatibility_metrics.snapshot()["routes"] == {}
