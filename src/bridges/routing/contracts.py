"""自然语言能力路由的版本化合同。"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from bridges.contracts.career import CareerPlanningRouteContract
from bridges.video.constants import (
    VIDEO_MODEL_ID,
    VIDEO_SUPPORTED_DURATIONS_SECONDS,
    VIDEO_SUPPORTED_SIZES,
)


ROUTE_CONTRACT_VERSION = "2026.08.09"
PAPER_QUERY_VERSION = "2026.08.12"


def _chat_module_values() -> frozenset[str]:
    """显式模块 ID 集合；延迟导入避免 contracts.chat → routing 的循环。"""

    from bridges.contracts.chat import CHAT_MODULE_VALUES

    return CHAT_MODULE_VALUES


RemovedQueryCategory = Literal[
    "instruction_scaffold",
    "code",
    "credential",
    "private_material",
    "email",
    "url",
]


class MainCapability(StrEnum):
    """当前主能力集合；后续能力只能以新注册项追加。"""

    ORDINARY_CHAT = "ordinary_chat"
    PAPER_SEARCH = "paper_search"
    CLARIFICATION = "clarification"
    HUMANIZER = "humanizer"
    IMAGE = "image"
    VIDEO = "video"
    CAREER = "career"
    # 工单 12：正文明确单模块直启时，路由快照记录实际模块能力；这些能力
    # 不携带额外可执行计划（参数由对应模块自行解析），因此不属于重预算。
    COMMUTE = "commute"
    RESOURCES = "resources"
    TIEBA = "tieba"
    GITHUB = "github"


#: 登记模块 → 主能力（工单 12：理解与路由快照共用一份映射）。
MODULE_CAPABILITIES: dict[str, MainCapability] = {
    "paper": MainCapability.PAPER_SEARCH,
    "commute": MainCapability.COMMUTE,
    "resources": MainCapability.RESOURCES,
    "tieba": MainCapability.TIEBA,
    "career": MainCapability.CAREER,
    "github": MainCapability.GITHUB,
}


class RouteStatus(StrEnum):
    """路由裁决状态。"""

    MATCHED = "matched"
    CLARIFY = "clarify"
    REJECTED = "rejected"
    ORDINARY = "ordinary"


class PaperSearchConstraints(BaseModel):
    """仅允许发送给论文适配器的结构化筛选约束。"""

    model_config = ConfigDict(extra="forbid")

    topic_terms: list[str] = Field(default_factory=list, max_length=12)
    author: str | None = Field(default=None, max_length=120)
    title: str | None = Field(default=None, max_length=240)
    arxiv_id: str | None = Field(default=None, max_length=80)
    year_from: int | None = Field(default=None, ge=1900, le=2100)
    year_to: int | None = Field(default=None, ge=1900, le=2100)
    max_results: Annotated[int, Field(ge=1, le=10)] = 5

    @model_validator(mode="after")
    def validate_range_and_query(self) -> PaperSearchConstraints:
        if self.year_from is not None and self.year_to is not None:
            if self.year_from > self.year_to:
                raise ValueError("年份范围起点不能晚于终点。")
        if not any((self.topic_terms, self.author, self.title, self.arxiv_id)):
            raise ValueError("论文查询至少需要主题、作者、标题或 arXiv 标识符。")
        return self


class PaperSearchPlan(BaseModel):
    """已规范化、可重放的论文搜索计划。"""

    model_config = ConfigDict(extra="forbid")

    version: str = PAPER_QUERY_VERSION
    normalized_query: str = Field(min_length=1, max_length=240)
    constraints: PaperSearchConstraints
    removed_categories: list[RemovedQueryCategory] = Field(
        default_factory=list, max_length=8
    )


class VideoGenerationPlan(BaseModel):
    """已规范化、可重放的文生视频生成合同。"""

    model_config = ConfigDict(extra="forbid")

    version: str = ROUTE_CONTRACT_VERSION
    model_id: str = VIDEO_MODEL_ID
    prompt: str = Field(min_length=1, max_length=2000)
    aspect_ratio: Literal["16:9", "9:16"] = "16:9"
    size: Literal["1280*720", "720*1280"] = "1280*720"
    duration_seconds: Literal[5, 10] = 5
    account_object_domain: Literal["account"] = "account"

    @model_validator(mode="after")
    def validate_fixed_contract(self) -> VideoGenerationPlan:
        if self.version != ROUTE_CONTRACT_VERSION:
            raise ValueError("未知视频路由合同版本。")
        if self.model_id != VIDEO_MODEL_ID:
            raise ValueError("视频模型必须使用固定能力快照。")
        expected_size = {
            "16:9": VIDEO_SUPPORTED_SIZES[0],
            "9:16": VIDEO_SUPPORTED_SIZES[1],
        }[self.aspect_ratio]
        if self.size != expected_size:
            raise ValueError("视频画幅与尺寸不匹配。")
        if self.duration_seconds not in VIDEO_SUPPORTED_DURATIONS_SECONDS:
            raise ValueError("视频时长不在支持范围内。")
        return self


class CapabilityRoute(BaseModel):
    """一次用户消息的主能力路由快照。"""

    model_config = ConfigDict(extra="forbid")

    version: str = ROUTE_CONTRACT_VERSION
    status: RouteStatus
    main_capability: MainCapability
    confidence: Annotated[float, Field(ge=0, le=1)]
    reason: str = Field(min_length=1, max_length=240)
    normalized_query: str | None = Field(default=None, max_length=2_000)
    paper_search: PaperSearchPlan | None = None
    video: VideoGenerationPlan | None = None
    career_contract: CareerPlanningRouteContract | None = None
    clarification_question: str | None = Field(default=None, max_length=240)
    error_code: str | None = Field(default=None, max_length=80)
    knowledge_base_allowed: bool = True
    web_search_allowed: bool = True
    side_effects: list[str] = Field(default_factory=list, max_length=8)
    #: 工单 12：实际生效的模块（代码校验后）；``None`` 表示不派发模块。
    module_id: str | None = Field(default=None, max_length=40)
    #: 请求携带的模块提示（历史标识，不重写）；正文优先于该字段。
    requested_module_id: str | None = Field(default=None, max_length=40)
    #: 实际路由来源（RouteSource 值），与请求提示分开记录。
    route_source: str | None = Field(default=None, max_length=40)
    #: 本次理解关联的能力列表（模块 ID，按实际路由）。
    capability_list: list[str] = Field(default_factory=list, max_length=8)
    #: 生成该路由快照的主理解合同版本（审计与跨版本读取）。
    understanding_version: str | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def validate_capability_payload(self) -> CapabilityRoute:
        if self.main_capability == MainCapability.PAPER_SEARCH:
            if self.status == RouteStatus.MATCHED and self.paper_search is None:
                raise ValueError("论文搜索命中必须携带搜索计划。")
            if self.status != RouteStatus.MATCHED and self.paper_search is not None:
                raise ValueError("未命中论文搜索不能携带可执行计划。")
        elif self.paper_search is not None:
            raise ValueError("非论文主能力不能携带论文搜索计划。")
        if self.main_capability == MainCapability.VIDEO:
            if self.status == RouteStatus.MATCHED and self.video is None:
                raise ValueError("视频路由命中必须携带生成合同。")
            if self.status != RouteStatus.MATCHED and self.video is not None:
                raise ValueError("未命中视频路由不能携带生成合同。")
        elif self.video is not None:
            raise ValueError("非视频主能力不能携带视频生成合同。")
        if self.main_capability == MainCapability.CAREER:
            if self.status == RouteStatus.MATCHED and self.career_contract is None:
                raise ValueError("生涯规划路由命中必须携带规划合同。")
            if self.status != RouteStatus.MATCHED and self.career_contract is not None:
                raise ValueError("未命中生涯规划不能携带规划合同。")
        elif self.career_contract is not None:
            raise ValueError("非生涯规划主能力不能携带规划合同。")
        if self.status in {RouteStatus.CLARIFY, RouteStatus.REJECTED} and not (
            self.clarification_question or self.error_code
        ):
            raise ValueError("澄清或拒绝路由必须有可操作反馈。")
        if self.status == RouteStatus.MATCHED and self.clarification_question:
            raise ValueError("已命中路由不能同时要求澄清。")
        for module_id in (self.module_id, self.requested_module_id):
            if module_id is not None and module_id not in _chat_module_values():
                raise ValueError("模块 ID 必须是已登记的显式模块。")
        if self.capability_list:
            unknown = [
                item for item in self.capability_list if item not in _chat_module_values()
            ]
            if unknown:
                raise ValueError("能力列表只能是已登记的显式模块。")
        return self

    @property
    def is_paper_search(self) -> bool:
        """当前快照是否代表可执行的论文搜索主能力。"""
        return (
            self.status == RouteStatus.MATCHED
            and self.main_capability == MainCapability.PAPER_SEARCH
            and self.paper_search is not None
        )

    @property
    def is_video(self) -> bool:
        """当前快照是否代表可执行的文生视频主能力。"""
        return (
            self.status == RouteStatus.MATCHED
            and self.main_capability == MainCapability.VIDEO
            and self.video is not None
        )

    @property
    def is_career(self) -> bool:
        """当前快照是否代表可执行的生涯规划主能力。"""
        return (
            self.status == RouteStatus.MATCHED
            and self.main_capability == MainCapability.CAREER
            and self.career_contract is not None
        )
