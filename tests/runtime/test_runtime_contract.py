"""Issue 06：统一源码与容器运行合同测试。

测试接缝：数据目录单实例锁（轮转、跨进程拒绝、进程死亡自动释放）、后台
执行器真实清理、提醒调度器心跳、``BridGes start`` 全流程（启动 → 健康检查 →
重复启动拒绝 → 优雅停止 → 再次启动）与关键服务失败的非零退出语义。
"""

from __future__ import annotations

import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

import pytest
from pydantic import SecretStr

from bridges.config import Settings
from bridges.runtime import (
    BackgroundExecutor,
    DataDirectoryLock,
    ReminderScheduler,
    RuntimeLockError,
)
from bridges.runtime.loop import supervised_loop
from bridges.storage import (
    BridgesDatabase,
    BridgesObjectRepository,
    EncryptedFileObjectStore,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

NEW_PROCESS_GROUP = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0

#: Web 生产构建产物（`.next` 被 gitignore，未构建的环境跳过全流程冒烟）。
WEB_STANDALONE = REPO_ROOT / "apps" / "web" / ".next" / "standalone" / "server.js"

NEEDS_WEB_BUILD = pytest.mark.skipif(
    not WEB_STANDALONE.exists(),
    reason="需要先在 apps/web 执行 npm run build 生成生产构建产物",
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _clean_env(tmp: Path | None = None) -> dict[str, str]:
    """构造显式子进程环境：剔除 BRIDGES_* 与迁移期旧 SCIENCE_COMPANION_*
    前缀变量及 Conda 变量，强制 UTF-8 输出。

    旧前缀仍被 config 兼容接受（新前缀优先），若不剔除，开发机残留的
    SCIENCE_COMPANION_* 真实凭据会进入子进程——与 test_runtime_smoke 的
    隔离语义保持一致。
    """
    merged = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("BRIDGES_", "SCIENCE_COMPANION_"))
        and key not in {"CONDA_PREFIX", "CONDA_DEFAULT_ENV", "CONDA_SHLVL"}
    }
    merged["PYTHONIOENCODING"] = "utf-8"
    if tmp is not None:
        merged.update(
            {
                "BRIDGES_ENVIRONMENT": "production",
                # GQ-01 启动硬门：production 环境必须配置全局百炼运行凭据；
                # 占位值只用于放行启动流程，子进程不发起任何真实网络请求。
                "BRIDGES_QWEN_API_KEY": "placeholder-global-key-not-real",
                "BRIDGES_DATABASE_URL": f"sqlite:///{tmp}/bridges.db",
                "BRIDGES_SECRET_KEY": "test-secret-0000000000000000",
                "BRIDGES_API_HOST": "127.0.0.1",
                "BRIDGES_API_PORT": str(_free_port()),
                "BRIDGES_WEB_PORT": str(_free_port()),
            }
        )
    return merged


def _run_cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "bridges.cli.main", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env or _clean_env(),
        check=False,
    )


def _runtime_settings(tmp: Path) -> Settings:
    return Settings(
        environment="test",
        database_url=SecretStr(f"sqlite:///{tmp}/bridges.db"),
        secret_key=SecretStr("test-secret-0000000000000000"),
    )


# ---------------------------------------------------------------------------
# 数据目录单实例锁
# ---------------------------------------------------------------------------


def test_runtime_lock_roundtrip_acquire_release() -> None:
    with tempfile.TemporaryDirectory() as raw:
        lock = DataDirectoryLock(Path(raw))
        lock.acquire()
        assert lock.is_held
        lock.release()
        assert not lock.is_held


def test_startup_lock_rejects_second_instance_on_same_data_directory() -> None:
    """第二个实例不能对同一数据目录重复启动（AC7）。"""
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        lock = DataDirectoryLock(tmp)
        lock.acquire()
        try:
            with pytest.raises(RuntimeLockError, match="另一个 BridGes 实例"):
                DataDirectoryLock(tmp).acquire()
        finally:
            lock.release()
        # 释放后同一数据目录可再次获取
        lock = DataDirectoryLock(tmp)
        lock.acquire()
        assert lock.is_held
        lock.release()


