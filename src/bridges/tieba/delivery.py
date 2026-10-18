"""贴吧研究结果的终态交付、投影与消息持久化。"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from bridges.contracts.chat import ChatMessageStatus
from bridges.contracts.modules import ModuleQueryRecord, ModuleWaitState
from bridges.kernel.contracts import (
    KernelResult,
    KernelStatus,
)
from bridges.tieba.contracts import (
    TiebaQuestionAnalysis,
    TiebaResearchProjection,
    TiebaResearchStatus,
)
from bridges.tieba.evidence_projection import topic_of
from bridges.tieba.evidence_search import FORUM_NAME
from bridges.tieba.evidence_time import (
    time_filter_state as _time_filter_state,
)
from bridges.tieba.parsing import pending_payload
from bridges.tieba.presenting import (
    render_clarification_content,
    render_empty_content,
    render_result_content,
    render_stopped_content,
    render_waiting_auth_content,
)
from bridges.tieba.quality_gates import verified_projection
from bridges.tieba.recipe import (
    NODE_PARSE,
    NODE_SEARCH,
    NODE_SYNTHESIZE,
    NODE_VERIFY,
)
from bridges.tieba.searching import plan_query_rounds
from bridges.tieba.service_contract import (
    TIEBA_MODULE_ID,
    TIEBA_TOTAL_BUDGET_MS,
    WAIT_KIND_AUTH,
    WAIT_KIND_CLARIFICATION,
    WAIT_REASON_AUTH,
    WAIT_REASON_CLARIFICATION,
    TiebaModuleError,
    TiebaRunOutcome,
    is_auth_wait_error,
)

if TYPE_CHECKING:
    from bridges.chat.repository import ConversationRepository


class TiebaResultDelivery:
    """把内核终态转换为经过核验的用户结果并原子持久化。"""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))

    def deliver(
        self,
        repo: ConversationRepository,
        *,
        account_id: str,
        assistant_message_id: str,
        result: KernelResult,
        stop_event: threading.Event | None,
        remaining_budget_ms: int | None,
    ) -> TiebaRunOutcome:
        if result.status is KernelStatus.COMPLETED:
            return self._deliver_completed(
                repo,
                account_id,
                assistant_message_id,
                result,
                remaining_budget_ms=remaining_budget_ms,
            )
        if result.status is KernelStatus.NEEDS_INPUT:
            return self._deliver_clarification(repo, account_id, assistant_message_id, result)
        if result.status in {KernelStatus.STOPPED, KernelStatus.INVALIDATED} or (
            stop_event is not None and stop_event.is_set()
        ):
            return self._deliver_stopped(repo, account_id, assistant_message_id, result)
        if result.status is KernelStatus.REJECTED:
            raise TiebaModuleError(
                result.stopped_at or NODE_PARSE,
                result.rejection_code or "generation_superseded",
                "本轮结果在提交前已失效（运行已转交或任务已变更），未写入。",
                retryable=True,
            )
        return self._deliver_failure(repo, account_id, assistant_message_id, result)

    def _deliver_completed(
        self,
        repo: ConversationRepository,
        account_id: str,
        assistant_message_id: str,
        result: KernelResult,
        *,
        remaining_budget_ms: int | None,
    ) -> TiebaRunOutcome:
        projection = verified_projection(result)
        if projection is None:
            raise TiebaModuleError(
                NODE_VERIFY,
                "tieba_delivery_missing",
                "贴吧模块缺少已核验的结果产物，本轮未提交。",
                retryable=True,
            )
        needs_auth = (
            any(is_auth_wait_error(record.error_code) for record in projection.queries)
            or any(is_auth_wait_error(post.read_error_code) for post in projection.confirmed_posts)
            or any(is_auth_wait_error(link.read_error_code) for link in projection.candidate_links)
        )
        if needs_auth:
            analysis = _analysis_of(result)
            if analysis is not None:
                return self._deliver_auth_wait(
                    repo,
                    account_id,
                    assistant_message_id,
                    projection,
                    analysis,
                    remaining_budget_ms=remaining_budget_ms,
                )
        if projection.status is TiebaResearchStatus.ERROR:
            self._finalize(
                repo,
                account_id=account_id,
                assistant_message_id=assistant_message_id,
                status=ChatMessageStatus.ERROR,
                projection=projection,
                content=render_empty_content(projection),
            )
            raise TiebaModuleError(
                NODE_SEARCH,
                projection.error_code or "tieba_search_failed",
                projection.error_message or "贴吧检索失败，请稍后重试。",
                retryable=projection.retryable,
            )
        self._finalize(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            status=ChatMessageStatus.DONE,
            projection=projection,
            content=(
                render_result_content(projection)
                if projection.confirmed_posts
                else render_empty_content(projection)
            ),
        )
        return TiebaRunOutcome(
            status=projection.status,
            wait_reason=None,
            queries=list(projection.queries),
        )

    def _deliver_auth_wait(
        self,
        repo: ConversationRepository,
        account_id: str,
        assistant_message_id: str,
        projection: TiebaResearchProjection,
        analysis: TiebaQuestionAnalysis,
        *,
        remaining_budget_ms: int | None,
    ) -> TiebaRunOutcome:
        now = self._clock()
        context = pending_payload(analysis)
        context["remaining_work_ms"] = max(
            0,
            min(
                TIEBA_TOTAL_BUDGET_MS,
                TIEBA_TOTAL_BUDGET_MS if remaining_budget_ms is None else remaining_budget_ms,
            ),
        )
        pending = ModuleWaitState(
            module_id=TIEBA_MODULE_ID,
            kind=WAIT_KIND_AUTH,
            question="已打开当前账户专用的百度贴吧登录或安全验证页面。完成后在聊天中说“继续”，我会接着原话题检索。",
            origin_message_id=assistant_message_id,
            context=context,
            created_at=now,
        )
        waiting = projection.model_copy(
            update={
                "status": TiebaResearchStatus.WAITING_AUTH,
                "pending": pending,
                "completed_at": now,
                "error_code": None,
                "error_message": None,
                "retryable": False,
            }
        )
        self._finalize(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            status=ChatMessageStatus.DONE,
            projection=waiting,
            content=render_waiting_auth_content(waiting),
        )
        return TiebaRunOutcome(
            status=TiebaResearchStatus.WAITING_AUTH,
            wait_reason=WAIT_REASON_AUTH,
            queries=list(waiting.queries),
        )

    def _deliver_clarification(
        self,
        repo: ConversationRepository,
        account_id: str,
        assistant_message_id: str,
        result: KernelResult,
    ) -> TiebaRunOutcome:
        analysis = _analysis_of(result)
        if analysis is None or analysis.clarification is None:
            return self._deliver_failure(repo, account_id, assistant_message_id, result)
        now = self._clock()
        projection = _base_projection(
            analysis,
            status=TiebaResearchStatus.CLARIFICATION,
            pending=ModuleWaitState(
                module_id=TIEBA_MODULE_ID,
                kind=WAIT_KIND_CLARIFICATION,
                question=analysis.clarification.question,
                origin_message_id=assistant_message_id,
                context=pending_payload(analysis),
                created_at=now,
            ),
            completed_at=now,
        )
        self._finalize(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            status=ChatMessageStatus.DONE,
            projection=projection,
            content=render_clarification_content(analysis),
        )
        return TiebaRunOutcome(
            status=projection.status,
            wait_reason=WAIT_REASON_CLARIFICATION,
            queries=[],
        )

    def _deliver_stopped(
        self,
        repo: ConversationRepository,
        account_id: str,
        assistant_message_id: str,
        result: KernelResult,
    ) -> TiebaRunOutcome:
        analysis = _analysis_of(result)
        if analysis is None:
            analysis = _empty_analysis()
        projection = _base_projection(
            analysis,
            status=TiebaResearchStatus.STOPPED,
            queries=_records_of(result),
            confirmed=_confirmed_of(result),
            evidence_boundary=["你已停止本轮搜集，未继续读取页面。"],
            completed_at=self._clock(),
        )
        self._finalize(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            status=ChatMessageStatus.STOPPED,
            projection=projection,
            content=render_stopped_content(projection),
        )
        return TiebaRunOutcome(
            status=TiebaResearchStatus.STOPPED,
            wait_reason=None,
            queries=list(projection.queries),
        )

    def _deliver_failure(
        self,
        repo: ConversationRepository,
        account_id: str,
        assistant_message_id: str,
        result: KernelResult,
    ) -> TiebaRunOutcome:
        analysis = _analysis_of(result) or _empty_analysis()
        projection = _projection_of(result)
        if projection is None:
            failure = result.failure
            projection = _base_projection(
                analysis,
                status=TiebaResearchStatus.ERROR,
                queries=_records_of(result),
                completed_at=self._clock(),
                error_code=(failure.code if failure else "") or "tieba_failed",
                error_message=(failure.message if failure else "")
                or "贴吧信息搜集失败，请稍后重试。",
                retryable=failure.retryable if failure else True,
            )
        self._finalize(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            status=ChatMessageStatus.ERROR,
            projection=projection,
            content=render_empty_content(projection),
        )
        raise TiebaModuleError(
            (result.failure.node if result.failure else NODE_SEARCH),
            projection.error_code or "tieba_failed",
            projection.error_message or "贴吧信息搜集失败，请稍后重试。",
            retryable=projection.retryable,
        )

    def _finalize(
        self,
        repo: ConversationRepository,
        *,
        account_id: str,
        assistant_message_id: str,
        status: ChatMessageStatus,
        projection: TiebaResearchProjection,
        content: str,
    ) -> None:
        now = self._clock()
        projection = projection.model_copy(update={"completed_at": projection.completed_at or now})
        # 局部导入：模块子图与 chat 服务互相引用（父图调用子图、子图复用消息
        # 终态收敛），模块级导入会形成包级循环。``finalize_message`` 会并入
        # 外层提交守卫事务（存在时），因此这里的终态与产物同一事务提交。
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
            run_lock_id=None,
            lock=None,
            started=time.monotonic(),
            now=now,
            final_content=content or None,
            tieba_research=projection.model_dump(mode="json"),
        )


def _delivery_artifact(result: KernelResult) -> Any:
    return result.artifact(NODE_VERIFY) or result.artifact(NODE_SYNTHESIZE)


def _projection_of(result: KernelResult) -> TiebaResearchProjection | None:
    artifact = _delivery_artifact(result)
    if artifact is None:
        return None
    raw = artifact.payload.get("projection")
    if not isinstance(raw, dict):
        return None
    try:
        return TiebaResearchProjection.model_validate(raw)
    except ValueError:
        return None


def _analysis_of(result: KernelResult) -> TiebaQuestionAnalysis | None:
    artifact = result.artifact(NODE_PARSE)
    if artifact is None:
        return None
    raw = artifact.payload.get("analysis")
    if not isinstance(raw, dict):
        return None
    try:
        return TiebaQuestionAnalysis.model_validate(raw)
    except ValueError:
        return None


def _records_of(result: KernelResult) -> list[ModuleQueryRecord]:
    artifact = result.artifact(NODE_SEARCH)
    if artifact is None:
        return []
    records: list[ModuleQueryRecord] = []
    for raw in artifact.payload.get("records", []):
        try:
            records.append(ModuleQueryRecord.model_validate(raw))
        except ValueError:
            continue
    return records


def _confirmed_of(result: KernelResult) -> list[Any]:
    from bridges.tieba.contracts import TiebaPostProjection

    artifact = result.artifact("tieba.read")
    if artifact is None:
        return []
    posts: list[TiebaPostProjection] = []
    for raw in artifact.payload.get("confirmed", []):
        try:
            posts.append(TiebaPostProjection.model_validate(raw))
        except ValueError:
            continue
    return posts


def _base_projection(
    analysis: TiebaQuestionAnalysis,
    *,
    status: TiebaResearchStatus,
    queries: Sequence[ModuleQueryRecord] = (),
    confirmed: Sequence[Any] = (),
    evidence_boundary: Sequence[str] = (),
    pending: ModuleWaitState | None = None,
    completed_at: datetime | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    retryable: bool = False,
) -> TiebaResearchProjection:
    return TiebaResearchProjection(
        status=status,
        topic=topic_of(analysis) or FORUM_NAME,
        original_question=analysis.original_question,
        topic_terms=list(analysis.topic_terms),
        place_or_event=list(analysis.place_or_event),
        question_kind=analysis.question_kind,
        campus_terms=list(analysis.campus_terms),
        query_rounds=list(plan_query_rounds(analysis)),
        time_filter=_time_filter_state(analysis, list(confirmed)),
        queries=list(queries),
        confirmed_posts=list(confirmed),
        official_check_requested=analysis.needs_official_check,
        evidence_boundary=list(evidence_boundary),
        pending=pending,
        completed_at=completed_at,
        error_code=error_code,
        error_message=error_message,
        retryable=retryable,
    )


def _empty_analysis() -> TiebaQuestionAnalysis:
    return TiebaQuestionAnalysis(original_question="", topic_terms=[])


def stopped_result(result: KernelResult) -> KernelResult:
    from dataclasses import replace

    return replace(result, status=KernelStatus.STOPPED)
