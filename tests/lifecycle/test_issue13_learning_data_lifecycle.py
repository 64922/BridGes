"""工单 13：学习模式数据的导出、删除隔离与备份恢复整合演练。

与各票的单元测试不同，本文件在真实文件数据库上铺设全套学习模式数据
（进展/待答/证据/目标/路线/诊断/档案/采用/来源核实），验证发布级数据
生命周期：导出按账户隔离、删除只清目标账户、备份恢复完整回卷。
"""

from __future__ import annotations

import json
from typing import Any

from harness import Harness

from bridges.contracts.lifecycle import AccountDeletionStatus

_STUDY_TABLES = (
    "study_local_progress",
    "study_pending_checks",
    "study_evidence",
    "study_source_verifications",
    "study_goals",
    "study_goal_routes",
    "study_conversation_goals",
    "study_diagnosis_tasks",
    "study_diagnosis_results",
    "study_learning_records",
    "study_record_adoptions",
)

_NOW = "2026-10-14T10:00:00+00:00"


def _seed_learning_data(harness: Harness, account_id: str) -> dict[str, str]:
    """按真实外键关系铺设一个账户的完整学习模式数据，返回关键标识。"""

    database = harness.database
    tag = account_id[:6]
    conversation_id = harness.create_conversation(account_id, f"学习-{tag}", 2)
    ids = {
        "conversation_id": conversation_id,
        "goal_id": f"goal-{tag}",
        "record_id": f"rec-{tag}",
        "task_id": f"task-{tag}",
        "verification_id": f"ver-{tag}",
        "adoption_id": f"ado-{tag}",
    }
    scoped = database.scoped(account_id)
    with database.transaction():
        scoped.execute(
            "INSERT INTO study_local_progress(account_id, conversation_id, topic,"
            " knowledge_context, current_focus, source_message_ids_json, version,"
            " runtime_run_id, updated_at) VALUES (?, ?, ?, ?, ?, '[]', 2, ?, ?)",
            (
                account_id,
                conversation_id,
                f"学习主题-{tag}",
                "互斥与独立",
                f"当前焦点-{tag}",
                f"run-{tag}",
                _NOW,
            ),
        )
        scoped.execute(
            "INSERT INTO study_pending_checks(account_id, conversation_id, check_id,"
            " kind, question, knowledge_context, basis_json, hint_history_json,"
            " source_message_id, version, created_at, updated_at)"
            " VALUES (?, ?, ?, 'formal_check', ?, '互斥与独立', '{}', '[]', ?, 1, ?, ?)",
            (
                account_id,
                conversation_id,
                f"check-{tag}",
                f"待答问题-{tag}",
                f"msg-{tag}",
                _NOW,
                _NOW,
            ),
        )
        scoped.execute(
            "INSERT INTO study_evidence(evidence_id, account_id, conversation_id,"
            " run_id, check_id, check_kind, check_version, question,"
            " knowledge_context, basis_protocol_version, answered, outcome,"
            " semantics, assistance, created_at)"
            " VALUES (?, ?, ?, ?, ?, 'formal_check', 1, ?, '互斥与独立',"
            " 'study-verification-v1', 1, 'correct', 'independent_completion',"
            " 'independent', ?)",
            (
                f"ev-{tag}",
                account_id,
                conversation_id,
                f"run-{tag}",
                f"check-{tag}",
                f"证据问题-{tag}",
                _NOW,
            ),
        )
        scoped.execute(
            "INSERT INTO study_goals(goal_id, account_id, statement,"
            " source_message_id, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                ids["goal_id"],
                account_id,
                f"学习目标-{tag}",
                f"msg-{tag}",
                _NOW,
                _NOW,
            ),
        )
        scoped.execute(
            "INSERT INTO study_goal_routes(account_id, goal_id, version, nodes_json,"
            " current_node_id, revision_reason, origin, created_at)"
            " VALUES (?, ?, 1, ?, 'n1', '', 'assistant_proposal', ?)",
            (
                account_id,
                ids["goal_id"],
                json.dumps(
                    [{"node_id": "n1", "title": f"节点-{tag}", "prerequisites": [], "depth": "基础"}],
                    ensure_ascii=False,
                ),
                _NOW,
            ),
        )
        scoped.execute(
            "INSERT INTO study_conversation_goals(account_id, conversation_id,"
            " goal_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (account_id, conversation_id, ids["goal_id"], _NOW, _NOW),
        )
        scoped.execute(
            "INSERT INTO study_diagnosis_tasks(task_id, account_id, conversation_id,"
            " evidence_id, question, knowledge_context, input_version,"
            " protocol_version, status, attempt, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, '互斥与独立', ?, 'study-diagnosis-v1',"
            " 'succeeded', 1, ?, ?)",
            (
                ids["task_id"],
                account_id,
                conversation_id,
                f"ev-{tag}",
                f"证据问题-{tag}",
                f"input-{tag}",
                _NOW,
                _NOW,
            ),
        )
        scoped.execute(
            "INSERT INTO study_diagnosis_results(result_id, task_id, account_id,"
            " conversation_id, evidence_id, record_id, protocol_version,"
            " input_version, result_version, verdict, subject, knowledge_title,"
            " created_at) VALUES (?, ?, ?, ?, ?, ?, 'study-diagnosis-v1', ?, 1,"
            " 'independent_completion', '概率论', '互斥与独立', ?)",
            (
                f"res-{tag}",
                ids["task_id"],
                account_id,
                conversation_id,
                f"ev-{tag}",
                ids["record_id"],
                f"input-{tag}",
                _NOW,
            ),
        )
        scoped.execute(
            "INSERT INTO study_learning_records(record_id, account_id,"
            " knowledge_key, subject, title, verdict, performance,"
            " latest_evidence_id, latest_result_id, latest_evidence_at,"
            " evidence_ids_json, created_at, updated_at)"
            " VALUES (?, ?, ?, '概率论', '互斥与独立', 'independent_completion',"
            " ?, ?, ?, ?, ?, ?, ?)",
            (
                ids["record_id"],
                account_id,
                f"kk-{tag}",
                f"表现-{tag}",
                f"ev-{tag}",
                f"res-{tag}",
                _NOW,
                json.dumps([f"ev-{tag}"]),
                _NOW,
                _NOW,
            ),
        )
        scoped.execute(
            "INSERT INTO study_record_adoptions(adoption_id, account_id,"
            " conversation_id, run_id, assistant_message_id, status,"
            " entries_json, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, 'applied', '[]', ?, ?)",
            (
                ids["adoption_id"],
                account_id,
                conversation_id,
                f"run-{tag}",
                f"assistant-{tag}",
                _NOW,
                _NOW,
            ),
        )
        scoped.execute(
            "INSERT INTO study_source_verifications(verification_id, account_id,"
            " conversation_id, run_id, assistant_message_id, purpose, status,"
            " query_summary, created_at) VALUES (?, ?, ?, ?, ?,"
            " 'explicit_request', 'success', ?, ?)",
            (
                ids["verification_id"],
                account_id,
                conversation_id,
                f"run-{tag}",
                f"assistant-{tag}",
                f"核实-{tag}",
                _NOW,
            ),
        )
    return ids


