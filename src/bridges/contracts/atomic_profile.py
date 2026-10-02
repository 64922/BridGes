"""V2 Issue 08：无固定类别的原子画像合同。

原子条目是用户看到、修改和删除的长期信息单位：没有四类分组、没有类别
筛选，也没有人格或心理标签。每条只保留正文、来源消息、时间与乐观锁版本。

四维记录仍是自动抽取与冲突消解的写入引擎（``bridges.profiles.four_dimensions``）；
本合同的条目镜像它的结果，并额外承载用户编辑时间、删除墓碑与迁移批次，
用于「用户编辑优先」「删除不复活」和「可对账、可恢复的迁移」。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field

from bridges.contracts.profiles import FourDimensionConfidence


class AtomicProfileItemStatus(StrEnum):
    """原子条目状态。

    ``SUPERSEDED`` 是同一属性槽发生明确变化后退休的旧版本：不再进入列表
    与切片，但保留正文、来源与被替代关系供对账，不是删除墓碑。
    """

    ACTIVE = "active"
    SUPERSEDED = "superseded"
    WITHDRAWN = "withdrawn"


class AtomicProfileFactRelation(StrEnum):
    """事实身份中的关系/属性槽（改进工单 16）。

    身份不使用旧的画像维度作键：维度只作为解析提示，关系决定两条事实是否
    描述同一件事。``GRADE``/``MAJOR``/``IDENTITY`` 是单值属性槽，同槽的
    明确新值替代旧值；其余关系默认多值并存。
    """

    STATEMENT = "statement"
    IDENTITY = "identity"
    GRADE = "grade"
    MAJOR = "major"
    INTEREST = "interest"
    LEARNING = "learning"
    RESEARCH = "research"
    GOAL = "goal"

    @property
    def is_single_valued(self) -> bool:
        """单值属性槽：同主体、同范围、同关系产生新值时替代旧活动条目。"""

        return self in {
            AtomicProfileFactRelation.IDENTITY,
            AtomicProfileFactRelation.GRADE,
            AtomicProfileFactRelation.MAJOR,
        }


class AtomicProfileFactScope(StrEnum):
    """事实适用范围（身份的一部分；默认长期）。"""

    LONG_TERM = "long_term"
    CURRENT = "current"


class AtomicProfileGoalState(StrEnum):
    """目标生命周期标记（改进工单 18；只按用户明确信号变化）。

    暂停/完成的目标停止作为当前目标使用，但保留正文、来源与法定期限；
    ``ACTIVE`` 是其余事实的默认状态。
    """

    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"


class AtomicProfileFactIdentity(BaseModel):
    """一条完整事实的主体/关系/对象/范围身份（内部合同，不进入页面）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    subject: str = Field(default="user", description="事实主体；当前账户用户。")
    relation: AtomicProfileFactRelation = Field(
        default=AtomicProfileFactRelation.STATEMENT, description="关系/属性槽。"
    )
    object: str = Field(default="", description="关系指向的对象或属性值。")
    scope: AtomicProfileFactScope = Field(
        default=AtomicProfileFactScope.LONG_TERM, description="适用范围。"
    )


class AtomicProfileWriteOrigin(StrEnum):
    """条目写入来源（内部字段，页面只用于诚实标注最近变化）。"""

    AUTOMATIC = "automatic"
    USER = "user"
    MIGRATION = "migration"


class AtomicProfileMemoryKind(StrEnum):
    """本轮同步处理的记忆指令类型。"""

    REMEMBER = "remember"
    FORGET = "forget"


class AtomicProfileMemoryStatus(StrEnum):
    """记忆指令结果；找不到目标时如实返回未完成，不假装成功。"""

    REMEMBERED = "remembered"
    FORGOTTEN = "forgotten"
    UNRESOLVED = "unresolved"


class AtomicProfileMigrationStatus(StrEnum):
    """账户级原子化迁移状态；成功与可重试失败分开，便于页面给出准确结论。"""

    COMPLETED = "completed"
    RETRYABLE = "retryable"
    UNDONE = "undone"


