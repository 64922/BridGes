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
from pathlib import Path
from typing import Any


class TiebaBrowserError(RuntimeError):
    """只携带稳定错误码，不回显上游页面或浏览器内部日志。"""

    def __init__(self, code: str, message: str, *, excluded_wait_ms: int = 0) -> None:
        super().__init__(message)
        self.code = code
        self.excluded_wait_ms = max(0, excluded_wait_ms)


def profile_path(data_dir: Path, account_id: str) -> Path:
    """目录由服务端认证账户生成，账户标识不能成为路径片段。"""
    digest = hashlib.sha256(account_id.encode()).hexdigest()
    return data_dir / "tieba-browser" / digest


class TiebaBrowserRuntime:
    """按账户隔离并串行复用专用浏览器配置；登录等待不消耗工作截止。"""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._setups: dict[str, subprocess.Popen[str]] = {}
        self._active: set[subprocess.Popen[str]] = set()
        self._account_locks: dict[str, threading.Lock] = {}
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
            process = self._spawn(profile, payload)
            try:
                return self._receive(process, stop_event, deadline or time.monotonic() + 25)
            finally:
                self._dispose(process)
        finally:
            account_lock.release()

    def open(self, account_id: str) -> None:
        with self._lock:
            prior = self._setups.get(account_id)
            if prior is not None and prior.poll() is None:
                return
            profile = profile_path(self._data_dir, account_id)
            profile.mkdir(parents=True, exist_ok=True)
            process = self._spawn(profile, {"action": "setup"})
            try:
                self._receive(process, None, time.monotonic() + 25)
            except TiebaBrowserError:
                self._dispose(process)
                raise
            self._setups[account_id] = process

    def complete(self, account_id: str) -> None:
        with self._lock:
            process = self._setups.pop(account_id, None)
        if process is not None:
            self._dispose(process)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            active = tuple(self._active)
            self._setups.clear()
        for process in active:
            self._dispose(process)

    def _spawn(self, profile: Path, payload: dict[str, Any]) -> subprocess.Popen[str]:
        with self._lock:
            self._active = {p for p in self._active if p.poll() is None}
            if self._closed or len(self._active) >= 4:
                raise TiebaBrowserError("tieba_browser_busy", "贴吧浏览器正在使用，请稍后重试。")
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
            self._active.add(process)
            assert process.stdin is not None
            process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            process.stdin.flush()
            return process

    def _receive(
        self,
        process: subprocess.Popen[str],
        stop_event: object | None,
        deadline: float,
    ) -> dict[str, Any]:
        output: queue.Queue[str] = queue.Queue()
        assert process.stdout is not None
        stream = process.stdout

        def receive_line() -> None:
            try:
                for line in stream:
                    output.put(line[:100_000])
            except (OSError, ValueError):
                pass
            finally:
                output.put("")

        threading.Thread(target=receive_line, daemon=True).start()
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
                line = output.get(timeout=0.1)
            except queue.Empty:
                continue
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
                    str(value["error"]), str(value["message"]),
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
                auth_wait_started = None
                value["excluded_wait_ms"] = int(auth_wait_total * 1000)
            return value

    def _dispose(self, process: subprocess.Popen[str]) -> None:
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
        with self._lock:
            self._active.discard(process)
