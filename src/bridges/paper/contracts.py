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

from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from bridges.contracts.modules import ModuleQueryRecord, ModuleQueryStatus, ModuleWaitState


class PaperSearchStatus(StrEnum):
    """论文模块的用户可见状态（同一消息内如实显示）。"""

    CLARIFICATION = "clarification"
    SEARCHING = "searching"
    SUCCESS = "success"
    EMPTY = "empty"
    ERROR = "error"
    STOPPED = "stopped"


class PaperReadScope(StrEnum):
    """一篇结果在本轮实际取得的阅读范围（工单 24：断言强度与读取范围一致）。"""

    ABSTRACT = "abstract"
    PARTIAL = "partial"
    FULL_TEXT = "full_text"


class PaperSortIntent(StrEnum):
    """用户表达的排序意图（``paper.plan`` 据此设置 arXiv 排序）。"""

    RELEVANCE = "relevance"
    LATEST = "latest"
    CLASSIC = "classic"
    BEGINNER = "beginner"


class PaperRelevance(StrEnum):
    """相关程度只描述需求覆盖，不代表论文质量。"""

    DIRECT = "direct"
    PARTIAL = "partial"
    EXPLORATORY = "exploratory"


#: 阅读顺序中的角色（``paper.rank`` 判定、``paper.present`` 照原样展示）。
ROLE_SURVEY = "survey"
ROLE_TUTORIAL = "tutorial"
ROLE_FOUNDATION = "foundation"
ROLE_RECENT = "recent"
ROLE_RELATED = "related"

