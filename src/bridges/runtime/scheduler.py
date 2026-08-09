"""提醒调度器：受监督的到期提醒分发循环（``BridGes scheduler``）。

ADR-0013 规定 QQ 邮件提醒由独立受监督的调度器处理；ADR-0019 规定
24 小时有限补发语义。本模块交付调度器的运行合同：真实进程、受监督
循环、平滑停止、中文诊断，且不要求任何凭据配置——未配置数据库时
待机，缺失 SMTP 授权码不阻止基础服务启动。

提醒能力已按 Issue 03 退役。调度器保留为兼容期清理进程：只负责幂等
取消遗留提醒、结束验证状态和清除 ``smtp`` 命名空间授权码，绝不构造
提醒服务、读取邮件内容或发送任何消息。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from bridges.config import Settings
from bridges.credentials.store import (
    EncryptedVolumeCredentialStore,
    OsCredentialStore,
)
from bridges.persistence import PersistenceError, resolve_database_path
from bridges.retirement import run_reminder_retirement
from bridges.runtime.loop import supervised_loop
from bridges.storage import BridgesDatabase, StorageError

#: 默认轮询间隔（秒）。
DEFAULT_SCHEDULER_INTERVAL_SECONDS = 60


class ReminderScheduler:
    """受监督的提醒调度器。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._database: BridgesDatabase | None = None
        self._credential_store: OsCredentialStore | EncryptedVolumeCredentialStore | None = None
        self._idle_reason: str | None = None

    def _ensure_database(self) -> BridgesDatabase | None:
        """惰性连接权威数据库；配置缺失或打开失败时给出中文待机原因。"""
        if self._database is not None or self._idle_reason is not None:
            return self._database
        settings = self._settings
        if settings.database_url is None or not settings.database_url.get_secret_value():
            self._idle_reason = (
                "scheduler: 未配置 BRIDGES_DATABASE_URL，提醒调度器待机。"
                "配置数据库后重启以启用提醒调度。"
            )
            return None
        try:
            path = Path(resolve_database_path(settings.database_url))
            database = BridgesDatabase(path)
            database.initialize()
        except (StorageError, PersistenceError, ValueError) as exc:
            self._idle_reason = f"error: {exc}"
            return None
        self._database = database
        return database

    def _ensure_credential_store(
        self,
    ) -> OsCredentialStore | EncryptedVolumeCredentialStore | None:
        """惰性构造 smtp 凭据存储，仅用于退役清理，不用于投递。"""
        if self._credential_store is not None or self._idle_reason is not None:
            return self._credential_store
        database = self._ensure_database()
        if database is None:
            return None
        settings = self._settings
        try:
            data_dir = Path(
                resolve_database_path(settings.database_url or "")
            ).parent
            credential_store = (
                EncryptedVolumeCredentialStore(data_dir, namespace="smtp")
                if settings.credential_backend == "encrypted-volume"
                else OsCredentialStore(data_dir=data_dir, namespace="smtp")
            )
            self._credential_store = credential_store
        except (StorageError, PersistenceError, ValueError) as exc:
            self._idle_reason = f"error: {exc}"
            return None
        return self._credential_store

    def run_tick(self) -> str:
        """执行一轮提醒分发并返回中文摘要；可重试错误只记录不退出。"""
        database = self._ensure_database()
        if database is None:
            assert self._idle_reason is not None
            return self._idle_reason
        if not database.health_check():
            return "error: 数据库当前不可查询，请检查数据目录。"
        credential_store = self._ensure_credential_store()
        if credential_store is None:
            assert self._idle_reason is not None
            return self._idle_reason
        try:
            report = run_reminder_retirement(
                database=database,
                credential_store=credential_store,
            )
        except Exception as exc:  # noqa: BLE001 - 单轮错误记录但不退出循环
            return f"scheduler: 退役清理可重试失败：{exc}"
        return (
            "scheduler: 调度心跳正常，学习提醒已退役，"
            f"遗留提醒 {report['reminders_retired']} 个，"
            f"凭据清除失败 {report['credential_failures']} 个，未发送新提醒。"
        )

    def run_loop(
        self,
        *,
        interval: float = DEFAULT_SCHEDULER_INTERVAL_SECONDS,
        stop: threading.Event | None = None,
        emit: Callable[[str], None] = print,
    ) -> None:
        """受监督循环：每轮只执行退役清理，绝不分发提醒。"""
        supervised_loop(
            tick=self.run_tick,
            stop=stop or threading.Event(),
            interval=interval,
            emit=emit,
        )
