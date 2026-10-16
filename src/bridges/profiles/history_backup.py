"""画像工单 16：历史重整的可恢复备份与隔离恢复演练。

- :func:`create_history_backup`：按新鲜预览清单创建 SQLite 在线备份，并在
  隔离副本中实际打开、对账事实、证据、控制、删除屏障与领域引用；只验证
  文件存在不算通过。无论隔离开演结果如何都写出
  ``<target>.history-record.json`` 恢复记录。
- :func:`verify_history_restore`：对已有已验证备份重新执行恢复演练（可重复）。

备份为实例级完整数据库副本，预览与对账的范围绑定到清单中的账户。备份与
恢复演练复用 :mod:`bridges.profiles.replay` 已验证的身份识别、在线备份与
备份校验原语（同一包内辅助函数），但本模块不是旧 ``profile-auto-v2``
回放命令，也不代表新历史重整合同的执行入口（执行属于工单 17）。
"""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from bridges.profiles.history_contracts import (
    AccountSnapshot,
    HistoryPreviewManifest,
)
from bridges.profiles.history_preview import (
    ProfileHistoryError,
    _history_error,
    _now_text,
    verify_preview_manifest,
)
from bridges.profiles.history_repository import ProfileHistorySnapshotReader
from bridges.profiles.history_snapshot import compute_account_snapshot
from bridges.profiles.replay import (
    DatabaseIdentity,
    ProfileReplayError,
    VerifiedBackup,
    _check_integrity,
    _load_verified_backup,
    _readonly_connection,
    create_verified_backup,
)
from bridges.storage.database import SCHEMA_VERSION, BridgesDatabase

#: 备份恢复记录合同版本。
HISTORY_BACKUP_RECORD_VERSION = "profile-history-backup-record-v1"
#: 独立恢复演练记录合同版本。
HISTORY_RESTORE_RECORD_VERSION = "profile-history-restore-record-v1"


def _flatten(prefix: str, value: object, out: dict[str, object]) -> None:
    if isinstance(value, dict):
        for key, item in sorted(value.items(), key=lambda pair: str(pair[0])):
            _flatten(f"{prefix}.{key}", item, out)
        return
    out[prefix] = value


def _reconciliation_pairs(
    preview: HistoryPreviewManifest, snapshot: AccountSnapshot
) -> dict[str, tuple[object, object]]:
    expected: dict[str, object] = {}
    restored: dict[str, object] = {}
    _flatten("items", preview.items.as_dict(), expected)
    _flatten("items", snapshot.items.as_dict(), restored)
    _flatten("chat_scope", preview.chat_scope.as_dict(), expected)
    _flatten("chat_scope", snapshot.chat_scope.as_dict(), restored)
    _flatten("controls", preview.controls.as_dict(), expected)
    _flatten("controls", snapshot.controls.as_dict(), restored)
    _flatten("domain_refs", preview.domain_refs.as_dict(), expected)
    _flatten("domain_refs", snapshot.domain_refs.as_dict(), restored)
    pairs: dict[str, tuple[object, object]] = {}
    for key in sorted(expected):
        pairs[key] = (expected[key], restored.get(key))
    return pairs


