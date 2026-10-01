"""跨轮任务、有效条件与澄清等待的领域服务（工单 08）。

职责边界：本服务是任务状态机的**唯一裁决点**。工单 12 的主智能体负责
「理解」并提交 :class:`TaskTurnRequest`（任务关系、目标、条件、缺失字段），
本服务负责「落地」：按代码规则解析关系、绑定条件、生成版本、处置等待，
并把结果投影给 API 与后续消费者。模型不直接改表，也不能用置信度替代
来源。

关键不变量（与工单验收标准一一对应）：

- **来源绑定**：每条条件携带 ``source_message_id``；草案与线索不进入
  有效条件。
- **最新纠正生效**：同类别明示修订立即取代旧值并生成新版本；被取代的
  旧值不因话题往返复活。
- **范围隔离**：任务条件默认只约束原任务，换话题时不采用；会话条件
  （用户明示「这个聊天都……」）跨话题保留。
- **等待匹配**：只有明确答复、版本匹配且实际补齐缺失字段的消息才填等待；
  新话题、画像命令与学习动作不填旧等待，并让旧等待失去当前激活状态。
- **终态不复活**：取消的任务不再续接，其等待进入终态并释放资源。
"""

from __future__ import annotations

from datetime import UTC, datetime

from bridges.contracts.tasks import (
    ConditionOrigin,
    ConditionScope,
    TaskCondition,
    TaskConditionInput,
    TaskProjection,
    TaskRecord,
    TaskRelation,
    TaskStatus,
    TaskTurnRequest,
    TaskTurnResult,
    TaskVersion,
    TaskWait,
    WaitResolution,
    WaitStatus,
)
from bridges.tasks.repository import (
    TaskNotFound,
    TaskRepository,
    TaskStateConflict,
    TaskVersionConflict,
)

#: 可以直接成为用户约束的来源（用户明示 + 工具事实）。
_BINDING_ORIGINS = frozenset(
    {ConditionOrigin.USER_STATED, ConditionOrigin.TOOL_OBSERVATION}
)


