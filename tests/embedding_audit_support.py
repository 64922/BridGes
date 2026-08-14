"""Issue 15 共享测试基建：假 Qwen 客户端与真实接缝装配（供各目录测试复用）。

``FakeQwenClient`` 只模拟供应商 HTTP 行为（成功/异常/畸形响应），
``make_embedding_seam`` 按生产接线装配「注册矩阵 + QwenEmbeddingAdapter +
ModelGateway + Issue 10 recorder」的真实接缝。确定性 Embedding 只用于
合同校验路径，不用于证明真实调用（Issue 15 非目标）。
"""

from __future__ import annotations

from typing import Any

from pydantic import SecretStr

from bridges.ai.capability_registry import CapabilityRegistry
from bridges.ai.embedding_adapter import QwenEmbeddingAdapter
from bridges.ai.fixed_models import EMBEDDING_MODEL_ID
from bridges.ai.model_gateway import ModelGateway
from bridges.ai.ports import ModelRunLockRecorder
from bridges.ai.production import register_builtin_capabilities
from bridges.ai.sqlite_recorder import SqliteModelRunLockRecorder
from bridges.ingestion.embedding import (
    EMBEDDING_CAPABILITY_NAME,
    EMBEDDING_CAPABILITY_VERSION,
    EMBEDDING_DIMENSIONS,
    QwenEmbeddingPort,
)
from bridges.storage.database import BridgesDatabase


def embedding_response(
    texts: list[str],
    *,
    model: str = EMBEDDING_MODEL_ID,
    dimensions: int = EMBEDDING_DIMENSIONS,
    count: int | None = None,
    empty: bool = False,
) -> dict[str, Any]:
    """构造一次默认成功的 /embeddings 响应（可按数量/维度/空向量编排）。"""
    total = len(texts) if count is None else count
    return {
        "model": model,
        "data": [
            {
                "index": index,
                "embedding": [] if empty else [0.1] * dimensions,
            }
            for index in range(total)
        ],
        "usage": {"total_tokens": total * 4, "prompt_tokens": total * 4},
    }


class FakeQwenClient:
    """记录 embeddings 请求的假客户端；脚本按调用顺序弹出响应或异常。"""

    def __init__(self, fail_with: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.script: list[dict[str, Any] | Exception] = []
        self.fail_with = fail_with

    def embeddings(self, request_body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(dict(request_body))
        if self.script:
            outcome = self.script.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        if self.fail_with is not None:
            raise self.fail_with
        return embedding_response(request_body["input"])


def make_embedding_seam(
    database: BridgesDatabase,
    client: FakeQwenClient,
    *,
    recorder: ModelRunLockRecorder | None = None,
) -> tuple[QwenEmbeddingPort, ModelRunLockRecorder]:
    """按生产接线装配 Embedding 接缝：注册矩阵 + 真实 adapter + recorder。"""
    registry = CapabilityRegistry()
    register_builtin_capabilities(registry)
    gateway = ModelGateway(registry)
    gateway.register_adapter(
        EMBEDDING_CAPABILITY_NAME,
        EMBEDDING_CAPABILITY_VERSION,
        QwenEmbeddingAdapter(client),
    )
    lock_recorder = recorder or SqliteModelRunLockRecorder(database)
    port = QwenEmbeddingPort(
        api_key=SecretStr("test-global-key"),
        gateway=gateway,
        recorder=lock_recorder,
    )
    return port, lock_recorder
