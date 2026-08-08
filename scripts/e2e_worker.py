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
import http.server
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

from bridges.config import get_settings
from bridges.runtime.executor import BackgroundExecutor

HTTP_PORT = 8027


class _HealthHandler(http.server.BaseHTTPRequestHandler):
    """就绪探测端点：``GET /health`` 返回 200。"""

    def do_GET(self) -> None:  # noqa: N802 - http.server 约定命名
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"service":"e2e-worker","live":"pass"}')
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return


@contextmanager
def _health_server() -> Iterator[None]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", HTTP_PORT), _HealthHandler)
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
    stop = threading.Event()

    def _run() -> None:
        BackgroundExecutor(get_settings()).run_loop(
            interval=args.interval, stop=stop, emit=print
        )

    worker_thread = threading.Thread(target=_run, daemon=True)
    worker_thread.start()
    with _health_server():
        try:
            while worker_thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            stop.set()


if __name__ == "__main__":
    main()
