"""账户隔离的专用 Chrome 生命周期；不读取或导出个人浏览器凭据。"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class TiebaBrowserError(RuntimeError):
    """只携带稳定错误码，不回显上游页面或浏览器内部日志。"""

    def __init__(self, code: str, message: str, *, excluded_wait_ms: int = 0) -> None:
        super().__init__(message)
        self.code = code
        self.excluded_wait_ms = max(0, excluded_wait_ms)


@dataclass(eq=False)
class _BrowserWorker:
    process: subprocess.Popen[str]
    output: queue.Queue[str | None]


def profile_path(data_dir: Path, account_id: str) -> Path:
    """目录由服务端认证账户生成，账户标识不能成为路径片段。"""
    digest = hashlib.sha256(account_id.encode()).hexdigest()
    return data_dir / "tieba-browser" / digest


class TiebaBrowserRuntime:
    """按账户隔离、串行复用浏览器上下文；登录等待不消耗工作截止。"""

    _MAX_WORKERS = 4

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._workers: dict[str, _BrowserWorker] = {}
        self._account_locks: dict[str, threading.Lock] = {}
        self._last_used: dict[str, float] = {}
        self._starting_workers = 0
        self._lock = threading.RLock()
        self._closed = False

    def request(
        self,
        account_id: str,
        payload: dict[str, Any],
        *,
        stop_event: object | None = None,
        deadline: float | None = None,
    ) -> dict[str, Any]:
        profile = profile_path(self._data_dir, account_id)
        checker = getattr(stop_event, "is_set", None)
        if callable(checker) and checker():
            raise TiebaBrowserError("tieba_browser_cancelled", "已停止贴吧浏览器读取。")
        if deadline is not None and deadline <= time.monotonic():
            raise TiebaBrowserError("tieba_browser_timeout", "贴吧浏览器读取超时。")
        with self._lock:
            account_lock = self._account_locks.setdefault(account_id, threading.Lock())
        lock_timeout = max(0.1, deadline - time.monotonic()) if deadline else 25
        acquired = account_lock.acquire(timeout=lock_timeout)
        if not acquired:
            raise TiebaBrowserError("tieba_browser_busy", "该账户的贴吧会话正在使用，请稍后重试。")
        try:
            if deadline is not None and deadline <= time.monotonic():
                raise TiebaBrowserError(
                    "tieba_browser_timeout", "等待账户专用会话超时，请稍后重试。"
                )
            profile.mkdir(parents=True, exist_ok=True)
            worker = self._worker_for(account_id, profile, deadline=deadline)
            if deadline is not None and deadline <= time.monotonic():
                raise TiebaBrowserError("tieba_browser_timeout", "贴吧浏览器读取超时。")
            self._send(worker, payload)
            try:
                result = self._receive(
                    worker, stop_event, deadline or time.monotonic() + 25
                )
            except TiebaBrowserError as error:
                with self._lock:
                    self._last_used[account_id] = time.monotonic()
                if error.code in {
                    "tieba_browser_cancelled",
                    "tieba_browser_timeout",
                    "tieba_browser_unavailable",
                }:
                    self._drop_worker(account_id, worker)
                raise
            with self._lock:
                self._last_used[account_id] = time.monotonic()
            return result
        finally:
            account_lock.release()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            workers = tuple(self._workers.values())
            self._workers.clear()
            self._last_used.clear()
        for worker in workers:
            self._dispose(worker)

    def _worker_for(
        self, account_id: str, profile: Path, *, deadline: float | None
    ) -> _BrowserWorker:
        stale: _BrowserWorker | None = None
        evicted: _BrowserWorker | None = None
        evicted_lock: threading.Lock | None = None
        with self._lock:
            if self._closed:
                raise TiebaBrowserError("tieba_browser_busy", "贴吧浏览器运行时已关闭。")
            existing = self._workers.get(account_id)
            if existing is not None and existing.process.poll() is None:
                return existing
            if existing is not None:
                stale = existing
                self._workers.pop(account_id, None)
                self._last_used.pop(account_id, None)
            if len(self._workers) + self._starting_workers >= self._MAX_WORKERS:
                idle = self._take_idle_worker_locked()
                if idle is None:
                    raise TiebaBrowserError(
                        "tieba_browser_busy", "四个账户专用浏览器均在使用，请稍后重试。"
                    )
                evicted, evicted_lock = idle
            self._starting_workers += 1

        reserved = True
        worker: _BrowserWorker | None = None
        closed = False
        try:
            if stale is not None:
                self._dispose(stale)
            if evicted is not None:
                self._dispose(evicted)
            if deadline is not None and deadline <= time.monotonic():
                raise TiebaBrowserError(
                    "tieba_browser_timeout", "等待账户专用会话超时，请稍后重试。"
                )
            worker = self._spawn(profile)
            with self._lock:
                self._starting_workers -= 1
                reserved = False
                closed = self._closed
                if not closed:
                    self._workers[account_id] = worker
                    self._last_used[account_id] = time.monotonic()
            if closed:
                self._dispose(worker)
                raise TiebaBrowserError(
                    "tieba_browser_busy", "贴吧浏览器运行时已关闭。"
                )
            return worker
        finally:
            if reserved:
                with self._lock:
                    self._starting_workers -= 1
            if evicted_lock is not None:
                evicted_lock.release()

    def _take_idle_worker_locked(
        self,
    ) -> tuple[_BrowserWorker, threading.Lock] | None:
        for account_id in sorted(
            self._workers, key=lambda key: self._last_used.get(key, 0)
        ):
            account_lock = self._account_locks.get(account_id)
            worker = self._workers.get(account_id)
            if account_lock is None or worker is None or not account_lock.acquire(blocking=False):
                continue
            self._workers.pop(account_id, None)
            self._last_used.pop(account_id, None)
            return worker, account_lock
        return None

    def _spawn(self, profile: Path) -> _BrowserWorker:
        process = subprocess.Popen(
            [sys.executable, "-m", "bridges.tieba.browser_worker", str(profile)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        output: queue.Queue[str | None] = queue.Queue()
        worker = _BrowserWorker(process=process, output=output)
        stdout = process.stdout
        assert stdout is not None

        def receive_lines() -> None:
            try:
                for line in stdout:
                    output.put(line[:100_000])
            except (OSError, ValueError):
                pass
            finally:
                output.put(None)

        threading.Thread(target=receive_lines, daemon=True).start()
        return worker

    def _send(self, worker: _BrowserWorker, payload: dict[str, Any]) -> None:
        if worker.process.poll() is not None or worker.process.stdin is None:
            raise TiebaBrowserError("tieba_browser_unavailable", "专用浏览器进程已退出。")
        try:
            worker.process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            worker.process.stdin.flush()
        except (OSError, ValueError):
            raise TiebaBrowserError(
                "tieba_browser_unavailable", "专用浏览器进程未能接收读取请求。"
            ) from None

    def _receive(
        self,
        worker: _BrowserWorker,
        stop_event: object | None,
        deadline: float,
    ) -> dict[str, Any]:
        auth_wait_started: float | None = None
        auth_wait_total = 0.0
        while True:
            checker = getattr(stop_event, "is_set", None)
            if callable(checker) and checker():
                raise TiebaBrowserError("tieba_browser_cancelled", "已停止贴吧浏览器读取。")
            now = time.monotonic()
            if auth_wait_started is None and now >= deadline:
                raise TiebaBrowserError("tieba_browser_timeout", "贴吧浏览器读取超时，请稍后重试。")
            if auth_wait_started is not None and now - auth_wait_started >= 600:
                auth_wait_total += now - auth_wait_started
                raise TiebaBrowserError(
                    "tieba_browser_auth_interrupted",
                    "登录或验证等待超时；任务已保留，请完成后说“继续”。",
                    excluded_wait_ms=int(auth_wait_total * 1000),
                )
            try:
                line = worker.output.get(timeout=0.1)
            except queue.Empty:
                continue
            if line is None:
                raise TiebaBrowserError(
                    "tieba_browser_unavailable", "专用浏览器未返回可读取内容。"
                )
            try:
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError
            except (ValueError, TypeError):
                raise TiebaBrowserError(
                    "tieba_browser_unavailable", "专用浏览器未返回可读取内容。"
                ) from None
            if value.get("error"):
                if auth_wait_started is not None:
                    auth_wait_total += time.monotonic() - auth_wait_started
                    value["excluded_wait_ms"] = int(auth_wait_total * 1000)
                raise TiebaBrowserError(
                    str(value["error"]),
                    str(value["message"]),
                    excluded_wait_ms=int(value.get("excluded_wait_ms") or 0),
                )
            if value.get("waiting_for_auth"):
                if auth_wait_started is None:
                    auth_wait_started = time.monotonic()
                continue
            if auth_wait_started is not None:
                waited = time.monotonic() - auth_wait_started
                auth_wait_total += waited
                deadline += waited
                value["excluded_wait_ms"] = int(auth_wait_total * 1000)
            return value

    def _drop_worker(self, account_id: str, worker: _BrowserWorker) -> None:
        with self._lock:
            if self._workers.get(account_id) is worker:
                self._workers.pop(account_id, None)
                self._last_used.pop(account_id, None)
        self._dispose(worker)

    def _dispose(self, worker: _BrowserWorker) -> None:
        process = worker.process
        if process.poll() is None:
            try:
                if process.stdin is not None:
                    process.stdin.write("close\n")
                    process.stdin.flush()
                process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                # 仅终止本运行时启动的进程树，避免 Chrome 在取消后占用专用目录。
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                        timeout=5,
                    )
                else:
                    process.terminate()
                process.wait(timeout=5)
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                stream.close()


__all__ = ["TiebaBrowserError", "TiebaBrowserRuntime", "profile_path"]
