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

import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from bridges.ai.errors import ModelRunLockConflictError, ModelRunLockError
from bridges.ai.model_gateway import ModelGateway
from bridges.ai.ports import ModelRunLockRecorder, RecordRequest
from bridges.career.metrics import NOOP_CAREER_LOCK_METRICS, CareerLockMetrics
from bridges.chat.budget import RunBudget
from bridges.chat.global_writing_policy import GlobalWritingPolicySnapshot
from bridges.contracts.ai import BusinessRef, ModelCallStatus, ModelRunLock
from bridges.contracts.career import (
    CareerAssumption,
    CareerEvidenceKind,
    CareerEvidenceSource,
    CareerFact,
    CareerLockRef,
    CareerOption,
    CareerPlanningOutputContract,
    CareerPlanningProcessState,
    CareerPlanningProjection,
    CareerPlanningRouteContract,
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
from bridges.contracts.workflows import RunContextEnvelope
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

#: 运行锁业务关联：锁链接到规划（以助手消息为对象 ID）与会话，两者都
#: 按账户隔离；operation 是稳定阶段名，attempt_ordinal 是同 run 内真实
#: 供应商调用的稳定序号。本地宽容解析、确定性复核与投影构造不产生锁。
CAREER_LOCK_OBJECT_TYPE = "career_plan"
CAREER_LOCK_CONVERSATION_OBJECT_TYPE = "conversation"
CAREER_OPERATION_GENERATION = "career_generation"
CAREER_OPERATION_REPAIR = "career_repair"
CAREER_GENERATION_ORDINAL = 1
CAREER_REPAIR_ORDINAL = 2

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


@dataclass(frozen=True)
class _CallStage:
    """一次真实供应商调用的稳定阶段与序号（generation:1 → repair:2）。

    本地宽容解析、确定性复核与投影构造没有阶段，不产生模型锁。
    """

    operation: str
    attempt_ordinal: int

    @classmethod
    def generation(cls) -> _CallStage:
        return cls(CAREER_OPERATION_GENERATION, CAREER_GENERATION_ORDINAL)

    @classmethod
    def repair(cls) -> _CallStage:
        return cls(CAREER_OPERATION_REPAIR, CAREER_REPAIR_ORDINAL)


class CareerPlannerService:
    """生涯规划编排：证据组装 → 结构化生成 → 确定性复核 → 投影。"""

    def __init__(
        self,
        gateway: ModelGateway,
        profile_service: ProfileService | None = None,
        learning_service: LearningService | None = None,
        observability_service: ObservabilityService | None = None,
        run_lock_recorder: ModelRunLockRecorder | None = None,
        lock_metrics: CareerLockMetrics | None = None,
    ) -> None:
        self._gateway = gateway
        self._profiles = profile_service
        self._learning = learning_service
        self._observability = observability_service
        #: Issue 10 统一持久化端口：None 时锁只进入投影引用（评估/替身
        #: 环境），生产接线必须注入真实 recorder，缺审计证据即失败关闭。
        self._run_lock_recorder = run_lock_recorder
        #: Issue 12 Observability：按阶段/模型状态/稳定错误码聚合调用数、
        #: 锁数、缺锁数、延迟与 usage；不采集提示词或规划内容。
        self._lock_metrics = lock_metrics or NOOP_CAREER_LOCK_METRICS

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
        run_context: RunContextEnvelope,
        profile_enabled: bool,
        profile_used: bool,
        profile_items: list[Any],
        retrieval_round: Any | None = None,
        web_search_projection: Any | None = None,
        arxiv_search_projection: Any | None = None,
        route_contract: CareerPlanningRouteContract | None = None,
        budget: RunBudget | None = None,
        writing_policy: GlobalWritingPolicySnapshot | None = None,
    ) -> Iterator[CareerRunEvent]:
        """执行一次生涯规划：yield 过程事件，最后 yield 结果事件。

        ``budget``（Issue 06 阶段预算）：传入时结构化输出的有界修复在
        剩余预算内进行，预算不足直接失败（不无限重试）。
        """
        progress: list[str] = []
        now = datetime.now(UTC)
        #: 本轮真实供应商调用的锁引用（由本方法持有：即使后续抛出
        #: CareerError 也能在失败投影中保留已发生调用的锁引用）。
        lock_refs: list[CareerLockRef] = []

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
                writing_policy=writing_policy,
                route_contract=route_contract,
                budget=budget,
                lock_refs=lock_refs,
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
                route_contract=route_contract,
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
                run_id=run_context.run_id,
                run_lock_refs=list(lock_refs),
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
                route_contract=route_contract,
                run_id=run_context.run_id,
                run_lock_refs=list(lock_refs),
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
        run_context: RunContextEnvelope,
        writing_policy: GlobalWritingPolicySnapshot | None = None,
        route_contract: CareerPlanningRouteContract | None = None,
        budget: RunBudget | None = None,
        lock_refs: list[CareerLockRef] | None = None,
    ) -> CareerPlanningOutputContract:
        intent_text = intent.strip()
        if not intent_text:
            raise CareerError(
                "empty_intent", "缺少生涯问题：请说明你想规划的学业或职业方向。",
                retryable=True,
            )
        system_prompt = _build_system_prompt(mode, evidence)
        if writing_policy is not None:
            system_prompt = f"{system_prompt}\n\n{writing_policy.system_block}"
        user_prompt = _build_user_prompt(intent_text, evidence, route_contract)
        output, failure = self._invoke_structured(
            run_context,
            system_prompt,
            user_prompt,
            writing_policy,
            account_id=account_id,
            conversation_id=conversation_id,
            assistant_message_id=assistant_message_id,
            stage=_CallStage.generation(),
            lock_refs=lock_refs,
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
        # 修复序号守门：只有首次生成锁已持久化才允许发起第二次真实模型
        # 调用（Issue 12：稳定阶段 career_generation:1 → career_repair:2）。
        self._assert_repair_sequence_allowed(account_id, assistant_message_id)
        output, failure = self._invoke_structured(
            run_context,
            system_prompt,
            _build_repair_user_prompt(user_prompt, output, failure),
            writing_policy,
            account_id=account_id,
            conversation_id=conversation_id,
            assistant_message_id=assistant_message_id,
            stage=_CallStage.repair(),
            lock_refs=lock_refs,
        )
        if failure is not None:
            raise CareerError(
                "career_output_invalid",
                "规划结果连续未通过结构校验；请修改问题表述后重试（输入已保留）。",
                retryable=True,
            )
        return output

    def _assert_repair_sequence_allowed(
        self, account_id: str, assistant_message_id: str
    ) -> None:
        """修复调用前的序号守门：必须已存在本次规划的 generation:1 锁。

        任何路径都不得在缺少首次生成锁时发起第二次真实供应商调用；本地
        宽容解析、确定性复核和投影构造不产生锁，因此不存在其他合法来源。
        """
        if self._run_lock_recorder is None:
            return
        recorded = self._run_lock_recorder.list_locks_by_business_ref(
            account_id, CAREER_LOCK_OBJECT_TYPE, assistant_message_id
        )
        if not any(
            ref.operation == CAREER_OPERATION_GENERATION
            and ref.attempt_ordinal == CAREER_GENERATION_ORDINAL
            for lock in recorded
            for ref in lock.business_refs
        ):
            self._lock_metrics.record_sequence_mismatch()
            raise CareerError(
                "career_call_sequence_mismatch",
                "生涯规划调用序号异常：缺少首次生成锁，已停止修复。",
                retryable=False,
            )

    def _invoke_structured(
        self,
        run_context: RunContextEnvelope,
        system_prompt: str,
        user_prompt: str,
        writing_policy: GlobalWritingPolicySnapshot | None = None,
        *,
        account_id: str,
        conversation_id: str,
        assistant_message_id: str,
        stage: _CallStage,
        lock_refs: list[CareerLockRef] | None = None,
    ) -> tuple[CareerPlanningOutputContract, str | None]:
        """调用固定结构化模型，返回 (输出, 可修复失败原因)。

        - 每次真实网关调用后立即持久化返回锁（Issue 10 统一 recorder，
          幂等重放；缺锁或持久化失败时失败关闭，不得把无审计证据的
          结果提升为完成态）；
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
        if writing_policy is not None:
            payload["global_writing_policy"] = writing_policy.metadata()
        started = time.monotonic()
        call_result = self._gateway.invoke(
            CAREER_CAPABILITY_NAME,
            CAREER_CAPABILITY_VERSION,
            run_context,
            payload,
        )
        duration_ms = max(0, int((time.monotonic() - started) * 1000))
        # 每次真实调用都计入指标（含失败与缺锁，按阶段与模型状态聚合）。
        self._lock_metrics.record_call(
            operation=stage.operation,
            status=call_result.status.value,
            duration_ms=duration_ms,
            usage=(
                call_result.lock.usage if call_result.lock is not None else None
            ),
        )
        self._persist_call_lock(
            call_result.lock,
            run_context=run_context,
            account_id=account_id,
            conversation_id=conversation_id,
            assistant_message_id=assistant_message_id,
            stage=stage,
            lock_refs=lock_refs,
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

    def _persist_call_lock(
        self,
        lock: ModelRunLock | None,
        *,
        run_context: RunContextEnvelope,
        account_id: str,
        conversation_id: str,
        assistant_message_id: str,
        stage: _CallStage,
        lock_refs: list[CareerLockRef] | None = None,
    ) -> None:
        """每次结构化调用后立即、幂等持久化返回锁（Issue 12 接线）。

        失败关闭语义：
        - 网关未返回锁（理论不可达的防御）→ ``career_missing_run_lock``；
        - 锁的账户/项目/run 标识与当前业务 run 不一致（跨账户完整性
          防御）→ ``career_call_sequence_mismatch``；
        - 同一锁 ID 内容冲突（审计完整性异常）→
          ``career_call_sequence_mismatch``；
        - recorder 持久化失败 → ``career_lock_persist_failed``，业务终态
          不得覆盖模型运行状态，规划不进入完成态。
        锁关联规划（助手消息，主要锁）与会话两条业务引用；完整锁、提示词
        与规划正文绝不进入投影或审计。
        """
        if lock is None:
            self._lock_metrics.record_missing_run_lock()
            raise CareerError(
                "career_missing_run_lock",
                "模型调用未返回运行锁，生涯规划已停止（输入已保留）。",
                retryable=False,
            )
        if (
            lock.run_id != run_context.run_id
            or lock.account_id != run_context.account_id
            or lock.project_id != run_context.project_id
        ):
            self._lock_metrics.record_sequence_mismatch()
            raise CareerError(
                "career_call_sequence_mismatch",
                "模型调用与业务 run 关联异常，生涯规划已停止。",
                retryable=False,
            )
        if self._run_lock_recorder is None:
            # 评估/替身环境（无 recorder）：锁引用仍进入投影，不丢弃；
            # 指标标记该组合没有持久化审计证据，供运营识别。
            self._lock_metrics.record_recorder_missing()
            self._collect_lock_ref(lock, stage, lock_refs)
            return
        try:
            self._run_lock_recorder.record_many(
                [
                    RecordRequest(
                        lock=lock,
                        business_ref=BusinessRef(
                            object_type=CAREER_LOCK_OBJECT_TYPE,
                            object_id=assistant_message_id,
                            operation=stage.operation,
                            attempt_ordinal=stage.attempt_ordinal,
                            is_primary=(
                                stage.attempt_ordinal == CAREER_GENERATION_ORDINAL
                            ),
                        ),
                    ),
                    RecordRequest(
                        lock=lock,
                        business_ref=BusinessRef(
                            object_type=CAREER_LOCK_CONVERSATION_OBJECT_TYPE,
                            object_id=conversation_id,
                            operation=stage.operation,
                            attempt_ordinal=stage.attempt_ordinal,
                            is_primary=False,
                        ),
                    ),
                ]
            )
        except ModelRunLockConflictError as exc:
            # 相同 lock_id 不同内容：审计完整性异常，按序号异常失败关闭。
            self._lock_metrics.record_sequence_mismatch()
            raise CareerError(
                "career_call_sequence_mismatch",
                "生涯规划调用序号异常：运行锁内容冲突，已停止。",
                retryable=False,
            ) from exc
        except ModelRunLockError as exc:
            self._lock_metrics.record_persist_failed()
            raise CareerError(
                "career_lock_persist_failed",
                "模型调用审计记录写入失败，生涯规划未完成（输入已保留）。",
                retryable=False,
            ) from exc
        # 持久化成功后才记入投影引用：引用永远指向可查询的锁。
        self._collect_lock_ref(lock, stage, lock_refs)

    @staticmethod
    def _collect_lock_ref(
        lock: ModelRunLock,
        stage: _CallStage,
        lock_refs: list[CareerLockRef] | None,
    ) -> None:
        if lock_refs is None:
            return
        lock_refs.append(
            CareerLockRef(
                lock_id=lock.lock_id,
                operation=stage.operation,
                attempt_ordinal=stage.attempt_ordinal,
            )
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
        route_contract: CareerPlanningRouteContract | None = None,
        run_id: str | None = None,
        run_lock_refs: list[CareerLockRef] | None = None,
    ) -> CareerPlanningProjection:
        return CareerPlanningProjection(
            plan_id=assistant_message_id,
            intent=_truncate(intent, 240),
            route_contract=route_contract,
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
            run_id=run_id,
            run_lock_refs=list(run_lock_refs or []),
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
                # 锁审计证据只含稳定引用（锁 ID/阶段/序号）与业务 run，
                # 绝不包含提示词、规划正文、画像内容或完整模型响应。
                "run_id": projection.run_id,
                "lock_refs": [
                    {
                        "lock_id": ref.lock_id,
                        "operation": ref.operation,
                        "attempt_ordinal": ref.attempt_ordinal,
                    }
                    for ref in projection.run_lock_refs
                ],
                # AC6：首次结构无效并真实发起修复时，领域审计另行记录
                # repair 触发事实（模型锁仍如实记录调用成功，不改写）。
                "repair_triggered": any(
                    ref.operation == CAREER_OPERATION_REPAIR
                    for ref in projection.run_lock_refs
                ),
                "error_code": projection.error_code,
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


def _build_user_prompt(
    intent: str,
    evidence: list[CareerEvidenceSource],
    route_contract: CareerPlanningRouteContract | None = None,
) -> str:
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
        (
            "路由编译的规划合同（必须遵守，未决问题不得假装已解决）：\n"
            f"目标：{route_contract.target}\n"
            f"时间范围：{route_contract.time_horizon}\n"
            f"约束：{'；'.join(route_contract.constraints) or '未明确'}\n"
            f"允许证据：{'、'.join(route_contract.evidence_requirements)}\n"
            f"图片使用：{route_contract.image_usage}\n"
            f"未决问题：{'；'.join(route_contract.open_questions) or '无'}"
            if route_contract is not None
            else "本轮没有额外的路由合同；仍须遵守生涯规划边界。"
        ),
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
    "CAREER_LOCK_OBJECT_TYPE",
    "CAREER_LOCK_CONVERSATION_OBJECT_TYPE",
    "CAREER_OPERATION_GENERATION",
    "CAREER_OPERATION_REPAIR",
    "CAREER_GENERATION_ORDINAL",
    "CAREER_REPAIR_ORDINAL",
]
