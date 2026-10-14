"""Issue 08：跨会话续学的已保存上下文读取与提示词渲染。

职责边界：

- 只读取当前账户、当前目标关联会话中的已保存记录（进展、暂停、待答、
  未验证证据），不搬移或改写原会话消息；读取有界，按最近更新选择来源
  会话。
- 恢复提示词说明真实位置与待确认项，不宣称跳过或未验证内容已掌握；
  必要前文只带一小段最近知识上下文，其余由统一上下文编译按预算选择。
"""

from __future__ import annotations

from dataclasses import dataclass

from bridges.contracts.study_controls import StudySessionPauseProjection
from bridges.contracts.study_goals import StudyGoalProjection
from bridges.contracts.study_local_progress import StudyLocalProgressProjection
from bridges.contracts.study_verification import StudyPendingCheckProjection
from bridges.storage.database import BridgesDatabase
from bridges.study.goals import StudyGoalRepository

#: 扫描的关联会话上限：只按最近更新的有界来源恢复。
_SOURCE_CONVERSATION_LIMIT = 5
#: 恢复区块里的知识上下文与待确认项都有界，避免撑大提示词。
_KNOWLEDGE_CONTEXT_LIMIT = 600
_UNVERIFIED_EVIDENCE_LIMIT = 4
_UNVERIFIED_SEMANTICS = (
    "unverified",
    "undetermined",
    "partial_understanding",
    "misconception",
)


@dataclass(frozen=True)
class ResumeContext:
    """恢复目标时使用的已保存上下文（全部来自持久化记录）。"""

    goal: StudyGoalProjection
    progress: StudyLocalProgressProjection | None = None
    pause: StudySessionPauseProjection | None = None
    pending: StudyPendingCheckProjection | None = None
    unverified_questions: tuple[str, ...] = ()
    source_conversation_id: str = ""


def load_resume_context(
    database: BridgesDatabase,
    account_id: str,
    goal_id: str,
    *,
    exclude_conversation_id: str,
) -> ResumeContext | None:
    """按目标引用读取最近一次保存的进展与待确认项。

    目标不存在（或不属于当前账户）时返回 None；目标下没有关联会话时
    仍返回目标本身，其余字段为空——恢复提示词如实说明没有可用的旧进展。
    """

    rows = database.scoped(account_id).execute(
        "SELECT c.conversation_id FROM study_conversation_goals AS c"
        " JOIN study_goals AS g"
        " ON g.account_id = c.account_id AND g.goal_id = c.goal_id"
        " WHERE c.account_id = ? AND c.goal_id = ? AND c.conversation_id != ?"
        " ORDER BY c.updated_at DESC LIMIT ?",
        (account_id, goal_id, exclude_conversation_id, _SOURCE_CONVERSATION_LIMIT),
    ).fetchall()
    goal_row = database.scoped(account_id).execute(
        "SELECT goal_id FROM study_goals WHERE account_id = ? AND goal_id = ?",
        (account_id, goal_id),
    ).fetchone()
    if goal_row is None:
        return None
    goal_repository = StudyGoalRepository(database)
    goal = goal_repository.get_goal(account_id, goal_id)
    if goal is None:
        return None
    # 恢复区块要展示真实路线与当前位置：get_goal 不带路线，这里补上。
    route = goal_repository.current_route(account_id, goal_id)
    if route is not None:
        goal = goal.model_copy(update={"route": route})

    # 局部导入：controls/local_progress/verification 经 chat.repository 反向
    # 引用 chat 服务，模块级导入会在 goal_turn（经本模块）先于 chat 包
    # 初始化时形成循环。
    from bridges.study.controls import StudyControlRepository
    from bridges.study.local_progress import StudyLocalProgressRepository
    from bridges.study.verification import StudyVerificationRepository

    progress_repository = StudyLocalProgressRepository(database)
    control_repository = StudyControlRepository(database)
    verification_repository = StudyVerificationRepository(database)
    progress: StudyLocalProgressProjection | None = None
    pause: StudySessionPauseProjection | None = None
    pending: StudyPendingCheckProjection | None = None
    source_conversation_id = ""
    for row in rows:
        conversation_id = str(row["conversation_id"])
        candidate_progress = progress_repository.get(account_id, conversation_id)
        if candidate_progress is not None and progress is None:
            progress = candidate_progress
            source_conversation_id = source_conversation_id or conversation_id
        if pause is None:
            pause = control_repository.active_pause(account_id, conversation_id)
            if pause is not None and not source_conversation_id:
                source_conversation_id = conversation_id
        if pending is None:
            pending = verification_repository.pending_projection(
                account_id, conversation_id
            )
            if pending is not None and not source_conversation_id:
                source_conversation_id = conversation_id
    unverified = (
        _unverified_questions(database, account_id, source_conversation_id)
        if source_conversation_id
        else ()
    )
    return ResumeContext(
        goal=goal,
        progress=progress,
        pause=pause,
        pending=pending,
        unverified_questions=unverified,
        source_conversation_id=source_conversation_id,
    )


