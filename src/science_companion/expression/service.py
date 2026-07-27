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

import secrets
from datetime import UTC, datetime
from typing import Protocol

from science_companion.ai import ModelGateway
from science_companion.contracts.ai import ModelRunLock
from science_companion.contracts.expression import (
    ArgumentNode,
    ArgumentNodeRole,
    ArgumentPlan,
    DraftSpan,
    ExpressionBrief,
    ExpressionDraft,
    ExpressionDraftRequest,
    ExpressionDraftResult,
    ExpressionDraftStatus,
    ExpressionGateCheck,
    ExpressionGateResult,
    Genre,
    GenreContract,
    RiskTier,
)
from science_companion.contracts.identity import SubjectContext
from science_companion.contracts.profiles import ProfileSlice
from science_companion.contracts.projects import ObjectDomain
from science_companion.contracts.science import (
    ClaimGraph,
    ClaimImportance,
    ClaimTrustStatus,
    FactLock,
    FactLockSet,
    FactLockType,
    ValidationReport,
)
from science_companion.contracts.workflows import RunContextEnvelope
from science_companion.profiles import ProfileError, ProfileService
from science_companion.science import ClaimEvidenceService, ScienceError


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

        # If the brief asks for a task goal framing, add a non-claim transition
        # span that still binds to the brief so it is auditable.
        if brief.task_goal:
            spans.insert(
                0,
                DraftSpan(
                    span_id=_token("span"),
                    text=brief.task_goal,
                    argument_node_ids=[
                        node.argument_node_id
                        for node in argument_plan.nodes
                        if node.role == ArgumentNodeRole.QUESTION
                    ],
                    claim_ids=[],
                    citation_ids=[],
                    fact_lock_ids=[],
                    generated_by_run=run_context.run_id if run_context else None,
                ),
            )

        return spans


class ExpressionService:
    """Application service for generating fact-lock-bound expression drafts."""

    def __init__(
        self,
        claim_service: ClaimEvidenceService,
        profile_service: ProfileService | None = None,
        model_gateway: ModelGateway | None = None,
        draft_generator: DraftGeneratorPort | None = None,
    ) -> None:
        self._claim_service = claim_service
        self._profile_service = profile_service
        self._model_gateway = model_gateway
        self._draft_generator = draft_generator or _DeterministicDraftGenerator()
        self._drafts: dict[str, ExpressionDraft] = {}

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
                e.relation.value == "limits" for e in graph.evidence if e.claim_id == claim.claim_id
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

    def _maybe_invoke_model(
        self,
        subject: SubjectContext,
        brief: ExpressionBrief,
        graph: ClaimGraph,
    ) -> ModelRunLock | None:
        if self._model_gateway is None:
            return None
        run_context = RunContextEnvelope(
            run_id=_token("run"),
            account_id=subject.account_id,
            project_id=brief.brief_id,
            workflow_name="expression_draft_generation",
            workflow_version="1",
            object_domain=ObjectDomain.PERSONAL_VAULT,
            submitted_at=_now(),
        )
        result = self._model_gateway.invoke(
            capability_name="expression_draft_generation",
            capability_version="1",
            run_context=run_context,
            payload={
                "brief_id": brief.brief_id,
                "graph_id": graph.graph_id,
                "genre": brief.genre.value,
            },
        )
        return result.lock

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

        passed = all(checks.values())
        failed = [check for check, ok in checks.items() if not ok]

        if failed:
            if ExpressionGateCheck.RISK_TIER_HUMAN_REVIEW in failed:
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

        run_context: RunContextEnvelope | None = None
        if self._model_gateway is not None:
            run_context = RunContextEnvelope(
                run_id=_token("run"),
                account_id=subject.account_id,
                project_id=request.project_id or brief.brief_id,
                workflow_name="expression_draft_generation",
                workflow_version="1",
                object_domain=ObjectDomain.PERSONAL_VAULT,
                submitted_at=_now(),
            )

        argument_plan = self._build_argument_plan(brief, graph)
        spans = self._draft_generator.generate(
            brief=brief,
            graph=graph,
            locks=locks,
            argument_plan=argument_plan,
            memory_slice=memory_slice,
            run_context=run_context,
        )

        model_lock = self._maybe_invoke_model(subject, brief, graph)
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
            graph_id=graph.graph_id,
            account_id=subject.account_id,
            project_id=request.project_id,
            status=gate.draft_status,
            status_reason=gate.reason,
            argument_plan=argument_plan,
            spans=spans,
            fact_lock_set_id=locks.set_id,
            memory_slice_id=request.memory_slice_id,
            personalization_note=personalization_note,
            wording_strength_ceiling=validation_report.wording_strength_ceiling,
            model_run_lock=model_lock,
            created_at=_now(),
        )

        return ExpressionDraftResult(
            draft=draft,
            gate=gate,
            model_run_lock=model_lock,
        )

    def get_draft(self, account_id: str, draft_id: str) -> ExpressionDraft:
        """Return a draft if it belongs to the account."""
        stored = self._drafts.get(draft_id)
        if stored is None or stored.account_id != account_id:
            raise ExpressionServiceError("草稿不存在或没有访问权限。")
        return stored

    def create_draft_and_store(
        self,
        subject: SubjectContext,
        request: ExpressionDraftRequest,
    ) -> ExpressionDraftResult:
        """Generate and store a draft so it can be retrieved later."""
        result = self.create_draft(subject, request)
        self._drafts[result.draft.draft_id] = result.draft
        return result


__all__ = [
    "DraftGeneratorPort",
    "ExpressionService",
    "ExpressionServiceError",
]
