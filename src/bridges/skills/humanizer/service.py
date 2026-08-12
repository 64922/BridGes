"""bridges-humanizer 编排服务（Issue 28）。

智能体编排与 SKILL 规则共同工作：改写路径先提取任务契约与事实锁，生成
路径先收集/确认主题、受众、体裁、渠道与硬约束；随后按 SKILL.md、体裁
合同与事实锁组装结构化指令调用固定模型；产出后执行确定性复核（事实锁
前后比较/硬约束包含、体裁规则、输出合同完整性、引用保持），阻断冲突
停止交付，需人工事项明确标注。可引用来源一律经本地/联网证据合同呈现，
不虚构事实、论文或引用。
"""

from __future__ import annotations

import contextlib
import re
import secrets
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from bridges.ai.model_gateway import ModelGateway
from bridges.chat.attachments import ChatAttachmentError, ChatAttachmentService
from bridges.chat.budget import RESULT_FAILED, RunBudget, RunStage
from bridges.contracts.ai import ModelCallStatus
from bridges.contracts.humanizer import (
    FactLockCheckResult,
    FactLockKind,
    FactLockSeverity,
    FactLockStatus,
    FidelityCheckResult,
    HumanizerEdit,
    HumanizerEditKind,
    HumanizerFactCheckItem,
    HumanizerOutputContract,
    HumanizerPath,
    HumanizerProcessState,
    HumanizerQualityStatus,
    HumanizerReference,
    HumanizerResultProjection,
    HumanizerResultStatus,
    HumanizerSkillInput,
    HumanizerTaskContract,
    SourceLedger,
)
from bridges.contracts.observability import AuditAction, AuditResult
from bridges.ingestion.parsers import ParsedDocument, ParseError, parse_document
from bridges.knowledge_base.service import KnowledgeBaseError, KnowledgeBaseService
from bridges.observability.service import ObservabilityService
from bridges.retrieval.service import LayeredRetrievalService
from bridges.skills.humanizer.factlock import _KIND_LABEL_CN as _KIND_CN
from bridges.skills.humanizer.factlock import (
    check_requirements,
    compare_locks,
    extract_locks,
)
from bridges.skills.humanizer.genre_rules import (
    GenreCheckResult,
    check_genre,
    genre_rule_set,
)
from bridges.skills.humanizer.intent import HUMANIZER_ROUTE_VERSION
from bridges.skills.humanizer.method_rules import MethodScene, render_method_rules
from bridges.skills.humanizer.source_ledger import (
    FidelityCheckError,
    compile_source_ledger,
    run_fidelity_check,
)
from bridges.skills.registry import SkillRegistry
from bridges.web_search.service import WebSearchService

HUMANIZER_CAPABILITY_NAME = "qwen_structured_output"
HUMANIZER_CAPABILITY_VERSION = "1"

#: Issue 02 能力开关：关闭时新任务恢复旧流程（无来源账本与保真检查），
#: 投影不携带保真字段，不得把旧流程标记为新硬门通过（灰度与回滚用）。
FIDELITY_GATE_ENABLED = True

# 结构化输出 JSON Schema（与 HumanizerOutputContract 字段一一对应）
_OUTPUT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "final_text": {"type": "string"},
        "edits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "original": {"type": "string"},
                    "revised": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "enum": [
                            "rewrite",
                            "restructure",
                            "word_choice",
                            "audience_adapt",
                            "no_change",
                        ],
                    },
                    "reason": {"type": "string"},
                },
                "required": ["original", "revised", "kind", "reason"],
            },
        },
        "fact_check": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "item": {"type": "string"},
                    "result": {"type": "string"},
                    "evidence": {"type": "string"},
                },
                "required": ["item", "result", "evidence"],
            },
        },
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["final_text", "edits", "fact_check", "open_questions"],
}


class HumanizerError(Exception):
    """人味化编排错误：错误码 + 中文说明 + 是否可重试。"""

    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class HumanizerRunKind(StrEnum):
    """编排事件类别。"""

    PROCESS = "process"
    #: 草稿事件：模型产出正文后立即下发（复核/修复前），聊天服务收到
    #: 即持久化消息正文——刷新/切会话后可见草稿，软检查不删除草稿。
    DRAFT = "draft"
    RESULT = "result"


@dataclass
class HumanizerRunEvent:
    """编排向聊天服务产出的事件（过程事件、草稿或最终结果）。"""

    kind: HumanizerRunKind
    state: HumanizerProcessState | None = None
    step_label: str = ""
    detail: str | None = None
    retryable: bool = False
    progress_steps: list[str] = field(default_factory=list)
    #: 草稿事件携带的正文（终态前的最新可交付文本）。
    draft_text: str | None = None
    result: HumanizerResultProjection | None = None


@dataclass
class _ReviewCheckpoint:
    """一次确定性复核的结果（终态判定前的不变中间态）。"""

    output: HumanizerOutputContract
    fact_lock_check: FactLockCheckResult
    genre_check: GenreCheckResult
    references: list[HumanizerReference]
    ledger: SourceLedger | None = None
    fidelity_check: FidelityCheckResult | None = None


