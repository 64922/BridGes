"""学习目标与导师路线的公开投影。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

#: 路线来源：导师方案（助手提出）或按用户要求调整。
StudyGoalRouteOrigin = Literal["assistant_proposal", "user_request"]
#: 目标活动状态；07 只创建 active 目标，暂停/完成由后续票接入。
StudyGoalStatus = Literal["active", "paused", "completed"]
#: 目标关联/切换事件类型（Issue 08）。冲突与澄清事件也保留，供反馈与审计。
StudyGoalEventKind = Literal[
    "link", "switch_out", "conflict", "clarification", "clarification_lapsed"
]
#: 用户可见的目标通知语义；由权威事件派生，不由客户端自行切换名字。
StudyGoalNoticeKind = Literal["restored", "switched", "conflict", "clarification"]


class StudyRouteNode(BaseModel):
    """路线中的一个知识点；不含掌握状态，跳节点不代表前面已完成。"""

    node_id: str = Field(min_length=1, max_length=64, description="路线内稳定节点标识。")
    title: str = Field(min_length=1, max_length=80, description="知识点名称。")
    prerequisites: list[str] = Field(
        default_factory=list, description="前置知识点的节点标识；只引用本路线内节点。"
    )
    depth: str = Field(default="", max_length=20, description="该节点的教学深度提示。")


class StudyRouteProjection(BaseModel):
    """某一目标版本的学习路线（简短、可调整的导师方案）。"""

    version: int = Field(ge=1, description="路线递增版本；修订保留历史版本。")
    nodes: list[StudyRouteNode] = Field(
        default_factory=list, description="按教学顺序排列的知识点。"
    )
    current_node_id: str = Field(
        default="", max_length=64, description="当前推进的节点标识。"
    )
    revision_reason: str = Field(
        default="", max_length=200, description="形成或修订本版本的依据。"
    )
    origin: StudyGoalRouteOrigin = Field(
        description="路线来源：导师方案或按用户要求调整。"
    )
    created_at: datetime = Field(description="本版本创建时间。")


class StudyGoalProjection(BaseModel):
    """账户级长期学习目标；身份独立于会话，会话通过引用关联。"""

    goal_id: str = Field(min_length=1, description="稳定目标标识。")
    statement: str = Field(min_length=1, max_length=2000, description="用户目标原话。")
    observable_result: str = Field(
        default="", max_length=500, description="用户明确说出的可观察结果。"
    )
    scope: str = Field(default="", max_length=500, description="用户明确说出的适用范围。")
    constraints: str = Field(default="", max_length=500, description="用户明确说出的限制条件。")
    status: StudyGoalStatus = Field(default="active", description="目标活动状态。")
    version: int = Field(ge=1, description="目标递增版本。")
    source_message_id: str = Field(min_length=1, description="用户目标原话的来源消息。")
    created_at: datetime = Field(description="创建时间。")
    updated_at: datetime = Field(description="最近更新时间。")
    route: StudyRouteProjection | None = Field(
        default=None, description="当前生效的学习路线；尚未形成时为 null。"
    )


class StudyGoalSummaryProjection(BaseModel):
    """账户内本人目标的选择/续学条目；不含他人目标与无权来源。"""

    goal_id: str = Field(min_length=1, description="稳定目标标识。")
    statement: str = Field(min_length=1, max_length=2000, description="用户目标原话。")
    status: StudyGoalStatus = Field(description="目标活动状态。")
    current_node_title: str = Field(
        default="", max_length=80, description="当前路线节点的名称；未形成路线时为空。"
    )
    route_version: int = Field(default=0, ge=0, description="当前路线版本；未形成时为 0。")
    updated_at: datetime = Field(description="最近更新时间。")


class StudyGoalListProjection(BaseModel):
    """本人目标列表：无目标时为空列表，加载失败由接口层如实报错。"""

    goals: list[StudyGoalSummaryProjection] = Field(default_factory=list)


class StudyGoalNoticeProjection(BaseModel):
    """会话目标通知：恢复/切换/冲突/待确认由权威事件派生。"""

    kind: StudyGoalNoticeKind = Field(description="通知语义。")
    message: str = Field(min_length=1, description="固定中文状态文案。")
    goal_statement: str = Field(default="", description="相关目标原话；无则空。")
    created_at: datetime = Field(description="事件发生时间。")
