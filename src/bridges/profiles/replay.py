"""profile-auto-v2 安全回放的权威库、备份与队列编排。"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from pydantic import SecretStr

from bridges.config import Settings, get_settings
from bridges.contracts.profile_extraction import (
    ProfileExtractionOutcome,
    ProfileExtractionRetryTask,
    ProfileExtractionRun,
    ProfileExtractionStatus,
)
from bridges.contracts.profiles import (
    FourDimensionMigrationReport,
    FourDimensionMigrationStatus,
)
from bridges.persistence import PersistenceError, resolve_database_path
from bridges.profiles.automatic import (
    AUTOMATIC_EXTRACTOR_VERSION,
    PROFILE_REPLAY_QUEUE,
    PROFILE_REPLAY_SOURCE_HASH_PREFIX,
    AutomaticProfileExtractor,
    AutomaticProfileService,
    RuleBasedAutomaticProfileExtractor,
)
from bridges.profiles.four_dimensions import FourDimensionProfileService
from bridges.profiles.replay_repository import (
    ProfileReplayCandidate,
    ProfileReplayMessage,
    SqliteProfileReplayRepository,
)
from bridges.profiles.signals import ProfileSignalClassifier
from bridges.runtime.bootstrap import CONFIG_SCHEMA_VERSION, default_app_home
from bridges.runtime.lock import (
    LOCK_FILENAME,
    DataDirectoryLock,
    RuntimeLockError,
)
from bridges.storage.database import SCHEMA_VERSION, BridgesDatabase


class ProfileReplayError(RuntimeError):
    """回放硬门失败；code 供 CLI 和审计调用方稳定分支。"""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        self.message = message or code
        super().__init__(self.message)


@dataclass(frozen=True)
class DatabaseIdentity:
    """数据库身份快照，不包含账户或消息正文。"""

    path: Path
    normalized_path: str
    schema_version: int | None
    identity_digest: str
    size_bytes: int
    writer_state: str

    def as_dict(self) -> dict[str, object]:
        return {
            "normalized_path": self.normalized_path,
            "schema_version": self.schema_version,
            "identity_digest": self.identity_digest,
            "size_bytes": self.size_bytes,
            "writer_state": self.writer_state,
        }


@dataclass(frozen=True)
class VerifiedBackup:
    """经过 hash、schema、完整性和还原探针验证的备份。"""

    path: Path
    manifest_path: Path
    backup_id: str
    source_digest: str
    schema_version: int
    backup_digest: str
    size_bytes: int
    restore_probe_passed: bool = True

    def as_dict(self) -> dict[str, object]:
        return {
            "backup_id": self.backup_id,
            "path": str(self.path),
            "source_digest": self.source_digest,
            "schema_version": self.schema_version,
            "backup_digest": self.backup_digest,
            "size_bytes": self.size_bytes,
            "restore_probe_passed": self.restore_probe_passed,
        }


@dataclass(frozen=True)
class ProfileReplayReport:
    """只返回计数、版本和审计标识的回放报告。"""

    audit_id: str
    operation: str
    counts: dict[str, int]
    database_identity: dict[str, object]
    extractor_version: str
    classifier_version: str
    schema_version: int
    backup: dict[str, object] | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "audit_id": self.audit_id,
            "operation": self.operation,
            "versions": {
                "extractor": self.extractor_version,
                "classifier": self.classifier_version,
                "contract": "profile-extraction-v2",
            },
            "schema_version": self.schema_version,
            "database_identity": dict(self.database_identity),
            "counts": dict(self.counts),
            "backup": dict(self.backup) if self.backup is not None else None,
            "started_at": (
                self.started_at.isoformat(timespec="seconds")
                if self.started_at is not None
                else None
            ),
            "finished_at": (
                self.finished_at.isoformat(timespec="seconds")
                if self.finished_at is not None
                else None
            ),
        }


_BASE_COUNT_KEYS = (
    "eligible_exhausted_client_error_400",
    "eligible_succeeded_attempts_0",
    "eligible_total",
    "queued",
    "already_processed",
    "succeeded",
    "failed",
    "permanent_failed",
    "worker_steps",
    "skipped_missing_message",
    "skipped_stopped",
    "skipped_tombstone",
    "skipped_privacy_block",
    "skipped_withdrawn",
    "skipped_forbidden",
    "skipped_no_signal",
    "skipped_other",
)


def _new_counts() -> dict[str, int]:
    return dict.fromkeys(_BASE_COUNT_KEYS, 0)


def _now() -> datetime:
    return datetime.now(UTC)


def _now_text() -> str:
    return _now().isoformat(timespec="seconds")


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}-{digest[:32]}"


def _replay_source_hash(candidate: ProfileReplayCandidate) -> str:
    """为每条历史 v1 run 保留独立 v2 幂等边界，同时仍校验当前消息正文 hash。"""

    return (
        f"{candidate.source_hash}{PROFILE_REPLAY_SOURCE_HASH_PREFIX}"
        f"{candidate.extraction_id}"
    )


def _database_digest(path: Path) -> str:
    digest = hashlib.sha256()
    for candidate in (
        path,
        Path(f"{path}-wal"),
        Path(f"{path}-shm"),
    ):
        if not candidate.exists():
            continue
        digest.update(candidate.name.encode("utf-8"))
        digest.update(candidate.read_bytes())
    return digest.hexdigest()


def _readonly_connection(path: Path) -> sqlite3.Connection:
    uri_path = quote(str(path.resolve()).replace("\\", "/"), safe="/:")
    try:
        connection = sqlite3.connect(
            f"file:{uri_path}?mode=ro",
            uri=True,
            timeout=2.0,
        )
    except sqlite3.Error as exc:
        raise ProfileReplayError(
            "database_open_failed",
            f"无法以只读方式打开权威数据库：{path}",
        ) from exc
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only = ON")
    except sqlite3.Error as exc:
        connection.close()
        raise ProfileReplayError(
            "database_read_only_failed",
            "无法建立只读数据库会话。",
        ) from exc
    return connection


def _schema_version(connection: sqlite3.Connection) -> int | None:
    try:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = 'schema_meta'"
        ).fetchone()
        if table is None:
            return None
        row = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'version'"
        ).fetchone()
    except sqlite3.Error as exc:
        raise ProfileReplayError(
            "schema_inspection_failed",
            "无法读取数据库 schema 版本。",
        ) from exc
    if row is None:
        return None
    try:
        return int(str(row["value"]))
    except (TypeError, ValueError) as exc:
        raise ProfileReplayError(
            "schema_version_invalid",
            "数据库 schema 版本记录无效。",
        ) from exc


def _check_integrity(connection: sqlite3.Connection) -> None:
    try:
        result = connection.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.Error as exc:
        raise ProfileReplayError(
            "database_integrity_failed",
            "无法完成数据库完整性检查。",
        ) from exc
    if result is None or str(result[0]).lower() != "ok":
        detail = "unknown" if result is None else str(result[0])
        raise ProfileReplayError(
            "database_integrity_failed",
            f"数据库完整性检查失败：{detail}",
        )


def _probe_writer_state(data_dir: Path) -> str:
    """只探测已有锁，不创建或写入锁文件。"""

    lock_path = data_dir / LOCK_FILENAME
    if not lock_path.exists():
        return "inactive"
    try:
        marker = lock_path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return "unknown"
    if not marker or marker == "pid=0":
        return "inactive"
    if not marker.startswith("pid="):
        return "unknown"
    try:
        pid = int(marker.removeprefix("pid="))
    except ValueError:
        return "unknown"
    if pid <= 0:
        return "inactive"
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return "inactive"
    except PermissionError:
        return "active"
    except OSError:
        return "unknown"
    return "active"


def _inspect_database(path: Path) -> DatabaseIdentity:
    path = path.resolve()
    if not path.exists():
        raise ProfileReplayError(
            "database_missing",
            f"权威数据库不存在：{path}",
        )
    if not path.is_file():
        raise ProfileReplayError(
            "database_not_file",
            f"权威数据库路径不是文件：{path}",
        )
    connection = _readonly_connection(path)
    try:
        version = _schema_version(connection)
        _check_integrity(connection)
    finally:
        connection.close()
    return DatabaseIdentity(
        path=path,
        normalized_path=str(path),
        schema_version=version,
        identity_digest=_database_digest(path),
        size_bytes=path.stat().st_size,
        writer_state=_probe_writer_state(path.parent),
    )


def _path_from_value(value: object, base_dir: Path) -> Path | None:
    if isinstance(value, SecretStr):
        value = value.get_secret_value()
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == ":memory:":
        return None
    path = Path(text)
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def _state_database_values(state: object) -> list[object]:
    if state is None:
        return []
    if isinstance(state, Mapping):
        values: list[object] = []
        for key in ("database_path", "database_url", "path"):
            if key in state:
                values.append(state[key])
        database_value = state.get("database")
        if database_value is not None and not isinstance(database_value, Mapping):
            values.append(database_value)
        for key in ("runtime", "database", "storage"):
            nested = state.get(key)
            if isinstance(nested, Mapping):
                values.extend(_state_database_values(nested))
        return values
    values = []
    for key in ("database_path", "database_url", "path"):
        value = getattr(state, key, None)
        if value is not None:
            values.append(value)
    return values


def load_runtime_application_state() -> Mapping[str, object] | None:
    """只读加载本机托管的统一配置，缺失时不猜测数据库位置。"""

    configured_home = os.environ.get("BRIDGES_HOME", "").strip()
    app_home = (
        Path(configured_home).expanduser().resolve()
        if configured_home
        else default_app_home()
    )
    config_path = app_home / "config.json"
    if not config_path.exists():
        return None
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProfileReplayError(
            "runtime_config_invalid",
            f"无法读取统一运行时配置：{config_path}",
        ) from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != CONFIG_SCHEMA_VERSION:
        raise ProfileReplayError(
            "runtime_config_invalid",
            f"统一运行时配置版本不受支持：{config_path}",
        )
    return payload


def resolve_authoritative_database(
    *,
    settings: Settings | None = None,
    explicit_path: str | Path | None = None,
    application_state: object | None = None,
    candidates: Sequence[str | Path] = (),
    base_dir: Path | None = None,
) -> DatabaseIdentity:
    """从配置和应用状态解析唯一数据库；不提供 bridges.db 默认回退。"""

    settings_was_supplied = settings is not None
    settings = settings or get_settings()
    if application_state is None and not settings_was_supplied:
        application_state = load_runtime_application_state()
    root = (base_dir or Path.cwd()).resolve()
    configured: list[Path] = []

    database_url = settings.database_url
    database_url_text = (
        database_url.get_secret_value()
        if isinstance(database_url, SecretStr)
        else str(database_url or "")
    )
    if database_url_text.strip():
        try:
            configured_path = _path_from_value(
                resolve_database_path(database_url_text),
                root,
            )
        except PersistenceError as exc:
            raise ProfileReplayError(
                "database_configuration_invalid",
                "BRIDGES_DATABASE_URL 不是受支持的 SQLite 配置。",
            ) from exc
        if configured_path is not None:
            configured.append(configured_path)

    for value in _state_database_values(application_state):
        if isinstance(value, SecretStr) or (
            isinstance(value, str) and "://" in value
        ):
            try:
                value = resolve_database_path(value)
            except PersistenceError as exc:
                raise ProfileReplayError(
                    "database_configuration_invalid",
                    "应用状态中的数据库配置无效。",
                ) from exc
        path = _path_from_value(value, root)
        if path is not None:
            configured.append(path)

    if not configured:
        raise ProfileReplayError(
            "database_configuration_missing",
            "未找到运行时权威数据库配置；不会回退到仓库根目录的 bridges.db。",
        )

    all_candidates = list(configured)
    explicit = _path_from_value(explicit_path, root)
    if explicit is not None:
        all_candidates.append(explicit)
    all_candidates.extend(
        path
        for value in candidates
        if (path := _path_from_value(value, root)) is not None
    )
    unique = {str(path.resolve()): path.resolve() for path in all_candidates}
    if len(unique) != 1:
        raise ProfileReplayError(
            "database_path_conflict",
            "运行时配置、应用状态和显式候选路径指向不同数据库。",
        )
    return _inspect_database(next(iter(unique.values())))


def _require_current_schema(identity: DatabaseIdentity) -> DatabaseIdentity:
    current = _inspect_database(identity.path)
    if current.writer_state == "active":
        raise ProfileReplayError(
            "writer_active",
            "数据库仍有活动写入者，已停止回放操作。",
        )
    if current.writer_state == "unknown":
        raise ProfileReplayError(
            "writer_state_unknown",
            "无法确认数据库写入者状态，已停止回放操作。",
        )
    if current.schema_version is None or current.schema_version < SCHEMA_VERSION:
        raise ProfileReplayError(
            "schema_upgrade_required",
            f"数据库 schema 为 {current.schema_version}，请先执行显式 schema upgrade。",
        )
    if current.schema_version > SCHEMA_VERSION:
        raise ProfileReplayError(
            "schema_version_unsupported",
            f"数据库 schema 为 {current.schema_version}，高于当前程序支持的 {SCHEMA_VERSION}。",
        )
    return current


def _verify_backup_file(path: Path) -> tuple[int, str]:
    if not path.exists() or not path.is_file():
        raise ProfileReplayError(
            "backup_unverified",
            f"验证备份不存在：{path}",
        )
    connection = _readonly_connection(path)
    try:
        version = _schema_version(connection)
        _check_integrity(connection)
    finally:
        connection.close()
    if version != SCHEMA_VERSION:
        raise ProfileReplayError(
            "backup_schema_invalid",
            f"备份 schema 为 {version}，不是当前支持的 {SCHEMA_VERSION}。",
        )
    return version, _database_digest(path)


def _restore_probe(path: Path) -> bool:
    try:
        with tempfile.TemporaryDirectory(prefix="profile-replay-probe-") as temp_dir:
            probe = Path(temp_dir) / "restore.db"
            shutil.copy2(path, probe)
            _verify_backup_file(probe)
    except (OSError, ProfileReplayError):
        return False
    return True


def _manifest_path(path: Path) -> Path:
    return Path(f"{path}.manifest.json")


def _load_verified_backup(backup: VerifiedBackup | str | Path) -> VerifiedBackup:
    if isinstance(backup, VerifiedBackup):
        version, digest = _verify_backup_file(backup.path)
        if (
            version != backup.schema_version
            or digest != backup.backup_digest
            or not backup.restore_probe_passed
            or not _restore_probe(backup.path)
        ):
            raise ProfileReplayError(
                "backup_unverified",
                "备份 hash、schema 或还原探针校验失败。",
            )
        return backup

    path = Path(backup).resolve()
    manifest_path = _manifest_path(path)
    if not manifest_path.exists():
        raise ProfileReplayError(
            "backup_unverified",
            "备份缺少不可变 manifest，不能执行正式回放。",
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        backup_id = str(manifest["backup_id"])
        source_digest = str(manifest["source_digest"])
        schema_version = int(manifest["schema_version"])
        expected_digest = str(manifest["backup_digest"])
        expected_size = int(manifest["size_bytes"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ProfileReplayError(
            "backup_unverified",
            "备份 manifest 无法读取或字段不完整。",
        ) from exc
    version, digest = _verify_backup_file(path)
    if (
        version != schema_version
        or digest != expected_digest
        or path.stat().st_size != expected_size
        or not _restore_probe(path)
    ):
        raise ProfileReplayError(
            "backup_unverified",
            "备份 manifest 与实际文件不一致，或还原探针失败。",
        )
    return VerifiedBackup(
        path=path,
        manifest_path=manifest_path,
        backup_id=backup_id,
        source_digest=source_digest,
        schema_version=schema_version,
        backup_digest=digest,
        size_bytes=path.stat().st_size,
    )


def create_verified_backup(
    authority: DatabaseIdentity,
    target_path: str | Path,
) -> VerifiedBackup:
    """在维护锁内创建 SQLite 在线备份，并写入可验证 manifest。"""

    current = _require_current_schema(authority)
    target = Path(target_path).resolve()
    if target == current.path:
        raise ProfileReplayError(
            "backup_target_conflict",
            "备份目标不能覆盖权威数据库。",
        )
    if not target.parent.exists():
        raise ProfileReplayError(
            "backup_target_missing",
            "备份目标目录不存在；请先显式创建目标目录。",
        )
    manifest_path = _manifest_path(target)
    if target.exists() or manifest_path.exists():
        raise ProfileReplayError(
            "backup_target_exists",
            "备份目标或 manifest 已存在，不会覆盖已有备份。",
        )

    lock = DataDirectoryLock(current.path.parent)
    try:
        with lock:
            source_digest = _database_digest(current.path)
            source = _readonly_connection(current.path)
            destination: sqlite3.Connection | None = None
            try:
                destination = sqlite3.connect(str(target), timeout=10.0)
                source.backup(destination)
                destination.commit()
            except sqlite3.Error as exc:
                raise ProfileReplayError(
                    "backup_failed",
                    "SQLite 在线备份失败。",
                ) from exc
            finally:
                source.close()
                if destination is not None:
                    destination.close()

            version, backup_digest = _verify_backup_file(target)
            if not _restore_probe(target):
                raise ProfileReplayError(
                    "backup_restore_probe_failed",
                    "备份还原探针失败，不会进入正式回放。",
                )
            backup_id = _stable_id(
                "profile-replay-backup",
                source_digest,
                backup_digest,
                str(target),
            )
            manifest = {
                "backup_id": backup_id,
                "source_digest": source_digest,
                "schema_version": version,
                "backup_digest": backup_digest,
                "size_bytes": target.stat().st_size,
                "created_at": _now_text(),
            }
            try:
                manifest_path.write_text(
                    json.dumps(manifest, ensure_ascii=False, sort_keys=True),
                    encoding="utf-8",
                )
            except OSError as exc:
                target.unlink(missing_ok=True)
                raise ProfileReplayError(
                    "backup_manifest_failed",
                    "无法写入备份 manifest。",
                ) from exc
    except RuntimeLockError as exc:
        raise ProfileReplayError(
            "writer_active",
            "无法取得维护锁，数据库可能仍有活动写入者。",
        ) from exc
    except ProfileReplayError:
        target.unlink(missing_ok=True)
        manifest_path.unlink(missing_ok=True)
        raise

    return VerifiedBackup(
        path=target,
        manifest_path=manifest_path,
        backup_id=backup_id,
        source_digest=source_digest,
        schema_version=version,
        backup_digest=backup_digest,
        size_bytes=target.stat().st_size,
    )


def upgrade_authoritative_schema(authority: DatabaseIdentity) -> DatabaseIdentity:
    """唯一允许调用隐式迁移的显式 schema-upgrade 操作。"""

    current = _inspect_database(authority.path)
    if current.writer_state != "inactive":
        raise ProfileReplayError(
            "writer_unavailable",
            "无法确认数据库没有其他写入者。",
        )
    lock = DataDirectoryLock(current.path.parent)
    try:
        with lock:
            database = BridgesDatabase(current.path)
            try:
                database.initialize()
            finally:
                database.close()
    except RuntimeLockError as exc:
        raise ProfileReplayError(
            "writer_active",
            "无法取得 schema upgrade 维护锁。",
        ) from exc
    return _require_current_schema(current)


def _skipped_count_key(reason: str) -> str:
    return f"skipped_{reason}"


class ProfileReplayCoordinator:
    """执行 Issue 08 的 dry-run、正式回放和受监督 worker。"""

    def __init__(self, authority: DatabaseIdentity) -> None:
        self.authority = authority
        self._classifier = ProfileSignalClassifier()
        self._extractor_version = (
            f"{AUTOMATIC_EXTRACTOR_VERSION}+signal-{self._classifier.version}"
        )

    def _report(
        self,
        *,
        audit_id: str,
        operation: str,
        counts: dict[str, int],
        identity: DatabaseIdentity,
        backup: VerifiedBackup | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
    ) -> ProfileReplayReport:
        return ProfileReplayReport(
            audit_id=audit_id,
            operation=operation,
            counts=counts,
            database_identity=identity.as_dict(),
            extractor_version=self._extractor_version,
            classifier_version=self._classifier.version,
            schema_version=SCHEMA_VERSION,
            backup=backup.as_dict() if backup is not None else None,
            started_at=started_at or _now(),
            finished_at=finished_at or _now(),
        )

    def dry_run(self) -> ProfileReplayReport:
        """严格只读地统计两类 v1 候选及所有安全跳过原因。"""

        started_at = _now()
        identity = _require_current_schema(self.authority)
        counts = _new_counts()
        connection = _readonly_connection(identity.path)
        try:
            repository = SqliteProfileReplayRepository(connection=connection)
            candidates = repository.list_candidates()
            for candidate in candidates:
                counts[candidate.bucket] += 1
                counts["eligible_total"] += 1
                reason = repository.gate(candidate, self._classifier)
                if reason != "eligible":
                    counts[_skipped_count_key(reason)] += 1
        finally:
            connection.close()
        finished_at = _now()
        return self._report(
            audit_id=f"profile-replay-dry-run-{uuid.uuid4().hex}",
            operation="dry-run",
            counts=counts,
            identity=identity,
            started_at=started_at,
            finished_at=finished_at,
        )

    @staticmethod
    def _has_backup_audit(path: Path, backup_id: str) -> bool:
        connection = _readonly_connection(path)
        try:
            return SqliteProfileReplayRepository(
                connection=connection
            ).has_backup_audit(backup_id)
        finally:
            connection.close()

    def _build_service(
        self,
        *,
        replay_repository: SqliteProfileReplayRepository,
        extractor: AutomaticProfileExtractor | None,
    ) -> AutomaticProfileService:
        actual_extractor = extractor or RuleBasedAutomaticProfileExtractor(
            self._classifier
        )
        if actual_extractor.version != AUTOMATIC_EXTRACTOR_VERSION:
            raise ProfileReplayError(
                "extractor_version_invalid",
                "正式回放必须使用 profile-auto-v2 抽取器。",
            )
        automatic_repository = replay_repository.automatic_repository
        dimension_repository = replay_repository.dimension_repository
        dimensions = FourDimensionProfileService(
            source_repository=None,  # type: ignore[arg-type]
            repository=dimension_repository,
        )
        dimensions.set_observation_delete_callback(
            automatic_repository.delete_observations_for_record
        )

        def read_message(
            account_id: str, message_id: str
        ) -> ProfileReplayMessage | None:
            return replay_repository.get_message(account_id, message_id)

        return AutomaticProfileService(
            four_dimension_service=dimensions,
            repository=automatic_repository,
            extractor=actual_extractor,
            message_reader=read_message,
            classifier=self._classifier,
            queue_name=PROFILE_REPLAY_QUEUE,
        )

    def _seed_candidate(
        self,
        candidate: ProfileReplayCandidate,
        *,
        replay_repository: SqliteProfileReplayRepository,
        counts: dict[str, int],
        account_counts: dict[str, dict[str, int]],
        created_run_ids: dict[str, list[str]],
        source_extraction_ids: dict[str, list[str]],
    ) -> None:
        source_extraction_ids[candidate.account_id].append(candidate.extraction_id)
        account_counts[candidate.account_id]["seen"] += 1
        counts[candidate.bucket] += 1
        counts["eligible_total"] += 1
        reason = replay_repository.gate(candidate, self._classifier)
        if reason != "eligible":
            if reason == "missing_message":
                account_counts[candidate.account_id]["skipped"] += 1
                counts["skipped_missing_message"] += 1
                return
            counts[_skipped_count_key(reason)] += 1
            account_counts[candidate.account_id]["skipped"] += 1

        replay_source_hash = _replay_source_hash(candidate)
        extraction_id = _stable_id(
            "profile-replay-run",
            candidate.account_id,
            candidate.message_id,
            self._extractor_version,
            replay_source_hash,
        )
        task_id = _stable_id(
            "profile-replay-task",
            candidate.account_id,
            candidate.message_id,
            self._extractor_version,
            replay_source_hash,
        )
        existing_run = replay_repository.get_run(
            candidate.account_id,
            candidate.message_id,
            self._extractor_version,
            replay_source_hash,
        )
        existing_task = replay_repository.get_task(
            candidate.account_id,
            candidate.message_id,
            self._extractor_version,
            replay_source_hash,
        )
        if existing_run is not None and existing_run.status in {
            ProfileExtractionStatus.SUCCEEDED,
            ProfileExtractionStatus.EXHAUSTED,
        }:
            counts["already_processed"] += 1
            return
        if existing_run is not None and existing_task is not None:
            counts["already_processed"] += 1
            return

        now = _now()
        if reason != "eligible":
            status = (
                ProfileExtractionStatus.SUCCEEDED
                if reason == "no_signal"
                else ProfileExtractionStatus.EXHAUSTED
            )
            outcome = (
                ProfileExtractionOutcome.NO_SIGNAL
                if reason == "no_signal"
                else ProfileExtractionOutcome.PERMANENT_FAILURE
            )
            run = ProfileExtractionRun(
                extraction_id=extraction_id,
                account_id=candidate.account_id,
                message_id=candidate.message_id,
                extractor_version=self._extractor_version,
                source_hash=replay_source_hash,
                source_snapshot="",
                status=status,
                outcome=outcome,
                attempts=0,
                last_error=f"profile_replay_{reason}",
                created_at=now,
                updated_at=now,
            )
            replay_repository.save_run(run)
            return

        run = ProfileExtractionRun(
            extraction_id=extraction_id,
            account_id=candidate.account_id,
            message_id=candidate.message_id,
            extractor_version=self._extractor_version,
            source_hash=replay_source_hash,
            source_snapshot=candidate.content[:4000],
            status=ProfileExtractionStatus.PENDING,
            outcome=ProfileExtractionOutcome.PENDING_RETRY,
            attempts=0,
            created_at=now,
            updated_at=now,
        )
        task = ProfileExtractionRetryTask(
            task_id=task_id,
            account_id=candidate.account_id,
            message_id=candidate.message_id,
            extractor_version=self._extractor_version,
            source_hash=replay_source_hash,
            status=ProfileExtractionStatus.PENDING,
            attempts=0,
            created_at=now,
            updated_at=now,
        )
        replay_repository.save_run(run)
        replay_repository.save_task(task)
        replay_repository.enqueue(
            task.task_id,
            payload={
                "account_id": candidate.account_id,
                "message_id": candidate.message_id,
                "extractor_version": self._extractor_version,
                "source_hash": replay_source_hash,
            },
        )
        created_run_ids[candidate.account_id].append(extraction_id)
        counts["queued"] += 1

    @staticmethod
    def _queue_has_runnable(
        replay_repository: SqliteProfileReplayRepository,
    ) -> bool:
        return replay_repository.queue_has_runnable()

    @staticmethod
    def _assert_converged(
        replay_repository: SqliteProfileReplayRepository,
    ) -> None:
        if replay_repository.pending_v2_tasks() or replay_repository.queue_has_runnable():
            raise ProfileReplayError(
                "worker_not_converged",
                "profile-replay-v2 worker 结束时仍有 pending/running 任务。",
            )

    def _drain(
        self,
        replay_repository: SqliteProfileReplayRepository,
        service: AutomaticProfileService,
        *,
        max_steps: int = 100,
    ) -> int:
        steps = 0
        while self._queue_has_runnable(replay_repository):
            if steps >= max_steps:
                raise ProfileReplayError(
                    "worker_limit",
                    f"回放 worker 超过单次上限 {max_steps}。",
                )
            service.run_retry_tick()
            steps += 1
        return steps

    def _write_account_reports(
        self,
        replay_repository: SqliteProfileReplayRepository,
        *,
        audit_id: str,
        backup: VerifiedBackup,
        account_counts: dict[str, dict[str, int]],
        source_extraction_ids: dict[str, list[str]],
        created_run_ids: dict[str, list[str]],
        completed: bool,
    ) -> None:
        migration_version = (
            f"profile-auto-v2-replay:backup={backup.backup_id}:audit={audit_id}"
        )
        extraction_ids = [
            extraction_id
            for run_ids in created_run_ids.values()
            for extraction_id in run_ids
        ]
        run_statuses = {
            status.extraction_id: status
            for status in replay_repository.run_statuses(extraction_ids)
        }
        for account_id, values in account_counts.items():
            stable_record_ids: list[str] = []
            failed = 0
            for extraction_id in created_run_ids.get(account_id, []):
                status = run_statuses.get(extraction_id)
                if status is None:
                    continue
                if status.status == "succeeded":
                    stable_record_ids.extend(status.record_ids)
                elif status.status == "exhausted":
                    failed += 1
            source_links = [
                f"profile_replay_source:{source_id}"
                for source_id in sorted(set(source_extraction_ids.get(account_id, [])))
            ]
            failure_codes = source_links
            if failed:
                failure_codes = [*failure_codes, "profile_replay_worker_failed"]
            report = FourDimensionMigrationReport(
                report_id=_stable_id("profile-replay-audit", audit_id, account_id),
                owner_account_id=account_id,
                migration_version=migration_version,
                status=(
                    FourDimensionMigrationStatus.COMPLETED
                    if completed
                    else FourDimensionMigrationStatus.RETRYABLE
                ),
                four_dimension_migrated=len(set(stable_record_ids)),
                teaching_records_migrated=0,
                legacy_preserved=0,
                skipped=values["skipped"],
                failed=failed,
                stable_record_ids=sorted(set(stable_record_ids)),
                failure_codes=failure_codes,
                retryable=not completed,
                created_at=_now(),
            )
            replay_repository.save_migration_report(report)


    def replay(
        self,
        *,
        backup: VerifiedBackup | str | Path,
        confirmation: str,
        dry_run_report: ProfileReplayReport | None = None,
        extractor: AutomaticProfileExtractor | None = None,
        drain: bool = False,
        max_steps: int = 100,
    ) -> ProfileReplayReport:
        """用已验证备份执行幂等 v2 入队，并可选择运行受监督 worker。"""

        if confirmation != "回放":
            raise ProfileReplayError(
                "confirmation_required",
                "正式回放必须显式输入确认词：回放。",
            )
        if dry_run_report is None or dry_run_report.operation != "dry-run":
            raise ProfileReplayError(
                "dry_run_required",
                "正式回放前必须先完成同一权威数据库的 dry-run。",
            )
        started_at = _now()
        verified = _load_verified_backup(backup)
        if verified.schema_version != SCHEMA_VERSION:
            raise ProfileReplayError(
                "backup_schema_invalid",
                "正式回放的备份 schema 不满足当前版本。",
            )
        current = _require_current_schema(self.authority)
        if (
            dry_run_report.database_identity.get("identity_digest")
            != current.identity_digest
        ):
            raise ProfileReplayError(
                "dry_run_stale",
                "dry-run 与当前权威数据库身份不一致，已停止正式回放。",
            )
        if (
            current.identity_digest != verified.source_digest
            and not self._has_backup_audit(current.path, verified.backup_id)
        ):
            raise ProfileReplayError(
                "backup_source_mismatch",
                "权威数据库已偏离备份来源，且没有该备份的既有回放审计。",
            )

        audit_id = f"profile-replay-{uuid.uuid4().hex}"
        counts = _new_counts()
        account_counts: defaultdict[str, dict[str, int]] = defaultdict(
            lambda: {"seen": 0, "skipped": 0}
        )
        created_run_ids: defaultdict[str, list[str]] = defaultdict(list)
        source_extraction_ids: defaultdict[str, list[str]] = defaultdict(list)
        lock = DataDirectoryLock(current.path.parent)
        try:
            with lock:
                database = BridgesDatabase(current.path)
                try:
                    replay_repository = SqliteProfileReplayRepository(database)
                    with database.transaction():
                        candidates = replay_repository.list_candidates()
                        for candidate in candidates:
                            self._seed_candidate(
                                candidate,
                                replay_repository=replay_repository,
                                counts=counts,
                                account_counts=account_counts,
                                created_run_ids=created_run_ids,
                                source_extraction_ids=source_extraction_ids,
                            )
                    service = self._build_service(
                        replay_repository=replay_repository,
                        extractor=extractor,
                    )
                    if drain:
                        counts["worker_steps"] = self._drain(
                            replay_repository,
                            service,
                            max_steps=max_steps,
                        )
                        self._assert_converged(replay_repository)
                    with database.transaction():
                        self._write_account_reports(
                            replay_repository,
                            audit_id=audit_id,
                            backup=verified,
                            account_counts=account_counts,
                            source_extraction_ids=source_extraction_ids,
                            created_run_ids=created_run_ids,
                            completed=drain,
                        )
                    if created_run_ids:
                        extraction_ids = [
                            extraction_id
                            for run_ids in created_run_ids.values()
                            for extraction_id in run_ids
                        ]
                        statuses = replay_repository.run_statuses(extraction_ids)
                        counts["succeeded"] = sum(
                            1 for status in statuses if status.status == "succeeded"
                        )
                        counts["failed"] = sum(
                            1 for status in statuses if status.status == "exhausted"
                        )
                        counts["permanent_failed"] = counts["failed"]
                finally:
                    database.close()
        except RuntimeLockError as exc:
            raise ProfileReplayError(
                "writer_active",
                "无法取得正式回放维护锁。",
            ) from exc

        return self._report(
            audit_id=audit_id,
            operation="replay",
            counts=counts,
            identity=_inspect_database(current.path),
            backup=verified,
            started_at=started_at,
            finished_at=_now(),
        )

    def worker(
        self,
        *,
        extractor: AutomaticProfileExtractor | None = None,
        max_steps: int = 100,
    ) -> ProfileReplayReport:
        """只处理 profile-replay-v2 队列，不触碰旧 v1 队列。"""

        started_at = _now()
        current = _require_current_schema(self.authority)
        counts = _new_counts()
        audit_id = f"profile-replay-worker-{uuid.uuid4().hex}"
        lock = DataDirectoryLock(current.path.parent)
        try:
            with lock:
                database = BridgesDatabase(current.path)
                try:
                    replay_repository = SqliteProfileReplayRepository(database)
                    service = self._build_service(
                        replay_repository=replay_repository,
                        extractor=extractor,
                    )
                    counts["worker_steps"] = self._drain(
                        replay_repository,
                        service,
                        max_steps=max_steps,
                    )
                    self._assert_converged(replay_repository)
                    counts.update(replay_repository.v2_run_status_counts())
                finally:
                    database.close()
        except RuntimeLockError as exc:
            raise ProfileReplayError(
                "writer_active",
                "无法取得 profile-replay-v2 worker 维护锁。",
            ) from exc
        return self._report(
            audit_id=audit_id,
            operation="worker",
            counts=counts,
            identity=_inspect_database(current.path),
            started_at=started_at,
            finished_at=_now(),
        )


__all__ = [
    "DatabaseIdentity",
    "ProfileReplayCoordinator",
    "ProfileReplayError",
    "ProfileReplayReport",
    "VerifiedBackup",
    "create_verified_backup",
    "load_runtime_application_state",
    "resolve_authoritative_database",
    "upgrade_authoritative_schema",
]
