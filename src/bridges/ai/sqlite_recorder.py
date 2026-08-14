"""SQLite implementation of :class:`ModelRunLockRecorder`.

The recorder is the only durable writer for ``model_run_locks`` and its business
association table. It enforces account scope, idempotency, sanitization and
atomic batch writes.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any

from bridges.ai.errors import (
    ModelRunLockConflictError,
    ModelRunLockLinkError,
    ModelRunLockPersistError,
    ModelRunLockSecurityError,
)
from bridges.ai.metrics import NOOP_MODEL_RUN_LOCK_METRICS, ModelRunLockMetrics
from bridges.ai.ports import ModelRunLockRecorder, RecordRequest
from bridges.contracts.ai import (
    BusinessRef,
    ModelCallStatus,
    ModelRunLock,
    PersistedModelRunLock,
)
from bridges.storage.database import BridgesDatabase

_LEGACY_RUN_ID = "legacy-unknown"
_LEGACY_PROJECT_ID = "legacy-unknown"
_LEGACY_PROMPT_VERSION = "legacy"
_LEGACY_CONTRACT = "legacy"
_LEGACY_CANONICAL_PREFIX = "legacy-"

# Keys/patterns that must never appear in parameters or error messages persisted
# for audit. The list is intentionally broad: any payload that could carry prompt,
# response, key or media content is rejected before it reaches the database.
_FORBIDDEN_KEY_PATTERNS = (
    "api_key",
    "authorization",
    "auth_token",
    "access_token",
    "secret",
    "password",
    "credential",
    "prompt",
    "messages",
    "content",
    "response",
    "text",
    "ocr",
    "image",
    "video",
    "audio",
    "media",
)

_KEY_RE = re.compile(
    "|".join(re.escape(p) for p in _FORBIDDEN_KEY_PATTERNS),
    re.IGNORECASE,
)

# 值级扫描：即使字段名无害，字符串值若携带明显凭据形态也拒绝持久化
# （如 "sk-..." 密钥、Bearer 头、PEM 私钥、AWS AKIA 形态）。
_SECRET_VALUE_RE = re.compile(
    r"(sk-[A-Za-z0-9_-]{16,}|Bearer\s+[A-Za-z0-9._~+/=-]{12,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----|AKIA[0-9A-Z]{16})",
)

#: 自由文本字段（error_code/error_message/degradation_reason）的凭据形态
#: 关键词：命中即拒绝持久化。媒体边缘接缝（Issue 16）落库前据此脱敏
#: 供应商原文（``bridges.ai.lock_scrub``），必须与本常量保持单一事实源。
FREE_TEXT_FORBIDDEN_KEYWORDS: tuple[str, ...] = (
    "api_key",
    "authorization",
    "secret",
    "token",
)


def _iso(now: datetime) -> str:
    return now.isoformat()


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_loads(value: str | None) -> Any:
    if value is None:
        return None
    return json.loads(value)


class SqliteModelRunLockRecorder(ModelRunLockRecorder):
    """Account-scoped, idempotent SQLite recorder for model run locks."""

    def __init__(
        self,
        database: BridgesDatabase,
        metrics: ModelRunLockMetrics | None = None,
    ) -> None:
        self._db = database
        self._metrics = metrics or NOOP_MODEL_RUN_LOCK_METRICS

    def record(
        self,
        lock: ModelRunLock,
        *,
        business_ref: BusinessRef,
    ) -> PersistedModelRunLock:
        """Persist a single lock and link it to a business object.

        The lock row and its business link are written atomically: when the
        caller already holds an explicit transaction the writes join it (and
        roll back with it), otherwise the recorder opens its own transaction so
        a failed link write can never leave an orphan lock row behind.
        """
        if self._db.connection.in_transaction:
            return self._record(lock, business_ref)
        with self._db.transaction():
            return self._record(lock, business_ref)

    def record_many(
        self,
        requests: list[RecordRequest],
    ) -> list[PersistedModelRunLock]:
        """Persist multiple locks and links in the given order, atomically.

        When the caller already holds an explicit transaction the batch joins
        it and rolls back together with the surrounding business writes;
        otherwise the recorder opens its own transaction so a mid-batch failure
        rolls the whole batch back instead of leaving a partial prefix.
        """
        if not requests:
            return []
        if self._db.connection.in_transaction:
            return [self._record(request.lock, request.business_ref) for request in requests]
        with self._db.transaction():
            return [
                self._record(request.lock, request.business_ref) for request in requests
            ]

    def _record(
        self,
        lock: ModelRunLock,
        business_ref: BusinessRef,
    ) -> PersistedModelRunLock:
        self._sanitize(lock)
        canonical_hash = self._canonical_hash(lock)
        now = datetime.now(UTC)

        self._insert_lock_row(lock, canonical_hash)
        self._insert_link_row(lock, business_ref, now)

        persisted = self._load_one(lock.lock_id, lock.account_id)
        if persisted is None:
            raise ModelRunLockPersistError("运行锁写入后无法读取。")
        self._metrics.record_recorded(
            capability_name=lock.capability_name,
            status=lock.status.value,
        )
        return persisted

    def get_lock(
        self,
        lock_id: str,
        account_id: str,
    ) -> PersistedModelRunLock | None:
        return self._load_one(lock_id, account_id)

    def list_locks_by_run(
        self,
        account_id: str,
        run_id: str,
    ) -> list[PersistedModelRunLock]:
        # 按业务调用序号（attempt ordinal）排序：同一 run 的多条锁按操作内
        # 的稳定调用顺序返回；legacy 行没有关联，排到最后，再以 created_at
        # 与 lock_id 保证确定性。
        rows = self._db.scoped(account_id).execute(
            "SELECT l.* FROM model_run_locks l"
            " LEFT JOIN model_run_lock_links r"
            " ON l.lock_id = r.lock_id AND l.account_id = r.account_id"
            " WHERE l.account_id = ? AND l.run_id = ?"
            " GROUP BY l.lock_id"
            " ORDER BY COALESCE(MIN(r.attempt_ordinal), 2147483647),"
            " l.created_at, l.lock_id",
            (account_id, run_id),
        ).fetchall()
        return [self._persisted_from_row(row) for row in rows]

    def list_locks_by_business_ref(
        self,
        account_id: str,
        object_type: str,
        object_id: str,
    ) -> list[PersistedModelRunLock]:
        # ScopedConnection only checks that the SQL contains "account_id" in the
        # WHERE clause; the join below satisfies the checker while still filtering
        # both tables by account.
        rows = self._db.scoped(account_id).execute(
            "SELECT l.* FROM model_run_locks l"
            " JOIN model_run_lock_links r"
            " ON l.lock_id = r.lock_id AND l.account_id = r.account_id"
            " WHERE l.account_id = ? AND r.object_type = ? AND r.object_id = ?"
            " ORDER BY r.attempt_ordinal, l.created_at, l.lock_id",
            (account_id, object_type, object_id),
        ).fetchall()
        return [self._persisted_from_row(row) for row in rows]

    def _sanitize(self, lock: ModelRunLock) -> None:
        """Reject secrets or private body before they reach the database.

        ``parameters`` keys are blacklisted (prompt/messages/content/credentials
        must never appear) and string values are scanned for credential shapes;
        ``usage`` and ``cost_estimate`` instead enforce numeric-only values,
        because legitimate usage metadata legitimately contains keys such as
        ``prompt_tokens``/``completion_tokens`` while any secret payload would
        arrive as a string value.
        """
        self._assert_no_secrets(lock.parameters, path="parameters")
        self._assert_numeric_only(lock.usage, path="usage")
        self._assert_numeric_only(lock.cost_estimate, path="cost_estimate")
        for field_name, text in (
            ("error_message", lock.error_message),
            ("error_code", lock.error_code),
            ("degradation_reason", lock.degradation_reason),
        ):
            # 自由文本字段不得携带凭据：拒绝明显形态而非尝试脱敏。
            if text and any(
                keyword in text.lower()
                for keyword in FREE_TEXT_FORBIDDEN_KEYWORDS
            ):
                raise ModelRunLockSecurityError(
                    f"运行锁 {field_name} 包含疑似凭据，禁止持久化。"
                )

    def _assert_no_secrets(self, value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if _KEY_RE.search(key):
                    raise ModelRunLockSecurityError(
                        f"运行锁参数包含禁止字段 '{key}'（路径：{path}）。"
                    )
                self._assert_no_secrets(child, f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                self._assert_no_secrets(child, f"{path}[{index}]")
        elif isinstance(value, str) and _SECRET_VALUE_RE.search(value):
            raise ModelRunLockSecurityError(
                f"运行锁 {path} 的值疑似包含凭据，禁止持久化。"
            )

    def _assert_numeric_only(self, value: Any, path: str) -> None:
        """Reject any string/nested-text payload in usage/cost metadata."""
        if value is None:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                self._assert_numeric_only(child, f"{path}.{key}")
            return
        if isinstance(value, list):
            for index, child in enumerate(value):
                self._assert_numeric_only(child, f"{path}[{index}]")
            return
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ModelRunLockSecurityError(
                f"运行锁 {path} 只能包含数值，禁止持久化文本内容。"
            )

    def _canonical_hash(self, lock: ModelRunLock) -> str:
        """Stable hash of the lock's canonical content.

        ``created_at`` is excluded because a replay of the same logical lock may
        legitimately carry a different recording timestamp; everything else that
        describes the invocation is included and serialized with sorted keys.
        """
        payload = lock.model_dump()
        payload.pop("created_at", None)
        canonical = _json_dumps(payload)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _insert_lock_row(self, lock: ModelRunLock, canonical_hash: str) -> None:
        """Insert the lock row, or verify idempotency if the lock_id exists."""
        cursor = self._db.scoped(lock.account_id).execute(
            "INSERT OR IGNORE INTO model_run_locks"
            " (lock_id, account_id, run_id, project_id, capability_name,"
            " capability_version, actual_model_id, region, parameters,"
            " prompt_version, input_output_contract, fallback_path_json,"
            " status, retry_count, degradation_reason, error_code,"
            " error_message, usage, cost_estimate_json, canonical_hash,"
            " created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                lock.lock_id,
                lock.account_id,
                lock.run_id,
                lock.project_id,
                lock.capability_name,
                lock.capability_version,
                lock.actual_model_id,
                lock.region,
                _json_dumps(lock.parameters) if lock.parameters else "{}",
                lock.prompt_version,
                lock.input_output_contract,
                _json_dumps(lock.fallback_path) if lock.fallback_path else "[]",
                lock.status.value,
                lock.retry_count,
                lock.degradation_reason,
                lock.error_code,
                lock.error_message,
                _json_dumps(lock.usage) if lock.usage is not None else None,
                _json_dumps(lock.cost_estimate) if lock.cost_estimate is not None else None,
                canonical_hash,
                _iso(lock.created_at),
            ),
        )
        if cursor.rowcount == 1:
            return

        # lock_id already exists: compare canonical content to decide idempotency.
        existing = self._db.scoped(lock.account_id).execute(
            "SELECT canonical_hash FROM model_run_locks"
            " WHERE lock_id = ? AND account_id = ?",
            (lock.lock_id, lock.account_id),
        ).fetchone()
        if existing is None:
            # INSERT OR IGNORE 只可能在主键冲突时跳过；本账户查不到行说明
            # 该 lock_id 已被其他账户占用（lock_id 为全局主键）。这不是内容
            # 冲突，报持久化错误而不是伪造的冲突。
            raise ModelRunLockPersistError(
                "运行锁 ID 已被其他账户占用，无法为本账户持久化。",
            )
        existing_hash = str(existing["canonical_hash"])
        if existing_hash != canonical_hash:
            self._metrics.record_conflict(capability_name=lock.capability_name)
            raise ModelRunLockConflictError(
                "运行锁 ID 冲突：相同 lock_id 的已存记录与当前内容不同。"
            )

    def _insert_link_row(
        self,
        lock: ModelRunLock,
        business_ref: BusinessRef,
        now: datetime,
    ) -> None:
        link_id = (
            f"{lock.lock_id}:{business_ref.object_type}:{business_ref.object_id}:"
            f"{business_ref.operation}:{business_ref.attempt_ordinal}"
        )
        try:
            self._db.scoped(lock.account_id).execute(
                "INSERT OR IGNORE INTO model_run_lock_links"
                " (link_id, lock_id, account_id, object_type, object_id,"
                " operation, attempt_ordinal, is_primary, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    link_id,
                    lock.lock_id,
                    lock.account_id,
                    business_ref.object_type,
                    business_ref.object_id,
                    business_ref.operation,
                    business_ref.attempt_ordinal,
                    1 if business_ref.is_primary else 0,
                    _iso(now),
                ),
            )
        except Exception as exc:
            raise ModelRunLockLinkError(
                "运行锁业务关联写入失败。",
            ) from exc

    def _load_one(
        self,
        lock_id: str,
        account_id: str,
    ) -> PersistedModelRunLock | None:
        row = self._db.scoped(account_id).execute(
            "SELECT * FROM model_run_locks"
            " WHERE lock_id = ? AND account_id = ?",
            (lock_id, account_id),
        ).fetchone()
        if row is None:
            return None
        return self._persisted_from_row(row)

    def _persisted_from_row(self, row: Any) -> PersistedModelRunLock:
        lock_id = str(row["lock_id"])
        account_id = str(row["account_id"])
        run_id = str(row["run_id"])
        project_id = str(row["project_id"])
        prompt_version = str(row["prompt_version"])
        contract = str(row["input_output_contract"])
        canonical_hash = str(row["canonical_hash"])

        # 迁移 45 前写入的行（Issue 10 旧 schema）缺失全部新增列，由迁移以
        # 哨兵默认值回填。strong 标记（legacy-unknown/legacy 前缀）唯一确定
        # legacy 行；回填值与新行合法值无法区分的字段（retry_count=0、
        # degradation_reason=NULL 等）只在行已确定为 legacy 时一并标记，
        # 绝不把回填值当作真实调用事实。
        legacy = any(
            (
                run_id == _LEGACY_RUN_ID,
                project_id == _LEGACY_PROJECT_ID,
                prompt_version == _LEGACY_PROMPT_VERSION,
                contract == _LEGACY_CONTRACT,
                canonical_hash.startswith(_LEGACY_CANONICAL_PREFIX),
            )
        )
        legacy_missing_fields: set[str] = set()
        if legacy:
            legacy_missing_fields = {
                "run_id",
                "project_id",
                "prompt_version",
                "input_output_contract",
                "canonical_hash",
                "parameters",
                "fallback_path_json",
                "retry_count",
                "degradation_reason",
                "cost_estimate_json",
            }

        links = self._load_links(lock_id, account_id)
        if not links:
            # 无业务关联的行只能来自 Issue 10 之前的旧库；发出低基数指标
            # 供运维识别需要人工回填或归档的 legacy 数据。
            self._metrics.record_missing_business_ref(
                capability_name=str(row["capability_name"]),
            )

        lock = ModelRunLock(
            lock_id=lock_id,
            run_id=run_id,
            account_id=account_id,
            project_id=project_id,
            capability_name=str(row["capability_name"]),
            capability_version=str(row["capability_version"]),
            actual_model_id=(
                str(row["actual_model_id"]) if row["actual_model_id"] is not None else None
            ),
            region=str(row["region"]),
            parameters=_json_loads(str(row["parameters"])) or {},
            prompt_version=prompt_version,
            input_output_contract=contract,
            fallback_path=_json_loads(str(row["fallback_path_json"])) or [],
            status=ModelCallStatus(str(row["status"])),
            retry_count=int(row["retry_count"]),
            degradation_reason=(
                str(row["degradation_reason"])
                if row["degradation_reason"] is not None
                else None
            ),
            error_code=(
                str(row["error_code"]) if row["error_code"] is not None else None
            ),
            error_message=(
                str(row["error_message"]) if row["error_message"] is not None else None
            ),
            created_at=_parse_iso(str(row["created_at"])),
            usage=_json_loads(row["usage"]),
            cost_estimate=_json_loads(row["cost_estimate_json"]),
        )

        return PersistedModelRunLock(
            **lock.model_dump(),
            business_refs=links,
            legacy=legacy,
            legacy_missing_fields=legacy_missing_fields,
        )

    def _load_links(self, lock_id: str, account_id: str) -> list[BusinessRef]:
        rows = self._db.scoped(account_id).execute(
            "SELECT object_type, object_id, operation, attempt_ordinal, is_primary"
            " FROM model_run_lock_links"
            " WHERE account_id = ? AND lock_id = ?"
            " ORDER BY attempt_ordinal, created_at",
            (account_id, lock_id),
        ).fetchall()
        return [
            BusinessRef(
                object_type=str(row["object_type"]),
                object_id=str(row["object_id"]),
                operation=str(row["operation"]),
                attempt_ordinal=int(row["attempt_ordinal"]),
                is_primary=bool(int(row["is_primary"])),
            )
            for row in rows
        ]
