"""后台执行器：受监督的本地任务循环（``BridGes worker``）。

ADR-0013 定义的耗时任务（生成、索引、QQ 邮件提醒、清理）由独立受监督的
后台执行器与调度器处理。当前落地的后台任务：

- 文档摄取（Issue 17）：解析、哈希分块、向量化与版本化索引写入；
  重启后按领取租约自动恢复未完成任务，重复领取保持幂等；
- 对象清理：处理 ``pending_cleanup`` 记录与孤立文件。

- 配置了数据库：每轮先做摄取处理（含孤立摄取记录清理），再做对象清理，
  输出中文摘要；
- 未配置数据库或缺少加密密钥：待机循环，不崩溃——缺失凭据不阻止基础服务
  启动，也不假成功。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from bridges.ai import (
    CapabilityRegistry,
    CassetteStore,
    ModelGateway,
    QwenApiClient,
    QwenImageAdapter,
    QwenVisionAdapter,
    QwenWanAdapter,
)
from bridges.chat.repository import ConversationRepository
from bridges.config import Settings
from bridges.contracts.ai import (
    CapabilityKind,
    CapabilityRecord,
    CapabilityStatus,
    RetryPolicy,
)
from bridges.credentials.matrix import IMAGE_MODEL_ID, VIDEO_MODEL_ID
from bridges.credentials.probes import CapabilityProbeService
from bridges.credentials.store import EncryptedVolumeCredentialStore, OsCredentialStore
from bridges.identity.service import IdentityService
from bridges.image.service import ImageService
from bridges.ingestion.embedding import QwenEmbeddingPort
from bridges.ingestion.index import VersionedIndex
from bridges.ingestion.service import IngestionService
from bridges.lifecycle.deletion import DeletionService
from bridges.observability.service import ObservabilityService
from bridges.persistence import (
    PersistenceError,
    build_state_store,
    resolve_database_path,
)
from bridges.runtime.loop import supervised_loop
from bridges.storage import (
    BridgesDatabase,
    BridgesObjectRepository,
    EncryptedFileObjectStore,
    StorageError,
)
from bridges.video.service import VideoService

#: 默认轮询间隔（秒）。
DEFAULT_EXECUTOR_INTERVAL_SECONDS = 60


class BackgroundExecutor:
    """周期执行本地后台任务的执行器。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._repository: BridgesObjectRepository | None = None
        self._database: BridgesDatabase | None = None
        self._ingestion: IngestionService | None = None
        self._image: ImageService | None = None
        self._video: VideoService | None = None
        self._deletion: DeletionService | None = None
        self._idle_reason: str | None = None

    def _ensure_repository(self) -> BridgesObjectRepository | None:
        """惰性建立对象仓库；配置缺失或打开失败时给出中文待机原因。"""
        if self._repository is not None or self._idle_reason is not None:
            return self._repository
        settings = self._settings
        if settings.database_url is None or not settings.database_url.get_secret_value():
            self._idle_reason = (
                "worker: 未配置 BRIDGES_DATABASE_URL，后台执行器待机。"
                "配置数据库后重启以启用后台任务。"
            )
            return None
        if settings.secret_key is None or not settings.secret_key.get_secret_value():
            self._idle_reason = (
                "worker: 配置了数据库但缺少 BRIDGES_SECRET_KEY，"
                "无法打开加密对象库，后台执行器待机。"
            )
            return None
        try:
            path = Path(resolve_database_path(settings.database_url))
            database = BridgesDatabase(path)
            repository = BridgesObjectRepository(
                database,
                EncryptedFileObjectStore(
                    path.parent / "objects",
                    encryption_key=settings.secret_key,
                ),
            )
        except (StorageError, PersistenceError, ValueError) as exc:
            self._idle_reason = f"error: {exc}"
            return None
        self._database = database
        self._repository = repository
        return repository

    def _ensure_ingestion(self) -> IngestionService | None:
        """惰性建立摄取服务；与 API 进程共享同一数据目录与探测状态。"""
        if self._ingestion is not None or self._idle_reason is not None:
            return self._ingestion
        repository = self._ensure_repository()
        if repository is None:
            return None
        settings = self._settings
        database_url = settings.database_url
        if database_url is None or not database_url.get_secret_value():
            self._idle_reason = (
                "worker: 未配置 BRIDGES_DATABASE_URL，后台执行器待机。"
            )
            return None
        try:
            data_dir = Path(resolve_database_path(database_url)).parent
            state_store = build_state_store(
                database_url, encryption_key=settings.secret_key
            )
            region = settings.qwen_region
            workspace_id = settings.qwen_workspace_id
            cassette_dir = settings.qwen_cassette_dir
            record_mode = settings.qwen_record_cassettes
            credential_store = (
                EncryptedVolumeCredentialStore(data_dir)
                if settings.credential_backend == "encrypted-volume"
                else OsCredentialStore(data_dir=data_dir)
            )
            probe_service = CapabilityProbeService(
                state_store=state_store,
                region=region,
                workspace_id=workspace_id,
                cassette_dir=cassette_dir,
                record_mode=record_mode,
            )
            embedding = QwenEmbeddingPort(
                credential_store=credential_store,
                region=region,
                workspace_id=workspace_id,
                cassette_dir=cassette_dir,
                record_mode=record_mode,
            )
            assert self._database is not None
            self._ingestion = IngestionService(
                database=self._database,
                object_repository=repository,
                probe_service=probe_service,
                embedding=embedding,
                index=VersionedIndex(self._database, embedding),
            )
        except (StorageError, PersistenceError, ValueError) as exc:
            self._idle_reason = f"error: {exc}"
            return None
        return self._ingestion

    def _ensure_image_service(self) -> ImageService | None:
        """惰性建立图片任务处理服务（Issue 31）。

        与 API 进程共享同一数据目录：图片任务按租约领取、提交/轮询
        DashScope 云端任务、完成转存账户对象库并更新消息投影。缺少
        Qwen API Key 时待机（无凭据不假成功，任务保持排队等待用户
        处理）；API 进程与 worker 共享同一运行载体配置，正常情况下
        两者一致。
        """
        if self._image is not None or self._idle_reason is not None:
            return self._image
        repository = self._ensure_repository()
        if repository is None:
            return None
        settings = self._settings
        if settings.qwen_api_key is None or not settings.qwen_api_key.get_secret_value():
            self._idle_reason = (
                "worker: 未配置 Qwen API Key，图片任务处理待机。"
            )
            return None
        try:
            cassette_store = None
            if settings.qwen_cassette_dir is not None:
                cassette_store = CassetteStore(Path(settings.qwen_cassette_dir))
            client = QwenApiClient(
                api_key=settings.qwen_api_key,
                workspace_id=settings.qwen_workspace_id,
                region=settings.qwen_region,
                cassette_store=cassette_store,
                record_mode=settings.qwen_record_cassettes,
            )
            registry = CapabilityRegistry()
            registry.register(
                CapabilityRecord(
                    name="qwen_image",
                    version="1",
                    kind=CapabilityKind.MODEL,
                    vendor="qwen",
                    region="cn-beijing",
                    model_id=IMAGE_MODEL_ID,
                    input_schema_version="image-prompt-v1",
                    output_schema_version="image-task-v1",
                    status=CapabilityStatus.VERIFIED,
                    retry_policy=RetryPolicy(max_attempts=2, backoff_seconds=1.0),
                    prompt_version="2026-08-05",
                )
            )
            # 替代文本由核心视觉模型（qwen_vision，固定矩阵）自动生成：
            # 注册同一能力，失败时服务层确定性降级，不阻断生成完成。
            registry.register(
                CapabilityRecord(
                    name="qwen_vision",
                    version="1",
                    kind=CapabilityKind.MODEL,
                    vendor="qwen",
                    region="cn-beijing",
                    model_id="qwen3-vl-plus",
                    input_schema_version="image-vision-v1",
                    output_schema_version="vision-text-v1",
                    status=CapabilityStatus.VERIFIED,
                    retry_policy=RetryPolicy(max_attempts=2, backoff_seconds=1.0),
                    prompt_version="2026-08-05",
                )
            )
            gateway = ModelGateway(registry)
            gateway.register_adapter("qwen_image", "1", QwenImageAdapter(client))
            gateway.register_adapter("qwen_vision", "1", QwenVisionAdapter(client))
            assert self._database is not None
            self._image = ImageService(
                database=self._database,
                gateway=gateway,
                object_repository=repository,
                chat_repository=ConversationRepository(self._database),
                observability_service=ObservabilityService(),
            )
        except (StorageError, PersistenceError, ValueError) as exc:
            self._idle_reason = f"error: {exc}"
            return None
        return self._image

    def _ensure_video_service(self) -> VideoService | None:
        """惰性建立视频任务处理服务（Issue 32，ADR-0007 Wan 例外）。

        与 API 进程共享同一数据目录：视频任务按租约领取、提交/轮询
        DashScope 云端任务、完成转存账户对象库并更新消息投影。缺少
        Qwen API Key 时待机（无凭据不假成功，任务保持排队等待用户
        处理）；API 进程与 worker 共享同一运行载体配置，正常情况下
        两者一致。
        """
        if self._video is not None or self._idle_reason is not None:
            return self._video
        repository = self._ensure_repository()
        if repository is None:
            return None
        settings = self._settings
        if settings.qwen_api_key is None or not settings.qwen_api_key.get_secret_value():
            self._idle_reason = (
                "worker: 未配置 Qwen API Key，视频任务处理待机。"
            )
            return None
        try:
            cassette_store = None
            if settings.qwen_cassette_dir is not None:
                cassette_store = CassetteStore(Path(settings.qwen_cassette_dir))
            client = QwenApiClient(
                api_key=settings.qwen_api_key,
                workspace_id=settings.qwen_workspace_id,
                region=settings.qwen_region,
                cassette_store=cassette_store,
                record_mode=settings.qwen_record_cassettes,
            )
            registry = CapabilityRegistry()
            # Issue 32: Wan 是模型矩阵唯一非 Qwen 系列例外（ADR-0007），
            # 仍使用同一账户级百炼密钥，遵守单类别单快照合同。
            registry.register(
                CapabilityRecord(
                    name="qwen_wan",
                    version="1",
                    kind=CapabilityKind.MODEL,
                    vendor="wan",
                    region="cn-beijing",
                    model_id=VIDEO_MODEL_ID,
                    input_schema_version="video-prompt-v1",
                    output_schema_version="video-task-v1",
                    status=CapabilityStatus.VERIFIED,
                    retry_policy=RetryPolicy(max_attempts=2, backoff_seconds=1.0),
                    prompt_version="2026-08-05",
                )
            )
            gateway = ModelGateway(registry)
            gateway.register_adapter("qwen_wan", "1", QwenWanAdapter(client))
            assert self._database is not None
            self._video = VideoService(
                database=self._database,
                gateway=gateway,
                object_repository=repository,
                chat_repository=ConversationRepository(self._database),
                observability_service=ObservabilityService(),
            )
        except (StorageError, PersistenceError, ValueError) as exc:
            self._idle_reason = f"error: {exc}"
            return None
        return self._video

    def _ensure_deletion_service(self) -> DeletionService | None:
        """惰性建立账户删除重试服务（Issue 37）。

        与 API 进程共享同一数据目录与身份状态（StateStore）：重试失败
        状态的账户删除清理（对象物理文件、账户凭据与身份记录）。身份
        清理经 StateStore 持久化，API 进程运行期内存状态在重启后一致；
        API 进程的删除重试端点同样可经前端触发完整清理。
        """
        if self._deletion is not None or self._idle_reason is not None:
            return self._deletion
        repository = self._ensure_repository()
        if repository is None:
            return None
        settings = self._settings
        try:
            data_dir = Path(resolve_database_path(settings.database_url or "")).parent
            credential_store = (
                EncryptedVolumeCredentialStore(data_dir)
                if settings.credential_backend == "encrypted-volume"
                else OsCredentialStore(data_dir=data_dir)
            )
            smtp_credential_store = (
                EncryptedVolumeCredentialStore(data_dir, namespace="smtp")
                if settings.credential_backend == "encrypted-volume"
                else OsCredentialStore(data_dir=data_dir, namespace="smtp")
            )
            state_store = build_state_store(
                settings.database_url, encryption_key=settings.secret_key
            )
            identity_service = IdentityService(
                state_store=state_store, object_repository=repository
            )
            assert self._database is not None
            self._deletion = DeletionService(
                database=self._database,
                object_repository=repository,
                identity_service=identity_service,
                credential_store=credential_store,
                smtp_credential_store=smtp_credential_store,
                observability_service=ObservabilityService(),
            )
        except (StorageError, PersistenceError, ValueError) as exc:
            self._idle_reason = f"error: {exc}"
            return None
        return self._deletion

    def run_tick(self) -> str:
        """执行一轮后台任务并返回中文摘要；可重试错误只记录不退出。"""
        repository = self._ensure_repository()
        if repository is None:
            assert self._idle_reason is not None
            return self._idle_reason
        ingestion = self._ensure_ingestion()
        image = self._ensure_image_service()
        video = self._ensure_video_service()
        deletion = self._ensure_deletion_service()
        summaries: list[str] = []
        if ingestion is not None:
            try:
                summaries.append(ingestion.process_pending())
            except Exception as exc:  # noqa: BLE001 - 摄取失败记录但不退出循环
                summaries.append(f"worker: 摄取处理出错：{exc}")
        if image is not None:
            try:
                summaries.append(image.process_pending())
            except Exception as exc:  # noqa: BLE001 - 图片任务失败记录但不退出循环
                summaries.append(f"worker: 图片任务处理出错：{exc}")
        if video is not None:
            try:
                summaries.append(video.process_pending())
            except Exception as exc:  # noqa: BLE001 - 视频任务失败记录但不退出循环
                summaries.append(f"worker: 视频任务处理出错：{exc}")
        if deletion is not None:
            try:
                summaries.append(deletion.process_pending_retries())
            except Exception as exc:  # noqa: BLE001 - 删除重试失败记录但不退出循环
                summaries.append(f"worker: 账户删除重试出错：{exc}")
        try:
            cleaned = repository.run_pending_cleanups()
            orphans = repository.cleanup_orphans()
        except StorageError as exc:
            return f"error: {exc}"
        summaries.append(
            f"worker: 清理完成 {cleaned} 个待清理对象，移除 {orphans} 个孤立文件。"
        )
        return " ".join(summaries)

    def run_loop(
        self,
        *,
        interval: float = DEFAULT_EXECUTOR_INTERVAL_SECONDS,
        stop: threading.Event | None = None,
        emit: Callable[[str], None] = print,
    ) -> None:
        """受监督循环：每轮执行一次任务，收到停止信号后平滑退出。"""
        supervised_loop(
            tick=self.run_tick,
            stop=stop or threading.Event(),
            interval=interval,
            emit=emit,
        )
