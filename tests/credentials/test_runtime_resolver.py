"""运行期凭据解析入口的契约测试（工单 01 任务 3）。

合同与主模型运行配置（``RunModelConfigProvider``）一致：共享真相源 + 每次使用
前重读 + 读取失败回落进程内缓存；优先级是"环境变量/文件引用优先，凭据库兜底"，
并把当前生效来源如实报告出来（页面据此说明环境变量是否遮蔽了保存值）。
"""

from __future__ import annotations

import json

import pytest
from pydantic import SecretStr

from bridges.config import Settings
from bridges.credentials.ids import (
    AMAP_BROWSER_MAP_CREDENTIAL_ID,
    AMAP_WEB_SERVICE_CREDENTIAL_ID,
    GLOBAL_QWEN_CREDENTIAL_ID,
    SETTINGS_TAVILY_CREDENTIAL_ID,
)
from bridges.credentials.runtime_resolver import (
    CredentialSource,
    RuntimeCredentialResolver,
)
from bridges.credentials.store import (
    CredentialStoreError,
    CredentialStorePort,
    InMemoryCredentialStore,
)

_SECRET_FIELDS = (
    "BRIDGES_QWEN_API_KEY",
    "BRIDGES_QWEN_API_KEY_FILE",
    "BRIDGES_TAVILY_API_KEY",
    "BRIDGES_AMAP_WEB_SERVICE_KEY",
    "BRIDGES_AMAP_JS_API_KEY",
    "BRIDGES_AMAP_SECURITY_JS_CODE",
)


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _SECRET_FIELDS:
        monkeypatch.delenv(name, raising=False)


class _FlakyStore(CredentialStorePort):
    """可切换'读取失败'的替身：验证回落缓存与不含秘密的错误文案。"""

    def __init__(self) -> None:
        self.inner = InMemoryCredentialStore(namespace="runtime")
        self.failing = False

    def save(self, account_id: str, secret: SecretStr) -> None:
        self.inner.save(account_id, secret)

    def get(self, account_id: str) -> SecretStr | None:
        if self.failing:
            raise CredentialStoreError("凭据密文损坏，请重新保存密钥。")
        return self.inner.get(account_id)

    def delete(self, account_id: str) -> None:
        self.inner.delete(account_id)


def _settings(monkeypatch: pytest.MonkeyPatch, **values: str) -> Settings:
    for name, value in values.items():
        monkeypatch.setenv(f"BRIDGES_{name.upper()}", value)
    return Settings()


def test_environment_wins_over_the_credential_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InMemoryCredentialStore(namespace="runtime")
    store.save(GLOBAL_QWEN_CREDENTIAL_ID, SecretStr("stored-qwen-key"))
    resolver = RuntimeCredentialResolver(
        settings=_settings(monkeypatch, qwen_api_key="environment-qwen-key"),
        credential_store=store,
    )

    resolved = resolver.qwen_api_key()

    assert resolved.value == SecretStr("environment-qwen-key")
    assert resolved.source is CredentialSource.ENVIRONMENT


def test_store_is_used_when_the_environment_has_no_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InMemoryCredentialStore(namespace="runtime")
    store.save(GLOBAL_QWEN_CREDENTIAL_ID, SecretStr("stored-qwen-key"))
    store.save(SETTINGS_TAVILY_CREDENTIAL_ID, SecretStr("stored-tavily-key"))
    store.save(AMAP_WEB_SERVICE_CREDENTIAL_ID, SecretStr("stored-amap-key"))
    resolver = RuntimeCredentialResolver(
        settings=_settings(monkeypatch), credential_store=store
    )

    assert resolver.qwen_api_key().source is CredentialSource.CREDENTIAL_STORE
    assert resolver.tavily_api_key().value == SecretStr("stored-tavily-key")
    assert resolver.amap_web_service_key().value == SecretStr("stored-amap-key")


