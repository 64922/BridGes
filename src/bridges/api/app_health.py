from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from bridges.api.app_support import (
    retired_capability_regression,
)
from bridges.contracts.ai import CapabilityStatus
from bridges.contracts.health import DependencyHealth, HealthProjection, HealthStatus
from bridges.health.probe import build_health_projection
from bridges.storage import SCHEMA_VERSION, StorageError
from bridges.web_search.contracts import WebSearchHealthStatus


def register_health(app: FastAPI) -> None:
    @app.middleware("http")
    async def reject_unpersisted_requests(request: Request, call_next: Any) -> Any:
        """持久化不可用或退役能力回归时只保留健康检查，阻止数据请求。"""
        if app.state.persistence_error and not request.url.path.startswith("/health"):
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"detail": "持久化不可用，当前实例拒绝数据读写。"},
            )
        # Issue 07：已退役表达能力防复活——注册表再次出现旧能力时失败关闭，
        # 不恢复任何旧写入口的"伪模型成功"。
        retired_error = retired_capability_regression(app)
        if retired_error is not None and not request.url.path.startswith("/health"):
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"detail": retired_error},
            )
        return await call_next(request)

    def app_health_projection() -> HealthProjection:
        """将持久化配置纳入统一就绪检查，避免静默降级到内存。"""
        projection = build_health_projection(service="api")
        projection.extensions["run_id"] = os.environ.get("BRIDGES_RUN_ID", "")
        projection.extensions["database_path_fingerprint"] = getattr(
            app.state, "database_path_fingerprint", None
        )
        persistence_error = getattr(app.state, "persistence_error", None)
        if persistence_error:
            projection.dependencies.append(
                DependencyHealth(
                    name="persistence",
                    status=HealthStatus.FAIL,
                    required=True,
                    message=persistence_error,
                )
            )
            projection.ready = HealthStatus.FAIL
        # GQ-01：正式环境缺少全局百炼凭据时就绪检查必须 FAIL（纵深防御，
        # 正常路径由 CLI 启动硬门在进程启动前拦截；此处兜底直接 uvicorn
        # 启动的实例，避免 "ready=pass、模型不可用"）。
        qwen_key_error = getattr(app.state, "qwen_key_error", None)
        if qwen_key_error:
            projection.dependencies.append(
                DependencyHealth(
                    name="qwen_global_key",
                    status=HealthStatus.FAIL,
                    required=True,
                    message=qwen_key_error,
                )
            )
            projection.ready = HealthStatus.FAIL
        # Issue 07：已退役表达能力被重新注册时就绪检查 FAIL（失败关闭），
        # 与 CLI 启动硬门联动，阻止带回归的实例进入服务状态。
        retired_error = retired_capability_regression(app)
        if retired_error:
            projection.dependencies.append(
                DependencyHealth(
                    name="retired_capability_guard",
                    status=HealthStatus.FAIL,
                    required=True,
                    message=retired_error,
                )
            )
            projection.ready = HealthStatus.FAIL
        settings = getattr(app.state, "settings", None)
        if settings is None or settings.environment.lower() != "test":
            profile_capability_error = getattr(
                getattr(app.state, "automatic_profile_service", None),
                "capability_degraded_reason",
                None,
            )
            profile_capability_error = profile_capability_error or getattr(
                app.state, "profile_capability_canary_error", None
            )
            capability_registry = getattr(app.state, "capability_registry", None)
            model_gateway = getattr(app.state, "model_gateway", None)
            if capability_registry is None or model_gateway is None:
                profile_capability_error = profile_capability_error or "not_bound"
            else:
                try:
                    profile_capability = capability_registry.get(
                        "qwen_profile_extraction", "1"
                    )
                    if profile_capability.status != CapabilityStatus.VERIFIED:
                        profile_capability_error = profile_capability_error or "not_verified"
                    elif not profile_capability.validation_probe_version:
                        profile_capability_error = (
                            profile_capability_error or "canary_not_configured"
                        )
                    elif not model_gateway.is_adapter_registered(
                        "qwen_profile_extraction", "1"
                    ):
                        profile_capability_error = profile_capability_error or "not_bound"
                except Exception:  # noqa: BLE001 - 健康检查必须继续返回结构化结果
                    profile_capability_error = profile_capability_error or "not_registered"
            if profile_capability_error:
                projection.dependencies.append(
                    DependencyHealth(
                        name="profile_extraction",
                        status=HealthStatus.FAIL,
                        required=False,
                        message="画像整理能力当前不可用。",
                    )
                )
                projection.degraded = HealthStatus.FAIL
        # Issue 05/06: 配置了版本化数据库时，把 bridges.db schema 就绪状态
        # 作为必需依赖上报；schema 版本不正确或核心表/索引缺失时服务必须
        # FAIL，阻止 Playwright 在数据库未就绪时开始测试。
        # extensions 只暴露脱敏遥测（版本号、对象名清单），绝不返回表内容
        # 或任何账户数据；E2E 契约测试据此证明后端使用了本 run 的数据库。
        bridges_database = getattr(app.state, "bridges_database", None)
        if bridges_database is not None:
            schema_version = 0
            schema_ready = False
            schema_ready_message: str | None = None
            schema_tables: list[str] = []
            try:
                schema_version = bridges_database.schema_version
                schema_ready = bridges_database.schema_ready
                schema_tables = bridges_database.schema_table_names()
                if not schema_ready:
                    # 拿到具体缺失对象信息，避免「version 已等于 expected 却
                    # 报未就绪」的自相矛盾消息（verify 会抛稳定错误码）。
                    bridges_database.verify_schema_integrity()
            except StorageError as exc:
                schema_ready = False
                schema_ready_message = str(exc)
            if not schema_ready and schema_ready_message is None:
                schema_ready_message = (
                    f"数据库 schema 未就绪："
                    f"version={schema_version}，"
                    f"expected={SCHEMA_VERSION}。"
                )
            projection.extensions["database_schema_version"] = schema_version
            projection.extensions["expected_database_schema_version"] = SCHEMA_VERSION
            projection.extensions["database_schema_tables"] = schema_tables
            projection.dependencies.append(
                DependencyHealth(
                    name="database_schema_ready",
                    status=(
                        HealthStatus.PASS if schema_ready else HealthStatus.FAIL
                    ),
                    required=True,
                    message=schema_ready_message,
                )
            )
            if not schema_ready:
                projection.ready = HealthStatus.FAIL
        # Issue 04/01：readiness 独立暴露公网搜索（Tavily）健康快照与新鲜度；
        # 搜索不是必需依赖（断网时基础聊天、本地功能仍可用），但明确非
        # READY 时进入降级状态。extensions 只暴露脱敏遥测，绝不返回健康
        # 查询/响应正文、代理 URL 或任何凭据。
        web_search_health = getattr(
            getattr(app.state, "web_search_service", None), "health_monitor", None
        )
        if web_search_health is not None:
            snapshot = web_search_health.snapshot()
            projection.extensions["tavily_health_status"] = snapshot.status.value
            projection.extensions["tavily_provider_version"] = snapshot.provider_version
            projection.extensions["tavily_probe_latency_ms"] = snapshot.latency_ms
            projection.extensions["tavily_snapshot_age_ms"] = snapshot.age_ms
            projection.extensions["tavily_last_success_at"] = (
                snapshot.last_success_at.isoformat()
                if snapshot.last_success_at is not None
                else None
            )
            projection.extensions["tavily_error_code"] = snapshot.error_code
            projection.extensions["tavily_stale_ready"] = snapshot.stale_ready
            projection.extensions["tavily_refresh_count"] = snapshot.refresh_count
            if snapshot.pending:
                web_search_status = HealthStatus.UNKNOWN
                web_search_message = "搜索健康探测尚未完成首次检查。"
            elif snapshot.status == WebSearchHealthStatus.READY:
                web_search_status = HealthStatus.PASS
                web_search_message = "Tavily 公网搜索可用。"
            else:
                web_search_status = HealthStatus.FAIL
                web_search_message = (
                    "Tavily 公网搜索当前不可用，联网搜索将降级；"
                    "基础聊天与本地功能不受影响。"
                )
                projection.degraded = HealthStatus.FAIL
            projection.dependencies.append(
                DependencyHealth(
                    name="web_search",
                    status=web_search_status,
                    required=False,
                    message=web_search_message,
                    latency_ms=snapshot.latency_ms,
                )
            )
        return projection

    @app.get("/health/live", response_model=HealthProjection)
    async def health_live() -> HealthProjection:
        """Liveness probe: process event loop can respond."""
        projection = build_health_projection(service="api")
        projection.ready = HealthStatus.UNKNOWN
        projection.degraded = HealthStatus.UNKNOWN
        projection.dependencies = []
        return projection

    @app.get("/health/ready", response_model=HealthProjection)
    async def health_ready() -> HealthProjection | JSONResponse:
        """Readiness probe: required dependencies are healthy.

        Issue 06：未就绪（含数据库 schema 未达当前版本或核心对象缺失）时
        返回 HTTP 503 而非 200——Playwright 的 webServer URL 轮询只认状态码，
        只有 schema-ready 后才放行用户测试；初始化失败时启动明确失败而不是
        在首个会话请求中报 500。响应体始终是同一份 HealthProjection。
        """
        projection = app_health_projection()
        if projection.ready != HealthStatus.PASS:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content=projection.model_dump(mode="json"),
            )
        return projection

    @app.get("/health/degraded", response_model=HealthProjection)
    async def health_degraded() -> HealthProjection:
        """Degraded probe: optional dependencies and degradation capability."""
        return app_health_projection()

    @app.get("/health", response_model=HealthProjection)
    async def health_summary() -> HealthProjection:
        """Combined health summary."""
        return app_health_projection()
