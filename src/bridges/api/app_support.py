from __future__ import annotations

import base64
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI

from bridges.ai import CapabilityRegistry, CapabilityRegistryError
from bridges.api import commute as commute_api
from bridges.chat import ConversationRepository
from bridges.chat.attachments import CHAT_ATTACHMENT_MEDIA_TYPES, PHOTO_MEDIA_TYPES
from bridges.chat.attachments_repository import AttachmentRepository
from bridges.config import Settings
from bridges.contracts.ai import (
    CapabilityKind,
    CapabilityRecord,
    CapabilityStatus,
    ModelCallStatus,
)
from bridges.contracts.atomic_profile import (
    AtomicProfileItemStatus,
    AtomicProfileMaterialSource,
    AtomicProfileMaterialSourceStatus,
)
from bridges.contracts.projects import ObjectDomain
from bridges.contracts.workflows import RunContextEnvelope, WorkflowRunStatus
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
from bridges.ingestion.ocr import (
    OCR_CAPABILITY_NAME,
    OCR_CAPABILITY_VERSION,
    OCR_IMAGE_PROMPT,
)
from bridges.invalidation import AffectedDownstream, InvalidationService
from bridges.paper.sources import ENRICH_TIMEOUT_SECONDS
from bridges.profiles import (
    PROFILE_EXTRACTION_MAX_NEIGHBORS,
    PROFILE_EXTRACTION_MAX_RELATED_MEMORIES,
    AtomicProfileService,
    ProfileNeighborMessage,
    ProfileSourceMessage,
)
from bridges.profiles.automatic import AutomaticProfileError
from bridges.profiles.materials import (
    MATERIAL_READ_FAILED_ERROR,
    DesignatedMaterial,
    MaterialKind,
    MaterialRead,
    MaterialSegment,
    material_readiness,
)
from bridges.retrieval.repository import RetrievalRepository
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
) -> Callable[[str, str, str], list[ProfileNeighborMessage]]:
    """读取当前消息之前的有界邻近窗口，仅供画像抽取解析回指（工单 05）。

    按会话语序取当前消息之前最多 ``PROFILE_EXTRACTION_MAX_NEIGHBORS`` 条
    消息（含助手消息——跨轮指代需要助手提问等前文），严格不读未来；
    返回结构化消息，提交期片段核验复用同一读取器。
    """

    def read(
        account_id: str, conversation_id: str, message_id: str
    ) -> list[ProfileNeighborMessage]:
        messages = list(repository.list_messages(account_id, conversation_id))
        index = next(
            (
                position
                for position, message in enumerate(messages)
                if message.message_id == message_id
            ),
            None,
        )
        if index is None:
            return []
        window: list[ProfileNeighborMessage] = []
        for message in messages[:index]:
            role = str(getattr(message.role, "value", message.role))
            if role not in {"user", "assistant"} or not message.content.strip():
                continue
            window.append(
                ProfileNeighborMessage(
                    message_id=message.message_id,
                    role=role,
                    content=message.content,
                )
            )
        return window[-PROFILE_EXTRACTION_MAX_NEIGHBORS:]

    return read


def _profile_memory_reader(
    service: AtomicProfileService,
) -> Callable[[str, str], list[str]]:
    """读取与当前消息词面相关的既有原子条目，仅供画像抽取稳定表述。

    工单 04：相关旧记忆只作语义参考（有界、同账户、脱敏前用户原文），
    绝不作为事实证据；读取失败不阻断抽取。
    """

    def read(account_id: str, content: str) -> list[str]:
        probe = re.sub(r"\s+", "", content)
        if not probe:
            return []
        try:
            items = service.list_items(account_id)
        except Exception:  # noqa: BLE001 - 线索读取失败不阻断抽取
            return []
        matches: list[str] = []
        for item in items[:50]:
            text = item.text.strip()
            if not text or item.status != AtomicProfileItemStatus.ACTIVE:
                continue
            collapsed = re.sub(r"\s+", "", text)
            if any(
                collapsed[index : index + 2] in probe
                for index in range(max(len(collapsed) - 1, 0))
            ):
                matches.append(text)
            if len(matches) >= PROFILE_EXTRACTION_MAX_RELATED_MEMORIES:
                break
        return matches

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


#: 材料 OCR 运行锁的业务引用标签（审计里可区分「画像材料识别」调用）。
MATERIAL_OCR_BUSINESS_OBJECT = "profile_material_read"


