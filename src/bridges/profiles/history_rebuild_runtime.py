"""画像工单 17：历史重整的工作区、运行时装配与抽取器工厂。

工作目录布局（由 ``rebuild.json`` 锁定重整标识）：

    <work_dir>/rebuild.json            批次元数据（标识与版本绑定）
    <work_dir>/preview.json            预览清单副本（审计与漂移复核）
    <work_dir>/history.db              已验证备份（恢复前提）
    <work_dir>/working/bridges.db      工作副本（台账与提取写入同库）
    <work_dir>/reconciliation.json     对账报告（通过后写 reconciled_at）

运行时装配与组合根同一接线（复用 ``bridges.api.app_support`` 的读取
适配器，避免 profiles→api 模块级循环）；抽取器的模型适配部分由
``ExtractorFactory`` 注入，测试为确定性规则抽取器、生产为网关抽取器。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bridges.profiles.automatic import (
    AUTOMATIC_EXTRACTOR_VERSION,
    PROFILE_REBUILD_QUEUE,
    AutomaticProfileExtractor,
    AutomaticProfileService,
    NeighborWindowReader,
)
from bridges.profiles.history_rebuild_contracts import (
    REBUILD_BACKUP_FILENAME,
    REBUILD_METADATA_FILENAME,
    REBUILD_PREVIEW_FILENAME,
    REBUILD_WORKING_DB_FILENAME,
    REBUILD_WORKING_DIRNAME,
    ProfileRebuildError,
)
from bridges.profiles.signals import ProfileSignalClassifier
from bridges.storage.database import BridgesDatabase

if TYPE_CHECKING:
    from bridges.chat.repository import ConversationRepository
    from bridges.profiles.atomic import AtomicProfileService
    from bridges.profiles.overview import ProfileOverviewService

#: 抽取器工厂：由运行库状态装配模型适配部分；前文窗口与旧记忆参考读取器
#: 由运行库绑定后注入，保证与在线装配同一语义。
ExtractorFactory = Callable[
    [BridgesDatabase, NeighborWindowReader | None, Callable[[str, str], list[str]] | None],
    AutomaticProfileExtractor,
]


def rebuild_extractor_version() -> str:
    """重整使用的抽取器版本（与适配器实现无关，仅由版本常量决定）。"""

    return f"{AUTOMATIC_EXTRACTOR_VERSION}+signal-{ProfileSignalClassifier().version}"


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


def write_metadata(path: Path, metadata: RebuildMetadata) -> None:
    path.write_text(
        json.dumps(metadata.as_dict(), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def metadata_path(work_dir: str | Path) -> Path:
    return Path(work_dir) / REBUILD_METADATA_FILENAME


def load_workspace(work_dir: str | Path) -> RebuildWorkspace:
    """读取工作目录元数据；缺失或损坏显式失败，不猜测。"""

    base = Path(work_dir)
    metadata_file = metadata_path(base)
    if not base.exists():
        raise ProfileRebuildError("work_dir_missing", f"重整工作目录不存在：{base}")
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

    召回索引与在线一致为读时同步 + 版本/哈希过滤的派生数据：维护运行
    不承载对话召回读取，因此不挂载召回读端口；工作副本上的陈旧索引行
    由对账如实计数并在切换后的首次召回归位。
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
    from bridges.profiles.automatic import SqliteAutomaticProfileRepository
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


__all__ = [
    "ExtractorFactory",
    "RebuildMetadata",
    "RebuildRuntime",
    "RebuildWorkspace",
    "build_runtime",
    "load_workspace",
    "metadata_path",
    "open_working_database",
    "rebuild_extractor_version",
    "write_metadata",
]
