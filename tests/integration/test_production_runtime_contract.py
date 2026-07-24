"""Production runtime contract tests.

The seam under test: production artifacts (Dockerfiles, compose files) do not
require or detect Conda.
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

PRODUCTION_ARTIFACTS = [
    REPO_ROOT / "apps" / "api" / "Dockerfile",
    REPO_ROOT / "apps" / "web" / "Dockerfile",
    REPO_ROOT / "infra" / "compose" / "docker-compose.yml",
]


def test_dockerfiles_do_not_reference_conda() -> None:
    for path in PRODUCTION_ARTIFACTS:
        text = path.read_text(encoding="utf-8").lower()
        assert "conda" not in text, f"{path} must not reference Conda"
        assert "environment.yml" not in text, f"{path} must not reference environment.yml"
