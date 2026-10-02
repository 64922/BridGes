"""有界历史摘要缓存的共享合同（改进工单 13）。

``docs/上下文工程/改进方案.md`` §2 与决策 4/5 把「较早片段的结构化摘要」
定为**派生线索**：它由明确范围的已完成原始消息生成，带来源消息范围、
实例版本与覆盖边界；近期原文与最新纠正始终优先，摘要不得反过来成为用户
事实或改变学习题目/阶段。

本模块固化跨消费者共享的身份与常量：

- :class:`HistorySummary` 是一条**不可变摘要实例**（按覆盖片段缓存）；
  一条缓存的覆盖范围一经生成不再改写，来源变化即失效，重建回到原文，
  避免反复压缩上一版摘要而积累偏差。
- 实例只携带消息 ID、短线索与计数，**不携带生成者身份、凭据或未被覆盖
  的消息正文**；完整原文留在会话仓库，按 ID 读取。
- 生成口径（提示词/校验）变化递增 :data:`SUMMARY_GENERATOR_VERSION`，
  旧实例不被复用为当前依据。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

#: 摘要缓存合同版本（字段或语义变化时递增）。
SUMMARY_CONTRACT_VERSION = "summary-cache-v1"

#: 摘要实例版本：由哪一版生成口径（提示词 + 校验规则）产出。
SUMMARY_GENERATOR_VERSION = "summary-gen-v2"

#: 单条摘要正文最大字符数（有界；不要求每条旧消息留一行）。
SUMMARY_TEXT_MAX_CHARS = 1200
#: 单条对象线索/开放问题的最大字符数。
SUMMARY_ITEM_MAX_CHARS = 200
#: 对象线索与开放问题的条数上限。
SUMMARY_MAX_OBJECT_CLUES = 12
SUMMARY_MAX_OPEN_QUESTIONS = 8
#: 一次摘要生成可读取的来源原文 token 估算上限（按最旧在前截取）。
SUMMARY_MAX_SOURCE_TOKENS = 6000
#: 摘要调用的输出额度（token）。
SUMMARY_OUTPUT_TOKENS = 512
#: 同步限时补齐的调用墙钟上限（毫秒）。
SUMMARY_SYNC_TIMEOUT_MS = 8000
#: 后台整次摘要任务共享的墙钟上限（毫秒）。
SUMMARY_BACKGROUND_TIMEOUT_MS = 20000
#: 后台摘要任务的最大尝试次数（队列重试上限）。
SUMMARY_MAX_ATTEMPTS = 3
#: 后台单次任务最多生成的连续片段数（限制单任务调用/token 消耗）。
SUMMARY_MAX_CALLS_PER_TASK = 4
#: 触发后台准备的「新增未覆盖原文量」下限：不足时不调用摘要模型
#: （正常短对话因此永不触发）。
SUMMARY_MIN_NEW_MESSAGES = 2


class SummaryStatus(StrEnum):
    """摘要实例状态：有效（可复用）或已失效（保留审计，不复用）。"""

    ACTIVE = "active"
    INVALIDATED = "invalidated"


class SummarySourceMessage(BaseModel):
    """进入摘要生成的一条已完成原始消息（只读快照）。"""

    message_id: str
    role: str
    content: str


class HistorySummary(BaseModel):
    """一条按覆盖片段缓存的有界结构化摘要（不可变实例）。

    ``covered_first/last_message_id`` 与 ``covered_message_count`` 是覆盖边界；
    ``source_fingerprint`` 是覆盖范围原文的确定性指纹——任一条来源被删除或
    修改都会失配，消费者据此失效缓存，绝不把过期依据当作当前事实。
    """

    model_config = ConfigDict(frozen=True)

    summary_id: str
    account_id: str
    conversation_id: str
    contract_version: str = SUMMARY_CONTRACT_VERSION
    instance_version: str = SUMMARY_GENERATOR_VERSION
    covered_first_message_id: str
    covered_last_message_id: str
    covered_message_count: int
    source_fingerprint: str
    text: str
    object_clues: tuple[str, ...] = ()
    open_questions: tuple[str, ...] = ()
    status: SummaryStatus = SummaryStatus.ACTIVE
    invalidated_reason: str | None = None
    invalidated_at: datetime | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    created_at: datetime
    updated_at: datetime


__all__ = [
    "SUMMARY_BACKGROUND_TIMEOUT_MS",
    "SUMMARY_CONTRACT_VERSION",
    "SUMMARY_GENERATOR_VERSION",
    "SUMMARY_ITEM_MAX_CHARS",
    "SUMMARY_MAX_ATTEMPTS",
    "SUMMARY_MAX_CALLS_PER_TASK",
    "SUMMARY_MAX_OBJECT_CLUES",
    "SUMMARY_MAX_OPEN_QUESTIONS",
    "SUMMARY_MAX_SOURCE_TOKENS",
    "SUMMARY_MIN_NEW_MESSAGES",
    "SUMMARY_OUTPUT_TOKENS",
    "SUMMARY_SYNC_TIMEOUT_MS",
    "SUMMARY_TEXT_MAX_CHARS",
    "HistorySummary",
    "SummarySourceMessage",
    "SummaryStatus",
]
