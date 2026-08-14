"""AI/audit ownership boundary: ports for durable model run lock recording.

Business domains depend on these abstractions, not on ``ConversationRepository``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum

from bridges.contracts.ai import BusinessRef, ModelRunLock, PersistedModelRunLock

#: Embedding 审计稳定错误码（Issue 15 Observability 合同）。
#: 直连 Qwen client 绕过统一接缝（由架构测试扫描，不在运行时产生）。
EMBEDDING_DIRECT_CLIENT_BYPASS = "embedding_direct_client_bypass"
#: 调用缺少业务上下文（operation/run/对象），无法分类接线。
EMBEDDING_MISSING_CONTEXT = "embedding_missing_context"
#: 模型调用没有产生不可变运行锁（接缝未装配网关/recorder）。
EMBEDDING_MISSING_RUN_LOCK = "embedding_missing_run_lock"
#: 运行锁持久化失败（审计闭环失败关闭，不静默继续）。
EMBEDDING_LOCK_PERSIST_FAILED = "embedding_lock_persist_failed"
#: 远端成功但返回数量与请求批次不一致（本地合同校验失败）。
EMBEDDING_BATCH_COUNT_MISMATCH = "embedding_batch_count_mismatch"
#: 远端成功但返回维度与固定合同不符（本地合同校验失败）。
EMBEDDING_DIMENSION_MISMATCH = "embedding_dimension_mismatch"
#: 远端成功但返回空向量（本地合同校验失败）。
EMBEDDING_EMPTY_VECTOR = "embedding_empty_vector"
#: 响应结构无法解析（缺少 data/embedding 字段或非数值）。
EMBEDDING_INVALID_RESPONSE = "embedding_invalid_response"
#: adapter 收到空输入（端口已短路空输入，仅防御性护栏）。
EMBEDDING_EMPTY_INPUT = "embedding_empty_input"
#: 网关失败但无稳定错误码可归类时的兜底码。
EMBEDDING_CALL_FAILED = "embedding_call_failed"


class EmbeddingOperation(StrEnum):
    """Embedding 调用点的稳定 operation 标签（Issue 15：先分类后接线）。

    新增生产调用点必须归类到其中一个标签（或显式扩展本枚举并接线），
    不允许出现无 operation 的 Embedding 调用。
    """

    INGESTION_WRITE = "ingestion_write"
    INDEX_REBUILD = "index_rebuild"
    RETRIEVAL_QUERY = "retrieval_query"


@dataclass(frozen=True)
class EmbeddingContext:
    """一次 Embedding 调用的业务上下文（脱敏，仅含标识与序号）。

    ``operation``/``run_id``/``object_type``/``object_id`` 决定运行锁的
    业务关联；``batch_ordinal`` 是同一业务 run 内的批次序号（多批次
    重建）。``call_ordinal`` 是调用方给出的序号基准（默认 1）：真实重调
    时端口按同一 run/operation/对象已有锁自动递增序号，绝不覆盖旧锁。
    """

    operation: EmbeddingOperation
    run_id: str
    object_type: str
    object_id: str
    project_id: str = "default"
    batch_ordinal: int = field(default=1)
    call_ordinal: int = field(default=1)


@dataclass(frozen=True)
class RecordRequest:
    """One lock to record together with its business context."""

    lock: ModelRunLock
    business_ref: BusinessRef


class ModelRunLockRecorder(ABC):
    """Port for persistently recording immutable model run locks.

    Implementations guarantee:

    - account-scoped reads and writes;
    - idempotent inserts for the same ``lock_id`` + canonical content;
    - stable ``model_run_lock_conflict`` errors when the same ``lock_id`` carries
      different canonical content;
    - ordered ``record_many`` (atomicity is provided by the caller's transaction);
    - rejection or scrubbing of secrets and private body in ``parameters``.
    """

    @abstractmethod
    def record(
        self,
        lock: ModelRunLock,
        *,
        business_ref: BusinessRef,
    ) -> PersistedModelRunLock:
        """Persist a single lock and its business association.

        Calling this repeatedly with the same ``lock_id`` and identical canonical
        content returns the previously persisted lock without creating duplicates.
        """

    @abstractmethod
    def record_many(
        self,
        requests: list[RecordRequest],
    ) -> list[PersistedModelRunLock]:
        """Persist multiple locks and their associations in the order given.

        The batch is atomic: either every lock and link is committed in order,
        or none is. When the caller holds an explicit transaction the batch
        joins it and rolls back together with the surrounding business writes.
        """

    @abstractmethod
    def get_lock(
        self,
        lock_id: str,
        account_id: str,
    ) -> PersistedModelRunLock | None:
        """Return the persisted lock together with its business associations."""

    @abstractmethod
    def list_locks_by_run(
        self,
        account_id: str,
        run_id: str,
    ) -> list[PersistedModelRunLock]:
        """Return all locks for a run, ordered by attempt ordinal then created_at."""

    @abstractmethod
    def list_locks_by_business_ref(
        self,
        account_id: str,
        object_type: str,
        object_id: str,
    ) -> list[PersistedModelRunLock]:
        """Return all locks linked to a business object, ordered by attempt ordinal."""
