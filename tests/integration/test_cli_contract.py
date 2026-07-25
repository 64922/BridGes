"""Tests for the unified CLI and production runtime contract.

The seam under test: the CLI provides doctor/migrate/api/web/serve commands and
does not require or detect Conda in production runtime.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def run_cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    # Explicit env overrides the parent environment so tests can simulate
    # production runtimes without Conda variables.
    merged = {**os.environ, **(env or {})}
    return subprocess.run(
        [sys.executable, "-m", "science_companion.cli.main", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=merged,
        check=False,
    )


def test_doctor_reports_basic_diagnostics() -> None:
    result = run_cli("doctor")
    assert result.returncode == 0
    assert "version:" in result.stdout
    assert "doctor: passed" in result.stdout


def test_doctor_reports_no_conda_dependency_when_not_in_conda() -> None:
    # Simulate a production-like environment where Conda variables are unset.
    # Empty values are falsy, matching the runtime contract check in the CLI.
    env = {
        **os.environ,
        "CONDA_PREFIX": "",
        "CONDA_DEFAULT_ENV": "",
        "CONDA_SHLVL": "",
    }
    result = run_cli("doctor", env=env)
    assert result.returncode == 0
    assert "ok: no Conda dependency at runtime" in result.stdout


def test_migrate_exits_successfully() -> None:
    result = run_cli("migrate")
    assert result.returncode == 0
    assert "migrate:" in result.stdout


def test_api_help_exits_successfully() -> None:
    result = run_cli("api", "--help")
    assert result.returncode == 0
    assert "Run the API process" in result.stdout


def test_web_help_exits_successfully() -> None:
    result = run_cli("web", "--help")
    assert result.returncode == 0
    assert "Run the Web process" in result.stdout


def test_serve_help_exits_successfully() -> None:
    result = run_cli("serve", "--help")
    assert result.returncode == 0
    assert "Start Web and API" in result.stdout
