"""日常父图的composite plan实现。"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from bridges.career_plan.service import (
    CAREER_MODULE_ID,
)
from bridges.chat.run_executor import chat_run_context
from bridges.contracts.understanding import (
    HardConditionKind,
)
from bridges.github.service import (
    GITHUB_MODULE_ID,
)
from bridges.paper.service import PAPER_MODULE_ID
from bridges.resources.service import (
    RESOURCES_MODULE_ID,
)
from bridges.state_copy import render_state_copy

if TYPE_CHECKING:
    pass

#: 日常父图名称/版本（运行状态关联字段；节点集变化时递增）。
#: v2：论文子图接入 invoke_subgraph_or_chat（首个显式模块，Issue 11）。
#: v2 不变：Issue 12（通勤）与 Issue 13（资料）都只放开显式模块白名单，
#: 复用同一派发节点，节点集没有变化——旧检查点不会出现这两个模块的派发
#: （当时它们在 select 处被拒），续跑语义一致。
from bridges.chat.graph_contracts import (
    NODE_INVOKE_SUBGRAPH_OR_CHAT,
    DailyGraphSuperseded,
    DailyTurnError,
    DailyTurnState,
    _GraphDeps,
)

#: 可执行复合计划的模块到服务属性映射。
_COMPOSITE_SERVICE_ATTRS: dict[str, str] = {
    PAPER_MODULE_ID: "paper_search_service",
}

#: 中文序数词（“第二篇”）。
_CHINESE_ORDINALS: dict[str, int] = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}

def _composite_github_requirement(context: Any, goal: str) -> Any:
    """把上游交付转换为 GitHub 的类型化需求（身份未确认不宣称对应）。

    论文：只有投影里的 ``selected`` 身份字段完整且目标无歧义（“第二篇”或
    唯一候选）时才确认身份；岗位：只消费 career 公开的
    ``combination_requirements``（岗位方向与要求词），私人简历与背景正文
    不进入公网需求。
    """
    from bridges.github.contracts import GithubRequirementInput  # noqa: PLC0415

    paper = context.upstream.get(PAPER_MODULE_ID)
    career = context.upstream.get(CAREER_MODULE_ID)
    if paper is not None:
        source_ref = next(iter(paper.artifact_refs.values()), None)
        identity = _selected_paper_identity(paper, goal)
        if identity is not None:
            return GithubRequirementInput(
                kind="paper",
                label="选定论文",
                identifier=identity.arxiv_id or identity.doi or identity.title,
                phrase=goal,
                identity_confirmed=True,
                source_ref=source_ref,
                identity_note=f"论文身份已确认：{identity.title}。",
            )
        return GithubRequirementInput(
            kind="paper",
            label="候选论文",
            phrase=goal,
            identity_confirmed=False,
            source_ref=source_ref,
            identity_note="本轮只取得候选论文，身份尚未确认；不宣称仓库对应那份论文。",
        )
    if career is not None:
        source_ref = next(iter(career.artifact_refs.values()), None)
        requirement = _composite_public_requirement(career, "github")
        phrase = (
            _requirement_phrase(requirement)
            if requirement is not None
            else _composite_topic(career)
        )
        if phrase:
            return GithubRequirementInput(
                kind="job",
                label="岗位需求",
                phrase=phrase,
                identity_confirmed=requirement is not None,
                source_ref=source_ref,
                identity_note=(
                    "岗位需求来自已核验公开样本的要求词。"
                    if requirement is not None
                    else "岗位结果尚未形成公开需求清单；只按岗位方向检索，不宣称匹配已核验。"
                ),
            )
    return None

def _invoke_composite_plan(
    deps: _GraphDeps, state: DailyTurnState, understanding: Any
) -> dict[str, Any]:
    """工单 37：按已登记组合执行有限跨模块计划。

    模块经 ``defer_finalization`` 只提交内核产物与延迟交付，不结束助手
    消息；父图核验综合与最终门后，在守卫事务内一次性提交全部投影。
    """
    from bridges.chat.graph_composite_call import _composite_run_call  # noqa: PLC0415
    from bridges.chat.run_budget_ledger import RunBudgetLedgerRepository  # noqa: PLC0415
    from bridges.orchestration.contracts import CompositeStatus  # noqa: PLC0415
    from bridges.orchestration.evidence import (  # noqa: PLC0415
        DeliveryEvidence,
        EvidenceVerifier,
    )
    from bridges.orchestration.executor import (  # noqa: PLC0415
        CompositeBudget,
        StepRunContext,
    )
    from bridges.orchestration.planner import CompositePlanner  # noqa: PLC0415
    from bridges.orchestration.production import (  # noqa: PLC0415
        CompositeOrchestrationService,
        ModuleServiceStepRunner,
        changed_condition_keys,
        condition_snapshot,
        load_prior_composite,
        wait_reason_for,
    )
    from bridges.orchestration.synthesis import FinalGate  # noqa: PLC0415
    from bridges.tasks.repository import TaskRepository  # noqa: PLC0415

    run = deps.run
    conversation = deps.repo.get_conversation(run.account_id, run.conversation_id)
    if conversation is None or conversation.mode != "companion":
        raise DailyTurnError(
            NODE_INVOKE_SUBGRAPH_OR_CHAT, "module_mode_conflict",
            render_state_copy("error.module_mode_conflict"), retryable=False,
        )
    if understanding.blocks_network:
        raise DailyTurnError(
            NODE_INVOKE_SUBGRAPH_OR_CHAT, "network_not_allowed",
            render_state_copy("error.network_not_allowed"), retryable=False,
        )
    for module_id in understanding.capability_list:
        if not understanding.allows_module(module_id):
            raise DailyTurnError(
                NODE_INVOKE_SUBGRAPH_OR_CHAT, "source_not_allowed",
                render_state_copy("error.source_not_allowed"), retryable=False,
            )
    user_message = deps.repo.get_message(run.account_id, run.user_message_id)
    goal = understanding.goal or (
        user_message.content if user_message is not None else ""
    )
    planner = CompositePlanner()
    task = TaskRepository(deps.repo.database).current_task(
        run.account_id, run.conversation_id
    )
    task_ref = [task.task_id, task.current_version] if task is not None else [None, None]
    conditions = [
        *understanding.hard_conditions,
        *understanding.effective_hard_conditions,
    ]
    plan = planner.plan(
        goal=goal,
        user_message_id=run.user_message_id,
        module_ids=understanding.capability_list,
        task_id=task.task_id if task is not None else None,
        task_version=task.current_version if task is not None else None,
        hard_conditions=conditions,
    )
    if plan is None:
        raise DailyTurnError(
            NODE_INVOKE_SUBGRAPH_OR_CHAT, "composite_plan_unavailable",
            render_state_copy("error.composite_plan_unavailable"), retryable=False,
        )
    superseded: list[str] = []

    def emit_node(node: str, status: str, duration_ms: int | None) -> None:
        deps.emit_node(
            state["assistant_message_id"], node, status, duration_ms=duration_ms
        )

    from bridges.chat.progressive_result import ProgressiveResultPublisher  # noqa: PLC0415

    publisher = ProgressiveResultPublisher(
        deps.repo, run,
        (task.task_id, task.current_version) if task is not None else (None, None),
        deps.stop_event,
    )
    context = StepRunContext(
        account_id=run.account_id,
        conversation_id=run.conversation_id,
        run_id=run.run_id,
        user_message_id=run.user_message_id,
        assistant_message_id=run.assistant_message_id,
        goal=goal,
        run_context=chat_run_context(
            run.account_id, run.conversation_id, run.run_id
        ),
        stop_event=deps.stop_event,
        emit_node=emit_node,
        emit_result=publisher.publish,
        task_id=task.task_id if task is not None else None,
        task_version=task.current_version if task is not None else None,
    )
    evidence = DeliveryEvidence(deps.repo)
    prior_payload = load_prior_composite(
        deps.repo, account_id=run.account_id, conversation_id=run.conversation_id
    )
    prior_results: dict[str, Any] = {}
    records = prior_payload.get("steps") if prior_payload is not None else None
    if isinstance(records, list):
        steps_by_id = {step.step_id: step for step in plan.steps}
        for record in records:
            if not isinstance(record, dict):
                continue
            step_id = record.get("step_id")
            if not isinstance(step_id, str):
                continue
            step = steps_by_id.get(step_id)
            if step is None:
                continue
            restored = evidence.restore(step, context, record)
            if restored is not None:
                prior_results[step.step_id] = restored
    runners = {
        step.module_id: ModuleServiceStepRunner(
            resolve=_composite_resolver(
                understanding, step.module_id, goal
            ),
            run=_composite_run_call(
                deps, state, module_id=step.module_id, goal=goal,
                superseded=superseded,
            ),
            qualify=evidence.qualify,
        )
        for step in plan.steps
        if step.module_id in _COMPOSITE_SERVICE_ATTRS
    }
    result = CompositeOrchestrationService(
        planner=planner,
        gate=FinalGate(verifier=EvidenceVerifier()),
    ).run(
        plan=plan,
        runners=runners,
        context=context,
        budget=CompositeBudget(
            RunBudgetLedgerRepository(deps.repo.database),
            account_id=run.account_id,
            run_id=run.run_id,
        ),
        prior_results=prior_results,
        changed_conditions=changed_condition_keys(prior_payload, conditions),
    )
    if superseded:
        raise DailyGraphSuperseded(superseded[0])
    if result.outcome.status is CompositeStatus.REJECTED:
        raise DailyTurnError(
            NODE_INVOKE_SUBGRAPH_OR_CHAT, "composite_plan_rejected",
            render_state_copy("error.composite_plan_rejected"),
            retryable=False,
        )
    reason = wait_reason_for(result.outcome)
    if reason is not None:
        deps.repo.update_generation_progress(
            run.account_id, run.run_id, wait_reason=reason
        )
    outcome_payload = result.outcome.model_dump(mode="json")
    for step_payload in outcome_payload.get("steps") or []:
        # 原证据只在本次核验与最终门内使用；不把私人证据原文写进父图检查点。
        step_payload["evidence"] = {}
        if step_payload.get("module_id") == CAREER_MODULE_ID:
            # career 投影含私人背景/简历原文：检查点只留引用与短结论；
            # 提交时由 ``hydrate_career_projection`` 从内核产物回填。
            # 未达可信状态的步骤 summary 会回落到交付正文，这里一并清空。
            step_payload["summary"] = ""
            delivery_payload = step_payload.get("delivery")
            if isinstance(delivery_payload, dict):
                delivery_payload["projection"] = {}
                delivery_payload["content"] = ""
    return {
        "composite_outcome": outcome_payload,
        "composite_draft": result.draft.model_dump(mode="json"),
        "composite_gate": result.gate.model_dump(mode="json"),
        "composite_verified": False,
        "composite_lease_owner": run.lease_owner,
        "composite_task_ref": task_ref,
        "composite_conditions": condition_snapshot(conditions),
    }
def _composite_resolver(
    understanding: Any, module_id: str, goal: str
) -> Callable[[Any, Any], dict[str, Any]]:
    """按登记参数来源解析步骤参数（无外呼；私人背景不进入指纹与公网）。"""

    def resolve(step: Any, context: Any) -> dict[str, Any]:
        del step
        upstream = context.upstream
        if module_id == CAREER_MODULE_ID:
            params: dict[str, Any] = {"job_terms": goal}
            city = next(
                (
                    item.text
                    for item in [
                        *understanding.hard_conditions,
                        *understanding.effective_hard_conditions,
                    ]
                    if item.kind is HardConditionKind.CITY
                ),
                None,
            )
            if city:
                params["city"] = city
            return params
        if module_id == RESOURCES_MODULE_ID:
            # 只消费 career 已核验的公开最小需求；有 career 时绝不回退到
            # 可能含私人简历的原始消息。
            topic = _composite_resources_topic(upstream)
            if topic is None and CAREER_MODULE_ID not in upstream:
                topic = goal
            return {"topic": topic or ""}
        if module_id == GITHUB_MODULE_ID:
            requirement = _composite_github_requirement(context, goal)
            return {
                "topic": requirement.phrase if requirement is not None else context.goal,
                "identity_confirmed": bool(
                    requirement is not None and requirement.identity_confirmed
                ),
                "kind": requirement.kind if requirement is not None else "",
            }
        return {"topic": context.goal}

    return resolve
def _composite_topic(result: Any) -> str | None:
    """上游交付投影里的公开主题词（缺失或未核验时为 None，不臆造）。"""
    if result is None or result.delivery is None:
        return None
    if getattr(result, "trust_state", "draft") != "qualified":
        return None
    topic = (result.delivery.projection or {}).get("topic")
    return str(topic) if topic else None
def _composite_public_requirement(result: Any, kind: str) -> dict[str, Any] | None:
    """career 组合需求里的公开最小目标（只消费已核验的公开要求词）。"""
    if result is None or result.delivery is None:
        return None
    if getattr(result, "trust_state", "draft") != "qualified":
        return None
    requirements = (result.delivery.projection or {}).get("combination_requirements")
    if not isinstance(requirements, list):
        return None
    for item in requirements:
        if isinstance(item, dict) and item.get("kind") == kind:
            return item
    return None
def _requirement_phrase(requirement: dict[str, Any]) -> str:
    """把组合需求拼成一条公开查询短语（只用岗位方向与要求词）。"""
    topic = str(requirement.get("topic") or "").strip()
    goal = str(requirement.get("goal") or "").strip()
    skills = [
        str(skill).strip()
        for skill in requirement.get("skills") or []
        if str(skill).strip()
    ]
    parts = [topic or goal]
    if skills:
        parts.append("、".join(dict.fromkeys(skills)))
    return "：".join(part for part in parts if part)
def _composite_resources_topic(upstream: Any) -> str | None:
    career = upstream.get(CAREER_MODULE_ID)
    requirement = _composite_public_requirement(career, "resources")
    if requirement is not None:
        phrase = _requirement_phrase(requirement)
        if phrase:
            return phrase
    return _composite_topic(career)
def _goal_ordinal(goal: str) -> int | None:
    match = re.search(r"第\s*([一二三四五六七八九十\d]+)\s*篇", goal or "")
    if match is None:
        return None
    token = match.group(1)
    if token.isdigit():
        return int(token)
    return _CHINESE_ORDINALS.get(token)
def _selected_paper_identity(result: Any, goal: str) -> Any:
    """只有已核验步骤、且身份字段完整、目标无歧义时才确认论文身份。"""
    if getattr(result, "trust_state", "draft") != "qualified":
        return None
    if result.delivery is None:
        return None
    from bridges.paper.contracts import PaperIdentity  # noqa: PLC0415

    projection = result.delivery.projection or {}
    identities = []
    for item in projection.get("selected") or []:
        try:
            identity = PaperIdentity.model_validate(item)
        except ValueError:
            continue
        if identity.title and (identity.arxiv_id or identity.doi) and identity.abs_url:
            identities.append(identity)
    if not identities:
        return None
    ordinal = _goal_ordinal(goal)
    if ordinal is not None:
        return next(
            (identity for identity in identities if identity.order == ordinal), None
        )
    return identities[0] if len(identities) == 1 else None
