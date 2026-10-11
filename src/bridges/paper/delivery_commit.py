"""论文结果摘要、终态打包与原子提交。"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Any

from bridges.ai.model_quota import RunModelQuota
from bridges.ai.payload_budget import CallMaterialManifest
from bridges.contracts.ai import ModelRunLock
from bridges.contracts.chat import ChatMessageStatus
from bridges.contracts.modules import ModuleDelivery
from bridges.contracts.workflows import RunContextEnvelope
from bridges.kernel.contracts import KernelResult
from bridges.paper.contracts import PaperRecommendation, PaperSearchProjection, PaperSearchStatus
from bridges.paper.delivery_types import (
    PAPER_MODULE_ID,
    WAIT_REASON_CLARIFICATION,
    PaperRunOutcome,
    _PreparedDelivery,
)
from bridges.paper.presenting import SummaryOutcome
from bridges.paper.projection import _artifact_refs, _expression_snapshot

if TYPE_CHECKING:
    from bridges.chat.repository import ConversationRepository
    from bridges.paper.service import PaperSearchService



def _deliver(
    self: PaperSearchService,
    repo: ConversationRepository,
    *,
    account_id: str,
    assistant_message_id: str,
    prepared: _PreparedDelivery,
    result: KernelResult,
) -> PaperRunOutcome:
    projection = prepared.projection
    _finalize(self,
        repo,
        account_id=account_id,
        assistant_message_id=assistant_message_id,
        status=(
            ChatMessageStatus.ERROR
            if prepared.failure is not None
            else prepared.message_status
        ),
        projection=projection,
        content=prepared.content,
        now=self._clock(),
        lock=prepared.lock,
    )
    return PaperRunOutcome(
        status=projection.status,
        wait_reason=(
            WAIT_REASON_CLARIFICATION
            if projection.status is PaperSearchStatus.CLARIFICATION
            else None
        ),
        queries=list(projection.queries),
        artifacts=_artifact_refs(result),
    )


def _package_delivery(
    self: PaperSearchService,
    *,
    prepared: _PreparedDelivery,
    result: KernelResult,
) -> PaperRunOutcome:
    """生产父图路径只构造交付；不在模块内写助手消息终态。"""
    projection = prepared.projection
    failure = prepared.failure
    wait_reason = (
        WAIT_REASON_CLARIFICATION
        if projection.status is PaperSearchStatus.CLARIFICATION
        else None
    )
    artifact_refs = _artifact_refs(result)
    return PaperRunOutcome(
        status=projection.status,
        wait_reason=wait_reason,
        queries=list(projection.queries),
        artifacts=artifact_refs,
        delivery=ModuleDelivery(
            module_id=PAPER_MODULE_ID,
            status=projection.status.value,
            projection_field="paper_search",
            projection=projection.model_dump(mode="json"),
            content=prepared.content,
            message_status=(
                ChatMessageStatus.ERROR
                if failure is not None
                else prepared.message_status
            ).value,
            error_node=failure.node if failure is not None else None,
            error_code=failure.code if failure is not None else None,
            error_message=failure.message if failure is not None else None,
            retryable=failure.retryable if failure is not None else False,
            artifact_refs=artifact_refs,
            lock=prepared.lock,
            wait_reason=wait_reason,
        ),
    )


def _summarize(
    self: PaperSearchService,
    recommendations: list[PaperRecommendation],
    abstracts: dict[str, str],
    *,
    run_context: RunContextEnvelope,
    run_model_id: str | None,
    model_quota: RunModelQuota | None,
    manifest_sink: Callable[[CallMaterialManifest], None] | None,
    writing_policy: Mapping[str, Any] | None,
) -> tuple[SummaryOutcome | None, str | None]:
    if self._summarizer is None:
        return None, None
    expression = _expression_snapshot(writing_policy)
    result = self._summarizer.generate(
        run_context,
        recommendations,
        abstracts=abstracts,
        model_id=run_model_id,
        model_quota=model_quota,
        expression=expression,
    )
    if result.manifest is not None and manifest_sink is not None:
        manifest_sink(result.manifest)
    return result, expression.version if expression is not None else None


def _finalize(
    self: PaperSearchService,
    repo: ConversationRepository,
    *,
    account_id: str,
    assistant_message_id: str,
    status: ChatMessageStatus,
    projection: PaperSearchProjection,
    content: str,
    now: datetime,
    lock: ModelRunLock | None = None,
) -> None:
    # 正文与终态、论文投影在同一事务内一次提交（finalize_message 的
    # final_content 字段），不先走流式增量接口——外层已持有提交事务。
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
        run_lock_id=lock.lock_id if lock is not None else None,
        lock=lock,
        started=time.monotonic(),
        now=now,
        paper_search=projection.model_dump(mode="json"),
        final_content=content or None,
    )
