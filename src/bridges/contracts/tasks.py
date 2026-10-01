"""跨轮任务、任务版本、有效条件与澄清等待的权威合同（改进工单 08）。

本模块把 ``docs/workflow/orchestration.md`` 第 3 节的「会话 / 跨轮任务 /
任务版本 / 单次运行 / 节点产物」与 ``docs/上下文工程/改进方案.md`` 第 1 节
的「有效会话状态」固化为可复用的类型，供任务领域仓库、API 投影与后续
消费者（工单 11 定位对象、12 产生任务关系、09/10 运行账本）共用。

三条不可让渡的语义：

1. **有来源**：每条有效条件都携带 ``source_message_id`` 与原话范围
   ``source_span``；无法定位来源的推测不得成为用户约束。
2. **来源分级**：用户明示直接生效（``effective``），助手未获接受的方案
   是草案（``draft``），模型推测只作线索（``clue``），工具事实保留自身
   证据来源（``effective`` + ``tool_observation``）。草案与线索不进入
   「有效条件」投影。
3. **不可变版本**：条件修订生成新的任务版本，旧版本与旧条件值只读保留；
   被取代（``superseded``）或撤销（``revoked``）的条件值不因话题往返复活。

任务状态与单次运行状态、产物可信状态相互独立：消息 ``done`` 可以只是
一次澄清成功，任务仍在等待输入（见 ``docs/workflow/orchestration.md``
第 4 节）。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

#: 本模块合同的版本标识。任务行保存该值，供跨版本读取与迁移审计。
TASK_CONTRACT_VERSION = "task-v1"


class TaskStatus(StrEnum):
    """跨轮任务的当前状态；与单次运行状态、产物可信状态分离。"""

    ACTIVE = "active"
    WAITING = "waiting"
    PAUSED = "paused"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TaskRelation(StrEnum):
    """一轮理解产出的有限任务关系。

    ``new`` 是「本消息属于新任务」；没有目标也没有条件的普通聊天不创建
    任务（普通聊天保持轻量）。关系由工单 12 的主智能体理解产出，本领域
    只做代码校验与落地。

    ``complete`` 与 ``block`` 让 ``completed`` / ``blocked`` 成为可达状态：
    完成任务仍可被后续 ``revise`` 重新激活（任务内容第 7 条「完成目标可
    继续修订」），阻塞任务则不再接收澄清答复。
    """

    NEW = "new"
    CONTINUE = "continue"
    REVISE = "revise"
    PAUSE = "pause"
    COMPLETE = "complete"
    BLOCK = "block"
    CANCEL = "cancel"


class TaskEventKind(StrEnum):
    """任务审计事件的种类（单一事实源，避免各处各写一份字面量）。"""

    TASK_CREATED = "task_created"
    VERSION_CREATED = "version_created"
    TASK_PAUSED = "task_paused"
    TASK_RESUMED = "task_resumed"
    TASK_ACTIVATED = "task_activated"
    TASK_COMPLETED = "task_completed"
    TASK_BLOCKED = "task_blocked"
    TASK_CANCELLED = "task_cancelled"
    CONDITION_REVOKED = "condition_revoked"
    WAIT_RESOLVED = "wait_resolved"
    WAITS_SUSPENDED = "waits_suspended"
    WAITS_EXPIRED = "waits_expired"


class ConditionScope(StrEnum):
    """条件的有效范围：默认绑定原任务，用户明示才扩展到整个会话。"""

    TASK = "task"
    CONVERSATION = "conversation"


class ConditionOrigin(StrEnum):
    """条件来源分级；决定它是否可以直接成为约束。"""

    USER_STATED = "user_stated"
    ASSISTANT_PROPOSAL = "assistant_proposal"
    MODEL_INFERENCE = "model_inference"
    TOOL_OBSERVATION = "tool_observation"


class ConditionStatus(StrEnum):
    """条件的有效状态；只有 ``EFFECTIVE`` 进入有效条件投影。"""

    EFFECTIVE = "effective"
    DRAFT = "draft"
    CLUE = "clue"
    SUPERSEDED = "superseded"
    REVOKED = "revoked"


class WaitStatus(StrEnum):
    """澄清等待的生命周期状态。

    ``suspended`` 表示等待项仍存在但已失去当前激活状态（用户换了话题）：
    它不会被后续消息误填，也不会被悄悄恢复为 ``open``。
    """

    OPEN = "open"
    SUSPENDED = "suspended"
    RESOLVED = "resolved"
    EXPIRED = "expired"


class WaitResolution(StrEnum):
    """一次用户消息对等待项的处置结果。"""

    NONE = "none"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


#: 来源 → 落地状态：用户明示直接生效，助手草案是草案，模型推测只作线索，
#: 工具事实保留自身证据来源并直接生效。
_ORIGIN_STATUS: dict[ConditionOrigin, ConditionStatus] = {
    ConditionOrigin.USER_STATED: ConditionStatus.EFFECTIVE,
    ConditionOrigin.ASSISTANT_PROPOSAL: ConditionStatus.DRAFT,
    ConditionOrigin.MODEL_INFERENCE: ConditionStatus.CLUE,
    ConditionOrigin.TOOL_OBSERVATION: ConditionStatus.EFFECTIVE,
}


def status_for_origin(origin: ConditionOrigin) -> ConditionStatus:
    """返回某来源对应的落地状态（单一事实源，避免各处各写一份映射）。"""
    return _ORIGIN_STATUS[origin]


class TaskConditionInput(BaseModel):
    """一轮输入中提出的一条条件（尚未落地）。

    ``replaces`` 指向被本条纠正/撤销的旧条件 ID；同一任务内同 ``kind`` 的
    明示修订也会自动取代旧值（``5,000 改 3,000``）。
    """

    kind: str = Field(min_length=1, description="条件类别，例如 budget、exclusion、requirement。")
    text: str = Field(min_length=1, description="条件的原话正文（用户可见）。")
    scope: ConditionScope = Field(
        default=ConditionScope.TASK, description="有效范围：默认绑定原任务。"
    )
    origin: ConditionOrigin = Field(
        default=ConditionOrigin.USER_STATED, description="来源分级，决定是否直接生效。"
    )
    source_message_id: str = Field(min_length=1, description="条件来源的用户消息 ID。")
    source_span: str | None = Field(
        default=None, description="原话范围（可定位的最小片段），无定位时为 null。"
    )
    replaces: str | None = Field(default=None, description="被本条明示纠正/撤销的旧条件 ID。")


class TaskCondition(BaseModel):
    """一条已落地的条件（含来源与取代关系）。"""

    condition_id: str
    task_id: str
    version: int = Field(description="提出该条件的任务版本号。")
    kind: str
    text: str
    scope: ConditionScope
    origin: ConditionOrigin
    status: ConditionStatus
    source_message_id: str
    source_span: str | None = None
    supersedes_condition_id: str | None = Field(default=None, description="本条取代的旧条件 ID。")
    superseded_by: str | None = Field(
        default=None, description="取代本条的新条件 ID；非空即表示本条已失效。"
    )
    created_at: datetime
    updated_at: datetime


class TaskVersion(BaseModel):
    """任务在某次条件确定后的不可变快照。

    ``result_refs`` 保存该版本对应结果的引用（产物/运行 ID），只存引用不
    存正文：任务领域拥有写模型，产物由各自模块拥有（任务内容第 2 条
    「结果引用」、跨票接缝第 10 条）。
    """

    version_id: str
    task_id: str
    version: int
    goal: str
    condition_ids: list[str] = Field(default_factory=list)
    source_message_ids: list[str] = Field(default_factory=list)
    result_refs: list[str] = Field(default_factory=list)
    supersedes_version: int | None = None
    invalidated_at: datetime | None = None
    invalidation_reason: str | None = None
    created_at: datetime


class TaskWait(BaseModel):
    """一次跨轮澄清等待。

    等待项不携带工作租约：进入等待即释放执行资源（见
    ``docs/workflow/orchestration.md`` 第 3 节）。``expected_version`` 固定
    提问时的任务版本，答复必须匹配该版本才被接受。
    """

    wait_id: str
    task_id: str
    conversation_id: str
    expected_version: int
    missing_fields: list[str] = Field(default_factory=list)
    question: str
    origin_message_id: str = Field(description="提出该问题的助手消息 ID。")
    status: WaitStatus
    source_message_id: str = Field(description="触发该等待的用户消息 ID。")
    resolved_by_message_id: str | None = None
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None = None
    released_at: datetime | None = Field(
        default=None, description="终态释放时刻；非空即表示不再占用执行资源。"
    )


class TaskRecord(BaseModel):
    """跨轮任务的行投影（不含条件明细，明细按需查询）。"""

    task_id: str
    account_id: str
    conversation_id: str
    goal: str
    status: TaskStatus
    current_version: int
    contract_version: str = TASK_CONTRACT_VERSION
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None


class TaskProjection(BaseModel):
    """任务对外投影：有效条件 + 未决等待 + 来源。

    ``effective_conditions`` 只包含仍有效的用户约束与工具事实；草案与线索
    不在此列（它们不是用户约束）。``open_waits`` 只包含当前激活的等待项。
    """

    task: TaskRecord
    effective_conditions: list[TaskCondition] = Field(default_factory=list)
    conversation_conditions: list[TaskCondition] = Field(default_factory=list)
    open_waits: list[TaskWait] = Field(default_factory=list)


class TaskTurnRequest(BaseModel):
    """一次任务关系落地请求（由工单 12 的主智能体理解后提交）。

    普通聊天（无目标、无条件）不创建任务：这是「普通聊天轻量」的强制点。
    """

    conversation_id: str = Field(min_length=1)
    user_message_id: str = Field(min_length=1)
    relation: TaskRelation
    goal: str | None = Field(default=None, description="本消息表达的任务目标（若有）。")
    explicit_task_id: str | None = Field(
        default=None, description="用户明确指代的任务 ID；缺省用会话当前任务。"
    )
    conditions: list[TaskConditionInput] = Field(default_factory=list)
    revoked_condition_ids: list[str] = Field(
        default_factory=list,
        description="用户明确撤销的条件 ID；被撤销的值不因话题往返复活。",
    )
    result_refs: list[str] = Field(
        default_factory=list,
        description="本版本对应结果的引用（产物/运行 ID），只存引用不存正文。",
    )
    is_new_topic: bool = Field(
        default=False, description="本消息是否明确开启无关话题（暂停旧任务）。"
    )
    is_profile_command: bool = Field(
        default=False, description="画像命令（记住/忘掉）：不得填旧等待。"
    )
    is_learning_action: bool = Field(default=False, description="学习阶段动作：不得填旧等待。")
    expected_version: int | None = Field(
        default=None, description="乐观版本：与当前版本不符时拒绝修订。"
    )
    answer_fields: list[str] = Field(
        default_factory=list,
        description="本消息实际补齐的缺失字段；用于等待匹配。",
    )
    answer_text: str | None = Field(
        default=None, description="本消息作为澄清答复的正文（用于审计，可空）。"
    )


class TaskTurnResult(BaseModel):
    """一次任务关系落地的结果投影。"""

    created: bool = Field(description="本轮是否创建了新任务。")
    task: TaskProjection | None = None
    paused_task_ids: list[str] = Field(default_factory=list)
    cancelled_task_ids: list[str] = Field(default_factory=list)
    wait_resolution: WaitResolution = WaitResolution.NONE
    resolved_wait: TaskWait | None = None
    wait_rejection_reason: str | None = None
    events: list[TaskEventKind] = Field(
        default_factory=list, description="本轮产生的任务事件类型。"
    )


class TaskEvent(BaseModel):
    """任务审计事件（append-only）。"""

    event_id: str
    account_id: str
    conversation_id: str
    task_id: str | None
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
