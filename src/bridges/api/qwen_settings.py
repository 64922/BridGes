"""设置页 Qwen 凭据与主模型验证的共用接线（V2 Issue 09）。

凭据路由（``/settings/credentials``）与模型路由（``/settings/models``）共享
同一批运行时对象与探测接线：候选密钥只用于本次探测的客户端，绝不写回
``app.state`` 之外的位置；成功替换后才就地轮换运行期凭据。

绝不泄漏明文：本模块不记录、不序列化密钥正文，也不把候选值放进异常文本。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import Request
from pydantic import SecretStr

from bridges.ai.model_gateway import ModelGateway
from bridges.ai.model_metadata import BailianModelMetadataSource
from bridges.ai.production import build_production_composition
from bridges.ai.qwen_client import QwenApiClient
from bridges.ai.run_model_config import (
    RunModelConfigProvider,
    RunModelConfigSnapshot,
)
from bridges.config import secret_environment_source
from bridges.credentials.runtime_resolver import (
    CredentialSource,
    RuntimeCredentialResolver,
)
from bridges.credentials.store import CredentialStorePort

#: 最近一次主模型验证报告的内存键（进程内，重启后由激活记录本身兜底）。
MODEL_VALIDATION_STATE_KEY = "model_validation_state"

#: 探测 http 客户端的兜底超时（秒；与凭据探测同一约定）。
_DEFAULT_PROBE_TIMEOUT_SECONDS = 8.0


def probe_http_client(request: Request) -> httpx.Client:
    """返回共享的探测 http 客户端（测试注入 MockTransport 用同一入口）。"""
    client = getattr(request.app.state, "credential_probe_http_client", None)
    if isinstance(client, httpx.Client):
        return client
    return httpx.Client(timeout=_DEFAULT_PROBE_TIMEOUT_SECONDS)


def run_model_config_provider(request: Request) -> RunModelConfigProvider:
    """返回进程内运行配置提供者；未装配时使用出厂快照（只读兜底）。"""
    provider = getattr(request.app.state, "run_model_config_provider", None)
    if isinstance(provider, RunModelConfigProvider):
        return provider
    provider = RunModelConfigProvider()
    request.app.state.run_model_config_provider = provider
    return provider


def active_run_model_config(request: Request) -> RunModelConfigSnapshot:
    """当前生效的主模型运行配置快照。"""
    return run_model_config_provider(request).snapshot()


def runtime_settings(request: Request) -> Any:
    """运行期 ``Settings``（未成功加载时为 None）。"""
    return getattr(request.app.state, "settings", None)


def credential_store(request: Request) -> CredentialStorePort | None:
    """运行期凭据库（未装配时为 None；读取失败按未配置处理）。"""
    store = getattr(request.app.state, "runtime_credential_store", None)
    return store if store is not None else None


def credential_resolver(request: Request) -> RuntimeCredentialResolver:
    """运行期凭据解析入口（每次调用重读共享真相源，失败回落进程内缓存）。

    只使用组合根注入的实例，不在这里缓存自建实例：保存凭据后运行期
    ``Settings`` 会被整体替换，缓存旧实例会让后续读取看到退出历史的值。
    """
    resolver = getattr(request.app.state, "credential_resolver", None)
    if isinstance(resolver, RuntimeCredentialResolver):
        return resolver
    return RuntimeCredentialResolver(
        settings=runtime_settings(request), credential_store=credential_store(request)
    )


def active_qwen_key(request: Request) -> SecretStr | None:
    """当前可用的 Qwen 密钥：文件/环境变量优先，其次凭据库。"""
    return credential_resolver(request).qwen_api_key().value


def qwen_key_shadowed(request: Request) -> bool:
    """当前生效的 Qwen 密钥是否由环境变量（或 ``_FILE`` 引用）提供。

    环境变量在进程生命周期内不变，且优先于凭据库：此时页面保存的新密钥不会
    生效（重启后仍然如此），保存成功文案必须如实说明，否则用户会看到"已保存"
    却仍然失败。
    """
    return (
        credential_resolver(request).qwen_api_key().source
        is CredentialSource.ENVIRONMENT
    )


def qwen_key_shadow_notice() -> str:
    """密钥被环境变量遮蔽时的中文说明：写清真正生效的来源与下一步操作。"""
    env_var = secret_environment_source("QWEN_API_KEY") or "BRIDGES_QWEN_API_KEY"
    return (
        f"已通过验证并保存到凭据库；但 Qwen 密钥当前由环境变量（{env_var}）提供，"
        "环境变量优先，本次保存的密钥不会生效。若要改用页面保存的值，"
        "请先移除该环境变量（或其 _FILE 引用）并重启 BridGes。"
    )


def build_metadata_source(
    request: Request, api_key: SecretStr
) -> BailianModelMetadataSource:
    """用给定密钥构造百炼元数据查询源（不修改任何运行期状态）。"""
    settings = runtime_settings(request)
    return BailianModelMetadataSource(
        http_client=probe_http_client(request),
        api_key=api_key,
        workspace_id=getattr(settings, "qwen_workspace_id", None),
        region=getattr(settings, "qwen_region", "cn-beijing") or "cn-beijing",
    )


def build_probe_client(request: Request, api_key: SecretStr) -> QwenApiClient:
    """用给定密钥构造探测用 Qwen 客户端（共享探测 http 客户端）。"""
    settings = runtime_settings(request)
    return QwenApiClient(
        api_key=api_key,
        workspace_id=getattr(settings, "qwen_workspace_id", None),
        region=getattr(settings, "qwen_region", "cn-beijing") or "cn-beijing",
        http_client=probe_http_client(request),
    )


def apply_qwen_key(request: Request, key: SecretStr) -> None:
    """把已验证的新密钥就地应用到运行期（V2 Issue 09）。

    - 更新进程内 ``Settings``（后续读取同一入口的调用方立即看到新值）；
    - 已有 Qwen 客户端就地轮换密钥；服务在没有全局凭据的情况下启动（非规范
      入口：只注册矩阵、不绑定适配器）时补齐真实适配器绑定，避免"验证通过
      但调用仍报未绑定适配器"；
    - 轮换知识库向量化端口的密钥引用（向量模型本身仍独立固定）；
    - 清除未配置全局凭据的健康门标记。

    只作用于当前进程；后台执行器是独立进程，但它每轮开始前从同一真相源重读凭据
    （``bridges.runtime.executor.BackgroundExecutor.refresh_credentials``），因此
    保存的值无需重启 worker 即在下一次轮询生效（工单 01，ADR-0031 相应修订）。
    """
    settings = runtime_settings(request)
    if settings is not None:
        settings = settings.model_copy(
            update={"qwen_api_key": SecretStr(key.get_secret_value())}
        )
        request.app.state.settings = settings
    _apply_runtime_qwen_credentials(request, key, settings)
    port = getattr(request.app.state, "embedding_port", None)
    if port is not None and hasattr(port, "replace_api_key"):
        port.replace_api_key(key)
    request.app.state.qwen_key_error = None


def _apply_runtime_qwen_credentials(
    request: Request, key: SecretStr, settings: Any
) -> None:
    """就地把新凭据应用到模型调用链（轮换已有客户端 / 补齐缺失适配器）。"""
    client = getattr(request.app.state, "qwen_client", None)
    if isinstance(client, QwenApiClient):
        client.replace_api_key(key)
        return
    gateway = getattr(request.app.state, "model_gateway", None)
    if settings is None or not isinstance(gateway, ModelGateway):
        return
    # 只补齐当前未绑定的能力：测试环境的确定性适配器绑定不被覆盖。
    composition = build_production_composition(
        settings,
        model_config_provider=getattr(
            request.app.state, "run_model_config_provider", None
        ),
    )
    for capability in composition.registry.list_active():
        if gateway.is_adapter_registered(capability.name, capability.version):
            continue
        adapter = composition.gateway.get_adapter(capability.name, capability.version)
        if adapter is not None:
            gateway.register_adapter(capability.name, capability.version, adapter)
    request.app.state.qwen_client = composition.qwen_client


def validation_state(request: Request) -> dict[str, Any]:
    """进程内验证状态（最近一次报告与验证时间）。"""
    state = getattr(request.app.state, MODEL_VALIDATION_STATE_KEY, None)
    if not isinstance(state, dict):
        state = {}
        setattr(request.app.state, MODEL_VALIDATION_STATE_KEY, state)
    return state


def record_validation(request: Request, report: Any) -> datetime:
    """记录一次（成功或失败的）主模型验证结论，返回验证时间。"""
    checked_at = datetime.now(UTC)
    validation_state(request).update(
        {"last_validated_at": checked_at, "report": report}
    )
    return checked_at


def last_validation(request: Request) -> tuple[datetime | None, Any]:
    """最近一次验证时间与报告（未验证过时为 (None, None)）。"""
    state = validation_state(request)
    checked_at = state.get("last_validated_at")
    return (checked_at if isinstance(checked_at, datetime) else None, state.get("report"))


__all__ = [
    "MODEL_VALIDATION_STATE_KEY",
    "active_qwen_key",
    "active_run_model_config",
    "apply_qwen_key",
    "build_metadata_source",
    "build_probe_client",
    "credential_resolver",
    "credential_store",
    "last_validation",
    "probe_http_client",
    "qwen_key_shadow_notice",
    "qwen_key_shadowed",
    "record_validation",
    "run_model_config_provider",
    "runtime_settings",
    "validation_state",
]
