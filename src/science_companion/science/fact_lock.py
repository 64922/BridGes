"""Fact lock compilation, evidence conflict detection and honest degradation.

T016 implements the deterministic control-plane layer that turns a claim graph
into:

- a `FactLockSet` of immutable facts (numbers, units, objects, relations,
  qualifiers, terms, formulas, citations, strength ceilings);
- explicit `Conflict` records when supporting and refuting evidence coexist;
- a `ValidationReport` that maps each claim to an honest-degradation status and
  wording strength ceiling.

The module deliberately does not call models. Extraction is rule-based and
auditable so that downstream expression nodes (T025) and the task stage (T006)
can trust the locks without re-executing model inference.
"""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime

from science_companion.contracts.science import (
    Citation,
    CitationVerificationStatus,
    Claim,
    ClaimGraph,
    ClaimImportance,
    ClaimTrustStatus,
    ClaimType,
    Conflict,
    ConflictResolutionStatus,
    ConflictType,
    Evidence,
    EvidenceRelation,
    EvidenceState,
    FactLock,
    FactLockSet,
    FactLockType,
    LifecycleStatus,
    ScientificQualityGateCheck,
    ScientificQualityGateResult,
    ValidationReport,
    WordingStrength,
)

# Stable ordering for honest-degradation severity. Higher is more degraded.
_STATUS_SEVERITY: dict[ClaimTrustStatus, int] = {
    ClaimTrustStatus.VERIFIED: 1,
    ClaimTrustStatus.QUALIFIED: 2,
    ClaimTrustStatus.PARTIAL: 3,
    ClaimTrustStatus.METADATA_ONLY: 4,
    ClaimTrustStatus.CONFLICTED: 5,
    ClaimTrustStatus.QUARANTINED: 6,
    ClaimTrustStatus.BLOCKED: 7,
}


_NUMBER_UNIT_RE = re.compile(
    r"(?P<value>-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*(?P<unit>[°℃℉Ωμ²³¹%/·a-zA-Z\u4e00-\u9fff]+)?"
)

_TERM_FORMULA_RE = re.compile(
    r"([A-Za-z0-9α-ωΑ-Ω_\-+]+\s*[=＝≈≠≤≥⇔→⇒]\s*[A-Za-z0-9α-ωΑ-Ω_\-+\s\(\)\[\]\/\.^]+)"
)

_RELATION_KEYWORDS: dict[ClaimType, tuple[str, ...]] = {
    ClaimType.CAUSAL: ("导致", "引起", "造成", "causes", "leads to", "due to"),
    ClaimType.COMPARATIVE: (">", "<", "高于", "低于", "大于", "小于", "比", "compared to"),
    ClaimType.MECHANISTIC: ("通过", "机制", "via", "mechanism", "pathway"),
}


def _now() -> datetime:
    return datetime.now(UTC)


def _token(prefix: str = "lock") -> str:
    return f"{prefix}-{secrets.token_urlsafe(12)}"


def _wording_strength_for_state(state: EvidenceState, has_limits: bool) -> WordingStrength:
    """Map an aggregated evidence state to a deterministic wording ceiling."""
    if state == EvidenceState.CONFLICTED:
        return WordingStrength.CONFLICTING
    if state == EvidenceState.REFUTED:
        return WordingStrength.VERY_LOW
    if state == EvidenceState.INSUFFICIENT:
        return WordingStrength.VERY_LOW
    if state == EvidenceState.UNKNOWN:
        return WordingStrength.UNASSESSABLE
    if state == EvidenceState.LIMITED:
        return WordingStrength.LOW
    # SUPPORTED
    return WordingStrength.MODERATE if has_limits else WordingStrength.HIGH


