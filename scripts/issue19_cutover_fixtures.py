"""画像工单 19 演练的合成账户、条目与确定性重试探针。"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

CUTOVER_ACCOUNT = "account-issue19-cutover"
RETRY_ACCOUNT = "account-issue19-retry"
TS = "2026-10-17T00:00:00+00:00"


def _check(checks: list[dict[str, Any]], check_id: str, ok: bool, detail: str) -> None:
    checks.append({"check_id": check_id, "passed": bool(ok), "detail": detail})


def seed_account(database: Any, account_id: str) -> None:
    with database.transaction():
        database.connection.execute(
            "INSERT INTO accounts (account_id, email, created_at) VALUES (?, ?, ?)",
            (account_id, f"{account_id}@example.com", TS),
        )
        database.connection.execute(
            "INSERT INTO conversations "
            "(conversation_id, account_id, title, mode, created_at, updated_at) "
            "VALUES (?, ?, '', 'companion', ?, ?)",
            (f"conversation-{account_id}", account_id, TS, TS),
        )


def seed_item(
    database: Any,
    account_id: str,
    item_id: str,
    text: str,
    *,
    status: str = "active",
    identity: str | None = None,
) -> None:
    key = identity or hashlib.sha256(f"{account_id}:{text}".encode()).hexdigest()
    with database.transaction():
        database.connection.execute(
            "INSERT INTO profile_items "
            "(profile_item_id, account_id, text, identity_key, fact_key, "
            "source_message_ids_json, status, write_origin, confidence, version, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, '', '[]', ?, 'automatic', 'medium', 1, ?, ?)",
            (item_id, account_id, text, key, status, TS, TS),
        )


def run_retry_probe(target_path: Path, checks: list[dict[str, Any]]) -> dict[str, Any]:
    from bridges.contracts.profile_extraction import ProfileExtractionStatus
    from bridges.profiles.atomic import AtomicProfileService, SqliteAtomicProfileRepository
    from bridges.profiles.automatic import (
        AutomaticProfileService,
        RuleBasedAutomaticProfileExtractor,
        SqliteAutomaticProfileRepository,
    )
    from bridges.profiles.four_dimensions import (
        FourDimensionProfileService,
        SqliteFourDimensionProfileRepository,
    )
    from bridges.storage.database import BridgesDatabase

    database = BridgesDatabase(target_path)
    database.initialize()
    seed_account(database, RETRY_ACCOUNT)
    messages = {
        "message-deleted": "我养了一只猫叫团子",
        "message-open": "我喜欢跑步",
    }

    def reader(account_id: str, message_id: str) -> Any:
        del account_id
        content = messages[message_id]
        return SimpleNamespace(
            content=content,
            created_at=datetime.now(UTC),
            conversation_id=f"conversation-{RETRY_ACCOUNT}",
            role="user",
            status="done",
        )

    dimensions = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=SqliteFourDimensionProfileRepository(database),
    )
    atomic = AtomicProfileService(dimensions, SqliteAtomicProfileRepository(database))
    service = AutomaticProfileService(
        four_dimension_service=dimensions,
        repository=SqliteAutomaticProfileRepository(database),
        extractor=RuleBasedAutomaticProfileExtractor(),
        atomic_profile_service=atomic,
        message_reader=reader,
    )
    before = int(
        database.connection.execute(
            "SELECT COUNT(*) FROM profile_four_dimension_records"
        ).fetchone()[0]
    )
    service.schedule_message_extraction(
        RETRY_ACCOUNT,
        conversation_id=f"conversation-{RETRY_ACCOUNT}",
        message_id="message-deleted",
        content=messages["message-deleted"],
        run_id="run-deleted",
    )
    service.mark_message_tombstone(RETRY_ACCOUNT, "message-deleted")
    service.run_retry_tick()
    deleted_tasks = service.list_retry_tasks(RETRY_ACCOUNT)
    deleted_items = atomic.list_items(RETRY_ACCOUNT)
    _check(
        checks,
        "retry_after_tombstone_stays_deleted",
        not deleted_items
        and all(task.status == ProfileExtractionStatus.EXHAUSTED for task in deleted_tasks),
        "墓碑后的待处理任务被耗尽，未写入新条目",
    )

    service.schedule_message_extraction(
        RETRY_ACCOUNT,
        conversation_id=f"conversation-{RETRY_ACCOUNT}",
        message_id="message-open",
        content=messages["message-open"],
        run_id="run-open",
    )
    service.run_retry_tick()
    open_items = atomic.list_items(RETRY_ACCOUNT)
    after = int(
        database.connection.execute(
            "SELECT COUNT(*) FROM profile_four_dimension_records"
        ).fetchone()[0]
    )
    _check(
        checks,
        "open_fact_written_to_atomic_only",
        any("跑步" in item.text for item in open_items) and before == after,
        "开放事实写入原子条目，四维记录计数未增加",
    )
    database.close()
    return {
        "deleted_task_statuses": sorted(task.status.value for task in deleted_tasks),
        "atomic_open_fact_count": len(open_items),
        "legacy_record_count_before": before,
        "legacy_record_count_after": after,
    }


__all__ = [
    "CUTOVER_ACCOUNT",
    "RETRY_ACCOUNT",
    "TS",
    "run_retry_probe",
    "seed_account",
    "seed_item",
]
