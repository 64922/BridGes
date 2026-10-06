"""工单 39：真实网关装配（从 OS 凭据库读取运行期 Qwen 凭据）。

供评测入口脚本共用；缺少凭据或适配器时抛出 ``RuntimeError``，由入口
记录 ``inconclusive`` 并非零退出，不伪装通过。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def build_real_gateway(data_dir: Path) -> tuple[Any, Any]:
    """从 OS 凭据库读取运行期 Qwen 凭据并装配生产组合；缺凭据报错。"""

    from pydantic import SecretStr

    from bridges.ai.production import build_production_composition
    from bridges.config import get_settings
    from bridges.credentials.runtime_resolver import RuntimeCredentialResolver
    from bridges.credentials.store import build_credential_store

    settings = get_settings()
    store = build_credential_store(settings, data_dir, namespace="runtime")
    resolver = RuntimeCredentialResolver(settings=settings, credential_store=store)
    resolved = resolver.qwen_api_key()
    if not resolved.configured or resolved.value is None:
        raise RuntimeError("missing_global_qwen_key")
    composition = build_production_composition(
        settings.model_copy(
            update={"qwen_api_key": SecretStr(resolved.value.get_secret_value())}
        )
    )
    if not composition.global_key_configured:
        raise RuntimeError("missing_global_qwen_key")
    if composition.gateway.get_adapter("qwen_text_chat", "1") is None:
        raise RuntimeError("missing_text_chat_adapter")
    return composition, resolver


def build_eval_quota(composition: Any) -> Any:
    """评测调用使用的真实额度快照：优先当前激活配置，否则出厂批准矩阵。"""

    from bridges.ai.model_quota import build_run_model_quota
    from bridges.ai.run_model_config import factory_run_model_config

    provider = getattr(composition, "model_config_provider", None)
    snapshot = provider.snapshot() if provider is not None else factory_run_model_config()
    return build_run_model_quota(snapshot)


def receipt_run_context() -> Any:
    """评测收据的真实运行上下文（独立账户/会话/运行标识）。"""

    from bridges.chat.run_executor import chat_run_context

    return chat_run_context("eval39-account", "eval39-receipt", "eval39-receipt-run")


class RetryingStructuredGateway:
    """评测侧有界重试包装：只重试结构化输出的瞬时解析失败。

    推理模型偶发把全部输出额度用于思考、正文为空导致 JSON 解析失败；
    评测收据需要可完成的真实调用。每次调用最多 ``max_attempts`` 次，
    重试记录在 ``retry_log`` 中随收据公开；其他错误码不重试。
    """

    def __init__(self, wrapped: Any, *, max_attempts: int = 5) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts 必须为正整数。")
        self._wrapped = wrapped
        self.max_attempts = max_attempts
        self.retry_log: list[dict[str, Any]] = []

    def invoke(self, *args: Any, **kwargs: Any) -> Any:
        payload = kwargs.get("payload")
        if payload is None:
            payload = next((arg for arg in args if isinstance(arg, dict)), None)
        task = str((payload or {}).get("task") or "")
        result = None
        for attempt in range(1, self.max_attempts + 1):
            result = self._wrapped.invoke(*args, **kwargs)
            error_code = getattr(result, "error_code", None)
            if error_code != "structured_output_parse_failed":
                if attempt > 1:
                    self.retry_log.append(
                        {
                            "capability": args[0] if args else "",
                            "task": task,
                            "attempts": attempt,
                            "succeeded": True,
                        }
                    )
                return result
            self.retry_log.append(
                {
                    "capability": args[0] if args else "",
                    "task": task,
                    "attempt": attempt,
                    "succeeded": False,
                    "error_code": error_code,
                }
            )
        #: 有界重试耗尽后必须显式失败：把失败当成功返回会让调用方收到
        #: 空载荷（`output=None`）并误报为数据校验错误，掩盖真实失败原因。
        raise RuntimeError(
            f"structured_output_parse_failed（有界重试 {self.max_attempts} 次后仍失败）"
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)


__all__ = [
    "RetryingStructuredGateway",
    "build_eval_quota",
    "build_real_gateway",
    "receipt_run_context",
]
