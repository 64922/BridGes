"""会话局部学习进展的公开投影。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class StudyLocalProgressProjection(BaseModel):
    """单个学习对话的轻量进展，不包含模型提示词或完整上下文。"""

    topic: str = Field(min_length=1, max_length=160, description="当前对话主题。")
    knowledge_context: str = Field(
        default="", max_length=1600, description="最近一次已完成讲解的短摘录。"
    )
    current_focus: str = Field(max_length=320, description="最近一次成功回复对应的问题。")
    source_message_ids: list[str] = Field(
        default_factory=list, description="进展所依据的用户与助手消息标识。"
    )
    version: int = Field(ge=1, description="局部进展的递增版本。")
    runtime_run_id: str = Field(min_length=1, description="最近一次成功写入进展的运行标识。")
    updated_at: datetime = Field(description="最近一次成功更新进展的时间。")
