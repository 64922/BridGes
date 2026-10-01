"""节点产物/收据/事件外箱的 SQLite 写模型（改进工单 10）。

本模块是内核唯一允许直连数据库的层级：产物、收据与待投递事件在同一
节点局部事务提交；恢复路径先读完成收据，再决定回填产物引用还是安全
重试。所有查询经账户作用域（``scoped``），跨账户访问不可见。
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
from hashlib import sha256
from typing import Any

from bridges.kernel.contracts import (
    ArtifactTrust,
    InputDependency,
    NodeArtifact,
    NodeReceipt,
    NodeReceiptStatus,
    PendingNodeEvent,
    QualityVerdict,
)
from bridges.storage.database import BridgesDatabase


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _loads(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


class NodeKernelRepository:
    """节点产物、完成收据与待投递事件的持久化仓库。"""

    def __init__(self, database: BridgesDatabase) -> None:
        self._db = database

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """持有连接锁，组合操作并入外层事务（提交守卫与写入同事务）。"""
        with self._db.snapshot_lock():
            if self._db.connection.in_transaction:
                yield
            else:
                with self._db.transaction():
                    yield

    # ------------------------------------------------------------------
    # 产物
    # ------------------------------------------------------------------

    def find_artifact(
        self,
        account_id: str,
        conversation_id: str,
        node: str,
        input_key: str,
    ) -> NodeArtifact | None:
        row = self._db.scoped(account_id).execute(
            "SELECT * FROM node_artifacts WHERE account_id = ?"
            " AND conversation_id = ? AND node = ? AND input_key = ?",
            (account_id, conversation_id, node, input_key),
        ).fetchone()
        return _artifact_from_row(row) if row is not None else None

    def get_artifact(self, account_id: str, artifact_id: str) -> NodeArtifact | None:
        row = self._db.scoped(account_id).execute(
            "SELECT * FROM node_artifacts WHERE account_id = ? AND artifact_id = ?",
            (account_id, artifact_id),
        ).fetchone()
        return _artifact_from_row(row) if row is not None else None

    def list_artifacts(self, account_id: str, conversation_id: str) -> list[NodeArtifact]:
        rows = self._db.scoped(account_id).execute(
            "SELECT * FROM node_artifacts WHERE account_id = ? AND conversation_id = ?"
            " ORDER BY rowid",
            (account_id, conversation_id),
        ).fetchall()
        return [_artifact_from_row(row) for row in rows]

    def save_artifact(self, artifact: NodeArtifact) -> None:
        """落盘一个产物；同一身份（账户/会话/节点/输入键）原位更新。

        产物身份由输入键决定：同一个输入重放时保留同一行，绝不产生
        重复的“同一输入、多份产物”。旧租约与停止后的迟到写入由提交
        守卫在同一事务内先拒绝。
        """
        self._db.scoped(artifact.account_id).execute(
            """
            INSERT INTO node_artifacts (
                artifact_id, account_id, conversation_id, run_id, task_id,
                task_version, recipe_id, recipe_version, node, artifact_type,
                schema_version, capability_version, trust_state, input_key,
                input_deps_json, source_refs_json, read_scope,
                requirement_coverage_json, unconfirmed_json, error_json,
                payload_json, content_hash, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(artifact_id) DO UPDATE SET
                run_id = excluded.run_id,
                task_id = excluded.task_id,
                task_version = excluded.task_version,
                recipe_id = excluded.recipe_id,
                recipe_version = excluded.recipe_version,
                trust_state = excluded.trust_state,
                input_deps_json = excluded.input_deps_json,
                source_refs_json = excluded.source_refs_json,
                read_scope = excluded.read_scope,
                requirement_coverage_json = excluded.requirement_coverage_json,
                unconfirmed_json = excluded.unconfirmed_json,
                error_json = excluded.error_json,
                payload_json = excluded.payload_json,
                content_hash = excluded.content_hash,
                updated_at = excluded.updated_at
            """,
            (
                artifact.artifact_id,
                artifact.account_id,
                artifact.conversation_id,
                artifact.run_id,
                artifact.task_id,
                artifact.task_version,
                artifact.recipe_id,
                artifact.recipe_version,
                artifact.node,
                artifact.artifact_type,
                artifact.schema_version,
                artifact.capability_version,
                artifact.trust_state.value,
                artifact.input_key,
                _json([item.to_dict() for item in artifact.input_deps]),
                _json(list(artifact.source_refs)),
                artifact.read_scope,
                _json(list(artifact.requirement_coverage)),
                _json(list(artifact.unconfirmed)),
                _json(artifact.error) if artifact.error is not None else None,
                _json(artifact.payload),
                artifact.content_hash,
                _iso(artifact.created_at),
                _iso(artifact.updated_at),
            ),
        )

    def invalidate_node_artifacts(
        self,
        account_id: str,
        conversation_id: str,
        nodes: Sequence[str],
        *,
        except_artifact_ids: Sequence[str] = (),
        now: datetime,
    ) -> list[str]:
        """显式失效指定节点的历史产物（方式变更使路线/时间失效）。

        只失效仍可复用的产物，不做删除；同输入重跑会原位更新该行，失效
        轨迹由执行内核随收据写入的外箱事件（``node_artifacts_invalidated``）
        保留供审计与兼容解释。返回本次真正失效的产物 ID。
        """
        if not nodes:
            return []
        placeholders = ",".join("?" for _ in nodes)
        params: list[Any] = [account_id, conversation_id, *nodes]
        exclusion = ""
        if except_artifact_ids:
            marks = ",".join("?" for _ in except_artifact_ids)
            exclusion = f" AND artifact_id NOT IN ({marks})"
            params.extend(except_artifact_ids)
        rows = self._db.scoped(account_id).execute(
            f"SELECT artifact_id FROM node_artifacts WHERE account_id = ?"
            f" AND conversation_id = ? AND node IN ({placeholders})"
            f" AND trust_state IN ('draft', 'evidence_bound', 'qualified', 'conflicted')"
            f"{exclusion}",
            tuple(params),
        ).fetchall()
        artifact_ids = [str(row["artifact_id"]) for row in rows]
        if not artifact_ids:
            return []
        marks = ",".join("?" for _ in artifact_ids)
        self._db.scoped(account_id).execute(
            f"UPDATE node_artifacts SET trust_state = ?, updated_at = ?"
            f" WHERE account_id = ? AND artifact_id IN ({marks})",
            (ArtifactTrust.INVALIDATED.value, _iso(now), account_id, *artifact_ids),
        )
        return artifact_ids

    # ------------------------------------------------------------------
    # 完成收据
    # ------------------------------------------------------------------

    def load_receipt(
        self,
        account_id: str,
        run_id: str,
        node: str,
        input_key: str,
    ) -> NodeReceipt | None:
        row = self._db.scoped(account_id).execute(
            "SELECT * FROM node_receipts WHERE account_id = ? AND run_id = ?"
            " AND node = ? AND input_key = ?",
            (account_id, run_id, node, input_key),
        ).fetchone()
        return _receipt_from_row(row) if row is not None else None

    def list_receipts(self, account_id: str, run_id: str) -> list[NodeReceipt]:
        rows = self._db.scoped(account_id).execute(
            "SELECT * FROM node_receipts WHERE account_id = ? AND run_id = ?"
            " ORDER BY rowid",
            (account_id, run_id),
        ).fetchall()
        return [_receipt_from_row(row) for row in rows]

    def save_receipt(self, receipt: NodeReceipt) -> NodeReceipt:
        """提交完成收据；已存在的完成收据不被后续尝试覆盖（先完成者胜）。"""
        self._db.scoped(receipt.account_id).execute(
            """
            INSERT INTO node_receipts (
                receipt_id, account_id, conversation_id, run_id, node, input_key,
                output_key, artifact_id, status, quality_verdict, attempt,
                lease_owner, detail_json, committed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id, run_id, node, input_key) DO UPDATE SET
                receipt_id = excluded.receipt_id,
                output_key = excluded.output_key,
                artifact_id = excluded.artifact_id,
                status = excluded.status,
                quality_verdict = excluded.quality_verdict,
                attempt = excluded.attempt,
                lease_owner = excluded.lease_owner,
                detail_json = excluded.detail_json,
                committed_at = excluded.committed_at
            WHERE node_receipts.status != 'completed'
            """,
            (
                receipt.receipt_id,
                receipt.account_id,
                receipt.conversation_id,
                receipt.run_id,
                receipt.node,
                receipt.input_key,
                receipt.output_key,
                receipt.artifact_id,
                receipt.status.value,
                receipt.quality_verdict.value,
                receipt.attempt,
                receipt.lease_owner,
                _json(receipt.detail),
                _iso(receipt.committed_at),
            ),
        )
        stored = self.load_receipt(
            receipt.account_id, receipt.run_id, receipt.node, receipt.input_key
        )
        assert stored is not None
        return stored

    # ------------------------------------------------------------------
    # 待投递事件（外箱）
    # ------------------------------------------------------------------

    def enqueue_events(
        self,
        *,
        receipt_id: str,
        account_id: str,
        run_id: str,
        node: str,
        events: Sequence[PendingNodeEvent],
        now: datetime,
    ) -> None:
        if not events:
            return
        row = self._db.scoped(account_id).execute(
            "SELECT COALESCE(MAX(seq), 0) AS last_seq FROM node_outbox"
            " WHERE account_id = ? AND receipt_id = ?",
            (account_id, receipt_id),
        ).fetchone()
        seq = int(row["last_seq"]) if row is not None else 0
        for event in events:
            seq += 1
            self._db.scoped(account_id).execute(
                "INSERT OR IGNORE INTO node_outbox"
                "(receipt_id, seq, account_id, run_id, node, kind, payload_json,"
                " created_at, delivered_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                (
                    receipt_id,
                    seq,
                    account_id,
                    run_id,
                    node,
                    event.kind,
                    _json(event.payload),
                    _iso(now),
                ),
            )

    def undelivered_events(self, account_id: str, run_id: str) -> list[dict[str, Any]]:
        rows = self._db.scoped(account_id).execute(
            "SELECT receipt_id, seq, node, kind, payload_json FROM node_outbox"
            " WHERE account_id = ? AND run_id = ? AND delivered_at IS NULL"
            " ORDER BY rowid",
            (account_id, run_id),
        ).fetchall()
        return [
            {
                "receipt_id": str(row["receipt_id"]),
                "seq": int(row["seq"]),
                "node": str(row["node"]),
                "kind": str(row["kind"]),
                "payload": _loads(row["payload_json"], {}),
            }
            for row in rows
        ]

    def mark_events_delivered(
        self,
        account_id: str,
        receipt_id: str,
        seqs: Sequence[int],
        *,
        now: datetime,
    ) -> None:
        for seq in seqs:
            self._db.scoped(account_id).execute(
                "UPDATE node_outbox SET delivered_at = ?"
                " WHERE account_id = ? AND receipt_id = ? AND seq = ?"
                " AND delivered_at IS NULL",
                (_iso(now), account_id, receipt_id, seq),
            )


def receipt_identity(
    account_id: str, run_id: str, node: str, input_key: str
) -> str:
    material = f"{account_id}\x1f{run_id}\x1f{node}\x1f{input_key}"
    return "rcp_" + sha256(material.encode("utf-8")).hexdigest()[:32]


def output_identity(payload_hash: str, verdict: QualityVerdict) -> str:
    return sha256(f"{payload_hash}\x1f{verdict.value}".encode()).hexdigest()


def _artifact_from_row(row: Any) -> NodeArtifact:
    return NodeArtifact(
        artifact_id=str(row["artifact_id"]),
        account_id=str(row["account_id"]),
        conversation_id=str(row["conversation_id"]),
        run_id=str(row["run_id"]),
        task_id=row["task_id"],
        task_version=int(row["task_version"]) if row["task_version"] is not None else None,
        recipe_id=str(row["recipe_id"]),
        recipe_version=str(row["recipe_version"]),
        node=str(row["node"]),
        artifact_type=str(row["artifact_type"]),
        schema_version=str(row["schema_version"]),
        capability_version=str(row["capability_version"]),
        trust_state=ArtifactTrust(str(row["trust_state"])),
        input_key=str(row["input_key"]),
        input_deps=tuple(
            InputDependency.from_dict(item)
            for item in _loads(row["input_deps_json"], [])
        ),
        source_refs=tuple(str(item) for item in _loads(row["source_refs_json"], [])),
        read_scope=str(row["read_scope"] or ""),
        requirement_coverage=tuple(
            dict(item) for item in _loads(row["requirement_coverage_json"], [])
        ),
        unconfirmed=tuple(str(item) for item in _loads(row["unconfirmed_json"], [])),
        error=_loads(row["error_json"], None),
        payload=dict(_loads(row["payload_json"], {})),
        content_hash=str(row["content_hash"]),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
    )


def _receipt_from_row(row: Any) -> NodeReceipt:
    return NodeReceipt(
        receipt_id=str(row["receipt_id"]),
        account_id=str(row["account_id"]),
        conversation_id=str(row["conversation_id"]),
        run_id=str(row["run_id"]),
        node=str(row["node"]),
        input_key=str(row["input_key"]),
        output_key=str(row["output_key"]),
        artifact_id=row["artifact_id"],
        status=NodeReceiptStatus(str(row["status"])),
        quality_verdict=QualityVerdict(str(row["quality_verdict"])),
        attempt=int(row["attempt"]),
        lease_owner=row["lease_owner"],
        detail=dict(_loads(row["detail_json"], {})),
        committed_at=datetime.fromisoformat(str(row["committed_at"])),
    )
