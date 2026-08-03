"""流式聊天事件与适配器协议（Issue 11）。

流式路径独立于同步 ``invoke``：适配器逐块产出 ``StreamChunk``，网关把
块转换为携带不可变运行锁的 ``StreamEvent``，聊天服务负责落库。自动重试
不在流式路径内执行——聊天失败以"新建助手尝试"作为用户级重试机制。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol

from bridges.contracts.ai import CapabilityRecord, ModelRunLock
from bridges.contracts.workflows import RunContextEnvelope


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

    ``lock`` 在 done/error 事件上必填；delta 事件上为 None。
    """

    kind: str = "delta"
    delta: str = ""
    error_code: str | None = None
    error_message: str | None = None
    lock: ModelRunLock | None = None
    usage: dict[str, Any] | None = field(default=None, repr=False)


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
