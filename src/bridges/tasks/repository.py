"""有来源的跨轮任务、任务版本、有效条件与澄清等待的领域写模型（工单 08）。

领域模块化单体原则（``CONTEXT.md``）：本模块是任务/条件/等待的唯一写模型，
图、消息与运行只保存对任务 ID 的引用。所有查询经 ``scoped(account_id)``
强制账户隔离；所有写入走显式事务边界。

不可变与乐观并发：

- 任务版本一旦写入不再改写；条件修订生成新版本并把被取代条件标为
  ``superseded``（或 ``revoked``），旧值只读保留。
- ``append_version`` 接受 ``expected_version``；与当前版本不符时抛
  :class:`TaskVersionConflict`，由调用方决定重试或拒绝，避免并发覆盖。

组合操作（一次「落地一轮」可能同时写任务、条件、版本与审计）由服务层用
``in_transaction=True`` 串接，保证要么整体提交、要么整体回滚。
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from bridges.contracts.tasks import (
    TASK_CONTRACT_VERSION,
    ConditionOrigin,
    ConditionScope,
    ConditionStatus,
    TaskCondition,
    TaskConditionInput,
    TaskEvent,
    TaskRecord,
    TaskStatus,
    TaskVersion,
    TaskWait,
    WaitStatus,
    status_for_origin,
)
from bridges.storage.database import BridgesDatabase
from bridges.storage.errors import StorageError


class TaskError(StorageError):
    """任务领域的稳定错误基类。"""


class TaskNotFound(TaskError):  # noqa: N818 - 领域语义异常，命名保持可读
    """任务不存在或不属于当前账户。"""


class TaskVersionConflict(TaskError):  # noqa: N818 - 领域语义异常，命名保持可读
    """乐观版本冲突：期望版本与当前版本不符。"""

    def __init__(self, task_id: str, expected: int, current: int) -> None:
        self.task_id = task_id
        self.expected = expected
        self.current = current
        super().__init__(
            f"任务 {task_id} 的版本已变化（期望 {expected}，当前 {current}），"
            "本次修订未提交。"
        )


class TaskStateConflict(TaskError):  # noqa: N818 - 领域语义异常，命名保持可读
    """任务状态不允许该操作（例如对已取消任务续接）。"""


def _new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(12)}"


def _iso(now: datetime) -> str:
    return now.isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _loads(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def _parse_dt(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


class TaskRepository:
    """任务/版本/条件/等待的 SQLite 写模型，全部操作限定在账户内。"""

    def __init__(self, database: BridgesDatabase) -> None:
        self._db = database

    @property
    def database(self) -> BridgesDatabase:
        return self._db

    @contextmanager
    def _tx(self, in_transaction: bool) -> Iterator[None]:
        """事务边界：已在组合事务内则复用，否则自开一个。"""
        if in_transaction:
            yield
            return
        with self._db.transaction():
            yield

    # -- 任务 ---------------------------------------------------------------

    def create_task(
        self,
        *,
        account_id: str,
        conversation_id: str,
        goal: str,
        now: datetime | None = None,
        in_transaction: bool = False,
    ) -> TaskRecord:
        """创建任务并把会话当前指针指向它（同一事务）。"""
        moment = now or datetime.now(UTC)
        task_id = _new_id("task")
        try:
            with self._tx(in_transaction):
                self._db.scoped(account_id).execute(
                    "INSERT INTO conversation_tasks"
                    "(task_id, account_id, conversation_id, goal, status,"
                    " current_version, contract_version, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)",
                    (
                        task_id,
                        account_id,
                        conversation_id,
                        goal,
                        TaskStatus.ACTIVE.value,
                        TASK_CONTRACT_VERSION,
                        _iso(moment),
                        _iso(moment),
                    ),
                )
                self._set_current_task_in_transaction(
                    account_id, conversation_id, task_id
                )
        except StorageError:
            raise
        except Exception as exc:  # noqa: BLE001 - 统一中文错误
            raise TaskError("创建任务失败，请稍后重试。") from exc
        record = self.get_task(account_id, task_id)
        assert record is not None
        return record

    def get_task(self, account_id: str, task_id: str) -> TaskRecord | None:
        row = (
            self._db.scoped(account_id)
            .execute(
                "SELECT * FROM conversation_tasks"
                " WHERE account_id = ? AND task_id = ?",
                (account_id, task_id),
            )
            .fetchone()
        )
        return self._task_from_row(row) if row else None

    def list_tasks(self, account_id: str, conversation_id: str) -> list[TaskRecord]:
        rows = (
            self._db.scoped(account_id)
            .execute(
                "SELECT * FROM conversation_tasks"
                " WHERE account_id = ? AND conversation_id = ?"
                " ORDER BY created_at, task_id",
                (account_id, conversation_id),
            )
            .fetchall()
        )
        return [self._task_from_row(row) for row in rows]

    def current_task(self, account_id: str, conversation_id: str) -> TaskRecord | None:
        """读取会话当前任务指针指向的任务；指针悬空时返回 None。"""
        row = (
            self._db.scoped(account_id)
            .execute(
                "SELECT current_task_id FROM conversations"
                " WHERE account_id = ? AND conversation_id = ?",
                (account_id, conversation_id),
            )
            .fetchone()
        )
        if row is None or row["current_task_id"] is None:
            return None
        return self.get_task(account_id, str(row["current_task_id"]))

    def set_current_task(
        self,
        account_id: str,
        conversation_id: str,
        task_id: str | None,
        *,
        in_transaction: bool = False,
    ) -> None:
        """更新会话当前任务指针（单一事实源）。"""
        with self._tx(in_transaction):
            self._set_current_task_in_transaction(account_id, conversation_id, task_id)

    def _set_current_task_in_transaction(
        self, account_id: str, conversation_id: str, task_id: str | None
    ) -> None:
        self._db.scoped(account_id).execute(
            "UPDATE conversations SET current_task_id = ?"
            " WHERE account_id = ? AND conversation_id = ?",
            (task_id, account_id, conversation_id),
        )

    def rebuild_current_pointer(
        self, account_id: str, conversation_id: str
    ) -> str | None:
        """从持久任务重建当前指针（恢复/重建入口）。

        取最近更新且未取消的任务；全部已取消时指针清空。用于进程重启或
        指针损坏后的确定性恢复，不猜测用户意图。
        """
        row = (
            self._db.scoped(account_id)
            .execute(
                "SELECT task_id FROM conversation_tasks"
                " WHERE account_id = ? AND conversation_id = ? AND status <> ?"
                " ORDER BY updated_at DESC, task_id DESC LIMIT 1",
                (account_id, conversation_id, TaskStatus.CANCELLED.value),
            )
            .fetchone()
        )
        task_id = str(row["task_id"]) if row else None
        self.set_current_task(account_id, conversation_id, task_id)
        return task_id

    def update_task_status(
        self,
        account_id: str,
        task_id: str,
        status: TaskStatus,
        *,
        goal: str | None = None,
        now: datetime | None = None,
        in_transaction: bool = False,
    ) -> TaskRecord:
        moment = now or datetime.now(UTC)
        assignments = ["status = ?", "updated_at = ?"]
        params: list[Any] = [status.value, _iso(moment)]
        if goal is not None:
            assignments.append("goal = ?")
            params.append(goal)
        if status == TaskStatus.COMPLETED:
            assignments.append("completed_at = ?")
            params.append(_iso(moment))
        if status == TaskStatus.CANCELLED:
            assignments.append("cancelled_at = ?")
            params.append(_iso(moment))
        params.extend([account_id, task_id])
        with self._tx(in_transaction):
            self._db.scoped(account_id).execute(
                f"UPDATE conversation_tasks SET {', '.join(assignments)}"
                " WHERE account_id = ? AND task_id = ?",
                tuple(params),
            )
        record = self.get_task(account_id, task_id)
        if record is None:
            raise TaskNotFound("任务不存在或不属于当前账户。")
        return record

    # -- 版本 ---------------------------------------------------------------

    def append_version(
        self,
        *,
        account_id: str,
        task_id: str,
        goal: str,
        condition_ids: list[str],
        source_message_ids: list[str],
        expected_version: int | None = None,
        now: datetime | None = None,
        in_transaction: bool = False,
    ) -> TaskVersion:
        """追加不可变版本并推进当前版本号。

        ``expected_version`` 非空时做乐观检查：与当前版本不符即拒绝，
        不写入任何行。
        """
        moment = now or datetime.now(UTC)
        task = self.get_task(account_id, task_id)
        if task is None:
            raise TaskNotFound("任务不存在或不属于当前账户。")
        if expected_version is not None and expected_version != task.current_version:
            raise TaskVersionConflict(task_id, expected_version, task.current_version)
        new_version = task.current_version + 1
        version_id = _new_id("tv")
        try:
            with self._tx(in_transaction):
                self._db.scoped(account_id).execute(
                    "INSERT INTO task_versions"
                    "(version_id, task_id, account_id, version, goal,"
                    " condition_ids_json, source_message_ids_json,"
                    " supersedes_version, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        version_id,
                        task_id,
                        account_id,
                        new_version,
                        goal,
                        _json(condition_ids),
                        _json(source_message_ids),
                        task.current_version if task.current_version > 0 else None,
                        _iso(moment),
                    ),
                )
                self._db.scoped(account_id).execute(
                    "UPDATE conversation_tasks SET current_version = ?, goal = ?,"
                    " updated_at = ? WHERE account_id = ? AND task_id = ?",
                    (new_version, goal, _iso(moment), account_id, task_id),
                )
        except StorageError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise TaskError("保存任务版本失败，请稍后重试。") from exc
        return self.get_version(account_id, task_id, new_version)

    def get_version(self, account_id: str, task_id: str, version: int) -> TaskVersion:
        row = (
            self._db.scoped(account_id)
            .execute(
                "SELECT * FROM task_versions"
                " WHERE account_id = ? AND task_id = ? AND version = ?",
                (account_id, task_id, version),
            )
            .fetchone()
        )
        if row is None:
            raise TaskNotFound("任务版本不存在或不属于当前账户。")
        return self._version_from_row(row)

    def list_versions(self, account_id: str, task_id: str) -> list[TaskVersion]:
        rows = (
            self._db.scoped(account_id)
            .execute(
                "SELECT * FROM task_versions"
                " WHERE account_id = ? AND task_id = ? ORDER BY version",
                (account_id, task_id),
            )
            .fetchall()
        )
        return [self._version_from_row(row) for row in rows]

    # -- 条件 ---------------------------------------------------------------

    def insert_conditions(
        self,
        *,
        account_id: str,
        task_id: str,
        conversation_id: str,
        version: int,
        conditions: list[TaskConditionInput],
        now: datetime | None = None,
        in_transaction: bool = False,
    ) -> list[TaskCondition]:
        """落地一批条件，并按来源与取代关系设置状态。

        用户明示修订同 ``kind`` 的旧有效条件时自动取代旧值；显式
        ``replaces`` 指向的条件也被取代。助手草案与模型推测只作草案/线索。
        """
        moment = now or datetime.now(UTC)

        def _apply() -> list[TaskCondition]:
            created: list[TaskCondition] = []
            for item in conditions:
                status = status_for_origin(item.origin)
                supersedes = item.replaces
                if supersedes is None and item.origin == ConditionOrigin.USER_STATED:
                    # 同类别的明示修订取代仍有效的旧值（5,000 → 3,000）；
                    # 会话范围按会话 + 范围 + 类别定位，任务范围按任务定位。
                    if item.scope == ConditionScope.CONVERSATION:
                        prior = self._find_effective_conversation_condition_by_kind(
                            account_id, conversation_id, item.kind
                        )
                    else:
                        prior = self._find_effective_condition_by_kind(
                            account_id, task_id, item.kind
                        )
                    supersedes = prior.condition_id if prior else None
                condition_id = _new_id("cond")
                self._db.scoped(account_id).execute(
                    "INSERT INTO task_conditions"
                    "(condition_id, task_id, account_id, conversation_id, version,"
                    " kind, text, scope, origin, status, source_message_id,"
                    " source_span, supersedes_condition_id, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        condition_id,
                        task_id,
                        account_id,
                        conversation_id,
                        version,
                        item.kind,
                        item.text,
                        item.scope.value,
                        item.origin.value,
                        status.value,
                        item.source_message_id,
                        item.source_span,
                        supersedes,
                        _iso(moment),
                        _iso(moment),
                    ),
                )
                if supersedes is not None:
                    self._supersede_in_transaction(
                        account_id, supersedes, condition_id, moment
                    )
                created.append(self.get_condition(account_id, condition_id))
            return created

        with self._tx(in_transaction):
            return _apply()

    def get_condition(self, account_id: str, condition_id: str) -> TaskCondition:
        row = (
            self._db.scoped(account_id)
            .execute(
                "SELECT * FROM task_conditions"
                " WHERE account_id = ? AND condition_id = ?",
                (account_id, condition_id),
            )
            .fetchone()
        )
        if row is None:
            raise TaskNotFound("条件不存在或不属于当前账户。")
        return self._condition_from_row(row)

    def list_conditions(self, account_id: str, task_id: str) -> list[TaskCondition]:
        rows = (
            self._db.scoped(account_id)
            .execute(
                "SELECT * FROM task_conditions"
                " WHERE account_id = ? AND task_id = ? ORDER BY created_at, condition_id",
                (account_id, task_id),
            )
            .fetchall()
        )
        return [self._condition_from_row(row) for row in rows]

    def list_effective_conditions(
        self, account_id: str, task_id: str
    ) -> list[TaskCondition]:
        """任务内仍有效的条件（用户约束与工具事实），草案/线索被排除。

        只返回 ``scope='task'`` 的条件；会话范围条件由
        :meth:`list_conversation_conditions` 单独返回，两者不重复。
        """
        rows = (
            self._db.scoped(account_id)
            .execute(
                "SELECT * FROM task_conditions"
                " WHERE account_id = ? AND task_id = ? AND scope = ? AND status = ?"
                " ORDER BY created_at, condition_id",
                (
                    account_id,
                    task_id,
                    ConditionScope.TASK.value,
                    ConditionStatus.EFFECTIVE.value,
                ),
            )
            .fetchall()
        )
        return [self._condition_from_row(row) for row in rows]

    def list_conversation_conditions(
        self, account_id: str, conversation_id: str
    ) -> list[TaskCondition]:
        """整个会话范围内仍有效的条件（用户明示「这个聊天都……」）。"""
        rows = (
            self._db.scoped(account_id)
            .execute(
                "SELECT * FROM task_conditions"
                " WHERE account_id = ? AND conversation_id = ? AND scope = ?"
                " AND status = ? ORDER BY created_at, condition_id",
                (
                    account_id,
                    conversation_id,
                    ConditionScope.CONVERSATION.value,
                    ConditionStatus.EFFECTIVE.value,
                ),
            )
            .fetchall()
        )
        return [self._condition_from_row(row) for row in rows]

    def revoke_condition(
        self,
        account_id: str,
        condition_id: str,
        *,
        by_condition_id: str | None = None,
        now: datetime | None = None,
        in_transaction: bool = False,
    ) -> None:
        """撤销一条条件（用户明确撤销）；被撤销的值不因话题往返复活。"""
        moment = now or datetime.now(UTC)
        with self._tx(in_transaction):
            self._db.scoped(account_id).execute(
                "UPDATE task_conditions SET status = ?, superseded_by = ?,"
                " updated_at = ? WHERE account_id = ? AND condition_id = ?",
                (
                    ConditionStatus.REVOKED.value,
                    by_condition_id,
                    _iso(moment),
                    account_id,
                    condition_id,
                ),
            )

    def _find_effective_condition_by_kind(
        self, account_id: str, task_id: str, kind: str
    ) -> TaskCondition | None:
        row = (
            self._db.scoped(account_id)
            .execute(
                "SELECT * FROM task_conditions"
                " WHERE account_id = ? AND task_id = ? AND kind = ? AND status = ?"
                " ORDER BY created_at DESC, condition_id DESC LIMIT 1",
                (account_id, task_id, kind, ConditionStatus.EFFECTIVE.value),
            )
            .fetchone()
        )
        return self._condition_from_row(row) if row else None

    def _find_effective_conversation_condition_by_kind(
        self, account_id: str, conversation_id: str, kind: str
    ) -> TaskCondition | None:
        row = (
            self._db.scoped(account_id)
            .execute(
                "SELECT * FROM task_conditions"
                " WHERE account_id = ? AND conversation_id = ? AND scope = ?"
                " AND kind = ? AND status = ?"
                " ORDER BY created_at DESC, condition_id DESC LIMIT 1",
                (
                    account_id,
                    conversation_id,
                    ConditionScope.CONVERSATION.value,
                    kind,
                    ConditionStatus.EFFECTIVE.value,
                ),
            )
            .fetchone()
        )
        return self._condition_from_row(row) if row else None

    def _supersede_in_transaction(
        self,
        account_id: str,
        condition_id: str,
        by_condition_id: str,
        moment: datetime,
    ) -> None:
        self._db.scoped(account_id).execute(
            "UPDATE task_conditions SET status = ?, superseded_by = ?, updated_at = ?"
            " WHERE account_id = ? AND condition_id = ?",
            (
                ConditionStatus.SUPERSEDED.value,
                by_condition_id,
                _iso(moment),
                account_id,
                condition_id,
            ),
        )

    # -- 等待 ---------------------------------------------------------------

    def open_wait(
        self,
        *,
        account_id: str,
        task_id: str,
        conversation_id: str,
        expected_version: int,
        missing_fields: list[str],
        question: str,
        origin_message_id: str,
        source_message_id: str,
        now: datetime | None = None,
        in_transaction: bool = False,
    ) -> TaskWait:
        """保存一个澄清等待并让任务进入等待状态（同一事务）。

        等待不携带租约：写入即表示执行资源已释放（见工单第 7 条）。
        """
        moment = now or datetime.now(UTC)
        wait_id = _new_id("wait")
        try:
            with self._tx(in_transaction):
                self._db.scoped(account_id).execute(
                    "INSERT INTO task_waits"
                    "(wait_id, task_id, account_id, conversation_id, expected_version,"
                    " missing_fields_json, question, origin_message_id, status,"
                    " source_message_id, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        wait_id,
                        task_id,
                        account_id,
                        conversation_id,
                        expected_version,
                        _json(missing_fields),
                        question,
                        origin_message_id,
                        WaitStatus.OPEN.value,
                        source_message_id,
                        _iso(moment),
                        _iso(moment),
                    ),
                )
                self._db.scoped(account_id).execute(
                    "UPDATE conversation_tasks SET status = ?, updated_at = ?"
                    " WHERE account_id = ? AND task_id = ?",
                    (TaskStatus.WAITING.value, _iso(moment), account_id, task_id),
                )
        except StorageError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise TaskError("保存澄清等待失败，请稍后重试。") from exc
        return self.get_wait(account_id, wait_id)

    def get_wait(self, account_id: str, wait_id: str) -> TaskWait:
        row = (
            self._db.scoped(account_id)
            .execute(
                "SELECT * FROM task_waits WHERE account_id = ? AND wait_id = ?",
                (account_id, wait_id),
            )
            .fetchone()
        )
        if row is None:
            raise TaskNotFound("等待项不存在或不属于当前账户。")
        return self._wait_from_row(row)

    def list_waits(
        self,
        account_id: str,
        task_id: str,
        *,
        statuses: tuple[WaitStatus, ...] | None = None,
    ) -> list[TaskWait]:
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            rows = (
                self._db.scoped(account_id)
                .execute(
                    "SELECT * FROM task_waits"
                    f" WHERE account_id = ? AND task_id = ? AND status IN ({placeholders})"
                    " ORDER BY created_at, wait_id",
                    (account_id, task_id, *(s.value for s in statuses)),
                )
                .fetchall()
            )
        else:
            rows = (
                self._db.scoped(account_id)
                .execute(
                    "SELECT * FROM task_waits"
                    " WHERE account_id = ? AND task_id = ? ORDER BY created_at, wait_id",
                    (account_id, task_id),
                )
                .fetchall()
            )
        return [self._wait_from_row(row) for row in rows]

    def list_open_waits(self, account_id: str, conversation_id: str) -> list[TaskWait]:
        """会话内当前激活（``open``）的等待项。"""
        rows = (
            self._db.scoped(account_id)
            .execute(
                "SELECT * FROM task_waits"
                " WHERE account_id = ? AND conversation_id = ? AND status = ?"
                " ORDER BY created_at, wait_id",
                (account_id, conversation_id, WaitStatus.OPEN.value),
            )
            .fetchall()
        )
        return [self._wait_from_row(row) for row in rows]

    def resolve_wait(
        self,
        *,
        account_id: str,
        wait_id: str,
        resolved_by_message_id: str,
        now: datetime | None = None,
        in_transaction: bool = False,
    ) -> TaskWait:
        """把等待项置为已解决并恢复任务为活跃（释放执行资源）。"""
        moment = now or datetime.now(UTC)
        wait = self.get_wait(account_id, wait_id)
        with self._tx(in_transaction):
            self._db.scoped(account_id).execute(
                "UPDATE task_waits SET status = ?, resolved_by_message_id = ?,"
                " resolved_at = ?, released_at = ?, updated_at = ?"
                " WHERE account_id = ? AND wait_id = ?",
                (
                    WaitStatus.RESOLVED.value,
                    resolved_by_message_id,
                    _iso(moment),
                    _iso(moment),
                    _iso(moment),
                    account_id,
                    wait_id,
                ),
            )
            self._db.scoped(account_id).execute(
                "UPDATE conversation_tasks SET status = ?, updated_at = ?"
                " WHERE account_id = ? AND task_id = ? AND status = ?",
                (
                    TaskStatus.ACTIVE.value,
                    _iso(moment),
                    account_id,
                    wait.task_id,
                    TaskStatus.WAITING.value,
                ),
            )
        return self.get_wait(account_id, wait_id)

    def suspend_open_waits(
        self,
        account_id: str,
        task_id: str,
        *,
        now: datetime | None = None,
        in_transaction: bool = False,
    ) -> list[str]:
        """暂停任务时让仍开启的等待项失去当前激活状态（不删除）。"""
        moment = now or datetime.now(UTC)
        open_waits = self.list_waits(account_id, task_id, statuses=(WaitStatus.OPEN,))
        if not open_waits:
            return []
        with self._tx(in_transaction):
            for wait in open_waits:
                self._db.scoped(account_id).execute(
                    "UPDATE task_waits SET status = ?, updated_at = ?"
                    " WHERE account_id = ? AND wait_id = ?",
                    (WaitStatus.SUSPENDED.value, _iso(moment), account_id, wait.wait_id),
                )
        return [wait.wait_id for wait in open_waits]

    def expire_waits(
        self,
        account_id: str,
        task_id: str,
        *,
        statuses: tuple[WaitStatus, ...] = (WaitStatus.OPEN, WaitStatus.SUSPENDED),
        now: datetime | None = None,
        in_transaction: bool = False,
    ) -> list[str]:
        """让等待项进入终态并释放资源（取消任务时使用）。

        终态等待不再被后续消息填充，也不会被悄悄恢复为 ``open``。
        """
        moment = now or datetime.now(UTC)
        pending = self.list_waits(account_id, task_id, statuses=statuses)
        if not pending:
            return []
        with self._tx(in_transaction):
            for wait in pending:
                self._db.scoped(account_id).execute(
                    "UPDATE task_waits SET status = ?, released_at = ?, updated_at = ?"
                    " WHERE account_id = ? AND wait_id = ?",
                    (
                        WaitStatus.EXPIRED.value,
                        _iso(moment),
                        _iso(moment),
                        account_id,
                        wait.wait_id,
                    ),
                )
        return [wait.wait_id for wait in pending]

    # -- 审计 ---------------------------------------------------------------

    def record_event(
        self,
        *,
        account_id: str,
        conversation_id: str,
        kind: str,
        task_id: str | None = None,
        payload: dict[str, Any] | None = None,
        now: datetime | None = None,
        in_transaction: bool = False,
    ) -> TaskEvent:
        moment = now or datetime.now(UTC)
        event_id = _new_id("te")

        def _apply() -> TaskEvent:
            self._db.scoped(account_id).execute(
                "INSERT INTO task_events"
                "(event_id, account_id, conversation_id, task_id, kind,"
                " payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    event_id,
                    account_id,
                    conversation_id,
                    task_id,
                    kind,
                    _json(payload or {}),
                    _iso(moment),
                ),
            )
            return TaskEvent(
                event_id=event_id,
                account_id=account_id,
                conversation_id=conversation_id,
                task_id=task_id,
                kind=kind,
                payload=payload or {},
                created_at=moment,
            )

        with self._tx(in_transaction):
            return _apply()

    def list_events(self, account_id: str, conversation_id: str) -> list[TaskEvent]:
        rows = (
            self._db.scoped(account_id)
            .execute(
                "SELECT * FROM task_events"
                " WHERE account_id = ? AND conversation_id = ?"
                " ORDER BY created_at, event_id",
                (account_id, conversation_id),
            )
            .fetchall()
        )
        return [
            TaskEvent(
                event_id=str(row["event_id"]),
                account_id=str(row["account_id"]),
                conversation_id=str(row["conversation_id"]),
                task_id=row["task_id"],
                kind=str(row["kind"]),
                payload=_loads(row["payload_json"], {}),
                created_at=datetime.fromisoformat(str(row["created_at"])),
            )
            for row in rows
        ]

    # -- 行转换 -------------------------------------------------------------

    @staticmethod
    def _task_from_row(row: Any) -> TaskRecord:
        return TaskRecord(
            task_id=str(row["task_id"]),
            account_id=str(row["account_id"]),
            conversation_id=str(row["conversation_id"]),
            goal=str(row["goal"]),
            status=TaskStatus(str(row["status"])),
            current_version=int(row["current_version"]),
            contract_version=str(row["contract_version"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
            completed_at=_parse_dt(row["completed_at"]),
            cancelled_at=_parse_dt(row["cancelled_at"]),
        )

    @staticmethod
    def _version_from_row(row: Any) -> TaskVersion:
        return TaskVersion(
            version_id=str(row["version_id"]),
            task_id=str(row["task_id"]),
            version=int(row["version"]),
            goal=str(row["goal"]),
            condition_ids=_loads(row["condition_ids_json"], []),
            source_message_ids=_loads(row["source_message_ids_json"], []),
            supersedes_version=(
                int(row["supersedes_version"])
                if row["supersedes_version"] is not None
                else None
            ),
            invalidated_at=_parse_dt(row["invalidated_at"]),
            invalidation_reason=row["invalidation_reason"],
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )

    @staticmethod
    def _condition_from_row(row: Any) -> TaskCondition:
        return TaskCondition(
            condition_id=str(row["condition_id"]),
            task_id=str(row["task_id"]),
            version=int(row["version"]),
            kind=str(row["kind"]),
            text=str(row["text"]),
            scope=ConditionScope(str(row["scope"])),
            origin=ConditionOrigin(str(row["origin"])),
            status=ConditionStatus(str(row["status"])),
            source_message_id=str(row["source_message_id"]),
            source_span=row["source_span"],
            supersedes_condition_id=row["supersedes_condition_id"],
            superseded_by=row["superseded_by"],
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    @staticmethod
    def _wait_from_row(row: Any) -> TaskWait:
        return TaskWait(
            wait_id=str(row["wait_id"]),
            task_id=str(row["task_id"]),
            conversation_id=str(row["conversation_id"]),
            expected_version=int(row["expected_version"]),
            missing_fields=_loads(row["missing_fields_json"], []),
            question=str(row["question"]),
            origin_message_id=str(row["origin_message_id"]),
            status=WaitStatus(str(row["status"])),
            source_message_id=str(row["source_message_id"]),
            resolved_by_message_id=row["resolved_by_message_id"],
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
            resolved_at=_parse_dt(row["resolved_at"]),
            released_at=_parse_dt(row["released_at"]),
        )
