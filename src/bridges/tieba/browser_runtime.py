"""账户隔离的专用 Chrome 生命周期；不读取或导出个人浏览器凭据。"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bridges.runtime.lock import LOCK_FILENAME, DataDirectoryLock, RuntimeLockError
from bridges.tieba.browser_errors import TiebaBrowserError
from bridges.tieba.browser_receive import receive_result

if TYPE_CHECKING:
    from bridges.tieba.budget import TiebaBudget

ACCOUNT_DELETION_MARKER = ".account-deleting"

BROWSER_CLOSE_TIMEOUT_SECONDS = 3.0
BROWSER_KILL_TIMEOUT_SECONDS = 5.0
BROWSER_EXIT_TIMEOUT_SECONDS = 5.0
BROWSER_CLEANUP_TOLERANCE_SECONDS = (
    BROWSER_CLOSE_TIMEOUT_SECONDS
    + BROWSER_KILL_TIMEOUT_SECONDS
    + BROWSER_EXIT_TIMEOUT_SECONDS
)


@dataclass(eq=False)
class _BrowserWorker:
    process: subprocess.Popen[str]
    output: queue.Queue[str | None]


def profile_path(data_dir: Path, account_id: str) -> Path:
    """目录由服务端认证账户生成，账户标识不能成为路径片段。"""
    digest = hashlib.sha256(account_id.encode()).hexdigest()
    return data_dir / "tieba-browser" / digest


def account_profile_is_fenced(profile: Path) -> bool:
    """账户删除后保留无敏感内容的标记，阻止待启动 worker 重建资料。"""
    return (profile / ACCOUNT_DELETION_MARKER).exists()


def fence_account_profile(data_dir: Path, account_id: str) -> None:
    """持久化账户会话删除栅栏，供 API 与后台重试共同使用。"""
    profile = profile_path(data_dir, account_id)
    profile.mkdir(parents=True, exist_ok=True)
    (profile / ACCOUNT_DELETION_MARKER).touch(exist_ok=True)


def remove_account_profile(data_dir: Path, account_id: str) -> None:
    """删除未被运行中浏览器占用的账户专用资料目录。"""
    profile = profile_path(data_dir, account_id)
    fence_account_profile(data_dir, account_id)
    lock = DataDirectoryLock(profile)
    try:
        lock.acquire()
    except RuntimeLockError as exc:
        raise TiebaBrowserError(
            "tieba_browser_busy", "贴吧专用会话仍在使用，账户清理可稍后重试。"
        ) from exc
    try:
        for entry in profile.iterdir():
            if entry.name in {LOCK_FILENAME, ACCOUNT_DELETION_MARKER}:
                continue
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()
    finally:
        lock.release()
    # 保留无敏感数据的锁文件，避免解锁后立即 unlink 造成并发 worker
    # 分别锁住旧、新 inode 的窗口。


class TiebaBrowserRuntime:
    """按账户隔离、串行复用浏览器上下文；登录等待不消耗工作截止。"""

    _MAX_WORKERS = 4
    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._workers: dict[str, _BrowserWorker] = {}
        self._account_locks: dict[str, threading.Lock] = {}
        self._last_used: dict[str, float] = {}
        self._deleting_accounts: set[str] = set()
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
        request_budget: TiebaBudget | None = None,
        request_guard: Callable[[], str | None] | None = None,
    ) -> dict[str, Any]:
        profile = profile_path(self._data_dir, account_id)
        checker = getattr(stop_event, "is_set", None)
        if callable(checker) and checker():
            raise TiebaBrowserError("tieba_browser_cancelled", "已停止贴吧浏览器读取。")
        if deadline is not None and deadline <= time.monotonic():
            raise TiebaBrowserError("tieba_browser_timeout", "贴吧浏览器读取超时。")
        self._raise_if_account_is_deleting(account_id, profile)
        with self._lock:
            account_lock = self._account_locks.setdefault(account_id, threading.Lock())
        lock_deadline = deadline if deadline is not None else time.monotonic() + 25
        while not account_lock.acquire(timeout=min(0.1, max(0, lock_deadline - time.monotonic()))):
            self._raise_if_account_is_deleting(account_id, profile)
            if callable(checker) and checker():
                raise TiebaBrowserError("tieba_browser_cancelled", "已停止贴吧浏览器读取。")
            if time.monotonic() >= lock_deadline:
                if deadline is not None:
                    raise TiebaBrowserError(
                        "tieba_browser_timeout", "等待账户专用会话超时，请稍后重试。"
                    )
                raise TiebaBrowserError(
                    "tieba_browser_busy", "该账户的贴吧会话正在使用，请稍后重试。"
                )
        try:
            self._raise_if_account_is_deleting(account_id, profile)
            if callable(checker) and checker():
                raise TiebaBrowserError("tieba_browser_cancelled", "已停止贴吧浏览器读取。")
            if deadline is not None and deadline <= time.monotonic():
                raise TiebaBrowserError(
                    "tieba_browser_timeout", "等待账户专用会话超时，请稍后重试。"
                )
            profile.mkdir(parents=True, exist_ok=True)
            worker = self._worker_for(account_id, profile, deadline=deadline)
            try:
                self._raise_if_account_is_deleting(account_id, profile)
                if callable(checker) and checker():
                    raise TiebaBrowserError("tieba_browser_cancelled", "已停止贴吧浏览器读取。")
                if deadline is not None and deadline <= time.monotonic():
                    raise TiebaBrowserError("tieba_browser_timeout", "贴吧浏览器读取超时。")
                self._send(worker, payload)
                result = self._receive(
                    worker,
                    stop_event,
                    deadline or time.monotonic() + 25,
                    request_budget,
                    self._deletion_aware_guard(
                        account_id,
                        profile,
                        request_guard
                        or (
                            request_budget.request_block_reason
                            if request_budget is not None
                            else None
                        ),
                    ),
                )
            except TiebaBrowserError as error:
                with self._lock:
                    self._last_used[account_id] = time.monotonic()
                if error.code in {
                    "tieba_browser_cancelled",
                    "tieba_browser_task_changed",
                    "tieba_browser_run_invalidated",
                    "tieba_browser_request_guard_failed",
                    "tieba_browser_timeout",
                    "tieba_browser_unavailable",
                    "tieba_browser_work_time_exhausted",
                    "tieba_browser_request_queue_full",
                    "tieba_browser_account_deleting",
                }:
                    self._drop_worker(
                        account_id, worker, request_budget=request_budget
                    )
                raise
            with self._lock:
                self._last_used[account_id] = time.monotonic()
            return result
        finally:
            if self._account_is_deleting(account_id, profile):
                self._dispose_account_worker(account_id)
            account_lock.release()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            workers = tuple(self._workers.values())
            self._workers.clear()
            self._last_used.clear()
        for worker in workers:
            self._dispose(worker)

    def remove_account(self, account_id: str) -> None:
        """停止账户专属 worker，并删除其浏览器登录资料。"""
        account_lock = self._fence_account(account_id)
        if not account_lock.acquire(blocking=False):
            raise TiebaBrowserError(
                "tieba_browser_busy", "贴吧专用会话仍在使用，账户清理可稍后重试。"
            )
        try:
            with self._lock:
                worker = self._workers.pop(account_id, None)
                self._last_used.pop(account_id, None)
            if worker is not None:
                self._dispose(worker)
            remove_account_profile(self._data_dir, account_id)
        finally:
            account_lock.release()

    def fence_account(self, account_id: str) -> None:
        """禁止此账户发起新浏览器动作，并持久化跨重试栅栏。"""
        with self._lock:
            self._deleting_accounts.add(account_id)
            account_lock = self._account_locks.setdefault(
                account_id, threading.Lock()
            )
        fence_account_profile(self._data_dir, account_id)
        if not account_lock.acquire(blocking=False):
            return
        try:
            self._dispose_account_worker(account_id)
        finally:
            account_lock.release()

    def _fence_account(self, account_id: str) -> threading.Lock:
        with self._lock:
            self._deleting_accounts.add(account_id)
            account_lock = self._account_locks.setdefault(
                account_id, threading.Lock()
            )
        fence_account_profile(self._data_dir, account_id)
        return account_lock

    def _dispose_account_worker(self, account_id: str) -> None:
        with self._lock:
            worker = self._workers.pop(account_id, None)
            self._last_used.pop(account_id, None)
        if worker is not None:
            self._dispose(worker)

    def account_is_deleting(self, account_id: str) -> bool:
        """账户删除栅栏是否生效（内存标记或跨进程落盘标记）。"""
        return self._account_is_deleting(
            account_id, profile_path(self._data_dir, account_id)
        )

    def _account_is_deleting(self, account_id: str, profile: Path) -> bool:
        with self._lock:
            deleting = account_id in self._deleting_accounts
        return deleting or account_profile_is_fenced(profile)

    def _deletion_aware_guard(
        self,
        account_id: str,
        profile: Path,
        request_guard: Callable[[], str | None] | None,
    ) -> Callable[[], str | None]:
        """每个请求与等待周期都先检查删除栅栏，再执行原运行守卫。"""

        def guard() -> str | None:
            if self._account_is_deleting(account_id, profile):
                return "tieba_browser_account_deleting"
            return request_guard() if request_guard is not None else None

        return guard

    def _raise_if_account_is_deleting(self, account_id: str, profile: Path) -> None:
        with self._lock:
            deleting = account_id in self._deleting_accounts
        if deleting or account_profile_is_fenced(profile):
            raise TiebaBrowserError(
                "tieba_browser_account_deleting",
                "账户正在删除，贴吧专用会话不可继续使用。",
            )

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
                raise TiebaBrowserError("tieba_browser_busy", "贴吧浏览器运行时已关闭。")
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
        for account_id in sorted(self._workers, key=lambda key: self._last_used.get(key, 0)):
            account_lock = self._account_locks.get(account_id)
            worker = self._workers.get(account_id)
            if account_lock is None or worker is None or not account_lock.acquire(blocking=False):
                continue
            self._workers.pop(account_id, None)
            self._last_used.pop(account_id, None)
            return worker, account_lock
        return None

    def _spawn(self, profile: Path) -> _BrowserWorker:
        if account_profile_is_fenced(profile):
            raise TiebaBrowserError(
                "tieba_browser_account_deleting",
                "账户正在删除，贴吧专用会话不可继续使用。",
            )
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
                    # JSON 必须保持完整；原文的字段/条数预算在采集投影边界控制。
                    output.put(line)
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
        request_budget: TiebaBudget | None = None,
        request_guard: Callable[[], str | None] | None = None,
    ) -> dict[str, Any]:
        return receive_result(
            worker,
            stop_event=stop_event,
            deadline=deadline,
            request_budget=request_budget,
            request_guard=request_guard,
            send=lambda value: self._send(worker, value),
        )

    def _drop_worker(
        self,
        account_id: str,
        worker: _BrowserWorker,
        *,
        request_budget: TiebaBudget | None = None,
    ) -> None:
        with self._lock:
            if self._workers.get(account_id) is worker:
                self._workers.pop(account_id, None)
                self._last_used.pop(account_id, None)
        duration_ms = self._dispose(worker)
        record_cleanup = getattr(request_budget, "record_browser_cleanup", None)
        if duration_ms is not None and callable(record_cleanup):
            record_cleanup(duration_ms)

    def _dispose(self, worker: _BrowserWorker) -> int:
        started = time.monotonic()
        process = worker.process
        if process.poll() is None:
            try:
                if process.stdin is not None:
                    process.stdin.write("close\n")
                    process.stdin.flush()
                process.wait(timeout=BROWSER_CLOSE_TIMEOUT_SECONDS)
            except (OSError, subprocess.TimeoutExpired):
                # 仅终止本运行时启动的进程树，避免 Chrome 在取消后占用专用目录。
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                        timeout=BROWSER_KILL_TIMEOUT_SECONDS,
                    )
                else:
                    process.terminate()
                process.wait(timeout=BROWSER_EXIT_TIMEOUT_SECONDS)
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                stream.close()
        return max(0, int((time.monotonic() - started) * 1000))


__all__ = [
    "BROWSER_CLEANUP_TOLERANCE_SECONDS",
    "TiebaBrowserError",
    "TiebaBrowserRuntime",
    "account_profile_is_fenced",
    "fence_account_profile",
    "profile_path",
    "remove_account_profile",
]
