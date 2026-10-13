from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from fastapi import FastAPI

from bridges.api.app_support import (
    _has_real_qwen_key,
    _register_builtin_domain_packs,
    _register_builtin_workflows,
    _register_domain_pack_capabilities,
)
from bridges.contracts.domain import PackImpactAction, PackImpactCategory, PackImpactItem
from bridges.contracts.workflows import RunProjection
from bridges.domain import DomainPackLoader, DomainPackRegistry, DomainPackValidationRuntime
from bridges.domain.pack_lifecycle import DomainPackLifecycleError, DomainPackLifecycleService
from bridges.domain.workbench import DomainPackWorkbenchService
from bridges.evaluation import EvaluationService
from bridges.expression import ExpressionService
from bridges.lifecycle.backup import BackupService
from bridges.lifecycle.deletion import DeletionService
from bridges.lifecycle.exports import ExportService
from bridges.media import (
    AccessibilityService,
    MediaGenerationService,
    MediaIngestionService,
    MediaPublishService,
    SandboxService,
    StoryboardService,
    build_media_impact_resolver,
    build_media_publish_impact_resolver,
)
from bridges.persistence import SqliteStateStore
from bridges.science import ClaimEvidenceService, ScienceSearchService, ScienceSourceService
from bridges.science.claims import ClaimGraphRevalidationHandler, build_claim_impact_resolver
from bridges.science.service import build_source_impact_resolver
from bridges.workflows import WorkflowError, WorkflowService


