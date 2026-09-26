"""Issue 12 端到端合同：显式派发、三种方式隔离、澄清等待、降级与停止。

全部走真实 HTTP + SQLite + 后台执行器（假高德端口，无外网）：

- 通勤子图只由逐消息 ``module_id=commute`` 或「点击建议」显式启动；
- 结果消息包含真实起终点、方式、距离、高德耗时、路径点、文字路段与课间规则缓冲；
- 缺起点／终点／方式只问一项，回答后从等待状态恢复；
- 校内 POI 候选冲突时持久化等待用户选择；校外地名被如实拒绝；
- 无凭据、路线服务失败与停止都是可见的真实状态，绝不编造路线。
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from bridges.ai.adapters import StreamChunk
from bridges.commute.contracts import CommuteMode
from bridges.commute.service import CommuteService
from bridges.commute.sources import (
    AMAP_SOURCE_PLACE,
    AMAP_SOURCE_ROUTE,
    AmapPoi,
    AmapRouteClient,
    AmapStep,
    PlaceSearchOutcome,
    RouteOutcome,
    RoutePath,
)
from bridges.contracts.modules import ModuleQueryRecord, ModuleQueryStatus
from tests.chat.test_chat_api import _create_conversation, _gateway_with, _register

#: 固定时钟：2026-09-25 04:00 UTC = 12:00 Asia/Shanghai（命中课间点 12:00）。
NOON_SHANGHAI = datetime(2026, 9, 25, 4, 0, tzinfo=UTC)
#: 固定时钟：2026-09-25 03:00 UTC = 11:00 Asia/Shanghai（不在任何课间窗口内）。
PLAIN_TIME = datetime(2026, 9, 25, 3, 0, tzinfo=UTC)

#: 三种方式的假高德结果（距离与耗时互不相同，用于验证不串用）。
MODE_FIXTURES: dict[CommuteMode, tuple[int, int]] = {
    CommuteMode.WALKING: (1800, 1400),
    CommuteMode.BICYCLING: (2400, 600),
    CommuteMode.ELECTROBIKE: (2400, 480),
}


class _FakeAmap:
    """可控高德端口：POI 与路线都由测试给定，记录每次真实调用。"""

    def __init__(
        self,
        *,
        places: dict[str, list[AmapPoi]] | None = None,
        default_campus: bool = True,
        with_polyline: bool = True,
        snapped_far: bool = False,
        route_failure: ModuleQueryStatus | None = None,
        route_error_code: str = "amap_timeout",
        route_error_message: str = "高德查询超时，请稍后重试。",
        route_retryable: bool = True,
        stop_on_route: bool = False,
    ) -> None:
        self.places = places or {}
        self.default_campus = default_campus
        self.with_polyline = with_polyline
        self.snapped_far = snapped_far
        self.route_failure = route_failure
        self.route_error_code = route_error_code
        self.route_error_message = route_error_message
        self.route_retryable = route_retryable
        self.stop_on_route = stop_on_route
        self.place_calls: list[str] = []
        self.route_calls: list[tuple[CommuteMode, str, str]] = []
        self._lock = threading.Lock()
        self._locations: dict[str, str] = {}

    # -- 端口实现 --------------------------------------------------------

    def search_place(
        self,
        account_id: str,
        keywords: str,
        *,
        city: str = "南昌",
        deadline: float | None = None,
        stop_event: Any = None,
    ) -> PlaceSearchOutcome:
        del account_id, city, deadline, stop_event
        with self._lock:
            self.place_calls.append(keywords)
        pois = self.places.get(keywords)
        if pois is None:
            pois = self._default_pois(keywords)
        return PlaceSearchOutcome(
            query=keywords,
            pois=pois,
            record=ModuleQueryRecord(
                source=AMAP_SOURCE_PLACE,
                query=keywords,
                status=ModuleQueryStatus.SUCCESS if pois else ModuleQueryStatus.EMPTY,
                evidence_count=len(pois),
                retrieved_at=datetime.now(UTC),
            ),
        )

    def route(
        self,
        account_id: str,
        mode: CommuteMode,
        *,
        origin: str,
        destination: str,
        origin_name: str,
        destination_name: str,
        deadline: float | None = None,
        stop_event: Any = None,
    ) -> RouteOutcome:
        del account_id, deadline
        query = f"{origin_name}→{destination_name}"
        with self._lock:
            self.route_calls.append((mode, origin, destination))
        if self.stop_on_route and stop_event is not None:
            stop_event.set()
            return RouteOutcome(
                query=query,
                mode=mode,
                record=ModuleQueryRecord(
                    source=AMAP_SOURCE_ROUTE,
                    query=query,
                    status=ModuleQueryStatus.CANCELLED,
                    retrieved_at=datetime.now(UTC),
                    error_code="amap_cancelled",
                    error_message="用户停止了本轮查询。",
                ),
            )
        if self.route_failure is not None:
            return RouteOutcome(
                query=query,
                mode=mode,
                record=ModuleQueryRecord(
                    source=AMAP_SOURCE_ROUTE,
                    query=query,
                    status=self.route_failure,
                    retrieved_at=datetime.now(UTC),
                    error_code=self.route_error_code,
                    error_message=self.route_error_message,
                    retryable=self.route_retryable,
                ),
            )
        distance, duration = MODE_FIXTURES[mode]
        polyline = (
            ("115.870000,28.750000", "115.869000,28.750200") if self.with_polyline else ()
        )
        snapped_origin = "115.880000,28.760000" if self.snapped_far else origin
        return RouteOutcome(
            query=query,
            mode=mode,
            path=RoutePath(
                distance_m=distance,
                duration_seconds=duration,
                steps=(
                    AmapStep(
                        instruction="沿双港东大街向西", road_name="双港东大街", distance_m=200
                    ),
                    AmapStep(instruction="到达目的地", road_name=None, distance_m=100),
                ),
                polyline=polyline,
                snapped_origin=snapped_origin,
                snapped_destination=destination,
                plan_count=1,
            ),
            record=ModuleQueryRecord(
                source=AMAP_SOURCE_ROUTE,
                query=query,
                status=ModuleQueryStatus.SUCCESS,
                evidence_count=1,
                retrieved_at=datetime.now(UTC),
                detail=f"方案数 1，采用第 1 条；路径点 {len(polyline)} 个。",
            ),
        )

    def _default_pois(self, keywords: str) -> list[AmapPoi]:
        name = keywords if self.default_campus else keywords.replace("华东交通大学", "南昌")
        # 每个检索词一个稳定的独立坐标：起终点不会撞成同一点，仍可重复断言。
        location = self._locations.setdefault(
            keywords, f"115.8700{len(self._locations):02d},28.7500{len(self._locations):02d}"
        )
        return [
            AmapPoi(
                name=name,
                location=location,
                address="双港东大街808号",
                poi_id=f"B{len(self._locations):03d}",
                district="青山湖区",
                            )
        ]


class _SilentAdapter:
    """普通聊天用的静默流式适配器（通勤轮不调用模型）。"""

    def stream_call(self, capability: Any, run_context: Any, payload: dict[str, Any]) -> Any:
        del capability, run_context, payload
        yield StreamChunk(kind="delta", delta="好的。")


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


def _install_commute(app: Any, amap: Any, *, clock: datetime = PLAIN_TIME) -> Any:
    """把通勤模块的高德端口换成替身（编排、父图派发与消息落库保持真实）。"""
    app.state.commute_service = CommuteService(amap=amap, clock=lambda: clock)
    app.state.chat_service._commute = app.state.commute_service  # noqa: SLF001
    return amap


def _send(
    client: TestClient, conversation_id: str, content: str, *, module_id: str | None = None
) -> dict[str, Any]:
    body: dict[str, Any] = {"content": content}
    if module_id is not None:
        body["module_id"] = module_id
    response = client.post(f"/chat/conversations/{conversation_id}/messages", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def _run_and_read(
    app: Any, client: TestClient, drive: Any, conversation_id: str
) -> dict[str, Any]:
    drive(app)
    projection = client.get(f"/chat/conversations/{conversation_id}").json()
    return [m for m in projection["messages"] if m["role"] == "assistant"][-1]


def test_explicit_commute_module_returns_real_route_with_break_buffer(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """显式选择校园通勤：真实地点、距离、耗时、路段、路径点与课间缓冲。"""
    _register(client)
    fake = _install_commute(sqlite_app, _FakeAmap(), clock=NOON_SHANGHAI)
    conversation_id = _create_conversation(client)
    _send(client, conversation_id, "从南区步行到图书馆", module_id="commute")
    assistant = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    assert assistant["status"] == "done"
    route = assistant["commute_route"]
    assert route["status"] == "success"
    assert route["mode"] == CommuteMode.WALKING.value
    assert route["mode_label"] == "步行"
    assert route["mode_phrase"] == "步行"
    assert route["origin"]["name"] == "华东交通大学南区"
    assert route["destination"]["name"] == "华东交通大学图书馆"
    assert route["origin"]["campus_verified"] is True
    assert route["distance_m"] == MODE_FIXTURES[CommuteMode.WALKING][0]
    assert route["base_duration_seconds"] == MODE_FIXTURES[CommuteMode.WALKING][1]
    # 课间缓冲：12:00 命中窗口，建议总时间 = 高德耗时 + 5 分钟。
    assert route["buffer"]["in_window"] is True
    assert route["buffer"]["matched_break_time"] == "12:00"
    assert route["buffer"]["added_minutes"] == 5
    assert route["buffer"]["timezone"] == "Asia/Shanghai"
    assert "不是实时人流数据" in route["buffer"]["rule_note"]
    assert route["suggested_total_seconds"] == route["base_duration_seconds"] + 300
    assert route["path_verified"] is True
    assert len(route["polyline"]) == 2
    assert [step["instruction"] for step in route["steps"]] == [
        "沿双港东大街向西",
        "到达目的地",
    ]
    assert route["queries"][0]["source"] == AMAP_SOURCE_PLACE
    assert route["queries"][-1]["source"] == AMAP_SOURCE_ROUTE
    assert route["pending"] is None
    assert assistant["paper_search"] is None
    # 正文写清范围与规则，且不展示内部日志
    assert "高德基础耗时" in assistant["content"]
    assert "课间缓冲" in assistant["content"]
    assert "不是实时人流数据" in assistant["content"]
    assert fake.route_calls == [(CommuteMode.WALKING, *fake.route_calls[0][1:])]


def test_three_modes_each_use_their_own_amap_result(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """步行／自行车／电动车各取对应高德结果，耗时与距离不互相顶替。"""
    _register(client)
    fake = _install_commute(sqlite_app, _FakeAmap())
    conversation_id = _create_conversation(client)
    seen: dict[str, tuple[int, int]] = {}
    for phrase, mode in (
        ("从南区步行到图书馆", CommuteMode.WALKING),
        ("从南区骑车到图书馆", CommuteMode.BICYCLING),
        ("从南区骑电动车到图书馆", CommuteMode.ELECTROBIKE),
    ):
        _send(client, conversation_id, phrase, module_id="commute")
        assistant = _run_and_read(
            sqlite_app, client, generation_helpers["drive"], conversation_id
        )
        route = assistant["commute_route"]
        assert route["status"] == "success"
        assert route["mode"] == mode.value
        distance, duration = MODE_FIXTURES[mode]
        assert route["distance_m"] == distance
        assert route["base_duration_seconds"] == duration
        seen[mode.value] = (distance, duration)

    assert len(set(seen.values())) == 3, "三种方式的结果必须互不相同"
    assert [mode for mode, _, _ in fake.route_calls] == list(MODE_FIXTURES)


def test_missing_mode_asks_one_question_then_resumes(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """缺方式只追问方式，回答后从等待状态恢复并真实规划。"""
    _register(client)
    fake = _install_commute(sqlite_app, _FakeAmap())
    conversation_id = _create_conversation(client)
    _send(client, conversation_id, "从南区到图书馆怎么走", module_id="commute")
    assistant = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    route = assistant["commute_route"]
    assert route["status"] == "clarification"
    assert route["pending"] is not None
    assert route["pending"]["module_id"] == "commute"
    assert route["pending"]["kind"] == "clarification"
    assert assistant["content"].count("？") == 1
    assert fake.place_calls == [], "澄清轮绝不发外部请求"
    assert fake.route_calls == []

    _send(client, conversation_id, "骑车", module_id="commute")
    resumed = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    assert resumed["commute_route"]["status"] == "success"
    assert resumed["commute_route"]["mode"] == CommuteMode.BICYCLING.value
    assert fake.route_calls[0][0] is CommuteMode.BICYCLING


def test_missing_origin_then_destination_then_mode_resumes_across_turns(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """只有目的地时先问起点，答完再问方式；每轮只问一项。"""
    _register(client)
    _install_commute(sqlite_app, _FakeAmap())
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, "去图书馆怎么走", module_id="commute")
    first = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)
    assert first["commute_route"]["pending"]["context"]["awaiting"] == "origin"
    assert first["content"].count("？") == 1

    _send(client, conversation_id, "南区", module_id="commute")
    second = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)
    assert second["commute_route"]["status"] == "clarification"
    assert second["commute_route"]["pending"]["context"]["awaiting"] == "mode"
    assert second["commute_route"]["origin"] is None, "未确定方式前不做外部解析"

    _send(client, conversation_id, "步行", module_id="commute")
    third = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)
    route = third["commute_route"]
    assert route["status"] == "success"
    assert route["origin"]["name"] == "华东交通大学南区"
    assert route["destination"]["name"] == "华东交通大学图书馆"


def test_multiple_campus_pois_wait_for_user_choice_and_reuse_it(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """校内 POI 候选冲突：列出候选等待用户选择，选定后不再重复检索该侧。"""
    _register(client)
    candidates = [
        AmapPoi(
            name="华东交通大学图书馆",
            location="115.870000,28.750000",
            address="双港东大街808号",
            poi_id="B001",
            district="青山湖区",
                    ),
        AmapPoi(
            name="华东交通大学南区图书馆",
            location="115.871000,28.750500",
            address="双港东大街808号南区",
            poi_id="B002",
            district="青山湖区",
                    ),
    ]
    fake = _install_commute(
        sqlite_app, _FakeAmap(places={"华东交通大学图书馆": candidates})
    )
    conversation_id = _create_conversation(client)
    _send(client, conversation_id, "从南区步行到图书馆", module_id="commute")
    assistant = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    route = assistant["commute_route"]
    assert route["status"] == "clarification"
    assert [item["name"] for item in route["destination_candidates"]] == [
        "华东交通大学图书馆",
        "华东交通大学南区图书馆",
    ]
    assert route["pending"]["kind"] == "clarification"
    assert "华东交通大学南区图书馆" in assistant["content"]
    assert fake.route_calls == [], "候选未确定前绝不规划路线"
    library_calls = [item for item in fake.place_calls if "图书馆" in item]

    _send(client, conversation_id, "2", module_id="commute")
    resumed = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    final = resumed["commute_route"]
    assert final["status"] == "success"
    assert final["destination"]["name"] == "华东交通大学南区图书馆"
    assert final["destination"]["location"] == "115.871000,28.750500"
    assert any("沿用上一轮候选选择" in note for note in final["evidence_notes"])
    assert [item for item in fake.place_calls if "图书馆" in item] == library_calls, (
        "选定后不再重复检索同一侧地点"
    )


def test_out_of_campus_place_is_refused_with_visible_reason(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """只匹配到校外地名时如实说明范围限制，不规划校外路线。"""
    _register(client)
    fake = _install_commute(sqlite_app, _FakeAmap(default_campus=False))
    conversation_id = _create_conversation(client)
    _send(client, conversation_id, "从南区步行到南昌西站", module_id="commute")
    assistant = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    route = assistant["commute_route"]
    assert route["status"] == "clarification"
    assert "不规划校外路线" in assistant["content"]
    assert fake.route_calls == [], "校外地名绝不发起路线规划"


def test_unresolvable_place_asks_for_building_name_without_guessing(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """POI 检索没有结果：只请用户补建筑名或入口，不猜坐标。"""
    _register(client)
    _install_commute(
        sqlite_app,
        _FakeAmap(places={"华东交通大学魔法楼": [], "魔法楼": []}),
    )
    conversation_id = _create_conversation(client)
    _send(client, conversation_id, "从南区步行到魔法楼", module_id="commute")
    assistant = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    route = assistant["commute_route"]
    assert route["status"] == "clarification"
    assert "没有在高德找到" in assistant["content"]
    assert "猜测的坐标" in assistant["content"]
    assert route["destination"] is None


def test_missing_amap_credential_degrades_to_settings_guidance(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """无高德凭据：可见降级并指出配置位置，不产生任何外部请求。"""
    _register(client)
    calls: list[str] = []

    def _fail_if_called(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(500, json={})

    real_client = AmapRouteClient(
        key_provider=lambda: None,
        client=httpx.Client(transport=httpx.MockTransport(_fail_if_called)),
    )
    _install_commute(sqlite_app, real_client)
    conversation_id = _create_conversation(client)
    _send(client, conversation_id, "从南区步行到图书馆", module_id="commute")
    assistant = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    assert assistant["status"] == "error"
    assert assistant["error_code"] == "amap_not_configured"
    route = assistant["commute_route"]
    assert route["status"] == "error"
    assert route["retryable"] is False
    assert "设置页" in (route["error_message"] or "")
    assert calls == [], "未配置凭据时绝不发出外部请求"


def test_route_failure_is_recorded_and_retryable_then_succeeds(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """路线服务失败：分类与查询词如实落在同一消息里，重试走既有路径。"""
    _register(client)
    _install_commute(
        sqlite_app,
        _FakeAmap(
            route_failure=ModuleQueryStatus.TIMEOUT,
            route_error_code="amap_timeout",
            route_error_message="高德查询超时，请稍后重试。",
        ),
    )
    conversation_id = _create_conversation(client)
    _send(client, conversation_id, "从南区步行到图书馆", module_id="commute")
    assistant = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    assert assistant["status"] == "error"
    assert assistant["error_code"] == "amap_timeout"
    route = assistant["commute_route"]
    assert route["status"] == "error"
    assert route["retryable"] is True
    assert route["origin"]["name"] == "华东交通大学南区"
    assert route["queries"][-1]["status"] == ModuleQueryStatus.TIMEOUT.value

    _install_commute(sqlite_app, _FakeAmap())
    retried = client.post(
        f"/chat/conversations/{conversation_id}/messages/{assistant['message_id']}/retry",
        json={},
    )
    assert retried.status_code == 200, retried.text
    new_message_id = retried.json()["assistant_message"]["message_id"]
    generation_helpers["drive"](sqlite_app)
    final = client.get(f"/chat/conversations/{conversation_id}").json()
    attempt = next(m for m in final["messages"] if m["message_id"] == new_message_id)
    assert attempt["commute_route"]["status"] == "success"


def test_new_complete_request_supersedes_an_older_wait(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """等待状态下重新写完整的出行请求：以新话为准，不被旧载荷改写。"""
    _register(client)
    _install_commute(sqlite_app, _FakeAmap())
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, "从南区到图书馆怎么走", module_id="commute")
    first = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)
    assert first["commute_route"]["pending"]["context"]["awaiting"] == "mode"

    _send(client, conversation_id, "从宿舍骑车到食堂怎么走", module_id="commute")
    second = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    route = second["commute_route"]
    assert route["status"] == "success"
    assert route["origin"]["name"] == "华东交通大学宿舍"
    assert route["destination"]["name"] == "华东交通大学食堂"
    assert route["mode"] == CommuteMode.BICYCLING.value


def test_older_wait_is_not_resurrected_after_a_failed_turn(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """失败轮之后不再翻出更早的等待：新消息按全新请求解析，只问真正缺的一项。"""
    _register(client)
    _install_commute(
        sqlite_app, _FakeAmap(route_failure=ModuleQueryStatus.TIMEOUT)
    )
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, "从南区到图书馆怎么走", module_id="commute")
    first = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)
    assert first["commute_route"]["pending"]["context"]["awaiting"] == "mode"

    _send(client, conversation_id, "步行", module_id="commute")
    second = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)
    assert second["commute_route"]["status"] == "error"
    assert second["commute_route"]["pending"] is None

    _send(client, conversation_id, "去北门怎么走", module_id="commute")
    third = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    route = third["commute_route"]
    assert route["status"] == "clarification"
    # 缺的是起点：不能被两轮前的「等方式」载荷接管
    assert route["pending"]["context"]["awaiting"] == "origin"


def test_evidence_notes_quote_only_queries_actually_sent(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """证据边界里的检索词必须是真正发出去的词，不是重新推导出来的候选词。"""
    _register(client)
    fake = _install_commute(sqlite_app, _FakeAmap())
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, "从南区步行到图书馆", module_id="commute")
    assistant = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    route = assistant["commute_route"]
    assert route["status"] == "success"
    assert route["origin"]["query"] in fake.place_calls
    assert route["destination"]["query"] in fake.place_calls
    evidence = " ".join(route["evidence_notes"])
    assert route["origin"]["query"] in evidence
    assert route["destination"]["query"] in evidence
    sent = {item["query"] for item in route["queries"] if item["source"] == "amap_place"}
    assert {route["origin"]["query"], route["destination"]["query"]} <= sent


def test_stop_during_route_converges_to_stopped(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """用户停止：如实显示已停止，不写成失败或「没有结果」。"""
    _register(client)
    _install_commute(sqlite_app, _FakeAmap(stop_on_route=True))
    conversation_id = _create_conversation(client)
    _send(client, conversation_id, "从南区步行到图书馆", module_id="commute")
    assistant = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    assert assistant["status"] == "stopped"
    assert assistant["commute_route"]["status"] == "stopped"
    assert "已停止" in assistant["content"]


def test_missing_path_points_marks_unverified_and_draws_nothing(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """未取得路径点：状态标为未验证，明确不绘制路线，只保留已证实信息。"""
    _register(client)
    _install_commute(sqlite_app, _FakeAmap(with_polyline=False))
    conversation_id = _create_conversation(client)
    _send(client, conversation_id, "从南区步行到图书馆", module_id="commute")
    assistant = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    route = assistant["commute_route"]
    assert route["status"] == "unverified"
    assert route["path_verified"] is False
    assert route["polyline"] == []
    assert route["distance_m"] == MODE_FIXTURES[CommuteMode.WALKING][0]
    assert "没有绘制任何路线线" in assistant["content"]
    assert any("不绘制任何路线线" in note for note in route["evidence_notes"])


def test_far_snapping_is_reported_as_a_limitation(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """高德把起点吸附到很远处：如实标注，不把它当成精确楼门。"""
    _register(client)
    _install_commute(sqlite_app, _FakeAmap(snapped_far=True))
    conversation_id = _create_conversation(client)
    _send(client, conversation_id, "从南区步行到图书馆", module_id="commute")
    assistant = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    route = assistant["commute_route"]
    assert route["status"] == "success"
    assert any("吸附到约" in note and "该楼门可能不在可通行道路旁" in note
               for note in route["evidence_notes"])


def test_plain_chat_suggests_commute_without_any_external_call(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """普通聊天里的明显通勤请求只给一键建议，点击后以原文显式派发。"""
    _register(client)
    fake = _install_commute(sqlite_app, _FakeAmap())
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)
    created = _send(client, conversation_id, "从南区骑车到图书馆怎么走")
    assistant = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    suggestion = assistant["module_suggestion"]
    assert suggestion is not None
    assert suggestion["module_id"] == "commute"
    assert suggestion["label"] == "使用校园通勤"
    assert suggestion["text"] == "从南区骑车到图书馆怎么走"
    assert assistant["commute_route"] is None
    assert fake.place_calls == [], "建议本身绝不发起外部请求"
    assert fake.route_calls == []

    dispatched = client.post(
        f"/chat/conversations/{conversation_id}/messages/{assistant['message_id']}/retry",
        json={"module_id": "commute"},
    )
    assert dispatched.status_code == 200, dispatched.text
    assert (
        dispatched.json()["user_message"]["message_id"] == created["user_message"]["message_id"]
    )
    generation_helpers["drive"](sqlite_app)
    final = client.get(f"/chat/conversations/{conversation_id}").json()
    users = [m for m in final["messages"] if m["role"] == "user"]
    assert len(users) == 1, "点击建议不得重复写用户消息"
    assert users[0]["module_id"] is None, "历史模块标识不被改写"
    latest = [m for m in final["messages"] if m["role"] == "assistant"][-1]
    assert latest["commute_route"]["status"] == "success"
    assert fake.route_calls, "点击建议后确实规划路线"


def test_modules_not_yet_available_are_still_rejected(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """未接入模块仍被明确拒绝；普通聊天不产生任何通勤记录。"""
    _register(client)
    _install_commute(sqlite_app, _FakeAmap())
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)
    # 反例取尚未接入的 github（Issue 15 合并后 career 已可用，不能再当反例）。
    _send(client, conversation_id, "帮我推荐几个开源项目", module_id="github")
    assistant = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    assert assistant["status"] == "error"
    assert assistant["error_code"] == "module_not_available"

    plain = _send(client, conversation_id, "你好，随便聊聊")
    plain_assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )
    assert plain_assistant["commute_route"] is None
    assert plain["user_message"]["module_id"] is None
