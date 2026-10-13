"""节点并行组的登记、执行顺序、上限和部分提交合同。"""

from __future__ import annotations

import threading
import time
from dataclasses import replace

import pytest

from bridges.kernel.contracts import (
    KernelStatus,
    NodeExecution,
    NodeInvocation,
    RecipeDefinition,
)
from bridges.kernel.registry import RecipeRegistry, RecipeValidationError
from bridges.storage.database import BridgesDatabase
from tests.kernel.support import (
    CAPABILITIES,
    GATES,
    Runner,
    node_kernel,
    node_spec,
    parallel_recipe,
    recipe_inputs,
    seed_generation_run,
)


def test_parallel_group_rejects_intra_group_dependency() -> None:
    registry = RecipeRegistry(capabilities=CAPABILITIES, gates=GATES)
    recipe = RecipeDefinition(
        recipe_id="bad-parallel",
        recipe_version="v1",
        nodes=(
            node_spec("a"),
            replace(node_spec("b"), parallel_group="search"),
            replace(node_spec("c", depends_on=("b",)), parallel_group="search"),
        ),
        parallel_limit=2,
    )
    with pytest.raises(RecipeValidationError, match="parallel_group_dependency"):
        registry.register(recipe)


def test_parallel_group_rejects_split_registration() -> None:
    registry = RecipeRegistry(capabilities=CAPABILITIES, gates=GATES)
    recipe = RecipeDefinition(
        recipe_id="split-parallel",
        recipe_version="v1",
        nodes=(
            replace(node_spec("a"), parallel_group="search"),
            node_spec("b", depends_on=("a",)),
            replace(node_spec("c", depends_on=("b",)), parallel_group="search"),
        ),
        parallel_limit=2,
    )
    with pytest.raises(RecipeValidationError, match="parallel_group_split"):
        registry.register(recipe)


def test_parallel_group_runs_concurrently_and_keeps_recipe_order(
    database: BridgesDatabase,
) -> None:
    """组内节点真实并发（两侧互相等待才能通过），提交顺序仍是配方顺序。"""
    seed_generation_run(database)
    barrier = threading.Barrier(2, timeout=5)
    overlap: list[str] = []
    lock = threading.Lock()

    def on_call(name: str) -> None:
        if name not in {"b", "c"}:
            return
        with lock:
            overlap.append(name)
        barrier.wait()

    runner = Runner(on_call=on_call)
    result = node_kernel(database, runner).execute(
        recipe=parallel_recipe(), inputs=recipe_inputs()
    )
    assert result.status is KernelStatus.COMPLETED
    assert sorted(overlap) == ["b", "c"]
    assert [state.node for state in result.nodes] == ["a", "b", "c"]
    assert result.delivery is not None and result.delivery.node == "c"


def test_parallel_group_enforces_limit(database: BridgesDatabase) -> None:
    """并行上限为 2 时三个互不依赖节点最多同时执行两个（分批执行完）。"""
    seed_generation_run(database)
    lock = threading.Lock()
    active = 0
    peak = 0

    class LimitRunner(Runner):
        def __call__(self, invocation: NodeInvocation) -> NodeExecution:
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            try:
                time.sleep(0.02)
                return super().__call__(invocation)
            finally:
                with lock:
                    active -= 1

    recipe = RecipeDefinition(
        recipe_id="limited-parallel",
        recipe_version="v1",
        nodes=(
            replace(node_spec("a"), parallel_group="search"),
            replace(node_spec("b"), parallel_group="search"),
            replace(node_spec("c"), parallel_group="search"),
        ),
        parallel_limit=2,
    )
    runner = LimitRunner()
    result = node_kernel(database, runner).execute(recipe=recipe, inputs=recipe_inputs())
    assert result.status is KernelStatus.COMPLETED
    assert peak == 2
    assert sorted(runner.calls) == ["a", "b", "c"]


def test_parallel_group_failure_keeps_sibling_artifact(
    database: BridgesDatabase,
) -> None:
    """一侧失败不抹掉另一侧：同组已提交的兄弟产物保留，下游不执行。"""
    seed_generation_run(database)
    runner = Runner()
    runner.fail_next["c"] = 1
    result = node_kernel(database, runner).execute(
        recipe=parallel_recipe(), inputs=recipe_inputs()
    )
    assert result.status is KernelStatus.FAILED
    assert result.failure is not None and result.failure.node == "c"
    assert runner.calls == ["a", "b", "c"]
    assert [artifact.node for artifact in result.artifacts] == ["a", "b", "c"]

    retry_runner = Runner()
    retried = node_kernel(database, retry_runner).execute(
        recipe=parallel_recipe(), inputs=recipe_inputs()
    )
    assert retried.status is KernelStatus.COMPLETED
    assert retry_runner.calls == ["c"]
