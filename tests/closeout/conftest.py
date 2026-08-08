"""收尾 smoke 共享 fixture（issue 01：Windows 可执行反馈环）。

提供「真实边界、确定性替身」的运行底座：

- ``venv_python``：显式使用仓库虚拟环境里的 Python，不依赖 PATH 上的
  Anaconda/系统 Python；
- ``unique_data_dir``：每次运行唯一、绝对的 SQLite 与对象目录（父目录
  启动前创建，结束后只清理本次运行目录）；
- ``fake_mail_server``：真实子进程假邮件服务器（scripts/e2e_mail_server.py，
  可配置投递延迟与丢弃令牌，覆盖 0 秒/10 秒/超时三种投递）；
- ``api_server``：真实 API 子进程，启动前做数据目录可写性与端口预检
  （不可写目录 10 秒内以「数据目录不可写」失败，而非笼统超时），进程
  提前退出时按输出分类（端口占用/数据库/迁移），错误信息中文可区分；
- ``artifacts``：为失败保留脱敏日志与 trace；所有日志经 ``sanitize``
  过滤（不含授权码、测试密钥、密码、Cookie 与 API Key 正文）。

成功运行后不遗留进程（整树终止）、锁文件或数据库句柄（数据目录被清理）。
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: 收尾测试固定秘密（均含 test 标记，供秘密扫描白名单识别）。日志与
#: trace 一律经 ``sanitize`` 脱敏，任何正文不得出现在失败产物中。
TEST_SECRET_KEY = "e2e-closeout-secret-key-01"
TEST_AUTH_CODE = "e2etestauthcode33"
TEST_PASSWORD = "correct-horse-closeout"

#: 失败产物目录（gitignore 已排除 test-results/）：每次运行一个子目录。
_RESULTS_ROOT = REPO_ROOT / "test-results" / "closeout"

_SECRET_PLACEHOLDERS: list[tuple[str, str]] = [
    (TEST_SECRET_KEY, "<secret-key>"),
    (TEST_AUTH_CODE, "<auth-code>"),
    (TEST_PASSWORD, "<password>"),
]
_API_KEY_RE = re.compile(r"sk-[A-Za-z0-9_\-]{16,}")
_COOKIE_RE = re.compile(r"(session|device)_token=[A-Za-z0-9_\-]{20,}")
_BEARER_RE = re.compile(r"Bearer [A-Za-z0-9._\-]{20,}")


def sanitize(text: str) -> str:
    """脱敏：测试秘密、API Key、Cookie 与 Bearer 令牌不进入产物。"""
    for raw, placeholder in _SECRET_PLACEHOLDERS:
        text = text.replace(raw, placeholder)
    text = _API_KEY_RE.sub("<api-key>", text)
    text = _COOKIE_RE.sub(r"\1_token=<cookie>", text)
    text = _BEARER_RE.sub("Bearer <token>", text)
    return text


def free_port() -> int:
    """返回当前空闲的 TCP 端口（绑定 0 获取，随即释放）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _http_get(url: str, timeout: float = 2.0) -> int:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return int(resp.status)
    except (urllib.error.URLError, OSError, TimeoutError):
        return 0


def _api_ready(base_url: str) -> bool:
    """``/health/ready`` 返回 ``ready: pass`` 视为就绪。"""
    try:
        with urllib.request.urlopen(f"{base_url}/health/ready", timeout=2.0) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return bool(payload.get("ready") == "pass")
    except (urllib.error.URLError, OSError, TimeoutError, ValueError):
        return False


def _kill_process_tree(proc: subprocess.Popen[str]) -> None:
    """先优雅停止，超时后整树强制结束（Windows 用 taskkill /T）。"""
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True,
                    check=False,
                )
            else:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (OSError, ValueError):
                    proc.kill()
    # 清理路径不阻塞：已退出或等待超时都视为完成
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=10)


