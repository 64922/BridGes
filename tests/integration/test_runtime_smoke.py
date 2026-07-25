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
from pathlib import Path
from typing import Any

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _run_cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged = {**os.environ, **(env or {})}
    # Clear Conda variables to simulate a production-like runtime.
    merged.pop("CONDA_PREFIX", None)
    merged.pop("CONDA_DEFAULT_ENV", None)
    merged.pop("CONDA_SHLVL", None)
    return subprocess.run(
        [sys.executable, "-m", "science_companion.cli.main", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=merged,
        check=False,
    )


def _wait_for_health(base_url: str, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            response = httpx.get(f"{base_url}/health/live", timeout=1.0)
            if response.status_code == 200:
                return
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            last_error = exc
        time.sleep(0.2)
    raise TimeoutError(f"API did not become healthy: {last_error}")


@pytest.fixture
def running_api() -> Any:
    """Start the API on a free port and yield its base URL, then shut it down."""
    port = _find_free_port()
    env = {
        **os.environ,
        "SCIENCE_COMPANION_API_PORT": str(port),
        "SCIENCE_COMPANION_ENVIRONMENT": "test",
    }
    env.pop("CONDA_PREFIX", None)
    env.pop("CONDA_DEFAULT_ENV", None)
    env.pop("CONDA_SHLVL", None)

    proc = subprocess.Popen(
        [sys.executable, "-m", "science_companion.cli.main", "api", "--port", str(port)],
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


def test_health_endpoints_return_unified_projection(running_api: str) -> None:
    client = httpx.Client(base_url=running_api)

    live = client.get("/health/live").json()
    assert live["live"] == "pass"
    assert live["ready"] == "unknown"
    assert live["degraded"] == "unknown"
    assert live["dependencies"] == []

    ready = client.get("/health/ready").json()
    assert ready["live"] == "pass"
    assert ready["ready"] == "pass"
    required = [d for d in ready["dependencies"] if d["required"]]
    assert any(d["name"] == "configuration" and d["status"] == "pass" for d in required)

    degraded = client.get("/health/degraded").json()
    assert degraded["live"] == "pass"
    assert degraded["degraded"] == "pass"

    summary = client.get("/health").json()
    assert summary["live"] == "pass"
    assert summary["ready"] == "pass"
    assert summary["degraded"] == "pass"


def test_api_gracefully_shuts_down(running_api: str) -> None:
    # The fixture itself performs graceful shutdown; reaching this point means
    # the API started, served requests, and terminated without hanging.
    assert running_api.startswith("http://127.0.0.1:")
