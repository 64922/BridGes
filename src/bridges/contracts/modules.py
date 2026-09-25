"""日常模块子图的共享证据与等待状态合同（V2 Issue 11 首个子图建立）。

ADR-0030 与 ``docs/v2/architecture.md`` 第 8 节要求：所有外部适配器都提供
``query``、``evidence``、``retrieved_at``、``error`` 的统一记录，但各来源的
含义不被抹平。本模块把这份合同固化为可复用的类型，供论文子图（Issue 11）
及后续五个模块子图共用——后续模块只扩展自己的来源字段，不重新定义查询/
证据/时间的语义。

两种状态进入消息投影（长期权威源）：

- :class:`ModuleQueryRecord`：一次外部工具调用的真实记录（发送了什么查询、
  取得多少证据、何时取得、失败原因与是否可重试）；
- :class:`ModuleWaitState`：跨轮次的持久化等待状态（澄清问题等）。等待不
  靠内存协程跨请求存活：它随助手消息落库，恢复时读取权威记录与实际回复。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ModuleQueryStatus(StrEnum):
    """一次外部工具调用的结果分类（各来源共用，不隐藏失败）。"""

    SUCCESS = "success"
    EMPTY = "empty"
    SKIPPED = "skipped"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    RATE_LIMITED = "rate_limited"
    ERROR = "error"


class ModuleQueryRecord(BaseModel):
    """一次外部工具调用的统一记录：查询、证据、时间、错误。

    ``evidence_count`` 是本次调用真实取得的证据条数（0 表示确实没有取得，
    绝不表示"未查询"）；``error_message`` 必须是可操作的中文说明，且不得
    携带用户私有材料或上游原始正文。
    """

    source: str = Field(description="来源标识，例如 arxiv、crossref、openalex。")
    query: str = Field(description="实际发送给该来源的最小查询词（不含私有上下文）。")
    status: ModuleQueryStatus = Field(description="本次调用结果分类。")
    evidence_count: int = Field(default=0, ge=0, description="真实取得的证据条数。")
    retrieved_at: datetime | None = Field(default=None, description="取得证据的时间。")
    error_code: str | None = Field(default=None, description="稳定错误分类码。")
    error_message: str | None = Field(default=None, description="可操作的中文错误说明。")
    retryable: bool = Field(default=False, description="同一查询是否值得重试。")
    detail: str | None = Field(
        default=None, description="脱敏补充说明（例如缓存命中、陈旧回退、分页上限）。"
    )


class ModuleWaitState(BaseModel):
    """跨轮次的持久化等待状态：澄清问题与恢复载荷。

    ``context`` 只存该模块自己的可序列化恢复数据（不存服务、协程或私有材
    料原文的副本）；``origin_message_id`` 指向提问的助手消息，恢复时据此
    在会话内定位"等待中的那一轮"。
    """

    module_id: str = Field(description="等待所属模块（六个显式模块 ID 之一）。")
    kind: str = Field(description="等待类型，例如 clarification。")
    question: str = Field(description="已向用户提出的那一个问题（中文）。")
    origin_message_id: str = Field(description="提出该问题的助手消息标识。")
    context: dict[str, Any] = Field(
        default_factory=dict, description="模块自己的恢复载荷（JSON 可序列化）。"
    )
    created_at: datetime = Field(description="进入等待状态的时间。")
