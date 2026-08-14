"""版本化全文/向量索引：不可混写的版本合同（ADR-0008/0020）。

索引版本由 Embedding 模型 ID、维度、规范化、分块器与 Schema 共同确定
（:class:`IndexContract`）；当前索引版本绝不接受不同合同内容的混合写入，
违反即抛 :class:`IndexWriteError`。合同变化（或向量能力可用性变化）时
创建新版本、从全部就绪分块全量重建、校验覆盖率与维度后原子切换；
重建失败保留上一可用版本继续服务，旧版本在明确清理前仍可回滚。
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from bridges.ai.fixed_models import EMBEDDING_MODEL_ID
from bridges.ai.ports import EmbeddingContext, EmbeddingOperation
from bridges.contracts.ingestion import (
    IndexContractProjection,
    IndexStatusProjection,
    IndexVersionProjection,
    IndexVersionStatus,
)
from bridges.ingestion.chunker import CHUNKER_VERSION
from bridges.ingestion.embedding import EMBEDDING_DIMENSIONS, NORMALIZATION, EmbeddingPort
from bridges.storage.database import BridgesDatabase

#: 索引 Schema 版本：Schema 结构变化时递增（触发全量重建）。
INDEX_SCHEMA_VERSION = "index-schema-v1"
#: 单次向量化调用的批量文本数。
EMBED_BATCH_SIZE = 16
#: 重建单轮最多写入的分块数（防御性上限，正常本地量级远低于此）。
REBUILD_MAX_CHUNKS = 200_000


class IndexWriteError(Exception):
    """索引写入被拒绝（合同不匹配等）；message 为面向用户的中文原因。"""


@dataclass(frozen=True)
class IndexContract:
    """不可混写的索引版本合同。"""

    model_id: str
    dimensions: int
    normalization: str
    chunker: str
    schema_version: str

    def contract_hash(self) -> str:
        canonical = json.dumps(
            asdict(self), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)

    def to_projection(self) -> IndexContractProjection:
        return IndexContractProjection(
            model_id=self.model_id,
            dimensions=self.dimensions,
            normalization=self.normalization,
            chunker=self.chunker,
            schema_version=self.schema_version,
            contract_hash=self.contract_hash(),
        )

    @classmethod
    def from_json(cls, value: str) -> IndexContract:
        raw = json.loads(value)
        return cls(
            model_id=str(raw["model_id"]),
            dimensions=int(raw["dimensions"]),
            normalization=str(raw["normalization"]),
            chunker=str(raw["chunker"]),
            schema_version=str(raw["schema_version"]),
        )


#: 当前生效的固定合同（随固定矩阵与分块器版本变化）。
CURRENT_CONTRACT = IndexContract(
    model_id=EMBEDDING_MODEL_ID,
    dimensions=EMBEDDING_DIMENSIONS,
    normalization=NORMALIZATION,
    chunker=CHUNKER_VERSION,
    schema_version=INDEX_SCHEMA_VERSION,
)


@dataclass(frozen=True)
class WriteTarget:
    """一次增量写入的目标：活跃版本行与本次是否启用了向量。"""

    version_row: dict[str, Any] | None
    embedding_available: bool = False


class VersionedIndex:
    """账户隔离的版本化索引写模型（worker 进程使用）。"""

    def __init__(self, database: BridgesDatabase, embedding: EmbeddingPort) -> None:
        self._database = database
        self._embedding = embedding

    # ------------------------------------------------------------------
    # 合同与版本查询
    # ------------------------------------------------------------------

    def active_version(self, account_id: str) -> dict[str, Any] | None:
        row = self._database.connection.execute(
            "SELECT v.* FROM index_active a"
            " JOIN index_versions v ON v.version_id = a.version_id"
            " WHERE a.account_id = ?",
            (account_id,),
        ).fetchone()
        return dict(row) if row is not None else None

    def versions(self, account_id: str) -> list[dict[str, Any]]:
        rows = self._database.connection.execute(
            "SELECT * FROM index_versions WHERE account_id = ? ORDER BY created_at, version_id",
            (account_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def ensure_contract(
        self, account_id: str, *, embedding_available: bool
    ) -> WriteTarget:
        """返回增量写入目标；合同或向量能力变化时先全量重建再返回新版本。

        无活跃版本且账户尚无就绪分块时返回 ``WriteTarget(None)``（没有
        可索引内容，不创建空版本）。
        """
        active = self.active_version(account_id)
        if active is None:
            if self._ready_chunk_count(account_id) == 0:
                return WriteTarget(None)
            self.rebuild(account_id, embedding_available=embedding_available)
            return WriteTarget(self.active_version(account_id), embedding_available)
        if str(active["contract_hash"]) != CURRENT_CONTRACT.contract_hash():
            self.rebuild(account_id, embedding_available=embedding_available)
            return WriteTarget(self.active_version(account_id), embedding_available)
        if (
            embedding_available
            and int(active["vector_count"]) < int(active["chunk_count"])
            and int(active["chunk_count"]) > 0
        ):
            # 向量能力恢复：既有版本向量覆盖不全（含全无向量），重建补齐后
            # 原子切换，绝不向混合覆盖版本继续写入。
            self.rebuild(account_id, embedding_available=True)
            return WriteTarget(self.active_version(account_id), True)
        return WriteTarget(active, embedding_available)

    # ------------------------------------------------------------------
    # 增量写入（合同校验 + 原子性）
    # ------------------------------------------------------------------

    def write_document_chunks(
        self,
        account_id: str,
        target: WriteTarget,
        *,
        chunk_rows: list[dict[str, Any]],
        vectors: list[list[float] | None],
    ) -> None:
        """把一份文档的全部分块与向量写入当前活跃版本。

        合同与版本状态不符立即拒绝（混合写入防护）；全部写入在单个
        事务内完成，失败不留半写状态。
        """
        version = target.version_row
        if version is None:
            raise IndexWriteError("索引尚未建立，无法写入分块。")
        if str(version["contract_hash"]) != CURRENT_CONTRACT.contract_hash():
            raise IndexWriteError("索引合同已变化，禁止向旧版本混合写入。")
        if str(version["status"]) != "active":
            raise IndexWriteError("当前索引版本不可写，请等待版本切换完成。")
        if len(vectors) != len(chunk_rows):
            raise IndexWriteError("分块与向量数量不一致，写入被拒绝。")
        now = datetime.now(UTC).isoformat(timespec="seconds")
        try:
            with self._database.transaction():
                written, vectors_written = self._insert_rows(
                    str(version["version_id"]), account_id, chunk_rows, vectors, now
                )
                self._database.connection.execute(
                    "UPDATE index_versions SET chunk_count = chunk_count + ?,"
                    " vector_count = vector_count + ? WHERE version_id = ?",
                    (written, vectors_written, version["version_id"]),
                )
        except IndexWriteError:
            raise
        except sqlite3.Error as exc:
            raise IndexWriteError("索引写入失败，请检查数据目录。") from exc

    # ------------------------------------------------------------------
    # 版本构建：增量种子 / 全量重建 / 校验与原子切换
    # ------------------------------------------------------------------

    def build_initial(
        self,
        account_id: str,
        *,
        chunk_rows: list[dict[str, Any]],
        vectors: list[list[float] | None],
        embedding_available: bool,
    ) -> WriteTarget:
        """以首份文档为种子建立初始版本并原子切换（账户首个文档路径）。"""
        expected = len(chunk_rows)
        if expected == 0:
            raise IndexWriteError("没有可索引的分块，无法建立索引版本。")
        version_id = f"idx-{secrets.token_urlsafe(12)}"
        now = datetime.now(UTC).isoformat(timespec="seconds")
        try:
            with self._database.transaction():
                self._database.connection.execute(
                    "INSERT INTO index_versions"
                    " (version_id, account_id, contract_json, contract_hash, status,"
                    "  expected_chunk_count, created_at)"
                    " VALUES (?, ?, ?, ?, 'building', ?, ?)",
                    (
                        version_id,
                        account_id,
                        CURRENT_CONTRACT.to_json(),
                        CURRENT_CONTRACT.contract_hash(),
                        expected,
                        now,
                    ),
                )
                written, vectors_written = self._insert_rows(
                    version_id, account_id, chunk_rows, vectors, now
                )
                self._database.connection.execute(
                    "UPDATE index_versions SET chunk_count = ?, vector_count = ?,"
                    " built_at = ? WHERE version_id = ?",
                    (written, vectors_written, now, version_id),
                )
            self._verify_and_switch(
                account_id, version_id, expected, vectors_written, embedding_available
            )
        except (IndexWriteError, sqlite3.Error) as exc:
            reason = str(exc)
            self._mark_failed(version_id, reason)
            raise IndexWriteError(reason) from exc
        return WriteTarget(self.active_version(account_id), embedding_available)

    def rebuild(self, account_id: str, *, embedding_available: bool) -> None:
        """按当前合同全量重建账户索引；失败把新版本标记 failed 并继续用旧版。"""
        expected = self._ready_chunk_count(account_id)
        if expected == 0:
            return
        version_id = f"idx-{secrets.token_urlsafe(12)}"
        now = datetime.now(UTC).isoformat(timespec="seconds")
        try:
            with self._database.transaction():
                self._database.connection.execute(
                    "INSERT INTO index_versions"
                    " (version_id, account_id, contract_json, contract_hash, status,"
                    "  expected_chunk_count, created_at)"
                    " VALUES (?, ?, ?, ?, 'building', ?, ?)",
                    (
                        version_id,
                        account_id,
                        CURRENT_CONTRACT.to_json(),
                        CURRENT_CONTRACT.contract_hash(),
                        expected,
                        now,
                    ),
                )
            rows = self._database.connection.execute(
                "SELECT c.chunk_id, c.content FROM document_chunks c"
                " JOIN document_records r ON r.document_id = c.document_id"
                " WHERE r.account_id = ? AND r.status = 'ready'"
                " ORDER BY r.document_id, c.chunk_index LIMIT ?",
                (account_id, REBUILD_MAX_CHUNKS),
            ).fetchall()
            written = 0
            vectors_written = 0
            for batch_start in range(0, len(rows), EMBED_BATCH_SIZE):
                batch = rows[batch_start : batch_start + EMBED_BATCH_SIZE]
                # Issue 15：重建批次携带索引版本对象与批次序号；调用序号由
                # 端口按同 run 已有锁自动递增（每批一次调用，真实重调新增
                # 序号，绝不覆盖旧锁）。
                batch_ordinal = batch_start // EMBED_BATCH_SIZE + 1
                texts = [str(row["content"]) for row in batch]
                vectors = self._embed_or_fail(
                    account_id,
                    embedding_available,
                    texts,
                    context=EmbeddingContext(
                        operation=EmbeddingOperation.INDEX_REBUILD,
                        run_id=version_id,
                        object_type="index_version",
                        object_id=version_id,
                        batch_ordinal=batch_ordinal,
                    ),
                )
                with self._database.transaction():
                    written, batch_vectors = self._insert_rows(
                        version_id, account_id, batch, vectors, now, start=written
                    )
                    vectors_written += batch_vectors
                    self._database.connection.execute(
                        "UPDATE index_versions SET chunk_count = ?, vector_count = ?,"
                        " built_at = ? WHERE version_id = ?",
                        (written, vectors_written, now, version_id),
                    )
            self._verify_and_switch(
                account_id, version_id, expected, vectors_written, embedding_available
            )
        except (IndexWriteError, sqlite3.Error) as exc:
            reason = str(exc)
            self._mark_failed(version_id, reason)
            raise IndexWriteError(reason) from exc

    def _insert_rows(
        self,
        version_id: str,
        account_id: str,
        rows: list[dict[str, Any]],
        vectors: list[list[float] | None],
        now: str,
        *,
        start: int = 0,
    ) -> tuple[int, int]:
        """把分块与向量写入指定版本；维度不符立即拒绝（事务内回滚）。

        返回 (累计分块数, 累计向量数)——``start`` 用于分批重建时累加。
        """
        if len(vectors) != len(rows):
            raise IndexWriteError("分块与向量数量不一致，写入被拒绝。")
        written = start
        vectors_written = 0
        for row, vector in zip(rows, vectors, strict=True):
            chunk_id = str(row["chunk_id"])
            self._database.connection.execute(
                "INSERT INTO fts_chunks(version_id, account_id, chunk_id, content)"
                " VALUES (?, ?, ?, ?)",
                (version_id, account_id, chunk_id, str(row["content"])),
            )
            if vector is not None:
                if len(vector) != CURRENT_CONTRACT.dimensions:
                    raise IndexWriteError(
                        f"向量维度为 {len(vector)}，与固定合同"
                        f" {CURRENT_CONTRACT.dimensions} 维不符，写入被拒绝。"
                    )
                self._database.connection.execute(
                    "INSERT INTO index_vectors"
                    " (vector_id, version_id, account_id, chunk_id, vector_json,"
                    "  dimension_count, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        f"{version_id}:{chunk_id}",
                        version_id,
                        account_id,
                        chunk_id,
                        json.dumps(vector),
                        len(vector),
                        now,
                    ),
                )
                vectors_written += 1
            written += 1
        return written, vectors_written

    def _embed_or_fail(
        self,
        account_id: str,
        embedding_available: bool,
        texts: list[str],
        *,
        context: EmbeddingContext,
    ) -> list[list[float] | None]:
        if not embedding_available:
            return [None] * len(texts)
        try:
            embedded = self._embedding.embed(account_id, texts, context=context)
        except Exception as exc:  # noqa: BLE001 - 失败统一折叠为可重试原因
            raise IndexWriteError(str(exc)) from exc
        return list(embedded)

    def _verify_and_switch(
        self,
        account_id: str,
        version_id: str,
        expected: int,
        vectors_written: int,
        embedding_available: bool,
    ) -> None:
        """校验覆盖率与维度，全部通过后原子切换活跃指针。"""
        row = self._database.connection.execute(
            "SELECT chunk_count, vector_count FROM index_versions WHERE version_id = ?",
            (version_id,),
        ).fetchone()
        assert row is not None
        if int(row["chunk_count"]) != expected:
            raise IndexWriteError(
                f"索引重建校验失败：预期 {expected} 个分块，实际写入 {int(row['chunk_count'])} 个。"
            )
        if embedding_available and vectors_written != expected:
            raise IndexWriteError(
                f"索引重建校验失败：预期 {expected} 个向量，实际写入 {vectors_written} 个。"
            )
        wrong_dims = self._database.connection.execute(
            "SELECT COUNT(*) AS count FROM index_vectors"
            " WHERE version_id = ? AND dimension_count != ?",
            (version_id, CURRENT_CONTRACT.dimensions),
        ).fetchone()
        if wrong_dims is not None and int(wrong_dims["count"]) > 0:
            raise IndexWriteError(
                f"索引重建校验失败：存在 {int(wrong_dims['count'])} 个维度不符的向量。"
            )
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with self._database.transaction():
            old = self.active_version(account_id)
            if old is not None and str(old["version_id"]) != version_id:
                self._database.connection.execute(
                    "UPDATE index_versions SET status = 'obsolete' WHERE version_id = ?",
                    (old["version_id"],),
                )
            self._database.connection.execute(
                "UPDATE index_versions SET status = 'active', switched_at = ? WHERE version_id = ?",
                (now, version_id),
            )
            self._database.connection.execute(
                "INSERT INTO index_active(account_id, version_id) VALUES (?, ?)"
                " ON CONFLICT(account_id) DO UPDATE SET version_id = excluded.version_id",
                (account_id, version_id),
            )

    def _mark_failed(self, version_id: str, reason: str) -> None:
        with self._database.transaction():
            self._database.connection.execute(
                "UPDATE index_versions SET status = 'failed', error_message = ?,"
                " built_at = ? WHERE version_id = ? AND status = 'building'",
                (reason[:1000], datetime.now(UTC).isoformat(timespec="seconds"), version_id),
            )

    # ------------------------------------------------------------------
    # 回滚与投影
    # ------------------------------------------------------------------

    def rollback(self, account_id: str, version_id: str) -> None:
        """把活跃指针切回一个旧版本（明确清理前可回滚）。"""
        row = self._database.connection.execute(
            "SELECT status FROM index_versions WHERE version_id = ? AND account_id = ?",
            (version_id, account_id),
        ).fetchone()
        if row is None:
            raise IndexWriteError("索引版本不存在或不属于当前账户。")
        if str(row["status"]) == "failed":
            raise IndexWriteError("失败的索引版本不能作为回滚目标。")
        active = self.active_version(account_id)
        if active is not None and str(active["version_id"]) == version_id:
            return
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with self._database.transaction():
            if active is not None:
                self._database.connection.execute(
                    "UPDATE index_versions SET status = 'obsolete' WHERE version_id = ?",
                    (active["version_id"],),
                )
            self._database.connection.execute(
                "UPDATE index_versions SET status = 'active', switched_at = ? WHERE version_id = ?",
                (now, version_id),
            )
            self._database.connection.execute(
                "UPDATE index_active SET version_id = ? WHERE account_id = ?",
                (version_id, account_id),
            )

    def status_projection(
        self, account_id: str, *, embedding_probed: bool, embedding_available: bool,
        vector_unavailable_reason: str | None,
    ) -> IndexStatusProjection:
        """聚合当前账户的索引状态投影（API 进程只读使用）。"""
        return build_index_status(
            self._database,
            account_id,
            embedding_probed=embedding_probed,
            embedding_available=embedding_available,
            vector_unavailable_reason=vector_unavailable_reason,
        )

    @staticmethod
    def _version_projection(row: dict[str, Any]) -> IndexVersionProjection:
        contract = IndexContract.from_json(str(row["contract_json"]))
        created_at = _parse_dt(row["created_at"])
        assert created_at is not None
        return IndexVersionProjection(
            version_id=str(row["version_id"]),
            contract=contract.to_projection(),
            status=IndexVersionStatus(str(row["status"])),
            expected_chunk_count=int(row["expected_chunk_count"]),
            chunk_count=int(row["chunk_count"]),
            vector_count=int(row["vector_count"]),
            error_message=str(row["error_message"]) if row["error_message"] else None,
            built_at=_parse_dt(row["built_at"]),
            switched_at=_parse_dt(row["switched_at"]),
            created_at=created_at,
        )

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _ready_chunk_count(self, account_id: str) -> int:
        row = self._database.connection.execute(
            "SELECT COUNT(*) AS count FROM document_chunks c"
            " JOIN document_records r ON r.document_id = c.document_id"
            " WHERE r.account_id = ? AND r.status = 'ready'",
            (account_id,),
        ).fetchone()
        assert row is not None
        return int(row["count"])


def _parse_dt(value: object) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value))


def build_index_status(
    database: BridgesDatabase,
    account_id: str,
    *,
    embedding_probed: bool,
    embedding_available: bool,
    vector_unavailable_reason: str | None,
) -> IndexStatusProjection:
    """聚合当前账户的索引状态投影（纯读数据库，API 与 worker 进程共用）。

    只读路径不依赖 VersionedIndex 实例：API 进程没有写组件也能展示
    由 worker 构建的真实版本链。
    """
    active = database.connection.execute(
        "SELECT v.* FROM index_active a"
        " JOIN index_versions v ON v.version_id = a.version_id"
        " WHERE a.account_id = ?",
        (account_id,),
    ).fetchone()
    rows = database.connection.execute(
        "SELECT * FROM index_versions WHERE account_id = ? ORDER BY created_at, version_id",
        (account_id,),
    ).fetchall()
    return IndexStatusProjection(
        embedding_probed=embedding_probed,
        embedding_available=embedding_available,
        vector_unavailable_reason=vector_unavailable_reason,
        active_version=(
            VersionedIndex._version_projection(dict(active)) if active is not None else None
        ),
        versions=[VersionedIndex._version_projection(dict(row)) for row in rows],
    )
