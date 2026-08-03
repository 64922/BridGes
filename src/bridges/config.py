"""BridGes 统一运行配置 Schema。

源码环境、统一 CLI、Docker 与 Podman 四条运行载体都通过 ``BRIDGES_``
前缀的环境变量读取本 Schema；迁移期同时接受旧前缀 ``SCIENCE_COMPANION_``，
新前缀优先。秘密既可以直接提供，也可以用 ``<NAME>_FILE`` 文件引用。

配置不读取任何 ``.env`` 文件：源码与容器部署都不要求用户创建 ``.env``；
未配置的项使用下方声明的安全默认值。
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_PREFIX = "BRIDGES_"
LEGACY_ENV_PREFIX = "SCIENCE_COMPANION_"


def _env_aliases(name: str) -> AliasChoices:
    """返回环境变量别名：新前缀优先，迁移期兼容旧前缀。"""
    return AliasChoices(f"{ENV_PREFIX}{name}", f"{LEGACY_ENV_PREFIX}{name}")


class Settings(BaseSettings):
    """Single source of truth for runtime configuration.

    Defaults are safe for local development. Production carriers override them
    through environment variables or Compose manifests. No ``.env`` file is
    read or required by any carrier.
    """

    model_config = SettingsConfigDict(
        populate_by_name=True,
        extra="ignore",
        validate_default=True,
    )

    # Runtime identity
    environment: str = Field(
        default="development", validation_alias=_env_aliases("ENVIRONMENT")
    )

    # API server binding
    api_host: str = Field(default="127.0.0.1", validation_alias=_env_aliases("API_HOST"))
    api_port: int = Field(default=8000, validation_alias=_env_aliases("API_PORT"))

    # Web server binding / mode
    web_port: int = Field(default=3000, validation_alias=_env_aliases("WEB_PORT"))
    web_dev: bool = Field(default=True, validation_alias=_env_aliases("WEB_DEV"))

    # Security
    session_cookie_secure: bool = Field(
        default=False, validation_alias=_env_aliases("SESSION_COOKIE_SECURE")
    )

    # Secrets: direct env var or *_FILE file reference.
    secret_key: SecretStr | None = Field(
        default=None, validation_alias=_env_aliases("SECRET_KEY")
    )
    database_url: SecretStr | None = Field(
        default=None, validation_alias=_env_aliases("DATABASE_URL")
    )
    redis_url: SecretStr | None = Field(
        default=None, validation_alias=_env_aliases("REDIS_URL")
    )
    object_storage_url: SecretStr | None = Field(
        default=None, validation_alias=_env_aliases("OBJECT_STORAGE_URL")
    )
    qwen_api_key: SecretStr | None = Field(
        default=None, validation_alias=_env_aliases("QWEN_API_KEY")
    )

    # 账户级凭据存储后端：源码环境用操作系统凭据库（os），容器用自动生成
    # 主密钥保护的加密凭据卷（encrypted-volume）。容器 Compose 显式设置
    # encrypted-volume；其余环境默认 os。
    credential_backend: str = Field(
        default="os", validation_alias=_env_aliases("CREDENTIAL_BACKEND")
    )

    # Qwen routing configuration.
    qwen_workspace_id: str | None = Field(
        default=None, validation_alias=_env_aliases("QWEN_WORKSPACE_ID")
    )
    qwen_region: str = Field(
        default="cn-beijing", validation_alias=_env_aliases("QWEN_REGION")
    )
    qwen_cassette_dir: str | None = Field(
        default=None, validation_alias=_env_aliases("QWEN_CASSETTE_DIR")
    )
    qwen_record_cassettes: bool = Field(
        default=False, validation_alias=_env_aliases("QWEN_RECORD_CASSETTES")
    )
    qwen_force_stub: bool = Field(
        default=False, validation_alias=_env_aliases("QWEN_FORCE_STUB")
    )

    _SECRET_FIELDS: frozenset[str] = frozenset(
        {"secret_key", "database_url", "redis_url", "object_storage_url", "qwen_api_key"}
    )

    def model_post_init(self, __context: Any) -> None:
        """Resolve any ``<FIELD>_FILE`` secret references after initial parsing."""
        self._load_secret_files()

    def _secret_file_env(self, field_name: str) -> str | None:
        """返回 ``<FIELD>_FILE`` 环境变量：新前缀优先，迁移期兼容旧前缀。"""
        env_name = f"{ENV_PREFIX}{field_name.upper()}_FILE"
        value = os.environ.get(env_name)
        if value is not None:
            return value
        return os.environ.get(f"{LEGACY_ENV_PREFIX}{field_name.upper()}_FILE")

    def _load_secret_files(self) -> None:
        for field_name in self._SECRET_FIELDS:
            file_path = self._secret_file_env(field_name)
            if not file_path:
                continue
            try:
                secret = Path(file_path).read_text(encoding="utf-8").strip()
            except OSError as exc:
                env_var = f"{ENV_PREFIX}{field_name.upper()}_FILE"
                raise ValueError(
                    f"Cannot read secret file for {field_name} ({env_var}={file_path}): {exc}"
                ) from exc
            object.__setattr__(self, field_name, SecretStr(secret))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached runtime settings object.

    Callers that need to reload settings (e.g. tests) can use
    ``get_settings.cache_clear()``.
    """
    return Settings()
