"""画像工单 17：历史重整的启动、进度与清理（隔离副本生命周期）。

启动（``start_rebuild``）按冻结顺序建立恢复前提，任何一步崩溃后重复
启动都会继续完成剩余步骤；工作副本一旦建立台账（运行行），重复启动只
报告状态，不再触碰副本：

1. 派生批次标识并写入 ``rebuild.json``（崩溃恢复的定位标记，先于一切
   数据产物）；
2. 校验清单新鲜度（仅在备份尚未落盘时）；写出清单副本与已验证备份；
3. 从备份复制工作副本（仅在副本缺失时）；
4. 台账运行行缺失时（启动阶段未完成）：在副本上重算基线摘要并与清单
   配对、按预览同一门控播种、幂等写入运行行与计数。

进度（``rebuild_status``）与清理（``cleanup_rebuild``）语义见各函数
docstring；驱动的有界推进在 ``history_rebuild_drive``。
"""

from __future__ import annotations

import contextlib
import shutil
from datetime import datetime
from pathlib import Path

from bridges.profiles.history_backup import HistoryBackupRecord, create_history_backup
from bridges.profiles.history_contracts import AccountSnapshot, HistoryPreviewManifest
from bridges.profiles.history_preview import (
    save_preview_manifest,
    verify_preview_manifest,
)
from bridges.profiles.history_rebuild_contracts import (
    HISTORY_REBUILD_POLICY_VERSION,
    REBUILD_BACKUP_FILENAME,
    REBUILD_METADATA_FILENAME,
    REBUILD_PREVIEW_FILENAME,
    REBUILD_REPORT_FILENAME,
    REBUILD_WORKING_DB_FILENAME,
    REBUILD_WORKING_DIRNAME,
    ProfileRebuildError,
    RebuildItemStatus,
    RebuildSkipReason,
    RebuildStatus,
    RebuildWindow,
    now_text,
    rebuild_id_for,
    text_digest,
)
from bridges.profiles.history_rebuild_records import (
    ManualItemDigest,
    RebuildBaseline,
    RebuildRunRecord,
    RebuildSeedItem,
)
from bridges.profiles.history_rebuild_repository import (
    SqliteProfileRebuildRepository,
    build_progress,
    require_run,
)
from bridges.profiles.history_rebuild_runtime import (
    RebuildMetadata,
    load_workspace,
    metadata_path,
    open_working_database,
    rebuild_extractor_version,
    write_metadata,
)
from bridges.profiles.history_repository import (
    HistoryControlsRecord,
    HistoryItemRecord,
    ProfileHistorySnapshotReader,
)
from bridges.profiles.history_snapshot import (
    classify_history_message,
    compute_account_snapshot,
)
from bridges.profiles.replay import DatabaseIdentity
from bridges.storage.database import BridgesDatabase


def _require_schema_version_here(database: BridgesDatabase) -> int:
    version = database.schema_version
    if version is None:
        raise ProfileRebuildError(
            "schema_version_missing", "工作副本没有 schema 版本记录。"
        )
    return version


def _baseline_from_copy(
    database: BridgesDatabase,
    manifest: HistoryPreviewManifest,
) -> tuple[AccountSnapshot, list[HistoryItemRecord]]:
    """在工作副本上重算快照并核对与清单一致；返回 (快照, 条目行)。"""

    connection = database.connection
    reader = ProfileHistorySnapshotReader(connection)
    if not reader.account_exists(manifest.account_id):
        raise ProfileRebuildError(
            "account_missing", f"工作副本中不存在账户：{manifest.account_id}"
        )
    schema_version = _require_schema_version_here(database)
    snapshot = compute_account_snapshot(
        connection, manifest.account_id, schema_version=schema_version
    )
    if snapshot.state_digest != manifest.state_digest:
        raise ProfileRebuildError(
            "working_copy_mismatch",
            "工作副本内容状态与预览清单不一致：备份或副本被并发修改，禁止开始重整。",
        )
    return snapshot, reader.list_items(manifest.account_id)


