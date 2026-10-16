"""按任务召回的派生向量索引（画像工单 11）。

本模块只做一件事：为用户画像的**语义召回**维护一份可重建的派生索引，
让「换一种说法」的问题也能找到相关条目。边界与不变量：

- **派生数据，不持有事实**：索引行只是原子条目的向量副本；事实真伪、
  权限、用途、时效与撤回全部由原子条目在编译时判定（见
  :meth:`AtomicProfileService.compile_adopted_slice`）。索引命中绝不绕过
  任何门禁，也不扩大权限。
- **按账户隔离**：全部读写带 ``account_id``；不存在跨账户候选。
- **版本固定**：版本行记录向量模型、维度、规范化与召回合同哈希；合同变化
  时不混用旧向量（旧版本直接退休重建），调用方拿到显式降级原因而不是
  「没有相关记忆」。
- **可重建、不复活**：重建只从当前有效条目重算；删除或撤回的条目在同步时
  移除向量行，重建也不会把墓碑正文写回（墓碑正文为空）。
- **诚实降级**：向量化端口未配置、调用失败或索引读写失败时，返回稳定的
  中文降级原因，关键词与任务规则通道继续工作。

模型调用不进入任何写事务；每次调用携带 :class:`EmbeddingContext` 审计
上下文，运行锁由既有网关/recorder 接缝持久化。
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import secrets
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from bridges.ai.fixed_models import EMBEDDING_MODEL_ID
from bridges.ai.ports import EmbeddingContext, EmbeddingOperation
from bridges.contracts.atomic_profile import (
    AtomicProfileItem,
    AtomicProfileItemStatus,
)
from bridges.ingestion.embedding import (
    EMBEDDING_DIMENSIONS,
    NORMALIZATION,
    EmbeddingError,
    EmbeddingPort,
)
from bridges.storage.database import BridgesDatabase

logger = logging.getLogger(__name__)

#: 召回索引合同版本：变更选择规则或载体格式时递增（与主模型无关）。
PROFILE_RECALL_POLICY_VERSION = "profile-recall-1.0"
#: 向量候选进入候选集的最小余弦相似度。画像条目是短句事实，语义相关的
#: 改写通常明显高于不相关文本；阈值偏保守，宁可漏用也不注入无关内容。
PROFILE_RECALL_MIN_SIMILARITY = 0.55
#: 单次 Embedding 调用的最大条目数（索引重建/同步共用）。
PROFILE_RECALL_EMBED_BATCH = 10

#: 稳定降级原因前缀：聊天披露与测试据此区分「服务失败」与「没有相关记忆」。
RECALL_DEGRADED_PREFIX = "语义召回暂不可用"
#: 未装配向量化端口（仅测试/内存组合可能出现）。
RECALL_DEGRADED_NO_EMBEDDING = f"{RECALL_DEGRADED_PREFIX}：未配置向量化端口"
#: 索引访问或写入失败（表缺失、磁盘错误等；事实与关键词通道不受影响）。
RECALL_DEGRADED_INDEX_ERROR = f"{RECALL_DEGRADED_PREFIX}：召回索引暂不可用"


def profile_recall_text_hash(text: str) -> str:
    """条目正文的内容哈希：正文变化时旧向量立即失效（不混用旧值）。"""

    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class ProfileRecallContract:
    """向量召回合同：模型、维度、规范化与召回规则版本，独立于主模型。"""

    model_id: str = EMBEDDING_MODEL_ID
    dimensions: int = EMBEDDING_DIMENSIONS
    normalization: str = NORMALIZATION
    policy_version: str = PROFILE_RECALL_POLICY_VERSION

    def to_json(self) -> str:
        return json.dumps(
            {
                "model_id": self.model_id,
                "dimensions": self.dimensions,
                "normalization": self.normalization,
                "policy_version": self.policy_version,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def contract_hash(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()


CURRENT_RECALL_CONTRACT = ProfileRecallContract()


@dataclass(frozen=True)
class ProfileRecallOutcome:
    """一次语义召回的结果：相似度命中、降级原因与索引版本。

    ``semantic_scores`` 只包含通过相似度阈值的条目（``item_id → 余弦``）；
    降级原因非空时说明向量通道受限的原因，调用方必须把它如实带进采用记录，
    不能把服务失败伪装成「没有相关记忆」。
    """

    semantic_scores: dict[str, float] = field(default_factory=dict)
    degradation_reason: str | None = None
    index_version: str | None = None
    synced_item_count: int = 0


class ProfileRecallIndexPort(Protocol):
    """召回索引端口：编译切片时同步索引并做语义检索（确定性替身可注入）。"""

    def recall(
        self,
        account_id: str,
        *,
        items: Sequence[AtomicProfileItem],
        query: str | None,
        run_id: str,
    ) -> ProfileRecallOutcome:
        """同步索引并返回语义命中；失败与降级都必须自包含（不向上抛）。"""


class ProfileRecallIndex:
    """SQLite 实现的版本化向量召回索引（可重建派生数据）。"""

    def __init__(
        self,
        database: BridgesDatabase,
        embedding: EmbeddingPort | None,
        *,
        contract: ProfileRecallContract = CURRENT_RECALL_CONTRACT,
        min_similarity: float = PROFILE_RECALL_MIN_SIMILARITY,
    ) -> None:
        self._database = database
        self._embedding = embedding
        self._contract = contract
        self._min_similarity = min_similarity

    # ------------------------------------------------------------------
    # 对外入口
    # ------------------------------------------------------------------

    def recall(
        self,
        account_id: str,
        *,
        items: Sequence[AtomicProfileItem],
        query: str | None,
        run_id: str,
    ) -> ProfileRecallOutcome:
        """同步索引并做语义检索；有问题时返回显式降级而不是静默空结果。"""

        if not query or not query.strip():
            return ProfileRecallOutcome()
        eligible = [item for item in items if _indexable(item)]
        if self._embedding is None:
            return ProfileRecallOutcome(
                degradation_reason=RECALL_DEGRADED_NO_EMBEDDING
            )
        try:
            active = self._active_version(account_id)
            if active is not None and str(active["contract_hash"]) != (
                self._contract.contract_hash()
            ):
                # 合同变化（例如向量模型/召回规则升级）：旧向量绝不混用，
                # 直接退休并重建；重建失败时本轮显式降级。
                self._retire_versions(account_id)
                active = None
            if active is None:
                if not eligible:
                    return ProfileRecallOutcome()
                self.rebuild(account_id, items=eligible, run_id=run_id)
                active = self._active_version(account_id)
            else:
                self._sync_entries(
                    account_id,
                    version_id=str(active["version_id"]),
                    eligible=eligible,
                    run_id=run_id,
                )
        except EmbeddingError as exc:
            return ProfileRecallOutcome(
                degradation_reason=f"{RECALL_DEGRADED_PREFIX}：{exc.message}"
            )
        except Exception:  # noqa: BLE001 - 派生索引故障不阻断回答，必须显式降级
            logger.warning("画像召回索引访问失败，本轮退化到关键词通道", exc_info=True)
            return ProfileRecallOutcome(
                degradation_reason=RECALL_DEGRADED_INDEX_ERROR
            )
        if active is None:
            return ProfileRecallOutcome(
                degradation_reason=RECALL_DEGRADED_INDEX_ERROR
            )
        version_id = str(active["version_id"])
        try:
            query_vector = self._embedding.embed(
                account_id,
                [query],
                context=EmbeddingContext(
                    operation=EmbeddingOperation.PROFILE_RECALL,
                    run_id=run_id,
                    object_type="profile_recall_query",
                    object_id=run_id,
                    batch_ordinal=1,
                ),
            )[0]
        except EmbeddingError as exc:
            return ProfileRecallOutcome(
                degradation_reason=f"{RECALL_DEGRADED_PREFIX}：{exc.message}",
                index_version=version_id,
            )
        scores = self._search(account_id, version_id, eligible, query_vector)
        return ProfileRecallOutcome(
            semantic_scores=scores,
            index_version=version_id,
            synced_item_count=len(eligible),
        )

    def rebuild(
        self,
        account_id: str,
        *,
        items: Sequence[AtomicProfileItem],
        run_id: str,
    ) -> str:
        """按当前合同从有效条目全量重建并原子切换（旧版本不复活）。"""

        eligible = [item for item in items if _indexable(item)]
        if self._embedding is None:
            raise EmbeddingError(RECALL_DEGRADED_NO_EMBEDDING, retryable=False)
        version_id = f"pridx-{secrets.token_urlsafe(12)}"
        vectors = self._embed_items(account_id, eligible, run_id=run_id)
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with self._database.transaction():
            # 重建的唯一输入是当前有效条目：删除/撤回的条目不可能被写回。
            self._delete_versions(account_id)
            self._database.connection.execute(
                "INSERT INTO profile_recall_index_versions"
                " (version_id, account_id, contract_json, contract_hash, status,"
                "  item_count, vector_count, created_at, built_at)"
                " VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?)",
                (
                    version_id,
                    account_id,
                    self._contract.to_json(),
                    self._contract.contract_hash(),
                    len(eligible),
                    sum(1 for vector in vectors if vector is not None),
                    now,
                    now,
                ),
            )
            for item, vector in zip(eligible, vectors, strict=True):
                self._upsert_entry(
                    version_id, account_id, item, vector, updated_at=now
                )
        return version_id

    # ------------------------------------------------------------------
    # 增量同步
    # ------------------------------------------------------------------

    def _sync_entries(
        self,
        account_id: str,
        *,
        version_id: str,
        eligible: Sequence[AtomicProfileItem],
        run_id: str,
    ) -> None:
        """把缺失/过期向量补齐、把不再有效的条目行移除（不混用旧内容）。"""

        wanted = {
            item.profile_item_id: (item.version, profile_recall_text_hash(item.text))
            for item in eligible
        }
        existing = self._entry_rows(account_id, version_id)
        stale: list[AtomicProfileItem] = []
        for item in eligible:
            row = existing.get(item.profile_item_id)
            if row is None:
                stale.append(item)
                continue
            if (
                int(row["item_version"]) != item.version
                or str(row["text_hash"]) != wanted[item.profile_item_id][1]
                or row["vector_json"] is None
            ):
                stale.append(item)
        removals = [item_id for item_id in existing if item_id not in wanted]
        if not stale and not removals:
            return
        vectors: list[list[float] | None] = (
            self._embed_items(account_id, stale, run_id=run_id) if stale else []
        )
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with self._database.transaction():
            for item_id in removals:
                self._database.connection.execute(
                    "DELETE FROM profile_recall_index_entries"
                    " WHERE version_id = ? AND profile_item_id = ?",
                    (version_id, item_id),
                )
            for item, vector in zip(stale, vectors, strict=True):
                self._upsert_entry(
                    version_id, account_id, item, vector, updated_at=now
                )
            counts = self._database.connection.execute(
                "SELECT COUNT(*) AS item_count,"
                " SUM(CASE WHEN vector_json IS NULL THEN 0 ELSE 1 END) AS vector_count"
                " FROM profile_recall_index_entries WHERE version_id = ?",
                (version_id,),
            ).fetchone()
            self._database.connection.execute(
                "UPDATE profile_recall_index_versions"
                " SET item_count = ?, vector_count = ?, built_at = ?"
                " WHERE version_id = ?",
                (
                    int(counts["item_count"] or 0),
                    int(counts["vector_count"] or 0),
                    now,
                    version_id,
                ),
            )

    def _embed_items(
        self,
        account_id: str,
        items: Sequence[AtomicProfileItem],
        *,
        run_id: str,
    ) -> list[list[float] | None]:
        assert self._embedding is not None
        vectors: list[list[float] | None] = []
        for start in range(0, len(items), PROFILE_RECALL_EMBED_BATCH):
            batch = list(items[start : start + PROFILE_RECALL_EMBED_BATCH])
            embedded = self._embedding.embed(
                account_id,
                [item.text for item in batch],
                context=EmbeddingContext(
                    operation=EmbeddingOperation.PROFILE_RECALL,
                    run_id=run_id,
                    object_type="profile_recall_index",
                    object_id=account_id,
                    batch_ordinal=start // PROFILE_RECALL_EMBED_BATCH + 1,
                ),
            )
            vectors.extend(embedded)
        if len(vectors) != len(items):
            # 端口合同校验已保证数量一致；这里防御性失败关闭，不写错误向量。
            raise EmbeddingError(
                "向量化失败：索引嵌入数量与条目不一致。", retryable=False
            )
        return vectors

    def _search(
        self,
        account_id: str,
        version_id: str,
        eligible: Sequence[AtomicProfileItem],
        query_vector: Sequence[float],
    ) -> dict[str, float]:
        wanted = {
            item.profile_item_id: (item.version, profile_recall_text_hash(item.text))
            for item in eligible
        }
        scores: dict[str, float] = {}
        for row in self._entry_rows(account_id, version_id).values():
            item_id = str(row["profile_item_id"])
            expected = wanted.get(item_id)
            if expected is None:
                continue
            if (
                int(row["item_version"]) != expected[0]
                or str(row["text_hash"]) != expected[1]
                or row["vector_json"] is None
            ):
                # 过期向量（正文已变更或尚未重嵌入）不参与检索。
                continue
            try:
                values = [float(value) for value in json.loads(str(row["vector_json"]))]
            except (TypeError, ValueError):
                continue
            if len(values) != len(query_vector):
                continue
            similarity = _cosine(values, query_vector)
            if similarity >= self._min_similarity:
                scores[item_id] = similarity
        return scores

    # ------------------------------------------------------------------
    # 仓储底层
    # ------------------------------------------------------------------

    def _active_version(self, account_id: str) -> dict[str, Any] | None:
        row = self._database.connection.execute(
            "SELECT * FROM profile_recall_index_versions"
            " WHERE account_id = ? AND status = 'active'"
            " ORDER BY created_at DESC, version_id DESC LIMIT 1",
            (account_id,),
        ).fetchone()
        return dict(row) if row is not None else None

    def _entry_rows(
        self, account_id: str, version_id: str
    ) -> dict[str, dict[str, Any]]:
        rows = self._database.connection.execute(
            "SELECT * FROM profile_recall_index_entries"
            " WHERE account_id = ? AND version_id = ?",
            (account_id, version_id),
        ).fetchall()
        return {str(row["profile_item_id"]): dict(row) for row in rows}

    def _upsert_entry(
        self,
        version_id: str,
        account_id: str,
        item: AtomicProfileItem,
        vector: list[float] | None,
        *,
        updated_at: str,
    ) -> None:
        self._database.connection.execute(
            "INSERT INTO profile_recall_index_entries"
            " (version_id, account_id, profile_item_id, item_version, text_hash,"
            "  vector_json, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(version_id, profile_item_id) DO UPDATE SET"
            " item_version = excluded.item_version,"
            " text_hash = excluded.text_hash,"
            " vector_json = excluded.vector_json,"
            " updated_at = excluded.updated_at",
            (
                version_id,
                account_id,
                item.profile_item_id,
                item.version,
                profile_recall_text_hash(item.text),
                json.dumps(vector) if vector is not None else None,
                updated_at,
            ),
        )

    def _delete_versions(self, account_id: str) -> None:
        """删除该账户的全部索引版本与条目行（重建切换/退休共用）。"""

        connection = self._database.connection
        connection.execute(
            "DELETE FROM profile_recall_index_entries WHERE account_id = ?",
            (account_id,),
        )
        connection.execute(
            "DELETE FROM profile_recall_index_versions WHERE account_id = ?",
            (account_id,),
        )

    def _retire_versions(self, account_id: str) -> None:
        with self._database.transaction():
            self._delete_versions(account_id)


def _indexable(item: AtomicProfileItem) -> bool:
    """可进入索引的条目：有正文（墓碑正文为空）且未被删除。

    被替代（superseded）的条目仍是真实经历，供回顾用途的语义召回使用；
    删除墓碑没有正文，永远不会被索引或复活。
    """

    return (
        item.status != AtomicProfileItemStatus.WITHDRAWN and bool(item.text.strip())
    )


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """两个 L2 规范化向量的余弦相似度（点积；维度不符返回负值）。"""

    if len(a) != len(b):
        return -1.0
    return math.fsum(x * y for x, y in zip(a, b, strict=True))


__all__ = [
    "CURRENT_RECALL_CONTRACT",
    "PROFILE_RECALL_EMBED_BATCH",
    "PROFILE_RECALL_MIN_SIMILARITY",
    "PROFILE_RECALL_POLICY_VERSION",
    "RECALL_DEGRADED_INDEX_ERROR",
    "RECALL_DEGRADED_NO_EMBEDDING",
    "RECALL_DEGRADED_PREFIX",
    "ProfileRecallContract",
    "ProfileRecallIndex",
    "ProfileRecallIndexPort",
    "ProfileRecallOutcome",
    "profile_recall_text_hash",
]
