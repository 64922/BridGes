"""收尾 smoke（issue 05）：真实 arXiv worker 的 UTF-8 协议与可靠性反馈环。

全部用例启动真实 ``bridges.arxiv_mcp.worker`` 子进程（非 mock Popen），
经 ``sitecustomize`` 在 worker 启动时把外部边界替换为确定性替身（只替换
arXiv 网络客户端或标准输出，worker 的协议循环保持真实）。覆盖：UTF-8
双向往返、握手超时/启动即退/非法握手、中途崩溃、非法 JSON、网络超时、
崩溃后恰一次安全重启、取消 2 秒内终止当前请求。

替身注入哨兵：每个 worker 进程启动时把 pid 追加到 SPAWN_MARKER，测试
据此断言 spawn 次数（无无限重启、无僵尸进程）；固定客户端把收到的查询
原样写入 QUERY_MARKER，证明父进程 → worker 的中文查询经 UTF-8 完整
到达。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from bridges.arxiv_mcp.client import ArxivMcpError
from bridges.arxiv_mcp.process import ArxivMcpProcessClient

_TEMPLATE = """\
# issue 05 确定性替身：__NAME__。
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from bridges.arxiv_mcp.client import ArxivMcpError
from bridges.arxiv_mcp.contracts import ArxivPaper

SPAWN_MARKER = Path('__SPAWN_MARKER__')
QUERY_MARKER = Path('__QUERY_MARKER__')

__BODY__

import bridges.arxiv_mcp.client as _client_module
__CLIENT_ASSIGNMENT__

with SPAWN_MARKER.open("a", encoding="utf-8") as _spawn_log:
    _spawn_log.write(str(os.getpid()) + "\\n")

import bridges.arxiv_mcp.worker as _worker_module
_worker_module.main = _MAIN
"""

#: 固定论文：标题/作者/摘要全含 GBK 不可编码字符（emoji 与非断行连字符）。
_FIXED_PAPERS = """\
_FIXED_PAPERS = [
    ArxivPaper(
        arxiv_id="2401.12345v2",
        title="Transformer 注意力机制 🚀 与非断行‑连字符（U+2011）",
        authors=["Ada Lovelace 🚀"],
        published_at=datetime.fromisoformat("2024-01-18T12:00:00+00:00").astimezone(UTC),
        abs_url="https://arxiv.org/abs/2401.12345v2",
        pdf_url="https://arxiv.org/pdf/2401.12345v2",
        abstract="含 🚀 的摘要正文，必须在 UTF-8 管道中往返。",
    )
]
"""

_GOOD_BODY = f"""{_FIXED_PAPERS}

class _FixedClient:
    def search(self, query, *, max_results=5):
        with QUERY_MARKER.open("a", encoding="utf-8") as _f:
            _f.write(query + "\\n")
        return _FIXED_PAPERS[:max_results]


_MAIN = None
"""

#: 标准输出替换为黑洞：worker 永远不会写出 ready → 握手超时。
_DEVNULL_BODY = """\
sys.stdout = open(os.devnull, "w", encoding="utf-8")
_MAIN = None
"""

#: 标准输出写入即抛异常：worker 在写出 ready 前崩溃退出 → 握手 EOF。
_EXPLODE_BODY = """\
class _ExplodingWriter:
    def write(self, _text):
        raise OSError("stdout closed by sidecar")

    def flush(self):
        pass


sys.stdout = _ExplodingWriter()
_MAIN = None
"""

#: 剪掉仓库路径（__REPO_ROOT__ 由安装器嵌入）：子进程无法导入 bridges
#: 包，``-m`` 启动即 ModuleNotFoundError → 真实"worker 模块不存在"路径
#: （启动即退 + stderr 含异常类型）。
_MODULE_MISSING_BODY = """\
# 剪掉仓库路径并清空已缓存的 bridges 模块：sitecustomize 顶部导入
# 已把 bridges 放进 sys.modules，不清理则 -m worker 仍可导入。
_repo = os.path.normcase(r"__REPO_ROOT__")
sys.path[:] = [p for p in sys.path if not os.path.normcase(p).startswith(_repo)]
for _name in [n for n in sys.modules if n == "bridges" or n.startswith("bridges.")]:
    del sys.modules[_name]