class HumanizerService:
    """bridges-humanizer 的两条路径编排。"""

    def __init__(
        self,
        registry: SkillRegistry,
        gateway: ModelGateway,
        attachment_service: ChatAttachmentService | None = None,
        knowledge_base_service: KnowledgeBaseService | None = None,
        retrieval_service: LayeredRetrievalService | None = None,
        web_search_service: WebSearchService | None = None,
        observability_service: ObservabilityService | None = None,
    ) -> None:
        self._registry = registry
        self._gateway = gateway
        self._attachments = attachment_service
        self._knowledge_base = knowledge_base_service
        self._retrieval = retrieval_service
        self._web_search = web_search_service
        self._observability = observability_service

    # ------------------------------------------------------------------
    # 对外入口
    # ------------------------------------------------------------------

    def resolve_skill(self, skill_input: HumanizerSkillInput) -> str:
        """校验 SKILL 载荷：标识必须已注册为内置只读能力，版本固定。"""
        if (
            skill_input.route is not None
            and skill_input.route.source.value == "natural_language"
            and skill_input.route.version != HUMANIZER_ROUTE_VERSION
        ):
            raise HumanizerError(
                "humanizer_route_version_conflict",
                "自然语言人味化路由版本不受支持，请重新发送任务。",
                retryable=False,
            )
        manifest = self._registry.get(skill_input.skill_id)
        if skill_input.version is not None and skill_input.version != manifest.version:
            raise HumanizerError(
                "skill_version_conflict",
                f"SKILL {skill_input.skill_id} 版本固定为 {manifest.version}，"
                f"请求版本 {skill_input.version} 不受支持。",
                retryable=False,
            )
        return manifest.version

    def run_task(
        self,
        account_id: str,
        conversation_id: str,
        assistant_message_id: str,
        skill_input: HumanizerSkillInput,
        run_context: Any,
        retrieval_round: Any | None = None,
        web_search_projection: Any | None = None,
        arxiv_search_projection: Any | None = None,
        budget: RunBudget | None = None,
    ) -> Iterator[HumanizerRunEvent]:
        """执行一条人味化任务：yield 过程事件，最后 yield 结果事件。

        Issue 07：模型产出正文后立即 yield 草稿事件（聊天服务持久化
        消息正文，软检查只更新状态不删草稿）；自然语言路由只做一次
        结构化生成并执行确定性复核，仍未完全通过时交付当前最佳正文与
        具体警告；旧显式 SKILL 继续保留既有的一次定向修复兼容行为。
        """
        skill_version = self.resolve_skill(skill_input)
        contract = skill_input.contract
        progress: list[str] = []

        def process(
            state: HumanizerProcessState,
            label: str,
            *,
            detail: str | None = None,
            retryable: bool = False,
        ) -> HumanizerRunEvent:
            return HumanizerRunEvent(
                kind=HumanizerRunKind.PROCESS,
                state=state,
                step_label=label,
                detail=detail,
                retryable=retryable,
                progress_steps=list(progress),
            )

        try:
            # 步骤 1：解析任务契约与来源（改写路径）
            yield process(HumanizerProcessState.LOADING, "正在解析任务契约…")
            (
                source_text,
                source_label,
                references,
                source_knowledge_base_object_ids,
                account_materials,
            ) = self._resolve_source(
                account_id, conversation_id, contract, retrieval_round,
                web_search_projection, arxiv_search_projection,
            )
            progress.append("解析任务契约")

            # 步骤 2：提取事实锁 / 编译硬约束
            yield process(HumanizerProcessState.LOADING, "正在提取事实锁…")
            if contract.path == HumanizerPath.REWRITE:
                if not source_text.strip():
                    raise HumanizerError(
                        "empty_source",
                        "没有可改写的原文：请粘贴文本或选择当前账户知识库材料。",
                        retryable=True,
                    )
                locks = extract_locks(source_text)
                fact_lock_source = source_text
            else:
                constraints_text = self._constraints_text(contract)
                locks = extract_locks(constraints_text)
                fact_lock_source = constraints_text
            ledger = self._compile_ledger(
                contract,
                fact_lock_source,
                source_label,
                references,
                account_materials=account_materials,
                external_evidence_requested=bool(
                    skill_input.route is not None
                    and skill_input.route.external_evidence_requested
                ),
            )
            progress.append(
                "提取事实锁" if contract.path == HumanizerPath.REWRITE else "编译硬约束"
            )

            # 步骤 3：按 SKILL 规则组装指令并调用固定模型
            yield process(HumanizerProcessState.LOADING, "正在按体裁规则生成…")
            result = self._invoke_model(
                account_id,
                conversation_id,
                assistant_message_id,
                skill_input,
                skill_version,
                source_text,
                source_label,
                locks,
                references,
                run_context,
            )
            progress.append("按体裁规则生成")

            # 步骤 3.5：模型产出正文后立即下发草稿事件——聊天服务据此
            # 持久化消息正文；随后的软检查/修复/复核不删除草稿（Issue 07）。
            output = self._coerce_output(result.output or {})
            output = output.model_copy(
                update={
                    "source_attachment_ids": [],
                    "source_knowledge_base_object_ids": list(
                        source_knowledge_base_object_ids
                    ),
                }
            )
            yield HumanizerRunEvent(
                kind=HumanizerRunKind.DRAFT,
                draft_text=output.final_text.strip(),
                progress_steps=list(progress),
            )

            # 步骤 4：确定性复核——硬门（事实锁/来源账本保真冲突）停止交付；
            # 软门（体裁等风格指标）至多一次有预算的定向修复，仍不过则交付
            # 当前最佳正文与具体警告（Issue 07 两级质量门 + Issue 02 保真硬门）。
            yield process(HumanizerProcessState.LOADING, "正在复核事实锁与体裁规则…")
            checkpoint = self._review_checks(
                contract, output, source_text, fact_lock_source, references,
                ledger=ledger,
            )
            repair_attempts = 0
            if (
                not self._is_natural_language_route(skill_input)
                and checkpoint.genre_check
                and not checkpoint.genre_check.passed
                and not checkpoint.fact_lock_check.blocking_conflicts
            ):
                repair_attempts, checkpoint = self._repair_once(
                    account_id,
                    conversation_id,
                    assistant_message_id,
                    skill_input,
                    skill_version,
                    source_text,
                    source_label,
                    locks,
                    run_context,
                    checkpoint,
                    budget,
                    fact_lock_source,
                )
            final_result = self._finalize_result(
                account_id,
                conversation_id,
                assistant_message_id,
                skill_input,
                skill_version,
                checkpoint,
                repair_attempts,
            )
            progress.append("确定性复核")
            final_result.process_steps = progress
            yield HumanizerRunEvent(
                kind=HumanizerRunKind.RESULT,
                step_label="人味化完成",
                result=final_result,
            )
        except HumanizerError as exc:
            if exc.code in ("empty_source", "empty_topic"):
                state = HumanizerProcessState.EMPTY
            elif exc.code in _PERMISSION_ERROR_CODES:
                # 密钥/能力/区域类失败 → permission 态（过程卡五态之一）
                state = HumanizerProcessState.PERMISSION
            elif exc.retryable:
                state = HumanizerProcessState.RECOVERY
            else:
                state = HumanizerProcessState.ERROR
            failed = self._failed_projection(
                account_id,
                conversation_id,
                assistant_message_id,
                skill_input,
                skill_version,
                exc.code,
                exc.message,
                retryable=exc.retryable,
                process_steps=progress,
                state=state,
            )
            yield process(
                state,
                "任务未完成",
                detail=exc.message,
                retryable=exc.retryable,
            )
            yield HumanizerRunEvent(kind=HumanizerRunKind.RESULT, result=failed)

    @staticmethod
    def _is_natural_language_route(skill_input: HumanizerSkillInput) -> bool:
        route = skill_input.route
        return route is not None and route.source.value == "natural_language"

    # ------------------------------------------------------------------
    # 来源解析
    # ------------------------------------------------------------------

    def _resolve_source(
        self,
        account_id: str,
        conversation_id: str,
        contract: HumanizerTaskContract,
        retrieval_round: Any | None,
        web_search_projection: Any | None,
        arxiv_search_projection: Any | None,
    ) -> tuple[str, str, list[HumanizerReference], list[str], list[tuple[str, str]]]:
        """改写路径解析原文（粘贴或知识库材料），并组装证据合同引用清单。

        Issue 11：改写原文只来自用户粘贴/知识库材料，默认不引用知识库检索
        候选（知识库中不相关图片等材料绝不进入改写证据合同）；解析成功
        的知识库材料 ID 随输出持久化，与账户授权一致，供界面核对原文文件名。
        Issue 02：知识库材料作为「账户作用域授权材料」返回（标题, 正文），
        供来源账本区分来源类型；粘贴文本仍是唯一主来源。
        """
        references: list[HumanizerReference] = []
        account_materials: list[tuple[str, str]] = []
        if contract.path == HumanizerPath.GENERATE:
            topic = (contract.topic or "").strip()
            if not topic:
                raise HumanizerError(
                    "empty_topic", "缺少主题：请填写要生成的文章主题。", retryable=True
                )
            return topic, "主题", references, [], account_materials

        source_parts: list[str] = []
        label_parts: list[str] = []
        source_knowledge_base_object_ids: list[str] = []
        if contract.source_text and contract.source_text.strip():
            source_parts.append(contract.source_text.strip())
            label_parts.append("粘贴文本")
        # Issue 11：能力输入只允许当前账户已授权且完成解析的全局知识库材料。
        for object_id in contract.knowledge_base_object_ids:
            parsed = self._parse_knowledge_base_material(account_id, object_id)
            source_parts.append(parsed.text)
            label_parts.append(parsed.title)
            source_knowledge_base_object_ids.append(object_id)
            account_materials.append((parsed.title, parsed.text))
            references.append(
                HumanizerReference(
                    reference_id=f"ref-{secrets.token_urlsafe(8)}",
                    label=parsed.title,
                    source_type="knowledge_base",
                    detail=f"知识库材料：{parsed.title}",
                    preserved=True,
                )
            )
        retrieval_citations = list(getattr(retrieval_round, "citations", []) or [])
        if contract.knowledge_base_reference:
            expected = contract.knowledge_base_reference.strip().casefold()
            matching = [
                citation
                for citation in retrieval_citations
                if str(getattr(citation, "filename", "")).strip().casefold() == expected
            ]
            if not matching:
                raise HumanizerError(
                    "knowledge_base_reference_unreadable",
                    f"知识库文档「{contract.knowledge_base_reference}」不可读或没有访问权限，"
                    "请确认文档已上传到当前账户知识库后重试。",
                    retryable=True,
                )
            if len(matching) > 1:
                raise HumanizerError(
                    "knowledge_base_reference_ambiguous",
                    f"知识库文档「{contract.knowledge_base_reference}」存在多个同名材料，"
                    "请改用唯一文件名后重试。",
                    retryable=True,
                )
            for citation in matching:
                snippet = str(getattr(citation, "snippet", "")).strip()
                if snippet:
                    source_parts.append(snippet)
                    label_parts.append(contract.knowledge_base_reference)
        if not source_parts:
            raise HumanizerError(
                "empty_source",
                "没有可改写的原文：请粘贴文本或选择当前账户知识库材料。",
                retryable=True,
            )
        # 证据合同：改写默认不引用知识库检索候选（原文即用户材料）；
        # 仅在显式开启补充检索（retrieval_round 非空）时，检索轮次引用
        # 才进入改写证据合同——知识库材料仅作补充，绝不替代原文。
        # 联网/arXiv 仅在明确触发时进入引用清单（模型只可引用清单内材料）。
        retrieval_references = self._citations_from(retrieval_round)
        if contract.knowledge_base_reference:
            expected = contract.knowledge_base_reference.strip().casefold()
            retrieval_references = [
                reference
                for reference in retrieval_references
                if (reference.label or "").strip().casefold() == expected
            ]
        for citation in retrieval_references:
            references.append(citation)
        for item in self._citations_from_web(web_search_projection):
            references.append(item)
        for item in self._citations_from_arxiv(arxiv_search_projection):
            references.append(item)
        return (
            "\n\n".join(source_parts),
            "、".join(label_parts) or "原文",
            references,
            source_knowledge_base_object_ids,
            account_materials,
        )

    def _parse_knowledge_base_material(
        self, account_id: str, object_id: str
    ) -> ParsedDocument:
        """读取当前账户已授权的知识库材料并解析为能力输入。"""
        if self._knowledge_base is None:
            raise HumanizerError(
                "knowledge_base_unavailable",
                "全局知识库服务未启用，请稍后重试。",
                retryable=True,
            )
        try:
            record, content = self._knowledge_base.download_for_capability(
                account_id, object_id
            )
        except KnowledgeBaseError as exc:
            raise HumanizerError(
                "knowledge_base_material_unreadable",
                f"知识库材料（{object_id}）读取失败：{exc.message}",
                retryable=exc.status_code in {409, 429, 500, 503},
            ) from exc
        try:
            return parse_document(
                content,
                record.original_filename,
                record.media_type or "application/octet-stream",
            )
        except ParseError as exc:
            raise HumanizerError(
                "knowledge_base_material_parse_failed",
                f"无法解析知识库材料“{record.original_filename}”，请重试摄取或更换材料。",
                retryable=True,
            ) from exc

    def _parse_attachment(
        self, account_id: str, conversation_id: str, attachment_id: str
    ) -> ParsedDocument:
        """读取当前账户附件并解析为文本（Issue 04 失败指名文件，不静默跳过）。"""
        if self._attachments is None:
            raise HumanizerError(
                "attachments_unavailable",
                "附件服务未启用，请稍后重试。",
                retryable=True,
            )
        try:
            record, content = self._attachments.download(
                account_id, conversation_id, attachment_id
            )
        except ChatAttachmentError as exc:
            raise HumanizerError(
                "attachment_unreadable",
                f"附件（{attachment_id}）读取失败，请重新选择文件后重试。",
                retryable=True,
            ) from exc
        try:
            return parse_document(
                content,
                record.original_filename,
                record.media_type or "application/octet-stream",
            )
        except ParseError as exc:
            raise HumanizerError(
                "attachment_parse_failed",
                f"无法解析文件「{record.original_filename}」：仅支持 PDF、"
                "DOCX、TXT、Markdown 与常见图片。请重试或更换文件。",
                retryable=True,
            ) from exc

    def _citations_from(self, retrieval_round: Any | None) -> list[HumanizerReference]:
        """知识库检索轮次的引用条目（改写路径仅在显式开启补充检索时消费）。

        Issue 07：默认路径不检索、不展示、不引用知识库材料（改写原文只
        来自用户材料）；用户显式开启「补充检索全局知识库」时检索轮次
        才作为证据合同引用进入改写——知识库材料仅作补充，绝不替代原文。
        """
        if retrieval_round is None:
            return []
        refs: list[HumanizerReference] = []
        for citation in getattr(retrieval_round, "citations", []) or []:
            locator = ""
            if getattr(citation, "page_number", None) is not None:
                locator += f"第 {citation.page_number} 页"
            if getattr(citation, "section_title", None):
                locator += f"（{citation.section_title}）"
            refs.append(
                HumanizerReference(
                    reference_id=f"ref-{secrets.token_urlsafe(8)}",
                    label=getattr(citation, "filename", "") or "本地材料",
                    source_type="retrieval",
                    detail=f"{locator}｜快照：{getattr(citation, 'snippet', '')[:60]}",
                    citation_surface=None,
                    preserved=True,
                )
            )
        return refs

    def _citations_from_web(self, projection: Any | None) -> list[HumanizerReference]:
        if projection is None:
            return []
        refs: list[HumanizerReference] = []
        for result in getattr(projection, "results", []) or []:
            refs.append(
                HumanizerReference(
                    reference_id=f"ref-{secrets.token_urlsafe(8)}",
                    label=getattr(result, "title", "") or "联网来源",
                    source_type="web",
                    detail=(
                        f"{getattr(result, 'url', '')}（访问于 "
                        f"{getattr(result, 'accessed_at', '')}）"
                    ),
                    citation_surface=getattr(result, "url", None),
                    preserved=True,
                )
            )
        return refs

    def _citations_from_arxiv(self, projection: Any | None) -> list[HumanizerReference]:
        if projection is None:
            return []
        refs: list[HumanizerReference] = []
        for paper in getattr(projection, "papers", []) or []:
            refs.append(
                HumanizerReference(
                    reference_id=f"ref-{secrets.token_urlsafe(8)}",
                    label=getattr(paper, "title", "") or "arXiv 论文",
                    source_type="arxiv",
                    detail=(
                        f"{getattr(paper, 'arxiv_id', '')}｜摘要页："
                        f"{getattr(paper, 'abs_url', '')}"
                    ),
                    citation_surface=getattr(paper, "arxiv_id", None),
                    preserved=True,
                )
            )
        return refs

    # ------------------------------------------------------------------
    # 模型调用
    # ------------------------------------------------------------------

    def _invoke_model(
        self,
        account_id: str,
        conversation_id: str,
        assistant_message_id: str,
        skill_input: HumanizerSkillInput,
        skill_version: str,
        source_text: str,
        source_label: str,
        locks: list[Any],
        references: list[HumanizerReference],
        run_context: Any,
        repair_instructions: list[str] | None = None,
    ) -> Any:
        contract = skill_input.contract
        genre_set = genre_rule_set(contract.genre)
        system_prompt = self._build_system_prompt(
            skill_version, contract, genre_set, locks, source_label, references
        )
        if repair_instructions:
            # 软门定向修复（Issue 07）：只针对未满足的风格规则修正正文，
            # 不改变任务边界、事实锁与证据合同，不泄漏内部复核细节。
            system_prompt += (
                "\n\n【本轮定向修正】上一版正文未完全满足以下体裁规则"
                "（事实锁与引用关系必须保持）：\n"
                + "\n".join(f"- {rule}" for rule in repair_instructions)
                + "\n请只修正正文中对应部分后，重新输出完整 JSON。"
            )
        user_prompt = self._build_user_prompt(contract, source_text)
        payload = {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "json_schema": _OUTPUT_JSON_SCHEMA,
            "temperature": 0.4,
            "max_tokens": 4096,
        }
        call_result = self._gateway.invoke(
            HUMANIZER_CAPABILITY_NAME,
            HUMANIZER_CAPABILITY_VERSION,
            run_context,
            payload,
        )
        if call_result.status not in (ModelCallStatus.SUCCESS, ModelCallStatus.DEGRADED):
            raise HumanizerError(
                call_result.error_code or "humanizer_generation_failed",
                call_result.error_message or "人味化生成失败，请重试。",
                retryable=call_result.status == ModelCallStatus.RETRYABLE_FAIL,
            )
        return call_result

    def _build_system_prompt(
        self,
        skill_version: str,
        contract: HumanizerTaskContract,
        genre_set: Any,
        locks: list[Any],
        source_label: str,
        references: list[HumanizerReference],
    ) -> str:
        """SKILL 规则 + 体裁合同 + 事实锁 + 证据合同的组装（非单一提示词）。"""
        lock_lines = "\n".join(
            f"- {lock.surface}（{_KIND_CN.get(lock.kind.value, lock.kind.value)}）"
            for lock in locks
        ) or "（无）"
        reference_lines = "\n".join(
            f"- {ref.label}｜{ref.source_type}｜{ref.detail}"
            for ref in references
        ) or "（无：只能依赖原文，新增引用一律标记为需人工核实）"
        genre_doc = genre_rule_set(contract.genre)
        required_lines = "；".join(rule.label for rule in genre_doc.required)
        prohibited_lines = "；".join(rule.label for rule in genre_doc.prohibited)
        method_scene = (
            MethodScene.ARTICLE_REWRITE
            if contract.path == HumanizerPath.REWRITE
            else MethodScene.ARTICLE_GENERATE
        )
        method_block = render_method_rules(
            method_scene,
            genre_name=genre_doc.display_name,
        )
        return f"""你是 BridGes 内置「文章人味化」SKILL（版本 {skill_version}）的执行器。

【任务边界】
- 目标：在保持科学判断、证据强度、限定条件和引用关系的前提下，改进表达的自然度、任务适配度与可读性。
- 禁止：规避 AI 检测、冒充真人、伪造个人经历、欺骗性代写；不虚构事实、论文或引用；不以牺牲科学事实换取口语化。
- 体裁：{genre_doc.display_name}。必含：{required_lines}。禁止：{prohibited_lines}。
- 体裁责任：{genre_doc.human_responsibility}

【方法体系（必须执行）】
{method_block}

【事实锁（不得改变，冲突时宁可保留原文）】
{lock_lines}

【证据合同（可引用来源清单；只可引用清单内材料，不得虚构）】
{reference_lines}

【输出要求】严格输出 JSON，不得输出 JSON 之外的任何内容：
{{"final_text": 最终文本, "edits": [{{"original": 原文片段, "revised": 新文片段,
"kind": "rewrite|restructure|word_choice|audience_adapt|no_change",
"reason": 理由（对应哪条体裁或方法规则）}}], "fact_check": [{{"item": 核查对象,
"result": "已核实|需人工确认|存在虚构风险", "evidence": 依据}}],
"open_questions": [尚未解决的问题]}}"""  # noqa: E501

    def _build_user_prompt(
        self, contract: HumanizerTaskContract, source_text: str
    ) -> str:
        if contract.path == HumanizerPath.REWRITE:
            task_line = "改写以下原文，使其更自然、更贴合受众与渠道"
        else:
            task_line = "按以下主题生成新文章"
        lines = [
            f"任务：{task_line}。",
            f"受众：{contract.audience or '未指定（默认面向一般读者）'}。",
            f"渠道：{contract.channel or '未指定'}。",
            f"长度：{contract.length_target or '未指定'}。",
        ]
        if contract.hard_constraints:
            lines.append("硬约束：" + "；".join(contract.hard_constraints) + "。")
        if contract.path == HumanizerPath.GENERATE:
            lines.append("主题：")
        lines.append(source_text)
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 确定性复核
    # ------------------------------------------------------------------

    def _coerce_output(self, raw: dict[str, Any]) -> HumanizerOutputContract:
        """把模型输出收窄为输出合同；非 JSON 结构时给出需人工核实的诚实结果。"""
        try:
            final_text = str(raw.get("final_text") or "").strip()
        except (TypeError, ValueError):
            final_text = ""
        edits_value = raw.get("edits")
        edits_raw = edits_value if isinstance(edits_value, list) else []
        edits: list[HumanizerEdit] = []
        for item in edits_raw:
            if not isinstance(item, dict):
                continue
            try:
                kind = HumanizerEditKind(
                    str(item.get("kind") or HumanizerEditKind.REWRITE.value)
                )
            except ValueError:
                kind = HumanizerEditKind.REWRITE
            edits.append(
                HumanizerEdit(
                    edit_id=f"ed-{secrets.token_urlsafe(8)}",
                    kind=kind,
                    original=str(item.get("original") or ""),
                    revised=str(item.get("revised") or ""),
                    reason=str(item.get("reason") or ""),
                    genre_rule=str(item.get("genre_rule")) if item.get("genre_rule") else None,
                )
            )
        fact_check_value = raw.get("fact_check")
        fact_check_raw = fact_check_value if isinstance(fact_check_value, list) else []
        fact_check: list[HumanizerFactCheckItem] = []
        for item in fact_check_raw:
            if not isinstance(item, dict):
                continue
            fact_check.append(
                HumanizerFactCheckItem(
                    item=str(item.get("item") or "未命名核查项"),
                    result=str(item.get("result") or "需人工确认"),
                    evidence=str(item.get("evidence") or "无依据"),
                )
            )
        open_questions_value = raw.get("open_questions")
        open_questions_raw = (
            open_questions_value if isinstance(open_questions_value, list) else []
        )
        open_questions = [str(q) for q in open_questions_raw if str(q).strip()]
        return HumanizerOutputContract(
            final_text=final_text,
            edits=edits,
            fact_check=fact_check,
            open_questions=open_questions,
        )

    def _review_checks(
        self,
        contract: HumanizerTaskContract,
        output: HumanizerOutputContract,
        source_text: str,
        fact_lock_source: str,
        references: list[HumanizerReference],
        *,
        ledger: SourceLedger | None = None,
    ) -> _ReviewCheckpoint:
        """确定性复核（不含终态判定）：事实锁/硬约束、保真硬门、体裁、引用与
        合同完整性。

        Issue 07：软门（体裁等风格指标）只生成复核结果，不在此清空正文；
        硬门（事实锁冲突、来源账本保真冲突）由调用方在终态判定中停止交付。
        Issue 02：保真检查失败关闭——账本版本不受支持、哈希不一致或检查
        异常时抛错，不把缺失检查的结果标记为通过。
        """
        final_text = output.final_text.strip()
        if not final_text:
            raise HumanizerError(
                "empty_output",
                "生成结果缺少最终文本，请重试。",
                retryable=True,
            )

        # 事实锁比较（改写）或硬约束包含（生成）
        if contract.path == HumanizerPath.REWRITE:
            fact_lock_check = compare_locks(
                fact_lock_source, final_text, source_label="原文 vs 改写结果"
            )
        else:
            fact_lock_check = check_requirements(
                contract.hard_constraints,
                final_text,
                source_label="硬约束 vs 生成结果",
            )

        # 来源账本保真硬门（Issue 02）：保留检查 + 新增 claim 来源检查
        fidelity_check: FidelityCheckResult | None = None
        if ledger is not None:
            try:
                fidelity_check = run_fidelity_check(
                    ledger,
                    final_text,
                    contract_path=contract.path,
                    allow_assumptions=contract.allow_assumptions,
                )
            except FidelityCheckError as exc:
                raise HumanizerError(
                    "fidelity_check_failed",
                    f"来源保真检查未完成：{exc}",
                    retryable=False,
                ) from exc

        # 体裁规则复核（软门）
        genre_check = check_genre(final_text, contract.genre)

        # 引用保持检查：原文引用必须仍出现；新引用必须来自证据合同
        references = self._check_citation_preservation(
            contract, source_text, final_text, references
        )

        # 组装事实核查（确定性为准，模型条目附后）
        fact_check = self._deterministic_fact_check(
            fact_lock_check, genre_check, references, contract
        )
        for item in output.fact_check:
            fact_check.append(item)
        output = output.model_copy(update={"fact_check": fact_check})

        # 输出合同完整性门：改写后原文未变且无修改项时补一条诚实的不改说明
        if (
            contract.path == HumanizerPath.REWRITE
            and final_text == source_text.strip()
            and not output.edits
        ):
            output = output.model_copy(
                update={
                    "edits": [
                        HumanizerEdit(
                            edit_id=f"ed-{secrets.token_urlsafe(8)}",
                            kind=HumanizerEditKind.NO_CHANGE,
                            original=source_text.strip()[:60],
                            revised=final_text[:60],
                            reason="原文已符合体裁规则与表达目标，无需修改。",
                        )
                    ]
                }
            )
        gaps = output.completeness_gaps()
        if gaps:
            raise HumanizerError(
                "output_contract_incomplete",
                "输出合同不完整：" + "；".join(gaps) + "。",
                retryable=True,
            )
        return _ReviewCheckpoint(
            output=output,
            fact_lock_check=fact_lock_check,
            genre_check=genre_check,
            references=references,
            ledger=ledger,
            fidelity_check=fidelity_check,
        )

    def _repair_once(
        self,
        account_id: str,
        conversation_id: str,
        assistant_message_id: str,
        skill_input: HumanizerSkillInput,
        skill_version: str,
        source_text: str,
        source_label: str,
        locks: list[Any],
        run_context: Any,
        checkpoint: _ReviewCheckpoint,
        budget: RunBudget | None,
        fact_lock_source: str,
    ) -> tuple[int, _ReviewCheckpoint]:
        """软门定向修复：至多一次、受总预算约束；失败则交付原草稿。

        只针对未满足的体裁规则做一次有预算的修复调用；预算不足、修复
        调用失败或修复稿复核失败时返回 ``(0 或 1, 原 checkpoint)``——
        正文与警告照常交付，绝不循环重生成（Issue 07）。
        """
        failed_rules = [
            f.label for f in checkpoint.genre_check.findings if not f.passed
        ]
        if not failed_rules:
            return 0, checkpoint
        if budget is None or not budget.can_retry():
            return 0, checkpoint
        entered = budget.enter(RunStage.REPAIR)
        try:
            repair_result = self._invoke_model(
                account_id,
                conversation_id,
                assistant_message_id,
                skill_input,
                skill_version,
                source_text,
                source_label,
                locks,
                checkpoint.references,
                run_context,
                repair_instructions=failed_rules,
            )
            if entered:
                budget.exit(
                    RunStage.REPAIR,
                    category="qwen_structured_output",
                    count=1,
                )
        except HumanizerError:
            if entered:
                budget.exit(RunStage.REPAIR, result=RESULT_FAILED, count=1)
            return 1, checkpoint
        try:
            repaired = self._coerce_output(repair_result.output or {})
            if not repaired.final_text.strip():
                return 1, checkpoint
            repaired = repaired.model_copy(
                update={
                    "source_attachment_ids": list(checkpoint.output.source_attachment_ids),
                    "source_knowledge_base_object_ids": list(
                        checkpoint.output.source_knowledge_base_object_ids
                    ),
                }
            )
            return 1, self._review_checks(
                skill_input.contract,
                repaired,
                source_text,
                fact_lock_source,
                checkpoint.references,
                ledger=checkpoint.ledger,
            )
        except (HumanizerError, TypeError, ValueError):
            # 修复稿不可解析/合同不完整/复核失败：交付原草稿与具体警告
            return 1, checkpoint

    def _finalize_result(
        self,
        account_id: str,
        conversation_id: str,
        assistant_message_id: str,
        skill_input: HumanizerSkillInput,
        skill_version: str,
        checkpoint: _ReviewCheckpoint,
        repair_attempts: int,
    ) -> HumanizerResultProjection:
        """终态判定与结果投影：硬门停止交付；软门交付正文与具体警告。

        Issue 07：任何成功/软失败结果都包含正文；只有硬门（事实锁冲突
        等）或无正文的模型错误可以没有 final text。硬门投影附冲突项与
        可操作的恢复方式，不泄漏内部 prompt。
        """
        contract = skill_input.contract
        fact_lock_check = checkpoint.fact_lock_check
        genre_check = checkpoint.genre_check
        genre_summary = genre_check.summary()
        output = checkpoint.output
        references = checkpoint.references
        final_text = output.final_text.strip()
        fidelity_check = checkpoint.fidelity_check
        fidelity_blocking = (
            list(fidelity_check.blocking_failures)
            if fidelity_check is not None
            else []
        )

        # 软门：体裁风格未完全通过 → 正文照常交付并附未完全满足项
        quality_warnings: list[str] = []
        soft_failed = not genre_check.passed and final_text
        if soft_failed:
            quality_warnings = [
                f"体裁规则「{finding.label}」未完全满足：{finding.detail}"
                for finding in genre_check.findings
                if not finding.passed
            ]
            if repair_attempts == 0:
                quality_warnings.append(
                    "本轮预算内未执行定向修复（剩余预算不足或修复不可用），"
                    "正文按当前最佳稿交付。"
                )
            output = output.model_copy(
                update={"quality_status": HumanizerQualityStatus.WARN}
            )

        # 终态判定
        status = HumanizerResultStatus.DONE
        state = HumanizerProcessState.LOADING
        error_code: str | None = None
        error_message: str | None = None
        if fidelity_blocking:
            # 硬门：来源保真冲突（新增无来源 claim/亲历/保护项被破坏）→
            # 阻止把违规正文标记为最终稿；任何风格均分不能覆盖该失败。
            status = HumanizerResultStatus.ERROR
            state = HumanizerProcessState.ERROR
            error_code = "fidelity_gate_conflict"
            error_message = (
                "来源保真硬门未通过，已停止交付："
                + "；".join(f.note for f in fidelity_blocking[:3])
                + "。恢复方式：删除或修正无来源的新增内容后重试"
                "（任务输入与附件已保留）。"
            )
        elif fact_lock_check.blocking_conflicts:
            # 硬门：事实锁冲突 → 阻止把错误版本标记为最终稿（不交付违规
            # 正文）；投影附冲突项与恢复方式，保留输入供重试。
            status = HumanizerResultStatus.ERROR
            state = HumanizerProcessState.ERROR
            error_code = "fact_lock_conflict"
            error_message = (
                "事实锁冲突，已停止交付："
                + "；".join(fact_lock_check.blocking_conflicts[:3])
                + "。恢复方式：在原文中修正上述冲突字段后重试"
                "（任务输入与附件已保留）。"
            )
        elif soft_failed:
            # 软门未完全通过：交付正文 + 警告（重新生成是可选操作，不是唯一出口）
            status = HumanizerResultStatus.NEEDS_HUMAN
            state = HumanizerProcessState.DONE
        elif (
            fact_lock_check.needs_human
            or any(not ref.preserved for ref in references)
            or (
                checkpoint.fidelity_check is not None
                and bool(checkpoint.fidelity_check.needs_confirmation)
            )
        ):
            # 需人工事项（事实锁弱冲突/新增未核实引用/保真无法判定项）→
            # 明确标注人工确认；输出合同的「未决问题」属于正常交付内容，
            # 不强制 needs_human。
            status = HumanizerResultStatus.NEEDS_HUMAN
            state = HumanizerProcessState.DONE
        else:
            status = HumanizerResultStatus.DONE
            state = HumanizerProcessState.DONE

        result = HumanizerResultProjection(
            task_id=assistant_message_id,
            skill_id=skill_input.skill_id,
            skill_version=skill_version,
            path=contract.path,
            genre=contract.genre,
            contract=contract,
            status=status,
            output=output if status != HumanizerResultStatus.ERROR else None,
            fact_lock_check=fact_lock_check,
            source_ledger=checkpoint.ledger,
            fidelity_check=fidelity_check,
            references=references,
            genre_check=genre_summary,
            quality_warnings=quality_warnings,
            repair_attempts=repair_attempts,
            process_state=state,
            error_code=error_code,
            error_message=error_message,
            created_at=datetime.now(UTC),
        )
        self._audit(
            account_id,
            conversation_id,
            assistant_message_id,
            skill_input.skill_id,
            skill_version,
            contract,
            status,
            fact_lock_check,
            fidelity_check,
            checkpoint.ledger,
        )
        return result

    def _deterministic_fact_check(
        self,
        fact_lock_check: FactLockCheckResult,
        genre_check: GenreCheckResult,
        references: list[HumanizerReference],
        contract: HumanizerTaskContract,
    ) -> list[HumanizerFactCheckItem]:
        items: list[HumanizerFactCheckItem] = []
        for entry in fact_lock_check.entries:
            if entry.severity == FactLockSeverity.INFO and entry.status in (
                FactLockStatus.PRESERVED,
            ):
                continue
            if entry.status == FactLockStatus.ADDED:
                items.append(
                    HumanizerFactCheckItem(
                        item=f"新增「{entry.surface_after}」",
                        result="需人工确认",
                        evidence="新增内容未经原文事实锁覆盖，须人工核对来源。",
                    )
                )
            elif entry.status == FactLockStatus.PRESERVED:
                items.append(
                    HumanizerFactCheckItem(
                        item=f"「{entry.surface_before}」保持",
                        result="已核实",
                        evidence="前后比较一致。",
                    )
                )
            else:
                result_label = (
                    "需人工确认"
                    if entry.severity == FactLockSeverity.NEEDS_HUMAN
                    else "存在虚构风险"
                )
                items.append(
                    HumanizerFactCheckItem(
                        item=f"「{entry.surface_before}」变更",
                        result=result_label,
                        evidence=entry.note,
                    )
                )
        for reference in references:
            if not reference.preserved:
                if reference.source_type == "unverified":
                    items.append(
                        HumanizerFactCheckItem(
                            item=f"新增引用「{reference.label}」",
                            result="需人工确认",
                            evidence="新增引用不在证据合同清单内，须人工核实真实来源。",
                        )
                    )
                else:
                    items.append(
                        HumanizerFactCheckItem(
                            item=f"引用「{reference.label}」未保持",
                            result="需人工确认",
                            evidence="原文引用的来源在结果中缺失。",
                        )
                    )
        if not references:
            # 改写/生成路径都保证事实核查至少一条确定性条目：无证据合同时
            # 明确披露「无来源可核」，避免输出合同完整性门依赖模型自报。
            items.append(
                HumanizerFactCheckItem(
                    item="外部来源",
                    result="需人工确认",
                    evidence=(
                        "本轮未配置本地检索或联网来源；正文引用须回到原文核验，"
                        "生成内容中的事实与引用须人工核对。"
                    ),
                )
            )
        elif contract.path == HumanizerPath.GENERATE and not fact_lock_check.entries:
            items.append(
                HumanizerFactCheckItem(
                    item="硬约束与事实锁",
                    result="已核实",
                    evidence="硬约束经确定性包含检查通过；无独立证据来源可供进一步核查。",
                )
            )
        if not genre_check.passed:
            for finding in genre_check.findings:
                if not finding.passed:
                    items.append(
                        HumanizerFactCheckItem(
                            item=f"体裁规则「{finding.label}」",
                            result="需人工确认",
                            evidence=finding.detail,
                        )
                    )
        return items

    def _check_citation_preservation(
        self,
        contract: HumanizerTaskContract,
        source_text: str,
        final_text: str,
        references: list[HumanizerReference],
    ) -> list[HumanizerReference]:
        """原文/约束中的引用必须仍在正文出现；新增引用必须来自证据合同。"""
        if contract.path == HumanizerPath.REWRITE:
            source_keys = {d.canonical for d in extract_locks(source_text)}
            final_keys = {d.canonical for d in extract_locks(final_text)}
            source_citations = [
                d for d in extract_locks(source_text) if d.kind == FactLockKind.CITATION
            ]
            for draft in source_citations:
                preserved = draft.canonical in final_keys
                if not preserved and not any(
                    ref.citation_surface and ref.citation_surface in final_text
                    for ref in references
                ):
                    references.append(
                        HumanizerReference(
                            reference_id=f"ref-{secrets.token_urlsafe(8)}",
                            label=f"原文引用「{draft.surface}」",
                            source_type="original",
                            detail="原文中的引用在改写结果中缺失。",
                            citation_surface=draft.surface,
                            preserved=False,
                        )
                    )
            # 正文中出现的新引用（不在原文）必须能在证据合同中找到
            allowed_surfaces = {
                ref.citation_surface
                for ref in references
                if ref.citation_surface is not None and ref.preserved
            }
            for draft in extract_locks(final_text):
                if draft.kind != FactLockKind.CITATION:
                    continue
                if draft.canonical in source_keys or draft.surface in allowed_surfaces:
                    continue
                references.append(
                    HumanizerReference(
                        reference_id=f"ref-{secrets.token_urlsafe(8)}",
                        label=f"新增引用「{draft.surface}」",
                        source_type="unverified",
                        detail="新增引用不在证据合同清单内，须人工核实真实来源。",
                        citation_surface=draft.surface,
                        preserved=False,
                    )
                )
        return references

    def _failed_projection(
        self,
        account_id: str,
        conversation_id: str,
        assistant_message_id: str,
        skill_input: HumanizerSkillInput,
        skill_version: str,
        error_code: str,
        error_message: str,
        *,
        retryable: bool,
        process_steps: list[str],
        state: HumanizerProcessState,
    ) -> HumanizerResultProjection:
        self._audit(
            account_id,
            conversation_id,
            assistant_message_id,
            skill_input.skill_id,
            skill_version,
            skill_input.contract,
            HumanizerResultStatus.ERROR,
            None,
        )
        return HumanizerResultProjection(
            task_id=assistant_message_id,
            skill_id=skill_input.skill_id,
            skill_version=skill_version,
            path=skill_input.contract.path,
            genre=skill_input.contract.genre,
            contract=skill_input.contract,
            status=HumanizerResultStatus.ERROR,
            output=None,
            process_state=state,
            process_steps=process_steps,
            error_code=error_code,
            error_message=error_message + ("（可重试，输入保留）" if retryable else ""),
            created_at=datetime.now(UTC),
        )

    def _audit(
        self,
        account_id: str,
        conversation_id: str,
        assistant_message_id: str,
        skill_id: str,
        skill_version: str,
        contract: HumanizerTaskContract,
        status: HumanizerResultStatus,
        fact_lock_check: FactLockCheckResult | None,
        fidelity_check: FidelityCheckResult | None = None,
        ledger: SourceLedger | None = None,
    ) -> None:
        if self._observability is None:
            return
        with contextlib.suppress(Exception):  # noqa: BLE001 - 审计失败不阻断主流程
            self._observability.log_audit(
                actor_account_id=account_id,
                action=AuditAction.HUMANIZER_GENERATE,
                result=(
                    AuditResult.SUCCESS
                    if status in (HumanizerResultStatus.DONE, HumanizerResultStatus.NEEDS_HUMAN)
                    else AuditResult.BLOCKED
                ),
                object_refs=[conversation_id, assistant_message_id],
                reason="调用内置 bridges-humanizer SKILL 执行人味化任务。",
                details={
                    "skill_id": skill_id,
                    "skill_version": skill_version,
                    "path": contract.path.value,
                    "genre": contract.genre.value,
                    "status": status.value,
                    "blocking_conflicts": (
                        len(fact_lock_check.blocking_conflicts) if fact_lock_check else 0
                    ),
                    "needs_human": (
                        len(fact_lock_check.needs_human) if fact_lock_check else 0
                    ),
                    # Issue 02 保真硬门：只记录版本/哈希/计数/失败码，不记录正文
                    "ledger_version": (
                        fidelity_check.ledger_version if fidelity_check else None
                    ),
                    "checker_version": (
                        fidelity_check.checker_version if fidelity_check else None
                    ),
                    "ledger_hash": (
                        fidelity_check.ledger_hash if fidelity_check else None
                    ),
                    "fidelity_blocking": (
                        len(fidelity_check.blocking_failures) if fidelity_check else 0
                    ),
                    "fidelity_needs_confirmation": (
                        len(fidelity_check.needs_confirmation) if fidelity_check else 0
                    ),
                    "fidelity_failure_codes": (
                        [f.code.value for f in fidelity_check.blocking_failures]
                        + [f.code.value for f in fidelity_check.needs_confirmation]
                        if fidelity_check
                        else []
                    ),
                    "fidelity_new_claims": (
                        fidelity_check.summary.new_claim_count if fidelity_check else 0
                    ),
                    "fidelity_unattributed": (
                        fidelity_check.summary.unattributed_claim_count if fidelity_check else 0
                    ),
                    "fidelity_first_person_interceptions": (
                        fidelity_check.summary.first_person_interception_count
                        if fidelity_check else 0
                    ),
                    "fidelity_protected_spans": (
                        fidelity_check.summary.protected_span_count if fidelity_check else 0
                    ),
                    "fidelity_spans_by_kind": (
                        ledger.compile_summary.protected_spans_by_kind
                        if ledger is not None
                        and ledger.compile_summary is not None
                        else {}
                    ),
                },
            )


    def _compile_ledger(
        self,
        contract: HumanizerTaskContract,
        primary_text: str,
        source_label: str,
        references: list[HumanizerReference],
        *,
        account_materials: list[tuple[str, str]] | None = None,
        external_evidence_requested: bool = False,
    ) -> SourceLedger | None:
        """编译来源账本（Issue 02）：粘贴原文/约束为唯一主来源，知识库材料
        作为账户作用域授权材料，外部引用仅在用户明确请求外部证据时作为
        授权外部来源；用户指定措辞从硬约束提取。能力开关关闭时返回 None
        （回滚为旧流程）。"""
        if not FIDELITY_GATE_ENABLED:
            return None
        external_allowed: list[tuple[str, str]] = []
        if external_evidence_requested:
            for ref in references:
                detail = ref.detail or ref.citation_surface or ""
                if ref.label or detail:
                    external_allowed.append((ref.label or "外部来源", detail))
        return compile_source_ledger(
            primary_text,
            primary_label=source_label,
            account_scoped=account_materials or [],
            external_allowed=external_allowed,
            common_knowledge=list(contract.explicit_common_knowledge),
            user_phrases=self._user_phrases_from_constraints(contract),
            allow_first_person=contract.allow_first_person,
        )

    @staticmethod
    def _user_phrases_from_constraints(
        contract: HumanizerTaskContract,
    ) -> list[str]:
        """从硬约束提取用户指定措辞（必须保留/不得改写的引号内或指定短语）。"""
        phrases: list[str] = []
        for constraint in contract.hard_constraints:
            for match in re.finditer(
                r"[「“『\"]([^「」“”『』\"']{2,40})[」”』\"]",
                constraint,
            ):
                phrase = match.group(1).strip()
                if phrase:
                    phrases.append(phrase)
            for match in re.finditer(
                r"必须(?:保留|写清|使用|体现|写出)[：:，,]?\s*([一-鿿A-Za-z0-9]{2,30})",
                constraint,
            ):
                phrase = match.group(1).strip()
                if phrase and phrase not in phrases:
                    phrases.append(phrase)
        return phrases

    def _constraints_text(self, contract: HumanizerTaskContract) -> str:
        parts: list[str] = []
        for constraint in contract.hard_constraints:
            parts.append(constraint)
        if contract.audience:
            parts.append(f"受众：{contract.audience}")
        if contract.channel:
            parts.append(f"渠道：{contract.channel}")
        if contract.length_target:
            parts.append(f"长度：{contract.length_target}")
        return "\n".join(parts) if parts else "（无硬约束）"


#: 账户能力/凭据/区域类错误 → 过程卡 permission 态
_PERMISSION_ERROR_CODES = frozenset(
    {"auth_error", "capability_not_verified", "unregistered_capability", "region_error"}
)

__all__ = [
    "HumanizerService",
    "HumanizerRunEvent",
    "HumanizerError",
    "HUMANIZER_CAPABILITY_NAME",
    "HUMANIZER_CAPABILITY_VERSION",
]
