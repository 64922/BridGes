"""提交前守卫：租约、停止、任务版本与消息归属（改进工单 10）。

节点产物与交付都必须先通过守卫才落盘：失去执行权（旧租约）、已停止、
运行已终态或任务版本已变化的迟到结果一律拒绝，不得覆盖新状态。守卫在
同一写事务内读取行，因此“检查”与“提交”之间不存在可乘窗口。

守卫不直连数据库：运行与消息快照经属主 repository 的只读小接口读取
（由组合根注入），root 级 discipline 见 ``docs/table-owners.md``。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

#: 任务版本引用提供者：(account_id, conversation_id) -> (task_id, version) | None。
TaskVersionProvider = Callable[[str, str], "tuple[str | None, int | None] | None"]


class CommitScopeReader(Protocol):
    """提交守卫需要的最小只读面（属主 repository 提供运行/消息快照）。"""

    def get_generation_run(self, account_id: str, run_id: str) -> Any | None: ...

    def get_message(self, account_id: str, message_id: str) -> Any | None: ...


@dataclass(frozen=True, slots=True)
class CommitDecision:
    """提交守卫的结构化裁决。"""

    ok: bool
    code: str = ""
    message: str = ""


class RunCommitGuard:
    """一次内核执行的租约/停止/版本快照与提交校验。"""

    def __init__(
        self,
        scope: CommitScopeReader,
        *,
        account_id: str,
        run_id: str,
        conversation_id: str,
        assistant_message_id: str,
        task_ref: tuple[str | None, int | None] | None = None,
        task_version_provider: TaskVersionProvider | None = None,
        stop_event: threading.Event | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._scope = scope
        self._account_id = account_id
        self._run_id = run_id
        self._conversation_id = conversation_id
        self._assistant_message_id = assistant_message_id
        self._task_version_provider = task_version_provider
        self._stop_event = stop_event
        self._clock = clock or (lambda: datetime.now(UTC))
        #: 运行启动时的授权快照（capture 填充）。
        self._lease_owner: str | None = None
        self._task_ref: tuple[str | None, int | None] | None = task_ref

    @property
    def lease_owner(self) -> str | None:
        return self._lease_owner

    def capture(self) -> None:
        """记录本次执行的租约归属与任务版本（进入内核前调用一次）。"""
        run = self._scope.get_generation_run(self._account_id, self._run_id)
        if run is not None:
            self._lease_owner = run.lease_owner
        if self._task_ref is None and self._task_version_provider is not None:
            self._task_ref = self._task_version_provider(
                self._account_id, self._conversation_id
            )

    def verify(self) -> CommitDecision:
        """提交前校验；必须在写事务内调用。"""
        now = self._clock()
        run = self._scope.get_generation_run(self._account_id, self._run_id)
        if run is None:
            return CommitDecision(False, "run_missing", "本轮运行已不存在。")
        if run.status not in {"queued", "running"}:
            return CommitDecision(False, "run_terminal", "本轮运行已收敛，迟到结果被拒绝。")
        if self._lease_owner is not None:
            if run.lease_owner != self._lease_owner:
                return CommitDecision(
                    False, "lease_lost", "执行租约已转移，旧执行者的结果被拒绝。"
                )
            if run.lease_expires_at is not None and run.lease_expires_at <= now:
                return CommitDecision(
                    False, "lease_expired", "执行租约已过期，旧执行者的结果被拒绝。"
                )
        message = self._scope.get_message(self._account_id, self._assistant_message_id)
        if message is None or message.conversation_id != self._conversation_id:
            return CommitDecision(False, "message_missing", "助手消息不存在。")
        if getattr(message.status, "value", message.status) != "streaming":
            return CommitDecision(
                False, "message_terminal", "助手消息已收敛，迟到结果被拒绝。"
            )
        if self._task_version_provider is not None:
            current = self._task_version_provider(self._account_id, self._conversation_id)
            if current != self._task_ref:
                return CommitDecision(
                    False, "task_version_changed", "任务版本已变化，迟到结果被拒绝。"
                )
        # 停止也只能由仍拥有租约且版本有效的执行者收敛。
        if run.stop_requested or (
            self._stop_event is not None and self._stop_event.is_set()
        ):
            return CommitDecision(False, "run_stopped", "用户已停止本轮生成。")
        return CommitDecision(True)