def _claim_status_from_state(
    state: EvidenceState,
    importance: ClaimImportance,
    has_limits: bool = False,
) -> ClaimTrustStatus:
    """Derive a claim-level honest-degradation status."""
    if state in {EvidenceState.CONFLICTED, EvidenceState.REFUTED}:
        return ClaimTrustStatus.CONFLICTED
    if state == EvidenceState.INSUFFICIENT:
        return (
            ClaimTrustStatus.PARTIAL
            if importance == ClaimImportance.KEY
            else ClaimTrustStatus.QUALIFIED
        )
    if state == EvidenceState.UNKNOWN:
        return ClaimTrustStatus.QUALIFIED
    if state == EvidenceState.LIMITED:
        return ClaimTrustStatus.QUALIFIED
    if state == EvidenceState.SUPPORTED and has_limits:
        return ClaimTrustStatus.QUALIFIED
    return ClaimTrustStatus.VERIFIED


def _extract_number_unit_locks(claim: Claim, evidence_ids: list[str]) -> list[FactLock]:
    """Extract exact-value locks for numbers with units."""
    locks: list[FactLock] = []
    seen: set[str] = set()
    for match in _NUMBER_UNIT_RE.finditer(claim.text):
        value = match.group("value")
        unit = (match.group("unit") or "").strip()
        if not unit:
            continue
        canonical = f"value={value},unit={unit}"
        if canonical in seen:
            continue
        seen.add(canonical)
        locks.append(
            FactLock(
                lock_id=_token("num"),
                claim_id=claim.claim_id,
                lock_type=FactLockType.EXACT_VALUE,
                canonical_value=canonical,
                allowed_variants=[f"{value} {unit}"],
                forbidden_transformations=[
                    "不得改变数值",
                    "不得改变单位",
                    "不得省略误差或置信区间",
                ],
                required_qualifiers=[],
                evidence_ids=evidence_ids,
                citation_ids=[],
                wording_strength_ceiling=WordingStrength.HIGH,
                verification_method="rule",
            )
        )
    return locks


def _extract_term_formula_locks(claim: Claim, evidence_ids: list[str]) -> list[FactLock]:
    """Extract term/formula locks from definitions, proofs and quantitative claims."""
    locks: list[FactLock] = []
    if claim.claim_type not in {
        ClaimType.DEFINITION,
        ClaimType.PROOF_STEP,
        ClaimType.QUANTITATIVE,
        ClaimType.MECHANISTIC,
    }:
        return locks

    seen: set[str] = set()
    for match in _TERM_FORMULA_RE.finditer(claim.text):
        canonical = match.group(0).strip()
        if not canonical or canonical in seen:
            continue
        seen.add(canonical)
        locks.append(
            FactLock(
                lock_id=_token("term"),
                claim_id=claim.claim_id,
                lock_type=FactLockType.TERM_FORMULA,
                canonical_value=canonical,
                allowed_variants=[],
                forbidden_transformations=[
                    "不得改变符号或变量定义",
                    "不得改变公式结构",
                ],
                required_qualifiers=[],
                evidence_ids=evidence_ids,
                citation_ids=[],
                wording_strength_ceiling=WordingStrength.HIGH,
                verification_method="rule",
            )
        )

    # Fallback: the whole claim text is the term/formula lock for definitions.
    if claim.claim_type == ClaimType.DEFINITION and not locks:
        locks.append(
            FactLock(
                lock_id=_token("term"),
                claim_id=claim.claim_id,
                lock_type=FactLockType.TERM_FORMULA,
                canonical_value=claim.text,
                allowed_variants=[],
                forbidden_transformations=["不得改变定义对象或内涵"],
                required_qualifiers=[],
                evidence_ids=evidence_ids,
                citation_ids=[],
                wording_strength_ceiling=WordingStrength.HIGH,
                verification_method="rule",
            )
        )
    return locks