class AtomicProfileReconciliationOutcome(StrEnum):
    """单条旧记录的对账结论；取值与迁移报告的计数同名，便于用计数复核明细。

    ``duplicated`` 是「来源或身份已经存在，因此没有新建条目」的合并计数：具体
    是用户条目优先、来源已迁移还是墓碑抑制，看同一条明细的原因码。``failed``
    只出现在可重试报告里——整批已回滚，台账只留失败那一条。
    """

    MIGRATED = "migrated"
    DUPLICATED = "duplicated"
    TOMBSTONED = "tombstoned"
    SKIPPED = "skipped"
    FAILED = "failed"


class AtomicProfileReconciliationEntry(BaseModel):
    """单条旧四维记录的对账明细：只有标识、结论与原因码，没有画像正文。

    报告里的计数回答「迁入了几条」；本明细回答「哪一条为什么没迁入」，使
    用户编辑优先、删除墓碑和重复来源的判断可复核，而不是靠猜。
    """

    model_config = ConfigDict(extra="forbid")

    source_record_id: str = Field(description="旧四维记录标识。")
    outcome: AtomicProfileReconciliationOutcome = Field(description="该条结论。")
    reason_code: str = Field(description="结论的确定性原因码，不含正文。")
    profile_item_id: str | None = Field(
        default=None,
        description="迁入或已存在的原子条目标识；只写墓碑或未写入时为空。",
    )


class AtomicProfileItem(BaseModel):
    """一条原子画像信息（内部记录）。

    ``identity_key`` 仍是「账户 + 规范化正文」的文本抑制键（旧墓碑兼容），
    事实身份另由 ``fact_subject``/``fact_relation``/``fact_object``/
    ``fact_scope`` 构成并折算为 ``fact_key``：同一事实只保留一条活动条目，
    同槽明确变化留下被替代版本，删除后同键与同事实都不复活。
    ``source_record_id`` 指向产生该条目的四维记录（用户单独记住的条目可以
    为空）。``topic_hint`` 只供既有内部接线使用（例如学习模式的当前水平
    假设），绝不进入页面投影、页面分组或模型上下文正文。
    """

    profile_item_id: str = Field(description="稳定的原子条目标识。")
    owner_account_id: str = Field(description="所属账户标识。")
    text: str = Field(description="条目正文；墓碑条目为空串。")
    identity_key: str = Field(description="账户 + 规范化正文的去重与抑制键。")
    fact_subject: str = Field(default="user", description="事实主体；当前账户用户。")
    fact_relation: AtomicProfileFactRelation = Field(
        default=AtomicProfileFactRelation.STATEMENT, description="事实关系/属性槽。"
    )
    fact_object: str = Field(default="", description="事实对象或属性值。")
    fact_scope: AtomicProfileFactScope = Field(
        default=AtomicProfileFactScope.LONG_TERM, description="事实适用范围。"
    )
    fact_key: str = Field(default="", description="事实身份键；用于同事实合并与抑制。")
    evidence_quote: str | None = Field(
        default=None,
        max_length=500,
        description="精确来源原话；旧记录未保存原话时为空，绝不伪造。",
    )
    supersedes_id: str | None = Field(
        default=None, description="本条替代的旧条目标识；可为空。"
    )
    superseded_by_id: str | None = Field(
        default=None, description="本条被哪条新版本替代；活动条目为空。"
    )
    valid_from: datetime | None = Field(
        default=None, description="有效期起点；没有明示时间时为空。"
    )
    valid_until: datetime | None = Field(
        default=None,
        description="有效期终点；到点后不再注入。没有明示期限时为空，不设统一 TTL。",
    )
    validity_anchor_at: datetime | None = Field(
        default=None, description="相对时间的来源消息时间锚；没有时间表达时为空。"
    )
    validity_phrase: str | None = Field(
        default=None, max_length=100, description="原文明示的时间表达，供审计核对。"
    )
    goal_state: AtomicProfileGoalState = Field(
        default=AtomicProfileGoalState.ACTIVE,
        description="目标生命周期标记；普通事实恒为 active。",
    )
    source_record_id: str | None = Field(
        default=None, description="产生该条目的四维记录标识，可为空。"
    )
    source_message_ids: list[str] = Field(
        default_factory=list, description="证据来源消息标识，去重后按出现顺序保留。"
    )
    topic_hint: str | None = Field(
        default=None, description="内部提示，不进入页面投影或模型上下文。"
    )
    status: AtomicProfileItemStatus = Field(description="活动条目或删除墓碑。")
    write_origin: AtomicProfileWriteOrigin = Field(description="条目最近一次写入来源。")
    confidence: FourDimensionConfidence = Field(
        description="沿用的证据把握度档位，用于抽取排序。"
    )
    version: int = Field(ge=1, description="修改/删除使用的乐观锁版本号。")
    created_at: datetime = Field(description="条目首次出现时间。")
    updated_at: datetime = Field(description="最近一次内容或状态变化时间。")
    user_edited_at: datetime | None = Field(
        default=None, description="用户最近一次编辑时间；非空表示用户版本优先。"
    )
    migration_run_id: str | None = Field(
        default=None, description="创建该条目的原子化迁移批次，用于可恢复回滚。"
    )