def _run_restore_drill(
    backup: VerifiedBackup,
    preview: HistoryPreviewManifest,
    *,
    work_dir: Path | None,
) -> dict[str, object]:
    """在隔离副本中实际打开备份并核对合同范围内的全部计数与摘要。"""

    if backup.schema_version != SCHEMA_VERSION:
        raise ProfileHistoryError(
            "backup_schema_invalid",
            f"备份 schema 为 {backup.schema_version}，当前程序恢复需要 {SCHEMA_VERSION}。",
        )
    if work_dir is not None and not Path(work_dir).exists():
        raise ProfileHistoryError(
            "work_dir_missing", f"恢复演练工作目录不存在：{work_dir}"
        )
    result: dict[str, object]
    with tempfile.TemporaryDirectory(
        dir=None if work_dir is None else str(work_dir),
        prefix="profile-history-restore-",
    ) as temp_dir:
        restored_path = Path(temp_dir) / "bridges.db"
        try:
            shutil.copyfile(backup.path, restored_path)
        except OSError as exc:
            raise ProfileHistoryError(
                "restore_copy_failed", "无法创建隔离恢复副本。"
            ) from exc
        probe = _readonly_connection(restored_path)
        try:
            _check_integrity(probe)
        finally:
            probe.close()

        database = BridgesDatabase(restored_path)
        try:
            version = database.initialize()
            connection = database.connection
            reader = ProfileHistorySnapshotReader(connection)
            if not reader.account_exists(preview.account_id):
                raise ProfileHistoryError(
                    "restore_account_missing",
                    f"恢复副本中不存在预览清单绑定的账户：{preview.account_id}",
                )
            snapshot = compute_account_snapshot(
                connection, preview.account_id, schema_version=version
            )
        finally:
            database.close()

        mismatches: dict[str, dict[str, object]] = {}
        if snapshot.state_digest != preview.state_digest:
            mismatches["state_digest"] = {
                "expected": preview.state_digest,
                "restored": snapshot.state_digest,
            }
        pairs = _reconciliation_pairs(preview, snapshot)
        for key, (expected_value, restored_value) in pairs.items():
            if expected_value != restored_value:
                mismatches[key] = {
                    "expected": expected_value,
                    "restored": restored_value,
                }
        result = {
            "opened": True,
            "integrity_check": "ok",
            "schema_version": version,
            "state_digest": snapshot.state_digest,
            "state_digest_matches_preview": snapshot.state_digest
            == preview.state_digest,
            "reconciliation_passed": not mismatches,
            "checked_keys": sorted(pairs),
            "mismatches": mismatches,
        }
    result["isolated_copy_removed"] = True
    return result


def _mismatch_summary(restore: dict[str, object]) -> str:
    mismatches = restore.get("mismatches")
    if not isinstance(mismatches, dict) or not mismatches:
        return ""
    keys = sorted(str(key) for key in mismatches)
    shown = "、".join(keys[:5])
    suffix = f" 等 {len(keys)} 项" if len(keys) > 5 else ""
    return f"不匹配项：{shown}{suffix}。"


def _ensure_reconciled(restore: dict[str, object], *, hint: str = "") -> None:
    """演练未配平时统一报 ``backup_drill_failed``，绝不把坏备份当可用。"""

    if bool(restore.get("reconciliation_passed")):
        return
    raise ProfileHistoryError(
        "backup_drill_failed",
        "隔离恢复核对未通过：备份内容与预览清单不一致。"
        f"{_mismatch_summary(restore)}{hint}",
    )


@dataclass(frozen=True)
class HistoryBackupRecord:
    """备份与恢复演练记录：一致性方式、校验结果与恢复所需版本。"""

    created_at: str
    account_id: str
    consistency: dict[str, object]
    preview: dict[str, object]
    backup: dict[str, object]
    restore: dict[str, object]
    verified: bool
    record_version: str = HISTORY_BACKUP_RECORD_VERSION

    def as_dict(self) -> dict[str, object]:
        return {
            "record_version": self.record_version,
            "created_at": self.created_at,
            "account_id": self.account_id,
            "verified": self.verified,
            "consistency": dict(self.consistency),
            "preview": dict(self.preview),
            "backup": dict(self.backup),
            "restore": dict(self.restore),
        }


@dataclass(frozen=True)
class HistoryRestoreRecord:
    """独立恢复演练记录（对已有备份重新核对）。"""

    created_at: str
    account_id: str
    preview: dict[str, object]
    backup: dict[str, object]
    restore: dict[str, object]
    verified: bool
    record_version: str = HISTORY_RESTORE_RECORD_VERSION

    def as_dict(self) -> dict[str, object]:
        return {
            "record_version": self.record_version,
            "created_at": self.created_at,
            "account_id": self.account_id,
            "verified": self.verified,
            "preview": dict(self.preview),
            "backup": dict(self.backup),
            "restore": dict(self.restore),
        }


def _backup_record_path(backup_path: Path) -> Path:
    return Path(f"{backup_path}.history-record.json")


