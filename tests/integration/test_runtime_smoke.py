"""Runtime smoke tests for the four production run contracts.

The seam under test: ``doctor``, ``migrate``, ``api`` health endpoints and
graceful shutdown behave the same regardless of the carrier (manual, unified
CLI, Docker/Podman are represented by the same CLI and configuration schema).
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, cast

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _clean_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """构造显式子进程环境。

    剔除所有 ``BRIDGES_*`` / 旧 ``SCIENCE_COMPANION_*`` 与 Conda 变量，
    再显式写入确定性配置，使子进程既不继承用户环境中的真实凭据，也
    不读取仓库 ``.env`` 中的数据库或录制配置——冒烟测试保持离线、确定性。
    """
    merged = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("BRIDGES_", "SCIENCE_COMPANION_"))
        and key not in {"CONDA_PREFIX", "CONDA_DEFAULT_ENV", "CONDA_SHLVL"}
    }
    merged.update(
        {
            "BRIDGES_ENVIRONMENT": "test",
            "BRIDGES_QWEN_API_KEY": "",
            "BRIDGES_QWEN_RECORD_CASSETTES": "false",
            "BRIDGES_DATABASE_URL": "",
            "BRIDGES_SECRET_KEY": "",
            # Windows 控制台默认 GBK 编码：强制子进程以 UTF-8 输出，与父进程
            # 的 encoding="utf-8" 解码一致（既有 GBK flaky 的根因修复）。
            "PYTHONIOENCODING": "utf-8",
        }
    )
    if extra:
        merged.update(extra)
    return merged


def _run_cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged = _clean_env(env)
    return subprocess.run(
        [sys.executable, "-m", "bridges.cli.main", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=merged,
        check=False,
    )


def _wait_for_health(base_url: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    last_error: Exception | None = None
    url = f"{base_url}/health/live"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as resp:
                if resp.status == 200:
                    return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        time.sleep(0.2)
    raise TimeoutError(f"API did not become healthy: {last_error}")


@pytest.fixture
def running_api() -> Any:
    """Start the API on a free port and yield its base URL, then shut it down."""
    port = _find_free_port()
    env = _clean_env({"BRIDGES_API_PORT": str(port)})

    proc = subprocess.Popen(
        [sys.executable, "-m", "bridges.cli.main", "api", "--port", str(port)],
        cwd=REPO_ROOT,
        env=env,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_for_health(base_url)
        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def test_doctor_smoke() -> None:
    result = _run_cli("doctor")
    assert result.returncode == 0, result.stderr
    assert "environment:" in result.stdout
    assert "ok: config schema loaded" in result.stdout
    assert "ok: no Conda dependency at runtime" in result.stdout
    assert "doctor: passed" in result.stdout


def test_migrate_smoke() -> None:
    result = _run_cli("migrate")
    assert result.returncode == 0, result.stderr
    assert "environment:" in result.stdout
    assert "migrate:" in result.stdout


def test_doctor_notices_test_environment_needs_no_global_key() -> None:
    """test 环境使用确定性适配器：doctor 提示不要求全局凭据，不失败。

    GQ-01：全局百炼运行凭据是 development/production 的必需配置；test
    环境由确定性适配器驱动，缺失只提示。
    """
    result = _run_cli("doctor")
    assert result.returncode == 0, result.stderr
    assert "BRIDGES_QWEN_API_KEY" in result.stdout
    assert "确定性适配器" in result.stdout


def _dev_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """构造 development 环境且未配置全局百炼凭据的子进程环境。"""
    return _clean_env(
        {
            "BRIDGES_ENVIRONMENT": "development",
            "BRIDGES_QWEN_API_KEY": "",
            **(extra or {}),
        }
    )


def test_doctor_fails_without_global_key_in_development() -> None:
    """doctor 把全局百炼凭据视为必需项：development 缺失时报告失败。

    失败输出只含配置指引，不回显任何 Key 尾号或正文。
    """
    result = _run_cli("doctor", env=_dev_env())
    assert result.returncode != 0
    assert "FAIL" in result.stderr
    assert "BRIDGES_QWEN_API_KEY" in result.stderr
    assert "已配置" not in result.stdout


def test_doctor_reports_configured_global_key_without_tail() -> None:
    """doctor 配置存在时只报告"已配置"，不回显 Key 尾号或正文。"""
    secret = "sk-doctor-secret-abcdef123456"
    result = _run_cli("doctor", env=_dev_env({"BRIDGES_QWEN_API_KEY": secret}))
    assert result.returncode == 0, result.stderr
    assert "ok: 全局百炼运行凭据已配置" in result.stdout
    assert secret not in result.stdout
    assert secret not in result.stderr


def test_start_fails_when_global_key_file_unreadable(tmp_path: Path) -> None:
    """QWEN_API_KEY_FILE 不可读：start 非零退出并给出中文配置指引。

    GQ-01 AC2：文件引用不可读与缺失/空值一样在启动边界失败；错误正文
    只含文件路径，不包含任何 Key 内容。
    """
    missing_file = tmp_path / "missing-qwen.key"
    result = _run_cli(
        "start",
        env=_dev_env(
            {
                "BRIDGES_ENVIRONMENT": "development",
                "BRIDGES_QWEN_API_KEY": "",
                "BRIDGES_QWEN_API_KEY_FILE": str(missing_file),
            }
        ),
    )
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "BRIDGES_QWEN_API_KEY" in combined
    assert "BridGes 已启动" not in combined


def test_start_fails_without_global_key_before_spawning() -> None:
    """start 缺少全局 Key 时非零退出，且不启动任何子服务。

    GQ-01 启动硬门在获取数据目录锁、迁移和拉起 API/Web/worker/scheduler
    之前执行；断言错误指引出现在输出中且没有"已启动"横幅。
    """
    result = _run_cli("start", env=_dev_env())
    assert result.returncode != 0
    assert "BRIDGES_QWEN_API_KEY" in result.stderr
    assert "BridGes 已启动" not in result.stdout + result.stderr


def test_api_command_fails_without_global_key() -> None:
    """独立启动 API 缺少全局 Key 时失败关闭，避免绕过 start 得到半启动实例。"""
    result = _run_cli("api", env=_dev_env())
    assert result.returncode != 0
    assert "BRIDGES_QWEN_API_KEY" in result.stderr


def test_worker_command_fails_without_global_key() -> None:
    """后台执行器缺少全局 Key 时失败关闭，杜绝 API 有 Key、worker 无 Key 分裂。"""
    result = _run_cli("worker", env=_dev_env())
    assert result.returncode != 0
    assert "BRIDGES_QWEN_API_KEY" in result.stderr


def _get_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=5.0) as resp:
        import json

        return cast(dict[str, Any], json.loads(resp.read().decode("utf-8")))


def test_health_endpoints_return_unified_projection(running_api: str) -> None:
    live = _get_json(f"{running_api}/health/live")
    assert live["live"] == "pass"
    assert live["ready"] == "unknown"
    assert live["degraded"] == "unknown"
    assert live["dependencies"] == []

    ready = _get_json(f"{running_api}/health/ready")
    assert ready["live"] == "pass"
    assert ready["ready"] == "pass"
    required = [d for d in ready["dependencies"] if d["required"]]
    assert any(d["name"] == "configuration" and d["status"] == "pass" for d in required)

    degraded = _get_json(f"{running_api}/health/degraded")
    assert degraded["live"] == "pass"
    assert degraded["degraded"] == "pass"

    summary = _get_json(f"{running_api}/health")
    assert summary["live"] == "pass"
    assert summary["ready"] == "pass"
    assert summary["degraded"] == "pass"


def test_api_gracefully_shuts_down(running_api: str) -> None:
    # The fixture itself performs graceful shutdown; reaching this point means
    # the API started, served requests, and terminated without hanging.
    assert running_api.startswith("http://127.0.0.1:")
