"""画像工单 16：历史重整预览的账户快照计算（只读、确定性）。

账户快照 :class:`AccountSnapshot` 覆盖事实、证据可得性、控制开关、删除
屏障与当前 schema 可用的领域引用；预览清单与备份恢复演练共用本模块的
同一份计算，保证两侧核对的是同一合同。输出只含计数、定位标识与摘要，
条目正文与消息正文只在内存中参与分类与哈希计算。
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass, replace

from bridges.profiles.atomic import identity_key
from bridges.profiles.automatic import normalize_message_content
from bridges.profiles.history_contracts import (
    ITEM_COUNT_KEYS,
    ITEM_LOCATOR_KEYS,
    AccountSnapshot,
    ChatScopeSummary,
    ControlsSummary,
    DomainRefsSummary,
    ItemEvidenceSummary,
    digest_lines,
)
from bridges.profiles.history_repository import (
    HistoryItemRecord,
    HistoryMessageRecord,
    ProfileHistorySnapshotReader,
)
from bridges.profiles.sensitive import MemorySensitivity, classify_memory_sensitivity


def _matches_content_block(normalized_content: str, blocks: tuple[str, ...]) -> bool:
    """与自动抽取的禁止记录匹配保持一致：空值互相包含，双向子串命中。"""

    return any(
        block == normalized_content
        or block in normalized_content
        or normalized_content in block
        for block in blocks
    )


@dataclass(frozen=True)
class HistoryMessageDecision:
    """一条用户消息在历史重整门控下的确定性判定（不含正文）。"""

    message_id: str
    conversation_id: str
    created_at: str
    readable: bool
    eligible: bool
    skip_reason: str | None


def classify_history_message(
    message: HistoryMessageRecord,
    *,
    account_id: str,
    tombstoned_message_ids: set[str] | frozenset[str],
    withdrawn_source_ids: set[str] | frozenset[str],
    withdrawn_identity_keys: set[str] | frozenset[str],
    content_blocks: tuple[str, ...],
    recording_enabled: bool,
) -> HistoryMessageDecision:
    """历史消息门控的唯一实现（预览与重整共用同一合同）。

    ``readable`` 表示该消息可作为有效来源参与证据核验（用户消息、回合完成、
    非空、且未被墓碑删除）；``eligible`` 表示在可读基础上未被禁止内容、
    撤回抑制或停止记录拦截，可以进入语义提取范围。跳过原因词表与预览
    ``chat_scope`` 计数一一对应。
    """

    base = HistoryMessageDecision(
        message_id=message.message_id,
        conversation_id=message.conversation_id,
        created_at=message.created_at,
        readable=False,
        eligible=False,
        skip_reason=None,
    )
    if message.role != "user":
        return base
    if message.status != "done":
        return replace(base, skip_reason="unfinished")
    if not message.content.strip():
        return replace(base, skip_reason="empty")
    if message.message_id in tombstoned_message_ids:
        return replace(base, skip_reason="tombstoned")
    readable = replace(base, readable=True)
    normalized = normalize_message_content(message.content)
    if _matches_content_block(normalized, content_blocks):
        return replace(readable, skip_reason="content_blocked")
    if (
        message.message_id in withdrawn_source_ids
        or identity_key(account_id, message.content) in withdrawn_identity_keys
    ):
        return replace(readable, skip_reason="withdrawn_suppressed")
    if not recording_enabled:
        return replace(readable, skip_reason="recording_off")
    return replace(readable, eligible=True)


def _classify_items(
    items: list[HistoryItemRecord],
    readable_user_ids: set[str],
    tombstoned_message_ids: set[str],
) -> ItemEvidenceSummary:
    """把条目分为保留/可核验/缺失证据/敏感/屏障分类；只输出计数与标识。"""

    counts = dict.fromkeys(ITEM_COUNT_KEYS, 0)
    locators: dict[str, list[str]] = {key: [] for key in ITEM_LOCATOR_KEYS}
    unverified_source_refs: set[str] = set()

    def record(key: str, item_id: str) -> None:
        counts[key] += 1
        locators[key].append(item_id)

    for item in items:
        if item.status == "withdrawn":
            record("withdrawn", item.profile_item_id)
            continue
        if item.status == "superseded":
            record("superseded", item.profile_item_id)
            continue
        counts["active_total"] += 1
        if item.write_origin == "user" or item.user_edited_at is not None:
            record("manual_preserved", item.profile_item_id)
        else:
            available = [
                message_id
                for message_id in item.source_message_ids
                if message_id in readable_user_ids
            ]
            if item.source_message_ids and len(available) == len(
                item.source_message_ids
            ):
                record("automatic_verifiable", item.profile_item_id)
            elif available:
                record("automatic_partial_evidence", item.profile_item_id)
            elif not item.source_message_ids:
                record("automatic_missing_evidence", item.profile_item_id)
            elif any(
                message_id in tombstoned_message_ids
                for message_id in item.source_message_ids
            ):
                record("automatic_deleted_evidence", item.profile_item_id)
            else:
                record("automatic_unverified_source", item.profile_item_id)
                unverified_source_refs.update(item.source_message_ids)
        sensitivity = classify_memory_sensitivity(item.text)
        if sensitivity == MemorySensitivity.CREDENTIAL:
            record("credentials_flagged", item.profile_item_id)
        elif sensitivity == MemorySensitivity.SENSITIVE:
            if item.usage_purpose:
                record("sensitive_authorized", item.profile_item_id)
            else:
                record("sensitive_unauthorized", item.profile_item_id)

    return ItemEvidenceSummary(
        active_total=counts["active_total"],
        manual_preserved=counts["manual_preserved"],
        automatic_verifiable=counts["automatic_verifiable"],
        automatic_partial_evidence=counts["automatic_partial_evidence"],
        automatic_deleted_evidence=counts["automatic_deleted_evidence"],
        automatic_unverified_source=counts["automatic_unverified_source"],
        automatic_missing_evidence=counts["automatic_missing_evidence"],
        sensitive_authorized=counts["sensitive_authorized"],
        sensitive_unauthorized=counts["sensitive_unauthorized"],
        credentials_flagged=counts["credentials_flagged"],
        withdrawn=counts["withdrawn"],
        superseded=counts["superseded"],
        locators=locators,
        unverified_source_refs=tuple(sorted(unverified_source_refs)),
    )


def compute_account_snapshot(
    connection: sqlite3.Connection,
    account_id: str,
    *,
    schema_version: int,
) -> AccountSnapshot:
    """在调用方给定的快照（只读事务）里计算账户完整摘要。"""

    reader = ProfileHistorySnapshotReader(connection)
    items = reader.list_items(account_id)
    controls = reader.read_controls(account_id)
    feedback = reader.list_feedback(account_id)
    migration_reports = reader.count_migration_reports(account_id)

    withdrawn_keys = {
        item.identity_key for item in items if item.status == "withdrawn"
    }
    withdrawn_source_ids = {
        message_id
        for item in items
        if item.status == "withdrawn"
        for message_id in item.source_message_ids
    }
    tombstoned = set(controls.tombstoned_message_ids)

    hasher = hashlib.sha256()
    hasher.update(f"schema={schema_version}\n".encode())
    readable_user_ids: set[str] = set()
    user_total = 0
    eligible = 0
    skipped_tombstoned = 0
    skipped_unfinished = 0
    skipped_empty = 0
    skipped_content_blocked = 0
    skipped_withdrawn_suppressed = 0
    skipped_recording_off = 0
    eligible_first: str | None = None
    eligible_last: str | None = None
    eligible_conversations: set[str] = set()

    for message in reader.iter_messages(account_id):
        content_digest = hashlib.sha256(message.content.encode("utf-8")).hexdigest()
        hasher.update(
            f"M|{message.message_id}|{message.conversation_id}|{message.role}|"
            f"{message.status}|{message.created_at}|{content_digest}\n".encode()
        )
        if message.role != "user":
            continue
        user_total += 1
        decision = classify_history_message(
            message,
            account_id=account_id,
            tombstoned_message_ids=tombstoned,
            withdrawn_source_ids=withdrawn_source_ids,
            withdrawn_identity_keys=withdrawn_keys,
            content_blocks=controls.content_blocks,
            recording_enabled=controls.recording_enabled,
        )
        if decision.readable:
            readable_user_ids.add(message.message_id)
        if decision.eligible:
            eligible += 1
            eligible_conversations.add(message.conversation_id)
            if eligible_first is None or message.created_at < eligible_first:
                eligible_first = message.created_at
            if eligible_last is None or message.created_at > eligible_last:
                eligible_last = message.created_at
        elif decision.skip_reason == "tombstoned":
            skipped_tombstoned += 1
        elif decision.skip_reason == "unfinished":
            skipped_unfinished += 1
        elif decision.skip_reason == "empty":
            skipped_empty += 1
        elif decision.skip_reason == "content_blocked":
            skipped_content_blocked += 1
        elif decision.skip_reason == "withdrawn_suppressed":
            skipped_withdrawn_suppressed += 1
        elif decision.skip_reason == "recording_off":
            skipped_recording_off += 1

    for item in sorted(items, key=lambda entry: entry.profile_item_id):
        text_digest = hashlib.sha256(item.text.encode("utf-8")).hexdigest()
        quotes_digest = digest_lines(
            [f"{key}={value}" for key, value in sorted(item.source_quotes.items())]
        )
        hasher.update(
            f"I|{item.profile_item_id}|{item.status}|{item.version}|"
            f"{item.updated_at}|{item.write_origin}|{item.user_edited_at or ''}|"
            f"{item.fact_key}|{item.usage_purpose or ''}|{text_digest}|"
            f"{','.join(item.source_message_ids)}|{quotes_digest}|"
            f"{item.source_record_id or ''}|{item.migration_run_id or ''}\n".encode()
        )

    block_digest = digest_lines(
        [
            hashlib.sha256(block.encode("utf-8")).hexdigest()
            for block in controls.content_blocks
        ]
    )
    hasher.update(
        f"C|{int(controls.recording_enabled)}|{int(controls.usage_enabled)}|"
        f"{controls.usage_control_version}|{block_digest}|"
        f"{digest_lines(list(controls.tombstoned_message_ids))}\n".encode()
    )
    for entry in sorted(feedback, key=lambda row: row.feedback_id):
        hasher.update(
            f"F|{entry.feedback_id}|{entry.profile_item_id}|{entry.kind}|"
            f"{entry.item_version}|{entry.created_at}\n".encode()
        )
    hasher.update(f"G|{migration_reports}\n".encode())

    items_with_ref = sum(1 for item in items if item.source_record_id)
    unresolved_refs = reader.count_unresolved_source_record_refs(account_id, items)

    return AccountSnapshot(
        account_id=account_id,
        schema_version=schema_version,
        state_digest=hasher.hexdigest(),
        items=_classify_items(items, readable_user_ids, tombstoned),
        chat_scope=ChatScopeSummary(
            user_messages_total=user_total,
            eligible=eligible,
            eligible_first_at=eligible_first,
            eligible_last_at=eligible_last,
            eligible_conversation_ids=tuple(sorted(eligible_conversations)),
            skipped_tombstoned=skipped_tombstoned,
            skipped_unfinished=skipped_unfinished,
            skipped_empty=skipped_empty,
            skipped_content_blocked=skipped_content_blocked,
            skipped_withdrawn_suppressed=skipped_withdrawn_suppressed,
            skipped_recording_off=skipped_recording_off,
        ),
        controls=ControlsSummary(
            recording_enabled=controls.recording_enabled,
            usage_enabled=controls.usage_enabled,
            usage_control_version=controls.usage_control_version,
            content_block_count=len(controls.content_blocks),
            tombstone_count=len(controls.tombstoned_message_ids),
        ),
        domain_refs=DomainRefsSummary(
            feedback_count=len(feedback),
            items_with_source_record_ref=items_with_ref,
            unresolved_source_record_refs=unresolved_refs,
            migration_report_count=migration_reports,
        ),
    )


__all__ = [
    "HistoryMessageDecision",
    "classify_history_message",
    "compute_account_snapshot",
]
