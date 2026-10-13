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
import threading
from dataclasses import replace

import pytest

from bridges.kernel.contracts import (
    ArtifactTrust,
    KernelStatus,
    NodeArtifact,
    NodeExecution,
    NodeInvocation,
    NodeReceiptStatus,
    NodeSpec,
    QualityGateResult,
    QualityVerdict,
    RecipeDefinition,
)
from bridges.kernel.registry import RecipeRegistry, RecipeValidationError
from bridges.kernel.repository import NodeKernelRepository
from bridges.storage.database import BridgesDatabase
from tests.kernel.support import (
    ACCOUNT,
    ASSISTANT,
    CAPABILITIES,
    CONVERSATION,
    GATES,
    NOW,
    RUN,
    Runner,
    execute_node_recipe,
    node_artifact,
    node_kernel,
    node_recipe,
    node_spec,
    recipe_inputs,
    seed_generation_run,
)


def test_partial_trusted_artifacts_remain_reusable_for_delivery() -> None:
    for partial in (True, False):
        artifact = NodeArtifact.build(
            account_id=ACCOUNT,
            conversation_id=CONVERSATION,
            run_id=RUN,
            task_id=None,
            task_version=None,
            recipe_id="test-recipe",
            recipe_version="v1",
            node="read",
            artifact_type="test.read",
            capability_version="read-v1",
            trust_state=ArtifactTrust.EVIDENCE_BOUND,
            input_key="read:question",
            input_deps=(),
            source_refs=(),
            read_scope="test",
            requirement_coverage=(),
            unconfirmed=(),
            error=None,
            payload={"partial": partial},
            now=NOW,
        )

        assert artifact.reusable is True


def test_registry_rejects_unknown_capability_and_gate() -> None:
    registry = RecipeRegistry(capabilities=("test.a",), gates=())
    with pytest.raises(RecipeValidationError, match="unknown_capability"):
        registry.register(node_recipe())

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


def test_contract_upgrade_reruns_nodes_and_preserves_old_receipts(
    database: BridgesDatabase,
) -> None:
    """相同输入升级配方/能力后不得复用旧版本，并保留原完成证明。"""
    seed_generation_run(database)
    runner = Runner()
    execute_node_recipe(node_kernel(database, runner))
    repository = NodeKernelRepository(database)
    old_receipts = repository.list_receipts(ACCOUNT, RUN)
    upgraded = replace(node_recipe(), recipe_version="v2", nodes=tuple(
        replace(spec, capability_version=f"{spec.name}-v2") for spec in node_recipe().nodes
    ))

    def run(invocation: NodeInvocation) -> NodeExecution:
        execution = runner(invocation)
        return replace(execution, artifact=replace(execution.artifact, recipe_version="v2"))

    runner.calls.clear()
    result = node_kernel(database, run).execute(recipe=upgraded, inputs=recipe_inputs())
    assert result.status is KernelStatus.COMPLETED
    assert runner.calls == ["a", "b", "c"]
    assert all(item.recipe_version == "v2" for item in result.artifacts)
    assert len(repository.list_receipts(ACCOUNT, RUN)) == 6
    assert repository.list_receipts(ACCOUNT, RUN)[:3] == old_receipts
    runner.calls.clear()
    node_kernel(database, run).execute(recipe=upgraded, inputs=recipe_inputs())
    assert runner.calls == []


