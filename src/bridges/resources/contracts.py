"""学习资料推荐模块的公开合同（V2 Issue 13）。

编排合同（``.scratch/bridges-v2/issues/13-learning-resources.md``）要求：保留
本轮原始专业名词与学习目的，**只在学习层次确实影响推荐且上下文不足时**追问
这一项；默认尝试给两本书与三条哔哩哔哩视频，逐项核对可取得的元数据；展示由浅
入深的顺序与选择理由；未看过的视频不描述不可验证的具体内容。

本模块沿用 Issue 11 建立的证据（``ModuleQueryRecord``）与等待（``ModuleWaitState``）
合同，只扩展自己的来源字段：

- :class:`ResourcesTermAnalysis`：``resources.parse`` 的解析结果（原词逐字保留）；
- :class:`ResourcesQueryPlan`：``resources.plan`` 的实际查询词与目标数量；
- :class:`ResourceItem`：一本书或一条视频，两者共用一个有序清单类型（``kind``
  区分），因此「由浅入深」的顺序在一处表达；
- :class:`LearningResourcesProjection`：随助手消息持久化的完整状态。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from bridges.contracts.modules import ModuleQueryRecord, ModuleWaitState


class ResourcesStatus(StrEnum):
    """资料模块的用户可见状态（同一消息内如实显示）。"""

    CLARIFICATION = "clarification"
    SEARCHING = "searching"
    SUCCESS = "success"
    EMPTY = "empty"
    ERROR = "error"
    STOPPED = "stopped"


class ResourceKind(StrEnum):
    """清单条目的类型（图书 / 视频）。"""

    BOOK = "book"
    VIDEO = "video"


class ResourcesLevel(StrEnum):
    """学习层次（唯一会触发追问的一项）。"""

    BEGINNER = "beginner"
    BASIC = "basic"
    ADVANCED = "advanced"


#: 层次 → 中文标签（解析、排序与正文共用一份，避免两处各写一张表而走样）。
LEVEL_LABELS: dict[str, str] = {
    ResourcesLevel.BEGINNER: "零基础入门",
    ResourcesLevel.BASIC: "有一定基础",
    ResourcesLevel.ADVANCED: "进阶提高",
}

#: 清单条目的适用阶段（与学习层次同一套措辞，按从浅到深排序）。
STAGE_BEGINNER = "入门"
STAGE_FOUNDATION = "打基础"
STAGE_ADVANCED = "进阶"

STAGE_ORDER: tuple[str, ...] = (STAGE_BEGINNER, STAGE_FOUNDATION, STAGE_ADVANCED)


def format_duration(seconds: int) -> str:
    """秒 → 中文时长（排序理由与正文共用一份措辞）。"""
    minutes, remainder = divmod(max(0, seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} 小时 {minutes} 分"
    return f"{minutes} 分 {remainder} 秒" if minutes else f"{remainder} 秒"


class ResourcesLevelCandidate(BaseModel):
    """澄清时逐项列出的层次候选（用户回答后据此解析）。"""

    key: str = Field(description="层次键：beginner / basic / advanced。")
    label: str = Field(description="中文候选说明，例如「零基础入门」。")
    keywords: tuple[str, ...] = Field(
        default_factory=tuple, description="用户回答或前文中指向该层次的中文/英文词。"
    )


class ResourcesClarification(BaseModel):
    """一条待用户回答的澄清（本模块只问学习层次这一项）。"""

    question: str = Field(description="向用户提出的那一个问题（中文，只问一项）。")
    missing: str = Field(description="缺失项分类，本模块固定为 level（学习层次）。")
    original_phrase: str = Field(description="用户原文中的原始短语（逐字保留）。")
    candidates: list[ResourcesLevelCandidate] = Field(
        default_factory=list, description="学习层次候选（澄清时逐项列出）。"
    )


class ResourcesTermAnalysis(BaseModel):
    """``resources.parse`` 的解析结果。"""

    original_phrase: str = Field(description="用户原文中的原始专业名词（逐字保留，绝不替换）。")
    normalized_term: str = Field(description="规范化值（保留原词，仅做大小写/空白整理）。")
    expansions: list[str] = Field(
        default_factory=list, description="扩展词（译名/同义词），只作扩展，不替掉原词。"
    )
    confidence: float = Field(ge=0.0, le=1.0, description="解析置信度（0–1）。")
    goal: str | None = Field(
        default=None, description="本轮学习目的（备考/项目/入门等）；未表达为 None。"
    )
    level: ResourcesLevel | None = Field(
        default=None, description="学习层次；上下文不足且影响推荐时为 None（触发追问）。"
    )
    level_basis: str | None = Field(
        default=None, description="层次判定依据（原话或前文），供正文如实说明。"
    )
    final_query: str = Field(
        default="", description="用于检索的最终查询词（原词优先）；澄清时为原始短语。"
    )
    clarification: ResourcesClarification | None = Field(
        default=None, description="缺失学习层次时的单一澄清问题；无需澄清为 None。"
    )


class ResourcesQueryPlan(BaseModel):
    """``resources.plan`` 生成的检索计划。"""

    term: str = Field(description="进入所有查询的主词（原词优先，扩展词只作补充）。")
    book_query: str = Field(description="发送给图书书目来源的最小查询词（不含私有上下文）。")
    video_query: str = Field(description="发送给公网搜索的最小查询词（用于发现哔哩哔哩直达页）。")
    target_books: int = Field(default=2, ge=0, description="本轮目标图书数量（默认 2）。")
    target_videos: int = Field(default=3, ge=0, description="本轮目标视频数量（默认 3）。")
    book_candidate_limit: int = Field(ge=1, description="图书候选的请求上限（高于目标数）。")
    video_candidate_limit: int = Field(ge=1, description="视频候选的请求上限（高于目标数）。")
    expansions_used: list[str] = Field(
        default_factory=list, description="实际进入查询的扩展词（原词始终在查询中）。"
    )
    rationale: str = Field(description="中文说明：为什么这样构造查询（原词优先、扩展只作补充）。")


class ResourceItem(BaseModel):
    """一份经来源核对的推荐条目（图书或视频，阅读顺序见 ``order``）。"""

    order: int = Field(ge=1, description="由浅入深的学习顺序（从 1 开始）。")
    kind: ResourceKind = Field(description="条目类型：book（图书）或 video（视频）。")
    title: str = Field(description="来源返回的原始标题。")
    creator: str | None = Field(
        default=None, description="图书作者或视频作者（UP 主）名称；来源未给为 None。"
    )
    year: int | None = Field(default=None, description="图书出版年份或视频发布年份。")
    source: str = Field(description="元数据来源（openlibrary / openalex / bilibili）。")
    url: str = Field(description="可点开的直达链接（书目页 / 视频页）。")
    stage: str = Field(description="适用阶段（入门 / 打基础 / 进阶）。")
    reason_zh: str = Field(description="中文选择理由（含阶段判定与搭配依据）。")
    match_basis: str = Field(description="主题与来源核对依据（命中哪些词、核对了什么元数据）。")
    publisher: str | None = Field(
        default=None, description="图书出版社；来源未给或视频条目为 None。"
    )
    isbn: str | None = Field(default=None, description="图书 ISBN；来源未给或视频条目为 None。")
    duration_seconds: int | None = Field(
        default=None, description="视频时长（秒）；图书条目或来源未给为 None。"
    )
    published_at: datetime | None = Field(
        default=None, description="来源声明的发布时间；未给为 None。"
    )
    view_count: int | None = Field(
        default=None, description="视频公开播放次数（平台计数）；图书条目为 None。"
    )
    like_count: int | None = Field(
        default=None, description="视频公开点赞数（平台计数，只作弱证据）；图书条目为 None。"
    )
    unverified: list[str] = Field(
        default_factory=list, description="本条未核实项（例如未观看视频、缺 ISBN）。"
    )


class LearningResourcesProjection(BaseModel):
    """资料模块随助手消息持久化的完整投影。"""

    status: ResourcesStatus = Field(description="本轮模块状态。")
    original_phrase: str = Field(default="", description="保留的原始专业名词。")
    normalized_term: str = Field(default="", description="规范化值。")
    expansions: list[str] = Field(default_factory=list, description="本轮使用的扩展词。")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="解析置信度。")
    goal: str | None = Field(default=None, description="本轮学习目的（备考/项目等）。")
    level_label: str | None = Field(default=None, description="本轮学习层次的中文标签。")
    level_basis: str | None = Field(default=None, description="层次判定依据。")
    queries: list[ModuleQueryRecord] = Field(
        default_factory=list, description="本轮全部外部调用的统一记录（查询/证据/时间/错误）。"
    )
    final_query: str = Field(default="", description="实际用于检索的最终查询词。")
    items: list[ResourceItem] = Field(
        default_factory=list, description="按由浅入深顺序排列的真实资料条目。"
    )
    requested_books: int = Field(default=0, ge=0, description="本轮目标图书数量。")
    requested_videos: int = Field(default=0, ge=0, description="本轮目标视频数量。")
    evidence_notes: list[str] = Field(
        default_factory=list, description="证据边界说明（实际数量、来源不足、未观看等）。"
    )
    pending: ModuleWaitState | None = Field(
        default=None, description="跨轮次等待状态（学习层次澄清）；无等待为 None。"
    )
    searched_at: datetime | None = Field(default=None, description="检索完成时间。")
    error_code: str | None = Field(default=None, description="失败分类码。")
    error_message: str | None = Field(default=None, description="可操作的中文错误说明。")
    retryable: bool = Field(default=False, description="本轮失败是否可重试。")
