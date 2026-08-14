"""向量化端口：固定 Embedding 模型、网关审计接缝与确定性替身（Issue 15/17）。

真实实现固定使用 ``text-embedding-v4``（ADR-0008 合同锁定，模型 ID 来自
Issue 09 单一事实源 ``bridges.ai.fixed_models``）与 1024 维，写入前做 L2
规范化并逐条校验维度；任何维度不符都抛中文错误，绝不写空向量或降级模型。

Issue 15 起生产 Embedding 不再由知识库业务端口直接构造 ``QwenApiClient``
绕过审计：``QwenEmbeddingPort`` 经 ``ModelGateway`` 调用真实
``QwenEmbeddingAdapter``（每次实际远端批次由网关产生一条不可变运行锁），
并立即用 Issue 10 的 ``ModelRunLockRecorder`` 持久化锁与业务关联（账户、
业务 run、对象/索引版本/检索 round、批次序号与调用序号）。锁粒度与实际
HTTP/API 请求一一对应：空输入不发远端请求、不建锁；真实重试以新调用序号
新增锁，绝不覆盖旧锁。供应商成功但本地合同校验失败（数量/维度/空向量）时
锁如实记录远端调用状态，本地另记稳定失败原因。凭据来源是唯一的全局百炼
运行凭据（GQ-01）：真实端口从全局 Secret 构造客户端，可用性由运行时是否
成功构造端口以及实际调用结果决定。调用失败时上游走"关键词检索诚实降级"
路径，全文索引仍独立完成，绝不伪装向量就绪。
"""

from __future__ import annotations

import hashlib
import math
import time
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import SecretStr

from bridges.ai.errors import ModelRunLockError
from bridges.ai.model_gateway import ModelGateway
from bridges.ai.ports import (
    EMBEDDING_BATCH_COUNT_MISMATCH,
    EMBEDDING_CALL_FAILED,
    EMBEDDING_DIMENSION_MISMATCH,
    EMBEDDING_EMPTY_VECTOR,
    EMBEDDING_LOCK_PERSIST_FAILED,
    EMBEDDING_MISSING_CONTEXT,
    EMBEDDING_MISSING_RUN_LOCK,
    EmbeddingContext,
    ModelRunLockRecorder,
)
from bridges.contracts.ai import BusinessRef, ModelCallStatus
from bridges.contracts.workflows import RunContextEnvelope

#: 合同锁定的向量维度（与固定矩阵参数一致）。
EMBEDDING_DIMENSIONS = 1024
#: 向量规范化合同。
NORMALIZATION = "l2"
#: 注册表能力名与版本（Issue 09 矩阵单一事实源；与
#: ``bridges.ai.production.register_builtin_capabilities`` 一致）。
EMBEDDING_CAPABILITY_NAME = "qwen_embedding"
EMBEDDING_CAPABILITY_VERSION = "1"


