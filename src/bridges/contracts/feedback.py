"""回答反馈与画像修正闭环契约（Issue 27）。

反馈是「回答—反馈—修正画像或策略—下一轮验证」A 方向多轮闭环的入口：
用户可区分「这次回答有问题」与「画像记录有误」，前者携带偏好反馈，
后者定位到具体画像断言并由画像治理 API（modify/freeze/withdraw）完成
修正。反馈按账户隔离持久化，同一条反馈幂等可重试，失败不丢失。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class FeedbackKind(StrEnum):
    """用户对一次回答的反馈类别。

    - ``answer_inappropriate``：这次回答有问题（携带偏好修正反馈）；
    - ``profile_incorrect``：画像记录有误（定位到具体断言并修正）。
    """

    ANSWER_INAPPROPRIATE = "answer_inappropriate"
    PROFILE_INCORRECT = "profile_incorrect"


class FeedbackStatus(StrEnum):
    """一条反馈的生命周期状态。

    - ``submitted``：已提交，等待处理；
    - ``resolved``：已按反馈修正画像或调整策略。
    """

    SUBMITTED = "submitted"
    RESOLVED = "resolved"


class AnswerFeedbackRequest(BaseModel):
    """提交一条对某条助手消息的反馈。

    ``kind=answer_inappropriate`` 时 ``feedback_text`` 说明问题、
    ``preference`` 可给出希望的回答偏好；``kind=profile_incorrect`` 时
    ``assertion_id`` 定位到使用的画像记录（来自上下文说明）。
    ``career_item_ref`` 可把反馈定位到生涯规划结果的具体条目
    （如 ``fact:1``/``assumption:2``/``suggestion:3``，来自生涯规划结果卡）。
    同一账户对同一消息的相同反馈（kind + assertion_id + career_item_ref +
    文本）幂等去重，失败重试不会产生重复记录。
    """

    kind: FeedbackKind = Field(description="反馈类别。")
    feedback_text: str = Field(
        min_length=1,
        max_length=500,
        description="反馈内容（中文说明问题所在或记录的错误之处）。",
    )
    preference: str | None = Field(
        default=None,
        max_length=500,
        description="回答不合适时的偏好修正（希望怎么回答）。",
    )
    assertion_id: str | None = Field(
        default=None,
        description="画像有误时定位的画像记录标识（来自上下文说明）。",
    )
    career_item_ref: str | None = Field(
        default=None,
        max_length=40,
        description="生涯规划结果的具体条目标识（逐项反馈定位，如 fact:1）。",
    )


class AnswerFeedback(BaseModel):
    """一条已持久化的回答反馈（按账户隔离）。"""

    feedback_id: str = Field(description="稳定反馈标识。")
    account_id: str = Field(description="提交反馈的账户标识。")
    conversation_id: str = Field(description="反馈所属对话标识。")
    message_id: str = Field(description="反馈针对的助手消息标识。")
    kind: FeedbackKind = Field(description="反馈类别。")
    feedback_text: str = Field(description="反馈内容。")
    preference: str | None = Field(default=None, description="偏好修正（可选）。")
    assertion_id: str | None = Field(default=None, description="定位的画像记录（可选）。")
    career_item_ref: str | None = Field(
        default=None, description="定位的生涯规划条目标识（可选）。"
    )
    status: FeedbackStatus = Field(description="反馈生命周期状态。")
    resolution_note: str | None = Field(
        default=None, description="已处理时的修正说明（可选）。"
    )
    created_at: datetime = Field(description="提交时间。")
    updated_at: datetime = Field(description="最近更新时间。")


class FeedbackResolveRequest(BaseModel):
    """把一条反馈标记为已处理并记录修正说明。"""

    resolution_note: str = Field(
        min_length=1,
        max_length=500,
        description="修正说明（如「已将表达偏好更新为……」）。",
    )
