"""E2E 后台执行器启动器（Issue 04 专用）。

Playwright webServer 要求每个条目有独立的就绪 URL（相同 URL 的条目
会被视为「已存在」而跳过启动）。本启动器把 ``BackgroundExecutor``
（摄取/对象清理/孤儿附件清理）放进后台线程，并暴露独立的 HTTP 健康
端点（默认 8027）供 Playwright 探测。与生产 ``BridGes start`` 的
api + worker 拓扑一致：E2E 的检索披露依赖文档真实入索引。

仅用于本地 E2E；生产 worker 走 ``bridges.cli.main worker``。
"""

from __future__ import annotations

import argparse
import functools
import http.server
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from bridges.config import get_settings
from bridges.runtime.executor import BackgroundExecutor

HTTP_PORT = 8027


class _HealthHandler(http.server.BaseHTTPRequestHandler):
    """就绪探测端点：``GET /health`` 在数据库就绪前返回 503。

    Issue 06：仅端口可连或基础 HTTP 200 不足以证明 worker 可用——必须等
    ``BackgroundExecutor`` 完成 ``initialize()`` 且 schema 就绪后才返回 200，
    Playwright 的 URL 轮询因此不会在数据库未迁移完成时开始用户测试。
    """

    def __init__(
        self,
        *args: object,
        ready_probe: Callable[[], bool],
        **kwargs: object,
    ) -> None:
        self._ready_probe = ready_probe
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]

    def do_GET(self) -> None:  # noqa: N802 - http.server 约定命名
        if self.path == "/health":
            ready = self._ready_probe()
            self.send_response(200 if ready else 503)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            payload = (
                '{"service":"e2e-worker","live":"pass","ready":"pass"}'
                if ready
                else '{"service":"e2e-worker","live":"pass","ready":"fail"}'
            )
            self.wfile.write(payload.encode("utf-8"))
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return


@contextmanager
def _health_server(ready_probe: Callable[[], bool]) -> Iterator[None]:
    handler = functools.partial(_HealthHandler, ready_probe=ready_probe)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", HTTP_PORT), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield
    finally:
        server.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="每轮后台任务的轮询间隔（秒）。",
    )
    args = parser.parse_args()

    # 与 CLI worker 同一语义：test 环境跳过全局 Key 硬门。
    get_settings()
    executor = BackgroundExecutor(get_settings())
    # Issue 06 启动契约：worker 在报告就绪/构造任何仓库前，先对配置指向的
    # 同一个 SQLite 数据库执行 initialize() 并校验 schema；失败时健康端点
    # 保持 503，Playwright 启动即明确失败而非带缺表继续服务。
    executor.ensure_database()
    if not executor.database_ready:
        print(
            f"e2e-worker: 数据库未就绪，后台执行器待机："
            f"{executor.idle_reason or '未知原因'}",
            flush=True,
        )
    else:
        print(
            "e2e-worker: 数据库 schema 就绪（版本与核心对象已校验）。",
            flush=True,
        )
    stop = threading.Event()

    def _run() -> None:
        executor.run_loop(
            interval=args.interval, stop=stop, emit=print
        )

    worker_thread = threading.Thread(target=_run, daemon=True)
    worker_thread.start()
    # 每次请求时求值（property 不能直接传入，否则只在启动时取一次布尔值）。
    with _health_server(lambda: executor.database_ready):
        try:
            while worker_thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            stop.set()


if __name__ == "__main__":
    main()
