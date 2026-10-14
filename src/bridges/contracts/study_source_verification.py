"""按需公开查证的来源证据合同（Issue 11）。

学习模式只在具体需要时执行公开查证：基础概念直接教学，论文、时效事实、
精确外部数据、争议结论与用户明确核实要求走真实查证分支。本模块定义查
证产物的公开投影与语义枚举：

- 读取范围区分全文抓取、结构化结果与仅搜索摘要，摘要不冒充全文；
- 采用关系与来源版本记录「实际取得什么」，搜索成功不等于结论已核实；
- 结果状态覆盖空命中、不可达、凭据未配置、来源冲突、用户限制与失败；
- 私有查询上下文（学习档案、完整会话、私人材料与凭据）永不进入投影。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

#: 本模块语义的协议版本；查证记录行随该版本冻结。
STUDY_SOURCE_VERIFICATION_VERSION = "study-source-verification-v1"


class StudyVerificationPurpose(StrEnum):
    """触发本轮公开查证的具体需要类别（脱敏、可展示）。"""

    EXPLICIT_REQUEST = "explicit_request"
    PAPER = "paper"
    FRESHNESS = "freshness"
    PRECISE_DATA = "precise_data"
    CONTROVERSY = "controversy"


PURPOSE_LABELS: dict[StudyVerificationPurpose, str] = {
    StudyVerificationPurpose.EXPLICIT_REQUEST: "用户明确要求核实或引用来源",
    StudyVerificationPurpose.PAPER: "具体论文或文献结论需要核对",
    StudyVerificationPurpose.FRESHNESS: "结论依赖时效信息",
    StudyVerificationPurpose.PRECISE_DATA: "需要精确外部数据",
    StudyVerificationPurpose.CONTROVERSY: "结论存在争议需要分别呈现",
}


class StudySourceReadScope(StrEnum):
    """单个公开来源的实际读取范围；摘要不冒充全文。"""

    FULL_TEXT = "full_text"
    STRUCTURED = "structured"
    ABSTRACT = "abstract"
    NONE = "none"


class StudySourceAdoption(StrEnum):
    """来源与交付内容的采用关系。"""

    ADOPTED = "adopted"
    SUMMARY_ONLY = "summary_only"
    EXCLUDED = "excluded"


class StudySourceVerificationStatus(StrEnum):
    """本轮公开查证的结果状态；各有可恢复说明。"""

    SUCCESS = "success"
    PARTIAL = "partial"
    EMPTY = "empty"
    INSUFFICIENT = "insufficient"
    FETCH_ERROR = "fetch_error"
    CONFLICT = "conflict"
    ERROR = "error"
    UNCONFIGURED = "unconfigured"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class StudyVerificationSourceRef(BaseModel):
    """冻结合同中的来源版本引用（实际取得并允许引用的公开来源）。"""

    source_id: str = Field(description="本轮查证内的稳定来源编号。")
    title: str = Field(default="", description="来源标题。")
    url: str = Field(default="", description="来源地址。")
    read_scope: StudySourceReadScope = Field(description="实际读取范围。")
    provider: str = Field(default="", description="实际返回该来源的提供方。")
    provider_version: str = Field(default="", description="提供方合同版本。")
    accessed_at: datetime | None = Field(default=None, description="本次访问时间。")
    fetched_at: datetime | None = Field(
        default=None, description="本地实际抓取来源页面的时间；未抓取为空。"
    )


class StudyVerificationSource(BaseModel):
    """一条实际取得的公开来源及其读取范围与采用关系。"""

    source_id: str = Field(description="本轮查证内的稳定来源编号。")
    title: str = Field(default="", description="来源标题。")
    site: str = Field(default="", description="来源站点。")
    url: str = Field(default="", description="来源地址。")
    read_scope: StudySourceReadScope = Field(description="实际读取范围。")
    adoption: StudySourceAdoption = Field(description="与交付内容的采用关系。")
    provider: str = Field(default="", description="实际返回该来源的提供方。")
    provider_version: str = Field(default="", description="提供方合同版本。")
    published_at: datetime | None = Field(
        default=None, description="来源声明的发布时间；无法解析时为空。"
    )
    accessed_at: datetime | None = Field(default=None, description="本次访问时间。")
    fetched_at: datetime | None = Field(
        default=None, description="本地实际抓取来源页面的时间；未抓取为空。"
    )


class StudySourceVerificationProjection(BaseModel):
    """一条已提交查证记录的用户可见投影（不含私有查询上下文）。"""

    verification_id: str = Field(description="稳定查证记录标识。")
    purpose: StudyVerificationPurpose = Field(description="触发查证的具体需要。")
    purpose_label: str = Field(description="触发需要的中文说明。")
    status: StudySourceVerificationStatus = Field(description="本轮查证结果状态。")
    status_note: str = Field(description="状态的可恢复中文说明。")
    query_summary: str = Field(
        default="", description="本地脱敏后实际外发的最小查询概述。"
    )
    sources: list[StudyVerificationSource] = Field(
        default_factory=list, description="实际取得的来源及读取范围与采用关系。"
    )
    adopted_source_ids: list[str] = Field(
        default_factory=list, description="允许作为结论依据采用的来源编号。"
    )
    has_conflicts: bool = Field(
        default=False, description="多个来源是否给出冲突信息。"
    )
    gap_note: str = Field(
        default="", description="仍保持未核实的缺口说明（无缺口时为空）。"
    )
    can_retry: bool = Field(default=False, description="本轮是否可以显式重试查证。")
    contract_version: str = Field(
        default=STUDY_SOURCE_VERIFICATION_VERSION,
        description="查证记录协议版本。",
    )
    created_at: datetime = Field(description="记录提交时间。")


def read_scope_for(verification: str) -> StudySourceReadScope:
    """把来源证据状态映射为实际读取范围（摘要不冒充全文）。"""

    if verification in {"verified", "cross_verified"}:
        return StudySourceReadScope.FULL_TEXT
    if verification == "structured":
        return StudySourceReadScope.STRUCTURED
    if verification == "summary_only":
        return StudySourceReadScope.ABSTRACT
    if verification == "conflicting":
        # 冲突来源仍可能有本地抓取内容；按实际字段保守判定。
        return StudySourceReadScope.ABSTRACT
    return StudySourceReadScope.NONE


def adoption_for(verification: str) -> StudySourceAdoption:
    """把来源证据状态映射为采用关系；抓取失败与冲突来源不进入依据。"""

    if verification in {"verified", "cross_verified", "structured"}:
        return StudySourceAdoption.ADOPTED
    if verification == "summary_only":
        return StudySourceAdoption.SUMMARY_ONLY
    return StudySourceAdoption.EXCLUDED
