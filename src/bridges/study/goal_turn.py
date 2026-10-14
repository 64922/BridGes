"""Issue 07/08：一次导师回合内的目标/路线规划与终态原子提交。

本模块拥有：新建目标与路线、路线修订、跨会话续学的目标关联、明确改学
的原子切换，以及失败/冲突的如实登记。职责边界：

- 规划只在确定性触发器命中时调用结构化能力；是否续学、是否切换由规划
  按完整语义判断，不按最近关键词自动改目标。
- 终态提交在消息 streaming→done 的同一事务内完成：切换失败不留半次
  切换；冲突记录为事件，消息交付与目标进展提交分开呈现。
- 恢复读取既有会话保存的进展与待答处置，不搬移或改写原消息。
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from bridges.contracts.study_goals import StudyGoalProjection, StudyRouteProjection
from bridges.study.goal_events import StudyGoalEventRepository
from bridges.study.goal_intent import (
    adjustment_candidate,
    goal_candidate,
    resume_candidate,
    switch_candidate,
)
from bridges.study.goal_planning import (
    StudyGoalPlan,
    StudyGoalPlanner,
    StudyRouteDraft,
    route_projection_from_draft,
)
from bridges.study.goals import StudyGoalRepository
from bridges.study.resume_context import (
    ResumeContext,
    load_resume_context,
    render_clarification_block,
    render_resume_block,
)

if TYPE_CHECKING:
    from bridges.ai.model_quota import RunModelQuota
    from bridges.chat.repository import GenerationRunRecord, MessageRecord
    from bridges.chat.run_budget import RunBudget
    from bridges.study.verification import StudyVerificationRepository

StudyGoalTurnMode = Literal[
    "create",
    "build_route",
    "revision",
    "resume",
    "switch",
    "reactivate",
    "clarification_lapsed",
]

_MODE_CREATE: Literal["create"] = "create"
_MODE_BUILD: Literal["build_route"] = "build_route"
_MODE_REVISION: Literal["revision"] = "revision"
_MODE_RESUME: Literal["resume"] = "resume"
_MODE_SWITCH: Literal["switch"] = "switch"
_MODE_REACTIVATE: Literal["reactivate"] = "reactivate"
_MODE_LAPSED: Literal["clarification_lapsed"] = "clarification_lapsed"

#: 主动续学的运行配置键（工单 12）：用户在续学入口显式选择继续的目标，
#: 已在生成前确定，不再经规划角色判别归属（也不产生第二次模型调用）。
STUDY_RESUME_GOAL_CONFIG_KEY = "study_resume_goal_id"

_PLANNING_FAILED_BLOCK = (
    "【学习目标】\n"
    "用户目标原话：{statement}\n"
    "本次未能形成学习路线。不要声称已保存路线或课程；按用户当前消息正常教学，"
    "必要时最多问一个关键问题；不要假定用户基础或进行入门考试。"
)


@dataclass(frozen=True)
class StudyGoalTurn:
    """本轮的规划结果：注入导师提示词的区块与待提交内容。"""

    mode: StudyGoalTurnMode
    plan: StudyGoalPlan | None
    goal: StudyGoalProjection | None
    expected_route_version: int
    prompt_block: str
    target_goal: StudyGoalProjection | None = None
    candidate_goal_ids: tuple[str, ...] = ()


class StudyGoalTurnService:
    """判定是否需要规划、调用规划器并渲染提示词区块；提交在终态事务内完成。"""

    def __init__(
        self,
        repository: StudyGoalRepository,
        planner: StudyGoalPlanner,
        verification: StudyVerificationRepository,
        events: StudyGoalEventRepository,
    ) -> None:
        self._goals = repository
        self._planner = planner
        self._verification = verification
        self._events = events

    # ------------------------------------------------------------------
    # 规划（生成前）
    # ------------------------------------------------------------------

    def plan_for_turn(
        self,
        *,
        run: GenerationRunRecord,
        user_message: MessageRecord,
        compiled_messages: list[dict[str, str]] | None,
        model_quota: RunModelQuota | None,
        budget: RunBudget | None,
    ) -> StudyGoalTurn | None:
        # 工单 12：主动续学的目标由用户在续学入口选定并随运行配置冻结——
        # 直接按显式标识恢复（暂停目标在终态提交时一并恢复为活动），
        # 不经规划模型判别，也不把旧会话的旧进展当成新会话的进展。
        resume_goal_id = str(
            (run.config or {}).get(STUDY_RESUME_GOAL_CONFIG_KEY) or ""
        ).strip()
        if resume_goal_id:
            target = self._goals.get_goal(run.account_id, resume_goal_id)
            if target is not None and target.status != "completed":
                return self._resume_turn(
                    target=target,
                    run=run,
                    plan=StudyGoalPlan(kind="resume", target_goal_id=target.goal_id),
                )
        goal = self._goals.get_linked_goal(run.account_id, run.conversation_id)
        if goal is None:
            return self._plan_without_goal(
                run=run,
                user_message=user_message,
                compiled_messages=compiled_messages,
                model_quota=model_quota,
                budget=budget,
            )
        route = self._goals.current_route(run.account_id, goal.goal_id)
        if goal.status == "paused" and resume_candidate(user_message.content):
            # 该目标在其他会话被暂停后，本会话明确要求继续：按最新版本恢复。
            return self._reactivate_turn(
                goal=goal, route=route, run=run, user_message=user_message
            )
        if switch_candidate(user_message.content):
            return self._plan_switch(
                goal=goal,
                route=route,
                run=run,
                user_message=user_message,
                compiled_messages=compiled_messages,
                model_quota=model_quota,
                budget=budget,
            )
        if route is None:
            mode: Literal["build_route", "revision"] = _MODE_BUILD
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
        return StudyGoalTurn(
            mode=mode,
            plan=plan,
            goal=goal,
            expected_route_version=(route.version if route is not None else 0),
            prompt_block=_render_prompt_block(
                mode=mode, goal=goal, user_content=user_message.content, plan=plan
            ),
        )

    def _plan_without_goal(
        self,
        *,
        run: GenerationRunRecord,
        user_message: MessageRecord,
        compiled_messages: list[dict[str, str]] | None,
        model_quota: RunModelQuota | None,
        budget: RunBudget | None,
    ) -> StudyGoalTurn | None:
        """无关联目标的新会话：先判断续学（含未决澄清），再判断新建目标。"""

        content = user_message.content
        pending_clarification = self._events.pending_clarification(
            run.account_id, run.conversation_id
        )
        if pending_clarification or resume_candidate(content):
            candidates = self._goals.list_resume_candidates(run.account_id)
            if not candidates:
                # 候选已不存在：待确认失去对象，记失效并按普通教学继续。
                return _lapsed_turn() if pending_clarification else None
            plan = self._planner.plan(
                mode=_MODE_RESUME,
                run=run,
                user_content=content,
                goal=None,
                route=None,
                messages=compiled_messages,
                model_quota=model_quota,
                budget=budget,
                candidates=candidates,
            )
            if plan is None:
                # 规划失败：不领取、不放弃，也不把一次基础设施失败当作
                # 用户未确认；如实按普通问题继续，下一条消息可再判定。
                return None
            target = _target_of(plan, candidates)
            if plan.kind == "resume" and target is not None:
                return self._resume_turn(target=target, run=run, plan=plan)
            if plan.kind == "resume":
                if pending_clarification:
                    # 已问过一次仍无法确定：不再追问（记失效），按普通教学继续。
                    return _lapsed_turn()
                # 确有歧义或无法确定：只澄清一个必要问题，不默认候选。
                return StudyGoalTurn(
                    mode=_MODE_RESUME,
                    plan=plan,
                    goal=None,
                    expected_route_version=0,
                    prompt_block=render_clarification_block(candidates),
                    candidate_goal_ids=tuple(item.goal_id for item in candidates),
                )
            if plan.kind == "switch" and target is not None:
                return self._resume_turn(target=target, run=run, plan=plan)
            if plan.kind == "systematic_goal" or (
                plan.kind == "switch" and switch_candidate(content)
            ):
                # 澄清后的下一条消息可能改成明确的新目标：按新目标创建。
                return _create_turn(goal=None, plan=plan, user_content=content)
            return _lapsed_turn() if pending_clarification else None
        if not (goal_candidate(content) or switch_candidate(content)):
            return None
        plan = self._planner.plan(
            mode=_MODE_CREATE,
            run=run,
            user_content=content,
            goal=None,
            route=None,
            messages=compiled_messages,
            model_quota=model_quota,
            budget=budget,
        )
        if plan is not None and plan.kind == "local_question":
            return None
        return _create_turn(goal=None, plan=plan, user_content=content)

    def _resume_turn(
        self,
        *,
        target: StudyGoalProjection,
        run: GenerationRunRecord,
        plan: StudyGoalPlan,
    ) -> StudyGoalTurn:
        context = load_resume_context(
            self._goals.database,
            run.account_id,
            target.goal_id,
            exclude_conversation_id=run.conversation_id,
        )
        if context is None:
            context = ResumeContext(goal=target)
        return StudyGoalTurn(
            mode=_MODE_RESUME,
            plan=plan,
            goal=None,
            expected_route_version=0,
            prompt_block=render_resume_block(context),
            target_goal=target,
        )

    def _reactivate_turn(
        self,
        *,
        goal: StudyGoalProjection,
        route: StudyRouteProjection | None,
        run: GenerationRunRecord,
        user_message: MessageRecord,
    ) -> StudyGoalTurn:
        """本会话明确要求继续已被其他会话暂停的当前目标。

        不需要规划判别：目标不变、不建新目标，只按最新版本恢复活动状态；
        恢复区块按本目标的其他已保存会话渲染（本会话读取时排除）。
        """

        context = load_resume_context(
            self._goals.database,
            run.account_id,
            goal.goal_id,
            exclude_conversation_id=run.conversation_id,
        )
        if context is None:
            context = ResumeContext(goal=goal)
        return StudyGoalTurn(
            mode=_MODE_REACTIVATE,
            plan=None,
            goal=goal,
            expected_route_version=(route.version if route is not None else 0),
            prompt_block=render_resume_block(context),
            target_goal=goal,
        )

    def _plan_switch(
        self,
        *,
        goal: StudyGoalProjection,
        route: StudyRouteProjection | None,
        run: GenerationRunRecord,
        user_message: MessageRecord,
        compiled_messages: list[dict[str, str]] | None,
        model_quota: RunModelQuota | None,
        budget: RunBudget | None,
    ) -> StudyGoalTurn | None:
        candidates = [
            item
            for item in self._goals.list_resume_candidates(run.account_id)
            if item.goal_id != goal.goal_id
        ]
        plan = self._planner.plan(
            mode=_MODE_SWITCH,
            run=run,
            user_content=user_message.content,
            goal=goal,
            route=route,
            messages=compiled_messages,
            model_quota=model_quota,
            budget=budget,
            candidates=candidates,
        )
        if plan is None or plan.kind not in {"switch", "systematic_goal"}:
            # 规划失败或只是顺便提问：不改目标，按普通教学继续，不虚报。
            return None
        target = _target_of(plan, candidates)
        if plan.target_goal_id.strip() and target is None:
            # 规划给出了候选之外的目标：不切换，也不凭空创建。
            return None
        prompt_block = _render_switch_block(
            goal=goal,
            user_content=user_message.content,
            plan=plan,
            target=target,
        )
        return StudyGoalTurn(
            mode=_MODE_SWITCH,
            plan=plan,
            goal=goal,
            expected_route_version=(route.version if route is not None else 0),
            prompt_block=prompt_block,
            target_goal=target,
        )

    # ------------------------------------------------------------------
    # 终态提交（在消息 streaming→done 的同一事务内调用）
    # ------------------------------------------------------------------

    def commit(
        self,
        turn: StudyGoalTurn,
        run: GenerationRunRecord,
        user_message: MessageRecord,
    ) -> None:
        """按规划快照提交目标/路线；冲突或版本不符时如实登记，不虚报。"""

        now = datetime.now(UTC)
        if turn.mode == _MODE_CREATE:
            self._commit_create(turn, run, user_message, now)
        elif turn.mode in {_MODE_BUILD, _MODE_REVISION}:
            self._commit_route(turn, run, user_message, now)
        elif turn.mode == _MODE_RESUME:
            self._commit_resume(turn, run, user_message, now)
        elif turn.mode == _MODE_REACTIVATE:
            self._commit_reactivate(turn, run, user_message, now)
        elif turn.mode == _MODE_SWITCH:
            self._commit_switch(turn, run, user_message, now)
        elif turn.mode == _MODE_LAPSED:
            self._events.record_in_transaction(
                account_id=run.account_id,
                conversation_id=run.conversation_id,
                kind="clarification_lapsed",
                source_message_id=user_message.message_id,
                detail="未选择候选目标",
                now=now,
            )

    def _commit_create(
        self,
        turn: StudyGoalTurn,
        run: GenerationRunRecord,
        user_message: MessageRecord,
        now: datetime,
    ) -> None:
        goal = _new_goal(turn, run, user_message, now)
        created = self._goals.create_goal_with_route_in_transaction(
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
        if created:
            self._events.record_in_transaction(
                account_id=run.account_id,
                conversation_id=run.conversation_id,
                kind="link",
                source_message_id=user_message.message_id,
                goal_id=goal.goal_id,
                goal_statement=goal.statement,
                detail="created",
                goal_version=goal.version,
                now=now,
            )

    def _commit_route(
        self,
        turn: StudyGoalTurn,
        run: GenerationRunRecord,
        user_message: MessageRecord,
        now: datetime,
    ) -> None:
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
        saved = self._goals.save_route_revision_in_transaction(
            account_id=run.account_id,
            goal_id=turn.goal.goal_id,
            route=route,
            expected_version=turn.expected_route_version,
        )
        if not saved:
            self._record_conflict(
                run,
                user_message,
                detail="route_conflict",
                goal=turn.goal,
                now=now,
            )

    def _commit_resume(
        self,
        turn: StudyGoalTurn,
        run: GenerationRunRecord,
        user_message: MessageRecord,
        now: datetime,
    ) -> None:
        plan = turn.plan
        if plan is None or plan.kind not in {"resume", "switch"}:
            return
        if turn.target_goal is None:
            # 歧义：登记「待用户确认」事件；确认前不改动任何目标或进展。
            self._events.record_in_transaction(
                account_id=run.account_id,
                conversation_id=run.conversation_id,
                kind="clarification",
                source_message_id=user_message.message_id,
                detail="续学目标有多个候选",
                candidate_goal_ids=list(turn.candidate_goal_ids),
                now=now,
            )
            return
        target = turn.target_goal
        linked = self._goals.link_existing_goal_in_transaction(
            account_id=run.account_id,
            conversation_id=run.conversation_id,
            goal_id=target.goal_id,
            expected_version=target.version,
            now=now,
        )
        if not linked:
            detail = (
                "goal_missing"
                if self._goals.get_goal(run.account_id, target.goal_id) is None
                else "goal_changed"
            )
            self._record_conflict(run, user_message, detail=detail, goal=target, now=now)
            return
        fresh = self._goals.get_goal(run.account_id, target.goal_id)
        self._events.record_in_transaction(
            account_id=run.account_id,
            conversation_id=run.conversation_id,
            kind="link",
            source_message_id=user_message.message_id,
            goal_id=target.goal_id,
            goal_statement=target.statement,
            detail="resume",
            goal_version=fresh.version if fresh is not None else target.version,
            now=now,
        )

    def _commit_reactivate(
        self,
        turn: StudyGoalTurn,
        run: GenerationRunRecord,
        user_message: MessageRecord,
        now: datetime,
    ) -> None:
        """按最新版本恢复本会话已关联的暂停目标；版本不符只记冲突。"""

        if turn.goal is None:
            return
        current = self._goals.get_linked_goal(run.account_id, run.conversation_id)
        if current is None or current.goal_id != turn.goal.goal_id:
            self._record_conflict(
                run, user_message, detail="switch_conflict", goal=turn.goal, now=now
            )
            return
        fresh = self._goals.get_goal(run.account_id, current.goal_id)
        if fresh is None or fresh.status == "completed":
            self._record_conflict(
                run, user_message, detail="goal_missing", goal=turn.goal, now=now
            )
            return
        if fresh.version != turn.goal.version:
            self._record_conflict(
                run, user_message, detail="goal_changed", goal=turn.goal, now=now
            )
            return
        if fresh.status != "paused":
            # 已由其他会话恢复：不重复记账，消息照常交付。
            return
        if not self._goals.reactivate_goal_in_transaction(
            account_id=run.account_id,
            goal_id=fresh.goal_id,
            expected_version=fresh.version,
            now=now,
        ):
            self._record_conflict(
                run, user_message, detail="goal_changed", goal=turn.goal, now=now
            )
            return
        self._events.record_in_transaction(
            account_id=run.account_id,
            conversation_id=run.conversation_id,
            kind="link",
            source_message_id=user_message.message_id,
            goal_id=fresh.goal_id,
            goal_statement=fresh.statement,
            detail="resume",
            goal_version=fresh.version + 1,
            now=now,
        )

    def _commit_switch(
        self,
        turn: StudyGoalTurn,
        run: GenerationRunRecord,
        user_message: MessageRecord,
        now: datetime,
    ) -> None:
        """明确改学的原子提交：先全部核对，再一次性写入；失败只留冲突事件。"""

        if turn.goal is None or turn.plan is None:
            return
        account_id = run.account_id
        conversation_id = run.conversation_id
        current = self._goals.get_linked_goal(account_id, conversation_id)
        if current is None or current.goal_id != turn.goal.goal_id:
            self._record_conflict(
                run, user_message, detail="switch_conflict", goal=turn.goal, now=now
            )
            return
        fresh = self._goals.get_goal(account_id, current.goal_id)
        if fresh is None:
            self._record_conflict(
                run, user_message, detail="goal_missing", goal=turn.goal, now=now
            )
            return
        if fresh.status == "completed":
            self._record_conflict(
                run, user_message, detail="goal_missing", goal=turn.goal, now=now
            )
            return
        if fresh.version != turn.goal.version:
            self._record_conflict(
                run, user_message, detail="goal_changed", goal=turn.goal, now=now
            )
            return
        target = turn.target_goal
        if turn.plan.target_goal_id.strip() and target is None:
            self._record_conflict(
                run, user_message, detail="goal_missing", goal=turn.goal, now=now
            )
            return
        if target is not None:
            fresh_target = self._goals.get_goal(account_id, target.goal_id)
            if fresh_target is None or fresh_target.status == "completed":
                self._record_conflict(
                    run, user_message, detail="goal_missing", goal=turn.goal, now=now
                )
                return
            if fresh_target.version != target.version:
                self._record_conflict(
                    run, user_message, detail="goal_changed", goal=turn.goal, now=now
                )
                return
        progress_version = self._progress_version(account_id, conversation_id)
        created_new = False
        if target is None:
            new_goal = _new_goal(turn, run, user_message, now)
            new_route = _new_route(
                turn,
                expected_version=0,
                origin="assistant_proposal",
                default_reason="明确改学后首次形成导师路线",
                now=now,
            )
            self._goals.insert_goal_with_route_in_transaction(
                account_id=account_id, goal=new_goal, route=new_route, now=now
            )
            created_new = True
            new_goal_id = new_goal.goal_id
            new_statement = new_goal.statement
            new_version = new_goal.version
        else:
            new_goal_id = target.goal_id
            new_statement = target.statement
            new_version = target.version + (1 if target.status == "paused" else 0)

        paused_ok = True
        if fresh.status == "active":
            paused_ok = self._goals.pause_goal_in_transaction(
                account_id=account_id,
                goal_id=fresh.goal_id,
                expected_version=fresh.version,
                now=now,
            )
        linked_ok = paused_ok and (
            self._goals.switch_to_existing_goal_in_transaction(
                account_id=account_id,
                conversation_id=conversation_id,
                previous_goal_id=fresh.goal_id,
                goal_id=target.goal_id,
                expected_version=target.version,
                now=now,
            )
            if target is not None
            else self._goals.replace_link_in_transaction(
                account_id=account_id,
                conversation_id=conversation_id,
                previous_goal_id=fresh.goal_id,
                goal_id=new_goal_id,
                now=now,
            )
        )
        if not linked_ok:
            if created_new:
                # 补偿：整笔切换未成立，新目标行不保留（同一事务内回滚语义）。
                self._goals.delete_goal_row_in_transaction(
                    account_id=account_id, goal_id=new_goal_id
                )
            self._record_conflict(
                run, user_message, detail="switch_conflict", goal=turn.goal, now=now
            )
            return
        old_version = fresh.version + 1 if fresh.status == "active" else fresh.version
        # 切换已成立：旧目标的待答如实记为未验证并解除等待（同一事务）。
        disposition = self._verification.close_pending_for_goal_switch(run, now)
        self._events.record_in_transaction(
            account_id=account_id,
            conversation_id=conversation_id,
            kind="switch_out",
            source_message_id=user_message.message_id,
            goal_id=fresh.goal_id,
            goal_statement=fresh.statement,
            detail="明确改学",
            goal_version=old_version,
            progress_version=progress_version,
            pending_check_id=str((disposition or {}).get("check_id") or ""),
            pending_question=str((disposition or {}).get("question") or ""),
            now=now,
        )
        self._events.record_in_transaction(
            account_id=account_id,
            conversation_id=conversation_id,
            kind="link",
            source_message_id=user_message.message_id,
            goal_id=new_goal_id,
            goal_statement=new_statement,
            previous_goal_id=fresh.goal_id,
            previous_goal_statement=fresh.statement,
            detail="switch",
            goal_version=new_version,
            progress_version=progress_version,
            pending_check_id=str((disposition or {}).get("check_id") or ""),
            pending_question=str((disposition or {}).get("question") or ""),
            now=now,
        )

    def _record_conflict(
        self,
        run: GenerationRunRecord,
        user_message: MessageRecord,
        *,
        detail: str,
        goal: StudyGoalProjection,
        now: datetime,
    ) -> None:
        self._events.record_in_transaction(
            account_id=run.account_id,
            conversation_id=run.conversation_id,
            kind="conflict",
            source_message_id=user_message.message_id,
            goal_id=goal.goal_id,
            goal_statement=goal.statement,
            detail=detail,
            goal_version=goal.version,
            now=now,
        )

    def _progress_version(self, account_id: str, conversation_id: str) -> int:
        row = self._goals.database.scoped(account_id).execute(
            "SELECT version FROM study_local_progress"
            " WHERE account_id = ? AND conversation_id = ?",
            (account_id, conversation_id),
        ).fetchone()
        return int(row["version"]) if row is not None else 0


def _target_of(
    plan: StudyGoalPlan, candidates: list[StudyGoalProjection]
) -> StudyGoalProjection | None:
    """目标必须来自本轮提供的候选；候选外的选择视为无法确定。"""

    target_id = plan.target_goal_id.strip()
    if not target_id:
        return None
    return next((item for item in candidates if item.goal_id == target_id), None)


def _create_turn(
    *,
    goal: StudyGoalProjection | None,
    plan: StudyGoalPlan | None,
    user_content: str,
) -> StudyGoalTurn:
    return StudyGoalTurn(
        mode=_MODE_CREATE,
        plan=plan,
        goal=goal,
        expected_route_version=0,
        prompt_block=_render_prompt_block(
            mode=_MODE_CREATE, goal=goal, user_content=user_content, plan=plan
        ),
    )


def _lapsed_turn() -> StudyGoalTurn:
    """澄清无法解决：不再追问第二个问题，按普通问题继续。"""

    return StudyGoalTurn(
        mode=_MODE_LAPSED,
        plan=None,
        goal=None,
        expected_route_version=0,
        prompt_block="",
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


def _render_switch_block(
    *,
    goal: StudyGoalProjection,
    user_content: str,
    plan: StudyGoalPlan,
    target: StudyGoalProjection | None,
) -> str:
    """明确改学的导师提示词区块；新目标时沿用创建语义给出路线。"""

    lines = [
        "【目标切换】",
        f"用户明确要求改学，不再继续当前目标：「{goal.statement}」。",
    ]
    if target is not None:
        lines.append(f"已保存并暂停原目标，切换回既有目标：「{target.statement}」")
        if target.route is not None and target.route.nodes:
            lines.append(_route_lines(target.route))
        lines.append(
            "请简短确认切换（说明原目标已保存、随时可以改回来），"
            "再围绕该目标的当前节点继续教学。原目标未验证的内容保持未验证，"
            "不得声称原目标已完成或已掌握。"
        )
        return "\n".join(lines)
    statement = _short_text(user_content, 300)
    lines.append(f"已保存并暂停原目标，开始新的学习目标：「{statement}」")
    if plan.observable_result:
        lines.append(f"用户明确的用途或结果：{plan.observable_result}")
    if plan.scope:
        lines.append(f"用户明确的范围：{plan.scope}")
    if plan.constraints:
        lines.append(f"用户明确的限制：{plan.constraints}")
    if plan.route is not None:
        lines.append(
            "请先用一两句话简要确认切换与这条简短学习路线（说明可随时调整），"
            "再立即围绕当前节点开始教学："
        )
        lines.append(_route_lines(plan.route))
        lines.append(
            "路线是可调整的导师方案；用户明确说过的目的、范围与限制始终优先。"
            "不要进行入门考试或假定已有基础；未经验证的内容不得称为已掌握。"
        )
    else:
        lines.append(
            "本次未能形成学习路线：只简要确认切换，按用户当前需要开始教学，"
            "不要声称已保存路线。"
        )
    return "\n".join(lines)


def _render_prompt_block(
    *,
    mode: StudyGoalTurnMode,
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


def _route_lines(route: StudyRouteProjection | StudyRouteDraft) -> str:
    titles = {node.node_id: node.title for node in route.nodes}
    lines: list[str] = []
    for index, node in enumerate(route.nodes, 1):
        prerequisites = "、".join(
            titles[ref] for ref in node.prerequisites if ref in titles
        ) or "无"
        depth = f"（{node.depth}）" if node.depth else ""
        marker = " ← 当前节点" if node.node_id == route.current_node_id else ""
        lines.append(f"{index}. {node.title}{depth}〔前置：{prerequisites}〕{marker}")
    return "\n".join(lines)


def _short_text(value: str, limit: int) -> str:
    return " ".join(value.split())[:limit]
