"""``paper.plan``：精确词优先的 arXiv 查询与排序。

规则（``docs/v2/workflows.md`` 第 2 节）：生成精确词优先的 arXiv 查询；译名／
同义词只作为扩展，不能替掉原词；按「最新／经典／入门」意图设置排序。

查询尝试有界：先按「主词 + 扩展词」精确检索；结果过少时第二次只用主词放宽
召回——两次都仍不足时如实报实际数量，不凑篇数。上限由本模块返回的计划条数
决定（无扩展词时只有一条），调用方不再另设次数。
"""

from __future__ import annotations

from bridges.paper.contracts import (
    PaperQueryPlan,
    PaperSortIntent,
    PaperTermAnalysis,
)

#: 本轮默认目标篇数（验收要求默认 3–5 篇）。
DEFAULT_TARGET_COUNT = 5
MIN_TARGET_COUNT = 3
MAX_TARGET_COUNT = 5

#: 进入查询的扩展词上限（扩展只作补充，避免把查询收窄到无结果）。
MAX_QUERY_EXPANSIONS = 2

#: 候选请求量：高于目标篇数以留出去重与主题筛除的余量。
CANDIDATE_MULTIPLIER = 3


def plan_queries(analysis: PaperTermAnalysis) -> tuple[PaperQueryPlan, ...]:
    """按解析结果生成有界的查询计划（第一条为精确词优先）。"""
    primary = analysis.final_query.strip() or analysis.normalized_term.strip()
    expansions = [item for item in analysis.expansions if item.strip()][:MAX_QUERY_EXPANSIONS]
    sort_by, sort_order = _sorting(analysis.constraints.sort_intent)
    max_results = _candidate_limit(DEFAULT_TARGET_COUNT)
    precise_query = _join_query(primary, expansions)
    plans: list[PaperQueryPlan] = [
        PaperQueryPlan(
            query=precise_query,
            sort_by=sort_by,
            sort_order=sort_order,
            max_results=max_results,
            target_count=DEFAULT_TARGET_COUNT,
            expansions_used=expansions,
            rationale=(
                f"主词「{primary}」对应你的原始术语；"
                + (
                    f"扩展词 {'、'.join(expansions)} 作为补充线索。"
                    if expansions
                    else "未使用扩展词。"
                )
                + _sort_rationale(analysis.constraints.sort_intent)
            ),
        )
    ]
    if expansions:
        # 精确词 + 扩展词没有足够结果时，放宽为只用主词（仍然保留原词）。
        plans.append(
            PaperQueryPlan(
                query=primary,
                sort_by=sort_by,
                sort_order=sort_order,
                max_results=max_results,
                target_count=DEFAULT_TARGET_COUNT,
                expansions_used=[],
                rationale=f"扩展词使结果过少，第二轮只用主词「{primary}」放宽召回。",
            )
        )
    return tuple(plans)


def _join_query(primary: str, expansions: list[str]) -> str:
    parts = [primary.strip()] if primary.strip() else []
    parts.extend(item.strip() for item in expansions)
    return " ".join(part for part in parts if part)


def _candidate_limit(target: int) -> int:
    return max(MIN_TARGET_COUNT, min(MAX_TARGET_COUNT, target)) * CANDIDATE_MULTIPLIER


def _sorting(intent: PaperSortIntent) -> tuple[str, str]:
    """arXiv 只提供 submittedDate/relevance/lastUpdatedDate；经典无原生排序。

    「最新」用提交时间倒序；其余用相关度——经典与入门只能在排序阶段用真实
    取得的引用/年份证据重排，绝不假装 arXiv 支持引用排序。
    """
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
