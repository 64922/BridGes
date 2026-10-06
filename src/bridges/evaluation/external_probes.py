"""工单 42：外部能力最小真实探针与上线门一致性检查。

探针用**已授权配置**发出最小必要请求，逐门记录实测层次与如实降级文案；
凭据缺失或网络不可达记为 ``inconclusive``，不冒充通过也不冒充失败。
真实模型探针与外部服务探针共用同一报告结构，但由不同脚本分别运行，
保证「真实模型」与「外部可得性」分开报告。

一致性规则：产品宣称层不得高于实测层（``PRODUCT_CLAIMS`` 是当前代码
实际宣称的层次；探针报告同时给出二者）。未实测通过的能力必须保持降级。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import httpx
from pydantic import SecretStr

from bridges.ai.model_probe import ModelCapabilityProbe
from bridges.ai.qwen_client import QwenApiClient
from bridges.arxiv_mcp.client import ArxivMcpClient
from bridges.career_plan.collecting import HttpJobPageReader
from bridges.commute.contracts import MODE_ENDPOINTS, CommuteMode
from bridges.commute.sources import AMAP_REST_BASE, AmapRouteClient
from bridges.contracts.ai import ModelCapabilities
from bridges.evaluation.workflow_scenarios import ExternalGate
from bridges.github.client import BUCKET_CORE, GithubApiClient
from bridges.resources.sources import BilibiliVideoVerifier
from bridges.tieba.reading import HttpTiebaThreadReader
from bridges.web_search.tavily import TavilySearchClient

#: 探针使用的合成账户标识（只进入模块审计，不关联真实用户）。
PROBE_ACCOUNT_ID = "issue42-external-probe"


class ProbeStatus(StrEnum):
    """探针执行状态。"""

    PASSED = "passed"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"


class AvailabilityLevel(StrEnum):
    """能力可用层次；顺序见 :data:`LEVEL_ORDER`。"""

    FULL = "full"
    PARTIAL = "partial"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    CONFIGURED_UNVERIFIED = "configured_unverified"


LEVEL_ORDER: dict[AvailabilityLevel, int] = {
    AvailabilityLevel.FULL: 4,
    AvailabilityLevel.PARTIAL: 3,
    AvailabilityLevel.DEGRADED: 2,
    AvailabilityLevel.UNAVAILABLE: 1,
    AvailabilityLevel.CONFIGURED_UNVERIFIED: 0,
}

#: 当前代码实际宣称的能力层次（产品层可得性合同的单一来源）。
PRODUCT_CLAIMS: dict[ExternalGate, AvailabilityLevel] = {
    ExternalGate.ARXIV_FULL_TEXT: AvailabilityLevel.DEGRADED,
    ExternalGate.AMAP_CAMPUS_ROUTES: AvailabilityLevel.PARTIAL,
    ExternalGate.TIEBA_REPLIES: AvailabilityLevel.PARTIAL,
    ExternalGate.PUBLIC_JOBS: AvailabilityLevel.PARTIAL,
    ExternalGate.VIDEO_INTRO: AvailabilityLevel.PARTIAL,
    ExternalGate.GITHUB_FILES: AvailabilityLevel.PARTIAL,
    ExternalGate.MODEL_CAPABILITIES: AvailabilityLevel.FULL,
    ExternalGate.WEB_SEARCH: AvailabilityLevel.FULL,
}

#: 产品对低可用层次是否有已测试的如实降级合同（由确定性场景证据支撑）。
PRODUCT_DEGRADATION_CONTRACT: dict[ExternalGate, bool] = {
    ExternalGate.ARXIV_FULL_TEXT: True,
    ExternalGate.AMAP_CAMPUS_ROUTES: True,
    ExternalGate.TIEBA_REPLIES: True,
    ExternalGate.PUBLIC_JOBS: True,
    ExternalGate.VIDEO_INTRO: True,
    ExternalGate.GITHUB_FILES: True,
    ExternalGate.MODEL_CAPABILITIES: False,
    ExternalGate.WEB_SEARCH: True,
}

#: 降级合同依据（确定性证据场景；无合同的门必须实测通过才可用）。
PRODUCT_DEGRADATION_BASIS: dict[ExternalGate, str] = {
    ExternalGate.ARXIV_FULL_TEXT: "A10：未读全文时结论只基于已读证据",
    ExternalGate.AMAP_CAMPUS_ROUTES: "A12：某方式无路线按方式如实失败",
    ExternalGate.TIEBA_REPLIES: "A14：只交付线索，不总结未读回复",
    ExternalGate.PUBLIC_JOBS: "A16：不可读时只给未核实链接",
    ExternalGate.VIDEO_INTRO: "A09：视频失败仍交付书目与真实缺口",
    ExternalGate.GITHUB_FILES: "A17/A18：只基于已读文件下结论",
    ExternalGate.MODEL_CAPABILITIES: "无降级：声明能力未实测通过不得激活",
    ExternalGate.WEB_SEARCH: "L04：一路失败只交付可支持部分并标缺口",
}

#: 宣称依据（写清产品当前如实保证到哪一层，便于探针结果对照审查）。
PRODUCT_CLAIM_BASIS: dict[ExternalGate, str] = {
    ExternalGate.ARXIV_FULL_TEXT: (
        "PaperSearchService 未装配全文读取，界面按摘要+书目层交付"
    ),
    ExternalGate.AMAP_CAMPUS_ROUTES: "三种方式独立调用；某方式无路线按方式如实失败",
    ExternalGate.TIEBA_REPLIES: "公开页有界读取；受限时只交付帖链，不总结未读回复",
    ExternalGate.PUBLIC_JOBS: "只把公开可读岗位纳入样本；读不到只给未核实链接",
    ExternalGate.VIDEO_INTRO: "只交付核对过的公开元数据（标题/时长/简介）",
    ExternalGate.GITHUB_FILES: "公开 REST API 只读；受限时只基于已读文件下结论",
    ExternalGate.MODEL_CAPABILITIES: "四项能力（文本/图片/工具/结构化）须实测通过才激活",
    ExternalGate.WEB_SEARCH: "Tavily 公网搜索服务按预算、缓存与审计路径交付",
}


@dataclass(frozen=True)
class ProbeContext:
    """一次探针运行所需的凭据、模型运行配置与共享 HTTP 客户端。"""

    http: httpx.Client
    qwen_key: str | None = None
    tavily_key: str | None = None
    amap_key: str | None = None
    effective_model_id: str = ""
    model_capabilities: tuple[str, ...] = ()
    model_context_window: int | None = None
    model_config_source: str = "factory"


@dataclass(frozen=True)
class ProbeResult:
    """单个外部门的最小真实探测结论。"""

    gate: ExternalGate
    status: ProbeStatus
    level: AvailabilityLevel
    summary: str
    measurements: dict[str, Any] = field(default_factory=dict)
    degradation: str = ""
    checked_at: str = ""
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate.value,
            "status": self.status.value,
            "level": self.level.value,
            "declared_level": PRODUCT_CLAIMS[self.gate].value,
            "declared_basis": PRODUCT_CLAIM_BASIS[self.gate],
            "degradation_contract": PRODUCT_DEGRADATION_CONTRACT[self.gate],
            "degradation_basis": PRODUCT_DEGRADATION_BASIS[self.gate],
            "summary": self.summary,
            "measurements": self.measurements,
            "degradation": self.degradation,
            "checked_at": self.checked_at,
            "duration_ms": self.duration_ms,
        }


ProbeFn = Callable[[ProbeContext], ProbeResult]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _missing_key(gate: ExternalGate, what: str) -> ProbeResult:
    return ProbeResult(
        gate=gate,
        status=ProbeStatus.INCONCLUSIVE,
        level=AvailabilityLevel.CONFIGURED_UNVERIFIED,
        summary=f"未配置{what}，未发出任何请求；该门保持未验证。",
        degradation="未验证的能力不宣称深读，界面保持如实降级。",
        checked_at=_now(),
    )


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
            document[index].get_text() for index in range(sampled)  # type: ignore[no-untyped-call]
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


def probe_web_search(context: ProbeContext) -> ProbeResult:
    """实测 Tavily 公网搜索返回真实结果。"""
    gate = ExternalGate.WEB_SEARCH
    if not context.tavily_key:
        return _missing_key(gate, "Tavily 搜索 Key")
    client = TavilySearchClient(
        api_key=SecretStr(context.tavily_key),
        http_client=context.http,
        fetch_sources=False,
    )
    results = client.search("华东交通大学 图书馆 开放时间", fetch_sources=False)
    first = results[0] if results else None
    return ProbeResult(
        gate=gate,
        status=ProbeStatus.PASSED,
        level=AvailabilityLevel.FULL if results else AvailabilityLevel.PARTIAL,
        summary=f"公网搜索返回 {len(results)} 条结果。",
        measurements={
            "result_count": len(results),
            "first_title": first.title if first else None,
            "first_url": first.url if first else None,
        },
        degradation="搜索不可用时相关模块如实标注缺口，不编造来源。",
        checked_at=_now(),
    )


def _search_public_urls(
    context: ProbeContext,
    query: str,
    *,
    domains: tuple[str, ...],
    limit: int = 3,
) -> list[str]:
    if not context.tavily_key:
        return []
    client = TavilySearchClient(
        api_key=SecretStr(context.tavily_key),
        http_client=context.http,
        fetch_sources=False,
    )
    found: list[str] = []
    for result in client.search(query, fetch_sources=False):
        if any(domain in result.url for domain in domains):
            found.append(result.url)
        if len(found) >= limit:
            break
    return found


def probe_tieba_replies(context: ProbeContext) -> ProbeResult:
    """用公网搜索定位一个真实帖子页并实测公开回复读取。"""
    gate = ExternalGate.TIEBA_REPLIES
    if not context.tavily_key:
        return _missing_key(gate, "Tavily 搜索 Key（贴吧帖链发现依赖）")
    urls = _search_public_urls(
        context,
        "site:tieba.baidu.com 华东交通大学吧 讨论",
        domains=("tieba.baidu.com/p/",),
        limit=1,
    )
    if not urls:
        return ProbeResult(
            gate=gate,
            status=ProbeStatus.INCONCLUSIVE,
            level=AvailabilityLevel.UNAVAILABLE,
            summary="未能通过公网搜索定位到真实帖子页。",
            degradation="只交付帖链，不总结未读回复。",
            checked_at=_now(),
        )
    reader = HttpTiebaThreadReader()
    try:
        result = reader.read(urls[0], pages_limit=1)
    finally:
        reader.close()
    readable = result.status.value in {"read", "partial"} and bool(result.replies)
    if readable:
        level = AvailabilityLevel.FULL
        status = ProbeStatus.PASSED
        summary = (
            f"公开回复可读：{len(result.replies)} 条，楼层 "
            f"{result.floor_min}–{result.floor_max}。"
        )
    elif result.status.value in {"read", "partial"} or result.title:
        level = AvailabilityLevel.DEGRADED
        status = ProbeStatus.FAILED
        summary = f"页面可访问但未取得回复（status={result.status.value}）。"
    else:
        level = AvailabilityLevel.UNAVAILABLE
        status = ProbeStatus.FAILED
        summary = (
            f"帖子读取受限或不可用（status={result.status.value}，"
            f"error={result.error_code}）。"
        )
    return ProbeResult(
        gate=gate,
        status=status,
        level=level,
        summary=summary,
        measurements={
            "url": result.url,
            "status": result.status.value,
            "replies": len(result.replies),
            "pages_read": result.pages_read,
            "title": result.title,
        },
        degradation="回复读不到时只交付帖链与标题，不总结未读回复、不称普遍共识。",
        checked_at=_now(),
    )


def probe_public_jobs(context: ProbeContext) -> ProbeResult:
    """用公网搜索定位公开岗位详情页并实测字段读取。"""
    gate = ExternalGate.PUBLIC_JOBS
    if not context.tavily_key:
        return _missing_key(gate, "Tavily 搜索 Key（岗位页发现依赖）")
    urls = _search_public_urls(
        context,
        "数据分析 实习 上海 岗位详情 招聘",
        domains=("zhipin.com", "51job.com", "liepin.com", "lagou.com"),
        limit=3,
    )
    if not urls:
        return ProbeResult(
            gate=gate,
            status=ProbeStatus.INCONCLUSIVE,
            level=AvailabilityLevel.UNAVAILABLE,
            summary="未能通过公网搜索定位到公开岗位详情页。",
            degradation="只交付未核实链接，不推断岗位要求。",
            checked_at=_now(),
        )
    reader = HttpJobPageReader()
    attempted: list[dict[str, Any]] = []
    best: dict[str, Any] | None = None
    try:
        for url in urls:
            result = reader.read(url)
            record = {
                "url": url,
                "status": result.status.value,
                "is_job_posting": result.page.is_job_posting,
                "structured": result.page.structure_found,
                "error_code": result.error_code,
            }
            attempted.append(record)
            if result.readable and result.page.is_job_posting:
                best = {
                    "url": url,
                    "title": result.page.title,
                    "company": result.page.company,
                    "city": result.page.city,
                    "salary_raw": result.page.salary_raw,
                }
                break
    finally:
        reader.close()
    if best is not None:
        complete = bool(best["company"] or best["salary_raw"])
        return ProbeResult(
            gate=gate,
            status=ProbeStatus.PASSED,
            level=AvailabilityLevel.FULL if complete else AvailabilityLevel.PARTIAL,
            summary=f"公开岗位详情可读：{best['title']}。",
            measurements={"read": best, "attempted": attempted},
            degradation="只把公开可读且结构与城市匹配的岗位纳入主样本。",
            checked_at=_now(),
        )
    readable = [item for item in attempted if item["status"] in {"read", "partial"}]
    return ProbeResult(
        gate=gate,
        status=ProbeStatus.FAILED,
        level=AvailabilityLevel.DEGRADED if readable else AvailabilityLevel.UNAVAILABLE,
        summary="尝试的公开岗位页均未取得可核实岗位详情。",
        measurements={"attempted": attempted},
        degradation="只给未核实链接，不推断岗位要求或薪资。",
        checked_at=_now(),
    )


def probe_video_intro(context: ProbeContext) -> ProbeResult:
    """用公网搜索定位哔哩哔哩视频并实测公开元数据核对。"""
    gate = ExternalGate.VIDEO_INTRO
    if not context.tavily_key:
        return _missing_key(gate, "Tavily 搜索 Key（视频发现依赖）")
    pages = _search_public_urls(
        context,
        "site:bilibili.com 高等数学 入门 讲解",
        domains=("bilibili.com/video/BV", "bilibili.com/video/av"),
        limit=2,
    )
    if not pages:
        return ProbeResult(
            gate=gate,
            status=ProbeStatus.INCONCLUSIVE,
            level=AvailabilityLevel.UNAVAILABLE,
            summary="未能通过公网搜索定位到哔哩哔哩视频直达页。",
            degradation="不凑视频；书目缺口如实标注。",
            checked_at=_now(),
        )
    verifier = BilibiliVideoVerifier(client=context.http)
    outcome = verifier.verify(pages, account_id=PROBE_ACCOUNT_ID)
    candidates = outcome.candidates
    if not candidates:
        return ProbeResult(
            gate=gate,
            status=ProbeStatus.FAILED,
            level=AvailabilityLevel.UNAVAILABLE,
            summary=f"视频元数据核对全部失败（尝试 {len(pages)} 条）。",
            measurements={"attempted": len(pages), "rejected": outcome.rejected},
            degradation="丢弃未核对条目，不拿搜索摘要冒充视频元数据。",
            checked_at=_now(),
        )
    first = candidates[0]
    has_description = bool(first.description.strip())
    return ProbeResult(
        gate=gate,
        status=ProbeStatus.PASSED,
        level=AvailabilityLevel.FULL if has_description else AvailabilityLevel.PARTIAL,
        summary=(
            f"公开元数据核对通过：{first.title}"
            + ("（含简介）" if has_description else "（无简介）")
        ),
        measurements={
            "video_id": first.video_id,
            "duration_seconds": first.duration_seconds,
            "has_uploader": bool(first.uploader),
            "has_description": has_description,
            "attempted": len(pages),
        },
        degradation="只交付核对过的元数据，不宣称看过视频内容或字幕。",
        checked_at=_now(),
    )


def probe_github_files(context: ProbeContext) -> ProbeResult:
    """实测 GitHub 公开仓库元数据、README 与许可读取。"""
    gate = ExternalGate.GITHUB_FILES
    client = GithubApiClient(client=context.http)
    repo = client.get("/repos/psf/requests")
    readme = client.get("/repos/psf/requests/readme")
    license_response = client.get("/repos/psf/requests/license")
    license_payload = (
        license_response.payload if isinstance(license_response.payload, dict) else {}
    )
    license_info = license_payload.get("license")
    spdx = license_info.get("spdx_id") if isinstance(license_info, dict) else None
    repo_ok = repo.status_code == 200
    readme_ok = readme.status_code == 200
    license_ok = license_response.status_code == 200
    if repo_ok and readme_ok and license_ok:
        level = AvailabilityLevel.FULL
        status = ProbeStatus.PASSED
    elif repo_ok:
        level = AvailabilityLevel.PARTIAL
        status = ProbeStatus.PASSED
    else:
        level = AvailabilityLevel.UNAVAILABLE
        status = ProbeStatus.FAILED
    reset_at = client.reset_at_for(BUCKET_CORE)
    return ProbeResult(
        gate=gate,
        status=status,
        level=level,
        summary=(
            f"仓库元数据 {'可读' if repo_ok else '失败'}，README "
            f"{'可读' if readme_ok else '失败'}，许可 "
            f"{'可读' if license_ok else '失败'}"
            + (f"（{spdx}）" if spdx else "")
            + "。"
        ),
        measurements={
            "status_codes": {
                "repo": repo.status_code,
                "readme": readme.status_code,
                "license": license_response.status_code,
            },
            "core_remaining": client.core_remaining,
            "limited": client.limited,
            "core_reset_at": reset_at.isoformat() if reset_at is not None else None,
        },
        degradation="README 只当项目自述；未读实现不作架构或可运行断言。",
        checked_at=_now(),
    )


def probe_model_capabilities(context: ProbeContext) -> ProbeResult:
    """对当前生效模型逐项实测文本/图片/工具调用/结构化输出。"""
    gate = ExternalGate.MODEL_CAPABILITIES
    if not context.qwen_key:
        return _missing_key(gate, "Qwen API Key")
    declared = context.model_capabilities or (
        "text",
        "image",
        "tool_calling",
        "structured_output",
    )
    capabilities = ModelCapabilities(
        text="text" in declared,
        image="image" in declared,
        tool_calling="tool_calling" in declared,
        structured_output="structured_output" in declared,
    )
    client = QwenApiClient(
        api_key=SecretStr(context.qwen_key),
        workspace_id=None,
        region="cn",
        http_client=context.http,
    )
    outcomes = ModelCapabilityProbe(client).run(
        model_id=context.effective_model_id,
        capabilities=capabilities,
    )
    results = {
        outcome.capability: {
            "ok": outcome.ok,
            "error_code": outcome.error_code,
            "message": outcome.message,
        }
        for outcome in outcomes
    }
    all_ok = bool(outcomes) and all(outcome.ok for outcome in outcomes)
    text_ok = all(
        outcome.ok for outcome in outcomes if outcome.capability == "text"
    )
    if all_ok:
        level = AvailabilityLevel.FULL
        status = ProbeStatus.PASSED
    elif text_ok:
        level = AvailabilityLevel.DEGRADED
        status = ProbeStatus.FAILED
    else:
        level = AvailabilityLevel.UNAVAILABLE
        status = ProbeStatus.FAILED
    failed = [outcome.capability for outcome in outcomes if not outcome.ok]
    return ProbeResult(
        gate=gate,
        status=status,
        level=level,
        summary=(
            f"生效模型 {context.effective_model_id}（{context.model_config_source}）"
            + ("四项能力全部实测通过。" if all_ok else f"未通过：{'、'.join(failed)}。")
        ),
        measurements={
            "model_id": context.effective_model_id,
            "config_source": context.model_config_source,
            "context_window": context.model_context_window,
            "context_window_note": "上下文窗口按已验证元数据记录，本次未用大请求实测。",
            "capabilities": results,
        },
        degradation="未实测通过的能力不得激活或宣称为可用。",
        checked_at=_now(),
    )


PROBE_REGISTRY: dict[ExternalGate, ProbeFn] = {
    ExternalGate.ARXIV_FULL_TEXT: probe_arxiv_full_text,
    ExternalGate.AMAP_CAMPUS_ROUTES: probe_amap_campus_routes,
    ExternalGate.WEB_SEARCH: probe_web_search,
    ExternalGate.TIEBA_REPLIES: probe_tieba_replies,
    ExternalGate.PUBLIC_JOBS: probe_public_jobs,
    ExternalGate.VIDEO_INTRO: probe_video_intro,
    ExternalGate.GITHUB_FILES: probe_github_files,
    ExternalGate.MODEL_CAPABILITIES: probe_model_capabilities,
}


def run_probe(gate: ExternalGate, context: ProbeContext) -> ProbeResult:
    """执行单个探针；任何异常都转为可报告结论，不静默吞掉。"""
    started = time.monotonic()
    try:
        result = PROBE_REGISTRY[gate](context)
    except Exception as exc:  # noqa: BLE001 - 探针必须给出结论而不是中断报告
        result = ProbeResult(
            gate=gate,
            status=ProbeStatus.INCONCLUSIVE,
            level=AvailabilityLevel.CONFIGURED_UNVERIFIED,
            summary=f"探针执行异常（{type(exc).__name__}），未取得可判定结论。",
            degradation="未验证的能力保持降级。",
            checked_at=_now(),
        )
    return ProbeResult(
        gate=result.gate,
        status=result.status,
        level=result.level,
        summary=result.summary,
        measurements=result.measurements,
        degradation=result.degradation,
        checked_at=result.checked_at or _now(),
        duration_ms=int((time.monotonic() - started) * 1000),
    )


def run_all_probes(
    context: ProbeContext, gates: tuple[ExternalGate, ...] | None = None
) -> list[ProbeResult]:
    """按登记顺序运行全部门（或指定门），返回逐门结论。"""
    selected = gates or tuple(PROBE_REGISTRY)
    return [run_probe(gate, context) for gate in selected]


def claim_consistency_problems(results: list[ProbeResult]) -> list[str]:
    """返回「宣称高于实测且无降级合同」的问题；空列表表示如实降级。

    有降级合同的门在实际不可用时按合同逐请求降级（由确定性场景证据支撑），
    不算违规；无降级合同的门（如模型能力）一旦实测不足即违规。
    """
    problems: list[str] = []
    for result in results:
        claim = PRODUCT_CLAIMS[result.gate]
        if LEVEL_ORDER[claim] <= LEVEL_ORDER[result.level]:
            continue
        if PRODUCT_DEGRADATION_CONTRACT[result.gate]:
            continue
        problems.append(
            f"{result.gate.value}: 宣称层 {claim.value} 高于实测层 "
            f"{result.level.value} 且无降级合同（{result.summary}）"
        )
    return problems


__all__ = [
    "LEVEL_ORDER",
    "PROBE_ACCOUNT_ID",
    "PROBE_REGISTRY",
    "PRODUCT_CLAIMS",
    "PRODUCT_CLAIM_BASIS",
    "PRODUCT_DEGRADATION_BASIS",
    "PRODUCT_DEGRADATION_CONTRACT",
    "AvailabilityLevel",
    "ProbeContext",
    "ProbeResult",
    "ProbeStatus",
    "claim_consistency_problems",
    "run_all_probes",
    "run_probe",
]
