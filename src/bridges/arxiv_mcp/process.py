"""arXiv MCP 的受限 worker 进程适配器（issue 05：UTF-8 协议与可靠启动）。

协议显式、双向、固定为 UTF-8：父进程管道以 ``encoding="utf-8"`` 打开，
子进程环境受控设置 ``PYTHONIOENCODING/PYTHONUTF8``，worker 启动时再
显式 reconfigure 标准流——三处叠加后不依赖系统代码页（Windows 默认
GBK/CP936）。worker 启动先完成 ``ready`` 握手，父进程才接受响应；握手
与单次响应都设截止时间，超时/退出/协议损坏各自映射为稳定错误码，避免
把一切故障统一压成 ``arxiv_startup``。

可靠性：worker 首次异常退出时关闭旧句柄并安全重启一次，仍失败才返回
终态（``max_restarts`` 上限，无无限循环）；搜索期间 ``stop_event`` 置位
会在截止时间内终止当前请求并投影为取消。stderr 改为有上限的诊断采集
（防管道阻塞），只保留退出码、阶段与脱敏摘要，不记录查询或论文摘要。
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import subprocess
import sys
import threading
import time
from collections import deque
from contextlib import suppress
from datetime import datetime
from typing import Any

from bridges.arxiv_mcp import limits as arxiv_limits
from bridges.arxiv_mcp.client import ArxivMcpError
from bridges.arxiv_mcp.contracts import ArxivPaper
from bridges.arxiv_mcp.worker import HANDSHAKE_VERSION

logger = logging.getLogger("bridges.arxiv_mcp.process")

#: 子进程环境白名单：只继承代理与 Windows 启动必需变量，其余一律不传
#: （不继承模型 Key、SMTP 码等秘密；代理经明确白名单传入以满足 arXiv
#: 网络访问需求）。
_ENV_WHITELIST = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "TEMP",
    "TMP",
    "COMPUTERNAME",
)

#: worker 经 ok:false 载荷上报的稳定错误码（协议可信任，原样透传）。
_KNOWN_WORKER_CODES = frozenset(
    {
        "arxiv_timeout",
        "arxiv_offline",
        "arxiv_rate_limit",
        "arxiv_permission",
        "arxiv_request",
        "arxiv_parse",
        "arxiv_cancelled",
    }
)

#: 握手默认截止时间（秒）。
DEFAULT_HANDSHAKE_TIMEOUT = 5.0
#: 单次响应默认截止时间（秒）：长于 worker 侧网络超时（10s），仅兜底
#: worker 挂死等异常情况。
DEFAULT_RESPONSE_TIMEOUT = 30.0
#: 默认安全重启上限：首次崩溃后最多重启一次，仍失败才返回终态。
DEFAULT_MAX_RESTARTS = 1
DEFAULT_MAX_CONCURRENT_SEARCHES = 4

_SANITIZE_PATTERNS = (
    (re.compile(r"sk-[A-Za-z0-9_\-]{16,}"), "<api-key>"),
    (re.compile(r"(session|device)_token=[A-Za-z0-9_\-]{20,}"), r"\1_token=<token>"),
    (re.compile(r"Bearer [A-Za-z0-9._\-]{20,}"), "Bearer <token>"),
)


def _sanitize(text: str) -> str:
    """诊断采集的脱敏摘要：API Key、Cookie 与 Bearer 令牌不进入日志。"""
    for pattern, placeholder in _SANITIZE_PATTERNS:
        text = pattern.sub(placeholder, text)
    return text


class _ProcessWaitError(Exception):
    """父进程侧等待故障（握手超时/启动即退/响应超时/中途退出）。

    与 worker 载荷上报的错误分开：此类故障意味着当前 worker 不可信，
    值得关闭句柄后安全重启一次；载荷错误（网络超时/权限等）说明 worker
    存活，按错误码直接透传。
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class ArxivMcpProcessClient:
    """通过 UTF-8 JSONL 与固定模块 worker 通信，避免 MCP 继承应用权限。"""

    def __init__(
        self,
        *,
        python_executable: str | None = None,
        extra_env: dict[str, str] | None = None,
        handshake_timeout: float = DEFAULT_HANDSHAKE_TIMEOUT,
        response_timeout: float = DEFAULT_RESPONSE_TIMEOUT,
        max_restarts: int = DEFAULT_MAX_RESTARTS,
        max_concurrent_searches: int = DEFAULT_MAX_CONCURRENT_SEARCHES,
    ) -> None:
        self._python_executable = python_executable or sys.executable
        # 测试注入边界：收尾 smoke 用它在 worker 侧替换确定性 arXiv 客户端
        # （PYTHONPATH 前缀并入，shadow 模块先于真实包解析）。生产默认 None，
        # 行为与既有最小环境完全一致。
        self._extra_env = extra_env or {}
        self._handshake_timeout = handshake_timeout
        self._response_timeout = response_timeout
        self._max_restarts = max_restarts
        self._max_concurrent_searches = max(1, max_concurrent_searches)
        self._process: subprocess.Popen[str] | None = None
        #: 当前进程是否已完成 ready 握手（spawn 时重置）。
        self._ready = False
        #: 当前进程的标准输出行队列（reader 线程投递，EOF 投 None 哨兵）。
        self._lines: queue.Queue[str | None] = queue.Queue()
        #: 有上限的 stderr 诊断采集（脱敏后供日志使用）。
        self._stderr_tail = ""
        #: 本次搜索内的重启次数（每次 search 重置，上限 max_restarts）。
        self._restarts = 0
        #: 当前协议阶段（handshake/search），供诊断日志区分失败发生阶段。
        self._stage = "handshake"
        #: 单个可复用 worker 串行使用；并发请求使用隔离的临时 worker。
        self._search_lock = threading.Lock()
        self._extra_slots = threading.BoundedSemaphore(
            max(0, self._max_concurrent_searches - 1)
        )
        self._children_lock = threading.Lock()
        self._children: set[ArxivMcpProcessClient] = set()
        #: 预热互斥：应用启动期只预热一次常驻 worker，避免并发重复 spawn。
        self._warmup_lock = threading.Lock()
        self._warmup_counters_lock = threading.Lock()
        self._warmup_successes = 0
        self._warmup_failures = 0

    @property
    def warmup_successes(self) -> int:
        """启动期预热成功次数（脱敏计数，供 metrics_snapshot 聚合）。"""
        with self._warmup_counters_lock:
            return self._warmup_successes

    @property
    def warmup_failures(self) -> int:
        """启动期预热失败次数（脱敏计数，供 metrics_snapshot 聚合）。"""
        with self._warmup_counters_lock:
            return self._warmup_failures

    def warmup(self) -> bool:
        """启动期预热：spawn 常驻 worker 并完成握手（含 httpx 导入）。

        预热失败只记日志并返回 False：进程句柄已清理，首次搜索仍走
        懒启动兜底（``_ensure_process`` + ``_ensure_handshake``），不改变
        任何搜索语义。开关（``ARXIV_WARMUP_ENABLED``，单一来源
        :mod:`bridges.arxiv_mcp.limits`）关闭或已预热完成时直接返回
        对应状态。
        """
        if not arxiv_limits.ARXIV_WARMUP_ENABLED:
            return False
        with self._warmup_lock:
            process = self._process
            if self._ready and process is not None and process.poll() is None:
                return True
            try:
                process = self._ensure_process()
                self._stage = "handshake"
                # 截止比握手超时略宽，使超时按 handshake 阶段分类
                # （与搜索路径的握手失败分类一致），而不是折叠成 timeout。
                self._ensure_handshake(
                    process,
                    stop_event=None,
                    deadline=time.monotonic() + self._handshake_timeout + 0.5,
                )
            except Exception as exc:  # noqa: BLE001 - 预热失败绝不阻断应用启动
                code = getattr(exc, "code", exc.__class__.__name__)
                logger.warning(
                    "arxiv worker 预热失败 code=%s stderr=%s",
                    code,
                    self._stderr_tail[-512:] or "（无）",
                )
                with self._warmup_counters_lock:
                    self._warmup_failures += 1
                return False
            with self._warmup_counters_lock:
                self._warmup_successes += 1
            logger.info("arxiv worker 预热完成 pid=%s", process.pid)
            return True

    def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        stop_event: threading.Event | None = None,
        deadline: float | None = None,
    ) -> list[ArxivPaper]:
        """执行一次搜索：每个请求使用隔离 worker 与同一绝对截止时间。"""
        if _user_cancelled(stop_event):
            raise ArxivMcpError(
                "arxiv_cancelled",
                "已取消本轮论文搜索。",
                upstream_status="cancelled",
                retryable=False,
            )
        if deadline is not None and deadline <= time.monotonic():
            raise ArxivMcpError(
                "arxiv_timeout", "arXiv 搜索超时，请重试。", upstream_status="timeout"
            )
        if self._search_lock.acquire(blocking=False):
            try:
                return self._search_with_process(
                    query,
                    max_results=max_results,
                    stop_event=stop_event,
                    deadline=deadline,
                )
            finally:
                self._search_lock.release()
        if not self._extra_slots.acquire(blocking=False):
            raise ArxivMcpError(
                "arxiv_backpressure", "arXiv 搜索请求过多，请稍后重试。"
            )
        child = ArxivMcpProcessClient(
            python_executable=self._python_executable,
            extra_env=self._extra_env,
            handshake_timeout=self._handshake_timeout,
            response_timeout=self._response_timeout,
            max_restarts=self._max_restarts,
            max_concurrent_searches=1,
        )
        with self._children_lock:
            self._children.add(child)
        try:
            return child._search_with_process(
                query,
                max_results=max_results,
                stop_event=stop_event,
                deadline=deadline,
            )
        finally:
            child.close()
            with self._children_lock:
                self._children.discard(child)
            self._extra_slots.release()

    def _search_with_process(
        self,
        query: str,
        *,
        max_results: int,
        stop_event: threading.Event | None,
        deadline: float | None,
    ) -> list[ArxivPaper]:
        """在已分配的 worker 会话上执行请求；重试不重置绝对截止时间。"""
        self._restarts = 0
        for _ in range(self._max_restarts + 1):
            if _user_cancelled(stop_event):
                raise ArxivMcpError(
                    "arxiv_cancelled",
                    "已取消本轮论文搜索。",
                    upstream_status="cancelled",
                    retryable=False,
                )
            if deadline is not None and deadline <= time.monotonic():
                raise ArxivMcpError(
                    "arxiv_timeout", "arXiv 搜索超时，请重试。", upstream_status="timeout"
                )
            process = self._ensure_process()
            started = time.monotonic()
            try:
                self._stage = "handshake"
                self._ensure_handshake(
                    process, stop_event=stop_event, deadline=deadline
                )
                self._stage = "search"
                self._send_request(process, query, max_results, deadline=deadline)
                line = self._wait_line(
                    process,
                    stop_event=stop_event,
                    timeout=self._response_timeout,
                    deadline=deadline,
                    stage="search",
                )
                papers = self._decode_response(line)
                logger.info(
                    "arxiv worker 响应完成 pid=%s elapsed=%.2fs papers=%d",
                    process.pid,
                    time.monotonic() - started,
                    len(papers),
                )
                return papers
            except _ProcessWaitError as exc:
                if _user_cancelled(stop_event):
                    raise ArxivMcpError(
                        "arxiv_cancelled",
                        "已取消本轮论文搜索。",
                        upstream_status="cancelled",
                        retryable=False,
                    ) from exc
                if deadline is not None and time.monotonic() >= deadline:
                    raise ArxivMcpError(
                        exc.code,
                        exc.message,
                        upstream_status="timeout",
                    ) from exc
                if self._restarts >= self._max_restarts:
                    self._log_terminal_failure(process, exc, started)
                    raise ArxivMcpError(
                        exc.code,
                        exc.message,
                        upstream_status={
                            "arxiv_timeout": "timeout",
                            "arxiv_handshake": "handshake",
                            "arxiv_startup": "startup",
                            "arxiv_worker_exit": "worker_exit",
                        }.get(exc.code),
                    ) from exc
                self._close_process(process)
                self._restarts += 1
                logger.warning(
                    "arxiv worker 等待失败 pid=%s stage=%s code=%s elapsed=%.2fs"
                    " restarts=%d stderr=%s",
                    process.pid,
                    self._stage,
                    exc.code,
                    time.monotonic() - started,
                    self._restarts,
                    self._stderr_tail[-512:] or "（无）",
                )
        # 循环内所有路径均 return/raise；此处仅为类型收窄，理论不可达。
        raise ArxivMcpError("arxiv_internal", "arXiv 搜索服务异常，请重试。")

    def close(self) -> None:
        """关闭受限 worker：终止进程、关闭管道，避免遗留子进程与句柄。"""
        self._close_process(self._process)
        with self._children_lock:
            children = list(self._children)
        for child in children:
            child.close()

    # ------------------------------------------------------------------
    # 协议步骤
    # ------------------------------------------------------------------

    def _ensure_process(self) -> subprocess.Popen[str]:
        process = self._process
        if process is not None and process.poll() is None:
            return process
        command = [self._python_executable, "-m", "bridges.arxiv_mcp.worker"]
        if "PYTHONPATH" in self._extra_env:
            # 收尾替身通过 sitecustomize 注入。-S 防止 editable .pth 把主
            # 仓库源码重新加回 sys.path，bootstrap 再显式加载替身并运行 worker。
            worker_bootstrap = """
import importlib.util
import os
import runpy
import sys

paths = [path for path in os.environ.get("PYTHONPATH", "").split(os.pathsep) if path]
sys.path[:0] = paths
spec = importlib.util.find_spec("sitecustomize")
if spec is not None and spec.loader is not None:
    module = importlib.util.module_from_spec(spec)
    sys.modules["sitecustomize"] = module
    spec.loader.exec_module(module)
runpy.run_module("bridges.arxiv_mcp.worker", run_name="__main__")
"""
            command = [self._python_executable, "-S", "-c", worker_bootstrap]
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=self._child_env(),
            )
        except OSError as exc:
            raise ArxivMcpError("arxiv_startup", "arXiv 搜索服务启动失败，请重试。") from exc
        self._process = process
        self._ready = False
        self._lines = queue.Queue()
        self._stderr_tail = ""
        self._start_stdout_reader(process, self._lines)
        self._start_stderr_drainer(process)
        logger.info(
            "arxiv worker 已启动 pid=%s restarts=%d", process.pid, self._restarts
        )
        return process

    def _ensure_handshake(
        self,
        process: subprocess.Popen[str],
        *,
        stop_event: threading.Event | None,
        deadline: float | None,
    ) -> None:
        """等待 worker 的 ready 握手行并校验协议版本（只在首个请求前执行）。"""
        if self._ready:
            return
        line = self._wait_line(
            process,
            stop_event=stop_event,
            timeout=self._handshake_timeout,
            deadline=deadline,
            stage="handshake",
        )
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            self._close_process(process)
            raise _ProcessWaitError(
                "arxiv_handshake", "arXiv 搜索服务启动失败，请重试。"
            ) from exc
        if (
            not isinstance(payload, dict)
            or payload.get("type") != "ready"
            or payload.get("version") != HANDSHAKE_VERSION
        ):
            self._close_process(process)
            raise _ProcessWaitError(
                "arxiv_handshake", "arXiv 搜索服务协议握手失败，请重试。"
            )
        self._ready = True
        logger.info("arxiv worker 握手完成 pid=%s", process.pid)

    def _send_request(
        self,
        process: subprocess.Popen[str],
        query: str,
        max_results: int,
        *,
        deadline: float | None,
    ) -> None:
        if process.stdin is None or process.stdout is None:
            raise _ProcessWaitError(
                "arxiv_worker_exit", "arXiv 搜索服务进程已退出，请重试。"
            )
        request_payload: dict[str, Any] = {
            "query": query,
            "max_results": max_results,
        }
        if deadline is not None:
            request_payload["deadline"] = deadline
        request = json.dumps(request_payload, ensure_ascii=False)
        try:
            process.stdin.write(request + "\n")
            process.stdin.flush()
        except (OSError, ValueError) as exc:
            raise _ProcessWaitError(
                "arxiv_worker_exit", "arXiv 搜索服务进程已退出，请重试。"
            ) from exc

    def _wait_line(
        self,
        process: subprocess.Popen[str],
        *,
        stop_event: threading.Event | None,
        timeout: float,
        deadline: float | None,
        stage: str,
    ) -> str:
        """按截止时间等待一行协议输出；取消/超时/EOF 各自映射稳定错误。"""
        phase_deadline = time.monotonic() + timeout
        if deadline is not None:
            phase_deadline = min(phase_deadline, deadline)
        lines = self._lines
        while True:
            if _user_cancelled(stop_event):
                self._close_process(process)
                raise ArxivMcpError("arxiv_cancelled", "已取消本轮论文搜索。")
            remaining = phase_deadline - time.monotonic()
            if remaining <= 0:
                self._close_process(process)
                if deadline is not None and time.monotonic() >= deadline:
                    raise _ProcessWaitError(
                        "arxiv_timeout", "arXiv 搜索超时，请重试。"
                    )
                if stage == "handshake":
                    raise _ProcessWaitError(
                        "arxiv_handshake", "arXiv 搜索服务启动超时，请重试。"
                    )
                raise _ProcessWaitError("arxiv_timeout", "arXiv 搜索超时，请重试。")
            try:
                line = lines.get(timeout=min(0.05, remaining))
            except queue.Empty:
                continue
            if line is None:
                # EOF：worker 在握手前或请求处理中途退出
                self._close_process(process)
                if stage == "handshake":
                    if self._module_missing():
                        # 验收：worker 模块不存在是独立稳定错误码，
                        # 不与握手超时（arxiv_handshake）混淆。
                        raise _ProcessWaitError(
                            "arxiv_startup", "arXiv 搜索服务启动失败，请重试。"
                        )
                    raise _ProcessWaitError(
                        "arxiv_handshake", "arXiv 搜索服务启动失败，请重试。"
                    )
                raise _ProcessWaitError(
                    "arxiv_worker_exit", "arXiv 搜索服务进程已退出，请重试。"
                )
            return line

    def _decode_response(self, line: str) -> list[ArxivPaper]:
        """解析 worker 的响应行：非法 JSON / 损坏载荷 / 载荷错误分别分类。"""
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ArxivMcpError(
                "arxiv_parse",
                "arXiv 返回内容损坏，无法解析，请重试。",
                upstream_status="parse",
            ) from exc
        if not isinstance(payload, dict):
            raise ArxivMcpError(
                "arxiv_parse",
                "arXiv 返回内容损坏，无法解析，请重试。",
                upstream_status="parse",
            )
        if payload.get("ok") is not True:
            code = payload.get("code")
            message = payload.get("message")
            if code in _KNOWN_WORKER_CODES:
                raise ArxivMcpError(
                    str(code),
                    str(message or "arXiv 搜索未完成，请重试。"),
                    permission=code == "arxiv_permission",
                    upstream_status=_safe_worker_status(payload.get("upstream_status")),
                    retryable=payload.get("retryable") is not False,
                )
            raise ArxivMcpError(
                "arxiv_parse",
                "arXiv 返回内容损坏，无法解析，请重试。",
                upstream_status="parse",
            )
        raw_papers = payload.get("papers")
        if not isinstance(raw_papers, list):
            raise ArxivMcpError(
                "arxiv_parse",
                "arXiv 返回内容损坏，无法解析，请重试。",
                upstream_status="parse",
            )
        try:
            return [_paper_from_payload(item) for item in raw_papers]
        except (KeyError, TypeError, ValueError) as exc:
            raise ArxivMcpError(
                "arxiv_parse",
                "arXiv 返回内容损坏，无法解析，请重试。",
                upstream_status="parse",
            ) from exc

    # ------------------------------------------------------------------
    # 进程生命周期
    # ------------------------------------------------------------------

    def _start_stdout_reader(
        self, process: subprocess.Popen[str], lines: queue.Queue[str | None]
    ) -> None:
        """后台线程按行读取标准输出；EOF 或管道异常时投 None 哨兵。"""

        def _reader() -> None:
            stream = process.stdout
            if stream is None:
                lines.put(None)
                return
            try:
                for line in stream:
                    lines.put(line)
            except (OSError, ValueError):
                pass
            finally:
                lines.put(None)

        threading.Thread(
            target=_reader, daemon=True, name="arxiv-worker-reader"
        ).start()

    def _start_stderr_drainer(self, process: subprocess.Popen[str]) -> None:
        """有上限的 stderr 诊断采集：持续排空防止管道阻塞，只留脱敏尾。

        逐行增量发布到 ``_stderr_tail``：超时/挂死路径在进程终止后立刻
        写诊断日志，也能拿到已采集的脱敏尾部，而不是等 drainer 收尾。
        """

        def _drain() -> None:
            stream = process.stderr
            if stream is None:
                return
            tail: deque[str] = deque(maxlen=64)
            try:
                for line in stream:
                    tail.append(_sanitize(line))
                    self._stderr_tail = "".join(tail)
            except (OSError, ValueError):
                pass
            self._stderr_tail = "".join(tail)

        threading.Thread(
            target=_drain, daemon=True, name="arxiv-worker-stderr"
        ).start()

    def _close_process(self, process: subprocess.Popen[str] | None) -> None:
        """关闭旧句柄并终止进程（超时强制结束），后续搜索重新 spawn。"""
        if process is None:
            return
        if self._process is process:
            self._process = None
            self._ready = False
        if process.stdin is not None:
            with suppress(OSError, ValueError):
                process.stdin.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=0.5)  # kill 后回收退出码，避免僵尸残留
                except subprocess.TimeoutExpired:
                    logger.warning("arxiv worker 终止回收超时 pid=%s", process.pid)
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                with suppress(OSError, ValueError):
                    stream.close()

    def _log_terminal_failure(
        self,
        process: subprocess.Popen[str] | None,
        exc: _ProcessWaitError,
        started: float,
    ) -> None:
        """终态失败的脱敏诊断：退出码、阶段、耗时、重启次数，无查询正文。"""
        exit_code = process.returncode if process is not None else None
        logger.warning(
            "arxiv worker 终态失败 pid=%s code=%s exit_code=%s stage=%s elapsed=%.2fs"
            " restarts=%d stderr=%s",
            process.pid if process is not None else None,
            exc.code,
            exit_code,
            self._stage,
            time.monotonic() - started,
            self._restarts,
            self._stderr_tail[-512:] or "（无）",
        )

    # ------------------------------------------------------------------
    # 子进程环境
    # ------------------------------------------------------------------

    def _module_missing(self) -> bool:
        """按脱敏 stderr 摘要判断启动即退是否因 worker 模块缺失。"""
        return (
            "ModuleNotFoundError" in self._stderr_tail
            or "ImportError" in self._stderr_tail
        )

    def _child_env(self) -> dict[str, str]:
        """最小环境：PYTHONPATH + 受控 UTF-8 模式 + 白名单变量。"""
        env: dict[str, str] = {
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        }
        if "PYTHONPATH" in self._extra_env:
            source_root = os.path.normcase(
                os.path.abspath(
                    os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
                )
            )
            # worktree 场景下解释器可能还通过 editable 安装暴露主仓库的
            # ``src``；只在测试替身注入路径时过滤兄弟源码根，不改写宿主
            # 进程的全局 sys.path。
            isolated_paths = [
                path
                for path in sys.path
                if not (
                    path
                    and os.path.basename(os.path.normcase(os.path.abspath(path))) == "src"
                    and os.path.normcase(os.path.abspath(path)) != source_root
                )
            ]
            env["PYTHONPATH"] = (
                self._extra_env["PYTHONPATH"]
                + os.pathsep
                + os.pathsep.join(isolated_paths)
            )
        else:
            env["PYTHONPATH"] = os.pathsep.join(sys.path)
        for key in _ENV_WHITELIST:
            if key in os.environ:
                env[key] = os.environ[key]
        env.update(
            {key: value for key, value in self._extra_env.items() if key != "PYTHONPATH"}
        )
        return env


def _paper_from_payload(payload: Any) -> ArxivPaper:
    if not isinstance(payload, dict):
        raise TypeError("paper payload must be an object")
    published_at = datetime.fromisoformat(str(payload["published_at"]))
    return ArxivPaper(
        arxiv_id=str(payload["arxiv_id"]),
        title=str(payload["title"]),
        authors=[str(author) for author in payload["authors"]],
        published_at=published_at,
        abs_url=str(payload["abs_url"]),
        pdf_url=str(payload["pdf_url"]),
        abstract=str(payload["abstract"]),
    )


def _safe_worker_status(value: Any) -> str | None:
    if isinstance(value, str) and value in {
        "local_invariant",
        "network",
        "timeout",
        "permission",
        "http_4xx",
        "http_4xx_permission",
        "http_429",
        "http_5xx",
        "parse",
        "cancelled",
    }:
        return value
    return None


def _user_cancelled(stop_event: threading.Event | None) -> bool:
    """区分用户取消与编排器为截止时间发出的内部停止信号。"""
    if stop_event is None:
        return False
    marker = getattr(stop_event, "user_is_set", None)
    return bool(marker()) if callable(marker) else stop_event.is_set()