class _ProcessLog:
    """子进程输出的脱敏采集：后台线程逐行 sanitize 后写入文件与内存缓冲。

    日志文件本身就是脱敏后的产物（AC7）；失败时缓冲区尾部进入错误消息。
    """

    def __init__(self, path: Path, name: str) -> None:
        self.path = path
        self.name = name
        self._lines: list[str] = []
        self._lock = threading.Lock()

    def consume(self, stream: Any) -> None:
        def _reader() -> None:
            try:
                for line in stream:
                    safe = sanitize(line)
                    with self._lock:
                        self._lines.append(safe)
                        self.path.open("a", encoding="utf-8").write(safe)
            except (OSError, ValueError):
                pass

        threading.Thread(target=_reader, daemon=True).start()

    def tail(self, limit: int = 40) -> str:
        with self._lock:
            return "".join(self._lines[-limit:])


class _Artifacts:
    """本次测试的产物目录与进程日志句柄。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.logs: dict[str, _ProcessLog] = {}

    def log(self, name: str) -> _ProcessLog:
        log = self.logs.get(name)
        if log is None:
            log = _ProcessLog(self.root / f"{name}.log", name)
            self.logs[name] = log
        return log

    def write_trace(self, report: Any) -> None:
        """失败时写 trace.txt：脱敏日志尾部 + 失败信息（不含任何秘密）。"""
        lines = [f"test: {report.nodeid}", f"failed: {report.longrepr}"]
        for name, log in self.logs.items():
            lines.append(f"--- log: {name} ---")
            lines.append(log.tail(80))
        (self.root / "trace.txt").write_text(
            sanitize("\n".join(lines)), encoding="utf-8"
        )


class SpawnedApi:
    """已就绪的 API 子进程句柄。"""

    def __init__(self, proc: subprocess.Popen[str], base_url: str, data_dir: Path) -> None:
        self.proc = proc
        self.base_url = base_url
        self.data_dir = data_dir

    def register_account(self, *, qq_email: str | None = None) -> httpx.Client:
        """注册真实账户并返回携带会话 Cookie 的 HTTP 客户端（真实 SQLite）。

        ``trust_env=False``：不走 Windows 系统代理（本机可能配置了未运行的
        代理导致 127.0.0.1 请求挂起；收尾 smoke 只访问回环服务）。
        """
        # QQ 邮箱本地部分必须全为数字：时间戳 + 进程号 + 计数器拼成 16 位内数字
        digits = f"{int(time.time() * 1000)}{os.getpid()}{next(_ACCOUNT_COUNTER)}"[-16:]
        client = httpx.Client(base_url=self.base_url, timeout=15.0, trust_env=False)
        payload = {
            "username": f"closeout-{os.getpid()}-{next(_ACCOUNT_COUNTER)}",
            "qq_email": qq_email or f"{digits}@qq.com",
            "password": TEST_PASSWORD,
        }
        response = client.post("/auth/register", json=payload)
        assert response.status_code == 201, (
            f"注册失败：{response.status_code} {sanitize(response.text)}"
        )
        return client


_ACCOUNT_COUNTER = iter(range(1, 1_000_000))


def _classify_api_failure(output: str) -> str:
    """把 API 启动失败输出分类为可区分的中文原因（AC3）。"""
    lowered = output.lower()
    if "10048" in lowered or "address already in use" in lowered:
        return "端口被占用（Address already in use），请检查该端口是否被其他进程占用。"
    if "unable to open database" in lowered or "operationalerror" in lowered:
        return "数据库无法打开（SQLite OperationalError），请检查数据目录与 WAL 文件。"
    if "migrate" in lowered and "error" in lowered:
        return "数据库迁移失败，请检查迁移脚本或数据库文件完整性。"
    if "configuration load failed" in lowered:
        return "配置加载失败（BRIDGES_* 环境变量非法）。"
    return "API 进程提前退出，退出码如下所示。"


@pytest.fixture(scope="session")
def venv_python() -> str:
    """显式返回仓库虚拟环境中的 Python 可执行文件（issue 01 步骤 1）。

    优先仓库 ``.venv``；不存在时回退 ``sys.executable`` 并提示（当前测试
    进程本身由什么 Python 启动就由什么启动子进程，避免 PATH 上的
    Anaconda/系统 Python 混入）。
    """
    candidate = (
        REPO_ROOT / ".venv" / "Scripts" / "python.exe"
        if os.name == "nt"
        else REPO_ROOT / ".venv" / "bin" / "python"
    )
    if candidate.exists():
        return str(candidate)
    return sys.executable


@pytest.fixture(scope="session")
def closeout_run_root() -> Path:
    """本次 pytest 运行的收尾产物根目录（唯一，test-results/closeout/run-*）。"""
    run_root = _RESULTS_ROOT / (
        "run-" + time.strftime("%Y%m%d-%H%M%S") + f"-{os.getpid()}"
    )
    run_root.mkdir(parents=True, exist_ok=True)
    return run_root


@pytest.fixture
def artifacts(closeout_run_root: Path, request: pytest.FixtureRequest) -> _Artifacts:
    """本次测试的脱敏产物目录（失败 trace 由 pytest_runtest_makereport 写入）。"""
    artifacts_ = _Artifacts(closeout_run_root / request.node.name)
    request.node.stash[ARTIFACTS_STASH_KEY] = artifacts_  # type: ignore[attr-defined]
    return artifacts_


ARTIFACTS_STASH_KEY: Any = object()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[Any]) -> Iterator[Any]:
    outcome = yield
    report = outcome.get_result()
    if report.when == "call" and report.failed:
        artifacts_ = item.stash.get(ARTIFACTS_STASH_KEY, None)  # type: ignore[attr-defined]
        if artifacts_ is not None:
            artifacts_.write_trace(report)


@pytest.fixture
def unique_data_dir(tmp_path: Path) -> Path:
    """每次运行唯一、绝对的数据目录（SQLite 与 objects/ 的父目录）。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


