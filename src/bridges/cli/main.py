"""Unified CLI: BridGes.

Supports the production run contracts:
- unified CLI (`start`): 同步启动构建后的 Web、API、后台执行器与提醒调度器，
  获取数据目录单实例锁、执行迁移、等待健康检查并输出本地电脑端访问地址；
- manual split process (`api`, `web`, `worker`, `scheduler`)
- Docker / Podman (compose files in infra/compose)

Conda `agent` is only used for local development; this CLI never detects or
requires a Conda environment at runtime.

Issue 41：旧 ``science-companion`` 控制台入口与 ``python -m
science_companion.cli.main`` 迁移兼容层已随退出 Issue 移除，规范入口仅
``BridGes``。
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from types import FrameType
from typing import Annotated

import typer
import uvicorn
from pydantic import ValidationError

from bridges import __version__
from bridges.config import Settings, get_settings
from bridges.runtime import (
    DEFAULT_EXECUTOR_INTERVAL_SECONDS,
    DEFAULT_SCHEDULER_INTERVAL_SECONDS,
    BackgroundExecutor,
    DataDirectoryLock,
    ReminderScheduler,
    RuntimeLockError,
)

app = typer.Typer(
    name="BridGes",
    help="BridGes unified CLI",
    no_args_is_help=True,
    # Windows 子进程经常按系统代码页解码 stdout；使用 Click 的纯文本帮助，
    # 避免 Rich 绘制的 Unicode 边框造成 GBK 解码失败。
    rich_markup_mode=None,
)


def _repo_root() -> Path:
    """Return repository root assuming CLI runs from inside the repo."""
    marker = Path(__file__).resolve().parents[3]
    if (marker / "pyproject.toml").exists():
        return marker
    return Path.cwd()


def _load_settings_or_exit() -> None:
    """Ensure the unified configuration schema loads successfully.

    All carrier commands share this validation so configuration errors surface
    identically in manual, unified CLI, Docker and Podman runs.
    """
    try:
        get_settings()
    except (ValidationError, ValueError) as exc:
        typer.echo(f"error: configuration load failed: {exc}", err=True)
        raise typer.Exit(1) from exc


@app.command()
def doctor() -> None:
    """Run environment and dependency diagnostics."""
    checks = [
        ("python", sys.executable),
        ("version", __version__),
        ("repo_root", str(_repo_root())),
    ]
    for name, value in checks:
        typer.echo(f"{name}: {value}")

    # T008: validate the unified configuration schema.
    _load_settings_or_exit()
    settings = get_settings()
    typer.echo(f"environment: {settings.environment}")
    typer.echo("ok: config schema loaded")

    # 外部能力未配置时给出中文可操作提示：不导入失败，也不假成功。
    # Issue 41（AC3）：未配置密钥时能力明确停用（无离线桩），页面显示真实
    # 错误，配置后经真实能力探测才可用。
    qwen_key = settings.qwen_api_key
    if qwen_key is None or not qwen_key.get_secret_value():
        typer.echo("提示：未配置 BRIDGES_QWEN_API_KEY，Qwen 文本/语音/图像等"
                   "能力保持停用，并显示真实不可用状态。")
        typer.echo("  配置方式：设置环境变量 BRIDGES_QWEN_API_KEY，或登录后"
                   "在账户设置中配置百炼密钥并通过能力探测。")
    else:
        typer.echo("ok: Qwen API key 已配置")

    # Production runtime contract must not depend on Conda.
    conda_prefix = os.environ.get("CONDA_PREFIX")
    conda_default_env = os.environ.get("CONDA_DEFAULT_ENV")
    if conda_prefix or conda_default_env:
        typer.echo(
            "notice: Conda environment detected; this is acceptable for local "
            "development but production images do not require it."
        )
    else:
        typer.echo("ok: no Conda dependency at runtime")

    typer.echo("doctor: passed")


def _run_database_migration(settings: Settings) -> None:
    """配置了数据库时执行事务迁移（幂等）；失败时打印中文错误并非零退出。

    与 ``migrate`` 命令共用同一实现（``start`` 启动前也执行同一迁移）。
    """
    database_url = settings.database_url
    if database_url is None or not database_url.get_secret_value():
        typer.echo("migrate: 未配置 BRIDGES_DATABASE_URL，无需迁移。")
        return
    from bridges.persistence import PersistenceError, resolve_database_path
    from bridges.storage import BridgesDatabase, StorageError

    try:
        path = resolve_database_path(database_url)
        version = BridgesDatabase(path).initialize()
    except (PersistenceError, StorageError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"migrate: 数据库迁移完成，当前模式版本 {version}")


@app.command()
def migrate() -> None:
    """Run database migrations.

    Issue 05: 配置 BRIDGES_DATABASE_URL 时在空数据目录事务化创建带版本记录的
    ``bridges.db``；重复执行幂等，只补齐缺失的迁移，绝不重复迁移或破坏数据。
    """
    _load_settings_or_exit()
    settings = get_settings()
    typer.echo(f"environment: {settings.environment}")
    _run_database_migration(settings)


def _ensure_standalone_assets(web_dir: Path) -> None:
    """把 Web 构建产物的静态资源同步进 standalone 目录（与 Dockerfile 同款语义）。

    Next.js standalone 输出不含 ``.next/static`` 与 ``public``，而 standalone
    服务器只服务自身目录内的文件；缺失时所有 CSS/JS/品牌资源返回 404，页面
    退化为无样式裸 HTML（apps/web/Dockerfile 已通过 COPY 补齐，本地 CLI
    此前遗漏）。幂等合并拷贝，重复启动安全；构建产物变化后旧分块自然淘汰
    （不再被页面引用）。
    """
    standalone = web_dir / ".next" / "standalone"
    for relative in (".next/static", "public"):
        source = web_dir / relative
        if not source.exists():
            continue
        shutil.copytree(source, standalone / relative, dirs_exist_ok=True)


@app.command()
def api(
    host: Annotated[str | None, typer.Option("--host", help="Bind host")] = None,
    port: Annotated[int | None, typer.Option("--port", help="Bind port")] = None,
    reload: Annotated[bool, typer.Option("--reload", help="Enable auto-reload")] = False,
) -> None:
    """Run the API process."""
    settings = get_settings()
    uvicorn.run(
        "bridges.api.main:create_app",
        host=host or settings.api_host,
        port=port or settings.api_port,
        factory=True,
        reload=reload,
    )


@app.command()
def web(
    dev: Annotated[
        bool | None,
        typer.Option("--dev/--no-dev", help="Run Next.js dev server"),
    ] = None,
) -> None:
    """Run the Web process."""
    settings = get_settings()
    web_dir = _repo_root() / "apps" / "web"
    if not web_dir.exists():
        typer.echo(f"error: web app not found at {web_dir}", err=True)
        raise typer.Exit(1)

    use_dev = dev if dev is not None else settings.web_dev
    os.environ["PORT"] = str(settings.web_port)
    if use_dev:
        # 开发模式保持 npm 子进程：开发者在终端里按 Ctrl+C 会同时送达
        # npm 与 node。Windows 上 npm 常以 .cmd shim 形式存在（如 fnm 的
        # npm.CMD），CreateProcess 无法解析裸名，需用 shutil.which 解析。
        npm = shutil.which("npm") or "npm"
        subprocess.run([npm, "run", "dev"], cwd=web_dir, check=True)
        return
    standalone = web_dir / ".next" / "standalone" / "server.js"
    if not standalone.exists():
        typer.echo(
            f"error: 未找到 Web 生产构建产物 {standalone}，"
            "请先在 apps/web 目录执行 npm run build。",
            err=True,
        )
        raise typer.Exit(1)
    node = shutil.which("node") or "node"
    # 生产模式：本进程包装 node 服务器。收到停止信号（Ctrl+C / Ctrl+Break /
    # SIGTERM）时先转发给 node 再等待其退出，避免遗留孤儿 web 服务器继续
    # 占用端口。注意 Windows 上 os.execv 只是“新建进程后退出自身”，会让
    # 监管者误判 Web 进程已退出，因此这里使用显式的子进程包装。
    _ensure_standalone_assets(web_dir)
    node_proc = subprocess.Popen([node, str(standalone)], cwd=web_dir)

    def _stop_node(signum: int, frame: FrameType | None) -> None:
        if node_proc.poll() is None:
            node_proc.terminate()

    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(ValueError, OSError, AttributeError):
            signal.signal(sig, _stop_node)
    if os.name == "nt":
        with contextlib.suppress(ValueError, OSError, AttributeError):
            signal.signal(signal.SIGBREAK, _stop_node)
    code = node_proc.wait()
    raise typer.Exit(0 if code in (0, None) else code)


class _RuntimeStartupError(Exception):
    """启动或运行期错误：携带可操作中文消息与整体退出码。"""

    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code


def _terminate(proc: subprocess.Popen[bytes], name: str) -> None:
    """先优雅停止，超时后整树强制结束。

    子进程可能再派生孙进程（如 Web 开发模式的 npm→node 链），只杀直接
    子进程会遗留孤儿进程继续占用端口与日志管道，因此兜底用整树结束。
    """
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _kill_process_tree(proc)


def _kill_process_tree(proc: subprocess.Popen[bytes]) -> None:
    """整树强制结束子进程及其全部孙进程。"""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        # Windows 父进程被强杀不会连带子进程，必须用 taskkill /T 整树结束。
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            check=False,
        )
    else:
        try:
            # vars 字典方式引用，保证 mypy 在 Windows（无 killpg/SIGKILL 属性）
            # 与 POSIX 两种平台下都能通过。
            vars(os)["killpg"](proc.pid, vars(signal)["SIGKILL"])
        except (OSError, ValueError):
            proc.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=10)


def _local_address(host: str) -> str:
    """把任意绑定地址换算成本地电脑端可访问的地址。"""
    return "127.0.0.1" if host in {"0.0.0.0", "::"} else host


def _http_get_ok(url: str) -> bool:
    """URL 返回 HTTP 200 视为健康。"""
    try:
        with urllib.request.urlopen(url, timeout=1.0) as resp:
            return bool(resp.status == 200)
    except Exception:  # noqa: BLE001 - 健康轮询需要吞掉一切临时错误
        return False


def _api_ready_ok(base_url: str) -> bool:
    """API ``/health/ready`` 返回 ``ready: pass`` 视为就绪。"""
    try:
        with urllib.request.urlopen(f"{base_url}/health/ready", timeout=1.0) as resp:
            payload: dict[str, object] = json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - 健康轮询需要吞掉一切临时错误
        return False
    return payload.get("ready") == "pass"


def _wait_for_process_health(
    name: str,
    command: str,
    proc: subprocess.Popen[bytes],
    healthy: Callable[[], bool],
    timeout: float = 90.0,
) -> None:
    """等待子进程存活且健康；进程先退出或超时抛中文启动错误。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise _RuntimeStartupError(
                f"start: {name} 进程启动失败（退出码 {proc.returncode}）。"
                f"请检查 {name} 相关配置与端口占用，或单独运行"
                f" BridGes {command} 查看详细错误。"
            )
        if healthy():
            return
        time.sleep(0.5)
    raise _RuntimeStartupError(
        f"start: {name} 在 {int(timeout)} 秒内未通过健康检查，"
        "请检查上方日志与端口占用情况。"
    )


