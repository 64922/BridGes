"""``paper.present``：带来源的中文结果、阅读顺序与证据边界。

正文由**真实证据渲染**（查询词、实际篇数、每篇链接与选择理由、未核实项），
不依赖模型即保证零虚构。可选的中文概述由模型生成，但受严格门控：只接受本
轮真实候选中出现的 arXiv 标识、长度受限、空值丢弃；模型不可用或输出不合规
时省略概述并如实说明，绝不补造。

任何失败、空结果与停止都在同一消息里显示真实状态（``无数据`` 表示确实没有取得）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bridges.contracts.ai import ModelCallResult, ModelRunLock
from bridges.paper.contracts import (
    ROLE_LABELS,
    PaperQueryPlan,
    PaperRecommendation,
    PaperSearchProjection,
    PaperTermAnalysis,
)

#: 中文概述的长度上限（防止把摘要整段搬进来或生成失控长文）。
SUMMARY_MAX_CHARS = 160

SUMMARY_SYSTEM_PROMPT = (
    "你是学术检索助手。只依据给定的论文标题、摘要与类别写中文概述，"
    "不得引入未给出的结论、数字、对比或引用；不确定就不写。"
    "每篇概述不超过 120 字，用一句话说明它解决什么问题、用了什么方法。"
)

SUMMARY_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summaries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "arxiv_id": {"type": "string"},
                    "summary_zh": {"type": "string"},
                },
                "required": ["arxiv_id", "summary_zh"],
            },
        }
    },
    "required": ["summaries"],
}


@dataclass(frozen=True)
class SummaryOutcome:
    """概述生成结果：按 arxiv_id 的概述、说明与模型锁（供原子落库）。"""

    summaries: dict[str, str] = field(default_factory=dict)
    note: str | None = None
    lock: ModelRunLock | None = None
    dropped: int = 0


def render_clarification_content(analysis: PaperTermAnalysis) -> str:
    clarification = analysis.clarification
    if clarification is None:
        return ""
    return clarification.question


def render_result_content(
    analysis: PaperTermAnalysis,
    plan: PaperQueryPlan,
    projection: PaperSearchProjection,
) -> str:
    """成功结果的正文：查询词、阅读顺序、每篇链接与理由、证据边界。"""
    lines: list[str] = []
    term_note = ""
    if analysis.original_phrase and analysis.original_phrase != plan.query:
        term_note = f"（原始术语：{analysis.original_phrase}）"
    lines.append(
        f"已按论文模块检索 arXiv{term_note}，实际查询词：{plan.query}。"
    )
    if analysis.context_label:
        lines.append(f"语境：{analysis.context_label}。")
    lines.append("")
    lines.append(f"共 {len(projection.papers)} 篇，按入门阅读顺序：")
    for paper in projection.papers:
        role_label = ROLE_LABELS.get(paper.role, "相关研究")
        lines.append("")
        lines.append(f"{paper.order}. 《{paper.title}》（{paper.published_year}，{role_label}）")
        lines.append(f"   选择理由：{paper.reason_zh}")
        if paper.summary_zh:
            lines.append(f"   中文概述：{paper.summary_zh}")
        lines.append(f"   来源：{paper.abs_url}")
        if paper.full_text_available and paper.pdf_url:
            lines.append(f"   全文：{paper.pdf_url}")
        else:
            lines.append("   全文：未确认可获取的开放全文链接，本轮只核对标题与摘要。")
    if projection.evidence_notes:
        lines.append("")
        lines.append("证据边界：")
        lines.extend(f"- {note}" for note in projection.evidence_notes)
    return "\n".join(lines)


def render_empty_content(
    analysis: PaperTermAnalysis, plan: PaperQueryPlan, notes: list[str]
) -> str:
    del analysis
    lines = [
        f"已按论文模块检索 arXiv，实际查询词：{plan.query}。",
        "arXiv 没有返回结果，我没有可推荐的论文，也不会用记忆补造条目。",
    ]
    lines.extend(f"- {note}" for note in notes)
    lines.append("可以补充英文术语、放宽年份或换个相近方向后重试。")
    return "\n".join(lines)


def render_mismatch_content(
    analysis: PaperTermAnalysis, plan: PaperQueryPlan, notes: list[str]
) -> str:
    lines = [
        f"实际查询词：{plan.query}（原始术语：{analysis.original_phrase}）。",
        "检索回来的论文标题与摘要都没有覆盖你的原始术语，因此我停止推荐，没有凑篇数。",
    ]
    lines.extend(f"- {note}" for note in notes)
    lines.append("请补充你要的领域或纠正术语（例如说明是机器学习还是电力系统），我再检索。")
    return "\n".join(lines)


def render_stopped_content(
    analysis: PaperTermAnalysis, plan: PaperQueryPlan | None
) -> str:
    query = plan.query if plan is not None else (analysis.final_query or analysis.original_phrase)
    return f"论文检索已停止，实际查询词：{query}。已完成的步骤保留在本条消息内。"


class PaperSummaryGenerator:
    """可选的中文概述生成：严格门控，模型不得引入候选之外的内容。"""

    def __init__(
        self,
        gateway: Any,
        *,
        capability_name: str = "qwen_structured_output",
        capability_version: str = "1",
    ) -> None:
        self._gateway = gateway
        self._capability_name = capability_name
        self._capability_version = capability_version

    def generate(
        self,
        run_context: Any,
        papers: list[PaperRecommendation],
        *,
        abstracts: dict[str, str],
        model_id: str | None,
    ) -> SummaryOutcome:
        if not papers:
            return SummaryOutcome()
        payload = {
            "messages": [
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": _summary_prompt(papers, abstracts)},
            ],
            "json_schema": SUMMARY_JSON_SCHEMA,
            "temperature": 0.3,
            "max_tokens": 1200,
        }
        try:
            result: ModelCallResult = self._gateway.invoke(
                self._capability_name,
                self._capability_version,
                run_context,
                payload,
                model_override=model_id,
            )
        except Exception:  # noqa: BLE001 - 概述失败不影响真实结果呈现
            return SummaryOutcome(note="中文概述生成失败（模型调用异常），本轮只给来源元数据。")
        lock = result.lock
        if result.status.value not in {"success", "degraded"}:
            return SummaryOutcome(
                note="中文概述未生成（模型能力不可用或失败），本轮只给来源元数据与原始摘要依据。",
                lock=lock,
            )
        allowed = {paper.arxiv_id: paper for paper in papers if paper.arxiv_id}
        summaries: dict[str, str] = {}
        dropped = 0
        for item in (result.output or {}).get("summaries", []):
            if not isinstance(item, dict):
                dropped += 1
                continue
            arxiv_id = str(item.get("arxiv_id") or "")
            text = str(item.get("summary_zh") or "").strip()
            # 门控：只接受本轮真实候选的标识；空值/超长/未知标识一律丢弃。
            if arxiv_id not in allowed or not text or len(text) > SUMMARY_MAX_CHARS:
                dropped += 1
                continue
            summaries[arxiv_id] = text
        note = None
        if dropped:
            note = f"中文概述中有 {dropped} 条不符合证据约束（标识或长度），已丢弃。"
        return SummaryOutcome(summaries=summaries, note=note, lock=lock, dropped=dropped)


def _summary_prompt(
    papers: list[PaperRecommendation], abstracts: dict[str, str]
) -> str:
    lines = ["请为下列论文各写一句中文概述（只依据给出的标题与摘要）："]
    for paper in papers:
        lines.append(f"- arxiv_id: {paper.arxiv_id}")
        lines.append(f"  标题: {paper.title}")
        lines.append(f"  类别: {paper.primary_category or '未标注'}")
        abstract = (abstracts.get(paper.arxiv_id or "") or "").strip()
        lines.append(f"  摘要: {abstract[:1200] if abstract else '来源未返回摘要'}")
    lines.append('输出 JSON：{"summaries": [{"arxiv_id": "...", "summary_zh": "..."}]}')
    return "\n".join(lines)
