"""画像工单 14：学习领域对个人记忆的引用接缝（目标与学情）。

职责边界：

- **只经学习领域自己的授权接口读取**：目标走 :class:`StudyGoalRepository`
  （L08），学情判断走 :class:`StudyRecordControlService` 的有效读取边界
  （L06）。本模块不直接执行学习表的业务 SQL、不复制状态；全部按账户
  作用域读取，跨账户对象如实返回不存在。
- **领域引用解析**（个人记忆的 ``OngoingDomainReferenceProvider`` 接缝）：
  引用对象是学习目标（``domain="study"``、``reference_id=goal_id``）；
  目标不存在或不可读时返回 ``None``（上层如实显示未解析），绝不伪造
  学习状态。
- **状态操作**（``OngoingDomainStateProvider`` 接缝）：个人记忆页的
  暂停/继续/完成经目标仓库的事务方法执行，按目标版本守卫；版本冲突、
  目标不可用时抛出 :class:`OngoingDomainReferenceError`，不静默覆盖学习
  领域的新状态。画像条目不再单独维护同一目标的状态（不双写）。
- **记忆页学习引用投影**（``LearningReferenceReader`` 接缝）：目标与
  未被删除的学情判断分别列出；``verified``/``corrected``/
  ``pending_confirmation`` 原文透传，区分用户自述、已核验结论与不确定性。
"""

from __future__ import annotations

from datetime import UTC, datetime

from bridges.contracts.atomic_profile import AtomicProfileDomainReference
from bridges.contracts.learning_references import (
    LearningEvidenceReference,
    LearningGoalReference,
    LearningReferencesProjection,
)
from bridges.contracts.ongoing_profile import (
    OngoingDomainReferenceError,
    OngoingDomainReferenceState,
    OngoingGoalAction,
)
from bridges.contracts.study_goals import (
    StudyGoalProjection,
    StudyRouteNode,
    StudyRouteProjection,
)
from bridges.storage.database import BridgesDatabase
from bridges.study.goals import StudyGoalRepository
from bridges.study.record_controls import StudyRecordControlService

#: 个人记忆页领域引用的学习领域标识：引用对象是账户级学习目标。
STUDY_PROFILE_DOMAIN = "study"

#: 投影展示文本的长度上限（合同约束 200）。
_DISPLAY_LIMIT = 200