class EmbeddingError(Exception):
    """向量化失败；message 为面向用户的中文原因，code 为稳定错误码。"""

    def __init__(
        self,
        message: str,
        retryable: bool = True,
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.retryable = retryable
        self.code = code


class EmbeddingPort(Protocol):
    """向量化端口：生产实现真实调用 Qwen，测试注入确定性替身。

    每个生产调用点必须携带 :class:`EmbeddingContext`（operation/run/对象/
    批次与调用序号）——新增调用点先分类后接线，不允许无审计上下文的调用。
    """

    def embed(
        self,
        account_id: str,
        texts: list[str],
        *,
        context: EmbeddingContext | None = None,
    ) -> list[list[float]]:
        """对一批文本返回规范化向量；失败抛 EmbeddingError。"""


class QwenEmbeddingPort:
    """用全局百炼运行凭据经网关调用固定 Embedding 模型的真实实现（GQ-05）。

    与 API 查询向量和 worker 摄取/重建共享同一端口构造（同一模型 ID、
    区域、workspace、cassette 策略与 recorder）；``api_key`` 为 None 时
    （仅 test 环境可能）调用直接给出指向服务运行配置的中文错误，由调用方
    走关键词检索诚实降级，本端口不自行决定降级。``gateway``/``recorder``
    未装配时调用以 ``embedding_missing_run_lock`` 失败关闭（审计闭环缺失
    不得静默继续）。
    """

    def __init__(
        self,
        *,
        api_key: SecretStr | None,
        gateway: ModelGateway | None = None,
        recorder: ModelRunLockRecorder | None = None,
    ) -> None:
        self._api_key = api_key
        self._gateway = gateway
        self._recorder = recorder

    def embed(
        self,
        account_id: str,
        texts: list[str],
        *,
        context: EmbeddingContext | None = None,
    ) -> list[list[float]]:
        # 空输入：不发远端请求、不建锁（Issue 15 合同）。
        if not texts:
            return []
        if context is None:
            raise EmbeddingError(
                "向量化失败：缺少调用上下文，无法审计本次 Embedding 调用。",
                retryable=False,
                code=EMBEDDING_MISSING_CONTEXT,
            )
        if self._api_key is None or not self._api_key.get_secret_value():
            raise EmbeddingError(
                "向量化失败：未配置全局百炼运行凭据，请检查启动服务的全局配置。",
                retryable=False,
            )
        if self._gateway is None or self._recorder is None:
            raise EmbeddingError(
                "向量化失败：Embedding 审计接缝未装配（缺少网关或运行锁 recorder）。",
                retryable=False,
                code=EMBEDDING_MISSING_RUN_LOCK,
            )
        run_context = RunContextEnvelope(
            run_id=context.run_id,
            account_id=account_id,
            project_id=context.project_id,
            workflow_name="knowledge_embedding",
            workflow_version="1",
            submitted_at=datetime.now(UTC),
        )
        started = time.perf_counter()
        result = self._gateway.invoke(
            EMBEDDING_CAPABILITY_NAME,
            EMBEDDING_CAPABILITY_VERSION,
            run_context,
            {
                # 输入文本只出现在 payload（adapter 需要），绝不进入运行锁：
                # 网关的参数捕获白名单不含 texts。
                "texts": texts,
                "batch_size": len(texts),
                "batch_ordinal": context.batch_ordinal,
                "normalization": NORMALIZATION,
                "dimensions": EMBEDDING_DIMENSIONS,
                "provider": "qwen",
            },
        )
        if result.lock is None:
            raise EmbeddingError(
                "向量化失败：模型调用没有产生运行锁。",
                retryable=True,
                code=EMBEDDING_MISSING_RUN_LOCK,
            )
        # 实测延迟在调用返回后计入锁参数（数值脱敏，不含任何正文）。
        lock = result.lock.model_copy(
            update={
                "parameters": {
                    **result.lock.parameters,
                    "latency_seconds": round(time.perf_counter() - started, 4),
                }
            }
        )
        try:
            self._recorder.record(
                lock,
                business_ref=BusinessRef(
                    object_type=context.object_type,
                    object_id=context.object_id,
                    operation=context.operation.value,
                    # 真实重调新增调用序号：调用方显式给出更大序号时以调用方
                    # 为准，否则取同一 run/operation/对象已有锁的下一序号
                    # （重试不覆盖旧锁，排序稳定）。
                    attempt_ordinal=self._next_attempt_ordinal(account_id, context),
                ),
            )
        except ModelRunLockError as exc:
            raise EmbeddingError(
                "向量化失败：模型运行锁持久化失败，审计闭环未闭合。",
                retryable=True,
                code=EMBEDDING_LOCK_PERSIST_FAILED,
            ) from exc

        if result.status == ModelCallStatus.SUCCESS and result.output is not None:
            vectors = result.output.get("vectors")
            # 供应商成功但本地合同校验失败：锁已按远端调用状态持久化，
            # 这里另记稳定失败原因（数量/维度/空向量），绝不写入错误向量。
            return _validate_vectors(vectors, texts)
        # 远端失败（鉴权/限流/区域/网络等）：锁已记录真实调用状态。
        raise EmbeddingError(
            _failure_message(result),
            retryable=result.status == ModelCallStatus.RETRYABLE_FAIL,
            code=result.error_code or EMBEDDING_CALL_FAILED,
        )

    def _next_attempt_ordinal(
        self, account_id: str, context: EmbeddingContext
    ) -> int:
        """同一 run/operation/对象已有锁的下一调用序号（真实重调新增序号）。

        调用方显式给出更大 ``call_ordinal`` 时以调用方为准；否则在已持久化
        锁的最大序号上加一——重试再次调用同一批次/对象时序号递增，旧失败
        锁永不覆盖；多批次顺序调用时各批序号与批次顺序一致。
        """
        next_ordinal = context.call_ordinal
        assert self._recorder is not None  # embed() 入口已失败关闭未装配接缝
        existing = self._recorder.list_locks_by_run(account_id, context.run_id)
        for persisted in existing:
            for ref in persisted.business_refs:
                if (
                    ref.object_id == context.object_id
                    and ref.operation == context.operation.value
                ):
                    next_ordinal = max(next_ordinal, ref.attempt_ordinal + 1)
        return next_ordinal


class DeterministicEmbeddingPort:
    """测试用确定性替身：按内容哈希生成固定种子向量。

    ``dimensions`` 可配置为错误维度以验证合同校验路径。替身不产生模型
    运行锁（Issue 15：确定性/fixture 只用于合同测试，不能证明真实 Qwen
    调用）；调用上下文记录在 ``embed_contexts`` 供测试断言分类接线。
    """

    def __init__(self, dimensions: int = EMBEDDING_DIMENSIONS) -> None:
        self._dimensions = dimensions
        self.embed_calls: list[list[str]] = []
        self.embed_contexts: list[EmbeddingContext | None] = []

    def embed(
        self,
        account_id: str,
        texts: list[str],
        *,
        context: EmbeddingContext | None = None,
    ) -> list[list[float]]:
        self.embed_calls.append(list(texts))
        self.embed_contexts.append(context)
        vectors: list[list[float]] = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            values = [
                ((digest[i % len(digest)] + i * 13) % 1000) / 1000
                for i in range(self._dimensions)
            ]
            vectors.append(_l2_normalize(values))
        return vectors


def _validate_vectors(
    vectors: object, texts: list[str]
) -> list[list[float]]:
    """本地合同校验：数量、维度与空向量必须与固定合同一致（Issue 15）。

    校验失败抛带稳定错误码的 ``EmbeddingError``：运行锁已经如实记录远端
    调用状态，本地另记失败原因；调用方（索引/检索）据此拒绝写入错误向量
    并给出诚实降级说明。
    """
    if not isinstance(vectors, list) or len(vectors) != len(texts):
        raise EmbeddingError(
            "向量化失败：向量接口返回数量与请求不一致。",
            retryable=False,
            code=EMBEDDING_BATCH_COUNT_MISMATCH,
        )
    normalized: list[list[float]] = []
    for vector in vectors:
        if not isinstance(vector, list) or not vector:
            raise EmbeddingError(
                "向量化失败：向量接口返回了空向量。",
                retryable=False,
                code=EMBEDDING_EMPTY_VECTOR,
            )
        values = [float(value) for value in vector]
        if len(values) != EMBEDDING_DIMENSIONS:
            raise EmbeddingError(
                f"向量化失败：返回维度为 {len(values)}，与固定合同"
                f" {EMBEDDING_DIMENSIONS} 维不符。",
                retryable=False,
                code=EMBEDDING_DIMENSION_MISMATCH,
            )
        normalized.append(_l2_normalize(values))
    return normalized


def _l2_normalize(values: list[float]) -> list[float]:
    """L2 规范化：把向量归一为模长 1（合同要求，确定性实现）。"""
    norm = math.sqrt(sum(value * value for value in values))
    if norm == 0:
        return values
    return [value / norm for value in values]


def _failure_message(result: Any) -> str:
    """把网关失败结果映射为面向用户的中文原因（不泄漏正文与凭据）。"""
    code = result.error_code or ""
    if code == "auth_error":
        return (
            "向量化失败：全局百炼凭据无效或没有该模型权限，"
            "请检查启动服务的全局百炼配置与权限。"
        )
    if code == "rate_limit":
        return "向量化失败：请求过于频繁（限流），请稍后重试。"
    if code == "region_error":
        return "向量化失败：区域接入点不可达，请检查网络。"
    if code == "transient":
        return "向量化失败：服务暂时不可用或网络异常，请稍后重试。"
    if code == "actual_model_mismatch":
        return "向量化失败：实际返回模型与固定合同不一致，调用失败关闭。"
    message = (result.error_message or "").strip()
    if message:
        return f"向量化失败：{message}"
    return "向量化失败：接口调用异常。"


__all__ = [
    "DeterministicEmbeddingPort",
    "EMBEDDING_CAPABILITY_NAME",
    "EMBEDDING_CAPABILITY_VERSION",
    "EMBEDDING_DIMENSIONS",
    "EmbeddingContext",
    "EmbeddingError",
    "EmbeddingPort",
    "NORMALIZATION",
    "QwenEmbeddingPort",
]