def _now() -> datetime:
    return datetime.now(UTC)


class _DesignatedMaterialReader:
    """生产组合的指定材料读取器（工单 10）。

    材料来源=绑定在当前账户/会话消息上的聊天附件：当前消息优先，没有
    附件时按前文窗口回看「随后明确要求」的材料（有界条数）。草稿附件
    （未绑定消息）、其他会话与其他账户的材料绝不会返回；文件类读解析
    完成的分块（页码/章节/片段），图片类经固定 OCR 能力整图识别。
    读取一律经属主仓库：``chat_attachments`` → AttachmentRepository、
    ``document_chunks`` → RetrievalRepository、对象内容 → storage 域
    只读入口（``get_content``）。
    """

    def __init__(
        self,
        *,
        attachments: AttachmentRepository,
        conversations: ConversationRepository,
        retrieval: RetrievalRepository,
        objects: Any,
        gateway: Any,
        ocr_lock_sink: Callable[[str, Any], None] | None = None,
    ) -> None:
        self._attachments = attachments
        self._conversations = conversations
        self._retrieval = retrieval
        self._objects = objects
        self._gateway = gateway
        self._ocr_lock_sink = ocr_lock_sink

    def list_designated_materials(
        self,
        account_id: str,
        *,
        conversation_id: str,
        message_id: str,
        lookback: int,
        max_materials: int,
    ) -> list[DesignatedMaterial]:
        materials = self._materials_for_message(
            account_id, conversation_id, message_id, max_materials
        )
        if materials:
            return materials
        # 当前消息没有附件：按前文窗口回看最近一条带附件的用户消息，
        # 支持「先传简历，随后明确要求用它更新」的自然说法。
        try:
            messages = list(
                self._conversations.list_messages(account_id, conversation_id)
            )
        except Exception:  # noqa: BLE001 - 回看失败按没有材料处理
            return []
        index = next(
            (
                position
                for position, message in enumerate(messages)
                if message.message_id == message_id
            ),
            None,
        )
        if index is None:
            return []
        for message in reversed(messages[max(0, index - lookback) : index]):
            role = str(getattr(message.role, "value", message.role))
            if role != "user":
                continue
            materials = self._materials_for_message(
                account_id, conversation_id, message.message_id, max_materials
            )
            if materials:
                return materials
        return []

    def _materials_for_message(
        self,
        account_id: str,
        conversation_id: str,
        message_id: str,
        max_materials: int,
    ) -> list[DesignatedMaterial]:
        try:
            rows = self._attachments.rows_for_message(
                account_id, conversation_id, message_id
            )
        except Exception:  # noqa: BLE001 - 绑定读取失败按没有材料处理
            return []
        materials: list[DesignatedMaterial] = []
        for row in rows:
            media_type = str(row["media_type"] or "")
            if media_type not in CHAT_ATTACHMENT_MEDIA_TYPES:
                continue
            kind = (
                MaterialKind.PHOTO
                if media_type in PHOTO_MEDIA_TYPES
                else MaterialKind.FILE
            )
            raw_status = row["ingestion_raw_status"]
            materials.append(
                DesignatedMaterial(
                    object_id=str(row["object_id"]),
                    message_id=str(row["message_id"]),
                    conversation_id=str(row["conversation_id"]),
                    ordinal=int(row["ordinal"] or 0),
                    filename=str(row["original_filename"] or ""),
                    media_type=media_type,
                    kind=kind,
                    content_hash=(
                        str(row["content_hash"])
                        if row["content_hash"] is not None
                        else None
                    ),
                    readiness=material_readiness(
                        kind, str(raw_status) if raw_status is not None else None
                    ),
                )
            )
            if len(materials) >= max_materials:
                break
        return materials

    def read_material(
        self, account_id: str, material: DesignatedMaterial
    ) -> MaterialRead | None:
        if material.kind is MaterialKind.PHOTO:
            return self._read_photo(account_id, material)
        try:
            rows = self._retrieval.material_segments(
                account_id, object_id=material.object_id
            )
        except Exception:  # noqa: BLE001 - 分块读取失败不伪造内容
            return None
        segments = tuple(
            MaterialSegment(
                text=str(row["content"]),
                page_number=(
                    int(row["page_number"])
                    if row["page_number"] is not None
                    else None
                ),
                section_title=(
                    str(row["section_title"])
                    if row["section_title"] is not None
                    else None
                ),
                part_index=int(row["chunk_index"]),
            )
            for row in rows
        )
        if not segments:
            return None
        return MaterialRead(content_hash=material.content_hash, segments=segments)

    def _read_photo(
        self, account_id: str, material: DesignatedMaterial
    ) -> MaterialRead | None:
        if self._objects is None:
            return None
        try:
            content = self._objects.get_content(account_id, material.object_id)
        except Exception:  # noqa: BLE001 - 对象不可读不伪造内容
            return None
        context = RunContextEnvelope(
            run_id=material.object_id,
            account_id=account_id,
            project_id=material.conversation_id,
            workflow_name="profile-material-read",
            workflow_version="1",
            object_domain=ObjectDomain.PERSONAL_VAULT,
            submitted_at=_now(),
        )
        result = self._gateway.invoke(
            OCR_CAPABILITY_NAME,
            OCR_CAPABILITY_VERSION,
            context,
            payload={
                "image_base64": base64.b64encode(content).decode("ascii"),
                "mime_type": material.media_type,
                "prompt": OCR_IMAGE_PROMPT,
                "temperature": 0.01,
                "max_tokens": 4096,
            },
        )
        if result.lock is None:
            # 网关没有产生运行锁：与既有 OCR 端口同一失败关闭口径，绝不在
            # 没有审计证据的情况下把识别结果当作事实来源。
            raise AutomaticProfileError(
                MATERIAL_READ_FAILED_ERROR,
                "材料图片识别失败：OCR 服务调用未成功。",
                retryable=True,
            )
        if self._ocr_lock_sink is not None:
            # 落锁失败向上传播（失败关闭）：没有审计锁就不采用本次识别结果。
            self._ocr_lock_sink(material.object_id, result.lock)
        if result.status != ModelCallStatus.SUCCESS or result.output is None:
            raise AutomaticProfileError(
                result.error_code or MATERIAL_READ_FAILED_ERROR,
                result.error_message
                or "材料图片识别失败。",
                retryable=result.status == ModelCallStatus.RETRYABLE_FAIL,
            )
        text = str(result.output.get("content", ""))
        if not text.strip():
            return None
        return MaterialRead(
            content_hash=material.content_hash,
            segments=(MaterialSegment(text=text, part_index=0),),
        )


