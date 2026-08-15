"""Issue 22/05：受限 worker 的固定命令、干净环境与 UTF-8 JSONL 边界测试。

mock 只用于断言固定命令与最小环境契约；协议真实行为由
``tests/closeout/test_arxiv_worker_reliability.py`` 用真实 worker 覆盖。
"""

from __future__ import annotations

import io
import json
import os
import sys

import pytest

from bridges.arxiv_mcp.client import ArxivMcpError
from bridges.arxiv_mcp.process import ArxivMcpProcessClient

#: worker 在真实流程中先输出 ready 握手行，再输出搜索结果行。
_READY_LINE = '{"type": "ready", "version": 1}\n'
_RESPONSE_LINE = json.dumps(
    {
        "ok": True,
        "papers": [
            {
                "arxiv_id": "2401.12345v2",
                "title": "Quantum Error Correction",
                "authors": ["Ada Lovelace"],
                "published_at": "2024-01-18T12:00:00+00:00",
                "abs_url": "https://arxiv.org/abs/2401.12345v2",
                "pdf_url": "https://arxiv.org/pdf/2401.12345v2",
                "abstract": "An official abstract.",
            }
        ],
    }
) + "\n"
_ERROR_RESPONSE_LINE = json.dumps(
    {
        "ok": False,
        "code": "arxiv_offline",
        "message": "arXiv 暂时不可用，请稍后重试。",
        "upstream_status": "http_5xx",
        "retryable": True,
    },
    ensure_ascii=False,
) + "\n"


class _FakeProcess:
    def __init__(self) -> None:
        self.pid = 1234
        self.returncode = None
        self.stdin = io.StringIO()
        self.stderr = io.StringIO()
        self.stdout = io.StringIO(_READY_LINE + _RESPONSE_LINE)

    def poll(self) -> None:
        return None

    def terminate(self) -> None:
        return None

    def wait(self, timeout: float) -> None:
        return None

    def kill(self) -> None:
        return None


class _ErrorProcess(_FakeProcess):
    def __init__(self) -> None:
        super().__init__()
        self.stdout = io.StringIO(_READY_LINE + _ERROR_RESPONSE_LINE)

def test_process_uses_only_fixed_worker_and_clean_environment(monkeypatch) -> None:
    calls: dict[str, object] = {}
    process = _FakeProcess()

    def fake_popen(command, **kwargs):
        calls["command"] = command
        calls.update(kwargs)
        return process

    monkeypatch.setattr("bridges.arxiv_mcp.process.subprocess.Popen", fake_popen)
    client = ArxivMcpProcessClient(python_executable="fixed-python")

    papers = client.search("量子 纠错", max_results=1)

    assert calls["command"] == ["fixed-python", "-m", "bridges.arxiv_mcp.worker"]
    assert calls["encoding"] == "utf-8"
    assert calls["stderr"] is not None
    env = calls["env"]
    assert isinstance(env, dict)
    assert env["PYTHONPATH"] == os.pathsep.join(sys.path)
    # 受控 UTF-8 模式：不依赖系统代码页
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["PYTHONUTF8"] == "1"
    # 最小环境：任何 BRIDGES_* / 模型密钥 / SMTP 秘密都不得进入子进程
    assert not any(key.startswith("BRIDGES_") for key in env)
    assert not any(key in {"QWEN_API_KEY", "DASHSCOPE_API_KEY", "SMTP_PASSWORD"} for key in env)
    # 协议：父进程先读 ready 握手，再发送 UTF-8 请求行并解析响应
    assert json.loads(process.stdin.getvalue()) == {"query": "量子 纠错", "max_results": 1}
    assert papers[0].abs_url == "https://arxiv.org/abs/2401.12345v2"
    client.close()


def test_process_preserves_worker_error_category_and_retryability(monkeypatch) -> None:
    process = _ErrorProcess()
    monkeypatch.setattr(
        "bridges.arxiv_mcp.process.subprocess.Popen",
        lambda command, **kwargs: process,
    )
    client = ArxivMcpProcessClient(python_executable="fixed-python")

    try:
        with pytest.raises(ArxivMcpError) as exc_info:
            client.search("公开主题")
    finally:
        client.close()

    assert exc_info.value.code == "arxiv_offline"
    assert exc_info.value.upstream_status == "http_5xx"
    assert exc_info.value.retryable is True


def test_warmup_spawns_and_handshakes_resident_worker_once(monkeypatch) -> None:
    """Issue 04：预热完成 spawn + 握手，首次搜索复用同一进程不再冷启动。"""
    process = _FakeProcess()
    spawns: list[list[str]] = []
    monkeypatch.setattr(
        "bridges.arxiv_mcp.process.subprocess.Popen",
        lambda command, **kwargs: (spawns.append(command), process)[1],
    )
    client = ArxivMcpProcessClient(python_executable="fixed-python")

    assert client.warmup() is True
    assert len(spawns) == 1  # 预热只 spawn 一次
    assert client.warmup_successes == 1
    assert client.warmup_failures == 0
    assert client.warmup() is True  # 已预热完成：幂等，不重复 spawn
    assert len(spawns) == 1

    papers = client.search("量子 纠错", max_results=1)
    assert papers[0].abs_url == "https://arxiv.org/abs/2401.12345v2"
    assert len(spawns) == 1  # 首次搜索复用预热好的进程
    client.close()


def test_warmup_failure_logs_and_counts_without_breaking_lazy_start(
    monkeypatch,
) -> None:
    """预热失败只记日志并返回 False；后续搜索仍走懒启动兜底。"""
    broken = _FakeProcess()
    broken.stdout = io.StringIO("")  # 永不输出 ready → 握手 EOF
    healthy = _FakeProcess()
    spawns: list[list[str]] = []
    monkeypatch.setattr(
        "bridges.arxiv_mcp.process.subprocess.Popen",
        lambda command, **kwargs: (
            spawns.append(command),
            broken if len(spawns) == 1 else healthy,
        )[1],
    )
    client = ArxivMcpProcessClient(python_executable="fixed-python")

    assert client.warmup() is False
    assert client.warmup_failures == 1
    assert client.warmup_successes == 0

    papers = client.search("量子 纠错", max_results=1)  # 懒启动兜底
    assert papers[0].abs_url == "https://arxiv.org/abs/2401.12345v2"
    assert len(spawns) == 2
    client.close()


def test_warmup_disabled_returns_false_without_spawn(monkeypatch) -> None:
    """Issue 04：预热开关（limits 单一来源）关闭时不 spawn 任何进程。"""
    from bridges.arxiv_mcp import limits as arxiv_limits

    monkeypatch.setattr(arxiv_limits, "ARXIV_WARMUP_ENABLED", False)
    spawns: list[list[str]] = []
    monkeypatch.setattr(
        "bridges.arxiv_mcp.process.subprocess.Popen",
        lambda command, **kwargs: (spawns.append(command), _FakeProcess())[1],
    )
    client = ArxivMcpProcessClient(python_executable="fixed-python")

    assert client.warmup() is False
    assert spawns == []


def test_warmup_unexpected_exception_counts_failure(monkeypatch) -> None:
    """Issue 04：预热期的任意异常都计入失败计数，只记日志不阻断启动。"""

    def _explode() -> None:
        raise RuntimeError("sidecar warmup explosion")

    monkeypatch.setattr(
        "bridges.arxiv_mcp.process.ArxivMcpProcessClient._ensure_process", _explode
    )
    client = ArxivMcpProcessClient(python_executable="fixed-python")

    assert client.warmup() is False
    assert client.warmup_failures == 1
    assert client.warmup_successes == 0
