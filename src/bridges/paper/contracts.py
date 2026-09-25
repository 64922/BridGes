"""论文模块的公开合同（V2 Issue 11）。

编排合同（``.scratch/bridges-v2/issues/11-paper-search.md``）要求解析结果
记录**原文短语、规范化值、扩展词、置信度和最终查询**，并把「缺失或歧义只
问一项」的等待状态持久化。本模块把这些要求固化为投影类型：

- :class:`PaperTermAnalysis`：``paper.parse`` 的解析结果（原词逐字保留）；
- :class:`PaperQueryPlan`：``paper.plan`` 的实际查询与排序意图；
- :class:`PaperRecommendation`：``paper.rank`` 后带来源、全文可得性与选择
  理由的单篇结果（阅读顺序即 ``order``）；
- :class:`PaperSearchProjection`：随助手消息持久化的完整状态（查询词、工具
  记录、结果、证据边界、等待状态、失败与停止）。

对外（API/前端）只暴露这一份投影：用户既能看到实际用于检索的查询词，也能
看到未取得全文、来源不足等真实边界。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from bridges.contracts.modules import ModuleQueryRecord, ModuleWaitState


class PaperSearchStatus(StrEnum):
    """论文模块的用户可见状态（同一消息内如实显示）。"""

    CLARIFICATION = "clarification"
    SEARCHING = "searching"
    SUCCESS = "success"
    EMPTY = "empty"
    ERROR = "error"
    STOPPED = "stopped"


class PaperSortIntent(StrEnum):
    """用户表达的排序意图（``paper.plan`` 据此设置 arXiv 排序）。"""

    RELEVANCE = "relevance"
    LATEST = "latest"
    CLASSIC = "classic"
    BEGINNER = "beginner"


#: 阅读顺序中的角色（``paper.rank`` 判定、``paper.present`` 照原样展示）。
ROLE_SURVEY = "survey"
ROLE_TUTORIAL = "tutorial"
ROLE_FOUNDATION = "foundation"
ROLE_RECENT = "recent"

#: 角色 → 中文标签（排序与正文共用一份，避免两处各写一张表而走样）。
ROLE_LABELS: dict[str, str] = {
    ROLE_SURVEY: "综述",
    ROLE_TUTORIAL: "教程",
    ROLE_FOUNDATION: "奠基工作",
    ROLE_RECENT: "较新研究",
}


class PaperContextCandidate(BaseModel):
    """歧义术语的一个候选语境（澄清时逐项列出，用户回答后据此解析）。"""

    key: str = Field(description="稳定语境键，例如 machine_learning。")
    label: str = Field(description="中文候选说明，例如「机器学习中的 Transformer 结构」。")
    query_terms: tuple[str, ...] = Field(
        default_factory=tuple, description="该语境下用于检索的英文词（原词优先）。"
    )
    keywords: tuple[str, ...] = Field(
        default_factory=tuple, description="用户回答或前文中指向该语境的中文/英文词。"
    )


class PaperClarification(BaseModel):
    """一条待用户回答的澄清（每轮只问一项）。"""

    question: str = Field(description="向用户提出的那一个问题（中文，只问一项）。")
    missing: str = Field(description="缺失项分类：domain（语境）或 topic（主题）。")
    original_phrase: str = Field(description="用户原文中的原始短语（逐字保留）。")
    candidates: list[PaperContextCandidate] = Field(
        default_factory=list, description="歧义术语的候选语境（仅 domain 类澄清携带）。"
    )


class PaperConstraints(BaseModel):
    """本轮明确或可推断的检索限制。"""

    year_from: int | None = Field(default=None, description="起始年份（含）。")
    year_to: int | None = Field(default=None, description="结束年份（含）。")
    sort_intent: PaperSortIntent = Field(
        default=PaperSortIntent.RELEVANCE, description="排序意图（最新/经典/入门/相关）。"
    )
    prefer_survey: bool = Field(
        default=False, description="用户是否明确要综述/回顾论文（影响搭配角色）。"
    )


class PaperTermAnalysis(BaseModel):
    """``paper.parse`` 的解析结果。"""

    original_phrase: str = Field(description="用户原文中的原始术语（逐字保留，绝不替换）。")
    normalized_term: str = Field(description="规范化值（保留原词，仅做大小写/空白整理）。")
    expansions: list[str] = Field(
        default_factory=list, description="扩展词（译名/同义词/上位词），只作扩展，不替掉原词。"
    )
    confidence: float = Field(ge=0.0, le=1.0, description="解析置信度（0–1）。")
    context_key: str | None = Field(default=None, description="消歧后的语境键；未消歧为 None。")
    context_label: str | None = Field(default=None, description="消歧后语境的中文说明。")
    constraints: PaperConstraints = Field(
        default_factory=PaperConstraints, description="年份/类别/排序限制。"
    )
    final_query: str = Field(
        default="", description="用于检索的最终查询（原词优先）；澄清时为原始短语。"
    )
    clarification: PaperClarification | None = Field(
        default=None, description="缺失或歧义时的单一澄清问题；无需澄清为 None。"
    )


class PaperQueryPlan(BaseModel):
    """``paper.plan`` 生成的检索计划。"""

    query: str = Field(description="实际发送给 arXiv 的最小查询词（不含私有上下文）。")
    sort_by: str = Field(description="arXiv sortBy 参数值（relevance 意图用 submittedDate 兜底）。")
    sort_order: str = Field(description="arXiv sortOrder 参数值。")
    max_results: int = Field(description="本轮请求的候选数量上限。")
    target_count: int = Field(
        default=3, ge=1, description="本轮目标篇数（默认 3–5 篇）；实际数量不足时如实报数。"
    )
    expansions_used: list[str] = Field(
        default_factory=list, description="实际进入查询的扩展词（原词始终在查询中）。"
    )
    rationale: str = Field(description="中文说明：为什么这样构造查询（原词优先、扩展只作补充）。")


class PaperRecommendation(BaseModel):
    """一篇经主题与来源核对后的推荐（阅读顺序见 ``order``）。"""

    order: int = Field(ge=1, description="入门阅读顺序（从 1 开始）。")
    arxiv_id: str | None = Field(default=None, description="arXiv 标识符；非 arXiv 来源为 None。")
    title: str = Field(description="来源返回的原始标题。")
    authors: list[str] = Field(default_factory=list, description="来源返回的作者列表。")
    published_year: int | None = Field(default=None, description="来源返回的发表年份。")
    source: str = Field(description="元数据来源（arxiv / crossref / openalex）。")
    abs_url: str = Field(description="可点开的来源链接（摘要页）。")
    pdf_url: str | None = Field(default=None, description="全文 PDF 链接；未取得为 None。")
    primary_category: str | None = Field(default=None, description="来源返回的主类别。")
    full_text_available: bool = Field(
        default=False, description="是否确认取得全文（仅摘要时为 False）。"
    )
    role: str = Field(description="在阅读顺序中的角色：survey / foundation / recent / tutorial。")
    reason_zh: str = Field(description="中文选择理由（含搭配与顺序依据）。")
    match_basis: str = Field(description="主题与来源核对依据（命中哪些词、来源核对了什么）。")
    summary_zh: str | None = Field(
        default=None, description="基于来源摘要的中文概述；未生成时为 None（不虚构）。"
    )
    unverified: list[str] = Field(
        default_factory=list, description="本篇未核实项（例如未通读全文、缺发表信息）。"
    )


class PaperSearchProjection(BaseModel):
    """论文模块随助手消息持久化的完整投影。"""

    status: PaperSearchStatus = Field(description="本轮模块状态。")
    original_phrase: str = Field(default="", description="保留的原始术语。")
    normalized_term: str = Field(default="", description="规范化值。")
    expansions: list[str] = Field(default_factory=list, description="本轮使用的扩展词。")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="解析置信度。")
    context_label: str | None = Field(default=None, description="消歧后的语境说明。")
    queries: list[ModuleQueryRecord] = Field(
        default_factory=list, description="本轮全部外部调用的统一记录（查询/证据/时间/错误）。"
    )
    final_query: str = Field(default="", description="实际用于检索的最终查询词。")
    papers: list[PaperRecommendation] = Field(
        default_factory=list, description="按阅读顺序排列的真实论文结果。"
    )
    requested_count: int = Field(default=0, ge=0, description="本轮目标篇数（3–5）。")
    evidence_notes: list[str] = Field(
        default_factory=list, description="证据边界说明（实际数量、来源不足、未取全文等）。"
    )
    pending: ModuleWaitState | None = Field(
        default=None, description="跨轮次等待状态（澄清问题）；无等待为 None。"
    )
    searched_at: datetime | None = Field(default=None, description="检索完成时间。")
    error_code: str | None = Field(default=None, description="失败分类码。")
    error_message: str | None = Field(default=None, description="可操作的中文错误说明。")
    retryable: bool = Field(default=False, description="本轮失败是否可重试。")
