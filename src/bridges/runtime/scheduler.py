"""提醒调度器：受监督的到期提醒分发循环（``BridGes scheduler``）。

ADR-0013 规定 QQ 邮件提醒由独立受监督的调度器处理；ADR-0019 规定
24 小时有限补发语义。本模块交付调度器的运行合同：真实进程、受监督
循环、平滑停止、中文诊断，且不要求任何凭据配置——未配置数据库时
待机，缺失 SMTP 授权码不阻止基础服务启动。

到期分发由 :class:`ReminderService.process_due` 实现（Issue 33 交付
数据表与完整分发语义：一次性/重复/时区/暂停/取消/有限重试/24 小时
补发/投递记录），调度器负责惰性构造服务并与 API 进程共享同一数据
目录与凭据存储命名空间（``smtp``），授权码绝不进入调度器日志。
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
from bridges.observability.service import ObservabilityService
from bridges.persistence import PersistenceError, resolve_database_path
from bridges.reminder.service import ReminderService
from bridges.reminder.smtp import QqMailGateway
from bridges.runtime.loop import supervised_loop
from bridges.storage import BridgesDatabase, StorageError

#: 默认轮询间隔（秒）。
DEFAULT_SCHEDULER_INTERVAL_SECONDS = 60


class ReminderScheduler:
    """受监督的提醒调度器。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._database: BridgesDatabase | None = None
        self._reminder: ReminderService | None = None
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

    def _ensure_reminder_service(self) -> ReminderService | None:
        """惰性构造提醒服务；与 API 进程共享数据目录与 smtp 凭据命名空间。"""
        if self._reminder is not None or self._idle_reason is not None:
            return self._reminder
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
            self._reminder = ReminderService(
                database=database,
                credential_store=credential_store,
                observability_service=ObservabilityService(),
                # 调度进程只按提醒行内冻结的 qq_email 投递，不读身份服务。
                qq_email_provider=lambda _account_id: "",
                gateway=QqMailGateway(
                    smtp_host=settings.smtp_host,
                    smtp_port=settings.smtp_port,
                    smtp_starttls=settings.smtp_starttls,
                    smtp_plain=settings.smtp_plain,
                    imap_host=settings.imap_host,
                    imap_port=settings.imap_port,
                    imap_plain=settings.imap_plain,
                ),
            )
        except (StorageError, PersistenceError, ValueError) as exc:
            self._idle_reason = f"error: {exc}"
            return None
        return self._reminder

    def run_tick(self) -> str:
        """执行一轮提醒分发并返回中文摘要；可重试错误只记录不退出。"""
        database = self._ensure_database()
        if database is None:
            assert self._idle_reason is not None
            return self._idle_reason
        if not database.health_check():
            return "error: 数据库当前不可查询，请检查数据目录。"
        service = self._ensure_reminder_service()
        if service is None:
            assert self._idle_reason is not None
            return self._idle_reason
        try:
            summary = service.process_due()
        except Exception as exc:  # noqa: BLE001 - 单轮错误记录但不退出循环
            return f"scheduler: 本轮提醒处理出错：{exc}"
        # 摘要保持「调度心跳正常」前缀（运行合同测试断言），
        # 追加本轮投递/失败/跳过/补发计数。
        return f"scheduler: 调度心跳正常，{summary.removeprefix('scheduler: ')}"

    def run_loop(
        self,
        *,
        interval: float = DEFAULT_SCHEDULER_INTERVAL_SECONDS,
        stop: threading.Event | None = None,
        emit: Callable[[str], None] = print,
    ) -> None:
        """受监督循环：每轮执行一次提醒分发，收到停止信号后平滑退出。"""
        supervised_loop(
            tick=self.run_tick,
            stop=stop or threading.Event(),
            interval=interval,
            emit=emit,
        )
