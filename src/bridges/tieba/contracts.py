"""贴吧模块的内部类型与对外投影。

``TiebaResearchProjection`` 是唯一暴露给 API 与前端的结果类型：它只包含
真实发生的查询、真实读到的文本与如实的失败原因，不包含内部日志或缓存细节。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from bridges.contracts.modules import ModuleQueryRecord, ModuleWaitState
from bridges.tieba.lexicon import TARGET_FORUM_NAME


class TiebaResearchStatus(StrEnum):
    """一轮贴吧信息搜集的终态。

    ``SUCCESS`` 只表示真的读到了确认属于目标贴吧的帖子；只拿到候选帖链时
    是 ``LINKS_ONLY``（如实标注归属未确认、未取得回复内容），检索完全没有
    可用候选才是 ``EMPTY``。
    """

    CLARIFICATION = "clarification"
    SEARCHING = "searching"
    SUCCESS = "success"
    LINKS_ONLY = "links_only"
    EMPTY = "empty"
    ERROR = "error"
    STOPPED = "stopped"


class TiebaClarification(BaseModel):
    """缺失关键信息时只问一项，并把恢复载荷写回消息。"""

    question: str = Field(description="唯一要问的中文问题。")
    reason: str = Field(description="为什么要先问这一项。")


class TiebaQuestionKind(StrEnum):
    """问题类型：规定、体验与混合（决定取证顺序与来源范围）。

    ``POLICY`` 以学校官方规定为第一来源（贴吧提供实际办理经历）；
    ``EXPERIENCE`` 以目标贴吧的真实帖子与回复为主；``MIXED`` 两路独立
    取证后再综合。类型只由确定性词表判定，不调用模型推断。
    """

    POLICY = "policy"
    EXPERIENCE = "experience"
    MIXED = "mixed"


KIND_LABELS: dict[TiebaQuestionKind, str] = {
    TiebaQuestionKind.POLICY: "规定类",
    TiebaQuestionKind.EXPERIENCE: "体验类",
    TiebaQuestionKind.MIXED: "混合类",
}


class TiebaEvidenceRoute(StrEnum):
    """两条独立取证路径：贴吧公开帖子与学校官方页面。"""

    TIEBA = "tieba"
    OFFICIAL = "official"


class TiebaQuestionAnalysis(BaseModel):
    """``tieba.parse`` 的结构化结果（保留原话，只做确定性标注）。"""

    original_question: str = Field(description="用户原始问题，逐字保留。")
    topic_terms: list[str] = Field(default_factory=list, description="检索用原始名词。")
    place_or_event: list[str] = Field(default_factory=list, description="事件／地点原始名词。")
    campus_terms: list[str] = Field(
        default_factory=list, description="问题里点名的校区／区域原词。"
    )
    question_kind: TiebaQuestionKind = Field(
        default=TiebaQuestionKind.EXPERIENCE,
        description="规定／体验／混合分类（确定性词表判定）。",
    )
    experience_signals: list[str] = Field(
        default_factory=list, description="判为体验侧的原始信号词。"
    )
    time_requirement: str | None = Field(
        default=None, description="问题里的时间条件原话；没有为 None。"
    )
    time_year: int | None = Field(default=None, description="时间条件里的绝对年份。")
    needs_official_check: bool = Field(
        default=False, description="是否涉及校规／费用／开放时间／办事流程／放假安排。"
    )
    official_topics: list[str] = Field(
        default_factory=list, description="触发官方核验的原始名词与触发词。"
    )
    clarification: TiebaClarification | None = Field(
        default=None, description="需要用户补一项时的澄清问题。"
    )


class TiebaEvidencePlan(BaseModel):
    """``tieba.plan`` 的取证计划：来源顺序、并行关系与查询范围。"""

    contract_version: str = Field(default="tieba-evidence-plan-v1")
    question_kind: TiebaQuestionKind = Field(description="问题类型（规定／体验／混合）。")
    routes: list[TiebaEvidenceRoute] = Field(
        default_factory=list, description="本轮实际登记的取证路径。"
    )
    source_priority: list[TiebaEvidenceRoute] = Field(
        default_factory=list, description="综合时的来源优先级（官方在前或贴吧在前）。"
    )
    parallel_evidence: bool = Field(
        default=False, description="两路是否相互独立、在预算内并行取证。"
    )
    official_required: bool = Field(default=False, description="本轮是否必须核对官方页面。")
    official_blocked_reason: str | None = Field(
        default=None, description="官方路径被用户硬条件阻止时的原因；未阻止为 None。"
    )
    check_requirements: list[str] = Field(
        default_factory=list, description="本轮要核实的事项（面向用户的中文说明）。"
    )
    tieba_queries: list[str] = Field(default_factory=list, description="贴吧路径的有界查询词。")
    official_queries: list[str] = Field(
        default_factory=list, description="官方路径的有界查询词（受限时为省略）。"
    )
    rationale: str = Field(default="", description="为什么这样安排来源顺序的中文说明。")


class TiebaSearchHit(BaseModel):
    """允许的搜索服务返回的候选（尚未确认真属目标贴吧）。"""

    url: str = Field(description="候选帖链接。")
    title: str = Field(description="搜索服务给出的标题（未核实页面）。")
    snippet: str = Field(default="", description="搜索摘要（只作排除他吧的证据使用）。")
    thread_id: str | None = Field(default=None, description="帖子 ID；非帖子链接为 None。")


class TiebaRejectedCandidate(BaseModel):
    """被剔除的候选与剔除理由（他吧同名帖必须留下痕迹，不静默丢弃）。"""

    url: str
    title: str
    evidence: str = Field(description="剔除依据的中文说明。")


class ReadStatus(StrEnum):
    """单个帖子的真实读取结果分类。"""

    READ = "read"
    PARTIAL = "partial"
    ACCESS_RESTRICTED = "access_restricted"
    UNRECOGNIZED = "unrecognized"
    NOT_FOUND = "not_found"
    TIMEOUT = "timeout"
    ERROR = "error"
    CANCELLED = "cancelled"


class TiebaReply(BaseModel):
    """真实读到的楼层；时间与楼层按页面原文记录。"""

    floor: int | None = Field(default=None, description="楼层号；页面未给出为 None。")
    posted_at: str | None = Field(default=None, description="页面上的发帖时间原文。")
    is_original_poster: bool = Field(default=False, description="是否楼主本人发言。")
    content: str = Field(description="楼层正文（页面原文，已去标签）。")


class TiebaReadResult(BaseModel):
    """一次帖子读取的真实范围与结果。"""

    url: str
    thread_id: str | None = None
    status: ReadStatus
    forum_name: str | None = Field(default=None, description="页面自身声明所属贴吧。")
    title: str | None = Field(default=None, description="页面上的帖子标题。")
    pages_read: int = Field(default=0, description="实际读取成功的页数。")
    pages_limit: int = Field(default=0, description="本轮允许读取的最大页数。")
    total_pages: int | None = Field(default=None, description="页面声明的总页数上限。")
    floor_min: int | None = Field(default=None, description="实际读到的最小楼层。")
    floor_max: int | None = Field(default=None, description="实际读到的最大楼层。")
    replies: list[TiebaReply] = Field(default_factory=list, description="真实读到的楼层。")
    error_code: str | None = Field(default=None, description="失败分类码。")
    error_message: str | None = Field(default=None, description="可操作的中文失败说明。")
    retrieved_at: datetime = Field(description="读取时间。")

    @property
    def forum_matches_target(self) -> bool:
        """页面自身的吧名是否确为目标贴吧（唯一的归属确认来源）。"""
        return (self.forum_name or "").strip() == TARGET_FORUM_NAME


class TiebaOfficialApplicability(BaseModel):
    """一条官方证据与问题的适用性核对（主体／校区／用途／日期）。

    官方域名只证明页面属于学校，不证明它适用于本次问题；只有主体、点名
    校区（若有）、用途与点名日期（若有）都核对通过，才算「适用」。
    """

    subject: str = Field(default="", description="页面对应的主体（学校／部门）。")
    subject_confirmed: bool = Field(default=False, description="主体是否核对通过。")
    campus: str | None = Field(default=None, description="问题点名的校区原词；未点名为 None。")
    campus_confirmed: bool | None = Field(
        default=None, description="校区是否在页面里出现；问题未点名校区为 None。"
    )
    purpose_terms: list[str] = Field(default_factory=list, description="用途相关的原始名词。")
    purpose_confirmed: bool = Field(default=False, description="用途是否在页面里定位到。")
    date_requirement: str | None = Field(
        default=None, description="问题点名的日期／时效原话；未点名为 None。"
    )
    date_confirmed: bool | None = Field(
        default=None, description="点名日期是否在页面里出现；问题未点名为 None。"
    )
    applicable: bool = Field(default=False, description="以上维度都通过时才为真。")
    note: str = Field(default="", description="面向用户的适用性说明。")


class TiebaOfficialCheck(BaseModel):
    """学校官方页面核验结果（与吧友经历分开展示）。"""

    title: str = Field(description="官方页面标题。")
    url: str = Field(description="官方页面链接。")
    host: str = Field(description="页面主机名。")
    fetched_at: datetime = Field(description="实际取得该页面的时间。")
    status: str = Field(
        description="verified（已定位相关段落）／excerpt_not_found（未定位相关段落）／"
        "fetch_failed（未能取得页面）。"
    )
    excerpt: str | None = Field(
        default=None, description="官方页面中命中原词的真实文本窗口；未命中为 None。"
    )
    matched_terms: list[str] = Field(
        default_factory=list, description="在该页面文本中真实命中的原始名词。"
    )
    applicability: TiebaOfficialApplicability | None = Field(
        default=None, description="与问题的主体／校区／用途／日期适用性核对；未核对为 None。"
    )
    error_code: str | None = Field(default=None, description="未取得页面时的失败分类码。")
    error_message: str | None = Field(default=None, description="未取得页面时的中文说明。")


class TiebaPostProjection(BaseModel):
    """一个真实读到并确认属于目标贴吧的帖子。"""

    thread_id: str | None = Field(default=None, description="帖子 ID。")
    url: str = Field(description="帖子直达链接。")
    title: str | None = Field(default=None, description="页面上的帖子标题。")
    affiliation_evidence: str = Field(description="归属确认依据的中文说明。")
    read_status: ReadStatus = Field(description="读取结果分类。")
    pages_read: int = Field(default=0, description="实际读取页数。")
    pages_limit: int = Field(default=0, description="本轮页数上限。")
    total_pages: int | None = Field(default=None, description="页面声明的总页数。")
    floor_min: int | None = Field(default=None, description="实际读到的最小楼层。")
    floor_max: int | None = Field(default=None, description="实际读到的最大楼层。")
    replies_obtained: bool = Field(description="是否真的取得了回复文本。")
    replies: list[TiebaReply] = Field(default_factory=list, description="真实读到的楼层。")
    read_error_code: str | None = Field(default=None, description="未取得内容时的分类码。")
    read_error_message: str | None = Field(
        default=None, description="未取得内容时的中文原因说明。"
    )
    retrieved_at: datetime = Field(description="读取时间。")


class TiebaCandidateLink(BaseModel):
    """仅有搜索摘要时的帖链降级（归属未确认，不含任何回复内容）。"""

    url: str
    title: str = Field(description="搜索服务给出的标题（未核实页面）。")
    source: str = Field(description="检索来源标识。")
    unconfirmed_reason: str | None = Field(
        default=None, description="归属未确认的具体原因（页面不可读／未读取）。"
    )


class TiebaTimeFilter(BaseModel):
    """时间条件的真实执行状态：解析到了什么、有没有真的用上。"""

    requirement: str | None = Field(default=None, description="时间条件原话。")
    year: int | None = Field(default=None, description="绝对年份；相对说法为 None。")
    applied: bool = Field(default=False, description="是否真的按帖子时间过滤过。")
    note: str = Field(description="面向用户的中文说明（含未执行时的原因）。")


class TiebaConflictResolution(StrEnum):
    """官方与帖子经历冲突的确定性裁决结果。"""

    #: 官方页面含明确的新规定依据且不早于帖子：按新规定呈现。
    OFFICIAL_NEWER = "official_newer"
    #: 时间／适用范围无法判定替代关系：分别引用，保留冲突。
    KEPT_BOTH = "kept_both"


class TiebaConflictPostRef(BaseModel):
    """冲突里引用的真实楼层出处。"""

    url: str
    title: str | None = Field(default=None, description="帖子标题。")
    floor: int | None = Field(default=None, description="楼层号。")
    posted_at: str | None = Field(default=None, description="楼层时间原文。")
    quote: str = Field(description="与该冲突相关的真实回复摘录。")


class TiebaConflict(BaseModel):
    """官方规定与吧友经历的一处冲突及时间／范围核对依据。"""

    topic_terms: list[str] = Field(default_factory=list, description="双方共同涉及的原始名词。")
    official_title: str = Field(description="官方页面标题。")
    official_url: str = Field(description="官方页面链接。")
    official_date: str | None = Field(
        default=None, description="官方页面的日期／版本线索原文；没有为 None。"
    )
    official_statement: str = Field(description="官方页面中相关段落的真实摘录。")
    post_refs: list[TiebaConflictPostRef] = Field(
        default_factory=list, description="与该冲突相关的真实楼层出处。"
    )
    time_basis: str = Field(description="时间核对依据的中文说明。")
    scope_basis: str = Field(description="适用范围核对依据的中文说明。")
    resolution: TiebaConflictResolution = Field(description="裁决结果。")
    note: str = Field(description="面向用户的中文说明（为什么这样呈现）。")


class TiebaVerification(BaseModel):
    """``tieba.verify`` 的结构化核验结果（模型不能自行宣布通过）。"""

    contract_version: str = Field(default="tieba-verification-v1")
    attribution_consistent: bool = Field(description="确认帖都来自真实读到且吧名吻合的页面。")
    read_scope_consistent: bool = Field(description="引用楼层／时间／页数与实际读取范围一致。")
    unread_claims_absent: bool = Field(description="未读回复没有被总结或引用。")
    consensus_claims_absent: bool = Field(description="没有「吧友普遍认为」式共识结论。")
    official_scope_consistent: bool = Field(description="官方结论仅来自适用且定位到段落的页面。")
    conflicts_disclosed: bool = Field(description="检测到的冲突都如实分列。")
    summary: str = Field(description="核验结论的中文摘要。")
    unconfirmed: list[str] = Field(default_factory=list, description="仍未确认的事项。")


class TiebaResearchProjection(BaseModel):
    """贴吧模块对外的完整投影。"""

    status: TiebaResearchStatus
    topic: str = Field(description="本轮实际检索的主题词（中文拼接）。")
    original_question: str = Field(description="用户原始问题，逐字保留。")
    topic_terms: list[str] = Field(default_factory=list, description="检索用原始名词。")
    place_or_event: list[str] = Field(default_factory=list, description="事件／地点原始名词。")
    question_kind: TiebaQuestionKind = Field(
        default=TiebaQuestionKind.EXPERIENCE, description="规定／体验／混合分类。"
    )
    campus_terms: list[str] = Field(default_factory=list, description="问题点名的校区／区域原词。")
    source_priority: list[TiebaEvidenceRoute] = Field(
        default_factory=list, description="本轮实际采用的来源优先级。"
    )
    parallel_evidence: bool = Field(default=False, description="两路是否共享预算并行取证。")
    plan_rationale: str | None = Field(default=None, description="取证顺序的中文说明。")
    official_blocked_reason: str | None = Field(
        default=None, description="官方路径被硬条件阻止时的原因说明；未阻止为 None。"
    )
    official_unverified_note: str | None = Field(
        default=None, description="规定因缺官方证据（含用户限制）保持未核实时的说明。"
    )
    time_filter: TiebaTimeFilter = Field(description="时间条件的执行状态。")
    queries: list[ModuleQueryRecord] = Field(
        default_factory=list, description="每次外部调用的统一记录（查询词/条数/时间/错误）。"
    )
    forum: str = Field(default=TARGET_FORUM_NAME, description="目标贴吧名称。")
    confirmed_posts: list[TiebaPostProjection] = Field(
        default_factory=list, description="确认属于目标贴吧的公开帖子。"
    )
    candidate_links: list[TiebaCandidateLink] = Field(
        default_factory=list, description="仅有搜索摘要时的帖链（明确标注归属未确认）。"
    )
    rejected_candidates: list[TiebaRejectedCandidate] = Field(
        default_factory=list, description="被剔除的他吧同名帖与剔除依据。"
    )
    official_check_requested: bool = Field(
        default=False, description="本轮问题是否要求追加官方核验。"
    )
    official_checks: list[TiebaOfficialCheck] = Field(
        default_factory=list, description="实际取得的学校官方页面核验结果。"
    )
    conflicts: list[TiebaConflict] = Field(
        default_factory=list, description="官方与帖子经历按时间／范围核对后仍冲突的条目。"
    )
    sections: list[str] = Field(
        default_factory=list,
        description="只由已读文本分出的段落（可核验的个人经历／不同看法／不确定点）。",
    )
    verification: TiebaVerification | None = Field(
        default=None, description="核验节点的结构化结果；旧投影为 None。"
    )
    evidence_boundary: list[str] = Field(
        default_factory=list, description="本轮证据边界与缺口的中文说明。"
    )
    empty_reason: str | None = Field(default=None, description="没有结果时的中文原因。")
    retryable: bool = Field(default=False, description="失败是否可重试。")
    error_code: str | None = Field(default=None, description="失败分类码。")
    error_message: str | None = Field(default=None, description="可操作的中文错误说明。")
    completed_at: datetime | None = Field(default=None, description="本轮收敛时间。")
    pending: ModuleWaitState | None = Field(
        default=None, description="等待用户回答的澄清状态；无等待为 None。"
    )