def _rebuild_baseline(
    manifest: HistoryPreviewManifest,
    items: list[HistoryItemRecord],
    controls: HistoryControlsRecord,
) -> RebuildBaseline:
    manual_items = tuple(
        ManualItemDigest(
            profile_item_id=item.profile_item_id,
            version=item.version,
            user_edited_at=item.user_edited_at,
            text_digest=text_digest(item.text),
        )
        for item in items
        if item.status == "active"
        and (item.write_origin == "user" or item.user_edited_at is not None)
    )
    withdrawn = [item for item in items if item.status == "withdrawn"]
    return RebuildBaseline(
        preview_version=manifest.manifest_version,
        preview_created_at=manifest.created_at,
        state_digest=manifest.state_digest,
        chat_scope=manifest.chat_scope.as_dict(),
        items=manifest.items.as_dict(),
        controls=manifest.controls.as_dict(),
        domain_refs=manifest.domain_refs.as_dict(),
        manual_items=manual_items,
        withdrawn_item_ids=tuple(item.profile_item_id for item in withdrawn),
        withdrawn_identity_keys=tuple(item.identity_key for item in withdrawn),
        tombstoned_message_ids=tuple(controls.tombstoned_message_ids),
    )


def _seed_window_items(
    database: BridgesDatabase,
    manifest: HistoryPreviewManifest,
    window: RebuildWindow,
) -> list[RebuildSeedItem]:
    """按窗口与预览同一门控逐条判定；只写标识与原因，不读也不存正文。"""

    reader = ProfileHistorySnapshotReader(database.connection)
    controls = reader.read_controls(manifest.account_id)
    items = reader.list_items(manifest.account_id)
    withdrawn_source_ids = {
        message_id
        for item in items
        if item.status == "withdrawn"
        for message_id in item.source_message_ids
    }
    withdrawn_keys = {
        item.identity_key for item in items if item.status == "withdrawn"
    }
    tombstoned = set(controls.tombstoned_message_ids)

    seeded: list[RebuildSeedItem] = []
    for message in reader.iter_messages(manifest.account_id):
        if message.role != "user":
            continue
        if not window.contains(message.created_at):
            continue
        decision = classify_history_message(
            message,
            account_id=manifest.account_id,
            tombstoned_message_ids=tombstoned,
            withdrawn_source_ids=withdrawn_source_ids,
            withdrawn_identity_keys=withdrawn_keys,
            content_blocks=controls.content_blocks,
            recording_enabled=controls.recording_enabled,
        )
        if decision.eligible:
            seeded.append(
                RebuildSeedItem(
                    message_id=message.message_id,
                    conversation_id=message.conversation_id,
                    source_created_at=message.created_at,
                    status=RebuildItemStatus.PENDING,
                )
            )
        elif decision.skip_reason is not None:
            seeded.append(
                RebuildSeedItem(
                    message_id=message.message_id,
                    conversation_id=message.conversation_id,
                    source_created_at=message.created_at,
                    status=RebuildItemStatus.SKIPPED,
                    skip_reason=RebuildSkipReason(decision.skip_reason),
                )
            )
    return seeded