def _write_backup_record(backup_path: Path, record: HistoryBackupRecord) -> None:
    target = _backup_record_path(backup_path)
    try:
        target.write_text(
            json.dumps(record.as_dict(), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    except OSError as exc:
        raise ProfileHistoryError(
            "record_write_failed", f"无法写入备份恢复记录：{target}"
        ) from exc


def _preview_reference(manifest: HistoryPreviewManifest) -> dict[str, object]:
    return {
        "manifest_version": manifest.manifest_version,
        "created_at": manifest.created_at,
        "account_id": manifest.account_id,
        "state_digest": manifest.state_digest,
        "source_digest": manifest.source_digest,
    }


def _backup_reference(backup: VerifiedBackup) -> dict[str, object]:
    payload = backup.as_dict()
    payload["manifest_path"] = str(backup.manifest_path)
    return payload


def create_history_backup(
    authority: DatabaseIdentity,
    target: str | Path,
    *,
    preview: HistoryPreviewManifest,
    work_dir: Path | None = None,
    now: datetime | None = None,
) -> HistoryBackupRecord:
    """按预览清单创建可恢复备份，并在隔离副本中完成核对。

    备份必须先通过清单新鲜度校验：来源已经变化的清单不授权备份；备份创建
    在数据目录维护锁内进行，无法取得一致备份时显式失败。无论隔离开演结果
    如何都会写出 ``<target>.history-record.json`` 恢复记录。
    """

    drift = verify_preview_manifest(authority, preview)
    if not drift.fresh:
        raise ProfileHistoryError(
            "preview_stale",
            "预览清单与当前数据源不一致，必须重新预览后再创建备份。",
        )
    try:
        backup = create_verified_backup(authority, target)
    except ProfileHistoryError:
        raise
    except ProfileReplayError as exc:
        raise _history_error(exc) from exc
    consistency: dict[str, object] = {
        "method": "quiesced_sqlite_online_backup",
        "writer_state": "inactive",
        "restore_requirements": {
            "program_schema_version": SCHEMA_VERSION,
            "backup_schema_version": backup.schema_version,
        },
    }
    try:
        restore = _run_restore_drill(backup, preview, work_dir=work_dir)
    except ProfileReplayError as exc:
        record = HistoryBackupRecord(
            created_at=_now_text(now),
            account_id=preview.account_id,
            consistency=consistency,
            preview=_preview_reference(preview),
            backup=_backup_reference(backup),
            restore={"opened": False, "failure_code": exc.code},
            verified=False,
        )
        _write_backup_record(backup.path, record)
        if isinstance(exc, ProfileHistoryError):
            raise
        raise _history_error(exc) from exc
    record = HistoryBackupRecord(
        created_at=_now_text(now),
        account_id=preview.account_id,
        consistency=consistency,
        preview=_preview_reference(preview),
        backup=_backup_reference(backup),
        restore=restore,
        verified=bool(restore["reconciliation_passed"]),
    )
    _write_backup_record(backup.path, record)
    _ensure_reconciled(restore, hint="详见恢复记录。")
    return record


def verify_history_restore(
    backup_path: str | Path,
    *,
    preview: HistoryPreviewManifest,
    work_dir: Path | None = None,
    now: datetime | None = None,
) -> HistoryRestoreRecord:
    """对已有已验证备份重新执行隔离恢复核对（可重复运行）。"""

    try:
        backup = _load_verified_backup(Path(backup_path))
        restore = _run_restore_drill(backup, preview, work_dir=work_dir)
    except ProfileHistoryError:
        raise
    except ProfileReplayError as exc:
        raise _history_error(exc) from exc
    record = HistoryRestoreRecord(
        created_at=_now_text(now),
        account_id=preview.account_id,
        preview=_preview_reference(preview),
        backup=_backup_reference(backup),
        restore=restore,
        verified=bool(restore["reconciliation_passed"]),
    )
    _ensure_reconciled(restore)
    return record


__all__ = [
    "HISTORY_BACKUP_RECORD_VERSION",
    "HISTORY_RESTORE_RECORD_VERSION",
    "HistoryBackupRecord",
    "HistoryRestoreRecord",
    "create_history_backup",
    "verify_history_restore",
]
