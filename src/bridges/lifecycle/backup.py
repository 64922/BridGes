"""本地加密一致备份与恢复（Issue 37，AC5-8）。

备份 = 自定义加密容器（三种部署载体共用同一格式与恢复语义）：

    BRIDGESBACKUP1\n
    <manifest 明文 JSON 行>\n
    <payload：口令派生 Fernet 加密的 zip 字节>

payload zip 在受控一致性点打包：bridges.db 一致快照（含 FTS/向量索引，
``BridgesDatabase.snapshot_to`` 在快照锁内执行）、账户隔离对象目录的全部
加密文件、身份账户数据（含密码哈希，恢复后可登录）。百炼 Key、SMTP
授权码、会话令牌、恢复令牌、设备绑定、运行密钥与审计绝不进入备份。

恢复 = 预检（格式版本、payload 完整性、口令、逐文件摘要、可用空间、
目标状态）→ staging 解包 → 原子替换（旧文件先改名保留）→ 失败回滚；
成功后清除全部账户凭据（外部凭据待重新配置）、替换身份账户数据
（全部会话失效）、调度索引重建并输出可操作结果。
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
import os
import secrets
import shutil
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from bridges.contracts.lifecycle import (
    BackupFileEntry,
    BackupManifest,
    DataLifecycleError,
    RestorePreview,
)
from bridges.contracts.observability import AuditAction, AuditResult
from bridges.credentials.store import CredentialStorePort
from bridges.identity.service import IdentityService
from bridges.lifecycle.catalog import global_stats
from bridges.observability.service import ObservabilityService
from bridges.persistence import SqliteStateStore
from bridges.retirement import run_reminder_retirement
from bridges.storage.database import BridgesDatabase
from bridges.storage.errors import StorageError
from bridges.storage.object_store import EncryptedFileObjectStore
from bridges.storage.repository import BridgesObjectRepository

#: 备份容器魔数与格式版本（增量演进：低版本程序拒绝高版本备份）。
BACKUP_MAGIC = b"BRIDGESBACKUP1\n"
BACKUP_FORMAT_VERSION = 1

#: 口令派生参数（PBKDF2-HMAC-SHA256，与 object_store 同族但独立盐）。
KDF_ITERATIONS = 200_000

#: 恢复时保留旧数据所需的额外空间比例（旧数据改名保留用于回滚）。
_RECOVERY_RESERVE_FACTOR = 1.2

#: 审计 details 白名单（不含秘密与正文）。
_AUDIT_DETAIL_KEYS = frozenset(
    {"format_version", "schema_version", "account_count", "payload_size", "error"}
)

#: 备份包内固定条目名（解包白名单）。
DB_ENTRY = "bridges.db"
IDENTITY_ENTRY = "identity.json"
OBJECTS_PREFIX = "objects/"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _fernet_for(passphrase: str, salt: bytes) -> Fernet:
    """由口令与盐派生 Fernet 密钥（PBKDF2-HMAC-SHA256 → url-safe base64）。"""
    raw = hashlib.pbkdf2_hmac(
        "sha256", passphrase.encode("utf-8"), salt, KDF_ITERATIONS
    )
    return Fernet(base64.urlsafe_b64encode(raw))


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _split_container(backup_bytes: bytes) -> tuple[bytes, bytes]:
    """分离容器：([manifest 明文行], [payload 字节])。"""
    remainder = backup_bytes[len(BACKUP_MAGIC):]
    manifest_line, separator, payload = remainder.partition(b"\n")
    if not separator:
        raise DataLifecycleError(
            "backup_invalid_format", "备份清单缺失，文件已损坏。", 400
        )
    return manifest_line, payload


class BackupService:
    """本地加密备份创建与恢复（快照一致性点 + 预检 + 原子回滚）。"""

    def __init__(
        self,
        database: BridgesDatabase,
        object_repository: BridgesObjectRepository,
        object_store: EncryptedFileObjectStore,
        identity_service: IdentityService,
        smtp_credential_store: CredentialStorePort,
        observability_service: ObservabilityService,
        state_store: SqliteStateStore | None = None,
    ) -> None:
        self._database = database
        self._objects = object_repository
        self._object_store = object_store
        self._identity = identity_service
        self._smtp_credentials = smtp_credential_store
        self._observability = observability_service
        # 状态存储与 bridges.db 共用同一 SQLite 文件（多连接）：恢复的原子
        # 替换需要先关闭其连接，替换后再重开，否则 Windows 上文件被占用。
        self._state_store = state_store

    # ------------------------------------------------------------------
    # 创建备份
    # ------------------------------------------------------------------

    def create_backup(self, passphrase: str) -> tuple[str, bytes]:
        """在受控一致性点创建加密备份，返回 (文件名, 备份字节)。

        备份不包含任何凭据、会话令牌或运行密钥：对象文件是既有加密
        密文（跨环境恢复需要同一运行密钥重建对象），身份只含账户数据。
        口令只用于派生本包的独立加密密钥。
        """
        if not passphrase:
            raise DataLifecycleError(
                "backup_passphrase_required", "备份口令不能为空。", 422
            )
        try:
            with tempfile.TemporaryDirectory() as staging:
                staging_path = Path(staging)
                # 一致性点：数据库快照与对象文件复制都在数据库锁内完成；
                # 对象以内容哈希命名且不可变，快照点前后新增文件无害。
                with self._database.snapshot_lock():
                    self._database.snapshot_to(staging_path / DB_ENTRY)
                    object_files = self._collect_object_files(staging_path)
                identity_data = self._identity.export_accounts_for_backup()
                payload_zip = self._build_payload_zip(
                    staging_path, object_files, identity_data
                )
                salt = secrets.token_bytes(16)
                payload = _fernet_for(passphrase, salt).encrypt(payload_zip)
                manifest = self._build_manifest(
                    salt, payload_zip, payload, object_files
                )
                container = self._assemble_container(manifest, payload)
        except (StorageError, OSError, ValueError) as exc:
            raise DataLifecycleError(
                "backup_create_failed", f"备份创建失败：{exc}", 502, retryable=True
            ) from exc
        self._observability.log_audit(
            actor_account_id="",
            action=AuditAction.BACKUP_CREATE,
            result=AuditResult.SUCCESS,
            details={
                "format_version": manifest.format_version,
                "schema_version": manifest.schema_version,
                "account_count": manifest.account_count,
                "payload_size": manifest.payload_size,
            },
        )
        filename = (
            "bridges-backup-"
            f"{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.bridgesbackup"
        )
        return filename, container

    def _collect_object_files(self, staging: Path) -> list[tuple[str, Path]]:
        """复制对象目录全部加密文件到 staging，返回 [(条目名, staging 路径)]。"""
        entries: list[tuple[str, Path]] = []
        for content_hash in sorted(self._object_store.list_files()):
            source = self._object_store.objects_dir / content_hash[:2] / content_hash
            target = staging / OBJECTS_PREFIX / content_hash[:2] / content_hash
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            entries.append((f"{OBJECTS_PREFIX}{content_hash[:2]}/{content_hash}", target))
        return entries

    def _build_payload_zip(
        self,
        staging: Path,
        object_files: list[tuple[str, Path]],
        identity_data: dict[str, Any],
    ) -> bytes:
        """构建载荷 zip（bridges.db 快照 + 对象 + 身份账户数据）。"""
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(staging / DB_ENTRY, DB_ENTRY)
            for entry_name, path in object_files:
                archive.write(path, entry_name)
            archive.writestr(
                IDENTITY_ENTRY,
                json.dumps(identity_data, ensure_ascii=False).encode("utf-8"),
            )
        return buffer.getvalue()

    def _build_manifest(
        self,
        salt: bytes,
        payload_zip: bytes,
        payload: bytes,
        object_files: list[tuple[str, Path]],
    ) -> BackupManifest:
        """构建明文清单：加密参数、payload 完整性摘要与文件清单。

        ``payload_zip`` 是加密前的 zip 字节（逐文件摘要），``payload``
        是加密后的容器载荷（整体摘要）；两者都进入清单供恢复双校验。
        """
        with zipfile.ZipFile(io.BytesIO(payload_zip)) as archive:
            files: list[BackupFileEntry] = []
            for info in archive.infolist():
                if info.is_dir():
                    continue
                content = archive.read(info.filename)
                files.append(
                    BackupFileEntry(
                        name=info.filename,
                        sha256=_sha256(content),
                        size=len(content),
                    )
                )
        stats = global_stats(self._database)
        return BackupManifest(
            format_version=BACKUP_FORMAT_VERSION,
            created_at=datetime.now(UTC),
            kdf_iterations=KDF_ITERATIONS,
            salt=salt.hex(),
            payload_sha256=_sha256(payload),
            payload_size=len(payload),
            files=files,
            schema_version=self._database.initialize(),
            account_count=stats.get("accounts", 0),
            stats=stats,
        )

    def _assemble_container(self, manifest: BackupManifest, payload: bytes) -> bytes:
        manifest_line = json.dumps(
            manifest.model_dump(mode="json"), ensure_ascii=False
        ).encode("utf-8")
        return BACKUP_MAGIC + manifest_line + b"\n" + payload

    # ------------------------------------------------------------------
    # 恢复
    # ------------------------------------------------------------------

    def restore_backup(
        self,
        passphrase: str,
        backup_bytes: bytes,
        *,
        confirmation: str,
    ) -> RestorePreview:
        """恢复备份：预检通过后原子替换，失败回滚到恢复前状态。

        返回预检结果；任何预检失败（损坏/篡改/口令错误/空间不足/版本
        不兼容/确认缺失）都在替换前拒绝，绝不破坏现有数据。
        """
        manifest = self._parse_container(backup_bytes)
        preview = self._validate_restore(passphrase, manifest, backup_bytes, confirmation)
        if not preview.ok:
            self._audit_restore(preview, result=AuditResult.BLOCKED)
            return preview
        try:
            self._execute_restore(passphrase, manifest, backup_bytes)
        except DataLifecycleError:
            self._audit_restore(preview, result=AuditResult.RETRYABLE_FAIL)
            raise
        preview.reasons = []
        self._audit_restore(preview, result=AuditResult.SUCCESS)
        return preview

    def _parse_container(self, backup_bytes: bytes) -> BackupManifest:
        """解析容器头与明文清单；魔数/JSON 损坏立即拒绝。"""
        if not backup_bytes.startswith(BACKUP_MAGIC):
            raise DataLifecycleError(
                "backup_invalid_format",
                "文件不是有效的 BridGes 备份（格式头不匹配）。",
                400,
            )
        manifest_line, _ = _split_container(backup_bytes)
        try:
            return BackupManifest.model_validate(json.loads(manifest_line))
        except (ValueError, TypeError) as exc:
            raise DataLifecycleError(
                "backup_invalid_format", "备份清单无法解析，文件已损坏。", 400
            ) from exc

    def _validate_restore(
        self,
        passphrase: str,
        manifest: BackupManifest,
        backup_bytes: bytes,
        confirmation: str,
    ) -> RestorePreview:
        """恢复预检：版本/完整性/口令/空间/目标状态（替换前全部完成）。"""
        preview = RestorePreview(
            ok=True,
            format_version=manifest.format_version,
            schema_version=manifest.schema_version,
            created_at=manifest.created_at,
            account_count=manifest.account_count,
            stats=manifest.stats,
            payload_size=manifest.payload_size,
            requires_space=0,
            reasons=[],
        )
        if manifest.format_version != BACKUP_FORMAT_VERSION:
            preview.ok = False
            preview.reasons.append(
                f"备份格式版本（{manifest.format_version}）与当前程序"
                f"（{BACKUP_FORMAT_VERSION}）不兼容。"
            )
        current_schema = self._database.initialize()
        if manifest.schema_version > current_schema:
            preview.ok = False
            preview.reasons.append(
                f"备份来自更高版本程序（数据库模式 {manifest.schema_version} >"
                f" 当前 {current_schema}），请先升级程序再恢复。"
            )
        _, payload = _split_container(backup_bytes)
        if _sha256(payload) != manifest.payload_sha256:
            preview.ok = False
            preview.reasons.append("备份完整性校验失败：载荷摘要与清单不符。")
        if confirmation != "恢复":
            preview.ok = False
            preview.reasons.append("恢复确认文本不正确，操作已取消。")
        if not preview.ok:
            return preview
        try:
            salt = bytes.fromhex(manifest.salt)
            fer = _fernet_for(passphrase, salt)
        except (ValueError, TypeError):
            preview.ok = False
            preview.reasons.append("备份口令参数损坏。")
            return preview
        try:
            fer.decrypt(payload)  # 仅验证口令与完整性，载荷在替换阶段使用
        except InvalidToken:
            preview.ok = False
            preview.reasons.append("口令错误或备份数据损坏，无法解密。")
            return preview
        # 空间预检：按解包后文件总大小（清单逐文件 size 之和）计算，
        # 覆盖压缩包解包后膨胀的情况，并保留旧数据回滚余量。
        unpacked = sum(entry.size for entry in manifest.files)
        requires = int(unpacked * _RECOVERY_RESERVE_FACTOR)
        preview.requires_space = requires
        try:
            free = shutil.disk_usage(Path(self._database.path).parent).free
        except OSError:
            free = 0
        if free < requires:
            preview.ok = False
            preview.reasons.append(
                f"可用空间不足：需要约 {requires // (1024 * 1024)} MB，"
                f"当前可用约 {free // (1024 * 1024)} MB。"
            )
        # 目标状态预检：数据目录必须存在且可写，否则替换必然失败。
        data_dir = Path(self._database.path).parent
        if not data_dir.is_dir() or not os.access(data_dir, os.W_OK):
            preview.ok = False
            preview.reasons.append(
                "数据目录不可写，恢复无法进行，请检查目录权限。"
            )
        return preview

    def _execute_restore(
        self,
        passphrase: str,
        manifest: BackupManifest,
        backup_bytes: bytes,
    ) -> None:
        """staging 解包 → 逐文件校验 → 原子替换 → 回滚保护。"""
        _, payload = _split_container(backup_bytes)
        try:
            fer = _fernet_for(passphrase, bytes.fromhex(manifest.salt))
            decrypted = fer.decrypt(payload)
        except (InvalidToken, ValueError) as exc:
            raise DataLifecycleError(
                "backup_decryption_failed", "口令错误或备份数据损坏，无法解密。", 422
            ) from exc
        with tempfile.TemporaryDirectory() as staging:
            staging_path = Path(staging)
            try:
                with zipfile.ZipFile(io.BytesIO(decrypted)) as archive:
                    for info in archive.infolist():
                        self._extract_entry(archive, info, staging_path)
                self._verify_staged_files(manifest, staging_path)
            except (zipfile.BadZipFile, OSError, ValueError) as exc:
                raise DataLifecycleError(
                    "backup_corrupted", f"备份内容损坏：{exc}", 422
                ) from exc
            identity_data: dict[str, Any] = {}
            try:
                identity_data = json.loads(
                    (staging_path / IDENTITY_ENTRY).read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                identity_data = {}
            self._swap_into_place(staging_path, identity_data)

    def _extract_entry(
        self, archive: zipfile.ZipFile, info: zipfile.ZipInfo, staging: Path
    ) -> None:
        """解包单条目并拒绝路径穿越（条目必须解析到 staging 内）。"""
        name = info.filename
        if name != DB_ENTRY and name != IDENTITY_ENTRY and not name.startswith(
            OBJECTS_PREFIX
        ):
            return
        target = (staging / name).resolve()
        if not target.is_relative_to(staging.resolve()):
            raise ValueError(f"备份条目路径越界：{name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as source, target.open("wb") as output:
            shutil.copyfileobj(source, output)

    def _verify_staged_files(self, manifest: BackupManifest, staging: Path) -> None:
        """逐文件校验 staging 内容摘要与清单一致。"""
        for entry in manifest.files:
            path = staging / entry.name
            if not path.is_file():
                raise DataLifecycleError(
                    "backup_integrity_failed",
                    f"备份内容不完整：缺少 {entry.name}。",
                    422,
                )
            if _sha256(path.read_bytes()) != entry.sha256:
                raise DataLifecycleError(
                    "backup_integrity_failed",
                    f"备份内容被篡改或损坏：{entry.name} 摘要不符。",
                    422,
                )

    def _swap_into_place(self, staging: Path, identity_data: dict[str, Any]) -> None:
        """原子替换数据库与对象目录；任一步失败回滚到恢复前状态。

        对象库实际落盘目录是 ``objects_dir``（根目录下的 objects/ 子目录，
        内容哈希路径在其下）；备份包内条目前缀与其一致（objects/...）。
        """
        db_path = Path(self._database.path)
        objects_path = self._object_store.objects_dir
        stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
        db_old = db_path.with_name(f"{db_path.name}.pre-restore-{stamp}")
        objects_old = objects_path.with_name(f"{objects_path.name}.pre-restore-{stamp}")
        moved_db = False
        moved_objects = False
        # 身份复位失败时要回滚到恢复前状态：先保存旧身份账户数据。
        previous_identity = self._identity.export_accounts_for_backup()
        try:
            with self._database.snapshot_lock():
                # 关闭全部连接（WAL 先合并进主文件），改名旧文件为新文件腾位；
                # 状态存储与 bridges.db 共用文件，一并关闭避免文件占用。
                self._database.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                self._database.close()
                if self._state_store is not None:
                    self._state_store.close()
                db_path.replace(db_old)
                moved_db = True
                if objects_path.exists():
                    objects_path.replace(objects_old)
                    moved_objects = True
                shutil.move(str(staging / DB_ENTRY), str(db_path))
                if (staging / OBJECTS_PREFIX).exists():
                    shutil.move(str(staging / OBJECTS_PREFIX), str(objects_path))
                self._database.reopen()
                self._database.initialize()
                if self._state_store is not None:
                    self._state_store.reopen()
        except OSError as exc:
            self._rollback_replace(
                db_path, objects_path, db_old, objects_old, moved_db, moved_objects
            )
            raise DataLifecycleError(
                "restore_swap_failed", f"恢复替换失败：{exc}", 502, retryable=True
            ) from exc
        # 身份与凭据复位（替换后、清理旧数据前）：失败回滚文件替换与
        # 身份状态，保持「恢复中断或失败原子回到恢复前状态」。
        try:
            self._finish_restore(identity_data)
        except Exception as exc:  # noqa: BLE001 - 复位失败整体回滚
            self._rollback_replace(
                db_path, objects_path, db_old, objects_old, moved_db, moved_objects
            )
            self._identity.replace_accounts_from_backup(previous_identity)
            raise DataLifecycleError(
                "restore_finalize_failed",
                f"恢复完成前身份复位失败：{exc}。已回滚到恢复前状态。",
                502,
                retryable=True,
            ) from exc
        # 全部成功：清理旧数据备份。
        if moved_db:
            db_old.unlink(missing_ok=True)
        if moved_objects:
            shutil.rmtree(objects_old, ignore_errors=True)

    def _rollback_replace(
        self,
        db_path: Path,
        objects_path: Path,
        db_old: Path,
        objects_old: Path,
        moved_db: bool,
        moved_objects: bool,
    ) -> None:
        """把新文件移走并改回旧文件（恢复失败回到恢复前状态）。

        只有「已改名腾位」的文件才需要清理新文件——替换尚未开始的失败
        路径中 db_path/objects_path 仍是旧数据，绝不能删除。清理前先关闭
        可能指向新文件的连接，避免 Windows 上文件占用导致回滚失败。
        """
        with contextlib.suppress(Exception):  # noqa: BLE001 - 连接可能未打开
            self._database.close()
        if self._state_store is not None:
            with contextlib.suppress(Exception):  # noqa: BLE001
                self._state_store.close()
        if moved_db and db_path.exists():
            db_path.unlink(missing_ok=True)
        if moved_objects and objects_path.exists():
            shutil.rmtree(objects_path, ignore_errors=True)
        if db_old.exists():
            db_old.replace(db_path)
        if moved_objects and objects_old.exists():
            objects_old.replace(objects_path)
        with contextlib.suppress(StorageError):
            self._database.reopen()  # 回滚后连接重开失败由调用方继续上报
        if self._state_store is not None:
            with contextlib.suppress(Exception):  # noqa: BLE001
                self._state_store.reopen()

    def _finish_restore(self, identity_data: dict[str, Any]) -> None:
        """恢复后的能力复位：身份账户数据替换 + SMTP 凭据待重新配置。

        GQ-07 后账户 Qwen Key 已由启动清退整体退役，恢复不再处理它；
        SMTP 授权码不属于备份内容，恢复后必须重新配置。
        """
        self._identity.replace_accounts_from_backup(identity_data)
        for account_id in identity_data.get("accounts", {}):
            try:
                self._smtp_credentials.delete(account_id)
            except Exception:  # noqa: BLE001 - 凭据清理失败不阻断恢复
                continue
        # Issue 03：恢复旧备份也不能重新启用学习提醒或 SMTP 验证状态。
        run_reminder_retirement(
            database=self._database,
            credential_store=self._smtp_credentials,
            state_store=self._state_store,
        )
        self._schedule_index_rebuilds()

    def _schedule_index_rebuilds(self) -> None:
        """校验恢复后索引一致性，不一致账户标记重建（执行器重建）。"""
        rows = self._database.connection.execute(
            "SELECT ia.account_id, ia.version_id, iv.chunk_count, iv.vector_count"
            " FROM index_active ia"
            " JOIN index_versions iv ON iv.version_id = ia.version_id"
        ).fetchall()
        for row in rows:
            account_id = str(row["account_id"])
            chunk_count = int(row["chunk_count"])
            vector_count = int(row["vector_count"])
            actual_chunks = self._database.connection.execute(
                "SELECT COUNT(*) AS count FROM document_chunks WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            actual_vectors = self._database.connection.execute(
                "SELECT COUNT(*) AS count FROM index_vectors WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            if (
                chunk_count != int(actual_chunks["count"])
                or vector_count != int(actual_vectors["count"])
            ):
                self._database.connection.execute(
                    "UPDATE document_records SET rebuild_requested = 1,"
                    " updated_at = ? WHERE account_id = ?",
                    (_now(), account_id),
                )

    def _audit_restore(
        self, preview: RestorePreview, *, result: AuditResult
    ) -> None:
        self._observability.log_audit(
            actor_account_id="",
            action=(
                AuditAction.RESTORE_COMPLETE
                if result == AuditResult.SUCCESS
                else AuditAction.RESTORE_FAILED
            ),
            result=result,
            details={
                key: value
                for key, value in {
                    "format_version": preview.format_version,
                    "schema_version": preview.schema_version,
                    "account_count": preview.account_count,
                    "payload_size": preview.payload_size,
                    "error": "；".join(preview.reasons) if preview.reasons else None,
                }.items()
                if key in _AUDIT_DETAIL_KEYS and value is not None
            },
        )
