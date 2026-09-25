"""arXiv 触发、查询脱敏、中文结果编排与隐私审计。"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from inspect import Parameter, signature
from threading import Event
from typing import Any, Protocol, cast

from bridges.arxiv_mcp import limits as arxiv_limits
from bridges.arxiv_mcp.cache import ArxivResultCache
from bridges.arxiv_mcp.client import ArxivMcpError
from bridges.arxiv_mcp.contracts import (
    ArxivPaper,
    ArxivPaperProjection,
    ArxivSearchProjection,
    ArxivSearchStatus,
)
from bridges.arxiv_mcp.guard import ArxivCooldown, ArxivThrottle
from bridges.arxiv_mcp.process import ArxivMcpProcessClient
from bridges.contracts.chat import ChatMode
from bridges.contracts.observability import AuditAction, AuditResult
from bridges.observability.service import ObservabilityService
from bridges.routing import (
    CapabilityRoute,
    NaturalLanguageRouter,
    PaperSearchConstraints,
    RouteStatus,
)
from bridges.routing.contracts import PAPER_QUERY_VERSION, RemovedQueryCategory

logger = logging.getLogger("bridges.arxiv_mcp.service")

ARXIV_RELEVANCE_VERSION = "2026.08.12"

#: Issue 04：上游错误码 → 可靠性策略位（重试/stale/冷却的单一分类来源）。
#: - ``retry``：剩余预算允许时自动重发一次（超时/网络/5xx 瞬时类）；
#: - ``stale``：本次上游调用失败后允许同键过期缓存兜底；
#: - ``cooldown``：终态后激活 20s 上游冷却。
#: 429 只进 stale/cooldown（不重试，维持冷却语义）；4xx/parse 全不进。
_UPSTREAM_ERROR_POLICY: dict[str, frozenset[str]] = {
    "arxiv_timeout": frozenset({"retry", "stale", "cooldown"}),
    "arxiv_offline": frozenset({"retry", "stale"}),
    "arxiv_rate_limit": frozenset({"stale", "cooldown"}),
}


@dataclass(frozen=True)
class ArxivSearchPlan:
    """本地提炼的最小论文查询计划。"""

    should_search: bool
    query: str
    reason: str
    max_results: int = 5
    constraints: PaperSearchConstraints | None = None
    route_version: str = PAPER_QUERY_VERSION
    removed_categories: tuple[RemovedQueryCategory, ...] = ()


class ArxivClient(Protocol):
    def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        stop_event: Event | None = None,
    ) -> list[ArxivPaper]: ...


class ArxivQueryPlanner:
    """识别论文意图并删除身份、私密材料与凭据。"""

    def __init__(self, router: NaturalLanguageRouter | None = None) -> None:
        self._router = router or NaturalLanguageRouter()

    def route(self, content: str) -> CapabilityRoute:
        """只分类一次，供聊天入口持久化并在后续重放复用。"""
        return self._router.classify(content)

    def plan_from_route(self, route: CapabilityRoute) -> ArxivSearchPlan:
        """把已持久化的路由快照编译为论文 worker 计划，不重新分类。"""
        if route.status != RouteStatus.MATCHED or route.paper_search is None:
            return ArxivSearchPlan(False, "", "本轮未触发 arXiv 论文搜索")
        paper_plan = route.paper_search
        return ArxivSearchPlan(
            True,
            paper_plan.normalized_query,
            route.reason,
            paper_plan.constraints.max_results,
            paper_plan.constraints,
            paper_plan.version,
            tuple(paper_plan.removed_categories),
        )

    def plan(
        self,
        content: str,
        mode: ChatMode = ChatMode.COMPANION,
        *,
        force: bool = False,
    ) -> ArxivSearchPlan:
        route = self.route(content)
        if route.status == RouteStatus.MATCHED and route.paper_search is not None:
            paper_plan = route.paper_search
            return ArxivSearchPlan(
                True,
                paper_plan.normalized_query,
                route.reason,
                paper_plan.constraints.max_results,
                paper_plan.constraints,
                paper_plan.version,
                tuple(paper_plan.removed_categories),
            )
        if force:
            query = self._scrub(content)
            if not query:
                return ArxivSearchPlan(
                    False,
                    "",
                    "净化后没有可公开检索的论文主题",
                    route_version=PAPER_QUERY_VERSION,
                )
            constraints = PaperSearchConstraints(topic_terms=[query])
            return ArxivSearchPlan(
                True,
                query,
                "学习模式本地证据不足，自动补充 arXiv 论文",
                constraints.max_results,
                constraints,
                PAPER_QUERY_VERSION,
            )
        return ArxivSearchPlan(False, "", "本轮未触发 arXiv 论文搜索")

    def _scrub(self, content: str) -> str:
        return self._router.normalize_public_query(content)


class ArxivSearchService:
    """固定内置 arXiv MCP 的搜索编排服务。

    Issue 05：服务层单例持有结果缓存、进程级节流与上游冷却三件套——
    覆盖主 worker 与全部临时并发 worker 的上游请求；缓存命中、冷却
    拒绝与节流等待都在投影与审计中如实标记。
    Issue 04：有界自动重试（预算阈值门控、重试不绕过节流）、过期缓存
    stale 兜底（仅失败后返回并标注）与启动期 worker 预热三件套，各自
    独立回滚开关见 :mod:`bridges.arxiv_mcp.limits`。
    """

    def __init__(
        self,
        *,
        client: ArxivClient | None = None,
        planner: ArxivQueryPlanner | None = None,
        observability: ObservabilityService | None = None,
        cache: ArxivResultCache | None = None,
        throttle: ArxivThrottle | None = None,
        cooldown: ArxivCooldown | None = None,
    ) -> None:
        self._client = client or ArxivMcpProcessClient()
        self._planner = planner or ArxivQueryPlanner()
        self._observability = observability
        self._cache = cache or ArxivResultCache(
            ttl_seconds=arxiv_limits.ARXIV_CACHE_TTL_SECONDS,
            enabled=arxiv_limits.ARXIV_CACHE_ENABLED,
        )
        self._throttle = throttle or ArxivThrottle(
            min_interval=arxiv_limits.ARXIV_MIN_REQUEST_INTERVAL_SECONDS,
            enabled=arxiv_limits.ARXIV_THROTTLE_ENABLED,
        )
        self._cooldown = cooldown or ArxivCooldown(
            cooldown_seconds=arxiv_limits.ARXIV_COOLDOWN_SECONDS,
            enabled=arxiv_limits.ARXIV_COOLDOWN_ENABLED,
        )
        #: 脱敏累计指标（多线程并发搜索时受锁保护；不含查询与响应正文）。
        self._metrics_lock = threading.Lock()
        self._upstream_calls = 0
        self._cache_hits = 0
        self._cache_misses = 0
        self._throttle_wait_ms_total = 0
        self._retry_attempts = 0
        self._retry_successes = 0
        self._stale_serves = 0

    def plan(
        self,
        content: str,
        mode: ChatMode,
        *,
        force: bool = False,
    ) -> ArxivSearchPlan:
        return self._planner.plan(content, mode, force=force)

    def plan_from_route(self, route: CapabilityRoute) -> ArxivSearchPlan:
        return self._planner.plan_from_route(route)

    def close(self) -> None:
        """关闭受限 worker，避免应用重载后遗留子进程。"""
        close = getattr(self._client, "close", None)
        if callable(close):
            close()

    def warmup(self) -> bool:
        """应用启动期预热：预 spawn 常驻 worker 并完成握手（含 httpx 导入）。

        预热失败只记日志并返回 False，首次搜索仍走懒启动兜底；开关
        ``ARXIV_WARMUP_ENABLED`` 关闭或客户端不支持预热时直接返回 False，
        不阻断应用启动。
        """
        if not arxiv_limits.ARXIV_WARMUP_ENABLED:
            return False
        warmup = getattr(self._client, "warmup", None)
        if not callable(warmup):
            return False
        try:
            return bool(warmup())
        except Exception:  # noqa: BLE001 - 预热失败绝不能阻断应用启动
            logger.warning("arXiv worker 预热异常", exc_info=True)
            return False

    def initial_projection(
        self, plan: ArxivSearchPlan, *, recovery: bool = False
    ) -> ArxivSearchProjection | None:
        if not plan.should_search:
            return None
        return ArxivSearchProjection(
            status=ArxivSearchStatus.RECOVERY if recovery else ArxivSearchStatus.LOADING,
            trigger_reason=plan.reason,
            query_summary=plan.query,
            can_cancel=True,
        )

    def search(
        self,
        account_id: str,
        plan: ArxivSearchPlan,
        *,
        stop_event: Event | None = None,
        deadline: float | None = None,
    ) -> ArxivSearchProjection | None:
        if not plan.should_search:
            return None
        started = time.monotonic()
        if _user_cancelled(stop_event):
            result = self._cancelled_projection(plan)
            self._audit(account_id, plan, result, elapsed_ms=_elapsed_ms(started))
            return result
        if not 1 <= plan.max_results <= 10 or not plan.query.strip():
            result = ArxivSearchProjection(
                status=ArxivSearchStatus.ERROR,
                trigger_reason=plan.reason,
                query_summary=plan.query,
                searched_at=datetime.now(UTC),
                error_code="arxiv_request",
                error_message="论文搜索参数不合法，请调整主题、年份或结果数量后重试。",
                upstream_status="local_invariant",
                can_retry=False,
            )
            self._audit(account_id, plan, result, elapsed_ms=_elapsed_ms(started))
            return result
        # Issue 05：结果缓存 —— 相同规范化查询在 TTL 内直接返回成功投影，
        # 上游调用数为 0；失败与空结果从不入缓存（调用方只 put 成功）。
        # 键按账户隔离，不把账户 A 的查询主题展示到账户 B 的卡片。
        cached = self._cache.get(
            account_id, plan.query, plan.max_results, plan.route_version
        )
        if cached is not None:
            result = cached.model_copy(update={"attempt_count": 0})
            with self._metrics_lock:
                self._cache_hits += 1
            self._audit(
                account_id, plan, result, elapsed_ms=_elapsed_ms(started), cache_hit=True
            )
            return result
        with self._metrics_lock:
            self._cache_misses += 1
        # Issue 05：冷却拒绝 —— 429/超时终态后冷却期内的重试（含手动重试）
        # 不打上游，直接返回准确错误投影与剩余等待秒数。
        rejection = self._cooldown.reject()
        if rejection is not None:
            result = ArxivSearchProjection(
                status=ArxivSearchStatus.ERROR,
                trigger_reason=plan.reason,
                query_summary=plan.query,
                searched_at=datetime.now(UTC),
                error_code=rejection.code,
                error_message=rejection.message,
                upstream_status=rejection.upstream_status,
                can_retry=True,
                retry_after_seconds=rejection.retry_after_seconds,
            )
            self._audit(
                account_id,
                plan,
                result,
                elapsed_ms=_elapsed_ms(started),
                cooldown_rejection=True,
            )
            return result
        # Issue 05：进程级节流 —— 所有 worker 的上游请求串行化调度，等待
        # 计入阶段预算；剩余预算不足时不发起请求，按超时降级。
        wait_seconds = self._throttle.wait_for_request_slot(
            deadline=deadline, stop_event=stop_event
        )
        if wait_seconds is None:
            result = self._timeout_projection(plan)
            self._audit(account_id, plan, result, elapsed_ms=_elapsed_ms(started))
            return result
        throttle_wait_ms = max(0, int(wait_seconds * 1000))
        with self._metrics_lock:
            self._throttle_wait_ms_total += throttle_wait_ms
        if _user_cancelled(stop_event):
            result = self._cancelled_projection(plan)
            self._audit(
                account_id,
                plan,
                result,
                elapsed_ms=_elapsed_ms(started),
                throttle_wait_ms=throttle_wait_ms,
            )
            return result
        # Issue 04：有界自动重试 —— 超时/网络类瞬时错误在剩余预算 ≥
        # 阈值（默认 5s）时自动重发 1 次；429/4xx/解析错误不重试；重发前
        # 再次等待节流槽位（不绕过节流最小间隔），等待计入阶段预算；
        # attempt_count 如实反映真实上游调用数。
        attempts = 0
        final_error: ArxivMcpError | None = None
        papers: list[ArxivPaper] | None = None
        while papers is None:
            if _user_cancelled(stop_event):
                return self._finish_cancelled(
                    account_id,
                    plan,
                    started=started,
                    attempt_count=attempts,
                    deadline=deadline,
                    throttle_wait_ms=throttle_wait_ms,
                )
            attempts += 1
            with self._metrics_lock:
                self._upstream_calls += 1
            try:
                papers = _invoke_arxiv_client(
                    self._client,
                    plan.query,
                    max_results=plan.max_results,
                    stop_event=stop_event,
                    deadline=deadline,
                )
            except ArxivMcpError as exc:
                final_error = exc
                policy = _UPSTREAM_ERROR_POLICY.get(exc.code, frozenset())
                if (
                    attempts >= arxiv_limits.ARXIV_MAX_UPSTREAM_ATTEMPTS
                    or "retry" not in policy
                    or not arxiv_limits.ARXIV_RETRY_ENABLED
                ):
                    break
                remaining_budget = (
                    float("inf")
                    if deadline is None
                    else deadline - time.monotonic()
                )
                if remaining_budget < arxiv_limits.ARXIV_RETRY_MIN_BUDGET_SECONDS:
                    break
                retry_wait = self._throttle.wait_for_request_slot(
                    deadline=deadline, stop_event=stop_event
                )
                if retry_wait is None:
                    break
                # 等待期间用户取消：回到循环顶部投影为 cancelled
                # （attempt_count 如实保留已发生的调用数）。
                if _user_cancelled(stop_event):
                    continue
                with self._metrics_lock:
                    self._throttle_wait_ms_total += max(0, int(retry_wait * 1000))
                    self._retry_attempts += 1
                continue
        if papers is None and final_error is not None:
            error = final_error
            if error.code == "arxiv_cancelled":
                # 搜索期间用户取消：投影为 cancelled，而不是折叠成启动失败
                result = self._cancelled_projection(
                    plan,
                    with_timestamp=True,
                    error_code=error.code,
                    error_message=error.message,
                    upstream_status=error.upstream_status,
                    attempt_count=attempts,
                )
            else:
                error_policy = _UPSTREAM_ERROR_POLICY.get(error.code, frozenset())
                if "cooldown" in error_policy:
                    self._cooldown.activate(
                        error.code, error.message, error.upstream_status
                    )
                # Issue 04：陈旧缓存兜底 —— 仅当本次上游调用失败（超时/
                # 429/网络类）且存在同键 stale 条目时返回并标注；无 stale
                # 条目时维持原错误投影；失败/空结果仍不写入缓存。
                stale_projection = None
                if (
                    "stale" in error_policy
                    and arxiv_limits.ARXIV_STALE_FALLBACK_ENABLED
                ):
                    stale_projection = self._cache.get_stale(
                        account_id,
                        plan.query,
                        plan.max_results,
                        plan.route_version,
                    )
                if stale_projection is not None:
                    result = stale_projection.model_copy(
                        update={"attempt_count": attempts}
                    )
                    with self._metrics_lock:
                        self._stale_serves += 1
                    self._audit(
                        account_id,
                        plan,
                        result,
                        candidate_count=len(result.papers),
                        deadline=deadline,
                        elapsed_ms=_elapsed_ms(started),
                        throttle_wait_ms=throttle_wait_ms,
                        stale=True,
                    )
                    return result
                result = ArxivSearchProjection(
                    status=(
                        ArxivSearchStatus.PERMISSION
                        if error.permission
                        else ArxivSearchStatus.ERROR
                    ),
                    trigger_reason=plan.reason,
                    query_summary=plan.query,
                    searched_at=datetime.now(UTC),
                    error_code=error.code,
                    error_message=error.message,
                    upstream_status=error.upstream_status,
                    can_retry=error.retryable,
                    attempt_count=attempts,
                )
            self._audit(
                account_id,
                plan,
                result,
                deadline=deadline,
                elapsed_ms=_elapsed_ms(started),
                throttle_wait_ms=throttle_wait_ms,
            )
            return result
        if _user_cancelled(stop_event):
            return self._finish_cancelled(
                account_id,
                plan,
                started=started,
                attempt_count=attempts,
                deadline=deadline,
                throttle_wait_ms=throttle_wait_ms,
            )
        if deadline is not None and time.monotonic() >= deadline:
            result = self._timeout_projection(plan, attempt_count=attempts)
            self._cooldown.activate("arxiv_timeout", result.error_message or "", "timeout")
            self._audit(
                account_id,
                plan,
                result,
                deadline=deadline,
                elapsed_ms=_elapsed_ms(started),
                throttle_wait_ms=throttle_wait_ms,
            )
            return result
        assert papers is not None  # 循环以成功路径退出时必有真实结果
        candidates = _deduplicate_papers(papers)
        relevant_papers = [
            paper for paper in candidates if _paper_matches_plan(paper, plan)
        ]
        projection = [
            self._project_paper(index, paper, plan.query, plan.constraints)
            for index, paper in enumerate(relevant_papers, 1)
        ]
        error_code = None
        error_message = None
        if not projection:
            if candidates:
                error_code = "arxiv_no_relevant_results"
                error_message = "没有找到与主题相关的 arXiv 论文，请调整领域或约束后重试。"
            else:
                error_code = "arxiv_no_results"
                error_message = "没有找到匹配的 arXiv 论文，请调整领域或约束后重试。"
        result = ArxivSearchProjection(
            status=ArxivSearchStatus.SUCCESS if projection else ArxivSearchStatus.EMPTY,
            trigger_reason=plan.reason,
            query_summary=plan.query,
            papers=projection,
            searched_at=datetime.now(UTC),
            error_code=error_code,
            error_message=error_message,
            can_retry=not bool(projection),
            attempt_count=attempts,
        )
        if result.status == ArxivSearchStatus.SUCCESS:
            # 只缓存成功投影；失败与空结果不缓存（同一查询 TTL 内重试
            # 命中缓存，上游调用数为 0）。
            self._cache.put(
                account_id, plan.query, plan.max_results, plan.route_version, result
            )
            if attempts > 1:
                # 重试成功计数只统计交付结果的 SUCCESS 终态；EMPTY（无
                # 相关论文）对用户仍可见，不计入「用户无感知的重试成功」。
                with self._metrics_lock:
                    self._retry_successes += 1
        self._audit(
            account_id,
            plan,
            result,
            candidate_count=len(candidates),
            deadline=deadline,
            elapsed_ms=_elapsed_ms(started),
            throttle_wait_ms=throttle_wait_ms,
        )
        return result

    @staticmethod
    def _timeout_projection(
        plan: ArxivSearchPlan, *, attempt_count: int = 0
    ) -> ArxivSearchProjection:
        """构造超时终态投影（节流预算不足/截止边界共用）。"""
        return ArxivSearchProjection(
            status=ArxivSearchStatus.ERROR,
            trigger_reason=plan.reason,
            query_summary=plan.query,
            searched_at=datetime.now(UTC),
            error_code="arxiv_timeout",
            error_message="arXiv 搜索超时，请重试。",
            upstream_status="timeout",
            can_retry=True,
            attempt_count=attempt_count,
        )

    @staticmethod
    def _cancelled_projection(
        plan: ArxivSearchPlan,
        *,
        with_timestamp: bool = False,
        error_code: str | None = None,
        error_message: str = "已取消本轮论文搜索。",
        upstream_status: str | None = "cancelled",
        attempt_count: int = 0,
    ) -> ArxivSearchProjection:
        """构造取消投影（入口预检/搜索期间/后置检查三处共用）。"""
        return ArxivSearchProjection(
            status=ArxivSearchStatus.CANCELLED,
            trigger_reason=plan.reason,
            query_summary=plan.query,
            searched_at=datetime.now(UTC) if with_timestamp else None,
            error_code=error_code,
            error_message=error_message,
            upstream_status=upstream_status,
            attempt_count=attempt_count,
        )

    def _finish_cancelled(
        self,
        account_id: str,
        plan: ArxivSearchPlan,
        *,
        started: float,
        attempt_count: int,
        deadline: float | None,
        throttle_wait_ms: int,
    ) -> ArxivSearchProjection:
        """取消终态投影 + 审计（尝试循环顶部与成功后的后置检查共用）。

        ``attempt_count`` 如实保留已发生的上游调用数。
        """
        result = self._cancelled_projection(
            plan,
            with_timestamp=True,
            upstream_status="cancelled",
            attempt_count=attempt_count,
        )
        self._audit(
            account_id,
            plan,
            result,
            deadline=deadline,
            elapsed_ms=_elapsed_ms(started),
            throttle_wait_ms=throttle_wait_ms,
        )
        return result

    def _audit(
        self,
        account_id: str,
        plan: ArxivSearchPlan,
        result: ArxivSearchProjection,
        *,
        candidate_count: int = 0,
        deadline: float | None = None,
        elapsed_ms: int | None = None,
        cache_hit: bool = False,
        cooldown_rejection: bool = False,
        throttle_wait_ms: int = 0,
        stale: bool = False,
    ) -> None:
        if self._observability is None:
            return
        audit_result = {
            ArxivSearchStatus.SUCCESS: AuditResult.SUCCESS,
            ArxivSearchStatus.EMPTY: AuditResult.DEGRADED,
            ArxivSearchStatus.PERMISSION: AuditResult.DENIED,
            ArxivSearchStatus.CANCELLED: AuditResult.BLOCKED,
        }.get(result.status, AuditResult.RETRYABLE_FAIL)
        self._observability.log_audit(
            actor_account_id=account_id,
            action=AuditAction.ARXIV_SEARCH,
            result=audit_result,
            reason="论文搜索路由",
            details={
                "data_categories": ["public_query_terms"],
                "permission_version": "2026.08.04",
                "route_version": plan.route_version,
                "relevance_rule_version": ARXIV_RELEVANCE_VERSION,
                "query_type": _query_type(plan),
                "query_fingerprint": _query_fingerprint(plan.query),
                "public_term_count": len(_topic_terms(plan.constraints)),
                "removed_categories": list(plan.removed_categories),
                "candidate_count": candidate_count,
                "relevant_result_count": len(result.papers),
                "removed_candidate_count": max(0, candidate_count - len(result.papers)),
                "terminal": result.status.value,
                "result_code": result.error_code,
                "query_length": len(plan.query),
                "result_count": len(result.papers),
                "status": result.status.value,
                "error_code": result.error_code,
                "upstream_status": result.upstream_status,
                "provider": "arxiv",
                "active_sources": ["arxiv"],
                "budget_source": "arxiv_search",
                "elapsed_ms": elapsed_ms,
                "cache_hit": cache_hit or result.cache_hit,
                "stale": stale or result.stale,
                "attempt_count": result.attempt_count,
                "throttle_wait_ms": throttle_wait_ms,
                "cooldown_rejection": cooldown_rejection,
                "retry_after_seconds": result.retry_after_seconds,
                "deadline_remaining_ms": (
                    max(0, int((deadline - time.monotonic()) * 1000))
                    if deadline is not None
                    else None
                ),
            },
        )

    def metrics_snapshot(self) -> dict[str, object]:
        """脱敏的 arXiv 上游可靠性指标快照（不含查询原文与响应正文）。

        缓存命中/未命中、节流等待总毫秒、冷却拒绝次数、重试/重试成功/
        stale 兜底次数与实际上游调用数均可在此查询；逐次搜索明细见审计
        事件（``cache_hit``、``stale``、``attempt_count``、
        ``throttle_wait_ms``、``cooldown_rejection``）。
        """
        with self._metrics_lock:
            total = self._cache_hits + self._cache_misses
            warmup_successes = getattr(self._client, "warmup_successes", 0)
            warmup_failures = getattr(self._client, "warmup_failures", 0)
            return {
                "arxiv_upstream_calls": self._upstream_calls,
                "cache_hits": self._cache_hits,
                "cache_misses": self._cache_misses,
                "cache_hit_ratio": round(self._cache_hits / total, 4) if total else 0.0,
                "throttle_wait_ms_total": self._throttle_wait_ms_total,
                "cooldown_rejections": self._cooldown.rejections,
                "arxiv_retry_attempts": self._retry_attempts,
                "arxiv_retry_successes": self._retry_successes,
                "arxiv_stale_serves": self._stale_serves,
                "warmup_successes": int(warmup_successes or 0),
                "warmup_failures": int(warmup_failures or 0),
            }

    @staticmethod
    def _project_paper(
        index: int,
        paper: ArxivPaper,
        query: str,
        constraints: PaperSearchConstraints | None = None,
    ) -> ArxivPaperProjection:
        query_tokens = _topic_terms(constraints) or _query_tokens(query)
        matching = [
            token
            for token in query_tokens
            if token.lower() in f"{paper.title} {paper.abstract}".lower()
        ]
        matched_text = "、".join(matching[:4]) or "论文标题与摘要"
        return ArxivPaperProjection(
            citation_id=f"arxiv-{index}",
            arxiv_id=paper.arxiv_id,
            title=paper.title,
            authors=paper.authors,
            published_at=paper.published_at,
            abs_url=paper.abs_url,
            pdf_url=paper.pdf_url,
            abstract=paper.abstract,
            primary_category=paper.primary_category,
            summary_zh=(
                f"论文摘要：这篇论文围绕《{paper.title}》展开研究。"
                f"以下原始摘要由 arXiv 返回，未补造摘要之外的结论：{paper.abstract}"
            ),
            relevance_basis=(
                f"与确认查询「{query}」的相关依据来自 arXiv 返回的标题和摘要，"
                f"匹配线索：{matched_text}。"
            ),
            learning_advice_zh="建议先阅读摘要和引言，随后核对方法、实验条件与局限；如需深入，再从该论文的参考文献和后续版本继续学习。",
        )


_TOPIC_ALIASES: dict[str, tuple[str, ...]] = {
    "量子纠错": ("quantum error correction",),
    "量子": ("quantum",),
    "纠错": ("error correction",),
    "机器学习": ("machine learning",),
    "深度学习": ("deep learning",),
    "神经网络": ("neural network",),
    "自然语言处理": ("natural language processing",),
    "计算机视觉": ("computer vision",),
    "强化学习": ("reinforcement learning",),
}


def _paper_matches_plan(paper: ArxivPaper, plan: ArxivSearchPlan) -> bool:
    constraints = plan.constraints or _legacy_constraints(plan.query)
    searchable = _match_text(f"{paper.title} {paper.abstract}")
    for term in _topic_terms(constraints):
        if not _topic_matches(term, searchable):
            return False
    if constraints.author:
        authors = _match_text(" ".join(paper.authors))
        if _match_text(constraints.author) not in authors:
            return False
    if constraints.title and _match_text(constraints.title) not in _match_text(paper.title):
        return False
    if constraints.arxiv_id and not paper.arxiv_id.casefold().startswith(
        constraints.arxiv_id.casefold()
    ):
        return False
    if constraints.year_from is not None and paper.published_at.year < constraints.year_from:
        return False
    return not (
        constraints.year_to is not None
        and paper.published_at.year > constraints.year_to
    )


def _legacy_constraints(query: str) -> PaperSearchConstraints:
    terms = [
        token
        for token in _query_tokens(query)
        if token.casefold() not in {"author", "title", "id", "year"}
    ]
    return PaperSearchConstraints(topic_terms=terms[:12] or [query.strip()])


def _query_tokens(query: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]{1,}|[\u4e00-\u9fff]{2,}", query)


def _topic_terms(constraints: PaperSearchConstraints | None) -> list[str]:
    if constraints is None:
        return []
    return [
        term
        for term in constraints.topic_terms
        if not re.match(r"^(?:author|title|id|year):", term, re.IGNORECASE)
    ]


def _query_type(plan: ArxivSearchPlan) -> str:
    constraints = plan.constraints
    if constraints is not None and constraints.arxiv_id:
        return "id"
    if constraints is not None and any(
        (constraints.author, constraints.title, constraints.year_from, constraints.year_to)
    ):
        return "structured"
    return "ordinary"


def _query_fingerprint(query: str) -> str:
    return sha256(" ".join(query.split()).encode("utf-8")).hexdigest()[:16]


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _topic_matches(term: str, searchable: str) -> bool:
    normalized_term = _match_text(term)
    if not normalized_term:
        return True
    base_variants = [normalized_term]
    for suffix in ("方向相关", "相关", "方向"):
        if term.endswith(suffix) and len(term) > len(suffix) + 1:
            base_variants.append(_match_text(term[: -len(suffix)]))
    variants = list(base_variants)
    for variant in base_variants:
        variants.extend(
            _match_text(alias)
            for alias in _TOPIC_ALIASES.get(variant, ())
        )
    return any(variant and variant in searchable for variant in variants)


def _match_text(value: str) -> str:
    return re.sub(r"[\s\-_.,:;，。；：!?！？()（）\[\]【】]+", "", value.casefold())


def _deduplicate_papers(papers: list[ArxivPaper]) -> list[ArxivPaper]:
    """按原始 arXiv 标识去重并保持供应商返回顺序。"""
    seen: set[str] = set()
    unique: list[ArxivPaper] = []
    for paper in papers:
        if paper.arxiv_id in seen:
            continue
        seen.add(paper.arxiv_id)
        unique.append(paper)
    return unique


def _invoke_arxiv_client(
    client: Any,
    query: str,
    *,
    max_results: int,
    stop_event: Event | None,
    deadline: float | None,
) -> list[ArxivPaper]:
    """把截止时间传给新客户端，同时兼容旧的确定性测试替身。"""
    search = client.search
    try:
        parameters = tuple(signature(search).parameters.values())
    except (TypeError, ValueError):
        parameters = ()
        accepts_deadline = True
    else:
        accepts_deadline = any(
            parameter.name == "deadline" or parameter.kind == Parameter.VAR_KEYWORD
            for parameter in parameters
        )
    kwargs: dict[str, Any] = {"max_results": max_results, "stop_event": stop_event}
    if deadline is not None and accepts_deadline:
        kwargs["deadline"] = deadline
    return cast(list[ArxivPaper], search(query, **kwargs))


def _user_cancelled(stop_event: Event | None) -> bool:
    """区分用户取消与编排器为截止时间发出的内部停止信号。"""
    if stop_event is None:
        return False
    marker = getattr(stop_event, "user_is_set", None)
    return bool(marker()) if callable(marker) else stop_event.is_set()
