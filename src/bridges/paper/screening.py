"""``paper.screen``：硬条件过滤 + 需求到标题/摘要证据的语义匹配。

工单 24 的筛选合同：

- 年份等**明确硬条件由代码过滤**，绝不静默放宽；范围里没有结果就
  如实交付空结果与理由。
- 关键词只是检索线索：原词不字面命中的候选，只要标题/摘要证据支持研究
  需求，仍可判为相关（``synonym``）；只命中关键词而没有支持证据的候选
  排除或标注未确认（``keyword_only``）。
- 每条入选项记录「需求 → 证据」对应（标题/摘要片段），供用户核对与后续
  读取深度判断。

默认匹配是确定性的；``judge`` 接缝允许注入专业角色（模型调用）复核语义
相关性，但硬条件过滤与身份去重始终由代码执行。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from bridges.paper.candidate_codec import candidate_payload as candidate_payload
from bridges.paper.contracts import PaperTermAnalysis
from bridges.paper.ranking import matched_keywords, primary_keyword
from bridges.paper.sources import PaperCandidate

#: 匹配强度：直接命中 / 语义（扩展）相关 / 未确认。
STRENGTH_DIRECT = "direct"
STRENGTH_SYNONYM = "synonym"
STRENGTH_WEAK = "weak"

#: 排除原因（用户可见与审计共用稳定码）。
EXCLUDED_YEAR = "year_out_of_range"
EXCLUDED_KEYWORD_ONLY = "keyword_only_without_support"
EXCLUDED_NO_EVIDENCE = "no_supporting_evidence"
EXCLUDED_JUDGE = "judge_not_relevant"
EXCLUDED_SOURCE = "source_not_allowed"
EXCLUDED_SPECIFIED = "specified_paper_mismatch"


class PaperRelevanceJudge(Protocol):
    """专业角色接缝：把需求映射到候选的标题/摘要证据。"""

    def judge(
        self, analysis: PaperTermAnalysis, candidate: PaperCandidate
    ) -> Mapping[str, Any] | None: ...


class BatchPaperRelevanceJudge(Protocol):
    """批量专业角色接缝：一次调用判断全部候选，避免逐候选耗尽运行次数。"""

    def judge_many(
        self, analysis: PaperTermAnalysis, candidates: list[PaperCandidate]
    ) -> Mapping[str, Mapping[str, Any]]: ...


@dataclass(frozen=True)
class ScreenedCandidate:
    """通过硬条件的一条候选及其需求证据。"""

    candidate: PaperCandidate
    matched_requirements: tuple[str, ...]
    evidence: tuple[dict[str, str], ...]
    strength: str
    unconfirmed: tuple[str, ...] = ()
    relevance: str = "direct"
    research_group: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "candidate": candidate_payload(self.candidate),
            "matched_requirements": list(self.matched_requirements),
            "evidence": [dict(item) for item in self.evidence],
            "strength": self.strength,
            "unconfirmed": list(self.unconfirmed),
            "relevance": self.relevance,
            "research_group": self.research_group,
        }


@dataclass(frozen=True)
class ExcludedCandidate:
    """被硬条件或证据不足排除的候选（保留原因，便于如实说明）。"""

    candidate: PaperCandidate
    reason: str


@dataclass(frozen=True)
class ScreenOutcome:
    """一轮筛选结果：入选项、排除项、主题不匹配与硬条件阻塞。"""

    screened: list[ScreenedCandidate] = field(default_factory=list)
    excluded: list[ExcludedCandidate] = field(default_factory=list)
    topic_mismatch: bool = False
    hard_condition_blocked: str | None = None
    notes: list[str] = field(default_factory=list)
    judge_used: bool = False



def screen_candidates(
    analysis: PaperTermAnalysis,
    candidates: list[PaperCandidate],
    *,
    judge: PaperRelevanceJudge | BatchPaperRelevanceJudge | None = None,
) -> ScreenOutcome:
    """按硬条件与证据匹配筛选候选；不修改用户目标与明确条件。

    专业角色支持批量接缝（``judge_many``）：整批候选一次判断，避免逐候选
    模型调用耗尽共享运行次数；模型给出的每条证据都要能在标题/摘要里逐字
    定位，否则丢弃（不得伪造引用）。
    """
    primary = primary_keyword(analysis)
    expansions = [item.strip().lower() for item in analysis.expansions if item.strip()]
    constraints = analysis.constraints

    screened: list[ScreenedCandidate] = []
    excluded: list[ExcludedCandidate] = []
    year_filtered = 0
    context_rejected = 0
    source_rejected = 0
    specified_rejected = 0
    semantic_unverified = 0
    judges_in_batch = judge is not None and hasattr(judge, "judge_many")
    batch: Mapping[str, Mapping[str, Any]] = {}
    if judges_in_batch and candidates:
        try:
            batch = (
                judge.judge_many(  # type: ignore[union-attr]
                    analysis, candidates
                )
                or {}
            )
        except Exception:  # noqa: BLE001 - 专业角色失败回退确定性证据匹配
            batch = {}
    judge_used = judge is not None

    for candidate in candidates:
        candidate_sources = set(candidate.sources or (candidate.source,))
        if constraints.allowed_sources and not candidate_sources.intersection(
            constraints.allowed_sources
        ):
            excluded.append(ExcludedCandidate(candidate, EXCLUDED_SOURCE))
            source_rejected += 1
            continue
        if candidate_sources and candidate_sources.issubset(set(constraints.excluded_sources)):
            excluded.append(ExcludedCandidate(candidate, EXCLUDED_SOURCE))
            source_rejected += 1
            continue
        if constraints.arxiv_id and (
            candidate.arxiv_id is None
            or not _same_arxiv_id(candidate.arxiv_id, constraints.arxiv_id)
        ):
            excluded.append(ExcludedCandidate(candidate, EXCLUDED_SPECIFIED))
            specified_rejected += 1
            continue
        if constraints.paper_title and (
            " ".join(candidate.title.lower().split())
            != " ".join(constraints.paper_title.lower().split())
        ):
            excluded.append(ExcludedCandidate(candidate, EXCLUDED_SPECIFIED))
            specified_rejected += 1
            continue
        if not _within_year_range(analysis, candidate):
            excluded.append(ExcludedCandidate(candidate, EXCLUDED_YEAR))
            year_filtered += 1
            continue
        if constraints.exclude_survey and _is_survey(candidate):
            excluded.append(ExcludedCandidate(candidate, "survey_excluded"))
            context_rejected += 1
            continue
        matched = _evidence_for(analysis, candidate, primary, expansions)
        if constraints.arxiv_id or constraints.paper_title:
            matched = [
                {
                    "requirement": analysis.original_phrase,
                    "source": "title",
                    "quote": candidate.title[:160],
                    "strength": STRENGTH_DIRECT,
                }
            ]
        judge_result: Mapping[str, Any] | None = None
        if judges_in_batch:
            judge_result = batch.get(candidate.candidate_id) or batch.get(candidate.arxiv_id or "")
        elif judge is not None and not hasattr(judge, "judge_many"):
            try:
                judge_result = judge.judge(analysis, candidate)
            except Exception:  # noqa: BLE001 - 角色失败不阻断确定性证据匹配
                judge_result = None
        if analysis.concepts and judge is not None:
            semantic_evidence = _judge_evidence(judge_result or {}, candidate)
            requirements = {item.concept for item in analysis.concepts}
            semantic_evidence = [
                item for item in semantic_evidence if item["requirement"] in requirements
            ]
            if judge_result is None or not isinstance(judge_result.get("relevant"), bool):
                semantic_unverified += 1
                excluded.append(ExcludedCandidate(candidate, "semantic_evaluation_unavailable"))
                continue
            if judge_result.get("relevant") and not semantic_evidence:
                semantic_unverified += 1
                excluded.append(ExcludedCandidate(candidate, "semantic_evidence_unverified"))
                continue
            matched = semantic_evidence
        if judge_result is not None and judge_result.get("relevant") is False:
            excluded.append(ExcludedCandidate(candidate, EXCLUDED_JUDGE))
            context_rejected += 1
            continue
        if not matched and judge_result is not None and judge_result.get("relevant"):
            matched = _judge_evidence(judge_result, candidate)
        if not matched:
            reason = (
                EXCLUDED_KEYWORD_ONLY
                if matched_keywords(candidate, [primary])
                else EXCLUDED_NO_EVIDENCE
            )
            if reason == EXCLUDED_KEYWORD_ONLY:
                context_rejected += 1
            excluded.append(ExcludedCandidate(candidate, reason))
            continue
        strengths = {item["strength"] for item in matched}
        strength = (
            STRENGTH_DIRECT
            if STRENGTH_DIRECT in strengths
            else STRENGTH_SYNONYM
            if STRENGTH_SYNONYM in strengths
            else STRENGTH_WEAK
        )
        unconfirmed = _unconfirmed_for(analysis, candidate, matched, judge_result)
        screened.append(
            ScreenedCandidate(
                candidate=candidate,
                matched_requirements=tuple(dict.fromkeys(item["requirement"] for item in matched)),
                evidence=tuple(
                    {key: item[key] for key in ("requirement", "source", "quote")}
                    for item in matched
                ),
                strength=strength,
                unconfirmed=unconfirmed,
                relevance=(
                    "direct"
                    if _covered_concepts(analysis, matched) >= max(1, len(analysis.concepts))
                    else "exploratory"
                    if any(path.startswith("citation:") for path in candidate.discovery_paths)
                    else "partial"
                ),
                research_group=_research_group(analysis, matched),
            )
        )

    notes: list[str] = []
    if semantic_unverified:
        notes.append(
            f"{semantic_unverified} 篇候选未完成语义与原文证据核验，未作为已核验推荐交付；可重试。"
        )
    if year_filtered:
        notes.append(f"已按年份条件过滤：排除 {year_filtered} 篇范围外的论文，未放宽年份。")
    if source_rejected:
        notes.append(f"来源条件不允许，{source_rejected} 篇候选未采用；没有改用未允许的来源。")
    if context_rejected:
        notes.append(
            f"另有 {context_rejected} 篇只命中原词或关键词、但语境不匹配或没有支持研究需求的"
            "标题/摘要证据，已排除。"
        )
    if not screened:
        blocked: str | None = None
        mention_missing = False
        if constraints.arxiv_id or constraints.paper_title:
            blocked = "specified_paper"
            label = constraints.arxiv_id or constraints.paper_title or ""
            notes.append(f"没有检索到指定论文（{label}）；没有用其他论文替代。")
        elif source_rejected:
            blocked = "source"
        elif year_filtered and not excluded_other(excluded):
            blocked = "year"
        else:
            mention_missing = bool(candidates)
        return ScreenOutcome(
            excluded=excluded,
            topic_mismatch=blocked is None and mention_missing,
            hard_condition_blocked=blocked,
            notes=notes,
            judge_used=judge_used,
        )
    if specified_rejected:
        notes.append(f"已按指定论文条件过滤：排除 {specified_rejected} 篇不匹配的候选。")
    if len(screened) < len(candidates) - year_filtered:
        notes.append(
            f"共 {len(screened)} 篇候选有证据支持本轮需求，"
            f"{len(excluded)} 篇因证据不足或硬条件排除。"
        )
    return ScreenOutcome(
        screened=screened,
        excluded=excluded,
        notes=notes,
        judge_used=judge_used,
    )


def _same_arxiv_id(left: str, right: str) -> bool:
    """arXiv 标识比较忽略版本后缀（v1/v2 是同一篇论文的不同版本）。"""
    return left.split("v")[0].lower() == right.split("v")[0].lower()


def excluded_other(excluded: list[ExcludedCandidate]) -> bool:
    """除年份外是否还有其他排除原因（用于区分空结果与年份阻塞）。"""
    return any(item.reason != EXCLUDED_YEAR for item in excluded)


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------


def _requirements(analysis: PaperTermAnalysis, primary: str, expansions: list[str]) -> list[str]:
    if analysis.concepts:
        return [item.concept for item in analysis.concepts if item.concept]
    items = [primary] if primary else []
    items.extend(expansions)
    return [item for item in dict.fromkeys(items) if item]


def _within_year_range(analysis: PaperTermAnalysis, candidate: PaperCandidate) -> bool:
    year_from = analysis.constraints.year_from
    year_to = analysis.constraints.year_to
    year = candidate.published_year
    if year is None:
        return year_from is None and year_to is None
    if year_from is not None and year < year_from:
        return False
    return not (year_to is not None and year > year_to)


def _evidence_for(
    analysis: PaperTermAnalysis,
    candidate: PaperCandidate,
    primary: str,
    expansions: list[str],
) -> list[dict[str, str]]:
    """把每个需求词对应到标题/摘要里的真实片段。"""
    evidence: list[dict[str, str]] = []
    title = candidate.title
    abstract = candidate.abstract
    if analysis.concepts:
        for concept in analysis.concepts:
            for term in concept.english_search_terms:
                source, quote = _locate(term.lower(), title, abstract)
                if source is None:
                    source, quote = _locate_tokens(term.lower(), title, abstract)
                if source is not None:
                    evidence.append(
                        {
                            "requirement": concept.concept,
                            "source": source,
                            "quote": quote,
                            "strength": STRENGTH_DIRECT,
                        }
                    )
                    break
        return evidence
    if analysis.context_key and expansions and not matched_keywords(candidate, expansions):
        # 消歧后的领域是必要条件；孤立原词不能证明属于已确认领域。
        return []
    for requirement in _requirements(analysis, primary, expansions):
        source, quote = _locate(requirement, title, abstract)
        if source is None:
            continue
        strength = STRENGTH_DIRECT if requirement == primary else STRENGTH_SYNONYM
        evidence.append(
            {
                "requirement": requirement,
                "source": source,
                "quote": quote,
                "strength": strength,
            }
        )
    if evidence:
        return evidence
    # 多词短语按词元覆盖（标题/摘要存在曲折变化）时仍算直接证据。
    if primary:
        source, quote = _locate_tokens(primary, title, abstract)
        if source is not None:
            evidence.append(
                {
                    "requirement": primary,
                    "source": source,
                    "quote": quote,
                    "strength": STRENGTH_DIRECT,
                }
            )
    return evidence


def _locate(requirement: str, title: str, abstract: str) -> tuple[str | None, str]:
    if requirement in title.lower():
        return "title", _snippet(title, requirement)
    if requirement in abstract.lower():
        return "abstract", _snippet(abstract, requirement)
    return None, ""


def _locate_tokens(requirement: str, title: str, abstract: str) -> tuple[str | None, str]:
    words = [word for word in requirement.replace("_", " ").split() if word]
    if len(words) <= 1:
        return None, ""
    if all(word in title.lower() for word in words):
        return "title", _snippet(title, words[0])
    if all(word in abstract.lower() for word in words):
        return "abstract", _snippet(abstract, words[0])
    return None, ""


def _snippet(text: str, needle: str, *, limit: int = 160) -> str:
    lowered = text.lower()
    index = lowered.find(needle.lower())
    if index < 0:
        return text[:limit]
    start = max(0, index - 40)
    return text[start : start + limit]


def _judge_evidence(
    judge_result: Mapping[str, Any], candidate: PaperCandidate
) -> list[dict[str, str]]:
    evidence: list[dict[str, str]] = []
    for item in judge_result.get("evidence") or ():
        if not isinstance(item, Mapping):
            continue
        requirement = str(item.get("requirement") or "").strip()
        source = str(item.get("source") or "abstract").strip()
        quote = str(item.get("quote") or "").strip()[:160]
        source_text = {"title": candidate.title, "abstract": candidate.abstract}.get(source)
        if not requirement or not quote or source_text is None or quote not in source_text:
            continue
        evidence.append(
            {
                "requirement": requirement,
                "source": source,
                "quote": quote,
                "strength": STRENGTH_SYNONYM,
            }
        )
    return evidence


def _unconfirmed_for(
    analysis: PaperTermAnalysis,
    candidate: PaperCandidate,
    evidence: list[dict[str, str]],
    judge_result: Mapping[str, Any] | None,
) -> tuple[str, ...]:
    items: list[str] = []
    if analysis.constraints.prefer_survey and not any(
        marker in f"{candidate.title} {candidate.abstract}".lower()
        for marker in ("survey", "review", "综述")
    ):
        items.append("未确认综述性质")
    if judge_result is not None:
        for value in judge_result.get("unconfirmed") or ():
            text = str(value).strip()
            if text:
                items.append(text)
    return tuple(dict.fromkeys(items))


def _is_survey(candidate: PaperCandidate) -> bool:
    lowered = f"{candidate.title} {candidate.abstract}".lower()
    return any(marker in lowered for marker in ("survey", "review", "overview", "综述", "回顾"))


def _covered_concepts(analysis: PaperTermAnalysis, evidence: list[dict[str, str]]) -> int:
    if not analysis.concepts:
        return len(evidence)
    covered = {item["requirement"] for item in evidence}
    return sum(1 for item in analysis.concepts if item.concept in covered)


def _research_group(analysis: PaperTermAnalysis, evidence: list[dict[str, str]]) -> str:
    covered = {item["requirement"] for item in evidence}
    for concept in analysis.concepts:
        if concept.concept in covered and concept.kind in {"method", "application", "question"}:
            return concept.concept
    return analysis.research_object or analysis.research_question or "相关研究"
