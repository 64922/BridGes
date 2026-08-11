"""公网搜索缓存的账户隔离持久化适配器。"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from bridges.storage.database import BridgesDatabase
from bridges.web_search.contracts import WebSearchProjection
from bridges.web_search.service import SearchPlan, WebSearchCache


class WebSearchCacheRepository(WebSearchCache):
    """把已完成的搜索结果保存到当前账户的 SQLite 作用域。"""

    def __init__(self, database: BridgesDatabase) -> None:
        self._database = database

    def get(
        self, account_id: str, plan: SearchPlan, now: datetime
    ) -> WebSearchProjection | None:
        row = self._database.scoped(account_id).execute(
            "SELECT projection_json, expires_at FROM web_search_cache"
            " WHERE account_id = ? AND query_hash = ? AND provider = ?"
            " AND provider_version = ?"
            " AND freshness_window_seconds = ?",
            (
                account_id,
                plan.query_hash,
                plan.provider,
                plan.provider_version,
                plan.freshness_window_seconds,
            ),
        ).fetchone()
        if row is None:
            return None
        expires_at = _parse_datetime(str(row["expires_at"]))
        if expires_at <= now:
            with self._database.transaction():
                self._database.scoped(account_id).execute(
                    "DELETE FROM web_search_cache WHERE account_id = ?"
                    " AND query_hash = ? AND provider = ?"
                    " AND provider_version = ?"
                    " AND freshness_window_seconds = ?",
                    (
                        account_id,
                        plan.query_hash,
                        plan.provider,
                        plan.provider_version,
                        plan.freshness_window_seconds,
                    ),
                )
            return None
        try:
            projection = WebSearchProjection.model_validate(
                json.loads(str(row["projection_json"]))
            )
        except (ValueError, TypeError, json.JSONDecodeError):
            return None
        return projection.model_copy(update={"cache_expires_at": expires_at})

    def put(
        self,
        account_id: str,
        plan: SearchPlan,
        projection: WebSearchProjection,
        expires_at: datetime,
    ) -> None:
        now = datetime.now(UTC)
        cache_id = hashlib.sha256(
            f"{account_id}:{plan.query_hash}:{projection.provider}:"
            f"{projection.provider_version}:"
            f"{plan.freshness_window_seconds}".encode("utf-8")
        ).hexdigest()
        payload = json.dumps(
            projection.model_copy(update={"cache_hit": False}).model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
        )
        with self._database.transaction():
            self._database.scoped(account_id).execute(
                "INSERT INTO web_search_cache"
                "(cache_id, account_id, query_hash, provider, provider_version,"
                " rules_version, freshness_window_seconds, plan_id, projection_json,"
                " expires_at, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(account_id, query_hash, provider, provider_version,"
                " freshness_window_seconds) DO UPDATE SET"
                " cache_id = excluded.cache_id, provider = excluded.provider,"
                " rules_version = excluded.rules_version, plan_id = excluded.plan_id,"
                " projection_json = excluded.projection_json,"
                " expires_at = excluded.expires_at, updated_at = excluded.updated_at",
                (
                    cache_id,
                    account_id,
                    plan.query_hash,
                    projection.provider,
                    projection.provider_version,
                    plan.rules_version,
                    plan.freshness_window_seconds,
                    plan.plan_id,
                    payload,
                    _iso(expires_at),
                    _iso(now),
                    _iso(now),
                ),
            )


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
