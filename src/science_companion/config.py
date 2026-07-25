"""Unified production configuration schema.

All four runtime carriers (manual split process, unified CLI, Docker, Podman)
read this schema through environment variables with the prefix
``SCIENCE_COMPANION_``. Secrets can be supplied directly or referenced via a
file using the ``<NAME>_FILE`` suffix, which is the recommended production path.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_PREFIX = "SCIENCE_COMPANION_"


class Settings(BaseSettings):
    """Single source of truth for runtime configuration.

    Defaults are safe for local development. Production carriers override them
    through environment variables or Compose manifests.
    """

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # Runtime identity
    environment: str = "development"

    # API server binding
    api_host: str = "127.0.0.1"
    api_port: int = 8000

    # Web server binding / mode
    web_port: int = 3000
    web_dev: bool = True

    # Security
    session_cookie_secure: bool = False

    # Secrets: direct env var or *_FILE file reference.
    secret_key: SecretStr | None = None
    database_url: SecretStr | None = None
    redis_url: SecretStr | None = None
    object_storage_url: SecretStr | None = None
    qwen_api_key: SecretStr | None = None

    _SECRET_FIELDS: frozenset[str] = frozenset(
        {"secret_key", "database_url", "redis_url", "object_storage_url", "qwen_api_key"}
    )

    def model_post_init(self, __context: Any) -> None:
        """Resolve any ``<FIELD>_FILE`` secret references after initial parsing."""
        self._load_secret_files()

    def _load_secret_files(self) -> None:
        for field_name in self._SECRET_FIELDS:
            env_name = f"{ENV_PREFIX}{field_name.upper()}_FILE"
            file_path = os.environ.get(env_name)
            if not file_path:
                continue
            try:
                secret = Path(file_path).read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise ValueError(
                    f"Cannot read secret file for {field_name} ({env_name}={file_path}): {exc}"
                ) from exc
            object.__setattr__(self, field_name, SecretStr(secret))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached runtime settings object.

    Callers that need to reload settings (e.g. tests) can use
    ``get_settings.cache_clear()``.
    """
    return Settings()
