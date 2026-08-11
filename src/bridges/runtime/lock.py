"""跨平台数据目录单实例锁。

BridGes 的所有运行载体（源码 Conda/``.venv`` 与 Docker/Podman 容器）都通过
同一数据目录的 OS 建议锁保证同一时刻只有一个实例写入本地状态：

- POSIX 使用 ``fcntl.flock``，Windows 使用 ``msvcrt.locking``；
- 锁由操作系统在进程退出（包括异常终止）时自动释放，遗留锁文件不影响下次
  启动——异常终止后可以安全恢复；
- 获取失败时报可操作的中文错误，绝不删除或破坏其他实例持有的锁文件。
"""

from __future__ import annotations

import os
from pathlib import Path

#: 数据目录下的锁文件名。锁在文件描述符上，而非文件内容上，因此遗留文件
#: 无害；文件内只写入诊断用的持有者 PID。
LOCK_FILENAME = ".bridges.lock"


class RuntimeLockError(RuntimeError):
    """单实例锁获取失败（另一实例正在使用同一数据目录）。"""


def _platform_lock(fd: int) -> None:
    """在当前文件描述符上获取非阻塞建议锁；已被占用时抛 OSError。"""
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        # vars() 字典方式引用，保证 mypy 在 Windows（无 flock 属性）与
        # POSIX 两种平台下都能通过，同时满足 ruff B009 的告警要求。
        posix = vars(fcntl)
        posix["flock"](fd, posix["LOCK_EX"] | posix["LOCK_NB"])


def _platform_unlock(fd: int) -> None:
    """释放当前文件描述符上的建议锁。"""
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        vars(fcntl)["flock"](fd, vars(fcntl)["LOCK_UN"])


class DataDirectoryLock:
    """数据目录单实例锁（上下文管理器）。

    用法::

        with DataDirectoryLock(data_dir) as lock:
            ...  # 同一数据目录的第二个实例在此处会得到 RuntimeLockError
    """

    def __init__(self, data_dir: Path) -> None:
        self._path = Path(data_dir) / LOCK_FILENAME
        self._fd: int | None = None

    @property
    def path(self) -> Path:
        """锁文件路径（诊断用）。"""
        return self._path

    @property
    def is_held(self) -> bool:
        """当前进程是否持有该锁。"""
        return self._fd is not None

    def acquire(self) -> None:
        """获取锁；已被其他实例持有时抛 ``RuntimeLockError``。"""
        if self._fd is not None:
            return
        try:
            fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as exc:
            raise RuntimeLockError(
                f"无法创建数据目录锁文件 {self._path}，请检查数据目录权限。"
            ) from exc
        # 先写入 PID 保证文件至少 1 字节（Windows msvcrt 锁定区不能越过 EOF）。
        # Windows 上其他实例持有字节区锁时，本进程写入同一区域会抛
        # PermissionError——此时同样按“锁已被占用”处理。
        try:
            os.write(fd, f"pid={os.getpid()}\n".encode())
        except OSError:
            os.close(fd)
            raise RuntimeLockError(
                "另一个 BridGes 实例正在使用该数据目录"
                f"（锁文件：{self._path}）。请先停止已有实例（Ctrl+C），"
                "或为不同实例配置不同的 BRIDGES_DATABASE_URL。"
            ) from None
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            _platform_lock(fd)
        except OSError:
            os.close(fd)
            raise RuntimeLockError(
                "另一个 BridGes 实例正在使用该数据目录"
                f"（锁文件：{self._path}）。请先停止已有实例（Ctrl+C），"
                "或为不同实例配置不同的 BRIDGES_DATABASE_URL。"
            ) from None
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, f"pid={os.getpid()}\n".encode())
        self._fd = fd

    def release(self) -> None:
        """释放锁；未持有时为空操作。"""
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            # 保留锁文件但写入不可运行的 PID；只读探测据此识别已释放的锁，
            # 同时避免释放后留下当前进程 PID 造成永久误报。
            os.lseek(fd, 0, os.SEEK_SET)
            os.ftruncate(fd, 0)
            os.write(fd, b"pid=0\n")
        finally:
            try:
                _platform_unlock(fd)
            finally:
                os.close(fd)

    def __enter__(self) -> DataDirectoryLock:
        self.acquire()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()
