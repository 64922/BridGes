"""生成终态 module：消息、运行与持久终态事件由同一次收尾协调。

本 module 把「这一轮结束了吗、结果是什么、三个对象是否已经一致」收敛成
一个 interface（:meth:`GenerationTerminal.converge`）：调用者只提供标识，
以及消息尚未落终态时的兜底结果；其余全部在 implementation 内部完成——
从已提交的消息派生结果（完成/失败/停止、错误码、中文原因、可重试性、
耗时）、按固定顺序补齐终态事件与运行状态、并让重复收尾成为可重放的
修复操作。

恢复也只有一个归属（Issue 03）：后台执行器失联、领取后重跑、读取路径
遇到残留 streaming 消息，都从同一组不变量出发，而不是各自再写一份
「消息状态 → 事件/运行/文案」的映射。

- :meth:`GenerationTerminal.recover_committed_result`：执行前判定——
  消息已提交终态的运行（或消息已随会话删除的运行）不重新调用模型，
  只补齐终态；消息仍在生成中才交给执行器执行；
- :meth:`GenerationTerminal.reap_lost_run`：执行器租约到期且恢复预算
  耗尽后的收尸（消息仍在生成中 → 可重试失败；已提交终态 → 只补齐）；
- :meth:`GenerationTerminal.reconcile_stale_message`：读取路径的残留
  streaming 兜底（跟随已终态的运行，或按迁移前的断流语义收敛）。

恢复的验证入口：``tests/chat/test_terminal_recovery_and_replay.py``
（故障矩阵、跨进程恢复、删除竞态、游标回放），既有合同用例见
``tests/chat/test_terminal_core.py``、``test_terminal_stop_and_graph_errors.py``
与 ``tests/chat/test_issue02_durable_generation.py``。

不变量
------
1. **消息是终态的唯一真相源。** 结果以已提交的消息记录为准：运行表不携带
   内容，终态事件只是消息投影的快照，因此绝不从运行或事件反向推导结果；
   已终态的消息不会被改写（含并发收尾中先提交者的结果）。唯一例外是
   迁移前的半写数据（运行已终态而消息仍 streaming，正常提交顺序下不可能
   产生）——此时运行是唯一可用的事实，读取路径按运行终态修复该消息。
2. **提交顺序固定：消息 → 终态事件 → 运行。** 事件先于运行，因为订阅端点
   以运行终态判断结束——运行先终态会让最后一次回放读不到终态事件；运行
   放在最后，因为它是三者中唯一可由消息重算的对象。唯一例外是消息已被
   删除的竞态：没有消息就没有可投影的终态，此时只收敛运行、不补发事件
   （该运行没有订阅者会等待它的终态事件）。
3. **每一步都有持久守卫，因此部分提交可恢复。** 消息仅 streaming→终态、
   终态事件每个运行至多一条（判重与插入在同一事务内，并发收尾最多一条
   生效）、运行仅 queued/running→终态。任一步失败留下的残留（消息已终态
   而事件或运行缺失）都是可修复形态：重放同一收尾操作会补齐缺失部分，
   不改写已提交结果。调用者因此无需猜测顺序，也不需要把三个对象放进
   同一个事务。
4. **提交故障后不得出现互相矛盾的终态。** 失败只会留下「尚未补齐」的
   形态（未完成的运行），不会留下与已提交消息相冲突的终态事件或运行状态。
5. 只决定执行结果，不评估产物可信状态；不接管领取、续租、模型生成与事件
   订阅（调度仍属执行器）；不改写模型运行锁与历史消息（运行锁随消息终态
   在同一事务内提交，Issue 10）。
6. **停止不是错误。** 停止终态的消息不携带错误码与错误原因，但终态事件
   沿用既有 error 传输种类与稳定码 ``stopped``（合同不变，不为名称整齐
   新增前端状态）；停止同时把已触发的公网/arXiv 搜索与教学卡片收敛为
   取消态（内部策略，从消息当前投影派生），避免卡片残留 loading。竞争
   裁决同样靠持久守卫：已获准提交的终态（先到者）不被迟到完成、迟到
   异常或重复停止改写。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from bridges.chat.turn import (
    CHAT_MODE,
    cancelled_arxiv_search,
    cancelled_web_search,
    done_thinking,
    error_is_retryable,
    failed_thinking,
    finalize_message,
    initial_thinking,
    stopped_teaching_projection,
    stopped_thinking,
    user_facing_error,
)
from bridges.contracts.chat import (
    ChatMessageProjection,
    ChatMessageStatus,
    ChatMode,
    ChatRunStatus,
    ChatStreamDoneData,
    ChatStreamErrorData,
    ChatStreamErrorDetail,
    ChatStreamEventKind,
    ChatThinkingSummary,
)

if TYPE_CHECKING:
    from bridges.chat.repository import (
        ConversationRepository,
        GenerationRunRecord,
        MessageRecord,
    )

#: 残留 streaming 消息或缺错误码时的兜底错误码（与现行内部错误合同一致）。
INTERNAL_ERROR_CODE = "internal_error"
#: 停止收敛的稳定码与中文原因（停止事件沿用既有 error 传输种类与文案）。
STOPPED_CODE = "stopped"
STOPPED_MESSAGE = "生成已停止。"
#: 执行器失联收尸的稳定码（中文原因与可重试性走统一映射，不在此重复文案）。
WORKER_LOST_CODE = "generation_worker_lost"
#: 迁移前遗留（无运行记录）的断流收敛稳定码。
INTERRUPTED_CODE = "stream_interrupted"


class TerminalConvergeError(RuntimeError):
    """收尾请求缺少必要输入（消息仍处生成中但没有兜底结果）。"""


@dataclass(frozen=True)
class TerminalOutcome:
    """一次收尾要表达的结果（不含提交顺序与恢复知识）。"""

    status: ChatMessageStatus
    error_code: str | None = None
    error_message: str | None = None
    duration_ms: int | None = None

    @classmethod
    def from_message(cls, message: MessageRecord) -> TerminalOutcome:
        """从已提交的消息记录派生结果（消息是唯一真相源）。"""
        if message.status == ChatMessageStatus.DONE:
            return cls(status=message.status, duration_ms=message.duration_ms)
        if message.status == ChatMessageStatus.STOPPED:
            return cls(
                status=message.status,
                error_code=STOPPED_CODE,
                error_message=STOPPED_MESSAGE,
                duration_ms=message.duration_ms,
            )
        code = message.error_code or INTERNAL_ERROR_CODE
        return cls(
            status=ChatMessageStatus.ERROR,
            error_code=code,
            error_message=message.error_message or user_facing_error(code),
            duration_ms=message.duration_ms,
        )

    @classmethod
    def from_run(
        cls,
        run: GenerationRunRecord,
        *,
        elapsed_ms: int | None = None,
    ) -> TerminalOutcome:
        """从已提交的运行终态派生消息应当具有的终态（迁移前半写的修复方向）。

        正常提交顺序是「消息 → 事件 → 运行」，因此「运行已终态而消息仍
        streaming」只可能来自迁移前的半写：此时运行记录是唯一可用的事实。
        停止与失败沿用运行的原因；运行 ``done`` 而消息从未提交结果时按内部
        错误收敛——没有落库结果的消息不得因运行表而显示成成功。耗时优先
        采用运行已记录的墙钟，缺失时回退 ``elapsed_ms``。
        """
        duration_ms = run.duration_ms if run.duration_ms is not None else elapsed_ms
        if run.status == ChatRunStatus.STOPPED.value:
            return stopped_outcome(duration_ms)
        if run.status == ChatRunStatus.DONE.value:
            return TerminalOutcome(
                status=ChatMessageStatus.ERROR,
                error_code=INTERNAL_ERROR_CODE,
                error_message=user_facing_error(INTERNAL_ERROR_CODE),
                duration_ms=duration_ms,
            )
        code = run.error_code or INTERNAL_ERROR_CODE
        return TerminalOutcome(
            status=ChatMessageStatus.ERROR,
            error_code=code,
            error_message=run.error_message or user_facing_error(code),
            duration_ms=duration_ms,
        )

    @property
    def event_kind(self) -> str:
        """终态事件的传输种类：完成用 done，失败与停止沿用既有 error 种类。"""
        if self.status == ChatMessageStatus.DONE:
            return ChatStreamEventKind.DONE.value
        return ChatStreamEventKind.ERROR.value

    @property
    def run_status(self) -> str:
        """运行记录的终态：与消息终态一一对应。"""
        if self.status == ChatMessageStatus.DONE:
            return ChatRunStatus.DONE.value
        if self.status == ChatMessageStatus.STOPPED:
            return ChatRunStatus.STOPPED.value
        return ChatRunStatus.FAILED.value

    @property
    def retryable(self) -> bool:
        """可重试性与现行合同一致（停止按可重试呈现，沿用既有事件语义）。"""
        if self.status == ChatMessageStatus.STOPPED:
            return True
        return error_is_retryable(self.error_code)

    def run_error(self) -> tuple[str | None, str | None]:
        """运行记录的错误字段：只有失败运行记录错误码与原因。

        完成与停止的运行不携带错误码（与既有运行表语义一致，停止不是异常）。
        """
        if self.status == ChatMessageStatus.ERROR:
            return self.error_code, self.error_message
        return None, None


def internal_error_outcome() -> TerminalOutcome:
    """残留 streaming 消息的兜底结果（稳定码 + 合同中文原因）。"""
    return TerminalOutcome(
        status=ChatMessageStatus.ERROR,
        error_code=INTERNAL_ERROR_CODE,
        error_message=user_facing_error(INTERNAL_ERROR_CODE),
    )


def stopped_outcome(duration_ms: int | None = None) -> TerminalOutcome:
    """用户停止的兜底结果（稳定码与中文原因同停止事件合同）。

    ``duration_ms`` 供调用者已知耗时口径时随终态写入（如停止接口按
    单调起点或创建时间估算）；排队期/图边界停止由各入口按现行口径传值。
    """
    return TerminalOutcome(
        status=ChatMessageStatus.STOPPED,
        error_code=STOPPED_CODE,
        error_message=STOPPED_MESSAGE,
        duration_ms=duration_ms,
    )


def worker_lost_outcome() -> TerminalOutcome:
    """执行器失联收尸的兜底结果：可重试失败，保留已接收的正文。

    中文原因与可重试性不在此重复：错误码经 ``user_facing_error`` 与
    ``_RETRYABLE_CODES`` 派生出合同文案（「生成进程意外退出，已保留
    已接收内容，可点击重试。」）与可重试标记。
    """
    return TerminalOutcome(
        status=ChatMessageStatus.ERROR,
        error_code=WORKER_LOST_CODE,
        error_message=user_facing_error(WORKER_LOST_CODE),
    )


def interrupted_outcome(duration_ms: int | None = None) -> TerminalOutcome:
    """迁移前遗留（无运行记录）残留 streaming 的兜底结果：断流可重试失败。"""
    return TerminalOutcome(
        status=ChatMessageStatus.ERROR,
        error_code=INTERRUPTED_CODE,
        error_message=user_facing_error(INTERRUPTED_CODE),
        duration_ms=duration_ms,
    )


@dataclass(frozen=True)
class TerminalCommit:
    """收尾回执：本次调用相对重复调用真正写入了什么。"""

    outcome: TerminalOutcome
    #: 本次是否写入了消息终态（并发收尾由持久守卫裁决，后到者为假）。
    message_committed: bool
    #: 本次追加的终态事件 seq；已有终态事件时为 None（不重复追加）。
    event_seq: int | None
    #: 本次是否写入了运行终态。
    run_committed: bool

    @property
    def replayed(self) -> bool:
        """本次是否什么都没写：结果已由先前提交确定（幂等重放）。"""
        return (
            not self.message_committed
            and self.event_seq is None
            and not self.run_committed
        )


class GenerationTerminal:
    """生成终态 module：一次收尾确定消息、运行与终态事件的一致性。"""

    def __init__(
        self,
        repo: ConversationRepository,
        *,
        projection: Callable[[str, str], ChatMessageProjection | None],
    ) -> None:
        """``projection`` 是终态事件的载荷来源（消息投影读取面，按账户隔离）。"""
        self._repo = repo
        self._projection = projection

    # ------------------------------------------------------------------
    # 收尾
    # ------------------------------------------------------------------

    def converge(
        self,
        account_id: str,
        run_id: str | None,
        message_id: str,
        *,
        fallback: TerminalOutcome | None = None,
        run_duration_ms: int | None = None,
    ) -> TerminalCommit:
        """把运行收尾到与已提交消息一致的结果（可重复执行）。

        - 消息已终态：结果从消息派生，``fallback`` 忽略；
        - 消息仍 streaming：按 ``fallback`` 写入终态（残留兜底路径）；
        - 消息已不存在（会话/消息删除竞态）：只收敛运行，不补发事件；
        - ``run_id`` 为 None（消息没有关联运行，如直接编排的生成被停止）：
          只收敛消息，不补发终态事件、不收敛运行；
        - 已有终态事件：不再追加，订阅端回放保持单调。

        ``run_duration_ms`` 是执行器测量的本轮墙钟耗时（运行记录口径），
        与消息自身的耗时字段无关。
        """
        message = self._repo.get_message(account_id, message_id)
        message_committed = False
        if message is not None and message.status == ChatMessageStatus.STREAMING:
            if fallback is None:
                raise TerminalConvergeError(
                    "消息仍处生成中：收尾需要调用者给出兜底结果。"
                )
            message_committed = self._commit_message(message, fallback) > 0
            # 以提交后的记录为准：并发收尾由持久守卫裁决，后到者沿用先提交结果。
            message = self._repo.get_message(account_id, message_id)
        if run_id is None:
            if message is None:
                outcome = fallback or internal_error_outcome()
            else:
                outcome = TerminalOutcome.from_message(message)
            return TerminalCommit(
                outcome=outcome,
                message_committed=message_committed,
                event_seq=None,
                run_committed=False,
            )
        if message is None:
            outcome = fallback or internal_error_outcome()
            return TerminalCommit(
                outcome=outcome,
                message_committed=message_committed,
                event_seq=None,
                run_committed=self._finalize_run(
                    account_id, run_id, outcome, run_duration_ms
                ),
            )
        outcome = TerminalOutcome.from_message(message)
        event_seq = self._append_terminal_event(account_id, run_id, message_id, outcome)
        run_committed = self._finalize_run(account_id, run_id, outcome, run_duration_ms)
        return TerminalCommit(
            outcome=outcome,
            message_committed=message_committed,
            event_seq=event_seq,
            run_committed=run_committed,
        )

    # ------------------------------------------------------------------
    # 恢复（执行器失联、领取后重跑、读取路径残留兜底）
    # ------------------------------------------------------------------

    def recover_committed_result(
        self, account_id: str, run: GenerationRunRecord
    ) -> TerminalCommit | None:
        """执行前判定：结果已确定的运行只补齐终态，绝不重新调用模型。

        返回 ``None`` 表示消息仍在生成中——结果尚未确定，必须交给执行器
        执行。已确定的两种情况：

        - 消息已提交终态（上一次执行在事件/运行/队列确认附近中断）：结果
          由消息派生，补齐缺失的终态事件与运行终态；
        - 消息已随会话/消息删除（删除竞态下的迟到运行）：没有可投影的
          结果，只收敛运行、不补发事件，也不复活任何数据。

        恢复动作本身可重复执行：已补齐的运行只重放不写入。
        """
        message = self._repo.get_message(account_id, run.assistant_message_id)
        if message is not None and message.status == ChatMessageStatus.STREAMING:
            return None
        # 消息已终态：结果从消息派生；消息已删除：converge 的缺消息分支
        # 用兜底结果收敛运行且不补发事件。
        return self.converge(account_id, run.run_id, run.assistant_message_id)

    def reap_lost_run(self, run: GenerationRunRecord) -> TerminalCommit:
        """执行器失联收尸：租约到期且恢复预算耗尽后的收敛。

        消息仍在生成中 → 按可重试失败（``generation_worker_lost``）收敛
        并保留部分正文；消息已有终态 → 只补齐缺失的事件与运行（收尸绝不
        追加与已提交结果矛盾的终态事件）。
        """
        return self.converge(
            run.account_id,
            run.run_id,
            run.assistant_message_id,
            fallback=worker_lost_outcome(),
        )

    def reconcile_stale_message(
        self, account_id: str, message_id: str
    ) -> TerminalCommit:
        """读取路径的陈旧收敛：残留 streaming 消息按已提交事实收敛。

        调用方只需报告「这条消息没有活跃运行」，方向判定留在本 module：

        - 存在已终态运行（迁移前的半写）：跟随运行终态修复该消息；
        - 没有运行记录（迁移前遗留数据）：按断流收敛为可重试失败。

        两条路径都连带补齐缺失的终态事件与运行终态：读取修复完成后订阅
        端不会再悬挂，而不是只把消息改个状态了事。已终态的消息不进入本
        分支（调用方只对 streaming 消息调用），调用本身幂等。
        """
        message = self._repo.get_message(account_id, message_id)
        run = self._repo.get_run_by_message(account_id, message_id)
        if run is None:
            return self.converge(
                account_id,
                None,
                message_id,
                fallback=interrupted_outcome(self._elapsed_ms(message)),
            )
        return self.converge(
            account_id,
            run.run_id,
            message_id,
            fallback=TerminalOutcome.from_run(run, elapsed_ms=self._elapsed_ms(message)),
        )

    @staticmethod
    def _elapsed_ms(message: MessageRecord | None) -> int | None:
        """消息创建至今的墙钟毫秒（运行未记录耗时时的回退口径）。"""
        if message is None:
            return None
        elapsed = (datetime.now(UTC) - message.created_at).total_seconds()
        return max(1, int(elapsed * 1000))

    # ------------------------------------------------------------------
    # 终态事件载荷（运行期实时事件与收尾补齐共用同一形状）
    # ------------------------------------------------------------------

    def done_payload(self, account_id: str, message_id: str) -> dict[str, Any]:
        """done 事件载荷：完整消息投影（权威终态）。"""
        return ChatStreamDoneData(
            message_id=message_id,
            message=self._projection(account_id, message_id),
        ).model_dump(mode="json")

    def error_payload(
        self,
        account_id: str,
        message_id: str,
        *,
        code: str,
        message: str,
        retryable: bool,
    ) -> dict[str, Any]:
        """error 事件载荷：稳定码 + 中文原因 + 可重试性，并保留已完成投影。"""
        final = self._projection(account_id, message_id)
        return ChatStreamErrorData(
            message_id=message_id,
            error=ChatStreamErrorDetail(code=code, message=message, retryable=retryable),
            thinking=final.thinking if final is not None else None,
            duration_ms=final.duration_ms if final is not None else None,
            web_search=final.web_search if final is not None else None,
            arxiv_search=final.arxiv_search if final is not None else None,
            teaching=final.teaching if final is not None else None,
        ).model_dump(mode="json")

    # ------------------------------------------------------------------
    # 分步提交（顺序与守卫即本 module 的规则，不对外暴露）
    # ------------------------------------------------------------------

    def _commit_message(self, message: MessageRecord, outcome: TerminalOutcome) -> int:
        """按兜底结果写入消息终态；思考摘要按已记录摘要与结果派生。

        摘要基底取消息已记录的思考摘要（缺省时按对话模式的编排步骤初始
        化），只替换质量结论——恢复与陈旧收敛因此不会丢弃已累积的步骤与
        证据，各入口（执行器收尾、失联收尸、读取路径兜底）共享同一规则。

        停止终态不写错误码与错误原因（停止不是错误），并把已触发的搜索
        与教学卡片收敛为取消态；取消投影从消息当前记录派生，各停止入口
        （图边界、排队期、停止接口兜底）因此共享同一投影规则。

        返回影响行数：0 表示并发收尾已先提交（不覆盖已提交结果）。
        """
        thinking = (
            ChatThinkingSummary(**message.thinking)
            if message.thinking is not None
            else initial_thinking(self._mode_of(message))
        )
        web_search = None
        arxiv_search = None
        teaching = None
        if outcome.status == ChatMessageStatus.ERROR:
            thinking = failed_thinking(
                thinking, outcome.error_code or INTERNAL_ERROR_CODE
            )
        elif outcome.status == ChatMessageStatus.STOPPED:
            thinking = stopped_thinking(thinking)
            now = datetime.now(UTC)
            web_search = cancelled_web_search(message.web_search, now)
            arxiv_search = cancelled_arxiv_search(message.arxiv_search, now)
            stopped_teaching = stopped_teaching_projection(message.teaching)
            teaching = (
                stopped_teaching.model_dump(mode="json")
                if stopped_teaching is not None
                else None
            )
        else:
            thinking = done_thinking(thinking)
        return finalize_message(
            self._repo,
            message.account_id,
            message.message_id,
            status=outcome.status,
            error_code=(
                outcome.error_code if outcome.status == ChatMessageStatus.ERROR else None
            ),
            error_message=(
                outcome.error_message
                if outcome.status == ChatMessageStatus.ERROR
                else None
            ),
            duration_ms=outcome.duration_ms,
            # 已记录的运行锁与模型标识随消息保留：恢复不破坏在途/已提交的
            # 模型锁（streaming 消息通常两者皆空，迁移前的半写才非空）。
            model_id=message.model_id,
            run_lock_id=message.run_lock_id,
            started=time.monotonic(),
            now=datetime.now(UTC),
            thinking=thinking,
            web_search=web_search,
            arxiv_search=arxiv_search,
            teaching=teaching,
        )

    def _append_terminal_event(
        self,
        account_id: str,
        run_id: str,
        message_id: str,
        outcome: TerminalOutcome,
    ) -> int | None:
        """补齐终态事件（每个运行至多一条）；运行不存在或已有终态时返回 None。

        中文原因只有一条派生规则：映射表文案优先，收尾结果携带的原因作
        为未映射码的回退（与运行期实时事件同一规则）。终态事件的判重与
        插入由仓库在同一事务内完成（``append_generation_event``），因此
        运行期实时通道与这里的补齐、并发收尾与迟到收尾共享同一守卫。
        """
        if outcome.status == ChatMessageStatus.DONE:
            payload = self.done_payload(account_id, message_id)
        else:
            code = outcome.error_code or INTERNAL_ERROR_CODE
            if outcome.status == ChatMessageStatus.STOPPED:
                code, message = STOPPED_CODE, STOPPED_MESSAGE
            else:
                message = user_facing_error(code, outcome.error_message)
            payload = self.error_payload(
                account_id,
                message_id,
                code=code,
                message=message,
                retryable=outcome.retryable,
            )
        seq = self._repo.append_generation_event(
            account_id, run_id, outcome.event_kind, payload, datetime.now(UTC)
        )
        return seq or None

    def _finalize_run(
        self,
        account_id: str,
        run_id: str,
        outcome: TerminalOutcome,
        run_duration_ms: int | None,
    ) -> bool:
        """收敛运行终态（仅 queued/running → 目标状态）；返回是否本次写入。"""
        error_code, error_message = outcome.run_error()
        return (
            self._repo.finalize_generation_run(
                account_id,
                run_id,
                status=outcome.run_status,
                error_code=error_code,
                error_message=error_message,
                duration_ms=run_duration_ms,
                now=datetime.now(UTC),
            )
            > 0
        )

    def _mode_of(self, message: MessageRecord) -> ChatMode:
        """消息所属对话的当前模式（思考摘要按模式派生；缺对话用默认模式）。"""
        conversation = self._repo.get_conversation(message.account_id, message.conversation_id)
        return ChatMode(conversation.mode) if conversation is not None else CHAT_MODE
