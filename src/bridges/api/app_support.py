from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI

from bridges.ai import CapabilityRegistry, CapabilityRegistryError
from bridges.api import commute as commute_api
from bridges.chat import ConversationRepository
from bridges.config import Settings
from bridges.contracts.ai import CapabilityKind, CapabilityRecord, CapabilityStatus
from bridges.contracts.workflows import WorkflowRunStatus
from bridges.credentials.global_credential import is_global_qwen_key_configured
from bridges.credentials.store import CredentialStorePort, build_credential_store
from bridges.domain import (
    DomainPackRegistry,
    create_astronomy_pack,
    create_computer_science_pack,
    create_earth_climate_pack,
    create_life_science_pack,
    create_math_formal_proof_pack,
    create_medical_high_risk_pack,
    create_physics_chemistry_pack,
    create_standards_datasets_pack,
)
from bridges.invalidation import AffectedDownstream, InvalidationService
from bridges.paper.sources import ENRICH_TIMEOUT_SECONDS
from bridges.profiles import PROFILE_EXTRACTION_MAX_NEIGHBORS, ProfileSourceMessage
from bridges.workflows import WorkflowService

RETIRED_EXPRESSION_CAPABILITY = "expression_draft_generation"
RETIRED_EXPRESSION_CAPABILITY_VERSION = "1"
_RETIRED_CAPABILITY_REGRESSION_MESSAGE = (
    "已退役能力 expression_draft_generation 被重新注册，实例拒绝提供服务。"
)


def _register_builtin_workflows(service: WorkflowService) -> None:
    """Register the minimal workflow templates available at T006/T009."""
    service.register_workflow(
        name="generic_science_task",
        version="1",
        nodes=[
            {
                "node_id": "compile_context",
                "node_name": "编译上下文",
                "human_gate": False,
                "capability_name": "qwen_text_chat",
                "capability_version": "1",
            },
            {
                "node_id": "produce_output",
                "node_name": "生成产物",
                "human_gate": False,
                "capability_name": "qwen_structured_output",
                "capability_version": "1",
            },
        ],
        terminal_states=[
            WorkflowRunStatus.SUCCEEDED,
            WorkflowRunStatus.BLOCKED,
            WorkflowRunStatus.CANCELLED,
        ],
    )


def _register_builtin_invalidation_resolvers(service: InvalidationService) -> None:
    """Register generic impact resolvers for cache, index, runs and vault capsules.

    T011 foundation provides these resolvers so that any object invalidation
    produces a scope-correct impact set covering the most common downstream
    consumers. Domain modules register additional resolvers later without
    changing tombstone priority, history retention or failure lockout.
    """

    def _cache_resolver(event: Any) -> list[AffectedDownstream]:
        return [
            AffectedDownstream(
                downstream_id=f"cache:{event.object_ref.object_id}",
                downstream_type="cache",
                object_refs=[event.object_ref.object_id],
                scope_envelope=event.scope_envelope,
                action="invalidate",
            )
        ]

    def _index_resolver(event: Any) -> list[AffectedDownstream]:
        return [
            AffectedDownstream(
                downstream_id=f"index:{event.object_ref.object_id}",
                downstream_type="index_projection",
                object_refs=[event.object_ref.object_id],
                scope_envelope=event.scope_envelope,
                action="revalidate",
            )
        ]

    def _run_resolver(event: Any) -> list[AffectedDownstream]:
        return [
            AffectedDownstream(
                downstream_id=f"run:{event.object_ref.object_id}",
                downstream_type="workflow_run",
                object_refs=[event.object_ref.object_id],
                scope_envelope=event.scope_envelope,
                action="block_new_use",
            )
        ]

    def _vault_capsule_resolver(event: Any) -> list[AffectedDownstream]:
        return [
            AffectedDownstream(
                downstream_id=f"vault_capsule:{event.object_ref.object_id}",
                downstream_type="vault_capsule",
                object_refs=[event.object_ref.object_id],
                scope_envelope=event.scope_envelope,
                action="revoke",
            )
        ]

    service.register_impact_resolver("cache", _cache_resolver)
    service.register_impact_resolver("index_projection", _index_resolver)
    service.register_impact_resolver("workflow_run", _run_resolver)
    service.register_impact_resolver("vault_capsule", _vault_capsule_resolver)


def _has_real_qwen_key(settings: Settings | None) -> bool:
    """是否配置了非空全局百炼运行凭据（BRIDGES_QWEN_API_KEY/_FILE）。

    GQ-01：全局凭据是正式运行唯一的 Qwen 认证来源，真实适配器注册与模型
    网关接线都以本判定为准——无真实凭据时能力保持未绑定，绝不注册 Stub
    或假成功；启动硬门（CLI 与健康检查）保证正式运行不会缺 Key 半启动。
    账户级密钥已随 GQ-06/GQ-07 整体清退，不再驱动任何能力判定。
    """
    return settings is not None and is_global_qwen_key_configured(settings)


