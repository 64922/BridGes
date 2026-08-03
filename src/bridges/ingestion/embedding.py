"""向量化端口：固定 Embedding 模型与确定性替身（Issue 17）。

真实实现固定使用 ``text-embedding-v4``（ADR-0008 合同锁定）与 1024 维，
写入前做 L2 规范化并逐条校验维度；任何维度不符都抛中文错误，绝不写空
向量或降级模型。能力可用性由账户级探测快照把关：Embedding 未探测或
不可用时，上游走"向量索引不可用"路径，全文索引仍独立完成。
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any, Protocol

from pydantic import SecretStr

from bridges.ai.adapters import (
    AdapterError,
    AuthError,
    RateLimitError,
    RegionError,
    TransientError,
)
from bridges.ai.qwen_client import CassetteStore, QwenApiClient
from bridges.contracts.credentials import CapabilityProbeSummary, ProbeStatus
from bridges.credentials.matrix import EMBEDDING_MODEL_ID
from bridges.credentials.probes import CapabilityProbeService
from bridges.credentials.store import CredentialStoreError, CredentialStorePort

#: 合同锁定的向量维度（与固定矩阵参数一致）。
EMBEDDING_DIMENSIONS = 1024
#: 向量规范化合同。
NORMALIZATION = "l2"


class EmbeddingError(Exception):
    """向量化失败；message 为面向用户的中文原因。"""

    def __init__(self, message: str, retryable: bool = True) -> None:
        super().__init__(message)
        self.message = message
        self.retryable = retryable


class EmbeddingPort(Protocol):
    """向量化端口：生产实现真实调用 Qwen，测试注入确定性替身。"""

    def embed(self, account_id: str, texts: list[str]) -> list[list[float]]:
        """对一批文本返回规范化向量；失败抛 EmbeddingError。"""


class QwenEmbeddingPort:
    """按账户百炼 Key 调用固定 Embedding 模型的真实实现。

    每次调用按当前账户凭据构造独立客户端；Key 缺失、探测不可用由
    调用方（摄取服务）把关，本端口不自行决定降级。
    """

    def __init__(
        self,
        *,
        credential_store: CredentialStorePort,
        region: str = "cn-beijing",
        workspace_id: str | None = None,
        cassette_dir: str | None = None,
        record_mode: bool = False,
    ) -> None:
        self._credential_store = credential_store
        self._region = region
        self._workspace_id = workspace_id
        self._cassette_dir = cassette_dir
        self._record_mode = record_mode

    def embed(self, account_id: str, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            api_key = self._credential_store.get(account_id)
        except CredentialStoreError as exc:
            raise EmbeddingError(f"向量化失败：凭据存储不可用（{exc}）。") from exc
        if api_key is None:
            raise EmbeddingError("向量化失败：当前账户尚未配置百炼 Key。", retryable=False)
        client = self._build_client(api_key)
        try:
            response = client.embeddings(
                {
                    "model": EMBEDDING_MODEL_ID,
                    "input": texts,
                }
            )
        except AuthError as exc:
            raise EmbeddingError(
                "向量化失败：凭据无效或没有该模型权限，请检查百炼 Key。", retryable=False
            ) from exc
        except RegionError as exc:
            raise EmbeddingError("向量化失败：区域接入点不可达，请检查网络。") from exc
        except RateLimitError as exc:
            raise EmbeddingError("向量化失败：请求过于频繁（限流），请稍后重试。") from exc
        except TransientError as exc:
            raise EmbeddingError("向量化失败：服务暂时不可用或网络异常，请稍后重试。") from exc
        except AdapterError as exc:
            raise EmbeddingError(f"向量化失败：{_adapter_reason(exc)}。") from exc
        return _validate_and_normalize(response, texts)

    def _build_client(self, api_key: SecretStr) -> QwenApiClient:
        cassette_store = (
            CassetteStore(Path(self._cassette_dir)) if self._cassette_dir else None
        )
        return QwenApiClient(
            api_key=api_key,
            workspace_id=self._workspace_id,
            region=self._region,
            cassette_store=cassette_store,
            record_mode=self._record_mode,
        )


class DeterministicEmbeddingPort:
    """测试用确定性替身：按内容哈希生成固定种子向量。

    ``dimensions`` 可配置为错误维度以验证合同校验路径。
    """

    def __init__(self, dimensions: int = EMBEDDING_DIMENSIONS) -> None:
        self._dimensions = dimensions
        self.embed_calls: list[list[str]] = []

    def embed(self, account_id: str, texts: list[str]) -> list[list[float]]:
        self.embed_calls.append(list(texts))
        vectors: list[list[float]] = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            values = [
                ((digest[i % len(digest)] + i * 13) % 1000) / 1000
                for i in range(self._dimensions)
            ]
            vectors.append(_l2_normalize(values))
        return vectors


def _validate_and_normalize(
    response: dict[str, Any], texts: list[str]
) -> list[list[float]]:
    """解析 Embedding 响应：数量、顺序与维度都必须与请求一致。"""
    data = response.get("data")
    if not isinstance(data, list) or len(data) != len(texts):
        raise EmbeddingError("向量化失败：向量接口返回数量与请求不一致。", retryable=False)
    ordered = sorted(
        (item for item in data if isinstance(item, dict)),
        key=lambda item: int(item.get("index", 0)),
    )
    if len(ordered) != len(texts):
        raise EmbeddingError("向量化失败：向量接口返回缺少索引。", retryable=False)
    vectors: list[list[float]] = []
    for item in ordered:
        embedding = item.get("embedding")
        if not isinstance(embedding, list):
            raise EmbeddingError("向量化失败：向量接口返回了空向量。", retryable=False)
        values = [float(value) for value in embedding]
        if len(values) != EMBEDDING_DIMENSIONS:
            raise EmbeddingError(
                f"向量化失败：返回维度为 {len(values)}，与固定合同"
                f" {EMBEDDING_DIMENSIONS} 维不符。",
                retryable=False,
            )
        vectors.append(_l2_normalize(values))
    return vectors


def _l2_normalize(values: list[float]) -> list[float]:
    """L2 规范化：把向量归一为模长 1（合同要求，确定性实现）。"""
    norm = math.sqrt(sum(value * value for value in values))
    if norm == 0:
        return values
    return [value / norm for value in values]


def _adapter_reason(exc: AdapterError) -> str:
    code = getattr(exc, "code", "") or ""
    message = getattr(exc, "message", "") or ""
    if "authentication" in code or "permission" in code:
        return "凭据无效或没有该模型权限，请检查百炼 Key"
    return message.strip() or "接口调用异常"


def embedding_availability(
    probe_service: CapabilityProbeService, account_id: str
) -> tuple[bool, str | None, bool]:
    """按账户探测快照判定 Embedding 是否可用；返回 (可用, 中文原因, 是否已探测)。

    未探测、探测中、不可用都视为不可用并给出可操作原因——绝不以
    Stub 或空向量标记成功。
    """
    snapshot = probe_service.status_snapshot(account_id)
    summary = next(
        (
            item
            for item in snapshot
            if isinstance(item, CapabilityProbeSummary)
            and item.capability_id == "embedding"
        ),
        None,
    )
    if summary is None:
        return False, "尚未完成 Embedding 能力探测。", False
    if summary.status == ProbeStatus.AVAILABLE:
        return True, None, True
    if summary.status == ProbeStatus.PROBING:
        return False, "Embedding 能力正在探测中。", True
    if summary.status == ProbeStatus.UNAVAILABLE:
        reason = summary.message or "Embedding 能力不可用。"
        return False, f"Embedding 能力不可用：{reason}", True
    return False, summary.message or "Embedding 能力尚未探测。", True
