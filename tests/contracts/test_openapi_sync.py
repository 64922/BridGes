"""Contract test: committed OpenAPI and TypeScript types stay in sync.

The Python Pydantic models in src/science_companion/contracts remain the single
source of truth. openapi.json is the committed generation anchor; generated.ts
is derived from it.
"""

import json
from pathlib import Path

from science_companion.api.main import create_app

REPO_ROOT = Path(__file__).resolve().parents[2]
OPENAPI_PATH = REPO_ROOT / "openapi.json"
GENERATED_TS_PATH = REPO_ROOT / "packages" / "contracts" / "src" / "generated.ts"


def test_committed_openapi_matches_current_api() -> None:
    """Fail if openapi.json drifts from the current FastAPI app output."""
    assert OPENAPI_PATH.exists(), f"{OPENAPI_PATH} is missing; regenerate it from the API app"
    current = create_app().openapi()
    with OPENAPI_PATH.open("r", encoding="utf-8") as f:
        committed = json.load(f)
    assert committed == current, (
        f"{OPENAPI_PATH} is out of sync with the current API. "
        "Regenerate with: python -c 'from science_companion.api.main import create_app; ...'"
    )


def test_generated_types_file_exists() -> None:
    """Ensure TypeScript consumers have a committed contract artifact."""
    assert GENERATED_TS_PATH.exists(), f"{GENERATED_TS_PATH} is missing; regenerate from openapi.json"