def _extract_relation_locks(claim: Claim, evidence_ids: list[str]) -> list[FactLock]:
    """Extract relation locks for causal/comparative/mechanistic claims."""
    if claim.claim_type not in _RELATION_KEYWORDS:
        return []
    keywords = _RELATION_KEYWORDS[claim.claim_type]
    if not any(kw in claim.text for kw in keywords):
        return []

    forbidden: list[str] = ["不得改变关系方向"]
    if claim.claim_type == ClaimType.CAUSAL:
        forbidden.append("不得将因果降级为相关或共现")
        forbidden.append("不得将相关升级为因果")
    if claim.claim_type == ClaimType.COMPARATIVE:
        forbidden.append("不得颠倒比较方向")

    return [
        FactLock(
            lock_id=_token("rel"),
            claim_id=claim.claim_id,
            lock_type=FactLockType.RELATION,
            canonical_value=claim.text,
            allowed_variants=[],
            forbidden_transformations=forbidden,
            required_qualifiers=[claim.scope] if claim.scope else [],
            evidence_ids=evidence_ids,
            citation_ids=[],
            wording_strength_ceiling=WordingStrength.HIGH,
            verification_method="rule",
        )
    ]


def _extract_condition_locks(claim: Claim, evidence_ids: list[str]) -> list[FactLock]:
    """Extract condition locks from scope qualifiers."""
    if not claim.scope:
        return []
    return [
        FactLock(
            lock_id=_token("cond"),
            claim_id=claim.claim_id,
            lock_type=FactLockType.CONDITION,
            canonical_value=claim.scope,
            allowed_variants=[],
            forbidden_transformations=["不得删除适用范围或限定条件"],
            required_qualifiers=[claim.scope],
            evidence_ids=evidence_ids,
            citation_ids=[],
            wording_strength_ceiling=WordingStrength.HIGH,
            verification_method="rule",
        )
    ]


def _extract_citation_locks(claim: Claim, citations: list[Citation]) -> list[FactLock]:
    """Extract identifier locks for bound citations."""
    if not citations:
        return []
    citation_ids = [c.citation_id for c in citations]
    evidence_ids = list({c.evidence_id for c in citations})
    identifier_parts: list[str] = []
    for citation in citations:
        snapshot = citation.identifier_snapshot or {}
        for key in sorted(snapshot):
            value = snapshot.get(key)
            if value:
                identifier_parts.append(f"{key}={value}")
    canonical = ";".join(identifier_parts) if identifier_parts else "citation-bound"
    return [
        FactLock(
            lock_id=_token("cite"),
            claim_id=claim.claim_id,
            lock_type=FactLockType.IDENTIFIER,
            canonical_value=canonical,
            allowed_variants=[],
            forbidden_transformations=[
                "不得编造或替换引用",
                "不得更改来源版本",
            ],
            required_qualifiers=[],
            evidence_ids=evidence_ids,
            citation_ids=citation_ids,
            wording_strength_ceiling=WordingStrength.HIGH,
            verification_method="rule",
        )
    ]


def _extract_strength_lock(
    claim: Claim, state: EvidenceState, has_limits: bool
) -> FactLock:
    """Extract a strength lock from the evidence-derived wording ceiling."""
    ceiling = _wording_strength_for_state(state, has_limits)
    return FactLock(
        lock_id=_token("str"),
        claim_id=claim.claim_id,
        lock_type=FactLockType.STRENGTH,
        canonical_value=ceiling.value,
        allowed_variants=[],
        forbidden_transformations=["不得超过证据允许的措辞强度"],
        required_qualifiers=[],
        evidence_ids=claim.evidence_ids,
        citation_ids=claim.citation_ids,
        wording_strength_ceiling=ceiling,
        verification_method="rule",
    )


def _assess_evidence_state(
    claim_evidence: list[Evidence],
) -> EvidenceState:
    """Aggregate evidence relations for a claim into a single evidence state."""
    if not claim_evidence:
        return EvidenceState.INSUFFICIENT

    active = [e for e in claim_evidence if e.invalidated_at is None]
    if not active:
        return EvidenceState.UNKNOWN

    relations = {e.relation for e in active}
    if EvidenceRelation.REFUTES in relations and EvidenceRelation.SUPPORTS in relations:
        return EvidenceState.CONFLICTED
    if EvidenceRelation.REFUTES in relations:
        return EvidenceState.REFUTED
    if EvidenceRelation.SUPPORTS not in relations and EvidenceRelation.LIMITS in relations:
        return EvidenceState.LIMITED
    if EvidenceRelation.SUPPORTS not in relations:
        return EvidenceState.UNKNOWN

    # SUPPORTS present. Check whether the evidence is trustworthy enough.
    if any(
        e.lifecycle_status == LifecycleStatus.UNKNOWN
        or e.source_proximity == "metadata_only"
        for e in active
    ):
        return EvidenceState.UNKNOWN

    return EvidenceState.SUPPORTED


