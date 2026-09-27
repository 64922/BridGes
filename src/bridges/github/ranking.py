"""``github.rank``：功能匹配优先，随后才是文档、维护与许可；stars 只是辅助。

排序完全确定：先比命中的要点数，再比证据等级（API 元数据 < README 自述 <
实际读取的实现文件），再比活跃度，之后才用 star 数定次序，仍相同即按
``full_name`` 排定——同一轮输入永远得到同一份推荐顺序。

匹配判定只认**证据文本里真实出现的关键词**，并把命中的词与原文窗口一起留下
（``GithubFeatureMatch.matched_terms`` / ``.evidence``）。命中不了就写「未命中」，
绝不为了凑推荐把不相关的仓库说成覆盖。

覆盖范围由命中的要点数决定：命中比例达到 ``COVERAGE_WHOLE_RATIO`` 按整体项目
呈现，否则按组件项目呈现并逐条列出它只覆盖了哪一部分；用户本轮明确只要组件时
一律按组件呈现。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from bridges.github.contracts import (
    GithubCoverage,
    GithubDeepCheckStatus,
    GithubEvidenceKind,
    GithubFeatureMatch,
    GithubIdeaAnalysis,
    GithubMaintenanceEvidence,
    GithubReadmeStatus,
    GithubRecommendation,
    GithubRejectedRepository,
    GithubRepositoryCandidate,
    GithubRepositoryEvidence,
)

#: 命中的要点占比达到该值即按「整体项目」呈现。
COVERAGE_WHOLE_RATIO = 0.7

#: 默认推荐上限（设计为 2–3 个仓库）。
DEFAULT_RECOMMENDATION_COUNT = 3

#: 活跃维护与仍在维护的时间窗（月）。
ACTIVE_MONTHS = 6
MAINTAINED_MONTHS = 24

#: 判定命中时的最少关键词命中占比（要点切出的关键词多于两条时生效）。
MIN_KEYWORD_RATIO = 0.34

#: 证据窗口的上下文长度（命中词前后各取若干字符）。
QUOTE_PADDING = 40
QUOTE_TAIL = 60

#: 抽取关键词时应剥离的辅助词（只影响匹配判定，不改写展示的原词）。
#: 剥法分两档（见 ``_core``）：多字辅助词整体剥离；单字辅助词**只在词或句法
#: 边界**剥离——它们常是合法术语的一部分（「智能体」的「能」、「记账能力」
#: 的「能」），按子串删除会把原词拆成核不到的碎片。
AUX_WORDS: tuple[str, ...] = (
    "学生",
    "用户",
    "大家",
    "人们",
    "我们",
    "你们",
    "他们",
    "可以",
    "能够",
    "需要",
    "支持",
    "提供",
    "允许",
    "实现",
    "通过",
    "使用",
    "进行",
    "想要",
    "自己",
    "每个",
    "各个",
    "所有",
    "一些",
    "一个",
    "各种",
    "多种",
    "相关",
    "对应",
    "能",
    "会",
    "要",
    "想",
    "来",
    "去",
    "把",
    "被",
    "让",
    "给",
    "的",
    "了",
    "着",
    "过",
)

#: 过于通用的中文双字词（命中它们不算功能匹配证据）。
GENERIC_BIGRAMS: frozenset[str] = frozenset(
    {
        "可以",
        "能够",
        "支持",
        "需要",
        "一个",
        "这个",
        "那个",
        "我们",
        "你们",
        "他们",
        "什么",
        "怎么",
        "以及",
        "或者",
        "并且",
        "同时",
        "而且",
        "但是",
        "因为",
        "所以",
        "如果",
        "为了",
        "不同",
        "各种",
        "多种",
        "相关",
        "对应",
        "使用",
        "进行",
        "通过",
        "提供",
        "包含",
        "具有",
        "实现",
        "功能",
        "系统",
        "平台",
        "项目",
        "应用",
        "网站",
        "用户",
        "学生",
        "界面",
        "页面",
        "数据",
        "信息",
        "内容",
        "操作",
        "快捷",
        "方便",
        "简单",
        "高效",
        "完整",
        "友好",
        "灵活",
        "强大",
        "丰富",
    }
)

#: 过于通用的拉丁词（命中它们不算功能匹配证据）。
GENERIC_LATIN: frozenset[str] = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "your",
        "you",
        "are",
        "has",
        "have",
        "can",
        "will",
        "app",
        "apps",
        "web",
        "site",
        "user",
        "users",
        "data",
        "based",
        "using",
        "build",
        "built",
        "project",
        "projects",
        "simple",
        "demo",
        "test",
        "tests",
        "code",
        "open",
        "source",
        "free",
        "new",
        "all",
        "not",
        "use",
        "used",
        "supports",
        "feature",
        "features",
        "tool",
        "tools",
        "server",
        "client",
        "version",
        "system",
        "platform",
        "management",
        "information",
        "content",
    }
)

_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
_LATIN = re.compile(r"[A-Za-z][A-Za-z0-9+.#_-]{2,}")

#: 多字辅助词：整体出现时剥离（「可以」「支持」不会成为别的词的一部分）。
_MULTI_AUX: tuple[str, ...] = tuple(
    sorted((word for word in AUX_WORDS if len(word) > 1), key=len, reverse=True)
)

#: 单字辅助词：只在词或句法边界（首尾，或被辅助词隔开）剥离。
_EDGE_AUX: frozenset[str] = frozenset(word for word in AUX_WORDS if len(word) == 1)

#: 边界剥离后至少要留下的字符数（剥完更短就说明剥过头了，保留原样）。
_MIN_CORE_CHARS = 2

#: 短要点（只剩一两个双字词）命中时必须凑够的连续字数：「智能」单独出现
#: 不等于「智能体」，至少要连成这么长的原词片段才算。
_MIN_SHORT_CORE_SPAN = 3

_KIND_LABELS: dict[GithubEvidenceKind, str] = {
    GithubEvidenceKind.IMPLEMENTATION: "实际读取的实现文件",
    GithubEvidenceKind.README: "README 自述",
    GithubEvidenceKind.METADATA: "API 元数据",
}


#: 未完成检查的候选的剔除理由：说「未完成检查」，不能说成「不匹配」。
UNINSPECTED_REASON_LIMITED = (
    "上游额度已用尽，本轮未完成检查（这不代表它不匹配）；稍后重试可补齐证据。"
)
UNINSPECTED_REASON_BOUNDED = "本轮检查数量有上限，未完成检查（这不代表它不匹配）。"


@dataclass(frozen=True)
class RankOutcome:
    """排序结果：纳入推荐的仓库与被剔除的候选（各自带中文理由）。"""

    recommendations: list[GithubRecommendation] = field(default_factory=list)
    rejected: list[GithubRejectedRepository] = field(default_factory=list)


def rank_candidates(
    analysis: GithubIdeaAnalysis,
    evidences: list[GithubRepositoryEvidence],
    *,
    uninspected: list[GithubRepositoryCandidate] | None = None,
    now: datetime | None = None,
    limit: int = DEFAULT_RECOMMENDATION_COUNT,
    rate_limited: bool = False,
) -> RankOutcome:
    """把真实证据排成推荐列表；没有要点证据的候选如实剔除。"""
    current = now or datetime.now(UTC)
    cap = max(1, limit)
    scored: list[tuple[tuple[int, int, int, int, str], GithubRecommendation]] = []
    rejected: list[GithubRejectedRepository] = []
    for evidence in evidences:
        if analysis.whole_idea and not _identity_related(analysis, evidence):
            rejected.append(
                GithubRejectedRepository(
                    full_name=evidence.full_name,
                    url=evidence.html_url,
                    reason=(
                        "名称、简介与话题里都没有出现你整体想法的关键词（上游对中文是"
                        "模糊匹配，正文偶然命中不算），不按整体项目推荐。"
                    ),
                )
            )
            continue
        matches = match_features(analysis, evidence)
        matched_count = sum(1 for item in matches if item.matched)
        if matched_count == 0:
            rejected.append(
                GithubRejectedRepository(
                    full_name=evidence.full_name,
                    url=evidence.html_url,
                    reason=_no_evidence_reason(evidence),
                )
            )
            continue
        coverage, covers_parts, coverage_note = _coverage(analysis, matches, matched_count)
        maintenance, activity_score = _maintenance(evidence, now=current)
        record = GithubRecommendation(
            rank=0,
            full_name=evidence.full_name,
            html_url=evidence.html_url,
            coverage=coverage,
            covers_parts=covers_parts,
            coverage_note=coverage_note,
            description=evidence.description,
            topics=list(evidence.topics),
            language=evidence.language,
            feature_matches=matches,
            matched_feature_count=matched_count,
            evidence_kinds=_evidence_kinds(evidence),
            readme_status=evidence.readme_status,
            readme_url=evidence.readme_url,
            readme_excerpt=_readme_excerpt(evidence, matches),
            files_read=list(evidence.files_read),
            implementation_checks=list(evidence.implementation_checks),
            maintenance=maintenance,
            license=evidence.license,
            reason_zh=_reason(matches, matched_count, evidence, coverage),
            borrow_note=_borrow_note(evidence, coverage),
            strengths=_strengths(evidence, matches, matched_count),
            limitations=_limitations(evidence, coverage, matched_count),
            retrieved_at=evidence.retrieved_at,
        )
        scored.append(
            (
                (
                    -matched_count,
                    -_evidence_score(evidence),
                    -activity_score,
                    -evidence.stars,
                    evidence.full_name,
                ),
                record,
            )
        )
    scored.sort(key=lambda item: item[0])
    recommendations = [
        record.model_copy(update={"rank": index})
        for index, (_, record) in enumerate(scored[:cap], start=1)
    ]
    rejected.extend(
        GithubRejectedRepository(
            full_name=record.full_name,
            url=record.html_url,
            reason=f"排在本轮推荐上限（{cap} 个）之外，未列在推荐里。",
        )
        for _, record in scored[cap:]
    )
    rejected.extend(
        GithubRejectedRepository(
            full_name=candidate.full_name,
            url=candidate.html_url,
            reason=(
                UNINSPECTED_REASON_LIMITED if rate_limited else UNINSPECTED_REASON_BOUNDED
            ),
        )
        for candidate in uninspected or []
    )
    return RankOutcome(recommendations=recommendations, rejected=rejected)


def identity_relevance(
    analysis: GithubIdeaAnalysis,
    *,
    full_name: str,
    description: str | None,
    topics: list[str],
) -> int:
    """名称、简介与话题里与**核心场景**重合的关键词个数（0 表示毫无词汇重合）。

    上游对中文检索是**按字模糊匹配**的，长短语会把简介里偶然含相同字的无关仓库
    一起召回（实测过政治宣传库、大作业合集混进结果）。这条只数「身份信息」里的
    词汇重合，用来给候选排序（先读有重合的）并挡住整体项目里的误召回——它不进
    正文的功能匹配结论，功能匹配仍由 ``match_features`` 按要点逐条判定。

    只拿场景当参照，不掺要点：要点的辅助词会与无关正文撞车——实测中「搜索想要的书」
    里的「想要」让「更快找到你想要的答案」这类新标签页插件在身份上也算命中。
    """
    text = " ".join(
        part for part in [full_name, description or "", " ".join(topics)] if part
    )
    lowered = text.lower()
    return sum(
        1 for term in _identity_terms(analysis.scenario) if term in lowered or term in text
    )


def _identity_related(
    analysis: GithubIdeaAnalysis, evidence: GithubRepositoryEvidence
) -> bool:
    return (
        identity_relevance(
            analysis,
            full_name=evidence.full_name,
            description=evidence.description,
            topics=evidence.topics,
        )
        > 0
    )


def _identity_terms(text: str, *, limit: int = 40) -> list[str]:
    """从原文里切出可判重的关键词（有界）：拉丁词 + 中文三字／双字片段。"""
    terms: list[str] = []
    for token in {match.group(0).lower() for match in _LATIN.finditer(text)}:
        if token not in GENERIC_LATIN and token not in terms:
            terms.append(token)
    for run in _CJK_RUN.findall(text):
        for size in (3, 2):
            for index in range(len(run) - size + 1):
                gram = run[index : index + size]
                if size == 2 and gram in GENERIC_BIGRAMS:
                    continue
                if gram not in terms:
                    terms.append(gram)
    return terms[:limit]


def match_features(
    analysis: GithubIdeaAnalysis, evidence: GithubRepositoryEvidence
) -> list[GithubFeatureMatch]:
    """逐条要点做证据匹配：实现文件 > README 自述 > API 元数据。"""
    levels: list[tuple[GithubEvidenceKind, str]] = [
        (GithubEvidenceKind.IMPLEMENTATION, _implementation_text(evidence)),
        (GithubEvidenceKind.README, evidence.readme_text or ""),
        (
            GithubEvidenceKind.METADATA,
            " ".join(
                part
                for part in [
                    evidence.description or "",
                    " ".join(evidence.topics),
                    evidence.language or "",
                ]
                if part
            ),
        ),
    ]
    results: list[GithubFeatureMatch] = []
    for feature in analysis.features:
        core = _core(feature)
        match: GithubFeatureMatch | None = None
        for kind, text in levels:
            if not text.strip():
                continue
            hits = _keyword_hits(core, text, phrase=feature)
            if not hits:
                continue
            match = GithubFeatureMatch(
                feature=feature,
                matched=True,
                evidence_kind=kind,
                matched_terms=hits,
                evidence=(
                    f"{_KIND_LABELS[kind]}命中关键词「{'、'.join(hits)}」："
                    f"{_quote(text, hits[0])}"
                ),
            )
            break
        results.append(
            match
            if match is not None
            else GithubFeatureMatch(
                feature=feature,
                matched=False,
                evidence_kind=None,
                matched_terms=[],
                evidence=(
                    "本轮取得的证据（API 元数据、README 自述、已读文件）里"
                    "没有出现该要点的关键词。"
                ),
            )
        )
    return results


def _core(feature: str) -> str:
    """剥掉辅助词后的要点骨架（只用于匹配判定，展示仍用原词）。

    多字辅助词整体剥离；单字辅助词只在**词或句法边界**剥离，术语内部的单字
    一律保留——「智能体」里的「能」是术语的一部分，按子串删掉会让这个词永远
    匹配不上（``_keyword_hits`` 仍会先拿完整原词去比对）。剥完为空时保留原词。
    """
    stripped = feature
    for word in _MULTI_AUX:
        stripped = stripped.replace(word, " ")
    tokens = [_strip_edge_aux(token) for token in stripped.split()]
    collapsed = " ".join(token for token in tokens if token)
    return collapsed or feature


def _strip_edge_aux(token: str) -> str:
    """只剥 token 首尾的单字辅助词（逐字剥、次数有界）。"""
    value = token.strip()
    for _ in range(len(value)):
        before = value
        if len(value) > _MIN_CORE_CHARS and value[0] in _EDGE_AUX:
            value = value[1:]
        if len(value) > _MIN_CORE_CHARS and value[-1] in _EDGE_AUX:
            value = value[:-1]
        if value == before:
            break
    return value


def _keyword_hits(core: str, text: str, *, phrase: str | None = None) -> list[str]:
    """返回真实出现在证据文本里的关键词（空列表表示未命中）。

    先拿用户原词整体比对：完整短语在证据里连续出现即命中，辅助词一律不动它
    （「智能体」这种含单字的术语因此不会被剥碎）；整词没出现时才退回剥掉边界
    辅助词后的骨架做逐字命中。

    整词命中的 ``matched_terms`` 就是该要点本身（不是被切碎的字片段）：原词在
    证据里连续出现才算命中，展示时保留用户原话。只由通用词拼成的要点
    （「用户功能」这类）一律不算功能证据，与逐字命中路径同一条底线。
    """
    if _generic_phrase(core):
        return []
    if phrase:
        whole = _whole_phrase_hit(phrase, text)
        if whole is not None:
            return [whole]
    lowered = text.lower()
    latin = sorted(
        token
        for token in {match.group(0).lower() for match in _LATIN.finditer(core)}
        if token not in GENERIC_LATIN and token in lowered
    )
    if latin:
        return latin[:4]
    bigrams: list[str] = []
    trigrams: list[str] = []
    for run in _CJK_RUN.findall(core):
        for size, bucket in ((2, bigrams), (3, trigrams)):
            for index in range(len(run) - size + 1):
                gram = run[index : index + size]
                if size == 2 and gram in GENERIC_BIGRAMS:
                    continue
                if gram not in bucket:
                    bucket.append(gram)
    trigram_hits = [gram for gram in trigrams if gram in text]
    if trigram_hits:
        return _merge_hits(trigram_hits, text)[:4]
    bigram_hits = [gram for gram in bigrams if gram in text]
    if not bigram_hits:
        return []
    if len(bigrams) <= 2:
        # 短要点只能靠整词或相邻片段命中：「智能」单独出现不等于「智能体」。
        merged = _merge_hits(bigram_hits, text)
        if (
            _cjk_length(core) >= _MIN_SHORT_CORE_SPAN
            and max((len(span) for span in merged), default=0) < _MIN_SHORT_CORE_SPAN
        ):
            return []
        return merged[:4]
    if len(bigram_hits) / len(bigrams) >= MIN_KEYWORD_RATIO:
        return _merge_hits(bigram_hits, text)[:4]
    return []


def _whole_phrase_hit(phrase: str, text: str) -> str | None:
    """原词在证据里连续出现时返回它本身；通用词与过短片段不算命中。"""
    value = " ".join(phrase.split())
    if len(value) < _MIN_CORE_CHARS:
        return None
    if not _CJK_RUN.search(value):
        lowered = value.lower()
        if lowered in GENERIC_LATIN or len(value) < 3:
            return None
        return value if lowered in text.lower() else None
    if _generic_phrase(value):
        return None
    return value if value in text else None


def _generic_phrase(core: str) -> bool:
    """整条要点是否只由通用词拼成（「用户功能」不是功能证据）。

    把要点按通用双字词从左到右尽量盖住：盖得满说明它本身没有实质内容词，
    整词命中它不该算作覆盖了功能。含通用词的**真术语**不受影响——「智能体」
    里「智能」盖不住「体」，「搜索结果」里「搜索」不是通用词，都保留。
    """
    if core in GENERIC_BIGRAMS:
        return True
    runs = _CJK_RUN.findall(core)
    if not runs:
        return False
    for run in runs:
        index = 0
        while index < len(run):
            if run[index : index + 2] not in GENERIC_BIGRAMS:
                return False
            index += 2
    return True


def _cjk_length(text: str) -> int:
    return sum(len(run) for run in _CJK_RUN.findall(text))


def _merge_hits(hits: list[str], text: str) -> list[str]:
    """把在原文里重叠的片段并成完整词：命中「联邦学」「邦学习」时展示「联邦学习」。"""
    spans: list[tuple[int, int]] = []
    for gram in hits:
        index = text.find(gram)
        if index >= 0:
            spans.append((index, index + len(gram)))
    spans.sort()
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            previous_start, previous_end = merged[-1]
            merged[-1] = (previous_start, max(previous_end, end))
            continue
        merged.append((start, end))
    return [text[start:end] for start, end in merged]


def _no_evidence_reason(evidence: GithubRepositoryEvidence) -> str:
    """没有要点证据时的剔除理由：分清「读了但没命中」和「根本读不到文件」。"""
    if (
        evidence.readme_status is not GithubReadmeStatus.READ
        and not evidence.files_read
        and not any(check.status == "confirmed" for check in evidence.implementation_checks)
    ):
        return (
            "该仓库没有读到 README，也没有读到实现文件（可能是空仓库或只有代码），"
            "没有可核对的要点证据，因此不纳入推荐。"
        )
    return (
        "本轮取得的证据（API 元数据、README 自述、已读文件）里没有出现你 idea 的要点。"
    )


def _implementation_text(evidence: GithubRepositoryEvidence) -> str:
    """实际读取到的实现证据文本（只含确认存在的路径与真实片段）。"""
    parts: list[str] = []
    for check in evidence.implementation_checks:
        if check.status == "confirmed":
            parts.append(check.path)
    for item in evidence.files_read:
        parts.append(item.path)
        parts.extend(item.entries)
        if item.excerpt:
            parts.append(item.excerpt)
    return " ".join(parts)


def _quote(text: str, keyword: str) -> str:
    index = text.find(keyword)
    if index < 0:
        return "…"
    start = max(0, index - QUOTE_PADDING)
    end = min(len(text), index + len(keyword) + QUOTE_TAIL)
    window = " ".join(text[start:end].split())
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{window}{suffix}"


def _evidence_kinds(evidence: GithubRepositoryEvidence) -> list[GithubEvidenceKind]:
    kinds = [GithubEvidenceKind.METADATA]
    if evidence.readme_status is GithubReadmeStatus.READ and evidence.readme_text:
        kinds.append(GithubEvidenceKind.README)
    if evidence.files_read or any(
        check.status == "confirmed" for check in evidence.implementation_checks
    ):
        kinds.append(GithubEvidenceKind.IMPLEMENTATION)
    return kinds


def _evidence_score(evidence: GithubRepositoryEvidence) -> int:
    kinds = _evidence_kinds(evidence)
    if GithubEvidenceKind.IMPLEMENTATION in kinds:
        return 3
    return 2 if GithubEvidenceKind.README in kinds else 1


def _activity_score(evidence: GithubRepositoryEvidence, *, now: datetime) -> int:
    if evidence.archived:
        return -1
    pushed = evidence.pushed_at
    if pushed is None:
        return 0
    months = max(0.0, (now - pushed).days / 30.44)
    if months <= ACTIVE_MONTHS:
        return 2
    if months <= MAINTAINED_MONTHS:
        return 1
    return 0


def _coverage(
    analysis: GithubIdeaAnalysis, matches: list[GithubFeatureMatch], matched_count: int
) -> tuple[GithubCoverage, list[str], str]:
    matched = [item.feature for item in matches if item.matched]
    total = len(matches) or 1
    if not analysis.whole_idea:
        return (
            GithubCoverage.COMPONENT,
            matched,
            "你本轮要的是单个组件的公开实现，该仓库只覆盖这一部分，不代表完整产品。",
        )
    if matched_count >= total:
        return (
            GithubCoverage.WHOLE,
            [],
            f"覆盖了你列出的全部 {total} 项要点，按整体项目呈现。",
        )
    if matched_count / total >= COVERAGE_WHOLE_RATIO:
        return (
            GithubCoverage.WHOLE,
            [],
            f"覆盖了你列出的 {total} 项要点中的 {matched_count} 项，按整体项目呈现。",
        )
    return (
        GithubCoverage.COMPONENT,
        matched,
        (
            f"只覆盖你列出的 {total} 项要点中的 {matched_count} 项"
            f"（{'、'.join(matched) or '无'}）；这是组件项目，不是完整实现。"
        ),
    )


def _maintenance(
    evidence: GithubRepositoryEvidence, *, now: datetime
) -> tuple[GithubMaintenanceEvidence, int]:
    score = _activity_score(evidence, now=now)
    notes: list[str] = []
    if evidence.archived:
        notes.append("上游标注该仓库已归档（只读），不会再接受更新。")
    if evidence.is_fork:
        notes.append("该仓库是复刻（fork），实际维护可能在上游。")
    pushed = evidence.pushed_at
    if pushed is None:
        notes.append("上游没有给出最近推送时间。")
    else:
        months = max(0.0, (now - pushed).days / 30.44)
        notes.append(f"最近推送在 {months:.1f} 个月前（{pushed.date().isoformat()}）。")
    if evidence.runnable_hints:
        notes.append(f"根目录实际读到可运行线索：{'、'.join(evidence.runnable_hints)}。")
    else:
        notes.append(
            "根目录没有读到常见清单文件（package.json、pyproject.toml 等），运行方式未核实。"
        )
    notes.append(
        f"star {evidence.stars}、fork {evidence.forks}、未关闭 issue "
        f"{evidence.open_issues}；star 只作辅助。"
    )
    return (
        GithubMaintenanceEvidence(
            pushed_at=pushed,
            created_at=evidence.created_at,
            stars=evidence.stars,
            forks=evidence.forks,
            open_issues=evidence.open_issues,
            archived=evidence.archived,
            is_fork=evidence.is_fork,
            runnable_hints=list(evidence.runnable_hints),
            note="".join(notes),
        ),
        score,
    )


def _readme_excerpt(
    evidence: GithubRepositoryEvidence, matches: list[GithubFeatureMatch]
) -> str | None:
    """命中原词附近的 README 片段（项目自述，已截断）。"""
    text = evidence.readme_text
    if not text:
        return None
    for item in matches:
        if not item.matched or item.evidence_kind is not GithubEvidenceKind.README:
            continue
        for keyword in item.matched_terms:
            if keyword and keyword in text:
                return _quote(text, keyword)
    return " ".join(text[:400].split())


def _reason(
    matches: list[GithubFeatureMatch],
    matched_count: int,
    evidence: GithubRepositoryEvidence,
    coverage: GithubCoverage,
) -> str:
    total = len(matches) or 1
    kinds = _evidence_kinds(evidence)
    scope = "整体项目" if coverage is GithubCoverage.WHOLE else "组件项目"
    return (
        f"按功能匹配排在前面：{total} 项要点中命中 {matched_count} 项，"
        f"按{scope}呈现；本轮证据等级到「{_KIND_LABELS[kinds[-1]]}」。"
    )


def _borrow_note(evidence: GithubRepositoryEvidence, coverage: GithubCoverage) -> str:
    """可借鉴角度：只依据本轮真实取得的证据，未读实现文件时明说。"""
    confirmed = [
        check.path for check in evidence.implementation_checks if check.status == "confirmed"
    ]
    if confirmed:
        return (
            f"可借鉴：README 点名的「{'、'.join(confirmed[:3])}」在仓库里实际存在"
            "（已读取目录或文件片段），可先看这一层怎么划分职责。"
        )
    dirs = [item.path for item in evidence.files_read if item.kind == "dir"]
    if dirs:
        return (
            f"可借鉴：已读取的目录「{'、'.join(dirs[:3])}」，可以从目录划分看它的分层方式"
            "（这是实际读到的结构，不是 README 自述）。"
        )
    if evidence.readme_text:
        scope = "整体功能划分" if coverage is GithubCoverage.WHOLE else "这一部分功能的做法"
        return (
            f"可借鉴：README 自述里的{scope}（项目自述，本轮未读取实现文件，"
            "不对内部架构作断言）。"
        )
    return "可借鉴角度有限：本轮只取得 API 元数据，没有读到 README 与实现文件。"


def _strengths(
    evidence: GithubRepositoryEvidence, matches: list[GithubFeatureMatch], matched_count: int
) -> list[str]:
    items: list[str] = []
    if matched_count:
        hit = "、".join(item.feature for item in matches if item.matched)
        items.append(f"证据里命中的要点：{hit}。")
    if evidence.license.detected:
        label = evidence.license.spdx_id or evidence.license.name or "已标注"
        source = "已读取许可文件" if evidence.license.file_read else "元数据标注"
        items.append(f"许可：{label}（{source}）。")
    if evidence.runnable_hints:
        items.append(f"根目录有可运行线索：{'、'.join(evidence.runnable_hints)}。")
    if not evidence.archived and evidence.pushed_at is not None:
        items.append("仓库未归档，最近仍有推送。")
    return items


def _limitations(
    evidence: GithubRepositoryEvidence, coverage: GithubCoverage, matched_count: int
) -> list[str]:
    items: list[str] = []
    if not evidence.readme_text:
        items.append("没有取得 README，本轮无法引用项目自述。")
    if not evidence.files_read and not any(
        check.status == "confirmed" for check in evidence.implementation_checks
    ):
        items.append("未读取实现文件，不对内部架构与代码质量作断言。")
    if evidence.deep_checks is GithubDeepCheckStatus.RATE_LIMITED:
        items.append("上游额度限制导致该仓库的许可与实现文件核查本轮未完成。")
    elif evidence.deep_checks is GithubDeepCheckStatus.NOT_OBTAINED:
        items.append(
            "根目录清单本轮没有取得，许可与实现文件核查未完成"
            "（这不代表仓库里没有这些文件）。"
        )
    elif evidence.deep_checks is GithubDeepCheckStatus.INTERRUPTED:
        items.append("本轮检查时间用尽，许可与实现文件核查未完成（这不代表它不匹配）。")
    if not evidence.license.detected:
        items.append("未见许可证，不声称代码可自由复用。")
    elif not evidence.license.file_read:
        items.append("未读取许可文件正文，许可条款以仓库页面为准。")
    if evidence.archived:
        items.append("仓库已归档，功能不会再更新。")
    if evidence.is_fork:
        items.append("这是复刻仓库，优先看它的上游源仓库。")
    if matched_count == 0:
        items.append("证据里没有命中要点。")
    if coverage is GithubCoverage.COMPONENT:
        items.append("覆盖面有限：只覆盖上面列出的部分要点，不能当作完整实现。")
    unread = [check for check in evidence.implementation_checks if check.status == "unread"]
    if unread:
        items.append(f"另有 {len(unread)} 个 README 点名路径本轮没有读到。")
    return items
