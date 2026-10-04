"""综合与最终质量门：按一个目标组织结论并核验引用（改进工单 37）。

综合只使用**已通过节点门并带可信状态**的步骤结果：每个结论绑定来源
步骤与证据引用；最终门逐条检查新事实、限定条件与引用错连。独立核验按
风险触发（复杂比较、来源冲突、关键公式、个人差距、实现证据不足），只
接收结论、原证据与规则，不接收生成者辩护；核验自身的引用与裁决结构也
要校验。普通闲聊与纯工具计算不强制模型裁判，确定性计算先由代码核对。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from bridges.orchestration.contracts import (
    Claim,
    CompositeOutcome,
    IndependentVerification,
    StepResult,
    StepState,
    SynthesisDraft,
    SynthesisGateResult,
    SynthesisSection,
    VerificationSource,
    VerificationTrigger,
    VerificationVerdict,
)

#: 必须独立核验的风险类别（其余类别不强制模型裁判）。
RISK_TRIGGERS: tuple[VerificationTrigger, ...] = (
    VerificationTrigger.COMPLEX_COMPARISON,
    VerificationTrigger.SOURCE_CONFLICT,
    VerificationTrigger.KEY_FORMULA_OR_NUMBER,
    VerificationTrigger.PERSONAL_GAP,
    VerificationTrigger.IMPLEMENTATION_EVIDENCE,
)


class IndependentVerifier(Protocol):
    """独立核验端口：只接收结论、原证据与规则。"""

    def verify(
        self,
        *,
        conclusion: str,
        evidence: Sequence[str],
        rules: Sequence[str],
        trigger: VerificationTrigger,
    ) -> IndependentVerification: ...


class Synthesizer:
    """把步骤结果组织成单一目标的简洁综合草案。"""

    def build(self, outcome: CompositeOutcome) -> SynthesisDraft:
        delivered = [
            result
            for result in outcome.steps
            if result.state is StepState.COMPLETED
        ]
        sections: list[SynthesisSection] = []
        for result in delivered:
            body = result.summary or _first_paragraph(result)
            sections.append(
                SynthesisSection(
                    title=result.module_id,
                    body=body,
                    step_ids=[result.step_id],
                    evidence_refs=list(result.evidence_refs),
                    qualification=(
                        "；".join(result.unconfirmed) if result.unconfirmed else None
                    ),
                )
            )
        limitations: list[str] = []
        for result in outcome.steps:
            limitations.extend(result.unconfirmed)
            if result.state in {StepState.FAILED, StepState.BLOCKED, StepState.SKIPPED}:
                text = result.blocked_reason
                if not text and result.failure is not None:
                    text = result.failure.message
                if text:
                    limitations.append(f"{result.module_id}：{text}")
        blocked = [
            f"{result.module_id}：{result.blocked_reason}"
            for result in outcome.steps
            if result.state in {StepState.FAILED, StepState.BLOCKED}
            and result.blocked_reason
        ]
        if delivered:
            names = "、".join(result.module_id for result in delivered)
            summary = f"围绕「{outcome.plan.goal}」，本轮完成了：{names}。"
        else:
            summary = f"围绕「{outcome.plan.goal}」，本轮没有得到可交付结果。"
        if blocked:
            summary += " 以下结论因必要步骤未完成而阻塞：" + "；".join(blocked) + "。"
        return SynthesisDraft(
            summary=summary,
            sections=sections,
            limitations=list(dict.fromkeys(limitations)),
            blocked=blocked,
        )


class FinalGate:
    """综合后的最终门：新事实、限定条件、引用错连与核验结构。"""

    def __init__(
        self,
        *,
        verifier: IndependentVerifier | None = None,
        rules: Sequence[str] = (),
    ) -> None:
        self._verifier = verifier
        self._rules = tuple(rules)

    def check(
        self,
        draft: SynthesisDraft,
        steps: Sequence[StepResult],
    ) -> SynthesisGateResult:
        claims: list[Claim] = [
            claim for step in steps for claim in step.claims
        ]
        owned_refs: dict[str, set[str]] = {
            step.step_id: {
                *step.artifact_refs.values(),
                *step.evidence_refs,
            }
            for step in steps
        }
        unsupported = [claim.text for claim in claims if not claim.evidence_refs]
        mislinked: list[str] = []
        for step in steps:
            for claim in step.claims:
                for ref in claim.evidence_refs:
                    if ref and ref not in owned_refs.get(step.step_id, set()):
                        mislinked.append(f"{step.step_id}:{ref}")
        new_facts: list[str] = []
        for section in draft.sections:
            if not section.step_ids:
                new_facts.append(section.title)
                continue
            allowed: set[str] = set()
            for step_id in section.step_ids:
                allowed.update(owned_refs.get(step_id, set()))
            for ref in section.evidence_refs:
                if ref and ref not in allowed:
                    mislinked.append(f"{section.title}:{ref}")
        step_unconfirmed = {
            item for step in steps for item in step.unconfirmed if item
        }
        draft_limitations = set(draft.limitations) | {
            section.qualification or "" for section in draft.sections
        }
        missing_qualifications = sorted(
            item
            for item in step_unconfirmed
            if item not in draft_limitations
        )
        verifications = self._run_verifications(claims, steps)
        blocked_verdicts = [
            item
            for item in verifications
            if item.required
            and item.verdict in {VerificationVerdict.BLOCK, VerificationVerdict.REPAIR}
        ]
        passed = not (
            unsupported or mislinked or new_facts or missing_qualifications or blocked_verdicts
        )
        return SynthesisGateResult(
            passed=passed,
            code="" if passed else _gate_code(
                unsupported, mislinked, new_facts, missing_qualifications, blocked_verdicts
            ),
            message="" if passed else "综合未通过最终门：存在未支持断言、限定条件缺失或引用错连。",
            new_facts=new_facts,
            missing_qualifications=missing_qualifications,
            mislinked_refs=list(dict.fromkeys(mislinked)),
            unsupported_claims=unsupported,
            verifications=verifications,
        )

    def _run_verifications(
        self, claims: Sequence[Claim], steps: Sequence[StepResult]
    ) -> list[IndependentVerification]:
        results: list[IndependentVerification] = []
        for claim in claims:
            trigger = _as_trigger(claim.risk)
            if trigger is None:
                continue
            if trigger in RISK_TRIGGERS and self._verifier is not None:
                verification = self._verifier.verify(
                    conclusion=claim.text,
                    evidence=list(claim.evidence_refs),
                    rules=list(self._rules),
                    trigger=trigger,
                )
                results.append(_validate_structure(verification, claim))
            elif trigger in {
                VerificationTrigger.DETERMINISTIC_TOOL,
                VerificationTrigger.ORDINARY_CHAT,
            }:
                results.append(
                    IndependentVerification(
                        required=False,
                        trigger=trigger,
                        verdict=VerificationVerdict.NOT_APPLICABLE,
                        source=VerificationSource.DETERMINISTIC_CODE,
                        independent_fact_source=False,
                        rules=["纯工具/普通交流不追加模型裁判。"],
                        evidence_refs=list(claim.evidence_refs),
                        note="按风险策略不需要模型核验。",
                    )
                )
            elif trigger in RISK_TRIGGERS:
                # 风险类别但未装配核验体：不伪装已核验，保持阻塞。
                results.append(
                    IndependentVerification(
                        required=True,
                        trigger=trigger,
                        verdict=VerificationVerdict.BLOCK,
                        source=VerificationSource.DETERMINISTIC_CODE,
                        independent_fact_source=False,
                        rules=["关键结论缺少独立核验来源。"],
                        evidence_refs=list(claim.evidence_refs),
                        note="风险类别未装配独立核验，结论保持阻塞，不冒充已核实。",
                    )
                )
        # 没有任何风险结论时也说明普通/工具路径不强制裁判。
        if not results:
            results.append(
                IndependentVerification(
                    required=False,
                    trigger=VerificationTrigger.ORDINARY_CHAT,
                    verdict=VerificationVerdict.NOT_APPLICABLE,
                    source=VerificationSource.DETERMINISTIC_CODE,
                    independent_fact_source=False,
                    rules=["无风险关键结论，不追加模型裁判。"],
                    evidence_refs=[],
                    note="普通结果不强制模型裁判。",
                )
            )
        return results


def _validate_structure(
    verification: IndependentVerification, claim: Claim
) -> IndependentVerification:
    """核验输出也要检查引用与裁决结构；结构不合法按阻塞处理。"""
    valid = bool(
        verification.rules
        and verification.verdict in set(VerificationVerdict)
        and verification.source in set(VerificationSource)
        and (
            verification.verdict in {VerificationVerdict.NOT_APPLICABLE}
            or set(verification.evidence_refs).issubset(set(claim.evidence_refs))
        )
    )
    if valid:
        return verification
    return verification.model_copy(
        update={
            "verdict": VerificationVerdict.BLOCK,
            "note": "核验结构或引用不合法，按阻塞处理：" + (verification.note or ""),
        }
    )


def _as_trigger(value: str | None) -> VerificationTrigger | None:
    if not value:
        return None
    try:
        return VerificationTrigger(value)
    except ValueError:
        return None


def _first_paragraph(result: StepResult) -> str:
    content = result.delivery.content if result.delivery is not None else ""
    for line in content.splitlines():
        text = line.strip()
        if text:
            return text[:200]
    return ""


def _gate_code(
    unsupported: Sequence[str],
    mislinked: Sequence[str],
    new_facts: Sequence[str],
    missing_qualifications: Sequence[str],
    blocked_verdicts: Sequence[IndependentVerification],
) -> str:
    parts: list[str] = []
    if unsupported:
        parts.append("unsupported_claims")
    if mislinked:
        parts.append("mislinked_refs")
    if new_facts:
        parts.append("new_facts")
    if missing_qualifications:
        parts.append("missing_qualifications")
    if blocked_verdicts:
        parts.append("verification_blocked")
    return "+".join(parts)


def synthesis_payload(
    outcome: CompositeOutcome,
    gate: SynthesisGateResult,
    *,
    draft: SynthesisDraft,
) -> dict[str, object]:
    """给内核产物/审计使用的脱敏综合载荷（不携带私人正文）。"""
    return {
        "contract_version": outcome.contract_version,
        "status": outcome.status.value,
        "goal_hash": outcome.plan.plan_id,
        "revision": outcome.plan.revision,
        "delivered_steps": list(outcome.delivered_steps),
        "blocked_count": len(outcome.blocked_conclusions),
        "sections": [
            {
                "title": section.title,
                "step_ids": list(section.step_ids),
                "evidence_count": len(section.evidence_refs),
                "qualification": section.qualification,
            }
            for section in draft.sections
        ],
        "gate": gate.model_dump(mode="json"),
        "adjustments_used": outcome.adjustments_used,
    }


__all__ = [
    "RISK_TRIGGERS",
    "FinalGate",
    "IndependentVerifier",
    "Synthesizer",
    "synthesis_payload",
]
