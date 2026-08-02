"""提醒调度器：受监督的到期任务分发循环（``BridGes scheduler``）。

ADR-0013 规定 QQ 邮件提醒由独立受监督的调度器处理。本模块交付调度器的
运行合同：真实进程、受监督循环、平滑停止、中文诊断，且不要求任何凭据
配置——未配置数据库时待机，缺失 SMTP/百炼凭据不阻止基础服务启动。

到期提醒的分发接缝是 ``dispatch_due_reminders``：它查询权威数据库中的
提醒数据表并按到期时间分发。提醒数据表与完整分发语义由后续 Issue（QQ
SMTP 任务提醒）交付；在数据表存在前，调度器每轮如实报告“无到期提醒任务”，
不做假成功。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from bridges.config import Settings
from bridges.persistence import PersistenceError, resolve_database_path
from bridges.runtime.loop import supervised_loop
from bridges.storage import BridgesDatabase, StorageError

#: 默认轮询间隔（秒）。
DEFAULT_SCHEDULER_INTERVAL_SECONDS = 60

#: 提醒数据表名（由后续 Issue 交付该表及其分发语义）。
REMINDERS_TABLE = "reminders"


def dispatch_due_reminders(database: BridgesDatabase) -> int:
    """分发已到期且启用的提醒，返回分发数量。

    提醒数据表尚不存在时返回 0——这是真实的空结果，不是假装成功；后续
    Issue 交付数据表后，分发逻辑在此接缝上扩展。
    """
    row = database.connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (REMINDERS_TABLE,),
    ).fetchone()
    if row is None:
        return 0
    # 数据表已存在时的到期分发语义由交付该表的 Issue 在下方接缝实现。
    return 0


class ReminderScheduler:
    """受监督的提醒调度器。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._database: BridgesDatabase | None = None
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

    def run_tick(self) -> str:
        """执行一轮调度检查并返回中文摘要；可重试错误只记录不退出。"""
        database = self._ensure_database()
        if database is None:
            assert self._idle_reason is not None
            return self._idle_reason
        if not database.health_check():
            return "error: 数据库当前不可查询，请检查数据目录。"
        dispatched = dispatch_due_reminders(database)
        return f"scheduler: 调度心跳正常，本轮分发到期提醒 {dispatched} 个。"

    def run_loop(
        self,
        *,
        interval: float = DEFAULT_SCHEDULER_INTERVAL_SECONDS,
        stop: threading.Event | None = None,
        emit: Callable[[str], None] = print,
    ) -> None:
        """受监督循环：每轮执行一次调度检查，收到停止信号后平滑退出。"""
        supervised_loop(
            tick=self.run_tick,
            stop=stop or threading.Event(),
            interval=interval,
            emit=emit,
        )