def compile_fact_locks(graph: ClaimGraph) -> FactLockSet:
    """Compile a deterministic fact lock set from a claim graph.

    Each claim gets locks for its citations, numbers/units, terms/formulas,
    relations, conditions and evidence-derived wording strength. The result is
    the immutable boundary that expression nodes must respect.
    """
    locks: list[FactLock] = []
    evidence_index: dict[str, Evidence] = {e.evidence_id: e for e in graph.evidence}
    citation_index: dict[str, Citation] = {c.citation_id: c for c in graph.citations}

    for claim in graph.claims:
        claim_evidence = [
            evidence_index[eid]
            for eid in claim.evidence_ids
            if eid in evidence_index
        ]
        claim_citations = [
            citation_index[cid]
            for cid in claim.citation_ids
            if cid in citation_index
        ]
        active_evidence = [e for e in claim_evidence if e.invalidated_at is None]
        evidence_ids = [e.evidence_id for e in active_evidence]

        state = _assess_evidence_state(claim_evidence)
        has_limits = any(e.relation == EvidenceRelation.LIMITS for e in active_evidence)

        locks.extend(_extract_citation_locks(claim, claim_citations))
        locks.extend(_extract_number_unit_locks(claim, evidence_ids))
        locks.extend(_extract_term_formula_locks(claim, evidence_ids))
        locks.extend(_extract_relation_locks(claim, evidence_ids))
        locks.extend(_extract_condition_locks(claim, evidence_ids))
        locks.append(_extract_strength_lock(claim, state, has_limits))

    return FactLockSet(
        set_id=_token("set"),
        graph_id=graph.graph_id,
        account_id=graph.account_id,
        project_id=graph.project_id,
        locks=locks,
        created_at=_now(),
    )


def detect_conflicts(graph: ClaimGraph) -> list[Conflict]:
    """Detect evidence conflicts that cannot be auto-resolved.

    The current implementation surfaces per-claim conflicts when both supporting
    and refuting evidence exist. Cross-claim conflict detection is intentionally
    left minimal; it will be extended as domain packs arrive (T040+).
    """
    conflicts: list[Conflict] = []
    evidence_index = {e.evidence_id: e for e in graph.evidence}

    for claim in graph.claims:
        claim_evidence = [
            evidence_index[eid]
            for eid in claim.evidence_ids
            if eid in evidence_index
        ]
        claim_evidence = [e for e in claim_evidence if e.invalidated_at is None]
        relations = {e.relation for e in claim_evidence}
        if EvidenceRelation.SUPPORTS in relations and EvidenceRelation.REFUTES in relations:
            conflicts.append(
                Conflict(
                    conflict_id=_token("conf"),
                    graph_id=graph.graph_id,
                    claim_ids=[claim.claim_id],
                    evidence_ids=[e.evidence_id for e in claim_evidence],
                    conflict_type=ConflictType.TRUE_DISAGREEMENT,
                    materiality=claim.importance.value,
                    resolution_status=ConflictResolutionStatus.OPEN,
                    user_visible_summary=(
                        f"Claim {claim.claim_id} 同时存在支持与反驳证据，"
                        "目前不能给出单一确定结论。"
                    ),
                )
            )
    return conflicts


