"""收尾基础设施守卫：快速失败边界与脱敏产物（issue 01 AC3/AC7）。

- 数据父目录不可写时，10 秒内以「数据目录不可写」失败，而不是笼统超时；
- 显式端口被占用时立即失败，提示先停止占用进程；
- 失败产物（日志/trace）不包含授权码、测试密钥、密码、Cookie 或 API Key。
"""

from __future__ import annotations

import time

import pytest
from conftest import TEST_AUTH_CODE, TEST_PASSWORD, TEST_SECRET_KEY, sanitize

_FAST_FAIL_WINDOW_SECONDS = 10.0


def test_unwritable_data_dir_fails_within_10_seconds(api_server, tmp_path) -> None:
    # 数据库父路径落在「文件之下」：mkdir 必然失败，属于不可写/不可创建场景
    blocker = tmp_path / "blocker"
    blocker.write_text("occupied", encoding="utf-8")
    data_dir = blocker / "data"

    started = time.monotonic()
    with pytest.raises(RuntimeError, match="数据目录不可写"):
        api_server(data_dir)
    elapsed = time.monotonic() - started

    assert elapsed < _FAST_FAIL_WINDOW_SECONDS, (
        f"数据目录不可写应在 {_FAST_FAIL_WINDOW_SECONDS:.0f} 秒内失败，"
        f"实际耗时 {elapsed:.1f} 秒"
    )


def test_explicit_occupied_port_fails_fast(api_server, unique_data_dir) -> None:
    # 先占用一个端口（保留 socket 句柄），再把该端口显式传给 API
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
        sock.listen(1)

        started = time.monotonic()
        with pytest.raises(RuntimeError, match="端口被占用"):
            api_server(unique_data_dir, port=port)
        elapsed = time.monotonic() - started

    assert elapsed < _FAST_FAIL_WINDOW_SECONDS


def test_sanitize_removes_secrets_from_artifacts() -> None:
    # 模拟 API Key 刻意短于 32 位：仓库秘密扫描器把 sk- + 32 位视为真实
    # 密钥（测试假值不命中高置信度模式，与扫描器注释约定一致）
    payload = (
        f"code={TEST_AUTH_CODE} key={TEST_SECRET_KEY} password={TEST_PASSWORD} "
        "api=sk-abcdef1234567890 "
        "session_token=abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_- "
        "Bearer eyJhbGciOiJIUzI1NiJ9.xyz987"
    )
    cleaned = sanitize(payload)

    assert TEST_AUTH_CODE not in cleaned
    assert TEST_SECRET_KEY not in cleaned
    assert TEST_PASSWORD not in cleaned
    assert "sk-" not in cleaned
    assert "<auth-code>" in cleaned
    assert "<secret-key>" in cleaned
    assert "<password>" in cleaned
    assert "<api-key>" in cleaned
    assert "session_token=<cookie>" in cleaned
    assert "Bearer <token>" in cleaned
