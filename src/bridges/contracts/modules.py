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

from bridges.contracts.ai import ModelRunLock


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


class ModuleDelivery(BaseModel):
    """模块在「不自行结束助手消息」模式下返回的完整交付（工单 37 复合接缝）。

    模块仍在内核里提交真实产物与完成收据，但不调用 ``finalize_message``；
    复合调度器统一核验后，把每个分支的投影与最终正文在同一事务里一次提交。
    ``message_status`` 是 :class:`~bridges.contracts.chat.ChatMessageStatus`
    的值；失败分支保留错误投影与可重试性，由复合调度器按必要/可选步骤决定
    阻塞相关结论还是保留有效部分。``wait_reason`` 供澄清等待写入运行表。
    """

    module_id: str = Field(description="模块标识（与路由/配方一致）。")
    status: str = Field(description="模块投影状态值（成功/澄清/空/失败/停止）。")
    projection_field: str = Field(description="助手消息投影字段名，例如 paper_search。")
    projection: dict[str, Any] = Field(description="模块投影（JSON 可序列化）。")
    content: str = Field(default="", description="该分支候选正文（综合前，不直接终态）。")
    message_status: str = Field(description="ChatMessageStatus 的值。")
    error_node: str | None = Field(default=None, description="失败所在节点。")
    error_code: str | None = Field(default=None, description="稳定错误分类码。")
    error_message: str | None = Field(default=None, description="可操作的中文错误说明。")
    retryable: bool = Field(default=False, description="失败是否值得重试。")
    artifact_refs: dict[str, str] = Field(
        default_factory=dict, description="节点 → 产物 ID（引用，不复制载荷）。"
    )
    lock: ModelRunLock | None = Field(
        default=None, description="该分支实际生成的模型运行锁（可序列化）。"
    )
    wait_reason: str | None = Field(default=None, description="持久化等待原因（澄清等）。")