def _derive_graph_status(
    claim_statuses: dict[str, ClaimTrustStatus],
    had_candidates: bool,
) -> ClaimTrustStatus:
    """Pick the most degraded status among claims."""
    if not claim_statuses:
        return ClaimTrustStatus.METADATA_ONLY if not had_candidates else ClaimTrustStatus.BLOCKED

    statuses = set(claim_statuses.values())
    if ClaimTrustStatus.BLOCKED in statuses:
        return ClaimTrustStatus.BLOCKED
    if ClaimTrustStatus.CONFLICTED in statuses:
        return ClaimTrustStatus.CONFLICTED
    if ClaimTrustStatus.PARTIAL in statuses:
        return ClaimTrustStatus.PARTIAL
    if ClaimTrustStatus.METADATA_ONLY in statuses:
        return ClaimTrustStatus.METADATA_ONLY
    if ClaimTrustStatus.QUALIFIED in statuses:
        return ClaimTrustStatus.QUALIFIED
    return ClaimTrustStatus.VERIFIED


def _wording_ceiling(
    per_claim: dict[str, WordingStrength],
) -> WordingStrength:
    """Return the most restrictive wording ceiling across claims."""
    severity: dict[WordingStrength, int] = {
        WordingStrength.HIGH: 1,
        WordingStrength.MODERATE: 2,
        WordingStrength.LOW: 3,
        WordingStrength.VERY_LOW: 4,
        WordingStrength.UNASSESSABLE: 5,
        WordingStrength.METADATA_ONLY: 6,
        WordingStrength.CONFLICTING: 7,
    }
    if not per_claim:
        return WordingStrength.UNASSESSABLE
    return max(per_claim.values(), key=lambda s: severity[s])


def run_scientific_quality_gate(
    graph: ClaimGraph,
    fact_lock_set: FactLockSet,
    conflicts: list[Conflict],
    evidence_states: dict[str, EvidenceState],
) -> ScientificQualityGateResult:
    """Run deterministic scientific quality gate over a claim graph."""
    checks: dict[ScientificQualityGateCheck, bool] = dict.fromkeys(ScientificQualityGateCheck, True)
    blocked_claim_ids: list[str] = []
    reasons: list[str] = []

    evidence_index = {e.evidence_id: e for e in graph.evidence}
    citation_index = {c.citation_id: c for c in graph.citations}

    key_claims = [c for c in graph.claims if c.importance == ClaimImportance.KEY]
    if not key_claims and graph.status != ClaimTrustStatus.BLOCKED:
        checks[ScientificQualityGateCheck.KEY_CLAIM_COVERAGE] = False
        reasons.append("没有关键 Claim。")

    locks_by_claim: dict[str, list[FactLock]] = {}
    for lock in fact_lock_set.locks:
        locks_by_claim.setdefault(lock.claim_id, []).append(lock)

    for claim in graph.claims:
        if claim.importance != ClaimImportance.KEY:
            continue

        if not claim.evidence_ids:
            checks[ScientificQualityGateCheck.KEY_CLAIM_COVERAGE] = False
            blocked_claim_ids.append(claim.claim_id)
            reasons.append(f"关键 Claim {claim.claim_id} 缺少 Evidence。")
            continue

        claim_locks = locks_by_claim.get(claim.claim_id, [])
        if not claim_locks or not any(
            lock.lock_type != FactLockType.STRENGTH for lock in claim_locks
        ):
            checks[ScientificQualityGateCheck.FACT_LOCK_CONSISTENT] = False
            blocked_claim_ids.append(claim.claim_id)
            reasons.append(f"关键 Claim {claim.claim_id} 未生成事实锁。")

        has_support = False
        has_refute = False
        for eid in claim.evidence_ids:
            evidence = evidence_index.get(eid)
            if evidence is None:
                continue
            if evidence.invalidated_at is not None:
                checks[ScientificQualityGateCheck.SOURCE_ACTIVE] = False
                blocked_claim_ids.append(claim.claim_id)
                reasons.append(f"Evidence {eid} 已失效。")
            if evidence.relation == EvidenceRelation.SUPPORTS:
                has_support = True
            if evidence.relation == EvidenceRelation.REFUTES:
                has_refute = True

        if has_support and has_refute and not any(
            claim.claim_id in c.claim_ids for c in conflicts
        ):
            # Conflicts are legal states, not automatic failures, as long as they
            # are disclosed and not suppressed.
            checks[ScientificQualityGateCheck.CONFLICT_DISCLOSED] = False
            blocked_claim_ids.append(claim.claim_id)
            reasons.append(f"Claim {claim.claim_id} 的冲突未披露。")

        for cid in claim.citation_ids:
            citation = citation_index.get(cid)
            if citation is None:
                checks[ScientificQualityGateCheck.NO_FABRICATED_CITATIONS] = False
                blocked_claim_ids.append(claim.claim_id)
                reasons.append(f"Claim {claim.claim_id} 引用了不存在的 Citation。")
                continue
            if citation.verification_status in {
                CitationVerificationStatus.UNLOCATABLE,
                CitationVerificationStatus.SOURCE_REVOKED,
                CitationVerificationStatus.SOURCE_SUPERSEDED,
            }:
                checks[ScientificQualityGateCheck.CITATION_LOCATABLE] = False
                blocked_claim_ids.append(claim.claim_id)
                reasons.append(f"Citation {cid} 无法定位、来源已撤权或已有新版本。")

    # Evidence preserved: if a claim is conflicted, both sides must still be
    # present in the graph evidence list.
    for conflict in conflicts:
        present_evidence = {eid for eid in conflict.evidence_ids if eid in evidence_index}
        if len(present_evidence) < 2:
            checks[ScientificQualityGateCheck.EVIDENCE_PRESERVED] = False
            reasons.append(f"冲突 {conflict.conflict_id} 有一侧证据已被移除。")

    # Wording strength: actual strength is not present in T016 (expression nodes
    # own actual strength). The gate only checks that a ceiling was derived.
    if any(state == EvidenceState.UNKNOWN for state in evidence_states.values()):
        checks[ScientificQualityGateCheck.WORDING_STRENGTH_WITHIN_EVIDENCE] = False
        reasons.append("存在证据状态未知的 Claim，无法确定措辞上限。")

    passed = all(checks.values())
    failed = [check for check, ok in checks.items() if not ok]

    graph_status = graph.status
    if not passed and graph_status in {ClaimTrustStatus.VERIFIED, ClaimTrustStatus.QUALIFIED}:
        graph_status = ClaimTrustStatus.BLOCKED

    return ScientificQualityGateResult(
        passed=passed,
        graph_status=graph_status,
        checks=checks,
        failed_checks=failed,
        blocked_claim_ids=list(set(blocked_claim_ids)),
        reason="；".join(reasons) if reasons else None,
    )


