"""学习模式公开来源的确定性目标覆盖裁决。"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

from bridges.web_search.contracts import WebSearchResult, WebSearchVerification


# 保留旧版本常量作为受控回滚点；新运行默认使用 v2。
EVIDENCE_COVERAGE_RULES_V1 = "learning-evidence-coverage-v1"
EVIDENCE_COVERAGE_RULES_VERSION = "learning-evidence-coverage-v2"
TOPIC_ALIASES_VERSION = "learning-evidence-topic-aliases-v1"
_GOAL_MAX_CHARS = 240
_SOURCE_METADATA_MAX_CHARS = 600
_SOURCE_CONTENT_MAX_CHARS = 2400

_TRANSFORMER = re.compile(
    r"(?<![A-Za-z0-9_])transformer(?![A-Za-z0-9_])", re.IGNORECASE
)
_AI_CONTEXT = re.compile(
    r"人工智能|机器学习|深度学习|神经网络|模型|架构|architecture|"
    r"自注意力|self[- ]attention|attention|encoder|decoder|编码器|解码器",
    re.IGNORECASE,
)
_ELECTRICAL_CONTEXT = re.compile(
    r"电力|变电|配电|电压|电磁感应|交流|输电|绕组|power\s+transformer|"
    r"voltage|substation|winding",
    re.IGNORECASE,
)
_ARCHITECTURE = re.compile(
    r"架构|结构|architecture|encoder|decoder|编码器|解码器|层次化|模型结构",
    re.IGNORECASE,
)
_SELF_ATTENTION = re.compile(
    r"自注意力|自注意机制|self[- ]attention|multi[- ]head|多头注意力|"
    r"attention\s+mechanism|注意力机制|attention",
    re.IGNORECASE,
)
_TOPIC_PREFIX_NOISE = re.compile(
    r"^(?:(?:请|帮我)\s*)?(?:我想(?:要)?|想(?:要)?|要)?\s*"
    r"(?:学习|学一下|教我|了解|认识|入门|弄懂|搞清楚)\s*",
    re.IGNORECASE,
)
_TOPIC_OBJECTIVE_NOISE = re.compile(r"(?:并理解|理解)(?:其)?", re.IGNORECASE)
_TOPIC_TEMPLATE_SUFFIX = re.compile(
    r"(?:的)?(?:相关)?(?:基础知识|基础|简介|入门)$|"
    r"(?:的)?相关(?:知识)?$|知识$",
    re.IGNORECASE,
)
_TOPIC_NOISE_V1 = re.compile(
    r"学习|学一下|教我|了解|认识|入门|弄懂|搞清楚|请|帮我|我想(?:要)?|"
    r"想(?:要)?|要|的相关知识|相关知识|基础知识|核心机制|并理解|理解|知识|相关",
    re.IGNORECASE,
)
_TOPIC_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,8}")
_TOPIC_PARTICLE_SUFFIX = re.compile(r"(?<=[\u4e00-\u9fff])[的地得]+(?=$|[^\w\u4e00-\u9fff])")
_ASCII_ALIAS = re.compile(r"^[a-z0-9][a-z0-9 -]*$")
_GENERIC_STOPWORDS = frozenset(
    {"当前", "本轮", "目标", "主题", "内容", "概念", "一个", "一些", "什么"}
)

# 只维护小型、显式的术语别名；扩大表必须随规则版本一起评审。
TOPIC_ALIASES: dict[str, frozenset[str]] = {
    "卷积神经网络": frozenset(
        {"卷积神经网络", "CNN", "Convolutional Neural Network"}
    ),
}
ACCEPTED_WEB_VERIFICATIONS = frozenset(
    {"verified", "cross_verified", "structured"}
)


@dataclass(frozen=True)
class SourceCoverageDecision:
    """单条公开来源的内部裁决结果，不包含页面正文。"""

    result: WebSearchResult
    accepted: bool
    reason_code: str
    matched_dimensions: tuple[str, ...] = ()


@dataclass(frozen=True)
class WebEvidenceCoverage:
    """一轮公网来源与当前学习目标的确定性覆盖结果。"""

    required_dimensions: tuple[str, ...]
    decisions: tuple[SourceCoverageDecision, ...]
    rules_version: str = EVIDENCE_COVERAGE_RULES_VERSION

    @property
    def accepted_results(self) -> tuple[WebSearchResult, ...]:
        return tuple(decision.result for decision in self.decisions if decision.accepted)

    @property
    def has_accepted_source(self) -> bool:
        return bool(self.accepted_results)

    @property
    def candidate_count(self) -> int:
        return len(self.decisions)

    @property
    def fetched_count(self) -> int:
        return sum(decision.result.fetched_at is not None for decision in self.decisions)

    @property
    def accepted_count(self) -> int:
        return len(self.accepted_results)

    @property
    def rejection_counts(self) -> dict[str, int]:
        counts = Counter(
            decision.reason_code
            for decision in self.decisions
            if not decision.accepted
        )
        return dict(sorted(counts.items()))

    @property
    def conflict_count(self) -> int:
        return sum(
            decision.result.verification == WebSearchVerification.CONFLICTING
            for decision in self.decisions
        )


def adjudicate_web_sources(
    query: str,
    results: list[WebSearchResult],
    *,
    goal: str | None = None,
) -> WebEvidenceCoverage:
    """只接收已抓取、可安全引用且覆盖目标门槛的公开来源。

    ``query`` 是搜索服务的脱敏查询投影；教学模式可额外传入本轮稳定的
    ``goal``，避免把查询分词或截断结果误当成覆盖主题。
    """

    rollback_to_v1 = EVIDENCE_COVERAGE_RULES_VERSION == EVIDENCE_COVERAGE_RULES_V1
    required = (
        _required_dimensions_v1(query[:_GOAL_MAX_CHARS])
        if rollback_to_v1
        else _required_dimensions((goal or query)[:_GOAL_MAX_CHARS])
    )
    decisions = tuple(
        _decide_source(result, required, legacy=rollback_to_v1) for result in results
    )
    return WebEvidenceCoverage(
        rules_version=(
            EVIDENCE_COVERAGE_RULES_V1
            if rollback_to_v1
            else EVIDENCE_COVERAGE_RULES_VERSION
        ),
        required_dimensions=required,
        decisions=decisions,
    )


def _required_dimensions(query: str) -> tuple[str, ...]:
    normalized = _normalize_text(query)
    if _TRANSFORMER.search(normalized) and (
        _AI_CONTEXT.search(normalized) or "transformer" in normalized
    ):
        return ("ai_transformer", "architecture", "self_attention")

    cleaned = _normalize_topic_text(normalized)
    alias = _canonical_alias(cleaned)
    if alias is not None:
        return ("topic:" + alias,)
    terms = [
        token
        for token in _TOPIC_TOKEN.findall(cleaned)
        if token not in _GENERIC_STOPWORDS
    ]
    if not terms:
        return ("topic",)
    # 保留最少的脱敏主题词；不把整句会话或用户画像带入裁决。
    return ("topic:" + max(terms, key=len),)


def _normalize_topic_text(value: str) -> str:
    """只去除边界上的教学模板噪声，避免误删主题内部字符。"""

    quoted = re.search(r"[“\"']([^“”\"']+)[”\"']", value)
    cleaned = quoted.group(1) if quoted else value
    cleaned = re.sub(r"^topic\s*[:：]\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = _TOPIC_PREFIX_NOISE.sub("", cleaned)
    if quoted is None:
        cleaned = _TOPIC_OBJECTIVE_NOISE.split(cleaned, maxsplit=1)[0]
    cleaned = cleaned.strip(" \t\r\n。！？?!，,：:；;“”\"'()（）[]【】")
    previous = None
    while cleaned and cleaned != previous:
        previous = cleaned
        cleaned = _TOPIC_TEMPLATE_SUFFIX.sub("", cleaned).strip()
        cleaned = _TOPIC_PARTICLE_SUFFIX.sub("", cleaned).strip()
    return cleaned


def _required_dimensions_v1(query: str) -> tuple[str, ...]:
    """v1 回滚规则：保留查询摘要取词和旧模板噪声语义。"""

    normalized = query.casefold()
    if _TRANSFORMER.search(normalized) and (
        _AI_CONTEXT.search(normalized) or "transformer" in normalized
    ):
        return ("ai_transformer", "architecture", "self_attention")

    cleaned = _TOPIC_NOISE_V1.sub(" ", query)
    terms = [
        token.casefold()
        for token in _TOPIC_TOKEN.findall(cleaned)
        if token.casefold() not in _GENERIC_STOPWORDS
    ]
    if not terms:
        return ("topic",)
    return ("topic:" + max(terms, key=len),)


def _decide_source(
    result: WebSearchResult,
    required_dimensions: tuple[str, ...],
    *,
    legacy: bool = False,
) -> SourceCoverageDecision:
    accepted_verifications = (
        {WebSearchVerification.VERIFIED, WebSearchVerification.CROSS_VERIFIED}
        if legacy
        else ACCEPTED_WEB_VERIFICATIONS
    )
    verification = result.verification if legacy else result.verification.value
    if verification not in accepted_verifications:
        return SourceCoverageDecision(result, False, "not_safe_to_cite")
    # 结构化备用源的有界摘要就是其已登记的可引用载荷，不要求它伪造页面抓取时间；
    # verified/cross_verified 仍必须有真实页面抓取标记。
    if (
        result.fetched_at is None
        and (legacy or result.verification != WebSearchVerification.STRUCTURED)
    ) or not result.content_summary.strip():
        return SourceCoverageDecision(result, False, "not_fetched")

    raw_material = " ".join(
        (
            result.title[:_SOURCE_METADATA_MAX_CHARS],
            result.snippet[:_SOURCE_METADATA_MAX_CHARS],
            result.content_summary[:_SOURCE_CONTENT_MAX_CHARS],
        )
    )
    material = raw_material if legacy else _normalize_text(raw_material)
    if required_dimensions == ("ai_transformer", "architecture", "self_attention"):
        if not _TRANSFORMER.search(material) or _ELECTRICAL_CONTEXT.search(material):
            return SourceCoverageDecision(result, False, "topic_mismatch")
        matched = tuple(
            dimension
            for dimension, pattern in (
                ("ai_transformer", _AI_CONTEXT),
                ("architecture", _ARCHITECTURE),
                ("self_attention", _SELF_ATTENTION),
            )
            if pattern.search(material)
        )
        if len(matched) != 3:
            return SourceCoverageDecision(
                result, False, "coverage_incomplete", matched_dimensions=matched
            )
        return SourceCoverageDecision(result, True, "accepted", matched_dimensions=matched)

    topic = required_dimensions[0].removeprefix("topic:")
    topic_matches = (
        topic.casefold() in material.casefold()
        if legacy
        else any(
            _contains_literal(material, variant)
            for variant in _topic_variants(topic)
        )
    )
    if not topic_matches:
        return SourceCoverageDecision(result, False, "topic_mismatch")
    return SourceCoverageDecision(result, True, "accepted", matched_dimensions=("topic",))


def _normalize_text(value: str) -> str:
    """统一全半角、大小写和 Unicode 兼容形式后再做字面匹配。"""

    return unicodedata.normalize("NFKC", value).casefold()


def _canonical_alias(value: str) -> str | None:
    for canonical, aliases in TOPIC_ALIASES.items():
        if any(_contains_literal(value, alias) for alias in aliases):
            return _normalize_text(canonical)
    return None


def _topic_variants(topic: str) -> frozenset[str]:
    normalized_topic = _normalize_text(topic)
    for canonical, aliases in TOPIC_ALIASES.items():
        if normalized_topic == _normalize_text(canonical):
            return frozenset(_normalize_text(alias) for alias in aliases)
    return frozenset({normalized_topic})


def _contains_literal(material: str, term: str) -> bool:
    normalized_term = _normalize_text(term).strip()
    if not normalized_term:
        return False
    if _ASCII_ALIAS.fullmatch(normalized_term):
        pattern = rf"(?<![a-z0-9]){re.escape(normalized_term)}(?![a-z0-9])"
        return re.search(pattern, material) is not None
    return normalized_term in material


__all__ = [
    "ACCEPTED_WEB_VERIFICATIONS",
    "EVIDENCE_COVERAGE_RULES_VERSION",
    "EVIDENCE_COVERAGE_RULES_V1",
    "SourceCoverageDecision",
    "TOPIC_ALIASES",
    "TOPIC_ALIASES_VERSION",
    "WebEvidenceCoverage",
    "adjudicate_web_sources",
]