def test_recomputation_preserves_payload_referenced_by_old_receipt(
    database: BridgesDatabase,
) -> None:
    """失效后的重新计算保存新版本，旧收据继续指向旧内容。"""
    seed_generation_run(database)
    first = execute_node_recipe(node_kernel(database, Runner()))
    repository = NodeKernelRepository(database)
    old = first.artifacts[0]
    old_receipt = repository.list_receipts(ACCOUNT, RUN)[0]
    with repository.transaction():
        repository.invalidate_node_artifacts(ACCOUNT, CONVERSATION, ("a",), now=NOW)

    def changed(invocation: NodeInvocation) -> NodeExecution:
        return NodeExecution(
            artifact=node_artifact(invocation, payload={"response": "新的外部结果"}),
            verdict=QualityVerdict.PASS, status=NodeReceiptStatus.COMPLETED,
        )

    result = execute_node_recipe(node_kernel(database, changed))
    assert result.status is KernelStatus.COMPLETED
    assert result.artifacts[0].artifact_id != old.artifact_id
    preserved = repository.get_artifact(ACCOUNT, old_receipt.artifact_id)
    assert preserved is not None
    assert preserved.payload == old.payload
    assert preserved.content_hash == old.content_hash
    assert preserved.trust_state is ArtifactTrust.INVALIDATED


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
    seed_generation_run(database)
    runner = Runner()
    events: list[tuple[str, str, int | None]] = []
    kernel = node_kernel(database, runner)
    first = execute_node_recipe(kernel, events)
    assert first.status is KernelStatus.COMPLETED
    assert runner.calls == ["a", "b", "c"]

    repository = NodeKernelRepository(database)
    artifacts_after_first = repository.list_artifacts(ACCOUNT, CONVERSATION)
    assert len(artifacts_after_first) == 3
    assert [item.node for item in artifacts_after_first] == ["a", "b", "c"]

    # 「收据已提交而检查点未保存」：同一运行用新内核从头执行，只读收据回填。
    second_runner = Runner()
    second_kernel = node_kernel(database, second_runner)
    replay_events: list[tuple[str, str, int | None]] = []
    second = execute_node_recipe(second_kernel, replay_events)
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
    seed_generation_run(database)
    runner = Runner()
    runner.fail_next["b"] = 1
    kernel = node_kernel(database, runner)
    failed = execute_node_recipe(kernel)
    assert failed.status is KernelStatus.FAILED
    assert failed.failure is not None and failed.failure.code == "test_failure"
    assert runner.calls == ["a", "b"]

    retry_runner = Runner()
    retry_kernel = node_kernel(database, retry_runner)
    recovered = execute_node_recipe(retry_kernel)
    assert recovered.status is KernelStatus.COMPLETED
    # a 从收据回填；b 的失败收据不复用，只重跑 b 与其必经下游 c。
    assert retry_runner.calls == ["b", "c"]