def apply_honest_degradation(
    graph: ClaimGraph,
    *,
    had_candidates: bool = True,
) -> ValidationReport:
    """Analyse a claim graph and produce a validation report.

    The report records evidence states, conflicts, fact locks, wording ceilings,
    scientific quality gate results and recommended recovery actions. It does
    not mutate the graph; callers decide whether to update the graph status.
    """
    evidence_index = {e.evidence_id: e for e in graph.evidence}
    conflicts = detect_conflicts(graph)
    fact_lock_set = compile_fact_locks(graph)

    evidence_states: dict[str, EvidenceState] = {}
    per_claim_ceiling: dict[str, WordingStrength] = {}
    claim_statuses: dict[str, ClaimTrustStatus] = {}
    missing_evidence: list[str] = []
    recovery_actions: set[str] = set()
    human_gate_required = False
    human_gate_reasons: list[str] = []

    for claim in graph.claims:
        claim_evidence = [
            evidence_index[eid]
            for eid in claim.evidence_ids
            if eid in evidence_index
        ]
        state = _assess_evidence_state(claim_evidence)
        evidence_states[claim.claim_id] = state

        has_limits = any(e.relation == EvidenceRelation.LIMITS for e in claim_evidence)
        per_claim_ceiling[claim.claim_id] = _wording_strength_for_state(state, has_limits)

        if state == EvidenceState.INSUFFICIENT:
            missing_evidence.append(claim.claim_id)
            recovery_actions.add("add_evidence")
            recovery_actions.add("revise_claim")
            if claim.importance == ClaimImportance.KEY:
                human_gate_required = True
                human_gate_reasons.append(f"关键 Claim {claim.claim_id} 证据不足")

        if state in {EvidenceState.CONFLICTED, EvidenceState.REFUTED}:
            recovery_actions.add("add_evidence")
            recovery_actions.add("human_review")
            human_gate_required = True
            human_gate_reasons.append(f"Claim {claim.claim_id} 存在证据冲突")

        if state == EvidenceState.UNKNOWN:
            recovery_actions.add("add_evidence")
            recovery_actions.add("refresh_source")

        claim_statuses[claim.claim_id] = _claim_status_from_state(
            state, claim.importance, has_limits=has_limits
        )

    graph_status = _derive_graph_status(claim_statuses, had_candidates)

    # Honour explicit upstream statuses (e.g. METADATA_ONLY when no candidates
    # were retrieved, or BLOCKED when the model call failed). Do not silently
    # upgrade a more degraded explicit status.
    initial_status = graph.status
    if _STATUS_SEVERITY.get(initial_status, 0) > _STATUS_SEVERITY.get(graph_status, 0):
        graph_status = initial_status

    scientific_gate = run_scientific_quality_gate(
        graph=graph,
        fact_lock_set=fact_lock_set,
        conflicts=conflicts,
        evidence_states=evidence_states,
    )

    if not scientific_gate.passed and graph_status in {
        ClaimTrustStatus.VERIFIED,
        ClaimTrustStatus.QUALIFIED,
    }:
        graph_status = ClaimTrustStatus.BLOCKED
        recovery_actions.add("human_review")
        human_gate_required = True
        human_gate_reasons.append("科学质量门未通过")

    if conflicts:
        graph_status = max(
            {graph_status, ClaimTrustStatus.CONFLICTED},
            key=lambda s: _STATUS_SEVERITY[s],
        )

    # A graph with no retrievable candidates remains at the metadata-only ceiling.
    wording_ceiling = (
        WordingStrength.METADATA_ONLY
        if graph_status == ClaimTrustStatus.METADATA_ONLY
        else _wording_ceiling(per_claim_ceiling)
    )

    return ValidationReport(
        report_id=_token("report"),
        graph_id=graph.graph_id,
        account_id=graph.account_id,
        project_id=graph.project_id,
        status=graph_status,
        previous_status=graph.status,
        evidence_states=evidence_states,
        conflicts=conflicts,
        missing_evidence_claim_ids=missing_evidence,
        blocked_claim_ids=scientific_gate.blocked_claim_ids,
        fact_lock_set_id=fact_lock_set.set_id,
        wording_strength_ceiling=wording_ceiling,
        scientific_gate=scientific_gate,
        human_gate_required=human_gate_required,
        human_gate_reason="；".join(human_gate_reasons) if human_gate_reasons else None,
        recovery_actions=sorted(recovery_actions),
        created_at=_now(),
    )