def configure_closeout_services(app: FastAPI) -> None:
    settings = app.state.settings
    state_store = app.state.state_store
    bridges_database = app.state.bridges_database
    invalidation_service = app.state.invalidation_service
    model_gateway = app.state.model_gateway
    capability_registry = app.state.capability_registry
    mcp_runtime = app.state.mcp_runtime
    tieba_browser = app.state.tieba_browser_runtime
    # Issue 37: 数据生命周期（导出/删除/备份/恢复）。导出只读业务表（不读
    # 凭据/会话）；删除与恢复会清除账户级外部凭据（QQ SMTP 授权码，恢复后
    # 需重新配置；账户 Qwen Key 已由 GQ-07 启动清退整体退役，不再参与）；
    # 备份在受控一致性点打包数据库快照、对象与身份账户数据，绝不包含凭据、
    # 会话令牌或运行密钥。全部敏感端点经 RecentAuthRequired 敏感门；未配置
    # 数据库（内存模式）时服务为 None，
    # 路由统一 503（与既有持久化服务一致）。
    bridges_database_for_lifecycle = getattr(app.state, "bridges_database", None)
    object_repository_for_lifecycle = getattr(app.state, "object_repository", None)
    object_store_for_lifecycle = getattr(app.state, "object_store", None)
    if (
        bridges_database_for_lifecycle is not None
        and object_repository_for_lifecycle is not None
        and object_store_for_lifecycle is not None
    ):
        from bridges.tieba.account_sessions import TiebaAccountSessionLifecycle

        app.state.export_service = ExportService(
            database=bridges_database_for_lifecycle,
            identity_service=app.state.identity_service,
            observability_service=app.state.observability_service,
        )
        app.state.deletion_service = DeletionService(
            database=bridges_database_for_lifecycle,
            object_repository=object_repository_for_lifecycle,
            identity_service=app.state.identity_service,
            smtp_credential_store=app.state.smtp_credential_store,
            observability_service=app.state.observability_service,
            account_session_lifecycle=(
                TiebaAccountSessionLifecycle(runtime=tieba_browser)
                if tieba_browser is not None
                else None
            ),
        )
        app.state.backup_service = BackupService(
            database=bridges_database_for_lifecycle,
            object_repository=object_repository_for_lifecycle,
            object_store=object_store_for_lifecycle,
            identity_service=app.state.identity_service,
            smtp_credential_store=app.state.smtp_credential_store,
            observability_service=app.state.observability_service,
            # 挂载条件保证 state_store 是 SqliteStateStore（bridges.db 与
            # 状态存储共用同一文件，恢复需重开其连接）。
            state_store=cast(SqliteStateStore, state_store),
            mcp_runtime=mcp_runtime,
        )

    # T040/T046: register the built-in domain packs as candidates and attach the
    # expert workbench. The workbench owns three-signature release, semantic
    # diffs and gray-release candidates; it never activates a pack implicitly.
    _register_domain_pack_capabilities(capability_registry)
    domain_pack_loader = DomainPackLoader(capability_registry=capability_registry)
    domain_pack_registry = DomainPackRegistry(domain_pack_loader)
    _register_builtin_domain_packs(domain_pack_registry)
    domain_pack_workbench = DomainPackWorkbenchService(
        registry=domain_pack_registry,
        runtime=DomainPackValidationRuntime(domain_pack_loader),
        loader=domain_pack_loader,
    )
    app.state.domain_pack_workbench = domain_pack_workbench

    # T047: 挂接领域包生命周期服务。它负责失效事件、紧急撤销、影响解析、
    # 重验证推进与受信回滚；影响源随各下游服务出现时注册。
    domain_pack_lifecycle = DomainPackLifecycleService(
        workbench=domain_pack_workbench,
        loader=domain_pack_loader,
    )
    app.state.domain_pack_lifecycle = domain_pack_lifecycle

    # T006/T009: attach the workflow service and register workflows。
    # Issue 43：运行状态落 SQLite（workflow_runs），进程重建后从磁盘
    # 恢复进行中的运行；未配置数据库时保持纯内存。
    workflow_service = WorkflowService(
        scope_enforcer=app.state.scope_enforcer,
        model_gateway=model_gateway,
        observability_service=app.state.observability_service,
        invalidation_service=invalidation_service,
        profile_service=app.state.profile_service,
        database=bridges_database,
    )
    _register_builtin_workflows(workflow_service)
    app.state.workflow_service = workflow_service

    # T047: 撤销或失效的领域包不能开始新运行；运行门由生命周期服务裁决。
    def _pack_gate(pack_refs: Sequence[str]) -> None:
        try:
            domain_pack_lifecycle.require_packs_usable(pack_refs)
        except DomainPackLifecycleError as exc:
            raise WorkflowError(str(exc)) from exc

    workflow_service.set_pack_gate(_pack_gate)

    # T047: 运行影响源——引用该包版本的全部运行。
    def _pack_run_source(pack_id: str, version: str) -> list[PackImpactItem]:
        return [
            PackImpactItem(
                item_id=f"run:{projection.run_id}",
                category=PackImpactCategory.RUN,
                ref_id=projection.run_id,
                label=f"运行 {projection.run_id}（{projection.workflow_name}）",
                action=PackImpactAction.PRESERVE_AND_MARK,
                account_id=projection.context_envelope.account_id,
                project_id=projection.project_id,
                details={"run_status": projection.run_status.value},
            )
            for projection in workflow_service.find_runs_using_pack(pack_id, version)
        ]

    domain_pack_lifecycle.register_pack_impact_source(
        PackImpactCategory.RUN, _pack_run_source
    )

    # T012: attach the in-memory evaluation service and routes.
    evaluation_service = EvaluationService(workflow_service=workflow_service)
    app.state.evaluation_service = evaluation_service

    # T013: attach the in-memory science source service and register its impact
    # resolver so source invalidation propagates to index, cache and runs.
    science_source_service = ScienceSourceService(
        scope_enforcer=app.state.scope_enforcer,
        invalidation_service=invalidation_service,
        model_gateway=(
            # Issue 41（AC3）：只有真实环境密钥已配置时才接模型网关——
            # 无密钥时能力明确停用，媒体/科学提取走确定性提取器，绝不
            # 用 Stub 适配器产出假模型结果。
            model_gateway if _has_real_qwen_key(settings) else None
        ),
    )
    invalidation_service.register_impact_resolver(
        "science_source", build_source_impact_resolver(science_source_service)
    )
    app.state.science_source_service = science_source_service

    # T014: attach the scoped hybrid search service.
    app.state.science_search_service = ScienceSearchService(
        source_service=science_source_service,
        scope_enforcer=app.state.scope_enforcer,
        invalidation_service=invalidation_service,
    )

    # T015: attach the claim--evidence--citation service.
    claim_evidence_service = ClaimEvidenceService(
        source_service=science_source_service,
        search_service=app.state.science_search_service,
        invalidation_service=invalidation_service,
        model_gateway=model_gateway,
        scope_enforcer=app.state.scope_enforcer,
    )
    app.state.claim_evidence_service = claim_evidence_service

    # T017: source invalidation must propagate to claim graphs and fact lock sets,
    # and revalidation must create new graph versions rather than overwrite history.
    invalidation_service.register_impact_resolver(
        "claim_graph", build_claim_impact_resolver(claim_evidence_service)
    )
    invalidation_service.register_revalidation_handler(
        "claim_graph", ClaimGraphRevalidationHandler(claim_evidence_service)
    )

    # T047: Claim、Evidence 与 Wording 影响源——运行链路上派生图内的
    # Claim、Evidence 与措辞约束需要重验证。
    def _pack_runs_for(pack_id: str, version: str) -> list[RunProjection]:
        return workflow_service.find_runs_using_pack(pack_id, version)

    def _pack_graphs_for(pack_id: str, version: str) -> list[Any]:
        runs = _pack_runs_for(pack_id, version)
        return claim_evidence_service.find_graphs_by_run_ids(
            [projection.run_id for projection in runs]
        )

    def _pack_claim_source(pack_id: str, version: str) -> list[PackImpactItem]:
        return [
            PackImpactItem(
                item_id=f"claim:{graph.graph_id}",
                category=PackImpactCategory.CLAIM,
                ref_id=graph.graph_id,
                label=f"Claim 图 {graph.graph_id}（{len(graph.claims)} 条 Claim）",
                action=PackImpactAction.REVALIDATE,
                account_id=graph.account_id,
                project_id=graph.project_id,
                details={"graph_status": graph.status.value},
            )
            for graph in _pack_graphs_for(pack_id, version)
        ]

    def _pack_evidence_source(pack_id: str, version: str) -> list[PackImpactItem]:
        return [
            PackImpactItem(
                item_id=f"evidence:{evidence.evidence_id}",
                category=PackImpactCategory.EVIDENCE,
                ref_id=evidence.evidence_id,
                label=f"Evidence {evidence.evidence_id}（{evidence.relation.value}）",
                action=PackImpactAction.REVALIDATE,
                account_id=graph.account_id,
                project_id=graph.project_id,
            )
            for graph in _pack_graphs_for(pack_id, version)
            for evidence in graph.evidence
        ]

    def _pack_wording_source(pack_id: str, version: str) -> list[PackImpactItem]:
        return [
            PackImpactItem(
                item_id=f"wording:{graph.graph_id}",
                category=PackImpactCategory.WORDING,
                ref_id=graph.graph_id,
                label=f"措辞约束 {graph.graph_id}（校验报告需重验证）",
                action=PackImpactAction.REVALIDATE,
                account_id=graph.account_id,
                project_id=graph.project_id,
            )
            for graph in _pack_graphs_for(pack_id, version)
        ]

    def _pack_project_source(pack_id: str, version: str) -> list[PackImpactItem]:
        projects_seen: dict[str, str] = {}
        for projection in _pack_runs_for(pack_id, version):
            projects_seen[projection.project_id] = projection.context_envelope.account_id
        return [
            PackImpactItem(
                item_id=f"project:{project_id}",
                category=PackImpactCategory.PROJECT,
                ref_id=project_id,
                label=f"项目 {project_id}",
                action=PackImpactAction.PRESERVE_AND_MARK,
                account_id=account_id,
                project_id=project_id,
            )
            for project_id, account_id in projects_seen.items()
        ]

    def _pack_user_action_source(pack_id: str, version: str) -> list[PackImpactItem]:
        return [
            PackImpactItem(
                item_id=f"user_action:{projection.context_envelope.account_id}:{projection.run_id}",
                category=PackImpactCategory.USER_ACTION,
                ref_id=projection.run_id,
                label=(
                    f"用户动作：{projection.context_envelope.account_id} 提交了运行 "
                    f"{projection.run_id}（{projection.project_id}）"
                ),
                action=PackImpactAction.PRESERVE_AND_MARK,
                account_id=projection.context_envelope.account_id,
                project_id=projection.project_id,
                details={"run_status": projection.run_status.value},
            )
            for projection in _pack_runs_for(pack_id, version)
        ]

    for category, source in (
        (PackImpactCategory.CLAIM, _pack_claim_source),
        (PackImpactCategory.EVIDENCE, _pack_evidence_source),
        (PackImpactCategory.WORDING, _pack_wording_source),
        (PackImpactCategory.PROJECT, _pack_project_source),
        (PackImpactCategory.USER_ACTION, _pack_user_action_source),
    ):
        domain_pack_lifecycle.register_pack_impact_source(category, source)

    # T030: attach the media ingestion service and register its impact resolver so
    # media asset invalidation propagates to index, cache and runs.
    media_ingestion_service = MediaIngestionService(
        scope_enforcer=app.state.scope_enforcer,
        invalidation_service=invalidation_service,
        model_gateway=(
            # 与 ScienceSourceService 同一合同（Issue 41 AC3）：无真实密钥
            # 时不接模型网关，提取走确定性提取器。
            model_gateway if _has_real_qwen_key(settings) else None
        ),
    )
    invalidation_service.register_impact_resolver(
        "media_asset", build_media_impact_resolver(media_ingestion_service)
    )
    app.state.media_ingestion_service = media_ingestion_service

    # T033: attach the storyboard and sandbox services for historical read
    # access only. Their deterministic generators/runtimes are no longer
    # instantiated in production composition because the corresponding write
    # endpoints are retired (Issue 08 / ADR-0026).
    app.state.storyboard_service = StoryboardService()
    app.state.sandbox_service = SandboxService()

    # T032/T034: attach the media generation and accessibility services for
    # historical read access only. The retired chart/figure/bundle write
    # endpoints no longer drive deterministic generation or narration synthesis.
    app.state.media_generation_service = MediaGenerationService()
    app.state.accessibility_service = AccessibilityService(
        storyboard_service=app.state.storyboard_service,
        generation_service=app.state.media_generation_service,
        media_ingestion_service=media_ingestion_service,
        narration_synthesizer=None,
    )

    # T035: attach the multi-modal publish service for cross-media consistency
    # checking and multi-modal publish gate evaluation. Register the publish
    # impact resolver so source or fact-lock invalidation propagates.
    publish_service = MediaPublishService(
        generation_service=app.state.media_generation_service,
        storyboard_service=app.state.storyboard_service,
        media_ingestion_service=media_ingestion_service,
        accessibility_service=app.state.accessibility_service,
        invalidation_service=invalidation_service,
    )
    app.state.media_publish_service = publish_service
    invalidation_service.register_impact_resolver(
        "media_publish", build_media_publish_impact_resolver(publish_service)
    )

    # T025/T029: attach the expression service. It consumes claim graphs and fact
    # locks from T016, memory slices from T019, and uses the workflow service and
    # invalidation service for release gating. Legacy write routes are retired;
    # the service no longer records model run locks for draft generation.
    expression_service = ExpressionService(
        claim_service=claim_evidence_service,
        profile_service=app.state.profile_service,
        invalidation_service=invalidation_service,
        workflow_service=workflow_service,
    )
    app.state.expression_service = expression_service

    # T047: 产物影响源——运行链路派生的表达产物需要重验证。
    def _pack_artifact_source(pack_id: str, version: str) -> list[PackImpactItem]:
        runs = _pack_runs_for(pack_id, version)
        return [
            PackImpactItem(
                item_id=f"artifact:{draft.draft_id}",
                category=PackImpactCategory.ARTIFACT,
                ref_id=draft.draft_id,
                label=f"表达产物 {draft.draft_id}（{draft.genre.value}）",
                action=PackImpactAction.REVALIDATE,
                account_id=draft.account_id,
                project_id=draft.project_id,
                details={"artifact_trust_status": draft.artifact_trust_status.value},
            )
            for draft in expression_service.find_drafts_by_run_ids(
                [projection.run_id for projection in runs]
            )
        ]

    domain_pack_lifecycle.register_pack_impact_source(
        PackImpactCategory.ARTIFACT, _pack_artifact_source
    )
