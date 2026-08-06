"""BridGes 本地后台运行时：单实例锁、后台执行器与提醒调度器。

Issue 06 交付的桥梁：跨平台数据目录单实例锁（进程退出自动释放，异常终止
可安全恢复）、周期清理对象队列的后台执行器、以及受监督的提醒调度器进程。
源码 Conda/``.venv`` 与 Docker/Podman 容器共用同一套后台运行时语义。

``executor``/``scheduler`` 采用惰性导出（PEP 562）：它们依赖多个业务
子系统（chat/ingestion/image/...），而 Issue 43 起各子系统反向依赖
``runtime.queue``——顶层导入会让「首次导入 runtime 包」拉入整棵依赖树
并在部分初始化时形成循环。锁与循环语义（轻量、无反向依赖）保持顶层导入。
"""

from __future__ import annotations

from typing import Any

from bridges.runtime.lock import LOCK_FILENAME, DataDirectoryLock, RuntimeLockError
from bridges.runtime.loop import supervised_loop

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


def __getattr__(name: str) -> Any:
    """惰性导出重依赖模块，避免 runtime 包初始化期间的循环导入。"""
    if name == "BackgroundExecutor":
        from bridges.runtime.executor import BackgroundExecutor

        return BackgroundExecutor
    if name == "DEFAULT_EXECUTOR_INTERVAL_SECONDS":
        from bridges.runtime.executor import DEFAULT_EXECUTOR_INTERVAL_SECONDS

        return DEFAULT_EXECUTOR_INTERVAL_SECONDS
    if name == "ReminderScheduler":
        from bridges.runtime.scheduler import ReminderScheduler

        return ReminderScheduler
    if name == "DEFAULT_SCHEDULER_INTERVAL_SECONDS":
        from bridges.runtime.scheduler import DEFAULT_SCHEDULER_INTERVAL_SECONDS

        return DEFAULT_SCHEDULER_INTERVAL_SECONDS
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
