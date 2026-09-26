"""GitHub REST API 的有界客户端：有限缓存、额度退避与脱敏披露审计。

对公开仓库只发**最小查询词**（用户原文里的场景与要点），账户、会话、画像与
附件材料一律不出现在请求里。上游限流是这一来源的常态，因此：

- **有限缓存**：同一 URL + 参数在 TTL 内命中进程内缓存，不再外发（条目数有上限，
  超限淘汰最旧的一条）；
- **额度退避**：每次响应都记录检索接口与核心接口各自的剩余额度与重置时间；
  额度为 0 且尚未重置时**不再发请求**，直接返回可重试的限流结论——单轮不空等
  一小时，而是把「稍后可重试」如实交给用户；
- **脱敏审计**：每次真实外发都留下账户归属的披露记录（来源、查询指纹与长度、
  终止状态、耗时、是否命中缓存），不含查询正文。

错误分类稳定（``error_code``），上游原始响应体不进错误信息。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

import httpx

from bridges.contracts.observability import AuditAction, AuditResult
from bridges.github.lexicon import GITHUB_API_HOST
from bridges.observability.service import ObservabilityService

GITHUB_API_BASE = f"https://{GITHUB_API_HOST}"

#: 单次请求超时（秒）；上游偶发慢响应不拖住整轮。
GITHUB_TIMEOUT_SECONDS = 8.0

#: 进程内缓存的有效期与条目上限（有限缓存：不无限增长，也不长期陈旧）。
CACHE_TTL_SECONDS = 900.0
CACHE_MAX_ENTRIES = 96

#: 额度桶名：GitHub 对检索接口与其余接口分别计量。
BUCKET_SEARCH = "search"
BUCKET_CORE = "core"

USER_AGENT = "BridGes/1.0 (github project recommendation)"

#: GitHub 要求的固定请求头（API 版本固定，避免上游行为漂移）。
_API_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": USER_AGENT,
}


@dataclass(frozen=True)
class GithubResponse:
    """一次 API 调用的真实结果（含额度与缓存命中信息，便于如实记账）。"""

    path: str
    status_code: int | None
    payload: Any | None
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False
    from_cache: bool = False
    bucket: str | None = None
    remaining: int | None = None
    reset_at: datetime | None = None
    requested_at: datetime | None = None

    @property
    def ok(self) -> bool:
        return (
            self.error_code is None
            and self.status_code is not None
            and self.status_code < 400
        )

    @property
    def rate_limited(self) -> bool:
        return self.error_code is not None and self.error_code.startswith(
            "github_rate_limit"
        )


class GithubApiClient:
    """公开 GitHub REST API 的最小客户端（注入 ``httpx.Client`` 即可离线测试）。"""

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        observability: ObservabilityService | None = None,
        timeout: float = GITHUB_TIMEOUT_SECONDS,
        cache_ttl_seconds: float = CACHE_TTL_SECONDS,
        cache_max_entries: int = CACHE_MAX_ENTRIES,
    ) -> None:
        self._client = client
        self._observability = observability
        self._timeout = timeout
        self._cache_ttl_seconds = cache_ttl_seconds
        self._cache_max_entries = cache_max_entries
        self._cache: dict[str, tuple[float, GithubResponse]] = {}
        self._remaining: dict[str, int] = {}
        self._reset_at: dict[str, float] = {}
        self._limited: set[str] = set()

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    # -- 额度状态 --------------------------------------------------------

    @property
    def search_remaining(self) -> int | None:
        return self._remaining.get(BUCKET_SEARCH)

    @property
    def core_remaining(self) -> int | None:
        return self._remaining.get(BUCKET_CORE)

    @property
    def reset_at(self) -> datetime | None:
        """最近一次记录到的额度重置时间（按桶取最早者，便于提示何时可重试）。"""
        stamps = [
            self._reset_at[bucket]
            for bucket in (BUCKET_SEARCH, BUCKET_CORE)
            if bucket in self._reset_at
        ]
        if not stamps:
            return None
        return datetime.fromtimestamp(min(stamps), tz=UTC)

    @property
    def limited(self) -> bool:
        """本轮是否真的撞上额度限制（额度用尽且尚未重置）。"""
        now = time.time()
        for bucket in list(self._limited):
            reset = self._reset_at.get(bucket, 0.0)
            if reset <= now:
                self._limited.discard(bucket)
        return bool(self._limited)

    def clear_cache(self) -> None:
        """清空进程内缓存（测试与显式重新检索使用）。"""
        self._cache.clear()

    # -- 调用 ------------------------------------------------------------

    def get(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
        account_id: str | None = None,
        reason: str = "GitHub 项目推荐：公开仓库检索",
        cache_ttl_seconds: float | None = None,
    ) -> GithubResponse:
        """一次有界 GET；命中缓存不再外发，额度用尽不再空等。"""
        bucket = BUCKET_SEARCH if path.startswith("/search/") else BUCKET_CORE
        key = _cache_key(path, params)
        ttl = self._cache_ttl_seconds if cache_ttl_seconds is None else cache_ttl_seconds
        cached = self._cache.get(key)
        now_monotonic = time.monotonic()
        if cached is not None and cached[0] > now_monotonic:
            return GithubResponse(
                path=path,
                status_code=cached[1].status_code,
                payload=cached[1].payload,
                error_code=cached[1].error_code,
                error_message=cached[1].error_message,
                retryable=cached[1].retryable,
                from_cache=True,
                bucket=cached[1].bucket,
                remaining=cached[1].remaining,
                reset_at=cached[1].reset_at,
                requested_at=cached[1].requested_at,
            )
        if self._blocked(bucket):
            return self._rate_limited_response(
                path, bucket, reason=reason, account_id=account_id
            )
        if self._client is None:
            return GithubResponse(
                path=path,
                status_code=None,
                payload=None,
                error_code="github_unavailable",
                error_message="GitHub 接口客户端未装配，本轮未发起检索。",
                retryable=True,
                bucket=bucket,
                requested_at=datetime.now(UTC),
            )
        started = time.monotonic()
        response = self._send(path, params)
        outcome = self._interpret(path, bucket, response)
        self._remember(key, outcome, ttl)
        self._log(
            account_id=account_id,
            path=path,
            bucket=bucket,
            reason=reason,
            outcome=outcome,
            params=params,
            started=started,
            sent=response is not None,
        )
        return outcome

    # -- 内部实现 --------------------------------------------------------

    def _send(self, path: str, params: dict[str, str] | None) -> httpx.Response | None:
        assert self._client is not None  # 调用点已判定
        try:
            return self._client.get(
                f"{GITHUB_API_BASE}{path}",
                params=params,
                timeout=self._timeout,
                headers=_API_HEADERS,
            )
        except httpx.TimeoutException:
            return None
        except httpx.HTTPError:
            return None

    def _interpret(
        self, path: str, bucket: str, response: httpx.Response | None
    ) -> GithubResponse:
        requested_at = datetime.now(UTC)
        if response is None:
            return GithubResponse(
                path=path,
                status_code=None,
                payload=None,
                error_code="github_timeout",
                error_message="GitHub 接口超时或不可达，本轮未取得该请求的结果。",
                retryable=True,
                bucket=bucket,
                requested_at=requested_at,
            )
        remaining, reset_at = self._record_quota(bucket, response)
        status = response.status_code
        if status < 400:
            payload: Any
            try:
                payload = response.json()
            except ValueError:
                return GithubResponse(
                    path=path,
                    status_code=status,
                    payload=None,
                    error_code="github_parse",
                    error_message="GitHub 接口返回内容无法解析。",
                    retryable=False,
                    bucket=bucket,
                    remaining=remaining,
                    reset_at=reset_at,
                    requested_at=requested_at,
                )
            return GithubResponse(
                path=path,
                status_code=status,
                payload=payload,
                bucket=bucket,
                remaining=remaining,
                reset_at=reset_at,
                requested_at=requested_at,
            )
        if status in {403, 429} and _is_rate_limited(status, remaining, response):
            self._limited.add(bucket)
            return self._rate_limited_response(
                path,
                bucket,
                status_code=status,
                remaining=remaining,
                reset_at=reset_at,
            )
        if status == 404:
            return GithubResponse(
                path=path,
                status_code=status,
                payload=None,
                error_code="github_not_found",
                error_message="上游返回该资源不存在。",
                bucket=bucket,
                remaining=remaining,
                reset_at=reset_at,
                requested_at=requested_at,
            )
        if _means_too_large(status, response):
            return GithubResponse(
                path=path,
                status_code=status,
                payload=None,
                error_code="github_too_large",
                error_message="该文件超出内容接口的单文件上限，未取得正文。",
                bucket=bucket,
                remaining=remaining,
                reset_at=reset_at,
                requested_at=requested_at,
            )
        return GithubResponse(
            path=path,
            status_code=status,
            payload=None,
            error_code=f"github_http_{status}",
            error_message=f"GitHub 接口返回错误（HTTP {status}）。",
            retryable=status >= 500,
            bucket=bucket,
            remaining=remaining,
            reset_at=reset_at,
            requested_at=requested_at,
        )

    def _rate_limited_response(
        self,
        path: str,
        bucket: str,
        *,
        status_code: int | None = None,
        reason: str = "",
        account_id: str | None = None,
        remaining: int | None = None,
        reset_at: datetime | None = None,
    ) -> GithubResponse:
        """额度用尽：不空等，返回可重试的限流结论。

        没有真正外发的请求不留披露记录——披露记录只记发出去的请求，因此这条
        路径不调用 ``_log``（它是被阻塞，不是发生了外发）。
        """
        reset = reset_at
        if reset is None and bucket in self._reset_at:
            reset = datetime.fromtimestamp(self._reset_at[bucket], tz=UTC)
        self._limited.add(bucket)
        when = f"（额度将于 {reset.isoformat()} 前后重置）" if reset else ""
        outcome = GithubResponse(
            path=path,
            status_code=status_code,
            payload=None,
            error_code="github_rate_limit",
            error_message=f"GitHub 接口额度已用尽，本轮不再继续请求{when}，稍后可重试。",
            retryable=True,
            bucket=bucket,
            remaining=remaining if remaining is not None else 0,
            reset_at=reset,
            requested_at=datetime.now(UTC),
        )
        if account_id is not None:
            self._log(
                account_id=account_id,
                path=path,
                bucket=bucket,
                reason=reason,
                outcome=outcome,
                params=None,
                started=time.monotonic(),
                sent=False,
            )
        return outcome

    def _blocked(self, bucket: str) -> bool:
        if bucket not in self._limited:
            return False
        reset = self._reset_at.get(bucket, 0.0)
        if reset <= time.time():
            self._limited.discard(bucket)
            return False
        return True

    def _record_quota(
        self, bucket: str, response: httpx.Response
    ) -> tuple[int | None, datetime | None]:
        remaining = _header_int(response, "x-ratelimit-remaining")
        reset_epoch = _header_int(response, "x-ratelimit-reset")
        reset_at = (
            datetime.fromtimestamp(reset_epoch, tz=UTC) if reset_epoch is not None else None
        )
        if remaining is not None:
            self._remaining[bucket] = remaining
        if reset_epoch is not None:
            self._reset_at[bucket] = float(reset_epoch)
        return remaining, reset_at

    def _remember(self, key: str, outcome: GithubResponse, ttl: float) -> None:
        if outcome.status_code is None or outcome.status_code >= 400:
            return
        if ttl <= 0:
            return
        self._cache[key] = (time.monotonic() + ttl, outcome)
        while len(self._cache) > self._cache_max_entries:
            oldest = min(self._cache, key=lambda item: self._cache[item][0])
            del self._cache[oldest]

    def _log(
        self,
        *,
        account_id: str | None,
        path: str,
        bucket: str,
        reason: str,
        outcome: GithubResponse,
        params: dict[str, str] | None,
        started: float,
        sent: bool,
    ) -> None:
        """一次外发的脱敏披露记录（只记指纹与长度，不含查询正文）。"""
        if self._observability is None or account_id is None or not sent:
            return
        details: dict[str, Any] = {
            "data_categories": ["public_query_terms"],
            "provider": GITHUB_API_HOST,
            "endpoint_kind": _endpoint_kind(path),
            "endpoint_fingerprint": sha256(path.encode("utf-8")).hexdigest()[:16],
            "bucket": bucket,
            "terminal": outcome.error_code or "success",
            "status_code": outcome.status_code,
            "remaining": outcome.remaining,
            "elapsed_ms": max(0, int((time.monotonic() - started) * 1000)),
        }
        query = (params or {}).get("q")
        if query:
            # 只留指纹与长度：审计能核对「发过一条多长的检索词」，但正文不落库。
            details["query_fingerprint"] = sha256(query.encode("utf-8")).hexdigest()[:16]
            details["query_length"] = len(query)
        self._observability.log_audit(
            actor_account_id=account_id,
            action=AuditAction.GITHUB_REPOSITORY_LOOKUP,
            result=_audit_result(outcome),
            reason=reason,
            details=details,
        )


def _cache_key(path: str, params: dict[str, str] | None) -> str:
    if not params:
        return path
    joined = "&".join(f"{key}={params[key]}" for key in sorted(params))
    return f"{path}?{joined}"


def _means_too_large(status: int, response: httpx.Response) -> bool:
    """内容接口的单文件上限（超过 1 MB 时上游返回 403 并在正文里说明）。"""
    if status not in {403, 413}:
        return False
    try:
        message = str((response.json() or {}).get("message") or "")
    except ValueError:
        return False
    lowered = message.lower()
    return "too large" in lowered or "larger than" in lowered


def _is_rate_limited(
    status: int, remaining: int | None, response: httpx.Response
) -> bool:
    """403／429 是否属于额度限制（主额度用尽或次级限流）。

    GitHub 的次级限流也返回 403，但会给出 ``retry-after`` 或
    ``message`` 里写明 rate limit；把它当限流而不是权限错误，用户才能重试。
    """
    if status == 429:
        return True
    if remaining == 0:
        return True
    if response.headers.get("retry-after") is not None:
        return True
    try:
        message = str((response.json() or {}).get("message") or "")
    except ValueError:
        return remaining is None
    return "rate limit" in message.lower() or "abuse" in message.lower()


def _endpoint_kind(path: str) -> str:
    """端点分类（审计只记分类与路径指纹，不记查询词或仓库标识）。"""
    if path.startswith("/search/"):
        return "search"
    if path.endswith("/readme"):
        return "readme"
    if path.endswith("/license"):
        return "license"
    if "/contents" in path:
        return "contents"
    return "repository"


def _header_int(response: httpx.Response, name: str) -> int | None:
    raw = response.headers.get(name)
    if raw is None:
        return None
    try:
        return int(raw.strip())
    except ValueError:
        return None


def _audit_result(outcome: GithubResponse) -> AuditResult:
    if outcome.ok:
        return AuditResult.SUCCESS
    if outcome.error_code in {"github_rate_limit", "github_timeout"}:
        return AuditResult.RETRYABLE_FAIL
    if outcome.error_code == "github_unavailable":
        return AuditResult.BLOCKED
    return AuditResult.DEGRADED
