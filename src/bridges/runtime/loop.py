"""受监督的后台循环：执行一轮工作，等待间隔或停止信号。

后台执行器与提醒调度器共用同一循环语义：``stop``（threading.Event）被
信号处理器设置后，循环在最近一个等待点退出；等待使用 ``Event.wait``
而不是 ``time.sleep``，保证停止信号立刻生效，不需要等满整个轮询间隔。
"""

from __future__ import annotations

import threading
from collections.abc import Callable


#: 每轮输出回调；默认打印到 stdout。
def supervised_loop(
    *,
    tick: Callable[[], str],
    stop: threading.Event,
    interval: float,
    emit: Callable[[str], None] = print,
) -> None:
    """执行监督循环，直到收到停止信号。

    先执行一轮 ``tick``，然后等待 ``interval`` 秒或停止信号；``stop`` 已
    置位时最多再执行一轮即退出，保证平滑停机关闭所有资源。
    """
    while True:
        emit(tick())
        if stop.wait(interval):
            break