def _resolve_data_dir(settings: Settings) -> Path:
    """返回单实例锁与持久化文件所在的数据目录。

    配置了数据库时取数据库文件所在目录；未配置时退回仓库根目录，避免同一
    仓库内启动两个实例互相干扰。
    """
    if settings.database_url is not None and settings.database_url.get_secret_value():
        from bridges.persistence import PersistenceError, resolve_database_path

        try:
            return Path(resolve_database_path(settings.database_url)).parent
        except PersistenceError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(1) from exc
    return _repo_root()


def _spawn_services(settings: Settings, profile: str) -> dict[str, subprocess.Popen[bytes]]:
    """并行启动 API、Web、后台执行器与提醒调度器子进程。

    POSIX 上子进程使用独立会话（start_new_session）：整树停止（killpg）
    依赖子进程为会话组长，且终端 Ctrl+C 不会直接打断子进程——停止顺序由
    本监督进程的按序终止保证。Windows 无需此设置（taskkill /T 整树结束）。
    """
    new_session = os.name != "nt"
    api_proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "bridges.cli.main",
            "api",
            "--host",
            settings.api_host,
            "--port",
            str(settings.api_port),
        ],
        cwd=_repo_root(),
        start_new_session=new_session,
    )
    web_env = {**os.environ, "PORT": str(settings.web_port)}
    web_dir = _repo_root() / "apps" / "web"
    if profile == "development":
        # 开发模式：npm 包装（npm→node 链由停止时的整树结束兜底）。
        npm = shutil.which("npm") or "npm"
        web_proc = subprocess.Popen(
            [npm, "run", "dev"], cwd=web_dir, env=web_env, start_new_session=new_session
        )
    else:
        # 生产模式：直接监管 node 服务器进程本身，停止时不会遗留孤儿
        # web 服务器（与容器路径直接运行 node 的语义一致）。
        standalone = web_dir / ".next" / "standalone" / "server.js"
        node = shutil.which("node") or "node"
        _ensure_standalone_assets(web_dir)
        web_proc = subprocess.Popen(
            [node, str(standalone)],
            cwd=web_dir,
            env=web_env,
            start_new_session=new_session,
        )
    worker_proc = subprocess.Popen(
        [sys.executable, "-m", "bridges.cli.main", "worker"],
        cwd=_repo_root(),
        start_new_session=new_session,
    )
    scheduler_proc = subprocess.Popen(
        [sys.executable, "-m", "bridges.cli.main", "scheduler"],
        cwd=_repo_root(),
        start_new_session=new_session,
    )
    return {
        "api": api_proc,
        "web": web_proc,
        "worker": worker_proc,
        "scheduler": scheduler_proc,
    }


