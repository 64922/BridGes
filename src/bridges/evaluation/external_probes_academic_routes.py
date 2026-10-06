"""学术全文与校内路线的真实探针。"""

from __future__ import annotations

from typing import Any

from bridges.arxiv_mcp.client import ArxivMcpClient
from bridges.commute.contracts import MODE_ENDPOINTS, CommuteMode
from bridges.commute.sources import AMAP_REST_BASE, AmapRouteClient
from bridges.evaluation.external_probe_contracts import (
    PROBE_ACCOUNT_ID,
    AvailabilityLevel,
    ProbeContext,
    ProbeResult,
    ProbeStatus,
    _missing_key,
    _now,
)
from bridges.evaluation.workflow_scenario_contracts import ExternalGate


def probe_arxiv_full_text(context: ProbeContext) -> ProbeResult:
    """下载一篇 arXiv PDF 并提取正文，验证学术全文技术可达性。"""
    gate = ExternalGate.ARXIV_FULL_TEXT
    client = ArxivMcpClient(http_client=context.http)
    papers = client.search("transformer", max_results=1)
    if not papers:
        return ProbeResult(
            gate=gate,
            status=ProbeStatus.FAILED,
            level=AvailabilityLevel.UNAVAILABLE,
            summary="arXiv 搜索未返回结果。",
            degradation="全文层不可用，保持摘要+书目层。",
            checked_at=_now(),
        )
    paper = papers[0]
    response = context.http.get(paper.pdf_url)
    if response.status_code != 200:
        return ProbeResult(
            gate=gate,
            status=ProbeStatus.FAILED,
            level=AvailabilityLevel.UNAVAILABLE,
            summary=f"PDF 下载失败（HTTP {response.status_code}）。",
            measurements={"arxiv_id": paper.arxiv_id},
            degradation="全文层不可用，保持摘要+书目层。",
            checked_at=_now(),
        )
    import pymupdf

    document = pymupdf.open(stream=response.content, filetype="pdf")  # type: ignore[no-untyped-call]
    try:
        page_count = document.page_count
        sampled = min(3, page_count)
        text = "".join(
            document[index].get_text()  # type: ignore[no-untyped-call]
            for index in range(sampled)
        )
    finally:
        document.close()  # type: ignore[no-untyped-call]
    extracted = len(text.strip())
    opened = extracted >= 800
    return ProbeResult(
        gate=gate,
        status=ProbeStatus.PASSED if opened else ProbeStatus.FAILED,
        level=AvailabilityLevel.FULL if opened else AvailabilityLevel.DEGRADED,
        summary=(
            f"技术实测可下载并提取正文（前 {sampled} 页 {extracted} 字符）；"
            "产品未接线全文读取，界面保持摘要层。"
        ),
        measurements={
            "arxiv_id": paper.arxiv_id,
            "pdf_bytes": len(response.content),
            "pdf_pages": page_count,
            "extracted_chars_first_pages": extracted,
        },
        degradation="产品未宣称全文深读；接线扩展需另行审查。",
        checked_at=_now(),
    )


def probe_amap_campus_routes(context: ProbeContext) -> ProbeResult:
    """实测校内 POI 检索与三种方式的独立路线。"""
    gate = ExternalGate.AMAP_CAMPUS_ROUTES
    if not context.amap_key:
        return _missing_key(gate, "高德 Web 服务 Key")
    client = AmapRouteClient(key_provider=lambda: context.amap_key, client=context.http)
    origin_outcome = client.search_place(PROBE_ACCOUNT_ID, "华东交通大学南区宿舍")
    origins = [poi for poi in origin_outcome.pois if poi.location]
    if not origins:
        origin_outcome = client.search_place(PROBE_ACCOUNT_ID, "华东交通大学学生宿舍")
        origins = [poi for poi in origin_outcome.pois if poi.location]
    destination_outcome = client.search_place(PROBE_ACCOUNT_ID, "华东交通大学图书馆")
    destinations = [poi for poi in destination_outcome.pois if poi.location]
    if not origins or not destinations:
        return ProbeResult(
            gate=gate,
            status=ProbeStatus.FAILED,
            level=AvailabilityLevel.UNAVAILABLE,
            summary="校内 POI 检索未取得可用的起终点坐标。",
            measurements={
                "origin_count": len(origin_outcome.pois),
                "destination_count": len(destination_outcome.pois),
            },
            degradation="无法定位时只追问，不猜坐标、不生成地图。",
            checked_at=_now(),
        )
    origin = origins[0]
    destination = destinations[0]
    modes: dict[str, Any] = {}
    successful = 0
    with_polyline = 0
    for mode in CommuteMode:
        outcome = client.route(
            PROBE_ACCOUNT_ID,
            mode,
            origin=origin.location or "",
            destination=destination.location or "",
            origin_name=origin.name,
            destination_name=destination.name,
        )
        path = outcome.path
        modes[mode.value] = {
            "ok": path is not None,
            "distance_m": path.distance_m if path else None,
            "duration_seconds": path.duration_seconds if path else None,
            "polyline_points": len(path.polyline) if path else 0,
            "error_code": outcome.record.error_code if outcome.record else None,
        }
        if path is None:
            modes[mode.value]["raw_diagnosis"] = _route_raw_diagnosis(
                context,
                mode,
                origin=origin.location or "",
                destination=destination.location or "",
            )
        if path is not None:
            successful += 1
            if path.polyline:
                with_polyline += 1
    level = (
        AvailabilityLevel.FULL
        if successful == len(CommuteMode) and with_polyline == len(CommuteMode)
        else AvailabilityLevel.PARTIAL
        if successful
        else AvailabilityLevel.UNAVAILABLE
    )
    return ProbeResult(
        gate=gate,
        status=ProbeStatus.PASSED if successful else ProbeStatus.FAILED,
        level=level,
        summary=(
            f"校内起终点检索成功；三种方式 {successful}/{len(CommuteMode)} 返回路线，"
            f"{with_polyline} 条含路径点。"
        ),
        measurements={
            "origin": origin.name,
            "destination": destination.name,
            "modes": modes,
        },
        degradation="某方式无路线时按方式如实失败，不拿其他耗时或猜测地图替代。",
        checked_at=_now(),
    )


def _route_raw_diagnosis(
    context: ProbeContext, mode: CommuteMode, *, origin: str, destination: str
) -> dict[str, Any]:
    """失败方式补一次原始响应字段诊断，记录时长字段的真实位置。"""
    response = context.http.get(
        f"{AMAP_REST_BASE}{MODE_ENDPOINTS[mode]}",
        params={
            "key": context.amap_key or "",
            "origin": origin,
            "destination": destination,
            "show_fields": "cost,navi,polyline",
            "output": "JSON",
        },
    )
    if response.status_code != 200:
        return {"http_status": response.status_code}
    body = response.json() if response.text else {}
    route = body.get("route") if isinstance(body, dict) else None
    paths = route.get("paths") if isinstance(route, dict) else None
    if not isinstance(paths, list) or not paths or not isinstance(paths[0], dict):
        return {"paths": 0}
    first = paths[0]
    cost = first.get("cost")
    if isinstance(cost, dict) and cost.get("duration") is not None:
        duration_source = "cost.duration"
    elif first.get("duration") is not None:
        duration_source = "path.duration"
    else:
        duration_source = "missing"
    return {
        "paths": len(paths),
        "path_fields": sorted(first.keys()),
        "duration_source": duration_source,
    }
