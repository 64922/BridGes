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
import secrets
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from bridges.ai.model_gateway import ModelGateway
from bridges.chat.attachments import ChatAttachmentError, ChatAttachmentService
from bridges.contracts.ai import ModelCallStatus
from bridges.contracts.humanizer import (
    FactLockCheckResult,
    FactLockKind,
    FactLockSeverity,
    FactLockStatus,
    HumanizerEdit,
    HumanizerEditKind,
    HumanizerFactCheckItem,
    HumanizerOutputContract,
    HumanizerPath,
    HumanizerProcessState,
    HumanizerReference,
    HumanizerResultProjection,
    HumanizerResultStatus,
    HumanizerSkillInput,
    HumanizerTaskContract,
)
from bridges.contracts.observability import AuditAction, AuditResult
from bridges.ingestion.parsers import ParsedDocument, ParseError, parse_document
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
from bridges.skills.registry import SkillRegistry
from bridges.web_search.service import WebSearchService

HUMANIZER_CAPABILITY_NAME = "qwen_structured_output"
HUMANIZER_CAPABILITY_VERSION = "1"

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
    RESULT = "result"


@dataclass
class HumanizerRunEvent:
    """编排向聊天服务产出的事件（过程事件或最终结果）。"""

    kind: HumanizerRunKind
    state: HumanizerProcessState | None = None
    step_label: str = ""
    detail: str | None = None
    retryable: bool = False
    progress_steps: list[str] = field(default_factory=list)
    result: HumanizerResultProjection | None = None


