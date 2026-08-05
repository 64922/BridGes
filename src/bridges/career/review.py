"""生涯规划输出的确定性复核（Issue 29）。

复核是确定性控制平面的一部分，不依赖模型自评：

1. **承诺词边界**：正文或任一条目出现就业/薪酬/录取承诺（排除"不保证"
   等否定形式）→ 阻断交付（``passed=False``），错误终态说明违反项；
2. **完整性门**：正文与边界声明非空且六类中至少一类有内容，否则不完成；
3. **事实证据门**：``facts`` 条目无任何证据引用 → 标记未核实（不得当作
   稳定结论呈现），并提示其应视为待验证假设；
4. **引用核验**：``evidence_refs`` 指向不存在的证据 → 标记未核实；
5. **过时来源**：引用已过时（``stale=True``）来源的条目 → 标记过时；
6. **证据冲突**：引用多个证据但未说明证据间关系（冲突/一致/差异等）的
   条目 → 标记冲突，不作稳定判断（系统不越权）。
"""

from __future__ import annotations

import re
from datetime import datetime

from bridges.contracts.career import (
    CareerEvidenceSource,
    CareerItemBase,
    CareerItemState,
    CareerPlanningOutputContract,
    CareerReviewItem,
    CareerReviewResult,
)

#: 来源新鲜度阈值（天）：超过即视为可能过时，引用其的条目标记过时。
STALE_AFTER_DAYS = 90

#: 阻断性承诺词：命中即视为就业/薪酬/录取保证（否定形式先行剔除）。
#: 「保证/承诺/确保/一定/必然/稳」等承诺强度词在生涯规划语境中本身即
#: 越界（规划不得作任何就业、薪酬或录取保证），否定形式（不保证/不构成
#: …保证等）除外。
_PROMISE_PATTERNS: tuple[str, ...] = (
    "保证",
    "承诺",
    "确保",
    "包过",
    "包就业",
    "包上岸",
    "包分配",
    "包录取",
    "包找到",
    "一定找得到",
    "必然录取",
    "稳上",
    "稳过",
    "百分百",
    "100%录取",
    "100% 录取",
)

#: 否定提示词：承诺词前窗口内出现任一即视为否定语境（合规边界声明如
#: 「不构成就业、薪酬或录取保证」不会误伤；「保证就业」等正向承诺仍阻断）。
_NEGATION_HINTS: tuple[str, ...] = (
    "不",
    "无",
    "未",
    "没",
    "别",
    "勿",
    "拒绝",
    "避免",
    "并非",
    "不会",
    "无法",
    "不能",
    "难以",
)

#: 否定检测的向前扫描窗口长度（字符），覆盖「不构成就业、薪酬或录取保证」。
_NEGATION_WINDOW = 15

#: 证据关系说明词：条目引用多个证据时，note 含任一即视为已说明关系。
_RELATIONSHIP_WORDS: tuple[str, ...] = (
    "冲突",
    "不一致",
    "矛盾",
    "分歧",
    "差异",
    "不同",
    "一致",
    "印证",
    "补充",
    "对照",
    "进一步核查",
    "待核实",
    "未核实",
)

_CATEGORY_KEYS: tuple[str, ...] = (
    "facts",
    "assumptions",
    "options",
    "risks",
    "path",
    "suggestions",
)

#: 类别中文标签（复核原因文案）。
_CATEGORY_CN: dict[str, str] = {
    "facts": "已知事实",
    "assumptions": "待验证假设",
    "options": "可选方向",
    "risks": "关键风险",
    "path": "成长路径",
    "suggestions": "近期建议",
}


def _iter_items(
    output: CareerPlanningOutputContract,
) -> list[tuple[str, CareerItemBase]]:
    """展开六类条目为 (category, item) 列表（保持类别顺序）。"""
    items: list[tuple[str, CareerItemBase]] = []
    for key in _CATEGORY_KEYS:
        for item in getattr(output, key):
            items.append((key, item))
    return items


def _contains_promise(text: str) -> bool:
    """检查是否命中正向承诺（承诺词前窗口内无否定提示词才算）。"""
    for pattern in _PROMISE_PATTERNS:
        for match in re.finditer(re.escape(pattern), text):
            before = text[max(0, match.start() - _NEGATION_WINDOW) : match.start()]
            if not any(hint in before for hint in _NEGATION_HINTS):
                return True
    return False


