"""画像改进工单 17：完整对账与恢复/删除/导出演练的合同测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bridges.profiles.history_rebuild import (
    rebuild_status,
    start_rebuild,
)
from bridges.profiles.history_rebuild_contracts import ProfileRebuildError
from bridges.profiles.history_rebuild_drive import run_rebuild
from bridges.profiles.history_rebuild_reconcile import reconcile_rebuild
from bridges.storage.database import BridgesDatabase
from tests.profiles.test_issue16_history_preview import (
    _business_row_counts,
    _open_database,
    _seed_message,
)
from tests.profiles.test_issue17_history_rebuild import (
    ACCOUNT,
    FIXED_NOW,
    _authority,
    _preview,
    _rule_factory,
    _seed_history,
)


def _open_history(tmp_path: Path):
    database_path, database = _open_database(tmp_path)
    _seed_history(database)
    return database_path, database


def _completed_rebuild(tmp_path: Path):
    database_path, database = _open_history(tmp_path)
    manifest = _preview(database_path)
    work = tmp_path / "work"
    start_rebuild(_authority(database_path), manifest, work_dir=work, now=FIXED_NOW)
    report = run_rebuild(
        work, extractor_factory=_rule_factory(), max_ticks=100, now=FIXED_NOW
    )
    assert report["completed"] is True
    return database_path, database, work


def test_reconcile_requires_settled_rebuild(tmp_path: Path) -> None:
    database_path, database = _open_history(tmp_path)
    manifest = _preview(database_path)
    work = tmp_path / "work"
    start_rebuild(_authority(database_path), manifest, work_dir=work, now=FIXED_NOW)

    with pytest.raises(ProfileRebuildError) as unsettled:
        reconcile_rebuild(
            work, extractor_factory=_rule_factory(), authority=_authority(database_path)
        )
    assert unsettled.value.code == "rebuild_not_settled"


def test_reconcile_passes_and_persists_report(tmp_path: Path) -> None:
    database_path, database, work = _completed_rebuild(tmp_path)
    before = _business_row_counts(database, ACCOUNT)

    report = reconcile_rebuild(
        work,
        extractor_factory=_rule_factory(),
        authority=_authority(database_path),
        now=FIXED_NOW,
    )

    assert report["passed"] is True, json.dumps(
        report["errors"], ensure_ascii=False, indent=2
    )
    assert report["errors"] == []

    facts = report["facts"]
    assert facts["manual_items_expected"] == 1
    assert facts["manual_items_intact"] == 1
    assert facts["duplicate_fact_keys"] == []
    assert facts["tombstoned_late_success"] == 0

    evidence = report["evidence"]
    assert evidence["unresolved_refs"] == []
    assert evidence["tombstone_refs"] == []
    assert evidence["known_unverified_refs"] == [
        "item-alice-auto-missing:msg-alice-deleted-source"
    ]
    assert evidence["ledger_inconsistent"] == []

    assert report["controls"]["unchanged"] is True
    assert report["derived"]["overview_rebuildable"] is True
    assert report["derived"]["recall_stale_entries"] == 0

    export = report["export"]
    assert export["failures"] == []
    assert export["ledger_rows"]["runs"] == 1
    assert export["ledger_rows"]["items"] == 8
    assert export["ledger_rows"]["preview_total"] == 9

    deletion = report["deletion"]
    assert deletion["executed"] is True
    assert deletion["residual"] == {}
    assert deletion["account_row_left"] is False

    restore = report["restore"]
    assert restore["backup_verified"] is True
    assert restore["restore_verified"] is True
    assert restore["restore_reconciliation_passed"] is True
    assert restore["logical_summary_mismatches"] == {}

    drift = report["source_drift"]
    assert drift["checked"] is True
    assert drift["fresh"] is True

    report_path = work / "reconciliation.json"
    assert report_path.exists()
    persisted = json.loads(report_path.read_text(encoding="utf-8"))
    assert persisted["passed"] is True
    assert persisted["rebuild_id"] == report["rebuild_id"]

    status = rebuild_status(work)
    assert status["reconciled_at"] is not None

    # 演练全部在副本上进行：工作副本本身未被修改。
    assert _business_row_counts(database, ACCOUNT) == before


def test_reconcile_detects_manual_tamper(tmp_path: Path) -> None:
    database_path, database, work = _completed_rebuild(tmp_path)
    working = BridgesDatabase(work / "working" / "bridges.db")
    working.initialize()
    with working.transaction():
        working.connection.execute(
            "UPDATE profile_items SET text = '我叫小李' WHERE profile_item_id = ?",
            ("item-alice-manual",),
        )
    working.close()

    report = reconcile_rebuild(
        work,
        extractor_factory=_rule_factory(),
        authority=_authority(database_path),
        now=FIXED_NOW,
    )
    assert report["passed"] is False
    checks = {str(error["check"]) for error in report["errors"]}
    assert "manual_items_preserved" in checks
    assert rebuild_status(work)["reconciled_at"] is None


def test_reconcile_records_source_drift_as_warning(tmp_path: Path) -> None:
    database_path, database, work = _completed_rebuild(tmp_path)
    _seed_message(database, ACCOUNT, "msg-alice-later", "我后来搬去了杭州")

    report = reconcile_rebuild(
        work,
        extractor_factory=_rule_factory(),
        authority=_authority(database_path),
        now=FIXED_NOW,
    )
    assert report["passed"] is True
    assert report["source_drift"]["checked"] is True
    assert report["source_drift"]["fresh"] is False
    assert any("已前进" in warning for warning in report["warnings"])


def test_reconcile_without_authority_degrades_to_warning(tmp_path: Path) -> None:
    _database_path, _database, work = _completed_rebuild(tmp_path)

    report = reconcile_rebuild(
        work, extractor_factory=_rule_factory(), authority=None, now=FIXED_NOW
    )
    assert report["passed"] is True
    assert report["source_drift"] == {
        "checked": False,
        "reason": "authority_unavailable",
    }
    assert any("未提供权威来源" in warning for warning in report["warnings"])
