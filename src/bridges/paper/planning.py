"""``paper.plan``：将动态研究意图映射到受约束的来源查询。

使用模型识别出的研究概念和英文检索表达，按用户指定范围与来源能力选择
最多三个已登记来源。首轮不预先生成放宽查询；有明确缺口时由筛选节点最多
发起一次针对性补搜，仍须保留所有明确条件。
"""

from __future__ import annotations

from bridges.paper.contracts import (
    PaperQueryPlan,
    PaperSortIntent,
    PaperTermAnalysis,
)

#: 普通请求重点推荐目标；完整候选仍独立展示。
DEFAULT_TARGET_COUNT = 8
MIN_TARGET_COUNT = 3
MAX_TARGET_COUNT = 30

#: 进入查询的扩展词上限（扩展只作补充，避免把查询收窄到无结果）。
MAX_QUERY_EXPANSIONS = 12

def plan_queries(
    analysis: PaperTermAnalysis,
    *,
    available_sources: tuple[str, ...] = ("arxiv",),
    missing_requirements: tuple[str, ...] = (),
) -> tuple[PaperQueryPlan, ...]:
    """为有限个已登记来源生成首轮或一次针对缺口的查询。"""
    all_terms = [item.strip() for item in analysis.expansions if item.strip()]
    if analysis.concepts:
        terms = [
            concept.english_search_terms[0].strip()
            for concept in analysis.concepts
            if concept.english_search_terms and concept.english_search_terms[0].strip()
        ]
    else:
        terms = all_terms[:MAX_QUERY_EXPANSIONS]
    if missing_requirements:
        focus = set(missing_requirements)
        alternatives = [
            term.strip()
            for concept in analysis.concepts
            if concept.source_phrase in focus or concept.concept in focus
            for term in concept.english_search_terms[1:]
            if term.strip()
        ]
        terms = list(dict.fromkeys([*terms, *alternatives]))[:MAX_QUERY_EXPANSIONS]
    query = " ".join(dict.fromkeys(terms)).strip()
    if not query:
        query = analysis.final_query.strip() or analysis.normalized_term.strip()
    sort_by, sort_order = _sorting(analysis.constraints.sort_intent)
    target_count = analysis.constraints.requested_count or DEFAULT_TARGET_COUNT
    max_results = _candidate_limit(analysis.constraints.requested_count or 10)
    excluded = set(analysis.constraints.excluded_sources)
    available = list(dict.fromkeys(available_sources))
    if analysis.constraints.allowed_sources:
        selected = [
            item for item in available if item in analysis.constraints.allowed_sources
        ]
    elif analysis.recommended_sources:
        selected = [
            item
            for item in analysis.recommended_sources
            if item in available and item not in excluded
        ][:3]
        fallback = [item for item in available if item not in excluded]
        desired_count = min(2, len(fallback))
        for item in fallback:
            if len(selected) >= desired_count or len(selected) >= 3:
                break
            if item not in selected:
                selected.append(item)
    else:
        # 仅旧版确定性解析结果没有模型来源建议；动态意图路径不得把所有来源补选。
        selected = available[:3]
    selected = [item for item in selected if item not in excluded]
    plans = [
        PaperQueryPlan(
            query=query,
            source=source,
            sort_by=sort_by,
            sort_order=sort_order,
            max_results=min(10, max_results),
            target_count=target_count,
            expansions_used=terms,
            rationale=(
                f"保留研究对象「{analysis.research_object or analysis.original_phrase}」、"
                f"问题「{analysis.research_question or analysis.original_phrase}」及方法/应用概念；"
                f"由 {source} 执行查询。" + _sort_rationale(analysis.constraints.sort_intent)
            ),
        )
        for source in selected
    ]
    return tuple(plans)


def _candidate_limit(target: int) -> int:
    return max(MIN_TARGET_COUNT, min(10, target))


def _sorting(intent: PaperSortIntent) -> tuple[str, str]:
    """将用户排序意图映射为通用偏好，来源适配器按自身能力应用。"""
    if intent is PaperSortIntent.LATEST:
        return "submittedDate", "descending"
    return "relevance", "descending"


def _sort_rationale(intent: PaperSortIntent) -> str:
    if intent is PaperSortIntent.LATEST:
        return "排序：按提交时间从新到旧。"
    if intent is PaperSortIntent.CLASSIC:
        return "排序：按相关度检索，再依实际取得的引用证据分辨奠基工作。"
    if intent is PaperSortIntent.BEGINNER:
        return "排序：按相关度检索，优先放入门综述与教程。"
    return "排序：按相关度检索。"