def test_runtime_lock_auto_released_after_process_death() -> None:
    """异常终止后锁自动释放，可安全恢复（AC7 安全恢复语义）。"""
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        holder = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import time; from pathlib import Path;"
                " from bridges.runtime import DataDirectoryLock;"
                f" DataDirectoryLock(Path(r'{tmp}')).acquire(); time.sleep(60)",
            ],
            env=_clean_env(),
        )
        try:
            deadline = time.time() + 10
            while time.time() < deadline and not (tmp / ".bridges.lock").exists():
                time.sleep(0.1)
            time.sleep(0.5)
            with pytest.raises(RuntimeLockError):
                DataDirectoryLock(tmp).acquire()
        finally:
            holder.kill()
            holder.wait()
        # 进程死亡后 OS 自动释放建议锁：遗留锁文件不影响再次获取。
        # Windows 上 TerminateProcess 后的句柄清理略有延迟，短暂重试。
        lock = DataDirectoryLock(tmp)
        deadline = time.monotonic() + 5
        while True:
            try:
                lock.acquire()
                break
            except RuntimeLockError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.1)
        assert lock.is_held
        lock.release()


# ---------------------------------------------------------------------------
# 后台执行器与提醒调度器
# ---------------------------------------------------------------------------


def test_runtime_worker_cleans_pending_objects_and_orphans(tmp_path: Path) -> None:
    """后台执行器执行真实清理：pending_cleanup 记录与孤立文件都被移除。"""
    settings = _runtime_settings(tmp_path)
    database = BridgesDatabase(tmp_path / "bridges.db")
    store = EncryptedFileObjectStore(tmp_path / "objects", encryption_key=settings.secret_key)
    repository = BridgesObjectRepository(database, store)
    account_id = repository.register_account("clean@example.com")
    content_hash = store.put(b"orphan-bytes")
    with database.transaction():
        database.connection.execute(
            "INSERT INTO objects(object_id, account_id, content_hash, original_filename,"
            " content_length, status, created_at, updated_at)"
            " VALUES ('obj-1', ?, ?, 'f.txt', 5, 'pending_cleanup',"
            " '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
            (account_id, content_hash),
        )
    assert len(repository.list_pending_cleanups()) == 1

    line = BackgroundExecutor(settings).run_tick()

    assert "清理完成 1 个待清理对象" in line
    assert repository.list_pending_cleanups() == []
    assert repository.find_orphans() == []


def test_runtime_worker_idles_without_database_configuration() -> None:
    """未配置数据库时执行器待机，不崩溃也不假成功（AC4）。"""
    line = BackgroundExecutor(Settings(environment="test")).run_tick()
    assert "待机" in line


def test_runtime_scheduler_heartbeat_with_and_without_database(tmp_path: Path) -> None:
    settings = _runtime_settings(tmp_path)
    BridgesDatabase(tmp_path / "bridges.db").initialize()
    line = ReminderScheduler(settings).run_tick()
    assert "调度心跳正常" in line
    assert "0 个" in line

    idle = ReminderScheduler(Settings(environment="test")).run_tick()
    assert "待机" in idle


def test_runtime_supervised_loop_stops_on_stop_event() -> None:
    """监督循环收到停止信号后平滑退出（最多再执行一轮）。"""
    stop = threading.Event()
    stop.set()
    lines: list[str] = []
    supervised_loop(tick=lambda: "tick", stop=stop, interval=60, emit=lines.append)
    assert lines == ["tick"]


# ---------------------------------------------------------------------------
# CLI 合同
# ---------------------------------------------------------------------------


