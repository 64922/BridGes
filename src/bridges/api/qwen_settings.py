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

from bridges.ai.model_metadata import BailianModelMetadataSource
from bridges.ai.qwen_client import QwenApiClient
from bridges.ai.run_model_config import (
    RunModelConfigProvider,
    RunModelConfigSnapshot,
)
from bridges.credentials.global_credential import resolve_settings_qwen_key
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


def active_qwen_key(request: Request) -> SecretStr | None:
    """当前可用的 Qwen 密钥：文件/环境变量优先，其次凭据库。"""
    return resolve_settings_qwen_key(runtime_settings(request), credential_store(request))


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
    - 轮换生产组合共享的 Qwen 客户端与知识库向量化端口的密钥引用；
    - 清除未配置全局凭据的健康门标记。

    后台执行器是独立进程，其凭据在下次启动时由启动流程从凭据库解析到新值
    （ADR-0024：全局 Key 轮换后重启相关服务即生效）。
    """
    settings = runtime_settings(request)
    if settings is not None:
        request.app.state.settings = settings.model_copy(
            update={"qwen_api_key": SecretStr(key.get_secret_value())}
        )
    client = getattr(request.app.state, "qwen_client", None)
    if client is not None and hasattr(client, "replace_api_key"):
        client.replace_api_key(key)
    for port_name in ("embedding_port",):
        port = getattr(request.app.state, port_name, None)
        if port is not None and hasattr(port, "replace_api_key"):
            port.replace_api_key(key)
    request.app.state.qwen_key_error = None


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
    "credential_store",
    "last_validation",
    "probe_http_client",
    "record_validation",
    "run_model_config_provider",
    "runtime_settings",
    "validation_state",
]
