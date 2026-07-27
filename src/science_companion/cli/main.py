"""Unified CLI: science-companion.

Supports the four production run contracts:
- manual split process (`api`, `web`)
- unified CLI (`serve`)
- Docker / Podman (compose files in infra/compose)

Conda `agent` is only used for local development; this CLI never detects or
requires a Conda environment at runtime.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer
import uvicorn
from pydantic import ValidationError

from science_companion import __version__
from science_companion.api.main import create_app
from science_companion.config import get_settings

app = typer.Typer(
    name="science-companion",
    help="Science Companion unified CLI",
    no_args_is_help=True,
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


@app.command()
def migrate() -> None:
    """Run database migrations.

    T008 has no persistent schema yet; this command validates the migration
    contract and configuration, then exits successfully.
    """
    _load_settings_or_exit()
    settings = get_settings()
    typer.echo(f"environment: {settings.environment}")
    typer.echo("migrate: no migrations to apply in T008")


@app.command()
def api(
    host: Annotated[str | None, typer.Option("--host", help="Bind host")] = None,
    port: Annotated[int | None, typer.Option("--port", help="Bind port")] = None,
    reload: Annotated[bool, typer.Option("--reload", help="Enable auto-reload")] = False,
) -> None:
    """Run the API process."""
    settings = get_settings()
    uvicorn.run(
        "science_companion.api.main:create_app",
        host=host or settings.api_host,
        port=port or settings.api_port,
        factory=True,
        reload=reload,
    )


@app.command()
def web(
    dev: Annotated[bool | None, typer.Option("--dev/--no-dev", help="Run Next.js dev server")] = None,
) -> None:
    """Run the Web process."""
    settings = get_settings()
    web_dir = _repo_root() / "apps" / "web"
    if not web_dir.exists():
        typer.echo(f"error: web app not found at {web_dir}", err=True)
        raise typer.Exit(1)

    use_dev = dev if dev is not None else settings.web_dev
    env = {**os.environ, "PORT": str(settings.web_port)}
    if use_dev:
        command = ["npm", "run", "dev"]
    else:
        standalone = web_dir / ".next" / "standalone" / "server.js"
        if standalone.exists():
            command = ["node", str(standalone)]
        else:
            command = ["npm", "run", "start"]
    subprocess.run(command, cwd=web_dir, check=True, env=env)


@app.command()
def serve(
    profile: Annotated[str, typer.Option("--profile", help="Runtime profile")] = "development",
) -> None:
    """Start Web and API under unified CLI supervision.

    Web and API remain independent OS processes; this command only orchestrates
    startup and graceful shutdown for local development convenience.
    """
    _load_settings_or_exit()
    settings = get_settings()
    typer.echo(f"serve profile={profile}")
    typer.echo(f"Starting API on {settings.api_host}:{settings.api_port} ...")
    typer.echo(f"Starting Web on 127.0.0.1:{settings.web_port} ...")

    api_proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "science_companion.cli.main",
            "api",
            "--host",
            settings.api_host,
            "--port",
            str(settings.api_port),
        ],
        cwd=_repo_root(),
    )

    web_env = {**os.environ, "PORT": str(settings.web_port)}
    web_proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "science_companion.cli.main",
            "web",
            "--dev" if profile == "development" else "--no-dev",
        ],
        cwd=_repo_root(),
        env=web_env,
    )

    def _terminate(proc: subprocess.Popen[bytes], name: str) -> None:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)

    exit_code = 0
    try:
        while api_proc.poll() is None and web_proc.poll() is None:
            try:
                api_proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                continue
    except KeyboardInterrupt:
        typer.echo("Shutting down...")
    finally:
        _terminate(web_proc, "web")
        _terminate(api_proc, "api")
        if exit_code == 0:
            if api_proc.returncode not in (0, None):
                exit_code = api_proc.returncode
            elif web_proc.returncode not in (0, None):
                exit_code = web_proc.returncode

    if exit_code != 0:
        raise typer.Exit(exit_code)


@app.command()
def worker(
    queue: Annotated[str, typer.Option("--queue", help="Worker queue")] = "interactive",
) -> None:
    """Run a worker process (stub for T001)."""
    typer.echo(f"worker queue={queue}: not implemented in T001")


if __name__ == "__main__":
    app()
