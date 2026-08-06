"""Issue 43：workflows 运行状态持久化（workflow_runs）崩溃恢复测试。

验收标准：进程重建后运行状态可从 SQLite 恢复——新 WorkflowService 实例
从 workflow_runs 表恢复进行中的运行（含节点进度、人工待办、运行锁、
状态机位置），恢复后可继续推进。
"""

from __future__ import annotations

from bridges.contracts.workflows import (
    ArtifactTrustStatus,
    NodeStatus,
    WorkflowRunStatus,
    WorkOrder,
)
from bridges.storage.database import BridgesDatabase
from bridges.workflows import WorkflowService


def _fresh_service(database: BridgesDatabase) -> WorkflowService:
    svc = WorkflowService(database=database)
    svc.register_workflow(
        name="demo_lesson",
        version="1",
        nodes=[
            {"node_id": "compile_context", "node_name": "编译上下文", "human_gate": False},
            {"node_id": "review_gate", "node_name": "人工复核", "human_gate": True},
            {"node_id": "produce_output", "node_name": "生成产物", "human_gate": False},
        ],
        terminal_states=[
            WorkflowRunStatus.SUCCEEDED,
            WorkflowRunStatus.BLOCKED,
            WorkflowRunStatus.CANCELLED,
        ],
    )
    return svc


def _order(project_id: str) -> WorkOrder:
    return WorkOrder(
        workflow_name="demo_lesson",
        workflow_version="1",
        project_id=project_id,
        objective="为大学生解释贝尔不等式",
        success_criteria="生成一份带引用和事实锁的科普草稿",
        risk_statement="涉及量子基础解释，可能产生过度简化",
    )


def _db(tmp_path) -> BridgesDatabase:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    return database


def test_run_state_recovered_after_process_restart(tmp_path) -> None:
    """进程重建：进行中的运行（WAITING_HUMAN + 人工待办）从 SQLite 恢复。"""
    database = _db(tmp_path)
    svc = _fresh_service(database)
    projection = svc.submit_work_order(account_id="alice", order=_order("project-1"))
    confirmed = svc.confirm_work_order(account_id="alice", run_id=projection.run_id)
    # 走到第二节点（human gate）：运行停在 WAITING_HUMAN，有人工待办。
    assert confirmed.run_status == WorkflowRunStatus.RUNNING
    assert confirmed.current_node_id == "compile_context"
    svc.advance_run(account_id="alice", run_id=projection.run_id)
    waiting = svc.get_run(account_id="alice", run_id=projection.run_id)
    assert waiting.run_status == WorkflowRunStatus.WAITING_HUMAN
    assert len(waiting.human_todos) == 1
    todo_id = waiting.human_todos[0].todo_id

    # 模拟进程崩溃：旧实例丢弃，新实例从同一数据库重建。
    svc2 = _fresh_service(database)
    recovered = svc2.get_run(account_id="alice", run_id=projection.run_id)
    assert recovered.run_status == WorkflowRunStatus.WAITING_HUMAN
    assert recovered.current_node_id == "review_gate"
    assert recovered.workflow_name == "demo_lesson"
    assert recovered.workflow_version == "1"
    assert recovered.objective == "为大学生解释贝尔不等式"
    assert recovered.artifact_trust_status == ArtifactTrustStatus.DRAFT
    assert [node.status for node in recovered.nodes] == [
        NodeStatus.COMPLETED,
        NodeStatus.PENDING,
        NodeStatus.PENDING,
    ]
    # 恢复后可继续推进（人工待办仍可解析）。
    resolved = svc2.resolve_human_todo(
        account_id="alice", run_id=projection.run_id, todo_id=todo_id, resolution="确认"
    )
    assert resolved.run_status == WorkflowRunStatus.RUNNING
    assert resolved.current_node_id == "produce_output"