def _study_counts(harness: Harness, account_id: str) -> dict[str, int]:
    return {
        table: int(
            harness.database.scoped(account_id)
            .execute(
                f"SELECT COUNT(*) AS count FROM {table} WHERE account_id = ?",
                (account_id,),
            )
            .fetchone()["count"]
        )
        for table in _STUDY_TABLES
    }


def test_learning_data_export_is_isolated_per_account(tmp_path: Any) -> None:
    harness = Harness(tmp_path)
    ids1 = _seed_learning_data(harness, harness.acc1)
    ids2 = _seed_learning_data(harness, harness.acc2)

    _, payload = harness.export.export_data(harness.acc1)
    blob = payload if isinstance(payload, str) else payload.decode("utf-8")
    for key in ("record_id", "goal_id", "verification_id", "adoption_id"):
        assert ids1[key] in blob, key
    for key in ("record_id", "goal_id", "verification_id", "adoption_id"):
        assert ids2[key] not in blob, key
    # 学习模式类别真实进入导出负载。
    parsed = json.loads(blob)
    categories = parsed["categories"]
    for category in (
        "study_goals",
        "study_diagnosis",
        "study_record_adoptions",
        "study_source_verification",
    ):
        assert category in categories, category
        assert categories[category]["items"], category


def test_learning_data_deletion_removes_only_target_account(tmp_path: Any) -> None:
    harness = Harness(tmp_path)
    _seed_learning_data(harness, harness.acc1)
    _seed_learning_data(harness, harness.acc2)
    kept = _study_counts(harness, harness.acc2)

    result = harness.delete_account(harness.acc1)
    assert result.status == AccountDeletionStatus.COMPLETED
    removed = _study_counts(harness, harness.acc1)
    assert all(count == 0 for count in removed.values()), removed
    assert _study_counts(harness, harness.acc2) == kept


def test_learning_data_survives_backup_restore(tmp_path: Any) -> None:
    harness = Harness(tmp_path)
    _seed_learning_data(harness, harness.acc1)
    before = _study_counts(harness, harness.acc1)
    assert sum(before.values()) > 0

    _, backup = harness.backup.create_backup("学习数据恢复测试口令")
    with harness.database.transaction():
        for table in _STUDY_TABLES:
            harness.database.connection.execute(
                f"DELETE FROM {table} WHERE account_id = ?", (harness.acc1,)
            )
    assert all(count == 0 for count in _study_counts(harness, harness.acc1).values())

    result = harness.backup.restore_backup(
        "学习数据恢复测试口令", backup, confirmation="恢复"
    )
    assert result.ok, result.reasons
    assert _study_counts(harness, harness.acc1) == before
