"""搜索、帖子、岗位、视频与公开仓库的真实探针。"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from pydantic import SecretStr

from bridges.career_plan.collecting import HttpJobPageReader
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
from bridges.github.client import BUCKET_CORE, GithubApiClient
from bridges.resources.sources import BilibiliVideoVerifier
from bridges.tieba.reading import HttpTiebaThreadReader
from bridges.web_search.tavily import TavilySearchClient


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


def _matches_public_source(url: str, source: str) -> bool:
    """只接受指定域及路径，避免把查询串或伪装域名当作来源。"""
    try:
        parsed = urlsplit(url)
        expected = urlsplit("https://" + source)
        host = (parsed.hostname or "").lower()
        expected_host = expected.hostname or ""
        return (
            parsed.scheme in {"http", "https"}
            and parsed.username is None
            and parsed.password is None
            and (host == expected_host or host.endswith("." + expected_host))
            and parsed.path.startswith(expected.path)
        )
    except ValueError:
        return False


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
        if any(_matches_public_source(result.url, domain) for domain in domains):
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
            f"公开回复可读：{len(result.replies)} 条，楼层 {result.floor_min}–{result.floor_max}。"
        )
    elif result.status.value in {"read", "partial"} or result.title:
        level = AvailabilityLevel.DEGRADED
        status = ProbeStatus.FAILED
        summary = f"页面可访问但未取得回复（status={result.status.value}）。"
    else:
        level = AvailabilityLevel.UNAVAILABLE
        status = ProbeStatus.FAILED
        summary = (
            f"帖子读取受限或不可用（status={result.status.value}，error={result.error_code}）。"
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
        "数据分析 实习 上海 site:liepin.com/job/ OR site:zhipin.com/job_detail/",
        domains=("zhipin.com/job_detail/", "liepin.com/job/", "jobs.51job.com/", "lagou.com/jobs/"),
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
    reader = HttpJobPageReader(client=context.http)
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
                "company_verified": bool(result.page.company),
                "duties_verified": bool(result.page.requirements),
                "city_verified": bool(result.page.city and "上海" in result.page.city),
                "expired": result.page.expired,
            }
            attempted.append(record)
            if (
                result.readable
                and result.page.is_job_posting
                and record["company_verified"]
                and record["duties_verified"]
                and record["city_verified"]
                and not result.page.expired
            ):
                best = {
                    "url": url,
                    "title": result.page.title,
                    "company": result.page.company,
                    "city": result.page.city,
                    "salary_raw": result.page.salary_raw,
                    "requirements": list(result.page.requirements),
                }
                break
    finally:
        reader.close()
    if best is not None:
        return ProbeResult(
            gate=gate,
            status=ProbeStatus.PASSED,
            level=AvailabilityLevel.FULL,
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
    license_payload = license_response.payload if isinstance(license_response.payload, dict) else {}
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
            f"{'可读' if license_ok else '失败'}" + (f"（{spdx}）" if spdx else "") + "。"
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
