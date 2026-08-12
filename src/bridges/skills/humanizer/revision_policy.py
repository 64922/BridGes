"""版本化定向修订触发裁决（人味化改造 Issue 05）。

首稿完成后，对来源保真硬门结果、任务契约检查（硬约束包含）与表达审稿
发现执行确定性裁决：只有可修复硬问题、无来源新增 claim、任务契约遗漏
或达到预注册阈值的高置信表达问题才触发修订；低置信软信号（suggestion/
info）与 ``no_change_recommended`` 不触发，模型/协议错误按既有失败语义
终态，也不是修订触发条件。

裁决输出待修问题清单（code/位置/证据/目标），供修订提示编译使用；问题
清单只包含待修项，不包含已通过项、全量黑名单或与当前问题无关的体裁规则。
本模块不调用模型，全部确定性、可测试；修订是否真正执行还由调用方结合
预算、停止信号与能力开关决定（本模块的 ``adjudicate_revision`` 只做
「是否应该修」的裁决，``REVISION_CAPABILITY_ENABLED`` 是回滚开关）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Sequence

from bridges.contracts.expression_review import (
    ExpressionReviewReport,
    ReviewCode,
    ReviewSeverity,
)
from bridges.contracts.humanizer import (
    FactLockCheckResult,
    FidelityCheckResult,
    FidelityFailure,
    FidelityFailureCode,
    SpanLocation,
)

#: 触发裁决策略版本（审计与观测用；阈值或类别调整时递增）。
REVISION_POLICY_VERSION = "revision-policy-v1"

#: Issue 05 能力开关：关闭时定向修订不可用，恢复一次调用；关闭不改变
#: 来源硬门行为，也不把未通过首稿标为成功（灰度与回滚用）。
REVISION_CAPABILITY_ENABLED = True


class RevisionTriggerKind(StrEnum):
    """触发裁决类别（版本化，至少区分七类）。"""

    REPAIRABLE_FIDELITY = "repairable_fidelity"
    UNSOURCED_CLAIM = "unsourced_claim"
    CONTRACT_OMISSION = "contract_omission"
    HIGH_CONFIDENCE_EXPRESSION = "high_confidence_expression"
    LOW_CONFIDENCE_SOFT_SIGNAL = "low_confidence_soft_signal"
    MODEL_PROTOCOL_ERROR = "model_protocol_error"
    NO_CHANGE = "no_change"


@dataclass(frozen=True)
class RevisionProblem:
    """一条待修问题：修订提示与审计都以此定位，不重复扫描全文。

    ``kind`` 区分问题类别（保真/无来源 claim/契约/表达）；``code`` 是
    稳定发现 code（FidelityFailureCode / ReviewCode / ``"contract"``）；
    ``evidence`` 是限长命中证据；``target`` 是定向修改目标（审稿建议或
    恢复方式），供修订提示直接使用。
    """

    kind: RevisionTriggerKind
    code: str
    category: str
    location: SpanLocation | None
    evidence: str
    target: str
    severity: str = "warning"


@dataclass(frozen=True)
class RevisionAdjudication:
    """一次触发裁决的结果：是否触发 + 待修问题清单 + 说明。"""

    triggered: bool
    kind: RevisionTriggerKind | None = None
    trigger_codes: tuple[str, ...] = field(default_factory=tuple)
    problems: tuple[RevisionProblem, ...] = field(default_factory=tuple)
    reason: str = ""


#: 预注册高置信表达触发阈值：warning 级命中数达到阈值才触发修订（Issue 05
#: 验收：只对达到预注册阈值的高置信表达问题触发）。阈值按 code 预注册、
#: 随策略版本固化，不随单次任务输入漂移；弱信号 warning code 可注册更高
#: 阈值（当前全部注册 1）。suggestion/info 级低置信软信号永不触发——
#: 对应阈值为无穷，不在此表内。
_HIGH_CONFIDENCE_THRESHOLDS: dict[ReviewCode, int] = {
    ReviewCode.ASSISTANT_IDENTITY_RESIDUE: 1,
    ReviewCode.MECHANICAL_TRANSITION_CLOSING: 1,
    ReviewCode.FAKE_CONCRETENESS: 1,
    ReviewCode.UNAUTHORIZED_FIRST_PERSON: 1,
}

#: 无来源新增 claim 的保真失败码（可修复：修订删除该 claim 即可恢复硬门）。
_UNSOURCED_CODES = frozenset(
    {
        FidelityFailureCode.UNATTRIBUTED_CLAIM,
        FidelityFailureCode.FIRST_PERSON_UNBOUND,
        FidelityFailureCode.ASSUMPTION_NOT_ALLOWED,
        FidelityFailureCode.ASSUMPTION_CARRIES_FACT,
    }
)


def _fidelity_problem(failure: FidelityFailure) -> RevisionProblem:
    kind = (
        RevisionTriggerKind.UNSOURCED_CLAIM
        if failure.code in _UNSOURCED_CODES
        else RevisionTriggerKind.REPAIRABLE_FIDELITY
    )
    return RevisionProblem(
        kind=kind,
        code=failure.code.value,
        category=failure.category,
        location=failure.location,
        evidence="",
        target=(
            "删除或改写该无来源内容，恢复为账本已有材料的事实"
            if kind == RevisionTriggerKind.UNSOURCED_CLAIM
            else f"恢复为来源中的原值（{failure.note}）"
        ),
        severity=failure.severity.value,
    )


def adjudicate_revision(
    *,
    fidelity_check: FidelityCheckResult | None,
    contract_check: FactLockCheckResult | None,
    review: ExpressionReviewReport,
) -> RevisionAdjudication:
    """对首稿检查结果执行触发裁决，返回待修问题清单与触发说明。

    优先级：可修复保真硬问题/无来源新增 claim > 任务契约遗漏 > 达到预
    注册阈值的高置信表达问题；低置信软信号与 ``no_change_recommended``
    不触发（``triggered=False``）。模型/协议错误不在本裁决内——调用方在
    模型调用失败时按既有失败语义终态，不进入修订。
    """
    problems: list[RevisionProblem] = []
    fidelity_failures = (
        list(fidelity_check.blocking_failures)
        if fidelity_check is not None
        else []
    )
    for failure in fidelity_failures:
        problems.append(_fidelity_problem(failure))
    # 契约遗漏：硬约束阻断项（否定式违背）与需人工确认项（正向自由文本
    # 约束未体现）都是可修复的契约缺口，触发定向修订补齐。
    contract_omissions = (
        list(contract_check.blocking_conflicts) + list(contract_check.needs_human)
        if contract_check is not None
        else []
    )
    for omission in contract_omissions:
        problems.append(
            RevisionProblem(
                kind=RevisionTriggerKind.CONTRACT_OMISSION,
                code="contract",
                category="任务契约遗漏",
                location=None,
                evidence=omission,
                target="补齐任务契约要求（硬约束必须包含的内容）。",
                severity="blocking",
            )
        )
    # 高置信表达问题：只取达到预注册阈值的 code（warning 级）；问题清单
    # 包含所有命中该 code 的 warning 发现（已通过项与低置信信号不进入）。
    warning_counts: dict[ReviewCode, int] = {}
    for finding in review.findings:
        if finding.severity == ReviewSeverity.WARNING:
            warning_counts[finding.code] = warning_counts.get(finding.code, 0) + 1
    triggered_expression_codes: list[ReviewCode] = []
    for code, count in warning_counts.items():
        threshold = _HIGH_CONFIDENCE_THRESHOLDS.get(code, 1)
        if count >= threshold:
            triggered_expression_codes.append(code)
    if triggered_expression_codes:
        for finding in review.findings:
            if (
                finding.severity == ReviewSeverity.WARNING
                and finding.code in set(triggered_expression_codes)
            ):
                problems.append(
                    RevisionProblem(
                        kind=RevisionTriggerKind.HIGH_CONFIDENCE_EXPRESSION,
                        code=finding.code.value,
                        category=finding.category,
                        location=finding.location,
                        evidence=finding.evidence,
                        target=finding.suggestion,
                        severity=finding.severity.value,
                    )
                )

    if not problems:
        return RevisionAdjudication(
            triggered=False,
            kind=RevisionTriggerKind.NO_CHANGE,
            reason=(
                "首稿没有可修复的保真问题、契约遗漏或达到阈值的高置信"
                "表达问题（低置信软信号与 no_change_recommended 不触发修订）。"
            ),
        )
    # 触发类别优先级：硬问题 > 契约遗漏 > 高置信表达（同类取首个 code）
    kinds_order = [
        RevisionTriggerKind.REPAIRABLE_FIDELITY,
        RevisionTriggerKind.UNSOURCED_CLAIM,
        RevisionTriggerKind.CONTRACT_OMISSION,
        RevisionTriggerKind.HIGH_CONFIDENCE_EXPRESSION,
    ]
    primary_kind = next(
        (kind for kind in kinds_order if any(p.kind == kind for p in problems)),
        RevisionTriggerKind.HIGH_CONFIDENCE_EXPRESSION,
    )
    trigger_codes = tuple(
        dict.fromkeys(p.code for p in problems if p.kind == primary_kind)
    )
    return RevisionAdjudication(
        triggered=True,
        kind=primary_kind,
        trigger_codes=trigger_codes,
        problems=tuple(problems),
        reason=(
            f"首稿存在 {len(problems)} 个可修复问题（{primary_kind.value}："
            f"{'、'.join(trigger_codes)}），触发一次定向修订。"
        ),
    )


__all__ = [
    "REVISION_POLICY_VERSION",
    "REVISION_CAPABILITY_ENABLED",
    "RevisionTriggerKind",
    "RevisionProblem",
    "RevisionAdjudication",
    "adjudicate_revision",
]
