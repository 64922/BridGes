"""进行中事项与近期工作上下文的合同（画像工单 13）。

事项投影从原子记忆的明确事实派生：目标是用户明确表达且仍在进行（或已暂停）
的目标条目，进展与待续问题是与该目标有确定性关联的原子条目本身。投影不新增
事实、不写心理判断，也不把助手建议解释为用户承诺；待续问题必须能回到真实
交流证据（条目携带的来源消息）。

近期工作上下文是这份投影的带版本派生视图：依赖条目版本、来源消息可得性与
账户控制状态；条目修改、来源删除或控制变化会立即让它失效，重建写入必须携带
失效前捕获的代数（epoch），迟到重建不能覆盖新状态。上下文只用于编译回答
切片，绝不作为独立事实源。

其他领域（如学习目标）的事项只以「领域引用」进入投影：状态由所属领域通过
注册的提供方在读取时解析，本模块与画像存储都不复制领域状态；没有接通提供方
时如实标记未解析，绝不伪造学习状态（学习专门对接由工单 14 完成）。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from bridges.contracts.atomic_profile import (
    AtomicProfileDomainReference,
    AtomicProfileGoalState,
    AtomicProfileValidityStatus,
)

#: 上下文派生合同的版本标识；派生逻辑变化时旧快照一律视为失效重建。
#: v2（画像工单 14）：指纹加入领域引用解析结果（状态、版本），学习领域的
#: 纠正/删除/暂停/切换会让个人近期上下文立即失效。
ONGOING_CONTEXT_CONTRACT_VERSION = "ongoing-context-v2"


class OngoingItemRole(StrEnum):
    """一条投影条目在事项里的角色：目标、进展或待续问题。"""

    GOAL = "goal"
    PROGRESS = "progress"
    PENDING_QUESTION = "pending_question"


class OngoingGoalAction(StrEnum):
    """页面对事项的明确操作；只作用于精确定位的单个目标。"""

    PAUSE = "pause"
    COMPLETE = "complete"
    RESUME = "resume"


class OngoingDomainReferenceState(BaseModel):
    """所属领域返回的实时解析结果（不落画像库）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    display: str = Field(
        min_length=1, max_length=200, description="所属领域提供的展示文本。"
    )
    state_code: str | None = Field(
        default=None,
        max_length=40,
        description="所属领域自己的状态标记（如 active/paused），原文透传。",
    )
    version: int | None = Field(
        default=None,
        ge=1,
        description=(
            "所属领域对象的当前版本（画像工单 14）；展示与跨会话并发操作"
            "的守卫依据，领域不提供版本时为空。"
        ),
    )


class OngoingDomainReferenceProjection(BaseModel):
    """投影里的领域引用：未解析时如实留空，不伪造领域状态。"""

    model_config = ConfigDict(extra="forbid")

    domain: str = Field(description="所属领域标识。")
    reference_id: str = Field(description="领域内对象标识。")
    resolved: bool = Field(description="本次读取是否成功取得领域状态。")
    display: str | None = Field(
        default=None, description="所属领域返回的展示文本；未解析时为空。"
    )
    state_code: str | None = Field(
        default=None, description="所属领域的状态标记；未解析时为空。"
    )
    version: int | None = Field(
        default=None,
        description=(
            "所属领域对象的当前版本（画像工单 14）；未解析或领域不提供时为空。"
        ),
    )


class OngoingDomainReferenceError(Exception):
    """领域引用状态操作的域错误（跨领域接缝；画像工单 14）。

    学习等所属领域在代理个人记忆页的状态操作时抛出，由个人记忆 API 映射
    为稳定 HTTP 状态；错误必须如实说明冲突或不可用，不能静默改写领域状态。
    """

    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class OngoingEntryProjection(BaseModel):
    """事项投影里的一条条目（目标是目标本身，其余是关联事实）。"""

    model_config = ConfigDict(extra="forbid")

    role: OngoingItemRole = Field(description="本条在事项里的角色。")
    profile_item_id: str = Field(description="稳定的原子条目标识。")
    version: int = Field(ge=1, description="操作使用的乐观锁版本号。")
    text: str = Field(description="条目正文（原样，不新增摘要）。")
    goal_state: AtomicProfileGoalState = Field(description="目标生命周期标记。")
    validity_status: AtomicProfileValidityStatus = Field(
        description="按有效期与目标状态折算的当前时效状态。"
    )
    updated_at: datetime = Field(description="最近一次变化时间。")
    source_message_ids: list[str] = Field(
        default_factory=list, description="证据来源消息标识；待续问题的交流依据。"
    )


