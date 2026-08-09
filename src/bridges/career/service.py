"""生涯规划助手编排服务（Issue 29）。

编排流程：接收聊天分支编译好的证据（画像切片披露、本地检索轮次、联网/
arXiv 投影、用户陈述）→ 补充学习记录证据 → 组装六类输出合同提示词 →
Qwen 结构化生成 → 宽容解析 → 确定性复核（承诺词边界/完整性门/事实证据门/
引用核验/过时/冲突）→ 投影构造与审计。

本服务不直接访问消息表：画像切片披露、检索轮次、联网投影与过程卡事件
由聊天分支（``chat/service.py``）负责落库与收敛；本服务只产出过程事件与
结果投影，失败通过 ``CareerError``（可重试/权限/空态分类）驱动前端五态
过程卡，绝不输出模板化假成功。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from bridges.ai.model_gateway import ModelGateway
from bridges.chat.budget import RunBudget
from bridges.contracts.ai import ModelCallStatus
from bridges.contracts.career import (
    CareerAssumption,
    CareerEvidenceKind,
    CareerEvidenceSource,
    CareerFact,
    CareerOption,
    CareerPlanningOutputContract,
    CareerPlanningProcessState,
    CareerPlanningProjection,
    CareerPlanningStatus,
    CareerRisk,
    CareerRunEvent,
    CareerRunEventKind,
    CareerStage,
    CareerSuggestion,
)
from bridges.contracts.observability import AuditAction, AuditResult
from bridges.contracts.profiles import (
    PROFILE_DIMENSION_LABELS,
    ProfileDimension,
)
from bridges.learning.service import LearningService
from bridges.observability.service import ObservabilityService
from bridges.profiles.service import ProfileService

from .review import (
    STALE_AFTER_DAYS,
    apply_review_states,
    review_output,
)

CAREER_CAPABILITY_NAME = "qwen_structured_output"
CAREER_CAPABILITY_VERSION = "1"

#: 学习使命/知识状态进入证据的最小化上限（不整体上传学习域）。
_MAX_MISSIONS = 3
_MAX_STATES_PER_MISSION = 5

#: 摘要快照截断长度（不复制完整私人正文）。
_SNIPPET_MAX = 160

#: 账户能力/凭据/区域类错误 → 过程卡 permission 态。
_PERMISSION_ERROR_CODES = frozenset(
    {"auth_error", "capability_not_verified", "unregistered_capability", "region_error"}
)

#: 六类条目类别的证据状态标签（与 review 模块一致，供投影构造）。
_CATEGORY_KEYS = ("facts", "assumptions", "options", "risks", "path", "suggestions")

#: 可修复的模型调用失败码（格式类）：JSON 解析失败等属于模型输出
#: 质量问题，带修复指令重调一次有意义；鉴权/区域/限流等能力问题不修复。
_REPAIRABLE_CALL_CODES = frozenset({"structured_output_parse_failed"})


class CareerError(Exception):
    """生涯规划编排的可预期失败（由服务映射为五态过程卡与可恢复错误）。"""

    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class CareerPlannerService:
    """生涯规划编排：证据组装 → 结构化生成 → 确定性复核 → 投影。"""

    def __init__(
        self,
        gateway: ModelGateway,
        profile_service: ProfileService | None = None,
        learning_service: LearningService | None = None,
        observability_service: ObservabilityService | None = None,
    ) -> None:
        self._gateway = gateway
        self._profiles = profile_service
        self._learning = learning_service
        self._observability = observability_service

    # ------------------------------------------------------------------
    # 对外入口
    # ------------------------------------------------------------------

    def run_task(
        self,
        account_id: str,
        conversation_id: str,
        assistant_message_id: str,
        intent: str,
        *,
        mode: str,
        run_context: Any,
        profile_enabled: bool,
        profile_used: bool,
        profile_items: list[Any],
        retrieval_round: Any | None = None,
        web_search_projection: Any | None = None,
        arxiv_search_projection: Any | None = None,
        budget: RunBudget | None = None,
    ) -> Iterator[CareerRunEvent]:
        """执行一次生涯规划：yield 过程事件，最后 yield 结果事件。

        ``budget``（Issue 06 阶段预算）：传入时结构化输出的有界修复在
        剩余预算内进行，预算不足直接失败（不无限重试）。
        """
        progress: list[str] = []
        now = datetime.now(UTC)

        def process(
            state: CareerPlanningProcessState,
            label: str,
            *,
            detail: str | None = None,
            retryable: bool = False,
        ) -> CareerRunEvent:
            return CareerRunEvent(
                kind=CareerRunEventKind.PROCESS,
                state=state,
                step_label=label,
                detail=detail,
                retryable=retryable,
                progress_steps=list(progress),
            )

        try:
            # 步骤 1：编译证据集合（画像切片披露 + 学习记录 + 检索/联网/陈述）
            yield process(
                CareerPlanningProcessState.LOADING, "正在编译可用证据…"
            )
            evidence = self._build_evidence(
                account_id,
                mode,
                profile_items,
                intent,
                retrieval_round,
                web_search_projection,
                arxiv_search_projection,
                now,
            )
            progress.append("编译可用证据")
            if not _has_material_evidence(evidence):
                # 合法空态：无画像/学习记录/材料/联网结果时仍基于用户陈述
                # 回答，但过程卡进入 empty 态说明证据缺口。
                yield process(
                    CareerPlanningProcessState.EMPTY,
                    "没有可用画像与材料证据",
                    detail="本轮没有找到可用的画像、学习记录或检索材料，"
                    "回答将只基于你本次陈述，并明确标注需要核查的未知。",
                    retryable=False,
                )
            evidence_map = {source.evidence_id: source for source in evidence}

            # 步骤 2：按规划规则组装提示词并调用固定模型
            yield process(
                CareerPlanningProcessState.LOADING, "正在生成六类规划结果…"
            )
            output = self._invoke_and_parse(
                account_id,
                conversation_id,
                assistant_message_id,
                intent,
                mode,
                evidence,
                run_context,
                budget=budget,
            )
            progress.append("生成六类规划结果")

            # 步骤 3：确定性复核（承诺词边界/完整性门/事实证据门/引用核验/
            # 过时/冲突）并回写条目状态说明。
            yield process(
                CareerPlanningProcessState.LOADING, "正在复核证据与边界…"
            )
            review = review_output(output, evidence_map, now=now)
            apply_review_states(output, review, now=now)
            progress.append("复核证据与边界")
            if not review.passed:
                raise CareerError(
                    "career_boundary_violation",
                    "规划结果未通过边界复核（"
                    + "；".join(review.boundary_violations)
                    + "）。已停止交付，请修改问题后重试。",
                    retryable=False,
                )

            # 步骤 4：投影构造与审计
            projection = CareerPlanningProjection(
                plan_id=assistant_message_id,
                intent=_truncate(intent, 240),
                status=CareerPlanningStatus.DONE,
                profile_enabled=profile_enabled,
                profile_used=profile_used and bool(profile_items),
                verified_at=now,
                output=output,
                evidence_sources=evidence,
                review=review,
                process_state=CareerPlanningProcessState.DONE,
                process_steps=list(progress),
                error_code=None,
                error_message=None,
                created_at=now,
            )
            self._audit(
                account_id,
                conversation_id,
                assistant_message_id,
                projection,
                failed=False,
            )
            yield CareerRunEvent(
                kind=CareerRunEventKind.RESULT,
                step_label="生涯规划完成",
                result=projection,
            )
        except CareerError as exc:
            if exc.code in ("empty_intent",):
                state = CareerPlanningProcessState.EMPTY
            elif exc.code in _PERMISSION_ERROR_CODES:
                state = CareerPlanningProcessState.PERMISSION
            elif exc.retryable:
                state = CareerPlanningProcessState.RECOVERY
            else:
                state = CareerPlanningProcessState.ERROR
            failed = self._failed_projection(
                assistant_message_id,
                intent,
                mode,
                profile_enabled,
                profile_used,
                exc.code,
                exc.message,
                retryable=exc.retryable,
                process_steps=progress,
                state=state,
            )
            self._audit(
                account_id,
                conversation_id,
                assistant_message_id,
                failed,
                failed=True,
            )
            yield process(
                state,
                "规划未完成",
                detail=exc.message,
                retryable=exc.retryable,
            )
            yield CareerRunEvent(
                kind=CareerRunEventKind.RESULT,
                step_label="规划未完成",
                result=failed,
            )

    # ------------------------------------------------------------------
    # 证据编译
    # ------------------------------------------------------------------

    def _build_evidence(
        self,
        account_id: str,
        mode: str,
        profile_items: list[Any],
        user_statement: str,  # 即本轮用户消息正文（intent 同源）
        retrieval_round: Any | None,
        web_search_projection: Any | None,
        arxiv_search_projection: Any | None,
        now: datetime,
    ) -> list[CareerEvidenceSource]:
        """编译本轮证据集合（全部按当前账户授权，带核查时间）。"""
        evidence: list[CareerEvidenceSource] = []

        for item in profile_items:
            # 画像记录的核查时间取记录本身更新时间（用户数据可能确实过时：
            # 一年前确认的兴趣可能已变化），读取失败回退本轮时间。
            accessed_at = _assertion_updated_at(
                self._profiles, account_id, item.assertion_id, now
            )
            value_summary = getattr(item, "value_summary", None) or getattr(
                item, "value_or_rule", ""
            )
            version = getattr(item, "version", None)
            locator = f"画像记录 {item.assertion_id}"
            if version is not None:
                locator += f"（版本 {version}）"
            evidence.append(
                CareerEvidenceSource(
                    evidence_id=f"profile:{item.assertion_id}",
                    kind=CareerEvidenceKind.PROFILE_SLICE,
                    title=_dimension_label(item.dimension),
                    locator=locator,
                    summary=_snippet(value_summary),
                    accessed_at=accessed_at,
                )
            )

        if self._learning is not None:
            evidence.extend(
                self._learning_evidence(account_id, now)
            )

        if retrieval_round is not None:
            for citation in getattr(retrieval_round, "citations", []) or []:
                locator = ""
                if getattr(citation, "page_number", None) is not None:
                    locator += f"第 {citation.page_number} 页"
                if getattr(citation, "section_title", None):
                    locator += f"（{citation.section_title}）"
                evidence.append(
                    CareerEvidenceSource(
                        evidence_id=f"retrieval:{citation.citation_id}",
                        kind=CareerEvidenceKind.RETRIEVAL,
                        title=getattr(citation, "filename", "") or "本地材料",
                        locator=locator or None,
                        summary=_snippet(getattr(citation, "snippet", "")),
                        accessed_at=getattr(retrieval_round, "created_at", now),
                    )
                )

        if web_search_projection is not None:
            for index, result in enumerate(
                getattr(web_search_projection, "results", []) or []
            ):
                evidence.append(
                    CareerEvidenceSource(
                        evidence_id=f"web:{index}",
                        kind=CareerEvidenceKind.WEB_SEARCH,
                        title=getattr(result, "title", "") or "联网来源",
                        locator=getattr(result, "url", None),
                        url=getattr(result, "url", None),
                        summary=_snippet(getattr(result, "snippet", "")),
                        accessed_at=(
                            getattr(result, "accessed_at", None) or now
                        ),
                    )
                )

        if arxiv_search_projection is not None:
            for index, paper in enumerate(
                getattr(arxiv_search_projection, "papers", []) or []
            ):
                evidence.append(
                    CareerEvidenceSource(
                        evidence_id=f"arxiv:{index}",
                        kind=CareerEvidenceKind.ARXIV,
                        title=getattr(paper, "title", "") or "arXiv 论文",
                        locator=getattr(paper, "arxiv_id", None),
                        url=getattr(paper, "abs_url", None),
                        summary=_snippet(
                            getattr(paper, "summary", "")
                            or getattr(paper, "abstract", "")
                        ),
                        accessed_at=(
                            getattr(arxiv_search_projection, "created_at", None)
                            or now
                        ),
                    )
                )

        statement = user_statement.strip()
        if statement:
            evidence.append(
                CareerEvidenceSource(
                    evidence_id="statement:current",
                    kind=CareerEvidenceKind.USER_STATEMENT,
                    title="用户本次陈述",
                    summary=_snippet(statement),
                    accessed_at=now,
                )
            )

        # 过时标记：超过新鲜度阈值的来源由复核标记为 outdated。
        for source in evidence:
            if (now - source.accessed_at).days > STALE_AFTER_DAYS:
                source.stale = True
        return evidence

    def _learning_evidence(
        self, account_id: str, now: datetime
    ) -> list[CareerEvidenceSource]:
        """读取当前账户学习使命与知识状态为证据（最小化上限，失败降级为空）。"""
        learning = self._learning
        if learning is None:
            return []
        evidence: list[CareerEvidenceSource] = []
        try:
            missions = learning.list_missions(account_id)
        except Exception:  # noqa: BLE001 - 学习域不可用按无学习记录处理
            return evidence
        for mission in missions[:_MAX_MISSIONS]:
            goal = getattr(mission, "goal", "") or ""
            evidence.append(
                CareerEvidenceSource(
                    evidence_id=f"learning:{mission.mission_id}",
                    kind=CareerEvidenceKind.LEARNING_RECORD,
                    title=(
                        f"学习使命：{getattr(mission, 'title', '') or '未命名'}"
                    ),
                    summary=_snippet(goal),
                    # 核查时间取使命更新时间（久未更新的使命可能已不反映现状）。
                    accessed_at=(
                        getattr(mission, "updated_at", None) or now
                    ),
                )
            )
            try:
                states = learning.list_knowledge_states(
                    account_id, mission.mission_id
                )
            except Exception:  # noqa: BLE001 - 单个使命的知识状态不可读不影响其余
                continue
            for state in states[:_MAX_STATES_PER_MISSION]:
                evidence.append(
                    CareerEvidenceSource(
                        evidence_id=f"learning:state:{state.state_id}",
                        kind=CareerEvidenceKind.LEARNING_RECORD,
                        title=f"知识状态：{getattr(state, 'concept_id', '') or '未命名概念'}",
                        summary=_snippet(
                            f"掌握程度 {getattr(state, 'status', 'unknown')}；"
                            f"置信 {getattr(state, 'confidence', 'unknown')}。"
                        ),
                        accessed_at=(
                            getattr(state, "updated_at", None) or now
                        ),
                    )
                )
        return evidence

    # ------------------------------------------------------------------
    # 模型调用
    # ------------------------------------------------------------------

    def _invoke_and_parse(
        self,
        account_id: str,
        conversation_id: str,
        assistant_message_id: str,
        intent: str,
        mode: str,
        evidence: list[CareerEvidenceSource],
        run_context: Any,
        budget: RunBudget | None = None,
    ) -> CareerPlanningOutputContract:
        intent_text = intent.strip()
        if not intent_text:
            raise CareerError(
                "empty_intent", "缺少生涯问题：请说明你想规划的学业或职业方向。",
                retryable=True,
            )
        system_prompt = _build_system_prompt(mode, evidence)
        user_prompt = _build_user_prompt(intent_text, evidence)
        output, failure = self._invoke_structured(
            run_context, system_prompt, user_prompt
        )
        if failure is None:
            return output
        # 一次有界修复（Issue 09 实施步骤 3）：结构化输出非法/格式错误时
        # 只带修复指令重调一次；剩余预算不足承担第二次调用时在总预算内
        # 失败，绝不做无限重试、不把 transport 断开映射成领域失败。
        if budget is not None and not budget.can_retry():
            raise CareerError(
                "career_output_invalid",
                "规划结果未通过结构校验，且剩余预算不足，无法修复；"
                "请重试（输入已保留）。",
                retryable=True,
            )
        output, failure = self._invoke_structured(
            run_context,
            system_prompt,
            _build_repair_user_prompt(user_prompt, output, failure),
        )
        if failure is not None:
            raise CareerError(
                "career_output_invalid",
                "规划结果连续未通过结构校验；请修改问题表述后重试（输入已保留）。",
                retryable=True,
            )
        return output

    def _invoke_structured(
        self,
        run_context: Any,
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[CareerPlanningOutputContract, str | None]:
        """调用固定结构化模型，返回 (输出, 可修复失败原因)。

        - 成功/DEGRADED：宽容解析，结构非法时 ``failure`` 非空；
        - 格式类失败（如 JSON 解析失败，``structured_output_parse_failed``）：
          作为可修复失败返回（第二次调用带修复指令）；
        - 其余能力类失败（鉴权/区域/限流等）：按现有分类直接抛
          ``CareerError``，不做修复（能力问题修复无意义）。
        """
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
            CAREER_CAPABILITY_NAME,
            CAREER_CAPABILITY_VERSION,
            run_context,
            payload,
        )
        if call_result.status in (
            ModelCallStatus.SUCCESS,
            ModelCallStatus.DEGRADED,
        ):
            output = self._coerce_output(call_result.output or {})
            if _output_is_invalid(output):
                return output, "输出未通过结构校验"
            return output, None
        if call_result.error_code in _REPAIRABLE_CALL_CODES:
            return (
                self._coerce_output(call_result.output or {}),
                call_result.error_message
                or "模型输出不是合法 JSON，无法解析。",
            )
        raise CareerError(
            call_result.error_code or "career_generation_failed",
            call_result.error_message or "生涯规划生成失败，请重试（输入已保留）。",
            retryable=call_result.status == ModelCallStatus.RETRYABLE_FAIL,
        )

    def _coerce_output(self, raw: dict[str, Any]) -> CareerPlanningOutputContract:
        """把模型输出收窄为输出合同；结构异常按缺项处理并交复核判定。"""
        return CareerPlanningOutputContract(
            final_text=str(raw.get("final_text") or "").strip(),
            facts=_coerce_items(raw.get("facts"), "fact", CareerFact),
            assumptions=_coerce_items(raw.get("assumptions"), "assumption", CareerAssumption),
            options=_coerce_items(raw.get("options"), "option", CareerOption),
            risks=_coerce_items(raw.get("risks"), "risk", CareerRisk),
            path=_coerce_items(raw.get("path"), "stage", CareerStage),
            suggestions=_coerce_items(raw.get("suggestions"), "suggestion", CareerSuggestion),
            boundary_statement=str(raw.get("boundary_statement") or "").strip(),
            open_questions=[
                str(q) for q in (raw.get("open_questions") or []) if str(q).strip()
            ],
        )

    # ------------------------------------------------------------------
    # 投影与审计
    # ------------------------------------------------------------------

    def _failed_projection(
        self,
        assistant_message_id: str,
        intent: str,
        mode: str,
        profile_enabled: bool,
        profile_used: bool,
        error_code: str,
        error_message: str,
        *,
        retryable: bool,
        process_steps: list[str],
        state: CareerPlanningProcessState,
    ) -> CareerPlanningProjection:
        return CareerPlanningProjection(
            plan_id=assistant_message_id,
            intent=_truncate(intent, 240),
            status=CareerPlanningStatus.ERROR,
            profile_enabled=profile_enabled,
            profile_used=profile_used,
            verified_at=datetime.now(UTC),
            output=None,
            evidence_sources=[],
            review=None,
            process_state=state,
            process_steps=list(process_steps),
            error_code=error_code,
            error_message=_safe_alternative(error_code, error_message, retryable),
            created_at=datetime.now(UTC),
        )

    def _audit(
        self,
        account_id: str,
        conversation_id: str,
        assistant_message_id: str,
        projection: CareerPlanningProjection,
        *,
        failed: bool,
    ) -> None:
        if self._observability is None:
            return
        item_counts: dict[str, int] = {}
        if projection.output is not None:
            for key in _CATEGORY_KEYS:
                item_counts[key] = len(getattr(projection.output, key))
        profile_refs = [
            source.evidence_id
            for source in projection.evidence_sources
            if source.kind == CareerEvidenceKind.PROFILE_SLICE
        ]
        self._observability.log_audit(
            actor_account_id=account_id,
            action=AuditAction.CAREER_PLANNING_GENERATED,
            result=AuditResult.BLOCKED if failed else AuditResult.SUCCESS,
            object_refs=[assistant_message_id],
            reason="生涯规划生成（不含正文，仅条目数与证据摘要）。",
            details={
                "item_counts": item_counts,
                "evidence_count": len(projection.evidence_sources),
                "profile_refs": profile_refs,
                "profile_used": projection.profile_used,
                "verified_at": projection.verified_at.isoformat(),
            },
        )


def _has_material_evidence(evidence: list[CareerEvidenceSource]) -> bool:
    """是否有画像/学习记录/检索/联网等材料证据（用户陈述不算材料）。"""
    return any(
        source.kind != CareerEvidenceKind.USER_STATEMENT for source in evidence
    )


def _output_is_invalid(output: CareerPlanningOutputContract) -> bool:
    """结构校验：正文/边界声明缺失或六类结果全空视为非法（可修复）。

    与复核的完整性门同一语义，但提前到模型调用层作为「有界修复」的
    触发条件：只有结构层面不可交付才重调一次；单类条目缺失仍由宽容
    解析与复核处理，不扩大修复范围。
    """
    if not output.final_text.strip():
        return True
    if not output.boundary_statement.strip():
        return True
    return not any(getattr(output, key) for key in _CATEGORY_KEYS)


def _build_repair_user_prompt(
    user_prompt: str,
    output: CareerPlanningOutputContract | None,
    failure: str,
) -> str:
    """构造一次修复的提示词：原问题 + 上次输出/错误 + 确定性失败原因。"""
    if output is None:
        # 格式类失败（JSON 解析失败等）：无上次输出可附，只给原因
        reasons = [failure or "输出不是合法 JSON"]
    else:
        reasons = []
        if not output.final_text.strip():
            reasons.append("缺少自然中文正文（final_text）")
        if not output.boundary_statement.strip():
            reasons.append("缺少保证边界声明（boundary_statement）")
        if not any(getattr(output, key) for key in _CATEGORY_KEYS):
            reasons.append(
                "六类结果（facts/assumptions/options/risks/path/suggestions）全为空"
            )
        if not reasons:
            reasons.append(failure or "输出未通过结构校验")
    return (
        f"{user_prompt}\n\n【修复要求】上一次输出未通过结构校验："
        f"{'、'.join(reasons)}。请严格按照输出要求重新输出完整 JSON，"
        "不要重复上次的错误，不要输出 JSON 之外的任何内容。"
    )


def _dimension_label(dimension: str) -> str:
    """画像维度中文标签；未知维度直接回退原始值，不抛错。"""
    try:
        return PROFILE_DIMENSION_LABELS[ProfileDimension(dimension)]
    except ValueError:
        return dimension


def _assertion_updated_at(
    profiles: ProfileService | None,
    account_id: str,
    assertion_id: str,
    fallback: datetime,
) -> datetime:
    """读取画像记录的更新时间作为核查时间（失败回退本轮时间）。"""
    if profiles is None:
        return fallback
    try:
        assertion = profiles.get_assertion(account_id, assertion_id)
    except Exception:  # noqa: BLE001 - 披露项尽力而为
        return fallback
    return getattr(assertion, "updated_at", None) or fallback


def _snippet(text: str, max_chars: int = _SNIPPET_MAX) -> str | None:
    """截断摘要；空文本返回 None。"""
    value = (text or "").strip()
    if not value:
        return None
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 1] + "…"


def _truncate(text: str, max_chars: int) -> str:
    value = (text or "").strip()
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 1] + "…"




def _coerce_items(raw: Any, prefix: str, model: type[Any]) -> list[Any]:
    """宽容解析模型输出的六类条目数组（每类独立前缀生成稳定 item_id）。"""
    items: list[Any] = []
    if not isinstance(raw, list):
        return items
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        kwargs: dict[str, Any] = {
            "item_id": f"{prefix}:{index}",
            "content": content,
            "evidence_refs": _coerce_refs(item.get("evidence_refs")),
            "note": _opt_str(item.get("note")),
        }
        for key, target in (
            ("verification_next_step", "verification_next_step"),
            ("rationale", "rationale"),
            ("trigger", "trigger"),
            ("timeline", "timeline"),
            ("verification", "verification"),
        ):
            value = _opt_str(item.get(key))
            if value is not None:
                kwargs[target] = value
        items.append(model(**kwargs))
    return items


def _coerce_refs(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(ref).strip() for ref in raw if str(ref).strip()]


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _safe_alternative(code: str, message: str, retryable: bool) -> str:
    """失败时给用户可操作的安全替代步骤（不输出模板化假成功）。"""
    base = message or "生涯规划未完成。"
    if code == "career_boundary_violation":
        return (
            base + " 你可以修改问题表述后重新发送；规划不会包含任何就业、"
            "薪酬或录取保证。"
        )
    if retryable:
        return base + " 可直接重试；也可改用普通对话继续追问。"
    return base


# ----------------------------------------------------------------------
# 提示词与输出合同（确定性控制平面的一部分，不随模型自述漂移）
# ----------------------------------------------------------------------

_OUTPUT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "final_text": {"type": "string"},
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "evidence_refs": {"type": "array", "items": {"type": "string"}},
                    "note": {"type": "string"},
                },
                "required": ["content"],
            },
        },
        "assumptions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "evidence_refs": {"type": "array", "items": {"type": "string"}},
                    "note": {"type": "string"},
                    "verification_next_step": {"type": "string"},
                },
                "required": ["content"],
            },
        },
        "options": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "evidence_refs": {"type": "array", "items": {"type": "string"}},
                    "note": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["content"],
            },
        },
        "risks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "evidence_refs": {"type": "array", "items": {"type": "string"}},
                    "note": {"type": "string"},
                    "trigger": {"type": "string"},
                },
                "required": ["content"],
            },
        },
        "path": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "evidence_refs": {"type": "array", "items": {"type": "string"}},
                    "note": {"type": "string"},
                    "timeline": {"type": "string"},
                },
                "required": ["content"],
            },
        },
        "suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "evidence_refs": {"type": "array", "items": {"type": "string"}},
                    "note": {"type": "string"},
                    "verification": {"type": "string"},
                },
                "required": ["content"],
            },
        },
        "boundary_statement": {"type": "string"},
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "final_text",
        "facts",
        "assumptions",
        "options",
        "risks",
        "path",
        "suggestions",
        "boundary_statement",
        "open_questions",
    ],
}

_EVIDENCE_KIND_CN: dict[str, str] = {
    "profile_slice": "画像切片",
    "learning_record": "学习记录",
    "retrieval": "本地材料",
    "web_search": "联网来源",
    "arxiv": "arXiv 论文",
    "user_statement": "用户陈述",
}


def _external_evidence_note(evidence: list[CareerEvidenceSource]) -> str:
    """外部证据缺失提示（Issue 09 实施步骤 4）：不伪造事实、交付骨架。

    本轮没有联网/论文/本地材料等外部证据时，明确要求模型不编造外部
    事实：涉及行业趋势、岗位要求、资格等内容的表述必须作为假设并给出
    核查方式，或在 open_questions 中列为待核实项。
    """
    has_external = any(
        source.kind
        in (
            CareerEvidenceKind.WEB_SEARCH,
            CareerEvidenceKind.ARXIV,
            CareerEvidenceKind.RETRIEVAL,
        )
        for source in evidence
    )
    if has_external:
        return ""
    return (
        "【外部证据缺失】本轮没有联网/论文/本地材料证据：涉及行业趋势、"
        "岗位要求、资格等会变化的外部事实不得写入 facts，必须作为假设并"
        "给出核查方式，或在 open_questions 中标为待核实项；规划骨架只"
        "基于用户自述与授权画像/学习记录。"
    )


def _evidence_lines(evidence: list[CareerEvidenceSource]) -> str:
    """证据合同清单：只可引用清单内材料，并带核查时间。"""
    lines: list[str] = []
    for source in evidence:
        stamp = source.accessed_at.strftime("%Y-%m-%d")
        stale = "（已过时，需复核）" if source.stale else ""
        locator = f"｜{source.locator}" if source.locator else ""
        kind_cn = _EVIDENCE_KIND_CN.get(source.kind.value, source.kind.value)
        lines.append(
            f"- [{source.evidence_id}] {kind_cn}：{source.title}{locator}"
            f"（核查于 {stamp}）{stale}"
        )
    return "\n".join(lines) or "（无：只能依赖用户本次陈述，且必须明确标注未知项）"


def _build_system_prompt(mode: str, evidence: list[CareerEvidenceSource]) -> str:
    """生涯规划系统提示词：任务边界 + 证据合同 + 六类输出要求 + 人味化约束。

    六类划分、事实与推测分离、边界禁令都是确定性合同；模型只负责在
    合同内生成，不自行扩大授权或改变输出结构。
    """
    mode_cn = "学习模式" if mode == "study" else "日常陪伴"
    return f"""你是 BridGes 的生涯规划助手。当前对话模式：{mode_cn}。你帮助用户在
