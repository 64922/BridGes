"""来源清洁扫描与数据卡（Issue 09，ADR-0011 净室边界）。

对全部语料与派生资产运行来源清洁扫描：跨案例精确/近似片段比对（防止
同源重复泄漏到 development/holdout 两侧）、许可证元数据完整性与净室
声明校验。任何无法自动确认来源清洁的资产不得进入 corpus——本扫描
是硬门而非人工抽查的替代。

数据卡输出语料统计：案例数、切片分布、路径/模式/强度/体裁/风险/对抗
覆盖计数与来源清洁结论（全部脱敏计数，不含私人正文）。
"""

from __future__ import annotations

import re
from typing import Iterable

from pydantic import BaseModel, Field

from bridges.humanize_eval.cases import (
    CasePartition,
    HumanizeCase,
    HumanizeCaseKind,
)

#: 中文近似重复检测的字符 n-gram 粒度（8 字符足以捕捉同源文本）。
_SHINGLE_SIZE = 8
#: 判定为可疑同源的 shingle 重叠阈值。
_SUSPICIOUS_OVERLAP = 0.7


def _shingles(text: str, size: int = _SHINGLE_SIZE) -> set[str]:
    text = re.sub(r"\s+", "", text)
    return {text[i : i + size] for i in range(max(0, len(text) - size + 1))}


