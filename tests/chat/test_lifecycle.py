"""候选 1：GenerationLifecycle 停止信号与活跃度跟踪的独立单测。

覆盖注册/续期/注销/TTL 陈旧判定；这些规则此前散落在 ChatService
私有方法中，收敛为独立模块后可在不启动数据库与网关的情况下直测。
"""

from __future__ import annotations

import threading
import time

from bridges.chat.lifecycle import GenerationLifecycle


def test_register_returns_stable_signal_and_is_active() -> None:
    lifecycle = GenerationLifecycle()
    first = lifecycle.register("m-1")
    assert lifecycle.register("m-1") is first  # 幂等：返回同一信号
    assert lifecycle.is_active("m-1")
    signal, started = lifecycle.signal_and_started("m-1")
    assert signal is first
    assert started is not None and started > 0


def test_touch_extends_activity_beyond_ttl() -> None:
    lifecycle = GenerationLifecycle(stale_ttl_seconds=0.05)
    lifecycle.register("m-1")
    for _ in range(5):
        time.sleep(0.03)
        lifecycle.touch("m-1")
        assert lifecycle.is_active("m-1")  # 每次续期后仍活跃


def test_is_active_false_after_ttl_without_touch() -> None:
    lifecycle = GenerationLifecycle(stale_ttl_seconds=0.05)
    lifecycle.register("m-1")
    assert lifecycle.is_active("m-1")
    time.sleep(0.1)
    assert not lifecycle.is_active("m-1")


def test_unregister_removes_signal_and_activity() -> None:
    lifecycle = GenerationLifecycle()
    lifecycle.register("m-1")
    lifecycle.unregister("m-1")
    assert lifecycle.signal_and_started("m-1") is None
    assert not lifecycle.is_active("m-1")


def test_unknown_message_is_not_active() -> None:
    lifecycle = GenerationLifecycle()
    assert lifecycle.signal_and_started("m-999") is None
    assert not lifecycle.is_active("m-999")


def test_signal_set_propagates_to_stream_reads() -> None:
    """停止语义：设置信号后，流侧读取同一 Event 能感知停止。"""
    lifecycle = GenerationLifecycle()
    event = lifecycle.register("m-1")
    stopped: list[bool] = []

    def waiter() -> None:
        stopped.append(event.wait(timeout=1.0))

    thread = threading.Thread(target=waiter)
    thread.start()
    signal, _ = lifecycle.signal_and_started("m-1")
    assert signal is not None
    signal.set()
    thread.join(timeout=2.0)
    assert stopped == [True]
