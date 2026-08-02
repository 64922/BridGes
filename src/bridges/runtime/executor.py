"""后台执行器：受监督的本地清理任务循环（``BridGes worker``）。

ADR-0013 定义的耗时任务（生成、索引、QQ 邮件提醒、清理）由独立受监督的
后台执行器与调度器处理。当前已落地的后台任务是从删除标记产生的待清理对象
队列与孤立对象文件；后续任务（生成、索引）按同一受监督循环接缝扩展。

- 配置了数据库：每轮清理 ``pending_cleanup`` 记录与孤立文件，输出中文摘要；
- 未配置数据库或缺少加密密钥：待机循环，不崩溃——缺失凭据不阻止基础服务
  启动，也不假成功。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from bridges.config import Settings
from bridges.persistence import PersistenceError, resolve_database_path
from bridges.runtime.loop import supervised_loop
from bridges.storage import (
    BridgesDatabase,
    BridgesObjectRepository,
    EncryptedFileObjectStore,
    StorageError,
)

#: 默认轮询间隔（秒）。
DEFAULT_EXECUTOR_INTERVAL_SECONDS = 60


class BackgroundExecutor:
    """周期执行本地后台任务的执行器。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._repository: BridgesObjectRepository | None = None
        self._idle_reason: str | None = None

    def _ensure_repository(self) -> BridgesObjectRepository | None:
        """惰性建立对象仓库；配置缺失或打开失败时给出中文待机原因。"""
        if self._repository is not None or self._idle_reason is not None:
            return self._repository
        settings = self._settings
        if settings.database_url is None or not settings.database_url.get_secret_value():
            self._idle_reason = (
                "worker: 未配置 BRIDGES_DATABASE_URL，后台执行器待机。"
                "配置数据库后重启以启用后台清理。"
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
        self._repository = repository
        return repository

    def run_tick(self) -> str:
        """执行一轮后台任务并返回中文摘要；可重试错误只记录不退出。"""
        repository = self._ensure_repository()
        if repository is None:
            assert self._idle_reason is not None
            return self._idle_reason
        try:
            cleaned = repository.run_pending_cleanups()
            orphans = repository.cleanup_orphans()
        except StorageError as exc:
            return f"error: {exc}"
        return f"worker: 清理完成 {cleaned} 个待清理对象，移除 {orphans} 个孤立文件。"

    def run_loop(
        self,
        *,
        interval: float = DEFAULT_EXECUTOR_INTERVAL_SECONDS,
        stop: threading.Event | None = None,
        emit: Callable[[str], None] = print,
    ) -> None:
        """受监督循环：每轮执行一次清理，收到停止信号后平滑退出。"""
        supervised_loop(
            tick=self.run_tick,
            stop=stop or threading.Event(),
            interval=interval,
            emit=emit,
        )
