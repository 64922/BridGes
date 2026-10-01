"""改进工单 10：通勤配方的恢复、复用与失效（固定高德响应的确定性验证）。

覆盖验收标准的确定性部分（真实服务可得性由评测票 42 单独实测）：

- 地点成功/路线失败后重试只重跑路线及下游，不重复地点检索与本地效果；
- 方式变更复用定位产物，并使旧的路线/时间/呈现产物失效；
- POI 成功不被当成路线成功：失败投影仍带真实地点与分类；
- 产物/收据/事件按账户与会话持久，节点事件只报告真实执行的节点。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.commute.contracts import CommuteMode
from bridges.contracts.modules import ModuleQueryStatus
from bridges.kernel.repository import NodeKernelRepository
from tests.chat.test_chat_api import _create_conversation, _register
from tests.commute.test_commute_module_flow import (
    MODE_FIXTURES,
    _FakeAmap,
    _install_commute,
    _run_and_read,
    _send,
)


@pytest.fixture
def sqlite_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    from bridges.api.main import create_app
    from bridges.config import get_settings

    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "api-test-secret-key")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    return create_app()


@pytest.fixture
def client(sqlite_app: Any) -> TestClient:
    return TestClient(sqlite_app)


def _account_id(sqlite_app: Any, conversation_id: str) -> str:
    row = sqlite_app.state.bridges_database.connection.execute(
        "SELECT account_id FROM conversations WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    assert row is not None
    return str(row["account_id"])


def _retry(client: TestClient, conversation_id: str, message_id: str) -> str:
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages/{message_id}/retry",
        json={},
    )
    assert response.status_code == 200, response.text
    return str(response.json()["assistant_message"]["message_id"])


def test_route_failure_resumes_route_only_without_repeating_local_effects(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """地点已成功、路线失败：重试复用定位产物，只重跑路线及下游节点。"""
    _register(client)
    fake = _install_commute(
        sqlite_app,
        _FakeAmap(
            route_failure=ModuleQueryStatus.TIMEOUT,
            route_error_code="amap_timeout",
            route_error_message="高德查询超时，请稍后重试。",
        ),
    )
    conversation_id = _create_conversation(client)
    _send(client, conversation_id, "从南区步行到图书馆", module_id="commute")
    first = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    assert first["status"] == "error"
    route = first["commute_route"]
    assert route["status"] == "error"
    assert route["error_code"] == "amap_timeout"
    # POI 成功不被当成路线成功：失败投影仍带真实已解析地点与查询记录。
    assert route["origin"]["name"] == "华东交通大学南区"
    assert route["destination"]["name"] == "华东交通大学图书馆"
    place_calls = list(fake.place_calls)
    assert place_calls, "首次运行必须真实检索地点"
    assert len(fake.route_calls) == 1

    # 恢复路线服务并走既有重试路径（新运行、同一条用户消息）。
    fake.route_failure = None
    _retry(client, conversation_id, first["message_id"])
    generation_helpers["drive"](sqlite_app)
    final = client.get(f"/chat/conversations/{conversation_id}").json()
    latest = [m for m in final["messages"] if m["role"] == "assistant"][-1]
    assert latest["commute_route"]["status"] == "success"
    assert len(latest["commute_route"]["polyline"]) == 2

    # 定位未重复检索；只有路线节点再次真实执行。
    assert fake.place_calls == place_calls
    assert [mode for mode, _, _ in fake.route_calls] == [
        CommuteMode.WALKING,
        CommuteMode.WALKING,
    ]
    database = sqlite_app.state.bridges_database
    assert database is not None
    account_id = _account_id(sqlite_app, conversation_id)
    repository = NodeKernelRepository(database)
    resolved = [
        item
        for item in repository.list_artifacts(account_id, conversation_id)
        if item.node == "route.resolve"
    ]
    assert len(resolved) == 1, "同一输入只保留一份定位产物"
    receipts = database.connection.execute(
        "SELECT node, COUNT(*) AS total FROM node_receipts"
        " WHERE account_id = ? AND conversation_id = ? GROUP BY node",
        (account_id, conversation_id),
    ).fetchall()
    counts = {str(row["node"]): int(row["total"]) for row in receipts}
    assert counts.get("route.resolve") == 1, "定位只执行/收据一次（重试回填）"
    assert counts.get("route.request") == 2, "路线失败一次、恢复一次，各留真实收据"
    assert counts.get("route.present") == 1, "失败轮不产生交付产物"


def test_mode_change_reuses_places_and_invalidates_old_route(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """方式变更复用定位产物，并按输入键使旧路线/时间/呈现产物失效。"""
    _register(client)
    fake = _install_commute(sqlite_app, _FakeAmap())
    conversation_id = _create_conversation(client)
    _send(client, conversation_id, "从南区步行到图书馆", module_id="commute")
    walking = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)
    assert walking["commute_route"]["status"] == "success"
    place_calls = list(fake.place_calls)

    _send(client, conversation_id, "从南区骑车到图书馆", module_id="commute")
    biking = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)
    route = biking["commute_route"]
    assert route["status"] == "success"
    assert route["mode"] == CommuteMode.BICYCLING.value
    distance, duration = MODE_FIXTURES[CommuteMode.BICYCLING]
    assert route["distance_m"] == distance
    assert route["base_duration_seconds"] == duration

    # 定位不重复检索；两种方式各取自己的高德结果。
    assert fake.place_calls == place_calls
    assert [mode for mode, _, _ in fake.route_calls] == [
        CommuteMode.WALKING,
        CommuteMode.BICYCLING,
    ]

    database = sqlite_app.state.bridges_database
    assert database is not None
    account_id = _account_id(sqlite_app, conversation_id)
    repository = NodeKernelRepository(database)
    artifacts = repository.list_artifacts(account_id, conversation_id)
    resolved = [item for item in artifacts if item.node == "route.resolve"]
    assert len(resolved) == 1, "方式变更必须复用同一份定位产物"
    requests = [item for item in artifacts if item.node == "route.request"]
    assert len(requests) == 2, "每种方式各一份真实路线产物"
    by_mode = {item.payload["mode"]: item for item in requests}
    assert by_mode[CommuteMode.WALKING.value].trust_state.value == "invalidated"
    assert by_mode[CommuteMode.BICYCLING.value].trust_state.value == "qualified"
