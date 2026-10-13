from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from bridges.api.app_support import (
    _atomic_profile_source_reader,
    _credential_store_for_namespace,
    _register_builtin_invalidation_resolvers,
)
from bridges.arxiv_mcp.service import ArxivSearchService
from bridges.chat import ConversationRepository
from bridges.closeout.fixtures import CloseoutArxivClient, CloseoutWebSearchClient
from bridges.contracts.projects import ObjectRef
from bridges.identity import IdentityService
from bridges.institution import InstitutionService
from bridges.invalidation import AffectedDownstream, InvalidationService
from bridges.learning import (
    InMemoryLearningRepository,
    LearningPathService,
    LearningService,
    TeachingProgressService,
    TeachingService,
)
from bridges.mcp.service import McpService
from bridges.observability.service import ObservabilityService
from bridges.profiles import (
    AtomicProfileService,
    FourDimensionContractGate,
    FourDimensionProfileService,
    InMemoryAtomicProfileRepository,
    InMemoryFourDimensionProfileRepository,
    InMemoryProfileRepository,
    ProfileService,
    SqliteAtomicProfileRepository,
    SqliteFourDimensionProfileRepository,
)
from bridges.profiles.sqlite_repository import SqliteProfileRepository
from bridges.projects import ProjectService
from bridges.retirement import run_reminder_retirement
from bridges.scope import ScopeEnforcer
from bridges.sharing import SharingService
from bridges.sync import SyncService
from bridges.vault import (
    FernetVaultEncryptionAdapter,
    InMemoryDeviceKeychain,
    InMemoryDevicePairingRepository,
    InMemoryVaultRepository,
    MemoryDeviceVaultPort,
    VaultService,
)
from bridges.web_search.providers import build_web_search_provider
from bridges.web_search.repository import WebSearchCacheRepository
from bridges.web_search.service import SearchClient, WebSearchService