def test_restart_recovers_blocked_run_with_failure_reason(tmp_path) -> None:
    """进程重建：BLOCKED 运行（模型调用失败）的失败原因可恢复。"""
    database = _db(tmp_path)

    class _BlockingGateway:
        def invoke(self, **kwargs):
            # 无运行锁（模型网关缺失能力）：advance_run 视为 BLOCKED。
            return type("Result", (), {"lock": None})()

    svc = WorkflowService(database=database, model_gateway=_BlockingGateway())  # type: ignore[arg-type]
    svc.register_workflow(
        name="demo_lesson",
        version="1",
        nodes=[
            {"node_id": "compile_context", "node_name": "编译上下文", "capability_name": "cap"},
        ],
        terminal_states=[WorkflowRunStatus.BLOCKED, WorkflowRunStatus.CANCELLED],
    )
    projection = svc.submit_work_order(account_id="alice", order=_order("project-1"))
    svc.confirm_work_order(account_id="alice", run_id=projection.run_id)
    blocked = svc.advance_run(account_id="alice", run_id=projection.run_id)
    assert blocked.run_status == WorkflowRunStatus.BLOCKED

    svc2 = WorkflowService(database=database, model_gateway=_BlockingGateway())  # type: ignore[arg-type]
    recovered = svc2.get_run(account_id="alice", run_id=projection.run_id)
    assert recovered.run_status == WorkflowRunStatus.BLOCKED
    assert recovered.nodes[0].status == NodeStatus.FAILED
    assert recovered.nodes[0].failure_reason == "模型调用失败"
    assert recovered.run_ended_at is not None


def test_draft_and_cancelled_runs_recovered(tmp_path) -> None:
    """进程重建：DRAFT 与 CANCELLED 运行同样从磁盘恢复。"""
    database = _db(tmp_path)
    svc = _fresh_service(database)
    draft = svc.submit_work_order(account_id="alice", order=_order("project-1"))
    cancelled = svc.submit_work_order(account_id="bob", order=_order("project-2"))
    svc.cancel_run(account_id="bob", run_id=cancelled.run_id, reason="用户放弃")

    svc2 = _fresh_service(database)
    assert svc2.get_run("alice", draft.run_id).run_status == WorkflowRunStatus.DRAFT
    recovered_cancel = svc2.get_run("bob", cancelled.run_id)
    assert recovered_cancel.run_status == WorkflowRunStatus.CANCELLED
    assert recovered_cancel.cancel_reason == "用户放弃"


def test_run_state_persisted_after_each_mutation(tmp_path) -> None:
    """每次状态变更即时落盘：不依赖进程优雅退出。"""
    database = _db(tmp_path)
    svc = _fresh_service(database)
    projection = svc.submit_work_order(account_id="alice", order=_order("project-1"))
    rows = database.connection.execute(
        "SELECT COUNT(*) AS count FROM workflow_runs WHERE run_id = ?",
        (projection.run_id,),
    ).fetchone()
    assert int(rows["count"]) == 1
    svc.confirm_work_order(account_id="alice", run_id=projection.run_id)
    row = database.connection.execute(
        "SELECT status FROM workflow_runs WHERE run_id = ?",
        (projection.run_id,),
    ).fetchone()
    assert row["status"] == WorkflowRunStatus.RUNNING.value


def test_persistence_optional_pure_memory(tmp_path) -> None:
    """未配置 database 时保持纯内存（既有调用方无感）。"""
    svc = WorkflowService()
    svc.register_workflow(
        name="demo_lesson",
        version="1",
        nodes=[{"node_id": "n1", "node_name": "节点一"}],
        terminal_states=[WorkflowRunStatus.SUCCEEDED],
    )
    projection = svc.submit_work_order(account_id="alice", order=_order("project-1"))
    confirmed = svc.confirm_work_order(account_id="alice", run_id=projection.run_id)
    assert confirmed.run_status == WorkflowRunStatus.RUNNING
    done = svc.advance_run(account_id="alice", run_id=projection.run_id)
    assert done.run_status == WorkflowRunStatus.SUCCEEDED
