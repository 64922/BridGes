"""Issue 16：四维画像公开合同与旧治理兼容窗口。"""

from __future__ import annotations

from typing import Any, cast

from fastapi.testclient import TestClient

from bridges.api.main import create_app
from bridges.contracts.profiles import ManualAssertionCreateRequest, ProfileDimension


def _register(client: TestClient, username: str, email: str) -> str:
    response = client.post(
        "/auth/register",
        json={
            "username": username,
            "qq_email": email,
            "password": "correct-horse-16",
        },
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())["account"]["id"]


def _seed_four_dimension_record(client: TestClient, account_id: str) -> None:
    old_service = client.app.state.profile_service
    old_service.manual_create_assertion(
        account_id,
        ManualAssertionCreateRequest(
            dimension=ProfileDimension.STAGE_GOAL,
            value_or_rule="完成物理实验报告",
        ),
    )
    client.app.state.four_dimension_profile_service.migrate_account(account_id)


def test_legacy_profile_routes_are_410_without_profile_side_effects() -> None:
    client = TestClient(create_app())
    account_id = _register(client, "issue16-retired", "160001@qq.com")

    retired_routes = [
        ("post", "/profiles/observations"),
        ("get", "/profiles/observations"),
        ("get", "/profiles/observations/observation-1"),
        ("post", "/profiles/candidates"),
        ("get", "/profiles/candidates"),
        ("get", "/profiles/candidates/candidate-1"),
        ("post", "/profiles/candidates/candidate-1/decision"),
        ("post", "/profiles/candidates/batch-decision"),
        ("get", "/profiles/assertions"),
        ("get", "/profiles/assertions/assertion-1"),
        ("post", "/profiles/assertions/manual"),
        ("post", "/profiles/assertions/assertion-1/freeze"),
        ("post", "/profiles/assertions/assertion-1/withdraw"),
        ("post", "/profiles/assertions/assertion-1/unfreeze"),
        ("post", "/profiles/assertions/assertion-1/modify"),
        ("post", "/profiles/assertions/assertion-1/rollback"),
        ("post", "/profiles/assertions/assertion-1/delete"),
        ("get", "/profiles/assertions/assertion-1/history"),
        ("get", "/profiles/export"),
        ("get", "/profiles/permissions"),
        ("put", "/profiles/permissions"),
        ("get", "/profiles/notifications"),
        ("post", "/profiles/notifications/notification-1/read"),
        ("post", "/profiles/notifications/notification-1/recall"),
        ("get", "/profiles/memory-slice"),
        ("get", "/profiles/memory-slices/slice-1"),
        ("get", "/profiles/memory-slices/slice-1/inspector"),
        ("post", "/profiles/memory-slices/slice-1/access-check"),
        ("get", "/profiles/four-dimensions/migration-report"),
        ("post", "/profiles/four-dimensions/migrate"),
    ]

    before = client.get("/profiles/four-dimensions")
    assert before.status_code == 200
    assert before.json() == []

    for method, path in retired_routes:
        response = client.request(
            method.upper(),
            path,
            content=b"{malformed legacy body",
        )
        assert response.status_code == 410, (method, path, response.text)
        detail = response.json()["detail"]
        assert detail == {
            "error": "profile_governance_retired",
            "message": "旧画像治理接口已退役，请在四维画像页面修改或撤回已有记录。",
            "replacement_path": "/account/profile",
            "endpoint": detail["endpoint"],
            "service_version": detail["service_version"],
        }
        assert "observation-1" not in str(detail)
        assert account_id not in str(detail)

    repeated = client.post(
        "/profiles/assertions/manual",
        content=b"{malformed legacy body",
    )
    assert repeated.status_code == 410

    after = client.get("/profiles/four-dimensions")
    assert after.status_code == 200
    assert after.json() == []

    metrics = client.app.state.compatibility_metrics.snapshot()
    assert metrics["routes"]["profiles.assertions.manual"] == {"real": 2, "probe": 0}


def test_legacy_profile_probe_is_observed_without_private_context() -> None:
    client = TestClient(create_app())
    account_id = _register(client, "issue16-probe", "160002@qq.com")

    response = client.get(
        "/profiles/notifications",
        headers={"X-Bridges-Compatibility-Probe": "true"},
    )

    assert response.status_code == 410
    detail = response.json()["detail"]
    assert "issue16-probe" not in str(detail)
    assert account_id not in str(detail)
    assert client.app.state.compatibility_metrics.snapshot()["routes"][
        "profiles.notifications.list"
    ] == {"real": 0, "probe": 1}
    assert client.app.state.observability_service.compatibility_gate_snapshot() == [
        {
            "endpoint_id": "profiles.notifications.list",
            "service_version": "0.1.0",
            "traffic_class": "probe",
            "status_code": 410,
            "count": 1,
        }
    ]


def test_four_dimension_public_contract_keeps_only_read_modify_withdraw() -> None:
    client = TestClient(create_app())
    account_id = _register(client, "issue16-four", "160003@qq.com")
    _seed_four_dimension_record(client, account_id)

    listed = client.get("/profiles/four-dimensions")
    assert listed.status_code == 200, listed.text
    record = listed.json()[0]
    assert set(record) == {
        "record_id",
        "dimension",
        "label",
        "content",
        "first_stable_recorded_at",
        "version",
        "status",
    }
    assert record["dimension"] == "stage_goal"
    first_recorded_at = record["first_stable_recorded_at"]

    modified = client.patch(
        f"/profiles/four-dimensions/{record['record_id']}",
        json={"content": "完成第二版物理实验报告", "version": record["version"]},
    )
    assert modified.status_code == 200, modified.text
    assert modified.json()["content"] == "完成第二版物理实验报告"
    assert modified.json()["first_stable_recorded_at"] == first_recorded_at
    assert modified.json()["version"] == record["version"] + 1

    stale = client.patch(
        f"/profiles/four-dimensions/{record['record_id']}",
        json={"content": "过期写入", "version": record["version"]},
    )
    assert stale.status_code == 409, stale.text

    withdrawn = client.post(
        f"/profiles/four-dimensions/{record['record_id']}/withdraw",
        json={"version": modified.json()["version"]},
    )
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["status"] == "withdrawn"
    assert client.get("/profiles/four-dimensions").json() == []

    profile_paths = client.app.openapi()["paths"]
    assert not any(
        method.lower() == "delete" and path.startswith("/profiles/four-dimensions")
        for path, operations in profile_paths.items()
        for method in operations
    )
