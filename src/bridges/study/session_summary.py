"""阶段小结的确定性依据与原文生成（Issue 04）。

小结只使用实际学习记录：本次验证（即时反馈）、尚未核验/待确认项与
可选下一步。正式诊断由后续独立切片提供；在本票中所有条目都是即时
反馈或待确认，不输出掌握百分比、正确率或长期能力判断。规范原文在
规划时确定性生成并随本轮回复交付，终态事务按实际证据重建依据引用，
不随后续学习覆盖。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: 每节最多展示的条目数；超出时只补充总数，保持小结简短。
_SECTION_LIMIT = 4
#: 单条问题在正文中的展示上限。
_QUESTION_LIMIT = 120

#: 证据语义 → 小结分节与表述（语义是实际记录，不是能力等级）。
_IMMEDIATE_LABELS: dict[str, str] = {
    "independent_completion": "本次独立完成",
    "hinted_completion": "在提示后完成，本次不属于独立完成",
    "partial_understanding": "只完成了部分推理环节，导师已即时引导",
    "misconception": "出现具体理解偏差，导师已即时纠正",
}
_PENDING_LABELS: dict[str, str] = {
    "undetermined": "本次回答无法判断，保持待确认",
    "unverified": "未作答，保持未验证；不计为答错、掌握或缺口",
    "understanding_clue": "只有开放性理解线索，尚未形成正式判断",
}


@dataclass(frozen=True)
class SummaryEvidenceItem:
    """一条进入小结的学习记录；来源区分先前证据与本轮规划结果。"""

    evidence_id: str
    question: str
    kind: str
    semantics: str
    source: str = "prior_evidence"


def build_summary_basis(
    *,
    trigger: str,
    items: list[SummaryEvidenceItem],
    pending_after: dict[str, Any] | None,
    progress: dict[str, Any] | None,
    generated_at: str,
) -> dict[str, Any]:
    """冻结小结的生成输入与依据引用（在规划时写入运行快照）。"""

    return {
        "contract_version": "study-summary-v1",
        "trigger": trigger,
        "generated_at": generated_at,
        "items": [
            {
                "evidence_id": item.evidence_id,
                "question": _short(item.question, _QUESTION_LIMIT),
                "kind": item.kind,
                "semantics": item.semantics,
                "source": item.source,
            }
            for item in items[:30]
        ],
        "pending_check": (
            {
                "check_id": str(pending_after.get("check_id") or ""),
                "question": _short(
                    str(pending_after.get("question") or ""), _QUESTION_LIMIT
                ),
                "version": int(pending_after.get("version") or 0),
            }
            if isinstance(pending_after, dict)
            else None
        ),
        "progress": (
            {
                "version": int(progress.get("version") or 0),
                "topic": str(progress.get("topic") or ""),
                "current_focus": str(progress.get("current_focus") or ""),
            }
            if isinstance(progress, dict)
            else None
        ),
        # Issue 05 起由独立后台诊断填充；未提供时小结如实保持待确认。
        "formal_diagnosis": {"status": "not_available", "items": []},
    }


def render_summary_content(basis: dict[str, Any]) -> str:
    """由冻结依据确定性生成规范小结原文（不依赖模型改写）。"""

    items = [
        item for item in basis.get("items", []) if isinstance(item, dict)
    ]
    immediate = [
        item for item in items if meaningful_semantics(item) in _IMMEDIATE_LABELS
    ]
    pending = [
        item for item in items if meaningful_semantics(item) in _PENDING_LABELS
    ]
    lines = ["【本阶段小结】"]
    lines.append("本次验证（即时反馈）：")
    lines.extend(_section_lines(immediate, _IMMEDIATE_LABELS, "本次没有已完成验证的作答记录。"))
    lines.append("仍待确认：")
    lines.extend(_section_lines(pending, _PENDING_LABELS, "本次没有未验证或无法判断的内容。"))
    lines.append(
        "依据说明：本小结只依据本次对话的实际作答与提问记录；"
        "独立诊断尚未提供，以上即时反馈不代表长期掌握，"
        "单次完成只描述本次表现。"
    )
    lines.append("可选下一步：")
    lines.extend(_next_step_lines(basis))
    return "\n".join(lines)


def summary_display_status(content: str, reply: str) -> str:
    """小结原文是否完整随回复交付：逐行核验，缺失任意一行即 partial。"""

    normalized_reply = " ".join(reply.split())
    required = [line for line in content.splitlines() if line.strip()]
    if required and all(" ".join(line.split()) in normalized_reply for line in required):
        return "complete"
    return "partial"


def semantics_verification(semantics: str) -> str:
    """证据语义 → 小结核验状态标签（即时反馈/无法判断/尚未核验）。"""

    if semantics in _IMMEDIATE_LABELS:
        return "immediate_feedback"
    if semantics == "undetermined":
        return "undetermined"
    if semantics == "understanding_clue":
        return "understanding_clue"
    return "unverified"


def meaningful_semantics(item: dict[str, Any]) -> str:
    """证据语义直接使用；未作答记录在写入时已记为未验证。"""

    return str(item.get("semantics") or "")


def _section_lines(
    items: list[dict[str, Any]],
    labels: dict[str, str],
    empty: str,
) -> list[str]:
    if not items:
        return [f"- {empty}"]
    lines = [
        f"- 「{_short(str(item.get('question') or ''), _QUESTION_LIMIT)}」："
        f"{labels[meaningful_semantics(item)]}。"
        for item in items[:_SECTION_LIMIT]
    ]
    if len(items) > _SECTION_LIMIT:
        lines.append(f"- 另有 {len(items) - _SECTION_LIMIT} 项同类记录未逐条列出。")
    return lines


def _next_step_lines(basis: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    pending = basis.get("pending_check")
    if isinstance(pending, dict) and pending.get("question"):
        lines.append(
            "可以继续回答当前待答问题"
            f"「{_short(str(pending['question']), _QUESTION_LIMIT)}」；"
            "也可以要求「直接完整讲解」或说「跳过」，未验证不会阻塞继续学习。"
        )
    else:
        lines.append("可以继续当前内容；也可以直接提出下一个问题。")
    lines.append("想结束时说「今天先到这里」即可暂停，进展会保存。")
    return lines


def _short(value: str, limit: int) -> str:
    return " ".join(str(value).split())[:limit]
