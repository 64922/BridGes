"""向量化端口：固定 Embedding 模型与确定性替身（Issue 17，GQ-05 迁移）。

真实实现固定使用 ``text-embedding-v4``（ADR-0008 合同锁定）与 1024 维，
写入前做 L2 规范化并逐条校验维度；任何维度不符都抛中文错误，绝不写空
向量或降级模型。凭据来源是唯一的全局百炼运行凭据（GQ-01）：真实端口
从全局 Secret 构造客户端，可用性由运行时是否成功构造端口以及实际调用
结果决定——不再读取账户凭据或探测快照。调用失败时上游走"关键词检索
诚实降级"路径，全文索引仍独立完成，绝不伪装向量就绪。
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
from bridges.ai.fixed_models import EMBEDDING_MODEL_ID
from bridges.ai.qwen_client import CassetteStore, QwenApiClient

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
    """用全局百炼运行凭据调用固定 Embedding 模型的真实实现（GQ-05）。

    API 查询向量与 worker 摄取/重建共享同一端口构造（同一模型 ID、
    区域、workspace 与 cassette 策略）；``api_key`` 为 None 时（仅
    test 环境可能）调用直接给出指向服务运行配置的中文错误，由调用方
    走关键词检索诚实降级，本端口不自行决定降级。
    """

    def __init__(
        self,
        *,
        api_key: SecretStr | None,
        region: str = "cn-beijing",
        workspace_id: str | None = None,
        cassette_dir: str | None = None,
        record_mode: bool = False,
    ) -> None:
        self._api_key = api_key
        self._region = region
        self._workspace_id = workspace_id
        self._cassette_dir = cassette_dir
        self._record_mode = record_mode

    def embed(self, account_id: str, texts: list[str]) -> list[list[float]]:
        # account_id 保留以维持端口签名稳定；全局凭据共享，账户不参与
        # 客户端构造，账户隔离由调用方（摄取/检索）的 SQL 作用域承担。
        if not texts:
            return []
        if self._api_key is None or not self._api_key.get_secret_value():
            raise EmbeddingError(
                "向量化失败：未配置全局百炼运行凭据，请检查启动服务的全局配置。",
                retryable=False,
            )
        client = self._build_client(self._api_key)
        try:
            response = client.embeddings(
                {
                    "model": EMBEDDING_MODEL_ID,
                    "input": texts,
                }
            )
        except AuthError as exc:
            raise EmbeddingError(
                "向量化失败：全局百炼凭据无效或没有该模型权限，"
                "请检查启动服务的全局百炼配置与权限。",
                retryable=False,
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
        return "全局百炼凭据无效或没有该模型权限，请检查启动服务的全局配置与权限"
    return message.strip() or "接口调用异常"