def render_resume_block(context: ResumeContext) -> str:
    """恢复目标的导师提示词区块：真实位置、待确认项与不越权要求。"""

    lines = [
        "【恢复学习目标】",
        f"已按账户内目标引用恢复目标：「{context.goal.statement}」",
    ]
    route = context.goal.route
    if route is not None and route.nodes:
        node = next(
            (item for item in route.nodes if item.node_id == route.current_node_id),
            None,
        )
        route_text = "、".join(item.title for item in route.nodes)
        lines.append(f"已保存路线（第 {route.version} 版）：{route_text}")
        if node is not None:
            lines.append(f"上次所在的知识点：{node.title}")
    else:
        lines.append("该目标尚未形成学习路线；本次不要重建，按用户当前需要继续教学。")
    if context.progress is not None:
        lines.append(
            "上次进展（来自该目标的既有会话，未经改写）："
            f"主题：{context.progress.topic}；"
            f"当前关注：{context.progress.current_focus}"
        )
        knowledge = " ".join(context.progress.knowledge_context.split())
        if knowledge:
            lines.append(f"必要前文（节选）：{knowledge[:_KNOWLEDGE_CONTEXT_LIMIT]}")
    else:
        lines.append("没有找到可用的旧会话进展；如实说明并按用户当前需要继续。")
    if context.pause is not None:
        lines.append(
            f"上次暂停位置：{context.pause.position or context.pause.topic}"
        )
    if context.pending is not None:
        lines.append(
            f"上次未作答的问题：「{context.pending.question}」——它保持未验证，"
            "不得称为已掌握、答对或已跳过该问题后的掌握。"
        )
    if context.unverified_questions:
        lines.append(
            "上次尚未确认的内容（保持待确认，不得宣称已经掌握）："
            + "；".join(context.unverified_questions)
        )
    lines.append(
        "请从已保存的真实位置继续：不要重建路线，不要重复询问已确认的目标与目的，"
        "不宣称跳过或未验证内容已掌握；若用户明确要求从别处开始，按用户意愿调整。"
    )
    return "\n".join(lines)


def render_clarification_block(
    candidates: list[StudyGoalProjection],
) -> str:
    """续学目标歧义：只问一个必要澄清问题的导师提示词区块。"""

    lines = [
        "【续学目标待确认】",
        "用户在要求继续之前的学习，但账户里有多个可能的目标：",
    ]
    for index, candidate in enumerate(candidates, 1):
        status = "学习中" if candidate.status == "active" else "已暂停"
        lines.append(f"{index}. 「{candidate.statement}」（{status}）")
    lines.append(
        "不要默认选择最近更新的目标，也不要同时展开教学。"
        "请只问一个让用户选择目标的问题（例如「你要继续的是哪一个？」），"
        "确认前不要改动任何目标或进展。"
    )
    return "\n".join(lines)


def _unverified_questions(
    database: BridgesDatabase, account_id: str, conversation_id: str
) -> tuple[str, ...]:
    placeholders = ", ".join("?" for _ in _UNVERIFIED_SEMANTICS)
    rows = database.scoped(account_id).execute(
        "SELECT question FROM study_evidence"
        " WHERE account_id = ? AND conversation_id = ?"
        f" AND semantics IN ({placeholders})"
        " GROUP BY check_id ORDER BY MIN(created_at) ASC LIMIT ?",
        (
            account_id,
            conversation_id,
            *_UNVERIFIED_SEMANTICS,
            _UNVERIFIED_EVIDENCE_LIMIT,
        ),
    ).fetchall()
    return tuple(str(row["question"]) for row in rows if str(row["question"]).strip())