#: 角色 → 中文标签（排序与正文共用一份，避免两处各写一张表而走样）。
ROLE_LABELS: dict[str, str] = {
    ROLE_SURVEY: "综述",
    ROLE_TUTORIAL: "教程",
    ROLE_FOUNDATION: "奠基工作",
    ROLE_RECENT: "较新研究",
    ROLE_RELATED: "相关研究",
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
    requested_count: int | None = Field(default=None, ge=1, description="用户明确要求的篇数。")
    sort_intent: PaperSortIntent = Field(
        default=PaperSortIntent.RELEVANCE, description="排序意图（最新/经典/入门/相关）。"
    )
    prefer_survey: bool = Field(
        default=False, description="用户是否明确要综述/回顾论文（影响搭配角色）。"
    )
    exclude_survey: bool = Field(default=False, description="用户是否明确排除综述/回顾论文。")
    arxiv_id: str | None = Field(default=None, description="指定论文的 arXiv 身份。")
    paper_title: str | None = Field(default=None, description="指定论文原始标题。")
    allowed_sources: list[str] = Field(default_factory=list, description="明确允许的来源。")
    excluded_sources: list[str] = Field(default_factory=list, description="明确禁止的来源。")
    unsupported_sources: list[str] = Field(
        default_factory=list, description="用户限定但本版未登记的来源。"
    )


class PaperResearchConcept(BaseModel):
    """研究请求中的概念与可追溯的英文检索表达。"""

    concept_id: str = Field(
        default="",
        description="当前分析内由代码分配的唯一稳定标识；模型不生成该标识。",
    )
    concept: str = Field(description="研究概念的简洁说明。")
    source_phrase: str = Field(description="用户原话或有效任务条件中的原始片段。")
    kind: str = Field(description="object/question/method/application/population/condition。")
    english_search_terms: list[str] = Field(
        default_factory=list, description="适合国际学术来源查询的英文概念与同义表达。"
    )


class PaperConditionSource(BaseModel):
    """硬条件与用户原话片段的对应。"""

    kind: str = Field(description="year/count/source/paper/type。")
    value: str = Field(description="由代码解析出的条件值。")
    source_phrase: str = Field(description="原请求中支持该条件的片段。")


class PaperTermAnalysis(BaseModel):
    """``paper.parse`` 的解析结果。"""

    original_phrase: str = Field(description="用户原文中的原始术语（逐字保留，绝不替换）。")
    original_request: str = Field(default="", description="本轮完整用户请求。")
    normalized_term: str = Field(description="规范化值（保留原词，仅做大小写/空白整理）。")
    expansions: list[str] = Field(
        default_factory=list, description="模型从研究意图推导的英文同义表达与相关概念。"
    )
    confidence: float = Field(ge=0.0, le=1.0, description="解析置信度（0–1）。")
    context_key: str | None = Field(default=None, description="消歧后的语境键；未消歧为 None。")
    context_label: str | None = Field(default=None, description="消歧后语境的中文说明。")
    research_object: str = Field(default="", description="研究对象。")
    research_question: str = Field(default="", description="研究问题或目标。")
    methods: list[str] = Field(default_factory=list, description="请求中涉及的方法。")
    applications: list[str] = Field(default_factory=list, description="请求中涉及的场景或应用。")
    reading_purpose: str = Field(default="", description="找候选、主题探索、解释或比较等阅读目的。")
    reading_purpose_inferred: bool = Field(default=False, description="阅读目的是否由模型推断。")
    concepts: list[PaperResearchConcept] = Field(default_factory=list)
    recommended_sources: list[str] = Field(
        default_factory=list, description="模型按领域提出的来源建议；仍由代码核对可用性。"
    )
    condition_sources: list[PaperConditionSource] = Field(default_factory=list)
    clarification_missing: str | None = Field(default=None)
    constraints: PaperConstraints = Field(
        default_factory=PaperConstraints, description="年份/类别/排序限制。"
    )
    final_query: str = Field(
        default="", description="模型从完整研究意图生成的检索查询；澄清时为原始短语。"
    )
    clarification: PaperClarification | None = Field(
        default=None, description="缺失或歧义时的单一澄清问题；无需澄清为 None。"
    )


class PaperQueryPlan(BaseModel):
    """``paper.plan`` 生成的检索计划。"""

    query: str = Field(description="实际发送给该学术来源的最小查询词。")
    source: str = Field(default="arxiv", description="执行该查询的已登记来源。")
    sort_by: str = Field(description="来源支持时采用的排序偏好；其余排序由本地结果阶段完成。")
    sort_order: str = Field(description="来源支持时采用的排序方向；不支持时忽略。")
    max_results: int = Field(description="本轮请求的候选数量上限。")
    target_count: int = Field(
        default=8, ge=1, description="重点推荐目标篇数（默认 5–8 篇）；实际不足时如实交付。"
    )
    expansions_used: list[str] = Field(
        default_factory=list, description="本轮实际进入来源查询的研究概念表达。"
    )
    rationale: str = Field(description="说明查询如何覆盖研究对象、问题、方法和应用概念。")


class PaperRequirementEvidence(BaseModel):
    """一条「需求 → 证据」对应（工单 24：专业角色判断语义相关性并给出片段）。"""

    requirement: str = Field(description="本研究需求（原词/语境/扩展/阅读目的之一）。")
    source: str = Field(description="证据来源：title / abstract / full_text:<小节>。")
    quote: str = Field(default="", max_length=400, description="支持该需求的来源片段（截断展示）。")


class PaperRecommendation(BaseModel):
    """一篇经主题与来源核对后的推荐（阅读顺序见 ``order``）。"""

    order: int = Field(ge=1, description="入门阅读顺序（从 1 开始）。")
    candidate_id: str = Field(default="", description="跨来源融合后的稳定候选身份。")
    arxiv_id: str | None = Field(default=None, description="arXiv 标识符；非 arXiv 来源为 None。")
    doi: str | None = Field(default=None, description="Crossref/OpenAlex 核对的 DOI；缺失为 None。")
    title: str = Field(description="来源返回的原始标题。")
    authors: list[str] = Field(default_factory=list, description="来源返回的作者列表。")
    published_year: int | None = Field(default=None, description="来源返回的发表年份。")
    source: str = Field(description="元数据来源（arxiv / crossref / openalex）。")
    sources: list[str] = Field(default_factory=list, description="实际提供该论文记录的来源。")
    source_ids: list[str] = Field(default_factory=list, description="融合记录中的来源原生标识。")
    venue: str | None = Field(default=None, description="核对的发表venue；缺失为 None。")
    cited_by_count: int | None = Field(
        default=None, description="核对的引用量；仅作辅助信息，不代表质量或入门性。"
    )
    abs_url: str = Field(description="可点开的来源链接（摘要页）。")
    pdf_url: str | None = Field(default=None, description="全文 PDF 链接；未取得为 None。")
    full_text_url: str | None = Field(
        default=None, description="元数据核对的开放全文链接；未核对为 None。"
    )
    primary_category: str | None = Field(default=None, description="来源返回的主类别。")
    full_text_available: bool = Field(
        default=False, description="是否有已核对的全文链接；本轮实际读取范围见 read_scope。"
    )
    read_scope: PaperReadScope = Field(
        default=PaperReadScope.ABSTRACT, description="本轮实际阅读范围（摘要/部分正文/全文）。"
    )
    role: str = Field(description="在阅读顺序中的角色：survey / foundation / recent / tutorial。")
    relevance: PaperRelevance = Field(default=PaperRelevance.DIRECT)
    research_group: str = Field(default="", description="按子问题或方法路线分组。")
    reason_zh: str = Field(description="中文选择理由（含搭配与顺序依据）。")
    match_basis: str = Field(description="主题与来源核对依据（命中哪些词、来源核对了什么）。")
    match_evidence: list[PaperRequirementEvidence] = Field(
        default_factory=list, description="逐条需求对应的标题/摘要/正文证据。"
    )
    supported_claims: list[str] = Field(
        default_factory=list, description="有正文依据支持的断言（方法/实验/局限/复现）。"
    )
    summary_zh: str | None = Field(
        default=None, description="基于来源摘要的中文概述；未生成时为 None（不虚构）。"
    )
    summary_evidence: PaperRequirementEvidence | None = Field(
        default=None,
        description="概述绑定的来源逐字证据（标题/摘要片段）；无证据的概述不保留。",
    )
    source_abstract: str = Field(default="", description="来源实际摘要，供证据复核。")
    read_evidence: list[PaperRequirementEvidence] = Field(
        default_factory=list, description="实际正文片段及小节定位，供断言核验。"
    )
    unverified: list[str] = Field(
        default_factory=list, description="本篇未核实项（例如未通读全文、缺发表信息）。"
    )


class PaperIdentity(BaseModel):
    """选定论文的可核实身份（工单 24：供后续模块精确引用，不靠自然语言猜）。"""

    order: int = Field(ge=1, description="阅读顺序。")
    candidate_id: str = Field(default="", description="跨来源融合后的稳定候选身份。")
    arxiv_id: str | None = Field(default=None, description="arXiv 标识符。")
    doi: str | None = Field(default=None, description="DOI（经 Crossref/OpenAlex 核对）。")
    title: str = Field(description="来源原始标题。")
    published_year: int | None = Field(default=None, description="发表年份。")
    abs_url: str = Field(description="摘要页链接。")
    content_hash: str = Field(default="", description="产物身份哈希（内容寻址，便于恢复核验）。")


class PaperCandidateProjection(BaseModel):
    """完整候选列表中的单篇论文摘要投影。"""

    candidate_id: str = Field(description="跨来源融合后的稳定候选身份。")
    arxiv_id: str | None = Field(default=None)
    doi: str | None = Field(default=None)
    title: str
    authors: list[str] = Field(default_factory=list)
    published_year: int | None = Field(default=None)
    primary_source: str
    sources: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    abs_url: str
    pdf_url: str | None = Field(default=None, description="全文链接；不代表本轮已读取。")
    full_text_url: str | None = Field(
        default=None, description="已核对的开放全文链接；不代表本轮已读取。"
    )
    abstract: str = Field(default="")
    relevance: PaperRelevance = Field(default=PaperRelevance.DIRECT)
    research_group: str = Field(default="")
    match_evidence: list[PaperRequirementEvidence] = Field(default_factory=list)
    discovery_paths: list[str] = Field(default_factory=list)


class PaperCitationEdge(BaseModel):
    """来自真实 OpenAlex 引用记录的一条有向关系。"""

    citing_candidate_id: str
    cited_candidate_id: str
    citing_title: str
    cited_title: str
    source: str = "openalex"


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
        default_factory=list, description="按阅读顺序排列的重点推荐（默认 5–8 篇）。"
    )
    candidates: list[PaperCandidateProjection] = Field(
        default_factory=list, description="通过硬条件与证据门的完整候选列表。"
    )
    citation_edges: list[PaperCitationEdge] = Field(
        default_factory=list, description="真实取得的引用边；为空时不提供引用图。"
    )
    research_question: str = Field(default="", description="本轮理解后的研究问题。")
    selected_sources: list[str] = Field(
        default_factory=list,
        description="本轮实际调用过的来源（含失败与空结果，不含未执行/跳过）。",
    )
    condition_sources: list[PaperConditionSource] = Field(
        default_factory=list, description="本轮硬条件及其来自请求/任务条件的原话片段。"
    )
    selected: list[PaperIdentity] = Field(
        default_factory=list, description="选定论文的可核实身份列表（供 GitHub 等精确引用）。"
    )
    requested_count: int = Field(default=0, ge=0, description="本轮重点推荐目标篇数。")
    artifacts: dict[str, str] = Field(
        default_factory=dict, description="本轮持久节点产物引用（节点名 → 产物 ID，可追溯/恢复）。"
    )
    expression_policy_version: str | None = Field(
        default=None, description="本轮用户可见概述采用的表达策略版本（未生成概述为 None）。"
    )
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


def selected_sources(records: Sequence[ModuleQueryRecord]) -> list[str]:
    """从真实调用记录派生实际来源（含失败/空结果，排除跳过）。"""
    return list(
        dict.fromkeys(
            record.source
            for record in records
            if record.query and record.status is not ModuleQueryStatus.SKIPPED
        )
    )
