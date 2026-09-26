"""GitHub 项目推荐模块的内部类型与对外投影。

``GithubProjectsProjection`` 是唯一暴露给 API 与前端的结果类型：它只包含
真实发生的检索、真的取得的上游文本，以及如实的失败与限流状态，不包含
内部日志或缓存细节。

证据分级是本模块的核心约束（``docs/v2/workflows.md`` 第 7 节）：

- :attr:`GithubEvidenceKind.METADATA`：GitHub API 返回的仓库元数据（名称、
  简介、话题、语言、star、最近推送、许可字段）；
- :attr:`GithubEvidenceKind.README`：**项目自述**，只说明它自称的能力；
- :attr:`GithubEvidenceKind.IMPLEMENTATION`：实际读取到的实现文件或目录
  （README 里点名的路径是否真的存在于仓库中）。

没有 ``IMPLEMENTATION`` 证据时不对内部架构作断言；没有取得许可文件时不
声称代码可自由复用——这两条在投影里由 ``limitations`` 与 ``license.note``
如实写出，不靠前端记忆。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from bridges.contracts.modules import ModuleQueryRecord, ModuleWaitState


class GithubProjectStatus(StrEnum):
    """一轮 GitHub 项目推荐的终态。

    ``SUCCESS`` 只表示真的取得了可展示的仓库证据（API 元数据 + 至少一次
    README 或实现文件读取）；只拿到 API 元数据时是 ``METADATA_ONLY``（如实
    标注未读取 README 与实现文件，通常是上游限流）；检索有结果但没有一个
    仓库覆盖 idea 的要点才是 ``EMPTY``。
    """

    CLARIFICATION = "clarification"
    SEARCHING = "searching"
    SUCCESS = "success"
    METADATA_ONLY = "metadata_only"
    EMPTY = "empty"
    ERROR = "error"
    STOPPED = "stopped"


class GithubEvidenceKind(StrEnum):
    """一条证据的来源等级（越低越弱，README 只是项目自述）。"""

    METADATA = "metadata"
    README = "readme"
    IMPLEMENTATION = "implementation"


class GithubCoverage(StrEnum):
    """一个推荐仓库相对用户 idea 的覆盖范围。"""

    WHOLE = "whole"
    COMPONENT = "component"


class GithubReadmeStatus(StrEnum):
    """README 的真实取得状态（缺失也是真实结果，不当作失败）。"""

    READ = "read"
    NOT_FOUND = "not_found"
    TOO_LARGE = "too_large"
    NOT_FETCHED = "not_fetched"
    ERROR = "error"


class GithubClarification(BaseModel):
    """缺失关键信息时只问一项，并把恢复载荷写回消息。"""

    question: str = Field(description="唯一要问的中文问题。")
    reason: str = Field(description="为什么要先问这一项。")


class GithubContextSource(BaseModel):
    """「它」指向的前文依据（可追溯：模块、原词与前文消息 ID）。"""

    kind: str = Field(description="前文来源分类：paper_search／github_projects。")
    label: str = Field(description="面向用户的中文说明（例如「上一轮论文搜索」）。")
    phrase: str = Field(description="前文里的原始词，逐字保留。")
    message_id: str | None = Field(
        default=None, description="承载该原词的助手消息 ID（可追溯前文原话）。"
    )


class GithubIdeaAnalysis(BaseModel):
    """``github.parse`` 的结构化结果（保留原话，只做确定性标注）。"""

    original_request: str = Field(description="用户本轮原文，逐字保留。")
    scenario: str = Field(description="核心用户场景（来自原文的连续短语）。")
    features: list[str] = Field(
        default_factory=list, description="必要功能（原文分段，逐字保留）。"
    )
    tech_terms: list[str] = Field(
        default_factory=list, description="可选技术词（原文里的拉丁串或登记技术词）。"
    )
    whole_idea: bool = Field(
        default=True, description="用户要的是完整产品 idea（False 表示只要某个组件）。"
    )
    component_terms: list[str] = Field(
        default_factory=list, description="用户明说要找的组件／能力原词（whole_idea 为假时非空）。"
    )
    context_source: GithubContextSource | None = Field(
        default=None, description="本轮 idea 来自前文时的可追溯依据；本轮自带主题为 None。"
    )
    clarification: GithubClarification | None = Field(
        default=None, description="需要用户补一项时的澄清问题。"
    )


class GithubRepositoryCandidate(BaseModel):
    """GitHub 检索 API 返回的候选仓库（全部字段来自上游响应，未做改写）。"""

    full_name: str = Field(description="owner/repo 形式的上游标识。")
    html_url: str = Field(description="仓库直达链接。")
    description: str | None = Field(default=None, description="上游返回的仓库简介原文。")
    topics: list[str] = Field(default_factory=list, description="上游返回的仓库话题标签。")
    language: str | None = Field(default=None, description="上游返回的主要语言。")
    stars: int = Field(default=0, description="上游返回的 star 数（仅作辅助）。")
    forks: int = Field(default=0, description="上游返回的 fork 数。")
    open_issues: int = Field(default=0, description="上游返回的未关闭 issue 数。")
    pushed_at: datetime | None = Field(default=None, description="上游返回的最近推送时间。")
    created_at: datetime | None = Field(default=None, description="上游返回的创建时间。")
    archived: bool = Field(default=False, description="上游标注的只读归档状态。")
    is_fork: bool = Field(default=False, description="上游标注的复刻状态。")
    default_branch: str | None = Field(default=None, description="上游返回的默认分支。")
    license_spdx_id: str | None = Field(
        default=None, description="元数据里的许可 SPDX 标识；上游未标注为 None。"
    )
    license_name: str | None = Field(default=None, description="元数据里的许可名称原文。")
    matched_query: str = Field(description="召回该候选的实际查询词。")
    source: str = Field(description="召回来源标识（github_search）。")


class GithubRejectedRepository(BaseModel):
    """被检索到但没有纳入推荐的仓库与理由（留下痕迹，不静默丢弃）。"""

    full_name: str
    url: str
    reason: str = Field(description="未纳入推荐的中文依据。")


class GithubFileRead(BaseModel):
    """一次真实读取到的仓库文件／目录（路径、类型与真实片段）。"""

    path: str = Field(description="仓库内的真实路径。")
    kind: str = Field(description="file 或 dir。")
    size: int | None = Field(default=None, description="上游返回的字节数；目录为 None。")
    sha: str | None = Field(default=None, description="上游返回的内容指纹。")
    entries: list[str] = Field(
        default_factory=list, description="目录读取到的直接子项名称（最多 30 条）。"
    )
    excerpt: str | None = Field(
        default=None, description="文件读取到的真实文本片段（去 base64 后截断）。"
    )


class GithubLicenseCheck(BaseModel):
    """许可证据：元数据字段与许可证文件是否真的读到。"""

    detected: bool = Field(description="上游元数据是否标注了许可。")
    spdx_id: str | None = Field(default=None, description="SPDX 标识（例如 MIT）。")
    name: str | None = Field(default=None, description="许可名称原文。")
    path: str | None = Field(default=None, description="仓库内许可文件路径。")
    license_url: str | None = Field(default=None, description="上游给出的许可说明链接。")
    file_read: bool = Field(default=False, description="是否真的读取了许可文件内容。")
    excerpt: str | None = Field(default=None, description="许可文件读到的真实片段。")
    note: str = Field(description="面向用户的中文结论（含未取得时的限制）。")


class GithubImplementationCheck(BaseModel):
    """README 点名路径的实际核对结果（README 自述 vs 真实目录）。"""

    claim: str = Field(description="README 里点名的路径／模块原文片段。")
    path: str = Field(description="在该仓库中核对的真实路径。")
    status: str = Field(
        description="confirmed（实际读取到存在）／missing（实际上游返回不存在）／"
        "unread（本轮没有读取，因此不下结论）。"
    )
    evidence: str = Field(description="中文依据说明。")


class GithubMaintenanceEvidence(BaseModel):
    """维护与活跃度证据（全部来自 API 元数据，含取得时间）。"""

    pushed_at: datetime | None = Field(default=None, description="最近推送时间。")
    created_at: datetime | None = Field(default=None, description="创建时间。")
    stars: int = Field(default=0, description="star 数（仅作辅助，不优先于功能匹配）。")
    forks: int = Field(default=0, description="fork 数。")
    open_issues: int = Field(default=0, description="未关闭 issue 数。")
    archived: bool = Field(default=False, description="是否只读归档。")
    is_fork: bool = Field(default=False, description="是否为复刻。")
    runnable_hints: list[str] = Field(
        default_factory=list, description="根目录里实际读到的可运行线索（清单文件等）。"
    )
    note: str = Field(description="面向用户的中文说明。")


class GithubFeatureMatch(BaseModel):
    """一条「idea 要点 → 仓库证据」的匹配判定（含证据类型与原文片段）。"""

    feature: str = Field(description="用户 idea 里的必要功能原词。")
    matched: bool = Field(description="是否在该仓库的已取得证据里真实出现。")
    evidence_kind: GithubEvidenceKind | None = Field(
        default=None, description="命中证据的等级；未命中为 None。"
    )
    matched_terms: list[str] = Field(
        default_factory=list, description="在证据文本里真实命中的关键词。"
    )
    evidence: str = Field(description="命中的真实文本片段或未命中的中文说明。")


class GithubRepositoryEvidence(BaseModel):
    """一个候选仓库在本轮真实取得的全部证据（``github.inspect`` 的产出）。

    ``readme_text`` 只在内部用于匹配与归纳，不进对外投影——投影只给命中原词
    的片段（``GithubRecommendation.readme_excerpt``），避免把整篇自述搬给前端。
    """

    full_name: str = Field(description="owner/repo。")
    html_url: str = Field(description="仓库直达链接。")
    description: str | None = Field(default=None, description="元数据里的仓库简介原文。")
    topics: list[str] = Field(default_factory=list, description="元数据里的话题标签。")
    language: str | None = Field(default=None, description="元数据里的主要语言。")
    stars: int = Field(default=0, description="star 数。")
    forks: int = Field(default=0, description="fork 数。")
    open_issues: int = Field(default=0, description="未关闭 issue 数。")
    pushed_at: datetime | None = Field(default=None, description="最近推送时间。")
    created_at: datetime | None = Field(default=None, description="创建时间。")
    archived: bool = Field(default=False, description="是否只读归档。")
    is_fork: bool = Field(default=False, description="是否为复刻。")
    default_branch: str | None = Field(default=None, description="默认分支。")
    license: GithubLicenseCheck = Field(description="许可证据。")
    readme_status: GithubReadmeStatus = Field(description="README 的真实取得状态。")
    readme_url: str | None = Field(default=None, description="README 页面链接。")
    readme_text: str | None = Field(
        default=None, description="本轮取得并截断的 README 正文（内部匹配与归纳用）。"
    )
    files_read: list[GithubFileRead] = Field(
        default_factory=list, description="实际读取到的实现文件／目录。"
    )
    implementation_checks: list[GithubImplementationCheck] = Field(
        default_factory=list, description="README 点名路径的实际核对结果。"
    )
    runnable_hints: list[str] = Field(
        default_factory=list, description="根目录里实际读到的可运行线索（清单文件等）。"
    )
    rate_limited: bool = Field(
        default=False, description="该仓库证据是否因上游额度限制而缺失（可重试）。"
    )
    matched_query: str = Field(description="召回该候选的实际查询词。")
    retrieved_at: datetime = Field(description="证据取得时间。")


class GithubRecommendation(BaseModel):
    """一个纳入推荐的仓库：覆盖范围、功能匹配、维护与许可证据、借鉴角度。"""

    rank: int = Field(description="推荐序号（1 起，按功能匹配优先排序）。")
    full_name: str = Field(description="owner/repo。")
    html_url: str = Field(description="仓库直达链接。")
    coverage: GithubCoverage = Field(description="整体项目还是组件项目。")
    covers_parts: list[str] = Field(
        default_factory=list, description="组件项目覆盖的 idea 要点；整体项目为空。"
    )
    coverage_note: str = Field(description="覆盖范围的中文说明。")
    description: str | None = Field(default=None, description="元数据里的仓库简介原文。")
    topics: list[str] = Field(default_factory=list, description="元数据里的话题标签。")
    language: str | None = Field(default=None, description="元数据里的主要语言。")
    feature_matches: list[GithubFeatureMatch] = Field(
        default_factory=list, description="逐条要点的匹配判定。"
    )
    matched_feature_count: int = Field(default=0, description="命中的要点数。")
    evidence_kinds: list[GithubEvidenceKind] = Field(
        default_factory=list, description="本轮真实取得的证据等级（去重、弱→强）。"
    )
    readme_status: GithubReadmeStatus = Field(description="README 的真实取得状态。")
    readme_url: str | None = Field(default=None, description="README 页面链接。")
    readme_excerpt: str | None = Field(
        default=None, description="README 里命中原词的片段（自述，已截断）。"
    )
    files_read: list[GithubFileRead] = Field(
        default_factory=list, description="实际读取到的实现文件／目录。"
    )
    implementation_checks: list[GithubImplementationCheck] = Field(
        default_factory=list, description="README 点名路径的实际核对结果。"
    )
    maintenance: GithubMaintenanceEvidence = Field(description="维护与活跃度证据。")
    license: GithubLicenseCheck = Field(description="许可证据。")
    reason_zh: str = Field(description="纳入推荐并排在此位次的中文理由。")
    borrow_note: str = Field(description="可借鉴角度的中文说明（只依据已取得的证据）。")
    strengths: list[str] = Field(default_factory=list, description="优点（有证据支持的部分）。")
    limitations: list[str] = Field(
        default_factory=list, description="局限（证据缺口与未核实的部分）。"
    )
    insight_zh: str | None = Field(
        default=None, description="模型在给定证据范围内写的中文归纳；未生成时为 None。"
    )
    retrieved_at: datetime = Field(description="本轮证据取得时间。")


class GithubRateLimitState(BaseModel):
    """上游限流的真实状态（额度用尽时保留可重试结论）。

    只给出「是否撞上」与面向用户的说明：剩余额度与重置时刻属于检索内部日志
    （``docs/v2/interaction.md`` §4），不进入投影，也不呈现给用户。
    """

    limited: bool = Field(default=False, description="本轮是否真的撞上额度限制。")
    note: str | None = Field(default=None, description="面向用户的中文说明。")


class GithubProjectsProjection(BaseModel):
    """GitHub 项目推荐对外的完整投影。"""

    status: GithubProjectStatus
    scenario: str = Field(description="核心用户场景（来自原文）。")
    original_request: str = Field(description="用户本轮原文，逐字保留。")
    features: list[str] = Field(default_factory=list, description="必要功能原词。")
    tech_terms: list[str] = Field(default_factory=list, description="可选技术词。")
    whole_idea: bool = Field(default=True, description="是否要找完整产品。")
    component_terms: list[str] = Field(
        default_factory=list, description="用户明说要找的组件能力原词。"
    )
    context_source: GithubContextSource | None = Field(
        default=None, description="idea 来自前文时的可追溯依据（AC5 的关联原话）。"
    )
    queries: list[ModuleQueryRecord] = Field(
        default_factory=list, description="每次外部调用的统一记录（查询词/条数/时间/错误）。"
    )
    recommendations: list[GithubRecommendation] = Field(
        default_factory=list, description="本轮真实取得证据并纳入推荐的仓库。"
    )
    rejected: list[GithubRejectedRepository] = Field(
        default_factory=list, description="被检索到但未纳入推荐的仓库与理由。"
    )
    rate_limit: GithubRateLimitState = Field(description="上游限流的真实状态。")
    evidence_boundary: list[str] = Field(
        default_factory=list, description="本轮证据边界与缺口的中文说明。"
    )
    empty_reason: str | None = Field(default=None, description="没有可推荐结果时的中文原因。")
    retryable: bool = Field(default=False, description="失败是否可重试。")
    error_code: str | None = Field(default=None, description="失败分类码。")
    error_message: str | None = Field(default=None, description="可操作的中文错误说明。")
    completed_at: datetime | None = Field(default=None, description="本轮收敛时间。")
    pending: ModuleWaitState | None = Field(
        default=None, description="等待用户回答的澄清状态；无等待为 None。"
    )
