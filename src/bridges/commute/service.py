"""通勤子图编排：七节点配方由持久节点内核执行（改进工单 10）。

模块不再把解析、定位、校验、路线、缓冲、核验与呈现包在一个黑盒函数
里：配方与节点定义在 :mod:`bridges.commute.kernel`，每个节点在自己的
事务中提交类型化产物与完成收据，恢复先读收据（地点成功而路线失败时
只重跑路线及其下游），方式变更复用定位并使路线/时间产物失效。本模块
只负责把内核结果翻译成既有消息投影，并复用既有终态收敛路径统一提交。

通勤不调用模型：正文完全来自高德返回的证据与确定性计算，不存在用
模型记忆补路线的路径（``run_model_id`` 仅保持调用签名一致）。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from bridges.commute.contracts import (
    MISSING_DESTINATION_CHOICE,
    MISSING_ORIGIN_CHOICE,
    MODE_LABELS,
    CommuteClarification,
    CommutePlace,
    CommutePlaceCandidate,
    CommuteRequestAnalysis,
    CommuteRouteProjection,
    CommuteRouteStatus,
)
from bridges.commute.kernel import (
    COMMUTE_GATE_HANDLERS,
    COMMUTE_NODE_LABELS,
    NODE_BUFFER,
    NODE_PARSE,
    NODE_PRESENT,
    NODE_REQUEST,
    NODE_RESOLVE,
    NODE_VALIDATE,
    NODE_VERIFY,
    ROUTE_DEADLINE_SECONDS,
    CommuteBudget,
    CommuteNodeFlow,
    build_commute_recipe,
    commute_recipe_registry,
)
from bridges.commute.parsing import pending_payload
from bridges.commute.presenting import status_content
from bridges.commute.sources import CommuteAmapPort
from bridges.contracts.chat import ChatMessageStatus
from bridges.contracts.modules import ModuleQueryRecord, ModuleWaitState
from bridges.contracts.workflows import RunContextEnvelope
from bridges.kernel.contracts import (
    KernelResult,
    KernelStatus,
    RecipeInputs,
)
from bridges.kernel.executor import NodeKernel
from bridges.kernel.guard import RunCommitGuard
from bridges.kernel.repository import NodeKernelRepository

if TYPE_CHECKING:
    from bridges.chat.repository import ConversationRepository

#: 澄清等待状态的类型标识（等待合同的一部分）。
WAIT_KIND_CLARIFICATION = "clarification"

COMMUTE_MODULE_ID = "commute"

#: 父图运行表的等待原因（可观测的持久化等待状态）。
WAIT_REASON_CLARIFICATION = "commute_clarification"


class CommuteModuleError(Exception):
    """通勤子图失败：节点位置、稳定错误码、可操作中文说明与是否可重试。"""

    def __init__(
        self, node: str, code: str, message: str, *, retryable: bool = True
    ) -> None:
        super().__init__(message)
        self.node = node
        self.code = code
        self.message = message
        self.retryable = retryable


class CommuteSupersededError(Exception):
    """迟到结果：租约/版本/消息归属已变化，本轮不写任何交付终态。"""


@dataclass(frozen=True)
class CommuteRunOutcome:
    """一轮通勤模块的收敛结果（父图据此写等待原因与判断终态）。"""

    status: CommuteRouteStatus
    wait_reason: str | None = None
    queries: list[ModuleQueryRecord] = field(default_factory=list)


class CommuteService:
    """校园通勤模块编排服务（由日常父图在显式派发时调用）。"""

    def __init__(
        self,
        *,
        amap: CommuteAmapPort,
        clock: Callable[[], datetime] | None = None,
        deadline_seconds: float = ROUTE_DEADLINE_SECONDS,
        task_version_provider: Callable[
            [str, str], tuple[str | None, int | None] | None
        ]
        | None = None,
    ) -> None:
        self._amap = amap
        self._clock = clock or (lambda: datetime.now(UTC))
        self._deadline_seconds = deadline_seconds
        self._task_version_provider = task_version_provider
        self._registry = commute_recipe_registry()
        self._recipe = build_commute_recipe()

    # -- 对外入口 --------------------------------------------------------

    def run(
        self,
        *,
        repo: ConversationRepository,
        account_id: str,
        conversation_id: str,
        user_message_id: str,
        assistant_message_id: str,
        run_context: RunContextEnvelope,
        run_model_id: str | None,
        emit_node: Callable[[str, str, int | None], None],
        stop_event: threading.Event | None,
    ) -> CommuteRunOutcome:
        """执行一轮通勤模块：持久节点内核执行，结果统一提交回同一消息。"""
        # 通勤不调用模型：本轮模型锁与本模块无关（签名保持一致）。
        del run_model_id
        user_message = repo.get_message(account_id, user_message_id)
        if user_message is None:
            raise CommuteModuleError(
                NODE_PARSE, "message_not_found", "消息不存在或没有访问权限。", retryable=False
            )
        pending = self._pending_wait(repo, account_id, conversation_id)
        prior = self._prior_user_messages(
            repo, account_id, conversation_id, user_message_id
        )
        run_id = run_context.run_id
        budget = self._load_budget(repo, account_id, run_id)
        task_ref = self._current_task_ref(account_id, conversation_id)
        guard = RunCommitGuard(
            repo,
            account_id=account_id,
            run_id=run_id,
            conversation_id=conversation_id,
            assistant_message_id=assistant_message_id,
            task_ref=task_ref,
            task_version_provider=self._task_version_provider,
            stop_event=stop_event,
            clock=self._clock,
        )
        flow = CommuteNodeFlow(
            amap=self._amap,
            clock=self._clock,
            prior_context=prior,
            pending_wait=pending,
            budget=budget,
            stop_event=stop_event,
            deadline_seconds=self._deadline_seconds,
        )
        kernel = NodeKernel(
            registry=self._registry,
            repository=NodeKernelRepository(repo.database),
            guard=guard,
            gates=COMMUTE_GATE_HANDLERS,
            runner=flow.run_node,
            clock=self._clock,
        )
        result = kernel.execute(
            recipe=self._recipe,
            inputs=RecipeInputs(
                account_id=account_id,
                conversation_id=conversation_id,
                run_id=run_id,
                user_message_id=user_message_id,
                user_content=user_message.content,
                task_id=task_ref[0] if task_ref is not None else None,
                task_version=task_ref[1] if task_ref is not None else None,
                wait_identity=self._wait_identity(pending),
                artifacts={},
                prior_digest=flow.prior_digest,
                buffer_snapshot=flow.buffer_snapshot,
            ),
            remaining_budget_ms=(
                budget.remaining_work_ms() if budget is not None else None
            ),
            event_sink=emit_node,
            stop_event=stop_event,
        )
        return self._deliver(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            result=result,
            stop_event=stop_event,
        )

    # -- 交付（既有终态收敛路径） ----------------------------------------

    def _deliver(
        self,
        repo: ConversationRepository,
        *,
        account_id: str,
        assistant_message_id: str,
        result: KernelResult,
        stop_event: threading.Event | None,
    ) -> CommuteRunOutcome:
        if result.status is KernelStatus.COMPLETED:
            return self._deliver_success(
                repo, account_id, assistant_message_id, result
            )
        if result.status is KernelStatus.NEEDS_INPUT:
            return self._deliver_clarification(
                repo, account_id, assistant_message_id, result
            )
        if result.status in {KernelStatus.STOPPED, KernelStatus.INVALIDATED} or (
            stop_event is not None and stop_event.is_set()
        ):
            # INVALIDATED 只出现在取消路径（停止或取消的外部调用）：按既有
            # 停止合同收敛，绝不当作可重试失败。
            return self._deliver_stopped(
                repo, account_id, assistant_message_id, result
            )
        if result.status is KernelStatus.REJECTED:
            raise CommuteSupersededError(
                result.rejection_code or "generation_superseded"
            )
        return self._deliver_failure(
            repo, account_id, assistant_message_id, result
        )

    def _deliver_success(
        self,
        repo: ConversationRepository,
        account_id: str,
        assistant_message_id: str,
        result: KernelResult,
    ) -> CommuteRunOutcome:
        artifact = result.delivery
        if artifact is None or artifact.node != NODE_PRESENT:
            raise CommuteModuleError(
                NODE_PRESENT,
                "commute_delivery_missing",
                "通勤结果没有形成待交付产物，本轮未提交。",
                retryable=True,
            )
        projection = CommuteRouteProjection.model_validate(
            artifact.payload["projection"]
        )
        now = datetime.now(UTC)
        self._finalize(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            status=ChatMessageStatus.DONE,
            projection=projection,
            content=status_content(projection),
            now=now,
        )
        return CommuteRunOutcome(
            status=projection.status, queries=list(projection.queries)
        )

    def _deliver_clarification(
        self,
        repo: ConversationRepository,
        account_id: str,
        assistant_message_id: str,
        result: KernelResult,
    ) -> CommuteRunOutcome:
        analysis = self._analysis(result)
        clarification, owner_payload = self._clarification(result)
        if clarification is None or analysis is None:
            raise CommuteModuleError(
                NODE_PARSE,
                "commute_clarification_missing",
                "通勤澄清状态缺少恢复载荷，本轮未提交。",
                retryable=True,
            )
        origin, destination = self._places(result)
        queries = self._queries(result)
        origin_candidates: Sequence[CommutePlaceCandidate] = (
            clarification.candidates
            if clarification.missing == MISSING_ORIGIN_CHOICE
            else ()
        )
        destination_candidates: Sequence[CommutePlaceCandidate] = (
            clarification.candidates
            if clarification.missing == MISSING_DESTINATION_CHOICE
            else ()
        )
        now = datetime.now(UTC)
        projection = CommuteRouteProjection(
            status=CommuteRouteStatus.CLARIFICATION,
            mode=analysis.mode,
            mode_label=MODE_LABELS[analysis.mode] if analysis.mode is not None else None,
            mode_phrase=analysis.mode_phrase,
            origin=origin,
            destination=destination,
            origin_candidates=list(origin_candidates),
            destination_candidates=list(destination_candidates),
            queries=queries,
            pending=ModuleWaitState(
                module_id=COMMUTE_MODULE_ID,
                kind=WAIT_KIND_CLARIFICATION,
                question=clarification.question,
                origin_message_id=assistant_message_id,
                context=pending_payload(
                    analysis,
                    awaiting=clarification.missing,
                    origin_candidates=origin_candidates,
                    destination_candidates=destination_candidates,
                    origin_candidate_query=_optional_str(
                        owner_payload.get("origin_candidate_query")
                    ),
                    destination_candidate_query=_optional_str(
                        owner_payload.get("destination_candidate_query")
                    ),
                ),
                created_at=now,
            ),
        )
        self._finalize(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            status=ChatMessageStatus.DONE,
            projection=projection,
            content=clarification.question,
            now=now,
        )
        return CommuteRunOutcome(
            status=CommuteRouteStatus.CLARIFICATION,
            wait_reason=WAIT_REASON_CLARIFICATION,
            queries=queries,
        )

    def _deliver_stopped(
        self,
        repo: ConversationRepository,
        account_id: str,
        assistant_message_id: str,
        result: KernelResult,
    ) -> CommuteRunOutcome:
        analysis = self._analysis(result)
        origin, destination = self._places(result)
        queries = self._queries(result)
        now = datetime.now(UTC)
        projection = CommuteRouteProjection(
            status=CommuteRouteStatus.STOPPED,
            mode=analysis.mode if analysis is not None else None,
            mode_label=(
                MODE_LABELS[analysis.mode]
                if analysis is not None and analysis.mode is not None
                else None
            ),
            mode_phrase=analysis.mode_phrase if analysis is not None else None,
            origin=origin,
            destination=destination,
            queries=queries,
            resolved_at=now,
        )
        self._finalize(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            status=ChatMessageStatus.STOPPED,
            projection=projection,
            content=status_content(projection),
            now=now,
        )
        return CommuteRunOutcome(status=CommuteRouteStatus.STOPPED, queries=queries)

    def _deliver_failure(
        self,
        repo: ConversationRepository,
        account_id: str,
        assistant_message_id: str,
        result: KernelResult,
    ) -> CommuteRunOutcome:
        analysis = self._analysis(result)
        origin, destination = self._places(result)
        queries = self._queries(result)
        failure = result.failure
        node = failure.node if failure is not None else NODE_VERIFY
        code = (failure.code if failure is not None else "") or "commute_failed"
        message = (
            failure.message if failure is not None and failure.message else ""
        ) or "校园通勤失败，请稍后重试。"
        retryable = failure.retryable if failure is not None else True
        now = datetime.now(UTC)
        projection = CommuteRouteProjection(
            status=CommuteRouteStatus.ERROR,
            mode=analysis.mode if analysis is not None else None,
            mode_label=(
                MODE_LABELS[analysis.mode]
                if analysis is not None and analysis.mode is not None
                else None
            ),
            mode_phrase=analysis.mode_phrase if analysis is not None else None,
            origin=origin,
            destination=destination,
            queries=queries,
            evidence_notes=self._query_notes(queries),
            resolved_at=now,
            error_code=code,
            error_message=message,
            retryable=retryable,
        )
        repo.update_message_commute_route(
            account_id, assistant_message_id, projection.model_dump(mode="json"), now
        )
        raise CommuteModuleError(node, code, message, retryable=retryable)

    # -- 内核结果读取 -----------------------------------------------------

    def _analysis(self, result: KernelResult) -> CommuteRequestAnalysis | None:
        artifact = result.artifact(NODE_PARSE)
        if artifact is None:
            return None
        analysis = artifact.payload.get("analysis")
        if not isinstance(analysis, dict):
            return None
        return CommuteRequestAnalysis.model_validate(analysis)

    def _places(
        self, result: KernelResult
    ) -> tuple[CommutePlace | None, CommutePlace | None]:
        for node in (NODE_VALIDATE, NODE_RESOLVE):
            artifact = result.artifact(node)
            if artifact is None:
                continue
            origin = artifact.payload.get("origin")
            destination = artifact.payload.get("destination")
            if origin is None and destination is None:
                continue
            return (
                CommutePlace.model_validate(origin) if origin else None,
                CommutePlace.model_validate(destination) if destination else None,
            )
        return None, None

    def _queries(self, result: KernelResult) -> list[ModuleQueryRecord]:
        queries: list[ModuleQueryRecord] = []
        resolve = result.artifact(NODE_RESOLVE)
        if resolve is not None:
            queries.extend(
                ModuleQueryRecord.model_validate(item)
                for item in resolve.payload.get("queries", [])
            )
        request = result.artifact(NODE_REQUEST)
        if request is not None and request.payload.get("record") is not None:
            queries.append(
                ModuleQueryRecord.model_validate(request.payload["record"])
            )
        return queries

    def _clarification(
        self, result: KernelResult
    ) -> tuple[CommuteClarification | None, dict[str, Any]]:
        parse = result.artifact(NODE_PARSE)
        if parse is not None:
            analysis = parse.payload.get("analysis") or {}
            raw = analysis.get("clarification")
            if isinstance(raw, dict):
                return CommuteClarification.model_validate(raw), {}
        for node in (NODE_RESOLVE, NODE_VALIDATE):
            artifact = result.artifact(node)
            if artifact is None:
                continue
            raw = artifact.payload.get("clarification")
            if isinstance(raw, dict):
                return (
                    CommuteClarification.model_validate(raw),
                    dict(artifact.payload),
                )
        return None, {}

    # -- 恢复与等待 ------------------------------------------------------

    def _pending_wait(
        self,
        repo: ConversationRepository,
        account_id: str,
        conversation_id: str,
    ) -> ModuleWaitState | None:
        """本会话最近一条通勤消息的等待状态（唯一权威来源）。

        只有最后一条带通勤投影的消息能代表当前等待：它若已经完成、未验证、
        失败或停止，上一轮的澄清问题就已被这轮结果取代——下一条消息按全新
        请求解析，不再从更早的历史里翻出旧等待。
        """
        for message in reversed(repo.list_messages(account_id, conversation_id)):
            projection = message.commute_route
            if not isinstance(projection, dict):
                continue
            try:
                stored = CommuteRouteProjection.model_validate(projection)
            except ValueError:
                continue
            return stored.pending
        return None

    def _prior_user_messages(
        self,
        repo: ConversationRepository,
        account_id: str,
        conversation_id: str,
        user_message_id: str,
    ) -> list[str]:
        """已确认的会话前文（最近若干条用户消息），只用于补齐缺失的地点。"""
        prior: list[str] = []
        for message in repo.list_messages(account_id, conversation_id):
            if message.message_id == user_message_id:
                break
            if message.role.value == "user" and message.content.strip():
                prior.append(message.content)
        return prior[-6:]

    def _wait_identity(self, pending: ModuleWaitState | None) -> str | None:
        if pending is None:
            return None
        return (
            f"{pending.module_id}:{pending.kind}:{pending.origin_message_id}:"
            f"{pending.created_at.isoformat()}"
        )

    def _current_task_ref(
        self, account_id: str, conversation_id: str
    ) -> tuple[str | None, int | None] | None:
        if self._task_version_provider is None:
            return None
        return self._task_version_provider(account_id, conversation_id)

    def _load_budget(
        self, repo: ConversationRepository, account_id: str, run_id: str
    ) -> CommuteBudget | None:
        """从持久账本加载共享预算（缺失行时按无预算模式保留本地截止）。"""
        # 局部导入：chat 包（预算账本属主）在模块级导入会与父图形成循环。
        from bridges.chat.run_budget_ledger import RunBudgetLedgerRepository

        ledger = RunBudgetLedgerRepository(repo.database)
        snapshot = ledger.load(account_id, run_id)
        if snapshot is None:
            return None
        ledger.recover_external_calls(account_id, run_id, now=datetime.now(UTC))
        work_deadline = snapshot.plan.deadline_at - timedelta(
            milliseconds=snapshot.plan.verify_deliver_reserve_ms
        )
        return CommuteBudget(
            ledger=ledger,
            account_id=account_id,
            run_id=run_id,
            work_deadline=work_deadline,
        )

    # -- 落库 ------------------------------------------------------------

    def _query_notes(self, queries: Sequence[ModuleQueryRecord]) -> list[str]:
        notes = [
            f"{record.source} 查询「{record.query}」：{record.status.value}"
            + (f"（{record.error_message}）" if record.error_message else "")
            for record in queries
        ]
        return notes or ["本轮没有发出可记录的外部查询。"]

    def _finalize(
        self,
        repo: ConversationRepository,
        *,
        account_id: str,
        assistant_message_id: str,
        status: ChatMessageStatus,
        projection: CommuteRouteProjection,
        content: str,
        now: datetime,
    ) -> None:
        # 正文只能在 streaming 期间写入（流式增量接口的守卫），因此先写正文
        # 再收敛终态；终态与通勤投影在同一事务内提交（finalize_message）。
        if content:
            repo.update_message_content(account_id, assistant_message_id, content, now)
        # 局部导入：模块子图与 chat 服务互相引用（父图调用子图、子图复用消息
        # 终态收敛），模块级导入会形成包级循环。
        from bridges.chat.turn import finalize_message

        finalize_message(
            repo,
            account_id,
            assistant_message_id,
            status=status,
            error_code=projection.error_code,
            error_message=projection.error_message,
            duration_ms=None,
            model_id=None,
            started=time.monotonic(),
            now=now,
            commute_route=projection.model_dump(mode="json"),
        )


def _optional_str(value: object) -> str | None:
    return str(value) if isinstance(value, str) else None


__all__ = [
    "COMMUTE_MODULE_ID",
    "COMMUTE_NODE_LABELS",
    "NODE_BUFFER",
    "NODE_PARSE",
    "NODE_PRESENT",
    "NODE_REQUEST",
    "NODE_RESOLVE",
    "NODE_VALIDATE",
    "NODE_VERIFY",
    "WAIT_KIND_CLARIFICATION",
    "WAIT_REASON_CLARIFICATION",
    "CommuteModuleError",
    "CommuteRunOutcome",
    "CommuteService",
    "CommuteSupersededError",
]