def test_cli_worker_scheduler_help_exits_successfully() -> None:
    worker_help = _run_cli("worker", "--help")
    assert worker_help.returncode == 0
    assert "background executor" in worker_help.stdout

    scheduler_help = _run_cli("scheduler", "--help")
    assert scheduler_help.returncode == 0
    assert "reminder scheduler" in scheduler_help.stdout


def test_cli_start_help_mentions_all_services() -> None:
    result = _run_cli("start", "--help")
    assert result.returncode == 0
    assert "background executor" in result.stdout
    assert "reminder scheduler" in result.stdout


def test_ensure_standalone_assets_copies_static_and_public(tmp_path: Path) -> None:
    """standalone 产物缺失的静态资源在启动前补齐（与 Dockerfile 同款语义）。

    回归保护：next build 的 standalone 输出不含 ``.next/static`` 与
    ``public``，若启动前不补齐，生产 Web 对所有 CSS/JS/品牌资源返回 404，
    页面退化为无样式裸 HTML。重复调用幂等。
    """
    from bridges.cli.main import _ensure_standalone_assets

    web_dir = tmp_path / "web"
    (web_dir / ".next" / "static" / "css").mkdir(parents=True)
    (web_dir / ".next" / "static" / "css" / "app.css").write_text(
        "body{}", encoding="utf-8"
    )
    (web_dir / "public" / "brand").mkdir(parents=True)
    (web_dir / "public" / "brand" / "logo.svg").write_text("<svg/>", encoding="utf-8")

    _ensure_standalone_assets(web_dir)
    _ensure_standalone_assets(web_dir)  # 幂等：重复启动不报错

    standalone = web_dir / ".next" / "standalone"
    assert (
        standalone / ".next" / "static" / "css" / "app.css"
    ).read_text(encoding="utf-8") == "body{}"
    assert (
        standalone / "public" / "brand" / "logo.svg"
    ).read_text(encoding="utf-8") == "<svg/>"


@NEEDS_WEB_BUILD
def test_startup_reports_chinese_error_when_port_is_occupied(tmp_path: Path) -> None:
    """关键服务启动失败时整体非零退出并显示可操作中文错误（AC6）。"""
    busy = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    busy.bind(("127.0.0.1", 0))
    busy.listen(1)
    try:
        env = _clean_env(tmp_path)
        env["BRIDGES_API_PORT"] = str(busy.getsockname()[1])
        output_file = tmp_path / "start_output.txt"
        with output_file.open("w", encoding="utf-8") as out:
            result = subprocess.run(
                [sys.executable, "-m", "bridges.cli.main", "start"],
                cwd=REPO_ROOT,
                stdout=out,
                stderr=subprocess.STDOUT,
                env=env,
                check=False,
                timeout=120,
            )
        assert result.returncode != 0
        output = output_file.read_text(encoding="utf-8")
        assert "启动失败" in output
    finally:
        busy.close()


# ---------------------------------------------------------------------------
# start 全流程冒烟（开发 profile：不要求生产构建产物）
# ---------------------------------------------------------------------------


