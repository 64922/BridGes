"""画像工单 17：历史重整的对账（在隔离副本上执行，可重复）。

对账覆盖工单验收要求的检查面：

- 事实：人工条目保留权威（正文摘要与编辑时间不变）、撤回不复活、墓碑
  来源无迟到成功、同一事实键不重复生效；
- 证据：有效条目的来源消息仍存在且未被墓碑删除；台账的迁入条目与管道
  运行终态一致；
- 控制：记录开关/内容禁止/墓碑数量与基线一致；
- 派生：概览可立即失效并重建（读取态回到当前），召回索引不泄漏旧事实
  （陈旧行不参与检索，如实计数）；
- 账户清理 / 导出 / 恢复演练：见 ``history_rebuild_drills``。

全部检查只输出计数、标识与摘要，绝不输出消息或条目正文。硬门不过时
报告照写、退出码 2；漂移（权威来源已前进）如实记录为警告而非静默。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from bridges.profiles.history_contracts import digest_lines
from bridges.profiles.history_preview import (
    load_preview_manifest,
    verify_preview_manifest,
)
from bridges.profiles.history_rebuild_contracts import (
    REBUILD_REPORT_FILENAME,
    ProfileRebuildError,
    RebuildItemStatus,
    json_mapping,
    now_text,
)
from bridges.profiles.history_rebuild_drills import (
    deletion_drill,
    export_section,
    restore_drill,
)
from bridges.profiles.history_rebuild_pipeline import ProfileRebuildPipelineView
from bridges.profiles.history_rebuild_records import RebuildRunRecord
from bridges.profiles.history_rebuild_repository import (
    SqliteProfileRebuildRepository,
    require_run,
)
from bridges.profiles.history_rebuild_runtime import (
    ExtractorFactory,
    RebuildRuntime,
    RebuildWorkspace,
    build_runtime,
    load_workspace,
    open_working_database,
)
from bridges.profiles.history_repository import ProfileHistorySnapshotReader
from bridges.profiles.recall import profile_recall_text_hash
from bridges.profiles.replay import DatabaseIdentity
from bridges.storage.database import BridgesDatabase


def _int_value(value: object) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def reconcile_rebuild(
    work_dir: str | Path,
    *,
    extractor_factory: ExtractorFactory,
    authority: DatabaseIdentity | None = None,
    drill_dir: str | Path | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    """执行完整对账与演练；返回报告（``passed`` 为全部硬门结论）。"""

    workspace = load_workspace(work_dir)
    drill_root = Path(drill_dir) if drill_dir is not None else workspace.work_dir / "drill"
    drill_root.mkdir(parents=True, exist_ok=True)
    errors: list[dict[str, object]] = []
    warnings: list[str] = []

    database = open_working_database(workspace)
    try:
        ledger = SqliteProfileRebuildRepository(database, initialize=False)
        pipeline = ProfileRebuildPipelineView(database)
        run = require_run(ledger, workspace.rebuild_id)
        counts = ledger.count_items(run.rebuild_id)
        remaining = counts.get(RebuildItemStatus.PENDING.value, 0) + counts.get(
            RebuildItemStatus.SCHEDULED.value, 0
        )
        if remaining:
            raise ProfileRebuildError(
                "rebuild_not_settled",
                f"仍有 {remaining} 条未决条目；先继续运行到全部终态再对账。",
            )
        runtime = build_runtime(database, extractor_factory)
        report: dict[str, object] = {
            "state_version": run.baseline.state_version,
            "rebuild_id": run.rebuild_id,
            "account_id": run.account_id,
            "generated_at": now_text(now),
            "policy_version": run.policy_version,
            "extractor_version": run.extractor_version,
            "facts": _facts_section(database, pipeline, run, errors),
            "evidence": _evidence_section(database, ledger, pipeline, runtime, run, errors),
            "controls": _controls_section(database, run, errors),
            "derived": _derived_section(database, runtime, run, errors, warnings),
            "export": export_section(database, run.account_id, errors),
            "source_drift": _drift_section(authority, workspace, warnings),
        }
    finally:
        database.close()

    report["deletion"] = deletion_drill(workspace, run.account_id, drill_root, errors)
    report["restore"] = restore_drill(workspace, run.account_id, drill_root, errors)
    report["warnings"] = warnings
    report["errors"] = errors
    report["passed"] = not errors

    (workspace.work_dir / REBUILD_REPORT_FILENAME).write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    if report["passed"]:
        database = open_working_database(workspace)
        try:
            ledger = SqliteProfileRebuildRepository(database, initialize=False)
            ledger.update_run(
                workspace.rebuild_id,
                reconciled_at=now_text(now),
                now=now_text(now),
            )
        finally:
            database.close()
    return report


# ----------------------------------------------------------------------
# 事实 / 证据 / 控制
# ----------------------------------------------------------------------


def _facts_section(
    database: BridgesDatabase,
    pipeline: ProfileRebuildPipelineView,
    run: RebuildRunRecord,
    errors: list[dict[str, object]],
) -> dict[str, object]:
    reader = ProfileHistorySnapshotReader(database.connection)
    items = reader.list_items(run.account_id)
    by_id = {item.profile_item_id: item for item in items}
    active_identity_keys = {
        item.identity_key for item in items if item.status == "active"
    }

    missing_manual: list[str] = []
    changed_manual: list[str] = []
    for entry in run.baseline.manual_items:
        current = by_id.get(entry.profile_item_id)
        if current is None:
            missing_manual.append(entry.profile_item_id)
            continue
        if (
            digest_lines([current.text]) != entry.text_digest
            or current.user_edited_at != entry.user_edited_at
        ):
            changed_manual.append(entry.profile_item_id)
    if missing_manual:
        errors.append(
            {
                "check": "manual_items_preserved",
                "detail": "人工条目缺失",
                "item_ids": missing_manual[:20],
            }
        )
    if changed_manual:
        errors.append(
            {
                "check": "manual_items_preserved",
                "detail": "人工条目正文或编辑时间被改写",
                "item_ids": changed_manual[:20],
            }
        )

    revived = sorted(
        item_id
        for item_id in run.baseline.withdrawn_item_ids
        if item_id in by_id and by_id[item_id].status == "active"
    )
    revived_keys = sorted(
        key
        for key in run.baseline.withdrawn_identity_keys
        if key in active_identity_keys
    )
    if revived or revived_keys:
        errors.append(
            {
                "check": "withdrawn_not_revived",
                "detail": "撤回内容在重整后重新生效",
                "item_ids": revived[:20],
                "identity_keys": revived_keys[:20],
            }
        )

    duplicate_fact_keys = [
        str(row["fact_key"])
        for row in database.connection.execute(
            "SELECT fact_key, COUNT(*) AS total FROM profile_items"
            " WHERE account_id = ? AND status = 'active' AND fact_key != ''"
            " GROUP BY fact_key HAVING COUNT(*) > 1",
            (run.account_id,),
        ).fetchall()
    ]
    if duplicate_fact_keys:
        errors.append(
            {
                "check": "no_duplicate_active_facts",
                "detail": "同一事实键存在多条有效条目",
                "fact_keys": duplicate_fact_keys[:20],
            }
        )

    late_success = pipeline.count_tombstoned_successes_since(
        run.account_id, since=run.created_at
    )
    if late_success:
        errors.append(
            {
                "check": "tombstone_barrier",
                "detail": "墓碑消息在重整期间出现成功抽取",
                "count": late_success,
            }
        )

    return {
        "manual_items_expected": len(run.baseline.manual_items),
        "manual_items_intact": len(run.baseline.manual_items)
        - len(missing_manual)
        - len(changed_manual),
        "withdrawn_items": len(run.baseline.withdrawn_item_ids),
        "duplicate_fact_keys": duplicate_fact_keys[:20],
        "tombstoned_late_success": late_success,
    }


def _baseline_locator_set(run: RebuildRunRecord, key: str) -> set[str]:
    """基线清单里某分类的条目标识集合（用于区分既有降级与新增悬挂引用）。"""

    items = json_mapping(run.baseline.items)
    locators = json_mapping(items.get("locators"))
    value = locators.get(key)
    if not isinstance(value, (list, tuple)):
        return set()
    return {str(entry) for entry in value}


def _evidence_section(
    database: BridgesDatabase,
    ledger: SqliteProfileRebuildRepository,
    pipeline: ProfileRebuildPipelineView,
    runtime: RebuildRuntime,
    run: RebuildRunRecord,
    errors: list[dict[str, object]],
) -> dict[str, object]:
    reader = ProfileHistorySnapshotReader(database.connection)
    items = reader.list_items(run.account_id)
    message_ids = {message.message_id for message in reader.iter_messages(run.account_id)}
    tombstoned = set(run.baseline.tombstoned_message_ids)

    # 预览基线已经分类过的既有降级（缺证据/悬挂来源）不是本次重整引入的
    # 问题：继续按“明确标记处理结果”对待，仅复核是否出现新增悬挂引用。
    known_degraded_items: set[str] = set()
    for key in (
        "automatic_missing_evidence",
        "automatic_unverified_source",
        "automatic_partial_evidence",
        "automatic_deleted_evidence",
    ):
        known_degraded_items |= _baseline_locator_set(run, key)
    unverified_payload = json_mapping(run.baseline.items).get("unverified_source_refs")
    baseline_unverified_refs = (
        {str(entry) for entry in unverified_payload}
        if isinstance(unverified_payload, (list, tuple))
        else set()
    )
    known_tombstone_items = _baseline_locator_set(run, "automatic_deleted_evidence")

    unresolved: list[str] = []
    citing_tombstone: list[str] = []
    known_unverified: list[str] = []
    known_tombstone: list[str] = []
    for item in items:
        if item.status != "active":
            continue
        for message_id in item.source_message_ids:
            ref = f"{item.profile_item_id}:{message_id}"
            if message_id not in message_ids:
                if (
                    item.profile_item_id in known_degraded_items
                    or message_id in baseline_unverified_refs
                ):
                    known_unverified.append(ref)
                else:
                    unresolved.append(ref)
            elif message_id in tombstoned:
                if item.profile_item_id in known_tombstone_items:
                    known_tombstone.append(ref)
                else:
                    citing_tombstone.append(ref)
    if unresolved:
        errors.append(
            {
                "check": "evidence_resolvable",
                "detail": "有效条目引用了不存在的来源消息（基线之外的悬挂引用）",
                "refs": unresolved[:20],
            }
        )
    if citing_tombstone:
        errors.append(
            {
                "check": "evidence_no_tombstone",
                "detail": "有效条目在重整后引用了已删除来源",
                "refs": citing_tombstone[:20],
            }
        )

    inconsistent: list[str] = []
    for ledger_item in ledger.list_items(run.rebuild_id):
        if ledger_item.status is not RebuildItemStatus.EXTRACTED:
            continue
        if ledger_item.source_hash is None:
            inconsistent.append(ledger_item.message_id)
            continue
        snapshot = pipeline.read_extraction_run(
            run.account_id,
            ledger_item.message_id,
            run.extractor_version,
            ledger_item.source_hash,
        )
        if snapshot is None or snapshot.status != "succeeded":
            inconsistent.append(ledger_item.message_id)
            continue
        if snapshot.committed_count != ledger_item.committed_count:
            inconsistent.append(ledger_item.message_id)
    if inconsistent:
        errors.append(
            {
                "check": "ledger_pipeline_consistency",
                "detail": "台账迁入条目与抽取运行终态不一致",
                "message_ids": inconsistent[:20],
            }
        )

    active_with_sources = sum(
        1
        for item in items
        if item.status == "active" and item.source_message_ids
    )
    return {
        "active_items_with_sources": active_with_sources,
        "unresolved_refs": unresolved[:20],
        "tombstone_refs": citing_tombstone[:20],
        "known_unverified_refs": known_unverified[:20],
        "known_tombstone_refs": known_tombstone[:20],
        "ledger_inconsistent": inconsistent[:20],
    }


def _controls_section(
    database: BridgesDatabase,
    run: RebuildRunRecord,
    errors: list[dict[str, object]],
) -> dict[str, object]:
    reader = ProfileHistorySnapshotReader(database.connection)
    controls = reader.read_controls(run.account_id)
    baseline = json_mapping(run.baseline.controls)
    current = {
        "recording_enabled": controls.recording_enabled,
        "usage_enabled": controls.usage_enabled,
        "content_block_count": len(controls.content_blocks),
        "tombstone_count": len(controls.tombstoned_message_ids),
    }
    expected = {
        "recording_enabled": bool(baseline.get("recording_enabled")),
        "usage_enabled": bool(baseline.get("usage_enabled")),
        "content_block_count": _int_value(baseline.get("content_block_count")),
        "tombstone_count": _int_value(baseline.get("tombstone_count")),
    }
    if current != expected:
        errors.append(
            {
                "check": "controls_unchanged",
                "detail": "控制状态在重整期间被改写",
                "expected": expected,
                "current": current,
            }
        )
    return {"expected": expected, "current": current, "unchanged": current == expected}


# ----------------------------------------------------------------------
# 派生（概览 / 召回索引）
# ----------------------------------------------------------------------


def _derived_section(
    database: BridgesDatabase,
    runtime: RebuildRuntime,
    run: RebuildRunRecord,
    errors: list[dict[str, object]],
    warnings: list[str],
) -> dict[str, object]:
    runtime.overview.invalidate(run.account_id, reason="history_rebuild_reconcile")
    projection = runtime.overview.list_overview(run.account_id)
    second = runtime.overview.list_overview(run.account_id)
    states = {str(getattr(projection.state, "value", projection.state))}
    overview_ok = (
        states <= {"current", "empty"}
        and projection.model_dump(mode="json") == second.model_dump(mode="json")
    )
    if not overview_ok:
        errors.append(
            {
                "check": "overview_rebuildable",
                "detail": "概览失效后未能重建为当前状态",
                "state": sorted(states),
            }
        )

    stale_entries = 0
    active_entries = 0
    rows = database.connection.execute(
        "SELECT e.profile_item_id, e.item_version, e.text_hash"
        " FROM profile_recall_index_entries e"
        " JOIN profile_recall_index_versions v"
        "   ON v.version_id = e.version_id AND v.status = 'active'"
        " WHERE e.account_id = ?",
        (run.account_id,),
    ).fetchall()
    items = {item.profile_item_id: item for item in runtime.atoms.list_items(run.account_id)}
    for row in rows:
        item = items.get(str(row["profile_item_id"]))
        if item is None or str(item.status.value) != "active":
            stale_entries += 1
            continue
        active_entries += 1
        if (
            int(row["item_version"]) != item.version
            or str(row["text_hash"]) != profile_recall_text_hash(item.text)
        ):
            stale_entries += 1
    if stale_entries:
        warnings.append(
            f"召回索引有 {stale_entries} 条陈旧行"
            "（检索按版本/哈希过滤，不参与召回；下次同步重建）。"
        )

    return {
        "overview_state": sorted(states),
        "overview_rebuildable": overview_ok,
        "recall_active_entries": active_entries,
        "recall_stale_entries": stale_entries,
    }


def _drift_section(
    authority: DatabaseIdentity | None,
    workspace: RebuildWorkspace,
    warnings: list[str],
) -> dict[str, object]:
    if authority is None:
        warnings.append("未提供权威来源，跳过清单漂移复核（执行开始时的复核仍然成立）。")
        return {"checked": False, "reason": "authority_unavailable"}
    manifest = load_preview_manifest(workspace.manifest_path)
    drift = verify_preview_manifest(authority, manifest)
    if not drift.fresh:
        warnings.append(
            "权威来源在重构期间已前进（清单过期）；本次执行基于冻结副本，"
            "切换由维护者按运行手册单独确认。"
        )
    return {"checked": True, **drift.as_dict()}


__all__ = ["reconcile_rebuild"]
