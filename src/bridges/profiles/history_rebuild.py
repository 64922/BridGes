"""画像工单 17：可恢复的历史重整编排（隔离副本、有界驱动、进度台账）。

工作目录布局（``start`` 一次性建立，之后只读或原位推进）：

- ``history.db``：按 16 的新鲜预览清单创建的已验证备份（不可变恢复前提，
  含备份记录与清单文件）；
- ``preview.json``：本次执行绑定的预览清单副本；
- ``working/bridges.db``：备份的隔离工作副本；重整只写这里；
- ``rebuild.json``：运行元数据（标识、账户、策略/抽取器版本、窗口）。

执行合同：

- 全部写入复用在线提取/验证/提交语义（同一 ``AutomaticProfileService``、
  同一提交边界），不建立宽松的迁移写入通道；模型调用不在写事务内，提交
  前复核墓碑、控制与来源版本；
- 来源时间决定事实时间：抽取提交以消息原始 ``created_at`` 作为事实锚，
  台账时间只记录操作时间；
- 有界窗口 + 有界驱动（``max_ticks`` 为模型调用上限）：每次调用收敛，
  中断后再次 ``run`` 恢复；任务幂等键与台账状态保证不重复生效；
- 失败不当作成功水位：逐条终态 = 迁入 / 提交前屏障跳过 / 失败；未决
  条目如实计入剩余范围，``completed`` 只在全部终态后成立；对账全部通过
  以 ``reconciled_at`` 单独记录（见 ``history_rebuild_reconcile``）。
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bridges.profiles.automatic import (
    AUTOMATIC_EXTRACTOR_VERSION,
    PROFILE_EXTRACTION_MAX_PENDING_PER_ACCOUNT,
    PROFILE_REBUILD_QUEUE,
    AutomaticProfileExtractor,
    AutomaticProfileService,
    NeighborWindowReader,
    SqliteAutomaticProfileRepository,
)
from bridges.profiles.history_backup import create_history_backup
from bridges.profiles.history_contracts import (
    HistoryPreviewManifest,
    digest_lines,
)
from bridges.profiles.history_preview import (
    ProfileHistoryError,
    save_preview_manifest,
    verify_preview_manifest,
)
from bridges.profiles.history_rebuild_contracts import (
    BARRIER_EXHAUSTION_CODES,
    HISTORY_REBUILD_POLICY_VERSION,
    REBUILD_BACKUP_FILENAME,
    REBUILD_METADATA_FILENAME,
    REBUILD_PREVIEW_FILENAME,
    REBUILD_WORKING_DB_FILENAME,
    REBUILD_WORKING_DIRNAME,
    ManualItemDigest,
    RebuildBaseline,
    RebuildItemStatus,
    RebuildProgress,
    RebuildRunRecord,
    RebuildSeedItem,
    RebuildSkipReason,
    RebuildStatus,
    RebuildWindow,
    now_text,
    rebuild_id_for,
    text_digest,
)
from bridges.profiles.history_rebuild_repository import (
    ExtractionRunSnapshot,
    SqliteProfileRebuildRepository,
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
from bridges.profiles.signals import ProfileSignalClassifier
from bridges.storage.database import BridgesDatabase

if TYPE_CHECKING:
    from bridges.chat.repository import ConversationRepository
    from bridges.profiles.atomic import AtomicProfileService
    from bridges.profiles.history_contracts import AccountSnapshot
    from bridges.profiles.overview import ProfileOverviewService

#: 一次 ``run`` 驱动默认的模型调用上限（有界驱动；可用 ``--max-ticks`` 覆盖）。
DEFAULT_REBUILD_MAX_TICKS = 100

#: 抽取器工厂：由运行库状态装配模型适配部分；前文窗口与旧记忆参考读取器
#: 由运行库绑定后注入，保证与在线装配同一语义。
ExtractorFactory = Callable[
    [
        BridgesDatabase,
        "NeighborWindowReader | None",
        "Callable[[str, str], list[str]] | None",
    ],
    AutomaticProfileExtractor,
]


def rebuild_extractor_version() -> str:
    """重整使用的抽取器版本（与适配器实现无关，仅由版本常量决定）。"""

    return f"{AUTOMATIC_EXTRACTOR_VERSION}+signal-{ProfileSignalClassifier().version}"


class ProfileRebuildError(ProfileHistoryError):
    """重整硬门失败；错误码供 CLI 与审计调用方稳定分支。"""


@dataclass(frozen=True)
class RebuildWorkspace:
    """工作目录的稳定路径集合（由 ``rebuild.json`` 锁定重整标识）。"""

    work_dir: Path
    rebuild_id: str
    account_id: str

    @property
    def metadata_path(self) -> Path:
        return self.work_dir / REBUILD_METADATA_FILENAME

    @property
    def manifest_path(self) -> Path:
        return self.work_dir / REBUILD_PREVIEW_FILENAME

    @property
    def backup_path(self) -> Path:
        return self.work_dir / REBUILD_BACKUP_FILENAME

    @property
    def working_dir(self) -> Path:
        return self.work_dir / REBUILD_WORKING_DIRNAME

    @property
    def working_db_path(self) -> Path:
        return self.working_dir / REBUILD_WORKING_DB_FILENAME


@dataclass(frozen=True)
class RebuildMetadata:
    """``rebuild.json``：重整标识与版本绑定的唯一权威记录。"""

    rebuild_id: str
    account_id: str
    policy_version: str
    extractor_version: str
    preview_version: str
    created_at: str

    def as_dict(self) -> dict[str, object]:
        return {
            "rebuild_id": self.rebuild_id,
            "account_id": self.account_id,
            "policy_version": self.policy_version,
            "extractor_version": self.extractor_version,
            "preview_version": self.preview_version,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> RebuildMetadata:
        return cls(
            rebuild_id=str(payload["rebuild_id"]),
            account_id=str(payload["account_id"]),
            policy_version=str(payload["policy_version"]),
            extractor_version=str(payload["extractor_version"]),
            preview_version=str(payload["preview_version"]),
            created_at=str(payload["created_at"]),
        )


def _write_metadata(path: Path, metadata: RebuildMetadata) -> None:
    path.write_text(
        json.dumps(metadata.as_dict(), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _metadata_path(work_dir: Path) -> Path:
    return work_dir / REBUILD_METADATA_FILENAME


def load_workspace(work_dir: str | Path) -> RebuildWorkspace:
    """读取工作目录元数据；缺失或损坏显式失败，不猜测。"""

    base = Path(work_dir)
    metadata_file = _metadata_path(base)
    if not base.exists():
        raise ProfileRebuildError(
            "work_dir_missing", f"重整工作目录不存在：{base}"
        )
    if not metadata_file.exists():
        raise ProfileRebuildError(
            "rebuild_metadata_missing",
            f"工作目录没有重整元数据（{REBUILD_METADATA_FILENAME}）：{base}",
        )
    try:
        payload = json.loads(metadata_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProfileRebuildError(
            "rebuild_metadata_invalid", f"重整元数据无法解析：{metadata_file}"
        ) from exc
    if not isinstance(payload, dict):
        raise ProfileRebuildError(
            "rebuild_metadata_invalid", f"重整元数据不是 JSON 对象：{metadata_file}"
        )
    try:
        metadata = RebuildMetadata.from_dict(payload)
    except (KeyError, TypeError, ValueError) as exc:
        raise ProfileRebuildError(
            "rebuild_metadata_invalid", f"重整元数据字段缺失或非法：{metadata_file}"
        ) from exc
    return RebuildWorkspace(
        work_dir=base,
        rebuild_id=metadata.rebuild_id,
        account_id=metadata.account_id,
    )


def load_metadata(work_dir: str | Path) -> RebuildMetadata:
    payload = json.loads(
        _metadata_path(Path(work_dir)).read_text(encoding="utf-8")
    )
    return RebuildMetadata.from_dict(payload)


# ----------------------------------------------------------------------
# 组装（与组合根同一接线的维护子集）
# ----------------------------------------------------------------------


def open_working_database(workspace: RebuildWorkspace) -> BridgesDatabase:
    """打开工作副本（幂等初始化 schema）；缺失时显式失败。

    不自动从备份重建：悄悄丢弃既有进度比立刻失败更危险。
    """

    if not workspace.working_db_path.exists():
        raise ProfileRebuildError(
            "working_copy_missing", f"工作副本不存在：{workspace.working_db_path}"
        )
    database = BridgesDatabase(workspace.working_db_path)
    database.initialize()
    return database


def _build_overview_service(
    database: BridgesDatabase,
    *,
    atoms: AtomicProfileService,
    controls_reader: Any,
    source_reader: Any,
) -> ProfileOverviewService:
    """概览服务接线（与组合根一致：同一份控制与来源读取）。"""

    from bridges.profiles.overview import ProfileOverviewService
    from bridges.profiles.overview_repository import SqliteProfileOverviewStore

    return ProfileOverviewService(
        atomic=atoms,
        overview_store=SqliteProfileOverviewStore(database),
        controls_reader=controls_reader,
        message_reader=source_reader,
    )


@dataclass
class RebuildRuntime:
    """维护运行的画像服务子集（与组合根同一接线，供驱动与对账共用）。"""

    service: AutomaticProfileService
    overview: ProfileOverviewService
    atoms: AtomicProfileService
    conversations: ConversationRepository


def build_runtime(
    database: BridgesDatabase,
    extractor_factory: ExtractorFactory,
) -> RebuildRuntime:
    """按组合根同一接线装配维护运行所需的画像服务子集。

    复用 ``bridges.api.app_support`` 的三个读取适配器（与评价运行器同一
    先例：非 API 层惰性导入，避免模块级循环依赖），保证前文窗口、旧记忆
    参考与来源快照的语义与在线完全一致；抽取器的模型适配部分由调用方
    工厂装配（测试为确定性规则抽取器，生产为网关抽取器）。
    """

    from bridges.ai.sqlite_recorder import SqliteModelRunLockRecorder
    from bridges.api.app_support import (
        _atomic_profile_source_reader,
        _profile_memory_reader,
        _profile_neighbor_reader,
    )
    from bridges.chat.repository import ConversationRepository
    from bridges.profiles.atomic import (
        AtomicProfileService,
        SqliteAtomicProfileRepository,
    )
    from bridges.profiles.four_dimensions import (
        FourDimensionProfileService,
        SqliteFourDimensionProfileRepository,
    )
    from bridges.profiles.sqlite_repository import SqliteProfileRepository

    conversation_repository = ConversationRepository(database)
    neighbor_reader = _profile_neighbor_reader(conversation_repository)
    dimensions = FourDimensionProfileService(
        source_repository=SqliteProfileRepository(database),
        repository=SqliteFourDimensionProfileRepository(database),
    )
    automatic_repository = SqliteAutomaticProfileRepository(database, initialize=False)
    dimensions.set_observation_delete_callback(
        automatic_repository.delete_observations_for_record
    )
    atoms = AtomicProfileService(
        four_dimensions=dimensions,
        repository=SqliteAtomicProfileRepository(database),
        message_reader=_atomic_profile_source_reader(conversation_repository),
    )
    extractor = extractor_factory(
        database, neighbor_reader, _profile_memory_reader(atoms)
    )
    service = AutomaticProfileService(
        four_dimension_service=dimensions,
        repository=automatic_repository,
        extractor=extractor,
        message_reader=conversation_repository.get_message,
        queue_name=PROFILE_REBUILD_QUEUE,
        lock_recorder=SqliteModelRunLockRecorder(database),
        atomic_profile_service=atoms,
        neighbor_reader=neighbor_reader,
    )
    overview = _build_overview_service(
        database,
        atoms=atoms,
        controls_reader=service.account_controls,
        source_reader=_atomic_profile_source_reader(conversation_repository),
    )
    atoms.set_profile_overview_listener(overview)
    return RebuildRuntime(
        service=service,
        overview=overview,
        atoms=atoms,
        conversations=conversation_repository,
    )


# ----------------------------------------------------------------------
# 启动：建立工作副本并播种窗口判定
# ----------------------------------------------------------------------


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


def _require_schema_version_here(database: BridgesDatabase) -> int:
    version = database.schema_version
    if version is None:
        raise ProfileRebuildError(
            "schema_version_missing", "工作副本没有 schema 版本记录。"
        )
    return version


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
    """建立隔离工作副本并播种窗口判定；同一批次的重复启动幂等返回。"""

    base = Path(work_dir)
    effective_window = window or RebuildWindow(None, None)
    rebuild_id = rebuild_id_for(
        account_id=manifest.account_id,
        state_digest=manifest.state_digest,
        policy_version=HISTORY_REBUILD_POLICY_VERSION,
        preview_version=manifest.manifest_version,
        window=effective_window,
    )
    metadata_file = _metadata_path(base)
    if metadata_file.exists():
        existing = load_workspace(base)
        if existing.rebuild_id != rebuild_id:
            raise ProfileRebuildError(
                "work_dir_conflict",
                "工作目录已绑定另一批次（标识或窗口不同）；请使用新的工作目录或先清理。",
            )
        return _start_report(base, existing.rebuild_id, reused=True)

    drift = verify_preview_manifest(authority, manifest)
    if not drift.fresh:
        raise ProfileRebuildError(
            "preview_stale",
            "预览清单与当前数据源不一致，必须重新预览后再启动重整。",
        )
    base.mkdir(parents=True, exist_ok=True)
    record = create_history_backup(
        authority,
        base / REBUILD_BACKUP_FILENAME,
        preview=manifest,
        work_dir=base,
    )
    save_preview_manifest(manifest, base / REBUILD_PREVIEW_FILENAME)
    working_dir = base / REBUILD_WORKING_DIRNAME
    working_dir.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copyfile(base / REBUILD_BACKUP_FILENAME, working_dir / REBUILD_WORKING_DB_FILENAME)
    except OSError as exc:
        raise ProfileRebuildError(
            "working_copy_failed", "无法从已验证备份创建工作副本。"
        ) from exc
    _write_metadata(
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

    database = BridgesDatabase(working_dir / REBUILD_WORKING_DB_FILENAME)
    try:
        database.initialize()
        schema_version = _require_schema_version_here(database)
        snapshot, item_rows = _baseline_from_copy(database, manifest)
        controls = ProfileHistorySnapshotReader(database.connection).read_controls(
            manifest.account_id
        )
        baseline = _rebuild_baseline(manifest, item_rows, controls)
        seeded = _seed_window_items(database, manifest, effective_window)
        ledger = SqliteProfileRebuildRepository(database, initialize=False)
        existing_run = ledger.get_run(rebuild_id)
        if existing_run is None:
            created_at = now_text(now)
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
                    created_at=created_at,
                    updated_at=created_at,
                )
            )
        ledger.seed_items(rebuild_id, manifest.account_id, seeded)
        ledger.refresh_counters(rebuild_id, now=now_text(now))
    finally:
        database.close()
    return _start_report(base, rebuild_id, reused=False, backup=record.as_dict())


def _start_report(
    base: Path,
    rebuild_id: str,
    *,
    reused: bool,
    backup: dict[str, object] | None = None,
) -> dict[str, object]:
    report = _progress_report(base)
    report["reused"] = reused
    if backup is not None:
        report["backup"] = backup
    return report


# ----------------------------------------------------------------------
# 驱动：有界调度 + 领取 + 分类同步
# ----------------------------------------------------------------------


def _progress(
    base: Path,
    ledger: SqliteProfileRebuildRepository,
    run: RebuildRunRecord,
) -> RebuildProgress:
    counts = ledger.count_items(run.rebuild_id)
    skipped_by_reason = ledger.count_skip_reasons(run.rebuild_id)
    pending = counts.get(RebuildItemStatus.PENDING.value, 0)
    scheduled = counts.get(RebuildItemStatus.SCHEDULED.value, 0)
    notes: list[str] = []
    if pending:
        notes.append(
            f"仍有 {pending} 条待调度（调度背压或来源状态在进入前被屏障拦截）。"
        )
    if scheduled:
        notes.append(f"仍有 {scheduled} 条在途（再次 run 将恢复推进）。")
    return RebuildProgress(
        status=run.status,
        window_total=run.window_total,
        eligible=run.eligible_count,
        extracted=run.extracted_count,
        committed=run.committed_count,
        skipped=run.skipped_count,
        failed=run.failed_count,
        pending=pending,
        scheduled=scheduled,
        skipped_by_reason=skipped_by_reason,
        notes=tuple(notes),
    )


def rebuild_status(work_dir: str | Path) -> dict[str, object]:
    """只读进度视图：分母、终态、剩余范围与版本绑定。"""

    workspace = load_workspace(work_dir)
    database = open_working_database(workspace)
    try:
        if database.schema_version is None:
            database.initialize()
        ledger = SqliteProfileRebuildRepository(database, initialize=False)
        run = ledger.get_run(workspace.rebuild_id)
        if run is None:
            raise ProfileRebuildError(
                "rebuild_not_found",
                f"工作副本中没有重整台账：{workspace.rebuild_id}",
            )
        progress = _progress(workspace.work_dir, ledger, run)
    finally:
        database.close()
    return {
        "rebuild_id": run.rebuild_id,
        "account_id": run.account_id,
        "policy_version": run.policy_version,
        "extractor_version": run.extractor_version,
        "source_digest": run.source_digest,
        "state_digest": run.state_digest,
        "baseline_digest": digest_lines(
            [
                f"{item.profile_item_id}|{item.version}"
                for item in run.baseline.manual_items
            ]
        ),
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


def _progress_report(base: Path) -> dict[str, object]:
    return rebuild_status(base)


def _load_run(ledger: SqliteProfileRebuildRepository, rebuild_id: str) -> RebuildRunRecord:
    run = ledger.get_run(rebuild_id)
    if run is None:
        raise ProfileRebuildError(
            "rebuild_not_found", f"工作副本中没有重整台账：{rebuild_id}"
        )
    return run


def _check_resume_versions(
    run: RebuildRunRecord, *, extractor_version: str, policy_version: str
) -> None:
    if run.policy_version != policy_version:
        raise ProfileRebuildError(
            "rebuild_version_mismatch",
            "策略版本已变化：禁止在新策略下恢复旧台账，请重新预览并启动新批次。",
        )
    if run.extractor_version != extractor_version:
        raise ProfileRebuildError(
            "rebuild_version_mismatch",
            "抽取器版本已变化：禁止恢复旧台账（幂等键绑定抽取器版本）。",
        )


def _read_window_message(
    database: BridgesDatabase, account_id: str, message_id: str
) -> Any | None:
    reader = ProfileHistorySnapshotReader(database.connection)
    for message in reader.iter_messages(account_id):
        if message.message_id == message_id:
            return message
    return None


def _classify_terminal_run(
    snapshot: ExtractionRunSnapshot,
) -> tuple[RebuildItemStatus, RebuildSkipReason | None, str | None]:
    if snapshot.status == "succeeded":
        return RebuildItemStatus.EXTRACTED, None, None
    if snapshot.status == "exhausted":
        barrier = BARRIER_EXHAUSTION_CODES.get(snapshot.last_error or "")
        if barrier is not None:
            return RebuildItemStatus.SKIPPED, barrier, None
        if snapshot.outcome == "no_signal" and snapshot.attempts == 0:
            return (
                RebuildItemStatus.SKIPPED,
                RebuildSkipReason.BLOCKED_AT_SCHEDULE,
                None,
            )
        return RebuildItemStatus.FAILED, None, snapshot.last_error
    return RebuildItemStatus.SCHEDULED, None, None


def _schedule_pending_items(
    database: BridgesDatabase,
    service: AutomaticProfileService,
    ledger: SqliteProfileRebuildRepository,
    run: RebuildRunRecord,
) -> tuple[int, int]:
    """调度待定条目：本地门控后经在线调度入口入队；受账户背压约束。"""

    classifier = ProfileSignalClassifier()
    scheduled = 0
    backlog_blocked = 0
    for item in ledger.list_items(
        run.rebuild_id, statuses=[RebuildItemStatus.PENDING]
    ):
        if (
            ledger.count_pending_tasks(run.account_id)
            >= PROFILE_EXTRACTION_MAX_PENDING_PER_ACCOUNT
        ):
            backlog_blocked += 1
            continue
        message = _read_window_message(database, run.account_id, item.message_id)
        if message is None:
            ledger.update_item(
                run.rebuild_id,
                item.message_id,
                status=RebuildItemStatus.SKIPPED,
                skip_reason=RebuildSkipReason.SOURCE_MISSING,
            )
            continue
        classification = classifier.extraction_classification(message.content)
        if not classifier.is_schedulable(message.content, classification):
            ledger.update_item(
                run.rebuild_id,
                item.message_id,
                status=RebuildItemStatus.SKIPPED,
                skip_reason=RebuildSkipReason.NO_SIGNAL,
            )
            continue
        extraction_run = service.schedule_message_extraction(
            run.account_id,
            conversation_id=item.conversation_id,
            message_id=item.message_id,
            content=message.content,
            run_id=run.rebuild_id,
        )
        if extraction_run is None:
            backlog_blocked += 1
            continue
        if extraction_run.run_kind.value == "material":
            ledger.update_item(
                run.rebuild_id,
                item.message_id,
                status=RebuildItemStatus.SKIPPED,
                skip_reason=RebuildSkipReason.MATERIAL_RUN_PRESENT,
                extraction_id=extraction_run.extraction_id,
                source_hash=extraction_run.source_hash,
            )
            scheduled += 1
            continue
        status, skip_reason, last_error = _classify_terminal_run(
            ExtractionRunSnapshot(
                extraction_id=extraction_run.extraction_id,
                status=extraction_run.status.value,
                outcome=extraction_run.outcome.value,
                attempts=extraction_run.attempts,
                committed_count=len(extraction_run.committed_record_ids),
                observed_count=extraction_run.observed_count,
                last_error=extraction_run.last_error,
                run_kind=extraction_run.run_kind.value,
            )
        )
        if status is RebuildItemStatus.SCHEDULED:
            queued = service.ensure_task_queued(
                run.account_id,
                message_id=extraction_run.message_id,
                extractor_version=extraction_run.extractor_version,
                source_hash=extraction_run.source_hash,
            )
            ledger.update_item(
                run.rebuild_id,
                item.message_id,
                status=RebuildItemStatus.SCHEDULED,
                source_hash=extraction_run.source_hash,
                extraction_id=extraction_run.extraction_id,
                last_error=None if queued else "run_not_queueable",
                now=now_text(),
            )
            scheduled += 1
            continue
        ledger.update_item(
            run.rebuild_id,
            item.message_id,
            status=status,
            skip_reason=skip_reason,
            extraction_id=extraction_run.extraction_id,
            source_hash=extraction_run.source_hash,
            committed_count=len(extraction_run.committed_record_ids),
            observed_count=extraction_run.observed_count,
            last_error=last_error,
        )
        scheduled += 1
    return scheduled, backlog_blocked


def _sync_inflight_items(
    service: AutomaticProfileService,
    ledger: SqliteProfileRebuildRepository,
    run: RebuildRunRecord,
) -> None:
    """把在途条目的真实管道状态同步回台账（幂等；崩溃恢复后同样成立）。"""

    extractor_version = service.extractor_version
    for item in ledger.list_items(
        run.rebuild_id, statuses=[RebuildItemStatus.SCHEDULED]
    ):
        if item.source_hash is None:
            ledger.update_item(
                run.rebuild_id,
                item.message_id,
                status=RebuildItemStatus.PENDING,
            )
            continue
        snapshot = ledger.read_extraction_run(
            run.account_id, item.message_id, extractor_version, item.source_hash
        )
        if snapshot is None:
            ledger.update_item(
                run.rebuild_id,
                item.message_id,
                status=RebuildItemStatus.PENDING,
            )
            continue
        status, skip_reason, last_error = _classify_terminal_run(snapshot)
        if status is not RebuildItemStatus.SCHEDULED:
            ledger.update_item(
                run.rebuild_id,
                item.message_id,
                status=status,
                skip_reason=skip_reason,
                extraction_id=snapshot.extraction_id,
                committed_count=snapshot.committed_count,
                observed_count=snapshot.observed_count,
                last_error=last_error,
            )
            continue
        task = ledger.read_task(
            run.account_id, item.message_id, extractor_version, item.source_hash
        )
        if task is None:
            service.ensure_task_queued(
                run.account_id,
                message_id=item.message_id,
                extractor_version=extractor_version,
                source_hash=item.source_hash,
            )
            continue
        claim = ledger.read_claim(PROFILE_REBUILD_QUEUE, task.task_id)
        if claim is not None and claim.next_retry_at is None and claim.status == "failed":
            # 重试预算耗尽：如实记为失败，不当作成功水位。
            ledger.update_item(
                run.rebuild_id,
                item.message_id,
                status=RebuildItemStatus.FAILED,
                last_error=snapshot.last_error or task.last_error or "retries_exhausted",
                committed_count=snapshot.committed_count,
                observed_count=snapshot.observed_count,
            )
            continue
        if claim is None:
            service.ensure_task_queued(
                run.account_id,
                message_id=item.message_id,
                extractor_version=extractor_version,
                source_hash=item.source_hash,
            )


_IDLE_TICK = "profile-extraction: 无待处理任务。"


def run_rebuild(
    work_dir: str | Path,
    *,
    extractor_factory: ExtractorFactory,
    max_ticks: int = DEFAULT_REBUILD_MAX_TICKS,
    now: datetime | None = None,
) -> dict[str, object]:
    """有界推进一次：调度 → 领取（至多 ``max_ticks`` 次模型调用）→ 同步。"""

    if max_ticks <= 0:
        raise ProfileRebuildError("max_ticks_invalid", "max_ticks 必须为正整数。")
    workspace = load_workspace(work_dir)
    database = open_working_database(workspace)
    try:
        ledger = SqliteProfileRebuildRepository(database, initialize=False)
        run = _load_run(ledger, workspace.rebuild_id)
        runtime = build_runtime(database, extractor_factory)
        service = runtime.service
        _check_resume_versions(
            run,
            extractor_version=service.extractor_version,
            policy_version=HISTORY_REBUILD_POLICY_VERSION,
        )
        if run.status is RebuildStatus.COMPLETED:
            progress = _progress(workspace.work_dir, ledger, run)
            return {
                "rebuild_id": run.rebuild_id,
                "completed": True,
                "ticks": 0,
                "max_ticks": max_ticks,
                "reached_budget": False,
                "progress": progress.as_dict(),
            }
        ledger.update_run(
            workspace.rebuild_id,
            status=RebuildStatus.RUNNING,
            clear_last_error=True,
            now=now_text(now),
        )
        _, backlog_blocked = _schedule_pending_items(database, service, ledger, run)
        ticks = 0
        while ticks < max_ticks:
            outcome = service.run_retry_tick()
            if outcome == _IDLE_TICK:
                break
            ticks += 1
        _schedule_pending_items(database, service, ledger, run)
        _sync_inflight_items(service, ledger, run)
        ledger.refresh_counters(workspace.rebuild_id, now=now_text(now))
        run = _load_run(ledger, workspace.rebuild_id)
        counts = ledger.count_items(run.rebuild_id)
        remaining = counts.get(RebuildItemStatus.PENDING.value, 0) + counts.get(
            RebuildItemStatus.SCHEDULED.value, 0
        )
        if remaining == 0:
            ledger.update_run(
                workspace.rebuild_id,
                status=RebuildStatus.COMPLETED,
                completed_at=run.completed_at or now_text(now),
                clear_last_error=True,
                now=now_text(now),
            )
        else:
            note = (
                "调度被账户背压拦截，剩余条目可稍后再次运行。"
                if backlog_blocked
                else "仍有在途/待定条目，再次运行将继续推进。"
            )
            ledger.update_run(
                workspace.rebuild_id,
                status=RebuildStatus.PAUSED,
                last_error=note,
                now=now_text(now),
            )
        run = _load_run(ledger, workspace.rebuild_id)
        progress = _progress(workspace.work_dir, ledger, run)
        return {
            "rebuild_id": run.rebuild_id,
            "completed": run.status is RebuildStatus.COMPLETED,
            "ticks": ticks,
            "max_ticks": max_ticks,
            "reached_budget": ticks >= max_ticks,
            "progress": progress.as_dict(),
        }
    finally:
        database.close()


# ----------------------------------------------------------------------
# 清理
# ----------------------------------------------------------------------


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
            "reconciliation.json",
            REBUILD_METADATA_FILENAME,
        ):
            target = base / name
            if target.exists():
                target.unlink()
                removed.append(str(target))
    return {"work_dir": str(base), "removed": removed, "purged": purge}


__all__ = [
    "DEFAULT_REBUILD_MAX_TICKS",
    "ProfileRebuildError",
    "RebuildMetadata",
    "RebuildWorkspace",
    "build_runtime",
    "cleanup_rebuild",
    "load_metadata",
    "load_workspace",
    "open_working_database",
    "rebuild_status",
    "run_rebuild",
    "start_rebuild",
]
