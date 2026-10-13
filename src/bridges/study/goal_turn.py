"""一次导师回合内的目标/路线规划与终态原子提交。"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from bridges.contracts.study_goals import StudyGoalProjection, StudyRouteProjection
from bridges.study.goal_intent import adjustment_candidate, goal_candidate
from bridges.study.goal_planning import (
    StudyGoalPlan,
    StudyGoalPlanner,
    StudyRouteDraft,
    route_projection_from_draft,
)
from bridges.study.goals import StudyGoalRepository

if TYPE_CHECKING:
    from bridges.ai.model_quota import RunModelQuota
    from bridges.chat.repository import GenerationRunRecord, MessageRecord
    from bridges.chat.run_budget import RunBudget

_MODE_CREATE: Literal["create"] = "create"
_MODE_BUILD: Literal["build_route"] = "build_route"
_MODE_REVISION: Literal["revision"] = "revision"

_PLANNING_FAILED_BLOCK = (
    "【学习目标】\n"
    "用户目标原话：{statement}\n"
    "本次未能形成学习路线。不要声称已保存路线或课程；按用户当前消息正常教学，"
    "必要时最多问一个关键问题；不要假定用户基础或进行入门考试。"
)


@dataclass(frozen=True, slots=True)
class StudyGoalTurn:
    """本轮的规划结果：注入导师提示词的区块与待提交内容。"""

    mode: Literal["create", "build_route", "revision"]
    plan: StudyGoalPlan | None
    goal: StudyGoalProjection | None
    expected_route_version: int
    prompt_block: str


class StudyGoalTurnService:
    """判定是否需要规划、调用规划器并渲染提示词区块；提交在终态事务内完成。"""

    def __init__(
        self, repository: StudyGoalRepository, planner: StudyGoalPlanner
    ) -> None:
        self._goals = repository
        self._planner = planner

    def plan_for_turn(
        self,
        *,
        run: GenerationRunRecord,
        user_message: MessageRecord,
        compiled_messages: list[dict[str, str]] | None,
        model_quota: RunModelQuota | None,
        budget: RunBudget | None,
    ) -> StudyGoalTurn | None:
        goal = self._goals.get_linked_goal(run.account_id, run.conversation_id)
        if goal is None:
            if not goal_candidate(user_message.content):
                return None
            mode: Literal["create", "build_route", "revision"] = _MODE_CREATE
            route = None
        else:
            route = self._goals.current_route(run.account_id, goal.goal_id)
            if route is None:
                mode = _MODE_BUILD
            elif adjustment_candidate(user_message.content):
                mode = _MODE_REVISION
            else:
                return None
        plan = self._planner.plan(
            mode=mode,
            run=run,
            user_content=user_message.content,
            goal=goal,
            route=route,
            messages=compiled_messages,
            model_quota=model_quota,
            budget=budget,
        )
        if mode == _MODE_CREATE and plan is not None and plan.kind == "local_question":
            return None
        return StudyGoalTurn(
            mode=mode,
            plan=plan,
            goal=goal,
            expected_route_version=(route.version if route is not None else 0),
            prompt_block=_render_prompt_block(
                mode=mode, goal=goal, user_content=user_message.content, plan=plan
            ),
        )

    def commit(
        self,
        turn: StudyGoalTurn,
        run: GenerationRunRecord,
        user_message: MessageRecord,
    ) -> None:
        """在消息终态事务内提交目标/路线；冲突或版本不符时如实跳过。"""

        now = datetime.now(UTC)
        if turn.mode == _MODE_CREATE:
            goal = _new_goal(turn, run, user_message, now)
            self._goals.create_goal_with_route_in_transaction(
                account_id=run.account_id,
                conversation_id=run.conversation_id,
                goal=goal,
                route=_new_route(
                    turn,
                    expected_version=0,
                    origin="assistant_proposal",
                    default_reason="首次形成导师路线",
                    now=now,
                ),
                now=now,
            )
            return
        if turn.goal is None or turn.plan is None or turn.plan.route is None:
            return
        origin: Literal["assistant_proposal", "user_request"] = (
            "user_request" if turn.mode == _MODE_REVISION else "assistant_proposal"
        )
        route = _new_route(
            turn,
            expected_version=turn.expected_route_version,
            origin=origin,
            default_reason=(
                "按用户要求调整路线" if origin == "user_request" else "补充信息后形成路线"
            ),
            now=now,
        )
        if route is None:
            return
        self._goals.save_route_revision_in_transaction(
            account_id=run.account_id,
            goal_id=turn.goal.goal_id,
            route=route,
            expected_version=turn.expected_route_version,
        )


def _new_goal(
    turn: StudyGoalTurn,
    run: GenerationRunRecord,
    user_message: MessageRecord,
    now: datetime,
) -> StudyGoalProjection:
    plan = turn.plan
    return StudyGoalProjection(
        goal_id=secrets.token_urlsafe(16),
        statement=_short_text(user_message.content, 2000),
        observable_result=_short_text(plan.observable_result, 500) if plan else "",
        scope=_short_text(plan.scope, 500) if plan else "",
        constraints=_short_text(plan.constraints, 500) if plan else "",
        version=1,
        source_message_id=user_message.message_id,
        created_at=now,
        updated_at=now,
    )


def _new_route(
    turn: StudyGoalTurn,
    *,
    expected_version: int,
    origin: Literal["assistant_proposal", "user_request"],
    default_reason: str,
    now: datetime,
) -> StudyRouteProjection | None:
    if turn.plan is None or turn.plan.route is None:
        return None
    draft: StudyRouteDraft = turn.plan.route
    reason = draft.revision_reason or default_reason
    return route_projection_from_draft(
        draft,
        version=expected_version + 1,
        origin=origin,
        revision_reason=reason,
        created_at=now,
    )


def _render_prompt_block(
    *,
    mode: Literal["create", "build_route", "revision"],
    goal: StudyGoalProjection | None,
    user_content: str,
    plan: StudyGoalPlan | None,
) -> str:
    """给导师的系统提示词区块：只做真实陈述，不代替用户决策。"""

    if plan is None:
        if mode == _MODE_CREATE:
            return _PLANNING_FAILED_BLOCK.format(
                statement=_short_text(user_content, 300)
            )
        return ""
    if plan.kind == "local_question":
        return ""
    statement = (
        _short_text(user_content, 300)
        if mode == _MODE_CREATE
        else _short_text(goal.statement if goal is not None else user_content, 300)
    )
    if mode == _MODE_REVISION:
        if plan.route is None:
            if not plan.note:
                return ""
            return (
                "【路线修订评估】用户提出了调整请求，但评估认为无需修订路线："
                + plan.note
                + "。请简短说明理由并按当前路线继续；不要改动用户的学习目的。"
            )
        lines = [
            "【路线修订】",
            f"用户目标原话：{statement}",
            f"修订依据：{plan.route.revision_reason or '按用户要求调整'}",
            _route_lines(plan.route),
            "请先简短说明这次调整的依据，再立即按新路线继续教学；"
            "用户的学习目的与限制保持不变。未经验证的内容不得称为已掌握；"
            "跳到某个节点不代表前面的知识点已完成。",
        ]
        return "\n".join(lines)
    lines = ["【学习目标】", f"用户目标原话：{statement}"]
    if plan.observable_result:
        lines.append(f"用户明确的用途或结果：{plan.observable_result}")
    if plan.scope:
        lines.append(f"用户明确的范围：{plan.scope}")
    if plan.constraints:
        lines.append(f"用户明确的限制：{plan.constraints}")
    if plan.route is not None:
        if mode == _MODE_BUILD:
            lines.append("已根据用户补充的信息形成简短学习路线：")
        else:
            lines.append(
                "请用一两句话先给出这条简短学习路线（说明可随时调整），再立即围绕当前节点开始教学："
            )
        lines.append(_route_lines(plan.route))
        lines.append(
            "路线是可调整的导师方案，不是用户不可更改的要求；用户明确说过的目的、范围与限制始终优先，"
            "不得反向改写。不要进行入门考试或假定用户已有基础。未经验证的内容不得称为已掌握；"
            "跳到某个节点不代表前面的知识点已完成。"
        )
    elif plan.clarification_question:
        lines.append(
            "缺少一个会改变教法的关键信息。请只问这一个必要问题，不要同时展开教学，"
            "不要进行入门考试或假定已有基础："
        )
        lines.append(plan.clarification_question)
    elif plan.note:
        lines.append("说明：" + plan.note)
    return "\n".join(lines)


def _route_lines(draft: StudyRouteDraft) -> str:
    titles = {node.node_id: node.title for node in draft.nodes}
    lines: list[str] = []
    for index, node in enumerate(draft.nodes, 1):
        prerequisites = "、".join(
            titles[ref] for ref in node.prerequisites if ref in titles
        ) or "无"
        depth = f"（{node.depth}）" if node.depth else ""
        marker = " ← 当前节点" if node.node_id == draft.current_node_id else ""
        lines.append(f"{index}. {node.title}{depth}〔前置：{prerequisites}〕{marker}")
    return "\n".join(lines)


def _short_text(value: str, limit: int) -> str:
    return " ".join(value.split())[:limit]
