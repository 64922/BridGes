"""聊天自然语言能力路由的版本化公开契约（Issue 06/10）。"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from bridges.contracts.career import CareerPlanningRouteContract

ROUTING_CONTRACT_VERSION = "chat-routing-1"


class RouteCapability(StrEnum):
    """一条消息允许选择的主能力。"""

    CHAT = "chat"
    CAREER_PLANNING = "career_planning"
    LEARNING_PLAN = "learning_plan"
    HUMANIZER = "humanizer"
    PAPER_SEARCH = "paper_search"
    IMAGE_GENERATION = "image_generation"
    VIDEO_GENERATION = "video_generation"
    MCP_CALL = "mcp_call"
    CLARIFY = "clarify"


class RouteConfidence(StrEnum):
    """确定性路由的置信等级。"""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class RouteDecision(BaseModel):
    """副作用前固化的一条主能力路由快照。"""

    version: Literal["chat-routing-1"] = Field(default=ROUTING_CONTRACT_VERSION)
    capability: RouteCapability
    confidence: RouteConfidence
    normalized_query: str = Field(description="用于能力编排的当前消息摘要。")
    reason_code: str = Field(description="稳定的确定性判定原因码。")
    conflict_capabilities: list[RouteCapability] = Field(
        default_factory=list,
        description="互斥能力；非空时 capability 必须为 clarify。",
    )
    clarification: str | None = Field(
        default=None, description="仅在需要用户选择主能力时提出的一个问题。"
    )
    career_contract: CareerPlanningRouteContract | None = Field(
        default=None, description="生涯规划路由与规划共享的最小合同。"
    )


__all__ = [
    "ROUTING_CONTRACT_VERSION",
    "RouteCapability",
    "RouteConfidence",
    "RouteDecision",
]
