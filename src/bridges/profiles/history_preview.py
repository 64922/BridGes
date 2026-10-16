"""画像工单 16：历史重整的只读预览、可恢复备份与来源版本校验。

本模块是维护者入口的编排层，组合三类只读能力：

- :func:`preview_history`：只读预览账户的可重整条目与聊天范围，生成带版本的
  预览清单；不写库、不升级 schema、不调用模型。
- :func:`verify_preview_manifest`：在执行重整前重新校验来源版本；摘要不一致
  要求重新预览，预览不是永久授权快照。
- :func:`create_history_backup` / :func:`verify_history_restore`：在数据目录
  维护锁内创建 SQLite 在线备份，并在隔离副本中实际打开、对账事实、证据、
  控制、删除屏障与领域引用；只验证文件存在不算通过。

备份为实例级完整数据库副本，预览与对账的范围绑定到清单中的账户。备份与
恢复演练复用 :mod:`bridges.profiles.replay` 已验证的身份识别、在线备份与
备份校验原语（同一包内辅助函数），但本模块不是旧 ``profile-auto-v2``
回放命令，也不代表新历史重整合同的执行入口（执行属于工单 17）。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from bridges.profiles.history_contracts import (
    HISTORY_PREVIEW_VERSION,
    AccountSnapshot,
    HistoryPreviewManifest,
)
from bridges.profiles.history_repository import (
    ProfileHistorySnapshotReader,
    compute_account_snapshot,
)
from bridges.profiles.replay import (
    DatabaseIdentity,
    ProfileReplayError,
    VerifiedBackup,
    _check_integrity,
    _load_verified_backup,
    _probe_writer_state,
    _readonly_connection,
    _schema_version,
    create_verified_backup,
)
from bridges.storage.database import SCHEMA_VERSION, BridgesDatabase

#: 备份恢复记录合同版本。
HISTORY_BACKUP_RECORD_VERSION = "profile-history-backup-record-v1"
#: 独立恢复演练记录合同版本。
HISTORY_RESTORE_RECORD_VERSION = "profile-history-restore-record-v1"


class ProfileHistoryError(ProfileReplayError):
    """画像历史预览/备份/恢复演练的硬门失败；code 供 CLI 与工单 17 稳定分支。"""


def _history_error(exc: ProfileReplayError) -> ProfileHistoryError:
    """把共享原语的基类错误转换为本模块的统一错误类型（保留 code 与消息）。"""

    if isinstance(exc, ProfileHistoryError):
        return exc
    return ProfileHistoryError(exc.code, exc.message)


def _now_text(now: datetime | None) -> str:
    moment = now or datetime.now(UTC)
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def _source_files_digest(path: Path) -> str:
    """数据文件摘要：数据库与 WAL（不含瞬态 shm），只作来源审计标识。"""

    digest = hashlib.sha256()
    for candidate in (path, Path(f"{path}-wal")):
        if candidate.exists():
            digest.update(candidate.name.encode("utf-8"))
            digest.update(candidate.read_bytes())
    return digest.hexdigest()


def _inspect_source(authority: DatabaseIdentity) -> DatabaseIdentity:
    """校验权威库身份；版本不支持或完整性失败时显式拒绝，绝不隐式升级。"""

    path = authority.path.resolve()
    if not path.exists():
        raise ProfileHistoryError("database_missing", f"权威数据库不存在：{path}")
    if not path.is_file():
        raise ProfileHistoryError(
            "database_not_file", f"权威数据库路径不是文件：{path}"
        )
    connection = _readonly_connection(path)
    try:
        version = _schema_version(connection)
        _check_integrity(connection)
    finally:
        connection.close()
    if version is None:
        raise ProfileHistoryError(
            "schema_version_missing", "数据库没有 schema 版本记录，无法确认合同版本。"
        )
    if version < SCHEMA_VERSION:
        raise ProfileHistoryError(
            "schema_upgrade_required",
            f"数据库 schema 为 {version}，低于当前程序支持的 {SCHEMA_VERSION}；"
            "请先按项目迁移流程显式升级，预览不会隐式迁移。",
        )
    if version > SCHEMA_VERSION:
        raise ProfileHistoryError(
            "schema_version_unsupported",
            f"数据库 schema 为 {version}，高于当前程序支持的 {SCHEMA_VERSION}。",
        )
    return DatabaseIdentity(
        path=path,
        normalized_path=str(path),
        schema_version=version,
        identity_digest=_source_files_digest(path),
        size_bytes=path.stat().st_size,
        writer_state=_probe_writer_state(path.parent),
    )


def _snapshot_for_account(
    connection: sqlite3.Connection, account_id: str, schema_version: int
) -> AccountSnapshot:
    reader = ProfileHistorySnapshotReader(connection)
    if not reader.account_exists(account_id):
        raise ProfileHistoryError("account_missing", f"账户不存在：{account_id}")
    return compute_account_snapshot(
        connection, account_id, schema_version=schema_version
    )


def _read_snapshot(path: Path, account_id: str, schema_version: int) -> AccountSnapshot:
    """在单个只读事务里读取账户快照；写入方并发提交不改变本次结果。"""
    connection = _readonly_connection(path)
    try:
        connection.execute("BEGIN")
        snapshot = _snapshot_for_account(connection, account_id, schema_version)
        connection.execute("COMMIT")
        return snapshot
    except sqlite3.Error as exc:
        raise ProfileHistoryError(
            "database_read_failed", "只读查询失败，无法形成一致快照。"
        ) from exc
    finally:
        connection.close()


def _require_schema_version(source: DatabaseIdentity) -> int:
    """``_inspect_source`` 已拒绝空版本；这里把窄化显式化供类型检查。"""

    version = source.schema_version
    if version is None:
        raise ProfileHistoryError(
            "schema_version_missing", "数据库没有 schema 版本记录，无法确认合同版本。"
        )
    return version


def preview_history(
    authority: DatabaseIdentity,
    *,
    account_id: str,
    now: datetime | None = None,
) -> HistoryPreviewManifest:
    """只读生成账户的历史重整预览清单。"""

    try:
        source = _inspect_source(authority)
        schema_version = _require_schema_version(source)
        snapshot = _read_snapshot(source.path, account_id, schema_version)
    except ProfileHistoryError:
        raise
    except ProfileReplayError as exc:
        raise _history_error(exc) from exc
    return HistoryPreviewManifest(
        created_at=_now_text(now),
        account_id=account_id,
        source_path=str(source.path),
        schema_version=schema_version,
        source_digest=source.identity_digest,
        state_digest=snapshot.state_digest,
        writer_state=source.writer_state,
        items=snapshot.items,
        chat_scope=snapshot.chat_scope,
        controls=snapshot.controls,
        domain_refs=snapshot.domain_refs,
    )


def save_preview_manifest(manifest: HistoryPreviewManifest, path: str | Path) -> Path:
    """写出预览清单；目标目录必须已存在，不隐式创建。"""

    target = Path(path)
    if not target.parent.exists():
        raise ProfileHistoryError(
            "manifest_target_missing", f"预览清单目标目录不存在：{target.parent}"
        )
    try:
        target.write_text(
            json.dumps(manifest.as_dict(), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    except OSError as exc:
        raise ProfileHistoryError(
            "manifest_write_failed", f"无法写入预览清单：{target}"
        ) from exc
    return target


def load_preview_manifest(path: str | Path) -> HistoryPreviewManifest:
    """读取预览清单；缺失、损坏或版本不支持都显式失败。"""

    target = Path(path)
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ProfileHistoryError(
            "manifest_load_failed", f"无法读取预览清单：{target}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise ProfileHistoryError(
            "manifest_invalid", f"预览清单不是有效 JSON：{target}"
        ) from exc
    if not isinstance(payload, dict):
        raise ProfileHistoryError(
            "manifest_invalid", f"预览清单不是 JSON 对象：{target}"
        )
    if payload.get("manifest_version") != HISTORY_PREVIEW_VERSION:
        raise ProfileHistoryError(
            "manifest_version_unsupported",
            f"预览清单版本不受支持：{payload.get('manifest_version')!r}",
        )
    try:
        return HistoryPreviewManifest.from_dict(payload)
    except (KeyError, TypeError, ValueError) as exc:
        raise ProfileHistoryError(
            "manifest_invalid", f"预览清单字段缺失或非法：{target}"
        ) from exc


@dataclass(frozen=True)
class HistoryDriftReport:
    """预览清单与当前来源的漂移校验结果（不携带正文）。"""

    checked_at: str
    account_id: str
    fresh: bool
    requires_recompute: bool
    affected_scope: str
    source_path: str
    state_digest: str
    deltas: dict[str, object]
    manifest_version: str = HISTORY_PREVIEW_VERSION

    def as_dict(self) -> dict[str, object]:
        return {
            "manifest_version": self.manifest_version,
            "checked_at": self.checked_at,
            "account_id": self.account_id,
            "fresh": self.fresh,
            "requires_recompute": self.requires_recompute,
            "affected_scope": self.affected_scope,
            "source_path": self.source_path,
            "state_digest": self.state_digest,
            "deltas": dict(self.deltas),
        }


def verify_preview_manifest(
    authority: DatabaseIdentity,
    manifest: HistoryPreviewManifest,
    *,
    now: datetime | None = None,
) -> HistoryDriftReport:
    """对照当前数据源校验预览清单；漂移时明确要求重算受影响范围。"""

    try:
        source = _inspect_source(authority)
        snapshot = _read_snapshot(
            source.path, manifest.account_id, _require_schema_version(source)
        )
    except ProfileHistoryError:
        raise
    except ProfileReplayError as exc:
        raise _history_error(exc) from exc
    fresh = snapshot.state_digest == manifest.state_digest
    deltas: dict[str, object] = {
        "user_messages": {
            "preview": manifest.chat_scope.user_messages_total,
            "current": snapshot.chat_scope.user_messages_total,
        },
        "eligible_messages": {
            "preview": manifest.chat_scope.eligible,
            "current": snapshot.chat_scope.eligible,
        },
        "active_items": {
            "preview": manifest.items.active_total,
            "current": snapshot.items.active_total,
        },
        "withdrawn_items": {
            "preview": manifest.items.withdrawn,
            "current": snapshot.items.withdrawn,
        },
        "tombstoned_messages": {
            "preview": manifest.controls.tombstone_count,
            "current": snapshot.controls.tombstone_count,
        },
        "controls_changed": manifest.controls.as_dict()
        != snapshot.controls.as_dict(),
        "domain_refs_changed": manifest.domain_refs.as_dict()
        != snapshot.domain_refs.as_dict(),
    }
    affected_scope = (
        "无：清单摘要与当前数据源一致。"
        if fresh
        else (
            f"账户 {manifest.account_id}：消息或画像状态已变化，必须重新执行"
            "预览并以新清单为新基准；旧清单不得用于工单 17 的执行。"
        )
    )
    return HistoryDriftReport(
        checked_at=_now_text(now),
        account_id=manifest.account_id,
        fresh=fresh,
        requires_recompute=not fresh,
        affected_scope=affected_scope,
        source_path=str(source.path),
        state_digest=snapshot.state_digest,
        deltas=deltas,
    )


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
    if not record.verified:
        raise ProfileHistoryError(
            "backup_drill_failed",
            f"隔离恢复核对未通过：备份内容与预览清单不一致。{_mismatch_summary(restore)}"
            "详见恢复记录。",
        )
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
    if not record.verified:
        raise ProfileHistoryError(
            "backup_drill_failed",
            f"隔离恢复核对未通过：备份内容与预览清单不一致。{_mismatch_summary(restore)}",
        )
    return record


__all__ = [
    "HISTORY_BACKUP_RECORD_VERSION",
    "HISTORY_RESTORE_RECORD_VERSION",
    "HistoryBackupRecord",
    "HistoryDriftReport",
    "HistoryRestoreRecord",
    "ProfileHistoryError",
    "create_history_backup",
    "load_preview_manifest",
    "preview_history",
    "save_preview_manifest",
    "verify_history_restore",
    "verify_preview_manifest",
]