def test_saved_value_is_visible_to_another_process_without_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """另一个进程（设置页所在的 API 进程）保存后，本进程下一次读取即看到新值。"""
    store = InMemoryCredentialStore(namespace="runtime")
    store.save(GLOBAL_QWEN_CREDENTIAL_ID, SecretStr("first-key"))
    resolver = RuntimeCredentialResolver(
        settings=_settings(monkeypatch), credential_store=store
    )
    assert resolver.qwen_api_key().value == SecretStr("first-key")

    store.save(GLOBAL_QWEN_CREDENTIAL_ID, SecretStr("rotated-key"))

    assert resolver.qwen_api_key().value == SecretStr("rotated-key")


def test_unreadable_store_falls_back_to_the_last_value_and_reports_why(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _FlakyStore()
    store.save(GLOBAL_QWEN_CREDENTIAL_ID, SecretStr("last-good-key"))
    resolver = RuntimeCredentialResolver(
        settings=_settings(monkeypatch), credential_store=store
    )
    assert resolver.qwen_api_key().value == SecretStr("last-good-key")

    store.failing = True
    resolved = resolver.qwen_api_key()

    assert resolved.value == SecretStr("last-good-key")
    assert resolver.load_error is not None
    assert "CredentialStoreError" in resolver.load_error
    assert "last-good-key" not in resolver.load_error

    store.failing = False
    resolver.qwen_api_key()
    assert resolver.load_error is None


def test_configured_settings_without_a_store_are_reported_as_effective(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """组合根启动时把凭据库的值并入了 Settings：解析器必须仍认得它。

    否则启动装载过的值会在解析入口"消失"，页面对已配置的项反而显示未配置。
    """
    merged = Settings().model_copy(
        update={"amap_web_service_key": SecretStr("merged-key")}
    )
    resolver = RuntimeCredentialResolver(
        settings=merged,
        credential_store=InMemoryCredentialStore(namespace="runtime"),
    )

    resolved = resolver.amap_web_service_key()

    assert resolved.value == SecretStr("merged-key")
    assert resolved.source is CredentialSource.CREDENTIAL_STORE


def test_browser_map_pair_requires_both_halves_from_the_same_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InMemoryCredentialStore(namespace="runtime")
    store.save(
        AMAP_BROWSER_MAP_CREDENTIAL_ID,
        SecretStr(
            json.dumps({"api_key": "stored-js-key", "security_js_code": "stored-code"})
        ),
    )
    # 环境里只有半个配对（缺安全码）→ 退回凭据库里的完整配对。
    resolver = RuntimeCredentialResolver(
        settings=_settings(monkeypatch, amap_js_api_key="env-js-key"),
        credential_store=store,
    )
    pair = resolver.amap_browser_map_pair()
    assert pair.configured is True
    assert pair.js_api_key == SecretStr("stored-js-key")
    assert pair.source is CredentialSource.CREDENTIAL_STORE

    # 环境里配对完整 → 环境生效。
    resolver = RuntimeCredentialResolver(
        settings=_settings(
            monkeypatch,
            amap_js_api_key="env-js-key",
            amap_security_js_code="env-code",
        ),
        credential_store=store,
    )
    pair = resolver.amap_browser_map_pair()
    assert pair.js_api_key == SecretStr("env-js-key")
    assert pair.security_js_code == SecretStr("env-code")
    assert pair.source is CredentialSource.ENVIRONMENT


def test_browser_map_pair_ignores_malformed_stored_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InMemoryCredentialStore(namespace="runtime")
    store.save(AMAP_BROWSER_MAP_CREDENTIAL_ID, SecretStr("{not json"))
    resolver = RuntimeCredentialResolver(
        settings=_settings(monkeypatch), credential_store=store
    )

    pair = resolver.amap_browser_map_pair()

    assert pair.configured is False
    assert pair.source is CredentialSource.NONE


def test_missing_store_adapter_is_treated_as_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = RuntimeCredentialResolver(
        settings=_settings(monkeypatch), credential_store=None
    )

    assert resolver.qwen_api_key().configured is False
    assert resolver.tavily_api_key().source is CredentialSource.NONE