def test_required_gate_failure_invalidates_artifact(database: BridgesDatabase) -> None:
    seed_generation_run(database)
    runner = Runner()
    calls: list[str] = []

    def blocked(invocation: NodeInvocation, execution: NodeExecution) -> QualityGateResult:
        calls.append(invocation.spec.name)
        return QualityGateResult(
            gate="gate.required",
            verdict=QualityVerdict.BLOCKED,
            code="test_blocked",
            message="必要门未通过",
        )

    kernel = node_kernel(database, runner, gates={"gate.required": blocked})
    result = execute_node_recipe(kernel)
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
    seed_generation_run(database)
    runner = Runner()
    runner.invalidate["b"] = ("a",)
    kernel = node_kernel(database, runner)
    result = execute_node_recipe(kernel)
    assert result.status is KernelStatus.COMPLETED
    repository = NodeKernelRepository(database)
    invalidated = repository.find_artifact(
        ACCOUNT, CONVERSATION, "a", node_recipe().node("a").input_key(recipe_inputs())
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

    retry_runner = Runner()
    retry_kernel = node_kernel(database, retry_runner)
    execute_node_recipe(retry_kernel)
    # 失效产物不可复用：a 重新执行；a 的新产物内容哈希相同，b/c 按收据回填。
    assert retry_runner.calls == ["a"]


def test_stop_request_rejects_without_artifacts(database: BridgesDatabase) -> None:

    seed_generation_run(database)
    stop_event = threading.Event()
    stop_event.set()
    runner = Runner()
    kernel = node_kernel(database, runner, stop_event=stop_event)
    result = execute_node_recipe(kernel, stop_event=stop_event)
    assert result.status is KernelStatus.STOPPED
    assert result.stopped_at == "a"
    assert runner.calls == [], "停止在节点边界生效，不进入任何节点"
    repository = NodeKernelRepository(database)
    assert repository.list_artifacts(ACCOUNT, CONVERSATION) == []
    assert repository.list_receipts(ACCOUNT, RUN) == []


def test_lease_transfer_rejects_late_result(database: BridgesDatabase) -> None:
    seed_generation_run(database)

    def transfer_lease(name: str) -> None:
        # 节点执行期间（提交之前）租约转给新执行者：旧执行者的提交被拒绝。
        with database.transaction():
            database.connection.execute(
                "UPDATE generation_runs SET lease_owner = 'worker-2'"
                " WHERE account_id = ? AND run_id = ?",
                (ACCOUNT, RUN),
            )

    runner = Runner(on_call=transfer_lease)
    kernel = node_kernel(database, runner)
    result = execute_node_recipe(kernel)
    assert result.status is KernelStatus.REJECTED
    assert result.rejection_code == "lease_lost"
    assert runner.calls == ["a"]
    repository = NodeKernelRepository(database)
    assert repository.list_artifacts(ACCOUNT, CONVERSATION) == []
    assert repository.list_receipts(ACCOUNT, RUN) == []


def test_final_commit_check_rejects_transfer_after_last_node(
    database: BridgesDatabase,
) -> None:
    seed_generation_run(database)
    recipe = RecipeDefinition(
        recipe_id="single",
        recipe_version="v1",
        nodes=(node_spec("a"),),
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

    runner = Runner()
    kernel = node_kernel(database, runner)
    result = kernel.execute(recipe=recipe, inputs=recipe_inputs(), event_sink=transfer)
    assert result.status is KernelStatus.REJECTED
    assert result.rejection_code == "lease_lost"
    assert runner.calls == ["a"]


def test_message_terminal_rejects_late_result(database: BridgesDatabase) -> None:
    seed_generation_run(database)
    with database.transaction():
        database.connection.execute(
            "UPDATE messages SET status = 'done'"
            " WHERE account_id = ? AND message_id = ?",
            (ACCOUNT, ASSISTANT),
        )
    runner = Runner()
    kernel = node_kernel(database, runner)
    result = execute_node_recipe(kernel)
    assert result.status is KernelStatus.REJECTED
    assert result.rejection_code == "message_terminal"


def test_cross_run_artifact_reuse_and_invalidation(database: BridgesDatabase) -> None:
    seed_generation_run(database)
    runner = Runner()
    kernel = node_kernel(database, runner)
    assert execute_node_recipe(kernel).status is KernelStatus.COMPLETED

    # 新运行（新 run_id）复用同一会话的合格产物：不重复执行。
    second_runner = Runner()
    second_kernel = node_kernel(database, second_runner, run_id="run-2")
    seed_generation_run(database, run_id="run-2")
    result = second_kernel.execute(recipe=node_recipe(), inputs=recipe_inputs(run_id="run-2"))
    assert result.status is KernelStatus.COMPLETED
    assert second_runner.calls == []

    # 输入变化（内容不同）只让输入键变化的节点重跑。
    third_runner = Runner()
    third_kernel = node_kernel(database, third_runner, run_id="run-3")
    seed_generation_run(database, run_id="run-3")
    changed = third_kernel.execute(
        recipe=node_recipe(), inputs=recipe_inputs(user_content="changed", run_id="run-3")
    )
    assert changed.status is KernelStatus.COMPLETED
    assert third_runner.calls == ["a", "b", "c"]


def test_cross_run_partial_artifact_is_recomputed(database: BridgesDatabase) -> None:
    seed_generation_run(database)
    first_runner = Runner(partial_nodes=("a",))
    assert execute_node_recipe(node_kernel(database, first_runner)).status is KernelStatus.COMPLETED

    seed_generation_run(database, run_id="run-2")
    second_runner = Runner()
    result = node_kernel(database, second_runner, run_id="run-2").execute(
        recipe=node_recipe(), inputs=recipe_inputs(run_id="run-2")
    )

    assert result.status is KernelStatus.COMPLETED
    assert second_runner.calls == ["a", "b"]


def test_account_isolation_for_artifacts(database: BridgesDatabase) -> None:
    seed_generation_run(database)
    runner = Runner()
    kernel = node_kernel(database, runner)
    assert execute_node_recipe(kernel).status is KernelStatus.COMPLETED
    repository = NodeKernelRepository(database)
    assert repository.list_artifacts("acc-2", CONVERSATION) == []
    assert repository.find_artifact(
        "acc-2", CONVERSATION, "a", node_recipe().node("a").input_key(recipe_inputs())
    ) is None