@pytest.fixture
def fake_mail_server(venv_python: str, artifacts: _Artifacts) -> Callable[..., dict[str, int]]:
    """工厂 fixture：启动真实假邮件服务子进程，返回 {smtp, imap, http} 端口。

    三种投递边界：
    - 0 秒：默认立即投递；
    - 10 秒：``deliver_delay_seconds=10`` 模拟晚到邮件；
    - 超时：``drop_subject_tokens`` 使邮件永远不入箱，验证最终超时失败。
    """
    created: list[subprocess.Popen[str]] = []

    def _start(
        *,
        deliver_delay_seconds: float = 0.0,
        drop_subject_tokens: tuple[str, ...] = (),
    ) -> dict[str, int]:
        smtp_port, imap_port, http_port = free_port(), free_port(), free_port()
        command = [
            str(venv_python),
            str(REPO_ROOT / "scripts" / "e2e_mail_server.py"),
            "--smtp-port",
            str(smtp_port),
            "--imap-port",
            str(imap_port),
            "--http-port",
            str(http_port),
        ]
        if deliver_delay_seconds > 0:
            command += ["--deliver-delay-seconds", str(deliver_delay_seconds)]
        if drop_subject_tokens:
            command += ["--drop-subject-tokens", ",".join(drop_subject_tokens)]
        proc = subprocess.Popen(
            command,
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        created.append(proc)
        log = artifacts.log(f"mail-{smtp_port}")
        log.consume(proc.stdout)
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(
                    f"假邮件服务启动失败（退出码 {proc.returncode}）：\n{log.tail()}"
                )
            if _http_get(f"http://127.0.0.1:{http_port}/health") == 200:
                return {"smtp": smtp_port, "imap": imap_port, "http": http_port}
            time.sleep(0.2)
        raise RuntimeError(
            f"假邮件服务 15 秒内未通过健康检查（端口 {smtp_port}）。\n{log.tail()}"
        )

    yield _start
    for proc in created:
        _kill_process_tree(proc)


@pytest.fixture
def api_server(
    venv_python: str,
    artifacts: _Artifacts,
) -> Callable[..., SpawnedApi]:
    """工厂 fixture：以唯一绝对数据目录启动真实 API 子进程（真实 SQLite）。

    启动前预检（快速失败，10 秒内）：数据目录可写性、显式端口占用。
    启动失败按输出分类给出中文原因（端口占用/数据库/迁移/配置）。
    测试结束后整树终止进程并清理本次数据目录。
    """
    created: list[tuple[subprocess.Popen[str], Path]] = []

    def _start(
        data_dir: Path,
        *,
        mail: dict[str, int] | None = None,
        port: int | None = None,
        health_timeout: float = 45.0,
    ) -> SpawnedApi:
        # 1) 数据目录预检：父目录必须可写，否则 10 秒内以中文原因失败
        #    （mkdir 与写入探针都可能在权限/路径非法时抛 OSError，统一转
        #    「数据目录不可写」而不是笼统超时）
        try:
            data_dir.mkdir(parents=True, exist_ok=True)
            probe = data_dir / ".closeout-write-probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            raise RuntimeError(
                f"数据目录不可写：{data_dir}（{exc.__class__.__name__}: {exc}）。"
                "请检查目录权限或更换 BRIDGES 测试数据根目录。"
            ) from exc

        # 2) 端口预检：显式端口被占用时立刻失败（不启动进程）
        if port is None:
            port = free_port()
        if not _port_free(port):
            raise RuntimeError(f"端口被占用：{port}。请先停止占用该端口的进程后重试。")

        database_url = f"sqlite:///{(data_dir / 'bridges.db').as_posix()}"
        env = {
            "BRIDGES_ENVIRONMENT": "test",
            "BRIDGES_DATABASE_URL": database_url,
            "BRIDGES_SECRET_KEY": TEST_SECRET_KEY,
            "BRIDGES_API_HOST": "127.0.0.1",
            "BRIDGES_API_PORT": str(port),
            "PYTHONIOENCODING": "utf-8",
        }
        if mail is not None:
            env.update(
                {
                    "BRIDGES_SMTP_HOST": "127.0.0.1",
                    "BRIDGES_SMTP_PORT": str(mail["smtp"]),
                    "BRIDGES_SMTP_PLAIN": "true",
                    "BRIDGES_IMAP_HOST": "127.0.0.1",
                    "BRIDGES_IMAP_PORT": str(mail["imap"]),
                    "BRIDGES_IMAP_PLAIN": "true",
                }
            )
        proc = subprocess.Popen(
            [
                str(venv_python),
                "-m",
                "bridges.cli.main",
                "api",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, **env},
        )
        created.append((proc, data_dir))
        log = artifacts.log(f"api-{port}")
        log.consume(proc.stdout)

        # 3) 健康等待：进程先退出或超时都给出分类后的中文原因
        base_url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + health_timeout
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(
                    f"{_classify_api_failure(log.tail())}\n"
                    f"退出码 {proc.returncode}，脱敏日志尾部：\n{log.tail()}"
                )
            if _http_get(f"{base_url}/health") == 200:
                break
            time.sleep(0.3)
        else:
            raise RuntimeError(
                f"API 在 {health_timeout:.0f} 秒内未通过 /health 检查（端口 {port}）。"
                f"脱敏日志尾部：\n{log.tail()}"
            )
        ready_deadline = time.monotonic() + health_timeout
        while time.monotonic() < ready_deadline:
            if _api_ready(base_url):
                return SpawnedApi(proc, base_url, data_dir)
            if proc.poll() is not None:
                raise RuntimeError(
                    f"{_classify_api_failure(log.tail())}\n"
                    f"退出码 {proc.returncode}，脱敏日志尾部：\n{log.tail()}"
                )
            time.sleep(0.3)
        raise RuntimeError(
            f"API 在 {health_timeout:.0f} 秒内未达到 ready=pass（端口 {port}）。"
            f"脱敏日志尾部：\n{log.tail()}"
        )

    yield _start

    # 4) 收尾：整树终止进程，随后清理本次运行的数据目录（只清理本次）
    for proc, data_dir in created:
        _kill_process_tree(proc)
        shutil.rmtree(data_dir, ignore_errors=True)


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
        return True
