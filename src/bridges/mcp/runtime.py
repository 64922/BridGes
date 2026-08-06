"""MCP 受限进程运行管理器（Issue 35）。

按（账户, MCP 标识）维护受限进程注册表：首次调用时惰性启动（initialize
握手成功即健康），进程意外退出由调用方捕获并标记失败；停用/卸载/撤权
时停止对应进程；应用重启后注册表重建（不继承任何内存进程引用），并从
pid 文件回收异常退出遗留的孤儿进程，保证重启恢复的是持久化的合法配置
而非僵尸进程。
"""

from __future__ import annotations

import os
import threading
from contextlib import suppress
from pathlib import Path

from bridges.mcp.process import STARTUP_TIMEOUT_SECONDS, McpProcessClient

_PID_PREFIX = "mcp-"
_PID_SUFFIX = ".pid"


def _pid_alive(pid: int) -> bool:
    """检查 pid 是否存活（Windows 兼容：PermissionError 表示存在）。"""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class McpRuntime:
    """每账户 MCP 进程注册表与生命周期管理。"""

    def __init__(self, *, pid_dir: Path | None = None) -> None:
        self._pid_dir = pid_dir
        self._processes: dict[tuple[str, str], McpProcessClient] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # 进程获取与停止
    # ------------------------------------------------------------------

    def get_or_start(
        self,
        account_id: str,
        mcp_id: str,
        command: list[str],
        *,
        startup_timeout: float = STARTUP_TIMEOUT_SECONDS,
    ) -> McpProcessClient:
        """返回存活进程；不存在或已退出时惰性启动新进程。"""
        key = (account_id, mcp_id)
        with self._lock:
            client = self._processes.get(key)
            if client is not None and client.is_alive():
                return client
            if self._pid_dir is not None:
                # Issue 39 AC8：先确保受限工作目录存在（进程 cwd 专用目录）
                with suppress(OSError):
                    self._pid_dir.mkdir(parents=True, exist_ok=True)
            client = McpProcessClient(
                command=command,
                startup_timeout=startup_timeout,
                # Issue 39 AC8：进程工作目录固定到 MCP 专用目录（数据目录下），
                # 不继承宿主任意工作区。
                cwd=str(self._pid_dir) if self._pid_dir is not None else None,
            )
            client.start()
            self._processes[key] = client
        self._write_pid_file(account_id, mcp_id, client)
        return client

    def stop(self, account_id: str, mcp_id: str) -> None:
        """停止并移除进程（停用/卸载/撤权时调用）。"""
        key = (account_id, mcp_id)
        with self._lock:
            client = self._processes.pop(key, None)
        if client is not None:
            client.terminate()
        self._remove_pid_file(account_id, mcp_id)

    def stop_all(self) -> None:
        """停止全部进程（应用关闭时调用，不遗留子进程）。"""
        with self._lock:
            clients = list(self._processes.values())
            self._processes.clear()
        for client in clients:
            client.terminate()
        if self._pid_dir is not None:
            for pid_file in self._pid_dir.glob(f"{_PID_PREFIX}*{_PID_SUFFIX}"):
                with suppress(OSError):
                    pid_file.unlink()

    def reap_orphans(self) -> int:
        """回收上次异常退出遗留的孤儿 MCP 进程；返回回收数。

        正常关闭时 shutdown 已回收全部进程并删除 pid 文件；本方法兜底
        处理强制退出遗留的存活子进程，避免僵尸进程继续占用资源。
        """
        if self._pid_dir is None or not self._pid_dir.exists():
            return 0
        reaped = 0
        for pid_file in self._pid_dir.glob(f"{_PID_PREFIX}*{_PID_SUFFIX}"):
            try:
                pid = int(pid_file.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                with suppress(OSError):
                    pid_file.unlink()
                continue
            if _pid_alive(pid):
                with suppress(OSError):
                    os.kill(pid, 9 if os.name == "nt" else 15)
                reaped += 1
            with suppress(OSError):
                pid_file.unlink()
        return reaped

    def is_running(self, account_id: str, mcp_id: str) -> bool:
        return self.get_running(account_id, mcp_id) is not None

    def get_running(self, account_id: str, mcp_id: str) -> McpProcessClient | None:
        """返回存活进程；未启动或已退出返回 None（不启动新进程）。"""
        key = (account_id, mcp_id)
        with self._lock:
            client = self._processes.get(key)
            if client is not None and client.is_alive():
                return client
            if client is not None:
                self._processes.pop(key, None)
            return None

    # ------------------------------------------------------------------
    # pid 文件
    # ------------------------------------------------------------------

    def _write_pid_file(self, account_id: str, mcp_id: str, client: McpProcessClient) -> None:
        if self._pid_dir is None:
            return
        pid = client.pid
        if not pid:
            return
        # pid 文件是尽力而为的兜底，失败不影响调用。
        with suppress(OSError):
            self._pid_dir.mkdir(parents=True, exist_ok=True)
            self._pid_dir.joinpath(self._pid_filename(account_id, mcp_id)).write_text(
                str(pid), encoding="utf-8"
            )

    def _remove_pid_file(self, account_id: str, mcp_id: str) -> None:
        if self._pid_dir is None:
            return
        with suppress(OSError):
            self._pid_dir.joinpath(self._pid_filename(account_id, mcp_id)).unlink()

    @staticmethod
    def _pid_filename(account_id: str, mcp_id: str) -> str:
        safe_account = account_id.replace(":", "_")
        return f"{_PID_PREFIX}{safe_account}-{mcp_id}{_PID_SUFFIX}"


__all__ = ["McpRuntime"]
