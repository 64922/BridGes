"""学习历史只读视图合同，与学习状态写模型分开。"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from bridges.contracts.study import (
    StudyExchange,
    StudyPage,
    StudyPageUpdate,
    StudyQuestion,
    StudyReview,
    StudyScope,
    StudySummary,
    StudySummaryRecord,
    StudyUnit,
)

StudyMaterialStatus = Literal["active", "pending_cleanup", "deleted", "unknown"]


class StudyHistoryStatus(StrEnum):
    """学习状态行在只读历史投影中的解释状态。"""

    LEGACY = "legacy"
    UNSUPPORTED = "unsupported"
    UNREADABLE = "unreadable"


class StudyHistoryStatusProjection(BaseModel):
    """旧版、未知版或不可解码学习状态的公开说明。"""

    status: StudyHistoryStatus = Field(description="历史学习状态的解释结果。")
    version: int | None = Field(
        default=None, ge=1, description="原记录声明的学习状态合同版本。"
    )
    message: str = Field(min_length=1, description="面向用户的固定说明。")


class StudyMaterialStatusProjection(BaseModel):
    """材料对象的账户范围内存储状态；不包含材料内容。"""

    object_id: str = Field(description="学习历史引用的材料对象标识。")
    status: StudyMaterialStatus = Field(
        description="对象存储中的当前状态。"
    )


# 历史读取 DTO 保留既有 OpenAPI StudyState schema 名与 JSON 结构。
class StudyState(BaseModel):

    subsection_id: str
    stage: Literal[
        "awaiting_pages", "recognizing", "preview", "tutoring", "review", "summary"
    ] = "awaiting_pages"
    wait_reason: str | None = None
    pages: list[StudyPage] = Field(default_factory=list)
    units: list[StudyUnit] = Field(default_factory=list)
    questions: list[StudyQuestion] = Field(default_factory=list)
    tutoring: list[StudyExchange] = Field(default_factory=list)
    page_update: StudyPageUpdate | None = None
    review: StudyReview | None = None
    summary: StudySummary | None = None
    summary_history: list[StudySummaryRecord] = Field(default_factory=list)
    pending_object_ids: list[str] = Field(default_factory=list)
    scope: StudyScope | None = None
    scope_history: list[StudyScope] = Field(default_factory=list)
    state_version: int = 1
    materials: list[StudyMaterialStatusProjection] = Field(
        default_factory=list,
        description="学习历史引用材料的当前对象存储状态，不包含材料内容。",
    )


StudyHistoryProjection = StudyState
