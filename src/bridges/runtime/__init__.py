"""BridGes 本地后台运行时：单实例锁、后台执行器与提醒调度器。

Issue 06 交付的桥梁：跨平台数据目录单实例锁（进程退出自动释放，异常终止
可安全恢复）、周期清理对象队列的后台执行器、以及受监督的提醒调度器进程。
源码 Conda/``.venv`` 与 Docker/Podman 容器共用同一套后台运行时语义。
"""

from __future__ import annotations

from bridges.runtime.executor import (
    DEFAULT_EXECUTOR_INTERVAL_SECONDS,
    BackgroundExecutor,
)
from bridges.runtime.lock import LOCK_FILENAME, DataDirectoryLock, RuntimeLockError
from bridges.runtime.loop import supervised_loop
from bridges.runtime.scheduler import (
    DEFAULT_SCHEDULER_INTERVAL_SECONDS,
    ReminderScheduler,
)

__all__ = [
    "DEFAULT_EXECUTOR_INTERVAL_SECONDS",
    "DEFAULT_SCHEDULER_INTERVAL_SECONDS",
    "BackgroundExecutor",
    "DataDirectoryLock",
    "LOCK_FILENAME",
    "ReminderScheduler",
    "RuntimeLockError",
    "supervised_loop",
]