科学学习与成长背景下思考学业/职业方向，输出结构化且自然的规划结果。

【任务边界】
- 只使用【证据合同】列出的材料：用户授权画像切片、学习记录、用户陈述、
  本地检索引用、联网来源与 arXiv 论文。不得虚构来源、数据、岗位信息或引用。
- 输出固定六类：已知事实（facts）、待验证假设（assumptions）、可选方向
  （options）、关键风险（risks）、分阶段成长路径（path）、近期学习建议
  （suggestions），并给出自然中文正文（final_text）与保证边界声明
  （boundary_statement）。
- 事实与推测分离：只有带证据引用的内容才能进入 facts；证据不足时归入
  assumptions 并给出下一步核查方式，或明确说明未知（open_questions）。
- 涉及岗位、教育路径、资格、行业趋势等会变化的信息时，每条必须引用
  证据合同内的来源（evidence_refs）；引用多个来源时在 note 中说明证据间
  关系（一致/差异/冲突）。
- 表达受 BridGes 人味化规则约束：自然、有分寸、面向用户真实情境，保留
  限定条件；不得模板化堆砌列表腔，不得以自然为代价牺牲事实与边界。
- 硬性禁止：不作就业、薪酬或录取保证；不基于单次情绪、敏感身份猜测或
  未确认画像作稳定职业判断；不提供招聘撮合、职位投递、录取预测或执业
  资格认证；不冒充持证职业顾问。
