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
from time import perf_counter
from typing import Any

from bridges.ai.model_gateway import ModelGateway
from bridges.chat.attachments import ChatAttachmentError, ChatAttachmentService
from bridges.chat.budget import RESULT_FAILED, RunBudget, RunStage
from bridges.contracts.ai import ModelCallStatus
from bridges.contracts.evidence_safety import (
    EvidenceRevisionChange,
    EvidenceRevisionMode,
    EvidenceRevisionStatus,
    EvidenceSafeReport,
)
from bridges.contracts.expression_review import (
    ExpressionReviewReport,
    ReviewSeverity,
)
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
from bridges.skills.humanizer.contract_compiler import KNOWN_SCHEMA_VERSIONS
from bridges.skills.humanizer.draft_compiler import (
    DRAFT_MAX_TOKENS,
    DRAFT_OUTPUT_JSON_SCHEMA,
    compile_draft_prompt,
)
from bridges.skills.humanizer.evidence_safety import (
    build_revision_prompt,
    compare_claim_changes,
    run_evidence_safety,
)
from bridges.skills.humanizer.expression_review import run_expression_review
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


@dataclass
class _ExpressionMetrics:
    """表达任务契约新流程的观测元数据（脱敏计数，不记录正文）。"""

    rule_count: int = 0
    draft_latency_ms: int | None = None
    revision_latency_ms: int | None = None


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
        Issue 04：带版本化表达任务契约的任务走新流程（首稿 profile +
        表达审稿 + 程序生成审计信息，模型调用至多一次）；旧显式 SKILL
        （无契约）保持原行为，直到 Issue 08 完成投影迁移。
        """
        skill_version = self.resolve_skill(skill_input)
        if skill_input.expression_contract is not None:
            yield from self._run_expression_task(
                account_id,
                conversation_id,
                assistant_message_id,
                skill_input,
                skill_version,
                run_context,
                retrieval_round,
                web_search_projection,
                arxiv_search_projection,
                budget,
            )
            return
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
    # 表达任务契约新流程（Issue 04：首稿 profile + 表达审稿 + 程序审计）
    # ------------------------------------------------------------------

    def _run_expression_task(
        self,
        account_id: str,
        conversation_id: str,
        assistant_message_id: str,
        skill_input: HumanizerSkillInput,
        skill_version: str,
        run_context: Any,
        retrieval_round: Any | None,
        web_search_projection: Any | None,
        arxiv_search_projection: Any | None,
        budget: RunBudget | None,
    ) -> Iterator[HumanizerRunEvent]:
        """带版本化表达任务契约的新文章流程。

        执行器只从契约与来源账本取得任务、权限和材料：契约版本未知或
        快照哈希不一致时在模型调用前稳定拒绝；首稿只编译当前 profile 的
        6—10 条正向规则；模型只产出候选正文（一次调用）；修改清单、保真
        结果、模式命中与可确定性差异全部由程序生成，不伪装成模型自证。
        """
        expression_contract = skill_input.expression_contract
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
            # 步骤 1：契约版本校验（模型调用前稳定拒绝）
            yield process(HumanizerProcessState.LOADING, "正在校验表达任务契约…")
            self._validate_expression_contract(expression_contract)
            progress.append("校验表达任务契约")

            # 步骤 2：解析来源与材料（原文/知识库/外部证据合同）
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

            # 步骤 3：编译来源账本（Issue 02 硬门，新流程强制启用）
            ledger = self._compile_ledger(
                contract,
                source_text if contract.path == HumanizerPath.REWRITE
                else self._constraints_text(contract),
                source_label,
                references,
                account_materials=account_materials,
                external_evidence_requested=bool(
                    skill_input.route is not None
                    and skill_input.route.external_evidence_requested
                ),
            )
            progress.append("编译来源账本")

            # 步骤 4：编译首稿提示（当前 profile 的 6—10 条正向规则）
            yield process(HumanizerProcessState.LOADING, "正在编译首稿规则…")
            draft = compile_draft_prompt(
                expression_contract,
                source_text=source_text,
                source_label=source_label,
                ledger=ledger,
            )
            progress.append("编译首稿规则")

            # 步骤 5：一次模型调用，模型只产出候选正文（首稿，至多一次）
            yield process(HumanizerProcessState.LOADING, "正在起草正文…")
            final_text, draft_latency_ms = self._invoke_draft_model(
                account_id,
                conversation_id,
                assistant_message_id,
                run_context,
                draft.system_prompt,
                source_text,
            )
            progress.append("起草正文")
            yield HumanizerRunEvent(
                kind=HumanizerRunKind.DRAFT,
                draft_text=final_text,
                progress_steps=list(progress),
            )

            # 步骤 6：证据安全（Issue 06）。
            # 默认模式只分类 claim 并生成独立风险项，不改变正文；只有契约
            # 进入 EVIDENCE_SAFE 且存在风险时才执行至多一次定向修订，修订
            # 后重新通过来源硬门与受保护项检查才应用，否则保持首稿并返回
            # 稳定的 hold_for_user 风险状态。
            yield process(HumanizerProcessState.LOADING, "正在检查证据边界…")
            evidence_report: EvidenceSafeReport | None = None
            revision_latency_ms: int | None = None
            if ledger is not None:
                evidence_report = run_evidence_safety(
                    final_text,
                    contract=expression_contract,
                    ledger=ledger,
                )
                progress.append("证据边界检查")
                if (
                    expression_contract.evidence_revision_mode
                    == EvidenceRevisionMode.EVIDENCE_SAFE
                    and evidence_report.risks
                ):
                    yield process(
                        HumanizerProcessState.LOADING, "正在执行证据安全修订…"
                    )
                    try:
                        revision_prompt = build_revision_prompt(
                            expression_contract,
                            final_text,
                            evidence_report.risks,
                            ledger,
                        )
                        revised_text, revision_latency_ms = self._invoke_draft_model(
                            account_id,
                            conversation_id,
                            assistant_message_id,
                            run_context,
                            revision_prompt,
                            final_text,
                        )
                        revision_fidelity = run_fidelity_check(
                            ledger,
                            revised_text,
                            contract_path=contract.path,
                            allow_assumptions=contract.allow_assumptions,
                        )
                    except (FidelityCheckError, HumanizerError):
                        # 修订调用或硬门失败：保持首稿，稳定返回待用户确认；
                        # 账本版本/哈希等系统问题由步骤 7 最终保真硬门失败
                        # 关闭兜底（同一账本，不会静默放行）。
                        revision_latency_ms = None
                        evidence_report = self._hold_evidence_report(
                            evidence_report, []
                        )
                        progress.append("证据安全修订保持原文")
                    else:
                        changes, conflicts = compare_claim_changes(
                            final_text, revised_text, evidence_report.claims
                        )
                        applied = (
                            revision_fidelity.passed
                            and not conflicts
                            and not any(
                                change.needs_user_confirmation
                                for change in changes
                            )
                        )
                        if applied:
                            final_text = revised_text
                            evidence_report = self._apply_evidence_report(
                                evidence_report, changes
                            )
                            progress.append("证据安全修订已应用")
                        else:
                            evidence_report = self._hold_evidence_report(
                                evidence_report,
                                changes,
                                conflicts=conflicts,
                            )
                            progress.append("证据安全修订保持原文")

            # 步骤 7：来源保真硬门（Issue 02 失败关闭，对最终交付文本执行）
            yield process(HumanizerProcessState.LOADING, "正在检查来源保真…")
            fidelity_check = None
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
            progress.append("来源保真检查")

            # 步骤 8：表达审稿（软审稿，只报警不做机械替换）
            review = run_expression_review(
                final_text,
                contract=expression_contract,
                source_text=(
                    source_text if contract.path == HumanizerPath.REWRITE else ""
                ),
                ledger=ledger,
            )
            progress.append("表达审稿")

            # 步骤 9：确定性适配器生成输出合同（修改清单/事实核查/未决问题）
            output = self._expression_output_contract(
                contract,
                final_text,
                fidelity_check,
                review,
                source_knowledge_base_object_ids,
            )
            progress.append("生成审计信息")
            metrics = _ExpressionMetrics(
                rule_count=draft.rule_count,
                draft_latency_ms=draft_latency_ms,
                revision_latency_ms=revision_latency_ms,
            )

            # 步骤 10：终态判定与结果投影（旧投影字段保持兼容）
            yield process(HumanizerProcessState.LOADING, "正在完成交付…")
            final_result = self._expression_finalize(
                account_id,
                conversation_id,
                assistant_message_id,
                skill_input,
                skill_version,
                contract,
                expression_contract,
                final_text,
                output,
                fidelity_check,
                review,
                references,
                progress,
                ledger,
                metrics,
                evidence_report,
            )
            yield HumanizerRunEvent(
                kind=HumanizerRunKind.RESULT,
                step_label="人味化完成",
                result=final_result,
            )
        except HumanizerError as exc:
            if exc.code in ("empty_source", "empty_topic"):
                state = HumanizerProcessState.EMPTY
            elif exc.code in _PERMISSION_ERROR_CODES:
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
    def _validate_expression_contract(expression_contract: Any) -> None:
        """契约版本校验：未知版本或哈希不一致时在模型调用前稳定拒绝。"""
        if expression_contract.schema_version not in KNOWN_SCHEMA_VERSIONS:
            raise HumanizerError(
                "expression_contract_unsupported",
                f"表达任务契约版本 {expression_contract.schema_version} 不受支持，"
                "请重新发送任务。",
                retryable=False,
            )
        if (
            expression_contract.compute_version_hash()
            != expression_contract.version_hash
        ):
            raise HumanizerError(
                "expression_contract_hash_mismatch",
                "表达任务契约快照哈希不一致，拒绝执行。",
                retryable=False,
            )

    def _invoke_draft_model(
        self,
        account_id: str,
        conversation_id: str,
        assistant_message_id: str,
        run_context: Any,
        system_prompt: str,
        source_text: str,
    ) -> tuple[str, int]:
        """一次正文生成调用：输出合同只有候选正文（不要求模型生产审计元数据）。

        返回（正文, 首稿延迟毫秒）供观测记录，不记录正文内容。
        """
        payload = {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": source_text.strip() or "请按上述要求起草正文。"},
            ],
            "json_schema": DRAFT_OUTPUT_JSON_SCHEMA,
            "temperature": 0.4,
            "max_tokens": DRAFT_MAX_TOKENS,
        }
        started = perf_counter()
        call_result = self._gateway.invoke(
            HUMANIZER_CAPABILITY_NAME,
            HUMANIZER_CAPABILITY_VERSION,
            run_context,
            payload,
        )
        latency_ms = int((perf_counter() - started) * 1000)
        if call_result.status not in (ModelCallStatus.SUCCESS, ModelCallStatus.DEGRADED):
            raise HumanizerError(
                call_result.error_code or "humanizer_generation_failed",
                call_result.error_message or "人味化生成失败，请重试。",
                retryable=call_result.status == ModelCallStatus.RETRYABLE_FAIL,
            )
        raw = call_result.output or {}
        final_text = str(raw.get("final_text") or "").strip()
        if not final_text:
            raise HumanizerError(
                "empty_output",
                "生成结果缺少正文，请重试。",
                retryable=True,
            )
        return final_text, latency_ms

    def _expression_output_contract(
        self,
        contract: HumanizerTaskContract,
        final_text: str,
        fidelity_check: FidelityCheckResult | None,
        review: ExpressionReviewReport,
        source_knowledge_base_object_ids: list[str],
    ) -> HumanizerOutputContract:
        """确定性适配器生成输出合同：修改清单/事实核查/未决问题全部程序生成。"""
        edits = self._expression_edits(review, final_text)
        fact_check = self._expression_fact_check(fidelity_check, review)
        open_questions = self._expression_open_questions(fidelity_check)
        return HumanizerOutputContract(
            final_text=final_text,
            edits=edits,
            fact_check=fact_check,
            open_questions=open_questions,
            quality_status=(
                HumanizerQualityStatus.WARN
                if not review.no_change_recommended
                else HumanizerQualityStatus.OK
            ),
            source_attachment_ids=[],
            source_knowledge_base_object_ids=list(source_knowledge_base_object_ids),
        )

    @staticmethod
    def _expression_edits(
        review: ExpressionReviewReport, final_text: str
    ) -> list[HumanizerEdit]:
        """从审稿发现生成逐项修改清单（定向建议，非机械替换）。

        只有 warning/suggestion 级发现才生成定向修改项；info 级观察与
        自然稿一致报告 NO_CHANGE，与 no_change_recommended 语义一致。
        """
        high_value = [
            finding
            for finding in review.findings
            if finding.severity in (ReviewSeverity.WARNING, ReviewSeverity.SUGGESTION)
        ]
        if not high_value:
            return [
                HumanizerEdit(
                    edit_id=f"ed-{secrets.token_urlsafe(8)}",
                    kind=HumanizerEditKind.NO_CHANGE,
                    original=final_text[:60],
                    revised=final_text[:60],
                    reason="表达审稿未发现高价值修改建议（no_change_recommended）。",
                )
            ]
        edits: list[HumanizerEdit] = []
        for finding in high_value[:8]:
            kind = (
                HumanizerEditKind.WORD_CHOICE
                if finding.location.end - finding.location.start <= 30
                else HumanizerEditKind.REWRITE
            )
            edits.append(
                HumanizerEdit(
                    edit_id=f"ed-{secrets.token_urlsafe(8)}",
                    kind=kind,
                    original=finding.evidence,
                    revised=finding.suggestion,
                    reason=f"表达审稿「{finding.category}」：{finding.explanation}"
                    f"（定向建议，待修订时应用）",
                )
            )
        return edits

    @staticmethod
    def _expression_fact_check(
        fidelity_check: FidelityCheckResult | None,
        review: ExpressionReviewReport,
    ) -> list[HumanizerFactCheckItem]:
        """保真结果由程序生成：不把模型输出伪装成模型自证。"""
        items: list[HumanizerFactCheckItem] = []
        if fidelity_check is None:
            items.append(
                HumanizerFactCheckItem(
                    item="来源保真",
                    result="需人工确认",
                    evidence="未配置来源账本，正文事实与引用须人工核对。",
                )
            )
        else:
            for failure in (
                *fidelity_check.blocking_failures,
                *fidelity_check.needs_confirmation,
            ):
                result_label = (
                    "需人工确认"
                    if failure.severity.value == "needs_user_confirmation"
                    else "存在虚构风险"
                )
                items.append(
                    HumanizerFactCheckItem(
                        item=f"保真「{failure.code.value}」",
                        result=result_label,
                        evidence=failure.note,
                    )
                )
            if fidelity_check.passed:
                items.append(
                    HumanizerFactCheckItem(
                        item="来源保真检查",
                        result="已核实",
                        evidence=(
                            f"账本版本 {fidelity_check.ledger_version}，"
                            "保留检查与新增 claim 来源检查通过。"
                        ),
                    )
                )
        if not review.no_change_recommended:
            items.append(
                HumanizerFactCheckItem(
                    item="表达审稿",
                    result="需人工确认",
                    evidence=(
                        f"发现 {review.summary.finding_count} 条风格发现（软审稿），"
                        "不影响正文交付，可在修订阶段处理。"
                    ),
                )
            )
        return items

    @staticmethod
    def _expression_open_questions(
        fidelity_check: FidelityCheckResult | None,
    ) -> list[str]:
        if fidelity_check is None:
            return ["未配置来源账本：正文中的事实与引用须人工核对。"]
        return [
            failure.note
            for failure in (
                *fidelity_check.blocking_failures,
                *fidelity_check.needs_confirmation,
            )
        ]

    def _expression_finalize(
        self,
        account_id: str,
        conversation_id: str,
        assistant_message_id: str,
        skill_input: HumanizerSkillInput,
        skill_version: str,
        contract: HumanizerTaskContract,
        expression_contract: Any,
        final_text: str,
        output: HumanizerOutputContract,
        fidelity_check: FidelityCheckResult | None,
        review: ExpressionReviewReport,
        references: list[HumanizerReference],
        progress: list[str],
        ledger: SourceLedger | None,
        metrics: _ExpressionMetrics,
        evidence_report: EvidenceSafeReport | None = None,
    ) -> HumanizerResultProjection:
        """终态判定与投影：保真硬门阻止交付，风格发现只警告照常交付。"""
        genre_check = check_genre(final_text, contract.genre)
        fidelity_blocking = (
            list(fidelity_check.blocking_failures)
            if fidelity_check is not None
            else []
        )
        quality_warnings: list[str] = []
        if evidence_report is not None:
            revision_applied = (
                evidence_report.revision_status
                == EvidenceRevisionStatus.APPLIED
            )
            for risk in evidence_report.risks:
                detail = (
                    "已按证据安全修订处理"
                    if revision_applied
                    else "默认模式保持原文结论语义，未自动改写"
                )
                quality_warnings.append(
                    f"证据风险「{risk.category}」：{risk.explanation}"
                    f"（位置 {risk.location.start}-{risk.location.end}，{detail}）"
                )
            if (
                evidence_report.revision_status
                == EvidenceRevisionStatus.HOLD_FOR_USER
            ):
                quality_warnings.append(
                    "证据安全修订未应用：修订未通过、材料不足或无法判定，"
                    "正文保持原结论，请人工确认后决定是否调整。"
                )
        if not review.no_change_recommended:
            for finding in review.findings:
                if finding.severity in (
                    ReviewSeverity.WARNING,
                    ReviewSeverity.SUGGESTION,
                ):
                    quality_warnings.append(
                        f"表达审稿「{finding.category}」：{finding.explanation}"
                        f"（位置 {finding.location.start}-{finding.location.end}，"
                        f"定向建议：{finding.suggestion}）"
                    )
        if not genre_check.passed:
            for finding in genre_check.findings:
                if not finding.passed:
                    quality_warnings.append(
                        f"体裁复核「{finding.label}」：{finding.detail}"
                    )
            output = output.model_copy(
                update={"quality_status": HumanizerQualityStatus.WARN}
            )

        status = HumanizerResultStatus.DONE
        state = HumanizerProcessState.DONE
        error_code: str | None = None
        error_message: str | None = None
        if fidelity_blocking:
            status = HumanizerResultStatus.ERROR
            state = HumanizerProcessState.ERROR
            error_code = "fidelity_gate_conflict"
            error_message = (
                "来源保真硬门未通过，已停止交付："
                + "；".join(f.note for f in fidelity_blocking[:3])
                + "。恢复方式：删除或修正无来源的新增内容后重试"
                "（任务输入与附件已保留）。"
            )

        result = HumanizerResultProjection(
            task_id=assistant_message_id,
            skill_id=skill_input.skill_id,
            skill_version=skill_version,
            path=contract.path,
            genre=contract.genre,
            contract=contract,
            expression_contract=expression_contract,
            status=status,
            output=output if status != HumanizerResultStatus.ERROR else None,
            fact_lock_check=None,
            source_ledger=ledger,
            fidelity_check=fidelity_check,
            expression_review=review,
            evidence_safe=evidence_report,
            references=references,
            genre_check=genre_check.summary(),
            quality_warnings=quality_warnings,
            repair_attempts=0,
            process_state=state,
            process_steps=list(progress),
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
            None,
            fidelity_check,
            expression_contract=expression_contract,
            expression_review=review,
            expression_metrics=metrics,
            evidence_safe=evidence_report,
        )
        return result

    @staticmethod
    def _apply_evidence_report(
        report: EvidenceSafeReport,
        changes: list[EvidenceRevisionChange],
    ) -> EvidenceSafeReport:
        """修订已应用：记录实质变化，状态置 applied。"""
        return report.model_copy(
            update={
                "revisions": list(changes),
                "revision_status": EvidenceRevisionStatus.APPLIED,
                "summary": report.summary.model_copy(
                    update={
                        "revision_count": len(changes),
                        "hold_for_user_count": 0,
                        "protected_conflict_count": 0,
                    }
                ),
            }
        )

    @staticmethod
    def _hold_evidence_report(
        report: EvidenceSafeReport,
        changes: list[EvidenceRevisionChange],
        *,
        conflicts: list[str] | None = None,
    ) -> EvidenceSafeReport:
        """修订未应用：正文保持原文，返回稳定 hold_for_user 状态。

        证据不足、来源冲突或无法判定时都走此路径；不为了让文本「更科学」
        自动添加保守套话，也不进入人工审稿队列。
        """
        conflicts = conflicts or []
        summary = report.summary
        return report.model_copy(
            update={
                "revisions": list(changes),
                "revision_status": EvidenceRevisionStatus.HOLD_FOR_USER,
                "summary": summary.model_copy(
                    update={
                        "revision_count": len(changes),
                        "hold_for_user_count": len(changes) + len(conflicts),
                        "protected_conflict_count": len(conflicts),
                    }
                ),
            }
        )

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
        # Issue 04：体裁 profile 只规定任务目标/风险/可选表达，不再有必现元素。
        risk_lines = "；".join(genre_doc.risks) if genre_doc.risks else "（无）"
        optional_lines = (
            "；".join(genre_doc.optional_devices)
            if genre_doc.optional_devices
            else "（无，按任务需要选择表达手段）"
        )
        genre_line = (
            f"体裁：{genre_doc.display_name} profile。任务目标：{genre_doc.task_goal} "
            f"风险：{risk_lines}。可选表达（按需使用）：{optional_lines}。"
            f"体裁责任：{genre_doc.human_responsibility}"
        )
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
- {genre_line}

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
            expression_contract=skill_input.expression_contract,
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
            expression_contract=skill_input.expression_contract,
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
            expression_contract=skill_input.expression_contract,
        )
        return HumanizerResultProjection(
            task_id=assistant_message_id,
            skill_id=skill_input.skill_id,
            skill_version=skill_version,
            path=skill_input.contract.path,
            genre=skill_input.contract.genre,
            contract=skill_input.contract,
            expression_contract=skill_input.expression_contract,
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
        expression_contract: Any = None,
        expression_review: ExpressionReviewReport | None = None,
        expression_metrics: _ExpressionMetrics | None = None,
        evidence_safe: EvidenceSafeReport | None = None,
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
                    "genre": contract.genre.value if contract.genre else None,
                    "contract_version": (
                        expression_contract.version_hash
                        if expression_contract is not None
                        else None
                    ),
                    "contract_surface": (
                        expression_contract.surface.value
                        if expression_contract is not None
                        else None
                    ),
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
                    # Issue 04 表达审稿：只记录 profile/规则数/code 计数，不记录正文
                    "review_version": (
                        expression_review.review_version
                        if expression_review is not None
                        else None
                    ),
                    "review_scene_profile": (
                        expression_review.scene_profile
                        if expression_review is not None
                        else None
                    ),
                    "review_finding_count": (
                        expression_review.summary.finding_count
                        if expression_review is not None
                        else 0
                    ),
                    # 各审稿 code 数量（不合并为单一总分，避免隐藏 profile 退化）
                    "review_code_counts": (
                        dict(expression_review.summary.by_code)
                        if expression_review is not None
                        else {}
                    ),
                    "review_no_change": (
                        expression_review.no_change_recommended
                        if expression_review is not None
                        else None
                    ),
                    # Issue 04 观测：编译规则数量与首稿延迟（不记录正文）
                    "draft_rule_count": (
                        expression_metrics.rule_count
                        if expression_metrics is not None
                        else None
                    ),
                    "draft_latency_ms": (
                        expression_metrics.draft_latency_ms
                        if expression_metrics is not None
                        else None
                    ),
                    # Issue 06 证据安全：只记录模式/风险/修订/保持计数，不记录正文
                    "evidence_mode": (
                        evidence_safe.mode.value if evidence_safe is not None else None
                    ),
                    "evidence_claim_count": (
                        evidence_safe.summary.claim_count if evidence_safe else 0
                    ),
                    "evidence_risk_count": (
                        evidence_safe.summary.risk_count if evidence_safe else 0
                    ),
                    "evidence_risk_codes": (
                        dict(evidence_safe.summary.by_risk_code)
                        if evidence_safe
                        else {}
                    ),
                    "evidence_revision_status": (
                        evidence_safe.revision_status.value
                        if evidence_safe is not None
                        else None
                    ),
                    "evidence_revision_count": (
                        evidence_safe.summary.revision_count if evidence_safe else 0
                    ),
                    "evidence_hold_for_user": (
                        evidence_safe.summary.hold_for_user_count if evidence_safe else 0
                    ),
                    "evidence_protected_conflicts": (
                        evidence_safe.summary.protected_conflict_count
                        if evidence_safe
                        else 0
                    ),
                    "revision_latency_ms": (
                        expression_metrics.revision_latency_ms
                        if expression_metrics is not None
                        else None
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
