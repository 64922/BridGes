"""画像工单 16：历史重整预览的清单合同（只含计数、定位标识与摘要，不含原文）。

预览清单回答维护者在重整前必须核对的问题：哪些人工条目会保留、哪些自动
条目还有可核验证据、哪些历史因删除、不记录或缺乏授权而跳过。合同刻意
不携带条目正文与消息正文：定位标识用于在页面按需查看，诊断输出不得
携带私人原文。

``manifest_version`` 是合同版本；读取方遇到未知版本必须显式失败，不能
猜测字段含义。账户与来源版本（``state_digest``）绑定在清单里，执行重整前
必须重新校验（见 :mod:`bridges.profiles.history_preview`）。
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

#: 预览清单合同版本；字段或语义变化时必须递增。
HISTORY_PREVIEW_VERSION = "profile-history-preview-v1"

#: 清单顶层的范围说明；解释了什么进入统计、什么被跳过。
HISTORY_PREVIEW_SCOPE_NOTE = (
    "范围仅限清单绑定的账户：事实分类来自该账户的原子条目，聊天范围来自"
    "该账户仍可读取的用户消息。跳过原因覆盖删除屏障（墓碑、撤回抑制）、"
    "内容禁止记录、记录开关与不可用来源。非聊天来源（如指定材料）不因存在"
    "旧条目而默认可提取：它们落入「来源无法核验」分类，重整时必须按各自的"
    "授权合同重新核验。不能仅凭预览清单宣称历史可提取，预览不是永久授权，"
    "执行重整前必须重新校验来源版本。"
)

#: 条目分类计数键；定位标识按同一组键给出对应条目 ID。
ITEM_COUNT_KEYS: tuple[str, ...] = (
    "active_total",
    "manual_preserved",
    "automatic_verifiable",
    "automatic_partial_evidence",
    "automatic_deleted_evidence",
    "automatic_unverified_source",
    "automatic_missing_evidence",
    "sensitive_authorized",
    "sensitive_unauthorized",
    "credentials_flagged",
    "withdrawn",
    "superseded",
)

#: 需要给出条目定位标识的分类（active_total 是汇总，不重复列定位）。
ITEM_LOCATOR_KEYS: tuple[str, ...] = tuple(
    key for key in ITEM_COUNT_KEYS if key != "active_total"
)

#: 聊天范围计数字段。
CHAT_SCOPE_COUNT_KEYS: tuple[str, ...] = (
    "user_messages_total",
    "eligible",
    "skipped_tombstoned",
    "skipped_unfinished",
    "skipped_empty",
    "skipped_content_blocked",
    "skipped_withdrawn_suppressed",
    "skipped_recording_off",
)


def _section(payload: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"缺少或非法字段：{key}")
    return value


def _int_value(section: Mapping[str, object], key: str) -> int:
    value = section.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"缺少或非法计数：{key}")
    return value


def _bool_value(section: Mapping[str, object], key: str) -> bool:
    value = section.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"缺少或非法布尔值：{key}")
    return value


def _str_value(section: Mapping[str, object], key: str) -> str:
    value = section.get(key)
    if not isinstance(value, str):
        raise ValueError(f"缺少或非法文本：{key}")
    return value


def _str_or_none(section: Mapping[str, object], key: str) -> str | None:
    value = section.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"缺少或非法可空文本：{key}")
    return value


def _str_tuple(section: Mapping[str, object], key: str) -> tuple[str, ...]:
    value = section.get(key)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"缺少或非法标识列表：{key}")
    return tuple(value)


def _str_map(section: Mapping[str, object], key: str) -> dict[str, str]:
    value = section.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"缺少或非法标识映射：{key}")
    return {str(k): str(v) for k, v in value.items()}


def _locators(section: Mapping[str, object]) -> dict[str, list[str]]:
    locators = _section(section, "locators")
    result: dict[str, list[str]] = {}
    for key in ITEM_LOCATOR_KEYS:
        result[key] = list(_str_tuple(locators, key))
    return result


@dataclass(frozen=True)
class ItemEvidenceSummary:
    """原子条目分类计数与定位标识（不含正文）。"""

    active_total: int
    manual_preserved: int
    automatic_verifiable: int
    automatic_partial_evidence: int
    automatic_deleted_evidence: int
    automatic_unverified_source: int
    automatic_missing_evidence: int
    sensitive_authorized: int
    sensitive_unauthorized: int
    credentials_flagged: int
    withdrawn: int
    superseded: int
    locators: dict[str, list[str]] = field(default_factory=dict)
    #: 「来源无法核验」条目的来源标识（非聊天/材料类或旧数据缺失）；
    #: 重整前必须按各自授权合同逐项重新核验，清单只给出待核验来源。
    unverified_source_refs: tuple[str, ...] = ()

    def count_dict(self) -> dict[str, int]:
        return {key: int(getattr(self, key)) for key in ITEM_COUNT_KEYS}

    def as_dict(self) -> dict[str, object]:
        return {
            "counts": self.count_dict(),
            "locators": {key: list(value) for key, value in self.locators.items()},
            "unverified_source_refs": list(self.unverified_source_refs),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> ItemEvidenceSummary:
        counts = _section(payload, "counts")
        return cls(
            active_total=_int_value(counts, "active_total"),
            manual_preserved=_int_value(counts, "manual_preserved"),
            automatic_verifiable=_int_value(counts, "automatic_verifiable"),
            automatic_partial_evidence=_int_value(
                counts, "automatic_partial_evidence"
            ),
            automatic_deleted_evidence=_int_value(
                counts, "automatic_deleted_evidence"
            ),
            automatic_unverified_source=_int_value(
                counts, "automatic_unverified_source"
            ),
            automatic_missing_evidence=_int_value(counts, "automatic_missing_evidence"),
            sensitive_authorized=_int_value(counts, "sensitive_authorized"),
            sensitive_unauthorized=_int_value(counts, "sensitive_unauthorized"),
            credentials_flagged=_int_value(counts, "credentials_flagged"),
            withdrawn=_int_value(counts, "withdrawn"),
            superseded=_int_value(counts, "superseded"),
            locators=_locators(payload),
            unverified_source_refs=_str_tuple(payload, "unverified_source_refs"),
        )


@dataclass(frozen=True)
class ChatScopeSummary:
    """可重整聊天范围与跳过原因计数（不含消息正文与逐条定位）。"""

    user_messages_total: int
    eligible: int
    eligible_first_at: str | None
    eligible_last_at: str | None
    eligible_conversation_ids: tuple[str, ...]
    skipped_tombstoned: int
    skipped_unfinished: int
    skipped_empty: int
    skipped_content_blocked: int
    skipped_withdrawn_suppressed: int
    skipped_recording_off: int

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            key: int(getattr(self, key)) for key in CHAT_SCOPE_COUNT_KEYS
        }
        payload["eligible_first_at"] = self.eligible_first_at
        payload["eligible_last_at"] = self.eligible_last_at
        payload["eligible_conversation_ids"] = list(self.eligible_conversation_ids)
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> ChatScopeSummary:
        return cls(
            user_messages_total=_int_value(payload, "user_messages_total"),
            eligible=_int_value(payload, "eligible"),
            eligible_first_at=_str_or_none(payload, "eligible_first_at"),
            eligible_last_at=_str_or_none(payload, "eligible_last_at"),
            eligible_conversation_ids=_str_tuple(payload, "eligible_conversation_ids"),
            skipped_tombstoned=_int_value(payload, "skipped_tombstoned"),
            skipped_unfinished=_int_value(payload, "skipped_unfinished"),
            skipped_empty=_int_value(payload, "skipped_empty"),
            skipped_content_blocked=_int_value(payload, "skipped_content_blocked"),
            skipped_withdrawn_suppressed=_int_value(
                payload, "skipped_withdrawn_suppressed"
            ),
            skipped_recording_off=_int_value(payload, "skipped_recording_off"),
        )


@dataclass(frozen=True)
class ControlsSummary:
    """账户控制开关与删除屏障计数。"""

    recording_enabled: bool
    usage_enabled: bool
    usage_control_version: int
    content_block_count: int
    tombstone_count: int

    def as_dict(self) -> dict[str, object]:
        return {
            "recording_enabled": self.recording_enabled,
            "usage_enabled": self.usage_enabled,
            "usage_control_version": self.usage_control_version,
            "content_block_count": self.content_block_count,
            "tombstone_count": self.tombstone_count,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> ControlsSummary:
        return cls(
            recording_enabled=_bool_value(payload, "recording_enabled"),
            usage_enabled=_bool_value(payload, "usage_enabled"),
            usage_control_version=_int_value(payload, "usage_control_version"),
            content_block_count=_int_value(payload, "content_block_count"),
            tombstone_count=_int_value(payload, "tombstone_count"),
        )


@dataclass(frozen=True)
class DomainRefsSummary:
    """当前 schema 可用的领域引用计数（反馈、旧记录引用、迁移台账）。"""

    feedback_count: int
    items_with_source_record_ref: int
    unresolved_source_record_refs: int
    migration_report_count: int

    def as_dict(self) -> dict[str, object]:
        return {
            "feedback_count": self.feedback_count,
            "items_with_source_record_ref": self.items_with_source_record_ref,
            "unresolved_source_record_refs": self.unresolved_source_record_refs,
            "migration_report_count": self.migration_report_count,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> DomainRefsSummary:
        return cls(
            feedback_count=_int_value(payload, "feedback_count"),
            items_with_source_record_ref=_int_value(
                payload, "items_with_source_record_ref"
            ),
            unresolved_source_record_refs=_int_value(
                payload, "unresolved_source_record_refs"
            ),
            migration_report_count=_int_value(payload, "migration_report_count"),
        )


@dataclass(frozen=True)
class AccountSnapshot:
    """一个账户的完整只读快照：预览与恢复演练共用同一份计算。"""

    account_id: str
    schema_version: int
    state_digest: str
    items: ItemEvidenceSummary
    chat_scope: ChatScopeSummary
    controls: ControlsSummary
    domain_refs: DomainRefsSummary

    def as_dict(self) -> dict[str, object]:
        return {
            "account_id": self.account_id,
            "schema_version": self.schema_version,
            "state_digest": self.state_digest,
            "items": self.items.as_dict(),
            "chat_scope": self.chat_scope.as_dict(),
            "controls": self.controls.as_dict(),
            "domain_refs": self.domain_refs.as_dict(),
        }


@dataclass(frozen=True)
class HistoryPreviewManifest:
    """带版本的预览清单：绑定账户、来源摘要与范围判定。"""

    created_at: str
    account_id: str
    source_path: str
    schema_version: int
    source_digest: str
    state_digest: str
    writer_state: str
    items: ItemEvidenceSummary
    chat_scope: ChatScopeSummary
    controls: ControlsSummary
    domain_refs: DomainRefsSummary
    manifest_version: str = HISTORY_PREVIEW_VERSION
    scope_note: str = HISTORY_PREVIEW_SCOPE_NOTE

    def as_dict(self) -> dict[str, object]:
        return {
            "manifest_version": self.manifest_version,
            "created_at": self.created_at,
            "account_id": self.account_id,
            "scope_note": self.scope_note,
            "source": {
                "path": self.source_path,
                "schema_version": self.schema_version,
                "source_digest": self.source_digest,
                "state_digest": self.state_digest,
                "writer_state": self.writer_state,
            },
            "items": self.items.as_dict(),
            "chat_scope": self.chat_scope.as_dict(),
            "controls": self.controls.as_dict(),
            "domain_refs": self.domain_refs.as_dict(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> HistoryPreviewManifest:
        if payload.get("manifest_version") != HISTORY_PREVIEW_VERSION:
            raise ValueError("不受支持的预览清单版本")
        source = _section(payload, "source")
        return cls(
            created_at=_str_value(payload, "created_at"),
            account_id=_str_value(payload, "account_id"),
            source_path=_str_value(source, "path"),
            schema_version=_int_value(source, "schema_version"),
            source_digest=_str_value(source, "source_digest"),
            state_digest=_str_value(source, "state_digest"),
            writer_state=_str_value(source, "writer_state"),
            items=ItemEvidenceSummary.from_dict(_section(payload, "items")),
            chat_scope=ChatScopeSummary.from_dict(_section(payload, "chat_scope")),
            controls=ControlsSummary.from_dict(_section(payload, "controls")),
            domain_refs=DomainRefsSummary.from_dict(_section(payload, "domain_refs")),
            scope_note=_str_value(payload, "scope_note"),
        )


def digest_lines(parts: Sequence[str]) -> str:
    """把规范化文本行折算为 sha256 摘要；只输出摘要，不输出原文。"""

    hasher = hashlib.sha256()
    for part in parts:
        hasher.update(part.encode("utf-8"))
        hasher.update(b"\n")
    return hasher.hexdigest()


__all__ = [
    "AccountSnapshot",
    "CHAT_SCOPE_COUNT_KEYS",
    "ChatScopeSummary",
    "ControlsSummary",
    "DomainRefsSummary",
    "HISTORY_PREVIEW_SCOPE_NOTE",
    "HISTORY_PREVIEW_VERSION",
    "HistoryPreviewManifest",
    "ITEM_COUNT_KEYS",
    "ITEM_LOCATOR_KEYS",
    "ItemEvidenceSummary",
    "digest_lines",
]
