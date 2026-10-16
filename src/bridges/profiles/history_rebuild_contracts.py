"""画像工单 17：历史重整的合同、台账词表与只读数据类。

本模块只定义跨层共享的稳定取值与数据结构，不接触数据库、不调用模型：

- 策略版本（``policy_version``）与状态版本（``state_version``）随每次重
  整记录，恢复时版本不一致必须显式拒绝，不做静默兼容；
- 幂等标识：``rebuild_id`` 由（账户、内容状态摘要、预览合同版本、策略
  版本、窗口）确定性派生，同一批次重复执行得到同一标识，不重复生效；
- 台账词表：运行状态 / 逐条状态 / 跳过原因。跳过原因与预览
  ``chat_scope`` 的计数键一一对应（``unfinished`` 等），运行期屏障使用
  与提交前复核一致的稳定错误码（``source_invalidated`` 等）。

台账只保存标识、计数与摘要，绝不保存消息或条目正文。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from bridges.profiles.history_contracts import HISTORY_PREVIEW_VERSION, digest_lines

#: 历史重整的本地门控/窗口/任务分类策略版本；策略变化必须显式拒绝恢复。
HISTORY_REBUILD_POLICY_VERSION = "profile-history-rebuild-policy-v1"
#: 台账载荷（baseline_json）与报告结构的合同版本。
HISTORY_REBUILD_STATE_VERSION = "profile-history-rebuild-state-v1"

#: 工作目录内的固定文件名（工作副本与恢复前提分开保存）。
REBUILD_METADATA_FILENAME = "rebuild.json"
REBUILD_PREVIEW_FILENAME = "preview.json"
REBUILD_BACKUP_FILENAME = "history.db"
REBUILD_WORKING_DIRNAME = "working"
REBUILD_WORKING_DB_FILENAME = "bridges.db"
REBUILD_REPORT_FILENAME = "reconciliation.json"


class RebuildStatus(StrEnum):
    """一次重整的运行状态（台账的单一权威取值）。"""

    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class RebuildItemStatus(StrEnum):
    """逐条进度状态。"""

    PENDING = "pending"
    SCHEDULED = "scheduled"
    EXTRACTED = "extracted"
    SKIPPED = "skipped"
    FAILED = "failed"


TERMINAL_ITEM_STATUSES: frozenset[RebuildItemStatus] = frozenset(
    {
        RebuildItemStatus.EXTRACTED,
        RebuildItemStatus.SKIPPED,
        RebuildItemStatus.FAILED,
    }
)

IN_FLIGHT_ITEM_STATUSES: frozenset[RebuildItemStatus] = frozenset(
    {RebuildItemStatus.PENDING, RebuildItemStatus.SCHEDULED}
)


class RebuildSkipReason(StrEnum):
    """跳过原因词表（与预览 chat_scope 计数键和提交前复核错误码对齐）。"""

    # 种子门控：与预览 chat_scope 计数一一对应，任何一项都不进入模型调用。
    UNFINISHED = "unfinished"
    EMPTY = "empty"
    TOMBSTONED = "tombstoned"
    CONTENT_BLOCKED = "content_blocked"
    WITHDRAWN_SUPPRESSED = "withdrawn_suppressed"
    RECORDING_OFF = "recording_off"
    NO_SIGNAL = "no_signal"
    # 管道既有结论：在线已终结 / 材料指令消息 / 来源不存在。
    ALREADY_SETTLED = "already_settled"
    MATERIAL_RUN_PRESENT = "material_run_present"
    SOURCE_MISSING = "source_missing"
    # 运行期屏障：提交前复核（墓碑/控制/版本/来源变化）拒绝迟到写入。
    SOURCE_INVALIDATED = "source_invalidated"
    PRIVACY_BLOCKED = "privacy_blocked"
    VERSION_UNAVAILABLE = "version_unavailable"
    BLOCKED_AT_SCHEDULE = "blocked_at_schedule"


#: 提交前复核把运行终结为屏障跳过时使用的稳定错误码（不是失败）。
BARRIER_EXHAUSTION_CODES: dict[str, RebuildSkipReason] = {
    "profile_extraction_source_invalidated": RebuildSkipReason.SOURCE_INVALIDATED,
    "profile_extraction_privacy_blocked": RebuildSkipReason.PRIVACY_BLOCKED,
    "profile_extraction_version_unavailable": RebuildSkipReason.VERSION_UNAVAILABLE,
}


@dataclass(frozen=True)
class RebuildWindow:
    """有界来源窗口：``messages.created_at`` 上的半开区间 ``[start, end)``。"""

    start_at: str | None
    end_at: str | None

    def as_dict(self) -> dict[str, object]:
        return {"start_at": self.start_at, "end_at": self.end_at}

    @property
    def bounded(self) -> bool:
        return self.start_at is not None or self.end_at is not None

    def contains(self, created_at: str) -> bool:
        """按解析后的时间比较；无法解析的时间戳不落入任何有界窗口。"""

        moment = _parse_moment(created_at)
        if moment is None:
            return not self.bounded
        if self.start_at is not None:
            start = _parse_moment(self.start_at)
            if start is None or moment < start:
                return False
        if self.end_at is not None:
            end = _parse_moment(self.end_at)
            if end is None or moment >= end:
                return False
        return True


def _parse_moment(value: str) -> datetime | None:
    """解析 ISO 时间戳为 UTC；缺失时区按 UTC 处理，损坏返回 None。"""

    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _int_value(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def _mapping(value: object) -> dict[str, object]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    return {}


def _str_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    return ()


@dataclass(frozen=True)
class ManualItemDigest:
    """人工/用户编辑条目的基线摘要（不含正文，用于对账保留权威）。"""

    profile_item_id: str
    version: int
    user_edited_at: str | None
    text_digest: str

    def as_dict(self) -> dict[str, object]:
        return {
            "profile_item_id": self.profile_item_id,
            "version": self.version,
            "user_edited_at": self.user_edited_at,
            "text_digest": self.text_digest,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> ManualItemDigest:
        return cls(
            profile_item_id=str(payload["profile_item_id"]),
            version=_int_value(payload.get("version")),
            user_edited_at=(
                str(payload["user_edited_at"])
                if payload.get("user_edited_at") is not None
                else None
            ),
            text_digest=str(payload["text_digest"]),
        )


@dataclass(frozen=True)
class RebuildBaseline:
    """开始时的账户基线：预览摘要、人工条目摘要、屏障标识（无正文）。"""

    preview_version: str
    preview_created_at: str
    state_digest: str
    chat_scope: dict[str, object]
    items: dict[str, object]
    controls: dict[str, object]
    domain_refs: dict[str, object]
    manual_items: tuple[ManualItemDigest, ...]
    withdrawn_item_ids: tuple[str, ...]
    withdrawn_identity_keys: tuple[str, ...]
    tombstoned_message_ids: tuple[str, ...]
    state_version: str = HISTORY_REBUILD_STATE_VERSION

    def as_dict(self) -> dict[str, object]:
        return {
            "state_version": self.state_version,
            "preview_version": self.preview_version,
            "preview_created_at": self.preview_created_at,
            "state_digest": self.state_digest,
            "chat_scope": dict(self.chat_scope),
            "items": dict(self.items),
            "controls": dict(self.controls),
            "domain_refs": dict(self.domain_refs),
            "manual_items": [item.as_dict() for item in self.manual_items],
            "manual_items_digest": self.manual_items_digest,
            "withdrawn_item_ids": list(self.withdrawn_item_ids),
            "withdrawn_identity_keys": list(self.withdrawn_identity_keys),
            "tombstoned_message_ids": list(self.tombstoned_message_ids),
        }

    @property
    def manual_items_digest(self) -> str:
        return digest_lines(
            [
                f"{item.profile_item_id}|{item.version}|{item.user_edited_at or ''}"
                f"|{item.text_digest}"
                for item in self.manual_items
            ]
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> RebuildBaseline:
        raw_manual = payload.get("manual_items")
        manual_items: list[ManualItemDigest] = []
        if isinstance(raw_manual, (list, tuple)):
            for entry in raw_manual:
                if isinstance(entry, Mapping):
                    manual_items.append(ManualItemDigest.from_dict(entry))
        return cls(
            preview_version=str(payload["preview_version"]),
            preview_created_at=str(payload["preview_created_at"]),
            state_digest=str(payload["state_digest"]),
            chat_scope=_mapping(payload.get("chat_scope")),
            items=_mapping(payload.get("items")),
            controls=_mapping(payload.get("controls")),
            domain_refs=_mapping(payload.get("domain_refs")),
            manual_items=tuple(manual_items),
            withdrawn_item_ids=_str_tuple(payload.get("withdrawn_item_ids")),
            withdrawn_identity_keys=_str_tuple(
                payload.get("withdrawn_identity_keys")
            ),
            tombstoned_message_ids=_str_tuple(payload.get("tombstoned_message_ids")),
        )


def rebuild_id_for(
    *,
    account_id: str,
    state_digest: str,
    policy_version: str,
    preview_version: str,
    window: RebuildWindow,
) -> str:
    """确定性派生重整标识：同一批次重复执行得到同一标识（幂等入口）。"""

    digest = digest_lines(
        [
            f"account={account_id}",
            f"state={state_digest}",
            f"policy={policy_version}",
            f"preview={preview_version}",
            f"window_start={window.start_at or ''}",
            f"window_end={window.end_at or ''}",
        ]
    )
    return f"profile-rebuild-{digest[:32]}"


@dataclass(frozen=True)
class RebuildSeedItem:
    """一条待写入台账的窗口消息（除跳过项外，均来自预览门控判定）。"""

    message_id: str
    conversation_id: str
    source_created_at: str
    status: RebuildItemStatus
    skip_reason: RebuildSkipReason | None = None


@dataclass(frozen=True)
class RebuildRunRecord:
    """``profile_rebuild_runs`` 的不可变视图。"""

    rebuild_id: str
    account_id: str
    status: RebuildStatus
    policy_version: str
    extractor_version: str
    source_digest: str
    state_digest: str
    schema_version: int
    window_start_at: str | None
    window_end_at: str | None
    baseline: RebuildBaseline
    window_total: int
    eligible_count: int
    extracted_count: int
    committed_count: int
    skipped_count: int
    failed_count: int
    last_error: str | None
    created_at: str
    updated_at: str
    completed_at: str | None = None
    reconciled_at: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "rebuild_id": self.rebuild_id,
            "account_id": self.account_id,
            "status": self.status.value,
            "policy_version": self.policy_version,
            "extractor_version": self.extractor_version,
            "source_digest": self.source_digest,
            "state_digest": self.state_digest,
            "schema_version": self.schema_version,
            "window": {
                "start_at": self.window_start_at,
                "end_at": self.window_end_at,
            },
            "counts": {
                "window_total": self.window_total,
                "eligible": self.eligible_count,
                "extracted": self.extracted_count,
                "committed": self.committed_count,
                "skipped": self.skipped_count,
                "failed": self.failed_count,
            },
            "last_error": self.last_error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "reconciled_at": self.reconciled_at,
        }


@dataclass(frozen=True)
class RebuildItemRecord:
    """``profile_rebuild_items`` 的不可变视图。"""

    rebuild_id: str
    message_id: str
    account_id: str
    conversation_id: str
    source_created_at: str
    status: RebuildItemStatus
    skip_reason: RebuildSkipReason | None
    source_hash: str | None
    task_key: str | None
    extraction_id: str | None
    committed_count: int
    observed_count: int
    last_error: str | None
    updated_at: str

    def as_dict(self) -> dict[str, object]:
        return {
            "message_id": self.message_id,
            "conversation_id": self.conversation_id,
            "source_created_at": self.source_created_at,
            "status": self.status.value,
            "skip_reason": self.skip_reason.value if self.skip_reason else None,
            "source_hash": self.source_hash,
            "task_key": self.task_key,
            "extraction_id": self.extraction_id,
            "committed_count": self.committed_count,
            "observed_count": self.observed_count,
            "last_error": self.last_error,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class RebuildProgress:
    """进度视图：分母真实（窗口范围、符合范围、剩余未决）。"""

    status: RebuildStatus
    window_total: int
    eligible: int
    extracted: int
    committed: int
    skipped: int
    failed: int
    pending: int
    scheduled: int
    skipped_by_reason: dict[str, int]
    notes: tuple[str, ...] = ()

    @property
    def remaining(self) -> int:
        return self.pending + self.scheduled

    @property
    def settled(self) -> int:
        return self.extracted + self.skipped + self.failed

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "window_total": self.window_total,
            "eligible": self.eligible,
            "extracted": self.extracted,
            "committed": self.committed,
            "skipped": self.skipped,
            "failed": self.failed,
            "pending": self.pending,
            "scheduled": self.scheduled,
            "remaining": self.remaining,
            "settled": self.settled,
            "skipped_by_reason": dict(self.skipped_by_reason),
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class RebuildDriveSummary:
    """一次受控驱动的结果：把本次新增的终态与遗留范围都如实报告。"""

    progress: RebuildProgress
    ticks: int
    max_ticks: int
    reached_budget: bool
    notes: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, object]:
        payload = dict(self.progress.as_dict())
        payload.update(
            {
                "ticks": self.ticks,
                "max_ticks": self.max_ticks,
                "reached_budget": self.reached_budget,
            }
        )
        return payload


def now_text(now: datetime | None = None) -> str:
    """台账时间戳：UTC ISO 秒精度（操作时间，不承担事实时间语义）。"""

    moment = now or datetime.now(UTC)
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def text_digest(text: str) -> str:
    return digest_lines([text])


def json_mapping(payload: Any) -> Mapping[str, object]:
    """解析台账中的 JSON 对象；损坏或非对象一律按空映射（失败关闭）。"""

    if isinstance(payload, Mapping):
        return payload
    return {}


__all__ = [
    "BARRIER_EXHAUSTION_CODES",
    "HISTORY_PREVIEW_VERSION",
    "HISTORY_REBUILD_POLICY_VERSION",
    "HISTORY_REBUILD_STATE_VERSION",
    "IN_FLIGHT_ITEM_STATUSES",
    "ManualItemDigest",
    "REBUILD_BACKUP_FILENAME",
    "REBUILD_METADATA_FILENAME",
    "REBUILD_PREVIEW_FILENAME",
    "REBUILD_REPORT_FILENAME",
    "REBUILD_WORKING_DB_FILENAME",
    "REBUILD_WORKING_DIRNAME",
    "RebuildBaseline",
    "RebuildDriveSummary",
    "RebuildItemRecord",
    "RebuildItemStatus",
    "RebuildProgress",
    "RebuildRunRecord",
    "RebuildSeedItem",
    "RebuildSkipReason",
    "RebuildStatus",
    "RebuildWindow",
    "TERMINAL_ITEM_STATUSES",
    "json_mapping",
    "now_text",
    "rebuild_id_for",
    "text_digest",
]
