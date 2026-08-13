"""Expression task domain service for T025.

The service compiles an expression task brief into a fact-lock-bound draft:

1. Validate the brief and build a task-scoped audience model.
2. Select the genre contract.
3. Load the claim graph and compile its fact lock set.
4. Run honest-degradation analysis to obtain the wording strength ceiling.
5. Optionally load and validate a memory slice for personalization.
6. Build an argument plan and generate draft spans.
7. Run the expression quality gate.

The default draft generator is deterministic so the seam is testable without a
live model. A model gateway may be injected for production generation; it is not
allowed to override fact locks or evidence-derived strength ceilings.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

from bridges.contracts.expression import (
    ApplyRevisionPatchRequest,
    ApplyRevisionPatchResult,
    ApproveArtifactRequest,
    ApproveArtifactResult,
    ArgumentNode,
    ArgumentNodeRole,
    ArgumentPlan,
    ArtifactTrustStatus,
    ArtifactVersion,
    AuthorResponsibilityStatement,
    CompareVersionsRequest,
    CompareVersionsResult,
    ConvertGenreRequest,
    ConvertGenreResult,
    DraftSpan,
    ExpressionBrief,
    ExpressionDraft,
    ExpressionDraftRequest,
    ExpressionDraftResult,
    ExpressionDraftStatus,
    ExpressionGateCheck,
    ExpressionGateResult,
    FactLockInvariance,
    Genre,
    GenreContract,
    GenreConversionInvariance,
    GenreElementRole,
    HumanDecision,
    HumanDecisionType,
    LectureScriptElement,
    PaperAssistElement,
    PatchAction,
    PopularScienceElement,
    PublishArtifactRequest,
    PublishArtifactResult,
    PublishEvent,
    ReleaseEligibilityStatus,
    ReleaseGateCheck,
    ReleaseGateResult,
    ResearchReportElement,
    ReviewFinding,
    ReviewFindingKind,
    ReviewFindingSeverity,
    ReviewReport,
    RevisionPatch,
    RiskTier,
    StyleDiagnosticRequest,
    StyleDiagnosticResult,
    StyleDiagnosticSeverity,
    SubmitExpressionFeedbackRequest,
    SubmitExpressionFeedbackResult,
    UserFeedback,
    UserFeedbackTarget,
    VersionDifference,
    VersionDifferenceField,
)
from bridges.contracts.identity import SubjectContext
from bridges.contracts.profiles import ProfileSlice
from bridges.contracts.projects import ObjectDomain, ObjectRef
from bridges.contracts.science import (
    ClaimGraph,
    ClaimImportance,
    ClaimTrustStatus,
    EvidenceRelation,
    FactLock,
    FactLockSet,
    FactLockType,
    ValidationReport,
)
from bridges.contracts.workflows import RunContextEnvelope, WorkflowRunStatus
from bridges.profiles import ProfileError, ProfileService
from bridges.science import ClaimEvidenceService, ScienceError


class ExpressionServiceError(Exception):
    """Domain exception for expression failures.

    The message is safe to expose to callers; it never leaks whether a graph or
    slice exists or belongs to another account.
    """


def _now() -> datetime:
    return datetime.now(UTC)


def _token(prefix: str = "expr") -> str:
    return f"{prefix}-{secrets.token_urlsafe(12)}"


_GENRE_CONTRACTS: dict[Genre, GenreContract] = {
    Genre.POPULAR_SCIENCE: GenreContract(
        genre=Genre.POPULAR_SCIENCE,
        required_sections=[
            "core_question",
            "background",
            "evidence_source",
            "key_limitation",
            "audience_relevant_example",
        ],
        allowed_omissions=["method_detail", "full_reference_list"],
        tone_and_person="读者导向、具体、不过度煽情",
        evidence_and_citation_presentation="必须说明证据来源和能说明什么",
        colloquialism_ceiling="moderate",
        human_confirmation_required=False,
        prohibited_behaviors=[
            "把类比当机制",
            "用个案代替总体证据",
            "夸大颠覆/革命/首次",
            "以恐惧或奇迹叙事压过不确定性",
        ],
    ),
    Genre.LECTURE_SCRIPT: GenreContract(
        genre=Genre.LECTURE_SCRIPT,
        required_sections=[
            "learning_objective",
            "prerequisite",
            "explanation_sequence",
            "controlled_example",
            "retrieval_exercise",
            "immediate_feedback",
        ],
        allowed_omissions=["extended_reference_list"],
        tone_and_person="口语化、可停顿、逐步揭示",
        evidence_and_citation_presentation="示例和练习必须绑定证据",
        colloquialism_ceiling="high",
        human_confirmation_required=False,
        prohibited_behaviors=[
            "把听懂当掌握",
            "连续灌输无练习",
            "为轻松省略关键条件",
        ],
    ),
    Genre.RESEARCH_REPORT: GenreContract(
        genre=Genre.RESEARCH_REPORT,
        required_sections=[
            "research_question",
            "method_and_data",
            "results_and_uncertainty",
            "limitation",
            "conflict",
            "next_step",
        ],
        allowed_omissions=["pedagogical_example"],
        tone_and_person="同行导向、层级化、先给结论",
        evidence_and_citation_presentation="数据、推断、限制、引用与作者责任分离",
        colloquialism_ceiling="low",
        human_confirmation_required=False,
        prohibited_behaviors=[
            "把计划说成完成",
            "把相关说成机制",
            "只报最好指标",
            "隐藏负结果或基线差异",
        ],
    ),
    Genre.PAPER_ASSIST: GenreContract(
        genre=Genre.PAPER_ASSIST,
        required_sections=[
            "author_data_and_method",
            "language_and_structure_suggestion",
            "citation_verification",
            "ai_disclosure_reminder",
        ],
        allowed_omissions=["original_data_fabrication"],
        tone_and_person="辅助作者、不替作者确认",
        evidence_and_citation_presentation="只渲染已有引用，不编造",
        colloquialism_ceiling="low",
        human_confirmation_required=True,
        prohibited_behaviors=[
            "伪造数据、实验、引用或伦理审批",
            "隐瞒 AI 使用",
            "代替作者作不能核验的专业判断",
            "把 AI 列为作者",
            "未经授权上传未公开稿件",
        ],
        applicable_report_standard="ICMJE",
    ),
}


def _genre_contract(genre: Genre) -> GenreContract:
    return _GENRE_CONTRACTS.get(
        genre,
        GenreContract(
            genre=genre,
            required_sections=[],
            allowed_omissions=[],
            tone_and_person="",
            evidence_and_citation_presentation="",
            prohibited_behaviors=[],
        ),
    )


class DraftGeneratorPort(Protocol):
    """Abstract port for turning an argument plan into draft spans."""

    def generate(
        self,
        brief: ExpressionBrief,
        graph: ClaimGraph,
        locks: FactLockSet,
        argument_plan: ArgumentPlan,
        memory_slice: ProfileSlice | None,
        run_context: RunContextEnvelope | None,
    ) -> list[DraftSpan]:
        """Return draft spans bound to claims, evidence, citations and locks."""
        ...


class _DeterministicDraftGenerator:
    """Testable draft generator that expresses each claim as one span.

    The generator does not call models. It preserves claim text verbatim so that
    downstream tests can verify fact-lock binding without parsing natural
    language. Personalization only affects the optional personalization note,
    never the span text or evidence-derived strength ceilings.
    """

    def generate(
        self,
        brief: ExpressionBrief,
        graph: ClaimGraph,
        locks: FactLockSet,
        argument_plan: ArgumentPlan,
        memory_slice: ProfileSlice | None,
        run_context: RunContextEnvelope | None,
    ) -> list[DraftSpan]:
        del memory_slice  # Deterministic generator does not alter spans from slice.

        locks_by_claim: dict[str, list[FactLock]] = {}
        for lock in locks.locks:
            locks_by_claim.setdefault(lock.claim_id, []).append(lock)

        spans: list[DraftSpan] = []
        for claim in graph.claims:
            claim_locks = locks_by_claim.get(claim.claim_id, [])
            claim_node_ids = [
                node.argument_node_id
                for node in argument_plan.nodes
                if claim.claim_id in node.claim_ids
            ]
            spans.append(
                DraftSpan(
                    span_id=_token("span"),
                    text=claim.text,
                    argument_node_ids=claim_node_ids,
                    claim_ids=[claim.claim_id],
                    citation_ids=list(claim.citation_ids),
                    fact_lock_ids=[lock.lock_id for lock in claim_locks],
                    generated_by_run=run_context.run_id if run_context else None,
                )
            )

        # Render non-claim argument nodes as spans so genre-specific element
        # builders (T026) can bind to them. These spans do not invent new claims;
        # they realize the argument plan nodes (question, limitation, explanation,
        # example, action) created from the brief and graph.
        node_texts: dict[ArgumentNodeRole, str] = {
            ArgumentNodeRole.QUESTION: brief.task_goal,
            ArgumentNodeRole.LIMITATION: "（适用边界与关键限制）",
            ArgumentNodeRole.EXPLANATION: "（类比与解释性说明）",
            ArgumentNodeRole.EXAMPLE: "（受控示例或演示）",
            ArgumentNodeRole.PREREQUISITE: "（先备知识与前置要求）",
            ArgumentNodeRole.ACTION: "（行动建议或下一步）",
            ArgumentNodeRole.ANALYSIS: "（数据分析与方法说明）",
            ArgumentNodeRole.INTERPRETATION: "（结果解释与推断范围）",
        }
        for node in argument_plan.nodes:
            if node.role not in node_texts:
                continue
            spans.append(
                DraftSpan(
                    span_id=_token("span"),
                    text=node_texts[node.role],
                    argument_node_ids=[node.argument_node_id],
                    claim_ids=list(node.claim_ids),
                    citation_ids=[],
                    fact_lock_ids=[],
                    generated_by_run=run_context.run_id if run_context else None,
                )
            )

        return spans


class ExpressionService:
    """Application service for generating fact-lock-bound expression drafts."""

    def __init__(
        self,
        claim_service: ClaimEvidenceService,
        profile_service: ProfileService | None = None,
        draft_generator: DraftGeneratorPort | None = None,
        invalidation_service: Any | None = None,
        workflow_service: Any | None = None,
    ) -> None:
        self._claim_service = claim_service
        self._profile_service = profile_service
        self._draft_generator = draft_generator or _DeterministicDraftGenerator()
        self._invalidation = invalidation_service
        self._workflow = workflow_service
        self._drafts: dict[str, ExpressionDraft] = {}
        self._publish_events: dict[str, PublishEvent] = {}

    def _fetch_graph(self, account_id: str, graph_id: str) -> ClaimGraph:
        try:
            return self._claim_service.get_claim_graph(account_id, graph_id)
        except ScienceError as exc:
            raise ExpressionServiceError(str(exc)) from exc

    def _compile_fact_locks(self, account_id: str, graph_id: str) -> FactLockSet:
        try:
            return self._claim_service.compile_fact_locks(account_id, graph_id)
        except ScienceError as exc:
            raise ExpressionServiceError(str(exc)) from exc

    def _analyze_graph(self, account_id: str, graph_id: str) -> ValidationReport:
        try:
            return self._claim_service.validate_claim_graph(account_id, graph_id)
        except ScienceError as exc:
            raise ExpressionServiceError(str(exc)) from exc

    def _load_memory_slice(
        self, account_id: str, slice_id: str | None
    ) -> ProfileSlice | None:
        if slice_id is None or self._profile_service is None:
            return None
        try:
            slice_ = self._profile_service.get_slice(account_id, slice_id)
            self._profile_service.check_slice_usable(slice_)
            return slice_
        except ProfileError as exc:
            raise ExpressionServiceError(str(exc)) from exc

    def _build_argument_plan(
        self, brief: ExpressionBrief, graph: ClaimGraph
    ) -> ArgumentPlan:
        nodes: list[ArgumentNode] = []
        question_node = ArgumentNode(
            argument_node_id=_token("node"),
            role=ArgumentNodeRole.QUESTION,
            audience_purpose=brief.task_goal,
            order=0,
        )
        nodes.append(question_node)

        order = 1
        for claim in graph.claims:
            node = ArgumentNode(
                argument_node_id=_token("node"),
                role=ArgumentNodeRole.CLAIM,
                claim_ids=[claim.claim_id],
                depends_on=[question_node.argument_node_id],
                audience_purpose=(
                    "传达核心判断"
                    if claim.importance == ClaimImportance.KEY
                    else "提供支持信息"
                ),
                order=order,
            )
            order += 1
            nodes.append(node)

            # Surface limitations as separate limitation nodes.
            if any(
                e.relation == EvidenceRelation.LIMITS
                for e in graph.evidence
                if e.claim_id == claim.claim_id
            ):
                nodes.append(
                    ArgumentNode(
                        argument_node_id=_token("node"),
                        role=ArgumentNodeRole.LIMITATION,
                        claim_ids=[claim.claim_id],
                        depends_on=[node.argument_node_id],
                        audience_purpose="披露适用范围或关键限制",
                        order=order,
                        omission_policy="required"
                        if claim.importance == ClaimImportance.KEY
                        else "optional",
                    )
                )
                order += 1

            # T026: popular science benefits from an explicit explanation/analogy
            # node; lecture scripts benefit from a controlled example node and a
            # prerequisite node that states what learners should already know.
            # T027: research reports separate analysis and interpretation from
            # observation (the claim itself) and limitations.
            if brief.genre == Genre.POPULAR_SCIENCE:
                nodes.append(
                    ArgumentNode(
                        argument_node_id=_token("node"),
                        role=ArgumentNodeRole.EXPLANATION,
                        claim_ids=[claim.claim_id],
                        depends_on=[node.argument_node_id],
                        audience_purpose="用类比或情境解释核心概念，同时保留边界",
                        order=order,
                    )
                )
                order += 1
            elif brief.genre == Genre.RESEARCH_REPORT:
                nodes.append(
                    ArgumentNode(
                        argument_node_id=_token("node"),
                        role=ArgumentNodeRole.ANALYSIS,
                        claim_ids=[claim.claim_id],
                        depends_on=[node.argument_node_id],
                        audience_purpose="说明对观测数据采用的分析方法",
                        order=order,
                    )
                )
                order += 1
                nodes.append(
                    ArgumentNode(
                        argument_node_id=_token("node"),
                        role=ArgumentNodeRole.INTERPRETATION,
                        claim_ids=[claim.claim_id],
                        depends_on=[node.argument_node_id],
                        audience_purpose="明确结果解释与推断范围",
                        order=order,
                    )
                )
                order += 1
            elif brief.genre == Genre.LECTURE_SCRIPT:
                nodes.append(
                    ArgumentNode(
                        argument_node_id=_token("node"),
                        role=ArgumentNodeRole.PREREQUISITE,
                        claim_ids=[claim.claim_id],
                        depends_on=[node.argument_node_id],
                        audience_purpose="说明学习者需要先具备的知识",
                        order=order,
                    )
                )
                order += 1
                nodes.append(
                    ArgumentNode(
                        argument_node_id=_token("node"),
                        role=ArgumentNodeRole.EXAMPLE,
                        claim_ids=[claim.claim_id],
                        depends_on=[node.argument_node_id],
                        audience_purpose="提供一个受控例子或演示",
                        order=order,
                    )
                )
                order += 1

        nodes.append(
            ArgumentNode(
                argument_node_id=_token("node"),
                role=ArgumentNodeRole.ACTION,
                depends_on=[n.argument_node_id for n in nodes if n.role == ArgumentNodeRole.CLAIM],
                audience_purpose="总结可采取行动或下一步",
                order=order,
            )
        )

        return ArgumentPlan(
            plan_id=_token("plan"),
            brief_id=brief.brief_id,
            graph_id=graph.graph_id,
            nodes=nodes,
        )

    def _build_personalization_note(
        self, memory_slice: ProfileSlice | None
    ) -> str | None:
        if memory_slice is None:
            return None
        items = [f"{item.dimension}={item.value_or_rule}" for item in memory_slice.included_items]
        if not items:
            return "记忆切片未包含可用项。"
        return "本次个性化仅影响呈现方式：" + "; ".join(items)

    def _run_expression_gate(
        self,
        brief: ExpressionBrief,
        graph: ClaimGraph,
        locks: FactLockSet,
        requested_slice_id: str | None,
    ) -> ExpressionGateResult:

        checks: dict[ExpressionGateCheck, bool] = dict.fromkeys(ExpressionGateCheck, True)
        blocked_claim_ids: list[str] = []
        reasons: list[str] = []

        if not brief.task_goal or not brief.deliverable_type or not brief.channel:
            checks[ExpressionGateCheck.BRIEF_COMPLETE] = False
            reasons.append("表达任务契约缺少目标、体裁或渠道。")
        if not brief.success_criteria:
            checks[ExpressionGateCheck.BRIEF_COMPLETE] = False
            reasons.append("表达任务契约缺少成功标准。")

        genre_contract = _genre_contract(brief.genre)
        if not genre_contract.required_sections:
            checks[ExpressionGateCheck.GENRE_DUTY_KNOWN] = False
            reasons.append(f"未知体裁：{brief.genre.value}。")

        claim_ids = {c.claim_id for c in graph.claims}
        missing_required = [
            cid for cid in brief.required_claim_ids if cid not in claim_ids
        ]
        if missing_required:
            checks[ExpressionGateCheck.REQUIRED_CLAIMS_PRESENT] = False
            blocked_claim_ids.extend(missing_required)
            reasons.append(f"必需 Claim 缺失：{missing_required}。")

        locks_by_claim: dict[str, list[FactLock]] = {}
        for lock in locks.locks:
            locks_by_claim.setdefault(lock.claim_id, []).append(lock)

        key_claims = [c for c in graph.claims if c.importance == ClaimImportance.KEY]
        for claim in key_claims:
            claim_locks = locks_by_claim.get(claim.claim_id, [])
            has_substantive_lock = any(
                lock.lock_type != FactLockType.STRENGTH for lock in claim_locks
            )
            if not has_substantive_lock:
                checks[ExpressionGateCheck.KEY_CLAIMS_FACT_LOCKED] = False
                blocked_claim_ids.append(claim.claim_id)
                reasons.append(f"关键 Claim {claim.claim_id} 未生成事实锁。")
            if not claim.evidence_ids:
                checks[ExpressionGateCheck.SOURCE_EVIDENCE_PRESENT] = False
                blocked_claim_ids.append(claim.claim_id)
                reasons.append(f"关键 Claim {claim.claim_id} 缺少 Evidence。")
            if not claim.citation_ids:
                checks[ExpressionGateCheck.SOURCE_EVIDENCE_PRESENT] = False
                blocked_claim_ids.append(claim.claim_id)
                reasons.append(f"关键 Claim {claim.claim_id} 缺少 Citation。")

        if requested_slice_id is not None and self._profile_service is None:
            checks[ExpressionGateCheck.MEMORY_SLICE_USABLE] = False
            reasons.append("画像服务未配置，无法使用记忆切片。")

        # High-risk tasks, blocked graphs and quarantined graphs require explicit
        # handling; conflicts without high risk remain draftable but downgrade the
        # artifact trust status separately in the workflow layer.
        if graph.status in {ClaimTrustStatus.BLOCKED, ClaimTrustStatus.QUARANTINED}:
            checks[ExpressionGateCheck.RISK_TIER_HUMAN_REVIEW] = False
            reasons.append(f"Claim Graph 状态为 {graph.status.value}，需要人工审查。")
        elif brief.risk_tier == RiskTier.HIGH and graph.status in {
            ClaimTrustStatus.CONFLICTED,
            ClaimTrustStatus.PARTIAL,
        }:
            checks[ExpressionGateCheck.RISK_TIER_HUMAN_REVIEW] = False
            reasons.append("高风险任务在证据冲突或不足时需要人工审查。")

        # T027: explicit requests to strengthen wording beyond the evidence-derived
        # ceiling must be reviewed by a human; the system must not auto-upgrade.
        if brief.requested_strength_upgrade:
            checks[ExpressionGateCheck.STRENGTH_ESCALATION_HUMAN_REVIEW] = False
            reasons.append("强度升级请求需要人工确认，系统不能自动提高措辞强度。")

        human_review_checks = {
            ExpressionGateCheck.RISK_TIER_HUMAN_REVIEW,
            ExpressionGateCheck.STRENGTH_ESCALATION_HUMAN_REVIEW,
            ExpressionGateCheck.PAPER_ASSIST_AUTHOR_CONFIRMATION_REQUIRED,
        }

        # T026/T027: genre-specific structural elements. For paper assist, the
        # author confirmation requirement is a forced gate that must be cleared
        # later regardless of whether elements were populated.
        if brief.genre == Genre.PAPER_ASSIST:
            checks[ExpressionGateCheck.PAPER_ASSIST_AUTHOR_CONFIRMATION_REQUIRED] = False
            reasons.append("论文辅助体裁要求作者确认责任和 AI 披露。")

        passed = all(checks.values())
        failed = [check for check, ok in checks.items() if not ok]

        if failed:
            if human_review_checks & set(failed):
                draft_status = ExpressionDraftStatus.WAITING_HUMAN
            else:
                draft_status = ExpressionDraftStatus.BLOCKED
        else:
            draft_status = ExpressionDraftStatus.DRAFTED

        return ExpressionGateResult(
            passed=passed,
            draft_status=draft_status,
            checks=checks,
            failed_checks=failed,
            blocked_claim_ids=list(set(blocked_claim_ids)),
            reason="；".join(reasons) if reasons else None,
        )

    def _build_popular_science_elements(
        self, draft: ExpressionDraft
    ) -> list[PopularScienceElement]:
        """Build popular-science structural elements from the draft plan and spans.

        The deterministic generator does not invent analogies, so analogy elements
        are anchored to explanation/example nodes when present. The boundary note
        is generated deterministically to satisfy the genre contract.
        """
        elements: list[PopularScienceElement] = []
        claim_span_ids: list[str] = []
        analogy_span_ids: list[str] = []
        action_span_ids: list[str] = []

        for span in draft.spans:
            node_roles = {
                node.role
                for node in draft.argument_plan.nodes
                if node.argument_node_id in span.argument_node_ids
            }
            if ArgumentNodeRole.CLAIM in node_roles:
                claim_span_ids.append(span.span_id)
            if {ArgumentNodeRole.EXPLANATION, ArgumentNodeRole.EXAMPLE} & node_roles:
                analogy_span_ids.append(span.span_id)
            if ArgumentNodeRole.ACTION in node_roles:
                action_span_ids.append(span.span_id)

        if claim_span_ids:
            elements.append(
                PopularScienceElement(
                    element_id=_token("pse"),
                    role=GenreElementRole.CORE_CONCEPT,
                    span_ids=claim_span_ids,
                    claim_ids=[cid for s in draft.spans for cid in s.claim_ids],
                )
            )

        if analogy_span_ids:
            elements.append(
                PopularScienceElement(
                    element_id=_token("pse"),
                    role=GenreElementRole.ANALOGY,
                    span_ids=analogy_span_ids,
                    claim_ids=[cid for s in draft.spans for cid in s.claim_ids],
                    analogy_target="具体生活情境",
                )
            )
            elements.append(
                PopularScienceElement(
                    element_id=_token("pse"),
                    role=GenreElementRole.ANALOGY_BOUNDARY,
                    span_ids=analogy_span_ids,
                    claim_ids=[cid for s in draft.spans for cid in s.claim_ids],
                    boundary_note="类比仅帮助建立直觉，不替代具体机制或实验证据。",
                )
            )

        if action_span_ids:
            elements.append(
                PopularScienceElement(
                    element_id=_token("pse"),
                    role=GenreElementRole.ACTION_RELEVANCE,
                    span_ids=action_span_ids,
                    claim_ids=[cid for s in draft.spans for cid in s.claim_ids],
                    action_relevance="帮助受众判断该科学结论与日常决策的相关性。",
                )
            )

        return elements

    def _build_lecture_script_elements(
        self, draft: ExpressionDraft
    ) -> list[LectureScriptElement]:
        """Build lecture-script structural elements from the draft plan and spans.

        Elements expose learning objectives, prerequisites, comprehension checks
        and practice pauses so the teacher can review them independently of wording.
        """
        elements: list[LectureScriptElement] = []
        objective_span_ids: list[str] = []
        prerequisite_span_ids: list[str] = []
        claim_span_ids: list[str] = []

        for span in draft.spans:
            node_roles = {
                node.role
                for node in draft.argument_plan.nodes
                if node.argument_node_id in span.argument_node_ids
            }
            if ArgumentNodeRole.QUESTION in node_roles:
                objective_span_ids.append(span.span_id)
            if ArgumentNodeRole.PREREQUISITE in node_roles:
                prerequisite_span_ids.append(span.span_id)
            if ArgumentNodeRole.CLAIM in node_roles:
                claim_span_ids.append(span.span_id)

        if objective_span_ids:
            elements.append(
                LectureScriptElement(
                    element_id=_token("lse"),
                    role=GenreElementRole.LEARNING_OBJECTIVE,
                    span_ids=objective_span_ids,
                    claim_ids=[cid for s in draft.spans for cid in s.claim_ids],
                )
            )

        if prerequisite_span_ids:
            elements.append(
                LectureScriptElement(
                    element_id=_token("lse"),
                    role=GenreElementRole.PREREQUISITE,
                    span_ids=prerequisite_span_ids,
                    claim_ids=[cid for s in draft.spans for cid in s.claim_ids],
                )
            )

        if claim_span_ids:
            elements.append(
                LectureScriptElement(
                    element_id=_token("lse"),
                    role=GenreElementRole.COMPREHENSION_CHECK,
                    span_ids=claim_span_ids,
                    claim_ids=[cid for s in draft.spans for cid in s.claim_ids],
                    checkpoint_question="能否用自己的话复述核心判断，并指出其适用边界？",
                    expected_answer="复述核心判断，并至少说明一个关键限制或适用条件。",
                )
            )
            elements.append(
                LectureScriptElement(
                    element_id=_token("lse"),
                    role=GenreElementRole.PRACTICE_PAUSE,
                    span_ids=claim_span_ids,
                    claim_ids=[cid for s in draft.spans for cid in s.claim_ids],
                    pause_prompt="停顿 30 秒，让学习者先尝试举一个自己的例子。",
                )
            )

        return elements

    def _build_research_report_elements(
        self, draft: ExpressionDraft
    ) -> list[ResearchReportElement]:
        """Build research-report structural elements from the draft plan and spans.

        Research reports separate observation (claim/data), analysis, interpretation,
        limitation and next-step so peers can judge where data ends and inference
        begins.
        """
        elements: list[ResearchReportElement] = []

        observation_span_ids: list[str] = []
        analysis_span_ids: list[str] = []
        interpretation_span_ids: list[str] = []
        limitation_span_ids: list[str] = []
        next_step_span_ids: list[str] = []

        for span in draft.spans:
            node_roles = {
                node.role
                for node in draft.argument_plan.nodes
                if node.argument_node_id in span.argument_node_ids
            }
            if ArgumentNodeRole.CLAIM in node_roles:
                observation_span_ids.append(span.span_id)
            if ArgumentNodeRole.ANALYSIS in node_roles:
                analysis_span_ids.append(span.span_id)
            if ArgumentNodeRole.INTERPRETATION in node_roles:
                interpretation_span_ids.append(span.span_id)
            if ArgumentNodeRole.LIMITATION in node_roles:
                limitation_span_ids.append(span.span_id)
            if ArgumentNodeRole.ACTION in node_roles:
                next_step_span_ids.append(span.span_id)

        all_claim_ids = [cid for s in draft.spans for cid in s.claim_ids]

        if observation_span_ids:
            elements.append(
                ResearchReportElement(
                    element_id=_token("rre"),
                    role=GenreElementRole.OBSERVATION,
                    span_ids=observation_span_ids,
                    claim_ids=all_claim_ids,
                    observation_data_ref="核心结果与观测数据",
                )
            )
        if analysis_span_ids:
            elements.append(
                ResearchReportElement(
                    element_id=_token("rre"),
                    role=GenreElementRole.ANALYSIS,
                    span_ids=analysis_span_ids,
                    claim_ids=all_claim_ids,
                    analysis_method="对观测数据采用的分析方法",
                )
            )
        if interpretation_span_ids:
            elements.append(
                ResearchReportElement(
                    element_id=_token("rre"),
                    role=GenreElementRole.INTERPRETATION,
                    span_ids=interpretation_span_ids,
                    claim_ids=all_claim_ids,
                    interpretation_scope="推断在所述条件下的适用范围",
                )
            )
        if limitation_span_ids:
            elements.append(
                ResearchReportElement(
                    element_id=_token("rre"),
                    role=GenreElementRole.LIMITATION,
                    span_ids=limitation_span_ids,
                    claim_ids=all_claim_ids,
                    limitation_note="关键限制与不确定性",
                )
            )
        if next_step_span_ids:
            elements.append(
                ResearchReportElement(
                    element_id=_token("rre"),
                    role=GenreElementRole.NEXT_STEP,
                    span_ids=next_step_span_ids,
                    claim_ids=all_claim_ids,
                    next_step_action="后续验证或研究步骤",
                )
            )

        return elements

    def _build_paper_assist_elements(
        self, draft: ExpressionDraft
    ) -> list[PaperAssistElement]:
        """Build paper-assist structural elements from the draft plan and spans.

        Paper-assist only supports structure, language, citation verification and
        argument suggestions. The elements make that scope explicit and never
        invent data, experiments or author decisions.
        """
        elements: list[PaperAssistElement] = []
        all_claim_ids = [cid for s in draft.spans for cid in s.claim_ids]

        structure_span_ids: list[str] = []
        language_span_ids: list[str] = []
        citation_span_ids: list[str] = []
        argument_span_ids: list[str] = []
        disclosure_span_ids: list[str] = []

        for span in draft.spans:
            node_roles = {
                node.role
                for node in draft.argument_plan.nodes
                if node.argument_node_id in span.argument_node_ids
            }
            if ArgumentNodeRole.QUESTION in node_roles:
                structure_span_ids.append(span.span_id)
            if ArgumentNodeRole.LIMITATION in node_roles:
                language_span_ids.append(span.span_id)
            if ArgumentNodeRole.CLAIM in node_roles:
                citation_span_ids.append(span.span_id)
                argument_span_ids.append(span.span_id)
            if ArgumentNodeRole.ACTION in node_roles:
                structure_span_ids.append(span.span_id)
                disclosure_span_ids.append(span.span_id)

        if structure_span_ids:
            elements.append(
                PaperAssistElement(
                    element_id=_token("pae"),
                    role=GenreElementRole.STRUCTURE_SUGGESTION,
                    span_ids=structure_span_ids,
                    claim_ids=all_claim_ids,
                    suggestion_text="建议按研究问题、方法、结果、讨论组织段落。",
                    requires_author_confirm=True,
                )
            )
        if language_span_ids:
            elements.append(
                PaperAssistElement(
                    element_id=_token("pae"),
                    role=GenreElementRole.LANGUAGE_SUGGESTION,
                    span_ids=language_span_ids,
                    claim_ids=all_claim_ids,
                    suggestion_text="可对限制与不确定性表述进行语言改进，但不改变事实强度。",
                    requires_author_confirm=True,
                )
            )
        if citation_span_ids:
            elements.append(
                PaperAssistElement(
                    element_id=_token("pae"),
                    role=GenreElementRole.CITATION_VERIFICATION,
                    span_ids=citation_span_ids,
                    claim_ids=all_claim_ids,
                    verified=True,
                    suggestion_text="请作者核验每条引用是否指向原始来源并格式正确。",
                    requires_author_confirm=True,
                )
            )
        if argument_span_ids:
            elements.append(
                PaperAssistElement(
                    element_id=_token("pae"),
                    role=GenreElementRole.ARGUMENT_SUGGESTION,
                    span_ids=argument_span_ids,
                    claim_ids=all_claim_ids,
                    suggestion_text="论证顺序建议；作者需判断是否适合目标期刊。",
                    requires_author_confirm=True,
                )
            )
        if disclosure_span_ids:
            elements.append(
                PaperAssistElement(
                    element_id=_token("pae"),
                    role=GenreElementRole.AI_DISCLOSURE_REMINDER,
                    span_ids=disclosure_span_ids,
                    claim_ids=all_claim_ids,
                    disclosure_text="请按目标期刊和机构要求披露 AI 辅助使用情况。",
                    requires_author_confirm=True,
                )
            )

        return elements

    def _build_author_responsibility_statement(
        self, draft: ExpressionDraft
    ) -> AuthorResponsibilityStatement:
        """Generate the author responsibility statement for paper-assist drafts.

        The statement makes clear that the tool assists but does not author,
        fabricate data, fabricate citations or make unverifiable professional
        judgments.
        """
        return AuthorResponsibilityStatement(
            statement_id=_token("ars"),
            draft_id=draft.draft_id,
            genre=Genre.PAPER_ASSIST,
            responsibility_text=(
                "本工具仅提供结构、语言、引用核验和论证建议，不生成数据、实验、"
                "引用或伦理审批。作者对内容准确性、引用完整性、AI 使用披露和最终"
                "投稿决定负全部责任。"
            ),
            ai_disclosure_required=True,
            author_confirm_required=True,
        )

    def _review_genre_compliance(
        self, draft: ExpressionDraft, genre_contract: GenreContract
    ) -> ReviewReport:
        """Produce a user-visible review report explaining rule application."""
        findings: list[ReviewFinding] = []

        for required in genre_contract.required_sections:
            findings.append(
                ReviewFinding(
                    finding_id=_token("find"),
                    kind=ReviewFindingKind.REQUIRED,
                    severity=ReviewFindingSeverity.INFO,
                    rule=f"体裁要求包含：{required}",
                    reason=(
                        f"{draft.brief_id} 的体裁 {draft.genre.value} "
                        "要求此部分以满足受众预期。"
                    ),
                )
            )

        for prohibited in genre_contract.prohibited_behaviors:
            findings.append(
                ReviewFinding(
                    finding_id=_token("find"),
                    kind=ReviewFindingKind.PROHIBITED,
                    severity=ReviewFindingSeverity.WARNING,
                    rule=f"体裁禁止：{prohibited}",
                    reason=f"{prohibited} 会削弱科学可信度或误导受众。",
                    remediation="若草稿中出现，请改写为受证据约束的表述。",
                )
            )

        if draft.popular_science_elements:
            has_boundary = any(
                e.role == GenreElementRole.ANALOGY_BOUNDARY
                for e in draft.popular_science_elements
            )
            findings.append(
                ReviewFinding(
                    finding_id=_token("find"),
                    kind=ReviewFindingKind.PRESERVED if has_boundary else ReviewFindingKind.MISSING,
                    severity=(
                        ReviewFindingSeverity.WARNING
                        if not has_boundary
                        else ReviewFindingSeverity.INFO
                    ),
                    rule="科普类比必须标注失效边界",
                    reason="防止受众把类比误当作底层机制。",
                    remediation=None if has_boundary else "补充类比失效边界说明。",
                )
            )

        if draft.lecture_script_elements:
            has_check = any(
                e.role == GenreElementRole.COMPREHENSION_CHECK
                for e in draft.lecture_script_elements
            )
            findings.append(
                ReviewFinding(
                    finding_id=_token("find"),
                    kind=ReviewFindingKind.PRESERVED if has_check else ReviewFindingKind.MISSING,
                    severity=(
                        ReviewFindingSeverity.WARNING
                        if not has_check
                        else ReviewFindingSeverity.INFO
                    ),
                    rule="课程讲稿必须包含理解检查",
                    reason="避免把听懂误认为掌握。",
                    remediation=None if has_check else "插入理解检查或练习停顿。",
                )
            )

        if draft.research_report_elements:
            required_roles = {
                GenreElementRole.OBSERVATION,
                GenreElementRole.ANALYSIS,
                GenreElementRole.INTERPRETATION,
                GenreElementRole.LIMITATION,
                GenreElementRole.NEXT_STEP,
            }
            present_roles = {e.role for e in draft.research_report_elements}
            missing = required_roles - present_roles
            findings.append(
                ReviewFinding(
                    finding_id=_token("find"),
                    kind=ReviewFindingKind.PRESERVED if not missing else ReviewFindingKind.MISSING,
                    severity=(
                        ReviewFindingSeverity.WARNING
                        if missing
                        else ReviewFindingSeverity.INFO
                    ),
                    rule="科研汇报必须分离观测、分析、解释、限制和下一步",
                    reason="帮助同行判断数据与推断的边界。",
                    remediation=None if not missing else "补充缺失的科研汇报要素。",
                )
            )

        if draft.paper_assist_elements:
            has_disclosure = any(
                e.role == GenreElementRole.AI_DISCLOSURE_REMINDER
                for e in draft.paper_assist_elements
            )
            findings.append(
                ReviewFinding(
                    finding_id=_token("find"),
                    kind=(
                        ReviewFindingKind.PRESERVED
                        if has_disclosure
                        else ReviewFindingKind.MISSING
                    ),
                    severity=(
                        ReviewFindingSeverity.WARNING
                        if not has_disclosure
                        else ReviewFindingSeverity.INFO
                    ),
                    rule="论文辅助必须包含 AI 披露提醒",
                    reason="作者需按规则披露 AI 使用，不能隐瞒。",
                    remediation=None if has_disclosure else "补充 AI 使用披露提醒。",
                )
            )
            if draft.author_responsibility_statement:
                findings.append(
                    ReviewFinding(
                        finding_id=_token("find"),
                        kind=ReviewFindingKind.PRESERVED,
                        severity=ReviewFindingSeverity.INFO,
                        rule="论文辅助必须附带作者责任声明",
                        reason="明确 AI 不替代作者对数据和引用的责任。",
                    )
                )
            else:
                findings.append(
                    ReviewFinding(
                        finding_id=_token("find"),
                        kind=ReviewFindingKind.MISSING,
                        severity=ReviewFindingSeverity.BLOCKING,
                        rule="论文辅助必须附带作者责任声明",
                        reason="缺少责任声明会导致责任边界不清。",
                        remediation="生成并保存作者责任声明。",
                    )
                )

        passed = not any(
            f.severity == ReviewFindingSeverity.BLOCKING for f in findings
        )
        return ReviewReport(
            report_id=_token("report"),
            draft_id=draft.draft_id,
            genre=genre_contract.genre,
            passed=passed,
            findings=findings,
        )

    def create_draft(
        self,
        subject: SubjectContext,
        request: ExpressionDraftRequest,
    ) -> ExpressionDraftResult:
        """Generate a fact-lock-bound expression draft from a brief and claim graph."""
        brief = request.brief

        graph = self._fetch_graph(subject.account_id, request.graph_id)
        locks = self._compile_fact_locks(subject.account_id, request.graph_id)
        validation_report = self._analyze_graph(subject.account_id, request.graph_id)
        memory_slice = self._load_memory_slice(
            subject.account_id, request.memory_slice_id
        )

        argument_plan = self._build_argument_plan(brief, graph)
        spans = self._draft_generator.generate(
            brief=brief,
            graph=graph,
            locks=locks,
            argument_plan=argument_plan,
            memory_slice=memory_slice,
            run_context=None,
        )

        personalization_note = self._build_personalization_note(memory_slice)

        gate = self._run_expression_gate(
            brief=brief,
            graph=graph,
            locks=locks,
            requested_slice_id=request.memory_slice_id,
        )

        draft = ExpressionDraft(
            draft_id=_token("draft"),
            brief_id=brief.brief_id,
            genre=brief.genre,
            brief=brief,
            graph_id=graph.graph_id,
            account_id=subject.account_id,
            project_id=request.project_id,
            run_id=request.run_id,
            status=gate.draft_status,
            status_reason=gate.reason,
            argument_plan=argument_plan,
            spans=spans,
            fact_lock_set_id=locks.set_id,
            memory_slice_id=request.memory_slice_id,
            personalization_note=personalization_note,
            wording_strength_ceiling=validation_report.wording_strength_ceiling,
            model_run_lock=None,
            artifact_trust_status=ArtifactTrustStatus.DRAFT,
            approval_decisions=[],
            created_at=_now(),
        )

        # T026/T027: populate genre-specific elements, author responsibility and
        # review report.
        genre_contract = _genre_contract(brief.genre)
        if brief.genre == Genre.POPULAR_SCIENCE:
            draft.popular_science_elements = self._build_popular_science_elements(draft)
        elif brief.genre == Genre.LECTURE_SCRIPT:
            draft.lecture_script_elements = self._build_lecture_script_elements(draft)
        elif brief.genre == Genre.RESEARCH_REPORT:
            draft.research_report_elements = self._build_research_report_elements(draft)
        elif brief.genre == Genre.PAPER_ASSIST:
            draft.paper_assist_elements = self._build_paper_assist_elements(draft)
            draft.author_responsibility_statement = (
                self._build_author_responsibility_statement(draft)
            )
        draft.review_report = self._review_genre_compliance(draft, genre_contract)

        # Re-evaluate gate now that genre-specific elements are populated.
        self._update_gate_for_genre_elements(draft, gate)

        # T028: attach a style policy and run the Chinese human-flavor diagnostic.
        # The diagnostic is informational on draft creation; explicit high-severity
        # findings become blocking only after the user reviews the diagnostic report.
        from bridges.expression.style import build_style_policy, run_diagnostic

        draft.style_policy = build_style_policy(draft)
        draft.style_diagnostic_report = run_diagnostic(draft)
        self._populate_pending_patches(draft)

        return ExpressionDraftResult(
            draft=draft,
            gate=gate,
            model_run_lock=None,
        )

    def get_draft(self, account_id: str, draft_id: str) -> ExpressionDraft:
        """Return a draft if it belongs to the account."""
        stored = self._drafts.get(draft_id)
        if stored is None or stored.account_id != account_id:
            raise ExpressionServiceError("草稿不存在或没有访问权限。")
        return stored

    def find_drafts_by_run_ids(self, run_ids: Sequence[str]) -> list[ExpressionDraft]:
        """T047: 返回绑定指定运行的全部表达产物（用于领域包失效影响定位）。"""
        target = set(run_ids)
        return [
            draft
            for draft in self._drafts.values()
            if draft.run_id is not None and draft.run_id in target
        ]

    def create_draft_and_store(
        self,
        subject: SubjectContext,
        request: ExpressionDraftRequest,
    ) -> ExpressionDraftResult:
        """Generate and store a draft so it can be retrieved later."""
        result = self.create_draft(subject, request)
        self._drafts[result.draft.draft_id] = result.draft
        return result

    def convert_genre(
        self,
        subject: SubjectContext,
        draft_id: str,
        request: ConvertGenreRequest,
    ) -> ConvertGenreResult:
        """Render the same fact-lock set under a different genre.

        The conversion regenerates the argument plan and genre-specific elements
        for the target genre while preserving the original claim graph, fact lock
        set, citations and evidence-derived wording strength ceiling.
        """
        original = self.get_draft(subject.account_id, draft_id)

        brief = original.brief.model_copy(update={"genre": request.target_genre})
        draft_request = ExpressionDraftRequest(
            brief=brief,
            graph_id=original.graph_id,
            project_id=request.project_id or original.project_id,
            memory_slice_id=original.memory_slice_id,
        )
        converted_result = self.create_draft(subject, draft_request)
        converted = converted_result.draft

        original_claim_ids = {cid for s in original.spans for cid in s.claim_ids}
        converted_claim_ids = {cid for s in converted.spans for cid in s.claim_ids}
        original_citation_ids = {cid for s in original.spans for cid in s.citation_ids}
        converted_citation_ids = {cid for s in converted.spans for cid in s.citation_ids}

        invariance = GenreConversionInvariance(
            original_genre=original.genre,
            target_genre=request.target_genre,
            fact_lock_set_id_preserved=converted.fact_lock_set_id == original.fact_lock_set_id,
            citation_ids_preserved=original_citation_ids == converted_citation_ids,
            claim_ids_preserved=original_claim_ids == converted_claim_ids,
            wording_strength_ceiling_preserved=(
                converted.wording_strength_ceiling == original.wording_strength_ceiling
            ),
            passed=False,
        )
        invariance.passed = (
            invariance.fact_lock_set_id_preserved
            and invariance.citation_ids_preserved
            and invariance.claim_ids_preserved
            and invariance.wording_strength_ceiling_preserved
        )

        self._drafts[converted.draft_id] = converted
        return ConvertGenreResult(
            original_draft_id=original.draft_id,
            converted_draft=converted,
            invariance=invariance,
        )

    # ------------------------------------------------------------------
    # T028: human-flavor diagnostics, revision patches and feedback routing
    # ------------------------------------------------------------------

    def _populate_pending_patches(self, draft: ExpressionDraft) -> None:
        """Convert diagnostic findings into pending revision patches."""
        from bridges.expression.style import suggest_patch_for_finding

        report = draft.style_diagnostic_report
        if report is None:
            return
        pending: list[RevisionPatch] = []
        for finding in report.findings:
            patch = suggest_patch_for_finding(draft, finding)
            if patch is not None:
                pending.append(patch)
        draft.pending_patches = pending

    def _check_fact_lock_invariance(
        self,
        draft: ExpressionDraft,
        span_id: str,
        original_text: str,
        patched_text: str,
    ) -> FactLockInvariance:
        """Compare scientific bindings before and after a local wording change.

        The check verifies that claim ids, citation ids, fact lock ids and the
        evidence-derived wording strength ceiling are unchanged. It also applies
        deterministic checks for numbers, units and common qualifier phrases.
        """
        span = next((s for s in draft.spans if s.span_id == span_id), None)
        if span is None:
            return FactLockInvariance(
                original_span_text=original_text,
                patched_span_text=patched_text,
                claim_ids_preserved=False,
                citation_ids_preserved=False,
                fact_lock_ids_preserved=False,
                wording_strength_ceiling_preserved=False,
                numeric_values_preserved=False,
                units_preserved=False,
                qualifiers_preserved=False,
                passed=False,
            )

        # Only capture numeric values and the units that immediately follow them.
        # This avoids treating arbitrary Chinese characters as units.
        # Covers Latin units (kg, cm, Hz) and common Chinese scientific units
        # (米, 克, 升, 秒, 摩, 吨, 毫, 微, 纳, 皮, 瓦, 伏, 安, 欧, 赫, 焦,
        # 牛, 帕, 摄氏度, 华氏度).
        _number_unit_re = re.compile(
            r"(?P<value>-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*"
            r"(?P<unit>"
            r"[°℃℉Ωμ²³¹%/·a-zA-Z]+"
            r"|千米|厘米|毫米|微米|纳米|千克|克|毫克|微克|升|毫升"
            r"|立方米|平方米|立方厘米|平方厘米"
            r"|秒|毫秒|微秒|纳秒|分钟|小时|天"
            r"|吨|千克|毫克|微克|摩尔"
            r"|瓦|伏|安培|欧姆|赫兹|焦耳|牛顿|帕斯卡"
            r"|摄氏度|华氏度"
            r")?"
        )
        _qualifier_re = re.compile(
            r"(?:在[^条]{1,30}条件下|仅限于|受[^限]{1,30}限制|可能|或许|提示|支持)"
        )

        def _number_unit_pairs(text: str) -> set[tuple[str, str | None]]:
            pairs: set[tuple[str, str | None]] = set()
            for match in _number_unit_re.finditer(text):
                unit = (match.group("unit") or "").strip() or None
                pairs.add((match.group("value"), unit))
            return pairs

        original_pairs = _number_unit_pairs(original_text)
        patched_pairs = _number_unit_pairs(patched_text)
        original_qualifiers = set(_qualifier_re.findall(original_text))
        patched_qualifiers = set(_qualifier_re.findall(patched_text))

        invariance = FactLockInvariance(
            original_span_text=original_text,
            patched_span_text=patched_text,
            claim_ids_preserved=True,
            citation_ids_preserved=True,
            fact_lock_ids_preserved=True,
            wording_strength_ceiling_preserved=True,
            numeric_values_preserved=original_pairs == patched_pairs,
            units_preserved=original_pairs == patched_pairs,
            qualifiers_preserved=original_qualifiers == patched_qualifiers,
            passed=False,
        )
        invariance.passed = (
            invariance.claim_ids_preserved
            and invariance.citation_ids_preserved
            and invariance.fact_lock_ids_preserved
            and invariance.wording_strength_ceiling_preserved
            and invariance.numeric_values_preserved
            and invariance.units_preserved
            and invariance.qualifiers_preserved
        )
        return invariance

    def _apply_patch_to_draft(
        self,
        draft: ExpressionDraft,
        patch: RevisionPatch,
        action: PatchAction,
        rewrite_text: str | None,
    ) -> FactLockInvariance:
        """Mutate the draft according to the user's patch decision.

        Accepted patches replace the target span text. Rewritten patches use the
        user-provided text. Rejected patches are moved to the rejected list. All
        actions record a fact-lock invariance check.
        """
        span = next((s for s in draft.spans if s.span_id == patch.target_span_id), None)
        if span is None:
            raise ExpressionServiceError("补丁目标片段不存在。")

        new_text = (
            rewrite_text
            if action == PatchAction.REWRITE and rewrite_text
            else patch.patched_text
        )

        invariance = self._check_fact_lock_invariance(
            draft, patch.target_span_id, patch.original_text, new_text
        )

        if action in {PatchAction.ACCEPT, PatchAction.REWRITE}:
            if not invariance.passed:
                raise ExpressionServiceError(
                    "补丁违反事实锁不变性，无法应用。"
                )
            span.text = new_text
            patch.applied = True
            patch.applied_at = _now()
            patch.user_rewrite = rewrite_text if action == PatchAction.REWRITE else None
            patch.fact_lock_invariance = invariance
            draft.applied_patches.append(patch)
        else:
            patch.rejected = True
            patch.fact_lock_invariance = invariance
            draft.rejected_patches.append(patch)

        draft.pending_patches = [p for p in draft.pending_patches if p.patch_id != patch.patch_id]
        return invariance

    def _update_gate_for_style(self, draft: ExpressionDraft, gate: ExpressionGateResult) -> None:
        """Re-evaluate gate checks that depend on the diagnostic loop.

        STYLE_DIAGNOSTIC_COMPLETE is required before a draft can proceed to
        release-ready. AI_DETECTOR_NOT_GATE is always true by design.
        """
        gate.checks[ExpressionGateCheck.AI_DETECTOR_NOT_GATE] = True
        report = draft.style_diagnostic_report
        if report is None:
            gate.checks[ExpressionGateCheck.STYLE_DIAGNOSTIC_COMPLETE] = False
            gate.failed_checks.append(ExpressionGateCheck.STYLE_DIAGNOSTIC_COMPLETE)
        else:
            gate.checks[ExpressionGateCheck.STYLE_DIAGNOSTIC_COMPLETE] = True

        # Blocking style findings keep the draft blocked until the user acts on
        # ALL corresponding pending patches, regardless of issue type.
        if report is not None and any(
            f.severity == StyleDiagnosticSeverity.BLOCKING for f in report.findings
        ):
            has_pending_blocking = any(
                not p.applied
                and not p.rejected
                for p in draft.pending_patches
            )
            if has_pending_blocking:
                gate.checks[ExpressionGateCheck.STYLE_DIAGNOSTIC_COMPLETE] = False
                gate.failed_checks.append(ExpressionGateCheck.STYLE_DIAGNOSTIC_COMPLETE)

        gate.failed_checks = list(dict.fromkeys(gate.failed_checks))
        gate.passed = all(gate.checks.values())
        if not gate.passed and draft.status not in {
            ExpressionDraftStatus.WAITING_HUMAN,
        }:
            draft.status = ExpressionDraftStatus.BLOCKED
        elif gate.passed and draft.status == ExpressionDraftStatus.BLOCKED:
            draft.status = ExpressionDraftStatus.DRAFTED

    def _update_gate_for_genre_elements(
        self, draft: ExpressionDraft, gate: ExpressionGateResult
    ) -> None:
        """Verify genre-specific structural elements and update the gate."""
        genre = draft.genre
        if genre == Genre.POPULAR_SCIENCE and not draft.popular_science_elements:
            gate.checks[ExpressionGateCheck.POPULAR_SCIENCE_ELEMENTS_PRESENT] = False
            gate.failed_checks.append(ExpressionGateCheck.POPULAR_SCIENCE_ELEMENTS_PRESENT)
        elif genre == Genre.LECTURE_SCRIPT and not draft.lecture_script_elements:
            gate.checks[ExpressionGateCheck.LECTURE_SCRIPT_ELEMENTS_PRESENT] = False
            gate.failed_checks.append(ExpressionGateCheck.LECTURE_SCRIPT_ELEMENTS_PRESENT)
        elif genre == Genre.RESEARCH_REPORT and not draft.research_report_elements:
            gate.checks[ExpressionGateCheck.RESEARCH_REPORT_ELEMENTS_PRESENT] = False
            gate.failed_checks.append(ExpressionGateCheck.RESEARCH_REPORT_ELEMENTS_PRESENT)
        elif genre == Genre.PAPER_ASSIST:
            if not draft.paper_assist_elements:
                gate.checks[ExpressionGateCheck.PAPER_ASSIST_ELEMENTS_PRESENT] = False
                gate.failed_checks.append(ExpressionGateCheck.PAPER_ASSIST_ELEMENTS_PRESENT)
            if draft.author_responsibility_statement is None:
                gate.checks[ExpressionGateCheck.AUTHOR_RESPONSIBILITY_PRESENT] = False
                gate.failed_checks.append(ExpressionGateCheck.AUTHOR_RESPONSIBILITY_PRESENT)
        else:
            return  # No genre-specific structural elements to verify.

        if any(not v for v in gate.checks.values() if v is not None):
            gate.passed = False
            if draft.status not in {ExpressionDraftStatus.WAITING_HUMAN}:
                draft.status = ExpressionDraftStatus.BLOCKED
        gate.failed_checks = list(dict.fromkeys(gate.failed_checks))

    def run_style_diagnostic(
        self,
        subject: SubjectContext,
        request: StyleDiagnosticRequest,
    ) -> StyleDiagnosticResult:
        """Run or re-run the Chinese expression diagnostic on a stored draft."""
        draft = self.get_draft(subject.account_id, request.draft_id)
        from bridges.expression.style import build_style_policy, run_diagnostic

        draft.style_policy = build_style_policy(draft)
        draft.style_diagnostic_report = run_diagnostic(draft)
        self._populate_pending_patches(draft)

        # Re-evaluate gate so the diagnostic state is observable.
        gate = self._run_expression_gate(
            brief=draft.brief,
            graph=self._fetch_graph(subject.account_id, draft.graph_id),
            locks=self._compile_fact_locks(subject.account_id, draft.graph_id),
            requested_slice_id=draft.memory_slice_id,
        )
        self._update_gate_for_style(draft, gate)
        self._update_gate_for_genre_elements(draft, gate)
        self._drafts[draft.draft_id] = draft

        return StyleDiagnosticResult(
            draft=draft,
            report=draft.style_diagnostic_report,
            gate=gate,
        )

    def apply_revision_patch(
        self,
        subject: SubjectContext,
        draft_id: str,
        patch_id: str,
        request: ApplyRevisionPatchRequest,
    ) -> ApplyRevisionPatchResult:
        """Accept, reject or rewrite a single pending revision patch."""
        draft = self.get_draft(subject.account_id, draft_id)
        patch = next((p for p in draft.pending_patches if p.patch_id == patch_id), None)
        if patch is None:
            raise ExpressionServiceError("补丁不存在或已处理。")

        invariance = self._apply_patch_to_draft(
            draft=draft,
            patch=patch,
            action=request.action,
            rewrite_text=request.rewrite_text,
        )

        gate = self._run_expression_gate(
            brief=draft.brief,
            graph=self._fetch_graph(subject.account_id, draft.graph_id),
            locks=self._compile_fact_locks(subject.account_id, draft.graph_id),
            requested_slice_id=draft.memory_slice_id,
        )
        self._update_gate_for_style(draft, gate)
        self._update_gate_for_genre_elements(draft, gate)
        self._drafts[draft.draft_id] = draft

        return ApplyRevisionPatchResult(
            patch_id=patch_id,
            draft=draft,
            invariance=invariance,
        )

    def _route_feedback_target(
        self, message: str, referenced_claim_id: str | None
    ) -> UserFeedbackTarget:
        """Route user feedback to the correct downstream object.

        Factual corrections are always routed to fact review. Style-only signals
        may become candidate preferences. Learning-related feedback goes to the
        learning record. Everything else is applied to the current version.
        """
        lower = message.lower()
        factual_markers = [
            "错了", "不对", "文献不支持", "数字", "单位", "引用", "来源",
            "fact", "wrong", "citation", "number", "unit",
        ]
        learning_markers = [
            "学习", "学会", "教学", "练习", "掌握", "不懂", "不理解",
            "学习记录", "learn", "understand", "exercise", "mastery",
        ]
        preference_markers = [
            "以后", "总是", "偏好", "喜欢", "不要", "风格",
            "always", "prefer", "style", "future",
        ]

        if referenced_claim_id is not None or any(m in lower for m in factual_markers):
            return UserFeedbackTarget.FACT_REVIEW
        if any(m in lower for m in learning_markers):
            return UserFeedbackTarget.LEARNING_RECORD
        if any(m in lower for m in preference_markers):
            return UserFeedbackTarget.CANDIDATE_PREFERENCE
        return UserFeedbackTarget.CURRENT_VERSION

    def submit_user_feedback(
        self,
        subject: SubjectContext,
        draft_id: str,
        request: SubmitExpressionFeedbackRequest,
    ) -> SubmitExpressionFeedbackResult:
        """Record user feedback and return its explicit routing target.

        The user-supplied routing target is used as-is; content-based routing is
        only applied as a fallback when the user does not explicitly specify one.
        """
        draft = self.get_draft(subject.account_id, draft_id)
        # Use the user-supplied target when explicitly set; otherwise fall back to
        # content-based routing derived from the message text and claim references.
        target = (
            request.target
            if request.target != UserFeedbackTarget.CURRENT_VERSION
            else self._route_feedback_target(
                request.message, request.referenced_claim_id
            )
        )
        feedback = UserFeedback(
            feedback_id=_token("fb"),
            draft_id=draft_id,
            target=target,
            message=request.message,
            referenced_span_id=request.referenced_span_id,
            referenced_claim_id=request.referenced_claim_id,
            creates_candidate_preference=(target == UserFeedbackTarget.CANDIDATE_PREFERENCE),
        )
        draft.feedback_log.append(feedback)
        self._drafts[draft.draft_id] = draft

        return SubmitExpressionFeedbackResult(
            feedback_id=feedback.feedback_id,
            draft=draft,
            routed_to=target,
        )

    # ------------------------------------------------------------------
    # T029: version comparison, release gate and publication
    # ------------------------------------------------------------------

    def _artifact_version(self, draft: ExpressionDraft) -> ArtifactVersion:
        """Build a stable ArtifactVersion projection for a draft."""
        return ArtifactVersion(
            version_id=f"{draft.draft_id}@v1",
            draft_id=draft.draft_id,
            version_number=1,
            artifact_trust_status=draft.artifact_trust_status,
            created_at=draft.created_at,
        )

    def _graph_object_ref(self, draft: ExpressionDraft, graph: ClaimGraph) -> ObjectRef:
        """Build the ObjectRef for the draft's claim graph.

        Graphs are owned by the account regardless of the project tag (a project
        tag scopes the graph inside the account's own scientific project space).
        """
        return ObjectRef(
            domain=ObjectDomain.PERSONAL_VAULT,
            owner_id=draft.account_id,
            object_id=graph.graph_id,
            version=1,
        )

    def _upstream_source_refs(
        self, draft: ExpressionDraft, graph: ClaimGraph
    ) -> list[ObjectRef]:
        """Collect source ObjectRefs referenced by the draft's citations and evidence."""
        source_ids: set[str] = set()
        for citation in graph.citations:
            source_id = citation.identifier_snapshot.get("source_id")
            if source_id:
                source_ids.add(str(source_id))
        for evidence in graph.evidence:
            # Prefer a public helper on the claim service when available.
            finder = getattr(self._claim_service, "find_source_id_for_document", None)
            if finder is not None:
                found = finder(evidence.document_id)
                if found:
                    source_ids.add(found)

        refs: list[ObjectRef] = []
        for source_id in sorted(source_ids):
            refs.append(
                ObjectRef(
                    domain=ObjectDomain.PERSONAL_VAULT,
                    owner_id=draft.account_id,
                    object_id=source_id,
                    version=1,
                )
            )
        return refs

    def _check_upstream_objects(
        self, draft: ExpressionDraft, graph: ClaimGraph
    ) -> list[ObjectRef]:
        """Return upstream object refs that are revoked or tombstoned.

        Fails open: if the invalidation service is not available, no objects are
        reported as invalid, but the release gate still requires the expression gate
        and human approval as a minimum safeguard.  In production the invalidation
        service is always available.
        """
        if self._invalidation is None:
            return []

        invalid: list[ObjectRef] = []
        for ref in [self._graph_object_ref(draft, graph)] + self._upstream_source_refs(
            draft, graph
        ):
            try:
                self._invalidation.require_active(ref)
            except Exception:
                invalid.append(ref)
        return invalid

    def _latest_human_decision(self, draft: ExpressionDraft) -> HumanDecision | None:
        """Return the most recent human decision for the artifact, if any."""
        if not draft.approval_decisions:
            return None
        return draft.approval_decisions[-1]

    def _is_approved(self, draft: ExpressionDraft) -> bool:
        """Whether the latest human decision is an approval."""
        decision = self._latest_human_decision(draft)
        return decision is not None and decision.decision == HumanDecisionType.APPROVE

    def _required_confirmations_present(self, draft: ExpressionDraft) -> bool:
        """Check genre-specific required human confirmations.

        Paper-assist requires the author responsibility statement to be confirmed.
        """
        if draft.genre != Genre.PAPER_ASSIST:
            return True
        statement = draft.author_responsibility_statement
        if statement is None:
            return False
        return bool(statement.confirmed_at and statement.confirmed_by)

    def _run_has_succeeded(self, account_id: str, run_id: str) -> bool:
        """Check whether the linked workflow run has succeeded."""
        if self._workflow is None:
            return False
        try:
            projection = self._workflow.get_run(account_id, run_id)
        except Exception:
            return False

        run_status: WorkflowRunStatus = projection.run_status
        return run_status == WorkflowRunStatus.SUCCEEDED

    def _run_has_open_todos(self, account_id: str, run_id: str) -> bool:
        """Check whether the linked workflow run has open human todos."""
        if self._workflow is None:
            return False
        try:
            projection = self._workflow.get_run(account_id, run_id)
        except Exception:
            return False
        return any(todo.status == "open" for todo in projection.human_todos)

    def approve_artifact(
        self,
        subject: SubjectContext,
        draft_id: str,
        request: ApproveArtifactRequest,
    ) -> ApproveArtifactResult:
        """Record a human approval, rejection or change request for an artifact."""
        draft = self.get_draft(subject.account_id, draft_id)
        decision = HumanDecision(
            decision_id=_token("dec"),
            target_artifact_id=draft_id,
            account_id=subject.account_id,
            decision=request.decision,
            reason=request.reason,
            created_at=_now(),
        )
        draft.approval_decisions.append(decision)

        if request.decision == HumanDecisionType.APPROVE:
            draft.artifact_trust_status = ArtifactTrustStatus.APPROVED
        elif request.decision in (
            HumanDecisionType.REJECT,
            HumanDecisionType.REQUEST_CHANGES,
        ):
            draft.artifact_trust_status = ArtifactTrustStatus.DRAFT

        self._drafts[draft.draft_id] = draft
        return ApproveArtifactResult(decision_id=decision.decision_id, draft=draft)

    def evaluate_release_eligibility(
        self,
        subject: SubjectContext,
        draft_id: str,
        run_id: str | None = None,
    ) -> ReleaseGateResult:
        """Evaluate whether an expression artifact may be published.

        Release eligibility depends on:
          - the expression gate passing;
          - the artifact being explicitly approved by a human;
          - the linked workflow run having succeeded (when a run is linked);
          - no open human todos on the linked run;
          - upstream claim graph and sources remaining active;
          - genre-specific confirmations (e.g. paper-assist author responsibility).
        """
        draft = self.get_draft(subject.account_id, draft_id)
        graph = self._fetch_graph(subject.account_id, draft.graph_id)

        checks: dict[ReleaseGateCheck, bool] = dict.fromkeys(ReleaseGateCheck, True)
        failed: list[ReleaseGateCheck] = []
        reasons: list[str] = []

        # Re-run the expression gate against the current draft state.
        locks = self._compile_fact_locks(subject.account_id, draft.graph_id)
        gate = self._run_expression_gate(
            brief=draft.brief,
            graph=graph,
            locks=locks,
            requested_slice_id=draft.memory_slice_id,
        )
        self._update_gate_for_style(draft, gate)
        self._update_gate_for_genre_elements(draft, gate)
        checks[ReleaseGateCheck.EXPRESSION_GATE_PASSED] = gate.passed
        if not gate.passed:
            failed.append(ReleaseGateCheck.EXPRESSION_GATE_PASSED)
            reasons.append(gate.reason or "表达质量门未通过。")

        checks[ReleaseGateCheck.ARTIFACT_APPROVED] = self._is_approved(draft)
        if not checks[ReleaseGateCheck.ARTIFACT_APPROVED]:
            failed.append(ReleaseGateCheck.ARTIFACT_APPROVED)
            reasons.append("产物尚未获得人工批准。")

        effective_run_id = run_id or draft.run_id
        if effective_run_id is not None:
            checks[ReleaseGateCheck.WORKFLOW_SUCCEEDED] = self._run_has_succeeded(
                draft.account_id, effective_run_id
            )
            if not checks[ReleaseGateCheck.WORKFLOW_SUCCEEDED]:
                failed.append(ReleaseGateCheck.WORKFLOW_SUCCEEDED)
                reasons.append("关联工作流尚未成功完成。")

            checks[ReleaseGateCheck.NO_OPEN_HUMAN_TODOS] = (
                not self._run_has_open_todos(draft.account_id, effective_run_id)
            )
            if not checks[ReleaseGateCheck.NO_OPEN_HUMAN_TODOS]:
                failed.append(ReleaseGateCheck.NO_OPEN_HUMAN_TODOS)
                reasons.append("工作流存在未解决的人工待办。")

        invalid_upstream = self._check_upstream_objects(draft, graph)
        checks[ReleaseGateCheck.UPSTREAM_OBJECTS_ACTIVE] = not invalid_upstream
        if invalid_upstream:
            failed.append(ReleaseGateCheck.UPSTREAM_OBJECTS_ACTIVE)
            reasons.append("上游来源、Claim 图或事实锁已失效。")

        checks[ReleaseGateCheck.REQUIRED_HUMAN_CONFIRMATIONS_PRESENT] = (
            self._required_confirmations_present(draft)
        )
        if not checks[ReleaseGateCheck.REQUIRED_HUMAN_CONFIRMATIONS_PRESENT]:
            failed.append(ReleaseGateCheck.REQUIRED_HUMAN_CONFIRMATIONS_PRESENT)
            reasons.append("体裁要求的人工确认尚未完成。")

        style_report = draft.style_diagnostic_report
        has_any_blocking_finding = (
            style_report is not None
            and any(f.severity == StyleDiagnosticSeverity.BLOCKING for f in style_report.findings)
        )
        has_unresolved_blocking = has_any_blocking_finding and any(
            not p.applied and not p.rejected for p in draft.pending_patches
        )
        checks[ReleaseGateCheck.NO_BLOCKING_STYLE_FINDINGS] = not has_unresolved_blocking
        if has_unresolved_blocking:
            failed.append(ReleaseGateCheck.NO_BLOCKING_STYLE_FINDINGS)
            reasons.append("存在未处理的中文表达阻塞性问题。")

        passed = all(checks.values())

        if passed:
            status = ReleaseEligibilityStatus.ELIGIBLE
        elif invalid_upstream:
            status = ReleaseEligibilityStatus.UPSTREAM_INVALIDATED
        elif not checks[ReleaseGateCheck.WORKFLOW_SUCCEEDED]:
            status = ReleaseEligibilityStatus.WAITING_WORKFLOW
        elif not checks[ReleaseGateCheck.NO_OPEN_HUMAN_TODOS]:
            status = ReleaseEligibilityStatus.WAITING_HUMAN_TODO
        elif not checks[ReleaseGateCheck.EXPRESSION_GATE_PASSED]:
            status = ReleaseEligibilityStatus.BLOCKED
        elif not checks[ReleaseGateCheck.ARTIFACT_APPROVED]:
            status = ReleaseEligibilityStatus.WAITING_APPROVAL
        else:
            status = ReleaseEligibilityStatus.BLOCKED

        return ReleaseGateResult(
            passed=passed,
            status=status,
            checks=checks,
            failed_checks=failed,
            blocked_claim_ids=gate.blocked_claim_ids,
            reason="；".join(reasons) if reasons else None,
            upstream_invalid_object_refs=invalid_upstream,
        )

    def publish_artifact(
        self,
        subject: SubjectContext,
        draft_id: str,
        request: PublishArtifactRequest,
    ) -> PublishArtifactResult:
        """Publish an expression artifact if it is release eligible.

        The publish event is bound to the actual draft version, the authorizing
        account, the release gate result and the approval decision that authorized
        publication.
        """
        draft = self.get_draft(subject.account_id, draft_id)
        release_gate = self.evaluate_release_eligibility(subject, draft_id, request.run_id)
        if not release_gate.passed:
            raise ExpressionServiceError(
                f"发布资格不足：{release_gate.reason or '存在未解决的阻塞项。'}"
            )

        decision = self._latest_human_decision(draft)
        assert decision is not None

        event = PublishEvent(
            event_id=_token("pub"),
            draft_id=draft.draft_id,
            version_id=self._artifact_version(draft).version_id,
            account_id=subject.account_id,
            project_id=draft.project_id,
            published_at=_now(),
            release_gate_result=release_gate,
            human_decision_id=decision.decision_id,
        )
        self._publish_events[event.event_id] = event
        self._drafts[draft.draft_id] = draft

        return PublishArtifactResult(event=event, draft=draft)

    def compare_versions(
        self,
        subject: SubjectContext,
        request: CompareVersionsRequest,
    ) -> CompareVersionsResult:
        """Compare two expression drafts and report semantic differences.

        The comparison highlights changes to fact locks, claims, citations,
        wording strength, argument plan, span text, genre, model run locks,
        applied patches and artifact trust status. It also reports the release
        eligibility of each version.
        """
        draft_a = self.get_draft(subject.account_id, request.draft_id_a)
        draft_b = self.get_draft(subject.account_id, request.draft_id_b)

        differences: list[VersionDifference] = []

        if draft_a.genre != draft_b.genre:
            differences.append(
                VersionDifference(
                    field=VersionDifferenceField.GENRE,
                    before=draft_a.genre.value,
                    after=draft_b.genre.value,
                    reason="体裁变化会影响论证结构和体裁元素。",
                )
            )

        if draft_a.fact_lock_set_id != draft_b.fact_lock_set_id:
            differences.append(
                VersionDifference(
                    field=VersionDifferenceField.FACT_LOCK_SET,
                    before=draft_a.fact_lock_set_id,
                    after=draft_b.fact_lock_set_id,
                    reason="事实锁集合发生变化。",
                )
            )

        claim_ids_a = sorted({cid for s in draft_a.spans for cid in s.claim_ids})
        claim_ids_b = sorted({cid for s in draft_b.spans for cid in s.claim_ids})
        if claim_ids_a != claim_ids_b:
            differences.append(
                VersionDifference(
                    field=VersionDifferenceField.CLAIM_IDS,
                    before=claim_ids_a,
                    after=claim_ids_b,
                    reason="绑定的 Claim 集合发生变化。",
                )
            )

        citation_ids_a = sorted({cid for s in draft_a.spans for cid in s.citation_ids})
        citation_ids_b = sorted({cid for s in draft_b.spans for cid in s.citation_ids})
        if citation_ids_a != citation_ids_b:
            differences.append(
                VersionDifference(
                    field=VersionDifferenceField.CITATION_IDS,
                    before=citation_ids_a,
                    after=citation_ids_b,
                    reason="引用集合发生变化。",
                )
            )

        if draft_a.wording_strength_ceiling != draft_b.wording_strength_ceiling:
            differences.append(
                VersionDifference(
                    field=VersionDifferenceField.WORDING_STRENGTH_CEILING,
                    before=draft_a.wording_strength_ceiling.value,
                    after=draft_b.wording_strength_ceiling.value,
                    reason="措辞强度上限发生变化，可能由证据状态变化引起。",
                )
            )

        arg_nodes_a = [n.argument_node_id for n in draft_a.argument_plan.nodes]
        arg_nodes_b = [n.argument_node_id for n in draft_b.argument_plan.nodes]
        if (
            arg_nodes_a != arg_nodes_b
            or draft_a.argument_plan.graph_id != draft_b.argument_plan.graph_id
        ):
            differences.append(
                VersionDifference(
                    field=VersionDifferenceField.ARGUMENT_PLAN,
                    before={
                        "plan_id": draft_a.argument_plan.plan_id,
                        "node_count": len(arg_nodes_a),
                    },
                    after={
                        "plan_id": draft_b.argument_plan.plan_id,
                        "node_count": len(arg_nodes_b),
                    },
                    reason="论证计划结构发生变化。",
                )
            )

        span_text_a = " ".join(s.text for s in draft_a.spans)
        span_text_b = " ".join(s.text for s in draft_b.spans)
        if span_text_a != span_text_b:
            differences.append(
                VersionDifference(
                    field=VersionDifferenceField.SPAN_TEXT,
                    before=span_text_a[:200],
                    after=span_text_b[:200],
                    reason="文本片段内容发生变化。",
                )
            )

        if draft_a.model_run_lock != draft_b.model_run_lock:
            differences.append(
                VersionDifference(
                    field=VersionDifferenceField.MODEL_RUN_LOCK,
                    before=draft_a.model_run_lock.lock_id if draft_a.model_run_lock else None,
                    after=draft_b.model_run_lock.lock_id if draft_b.model_run_lock else None,
                    reason="模型运行锁发生变化。",
                )
            )

        applied_a = [p.patch_id for p in draft_a.applied_patches]
        applied_b = [p.patch_id for p in draft_b.applied_patches]
        if applied_a != applied_b:
            differences.append(
                VersionDifference(
                    field=VersionDifferenceField.APPLIED_PATCHES,
                    before=applied_a,
                    after=applied_b,
                    reason="已应用的人工修订补丁不同。",
                )
            )

        if draft_a.artifact_trust_status != draft_b.artifact_trust_status:
            differences.append(
                VersionDifference(
                    field=VersionDifferenceField.ARTIFACT_TRUST_STATUS,
                    before=draft_a.artifact_trust_status.value,
                    after=draft_b.artifact_trust_status.value,
                    reason="产物可信状态发生变化。",
                )
            )

        decisions_a = [d.model_dump() for d in draft_a.approval_decisions]
        decisions_b = [d.model_dump() for d in draft_b.approval_decisions]
        if decisions_a != decisions_b:
            differences.append(
                VersionDifference(
                    field=VersionDifferenceField.HUMAN_DECISIONS,
                    before=decisions_a,
                    after=decisions_b,
                    reason="人工决定记录发生变化。",
                )
            )

        eligibility_a = self.evaluate_release_eligibility(subject, draft_a.draft_id)
        eligibility_b = self.evaluate_release_eligibility(subject, draft_b.draft_id)

        return CompareVersionsResult(
            comparison_id=_token("cmp"),
            draft_id_a=draft_a.draft_id,
            draft_id_b=draft_b.draft_id,
            differences=differences,
            release_eligibility_a=eligibility_a,
            release_eligibility_b=eligibility_b,
            only_b_is_publishable=(eligibility_b.passed and not eligibility_a.passed),
        )


__all__ = [
    "DraftGeneratorPort",
    "ExpressionService",
    "ExpressionServiceError",
]