def configure_core_services(
    app: FastAPI,
    *,
    arxiv_service_factory: type[ArxivSearchService] = ArxivSearchService,
) -> None:
    state_store = app.state.state_store
    data_dir = app.state.data_dir
    settings_at_credential = app.state.settings_at_credential
    # T007: attach the shared scope enforcer. All services, routes and background
    # task validators use the same interpreter so isolation rules do not drift.
    app.state.scope_enforcer = ScopeEnforcer()

    # T011: attach the shared invalidation service and register generic downstream
    # resolvers before any consumer is constructed.
    invalidation_service = InvalidationService(
        scope_enforcer=app.state.scope_enforcer,
    )
    _register_builtin_invalidation_resolvers(invalidation_service)
    app.state.invalidation_service = invalidation_service

    # T003: use the durable state port when configured; tests can omit it and
    # retain isolated in-memory services.
    app.state.identity_service = IdentityService(
        state_store=state_store,
        object_repository=app.state.object_repository,
    )

    # T004: project ownership is durable whenever the configured state store is.
    app.state.project_service = ProjectService(
        scope_enforcer=app.state.scope_enforcer,
        state_store=state_store,
    )

    # T005/T038: attach the in-memory vault service, device port, and encrypted
    # local storage seam. Device-local objects are encrypted with a data key
    # wrapped by the device key stored in the system keychain.
    vault_repository = InMemoryVaultRepository(state_store=state_store)
    device_keychain = InMemoryDeviceKeychain()
    vault_encryption = FernetVaultEncryptionAdapter()
    device_pairing_repository = InMemoryDevicePairingRepository(state_store=state_store)
    app.state.vault_service = VaultService(
        repository=vault_repository,
        device_port=MemoryDeviceVaultPort(vault_repository),
        scope_enforcer=app.state.scope_enforcer,
        invalidation_service=invalidation_service,
        device_keychain=device_keychain,
        vault_encryption=vault_encryption,
        device_pairing_repository=device_pairing_repository,
    )
    app.state.device_keychain = device_keychain

    def authorize_sync_object(account_id: str, object_ref: ObjectRef) -> bool:
        """复用共享项目成员检查，默认拒绝未知对象域。"""
        if object_ref.domain.value == "personal_vault":
            return object_ref.owner_id == account_id
        if object_ref.domain.value == "shared_project":
            sharing_service = getattr(app.state, "sharing_service", None)
            if sharing_service is None:
                return False
            try:
                sharing_service.get_shared_project(account_id, object_ref.owner_id)
            except Exception:
                return False
            return True
        return False

    # T039: synchronization reads the same device certificates and key epochs
    # as the vault boundary, so revoked devices cannot submit old outbox edits.
    app.state.sync_service = SyncService(
        device_pairing_repository=device_pairing_repository,
        state_store=state_store,
        object_authorizer=authorize_sync_object,
    )

    # T036: attach the explicit sharing service. It coordinates share previews,
    # minimized project copies, object grants and short-lived invite tokens.
    app.state.sharing_service = SharingService(
        vault_service=app.state.vault_service,
        scope_enforcer=app.state.scope_enforcer,
        identity_service=app.state.identity_service,
    )

    # T037: attach the institution management service. It manages institutions,
    # memberships, policies, institution-owned projects and controlled content
    # access while preserving the personal vault boundary.
    app.state.institution_service = InstitutionService()
    app.state.institution_service.bind_sharing_service(app.state.sharing_service)

    # T037: bind institution providers into the scope enforcer now that both the
    # institution service and sharing service exist. This breaks the construction
    # cycle without changing the enforcer's public interface. Issue 44: shared
    # project membership is also bound here so every service asks the enforcer
    # the same "who may access this project" question.
    app.state.scope_enforcer.set_institution_providers(
        institution_membership_provider=app.state.institution_service.get_membership,
        project_tenant_provider=app.state.sharing_service.get_project_institution_id,
        shared_project_membership_provider=app.state.sharing_service.is_member,
    )

    # T010: attach the observability service early so downstream services can
    # emit privacy-preserving audit events.
    app.state.observability_service = ObservabilityService()
    # T021-T024: 学习域服务（纯内存，无数据库依赖）在聊天服务之前挂载——
    # 生涯规划（Issue 29）需要按账户作用域读取学习使命/知识状态/学习记录。
    learning_repository = InMemoryLearningRepository()
    app.state.learning_service = LearningService(repository=learning_repository)
    app.state.teaching_service = TeachingService(repository=learning_repository)
    app.state.learning_path_service = LearningPathService(
        repository=learning_repository,
    )
    # Issue 01：生产公网搜索只登记 Tavily（build_web_search_provider 装配点）；
    # 失败交给聊天层的 Qwen 一般知识降级，绝不在本轮切换第二提供方。
    # Issue 04：test 环境关闭健康快照自动刷新——单元/集成测试不得因此
    # 访问公网；development/production 由 readiness 轮询驱动合并刷新。
    web_search_database = getattr(app.state, "bridges_database", None)
    use_closeout_fixtures = bool(
        app.state.settings is not None
        and app.state.settings.environment.lower() == "test"
        and app.state.settings.closeout_fixture_mode
    )
    web_search_health_auto_refresh = not (
        app.state.settings is not None
        and app.state.settings.environment.lower() == "test"
    )
    if use_closeout_fixtures:
        web_search_client: SearchClient = CloseoutWebSearchClient()
    else:
        # 缺 Key 时客户端构造不失败：联网搜索返回「未配置搜索凭据」投影。
        web_search_client = build_web_search_provider(app.state.settings)
    app.state.web_search_service = WebSearchService(
        client=web_search_client,
        observability=app.state.observability_service,
        cache=(
            WebSearchCacheRepository(web_search_database)
            if web_search_database is not None
            else None
        ),
        health_auto_refresh=web_search_health_auto_refresh,
    )
    # Issue 22：固定版本、只读、受限的 arXiv MCP；只接收本地脱敏后的
    # public_query_terms，不继承账户凭据或画像上下文。
    app.state.arxiv_search_service = arxiv_service_factory(
        client=CloseoutArxivClient() if use_closeout_fixtures else None,
        observability=app.state.observability_service
    )
    # Issue 04：应用启动时预热常驻 worker（spawn + 握手，含 httpx 导入），
    # 首次搜索不再付冷启动；预热失败只记日志并保留懒启动兜底。test 环境
    # 的应用装配不应产生子进程，closeout 替身（无真实 worker）同样跳过。
    arxiv_settings_at_warmup = app.state.settings
    if (
        not use_closeout_fixtures
        and arxiv_settings_at_warmup is not None
        and arxiv_settings_at_warmup.environment.lower() != "test"
    ):
        app.state.arxiv_search_service.warmup()
    app.router.add_event_handler("shutdown", app.state.arxiv_search_service.close)

    def _shutdown_mcp_servers() -> None:
        service: McpService | None = getattr(app.state, "mcp_service", None)
        if service is not None:
            service.shutdown()

    app.router.add_event_handler("shutdown", _shutdown_mcp_servers)

    # Issue 33: QQ SMTP 授权码使用独立命名空间（smtp）的凭据存储（GQ-07
    # 后是凭据存储唯一用途，账户 Qwen 命名空间已整体清退）；授权码绝不
    # 进入 SQLite、日志、模型或导出。源码环境使用操作系统凭据库（keyring，
    # Windows 兜底 DPAPI）；容器环境使用自动生成主密钥保护的加密凭据卷
    # （数据目录 credentials/ 子目录）。未配置数据库（内存模式，测试/E2E）
    # 时使用进程内替身，保证测试确定性。
    smtp_credential_store = _credential_store_for_namespace(
        settings_at_credential, data_dir, namespace="smtp"
    )
    app.state.smtp_credential_store = smtp_credential_store

    # Issue 03：退役前先按账户幂等停止遗留提醒、结束 SMTP 验证并清除
    # 专用授权码。报告只保存数量和状态；空环境也写入零迁移结果。
    if app.state.bridges_database is not None:
        app.state.reminder_retirement = run_reminder_retirement(
            database=app.state.bridges_database,
            credential_store=smtp_credential_store,
            state_store=state_store,
        )

    # T018/T020: attach the profile service. Candidate profiles cannot be
    # treated as stable facts until the user accepts them through the human
    # decision loop; accepted candidates are promoted to active assertions. At
    # T020 the service also freezes, deletes, rolls back and exports assertions
    # while propagating invalidations to slices, cache, index and runs.
    # Issue 26: 配置持久化数据目录时改用 SQLite 仓库，画像、许可与通知在
    # 重启后仍可追溯；未配置（内存模式，仅测试/E2E）时保持进程内仓库。
    profile_database = getattr(app.state, "bridges_database", None)
    profile_repository = (
        SqliteProfileRepository(profile_database)
        if profile_database is not None
        else InMemoryProfileRepository()
    )
    app.state.profile_service = ProfileService(
        repository=profile_repository,
        scope_enforcer=app.state.scope_enforcer,
        invalidation_service=invalidation_service,
        observability_service=app.state.observability_service,
    )
    app.state.teaching_progress_service = (
        TeachingProgressService(profile_database) if profile_database is not None else None
    )

    def _adjust_learning_after_profile_change(
        account_id: str,
        trigger: Any,
        trigger_key: str,
        reason: str,
    ) -> None:
        if app.state.teaching_progress_service is not None:
            app.state.teaching_progress_service.apply_profile_event(
                account_id, trigger, trigger_key, reason
            )

    four_dimension_repository = (
        SqliteFourDimensionProfileRepository(profile_database)
        if profile_database is not None
        else InMemoryFourDimensionProfileRepository()
    )
    app.state.four_dimension_profile_service = FourDimensionProfileService(
        source_repository=profile_repository,
        repository=four_dimension_repository,
        learning_adjustment_callback=_adjust_learning_after_profile_change,
    )
    app.state.four_dimension_contract_gate = FourDimensionContractGate(
        app.state.four_dimension_profile_service
    )

    # V2 Issue 08：无类别的原子画像列表。四维记录仍是提取与冲突消解引擎，
    # 原子条目是它的用户可见镜像；两者共用同一份证据，因此这里复用同一个
    # 数据目录：有持久化数据目录时落 bridges.db，内存模式（测试/E2E）不落盘。
    atomic_profile_repository = (
        SqliteAtomicProfileRepository(profile_database)
        if profile_database is not None
        else InMemoryAtomicProfileRepository()
    )
    # 改进工单 20：依据展开与自动画像提取共用同一个按账户读取会话消息的
    # 仓库；内存模式没有聊天仓库时来源如实标为不可读，不伪造定位。
    profile_conversation_repository = (
        ConversationRepository(profile_database) if profile_database is not None else None
    )
    app.state.atomic_profile_service = AtomicProfileService(
        four_dimensions=app.state.four_dimension_profile_service,
        repository=atomic_profile_repository,
        message_reader=(
            _atomic_profile_source_reader(profile_conversation_repository)
            if profile_conversation_repository is not None
            else None
        ),
        observability_service=app.state.observability_service,
    )

    # T020: register a profile-specific impact resolver so assertion deletions
    # produce scope-correct memory-slice downstreams in addition to the generic
    # cache, index and run resolvers registered by T011.
    def _profile_assertion_resolver(event: Any) -> list[AffectedDownstream]:
        affected: list[AffectedDownstream] = []
        profile_service = app.state.profile_service
        object_ref = event.object_ref
        if object_ref.domain.value != "personal_vault":
            return affected
        slices = profile_service.find_slices_for_assertion(
            object_ref.owner_id, object_ref.object_id
        )
        for slice_ in slices:
            affected.append(
                AffectedDownstream(
                    downstream_id=f"memory_slice:{slice_.slice_id}",
                    downstream_type="memory_slice",
                    object_refs=[slice_.slice_id],
                    scope_envelope=event.scope_envelope,
                    action="revoke",
                    details={"assertion_id": object_ref.object_id},
                )
            )
        return affected

    invalidation_service.register_impact_resolver(
        "profile_assertion", _profile_assertion_resolver
    )

    # T009/T059: attach the capability registry, model gateway and adapters.
    # Issue 09：生产组合由 bridges.ai.production 单一装配（批准矩阵 +
    # 真实适配器接线），API 组合根、CLI 启动门与发布门复用同一实现；
    app.state.profile_database = profile_database
    app.state.profile_conversation_repository = profile_conversation_repository
    app.state.use_closeout_fixtures = use_closeout_fixtures
