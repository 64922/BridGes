"""画像工单 17：历史重整台账的数据类（运行、逐条、进度与基线）。

只承载不可变视图与 JSON 往返，不接触数据库、不调用模型；写入侧拼装与
状态机保持在 ``history_rebuild``/``history_rebuild_drive``，
只读管道快照保持在 ``history_rebuild_repository``。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from bridges.profiles.history_contracts import digest_lines
from bridges.profiles.history_rebuild_contracts import (
    HISTORY_REBUILD_STATE_VERSION,
    RebuildItemStatus,
    RebuildSkipReason,
    RebuildStatus,
)


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


__all__ = [
    "ManualItemDigest",
    "RebuildBaseline",
    "RebuildItemRecord",
    "RebuildProgress",
    "RebuildRunRecord",
    "RebuildSeedItem",
]
