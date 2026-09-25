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

from pydantic import BaseModel, ConfigDict, Field

from bridges.contracts.profiles import FourDimensionConfidence


class AtomicProfileItemStatus(StrEnum):
    """原子条目状态。"""

    ACTIVE = "active"
    WITHDRAWN = "withdrawn"


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
    """账户级原子化迁移状态。"""

    COMPLETED = "completed"
    RETRYABLE = "retryable"
    UNDONE = "undone"


class AtomicProfileItem(BaseModel):
    """一条原子画像信息（内部记录）。

    ``identity_key`` 是「账户 + 规范化正文」构成的去重与抑制键：同一事实
    只保留一条活动条目，删除后同键条目成为墓碑。``source_record_id`` 指向
    产生该条目的四维记录（用户单独记住的条目可以为空）。``topic_hint``
    只供既有内部接线使用（例如学习模式的当前水平假设），绝不进入页面投影、
    页面分组或模型上下文正文。
    """

    profile_item_id: str = Field(description="稳定的原子条目标识。")
    owner_account_id: str = Field(description="所属账户标识。")
    text: str = Field(description="条目正文；墓碑条目为空串。")
    identity_key: str = Field(description="账户 + 规范化正文的去重与抑制键。")
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
    write_origin: AtomicProfileWriteOrigin = Field(
        description="最近一次写入来源，用于区分「你修改过」和自动整理。"
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
    retryable: bool = Field(description="同一批次是否可安全重试。")
    created_at: datetime = Field(description="报告创建时间。")
    undone_at: datetime | None = Field(
        default=None, description="回滚时间；非空表示批次已撤销。"
    )


__all__ = [
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
    "AtomicProfileWriteOrigin",
]
