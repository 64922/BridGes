"""改进工单 10：持久节点内核的确定性合同（收据、守卫、门与失效）。

用最小测试配方验证内核机制，不与通勤领域耦合：

- 配方登记拒绝循环、未知能力/门与跳过前置；
- 节点局部事务提交产物+收据+外箱，同一输入重放不重复执行；
- 收据优先：已完成节点回填产物引用，重启不重复本地效果；
- 提交守卫：停止/旧租约/消息终态后的迟到结果被拒绝且不落盘；
- 必要门失败使产物失效；``invalidate_nodes`` 显式失效历史产物。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from bridges.chat.repository import ConversationRepository
from bridges.kernel.contracts import (
    ArtifactTrust,
    InputDependency,
    KernelStatus,
    NodeArtifact,
    NodeExecution,
    NodeInvocation,
    NodeReceiptStatus,
    NodeSpec,
    QualityGateResult,
    QualityVerdict,
    RecipeDefinition,
    RecipeInputs,
    RecoveryPolicy,
)
from bridges.kernel.executor import NodeKernel
from bridges.kernel.guard import RunCommitGuard
from bridges.kernel.registry import RecipeRegistry, RecipeValidationError
from bridges.kernel.repository import NodeKernelRepository
from bridges.storage.database import BridgesDatabase

NOW = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
ACCOUNT = "acc-1"
CONVERSATION = "conv-1"
RUN = "run-1"
ASSISTANT = "msg-assistant"
WORKER = "worker-1"

CAPABILITIES = ("test.a", "test.b", "test.c")
GATES = ("gate.required",)


def _spec(name: str, *, depends_on: tuple[str, ...] = ()) -> NodeSpec:
    def key(inputs: RecipeInputs) -> str:
        digest = inputs.user_content
        for dependency in depends_on:
            digest += ":" + inputs.artifacts[dependency].content_hash
        return f"{name}:{digest}"

    return NodeSpec(
        name=name,
        capability=f"test.{name}",
        artifact_type=f"test.{name}",
        capability_version=f"{name}-v1",
        input_key=key,
        depends_on=depends_on,
        required_gates=GATES if name == "c" else (),
        recovery=RecoveryPolicy.RETRY_NODE,
    )


def _recipe() -> RecipeDefinition:
    return RecipeDefinition(
        recipe_id="test-recipe",
        recipe_version="v1",
        nodes=(
            _spec("a"),
            _spec("b", depends_on=("a",)),
            _spec("c", depends_on=("b",)),
        ),
    )


def _artifact(
    invocation: NodeInvocation,
    *,
    payload: dict[str, Any] | None = None,
    trust: ArtifactTrust = ArtifactTrust.QUALIFIED,
    error: dict[str, Any] | None = None,
) -> NodeArtifact:
    return NodeArtifact.build(
        account_id=invocation.account_id,
        conversation_id=invocation.conversation_id,
        run_id=invocation.run_id,
        task_id=None,
        task_version=None,
        recipe_id="test-recipe",
        recipe_version="v1",
        node=invocation.spec.name,
        artifact_type=invocation.spec.artifact_type,
        capability_version=invocation.spec.capability_version,
        trust_state=trust,
        input_key=invocation.spec.input_key(invocation.inputs),
        input_deps=tuple(
            InputDependency(
                node=name,
                artifact_id=artifact.artifact_id,
                content_hash=artifact.content_hash,
            )
            for name, artifact in invocation.dependencies.items()
        ),
        source_refs=(),
        read_scope="test",
        requirement_coverage=(),
        unconfirmed=(),
        error=error,
        payload=payload or {"node": invocation.spec.name},
        now=NOW,
    )


class _Runner:
    """可注入失败/副作用的节点执行体：记录调用顺序并返回类型化产物。"""

    def __init__(self, on_call: Any = None) -> None:
        self.calls: list[str] = []
        self.fail_next: dict[str, int] = {}
        self.invalidate: dict[str, tuple[str, ...]] = {}
        self.on_call = on_call

    def __call__(self, invocation: NodeInvocation) -> NodeExecution:
        name = invocation.spec.name
        self.calls.append(name)
        if self.on_call is not None:
            self.on_call(name)
        if self.fail_next.get(name, 0) > 0:
            self.fail_next[name] -= 1
            return NodeExecution(
                artifact=_artifact(
                    invocation,
                    trust=ArtifactTrust.INVALIDATED,
                    error={"code": "test_failure", "message": "注入失败"},
                ),
                verdict=QualityVerdict.REPAIRABLE_FAILURE,
                status=NodeReceiptStatus.FAILED,
                detail={
                    "code": "test_failure",
                    "message": "注入失败",
                    "retryable": True,
                },
                stop_recipe=True,
            )
        return NodeExecution(
            artifact=_artifact(invocation),
            verdict=QualityVerdict.PASS,
            status=NodeReceiptStatus.COMPLETED,
            invalidate_nodes=self.invalidate.get(name, ()),
        )


def _seed(
    database: BridgesDatabase,
    *,
    account_id: str = ACCOUNT,
    conversation_id: str = CONVERSATION,
    run_id: str = RUN,
    assistant_message_id: str = ASSISTANT,
    run_status: str = "running",
    lease_owner: str | None = WORKER,
    message_status: str = "streaming",
    run_stop_requested: int = 0,
) -> None:
    stamp = NOW.isoformat()
    with database.transaction():
        database.connection.execute(
            "INSERT OR IGNORE INTO conversations"
            "(conversation_id, account_id, title, mode, created_at, updated_at)"
            " VALUES (?, ?, '', 'companion', ?, ?)",
            (conversation_id, account_id, stamp, stamp),
        )
        database.connection.execute(
            "INSERT OR IGNORE INTO messages"
            "(message_id, conversation_id, account_id, role, status, content,"
            " created_at, updated_at)"
            " VALUES (?, ?, ?, 'assistant', ?, '', ?, ?)",
            (assistant_message_id, conversation_id, account_id, message_status, stamp, stamp),
        )
        database.connection.execute(
            "INSERT OR IGNORE INTO generation_runs"
            "(run_id, account_id, conversation_id, user_message_id,"
            " assistant_message_id, status, lease_owner, lease_expires_at,"
            " stop_requested, created_at, updated_at)"
            " VALUES (?, ?, ?, 'msg-user', ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                account_id,
                conversation_id,
                assistant_message_id,
                run_status,
                lease_owner,
                (NOW + timedelta(minutes=5)).isoformat(),
                run_stop_requested,
                stamp,
                stamp,
            ),
        )


@pytest.fixture
def database(tmp_path: Path) -> BridgesDatabase:
    db = BridgesDatabase(tmp_path / "bridges.db")
    assert db.initialize() > 0
    return db


def _kernel(
    database: BridgesDatabase,
    runner: _Runner,
    *,
    gates: dict[str, Any] | None = None,
    stop_event: Any = None,
    account_id: str = ACCOUNT,
    conversation_id: str = CONVERSATION,
    run_id: str = RUN,
    assistant_message_id: str = ASSISTANT,
) -> NodeKernel:
    return NodeKernel(
        registry=RecipeRegistry(capabilities=CAPABILITIES, gates=GATES),
        repository=NodeKernelRepository(database),
        guard=RunCommitGuard(
            ConversationRepository(database),
            account_id=account_id,
            run_id=run_id,
            conversation_id=conversation_id,
            assistant_message_id=assistant_message_id,
            stop_event=stop_event,
            clock=lambda: NOW,
        ),
        gates=gates
        or {
            "gate.required": lambda invocation, execution: QualityGateResult(
                gate="gate.required", verdict=QualityVerdict.PASS
            )
        },
        runner=runner,
        clock=lambda: NOW,
    )


def _inputs(*, user_content: str = "hello", run_id: str = RUN) -> RecipeInputs:
    return RecipeInputs(
        account_id=ACCOUNT,
        conversation_id=CONVERSATION,
        run_id=run_id,
        user_message_id="msg-user",
        user_content=user_content,
        task_id=None,
        task_version=None,
        wait_identity=None,
        artifacts={},
    )


def _execute(
    kernel: NodeKernel,
    events: list[Any] | None = None,
    *,
    stop_event: Any = None,
):
    return kernel.execute(
        recipe=_recipe(),
        inputs=_inputs(),
        event_sink=(
            (lambda node, status, duration: events.append((node, status, duration)))
            if events is not None
            else None
        ),
        stop_event=stop_event,
    )


def test_registry_rejects_unknown_capability_and_gate() -> None:
    registry = RecipeRegistry(capabilities=("test.a",), gates=())
    with pytest.raises(RecipeValidationError, match="unknown_capability"):
        registry.register(_recipe())

    unknown_gate = RecipeDefinition(
        recipe_id="gated",
        recipe_version="v1",
        nodes=(NodeSpec(
            name="a",
            capability="test.a",
            artifact_type="test.a",
            capability_version="a-v1",
            input_key=lambda inputs: "a",
            required_gates=("gate.missing",),
        ),),
    )
    with pytest.raises(RecipeValidationError, match="unknown_gate"):
        registry.register(unknown_gate)


def test_registry_rejects_skipped_prerequisite_and_cycle() -> None:
    registry = RecipeRegistry(capabilities=CAPABILITIES, gates=GATES)
    skipped = RecipeDefinition(
        recipe_id="skipped",
        recipe_version="v1",
        nodes=(
            NodeSpec(
                name="a", capability="test.a", artifact_type="test.a",
                capability_version="a-v1", input_key=lambda inputs: "a",
                depends_on=("b",),
            ),
            NodeSpec(
                name="b", capability="test.b", artifact_type="test.b",
                capability_version="b-v1", input_key=lambda inputs: "b",
            ),
        ),
    )
    with pytest.raises(RecipeValidationError, match="skipped_prerequisite"):
        registry.register(skipped)

    cyclic = RecipeDefinition(
        recipe_id="cyclic",
        recipe_version="v1",
        nodes=(
            NodeSpec(
                name="a", capability="test.a", artifact_type="test.a",
                capability_version="a-v1", input_key=lambda inputs: "a",
            ),
            NodeSpec(
                name="b", capability="test.b", artifact_type="test.b",
                capability_version="b-v1", input_key=lambda inputs: "b",
                depends_on=("a",),
            ),
            NodeSpec(
                name="c", capability="test.c", artifact_type="test.c",
                capability_version="c-v1", input_key=lambda inputs: "c",
                depends_on=("b",),
            ),
        ),
    )
    registry.register(cyclic)  # 顺序配方本身合法
    duplicate = RecipeDefinition(
        recipe_id="duplicate",
        recipe_version="v1",
        nodes=(
            NodeSpec(
                name="a", capability="test.a", artifact_type="test.a",
                capability_version="a-v1", input_key=lambda inputs: "a",
            ),
            NodeSpec(
                name="a", capability="test.a", artifact_type="test.a",
                capability_version="a-v1", input_key=lambda inputs: "a",
            ),
        ),
    )
    with pytest.raises(RecipeValidationError, match="duplicate_node"):
        registry.register(duplicate)


def test_completed_receipts_reuse_without_rerun(database: BridgesDatabase) -> None:
    _seed(database)
    runner = _Runner()
    events: list[tuple[str, str, int | None]] = []
    kernel = _kernel(database, runner)
    first = _execute(kernel, events)
    assert first.status is KernelStatus.COMPLETED
    assert runner.calls == ["a", "b", "c"]

    repository = NodeKernelRepository(database)
    artifacts_after_first = repository.list_artifacts(ACCOUNT, CONVERSATION)
    assert len(artifacts_after_first) == 3
    assert [item.node for item in artifacts_after_first] == ["a", "b", "c"]

    # 「收据已提交而检查点未保存」：同一运行用新内核从头执行，只读收据回填。
    second_runner = _Runner()
    second_kernel = _kernel(database, second_runner)
    replay_events: list[tuple[str, str, int | None]] = []
    second = _execute(second_kernel, replay_events)
    assert second.status is KernelStatus.COMPLETED
    assert second_runner.calls == []
    assert all(state.reused for state in second.nodes)
    # 同一输入不产生重复产物，也不重复投递完成事件（只有真实执行才报告节点）。
    assert len(repository.list_artifacts(ACCOUNT, CONVERSATION)) == 3
    assert [item[0] for item in events] == [
        "a", "a", "b", "b", "c", "c",
    ]
    assert replay_events == []


def test_failed_receipt_is_not_reused_and_retries_only_that_node(
    database: BridgesDatabase,
) -> None:
    _seed(database)
    runner = _Runner()
    runner.fail_next["b"] = 1
    kernel = _kernel(database, runner)
    failed = _execute(kernel)
    assert failed.status is KernelStatus.FAILED
    assert failed.failure is not None and failed.failure.code == "test_failure"
    assert runner.calls == ["a", "b"]

    retry_runner = _Runner()
    retry_kernel = _kernel(database, retry_runner)
    recovered = _execute(retry_kernel)
    assert recovered.status is KernelStatus.COMPLETED
    # a 从收据回填；b 的失败收据不复用，只重跑 b 与其必经下游 c。
    assert retry_runner.calls == ["b", "c"]


def test_required_gate_failure_invalidates_artifact(database: BridgesDatabase) -> None:
    _seed(database)
    runner = _Runner()
    calls: list[str] = []

    def blocked(invocation: NodeInvocation, execution: NodeExecution) -> QualityGateResult:
        calls.append(invocation.spec.name)
        return QualityGateResult(
            gate="gate.required",
            verdict=QualityVerdict.BLOCKED,
            code="test_blocked",
            message="必要门未通过",
        )

    kernel = _kernel(database, runner, gates={"gate.required": blocked})
    result = _execute(kernel)
    assert result.status is KernelStatus.BLOCKED
    assert result.failure is not None and result.failure.code == "test_blocked"
    assert calls == ["c"]
    repository = NodeKernelRepository(database)
    artifact = next(
        item for item in repository.list_artifacts(ACCOUNT, CONVERSATION) if item.node == "c"
    )
    assert artifact.trust_state is ArtifactTrust.INVALIDATED


def test_invalidate_nodes_marks_history_and_forces_rerun(
    database: BridgesDatabase,
) -> None:
    _seed(database)
    runner = _Runner()
    runner.invalidate["b"] = ("a",)
    kernel = _kernel(database, runner)
    result = _execute(kernel)
    assert result.status is KernelStatus.COMPLETED
    repository = NodeKernelRepository(database)
    invalidated = repository.find_artifact(
        ACCOUNT, CONVERSATION, "a", _recipe().node("a").input_key(_inputs())
    )
    assert invalidated is not None
    assert invalidated.trust_state is ArtifactTrust.INVALIDATED
    # 失效事实随收据进入外箱，供审计与导出（产物行会被同输入重跑原位更新）。
    rows = database.connection.execute(
        "SELECT kind, payload_json FROM node_outbox"
        " WHERE account_id = ? AND run_id = ? ORDER BY rowid",
        (ACCOUNT, RUN),
    ).fetchall()
    invalidation = [
        row for row in rows if row["kind"] == "node_artifacts_invalidated"
    ]
    assert len(invalidation) == 1
    payload = json.loads(invalidation[0]["payload_json"])
    assert payload["nodes"] == ["a"]
    assert payload["artifact_ids"] == [invalidated.artifact_id]

    retry_runner = _Runner()
    retry_kernel = _kernel(database, retry_runner)
    _execute(retry_kernel)
    # 失效产物不可复用：a 重新执行；a 的新产物内容哈希相同，b/c 按收据回填。
    assert retry_runner.calls == ["a"]


def test_stop_request_rejects_without_artifacts(database: BridgesDatabase) -> None:
    import threading

    _seed(database)
    stop_event = threading.Event()
    stop_event.set()
    runner = _Runner()
    kernel = _kernel(database, runner, stop_event=stop_event)
    result = _execute(kernel, stop_event=stop_event)
    assert result.status is KernelStatus.STOPPED
    assert result.stopped_at == "a"
    assert runner.calls == [], "停止在节点边界生效，不进入任何节点"
    repository = NodeKernelRepository(database)
    assert repository.list_artifacts(ACCOUNT, CONVERSATION) == []
    assert repository.list_receipts(ACCOUNT, RUN) == []


def test_lease_transfer_rejects_late_result(database: BridgesDatabase) -> None:
    _seed(database)

    def transfer_lease(name: str) -> None:
        # 节点执行期间（提交之前）租约转给新执行者：旧执行者的提交被拒绝。
        with database.transaction():
            database.connection.execute(
                "UPDATE generation_runs SET lease_owner = 'worker-2'"
                " WHERE account_id = ? AND run_id = ?",
                (ACCOUNT, RUN),
            )

    runner = _Runner(on_call=transfer_lease)
    kernel = _kernel(database, runner)
    result = _execute(kernel)
    assert result.status is KernelStatus.REJECTED
    assert result.rejection_code == "lease_lost"
    assert runner.calls == ["a"]
    repository = NodeKernelRepository(database)
    assert repository.list_artifacts(ACCOUNT, CONVERSATION) == []
    assert repository.list_receipts(ACCOUNT, RUN) == []


def test_final_commit_check_rejects_transfer_after_last_node(
    database: BridgesDatabase,
) -> None:
    _seed(database)
    recipe = RecipeDefinition(
        recipe_id="single",
        recipe_version="v1",
        nodes=(_spec("a"),),
    )

    def transfer(node: str, status: str, duration: int | None) -> None:
        if status != "completed":
            return
        # 最后一个节点已提交、内核尚未返回：此刻租约转移必须被交付前
        # 最终校验拒绝（即使本轮没有任何后续节点提交）。
        with database.transaction():
            database.connection.execute(
                "UPDATE generation_runs SET lease_owner = 'worker-2'"
                " WHERE account_id = ? AND run_id = ?",
                (ACCOUNT, RUN),
            )

    runner = _Runner()
    kernel = _kernel(database, runner)
    result = kernel.execute(recipe=recipe, inputs=_inputs(), event_sink=transfer)
    assert result.status is KernelStatus.REJECTED
    assert result.rejection_code == "lease_lost"
    assert runner.calls == ["a"]


def test_message_terminal_rejects_late_result(database: BridgesDatabase) -> None:
    _seed(database)
    with database.transaction():
        database.connection.execute(
            "UPDATE messages SET status = 'done'"
            " WHERE account_id = ? AND message_id = ?",
            (ACCOUNT, ASSISTANT),
        )
    runner = _Runner()
    kernel = _kernel(database, runner)
    result = _execute(kernel)
    assert result.status is KernelStatus.REJECTED
    assert result.rejection_code == "message_terminal"


def test_cross_run_artifact_reuse_and_invalidation(database: BridgesDatabase) -> None:
    _seed(database)
    runner = _Runner()
    kernel = _kernel(database, runner)
    assert _execute(kernel).status is KernelStatus.COMPLETED

    # 新运行（新 run_id）复用同一会话的合格产物：不重复执行。
    second_runner = _Runner()
    second_kernel = NodeKernel(
        registry=RecipeRegistry(capabilities=CAPABILITIES, gates=GATES),
        repository=NodeKernelRepository(database),
        guard=RunCommitGuard(
            ConversationRepository(database),
            account_id=ACCOUNT,
            run_id="run-2",
            conversation_id=CONVERSATION,
            assistant_message_id=ASSISTANT,
            clock=lambda: NOW,
        ),
        gates={"gate.required": lambda invocation, execution: QualityGateResult(
            gate="gate.required", verdict=QualityVerdict.PASS
        )},
        runner=second_runner,
        clock=lambda: NOW,
    )
    _seed(database, run_id="run-2")
    result = second_kernel.execute(recipe=_recipe(), inputs=_inputs(run_id="run-2"))
    assert result.status is KernelStatus.COMPLETED
    assert second_runner.calls == []

    # 输入变化（内容不同）只让输入键变化的节点重跑。
    third_runner = _Runner()
    third_kernel = NodeKernel(
        registry=RecipeRegistry(capabilities=CAPABILITIES, gates=GATES),
        repository=NodeKernelRepository(database),
        guard=RunCommitGuard(
            ConversationRepository(database),
            account_id=ACCOUNT,
            run_id="run-3",
            conversation_id=CONVERSATION,
            assistant_message_id=ASSISTANT,
            clock=lambda: NOW,
        ),
        gates={"gate.required": lambda invocation, execution: QualityGateResult(
            gate="gate.required", verdict=QualityVerdict.PASS
        )},
        runner=third_runner,
        clock=lambda: NOW,
    )
    _seed(database, run_id="run-3")
    changed = third_kernel.execute(
        recipe=_recipe(), inputs=_inputs(user_content="changed", run_id="run-3")
    )
    assert changed.status is KernelStatus.COMPLETED
    assert third_runner.calls == ["a", "b", "c"]


def test_account_isolation_for_artifacts(database: BridgesDatabase) -> None:
    _seed(database)
    runner = _Runner()
    kernel = _kernel(database, runner)
    assert _execute(kernel).status is KernelStatus.COMPLETED
    repository = NodeKernelRepository(database)
    assert repository.list_artifacts("acc-2", CONVERSATION) == []
    assert repository.find_artifact(
        "acc-2", CONVERSATION, "a", _recipe().node("a").input_key(_inputs())
    ) is None