def update_claim_graph_with_report(
    graph: ClaimGraph,
    report: ValidationReport,
) -> None:
    """Mutate a claim graph to reflect the honest-degradation report.

    This is an explicit, auditable update; it does not silently overwrite the
    previous status because `previous_status` is recorded in the report.
    """
    graph.status = report.status
    graph.status_reason = (
        f"诚实降级分析结果：{report.status.value}。"
        f"{' '.join(report.recovery_actions)}"
    )

    evidence_index = {e.evidence_id: e for e in graph.evidence}
    for claim in graph.claims:
        state = report.evidence_states.get(claim.claim_id)
        if state is None:
            continue
        claim_evidence = [
            evidence_index[eid]
            for eid in claim.evidence_ids
            if eid in evidence_index
        ]
        has_limits = any(e.relation == EvidenceRelation.LIMITS for e in claim_evidence)
        claim.status = _claim_status_from_state(state, claim.importance, has_limits=has_limits)
        claim.status_reason = (
            f"证据状态：{state.value}；"
            f"措辞上限：{_wording_strength_for_state(state, has_limits).value}"
        )


__all__ = [
    "apply_honest_degradation",
    "compile_fact_locks",
    "detect_conflicts",
    "run_scientific_quality_gate",
    "update_claim_graph_with_report",
]
