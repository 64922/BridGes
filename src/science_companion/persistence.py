"""Durable state port and SQLite adapter for the application bootstrap."""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import unquote, urlparse

from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr


class StateStore(Protocol):
    """Durable namespace-based state port."""

    def load(self, namespace: str) -> dict[str, Any] | None:
        """Load one JSON-compatible namespace."""

    def save(self, namespace: str, state: dict[str, Any]) -> None:
        """Atomically replace one namespace."""


class PersistenceError(ValueError):
    """Raised for an invalid or unsupported database configuration."""


class SqliteStateStore:
    """SQLite-backed JSON state store with WAL and full commit durability."""

    def __init__(
        self,
        path: str | Path,
        encryption_key: SecretStr | str | None = None,
    ) -> None:
        self.path = str(path)
        self._fernet = self._build_fernet(encryption_key)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(
            self.path,
            timeout=10.0,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=FULL")
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS application_state (
                    namespace TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            self._connection.commit()

    def load(self, namespace: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload FROM application_state WHERE namespace = ?",
                (namespace,),
            ).fetchone()
        if row is None:
            return None
        payload = str(row["payload"])
        if payload.startswith("enc:"):
            if self._fernet is None:
                raise PersistenceError("持久化数据已加密，但当前实例没有状态加密密钥。")
            try:
                payload = self._fernet.decrypt(payload[4:].encode("ascii")).decode("utf-8")
            except InvalidToken as exc:
                raise PersistenceError("状态加密密钥不匹配，无法读取持久化数据。") from exc
        value = json.loads(payload)
        if not isinstance(value, dict):
            raise PersistenceError(f"Persisted namespace {namespace!r} is not an object.")
        return value

    def save(self, namespace: str, state: dict[str, Any]) -> None:
        payload = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if self._fernet is not None:
            payload = "enc:" + self._fernet.encrypt(payload.encode("utf-8")).decode("ascii")
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO application_state(namespace, payload, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(namespace) DO UPDATE SET
                    payload = excluded.payload,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (namespace, payload),
            )
            self._connection.commit()

    def health_check(self) -> bool:
        with self._lock:
            row = self._connection.execute("SELECT 1 AS ok").fetchone()
        return row is not None and row["ok"] == 1

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    @staticmethod
    def _build_fernet(encryption_key: SecretStr | str | None) -> Fernet | None:
        if encryption_key is None:
            return None
        raw_key = (
            encryption_key.get_secret_value()
            if isinstance(encryption_key, SecretStr)
            else encryption_key
        )
        digest = hashlib.sha256(raw_key.encode("utf-8")).digest()
        return Fernet(base64.urlsafe_b64encode(digest))


def _sqlite_path(database_url: str) -> str:
    parsed = urlparse(database_url)
    if parsed.scheme not in {"sqlite", "sqlite+pysqlite"}:
        raise PersistenceError(
            "当前本地持久化适配器只支持 sqlite:// 数据库地址；"
            "生产 PostgreSQL 适配器尚未接入，不能静默回退到内存。"
        )
    if parsed.path in {"/:memory:", "/:memory"}:
        return ":memory:"
    path = f"//{parsed.netloc}{parsed.path}" if parsed.netloc and parsed.path else parsed.path
    if path.startswith("/") and len(path) >= 3 and path[2] == ":":
        path = path[1:]
    path = unquote(path)
    if not path:
        raise PersistenceError("sqlite 数据库地址缺少文件路径。")
    return path


def build_state_store(
    database_url: SecretStr | str | None,
    encryption_key: SecretStr | str | None = None,
) -> SqliteStateStore | None:
    """Build a durable store; configured databases require at-rest encryption."""
    if database_url is None:
        return None
    value = (
        database_url.get_secret_value()
        if isinstance(database_url, SecretStr)
        else database_url
    )
    if not value:
        return None
    if encryption_key is None or not (
        encryption_key.get_secret_value()
        if isinstance(encryption_key, SecretStr)
        else encryption_key
    ):
        raise PersistenceError(
            "配置数据库时必须同时配置 SCIENCE_COMPANION_SECRET_KEY，"
            "用于保护本地状态文件。"
        )
    return SqliteStateStore(_sqlite_path(value), encryption_key=encryption_key)