class AtomicProfileTombstoneEntry(BaseModel):
    """墓碑/被替代版本的审计投影（工单 18）：只含标识与状态，不含正文。

    删除墓碑与被替代版本的正文已在条目里清空或退休，本投影回答「哪条事实被
    撤回/替代、抑制的是哪个事实身份、来源在哪」，供账户导出、墓碑审计和
    撤回传播核对使用。
    """

    profile_item_id: str = Field(description="稳定的原子条目标识。")
    status: AtomicProfileItemStatus = Field(description="withdrawn 或 superseded。")
    fact_relation: AtomicProfileFactRelation = Field(description="事实关系/属性槽。")
    fact_scope: AtomicProfileFactScope = Field(description="事实适用范围。")
    has_fact_suppression: bool = Field(
        description="是否保留事实身份抑制键（普通近义提及不得复活）。"
    )
    superseded_by_id: str | None = Field(
        default=None, description="被哪条新版本替代；删除墓碑为空。"
    )
    source_message_ids: list[str] = Field(
        default_factory=list, description="旧证据来源消息标识；不含正文。"
    )
    updated_at: datetime = Field(description="最近一次状态变化时间。")


class AtomicProfileItemProjection(BaseModel):
    """原子画像页面投影：无类别、无分组、无内部哈希。"""

    profile_item_id: str = Field(description="稳定的原子条目标识。")
    text: str = Field(description="条目正文。")
    version: int = Field(ge=1, description="修改/删除使用的乐观锁版本号。")
    updated_at: datetime = Field(description="最近一次变化时间。")
    user_edited_at: datetime | None = Field(
        default=None, description="用户最近一次编辑时间，可为空。"
    )
    source_message_ids: list[str] = Field(
        default_factory=list, description="证据来源消息标识。"
    )
    evidence_quote: str | None = Field(
        default=None, description="精确来源原话，可为空（旧记录未保存原话）。"
    )
    write_origin: AtomicProfileWriteOrigin = Field(
        description="最近一次写入来源，用于区分「你修改过」和自动整理。"
    )
    supersedes_id: str | None = Field(
        default=None, description="本条替代的旧条目标识；可为空。"
    )
    valid_from: datetime | None = Field(
        default=None, description="有效期起点；没有明示时间时为空。"
    )
    valid_until: datetime | None = Field(
        default=None, description="有效期终点；到点后不再用于回答。"
    )
    validity_phrase: str | None = Field(
        default=None, description="原文明示的时间表达，供页面按需展示与核对。"
    )
    goal_state: AtomicProfileGoalState = Field(
        default=AtomicProfileGoalState.ACTIVE,
        description="目标生命周期标记；普通事实恒为 active。",
    )