_MAIN = None
"""

#: 首行输出非法 ready 载荷 → 握手协议校验失败。
_BOGUS_READY_BODY = """\
class _BogusReadyWriter:
    def __init__(self):
        self._real = sys.__stdout__
        self._first = True

    def write(self, text):
        if self._first:
            self._first = False
            return self._real.write('{"type":"bogus","version":99}\\n')
        return self._real.write(text)

    def flush(self):
        self._real.flush()


sys.stdout = _BogusReadyWriter()
_MAIN = None
"""

#: 握手行正常，响应行替换为非法 JSON → 协议/解析错误。
_CORRUPT_BODY = """\
class _CorruptingWriter:
    def __init__(self):
        self._real = sys.__stdout__
        self._count = 0

    def write(self, text):
        self._count += 1
        if self._count >= 2:
            return self._real.write("这不是 JSON 响应\\n")
        return self._real.write(text)

    def flush(self):
        self._real.flush()


sys.stdout = _CorruptingWriter()
_MAIN = None
"""

#: 每次搜索都崩溃（未捕获异常 → worker 中途退出）。
_CRASH_ALL_BODY = """\
class _AlwaysCrashClient:
    def search(self, query, *, max_results=5):
        raise RuntimeError("sidecar: worker always crashes")


_MAIN = None
"""

#: 第一个 worker 进程崩溃，重启后的进程正常返回论文。
_CRASH_ONCE_BODY = f"""{_FIXED_PAPERS}

class _CrashOnceClient:
    def search(self, query, *, max_results=5):
        spawned = len(SPAWN_MARKER.read_text(encoding="utf-8").splitlines())
        if spawned < 2:
            raise RuntimeError("sidecar: first worker crashes")
        return _FIXED_PAPERS[:max_results]


_MAIN = None
"""

#: 客户端挂死（模拟网络调用永不返回）→ 父进程响应截止/取消。
_HANG_BODY = """\
class _HangingClient:
    def search(self, query, *, max_results=5):
        time.sleep(60)
        return []


_MAIN = None
"""

#: 客户端上报网络超时错误码（worker 存活，载荷 ok:false）。
_TIMEOUT_BODY = """\
class _TimeoutClient:
    def search(self, query, *, max_results=5):
        raise ArxivMcpError("arxiv_timeout", "arXiv 搜索超时，请重试。")