@NEEDS_WEB_BUILD
def test_startup_full_journey_start_health_duplicate_reject_stop_restart() -> None:
    """空临时数据目录冒烟旅程：启动 → 迁移 → 健康检查 → 重复启动拒绝 →
    优雅停止 → 再次启动成功（AC1/AC5/AC6/AC7）。

    使用默认生产 profile（启动构建后的 Web，与 AC1 旅程一致）；开发服务器
    会重写 ``.next`` 目录，避免在测试中运行 dev 模式。
    """
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        env = _clean_env(tmp)
        first = _spawn_start(env)
        try:
            lines = _wait_started(first)
            assert "Web：" in "\n".join(lines)
            assert "API 就绪检查" in "\n".join(lines)
            assert (tmp / "bridges.db").exists(), "start 必须执行数据库迁移"
            assert (tmp / ".bridges.lock").exists(), "start 必须获取单实例锁"

            # 生产 Web 必须真正提供构建产物的静态资源（CSS 以 text/css
            # 返回而非 404 裸 HTML），否则页面退化为无样式裸 HTML。
            web_port = env["BRIDGES_WEB_PORT"]
            html = (
                urllib.request.urlopen(
                    f"http://127.0.0.1:{web_port}/", timeout=10
                )
                .read()
                .decode("utf-8", errors="replace")
            )
            css_match = re.search(r'href="(/_next/static/css/[^"]+\.css)"', html)
            assert css_match, "首页 HTML 必须引用构建后的样式表"
            with urllib.request.urlopen(
                f"http://127.0.0.1:{web_port}{css_match.group(1)}", timeout=10
            ) as css:
                assert css.status == 200
                assert "text/css" in css.headers.get("content-type", ""), (
                    "样式表必须以 text/css 提供，而不是 404 裸 HTML"
                )

            second_output = tmp / "second_start.txt"
            with second_output.open("w", encoding="utf-8") as out:
                second = subprocess.run(
                    [sys.executable, "-m", "bridges.cli.main", "start"],
                    cwd=REPO_ROOT,
                    stdout=out,
                    stderr=subprocess.STDOUT,
                    env=env,
                    check=False,
                    timeout=60,
                )
            assert second.returncode != 0
            assert "另一个 BridGes 实例" in second_output.read_text(encoding="utf-8")

            _graceful_stop(first)
        finally:
            _kill_tree(first)

        # 停止后再次启动：锁已释放，同一数据目录可安全恢复
        restart = _spawn_start(env)
        try:
            _wait_started(restart)
        finally:
            _graceful_stop(restart)
            _kill_tree(restart)


def _spawn_start(env: dict[str, str]) -> subprocess.Popen[str]:
    """以默认生产 profile 启动 ``BridGes start``（构建后的 Web）。"""
    return subprocess.Popen(
        [sys.executable, "-m", "bridges.cli.main", "start"],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        creationflags=NEW_PROCESS_GROUP,
    )


def _wait_started(proc: subprocess.Popen[str], timeout: float = 150.0) -> list[str]:
    """等待 start 输出完整横幅；读取放在守护线程，避免管道无输出时阻塞。"""
    import queue

    lines: list[str] = []
    pending: queue.Queue[str | None] = queue.Queue()

    def _reader() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            pending.put(line.rstrip())
        pending.put(None)

    threading.Thread(target=_reader, daemon=True).start()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            line = pending.get(timeout=0.2)
        except queue.Empty:
            continue
        if line is None:
            raise AssertionError(
                f"start 进程提前退出（退出码 {proc.returncode}）。输出：\n"
                + "\n".join(lines[-30:])
            )
        lines.append(line)
        # 完整横幅以“按 Ctrl+C 停止全部服务。”结尾，其后才是访问地址输出，
        # 因此等待该行而不是“已启动”行，保证地址断言可用。
        if "按 Ctrl+C 停止全部服务" in line:
            return lines
    raise AssertionError(
        f"start 未在 {timeout} 秒内就绪。输出：\n" + "\n".join(lines[-30:])
    )


def _graceful_stop(proc: subprocess.Popen[str]) -> None:
    if os.name == "nt":
        proc.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        proc.send_signal(signal.SIGINT)
    try:
        proc.wait(timeout=60)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)


def _kill_tree(proc: subprocess.Popen[str]) -> None:
    """强制结束进程及其全部子进程。

    Windows 上父进程被强杀不会连带结束子进程（worker/scheduler 等仍持有
    数据库文件句柄），必须使用 taskkill /T 整树结束；POSIX 使用进程组。
    """
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            check=False,
        )
    else:
        # vars 字典方式引用，保证 mypy 在 Windows（无 killpg/SIGKILL 属性）下通过。
        vars(os)["killpg"](proc.pid, vars(signal)["SIGKILL"])
    import contextlib

    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=10)