class StudyProfileLinkService:
    """学习目标/学情的引用解析、状态操作与记忆页只读投影（账户作用域）。"""

    def __init__(self, database: BridgesDatabase) -> None:
        self._database = database

    # ------------------------------------------------------------------
    # 领域引用解析（个人记忆的读取接缝）
    # ------------------------------------------------------------------

    def resolve(
        self, account_id: str, reference: AtomicProfileDomainReference
    ) -> OngoingDomainReferenceState | None:
        """按引用实时解析学习目标状态；不存在或不可读返回 ``None``。"""

        if reference.domain != STUDY_PROFILE_DOMAIN:
            return None
        return self._goal_state(account_id, reference.reference_id)

    def _goal_state(
        self, account_id: str, goal_id: str
    ) -> OngoingDomainReferenceState | None:
        repository = StudyGoalRepository(self._database)
        goal = repository.get_goal(account_id, goal_id)
        if goal is None:
            return None
        route = repository.current_route(account_id, goal_id)
        return OngoingDomainReferenceState(
            display=_goal_display(goal, route),
            state_code=goal.status,
            version=goal.version,
        )

    # ------------------------------------------------------------------
    # 领域状态操作（个人记忆页的写入接缝；经目标仓库事务方法）
    # ------------------------------------------------------------------

    def set_state(
        self,
        account_id: str,
        reference: AtomicProfileDomainReference,
        action: OngoingGoalAction,
        *,
        expected_version: int,
    ) -> OngoingDomainReferenceState:
        """经学习目标仓库执行暂停/继续/完成；版本守卫不一致时如实拒绝。

        重复的相同操作（目标已是本次操作的目标状态）是幂等空操作，不推进
        版本；未被满足且版本不符时按冲突拒绝，绝不覆盖其他会话刚发生的
        暂停/切换/恢复。
        """

        if reference.domain != STUDY_PROFILE_DOMAIN:
            raise OngoingDomainReferenceError(
                "ongoing_domain_unsupported",
                "该领域引用不支持在个人记忆页修改状态。",
                422,
            )
        goal_id = reference.reference_id
        repository = StudyGoalRepository(self._database)
        now = datetime.now(UTC)
        with self._database.transaction():
            goal = repository.get_goal(account_id, goal_id)
            if goal is None:
                raise OngoingDomainReferenceError(
                    "ongoing_domain_reference_missing",
                    "关联的学习目标不存在或不可访问。",
                    404,
                )
            if not _state_reached(goal.status, action):
                if goal.version != expected_version:
                    raise OngoingDomainReferenceError(
                        "ongoing_domain_reference_conflict",
                        "该学习目标已在其他位置更新，请刷新后重试。",
                        409,
                    )
                if not self._apply(
                    repository, account_id, goal_id, action, goal.version, now
                ):
                    # 事务内守卫失败：重读确认是否已被并发改为目标状态。
                    fresh = repository.get_goal(account_id, goal_id)
                    if fresh is None:
                        raise OngoingDomainReferenceError(
                            "ongoing_domain_reference_missing",
                            "关联的学习目标不存在或不可访问。",
                            404,
                        )
                    if not _state_reached(fresh.status, action):
                        raise OngoingDomainReferenceError(
                            "ongoing_domain_reference_conflict",
                            "该学习目标已在其他位置更新，请刷新后重试。",
                            409,
                        )
        state = self._goal_state(account_id, goal_id)
        if state is None:
            raise OngoingDomainReferenceError(
                "ongoing_domain_reference_missing",
                "关联的学习目标不存在或不可访问。",
                404,
            )
        return state

    @staticmethod
    def _apply(
        repository: StudyGoalRepository,
        account_id: str,
        goal_id: str,
        action: OngoingGoalAction,
        expected_version: int,
        now: datetime,
    ) -> bool:
        if action is OngoingGoalAction.PAUSE:
            return repository.pause_goal_in_transaction(
                account_id=account_id,
                goal_id=goal_id,
                expected_version=expected_version,
                now=now,
            )
        if action is OngoingGoalAction.RESUME:
            return repository.reactivate_goal_in_transaction(
                account_id=account_id,
                goal_id=goal_id,
                expected_version=expected_version,
                now=now,
            )
        return repository.complete_goal_in_transaction(
            account_id=account_id,
            goal_id=goal_id,
            expected_version=expected_version,
            now=now,
        )

    # ------------------------------------------------------------------
    # 记忆页学习引用投影（只读；学习侧纠正/删除后立即反映）
    # ------------------------------------------------------------------

    def read(self, account_id: str) -> LearningReferencesProjection:
        """实时读取本人目标与未被删除的学情判断；不落画像库。"""

        repository = StudyGoalRepository(self._database)
        goals: list[LearningGoalReference] = []
        for goal in repository.list_goals(account_id):
            route = goal.route
            node = _current_node(route)
            goals.append(
                LearningGoalReference(
                    goal_id=goal.goal_id,
                    statement=goal.statement,
                    status=goal.status,
                    version=goal.version,
                    current_node_title=node.title if node is not None else "",
                    route_version=route.version if route is not None else 0,
                    updated_at=goal.updated_at,
                )
            )
        boundaries = StudyRecordControlService(self._database).effective_records(
            account_id
        )
        evidences = [
            LearningEvidenceReference(
                record_id=boundary.record_id,
                record_version=boundary.record_version,
                subject=boundary.subject,
                title=boundary.title,
                verdict=boundary.verdict,
                state=boundary.state,
                verified=boundary.verified,
                pending_confirmation=boundary.pending_confirmation,
                source_available=boundary.source_available,
                uncertainty=boundary.uncertainty,
                user_note=boundary.user_note,
                updated_at=boundary.updated_at,
            )
            for boundary in boundaries
            if boundary.effective
        ]
        return LearningReferencesProjection(goals=goals, evidences=evidences)


def _state_reached(status: str, action: OngoingGoalAction) -> bool:
    """目标当前状态是否已满足本次操作（幂等判定）。"""

    if action is OngoingGoalAction.PAUSE:
        return status == "paused"
    if action is OngoingGoalAction.RESUME:
        return status == "active"
    return status == "completed"


def _goal_display(
    goal: StudyGoalProjection, route: StudyRouteProjection | None
) -> str:
    """目标展示文本：原话 + 真实状态与当前知识点（不新增判断）。"""

    parts = [f"学习目标「{goal.statement}」"]
    if goal.status == "paused":
        parts.append("当前已暂停")
    elif goal.status == "completed":
        parts.append("已完成")
    node = _current_node(route)
    if node is not None:
        parts.append(f"当前知识点：{node.title}")
    return "；".join(parts)[:_DISPLAY_LIMIT]


def _current_node(route: StudyRouteProjection | None) -> StudyRouteNode | None:
    if route is None:
        return None
    return next(
        (node for node in route.nodes if node.node_id == route.current_node_id),
        None,
    )


__all__ = ["STUDY_PROFILE_DOMAIN", "StudyProfileLinkService"]