class HumanizerService:
    """bridges-humanizer 的两条路径编排。"""

    def __init__(
        self,
        registry: SkillRegistry,
        gateway: ModelGateway,
        attachment_service: ChatAttachmentService | None = None,
        retrieval_service: LayeredRetrievalService | None = None,
        web_search_service: WebSearchService | None = None,
        observability_service: ObservabilityService | None = None,
    ) -> None:
        self._registry = registry
        self._gateway = gateway
        self._attachments = attachment_service
        self._retrieval = retrieval_service
        self._web_search = web_search_service
        self._observability = observability_service

    # ------------------------------------------------------------------
    # 对外入口
    # ------------------------------------------------------------------

    def resolve_skill(self, skill_input: HumanizerSkillInput) -> str:
        """校验 SKILL 载荷：标识必须已注册为内置只读能力，版本固定。"""
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
    ) -> Iterator[HumanizerRunEvent]:
        """执行一条人味化任务：yield 过程事件，最后 yield 结果事件。"""
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
            source_text, source_label, references = self._resolve_source(
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
                        "没有可改写的原文：请粘贴文本或选择当前账户文件。",
                        retryable=True,
                    )
                locks = extract_locks(source_text)
                fact_lock_source = source_text
            else:
                constraints_text = self._constraints_text(contract)
                locks = extract_locks(constraints_text)
                fact_lock_source = constraints_text
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

            # 步骤 4：确定性复核
            yield process(HumanizerProcessState.LOADING, "正在复核事实锁与体裁规则…")
            output = self._coerce_output(result.output or {})
            final_result = self._review(
                account_id,
                conversation_id,
                assistant_message_id,
                skill_input,
                skill_version,
                output,
                source_text,
                fact_lock_source,
                references,
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
    ) -> tuple[str, str, list[HumanizerReference]]:
        """改写路径解析原文（粘贴或附件），并组装证据合同引用清单。"""
        references: list[HumanizerReference] = []
        if contract.path == HumanizerPath.GENERATE:
            topic = (contract.topic or "").strip()
            if not topic:
                raise HumanizerError(
                    "empty_topic", "缺少主题：请填写要生成的文章主题。", retryable=True
                )
            return topic, "主题", references

        source_parts: list[str] = []
        label_parts: list[str] = []
        if contract.source_text and contract.source_text.strip():
            source_parts.append(contract.source_text.strip())
            label_parts.append("粘贴文本")
        # Issue 04：任何契约引用的附件解析失败都指名文件并给出支持格式，
        # 保留附件供重试，绝不静默改用另一材料（粘贴文本不顶替失败附件）。
        for attachment_id in contract.attachment_ids:
            parsed = self._parse_attachment(account_id, conversation_id, attachment_id)
            source_parts.append(parsed.text)
            label_parts.append(parsed.title)
            references.append(
                HumanizerReference(
                    reference_id=f"ref-{secrets.token_urlsafe(8)}",
                    label=parsed.title,
                    source_type="attachment",
                    detail=f"文件：{parsed.title}",
                    preserved=True,
                )
            )
        if not source_parts:
            raise HumanizerError(
                "empty_source",
                "没有可改写的原文：请粘贴文本或选择当前账户文件。",
                retryable=True,
            )
        # 证据合同：检索/联网来源进入引用清单（模型只可引用清单内材料）
        for citation in self._citations_from(retrieval_round):
            references.append(citation)
        for item in self._citations_from_web(web_search_projection):
            references.append(item)
        for item in self._citations_from_arxiv(arxiv_search_projection):
            references.append(item)
        return "\n\n".join(source_parts), "、".join(label_parts) or "原文", references

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
    ) -> Any:
        contract = skill_input.contract
        genre_set = genre_rule_set(contract.genre)
        system_prompt = self._build_system_prompt(
            skill_version, contract, genre_set, locks, source_label, references
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
        return f"""你是 BridGes 内置「文章人味化」SKILL（版本 {skill_version}）的执行器。

【任务边界】
- 目标：在保持科学判断、证据强度、限定条件和引用关系的前提下，改进表达的自然度、任务适配度与可读性。
- 禁止：规避 AI 检测、冒充真人、伪造个人经历、欺骗性代写；不虚构事实、论文或引用；不以牺牲科学事实换取口语化。
- 体裁：{genre_doc.display_name}。必含：{required_lines}。禁止：{prohibited_lines}。
- 体裁责任：{genre_doc.human_responsibility}

【事实锁（不得改变，冲突时宁可保留原文）】
{lock_lines}

【证据合同（可引用来源清单；只可引用清单内材料，不得虚构）】
{reference_lines}

【输出要求】严格输出 JSON，不得输出 JSON 之外的任何内容：
{{"final_text": 最终文本, "edits": [{{"original": 原文片段, "revised": 新文片段,
"kind": "rewrite|restructure|word_choice|audience_adapt|no_change",
"reason": 理由（对应哪条体裁规则）}}], "fact_check": [{{"item": 核查对象,
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

    def _review(
        self,
        account_id: str,
        conversation_id: str,
        assistant_message_id: str,
        skill_input: HumanizerSkillInput,
        skill_version: str,
        output: HumanizerOutputContract,
        source_text: str,
        fact_lock_source: str,
        references: list[HumanizerReference],
    ) -> HumanizerResultProjection:
        contract = skill_input.contract
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

        # 体裁规则复核
        genre_check = check_genre(final_text, contract.genre)
        genre_summary = genre_check.summary()

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

        # 终态判定
        status = HumanizerResultStatus.DONE
        state = HumanizerProcessState.LOADING
        error_code: str | None = None
        error_message: str | None = None
        if fact_lock_check.blocking_conflicts:
            status = HumanizerResultStatus.ERROR
            state = HumanizerProcessState.ERROR
            error_code = "fact_lock_conflict"
            error_message = (
                "事实锁冲突，已停止交付："
                + "；".join(fact_lock_check.blocking_conflicts[:3])
                + "。可调整任务后重试。"
            )
        elif not genre_check.passed:
            status = HumanizerResultStatus.ERROR
            state = HumanizerProcessState.ERROR
            error_code = "genre_check_failed"
            error_message = (
                f"体裁规则复核未通过（{genre_rule_set(contract.genre).display_name}）："
                + "；".join(genre_summary)
                + "。请重试。"
            )
        elif fact_lock_check.needs_human or any(
            not ref.preserved for ref in references
        ):
            # 需人工事项（事实锁弱冲突/新增未核实引用）→ 明确标注人工确认；
            # 输出合同的「未决问题」属于正常交付内容，不强制 needs_human。
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
            references=references,
            genre_check=genre_summary,
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
                },
            )


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
