"""``route.resolve``：校内别名表 + 真实 POI 解析地点（V2 Issue 12）。

规则（``docs/v2/workflows.md`` 第 3 节）：

- 地点先经**校内别名表**构造检索词，再查**真实 POI**；坐标只能来自高德返回；
- **候选重复时让用户选择**（列出候选，持久化等待），不替用户挑一个；
- **找不到时请用户补建筑名／入口**，模块不造坐标；
- 范围只到华东交通大学校内以及校门：命中校外地名时如实说明范围限制并请用户
  补充校内地点，不规划校外路线。

别名表只提供检索词模板，从不用于断言某个建筑是否存在（存在性由高德 POI 决定）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from threading import Event
from typing import Protocol

from bridges.commute.contracts import (
    MISSING_DESTINATION,
    MISSING_DESTINATION_CHOICE,
    MISSING_ORIGIN,
    MISSING_ORIGIN_CHOICE,
    PLACE_ROLE_LABELS,
    CommuteClarification,
    CommutePlace,
    CommutePlaceCandidate,
    CommutePlaceRole,
)
from bridges.commute.lexicon import (
    CAMPUS_ALIAS_ORDER,
    CAMPUS_KEYWORD,
    CAMPUS_TOKENS,
    PLACE_CITY,
)
from bridges.commute.sources import AmapPoi, PlaceSearchOutcome
from bridges.contracts.modules import ModuleQueryRecord, ModuleQueryStatus

#: 单个地点的检索词上限（有界：取得校内候选即停止）。
MAX_SEARCH_QUERIES = 2

#: 校外候选在澄清里最多列出几个（够用户判断，不堆列表）。
MAX_OUT_OF_SCOPE_NAMES = 3

#: 视为「检索没完成」的记录状态（区别于确实没有结果的 EMPTY）。
_FAILURE_STATUSES: frozenset[ModuleQueryStatus] = frozenset(
    {
        ModuleQueryStatus.ERROR,
        ModuleQueryStatus.TIMEOUT,
        ModuleQueryStatus.RATE_LIMITED,
    }
)


class PlaceResolver(Protocol):
    """POI 检索端口（生产实现为 ``AmapRouteClient``，测试用可控替身）。"""

    def search_place(
        self,
        account_id: str,
        keywords: str,
        *,
        city: str = PLACE_CITY,
        deadline: float | None = None,
        stop_event: Event | None = None,
    ) -> PlaceSearchOutcome: ...


@dataclass(frozen=True)
class PlaceResolution:
    """一侧地点的解析结果：已解析地点、候选、统一记录或单一澄清。"""

    place: CommutePlace | None = None
    candidates: list[CommutePlaceCandidate] = field(default_factory=list)
    records: list[ModuleQueryRecord] = field(default_factory=list)
    clarification: CommuteClarification | None = None
    #: 检索本身失败（未配置凭据／超时／限流等）：调用方据此可见降级，
    #: 绝不把「服务不可用」说成「找不到地点」再让用户补建筑名。
    failure: ModuleQueryRecord | None = None
    #: 外部调用被用户停止：由调用方收敛为「已停止」，不当作失败或空结果。
    cancelled: bool = False
    #: 真正命中的检索词（澄清候选与已解析地点都据此如实标注来源）。
    used_query: str | None = None


def resolve_place(
    role: CommutePlaceRole,
    phrase: str,
    *,
    resolver: PlaceResolver,
    account_id: str,
    deadline: float | None = None,
    stop_event: Event | None = None,
) -> PlaceResolution:
    """把一侧地点短语解析为可核验的 POI；冲突或找不到时返回单一澄清。"""
    records: list[ModuleQueryRecord] = []
    last_pois: list[AmapPoi] = []
    campus: list[AmapPoi] = []
    failure: ModuleQueryRecord | None = None
    used_query: str | None = None
    for query in search_queries(phrase):
        outcome = resolver.search_place(
            account_id, query, deadline=deadline, stop_event=stop_event
        )
        if outcome.record is not None:
            records.append(outcome.record)
            if outcome.record.status is ModuleQueryStatus.CANCELLED:
                return PlaceResolution(records=records, cancelled=True)
            if outcome.record.status in _FAILURE_STATUSES:
                # 检索没完成（服务不可用/超时/限流）：不再发起第二次查询，
                # 也不能把这种情况当成「高德没有这个地点」。
                failure = outcome.record
                break
        last_pois = list(outcome.pois)
        campus = [poi for poi in last_pois if is_campus_poi(poi)]
        if campus:
            used_query = query
            break
    if failure is not None:
        return PlaceResolution(records=records, failure=failure)
    if not campus:
        return PlaceResolution(
            records=records,
            clarification=_no_campus_clarification(role, phrase, last_pois),
        )
    candidates = _dedupe_candidates(campus)
    if len(candidates) > 1:
        return PlaceResolution(
            records=records,
            candidates=candidates,
            clarification=_choice_clarification(role, phrase, candidates),
            used_query=used_query,
        )
    return PlaceResolution(
        place=_place_from_candidate(role, candidates[0], phrase=phrase, query=used_query),
        records=records,
        used_query=used_query,
    )


def search_queries(phrase: str) -> list[str]:
    """构造有界的检索词：别名模板优先，其次原词（都限定在同一城市）。"""
    alias = match_campus_alias(phrase)
    primary = f"{CAMPUS_KEYWORD}{alias if alias is not None else phrase}"
    queries = [primary]
    if primary != phrase:
        queries.append(phrase)
    return queries[:MAX_SEARCH_QUERIES]


def match_campus_alias(phrase: str) -> str | None:
    """命中校内别名表时返回规范化检索词（长别名优先），否则 None。"""
    for alias, item in CAMPUS_ALIAS_ORDER:
        if alias in phrase:
            return item.poi_keyword
    return None


def is_campus_poi(poi: AmapPoi) -> bool:
    """是否确认属于华东交通大学校内或校门：名称或地址命中校名，且有坐标。"""
    if not poi.location:
        return False
    haystack = f"{poi.name}{poi.address or ''}"
    return any(token in haystack for token in CAMPUS_TOKENS)


def _dedupe_candidates(pois: Sequence[AmapPoi]) -> list[CommutePlaceCandidate]:
    """按 (名称, 坐标) 去重：同一 POI 在不同检索词下重复返回时只留一条。"""
    seen: set[tuple[str, str]] = set()
    candidates: list[CommutePlaceCandidate] = []
    for poi in pois:
        key = (poi.name, poi.location or "")
        if key in seen:
            continue
        seen.add(key)
        candidates.append(_candidate_from_poi(poi))
    return candidates


def _candidate_from_poi(poi: AmapPoi) -> CommutePlaceCandidate:
    return CommutePlaceCandidate(
        name=poi.name,
        location=poi.location,
        address=poi.address,
        poi_id=poi.poi_id,
        district=poi.district,
        campus=is_campus_poi(poi),
    )


def _place_from_candidate(
    role: CommutePlaceRole,
    candidate: CommutePlaceCandidate,
    *,
    phrase: str,
    query: str | None,
) -> CommutePlace:
    address = candidate.address or "高德未返回详细地址"
    return CommutePlace(
        role=role,
        original_phrase=phrase,
        query=query or candidate.name,
        name=candidate.name,
        location=candidate.location or "",
        address=candidate.address,
        poi_id=candidate.poi_id,
        district=candidate.district,
        campus_verified=candidate.campus,
        match_basis=(
            f"高德 POI 检索命中「{candidate.name}」（地址：{address}）；"
            f"名称或地址包含「{CAMPUS_KEYWORD}」，确认属于校内范围。"
        ),
        unverified=["未实测该楼门与校内道路的可通行性，路线吸附情况见路线卡局限说明。"],
    )


def _choice_clarification(
    role: CommutePlaceRole, phrase: str, candidates: Sequence[CommutePlaceCandidate]
) -> CommuteClarification:
    label = PLACE_ROLE_LABELS[role]
    listed = "；".join(
        f"{index}. {item.name}"
        + (f"（{item.address}）" if item.address else "")
        for index, item in enumerate(candidates, start=1)
    )
    return CommuteClarification(
        question=(
            f"「{phrase}」在华东交通大学匹配到多个{label}候选：{listed}；"
            "请告诉我用哪一个（回复序号或名称）？"
        ),
        missing=(
            MISSING_ORIGIN_CHOICE
            if role is CommutePlaceRole.ORIGIN
            else MISSING_DESTINATION_CHOICE
        ),
        role=role,
        candidates=list(candidates),
    )


def _no_campus_clarification(
    role: CommutePlaceRole, phrase: str, pois: Sequence[AmapPoi]
) -> CommuteClarification:
    """没有校内 POI：区分「检索无结果」与「只找到校外地名」两种情况。"""
    label = PLACE_ROLE_LABELS[role]
    missing = MISSING_ORIGIN if role is CommutePlaceRole.ORIGIN else MISSING_DESTINATION
    if pois:
        names = "；".join(
            poi.name + (f"（{poi.address}）" if poi.address else "")
            for poi in list(pois)[:MAX_OUT_OF_SCOPE_NAMES]
        )
        return CommuteClarification(
            question=(
                f"「{phrase}」只匹配到校外地名：{names}；"
                "本模块只规划华东交通大学校内以及校门到校内的路线，不规划校外路线；"
                f"请补充校内{label}的楼名或校门（例如「图书馆」「南门」）？"
            ),
            missing=missing,
            role=role,
        )
    return CommuteClarification(
        question=(
            f"我没有在高德找到「{phrase}」对应的华东交通大学校内地点；"
            f"请补充{label}的建筑名或入口名（例如「图书馆」「南门」）？"
            "我不会用猜测的坐标代替真实地点。"
        ),
        missing=missing,
        role=role,
    )
