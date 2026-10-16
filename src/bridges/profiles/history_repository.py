"""画像工单 16：历史重整预览与恢复对账的只读读取边界。

本模块只做只读查询与确定性摘要计算：不复用会初始化或写入的原子仓库，
也不把条目正文、消息正文写进输出。账户快照 :class:`AccountSnapshot`
覆盖事实、证据可得性、控制开关、删除屏障与当前 schema 可用的领域引用；
预览清单与备份恢复演练共用同一份计算，保证两侧核对的是同一合同。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass

from bridges.profiles.atomic import identity_key
from bridges.profiles.automatic import _normalize
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
from bridges.profiles.sensitive import MemorySensitivity, classify_memory_sensitivity


@dataclass(frozen=True)
class HistoryItemRecord:
    """一条原子条目的只读视图；``text`` 只在内存中用于确定性分类。"""

    profile_item_id: str
    status: str
    write_origin: str
    user_edited_at: str | None
    version: int
    created_at: str
    updated_at: str
    identity_key: str
    fact_key: str
    usage_purpose: str | None
    source_record_id: str | None
    migration_run_id: str | None
    text: str
    source_message_ids: tuple[str, ...]
    source_quotes: dict[str, str]


@dataclass(frozen=True)
class HistoryMessageRecord:
    """一条消息的只读视图；正文只在内存中参与摘要与门控判断。"""

    message_id: str
    conversation_id: str
    role: str
    status: str
    created_at: str
    content: str


@dataclass(frozen=True)
class HistoryControlsRecord:
    """账户控制开关与删除屏障的只读视图。"""

    recording_enabled: bool
    usage_enabled: bool
    usage_control_version: int
    content_blocks: tuple[str, ...]
    tombstoned_message_ids: tuple[str, ...]


@dataclass(frozen=True)
class HistoryFeedbackRecord:
    """一条画像反馈的领域引用（只作对账，不含正文）。"""

    feedback_id: str
    profile_item_id: str
    kind: str
    item_version: int
    created_at: str


def _json_str_tuple(raw: object) -> tuple[str, ...]:
    """解析 JSON 标识列表；损坏数据按空处理（证据不足方向，绝不伪造）。"""

    if not isinstance(raw, str) or not raw.strip():
        return ()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return ()
    if not isinstance(parsed, list):
        return ()
    return tuple(str(value) for value in parsed)


def _json_str_map(raw: object) -> dict[str, str]:
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(key): str(value) for key, value in parsed.items()}


class ProfileHistorySnapshotReader:
    """在给定连接上读取一个账户的只读历史快照。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def account_exists(self, account_id: str) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM accounts WHERE account_id = ?", (account_id,)
        ).fetchone()
        return row is not None

    def list_items(self, account_id: str) -> list[HistoryItemRecord]:
        rows = self._connection.execute(
            "SELECT profile_item_id, status, write_origin, user_edited_at, version, "
            "created_at, updated_at, identity_key, fact_key, usage_purpose, "
            "source_record_id, migration_run_id, text, source_message_ids_json, "
            "source_quotes_json "
            "FROM profile_items WHERE account_id = ? "
            "ORDER BY profile_item_id",
            (account_id,),
        ).fetchall()
        return [
            HistoryItemRecord(
                profile_item_id=str(row["profile_item_id"]),
                status=str(row["status"]),
                write_origin=str(row["write_origin"]),
                user_edited_at=(
                    str(row["user_edited_at"])
                    if row["user_edited_at"] is not None
                    else None
                ),
                version=int(row["version"]),
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
                identity_key=str(row["identity_key"]),
                fact_key=str(row["fact_key"]),
                usage_purpose=(
                    str(row["usage_purpose"])
                    if row["usage_purpose"] is not None
                    else None
                ),
                source_record_id=(
                    str(row["source_record_id"])
                    if row["source_record_id"] is not None
                    else None
                ),
                migration_run_id=(
                    str(row["migration_run_id"])
                    if row["migration_run_id"] is not None
                    else None
                ),
                text=str(row["text"]),
                source_message_ids=_json_str_tuple(row["source_message_ids_json"]),
                source_quotes=_json_str_map(row["source_quotes_json"]),
            )
            for row in rows
        ]

    def iter_messages(self, account_id: str) -> Iterator[HistoryMessageRecord]:
        cursor = self._connection.execute(
            "SELECT message_id, conversation_id, role, status, created_at, content "
            "FROM messages WHERE account_id = ? ORDER BY message_id",
            (account_id,),
        )
        for row in cursor:
            yield HistoryMessageRecord(
                message_id=str(row["message_id"]),
                conversation_id=str(row["conversation_id"]),
                role=str(row["role"]),
                status=str(row["status"]),
                created_at=str(row["created_at"]),
                content=str(row["content"]),
            )

    def read_controls(self, account_id: str) -> HistoryControlsRecord:
        recording_row = self._connection.execute(
            "SELECT 1 FROM profile_extraction_privacy_blocks "
            "WHERE account_id = ? AND scope = 'account' LIMIT 1",
            (account_id,),
        ).fetchone()
        block_rows = self._connection.execute(
            "SELECT normalized_value FROM profile_extraction_privacy_blocks "
            "WHERE account_id = ? AND scope = 'content' "
            "ORDER BY normalized_value",
            (account_id,),
        ).fetchall()
        tombstone_rows = self._connection.execute(
            "SELECT message_id FROM profile_extraction_tombstones "
            "WHERE account_id = ? ORDER BY message_id",
            (account_id,),
        ).fetchall()
        usage_row = self._connection.execute(
            "SELECT profile_usage_enabled, usage_control_version "
            "FROM profile_account_controls WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        return HistoryControlsRecord(
            recording_enabled=recording_row is None,
            usage_enabled=(
                True if usage_row is None else bool(usage_row["profile_usage_enabled"])
            ),
            usage_control_version=(
                0 if usage_row is None else int(usage_row["usage_control_version"])
            ),
            content_blocks=tuple(str(row["normalized_value"]) for row in block_rows),
            tombstoned_message_ids=tuple(
                str(row["message_id"]) for row in tombstone_rows
            ),
        )

    def list_feedback(self, account_id: str) -> list[HistoryFeedbackRecord]:
        rows = self._connection.execute(
            "SELECT feedback_id, profile_item_id, kind, item_version, created_at "
            "FROM profile_item_feedback WHERE account_id = ? "
            "ORDER BY feedback_id",
            (account_id,),
        ).fetchall()
        return [
            HistoryFeedbackRecord(
                feedback_id=str(row["feedback_id"]),
                profile_item_id=str(row["profile_item_id"]),
                kind=str(row["kind"]),
                item_version=int(row["item_version"]),
                created_at=str(row["created_at"]),
            )
            for row in rows
        ]

    def count_migration_reports(self, account_id: str) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) AS total FROM profile_item_migrations "
            "WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        return 0 if row is None else int(row["total"])

    def count_unresolved_source_record_refs(
        self, account_id: str, items: list[HistoryItemRecord]
    ) -> int:
        """统计指向已不存在的旧四维记录的条目数（领域引用对账）。"""

        refs = sorted(
            {item.source_record_id for item in items if item.source_record_id}
        )
        if not refs:
            return 0
        placeholders = ",".join("?" for _ in refs)
        rows = self._connection.execute(
            "SELECT record_id FROM profile_four_dimension_records "
            f"WHERE account_id = ? AND record_id IN ({placeholders})",
            (account_id, *refs),
        ).fetchall()
        existing = {str(row["record_id"]) for row in rows}
        return sum(1 for item in items if item.source_record_id not in existing)