_MAIN = None
"""


def _install_sidecar(
    tmp_path: Path,
    name: str,
    body: str,
    *,
    client_assignment: str = "pass",
) -> tuple[Path, Path, Path]:
    """写入 sitecustomize 替身，返回 (shadow 目录, spawn 标记, 查询标记)。"""
    spawn_marker = tmp_path / f"{name}-spawns.log"
    query_marker = tmp_path / f"{name}-queries.log"
    shadow = tmp_path / f"shadow-{name}"
    shadow.mkdir(parents=True, exist_ok=True)
    #: 仓库根（供模块缺失替身排除 repo 路径）：本文件位于 repo/tests/closeout/。
    repo_root = Path(__file__).resolve().parents[2]
    source = (
        _TEMPLATE.replace("__NAME__", name)
        .replace("__SPAWN_MARKER__", spawn_marker.as_posix())
        .replace("__QUERY_MARKER__", query_marker.as_posix())
        .replace("__BODY__", body)
        .replace("__CLIENT_ASSIGNMENT__", client_assignment)
        .replace("__REPO_ROOT__", str(repo_root))
    )
    (shadow / "sitecustomize.py").write_text(source, encoding="utf-8")
    return shadow, spawn_marker, query_marker


def _client(
    venv_python: str,
    tmp_path: Path,
    name: str,
    body: str,
    *,
    client_assignment: str = "pass",
    **kwargs: float,
) -> tuple[ArxivMcpProcessClient, Path, Path]:
    shadow, spawn_marker, query_marker = _install_sidecar(
        tmp_path, name, body, client_assignment=client_assignment
    )
    client = ArxivMcpProcessClient(
        python_executable=venv_python,
        extra_env={"PYTHONPATH": str(shadow)},
        **kwargs,
    )
    return client, spawn_marker, query_marker


def _spawn_count(spawn_marker: Path) -> int:
    return len(spawn_marker.read_text(encoding="utf-8").splitlines())


def test_real_worker_utf8_protocol_roundtrip_chinese_english_and_mixed(
    venv_python, tmp_path
) -> None:
    """第一条测试启动真实 worker：中文/英文/混合查询与 GBK 不可编码字符往返。"""
    client, _, query_marker = _client(
        venv_python,
        tmp_path,
        "good",
        _GOOD_BODY,
        client_assignment="_client_module.ArxivMcpClient = _FixedClient",
    )
    try:
        papers = client.search("量子 纠错", max_results=1)
        english = client.search("quantum error correction", max_results=1)
        mixed = client.search("量子 + English 混合查询", max_results=1)
    finally:
        client.close()

    # 父进程必须读到合法 UTF-8：emoji 与非断行连字符原样到达
    assert "🚀" in papers[0].title
    assert "‑" in papers[0].title
    assert "🚀" in papers[0].authors[0]
    assert "🚀" in papers[0].abstract
    assert english[0].arxiv_id == "2401.12345v2"
    assert mixed[0].arxiv_id == "2401.12345v2"
    # 父进程 → worker 的中文查询经 UTF-8 完整到达（同一客户端复用进程）
    queries = query_marker.read_text(encoding="utf-8").splitlines()
    assert "量子 纠错" in queries
    assert "quantum error correction" in queries
    assert "量子 + English 混合查询" in queries


def test_worker_module_missing_maps_to_arxiv_startup(venv_python, tmp_path) -> None:
    """真实模块缺失路径：子进程无法导入 bridges 包 → -m 启动即退。

    与握手超时（arxiv_handshake）区分：stderr 含 ModuleNotFoundError 时
    分类为 arxiv_startup，满足验收"模块不存在、握手超时分别得到不同
    稳定错误码"。
    """
    client, _, _ = _client(venv_python, tmp_path, "mod-missing", _MODULE_MISSING_BODY)
    try:
        with pytest.raises(ArxivMcpError) as exc_info:
            client.search("量子 纠错")
    finally:
        client.close()
    assert exc_info.value.code == "arxiv_startup"


def test_missing_worker_module_maps_to_arxiv_startup(tmp_path) -> None:
    client = ArxivMcpProcessClient(
        python_executable=str(tmp_path / "no-such-python.exe")
    )
    try:
        with pytest.raises(ArxivMcpError) as exc_info:
            client.search("量子 纠错")
    finally:
        client.close()
    assert exc_info.value.code == "arxiv_startup"


def test_handshake_timeout_maps_to_arxiv_handshake(venv_python, tmp_path) -> None:
    # 超时阈值须显著大于 worker 冷启动（带 sitecustomize 导入链约 0.4s，
    # 并行负载下更长），否则超时与启动竞态、断言失真。
    client, spawn_marker, _ = _client(
        venv_python,
        tmp_path, "devnull", _DEVNULL_BODY, handshake_timeout=1.5
    )
    started = time.monotonic()
    try:
        with pytest.raises(ArxivMcpError) as exc_info:
            client.search("量子 纠错")
    finally:
        client.close()
    assert exc_info.value.code == "arxiv_handshake"
    assert time.monotonic() - started < 8.0
    assert _spawn_count(spawn_marker) == 2  # 原尝试 + 恰一次安全重启


def test_worker_exits_before_ready_maps_to_arxiv_handshake(venv_python, tmp_path) -> None:
    client, spawn_marker, _ = _client(venv_python, tmp_path, "explode", _EXPLODE_BODY)
    try:
        with pytest.raises(ArxivMcpError) as exc_info:
            client.search("量子 纠错")
    finally:
        client.close()
    assert exc_info.value.code == "arxiv_handshake"
    assert _spawn_count(spawn_marker) == 2


def test_invalid_ready_payload_maps_to_arxiv_handshake(venv_python, tmp_path) -> None:
    client, spawn_marker, _ = _client(venv_python, tmp_path, "bogus-ready", _BOGUS_READY_BODY)
    try:
        with pytest.raises(ArxivMcpError) as exc_info:
            client.search("量子 纠错")
    finally:
        client.close()
    assert exc_info.value.code == "arxiv_handshake"
    assert _spawn_count(spawn_marker) == 2


def test_worker_crash_mid_request_maps_to_arxiv_worker_exit(venv_python, tmp_path) -> None:
    client, spawn_marker, _ = _client(
        venv_python,
        tmp_path,
        "crash-all",
        _CRASH_ALL_BODY,
        client_assignment="_client_module.ArxivMcpClient = _AlwaysCrashClient",
    )
    try:
        with pytest.raises(ArxivMcpError) as exc_info:
            client.search("量子 纠错")
    finally:
        client.close()
    assert exc_info.value.code == "arxiv_worker_exit"
    assert _spawn_count(spawn_marker) == 2  # 重启一次后仍失败才返回终态


def test_invalid_json_response_maps_to_arxiv_parse(venv_python, tmp_path) -> None:
    client, spawn_marker, _ = _client(venv_python, tmp_path, "corrupt", _CORRUPT_BODY)
    try:
        with pytest.raises(ArxivMcpError) as exc_info:
            client.search("量子 纠错")
    finally:
        client.close()
    assert exc_info.value.code == "arxiv_parse"
    assert _spawn_count(spawn_marker) == 1  # 协议错误不是崩溃，不触发重启


def test_network_timeout_payload_maps_to_arxiv_timeout(venv_python, tmp_path) -> None:
    client, spawn_marker, _ = _client(
        venv_python,
        tmp_path,
        "timeout",
        _TIMEOUT_BODY,
        client_assignment="_client_module.ArxivMcpClient = _TimeoutClient",
    )
    try:
        with pytest.raises(ArxivMcpError) as exc_info:
            client.search("量子 纠错")
    finally:
        client.close()
    assert exc_info.value.code == "arxiv_timeout"
    assert _spawn_count(spawn_marker) == 1  # worker 存活，载荷错误直接透传


def test_worker_crash_restarts_once_and_recovers(venv_python, tmp_path) -> None:
    client, spawn_marker, _ = _client(
        venv_python,
        tmp_path,
        "crash-once",
        _CRASH_ONCE_BODY,
        client_assignment="_client_module.ArxivMcpClient = _CrashOnceClient",
    )
    try:
        papers = client.search("量子 纠错", max_results=1)
    finally:
        client.close()
    assert papers and "🚀" in papers[0].title
    assert _spawn_count(spawn_marker) == 2  # 首次崩溃后自动重启一次并恢复


def test_warmup_pre_spawns_worker_and_first_search_has_no_cold_start(
    venv_python, tmp_path
) -> None:
    """Issue 04：预热完成 spawn + 握手后，首次搜索复用同一进程。

    握手（含 httpx 导入）在启动期完成，首次搜索不再付冷启动；预热
    失败（此处用永不输出 ready 的替身）只记日志，懒启动兜底正常。
    """
    client, spawn_marker, _ = _client(
        venv_python,
        tmp_path,
        "warmup-good",
        _GOOD_BODY,
        client_assignment="_client_module.ArxivMcpClient = _FixedClient",
    )
    try:
        assert client.warmup() is True
        assert client.warmup_successes == 1
        assert _spawn_count(spawn_marker) == 1
        papers = client.search("量子 纠错", max_results=1)
        assert "🚀" in papers[0].title
        assert _spawn_count(spawn_marker) == 1  # 首次搜索复用预热进程
    finally:
        client.close()

    failing_client, failing_marker, _ = _client(
        venv_python,
        tmp_path,
        "warmup-fail",
        _DEVNULL_BODY,
        handshake_timeout=1.5,  # 须显著大于 worker 冷启动（与握手超时用例一致）
    )
    try:
        assert failing_client.warmup() is False
        assert failing_client.warmup_failures == 1
        assert _spawn_count(failing_marker) == 1  # 预热只尝试一次
    finally:
        failing_client.close()


def test_response_timeout_maps_to_arxiv_timeout(venv_python, tmp_path) -> None:
    client, spawn_marker, _ = _client(
        venv_python,
        tmp_path,
        "hang",
        _HANG_BODY,
        client_assignment="_client_module.ArxivMcpClient = _HangingClient",
        response_timeout=0.5,
    )
    started = time.monotonic()
    try:
        with pytest.raises(ArxivMcpError) as exc_info:
            client.search("量子 纠错")
    finally:
        client.close()
    assert exc_info.value.code == "arxiv_timeout"
    assert time.monotonic() - started < 5.0
    assert _spawn_count(spawn_marker) == 2


def test_failure_log_shows_stage_exit_code_but_not_query_or_abstracts(
    venv_python, tmp_path, caplog
) -> None:
    """验收 6：终态失败日志含阶段/退出码/耗时/重启次数，不含查询与摘要。"""
    import logging

    client, _, _ = _client(
        venv_python,
        tmp_path,
        "crash-log",
        _CRASH_ALL_BODY,
        client_assignment="_client_module.ArxivMcpClient = _AlwaysCrashClient",
    )
    with caplog.at_level(logging.WARNING, logger="bridges.arxiv_mcp.process"):
        try:
            with pytest.raises(ArxivMcpError):
                client.search("量子 纠错")
        finally:
            client.close()
    records = [r for r in caplog.records if r.name == "bridges.arxiv_mcp.process"]
    terminal = "".join(r.message for r in records if "终态失败" in r.message)
    assert terminal, "应输出终态失败诊断日志"
    assert "code=arxiv_worker_exit" in terminal
    assert "exit_code=" in terminal
    assert "restarts=1" in terminal
    assert "elapsed=" in terminal
    assert "stage=search" in terminal  # 搜索中途崩溃不得误报 handshake 阶段
    # 脱敏：查询正文、论文摘要与任何秘密不得进入日志
    assert "量子 纠错" not in terminal
    assert "Transformer" not in terminal
    assert all("量子 纠错" not in (r.getMessage() or "") for r in records)


def test_cancel_terminates_request_within_two_seconds(venv_python, tmp_path) -> None:
    client, _, _ = _client(
        venv_python,
        tmp_path,
        "hang-cancel",
        _HANG_BODY,
        client_assignment="_client_module.ArxivMcpClient = _HangingClient",
    )
    stop_event = threading.Event()
    raised: list[ArxivMcpError] = []

    def _run() -> None:
        try:
            client.search("量子 纠错", max_results=1, stop_event=stop_event)
        except ArxivMcpError as exc:
            raised.append(exc)

    thread = threading.Thread(target=_run)
    started = time.monotonic()
    thread.start()
    time.sleep(0.3)
    stop_event.set()
    thread.join(timeout=2.0)
    elapsed = time.monotonic() - started
    try:
        assert not thread.is_alive(), "取消后搜索未在 2 秒内终止/回收"
        assert elapsed < 2.0
        assert raised and raised[0].code == "arxiv_cancelled"
    finally:
        client.close()
    # 注：取消路径可能在 worker 尚未执行 sitecustomize 时即被终止，
    # 因此不在此断言 spawn 计数；重启语义由崩溃用例单独覆盖。