class AtomicProfileItemModifyRequest(BaseModel):
    """行内编辑的乐观锁请求。"""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=1000, description="替换后的正文。")
    version: int = Field(ge=1, description="用户读到的版本号。")


class AtomicProfileItemDeleteRequest(BaseModel):
    """删除确认的乐观锁请求。"""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1, description="用户读到的版本号。")


class AtomicProfileMemoryResult(BaseModel):
    """本轮「记住／忘掉」的同步结果（不携带正文）。"""

    model_config = ConfigDict(extra="forbid")

    kind: AtomicProfileMemoryKind = Field(description="本轮记忆指令类型。")
    status: AtomicProfileMemoryStatus = Field(description="指令结果。")
    matched_count: int = Field(default=0, ge=0, description="命中的条目条数。")

    def context_metadata(self) -> dict[str, str | int]:
        """返回允许进入聊天提示词的最小脱敏元数据。"""

        return {
            "memory_kind": self.kind.value,
            "status": self.status.value,
            "matched_count": self.matched_count,
        }


class AtomicProfileMigrationReport(BaseModel):
    """账户级原子化迁移报告；只含计数、标识与对账摘要，不含正文。"""

    run_id: str = Field(description="迁移批次标识。")
    owner_account_id: str = Field(description="所属账户标识。")
    migration_version: str = Field(description="迁移合同版本。")
    status: AtomicProfileMigrationStatus = Field(description="迁移状态。")
    migrated: int = Field(ge=0, description="本批次新建的原子条目数。")
    duplicated: int = Field(ge=0, description="已存在同键条目、未重复写入的条数。")
    tombstoned: int = Field(ge=0, description="旧记录为撤回状态、只写墓碑的条数。")
    skipped: int = Field(ge=0, description="无正文等不可迁移的旧记录条数。")
    failed: int = Field(ge=0, description="失败条数。")
    created_item_ids: list[str] = Field(
        default_factory=list, description="本批次新建条目标识，回滚删除依据。"
    )
    source_record_ids: list[str] = Field(
        default_factory=list, description="本批次覆盖的四维记录标识，用于对账。"
    )
    reconciliation_digest: str = Field(description="来源记录标识与内容哈希的确定性摘要。")
    reconciliation: list[AtomicProfileReconciliationEntry] = Field(
        default_factory=list,
        description="逐条对账明细：每条来源记录的结论与原因码（不含正文）。",
    )
    retryable: bool = Field(description="同一批次是否可安全重试。")
    identity_backfilled: int = Field(
        default=0, ge=0, description="本批次为既有条目补齐事实身份的条数。"
    )
    created_at: datetime = Field(description="报告创建时间。")
    undone_at: datetime | None = Field(
        default=None, description="回滚时间；非空表示批次已撤销。"
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def converged(self) -> bool:
        """本账户是否已收敛到新事实形态：成功批次且没有失败来源。"""

        return (
            self.status == AtomicProfileMigrationStatus.COMPLETED and self.failed == 0
        )


__all__ = [
    "AtomicProfileFactIdentity",
    "AtomicProfileFactRelation",
    "AtomicProfileFactScope",
    "AtomicProfileGoalState",
    "AtomicProfileItem",
    "AtomicProfileItemDeleteRequest",
    "AtomicProfileItemModifyRequest",
    "AtomicProfileItemProjection",
    "AtomicProfileItemStatus",
    "AtomicProfileMemoryKind",
    "AtomicProfileMemoryResult",
    "AtomicProfileMemoryStatus",
    "AtomicProfileMigrationReport",
    "AtomicProfileMigrationStatus",
    "AtomicProfileReconciliationEntry",
    "AtomicProfileReconciliationOutcome",
    "AtomicProfileTombstoneEntry",
    "AtomicProfileWriteOrigin",
]
