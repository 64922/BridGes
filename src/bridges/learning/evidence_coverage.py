"""学习模式公开来源的确定性目标覆盖裁决。"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from bridges.web_search.contracts import WebSearchResult, WebSearchVerification


EVIDENCE_COVERAGE_RULES_VERSION = "learning-evidence-coverage-v1"
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
_TOPIC_NOISE = re.compile(
    r"学习|学一下|教我|了解|认识|入门|弄懂|搞清楚|请|帮我|我想(?:要)?|"
    r"想(?:要)?|要|的相关知识|相关知识|基础知识|核心机制|并理解|理解|知识|相关",
    re.IGNORECASE,
)
_TOPIC_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,8}")
_GENERIC_STOPWORDS = frozenset(
    {"当前", "本轮", "目标", "主题", "内容", "概念", "一个", "一些", "什么"}
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
    query: str, results: list[WebSearchResult]
) -> WebEvidenceCoverage:
    """只接收已抓取、可安全引用且覆盖目标门槛的公开来源。"""

    required = _required_dimensions(query[:_GOAL_MAX_CHARS])
    decisions = tuple(
        _decide_source(result, required) for result in results
    )
    return WebEvidenceCoverage(required_dimensions=required, decisions=decisions)


def _required_dimensions(query: str) -> tuple[str, ...]:
    normalized = query.casefold()
    if _TRANSFORMER.search(normalized) and (
        _AI_CONTEXT.search(normalized) or "transformer" in normalized
    ):
        return ("ai_transformer", "architecture", "self_attention")

    cleaned = _TOPIC_NOISE.sub(" ", query)
    terms = [
        token.casefold()
        for token in _TOPIC_TOKEN.findall(cleaned)
        if token.casefold() not in _GENERIC_STOPWORDS
    ]
    if not terms:
        return ("topic",)
    # 保留最少的脱敏主题词；不把整句会话或用户画像带入裁决。
    return ("topic:" + max(terms, key=len),)


def _decide_source(
    result: WebSearchResult, required_dimensions: tuple[str, ...]
) -> SourceCoverageDecision:
    if result.verification not in {
        WebSearchVerification.VERIFIED,
        WebSearchVerification.CROSS_VERIFIED,
    }:
        return SourceCoverageDecision(result, False, "not_safe_to_cite")
    if result.fetched_at is None or not result.content_summary.strip():
        return SourceCoverageDecision(result, False, "not_fetched")

    material = " ".join(
        (
            result.title[:_SOURCE_METADATA_MAX_CHARS],
            result.snippet[:_SOURCE_METADATA_MAX_CHARS],
            result.content_summary[:_SOURCE_CONTENT_MAX_CHARS],
        )
    )
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
    if topic.casefold() not in material.casefold():
        return SourceCoverageDecision(result, False, "topic_mismatch")
    return SourceCoverageDecision(result, True, "accepted", matched_dimensions=("topic",))


__all__ = [
    "EVIDENCE_COVERAGE_RULES_VERSION",
    "SourceCoverageDecision",
    "WebEvidenceCoverage",
    "adjudicate_web_sources",
]
