"""Capability adapters translate logical capability invocations into vendor calls.

Adapters are intentionally stateless and scoped to a single capability call. The
model gateway decides whether and how to retry; adapters only report success or
a classified failure.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from bridges.contracts.ai import CapabilityRecord, ModelRunLock
from bridges.contracts.chat import (
    ChatStreamCareerData,
    ChatStreamHumanizerData,
    ChatStreamImageData,
    ChatStreamMcpData,
    ChatStreamStageData,
    ChatStreamVideoData,
)
from bridges.contracts.workflows import RunContextEnvelope

#: 网关→适配器通道的保留载荷键：网关按剩余预算截断后的单次调用超时
#: （秒）。只在调用方传入预算（RunBudget）时注入；支持该键的适配器把
#: 它透传给底层 HTTP 客户端覆盖默认超时，其余适配器忽略未知键。
#: 该键不在运行锁参数白名单之外泄漏任何正文/密钥。
REQUEST_TIMEOUT_SECONDS_KEY = "request_timeout_seconds"


class AdapterError(Exception):
    """Base class for adapter failures.

    Attributes:
        code: Stable error code for the gateway to classify the failure.
        message: Human-readable, non-leaking explanation.
        retryable: Whether the gateway may retry this failure.
    """

    def __init__(self, *, code: str, message: str, retryable: bool = False) -> None:
        self.code = code
        self.message = message
        self.retryable = retryable
        super().__init__(message)


class RateLimitError(AdapterError):
    """Vendor returned a rate-limit (429) response."""

    def __init__(self, message: str = "Rate limited.") -> None:
        super().__init__(code="rate_limit", message=message, retryable=True)


class TransientError(AdapterError):
    """Transient vendor or network failure (5xx, timeout)."""

    def __init__(self, message: str = "Transient failure.") -> None:
        super().__init__(code="transient", message=message, retryable=True)


class RegionError(AdapterError):
    """Region misconfiguration or regional unavailability."""

    def __init__(self, message: str = "Region error.") -> None:
        super().__init__(code="region_error", message=message, retryable=False)


class AuthError(AdapterError):
    """Authentication or authorization failure (401/403)."""

    def __init__(self, message: str = "Authentication failed.") -> None:
        super().__init__(code="auth_error", message=message, retryable=False)


class AdapterResult:
    """Normalized result of a single adapter call.

    This is a plain object rather than a Pydantic model because it is an
    internal adapter/gateway boundary, not a public contract.
    """

    def __init__(
        self,
        *,
        actual_model_id: str | None,
        output: dict[str, Any],
        usage: dict[str, Any] | None = None,
    ) -> None:
        self.actual_model_id = actual_model_id
        self.output = output
        self.usage = usage


@runtime_checkable
class CapabilityAdapter(Protocol):
    """Protocol for capability adapters.

    Implementations receive the resolved capability and the immutable run
    context, then either return an ``AdapterResult`` or raise an
    ``AdapterError`` subclass.
    """

    def call(
        self,
        capability: CapabilityRecord,
        run_context: RunContextEnvelope,
        payload: dict[str, Any],
    ) -> AdapterResult:
        ...


class StubQwenAdapter:
    """Deterministic adapter for tests and local stubs.

    Returns a fixed structured output without network access. The actual model id
    is taken from the capability record so the resulting lock remains honest.
    """

    def call(
        self,
        capability: CapabilityRecord,
        run_context: RunContextEnvelope,
        payload: dict[str, Any],
    ) -> AdapterResult:
        return AdapterResult(
            actual_model_id=capability.model_id,
            output={
                "capability": capability.name,
                "version": capability.version,
                "run_id": run_context.run_id,
                "payload_keys": sorted(payload.keys()),
                "stub": True,
                # Issue 11: 聊天纵向切片把该输出当作一次完整回答（单块流式
                # 降级路径）；生产环境不会注册 Stub 到真实模型能力上。
                "content": "这是一条来自本地替身模式的确定性测试回答。",
            },
            usage={"prompt_tokens": 0, "completion_tokens": 0},
        )


# ---------------------------------------------------------------------------
# 流式事件与流式适配器协议（Issue 11）
# ---------------------------------------------------------------------------
#
# 流式路径独立于同步 ``invoke``：适配器逐块产出 ``StreamChunk``，网关把
# 块转换为携带不可变运行锁的 ``StreamEvent``，聊天服务负责落库。自动重试
# 不在流式路径内执行——聊天失败以"新建助手尝试"作为用户级重试机制。

@dataclass
class StreamChunk:
    """适配器产出的单块流式结果。

    - ``delta``：增量正文（仅 kind="delta"）；
    - ``done``：正常结束，携带 usage 与实际模型 ID；
    - ``error``：中途失败，携带稳定错误码与非泄漏说明（kind="error"）。
    """

    kind: str = "delta"
    delta: str = ""
    error_code: str | None = None
    error_message: str | None = None
    usage: dict[str, Any] | None = None
    actual_model_id: str | None = None


@dataclass
class StreamEvent:
    """网关对外产出的流式事件：增量、成功或失败（含运行锁）。

    ``lock`` 在 done/error 事件上必填；delta 事件上为 None。``humanizer``
    只由人味化编排路径（Issue 28）产出：携带过程卡五态载荷；``career``
    只由生涯规划编排路径（Issue 29）产出：携带规划过程卡五态载荷；
    ``image`` 只由图片任务编排路径（Issue 31）产出：携带任务状态快照；
    ``video`` 只由视频任务编排路径（Issue 32）产出：携带视频任务状态
    快照；``mcp_call`` 只由 MCP 调用编排路径（Issue 36）产出：携带调用
    结果投影，均与 delta/done/error 同一事件流。
    """

    kind: str = "delta"
    delta: str = ""
    error_code: str | None = None
    error_message: str | None = None
    lock: ModelRunLock | None = None
    usage: dict[str, Any] | None = field(default=None, repr=False)
    stage: ChatStreamStageData | None = field(default=None, repr=False)
    humanizer: ChatStreamHumanizerData | None = field(default=None, repr=False)
    career: ChatStreamCareerData | None = field(default=None, repr=False)
    image: ChatStreamImageData | None = field(default=None, repr=False)
    video: ChatStreamVideoData | None = field(default=None, repr=False)
    mcp_call: ChatStreamMcpData | None = field(default=None, repr=False)


class StreamingCapabilityAdapter(Protocol):
    """支持逐块流式输出的能力适配器。

    连接阶段的失败（尚未产出任何块）直接抛出 ``AdapterError`` 子类；
    已经开始输出后的失败以 ``StreamChunk(kind="error")`` 返回。
    网关用 ``hasattr(adapter, "stream_call")`` 判断是否走流式路径。
    """

    def stream_call(
        self,
        capability: CapabilityRecord,
        run_context: RunContextEnvelope,
        payload: dict[str, Any],
    ) -> Iterator[StreamChunk]:
        ...
