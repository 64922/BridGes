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
from typing import Annotated, Any

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

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
    # Issue 39 AC2：CSRF 来源校验的显式允许来源（逗号分隔，如
    # "http://localhost:3000,http://127.0.0.1:3000"）。容器/反向代理部署
    # 时浏览器来源与 API Host 不同，必须显式配置；未配置时中间件按
    # X-Forwarded-* / Host / 环回规则推导（见 api/csrf.py）。
    # NoDecode：禁用 pydantic-settings 对复杂类型的 JSON 解码，
    # 由下方 field_validator 按逗号分隔解析。
    allowed_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=list, validation_alias=_env_aliases("ALLOWED_ORIGINS")
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
    # Issue 41：生产配置不再提供 qwen_force_stub 开关——任何环境都不允许
    # 把 StubQwenAdapter 注册到真实模型能力上伪装成功（AC3）。

    # Issue 33: QQ SMTP 任务提醒的邮件端点（默认 QQ 邮箱官方服务器；测试与
    # E2E 通过环境变量指向本地假邮件服务器）。授权码按账户加密保存，配置
    # 项不包含任何秘密。
    smtp_host: str = Field(default="smtp.qq.com", validation_alias=_env_aliases("SMTP_HOST"))
    smtp_port: int = Field(default=465, validation_alias=_env_aliases("SMTP_PORT"))
    smtp_starttls: bool = Field(
        default=False, validation_alias=_env_aliases("SMTP_STARTTLS")
    )
    smtp_plain: bool = Field(
        default=False,
        validation_alias=_env_aliases("SMTP_PLAIN"),
        description="明文 SMTP（仅本地假邮件服务器测试用，生产保持 SSL）。",
    )
    imap_host: str = Field(default="imap.qq.com", validation_alias=_env_aliases("IMAP_HOST"))
    imap_port: int = Field(default=993, validation_alias=_env_aliases("IMAP_PORT"))
    imap_plain: bool = Field(
        default=False,
        validation_alias=_env_aliases("IMAP_PLAIN"),
        description="明文 IMAP（仅本地假邮件服务器测试用，生产保持 SSL）。",
    )
    # Issue 10: 自发自收验证的收件确认窗口（秒）与受监督轮询间隔（秒）。
    # 窗口覆盖正常投递延迟（默认 120 秒，超时终态 receipt_timeout）；
    # 轮询间隔是窗口内的有界退避粒度（同一 IMAP 会话 + NOOP 保活）。
    smtp_verify_window_seconds: float = Field(
        default=120.0, validation_alias=_env_aliases("SMTP_VERIFY_WINDOW_SECONDS")
    )
    smtp_verify_tick_seconds: float = Field(
        default=2.0, validation_alias=_env_aliases("SMTP_VERIFY_TICK_SECONDS")
    )

    _SECRET_FIELDS: frozenset[str] = frozenset(
        {"secret_key", "database_url", "redis_url", "object_storage_url", "qwen_api_key"}
    )

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def _parse_allowed_origins(cls, value: Any) -> Any:
        """按逗号分隔解析 BRIDGES_ALLOWED_ORIGINS（列表项去除空白）。"""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

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