def _overlap(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    denominator = min(len(a), len(b)) or 1
    return len(a & b) / denominator


class CleanlinessReport(BaseModel):
    """一次来源清洁扫描的完整结果（脱敏）。"""

    checked_cases: int = Field(description="参与扫描的案例数。")
    license_complete: int = Field(description="许可证/来源说明完整的案例数。")
    license_missing: int = Field(description="缺少净室声明的案例数。")
    duplicate_pairs: list[str] = Field(
        default_factory=list, description="跨分区近似重复对（case1~case2）。"
    )
    protected_violations: list[str] = Field(
        default_factory=list, description="保护项非原文逐字片段的案例。"
    )
    clean: bool = Field(description="扫描通过（无任何问题）。")


def scan_license(cases: Iterable[HumanizeCase]) -> tuple[int, list[str]]:
    """许可证/来源说明完整性：净室声明必须存在。"""
    complete = 0
    missing: list[str] = []
    for case in cases:
        note = case.license_source_note.strip()
        if note and ("净室" in note or "原创" in note or "无第三方" in note):
            complete += 1
        else:
            missing.append(case.case_id)
    return complete, missing


def scan_cross_partition_duplicates(
    cases: Iterable[HumanizeCase],
) -> list[str]:
    """跨 development/holdout 分区的近似重复检测（同源泄漏硬门）。"""
    by_partition: dict[str, list[tuple[str, set[str]]]] = {}
    for case in cases:
        text = "\n".join(
            filter(
                None,
                (
                    case.source_text or "",
                    case.user_request,
                    "\n".join(case.protected_items),
                ),
            )
        )
        by_partition.setdefault(case.partition.value, []).append(
            (case.case_id, _shingles(text))
        )
    problems: list[str] = []
    partitions = list(by_partition)
    for i, left_partition in enumerate(partitions):
        for right_partition in partitions[i + 1 :]:
            for left_id, left_set in by_partition[left_partition]:
                for right_id, right_set in by_partition[right_partition]:
                    if _overlap(left_set, right_set) >= _SUSPICIOUS_OVERLAP:
                        problems.append(
                            f"{left_id}~{right_id}（跨分区近似重复，"
                            f"重叠 {_overlap(left_set, right_set):.0%}）"
                        )
    return sorted(set(problems))


def scan_protected_verbatim(cases: Iterable[HumanizeCase]) -> list[str]:
    """保护项必须是原文逐字片段（保真检查才能可靠执行）。"""
    violations: list[str] = []
    for case in cases:
        if not case.source_text:
            continue
        for item in case.protected_items:
            if item and item not in case.source_text:
                violations.append(f"{case.case_id}：保护项非原文逐字片段：{item!r}")
    return sorted(set(violations))


def run_cleanliness_scan(cases: Iterable[HumanizeCase]) -> CleanlinessReport:
    """执行全部来源清洁扫描（硬门：任何问题都不得进入 corpus）。"""
    case_list = list(cases)
    complete, missing = scan_license(case_list)
    duplicates = scan_cross_partition_duplicates(case_list)
    verbatim = scan_protected_verbatim(case_list)
    return CleanlinessReport(
        checked_cases=len(case_list),
        license_complete=complete,
        license_missing=len(missing),
        duplicate_pairs=duplicates,
        protected_violations=verbatim,
        clean=not missing and not duplicates and not verbatim,
    )


# ---------------------------------------------------------------------------
# 数据卡
# ---------------------------------------------------------------------------

class SliceCount(BaseModel):
    """一个切片的覆盖计数。"""

    label: str = Field(description="切片标签。")
    count: int = Field(description="案例数。")
    ids: list[str] = Field(default_factory=list, description="案例 id 清单。")

    def model_dump_compact(self) -> dict[str, object]:
        return {"label": self.label, "count": self.count}


def _count_by(cases: list[HumanizeCase], key_fn) -> list[SliceCount]:
    counts: dict[str, list[str]] = {}
    for case in cases:
        label = key_fn(case)
        if label is None:
            continue
        counts.setdefault(label, []).append(case.case_id)
    return [
        SliceCount(label=label, count=len(ids), ids=sorted(ids))
        for label, ids in sorted(counts.items())
    ]


#: 聊天路径标签（slice_tags 中除模式外的路径切片）。
_CHAT_PATH_TAGS = {
    "short_answer",
    "explanation",
    "advice",
    "correction",
    "multi_turn",
    "emotion",
    "clarification",
    "tool_result",
    "error_refusal",
    "code_formula",
    "learning_lesson",
}


def _chat_path(case: HumanizeCase) -> str | None:
    """聊天案例的路径标签（slice_tags 中唯一非模式标签）。"""
    paths = [tag for tag in case.slice_tags if tag in _CHAT_PATH_TAGS]
    return paths[0] if len(paths) == 1 else None


def generate_datacard(cases: Iterable[HumanizeCase]) -> dict[str, object]:
    """生成语料数据卡（脱敏统计，供审计与发布）。"""
    case_list = list(cases)
    chat = [c for c in case_list if c.kind is HumanizeCaseKind.CHAT]
    article = [c for c in case_list if c.kind is HumanizeCaseKind.ARTICLE]
    scan = run_cleanliness_scan(case_list)

    return {
        "corpus_version": _corpus_version(),
        "total_cases": len(case_list),
        "surfaces": {
            "chat_naturalness": {
                "count": len(chat),
                "development": sum(
                    1 for c in chat if c.partition is CasePartition.DEVELOPMENT
                ),
                "holdout": sum(
                    1 for c in chat if c.partition is CasePartition.HOLDOUT
                ),
                "conversation_modes": [
                    sc.model_dump_compact()
                    for sc in _count_by(chat, lambda c: c.conversation_mode.value)
                ],
                "path_slices": [
                    sc.model_dump_compact()
                    for sc in _count_by(chat, _chat_path)
                ],
            },
            "article_humanization": {
                "count": len(article),
                "development": sum(
                    1 for c in article if c.partition is CasePartition.DEVELOPMENT
                ),
                "holdout": sum(
                    1 for c in article if c.partition is CasePartition.HOLDOUT
                ),
                "genres": [
                    sc.model_dump_compact()
                    for sc in _count_by(article, lambda c: c.genre_profile.value)
                ],
                "intensities": [
                    sc.model_dump_compact()
                    for sc in _count_by(
                        article, lambda c: c.rewrite_intensity.value
                    )
                ],
                "operations": [
                    sc.model_dump_compact()
                    for sc in _count_by(article, lambda c: c.operation.value)
                ],
            },
            "risk_distribution": [
                sc.model_dump_compact()
                for sc in _count_by(case_list, lambda c: c.risk.value)
            ],
            "adversarial_types": [
                sc.model_dump_compact()
                for sc in _count_by(case_list, lambda c: c.adversarial_type)
            ],
        },
        "cleanliness": {
            "checked_cases": scan.checked_cases,
            "license_complete": scan.license_complete,
            "license_missing": scan.license_missing,
            "duplicate_pairs": scan.duplicate_pairs,
            "protected_violations": scan.protected_violations,
            "clean": scan.clean,
        },
    }


def _corpus_version() -> str:
    from bridges.humanize_eval.cases import CORPUS_VERSION

    return CORPUS_VERSION