class TaskService:
    """任务领域服务：关系解析、版本生成、条件绑定与等待处置。"""

    def __init__(self, repository: TaskRepository) -> None:
        self._repo = repository

    @property
    def repository(self) -> TaskRepository:
        return self._repo

    # -- 读取投影 -----------------------------------------------------------

    def projection(self, account_id: str, task_id: str) -> TaskProjection | None:
        task = self._repo.get_task(account_id, task_id)
        if task is None:
            return None
        return self._build_projection(account_id, task)

    def list_projections(
        self, account_id: str, conversation_id: str
    ) -> list[TaskProjection]:
        return [
            self._build_projection(account_id, task)
            for task in self._repo.list_tasks(account_id, conversation_id)
        ]

    def list_versions(self, account_id: str, task_id: str) -> list[TaskVersion]:
        return self._repo.list_versions(account_id, task_id)

    def list_waits(self, account_id: str, task_id: str) -> list[TaskWait]:
        return self._repo.list_waits(account_id, task_id)

    def list_conditions(self, account_id: str, task_id: str) -> list[TaskCondition]:
        return self._repo.list_conditions(account_id, task_id)

    def _build_projection(self, account_id: str, task: TaskRecord) -> TaskProjection:
        return TaskProjection(
            task=task,
            effective_conditions=self._repo.list_effective_conditions(
                account_id, task.task_id
            ),
            conversation_conditions=self._repo.list_conversation_conditions(
                account_id, task.conversation_id
            ),
            open_waits=self._repo.list_waits(
                account_id, task.task_id, statuses=(WaitStatus.OPEN,)
            ),
        )

    # -- 澄清等待 -----------------------------------------------------------

    def open_wait(
        self,
        account_id: str,
        *,
        conversation_id: str,
        question: str,
        missing_fields: list[str],
        origin_message_id: str,
        source_message_id: str,
        task_id: str | None = None,
        now: datetime | None = None,
    ) -> TaskWait:
        """为当前任务登记一个澄清等待（等待不保留工作租约）。"""
        task = (
            self._repo.get_task(account_id, task_id)
            if task_id is not None
            else self._repo.current_task(account_id, conversation_id)
        )
        if task is None or task.conversation_id != conversation_id:
            raise TaskNotFound("没有可登记澄清等待的任务。")
        return self._repo.open_wait(
            account_id=account_id,
            task_id=task.task_id,
            conversation_id=conversation_id,
            expected_version=task.current_version,
            missing_fields=missing_fields,
            question=question,
            origin_message_id=origin_message_id,
            source_message_id=source_message_id,
            now=now,
        )

    # -- 关系落地 -----------------------------------------------------------

    def apply_turn(self, account_id: str, request: TaskTurnRequest) -> TaskTurnResult:
        """落地一轮任务关系，返回结果投影。

        普通聊天（无目标且无绑定来源条件）不创建任务——这是「普通聊天
        轻量」的强制点。
        """
        moment = datetime.now(UTC)
        conversation_id = request.conversation_id
        target = self._resolve_target(account_id, conversation_id, request)
        events: list[str] = []

        if request.relation == TaskRelation.NEW:
            return self._apply_new(account_id, request, target, moment, events)
        if request.relation == TaskRelation.CONTINUE:
            return self._apply_continue(account_id, request, target, moment, events)
        if request.relation == TaskRelation.REVISE:
            return self._apply_revise(account_id, request, target, moment, events)
        if request.relation == TaskRelation.PAUSE:
            return self._apply_pause(account_id, request, target, moment, events)
        # CANCEL
        return self._apply_cancel(account_id, request, target, moment, events)

    # -- 各关系实现 ---------------------------------------------------------

    def _apply_new(
        self,
        account_id: str,
        request: TaskTurnRequest,
        target: TaskRecord | None,
        moment: datetime,
        events: list[str],
    ) -> TaskTurnResult:
        conversation_id = request.conversation_id
        paused: list[str] = []
        resolution = WaitResolution.NONE
        reason: str | None = None
        # 换话题：暂停仍活跃的旧任务，并让它的旧等待失去当前激活状态。
        if target is not None and target.status in {TaskStatus.ACTIVE, TaskStatus.WAITING}:
            with self._repo.database.transaction():
                self._repo.update_task_status(
                    account_id, target.task_id, TaskStatus.PAUSED,
                    now=moment, in_transaction=True,
                )
                suspended = self._repo.suspend_open_waits(
                    account_id, target.task_id, now=moment, in_transaction=True
                )
                self._repo.record_event(
                    account_id=account_id, conversation_id=conversation_id,
                    task_id=target.task_id, kind="task_paused",
                    payload={"reason": "new_topic", "suspended_waits": suspended},
                    now=moment, in_transaction=True,
                )
            paused.append(target.task_id)
            events.append("task_paused")
            if suspended:
                events.append("waits_suspended")
            if suspended:
                resolution = WaitResolution.REJECTED
                reason = "已换话题，旧澄清等待不再接收本轮消息。"

        if not self._should_create_task(request):
            # 普通聊天：不创建复杂持久任务。
            return TaskTurnResult(
                created=False, task=None, paused_task_ids=paused,
                wait_resolution=resolution, wait_rejection_reason=reason, events=events,
            )

        goal = (request.goal or "").strip()
        task, conditions = self._create_task_with_conditions(
            account_id, request, goal, moment
        )
        events.append("task_created")
        if conditions:
            events.append("version_created")
        projection = self._build_projection(account_id, task)
        return TaskTurnResult(
            created=True, task=projection, paused_task_ids=paused,
            wait_resolution=resolution, wait_rejection_reason=reason, events=events,
        )

    def _apply_continue(
        self,
        account_id: str,
        request: TaskTurnRequest,
        target: TaskRecord | None,
        moment: datetime,
        events: list[str],
    ) -> TaskTurnResult:
        if target is None:
            # 没有可续接的任务：仅在确有目标/约束时新建，否则保持轻量。
            if not self._should_create_task(request):
                return TaskTurnResult(created=False, task=None, events=events)
            goal = (request.goal or "").strip()
            task, conditions = self._create_task_with_conditions(
                account_id, request, goal, moment
            )
            events.append("task_created")
            if conditions:
                events.append("version_created")
            return TaskTurnResult(
                created=True,
                task=self._build_projection(account_id, task),
                events=events,
            )

        if target.status == TaskStatus.CANCELLED:
            raise TaskStateConflict("该任务已取消，请创建新任务，不要续接已取消的目标。")

        # 先尝试匹配并填充等待（只有明确答复、版本匹配且补齐缺失字段才接受）。
        resolution, resolved_wait, reason = self._try_resolve_wait(
            account_id, request, target, moment
        )
        if resolution == WaitResolution.ACCEPTED:
            events.append("wait_resolved")

        resumed = False
        if target.status == TaskStatus.PAUSED:
            self._repo.update_task_status(
                account_id, target.task_id, TaskStatus.ACTIVE, now=moment
            )
            self._repo.record_event(
                account_id=account_id, conversation_id=request.conversation_id,
                task_id=target.task_id, kind="task_resumed", now=moment,
            )
            events.append("task_resumed")
            resumed = True

        # 附带新条件（或目标变化）时生成新版本；否则沿用当前版本。
        if request.conditions or (
            request.goal is not None and request.goal.strip() != target.goal
        ):
            self._append_revision(
                account_id, request, target, moment, expected=request.expected_version
            )
            events.append("version_created")

        del resumed
        refreshed = self._repo.get_task(account_id, target.task_id)
        assert refreshed is not None
        return TaskTurnResult(
            created=False, task=self._build_projection(account_id, refreshed),
            wait_resolution=resolution, resolved_wait=resolved_wait,
            wait_rejection_reason=reason, events=events,
        )

    def _apply_revise(
        self,
        account_id: str,
        request: TaskTurnRequest,
        target: TaskRecord | None,
        moment: datetime,
        events: list[str],
    ) -> TaskTurnResult:
        if target is None:
            raise TaskNotFound("没有可修订的任务。")
        if target.status == TaskStatus.CANCELLED:
            raise TaskStateConflict("该任务已取消，不能修订；请创建新任务。")

        resolution, resolved_wait, reason = self._try_resolve_wait(
            account_id, request, target, moment
        )
        if resolution == WaitResolution.ACCEPTED:
            events.append("wait_resolved")

        self._append_revision(
            account_id, request, target, moment, expected=request.expected_version
        )
        events.append("version_created")
        # 修订使任务回到活跃（完成/暂停的目标可通过新版本继续）。
        if target.status in {TaskStatus.PAUSED, TaskStatus.COMPLETED, TaskStatus.WAITING}:
            self._repo.update_task_status(
                account_id, target.task_id, TaskStatus.ACTIVE, now=moment
            )
            events.append("task_activated")

        task = self._repo.get_task(account_id, target.task_id)
        assert task is not None
        return TaskTurnResult(
            created=False, task=self._build_projection(account_id, task),
            wait_resolution=resolution, resolved_wait=resolved_wait,
            wait_rejection_reason=reason, events=events,
        )

    def _apply_pause(
        self,
        account_id: str,
        request: TaskTurnRequest,
        target: TaskRecord | None,
        moment: datetime,
        events: list[str],
    ) -> TaskTurnResult:
        if target is None or target.status in {TaskStatus.CANCELLED, TaskStatus.COMPLETED}:
            return TaskTurnResult(created=False, task=None, events=events)
        with self._repo.database.transaction():
            self._repo.update_task_status(
                account_id, target.task_id, TaskStatus.PAUSED,
                now=moment, in_transaction=True,
            )
            suspended = self._repo.suspend_open_waits(
                account_id, target.task_id, now=moment, in_transaction=True
            )
            self._repo.record_event(
                account_id=account_id, conversation_id=request.conversation_id,
                task_id=target.task_id, kind="task_paused",
                payload={"reason": "explicit", "suspended_waits": suspended},
                now=moment, in_transaction=True,
            )
        events.append("task_paused")
        if suspended:
            events.append("waits_suspended")
        task = self._repo.get_task(account_id, target.task_id)
        assert task is not None
        return TaskTurnResult(
            created=False, task=self._build_projection(account_id, task), events=events
        )

    def _apply_cancel(
        self,
        account_id: str,
        request: TaskTurnRequest,
        target: TaskRecord | None,
        moment: datetime,
        events: list[str],
    ) -> TaskTurnResult:
        if target is None or target.status == TaskStatus.CANCELLED:
            return TaskTurnResult(created=False, task=None, events=events)
        with self._repo.database.transaction():
            self._repo.update_task_status(
                account_id, target.task_id, TaskStatus.CANCELLED,
                now=moment, in_transaction=True,
            )
            expired = self._repo.expire_waits(
                account_id, target.task_id, now=moment, in_transaction=True
            )
            current = self._repo.current_task(account_id, request.conversation_id)
            if current is not None and current.task_id == target.task_id:
                self._repo.set_current_task(
                    account_id, request.conversation_id, None, in_transaction=True
                )
            self._repo.record_event(
                account_id=account_id, conversation_id=request.conversation_id,
                task_id=target.task_id, kind="task_cancelled",
                payload={"expired_waits": expired}, now=moment, in_transaction=True,
            )
        events.append("task_cancelled")
        if expired:
            events.append("waits_expired")
        task = self._repo.get_task(account_id, target.task_id)
        assert task is not None
        return TaskTurnResult(
            created=False, task=self._build_projection(account_id, task),
            cancelled_task_ids=[target.task_id], events=events,
        )

    # -- 内部工具 -----------------------------------------------------------

    def _resolve_target(
        self, account_id: str, conversation_id: str, request: TaskTurnRequest
    ) -> TaskRecord | None:
        if request.explicit_task_id is not None:
            task = self._repo.get_task(account_id, request.explicit_task_id)
            if task is None or task.conversation_id != conversation_id:
                raise TaskNotFound("指代的任务不存在或不属于当前会话。")
            return task
        return self._repo.current_task(account_id, conversation_id)

    def _should_create_task(self, request: TaskTurnRequest) -> bool:
        """只有真实目标或绑定来源条件才创建任务；普通聊天保持轻量。"""
        if request.goal is not None and request.goal.strip():
            return True
        return any(item.origin in _BINDING_ORIGINS for item in request.conditions)

    def _create_task_with_conditions(
        self,
        account_id: str,
        request: TaskTurnRequest,
        goal: str,
        moment: datetime,
    ) -> tuple[TaskRecord, list[TaskCondition]]:
        conversation_id = request.conversation_id
        with self._repo.database.transaction():
            task = self._repo.create_task(
                account_id=account_id,
                conversation_id=conversation_id,
                goal=goal,
                now=moment,
                in_transaction=True,
            )
            conditions = self._repo.insert_conditions(
                account_id=account_id,
                task_id=task.task_id,
                conversation_id=conversation_id,
                version=1,
                conditions=request.conditions,
                now=moment,
                in_transaction=True,
            )
            self._repo.append_version(
                account_id=account_id,
                task_id=task.task_id,
                goal=goal,
                condition_ids=[c.condition_id for c in conditions],
                source_message_ids=[request.user_message_id],
                expected_version=0,
                now=moment,
                in_transaction=True,
            )
            self._repo.record_event(
                account_id=account_id, conversation_id=conversation_id,
                task_id=task.task_id, kind="task_created",
                payload={"source_message_id": request.user_message_id},
                now=moment, in_transaction=True,
            )
        refreshed = self._repo.get_task(account_id, task.task_id)
        assert refreshed is not None
        return refreshed, conditions

    def _append_revision(
        self,
        account_id: str,
        request: TaskTurnRequest,
        target: TaskRecord,
        moment: datetime,
        *,
        expected: int | None,
    ) -> TaskVersion:
        if expected is not None and expected != target.current_version:
            raise TaskVersionConflict(target.task_id, expected, target.current_version)
        new_version = target.current_version + 1
        goal = (request.goal or target.goal).strip()
        with self._repo.database.transaction():
            created = self._repo.insert_conditions(
                account_id=account_id,
                task_id=target.task_id,
                conversation_id=request.conversation_id,
                version=new_version,
                conditions=request.conditions,
                now=moment,
                in_transaction=True,
            )
            version = self._repo.append_version(
                account_id=account_id,
                task_id=target.task_id,
                goal=goal,
                condition_ids=[c.condition_id for c in created],
                source_message_ids=[request.user_message_id],
                expected_version=target.current_version,
                now=moment,
                in_transaction=True,
            )
            self._repo.record_event(
                account_id=account_id, conversation_id=request.conversation_id,
                task_id=target.task_id, kind="version_created",
                payload={
                    "version": version.version,
                    "condition_ids": [c.condition_id for c in created],
                },
                now=moment, in_transaction=True,
            )
        return version

    def _try_resolve_wait(
        self,
        account_id: str,
        request: TaskTurnRequest,
        target: TaskRecord,
        moment: datetime,
    ) -> tuple[WaitResolution, TaskWait | None, str | None]:
        """尝试把本轮消息填进目标任务的旧等待。

        只接受匹配答复：必须是答复类关系、不是新话题/画像命令/学习动作、
        版本与提问时一致，且本轮 ``answer_fields`` 确实补齐了缺失字段。
        """
        open_waits = self._repo.list_waits(
            account_id, target.task_id, statuses=(WaitStatus.OPEN,)
        )
        if not open_waits:
            return WaitResolution.NONE, None, None
        wait = open_waits[0]

        if request.is_profile_command:
            return WaitResolution.REJECTED, None, "画像命令不填入旧澄清等待。"
        if request.is_learning_action:
            return WaitResolution.REJECTED, None, "学习阶段动作不填入旧澄清等待。"
        if request.is_new_topic:
            return WaitResolution.REJECTED, None, "已换话题，不填入旧澄清等待。"
        if wait.expected_version != target.current_version:
            return (
                WaitResolution.REJECTED,
                None,
                "任务版本已变化，旧澄清等待不再匹配本轮答复。",
            )
        matched = set(request.answer_fields) & set(wait.missing_fields)
        if not matched:
            return (
                WaitResolution.REJECTED,
                None,
                "本轮消息未补齐等待所需字段，不作为该澄清的答复。",
            )
        resolved = self._repo.resolve_wait(
            account_id=account_id,
            wait_id=wait.wait_id,
            resolved_by_message_id=request.user_message_id,
            now=moment,
        )
        self._repo.record_event(
            account_id=account_id, conversation_id=request.conversation_id,
            task_id=target.task_id, kind="wait_resolved",
            payload={
                "wait_id": wait.wait_id,
                "filled_fields": sorted(matched),
                "resolved_by_message_id": request.user_message_id,
            },
            now=moment,
        )
        return WaitResolution.ACCEPTED, resolved, None


#: 便于调用方按需构造条件输入（例如测试与工单 12 的适配层）。
def user_condition(
    *,
    kind: str,
    text: str,
    source_message_id: str,
    scope: ConditionScope = ConditionScope.TASK,
    source_span: str | None = None,
    replaces: str | None = None,
) -> TaskConditionInput:
    """构造一条「用户明示」条件输入（直接生效）。"""
    return TaskConditionInput(
        kind=kind,
        text=text,
        scope=scope,
        origin=ConditionOrigin.USER_STATED,
        source_message_id=source_message_id,
        source_span=source_span,
        replaces=replaces,
    )
