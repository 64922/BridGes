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

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def profile_path(data_dir: Path, account_id: str) -> Path:
    """目录由服务端认证账户生成，账户标识不能成为路径片段。"""
    digest = hashlib.sha256(account_id.encode()).hexdigest()
    return data_dir / "tieba-browser" / digest


class TiebaBrowserRuntime:
    """一次读取一个子进程；人工登录窗口在完成或十分钟后关闭。"""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._setups: dict[str, subprocess.Popen[str]] = {}
        self._active: set[subprocess.Popen[str]] = set()
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
        if not profile.exists():
            raise TiebaBrowserError(
                "tieba_browser_setup_required", "请先打开专用贴吧浏览器并完成验证或登录。"
            )
        if deadline is not None and deadline <= time.monotonic():
            raise TiebaBrowserError("tieba_browser_timeout", "贴吧浏览器读取超时。")
        process = self._spawn(profile, payload)
        try:
            return self._receive(process, stop_event, deadline or time.monotonic() + 25)
        finally:
            self._dispose(process)

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
        output: queue.Queue[str] = queue.Queue(maxsize=1)
        assert process.stdout is not None
        stream = process.stdout

        def receive_line() -> None:
            try:
                output.put(stream.readline(100_000))
            except (OSError, ValueError):
                output.put("")

        threading.Thread(target=receive_line, daemon=True).start()
        while time.monotonic() < deadline:
            checker = getattr(stop_event, "is_set", None)
            if callable(checker) and checker():
                raise TiebaBrowserError("tieba_browser_cancelled", "已停止贴吧浏览器读取。")
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
                raise TiebaBrowserError(str(value["error"]), str(value["message"]))
            return value
        raise TiebaBrowserError("tieba_browser_timeout", "贴吧浏览器读取超时，请稍后重试。")

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