def _matches_content_block(normalized_content: str, blocks: tuple[str, ...]) -> bool:
    """与自动抽取的禁止记录匹配保持一致：空值互相包含，双向子串命中。"""

    return any(
        block == normalized_content
        or block in normalized_content
        or normalized_content in block
        for block in blocks
    )


def _classify_items(
    items: list[HistoryItemRecord],
    readable_user_ids: set[str],
    tombstoned_message_ids: set[str],
) -> ItemEvidenceSummary:
    """把条目分为保留/可核验/缺失证据/敏感/屏障分类；只输出计数与标识。"""

    counts = dict.fromkeys(ITEM_COUNT_KEYS, 0)
    locators: dict[str, list[str]] = {key: [] for key in ITEM_LOCATOR_KEYS}

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
        if message.status != "done":
            skipped_unfinished += 1
            continue
        if not message.content.strip():
            skipped_empty += 1
            continue
        if message.message_id in tombstoned:
            skipped_tombstoned += 1
            continue
        readable_user_ids.add(message.message_id)
        normalized = _normalize(message.content)
        if _matches_content_block(normalized, controls.content_blocks):
            skipped_content_blocked += 1
            continue
        if (
            message.message_id in withdrawn_source_ids
            or identity_key(account_id, message.content) in withdrawn_keys
        ):
            skipped_withdrawn_suppressed += 1
            continue
        if not controls.recording_enabled:
            skipped_recording_off += 1
            continue
        eligible += 1
        eligible_conversations.add(message.conversation_id)
        if eligible_first is None or message.created_at < eligible_first:
            eligible_first = message.created_at
        if eligible_last is None or message.created_at > eligible_last:
            eligible_last = message.created_at

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
            f"{item.source_record_id or ''}|{item.migration_run_id or ''}\n"
            .encode()
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
    "HistoryControlsRecord",
    "HistoryFeedbackRecord",
    "HistoryItemRecord",
    "HistoryMessageRecord",
    "ProfileHistorySnapshotReader",
    "compute_account_snapshot",
]
