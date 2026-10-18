"""当前分析内的稳定概念标识与逐字语义证据校验。

概念标识只服务当前论文需求映射：由代码按分析顺序分配（``c1``…），模型
只能在证据里引用它，不能自由创造需求名称。显示名称仍用于用户可见说明。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from bridges.paper.contracts import PaperTermAnalysis
from bridges.paper.sources import PaperCandidate

SEMANTIC_EVALUATION_UNAVAILABLE = "semantic_evaluation_unavailable"
SEMANTIC_EVIDENCE_STRUCTURE_INVALID = "semantic_evidence_structure_invalid"
SEMANTIC_EVIDENCE_MISSING = "semantic_evidence_missing"
SEMANTIC_REQUIREMENT_UNKNOWN = "semantic_requirement_unknown"
SEMANTIC_SOURCE_INVALID = "semantic_source_invalid"
SEMANTIC_QUOTE_NOT_FOUND = "semantic_quote_not_found"

SUPPORTED_EVIDENCE_SOURCES = frozenset({"title", "abstract"})

_CONCEPT_ID = re.compile(r"^c[1-9][0-9]*$")

_SEMANTIC_REASON_ORDER = (
    SEMANTIC_EVALUATION_UNAVAILABLE,
    SEMANTIC_EVIDENCE_STRUCTURE_INVALID,
    SEMANTIC_EVIDENCE_MISSING,
    SEMANTIC_REQUIREMENT_UNKNOWN,
    SEMANTIC_SOURCE_INVALID,
    SEMANTIC_QUOTE_NOT_FOUND,
)
_SEMANTIC_REASON_LABELS = {
    SEMANTIC_EVALUATION_UNAVAILABLE: "缺少可用的语义裁决或裁决结构无效",
    SEMANTIC_EVIDENCE_STRUCTURE_INVALID: "证据结构无效",
    SEMANTIC_EVIDENCE_MISSING: "没有给出支持需求的证据",
    SEMANTIC_REQUIREMENT_UNKNOWN: "证据引用了未知需求标识",
    SEMANTIC_SOURCE_INVALID: "证据来源字段非法",
    SEMANTIC_QUOTE_NOT_FOUND: "引文无法在标题或摘要中逐字定位",
}


@dataclass(frozen=True)
class ConceptRequirement:
    """一条当前分析内的需求映射（内部标识 → 显示名称）。"""

    concept_id: str
    name: str
    source_phrase: str
    kind: str

    def to_payload(self) -> dict[str, str]:
        return {
            "id": self.concept_id,
            "name": self.name,
            "source_phrase": self.source_phrase,
            "kind": self.kind,
        }


@dataclass(frozen=True)
class SemanticEvidenceOutcome:
    """一次语义裁决的本地校验结果。"""

    relevant: bool | None = None
    evidence: tuple[dict[str, str], ...] = ()
    reason: str | None = None
    reason_counts: dict[str, int] = field(default_factory=dict)


def concept_requirements(analysis: PaperTermAnalysis) -> tuple[ConceptRequirement, ...]:
    """返回当前分析内的需求映射；缺失或非法的标识由代码补配并保持唯一。"""
    output: list[ConceptRequirement] = []
    used: set[str] = set()
    for index, concept in enumerate(analysis.concepts, start=1):
        name = concept.concept.strip()
        if not name:
            continue
        concept_id = concept.concept_id.strip()
        if not _CONCEPT_ID.fullmatch(concept_id) or concept_id in used:
            candidate_index = index
            while f"c{candidate_index}" in used:
                candidate_index += 1
            concept_id = f"c{candidate_index}"
        used.add(concept_id)
        output.append(
            ConceptRequirement(
                concept_id=concept_id,
                name=name,
                source_phrase=concept.source_phrase,
                kind=concept.kind,
            )
        )
    return tuple(output)


def concept_signature(analysis: PaperTermAnalysis) -> str:
    """缓存键：需求标识映射变化时不得复用旧裁决；显示名称只影响本地呈现。"""
    return "|".join(
        f"{item.concept_id}:{item.kind}:{item.source_phrase}"
        for item in concept_requirements(analysis)
    )


def validate_semantic_evidence(
    judge_result: Mapping[str, Any] | None,
    candidate: PaperCandidate,
    requirements: Sequence[ConceptRequirement],
    *,
    require_mapped_requirement: bool = True,
) -> SemanticEvidenceOutcome:
    """本地复核模型裁决；只接受当前需求标识和可逐字定位的来源片段。"""
    if judge_result is None or not isinstance(judge_result.get("relevant"), bool):
        return SemanticEvidenceOutcome(reason=SEMANTIC_EVALUATION_UNAVAILABLE)
    if judge_result.get("relevant") is False:
        return SemanticEvidenceOutcome(relevant=False)

    raw_evidence = judge_result.get("evidence")
    if raw_evidence is None:
        return SemanticEvidenceOutcome(
            relevant=True, reason=SEMANTIC_EVIDENCE_MISSING
        )
    if not isinstance(raw_evidence, Sequence) or isinstance(raw_evidence, (str, bytes)):
        return SemanticEvidenceOutcome(
            relevant=True,
            reason=SEMANTIC_EVIDENCE_STRUCTURE_INVALID,
            reason_counts={SEMANTIC_EVIDENCE_STRUCTURE_INVALID: 1},
        )

    requirement_by_id = {item.concept_id: item for item in requirements}
    evidence: list[dict[str, str]] = []
    counts: dict[str, int] = {}
    for entry in raw_evidence:
        if not isinstance(entry, Mapping):
            counts[SEMANTIC_EVIDENCE_STRUCTURE_INVALID] = (
                counts.get(SEMANTIC_EVIDENCE_STRUCTURE_INVALID, 0) + 1
            )
            continue
        requirement_id = str(entry.get("requirement") or "").strip()
        if require_mapped_requirement:
            requirement = requirement_by_id.get(requirement_id)
            if requirement is None:
                counts[SEMANTIC_REQUIREMENT_UNKNOWN] = (
                    counts.get(SEMANTIC_REQUIREMENT_UNKNOWN, 0) + 1
                )
                continue
            requirement_name = requirement.name
        else:
            if not requirement_id:
                counts[SEMANTIC_REQUIREMENT_UNKNOWN] = (
                    counts.get(SEMANTIC_REQUIREMENT_UNKNOWN, 0) + 1
                )
                continue
            requirement_name = requirement_id
        source = entry.get("source")
        if not isinstance(source, str) or source not in SUPPORTED_EVIDENCE_SOURCES:
            counts[SEMANTIC_SOURCE_INVALID] = counts.get(SEMANTIC_SOURCE_INVALID, 0) + 1
            continue
        quote = entry.get("quote")
        source_text = {"title": candidate.title, "abstract": candidate.abstract}[source]
        if not isinstance(quote, str) or not quote.strip() or quote.strip() not in source_text:
            counts[SEMANTIC_QUOTE_NOT_FOUND] = counts.get(SEMANTIC_QUOTE_NOT_FOUND, 0) + 1
            continue
        evidence.append(
            {
                "requirement": requirement_name,
                "source": source,
                "quote": quote.strip()[:160],
            }
        )
    if evidence:
        return SemanticEvidenceOutcome(
            relevant=True, evidence=tuple(evidence), reason_counts=counts
        )
    if not counts:
        return SemanticEvidenceOutcome(
            relevant=True, reason=SEMANTIC_EVIDENCE_MISSING
        )
    return SemanticEvidenceOutcome(
        relevant=True, reason=_primary_reason(counts), reason_counts=counts
    )


def _primary_reason(counts: Mapping[str, int]) -> str:
    for reason in _SEMANTIC_REASON_ORDER:
        if counts.get(reason):
            return reason
    return SEMANTIC_EVIDENCE_MISSING


def merge_reason_counts(target: dict[str, int], source: Mapping[str, int]) -> None:
    for reason, count in source.items():
        target[reason] = target.get(reason, 0) + count


def increment_reason_count(counts: dict[str, int], reason: str) -> None:
    counts[reason] = counts.get(reason, 0) + 1


def semantic_reason_summary(counts: Mapping[str, int]) -> str:
    """脱敏原因计数：只报告稳定类别与条数，不暴露内部标识或原始载荷。"""
    ordered = [
        f"{_SEMANTIC_REASON_LABELS.get(reason, reason)} {counts[reason]} 条"
        for reason in _SEMANTIC_REASON_ORDER
        if counts.get(reason)
    ]
    extra = [
        f"{reason} {count} 条"
        for reason, count in counts.items()
        if reason not in _SEMANTIC_REASON_ORDER
    ]
    return "原因计数：" + "、".join([*ordered, *extra])