class OngoingItemProjection(BaseModel):
    """一个进行中事项：目标 + 实际进展 + 待续问题 + 领域引用。"""

    model_config = ConfigDict(extra="forbid")

    goal: OngoingEntryProjection = Field(description="目标条目。")
    progress: list[OngoingEntryProjection] = Field(
        default_factory=list, description="与该目标关联、有真实交流依据的进展。"
    )
    pending_questions: list[OngoingEntryProjection] = Field(
        default_factory=list, description="与该目标关联、有真实交流依据的待续问题。"
    )
    domain_references: list[OngoingDomainReferenceProjection] = Field(
        default_factory=list, description="领域引用（状态实时解析，不落库复制）。"
    )


class OngoingItemsProjection(BaseModel):
    """「进行中事项」页面投影（加载/空态由前端按 items 渲染）。"""

    model_config = ConfigDict(extra="forbid")

    usage_enabled: bool = Field(
        description="长期记忆使用开关；关闭时页面仍列出事项供管理，回答不使用。"
    )
    notice: str | None = Field(
        default=None, description="使用开关关闭等稳定提示文案；没有时为空。"
    )
    items: list[OngoingItemProjection] = Field(
        default_factory=list, description="按目标最近更新时间倒序的进行中事项。"
    )


class OngoingItemStateUpdateRequest(BaseModel):
    """页面对一个精确事项的暂停/完成/恢复请求。

    条目未关联领域时 ``version`` 就是条目版本；已关联领域（如学习目标）时
    状态由所属领域管理：``version`` 仍须匹配页面读到的条目版本（防止页面
    基于过期引用操作），``reference_version`` 必须是投影里领域对象的版本
    （跨会话并发守卫；缺失时如实拒绝，不盲目覆盖领域新状态）。
    """

    model_config = ConfigDict(extra="forbid")

    action: OngoingGoalAction = Field(description="要执行的操作。")
    version: int = Field(ge=1, description="操作前读到的条目版本号。")
    reference_version: int | None = Field(
        default=None,
        ge=1,
        description=(
            "操作前读到的所属领域对象版本（画像工单 14）；关联领域的条目"
            "必填，未关联领域时省略。"
        ),
    )


class OngoingDomainReferenceUpdateRequest(BaseModel):
    """设置/清除目标的领域引用请求（``null`` 表示清除）。"""

    model_config = ConfigDict(extra="forbid")

    reference: AtomicProfileDomainReference | None = Field(
        default=None, description="领域引用；``null`` 表示清除。"
    )
    version: int = Field(ge=1, description="操作前读到的条目版本号。")


class OngoingContextState(StrEnum):
    """近期工作上下文的当前状态。"""

    EMPTY = "empty"
    CURRENT = "current"
    REBUILDING = "rebuilding"


class OngoingContextSnapshot(BaseModel):
    """近期工作上下文快照（内部派生视图，供回答编译与验证使用）。"""

    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(description="所属账户。")
    state: OngoingContextState = Field(description="当前状态。")
    version: int = Field(
        default=0,
        ge=0,
        description="当前上下文代数（失效或重建都会递增）；从未建立或为空时为 0。",
    )
    built_at: datetime | None = Field(
        default=None, description="最近一次成功重建时间；从未重建时为空。"
    )
    dependency_fingerprint: str = Field(
        default="", description="构建时捕获的依赖指纹（内部核对用）。"
    )
    items: list[OngoingItemProjection] = Field(
        default_factory=list,
        description="上下文中的事项；无法保证新版本时为空，绝不回退旧内容。",
    )


class OngoingContinuationGroup(BaseModel):
    """一次「继续某事项」时提供的条目集合（编译采用用，不额外落库）。"""

    model_config = ConfigDict(extra="forbid")

    goal_item_id: str = Field(description="命中的目标条目标识。")
    entry_item_ids: list[str] = Field(
        default_factory=list,
        description="同一事项的进展与待续问题条目标识（不含目标本身）。",
    )


__all__ = [
    "ONGOING_CONTEXT_CONTRACT_VERSION",
    "OngoingContextSnapshot",
    "OngoingContextState",
    "OngoingContinuationGroup",
    "OngoingDomainReferenceError",
    "OngoingDomainReferenceProjection",
    "OngoingDomainReferenceState",
    "OngoingDomainReferenceUpdateRequest",
    "OngoingEntryProjection",
    "OngoingGoalAction",
    "OngoingItemProjection",
    "OngoingItemRole",
    "OngoingItemStateUpdateRequest",
    "OngoingItemsProjection",
]
