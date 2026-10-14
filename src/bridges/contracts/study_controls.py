"""用户学习控制与阶段小结的公开投影与语义合同。

学习控制（直接讲解、跳过、暂停、继续、要求小结）由导师按完整语义识别，
与消息/进展版本绑定；阶段小结只依据实际学习证据，区分即时反馈、正式
诊断与尚未核验，不输出掌握百分比。私有评价依据与生成输入不进入投影。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

#: 学习控制记录的协议版本；控制行随记录冻结同一版本。
CONTROLS_CONTRACT_VERSION = "study-controls-v1"

#: 阶段小结的协议版本；小结行与其依据引用记录同一版本。
SUMMARY_CONTRACT_VERSION = "study-summary-v1"


class StudyControlKind(StrEnum):
    """用户明确的学习控制；按完整语义识别，不是关键词匹配。"""

    FULL_EXPLANATION = "full_explanation"
    SKIP = "skip"
    PAUSE = "pause"
    RESUME = "resume"
    SUMMARY_REQUEST = "summary_request"


class StudySummaryTrigger(StrEnum):
    """小结的触发来源：用户要求、完成后的自然收尾或暂停收尾。"""

    EXPLICIT_REQUEST = "explicit_request"
    NATURAL_COMPLETION = "natural_completion"
    PAUSE = "pause"


class StudySummaryStatus(StrEnum):
    """小结的真实交付状态；失败或部分展示必须如实呈现。"""

    COMPLETE = "complete"
    PARTIAL = "partial"


class StudyControlProjection(BaseModel):
    """最近一次学习控制的用户可见投影；含作用的问题与是否解除等待。"""

    control_id: str = Field(description="稳定控制记录标识。")
    kind: StudyControlKind = Field(description="控制类型。")
    question: str = Field(
        default="", description="本次控制作用的待答问题；没有关联时为空。"
    )
    released: bool = Field(
        default=False, description="是否解除了该问题的作答等待。"
    )
    created_at: datetime = Field(description="控制生效时间。")


class StudySessionPauseProjection(BaseModel):
    """当前暂停状态：可恢复位置与暂停时的问题处置。"""

    pause_id: str = Field(description="稳定暂停记录标识。")
    topic: str = Field(default="", description="暂停时的学习主题。")
    position: str = Field(
        default="", description="可继续的位置（暂停时的当前关注内容）。"
    )
    pending_question: str = Field(
        default="", description="暂停时保留的待答问题；已解除或没有时为空。"
    )
    progress_version: int = Field(ge=1, description="绑定的会话进展版本。")
    source_message_id: str = Field(description="声明暂停的用户消息标识。")
    paused_at: datetime = Field(description="暂停时间。")


class StudySessionSummaryProjection(BaseModel):
    """最近一次阶段小结：规范原文与真实交付状态。

    依据引用（证据、进展版本与生成时问题处置）保存在服务端，不在此
    投影中重复输出；``content`` 是生成时确定的规范小结文字，不随后续
    学习被覆盖。
    """

    summary_id: str = Field(description="稳定小结标识。")
    trigger: StudySummaryTrigger = Field(description="小结触发来源。")
    status: StudySummaryStatus = Field(
        description="complete：原文已完整随回复交付；partial：回复未完整包含原文。"
    )
    content: str = Field(description="生成时确定的规范小结文字。")
    source_message_id: str = Field(description="请求/触发该小结的用户消息标识。")
    assistant_message_id: str = Field(description="承载该小结的助手消息标识。")
    created_at: datetime = Field(description="小结生成时间。")
