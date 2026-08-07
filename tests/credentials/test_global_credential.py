"""GQ-01 全局百炼运行凭据合同测试。

覆盖：全局凭据解析（直接值/空值/未配置）、必需校验的错误语义——错误
消息只含配置指引，绝不包含任何 Key 正文。
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from bridges.config import Settings
from bridges.credentials.global_credential import (
    GLOBAL_QWEN_KEY_GUIDANCE,
    GlobalQwenCredentialError,
    is_global_qwen_key_configured,
    require_global_qwen_key,
    resolve_global_qwen_key,
)


def test_resolve_returns_none_when_unconfigured() -> None:
    settings = Settings(qwen_api_key=None)
    assert resolve_global_qwen_key(settings) is None
    assert not is_global_qwen_key_configured(settings)


def test_resolve_returns_none_for_blank_value() -> None:
    settings = Settings(qwen_api_key=SecretStr(""))
    assert resolve_global_qwen_key(settings) is None
    assert not is_global_qwen_key_configured(settings)


def test_resolve_returns_secret_when_configured() -> None:
    settings = Settings(qwen_api_key=SecretStr("sk-global-key"))
    key = resolve_global_qwen_key(settings)
    assert key is not None
    assert key.get_secret_value() == "sk-global-key"
    assert is_global_qwen_key_configured(settings)


def test_require_raises_chinese_guidance_without_secret() -> None:
    settings = Settings(qwen_api_key=None)
    with pytest.raises(GlobalQwenCredentialError) as exc_info:
        require_global_qwen_key(settings)
    message = str(exc_info.value)
    assert message == GLOBAL_QWEN_KEY_GUIDANCE
    assert "BRIDGES_QWEN_API_KEY" in message
    assert "sk-" not in message


def test_require_returns_secret_when_configured() -> None:
    settings = Settings(qwen_api_key=SecretStr("sk-require-key"))
    key = require_global_qwen_key(settings)
    assert key.get_secret_value() == "sk-require-key"
