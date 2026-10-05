"""公开回合结果投影的确定性推导（改进工单 38）。

主对话只发布用户可理解的交付面：请求模块提示与实际执行能力分开、已通过
质量门的结果块、被阻塞项、真实恢复方式与等待语义。本模块不发起模型调用、
不读取私有证据，也不发布待核验草稿——数据只来自消息终态、领域投影状态与
路由快照。

复合运行在提交事务内写入精确的 ``turn_result``（含阻塞结论与步骤可信状态）；
单模块、轻量聊天与学习路径在读取时按同一规则确定性推导，历史消息缺省字段
无需迁移即可获得一致投影。固定中文文案复用状态文案注册表（工单 23）。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from bridges.contracts.chat import (
    ChatMessageStatus,
    ResultTrust,
    TurnOutcome,
    TurnRecoveryProjection,
    TurnResultBlock,
    TurnResultProjection,
)
from bridges.state_copy import error_recovery, render_state_copy
from bridges.state_copy.catalog import COMPOSITE_MODULE_LABELS
from bridges.state_copy.types import RecoveryAction

#: 回合结果投影合同版本；字段增加保持向后兼容（旧读取忽略新字段）。
TURN_RESULT_VERSION = "turn-result-v1"

#: 领域投影字段 → 模块 ID（短标签复用登记表 COMPOSITE_MODULE_LABELS，单一源）。
MODULE_PROJECTION_FIELDS: tuple[tuple[str, str], ...] = (
    ("paper_search", "paper"),
    ("tieba_research", "tieba"),
    ("career_plan", "career"),
    ("learning_resources", "resources"),
    ("commute_route", "commute"),
    ("github_projects", "github"),
)

#: 领域状态分类：只在公开投影中按真实结果选择，不虚构。只有 ``success``
#: 视为已通过核验；``links_only``/``metadata_only``/``unverified`` 及未知
#: 状态一律按证据绑定交付，绝不冒充「已通过核验」。
_QUALIFIED_STATES = frozenset({"success"})
_EMPTY_STATES = frozenset({"empty"})
_NEEDS_INPUT_STATES = frozenset({"clarification"})
_FAILED_STATES = frozenset({"error"})
_STOPPED_STATES = frozenset({"stopped"})
_INTERMEDIATE_STATES = frozenset({"searching"})

_OUTCOME_COPY_PATHS: dict[TurnOutcome, str] = {
    TurnOutcome.COMPLETE: "chat.result.outcome.complete",
    TurnOutcome.NEEDS_INPUT: "chat.result.outcome.needs_input",
    TurnOutcome.PARTIAL: "chat.result.outcome.partial",
    TurnOutcome.BLOCKED: "chat.result.outcome.blocked",
    TurnOutcome.CANCELLED: "chat.result.outcome.cancelled",
    TurnOutcome.FAILED: "chat.result.outcome.failed",
}

_TRUST_COPY_PATHS: dict[ResultTrust, str] = {
    ResultTrust.QUALIFIED: "chat.result.trust.qualified",
    ResultTrust.EVIDENCE_BOUND: "chat.result.trust.evidence_bound",
}

#: 恢复方式 → （登记文案路径，是否开放点击重试）。
_RECOVERY_COPY_PATHS: dict[RecoveryAction, tuple[str, bool]] = {
    RecoveryAction.RETRY: ("chat.progress.recovery_retry", True),
    RecoveryAction.WAIT: ("chat.progress.recovery_wait", True),
    RecoveryAction.RECONFIGURE: ("chat.progress.recovery_reconfigure", False),
    RecoveryAction.ADJUST_REQUEST: ("chat.progress.recovery_adjust", False),
    RecoveryAction.REFRESH_STATE: ("chat.result.recovery_refresh", False),
}


def outcome_label(outcome: TurnOutcome) -> str:
    """交付分类的中文自然文案（注册表单源）。"""
    return render_state_copy(_OUTCOME_COPY_PATHS[outcome])


def trust_label(trust: ResultTrust) -> str:
    """可信状态的中文说明（注册表单源）。"""
    return render_state_copy(_TRUST_COPY_PATHS[trust])


def derive_turn_result(
    *,
    status: ChatMessageStatus,
    error_code: str | None = None,
    route: Mapping[str, Any] | None = None,
    projections: Mapping[str, Mapping[str, Any] | None] | None = None,
    wait_reason: str | None = None,
    web_search: Mapping[str, Any] | None = None,
) -> TurnResultProjection:
    """按消息终态与真实领域投影推导公开回合结果。

    - ``status``/``error_code`` 决定取消与失败；等待缘由由领域澄清状态与
      路由澄清共同判定，不靠猜测；
    - ``projections`` 只包含领域模块的公开状态；``searching`` 等中间态不
      进入已交付块（终态消息里不会出现，防御性跳过）；
    - 只把已提交的领域投影视为已交付；未通过门的草稿不在调用方输入中。
    """
    blocks_delivered: list[TurnResultBlock] = []
    blocks_blocked: list[TurnResultBlock] = []
    needs_input = False
    partial = False

    for field, module_id in MODULE_PROJECTION_FIELDS:
        payload = (projections or {}).get(field)
        if not payload:
            continue
        label = COMPOSITE_MODULE_LABELS.get(module_id, module_id)
        state = str(payload.get("status") or "")
        detail = str(payload.get("error_message") or "")
        if state in _NEEDS_INPUT_STATES:
            needs_input = True
            continue
        if state in _STOPPED_STATES or state in _INTERMEDIATE_STATES or not state:
            continue
        if state in _FAILED_STATES or state in _EMPTY_STATES:
            partial = True
            blocks_blocked.append(
                TurnResultBlock(
                    module_id=module_id, label=label, state=state,
                    trust=ResultTrust.EVIDENCE_BOUND,
                    detail=detail or render_state_copy("chat.result.blocked_detail"),
                )
            )
            continue
        trust = (
            ResultTrust.QUALIFIED if state in _QUALIFIED_STATES
            else ResultTrust.EVIDENCE_BOUND
        )
        partial = partial or trust is ResultTrust.EVIDENCE_BOUND
        blocks_delivered.append(
            TurnResultBlock(
                module_id=module_id, label=label, state=state, trust=trust,
                detail=detail,
            )
        )

    route_status = str((route or {}).get("status") or "")
    if route_status == "clarify":
        needs_input = True

    if status is ChatMessageStatus.STOPPED:
        outcome = TurnOutcome.CANCELLED
    elif status is ChatMessageStatus.ERROR:
        outcome = TurnOutcome.FAILED
    elif needs_input or wait_reason:
        outcome = TurnOutcome.NEEDS_INPUT
    elif blocks_blocked and not blocks_delivered:
        outcome = TurnOutcome.BLOCKED
    elif partial:
        outcome = TurnOutcome.PARTIAL
    else:
        outcome = TurnOutcome.COMPLETE

    overall_trust: ResultTrust | None = None
    if blocks_delivered:
        overall_trust = (
            ResultTrust.EVIDENCE_BOUND
            if any(item.trust is ResultTrust.EVIDENCE_BOUND for item in blocks_delivered)
            else ResultTrust.QUALIFIED
        )

    gap_items = [item.detail for item in blocks_blocked if item.detail]
    return TurnResultProjection(
        version=TURN_RESULT_VERSION,
        outcome=outcome,
        outcome_label=outcome_label(outcome),
        trust=overall_trust,
        trust_label=trust_label(overall_trust) if overall_trust is not None else None,
        requested_module_id=_optional_str((route or {}).get("requested_module_id")),
        actual_module_id=(
            _optional_str((route or {}).get("module_id"))
            or _actual_from_capabilities(route)
        ),
        capability_list=[
            str(item)
            for item in ((route or {}).get("capability_list") or [])
            if isinstance(item, str)
        ],
        route_source=_optional_str((route or {}).get("route_source")),
        delivered=blocks_delivered,
        blocked=blocks_blocked,
        gaps=list(dict.fromkeys(gap_items)),
        recovery=_recovery_for(
            outcome=outcome,
            error_code=error_code,
            web_search=web_search,
        ),
        wait_reason=wait_reason if outcome is TurnOutcome.NEEDS_INPUT else None,
    )


def _actual_from_capabilities(route: Mapping[str, Any] | None) -> str | None:
    """无单模块实际值但能力列表唯一时，视为该唯一实际能力。"""
    if not route:
        return None
    capabilities = [
        item for item in (route.get("capability_list") or []) if isinstance(item, str)
    ]
    if len(capabilities) == 1:
        return capabilities[0]
    return None


def _optional_str(value: object) -> str | None:
    return str(value) if isinstance(value, str) and value else None


def recovery_for_error(
    error_code: str | None,
    *,
    web_search: Mapping[str, Any] | None = None,
) -> TurnRecoveryProjection | None:
    """按登记的错误恢复方式构造；只承诺真实可用的操作。

    限流/冷却类恢复只在领域结果给出真实 ``cooldown_until`` 时才携带
    ``available_after``，前端按用户时区展示；没有真实时刻不编造等待时间。
    """
    action = error_recovery(error_code) if error_code else None
    if action is None or action is RecoveryAction.NONE:
        return None
    path, retryable = _RECOVERY_COPY_PATHS.get(action, ("", False))
    if not path:
        return None
    available_after: datetime | None = None
    if action is RecoveryAction.WAIT and web_search is not None:
        candidate = web_search.get("cooldown_until")
        if isinstance(candidate, datetime):
            available_after = candidate
        elif isinstance(candidate, str):
            try:
                available_after = datetime.fromisoformat(candidate)
            except ValueError:
                available_after = None
    return TurnRecoveryProjection(
        action=action.value,
        label=render_state_copy(path),
        retryable=retryable,
        available_after=available_after,
    )


def _recovery_for(
    *,
    outcome: TurnOutcome,
    error_code: str | None,
    web_search: Mapping[str, Any] | None,
) -> TurnRecoveryProjection | None:
    if outcome in {TurnOutcome.CANCELLED, TurnOutcome.NEEDS_INPUT}:
        return None
    if outcome is not TurnOutcome.FAILED and not error_code:
        return None
    recovery = recovery_for_error(error_code, web_search=web_search)
    if recovery is None or outcome is TurnOutcome.FAILED:
        return recovery
    # 部分交付：失败分支的恢复方式只作说明，不把整体结果标成可点击重试。
    return recovery.model_copy(update={"retryable": False})


__all__ = [
    "MODULE_PROJECTION_FIELDS",
    "TURN_RESULT_VERSION",
    "derive_turn_result",
    "outcome_label",
    "recovery_for_error",
    "trust_label",
]
