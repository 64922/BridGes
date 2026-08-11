"""Configuration contract tests.

The seam under test: all four production runtimes read the same config schema
and secret-reference rules (direct env var or ``<NAME>_FILE``).
"""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from bridges.config import ENV_PREFIX, LEGACY_ENV_PREFIX, Settings, get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Generator[None, None, None]:
    """Settings are cached; clear between tests so env changes take effect."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_direct_environment_variables_are_loaded() -> None:
    env = {
        f"{ENV_PREFIX}ENVIRONMENT": "test",
        f"{ENV_PREFIX}API_HOST": "0.0.0.0",
        f"{ENV_PREFIX}API_PORT": "9000",
    }
    for key, value in env.items():
        os.environ[key] = value
    try:
        settings = Settings()
        assert settings.environment == "test"
        assert settings.api_host == "0.0.0.0"
        assert settings.api_port == 9000
    finally:
        for key in env:
            os.environ.pop(key, None)


def test_legacy_environment_prefix_remains_supported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(f"{ENV_PREFIX}ENVIRONMENT", raising=False)
    monkeypatch.setenv(f"{LEGACY_ENV_PREFIX}ENVIRONMENT", "test")

    settings = Settings()

    assert settings.environment == "test"


def test_qwen_api_key_from_environment_is_loaded_without_exposing_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(f"{ENV_PREFIX}QWEN_API_KEY", "test-qwen-key")
    settings = Settings()

    assert settings.qwen_api_key is not None
    assert "test-qwen-key" not in repr(settings.qwen_api_key)


def test_secret_key_file_reference_is_resolved(tmp_path: Path) -> None:
    secret_file = tmp_path / "secret.key"
    secret_file.write_text("file-secret-value", encoding="utf-8")

    env_key = f"{ENV_PREFIX}SECRET_KEY_FILE"
    old = os.environ.get(env_key)
    os.environ[env_key] = str(secret_file)
    try:
        settings = Settings()
        assert settings.secret_key is not None
        assert settings.secret_key.get_secret_value() == "file-secret-value"
    finally:
        if old is None:
            os.environ.pop(env_key, None)
        else:
            os.environ[env_key] = old


def test_secret_direct_value_and_file_reference_are_equivalent(tmp_path: Path) -> None:
    value = "equivalent-secret"
    secret_file = tmp_path / "secret2.key"
    secret_file.write_text(value, encoding="utf-8")

    direct = Settings(secret_key=SecretStr(value))
    assert direct.secret_key is not None
    assert direct.secret_key.get_secret_value() == value

    env_key = f"{ENV_PREFIX}SECRET_KEY_FILE"
    old = os.environ.get(env_key)
    os.environ[env_key] = str(secret_file)
    try:
        from_file = Settings()
        assert from_file.secret_key is not None
        assert from_file.secret_key.get_secret_value() == value
    finally:
        if old is None:
            os.environ.pop(env_key, None)
        else:
            os.environ[env_key] = old


def test_invalid_value_raises_validation_error() -> None:
    env_key = f"{ENV_PREFIX}API_PORT"
    old = os.environ.get(env_key)
    os.environ[env_key] = "not-a-port"
    try:
        with pytest.raises(ValidationError) as exc_info:
            Settings()
        # 错误信息按环境变量名（别名）定位，用户可直接据此修正配置。
        assert "API_PORT" in str(exc_info.value)
    finally:
        if old is None:
            os.environ.pop(env_key, None)
        else:
            os.environ[env_key] = old


def test_settings_repr_does_not_expose_secrets(tmp_path: Path) -> None:
    secret_file = tmp_path / "secret3.key"
    secret_file.write_text("leak-test", encoding="utf-8")

    env_key = f"{ENV_PREFIX}SECRET_KEY_FILE"
    old = os.environ.get(env_key)
    os.environ[env_key] = str(secret_file)
    try:
        settings = Settings()
        representation = repr(settings)
        assert "leak-test" not in representation
        assert "**********" in representation or "SecretStr" in representation
    finally:
        if old is None:
            os.environ.pop(env_key, None)
        else:
            os.environ[env_key] = old


def test_missing_secret_file_raises_clear_error(tmp_path: Path) -> None:
    missing = tmp_path / "missing.key"
    env_key = f"{ENV_PREFIX}SECRET_KEY_FILE"
    old = os.environ.get(env_key)
    os.environ[env_key] = str(missing)
    try:
        with pytest.raises(ValueError) as exc_info:
            Settings()
        assert "SECRET_KEY_FILE" in str(exc_info.value)
    finally:
        if old is None:
            os.environ.pop(env_key, None)
        else:
            os.environ[env_key] = old


# ---------------------------------------------------------------------------
# GQ-01：全局百炼运行凭据（BRIDGES_QWEN_API_KEY 与 *_FILE 引用同一 Secret）
# ---------------------------------------------------------------------------


def test_qwen_api_key_file_reference_resolves_to_same_secret(tmp_path: Path) -> None:
    """BRIDGES_QWEN_API_KEY_FILE 与直接环境变量解析到同一 Secret 配置。"""
    value = "global-qwen-key-from-file"
    secret_file = tmp_path / "qwen.key"
    secret_file.write_text(value, encoding="utf-8")

    env_key = f"{ENV_PREFIX}QWEN_API_KEY_FILE"
    old = os.environ.get(env_key)
    os.environ[env_key] = str(secret_file)
    try:
        settings = Settings()
        assert settings.qwen_api_key is not None
        assert settings.qwen_api_key.get_secret_value() == value
    finally:
        if old is None:
            os.environ.pop(env_key, None)
        else:
            os.environ[env_key] = old


def test_qwen_api_key_absent_when_neither_env_nor_file_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未配置 BRIDGES_QWEN_API_KEY 与 _FILE 时全局凭据为 None。"""
    monkeypatch.delenv(f"{ENV_PREFIX}QWEN_API_KEY", raising=False)
    monkeypatch.delenv(f"{ENV_PREFIX}QWEN_API_KEY_FILE", raising=False)
    settings = Settings()
    assert settings.qwen_api_key is None


def test_settings_repr_does_not_expose_qwen_api_key() -> None:
    """Settings repr 不暴露全局 Qwen Key 正文（GQ-01 AC1）。"""
    env_key = f"{ENV_PREFIX}QWEN_API_KEY"
    old = os.environ.get(env_key)
    os.environ[env_key] = "repr-leak-check-qwen"
    try:
        settings = Settings()
        representation = repr(settings)
        assert "repr-leak-check-qwen" not in representation
    finally:
        if old is None:
            os.environ.pop(env_key, None)
        else:
            os.environ[env_key] = old