def _serve(profile: str) -> None:
    """Start Web, API, background executor and reminder scheduler.

    规范入口（见 ADR-0012 与 PRD DEPLOY-01：源码/容器路径均执行 ``BridGes
    start``）。流程：校验依赖与目录权限 → 获取数据目录单实例锁 → 执行数据库
    迁移 → 并行启动四个关键服务 → 等待健康检查并输出本地电脑端访问地址。
    任一关键服务失败整体返回非零退出码并显示可操作中文错误；Ctrl+C 按顺序
    停止子进程并释放锁。锁在进程退出（含异常终止）时由操作系统自动释放，
    异常终止后可安全恢复，不丢失已提交数据。
    """

    _load_settings_or_exit()
    settings = get_settings()
    typer.echo(f"start profile={profile}")

    # 1) 依赖与构建产物校验（可操作中文错误，非零退出）
    web_dir = _repo_root() / "apps" / "web"
    if not web_dir.exists():
        typer.echo(f"error: Web 应用目录不存在：{web_dir}", err=True)
        raise typer.Exit(1)
    if profile == "production":
        standalone = web_dir / ".next" / "standalone" / "server.js"
        if not standalone.exists():
            typer.echo(
                "error: 未找到 Web 生产构建产物。请先在 apps/web 目录执行"
                " npm install 与 npm run build；本地开发可改用"
                " BridGes start --profile development。",
                err=True,
            )
            raise typer.Exit(1)
        if shutil.which("node") is None:
            typer.echo(
                "error: 未找到 node 可执行文件，请安装 Node.js 20 或更高版本。",
                err=True,
            )
            raise typer.Exit(1)
    elif shutil.which("npm") is None:
        typer.echo(
            "error: 未找到 npm 可执行文件，请安装 Node.js 20 或更高版本。",
            err=True,
        )
        raise typer.Exit(1)

    # 2) 数据目录校验
    data_dir = _resolve_data_dir(settings)
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        if not os.access(data_dir, os.W_OK):
            raise OSError(f"数据目录不可写：{data_dir}")
    except OSError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc

    # Windows 控制台 Ctrl+Break 与 POSIX SIGTERM（docker stop 向容器 PID 1
    # 发送 SIGTERM）都触发与 Ctrl+C 相同的优雅停止路径。Windows 上控制台
    # 事件会同时广播给同控制台的子进程（多数子进程未注册时被直接终止），
    # 停止顺序仍由下方 finally 的按序终止保证，数据由 WAL 保护。
    def _graceful_stop(signum: int, frame: FrameType | None) -> None:
        raise KeyboardInterrupt

    graceful_signals = (signal.SIGBREAK if os.name == "nt" else signal.SIGTERM,)
    for sig in graceful_signals:
        with contextlib.suppress(ValueError, OSError, AttributeError):
            signal.signal(sig, _graceful_stop)

    # 3) 单实例锁 → 迁移 → 启动四个服务 → 健康检查 → 监督运行。锁获取后的
    #    一切失败路径（迁移失败、启动失败、Ctrl+C）都收敛到 finally：按序
    #    停止已启动的子进程并显式释放锁；锁本身在进程退出时由 OS 兜底释放。
    exit_code = 0
    procs: dict[str, subprocess.Popen[bytes]] = {}
    lock = DataDirectoryLock(data_dir)
    try:
        try:
            lock.acquire()
        except RuntimeLockError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(1) from exc

        _run_database_migration(settings)
        procs = _spawn_services(settings, profile)

        # 4) 等待健康检查并输出本地电脑端访问地址
        api_url = f"http://{_local_address(settings.api_host)}:{settings.api_port}"
        web_url = f"http://127.0.0.1:{settings.web_port}"
        _wait_for_process_health("API", "api", procs["api"], lambda: _api_ready_ok(api_url))
        _wait_for_process_health("Web", "web", procs["web"], lambda: _http_get_ok(web_url))
        _wait_for_process_health("后台执行器", "worker", procs["worker"], lambda: True)
        _wait_for_process_health("提醒调度器", "scheduler", procs["scheduler"], lambda: True)
        typer.echo("BridGes 已启动：")
        typer.echo(f"  Web：{web_url}")
        typer.echo(f"  API 就绪检查：{api_url}/health/ready")
        typer.echo("  后台执行器与提醒调度器运行中。按 Ctrl+C 停止全部服务。")

        # 5) 监督运行：任一关键服务退出即整体失败
        while True:
            for name, proc in procs.items():
                code = proc.poll()
                if code is not None:
                    raise _RuntimeStartupError(
                        f"start: {name} 进程异常退出（退出码 {code}），"
                        "正在停止其余服务。请查看上方日志，或单独运行"
                        " BridGes 对应命令复现。"
                    )
            time.sleep(1)
    except KeyboardInterrupt:
        typer.echo("正在停止全部服务...")
    except _RuntimeStartupError as exc:
        typer.echo(f"error: {exc.message}", err=True)
        exit_code = exc.exit_code
    finally:
        # 6) 按顺序停止子进程（提醒调度器 → 后台执行器 → Web → API）并释放锁
        for name in ("scheduler", "worker", "web", "api"):
            spawned = procs.get(name)
            if spawned is not None:
                _terminate(spawned, name)
        lock.release()

    if exit_code != 0:
        raise typer.Exit(exit_code)


