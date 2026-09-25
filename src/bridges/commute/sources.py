"""高德路线与 POI 适配器（V2 Issue 12）。

只做两件可核验的事，且每次都留下统一记录（``query``／``evidence``／
``retrieved_at``／``error``）：

1. **POI 检索**：把校内检索词发给 ``/v3/place/text``（城市限定南昌），返回
   真实 POI 名称、地址与坐标——地点坐标只能来自这里，模块不内置任何坐标；
2. **路线规划**：按方式调用高德路线规划 2.0 的**对应**接口
   （``/v5/direction/walking``、``/v5/direction/bicycling``、
   ``/v5/direction/electrobike``），保存原始距离、基础耗时、路径点与路段。

三种方式彼此独立：某方式失败或没有路线时如实报错，绝不拿另一种方式的耗时
冒充。外部调用只发送完成任务所需的检索词与坐标，用户私有材料不进入请求；
超时、有限重试（仅网络层，最多两次）与停止检查都在这里收口。

**实施期边界（无凭据环境不可完成的部分）**：本机没有可用的高德 Web 服务
凭据，接口字段（``route.paths[].steps[].polyline`` 等）按官方文档实现并用
录制响应测试；真实校园 POI 与楼门可通行性仍需在有凭据的机器上实测。
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

import httpx

from bridges.commute.contracts import MODE_ENDPOINTS, MODE_LABELS, CommuteMode
from bridges.commute.lexicon import PLACE_CITY
from bridges.contracts.modules import ModuleQueryRecord, ModuleQueryStatus
from bridges.contracts.observability import AuditAction, AuditResult
from bridges.observability.service import ObservabilityService

#: 来源标识（统一记录里区分 POI 检索与路线规划）。
AMAP_SOURCE_PLACE = "amap_place"
AMAP_SOURCE_ROUTE = "amap_route"

AMAP_REST_BASE = "https://restapi.amap.com"
PLACE_TEXT_PATH = "/v3/place/text"

#: 单次外部调用的墙钟预算（秒）：超时如实失败，绝不无限等待上游。
ROUTE_TIMEOUT_SECONDS = 12.0
PLACE_TIMEOUT_SECONDS = 10.0

#: 网络层失败的最大尝试次数（含首次）；协议层错误不重试。
MAX_ATTEMPTS = 2

#: POI 检索返回条数上限（够用即止，不翻页拉全量）。
PLACE_PAGE_SIZE = 10

#: 响应体大小上限（字节）：超出即视为不可用，不把整包正文读进内存。
MAX_RESPONSE_BYTES = 262_144

#: 路线吸附距离上限（米）：高德把起终点吸附到更远处的原 POI 坐标超过此值时，
#: 如实标注「楼门可能不在可通行道路旁」，不把它当成精确起终点。
SNAP_LIMIT_METERS = 300.0

#: 缺少凭据时的中文指引（含操作顺序），只提配置位置，不含任何秘密正文。
MISSING_KEY_CODE = "amap_not_configured"
MISSING_KEY_MESSAGE = (
    "未配置高德路线 Web 服务 Key：请在设置页「密钥与模型管理 → 高德凭据」"
    "中填写并验证路线规划 Key，然后重试。"
)


@dataclass(frozen=True)
class AmapPoi:
    """高德 POI 检索返回的一条结果（原样字段，模块不改写坐标）。"""

    name: str
    location: str | None
    address: str | None
    poi_id: str | None
    district: str | None
    city: str | None


@dataclass(frozen=True)
class AmapStep:
    """高德返回的一个路段。"""

    instruction: str
    road_name: str | None
    distance_m: int | None


@dataclass(frozen=True)
class RoutePath:
    """高德返回的一条可用路线（采用第一条方案）。"""

    distance_m: int
    duration_seconds: int
    steps: tuple[AmapStep, ...]
    polyline: tuple[str, ...]
    snapped_origin: str | None
    snapped_destination: str | None
    plan_count: int


@dataclass(frozen=True)
class PlaceSearchOutcome:
    """POI 检索结果与统一记录。"""

    query: str
    pois: list[AmapPoi] = field(default_factory=list)
    record: ModuleQueryRecord | None = None


@dataclass(frozen=True)
class RouteOutcome:
    """路线规划结果与统一记录。"""

    query: str
    mode: CommuteMode
    path: RoutePath | None = None
    record: ModuleQueryRecord | None = None


class CommuteAmapPort(Protocol):
    """通勤模块依赖的高德端口（生产实现为 ``AmapRouteClient``，测试用可控替身）。

    只有这两件事会真正发出外部请求；端口刻意保持最小，模块其余部分（解析、
    缓冲、呈现、落库）不依赖网络实现。
    """

    def search_place(
        self,
        account_id: str,
        keywords: str,
        *,
        city: str = PLACE_CITY,
        deadline: float | None = None,
        stop_event: threading.Event | None = None,
    ) -> PlaceSearchOutcome: ...

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
        stop_event: threading.Event | None = None,
    ) -> RouteOutcome: ...


class AmapRouteClient:
    """高德 Web 服务客户端：POI 检索 + 三种方式的路线规划。

    ``key_provider`` 每次调用时读取当前生效的高德 Web 服务 Key（设置页更换后
    立即对新请求生效，无需重建客户端）；没有 Key 时返回可见降级记录，而不是
    抛出异常或静默返回空结果。
    """

    def __init__(
        self,
        *,
        key_provider: Callable[[], str | None],
        client: httpx.Client | None = None,
        route_timeout: float = ROUTE_TIMEOUT_SECONDS,
        place_timeout: float = PLACE_TIMEOUT_SECONDS,
        observability: ObservabilityService | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self._key_provider = key_provider
        self._client = client or httpx.Client(timeout=route_timeout)
        self._owns_client = client is None
        self._route_timeout = route_timeout
        self._place_timeout = place_timeout
        self._observability = observability
        self._sleep = sleeper or time.sleep

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    # -- POI 检索 --------------------------------------------------------

    def search_place(
        self,
        account_id: str,
        keywords: str,
        *,
        city: str = PLACE_CITY,
        deadline: float | None = None,
        stop_event: threading.Event | None = None,
    ) -> PlaceSearchOutcome:
        """按检索词查找 POI；``city`` 限定为南昌，避免命中同名外地地点。"""
        query = f"{city}·{keywords}"
        key, failure = self._require_key(AMAP_SOURCE_PLACE, query)
        if failure is not None:
            return PlaceSearchOutcome(query=keywords, record=failure)
        assert key is not None
        params = {
            "key": key,
            "keywords": keywords,
            "city": city,
            "citylimit": "true",
            "offset": str(PLACE_PAGE_SIZE),
            "page": "1",
            "extensions": "all",
            "output": "JSON",
        }
        payload, error = self._get_json(
            AMAP_SOURCE_PLACE,
            PLACE_TEXT_PATH,
            params,
            query=query,
            keywords=keywords,
            timeout=self._place_timeout,
            deadline=deadline,
            stop_event=stop_event,
            account_id=account_id,
        )
        if error is not None:
            return PlaceSearchOutcome(query=keywords, record=error)
        pois = _pois_from_payload(payload)
        return PlaceSearchOutcome(
            query=keywords,
            pois=pois,
            record=ModuleQueryRecord(
                source=AMAP_SOURCE_PLACE,
                query=keywords,
                status=ModuleQueryStatus.SUCCESS if pois else ModuleQueryStatus.EMPTY,
                evidence_count=len(pois),
                retrieved_at=datetime.now(UTC),
                detail=None if pois else "高德在该检索词下没有返回 POI。",
            ),
        )

    # -- 路线规划 --------------------------------------------------------

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
        stop_event: threading.Event | None = None,
    ) -> RouteOutcome:
        """按方式调用对应的路线接口；只保存该方式自己的距离、耗时与路径点。"""
        endpoint = MODE_ENDPOINTS[mode]
        query = f"{MODE_LABELS[mode]} {origin_name}→{destination_name}"
        key, failure = self._require_key(AMAP_SOURCE_ROUTE, query)
        if failure is not None:
            return RouteOutcome(query=query, mode=mode, record=failure)
        assert key is not None
        params = {
            "key": key,
            "origin": origin,
            "destination": destination,
            "show_fields": "cost,navi,polyline",
            "output": "JSON",
        }
        payload, error = self._get_json(
            AMAP_SOURCE_ROUTE,
            endpoint,
            params,
            query=query,
            keywords=f"{origin_name}→{destination_name}",
            timeout=self._route_timeout,
            deadline=deadline,
            stop_event=stop_event,
            account_id=account_id,
        )
        if error is not None:
            return RouteOutcome(query=query, mode=mode, record=error)
        route_block = payload.get("route")
        paths = route_block.get("paths") if isinstance(route_block, dict) else None
        if not isinstance(paths, list) or not paths:
            return RouteOutcome(
                query=query,
                mode=mode,
                record=ModuleQueryRecord(
                    source=AMAP_SOURCE_ROUTE,
                    query=query,
                    status=ModuleQueryStatus.EMPTY,
                    evidence_count=0,
                    retrieved_at=datetime.now(UTC),
                    error_code="amap_route_unavailable",
                    error_message=(
                        f"高德没有返回{MODE_LABELS[mode]}的可用路线，"
                        "我不拿其他方式的耗时顶替。"
                    ),
                    retryable=False,
                    detail="路径规划返回空方案。",
                ),
            )
        path = _path_from_payload(
            paths[0], route_block if isinstance(route_block, dict) else {}, len(paths)
        )
        if path is None:
            return RouteOutcome(
                query=query,
                mode=mode,
                record=ModuleQueryRecord(
                    source=AMAP_SOURCE_ROUTE,
                    query=query,
                    status=ModuleQueryStatus.EMPTY,
                    evidence_count=0,
                    retrieved_at=datetime.now(UTC),
                    error_code="amap_route_unusable",
                    error_message="高德返回的路线缺少距离或耗时字段，本轮不展示不可核验的结果。",
                    retryable=True,
                    detail="路径规划响应缺少 distance/cost.duration。",
                ),
            )
        return RouteOutcome(
            query=query,
            mode=mode,
            path=path,
            record=ModuleQueryRecord(
                source=AMAP_SOURCE_ROUTE,
                query=query,
                status=ModuleQueryStatus.SUCCESS,
                evidence_count=1,
                retrieved_at=datetime.now(UTC),
                detail=f"方案数 {len(paths)}，采用第 1 条；路径点 {len(path.polyline)} 个。",
            ),
        )

    # -- 内部实现 --------------------------------------------------------

    def _require_key(
        self, source: str, query: str
    ) -> tuple[str | None, ModuleQueryRecord | None]:
        key = (self._key_provider() or "").strip()
        if key:
            return key, None
        return None, ModuleQueryRecord(
            source=source,
            query=query,
            status=ModuleQueryStatus.ERROR,
            evidence_count=0,
            error_code=MISSING_KEY_CODE,
            error_message=MISSING_KEY_MESSAGE,
            retryable=False,
            detail="未配置凭据，未发出任何外部请求。",
        )

    def _get_json(
        self,
        source: str,
        path: str,
        params: dict[str, str],
        *,
        query: str,
        keywords: str,
        timeout: float,
        deadline: float | None,
        stop_event: threading.Event | None,
        account_id: str,
    ) -> tuple[dict[str, object], ModuleQueryRecord | None]:
        """发起一次有界 GET：停止检查、超时、网络层有限重试与脱敏审计。"""
        started = time.monotonic()
        url = f"{AMAP_REST_BASE}{path}"
        attempts = 0
        transport_error: httpx.HTTPError | None = None
        while attempts < MAX_ATTEMPTS:
            if stop_event is not None and stop_event.is_set():
                return {}, _record(
                    source,
                    query,
                    ModuleQueryStatus.CANCELLED,
                    error_code="amap_cancelled",
                    error_message="用户停止了本轮查询。",
                    retryable=False,
                )
            attempts += 1
            try:
                response = self._client.get(
                    url, params=params, timeout=self._timeout_for(timeout, deadline)
                )
                response.raise_for_status()
                if len(response.content) > MAX_RESPONSE_BYTES:
                    return {}, _record(
                        source,
                        query,
                        ModuleQueryStatus.ERROR,
                        error_code="amap_response_too_large",
                        error_message="高德返回内容超出可处理大小，本轮不展示不可核验的结果。",
                        retryable=True,
                    )
                payload = response.json()
            except httpx.TimeoutException:
                transport_error = None
                self._audit(account_id, source, keywords, started, attempts, "timeout")
                return {}, _record(
                    source,
                    query,
                    ModuleQueryStatus.TIMEOUT,
                    error_code="amap_timeout",
                    error_message="高德查询超时，请稍后重试。",
                    retryable=True,
                    detail=f"上游请求 {attempts} 次。",
                )
            except (httpx.HTTPError, ValueError) as exc:
                transport_error = exc if isinstance(exc, httpx.HTTPError) else None
                if transport_error is not None and attempts < MAX_ATTEMPTS:
                    self._sleep(0.5)
                    continue
                self._audit(account_id, source, keywords, started, attempts, "transport_error")
                return {}, _record(
                    source,
                    query,
                    ModuleQueryStatus.ERROR,
                    error_code="amap_transport_error",
                    error_message="无法连接高德服务，请检查网络后重试。",
                    retryable=True,
                    detail=f"上游请求 {attempts} 次。",
                )
            if not isinstance(payload, dict):
                self._audit(account_id, source, keywords, started, attempts, "bad_payload")
                return {}, _record(
                    source,
                    query,
                    ModuleQueryStatus.ERROR,
                    error_code="amap_bad_response",
                    error_message="高德返回了无法解析的内容，本轮不展示不可核验的结果。",
                    retryable=True,
                )
            infocode = str(payload.get("infocode") or "")
            ok = str(payload.get("status")) == "1" and infocode in {"", "10000"}
            if ok:
                self._audit(account_id, source, keywords, started, attempts, "ok")
                return payload, None
            code, message, retryable = _classify_amap_failure(
                infocode, str(payload.get("info") or "")
            )
            self._audit(account_id, source, keywords, started, attempts, code)
            return {}, _record(
                source,
                query,
                ModuleQueryStatus.RATE_LIMITED
                if code == "amap_rate_limited"
                else ModuleQueryStatus.ERROR,
                error_code=code,
                error_message=message,
                retryable=retryable,
                detail=f"infocode={infocode or '未知'}",
            )
        del transport_error
        return {}, _record(
            source,
            query,
            ModuleQueryStatus.ERROR,
            error_code="amap_transport_error",
            error_message="无法连接高德服务，请检查网络后重试。",
            retryable=True,
        )

    def _timeout_for(self, timeout: float, deadline: float | None) -> float:
        if deadline is None:
            return timeout
        remaining = deadline - time.monotonic()
        return max(0.5, min(timeout, remaining))

    def _audit(
        self,
        account_id: str,
        source: str,
        keywords: str,
        started: float,
        attempts: int,
        terminal: str,
    ) -> None:
        """记录一次外发地图请求（账户归属 + 服务 + 查询指纹，不含查询正文）。"""
        if self._observability is None:
            return
        self._observability.log_audit(
            actor_account_id=account_id,
            action=AuditAction.CAMPUS_ROUTE_LOOKUP,
            result=(
                AuditResult.SUCCESS if terminal == "ok" else AuditResult.RETRYABLE_FAIL
            ),
            reason="校园通勤模块高德查询",
            details={
                "data_categories": ["public_query_terms"],
                "provider": source,
                "query_fingerprint": hashlib.sha256(keywords.encode("utf-8")).hexdigest()[:16],
                "query_length": len(keywords),
                "terminal": terminal,
                "attempt_count": attempts,
                "elapsed_ms": max(0, int((time.monotonic() - started) * 1000)),
            },
        )


def _record(
    source: str,
    query: str,
    status: ModuleQueryStatus,
    *,
    error_code: str,
    error_message: str,
    retryable: bool,
    detail: str | None = None,
) -> ModuleQueryRecord:
    return ModuleQueryRecord(
        source=source,
        query=query,
        status=status,
        evidence_count=0,
        retrieved_at=datetime.now(UTC),
        error_code=error_code,
        error_message=error_message,
        retryable=retryable,
        detail=detail,
    )


def _classify_amap_failure(infocode: str, info: str) -> tuple[str, str, bool]:
    """把高德失败码分类为 (稳定错误码, 中文说明, 是否值得重试)。

    只对已确知的凭据与限流类码做特殊处理；其余一律带原始 ``infocode`` 与
    ``info`` 如实转述，绝不臆断语义，也绝不改说成「没有结果」。
    """
    raw = f"{infocode} {info}".strip()
    if infocode in {"10001", "10009", "10012", "10013"}:
        return (
            "amap_key_rejected",
            f"高德凭据被拒绝（{raw}）：请在设置页「密钥与模型管理 → 高德凭据」"
            "核对路线规划 Web 服务 Key 与平台绑定。",
            False,
        )
    if infocode == "10003":
        return (
            "amap_daily_quota_exceeded",
            f"高德今日调用量已达上限（{raw}），请明天再试或更换 Key。",
            False,
        )
    if infocode == "10004":
        return (
            "amap_rate_limited",
            f"高德请求过于频繁（{raw}），请稍后重试。",
            True,
        )
    if infocode.startswith("208"):
        return (
            "amap_route_unavailable",
            f"高德没有返回该方式的可用路线（{raw}）。",
            False,
        )
    if infocode.startswith("2"):
        return (
            "amap_bad_request",
            f"高德拒绝了本次请求（{raw}）。",
            False,
        )
    return ("amap_error", f"高德返回失败（{raw}）。", True)


def _pois_from_payload(payload: dict[str, object]) -> list[AmapPoi]:
    raw = payload.get("pois")
    if not isinstance(raw, list):
        return []
    pois: list[AmapPoi] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        pois.append(
            AmapPoi(
                name=name.strip(),
                location=_text(item.get("location")),
                address=_text(item.get("address")),
                poi_id=_text(item.get("id")),
                district=_text(item.get("adname")),
                city=_text(item.get("cityname")),
            )
        )
    return pois


def _path_from_payload(
    path: object, route_block: dict[str, object], plan_count: int
) -> RoutePath | None:
    if not isinstance(path, dict):
        return None
    distance = _int(path.get("distance"))
    cost = path.get("cost")
    duration = _int(cost.get("duration")) if isinstance(cost, dict) else None
    if distance is None or duration is None:
        return None
    steps = _steps_from_path(path)
    return RoutePath(
        distance_m=distance,
        duration_seconds=duration,
        steps=tuple(steps),
        polyline=tuple(_polyline_from_path(path, steps)),
        snapped_origin=_text(route_block.get("origin")),
        snapped_destination=_text(route_block.get("destination")),
        plan_count=plan_count,
    )


def _steps_from_path(path: dict[str, object]) -> list[AmapStep]:
    raw = path.get("steps")
    if not isinstance(raw, list):
        return []
    steps: list[AmapStep] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        instruction = _text(item.get("instruction"))
        if instruction is None:
            continue
        steps.append(
            AmapStep(
                instruction=instruction,
                road_name=_text(item.get("road_name")),
                distance_m=_int(item.get("step_distance")),
            )
        )
    return steps


def _polyline_from_path(path: dict[str, object], steps: list[AmapStep]) -> list[str]:
    """路径点：优先逐段取（高德按 ``show_fields=polyline`` 返回），否则取整条。"""
    points: list[str] = []
    raw_steps = path.get("steps")
    if isinstance(raw_steps, list):
        for item in raw_steps:
            if not isinstance(item, dict):
                continue
            points.extend(_split_polyline(item.get("polyline")))
    if points:
        return points
    del steps
    return _split_polyline(path.get("polyline"))


def _split_polyline(raw: object) -> list[str]:
    if not isinstance(raw, str):
        return []
    return [item.strip() for item in raw.split(";") if item.strip()]


def _text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def coordinates_distance_meters(first: str | None, second: str | None) -> float | None:
    """两个高德坐标串之间的近似直线距离（米）；任一不可解析时返回 None。

    只用于**标注局限**（高德把起终点吸附到远处），不参与任何路线计算。
    """
    left = _parse_coordinate(first)
    right = _parse_coordinate(second)
    if left is None or right is None:
        return None
    from math import asin, cos, radians, sin, sqrt

    lon1, lat1 = left
    lon2, lat2 = right
    radius = 6_371_000.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * radius * asin(sqrt(a))


def _parse_coordinate(value: str | None) -> tuple[float, float] | None:
    if not value:
        return None
    parts = value.strip().split(",")
    if len(parts) != 2:
        return None
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        return None
