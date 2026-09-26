"""``career.filter``：决定哪些岗位进入主样本。

只把**公开可读、岗位名命中目标岗位（或真正同义名）、城市可核对且匹配**的
岗位纳入主样本；其余候选都留下剔除依据，不静默丢弃：

- 相邻岗位（目标「Java 后端实习」时的前端／测试岗位）单列，绝不混入样本；
- 已过期或已下线的岗位剔除，并写出依据（页面声明或发布日期超阈值）；
- 重复岗位（同一链接，或同公司+岗位+城市）只保留先读到的一条；
- 城市未在页面上给出的岗位不能核对城市，因此不进入主样本，降级为未核实链接。

岗位页读不到（访问受限／超时／结构不符）时不给样本，只说清失败原因。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from bridges.career_plan.collecting import (
    JobPageReadResult,
    ParsedJobPage,
    is_stale,
    skills_of,
)
from bridges.career_plan.contracts import (
    CareerCandidateLink,
    CareerRejectedSample,
    CareerRequestAnalysis,
    JobReadStatus,
    JobSample,
)
from bridges.career_plan.lexicon import (
    SOURCE_LABELS,
    TitleMatch,
    match_job_title,
    normalize_for_match,
)

#: 剔除分类（正文与前端都依赖这些稳定取值）。
KIND_ADJACENT = "adjacent"
KIND_CITY = "city"
KIND_EXPIRED = "expired"
KIND_DUPLICATE = "duplicate"
KIND_NOT_JOB = "not_job"
KIND_TITLE_MISMATCH = "title_mismatch"
KIND_CITY_UNVERIFIED = "city_unverified"

#: 未读到时给用户的说明（每类失败各自的真实原因）。
_READ_FAILURE_NOTES: dict[JobReadStatus, str] = {
    JobReadStatus.ACCESS_RESTRICTED: "岗位页要求登录或触发了访问验证，未取得岗位内容。",
    JobReadStatus.UNRECOGNIZED: "岗位页结构与预期不符，未取得可核实的岗位内容。",
    JobReadStatus.NOT_FOUND: "岗位页不存在或已下线。",
    JobReadStatus.TIMEOUT: "读取超时，未取得岗位内容。",
    JobReadStatus.ERROR: "岗位页读取失败，未取得岗位内容。",
    JobReadStatus.CANCELLED: "已停止读取，未取得岗位内容。",
}

#: 站点名（未核实链接的展示用；未在表内时用来源中文名）。
_SITE_LABELS: tuple[tuple[str, str], ...] = (
    ("zhipin.com", "BOSS 直聘"),
    ("zhaopin.com", "智联招聘"),
    ("51job.com", "前程无忧"),
    ("liepin.com", "猎聘"),
    ("lagou.com", "拉勾"),
    ("yingjiesheng.com", "应届生求职网"),
    ("nowcoder.com", "牛客"),
    ("iguopin.com", "国聘"),
)


@dataclass(frozen=True)
class JobCandidate:
    """一个待过滤的候选（来源链接 + 真实读取结果）。"""

    url: str
    source: str
    label: str
    read: JobPageReadResult


@dataclass(frozen=True)
class FilterOutcome:
    """过滤阶段的真实产出。"""

    samples: list[JobSample] = field(default_factory=list)
    rejected: list[CareerRejectedSample] = field(default_factory=list)
    unconfirmed: list[CareerCandidateLink] = field(default_factory=list)
    adjacent_counts: dict[str, int] = field(default_factory=dict)


def filter_candidates(
    candidates: list[JobCandidate],
    analysis: CareerRequestAnalysis,
    *,
    reference: datetime,
) -> FilterOutcome:
    """按顺序执行：可读性 → 岗位页 → 过期 → 岗位名 → 城市 → 去重。"""
    outcome = FilterOutcome()
    target = tuple(analysis.synonyms or analysis.job_terms)
    adjacent = tuple(analysis.adjacent_jobs)
    city_wanted = analysis.cities[0] if analysis.cities else None
    seen_urls: set[str] = set()
    seen_keys: set[str] = set()

    for candidate in candidates:
        if candidate.url in seen_urls:
            continue
        seen_urls.add(candidate.url)
        read = candidate.read
        if not read.readable:
            _record_read_failure(outcome, candidate)
            continue
        page = read.page
        if not page.is_job_posting:
            outcome.rejected.append(
                _rejected(
                    candidate,
                    kind=KIND_NOT_JOB,
                    title=page.title or "",
                    evidence=(
                        "读到的页面不是岗位详情页（没有岗位名或职位描述），"
                        "不作为岗位样本。"
                    ),
                )
            )
            continue
        if page.expired:
            outcome.rejected.append(
                _rejected(
                    candidate,
                    kind=KIND_EXPIRED,
                    title=page.title or "",
                    evidence=page.expired_evidence or "页面声明该岗位已下线。",
                )
            )
            continue
        published_date = page.published_date
        if published_date is not None and is_stale(published_date, reference=reference):
            outcome.rejected.append(
                _rejected(
                    candidate,
                    kind=KIND_EXPIRED,
                    title=page.title or "",
                    evidence=(
                        f"发布日期 {published_date.isoformat()}（原文："
                        f"{page.published_raw}）距今超过阈值，按过期岗位剔除。"
                    ),
                )
            )
            continue

        title = page.title or ""
        match = match_job_title(title, target_terms=target, adjacent=adjacent)
        if not match.matched:
            if match.adjacent_hits:
                hint = match.adjacent_hits[0]
                outcome.adjacent_counts[hint] = outcome.adjacent_counts.get(hint, 0) + 1
                outcome.rejected.append(
                    _rejected(
                        candidate,
                        kind=KIND_ADJACENT,
                        title=title,
                        evidence=(
                            f"页面岗位名是「{title}」，命中相邻岗位「{hint}」；"
                            "相邻岗位单列建议，不并入目标岗位样本。"
                        ),
                    )
                )
            else:
                outcome.rejected.append(
                    _rejected(
                        candidate,
                        kind=KIND_TITLE_MISMATCH,
                        title=title,
                        evidence=(
                            f"页面岗位名是「{title}」，既不命中目标岗位"
                            f"「{analysis.job_title or ''}」也不命中其同义名。"
                        ),
                    )
                )
            continue

        city_value = (page.city or "").strip() or None
        if city_wanted is not None:
            if city_value is None:
                outcome.rejected.append(
                    _rejected(
                        candidate,
                        kind=KIND_CITY_UNVERIFIED,
                        title=title,
                        evidence=(
                            f"页面没有给出城市，无法核对是否为你要求的「{city_wanted}」，"
                            "因此不纳入主样本。"
                        ),
                    )
                )
                continue
            if normalize_for_match(city_wanted) not in normalize_for_match(city_value):
                outcome.rejected.append(
                    _rejected(
                        candidate,
                        kind=KIND_CITY,
                        title=title,
                        evidence=(
                            f"页面城市是「{city_value}」，与你要求的「{city_wanted}」不符。"
                        ),
                    )
                )
                continue
            city_evidence = f"页面城市「{city_value}」与你要求的「{city_wanted}」一致。"
        else:
            city_evidence = (
                f"本轮未给出城市，未做城市过滤；页面城市为「{city_value}」。"
                if city_value
                else "本轮未给出城市，未做城市过滤；页面也未给出城市。"
            )

        key = _dedupe_key(page.company, title, city_value)
        if key in seen_keys:
            outcome.rejected.append(
                _rejected(
                    candidate,
                    kind=KIND_DUPLICATE,
                    title=title,
                    evidence=(
                        f"与已纳入样本的岗位重复（同公司＋岗位＋城市：{key}），只保留先读到的一条。"
                    ),
                )
            )
            continue
        seen_keys.add(key)
        outcome.samples.append(
            _sample(candidate, page=page, match=match, city_evidence=city_evidence)
        )
    return outcome


def _record_read_failure(outcome: FilterOutcome, candidate: JobCandidate) -> None:
    """读不到页面：可核实为已下线的写剔除，其余降级为未核实链接。"""
    status = candidate.read.status
    note = candidate.read.error_message or _READ_FAILURE_NOTES.get(
        status, "未取得岗位内容。"
    )
    if status == JobReadStatus.NOT_FOUND:
        outcome.rejected.append(
            _rejected(
                candidate, kind=KIND_EXPIRED, title="", evidence=f"{note}按已下线剔除。"
            )
        )
        return
    outcome.unconfirmed.append(
        CareerCandidateLink(
            url=candidate.url,
            title=candidate.label,
            source=candidate.source,
            source_label=SOURCE_LABELS.get(candidate.source, candidate.source),
            note=f"未核实：{note}",
        )
    )


def _sample(
    candidate: JobCandidate,
    *,
    page: ParsedJobPage,
    match: TitleMatch,
    city_evidence: str,
) -> JobSample:
    requirements = list(page.requirements)
    evidence = f"页面岗位名「{page.title or ''}」命中目标说法「{match.matched_terms[0]}」。"
    if match.adjacent_hits:
        evidence += f"同时提到相邻岗位「{match.adjacent_hits[0]}」，已记入相邻岗位建议。"
    return JobSample(
        url=candidate.url,
        source=candidate.source,
        source_label=SOURCE_LABELS.get(candidate.source, candidate.source),
        title=page.title or "",
        company=page.company,
        city=page.city,
        salary_raw=page.salary_raw,
        published_raw=page.published_raw,
        published_date=page.published_date,
        experience=page.experience,
        education=page.education,
        requirements=requirements,
        skills=skills_of(requirements),
        title_evidence=evidence,
        city_evidence=city_evidence,
        retrieved_at=candidate.read.retrieved_at,
        read_status=candidate.read.status,
        error_code=candidate.read.error_code,
        error_message=candidate.read.error_message,
    )


def _rejected(
    candidate: JobCandidate, *, kind: str, title: str, evidence: str
) -> CareerRejectedSample:
    return CareerRejectedSample(
        url=candidate.url,
        title=title or candidate.label,
        company=candidate.read.page.company,
        kind=kind,
        evidence=evidence,
    )


def _dedupe_key(company: str | None, title: str, city: str | None) -> str:
    """去重键：同公司＋岗位＋城市视为同一岗位的重复发布。"""
    return "|".join(
        part or ""
        for part in (
            normalize_for_match(company or ""),
            normalize_for_match(title),
            normalize_for_match(city or ""),
        )
    )


def site_label(url: str) -> str:
    """链接所属站点名（未核实链接的展示用）。"""
    lowered = url.casefold()
    for host, label in _SITE_LABELS:
        if host in lowered:
            return label
    return url