@app.command()
def start(
    profile: Annotated[
        str,
        typer.Option("--profile", help="Runtime profile (production or development)"),
    ] = "production",
) -> None:
    """Start Web, API, background executor and reminder scheduler.

    默认生产模式启动构建后的 Web；本地开发请使用 ``--profile development``。
    ``serve`` 为历史同实现别名，两者共享同一实现，不维护两套。
    """
    _serve(profile)


@app.command()
def serve(
    profile: Annotated[
        str,
        typer.Option("--profile", help="Runtime profile (production or development)"),
    ] = "production",
) -> None:
    """Start Web, API, background executor and reminder scheduler (legacy alias of ``start``)."""
    _serve(profile)


def _stop_event() -> threading.Event:
    """返回由停止信号置位的事件，供后台进程的受监督循环平滑退出。

    覆盖 Ctrl+C（SIGINT）、SIGTERM（容器 stop），Windows 另含 Ctrl+Break；
    平台不支持的信号注册失败时跳过。
    """

    def _on_signal(signum: int, frame: FrameType | None) -> None:
        stop.set()

    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(ValueError, OSError, AttributeError):
            signal.signal(sig, _on_signal)
    if os.name == "nt":
        with contextlib.suppress(ValueError, OSError, AttributeError):
            signal.signal(signal.SIGBREAK, _on_signal)
    return stop


@app.command()
def worker(
    interval: Annotated[
        int,
        typer.Option("--interval", help="Poll interval in seconds"),
    ] = DEFAULT_EXECUTOR_INTERVAL_SECONDS,
) -> None:
    """Run the background executor (cleanup of pending objects and orphans)."""
    _load_settings_or_exit()
    BackgroundExecutor(get_settings()).run_loop(
        interval=float(interval), stop=_stop_event(), emit=typer.echo
    )
    typer.echo("worker: 已平滑停止。")


@app.command()
def scheduler(
    interval: Annotated[
        int,
        typer.Option("--interval", help="Poll interval in seconds"),
    ] = DEFAULT_SCHEDULER_INTERVAL_SECONDS,
) -> None:
    """Run the reminder scheduler (dispatch of due reminders)."""
    _load_settings_or_exit()
    ReminderScheduler(get_settings()).run_loop(
        interval=float(interval), stop=_stop_event(), emit=typer.echo
    )
    typer.echo("scheduler: 已平滑停止。")


# Issue 40：注册 ``BridGes evaluate`` 子命令（可复现 A/B 科学评测）。
from bridges.cli.evaluate import evaluate_app  # noqa: E402

app.add_typer(evaluate_app)


if __name__ == "__main__":
    app()
