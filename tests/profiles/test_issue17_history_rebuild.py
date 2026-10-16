"""画像改进工单 17：可恢复历史重整引擎的端到端合同测试。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from bridges.profiles.automatic import RuleBasedAutomaticProfileExtractor
from bridges.profiles.history_preview import preview_history
from bridges.profiles.history_rebuild import (
    cleanup_rebuild,
    rebuild_status,
    start_rebuild,
)
from bridges.profiles.history_rebuild_contracts import (
    HISTORY_REBUILD_POLICY_VERSION,
    ProfileRebuildError,
    RebuildWindow,
    rebuild_id_for,
)
from bridges.profiles.history_rebuild_drive import run_rebuild
from bridges.profiles.history_rebuild_runtime import ExtractorFactory
from bridges.profiles.replay import DatabaseIdentity, resolve_authoritative_database
from bridges.storage.database import BridgesDatabase
from tests.profiles.test_issue16_history_preview import (
    ACCOUNT,
    TS,
    _business_row_counts,
    _open_database,
    _seed_account,
    _seed_item,
    _seed_message,
    _settings,
)

TS2 = "2026-09-01T00:00:00+00:00"
FIXED_NOW = datetime(2026, 10, 16, 4, 0, 0, tzinfo=UTC)


def _authority(database_path: Path) -> DatabaseIdentity:
    return resolve_authoritative_database(settings=_settings(database_path))


def _preview(database_path: Path):
    return preview_history(_authority(database_path), account_id=ACCOUNT, now=FIXED_NOW)


def _rule_factory() -> ExtractorFactory:
    def factory(database: Any, neighbor: Any, memory: Any) -> Any:
        del database, neighbor, memory
        return RuleBasedAutomaticProfileExtractor()

    return factory


def _seed_history(database: BridgesDatabase) -> dict[str, str]:
    """合成带屏障与既有条目的旧库（工单 17 的窗口来源）。"""

    _seed_account(database, ACCOUNT)
    prefix = "alice"
    messages = {
        "hobby": f"msg-{prefix}-hobby",
        "goal": f"msg-{prefix}-goal",
        "cat": f"msg-{prefix}-cat",
        "stopped": f"msg-{prefix}-stopped",
        "empty": f"msg-{prefix}-empty",
        "tombstoned": f"msg-{prefix}-tombstoned",
        "blocked": f"msg-{prefix}-blocked",
        "withdrawn": f"msg-{prefix}-withdrawn",
        "assistant": f"msg-{prefix}-assistant",
        "missing": f"msg-{prefix}-deleted-source",
    }
    _seed_message(database, ACCOUNT, messages["hobby"], "我喜欢跑步，每周跑三次。")
    _seed_message(
        database, ACCOUNT, messages["goal"], "我在准备考研，目标是北航。", created_at=TS2
    )
    _seed_message(database, ACCOUNT, messages["cat"], "我养了一只猫叫团子")
    _seed_message(
        database, ACCOUNT, messages["stopped"], "这是一个被停止的回合", status="stopped"
    )
    _seed_message(database, ACCOUNT, messages["empty"], "   ")
    _seed_message(database, ACCOUNT, messages["tombstoned"], "这段历史已经被删除")
    _seed_message(database, ACCOUNT, messages["blocked"], "这段内容被禁止记录")
    _seed_message(database, ACCOUNT, messages["withdrawn"], "我其实搬去了上海")
    _seed_message(
        database, ACCOUNT, messages["assistant"], "好的，我记住了", role="assistant"
    )
    _seed_item(database, ACCOUNT, f"item-{prefix}-manual", "我叫小林", origin="user")
    _seed_item(
        database,
        ACCOUNT,
        f"item-{prefix}-auto-ok",
        "我养了一只猫叫团子",
        sources=(messages["cat"],),
        quotes={messages["cat"]: "我养了一只猫叫团子"},
    )
    _seed_item(
        database,
        ACCOUNT,
        f"item-{prefix}-auto-missing",
        "我在读大二",
        sources=(messages["missing"],),
    )
    _seed_item(
        database,
        ACCOUNT,
        f"item-{prefix}-withdrawn",
        "",
        status="withdrawn",
        stored_identity_key="withdrawn-identity-alice",
        sources=(messages["withdrawn"],),
    )
    _seed_item(
        database, ACCOUNT, f"item-{prefix}-superseded", "我在读大一", status="superseded"
    )
    with database.transaction():
        database.connection.execute(
            "INSERT INTO profile_extraction_tombstones "
            "(account_id, message_id, created_at) VALUES (?, ?, ?)",
            (ACCOUNT, messages["tombstoned"], TS),
        )
        database.connection.execute(
            "INSERT INTO profile_extraction_privacy_blocks "
            "(block_id, account_id, scope, normalized_value, created_at) "
            "VALUES (?, ?, 'content', ?, ?)",
            (f"block-{prefix}", ACCOUNT, "这段内容被禁止记录", TS),
        )
    return messages


def _tables(database: BridgesDatabase) -> dict[str, int]:
    return _business_row_counts(database, ACCOUNT)


def test_start_builds_isolated_copy_and_seeds_scope(tmp_path: Path) -> None:
    database_path, database = _open_database(tmp_path)
    messages = _seed_history(database)
    manifest = _preview(database_path)
    authority = _authority(database_path)
    before_counts = _tables(database)
    work = tmp_path / "work"

    report = start_rebuild(authority, manifest, work_dir=work, now=FIXED_NOW)

    progress = report["progress"]
    assert isinstance(progress, dict)
    assert report["reused"] is False
    assert progress["window_total"] == 8
    assert progress["eligible"] == 3
    assert progress["skipped"] == 5
    assert progress["skipped_by_reason"] == {
        "content_blocked": 1,
        "empty": 1,
        "tombstoned": 1,
        "unfinished": 1,
        "withdrawn_suppressed": 1,
    }
    assert progress["pending"] == 3 and progress["scheduled"] == 0
    assert (
        progress["window_total"],
        progress["eligible"],
    ) == (manifest.chat_scope.user_messages_total, manifest.chat_scope.eligible)

    assert (work / "rebuild.json").exists()
    assert (work / "preview.json").exists()
    assert (work / "history.db").exists()
    assert (work / "working" / "bridges.db").exists()

    working = BridgesDatabase(work / "working" / "bridges.db")
    working.initialize()
    rows = working.connection.execute(
        "SELECT message_id, status, skip_reason FROM profile_rebuild_items"
        " WHERE rebuild_id = ? ORDER BY message_id",
        (report["rebuild_id"],),
    ).fetchall()
    working.close()
    statuses = {str(row["message_id"]): str(row["status"]) for row in rows}
    assert len(rows) == 8
    assert statuses[messages["hobby"]] == "pending"
    assert statuses[messages["goal"]] == "pending"
    assert statuses[messages["cat"]] == "pending"
    assert statuses[messages["blocked"]] == "skipped"

    assert _tables(database) == before_counts
    assert _preview(database_path).state_digest == manifest.state_digest

    again = start_rebuild(authority, manifest, work_dir=work, now=FIXED_NOW)
    assert again["reused"] is True
    assert again["rebuild_id"] == report["rebuild_id"]

    with pytest.raises(ProfileRebuildError) as conflict:
        start_rebuild(
            authority,
            manifest,
            work_dir=work,
            window=RebuildWindow(TS2, None),
            now=FIXED_NOW,
        )
    assert conflict.value.code == "work_dir_conflict"


def test_start_rejects_stale_preview(tmp_path: Path) -> None:
    database_path, database = _open_database(tmp_path)
    messages = _seed_history(database)
    manifest = _preview(database_path)
    _seed_message(database, ACCOUNT, messages["missing"], "我在读大二")

    with pytest.raises(ProfileRebuildError) as stale:
        start_rebuild(_authority(database_path), manifest, work_dir=tmp_path / "work")
    assert stale.value.code == "preview_stale"


def test_run_extracts_scope_bounded_and_resumable(tmp_path: Path) -> None:
    database_path, database = _open_database(tmp_path)
    messages = _seed_history(database)
    manifest = _preview(database_path)
    authority = _authority(database_path)
    work = tmp_path / "work"
    start_rebuild(authority, manifest, work_dir=work, now=FIXED_NOW)

    first = run_rebuild(work, extractor_factory=_rule_factory(), max_ticks=1, now=FIXED_NOW)
    assert isinstance(first["progress"], dict)
    assert first["ticks"] == 1
    assert first["reached_budget"] is True
    assert first["completed"] is False
    assert first["progress"]["extracted"] == 1

    report = first
    attempts = 1
    while not report["completed"] and attempts < 10:
        report = run_rebuild(
            work, extractor_factory=_rule_factory(), max_ticks=100, now=FIXED_NOW
        )
        attempts += 1

    assert report["completed"] is True
    progress = report["progress"]
    assert progress["extracted"] == 3
    assert progress["committed"] == 2
    assert progress["skipped"] == 5
    assert progress["failed"] == 0
    assert progress["pending"] == 0 and progress["scheduled"] == 0

    finished = run_rebuild(
        work, extractor_factory=_rule_factory(), max_ticks=100, now=FIXED_NOW
    )
    assert finished["completed"] is True
    assert finished["ticks"] == 0

    status = rebuild_status(work)
    assert status["extractor_version"]
    assert status["completed_at"] is not None
    assert status["reconciled_at"] is None

    working = BridgesDatabase(work / "working" / "bridges.db")
    working.initialize()
    extracted = working.connection.execute(
        "SELECT text, source_message_ids_json FROM profile_items"
        " WHERE account_id = ? AND write_origin = 'automatic' AND status = 'active'",
        (ACCOUNT,),
    ).fetchall()
    working.close()
    by_source: dict[str, str] = {}
    for row in extracted:
        for message_id in json.loads(str(row["source_message_ids_json"])):
            by_source[message_id] = str(row["text"])
    assert by_source[messages["hobby"]] == "我喜欢跑步"
    assert by_source[messages["goal"]] == "北航"

    assert _tables(database) == {
        "profile_items": 5,
        "profile_extraction_runs": 0,
        "profile_extraction_tasks": 0,
        "profile_extraction_observations": 0,
        "profile_extraction_tombstones": 1,
        "profile_extraction_privacy_blocks": 1,
        "profile_account_controls": 0,
        "profile_item_feedback": 0,
    }


def test_resume_mid_flight_is_idempotent(tmp_path: Path) -> None:
    database_path, database = _open_database(tmp_path)
    messages = _seed_history(database)
    manifest = _preview(database_path)
    work = tmp_path / "work"
    start_rebuild(_authority(database_path), manifest, work_dir=work, now=FIXED_NOW)

    first = run_rebuild(work, extractor_factory=_rule_factory(), max_ticks=1, now=FIXED_NOW)
    assert first["completed"] is False

    report = first
    attempts = 1
    while not report["completed"] and attempts < 10:
        report = run_rebuild(
            work, extractor_factory=_rule_factory(), max_ticks=100, now=FIXED_NOW
        )
        attempts += 1
    assert report["completed"] is True
    assert report["progress"]["extracted"] == 3
    assert report["progress"]["committed"] == 2

    working = BridgesDatabase(work / "working" / "bridges.db")
    working.initialize()
    hobby_runs = working.connection.execute(
        "SELECT COUNT(*) AS total FROM profile_extraction_runs"
        " WHERE account_id = ? AND message_id = ? AND status = 'succeeded'",
        (ACCOUNT, messages["hobby"]),
    ).fetchone()
    hobby_items = working.connection.execute(
        "SELECT COUNT(*) AS total FROM profile_items"
        " WHERE account_id = ? AND source_message_ids_json LIKE ?",
        (ACCOUNT, f"%{messages['hobby']}%"),
    ).fetchone()
    manual = working.connection.execute(
        "SELECT text, version, user_edited_at FROM profile_items WHERE profile_item_id = ?",
        ("item-alice-manual",),
    ).fetchone()
    working.close()
    assert hobby_runs is not None and int(hobby_runs["total"]) == 1
    assert hobby_items is not None and int(hobby_items["total"]) == 1
    assert manual is not None and str(manual["text"]) == "我叫小林"
    assert manual["user_edited_at"] is None


def _park_inflight_item(
    work: Path, *, message_id: str, barrier: str | None
) -> None:
    """把一条在途任务模拟成重试耗尽；``barrier`` 时改为提交前屏障终结。"""

    working = BridgesDatabase(work / "working" / "bridges.db")
    working.initialize()
    with working.transaction():
        if barrier is None:
            task = working.connection.execute(
                "SELECT task_id FROM profile_extraction_tasks"
                " WHERE account_id = ? AND message_id = ?",
                (ACCOUNT, message_id),
            ).fetchone()
            assert task is not None
            working.connection.execute(
                "UPDATE task_claims SET status = 'failed', next_retry_at = NULL"
                " WHERE task_key = ?",
                (str(task["task_id"]),),
            )
        else:
            working.connection.execute(
                "UPDATE profile_extraction_runs SET status = 'exhausted',"
                " last_error = ? WHERE account_id = ? AND message_id = ?",
                (barrier, ACCOUNT, message_id),
            )
    working.close()


def _scheduled_messages(work: Path) -> list[str]:
    working = BridgesDatabase(work / "working" / "bridges.db")
    working.initialize()
    rows = working.connection.execute(
        "SELECT message_id FROM profile_rebuild_items WHERE status = 'scheduled'"
        " ORDER BY message_id"
    ).fetchall()
    working.close()
    return [str(row["message_id"]) for row in rows]


def test_retry_exhaustion_marks_failed_not_success(tmp_path: Path) -> None:
    database_path, database = _open_database(tmp_path)
    messages = _seed_history(database)
    manifest = _preview(database_path)
    work = tmp_path / "work"
    start_rebuild(_authority(database_path), manifest, work_dir=work, now=FIXED_NOW)

    run_rebuild(work, extractor_factory=_rule_factory(), max_ticks=1, now=FIXED_NOW)
    pending = _scheduled_messages(work)
    assert pending == sorted([messages["goal"], messages["hobby"]])
    _park_inflight_item(work, message_id=messages["goal"], barrier=None)

    report = run_rebuild(
        work, extractor_factory=_rule_factory(), max_ticks=100, now=FIXED_NOW
    )
    assert report["completed"] is True
    progress = report["progress"]
    assert progress["failed"] == 1
    assert progress["extracted"] == 2
    assert progress["committed"] == 1
    assert "source_invalidated" not in progress["skipped_by_reason"]


def test_privacy_barrier_skip_is_not_failure(tmp_path: Path) -> None:
    database_path, database = _open_database(tmp_path)
    messages = _seed_history(database)
    manifest = _preview(database_path)
    work = tmp_path / "work"
    start_rebuild(_authority(database_path), manifest, work_dir=work, now=FIXED_NOW)

    run_rebuild(work, extractor_factory=_rule_factory(), max_ticks=1, now=FIXED_NOW)
    _park_inflight_item(
        work,
        message_id=messages["hobby"],
        barrier="profile_extraction_version_unavailable",
    )

    report = run_rebuild(
        work, extractor_factory=_rule_factory(), max_ticks=100, now=FIXED_NOW
    )
    assert report["completed"] is True
    progress = report["progress"]
    assert progress["failed"] == 0
    assert progress["extracted"] == 2
    assert progress["skipped_by_reason"].get("version_unavailable") == 1


def test_tombstone_barrier_blocks_late_proposal(tmp_path: Path) -> None:
    """重整中途出现墓碑：提交前复核拒绝迟到写入，如实记为屏障跳过。"""

    database_path, database = _open_database(tmp_path)
    _seed_history(database)
    manifest = _preview(database_path)
    work = tmp_path / "work"
    start_rebuild(_authority(database_path), manifest, work_dir=work, now=FIXED_NOW)

    run_rebuild(work, extractor_factory=_rule_factory(), max_ticks=1, now=FIXED_NOW)
    scheduled = _scheduled_messages(work)
    assert len(scheduled) == 2
    victim = scheduled[0]

    # 模拟重整期间用户在真实库删除该消息：工作副本按同一屏障语义落墓碑。
    working = BridgesDatabase(work / "working" / "bridges.db")
    working.initialize()
    with working.transaction():
        working.connection.execute(
            "INSERT INTO profile_extraction_tombstones "
            "(account_id, message_id, created_at) VALUES (?, ?, ?)",
            (ACCOUNT, victim, TS),
        )
    working.close()

    report = run_rebuild(
        work, extractor_factory=_rule_factory(), max_ticks=100, now=FIXED_NOW
    )
    assert report["completed"] is True
    progress = report["progress"]
    assert progress["failed"] == 0
    assert progress["extracted"] == 2
    assert progress["skipped_by_reason"].get("source_invalidated") == 1

    working = BridgesDatabase(work / "working" / "bridges.db")
    working.initialize()
    item = working.connection.execute(
        "SELECT status, skip_reason FROM profile_rebuild_items"
        " WHERE message_id = ?",
        (victim,),
    ).fetchone()
    runs = working.connection.execute(
        "SELECT status, last_error FROM profile_extraction_runs"
        " WHERE account_id = ? AND message_id = ?",
        (ACCOUNT, victim),
    ).fetchall()
    leaked = working.connection.execute(
        "SELECT COUNT(*) AS total FROM profile_items"
        " WHERE account_id = ? AND status = 'active'"
        " AND source_message_ids_json LIKE ?",
        (ACCOUNT, f"%{victim}%"),
    ).fetchone()
    working.close()

    assert item is not None
    assert str(item["status"]) == "skipped"
    assert str(item["skip_reason"]) == "source_invalidated"
    assert [(str(row["status"]), str(row["last_error"])) for row in runs] == [
        ("exhausted", "profile_extraction_source_invalidated")
    ]
    assert leaked is not None and int(leaked["total"]) == 0


def test_version_mismatch_blocks_resume(tmp_path: Path) -> None:
    database_path, database = _open_database(tmp_path)
    _seed_history(database)
    manifest = _preview(database_path)
    work = tmp_path / "work"
    start_rebuild(_authority(database_path), manifest, work_dir=work, now=FIXED_NOW)

    metadata_path = work / "rebuild.json"
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    rebuild_id = str(payload["rebuild_id"])
    working = BridgesDatabase(work / "working" / "bridges.db")
    working.initialize()
    with working.transaction():
        working.connection.execute(
            "UPDATE profile_rebuild_runs SET extractor_version = ?"
            " WHERE rebuild_id = ?",
            ("profile-auto-v2+signal-other", rebuild_id),
        )
    working.close()

    with pytest.raises(ProfileRebuildError) as mismatch:
        run_rebuild(work, extractor_factory=_rule_factory(), max_ticks=1, now=FIXED_NOW)
    assert mismatch.value.code == "rebuild_version_mismatch"


def test_cleanup_guards_and_purge(tmp_path: Path) -> None:
    database_path, database = _open_database(tmp_path)
    _seed_history(database)
    manifest = _preview(database_path)
    work = tmp_path / "work"
    start_rebuild(_authority(database_path), manifest, work_dir=work, now=FIXED_NOW)

    with pytest.raises(ProfileRebuildError) as unsettled:
        cleanup_rebuild(work)
    assert unsettled.value.code == "rebuild_not_settled"

    report = run_rebuild(
        work, extractor_factory=_rule_factory(), max_ticks=100, now=FIXED_NOW
    )
    assert report["completed"] is True

    cleaned = cleanup_rebuild(work)
    assert not (work / "working").exists()
    assert (work / "history.db").exists()
    assert (work / "rebuild.json").exists()

    repeated = cleanup_rebuild(work)
    assert repeated["removed"] == []
    assert cleaned["removed"] == [str(work / "working")]

    purged = cleanup_rebuild(work, purge=True)
    assert purged["purged"] is True
    assert not (work / "history.db").exists()
    assert not (work / "preview.json").exists()
    assert not (work / "rebuild.json").exists()
    with pytest.raises(ProfileRebuildError) as missing:
        rebuild_status(work)
    assert missing.value.code == "rebuild_metadata_missing"


def test_rebuild_id_binds_window_and_digests() -> None:
    kwargs = {
        "account_id": ACCOUNT,
        "state_digest": "state",
        "policy_version": HISTORY_REBUILD_POLICY_VERSION,
        "preview_version": "history-preview-v1",
    }
    unbounded = rebuild_id_for(window=RebuildWindow(None, None), **kwargs)
    bounded = rebuild_id_for(window=RebuildWindow(TS, TS2), **kwargs)
    assert unbounded.startswith("profile-rebuild-")
    assert unbounded != bounded
    assert len(unbounded) == len("profile-rebuild-") + 32
