"""进行中生成的停止信号与活跃度跟踪（单一权威）。

把"生成是否活跃、如何停止"从 ChatService 散落的注册表、续期与陈旧
判断收进一个深模块：停止信号（``threading.Event``）、生成启动时刻、
增量续期与 TTL 陈旧判定共享同一把锁与同一数据源，杜绝调用方各自
维护互斥状态导致的行为漂移。停止语义只有一个入口：注册 → 续期 →
（停止时）设置信号 → 终态注销。
"""

from __future__ import annotations

import threading
import time

#: 流式生成在注册表中"活跃"的 TTL；超过后视为陈旧（进程重启/断流后
#: 恢复场景），读取收敛时不再豁免，落库为可重试错误。
STALE_STREAMING_TTL_SECONDS = 60.0


class GenerationLifecycle:
    """一条消息从预注册到终态的停止信号与活跃度生命周期。

    并发访问经内部锁串行化；``touch`` 每收到一个增量产出即续期，长流
    不会被误判僵死；``is_active`` 在 TTL 内返回 True，进程重启后注册表
    为空，遗留 streaming 消息一律不豁免（由读取方收敛）。
    """

    def __init__(self, stale_ttl_seconds: float = STALE_STREAMING_TTL_SECONDS) -> None:
        self._stale_ttl = stale_ttl_seconds
        #: message_id → (停止信号, 生成启动单调时刻)
        self._entries: dict[str, tuple[threading.Event, float]] = {}
        self._guard = threading.Lock()

    def register(self, message_id: str) -> threading.Event:
        """预注册一条生成的停止信号；已注册时返回既有信号。

        生成一经创建即视为活跃（预注册在 start/retry 阶段完成，读取
        陈旧收敛不会误伤进行中的流）。
        """
        with self._guard:
            entry = self._entries.get(message_id)
            if entry is not None:
                return entry[0]
            event = threading.Event()
            self._entries[message_id] = (event, time.monotonic())
            return event

    def touch(self, message_id: str) -> None:
        """刷新活跃时间戳：每个增量产出都续期，避免长时间流被误判僵死。"""
        with self._guard:
            entry = self._entries.get(message_id)
            if entry is not None:
                self._entries[message_id] = (entry[0], time.monotonic())

    def unregister(self, message_id: str) -> None:
        """终态后注销：释放停止信号与活跃跟踪。"""
        with self._guard:
            self._entries.pop(message_id, None)

    def signal_and_started(self, message_id: str) -> tuple[threading.Event, float] | None:
        """原子读取停止信号与生成启动的单调时刻；未注册返回 None。"""
        with self._guard:
            entry = self._entries.get(message_id)
        return entry

    def is_active(self, message_id: str) -> bool:
        """消息是否处于"进行中"的生成（注册且未超过 TTL）。"""
        with self._guard:
            entry = self._entries.get(message_id)
            if entry is None:
                return False
            return time.monotonic() - entry[1] < self._stale_ttl
