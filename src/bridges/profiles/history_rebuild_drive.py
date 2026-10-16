"""画像工单 17：历史重整的有界驱动（调度 → 领取 → 分类同步）。

驱动只做三件事（顺序在每次 ``run_rebuild`` 内固定）：

1. 把台账里的待定条目经在线调度入口入队（受账户背压约束，被拦条目
   保持 ``pending`` 并如实计入暂停说明）；
2. 调用在线 ``run_retry_tick`` 至多 ``max_ticks`` 次（空闲输出提前收敛，
   模型调用次数即额度）；
3. 把在途条目的真实管道状态同步回台账（幂等；崩溃恢复后同样成立）。

终态分类：运行成功→已提取；提交前屏障错误码→屏障跳过；其余重试耗尽→
失败（绝不当作成功消费水位）；领取行重试预算耗尽（``failed`` 且无
``next_retry_at``）→失败。版本（策略/抽取器）不一致时拒绝恢复旧台账。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from bridges.profiles.automatic import (
    PROFILE_EXTRACTION_MAX_PENDING_PER_ACCOUNT,
    PROFILE_REBUILD_QUEUE,
    RETRY_TICK_IDLE_OUTPUTS,
    AutomaticProfileService,
)
from bridges.profiles.history_rebuild_contracts import (
    BARRIER_EXHAUSTION_CODES,
    HISTORY_REBUILD_POLICY_VERSION,
    ProfileRebuildError,
    RebuildItemStatus,
    RebuildSkipReason,
    RebuildStatus,
    now_text,
)
from bridges.profiles.history_rebuild_pipeline import (
    ExtractionRunSnapshot,
    ProfileRebuildPipelineView,
)
from bridges.profiles.history_rebuild_records import RebuildRunRecord
from bridges.profiles.history_rebuild_repository import (
    SqliteProfileRebuildRepository,
    build_progress,
    require_run,
)
from bridges.profiles.history_rebuild_runtime import (
    ExtractorFactory,
    build_runtime,
    load_workspace,
    open_working_database,
)
from bridges.profiles.history_repository import (
    HistoryMessageRecord,
    ProfileHistorySnapshotReader,
)
from bridges.profiles.signals import ProfileSignalClassifier
from bridges.storage.database import BridgesDatabase

#: 单次 ``run`` 的模型调用上限（达到上限即暂停，可再次运行继续）。
DEFAULT_REBUILD_MAX_TICKS = 100


def _check_resume_versions(
    run: RebuildRunRecord, *, extractor_version: str, policy_version: str
) -> None:
    if run.policy_version != policy_version:
        raise ProfileRebuildError(
            "rebuild_version_mismatch",
            "策略版本已变化：禁止在新策略下恢复旧台账，请重新预览并启动新批次。",
        )
    if run.extractor_version != extractor_version:
        raise ProfileRebuildError(
            "rebuild_version_mismatch",
            "抽取器版本已变化：禁止恢复旧台账（幂等键绑定抽取器版本）。",
        )


def _classify_terminal_run(
    snapshot: ExtractionRunSnapshot,
) -> tuple[RebuildItemStatus, RebuildSkipReason | None, str | None]:
    """把在线运行的终态映射为台账状态；屏障与失败严格区分。"""

    if snapshot.status == "succeeded":
        return RebuildItemStatus.EXTRACTED, None, None
    if snapshot.status == "exhausted":
        barrier = BARRIER_EXHAUSTION_CODES.get(snapshot.last_error or "")
        if barrier is not None:
            return RebuildItemStatus.SKIPPED, barrier, None
        if snapshot.outcome == "no_signal" and snapshot.attempts == 0:
            return (
                RebuildItemStatus.SKIPPED,
                RebuildSkipReason.BLOCKED_AT_SCHEDULE,
                None,
            )
        return RebuildItemStatus.FAILED, None, snapshot.last_error
    return RebuildItemStatus.SCHEDULED, None, None


def _schedule_pending_items(
    database: BridgesDatabase,
    service: AutomaticProfileService,
    ledger: SqliteProfileRebuildRepository,
    pipeline: ProfileRebuildPipelineView,
    run: RebuildRunRecord,
) -> tuple[int, int]:
    """调度待定条目：本地门控后经在线调度入口入队；受账户背压约束。"""

    classifier = ProfileSignalClassifier()
    messages: dict[str, HistoryMessageRecord] = {
        message.message_id: message
        for message in ProfileHistorySnapshotReader(
            database.connection
        ).iter_messages(run.account_id)
    }
    scheduled = 0
    backlog_blocked = 0
    for item in ledger.list_items(
        run.rebuild_id, statuses=[RebuildItemStatus.PENDING]
    ):
        if (
            pipeline.count_pending_tasks(run.account_id)
            >= PROFILE_EXTRACTION_MAX_PENDING_PER_ACCOUNT
        ):
            backlog_blocked += 1
            continue
        message = messages.get(item.message_id)
        if message is None:
            ledger.update_item(
                run.rebuild_id,
                item.message_id,
                status=RebuildItemStatus.SKIPPED,
                skip_reason=RebuildSkipReason.SOURCE_MISSING,
            )
            continue
        classification = classifier.extraction_classification(message.content)
        if not classifier.is_schedulable(message.content, classification):
            ledger.update_item(
                run.rebuild_id,
                item.message_id,
                status=RebuildItemStatus.SKIPPED,
                skip_reason=RebuildSkipReason.NO_SIGNAL,
            )
            continue
        extraction_run = service.schedule_message_extraction(
            run.account_id,
            conversation_id=item.conversation_id,
            message_id=item.message_id,
            content=message.content,
            run_id=run.rebuild_id,
        )
        if extraction_run is None:
            backlog_blocked += 1
            continue
        if extraction_run.run_kind.value == "material":
            ledger.update_item(
                run.rebuild_id,
                item.message_id,
                status=RebuildItemStatus.SKIPPED,
                skip_reason=RebuildSkipReason.MATERIAL_RUN_PRESENT,
                extraction_id=extraction_run.extraction_id,
                source_hash=extraction_run.source_hash,
            )
            scheduled += 1
            continue
        status, skip_reason, last_error = _classify_terminal_run(
            ExtractionRunSnapshot(
                extraction_id=extraction_run.extraction_id,
                status=extraction_run.status.value,
                outcome=extraction_run.outcome.value,
                attempts=extraction_run.attempts,
                committed_count=len(extraction_run.committed_record_ids),
                observed_count=extraction_run.observed_count,
                last_error=extraction_run.last_error,
                run_kind=extraction_run.run_kind.value,
            )
        )
        if status is RebuildItemStatus.SCHEDULED:
            queued = service.ensure_task_queued(
                run.account_id,
                message_id=extraction_run.message_id,
                extractor_version=extraction_run.extractor_version,
                source_hash=extraction_run.source_hash,
            )
            ledger.update_item(
                run.rebuild_id,
                item.message_id,
                status=RebuildItemStatus.SCHEDULED,
                source_hash=extraction_run.source_hash,
                extraction_id=extraction_run.extraction_id,
                last_error=None if queued else "run_not_queueable",
                now=now_text(),
            )
            scheduled += 1
            continue
        ledger.update_item(
            run.rebuild_id,
            item.message_id,
            status=status,
            skip_reason=skip_reason,
            extraction_id=extraction_run.extraction_id,
            source_hash=extraction_run.source_hash,
            committed_count=len(extraction_run.committed_record_ids),
            observed_count=extraction_run.observed_count,
            last_error=last_error,
        )
        scheduled += 1
    return scheduled, backlog_blocked


def _sync_inflight_items(
    service: AutomaticProfileService,
    ledger: SqliteProfileRebuildRepository,
    pipeline: ProfileRebuildPipelineView,
    run: RebuildRunRecord,
) -> None:
    """把在途条目的真实管道状态同步回台账（幂等；崩溃恢复后同样成立）。"""

    extractor_version = service.extractor_version
    for item in ledger.list_items(
        run.rebuild_id, statuses=[RebuildItemStatus.SCHEDULED]
    ):
        if item.source_hash is None:
            ledger.update_item(
                run.rebuild_id,
                item.message_id,
                status=RebuildItemStatus.PENDING,
            )
            continue
        snapshot = pipeline.read_extraction_run(
            run.account_id, item.message_id, extractor_version, item.source_hash
        )
        if snapshot is None:
            ledger.update_item(
                run.rebuild_id,
                item.message_id,
                status=RebuildItemStatus.PENDING,
            )
            continue
        status, skip_reason, last_error = _classify_terminal_run(snapshot)
        if status is not RebuildItemStatus.SCHEDULED:
            ledger.update_item(
                run.rebuild_id,
                item.message_id,
                status=status,
                skip_reason=skip_reason,
                extraction_id=snapshot.extraction_id,
                committed_count=snapshot.committed_count,
                observed_count=snapshot.observed_count,
                last_error=last_error,
            )
            continue
        task = pipeline.read_task(
            run.account_id, item.message_id, extractor_version, item.source_hash
        )
        if task is None:
            service.ensure_task_queued(
                run.account_id,
                message_id=item.message_id,
                extractor_version=extractor_version,
                source_hash=item.source_hash,
            )
            continue
        claim = pipeline.read_claim(PROFILE_REBUILD_QUEUE, task.task_id)
        if claim is not None and claim.next_retry_at is None and claim.status == "failed":
            # 重试预算耗尽：如实记为失败，不当作成功水位。
            ledger.update_item(
                run.rebuild_id,
                item.message_id,
                status=RebuildItemStatus.FAILED,
                last_error=snapshot.last_error or task.last_error or "retries_exhausted",
                committed_count=snapshot.committed_count,
                observed_count=snapshot.observed_count,
            )
            continue
        if claim is None:
            service.ensure_task_queued(
                run.account_id,
                message_id=item.message_id,
                extractor_version=extractor_version,
                source_hash=item.source_hash,
            )


def run_rebuild(
    work_dir: str | Path,
    *,
    extractor_factory: ExtractorFactory,
    max_ticks: int = DEFAULT_REBUILD_MAX_TICKS,
    now: datetime | None = None,
) -> dict[str, object]:
    """有界推进一次：调度 → 领取（至多 ``max_ticks`` 次模型调用）→ 同步。"""

    if max_ticks <= 0:
        raise ProfileRebuildError("max_ticks_invalid", "max_ticks 必须为正整数。")
    workspace = load_workspace(work_dir)
    database = open_working_database(workspace)
    try:
        ledger = SqliteProfileRebuildRepository(database, initialize=False)
        pipeline = ProfileRebuildPipelineView(database)
        run = require_run(ledger, workspace.rebuild_id)
        runtime = build_runtime(database, extractor_factory)
        service = runtime.service
        _check_resume_versions(
            run,
            extractor_version=service.extractor_version,
            policy_version=HISTORY_REBUILD_POLICY_VERSION,
        )
        if run.status is RebuildStatus.COMPLETED:
            progress = build_progress(ledger, run)
            return {
                "rebuild_id": run.rebuild_id,
                "completed": True,
                "ticks": 0,
                "max_ticks": max_ticks,
                "reached_budget": False,
                "progress": progress.as_dict(),
            }
        ledger.update_run(
            workspace.rebuild_id,
            status=RebuildStatus.RUNNING,
            clear_last_error=True,
            now=now_text(now),
        )
        _, backlog_blocked = _schedule_pending_items(
            database, service, ledger, pipeline, run
        )
        ticks = 0
        while ticks < max_ticks:
            outcome = service.run_retry_tick()
            if outcome in RETRY_TICK_IDLE_OUTPUTS:
                break
            ticks += 1
        _schedule_pending_items(database, service, ledger, pipeline, run)
        _sync_inflight_items(service, ledger, pipeline, run)
        ledger.refresh_counters(workspace.rebuild_id, now=now_text(now))
        run = require_run(ledger, workspace.rebuild_id)
        progress = build_progress(ledger, run)
        if progress.remaining == 0:
            ledger.update_run(
                workspace.rebuild_id,
                status=RebuildStatus.COMPLETED,
                completed_at=run.completed_at or now_text(now),
                clear_last_error=True,
                now=now_text(now),
            )
        else:
            note = (
                "调度被账户背压拦截，剩余条目可稍后再次运行。"
                if backlog_blocked
                else "仍有在途/待定条目，再次运行将继续推进。"
            )
            ledger.update_run(
                workspace.rebuild_id,
                status=RebuildStatus.PAUSED,
                last_error=note,
                now=now_text(now),
            )
        run = require_run(ledger, workspace.rebuild_id)
        progress = build_progress(ledger, run)
        return {
            "rebuild_id": run.rebuild_id,
            "completed": run.status is RebuildStatus.COMPLETED,
            "ticks": ticks,
            "max_ticks": max_ticks,
            "reached_budget": ticks >= max_ticks,
            "progress": progress.as_dict(),
        }
    finally:
        database.close()


__all__ = ["DEFAULT_REBUILD_MAX_TICKS", "run_rebuild"]
