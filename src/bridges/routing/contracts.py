"""自然语言能力路由的版本化合同。"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator


ROUTE_CONTRACT_VERSION = "2026.08.09"


class MainCapability(StrEnum):
    """当前主能力集合；后续能力只能以新注册项追加。"""

    ORDINARY_CHAT = "ordinary_chat"
    PAPER_SEARCH = "paper_search"
    CLARIFICATION = "clarification"
    HUMANIZER = "humanizer"
    IMAGE = "image"
    VIDEO = "video"
    CAREER = "career"


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

    version: str = ROUTE_CONTRACT_VERSION
    normalized_query: str = Field(min_length=1, max_length=240)
    constraints: PaperSearchConstraints


class CapabilityRoute(BaseModel):
    """一次用户消息的主能力路由快照。"""

    model_config = ConfigDict(extra="forbid")

    version: str = ROUTE_CONTRACT_VERSION
    status: RouteStatus
    main_capability: MainCapability
    confidence: Annotated[float, Field(ge=0, le=1)]
    reason: str = Field(min_length=1, max_length=240)
    paper_search: PaperSearchPlan | None = None
    clarification_question: str | None = Field(default=None, max_length=240)
    error_code: str | None = Field(default=None, max_length=80)
    knowledge_base_allowed: bool = True
    web_search_allowed: bool = True
    side_effects: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_capability_payload(self) -> CapabilityRoute:
        if self.main_capability == MainCapability.PAPER_SEARCH:
            if self.status == RouteStatus.MATCHED and self.paper_search is None:
                raise ValueError("论文搜索命中必须携带搜索计划。")
            if self.status != RouteStatus.MATCHED and self.paper_search is not None:
                raise ValueError("未命中论文搜索不能携带可执行计划。")
        elif self.paper_search is not None:
            raise ValueError("非论文主能力不能携带论文搜索计划。")
        if self.status in {RouteStatus.CLARIFY, RouteStatus.REJECTED} and not (
            self.clarification_question or self.error_code
        ):
            raise ValueError("澄清或拒绝路由必须有可操作反馈。")
        if self.status == RouteStatus.MATCHED and self.clarification_question:
            raise ValueError("已命中路由不能同时要求澄清。")
        return self

    @property
    def is_paper_search(self) -> bool:
        """当前快照是否代表可执行的论文搜索主能力。"""
        return (
            self.status == RouteStatus.MATCHED
            and self.main_capability == MainCapability.PAPER_SEARCH
            and self.paper_search is not None
        )