def _designated_material_reader(
    *,
    attachments: AttachmentRepository,
    conversations: ConversationRepository,
    retrieval: RetrievalRepository,
    objects: Any,
    gateway: Any,
    ocr_lock_sink: Callable[[str, Any], None] | None = None,
) -> _DesignatedMaterialReader:
    """组合根构造指定材料读取器；未接线时调用方传 ``None``（按无材料处理）。"""

    return _DesignatedMaterialReader(
        attachments=attachments,
        conversations=conversations,
        retrieval=retrieval,
        objects=objects,
        gateway=gateway,
        ocr_lock_sink=ocr_lock_sink,
    )


def _material_source_checker(
    attachments: AttachmentRepository,
    objects: Any | None,
) -> Callable[[str, AtomicProfileMaterialSource], AtomicProfileMaterialSourceStatus]:
    """材料据再核验：绑定仍在、对象活跃、内容版本一致才算可得（工单 10）。

    材料编辑（对象被替换为新版本）或删除、消息撤回（绑定行消失）都使旧
    引用失效；核验本身出错时按「无法核验」如实返回，绝不默认材料仍存在。
    """

    def check(
        account_id: str, source: AtomicProfileMaterialSource
    ) -> AtomicProfileMaterialSourceStatus:
        try:
            rows = attachments.rows_for_message(
                account_id, source.conversation_id, source.message_id
            )
            row = next(
                (
                    entry
                    for entry in rows
                    if str(entry["object_id"]) == source.object_id
                ),
                None,
            )
            if row is None:
                return AtomicProfileMaterialSourceStatus.DELETED
            row_hash = row["content_hash"]
            if (
                source.content_hash
                and row_hash is not None
                and str(row_hash) != source.content_hash
            ):
                return AtomicProfileMaterialSourceStatus.DELETED
            if objects is not None:
                status = objects.object_status(account_id, source.object_id)
                if status != "active":
                    return AtomicProfileMaterialSourceStatus.DELETED
            return AtomicProfileMaterialSourceStatus.AVAILABLE
        except Exception:  # noqa: BLE001 - 核验失败按无法核验处理
            return AtomicProfileMaterialSourceStatus.UNREADABLE

    return check
