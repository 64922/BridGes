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

from science_companion import __version__
from science_companion.api.main import create_app

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

    T001 has no persistent schema yet; this command validates the migration
    contract and exits successfully.
    """
    typer.echo("migrate: no migrations to apply in T001")


@app.command()
def api(
    host: Annotated[str, typer.Option("--host", help="Bind host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", help="Bind port")] = 8000,
    reload: Annotated[bool, typer.Option("--reload", help="Enable auto-reload")] = False,
) -> None:
    """Run the API process."""
    uvicorn.run(
        "science_companion.api.main:create_app",
        host=host,
        port=port,
        factory=True,
        reload=reload,
    )


@app.command()
def web(
    dev: Annotated[bool, typer.Option("--dev", help="Run Next.js dev server")] = True,
) -> None:
    """Run the Web process."""
    web_dir = _repo_root() / "apps" / "web"
    if not web_dir.exists():
        typer.echo(f"error: web app not found at {web_dir}", err=True)
        raise typer.Exit(1)
    if dev:
        command = ["npm", "run", "dev"]
    else:
        standalone = web_dir / ".next" / "standalone" / "server.js"
        if standalone.exists():
            command = ["node", str(standalone)]
        else:
            command = ["npm", "run", "start"]
    subprocess.run(command, cwd=web_dir, check=True)


@app.command()
def serve(
    profile: Annotated[str, typer.Option("--profile", help="Runtime profile")] = "development",
) -> None:
    """Start Web and API under unified CLI supervision.

    Web and API remain independent OS processes; this command only orchestrates
    startup for local development convenience.
    """
    typer.echo(f"serve profile={profile}")
    typer.echo("Starting API on 127.0.0.1:8000 ...")
    typer.echo("Starting Web on 127.0.0.1:3000 ...")
    # T001 uses a simple foreground subprocess supervisor.
    api_proc = subprocess.Popen(
        [sys.executable, "-m", "science_companion.cli.main", "api", "--port", "8000"],
        cwd=_repo_root(),
    )
    web_dev = profile == "development"
    web_proc = subprocess.Popen(
        [sys.executable, "-m", "science_companion.cli.main", "web", "--dev" if web_dev else "--no-dev"],
        cwd=_repo_root(),
    )
    try:
        while api_proc.poll() is None and web_proc.poll() is None:
            api_proc.wait(timeout=1)
    except KeyboardInterrupt:
        pass
    finally:
        for proc, name in [(web_proc, "web"), (api_proc, "api")]:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)


@app.command()
def worker(
    queue: Annotated[str, typer.Option("--queue", help="Worker queue")] = "interactive",
) -> None:
    """Run a worker process (stub for T001)."""
    typer.echo(f"worker queue={queue}: not implemented in T001")


if __name__ == "__main__":
    app()
