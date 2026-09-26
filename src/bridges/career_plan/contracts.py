"""职业规划模块的内部类型与对外投影（V2 Issue 15）。

``CareerPlanProjection`` 是唯一暴露给 API 与前端的结果类型：它只包含真实
发生的查询、真实读到的岗位字段与如实的失败原因。主样本（``samples``）只
纳入**公开可读且岗位与城市都匹配**的岗位；只有搜索摘要、未能读到页面时
降级为 ``candidate_links`` 并明确标注未核实。

沿用 Issue 11 建立的证据（``ModuleQueryRecord``）与等待（``ModuleWaitState``）
合同，只扩展本模块自己的来源字段。
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from bridges.contracts.modules import ModuleQueryRecord, ModuleWaitState


class CareerPlanStatus(StrEnum):
    """一轮职业规划分析的终态。

    ``SUCCESS`` 只表示真的读到了公开可读且匹配的岗位；只拿到搜索链接时是
    ``LINKS_ONLY``（明确标注未核实、未纳入样本），检索完全没有可用候选才是
    ``EMPTY``。
    """

    CLARIFICATION = "clarification"
    SUCCESS = "success"
    LINKS_ONLY = "links_only"
    EMPTY = "empty"
    ERROR = "error"
    STOPPED = "stopped"


class CareerClarification(BaseModel):
    """缺失岗位意图时只问一项，并把恢复载荷写回消息。"""

    question: str = Field(description="唯一要问的中文问题。")
    reason: str = Field(description="为什么要先问这一项。")


class CareerRequestAnalysis(BaseModel):
    """``career.parse`` 的结构化结果（保留原话，只做确定性标注）。"""

    original_request: str = Field(description="用户原始请求，逐字保留。")
    job_terms: list[str] = Field(default_factory=list, description="检索用岗位锚点原话。")
    job_title: str | None = Field(
        default=None, description="识别出的目标岗位名（原话，不替换成其他岗位）。"
    )
    family_title: str | None = Field(
        default=None, description="命中的岗位族名称；不在词表内为 None。"
    )
    synonyms: list[str] = Field(
        default_factory=list, description="目标岗位的真正同义说法（可并入主样本）。"
    )
    adjacent_jobs: list[str] = Field(
        default_factory=list, description="相邻岗位（单列建议，绝不混入主样本）。"
    )
    stage: str | None = Field(default=None, description="毕业阶段原话。")
    graduation_year: int | None = Field(default=None, description="届别年份。")
    cities: list[str] = Field(default_factory=list, description="期望城市（原话识别）。")
    experience_hint: str | None = Field(
        default=None, description="请求里提到的经验要求原话。"
    )
    constraints: list[str] = Field(
        default_factory=list, description="用户提出的其他约束原话。"
    )
    clarification: CareerClarification | None = Field(
        default=None, description="需要用户补一项时的澄清问题。"
    )


class CareerQueryPlanItem(BaseModel):
    """``career.plan`` 的一条检索计划（实际发送的查询词与筛选条件）。"""

    source: str = Field(description="来源标识：boss／corporate／campus。")
    source_label: str = Field(description="来源中文名。")
    query: str = Field(description="实际发送的最小查询词。")
    reason: str = Field(description="为什么这样查。")
    filters: list[str] = Field(
        default_factory=list, description="本轮对来源结果施加的筛选条件。"
    )


class JobReadStatus(StrEnum):
    """单个岗位页的真实读取结果分类。"""

    READ = "read"
    PARTIAL = "partial"
    ACCESS_RESTRICTED = "access_restricted"
    UNRECOGNIZED = "unrecognized"
    NOT_FOUND = "not_found"
    TIMEOUT = "timeout"
    ERROR = "error"
    CANCELLED = "cancelled"


class JobSample(BaseModel):
    """一个公开可读、岗位与城市都匹配的岗位样本。

    不可访问的字段留空（``None``／空列表），绝不用模型知识或邻近岗位填充。
    """

    url: str = Field(description="岗位直达链接。")
    source: str = Field(description="来源标识：boss／corporate／campus。")
    source_label: str = Field(description="来源中文名。")
    title: str = Field(description="页面上的岗位名原文。")
    company: str | None = Field(default=None, description="公司名原文。")
    city: str | None = Field(default=None, description="城市原文。")
    salary_raw: str | None = Field(default=None, description="薪资原文。")
    published_raw: str | None = Field(default=None, description="发布日期原文。")
    published_date: date | None = Field(
        default=None, description="能确定为绝对日期时的发布日期；相对说法为 None。"
    )
    experience: str | None = Field(default=None, description="经验要求原文。")
    education: str | None = Field(default=None, description="学历要求原文。")
    requirements: list[str] = Field(
        default_factory=list, description="页面上的能力要求原文条目。"
    )
    skills: list[str] = Field(
        default_factory=list, description="从能力要求原文里命中的技能关键词。"
    )
    title_evidence: str = Field(description="岗位名匹配目标岗位的依据说明。")
    city_evidence: str = Field(description="城市匹配的核对依据说明。")
    retrieved_at: datetime = Field(description="实际抓取时间。")
    read_status: JobReadStatus = Field(description="读取结果分类。")
    error_code: str | None = Field(default=None, description="未取得内容时的分类码。")
    error_message: str | None = Field(default=None, description="未取得内容时的中文原因。")


class CareerRejectedSample(BaseModel):
    """被主样本剔除的候选与剔除依据（不静默丢弃）。"""

    url: str = Field(description="候选链接。")
    title: str = Field(description="候选岗位名（未核实时为搜索标题）。")
    company: str | None = Field(default=None, description="公司名；未读到为 None。")
    kind: str = Field(description="剔除分类：adjacent／city／expired／duplicate／not_job。")
    evidence: str = Field(description="剔除依据的中文说明。")


class CareerCandidateLink(BaseModel):
    """仅有搜索摘要时的链接降级（未核实，不纳入样本）。"""

    url: str
    title: str = Field(description="搜索服务给出的标题（未核实页面）。")
    source: str = Field(description="来源标识。")
    source_label: str = Field(description="来源中文名。")
    note: str = Field(description="为什么没有纳入样本的中文说明。")


class SalaryInterval(BaseModel):
    """同一计薪单位下的薪资区间（样本量与原文都留痕）。"""

    unit: str = Field(description="计薪单位：元/月、元/天、元/年。")
    sample_count: int = Field(description="该单位下的岗位样本数。")
    amount_min: int = Field(description="区间下限（元）。")
    amount_max: int = Field(description="区间上限（元）。")
    amount_median: int = Field(description="样本中点中位数（元）。")
    cities: list[str] = Field(default_factory=list, description="该区间覆盖的城市。")
    raws: list[str] = Field(default_factory=list, description="该区间用到的薪资原文。")
    small_sample: bool = Field(
        description="样本量是否不足以支撑总体推断（此时不称为市场均值）。"
    )


class CityCount(BaseModel):
    """岗位样本的城市构成。"""

    city: str
    count: int


class SkillStat(BaseModel):
    """技能关键词统计（只统计主样本原文里命中的词）。"""

    term: str
    count: int = Field(description="出现该词的样本数。")
    urls: list[str] = Field(default_factory=list, description="来源样本链接。")


class CareerAnalysis(BaseModel):
    """``career.analyze`` 的分析结果（只基于主样本，附口径说明）。"""

    sample_count: int = Field(description="主样本岗位数。")
    city_composition: list[CityCount] = Field(default_factory=list)
    published_span: str | None = Field(
        default=None, description="样本发布日期范围；无绝对日期为 None。"
    )
    skill_stats: list[SkillStat] = Field(default_factory=list)
    salary_intervals: list[SalaryInterval] = Field(default_factory=list)
    incomparable_notes: list[str] = Field(
        default_factory=list, description="未并入区间的薪资原文与原因。"
    )
    sample_scope_note: str = Field(description="本轮样本口径说明（样本量、日期、地区）。")
    small_sample: bool = Field(description="样本量是否不足以支撑总体推断。")
    overall_inference_stopped: bool = Field(
        description="样本不足时是否已停止总体推断（不产出全国市场结论）。"
    )


class CareerAdviceItem(BaseModel):
    """``career.advise`` 的一条建议（区分证据与推断）。"""

    kind: str = Field(description="建议分类：skill／project／action。")
    title: str = Field(description="建议标题。")
    detail: str = Field(description="建议正文。")
    basis: list[str] = Field(
        default_factory=list, description="依据：命中的样本要求原文或样本链接。"
    )
    inference: bool = Field(
        description="是否为推断（不是样本原文直接支持的内容）。"
    )


class AdjacentJobSuggestion(BaseModel):
    """相邻岗位建议（单列，绝不混入主样本的薪资与技能统计）。"""

    title: str = Field(description="相邻岗位名。")
    reason: str = Field(description="为什么单列这一项。")
    sample_count: int = Field(default=0, description="本轮检索到的该岗位样本数。")


class CareerPlanProjection(BaseModel):
    """职业规划模块对外的完整投影。"""

    status: CareerPlanStatus
    topic: str = Field(description="本轮分析的岗位主题（中文）。")
    original_request: str = Field(description="用户原始请求，逐字保留。")
    job_terms: list[str] = Field(default_factory=list, description="检索用岗位锚点原话。")
    family_title: str | None = Field(default=None, description="命中的岗位族名称。")
    stage: str | None = Field(default=None, description="毕业阶段原话。")
    graduation_year: int | None = Field(default=None, description="届别年份。")
    cities: list[str] = Field(default_factory=list, description="期望城市。")
    constraints: list[str] = Field(default_factory=list, description="其他约束原话。")
    plan: list[CareerQueryPlanItem] = Field(
        default_factory=list, description="本轮实际执行的检索计划（来源、查询词、筛选条件）。"
    )
    queries: list[ModuleQueryRecord] = Field(
        default_factory=list, description="每次外部调用的统一记录（查询词/条数/时间/错误）。"
    )
    samples: list[JobSample] = Field(
        default_factory=list, description="公开可读且岗位与城市都匹配的主样本。"
    )
    candidate_links: list[CareerCandidateLink] = Field(
        default_factory=list, description="未核实或未读到的候选链接（不纳入样本）。"
    )
    rejected: list[CareerRejectedSample] = Field(
        default_factory=list, description="被剔除的候选与剔除依据。"
    )
    analysis: CareerAnalysis | None = Field(
        default=None, description="技能与薪资分析；样本为空时为 None。"
    )
    advices: list[CareerAdviceItem] = Field(
        default_factory=list, description="面向用户的可执行建议。"
    )
    adjacent_suggestions: list[AdjacentJobSuggestion] = Field(
        default_factory=list, description="相邻岗位单列建议。"
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