def review_output(
    output: CareerPlanningOutputContract,
    evidence_map: dict[str, CareerEvidenceSource],
    *,
    now: datetime,
) -> CareerReviewResult:
    """对模型输出执行确定性复核，返回复核结论（不修改传入对象）。"""
    reviews: list[CareerReviewItem] = []
    warnings: list[str] = []
    boundary_violations: list[str] = []

    # 1. 承诺词边界（阻断）：正文、保证边界声明与六类条目都不得含正向
    # 承诺（否定形式剔除后检查，合规边界声明不误伤）。
    promised_parts: list[str] = []
    if _contains_promise(output.final_text):
        promised_parts.append("正文")
    if _contains_promise(output.boundary_statement):
        promised_parts.append("保证边界声明")
    for category, item in _iter_items(output):
        if _contains_promise(item.content):
            promised_parts.append(f"{_CATEGORY_CN[category]}条目 {item.item_id}")
    if promised_parts:
        boundary_violations.append(
            "检测到就业/薪酬/录取类承诺表述（涉及：" + "、".join(promised_parts) + "）。"
        )

    # 2. 完整性门：正文/边界声明非空，且六类至少一类有内容。
    if not output.final_text.strip():
        boundary_violations.append("输出合同不完整：缺少自然中文正文（final_text）。")
    if not output.boundary_statement.strip():
        boundary_violations.append(
            "输出合同不完整：缺少保证边界声明（boundary_statement）。"
        )
    if not any(getattr(output, key) for key in _CATEGORY_KEYS):
        boundary_violations.append("输出合同不完整：六类结果全部为空。")

    # 3-6. 逐条复核（事实证据门/引用核验/过时/冲突）。
    for category, item in _iter_items(output):
        refs = list(item.evidence_refs)
        # 引用核验：先筛出真实存在的证据。
        known = [ref for ref in refs if ref in evidence_map]
        unknown = [ref for ref in refs if ref not in evidence_map]
        if unknown:
            warnings.append(
                f"{_CATEGORY_CN[category]}条目 {item.item_id} 引用了"
                f"本轮不存在的证据（{ '、'.join(unknown) }），已标记未核实。"
            )
        state: CareerItemState = CareerItemState.VERIFIED
        reason = "已核验：有可定位来源与核查时间。"
        if category == "facts" and not known:
            state = CareerItemState.UNVERIFIED
            reason = "无任何本轮证据引用，不得作为稳定结论呈现；应视为待验证假设。"
        elif not known and not refs:
            state = CareerItemState.UNVERIFIED
            reason = "无任何证据引用，复核时未能核验。"
        elif unknown:
            state = CareerItemState.UNVERIFIED
            reason = "引用了本轮不存在的证据，复核时未能核验。"
        elif any(evidence_map[ref].stale for ref in known):
            state = CareerItemState.OUTDATED
            reason = "引用的来源已超过新鲜度阈值，结论可能过时，建议重新核查。"
        elif len(known) >= 2 and not _explains_relationship(item.note):
            state = CareerItemState.CONFLICTED
            reason = "引用多个证据但未说明证据间关系，暂不作稳定判断。"
        if category == "assumptions" and not _has_verification_step(item):
            # 待验证假设必须给出下一步核查方式（确定性门：缺口可见）。
            warnings.append(
                f"待验证假设条目 {item.item_id} 缺少下一步核查方式，"
                "已标注需要进一步核实。"
            )
        reviews.append(
            CareerReviewItem(
                item_id=item.item_id,
                category=category,
                state=state,
                reason=reason,
            )
        )

    passed = not boundary_violations
    return CareerReviewResult(
        passed=passed,
        reviews=reviews,
        boundary_violations=boundary_violations,
        warnings=warnings,
    )


def _explains_relationship(note: str | None) -> bool:
    """条目 note 是否已说明证据间关系。"""
    if not note:
        return False
    return any(word in note for word in _RELATIONSHIP_WORDS)


def _has_verification_step(item: CareerItemBase) -> bool:
    """待验证假设是否给出了下一步核查方式（确定性门）。"""
    step = getattr(item, "verification_next_step", None)
    return bool(step and str(step).strip())


def apply_review_states(
    output: CareerPlanningOutputContract,
    result: CareerReviewResult,
    *,
    now: datetime,
) -> None:
    """把复核结论回写条目（就地修改；review 只读，apply 才写）。

    每条条目填充服务端核查时间（``verified_at``），并把非已核实状态以
    中文说明追加到 ``note``——历史回答保留当时的复核快照。
    """
    by_item: dict[str, CareerReviewItem] = {
        review.item_id: review for review in result.reviews
    }
    for _, item in _iter_items(output):
        item.verified_at = now
        review = by_item.get(item.item_id)
        if review is None or review.state == CareerItemState.VERIFIED:
            continue
        prefix = (
            f"【{_state_cn(review.state)}】{review.reason}"
            if item.note is None
            else f"{item.note}（{_state_cn(review.state)}：{review.reason}）"
        )
        item.note = prefix


def _state_cn(state: CareerItemState) -> str:
    return {
        CareerItemState.VERIFIED: "已核实",
        CareerItemState.UNVERIFIED: "未核实",
        CareerItemState.CONFLICTED: "证据冲突",
        CareerItemState.OUTDATED: "来源过时",
    }[state]
