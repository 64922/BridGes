from __future__ import annotations

import hashlib
import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI
from pydantic import ValidationError as PydanticValidationError

from bridges import __version__
from bridges.api.app_support import (
    _credential_store_for_namespace,
)
from bridges.api.csrf import CsrfOriginMiddleware
from bridges.config import get_settings
from bridges.credentials.global_credential import (
    GLOBAL_QWEN_KEY_GUIDANCE,
    is_global_qwen_key_configured,
)
from bridges.credentials.runtime_resolver import CredentialSource, RuntimeCredentialResolver
from bridges.credentials.store import CredentialStorePort
from bridges.persistence import PersistenceError, SqliteStateStore, StateStore, build_state_store
from bridges.retirement import CompatibilityMetrics
from bridges.storage import (
    SCHEMA_INTEGRITY_ERROR_CODE,
    SCHEMA_VERSION,
    BridgesDatabase,
    BridgesObjectRepository,
    EncryptedFileObjectStore,
    StorageError,
)

logger = logging.getLogger(__name__)


def build_base_app(
    state_store: StateStore | None = None,
    *,
    runtime_credential_store: CredentialStorePort | None = None,
    credential_probe_http_client: httpx.Client | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="BridGes API",
        version=__version__,
        description="长期科学学习与表达伙伴 API",
    )
    owns_credential_probe_client = credential_probe_http_client is None
    app.state.credential_probe_http_client = (
        credential_probe_http_client or httpx.Client(timeout=8.0)
    )
    if owns_credential_probe_client:
        app.router.add_event_handler(
            "shutdown", app.state.credential_probe_http_client.close
        )

    # T008: load the unified runtime configuration. All carriers (manual,
    # unified CLI, Docker, Podman) resolve the same schema and secret rules.
    # If configuration fails (e.g. missing secret file), the health probe
    # reports FAIL rather than crashing the process outright.
    try:
        app.state.settings = get_settings()
    except (PydanticValidationError, ValueError):
        app.state.settings = None

    app.state.persistence_error = None
    if state_store is None and app.state.settings is not None:
        try:
            state_store = build_state_store(
                app.state.settings.database_url,
                encryption_key=app.state.settings.secret_key,
            )
        except PersistenceError as exc:
            app.state.persistence_error = str(exc)
    if (
        app.state.settings is not None
        and app.state.settings.environment.lower() == "production"
        and state_store is None
        and app.state.persistence_error is None
    ):
        app.state.persistence_error = (
            "生产环境必须配置 BRIDGES_DATABASE_URL，不能使用进程内存储。"
        )
    app.state.state_store = state_store
    app.state.compatibility_metrics = CompatibilityMetrics(state_store)
    app.state.persistence_mode = (
        "error"
        if app.state.persistence_error
        else "sqlite"
        if state_store is not None
        else "memory"
    )
    settings_at_credential = app.state.settings
    data_dir: Path | None = None
    if isinstance(state_store, SqliteStateStore) and state_store.path != ":memory:":
        data_dir = Path(state_store.path).parent
    app.state.runtime_credential_store = (
        runtime_credential_store
        if runtime_credential_store is not None
        else _credential_store_for_namespace(
            settings_at_credential, data_dir, namespace="runtime"
        )
    )
    if settings_at_credential is not None:
        # V2 Issue 09 / 工单 01：把凭据库里已保存的值载入运行期 Settings，
        # 供进程内其他读取方使用。优先级只由 RuntimeCredentialResolver 决定
        # （文件/环境变量优先，凭据库兜底）：只有"当前生效来源是凭据库"的项
        # 才写入运行期快照，避免页面保存的值被环境变量静默遮蔽却仍显示已配置。
        # 解析器自身吞掉存储读取异常并回落本进程缓存（失败原因见 ``load_error``），
        # 因此这里没有需要单独处理的失败分支。
        resolver = RuntimeCredentialResolver(
            settings=settings_at_credential,
            credential_store=app.state.runtime_credential_store,
        )
        credential_updates: dict[str, Any] = {}
        qwen_key = resolver.qwen_api_key()
        if qwen_key.source is CredentialSource.CREDENTIAL_STORE:
            credential_updates["qwen_api_key"] = qwen_key.value
        tavily_key = resolver.tavily_api_key()
        if tavily_key.source is CredentialSource.CREDENTIAL_STORE:
            credential_updates["tavily_api_key"] = tavily_key.value
        web_service_key = resolver.amap_web_service_key()
        if web_service_key.source is CredentialSource.CREDENTIAL_STORE:
            credential_updates["amap_web_service_key"] = web_service_key.value
        browser_map_pair = resolver.amap_browser_map_pair()
        if browser_map_pair.source is CredentialSource.CREDENTIAL_STORE:
            credential_updates["amap_js_api_key"] = browser_map_pair.js_api_key
            credential_updates["amap_security_js_code"] = (
                browser_map_pair.security_js_code
            )
        app.state.credential_resolver = resolver
        if credential_updates:
            app.state.settings = settings_at_credential.model_copy(
                update=credential_updates
            )

    # GQ-01：全局百炼运行凭据是正式运行的必需配置。development/production
    # 缺少 Key 时设置不含秘密的错误标记，就绪检查报告 FAIL——即使绕过
    # ``BridGes start`` 直接启动 API，也不会出现 "ready=pass、模型不可用"
    # 的半启动实例。test 环境由确定性适配器驱动，豁免此门。
    app.state.qwen_key_error = None
    app.state.profile_capability_canary_error = None
    settings_for_qwen_gate = app.state.settings
    if (
        settings_for_qwen_gate is not None
        and settings_for_qwen_gate.environment.lower() != "test"
        and not is_global_qwen_key_configured(settings_for_qwen_gate)
    ):
        app.state.qwen_key_error = GLOBAL_QWEN_KEY_GUIDANCE

    # Issue 05/06: 配置数据库时以同一数据目录初始化版本化 bridges.db 与账户隔离
    # 加密对象库（对象目录为数据库同目录下的 objects/）。首次启动事务化创建
    # 带版本记录的 bridges.db；失败与持久化错误同样进入 503 拒绝路径，绝不
    # 静默降级。对象加密密钥派生自 BRIDGES_SECRET_KEY，任何位置不落盘密钥。
    # Issue 06: 构造任何会话 repository 前必须完成 initialize() 并校验 schema
    # 版本与核心契约表/索引；失败关闭并报告 database_schema_integrity。
    app.state.bridges_database = None
    app.state.object_repository = None
    app.state.database_path_fingerprint = None
    if (
        isinstance(state_store, SqliteStateStore)
        and state_store.path != ":memory:"
        and app.state.persistence_error is None
    ):
        settings = app.state.settings
        if settings is not None and settings.secret_key is not None:
            secret_value = settings.secret_key.get_secret_value()
            if secret_value:
                database_path = Path(state_store.path)
                run_id = os.environ.get("BRIDGES_RUN_ID", "")
                path_fingerprint = (
                    hashlib.sha256(str(database_path).encode("utf-8")).hexdigest()[:8]
                )
                app.state.database_path_fingerprint = path_fingerprint
                try:
                    database = BridgesDatabase(database_path)
                    logger.info(
                        "bridges_database_migration_start",
                        extra={
                            "run_id": run_id,
                            "path_fingerprint": path_fingerprint,
                            "expected_schema_version": SCHEMA_VERSION,
                        },
                    )
                    migrate_start = time.monotonic()
                    initialized_version = database.initialize()
                    migrate_duration_ms = int(
                        (time.monotonic() - migrate_start) * 1000
                    )
                    # Issue 06 启动契约：调用方校验返回版本等于当前支持版本，
                    # 不能只依赖 initialize() 内部路径（纵深防御）。
                    if initialized_version != SCHEMA_VERSION:
                        raise StorageError(
                            "数据库 schema 版本校验失败："
                            f"initialize 返回 {initialized_version}，"
                            f"当前程序支持 {SCHEMA_VERSION}。"
                        )
                    logger.info(
                        "bridges_database_initialized",
                        extra={
                            "run_id": run_id,
                            "path_fingerprint": path_fingerprint,
                            "schema_version": initialized_version,
                            "expected_schema_version": SCHEMA_VERSION,
                            "schema_ready": database.schema_ready,
                            "migrate_duration_ms": migrate_duration_ms,
                        },
                    )
                    app.state.bridges_database = database
                    object_store = EncryptedFileObjectStore(
                        database_path.parent / "objects",
                        encryption_key=settings.secret_key,
                    )
                    app.state.object_store = object_store
                    app.state.object_repository = BridgesObjectRepository(
                        database,
                        object_store,
                    )
                except StorageError as exc:
                    logger.error(
                        "bridges_database_initialization_failed",
                        extra={
                            "run_id": run_id,
                            "path_fingerprint": path_fingerprint,
                            "error_code": SCHEMA_INTEGRITY_ERROR_CODE
                            if SCHEMA_INTEGRITY_ERROR_CODE in str(exc)
                            else "database_initialization_failed",
                        },
                    )
                    app.state.persistence_error = str(exc)

    # Issue 39 AC2：CSRF 来源校验（在所有业务路由之前、持久化拒绝之后执行）。
    # 只校验改变状态的请求；未显式配置允许来源时按 X-Forwarded-* / Host /
    # 环回规则推导（见 api/csrf.py），本地开发无需配置即可工作。
    allowed_origins: list[str] = []
    if app.state.settings is not None:
        allowed_origins = list(app.state.settings.allowed_origins)
    app.add_middleware(CsrfOriginMiddleware, allowed_origins=allowed_origins)
    app.state.data_dir = data_dir
    app.state.settings_at_credential = settings_at_credential
    return app
