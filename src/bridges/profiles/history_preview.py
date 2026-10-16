"""画像工单 16：历史重整的只读预览与来源版本校验。

- :func:`preview_history`：只读预览账户的可重整条目与聊天范围，生成带版本的
  预览清单；不写库、不升级 schema、不调用模型。
- :func:`verify_preview_manifest`：在执行重整前重新校验来源版本；摘要不一致
  要求重新预览，预览不是永久授权快照。
- :func:`save_preview_manifest` / :func:`load_preview_manifest`：清单的
  JSON 持久化；缺失、损坏或版本不支持都显式失败。

可恢复备份与隔离恢复演练在 :mod:`bridges.profiles.history_backup`，二者
共用本模块的来源身份校验与只读快照读取。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from bridges.profiles.history_contracts import (
    HISTORY_PREVIEW_VERSION,
    AccountSnapshot,
    HistoryPreviewManifest,
)
from bridges.profiles.history_repository import ProfileHistorySnapshotReader
from bridges.profiles.history_snapshot import compute_account_snapshot
from bridges.profiles.replay import (
    DatabaseIdentity,
    ProfileReplayError,
    _check_integrity,
    _probe_writer_state,
    _readonly_connection,
    _schema_version,
)
from bridges.storage.database import SCHEMA_VERSION


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


def _read_snapshot(
    path: Path, account_id: str, schema_version: int
) -> AccountSnapshot:
    """在单个只读事务里读取账户快照；写入方并发提交不改变本次结果。"""
    connection = _readonly_connection(path)
    try:
        connection.execute("BEGIN")
        reader = ProfileHistorySnapshotReader(connection)
        if not reader.account_exists(account_id):
            raise ProfileHistoryError("account_missing", f"账户不存在：{account_id}")
        snapshot = compute_account_snapshot(
            connection, account_id, schema_version=schema_version
        )
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


__all__ = [
    "HistoryDriftReport",
    "ProfileHistoryError",
    "load_preview_manifest",
    "preview_history",
    "save_preview_manifest",
    "verify_preview_manifest",
]