- 未知优先：宁可明确说「目前没有足够依据」，也不编造确定结论。

【证据合同（只可引用清单内材料，不得虚构）】
{_evidence_lines(evidence)}

{_external_evidence_note(evidence)}

【可执行性要求】至少一条近期学习建议（suggestions）是未来 7 天可
执行的具体行动（如选课、投递实习、约谈导师、做一次调研），并给出
验证方式；成长路径（path）按时间先后排列，最后一步给出可检查的
里程碑（明确目标与时间点）。

【输出要求】严格输出 JSON，不得输出 JSON 之外的任何内容：
{{"final_text": 自然中文正文, "facts": [{{"content", "evidence_refs", "note"}}],
"assumptions": [{{"content", "evidence_refs", "note", "verification_next_step"}}],
"options": [{{"content", "evidence_refs", "note", "rationale"}}],
"risks": [{{"content", "evidence_refs", "note", "trigger"}}],
"path": [{{"content", "evidence_refs", "note", "timeline"}}],
"suggestions": [{{"content", "evidence_refs", "note", "verification"}}],
"boundary_statement": 保证边界声明, "open_questions": [未决问题与核查方式]}}"""


def _build_user_prompt(intent: str, evidence: list[CareerEvidenceSource]) -> str:
    profile_lines: list[str] = []
    learning_lines: list[str] = []
    for source in evidence:
        if source.kind == CareerEvidenceKind.PROFILE_SLICE:
            profile_lines.append(
                f"- {source.title}：{source.summary or '（无摘要）'}"
            )
        elif source.kind == CareerEvidenceKind.LEARNING_RECORD:
            learning_lines.append(
                f"- {source.title}：{source.summary or '（无摘要）'}"
            )
    parts = [
        f"用户的问题：{intent}",
        "已授权画像切片（只使用这些最小记录，不得推断更多）：\n"
        + ("\n".join(profile_lines) if profile_lines else "（本轮未使用画像或没有相关记录）"),
        "学习记录（使命与知识状态）：\n"
        + ("\n".join(learning_lines) if learning_lines else "（无）"),
    ]
    return "\n\n".join(parts)


__all__ = [
    "CareerPlannerService",
    "CareerRunEvent",
    "CareerError",
    "CAREER_CAPABILITY_NAME",
    "CAREER_CAPABILITY_VERSION",
]