def start_rebuild(
    authority: DatabaseIdentity,
    manifest: HistoryPreviewManifest,
    *,
    work_dir: str | Path,
    window: RebuildWindow | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    """建立隔离工作副本并播种窗口判定；崩溃后重复启动继续完成剩余步骤。

    幂等边界：批次标识由（账户, 状态摘要, 策略版本, 预览版本, 窗口）确定性
    派生；同一目录绑定另一批次显式拒绝。台账运行行一旦存在（即启动阶段
    完成），重复启动只报告状态，不再触碰副本。
    """

    base = Path(work_dir)
    effective_window = window or RebuildWindow(None, None)
    rebuild_id = rebuild_id_for(
        account_id=manifest.account_id,
        state_digest=manifest.state_digest,
        policy_version=HISTORY_REBUILD_POLICY_VERSION,
        preview_version=manifest.manifest_version,
        window=effective_window,
    )
    metadata_file = metadata_path(base)
    resumed = metadata_file.exists()
    if resumed:
        existing = load_workspace(base)
        if existing.rebuild_id != rebuild_id:
            raise ProfileRebuildError(
                "work_dir_conflict",
                "工作目录已绑定另一批次（标识或窗口不同）；请使用新的工作目录或先清理。",
            )
    created_dir = not base.exists()
    base.mkdir(parents=True, exist_ok=True)

    backup_path = base / REBUILD_BACKUP_FILENAME
    backup_manifest = Path(f"{backup_path}.manifest.json")
    if backup_path.exists() and not backup_manifest.exists():
        # 上次启动在在线备份中途崩溃：只有文件而没有完整性 manifest。
        # 备份是派生数据，丢弃后凭同一清单重新创建；完整性 manifest
        # 是备份完成的 journal 标记（见 create_verified_backup）。
        backup_path.unlink(missing_ok=True)
        Path(f"{backup_path}.history-record.json").unlink(missing_ok=True)
    if not resumed:
        # 崩溃恢复的定位标记先于一切数据产物；后续步骤在任何一步中断后
        # 都可凭它继续完成（见模块 docstring 的冻结顺序）。
        write_metadata(
            metadata_file,
            RebuildMetadata(
                rebuild_id=rebuild_id,
                account_id=manifest.account_id,
                policy_version=HISTORY_REBUILD_POLICY_VERSION,
                extractor_version=rebuild_extractor_version(),
                preview_version=manifest.manifest_version,
                created_at=now_text(now),
            ),
        )
    backup_record: HistoryBackupRecord | None = None
    if not backup_path.exists():
        drift = verify_preview_manifest(authority, manifest)
        if not drift.fresh:
            # 启动在任何数据产物之前失败：清掉定位标记与清单副本（本次或
            # 前次最早期崩溃的遗留），让工作目录回到干净状态，重新预览后
            # 可直接在同目录重试。
            metadata_file.unlink(missing_ok=True)
            (base / REBUILD_PREVIEW_FILENAME).unlink(missing_ok=True)
            if created_dir:
                with contextlib.suppress(OSError):
                    base.rmdir()
            raise ProfileRebuildError(
                "preview_stale",
                "预览清单与当前数据源不一致，必须重新预览后再启动重整。",
            )
        save_preview_manifest(manifest, base / REBUILD_PREVIEW_FILENAME)
        backup_record = create_history_backup(
            authority,
            backup_path,
            preview=manifest,
            work_dir=base,
        )
    working_dir = base / REBUILD_WORKING_DIRNAME
    working_dir.mkdir(parents=True, exist_ok=True)
    working_db_path = working_dir / REBUILD_WORKING_DB_FILENAME
    if not working_db_path.exists():
        try:
            shutil.copyfile(backup_path, working_db_path)
        except OSError as exc:
            raise ProfileRebuildError(
                "working_copy_failed", "无法从已验证备份创建工作副本。"
            ) from exc
    database = BridgesDatabase(working_db_path)
    seeded = 0
    try:
        database.initialize()
        ledger = SqliteProfileRebuildRepository(database, initialize=False)
        if ledger.get_run(rebuild_id) is None:
            # 启动阶段未完成：重算基线、幂等播种，最后写运行行（种子先于
            # 运行行，崩溃只会留下可安全重做的半成品）。
            schema_version = _require_schema_version_here(database)
            _snapshot, item_rows = _baseline_from_copy(database, manifest)
            controls = ProfileHistorySnapshotReader(
                database.connection
            ).read_controls(manifest.account_id)
            baseline = _rebuild_baseline(manifest, item_rows, controls)
            seeded_items = _seed_window_items(database, manifest, effective_window)
            ledger.seed_items(rebuild_id, manifest.account_id, seeded_items)
            ledger.create_run(
                RebuildRunRecord(
                    rebuild_id=rebuild_id,
                    account_id=manifest.account_id,
                    status=RebuildStatus.CREATED,
                    policy_version=HISTORY_REBUILD_POLICY_VERSION,
                    extractor_version=rebuild_extractor_version(),
                    source_digest=manifest.source_digest,
                    state_digest=manifest.state_digest,
                    schema_version=schema_version,
                    window_start_at=effective_window.start_at,
                    window_end_at=effective_window.end_at,
                    baseline=baseline,
                    window_total=0,
                    eligible_count=0,
                    extracted_count=0,
                    committed_count=0,
                    skipped_count=0,
                    failed_count=0,
                    last_error=None,
                    created_at=now_text(now),
                    updated_at=now_text(now),
                )
            )
            ledger.refresh_counters(rebuild_id, now=now_text(now))
            seeded = len(seeded_items)
    finally:
        database.close()
    return _start_report(
        base,
        rebuild_id,
        reused=resumed,
        seeded=seeded,
        backup=backup_record.as_dict() if backup_record is not None else None,
    )


def _start_report(
    base: Path,
    rebuild_id: str,
    *,
    reused: bool,
    seeded: int,
    backup: dict[str, object] | None = None,
) -> dict[str, object]:
    report = rebuild_status(base)
    report["reused"] = reused
    report["seeded"] = seeded
    if backup is not None:
        report["backup"] = backup
    return report


def rebuild_status(work_dir: str | Path) -> dict[str, object]:
    """只读进度视图：分母、终态、剩余范围与版本绑定。"""

    workspace = load_workspace(work_dir)
    database = open_working_database(workspace)
    try:
        ledger = SqliteProfileRebuildRepository(database, initialize=False)
        run = require_run(ledger, workspace.rebuild_id)
        progress = build_progress(ledger, run)
    finally:
        database.close()
    return {
        "rebuild_id": run.rebuild_id,
        "account_id": run.account_id,
        "policy_version": run.policy_version,
        "extractor_version": run.extractor_version,
        "source_digest": run.source_digest,
        "state_digest": run.state_digest,
        "baseline_digest": run.baseline.manual_items_digest,
        "window": {
            "start_at": run.window_start_at,
            "end_at": run.window_end_at,
        },
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "completed_at": run.completed_at,
        "reconciled_at": run.reconciled_at,
        "last_error": run.last_error,
        "progress": progress.as_dict(),
    }


def cleanup_rebuild(
    work_dir: str | Path,
    *,
    purge: bool = False,
    force: bool = False,
) -> dict[str, object]:
    """清理工作目录：删除工作副本与台账；``purge`` 一并删除恢复前提与记录。

    运行中/暂停中的台账默认拒绝清理（先继续推进或显式 ``--force`` 放弃）。
    已完成/已失败的批次保留恢复前提（已验证备份、预览清单、对账记录）供
    审计；``--purge`` 才全部删除。重复清理幂等。
    """

    base = Path(work_dir)
    if not base.exists():
        return {"work_dir": str(base), "removed": [], "purged": purge}
    workspace = load_workspace(base)
    if workspace.working_db_path.exists():
        database = open_working_database(workspace)
        try:
            ledger = SqliteProfileRebuildRepository(database, initialize=False)
            run = ledger.get_run(workspace.rebuild_id)
            if run is not None:
                settled = run.status in {
                    RebuildStatus.COMPLETED,
                    RebuildStatus.FAILED,
                }
                if not settled and not force:
                    raise ProfileRebuildError(
                        "rebuild_not_settled",
                        f"重整尚未收敛（{run.status.value}）；继续运行或使用 --force 放弃。",
                    )
                ledger.delete_run(workspace.rebuild_id)
        finally:
            database.close()
    removed: list[str] = []
    if workspace.working_dir.exists():
        shutil.rmtree(workspace.working_dir)
        removed.append(str(workspace.working_dir))
    if purge:
        for name in (
            REBUILD_BACKUP_FILENAME,
            f"{REBUILD_BACKUP_FILENAME}.manifest.json",
            f"{REBUILD_BACKUP_FILENAME}.history-record.json",
            REBUILD_PREVIEW_FILENAME,
            REBUILD_REPORT_FILENAME,
            REBUILD_METADATA_FILENAME,
        ):
            target = base / name
            if target.exists():
                target.unlink()
                removed.append(str(target))
    return {"work_dir": str(base), "removed": removed, "purged": purge}


__all__ = [
    "cleanup_rebuild",
    "rebuild_status",
    "start_rebuild",
]