def retired_capability_regression(app: FastAPI) -> str | None:
    """已退役表达能力防复活判定：注册表再次出现该能力时返回错误说明。

    返回 ``None`` 表示注册表干净；否则返回供健康检查与 503 响应使用的
    中文说明。判定是活的（每次请求/健康检查都查询注册表），因此运行时
    重新注册同样失败关闭，不限于启动时刻。
    """
    registry = getattr(app.state, "capability_registry", None)
    if registry is None:
        return None
    try:
        registry.get(
            RETIRED_EXPRESSION_CAPABILITY, RETIRED_EXPRESSION_CAPABILITY_VERSION
        )
    except CapabilityRegistryError:
        return None
    return _RETIRED_CAPABILITY_REGRESSION_MESSAGE


def _register_domain_pack_capabilities(capability_registry: CapabilityRegistry) -> None:
    """Register the deterministic TOOL capabilities declared by built-in packs.

    The packs declare logical capabilities (e.g. formal_proof_check) without
    binding to a concrete implementation; the registry maps each logical
    capability to a verified deterministic adapter, as required by T040.
    """
    factories = (
        create_math_formal_proof_pack,
        create_physics_chemistry_pack,
        create_life_science_pack,
        create_medical_high_risk_pack,
        create_earth_climate_pack,
        create_astronomy_pack,
        create_computer_science_pack,
        create_standards_datasets_pack,
    )
    for factory in factories:
        manifest = factory().manifest
        for validator in manifest.validators:
            try:
                capability_registry.get(
                    validator.capability_name, validator.capability_version
                )
            except CapabilityRegistryError:
                capability_registry.register(
                    CapabilityRecord(
                        name=validator.capability_name,
                        version=validator.capability_version,
                        kind=CapabilityKind.TOOL,
                        vendor="builtin",
                        region="local",
                        model_id="deterministic",
                        input_schema_version=validator.input_schema_version,
                        output_schema_version=validator.output_schema_version,
                        status=CapabilityStatus.VERIFIED,
                    )
                )


def _register_builtin_domain_packs(registry: DomainPackRegistry) -> None:
    """Register the eight built-in domain packs as draft candidates.

    T040–T045 deliver the packs as loadable candidates; T046 exposes them to
    the expert workbench where maintainers, reviewers and releasers govern
    signing, semantic diffs, gray release and activation.
    """
    for factory in (
        create_math_formal_proof_pack,
        create_physics_chemistry_pack,
        create_life_science_pack,
        create_medical_high_risk_pack,
        create_earth_climate_pack,
        create_astronomy_pack,
        create_computer_science_pack,
        create_standards_datasets_pack,
    ):
        registry.register(factory())


def _credential_store_for_namespace(
    settings: Settings | None, data_dir: Path | None, namespace: str
) -> CredentialStorePort:
    """按运行载体为指定命名空间创建凭据存储（规则与后台执行器共用一处）。"""
    return build_credential_store(settings, data_dir, namespace=namespace)


def _amap_web_service_key(app: Any) -> str | None:
    """读取当前生效的高德路线 Web 服务 Key；未配置时返回 None（模块据此降级）。"""
    return commute_api.secret_setting(
        getattr(app.state, "settings", None), "amap_web_service_key"
    )


def _current_task_reference(
    app: Any, account_id: str, conversation_id: str
) -> tuple[str | None, int | None] | None:
    """模块内核提交守卫的任务版本快照（当前任务不存在时为 None）。"""
    service = getattr(app.state, "task_service", None)
    if service is None:
        return None
    context = service.current_reference_context(account_id, conversation_id)
    if context is None:
        return None
    return (context.task_id, context.version)




def _paper_metadata_client() -> httpx.Client:
    """论文元数据补充的共享客户端（独立短超时；不承载账户凭据）。"""
    return httpx.Client(timeout=ENRICH_TIMEOUT_SECONDS)


def _profile_neighbor_reader(
    repository: ConversationRepository,
) -> Callable[[str, str, str], list[str]]:
    """读取当前消息之前的邻近用户原文，仅供画像抽取解析回指。"""

    def read(account_id: str, conversation_id: str, message_id: str) -> list[str]:
        neighbors: list[str] = []
        for message in reversed(repository.list_messages(account_id, conversation_id)):
            if message.message_id == message_id:
                break
            role = getattr(message.role, "value", message.role)
            if role == "user" and message.content.strip():
                neighbors.append(message.content)
            if len(neighbors) >= PROFILE_EXTRACTION_MAX_NEIGHBORS:
                break
        return list(reversed(neighbors))

    return read


def _atomic_profile_source_reader(
    repository: ConversationRepository,
) -> Callable[[str, str], ProfileSourceMessage | None]:
    """按账户读取画像依据来源消息的最小快照（改进工单 20）。

    聊天消息是物理删除：消息行不存在即来源已删除。仓库存取一律按账户作用域，
    跨账户消息标识查不到，不会给出其他账户的定位。
    """

    def read(account_id: str, message_id: str) -> ProfileSourceMessage | None:
        message = repository.get_message(account_id, message_id)
        if message is None:
            return None
        return ProfileSourceMessage(
            message_id=message.message_id,
            conversation_id=message.conversation_id,
            role=getattr(message.role, "value", message.role),
            status=getattr(message.status, "value", message.status),
            content=message.content,
            created_at=message.created_at,
        )

    return read
