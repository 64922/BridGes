"""运行期凭据解析入口（工单 01）。

与主模型运行配置（``bridges.ai.run_model_config.RunModelConfigProvider``）共用
同一套合同：**共享真相源 + 每次使用前重读 + 读取失败回落进程内缓存**。区别只在
真相源不同——凭据的真相源是操作系统凭据库（``runtime`` 命名空间），不是数据库
状态表，因此密钥正文仍然只存在于凭据库与进程内存里。

为什么需要它：设置页保存成功后，API 进程只轮换自己那份运行期配置，后台执行器
（worker）是独立进程，此前它读的是**启动时**解析出的 ``Settings`` 快照，于是
"提示已保存、后台任务却继续拿旧密钥失败"。本模块让需要凭据的进程按运行重读，
从而"保存即生效"有同一处可依赖的语义。

优先级（与 ADR-0024/ADR-0031 一致）：显式环境变量或 ``<FIELD>_FILE`` 引用优先，
凭据库兜底。环境变量在进程生命周期内不会变，因此环境提供时不再读凭据库；
``source`` 字段把"当前真正生效的是哪一个"如实报告给用户，避免页面在环境变量
遮蔽凭据库值时仍然显示"已配置"却解释不了行为。

纪律：解析失败绝不抛异常、绝不把凭据正文写进 ``load_error``；读取异常只记类型名。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from pydantic import SecretStr

from bridges.config import Settings, secret_environment_source
from bridges.credentials.ids import (
    AMAP_BROWSER_MAP_CREDENTIAL_ID,
    AMAP_BROWSER_MAP_ITEM,
    AMAP_WEB_SERVICE_CREDENTIAL_ID,
    AMAP_WEB_SERVICE_ITEM,
    GLOBAL_QWEN_CREDENTIAL_ID,
    QWEN_ITEM,
    RUNTIME_TAVILY_CREDENTIAL_ID,
    SETTINGS_TAVILY_CREDENTIAL_ID,
    TAVILY_ITEM,
)
from bridges.credentials.store import CredentialStoreError, CredentialStorePort

#: 读取凭据时按"未配置"处理的异常：凭据库不可读不该升级为业务请求失败。
_STORE_ERRORS = (CredentialStoreError, OSError, ValueError)


class CredentialSource(StrEnum):
    """一个凭据项当前生效的来源。"""

    ENVIRONMENT = "environment"
    CREDENTIAL_STORE = "credential_store"
    NONE = "none"


@dataclass(frozen=True)
class ResolvedCredential:
    """一次凭据解析结果：值（可能为空）与当前生效来源。"""

    value: SecretStr | None
    source: CredentialSource

    @property
    def configured(self) -> bool:
        return self.value is not None


@dataclass(frozen=True)
class ResolvedBrowserMapPair:
    """浏览器地图凭据对（JS API Key + 安全密钥，必须成对生效）。"""

    js_api_key: SecretStr | None
    security_js_code: SecretStr | None
    source: CredentialSource

    @property
    def configured(self) -> bool:
        return self.js_api_key is not None and self.security_js_code is not None


_NOT_CONFIGURED = ResolvedCredential(None, CredentialSource.NONE)


class RuntimeCredentialResolver:
    """按运行重读运行期凭据的唯一入口。

    - 每次调用都尝试从共享真相源（凭据库）重读，因此其他进程保存的新值在
      本进程的下一次调用即可见；
    - 真相源不可读时回落本进程最近一次成功解析的值，并通过 ``load_error``
      给出不含秘密的中文原因，绝不因为凭据库异常中断调用方。
    """

    def __init__(
        self,
        *,
        settings: Settings | None,
        credential_store: CredentialStorePort | None,
    ) -> None:
        self._settings = settings
        self._store = credential_store
        self._cache: dict[str, ResolvedCredential] = {}
        self._load_error: str | None = None

    @property
    def load_error(self) -> str | None:
        """最近一次读取失败的中文原因（读取成功时为 None）。"""
        return self._load_error

    def qwen_api_key(self) -> ResolvedCredential:
        """全局百炼运行凭据（设置页「Qwen 凭据」卡与后台任务共用）。"""
        return self._resolve(QWEN_ITEM, self._read_qwen)

    def tavily_api_key(self) -> ResolvedCredential:
        """Tavily 搜索凭据。"""
        return self._resolve(TAVILY_ITEM, self._read_tavily)

    def amap_web_service_key(self) -> ResolvedCredential:
        """高德 Web 服务 Key（服务端路线与地点查询）。"""
        return self._resolve(AMAP_WEB_SERVICE_ITEM, self._read_amap_web_service)

    def amap_browser_map_pair(self) -> ResolvedBrowserMapPair:
        """浏览器地图凭据对；两项必须同时可用才算已配置。"""
        resolved = self._resolve(AMAP_BROWSER_MAP_ITEM, self._read_amap_browser_map)
        pair = _decode_pair(resolved.value) if resolved.value is not None else None
        if pair is None:
            return ResolvedBrowserMapPair(None, None, resolved.source)
        return ResolvedBrowserMapPair(
            SecretStr(pair["api_key"]),
            SecretStr(pair["security_js_code"]),
            resolved.source,
        )

    def _resolve(
        self, item: str, reader: Callable[[], ResolvedCredential]
    ) -> ResolvedCredential:
        try:
            resolved = reader()
        except _STORE_ERRORS as exc:
            self._load_error = (
                f"读取运行期凭据失败（{exc.__class__.__name__}），"
                "继续使用本进程上一次生效的值。"
            )
            return self._cache.get(item, _NOT_CONFIGURED)
        self._load_error = None
        if resolved.configured:
            self._cache[item] = resolved
        return resolved

    def _read_qwen(self) -> ResolvedCredential:
        return self._read_environment_first(
            "qwen_api_key", "QWEN_API_KEY", (GLOBAL_QWEN_CREDENTIAL_ID,)
        )

    def _read_tavily(self) -> ResolvedCredential:
        return self._read_environment_first(
            "tavily_api_key",
            "TAVILY_API_KEY",
            (SETTINGS_TAVILY_CREDENTIAL_ID, RUNTIME_TAVILY_CREDENTIAL_ID),
        )

    def _read_amap_web_service(self) -> ResolvedCredential:
        return self._read_environment_first(
            "amap_web_service_key",
            "AMAP_WEB_SERVICE_KEY",
            (AMAP_WEB_SERVICE_CREDENTIAL_ID,),
        )

    def _read_amap_browser_map(self) -> ResolvedCredential:
        js_key = self._environment_value("amap_js_api_key", "AMAP_JS_API_KEY")
        security_code = self._environment_value(
            "amap_security_js_code", "AMAP_SECURITY_JS_CODE"
        )
        if js_key is not None and security_code is not None:
            return ResolvedCredential(
                _encode_pair(js_key, security_code), CredentialSource.ENVIRONMENT
            )
        stored = self._stored(AMAP_BROWSER_MAP_CREDENTIAL_ID)
        if stored is not None and _decode_pair(stored) is not None:
            return ResolvedCredential(stored, CredentialSource.CREDENTIAL_STORE)
        # 兜底：组合根启动时可能已把凭据库的值装载进运行期 Settings。
        settings_key = self._settings_value("amap_js_api_key")
        settings_code = self._settings_value("amap_security_js_code")
        if settings_key is not None and settings_code is not None:
            return ResolvedCredential(
                _encode_pair(settings_key, settings_code),
                CredentialSource.CREDENTIAL_STORE,
            )
        return _NOT_CONFIGURED

    def _read_environment_first(
        self,
        field_name: str,
        environment_name: str,
        identifiers: tuple[str, ...],
    ) -> ResolvedCredential:
        """环境/文件优先，其次凭据库，最后回落运行期 Settings 里已装载的值。

        与 ADR-0024 一致：显式环境变量或 ``<FIELD>_FILE`` 引用优先，凭据库兜底。
        最后那一步是必要的——组合根启动时会把凭据库的值并入运行期 Settings，
        不认这一步就会让已配置的项在解析入口"消失"。
        """
        from_environment = self._environment_value(field_name, environment_name)
        if from_environment is not None:
            return ResolvedCredential(from_environment, CredentialSource.ENVIRONMENT)
        for identifier in identifiers:
            stored = self._stored(identifier)
            if stored is not None:
                return ResolvedCredential(stored, CredentialSource.CREDENTIAL_STORE)
        value = self._settings_value(field_name)
        if value is not None:
            return ResolvedCredential(value, CredentialSource.CREDENTIAL_STORE)
        return _NOT_CONFIGURED

    def _environment_value(
        self, field_name: str, environment_name: str
    ) -> SecretStr | None:
        """返回环境/文件提供的该字段；未由环境提供时返回 None。"""
        if secret_environment_source(environment_name) is None:
            return None
        return self._settings_value(field_name)

    def _settings_value(self, field_name: str) -> SecretStr | None:
        value = getattr(self._settings, field_name, None) if self._settings else None
        if not isinstance(value, SecretStr) or not value.get_secret_value().strip():
            return None
        return value

    def _stored(self, identifier: str) -> SecretStr | None:
        if self._store is None:
            return None
        value = self._store.get(identifier)
        if value is None or not value.get_secret_value().strip():
            return None
        return value


def _encode_pair(api_key: SecretStr, security_js_code: SecretStr) -> SecretStr:
    return SecretStr(
        json.dumps(
            {
                "api_key": api_key.get_secret_value(),
                "security_js_code": security_js_code.get_secret_value(),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )


def _decode_pair(secret: SecretStr) -> dict[str, str] | None:
    try:
        payload = json.loads(secret.get_secret_value())
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    api_key = payload.get("api_key")
    security_js_code = payload.get("security_js_code")
    if not isinstance(api_key, str) or not isinstance(security_js_code, str):
        return None
    if not api_key.strip() or not security_js_code.strip():
        return None
    return {"api_key": api_key, "security_js_code": security_js_code}


__all__ = [
    "CredentialSource",
    "ResolvedBrowserMapPair",
    "ResolvedCredential",
    "RuntimeCredentialResolver",
]
