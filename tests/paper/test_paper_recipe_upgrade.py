"""论文合同升级不得复用旧词表路径产物，同版本仍应恢复复用。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from bridges.kernel.contracts import (
    ArtifactTrust,
    KernelResult,
    KernelStatus,
    NodeArtifact,
    NodeExecution,
    NodeInvocation,
    NodeReceiptStatus,
    QualityVerdict,
    RecipeDefinition,
    RecipeInputs,
)
from bridges.kernel.executor import NodeKernel
from bridges.kernel.guard import RunCommitGuard
from bridges.kernel.repository import NodeKernelRepository
from bridges.paper.artifact_codec import _digest
from bridges.paper.recipe import build_paper_recipe, paper_recipe_registry
from bridges.storage.database import BridgesDatabase

NOW = datetime(2026, 10, 11, tzinfo=UTC)


class _Scope:
    """稳定的授权读取面；测试使用真实持久节点仓库和执行内核。"""

    def get_generation_run(self, _account_id: str, _run_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            status="running", lease_owner="worker", stop_requested=False,
            lease_expires_at=NOW + timedelta(minutes=5),
        )

    def get_message(self, _account_id: str, _message_id: str) -> SimpleNamespace:
        return SimpleNamespace(conversation_id="conversation", status="streaming")


def _legacy_parse_key(inputs: RecipeInputs) -> str:
    """升级前实际输入键合同没有能力版本。"""
    return _digest({
        "content": inputs.user_content, "wait": inputs.wait_identity, "prior": inputs.prior_digest,
    })


@pytest.mark.parametrize("same_run", [False, True])
def test_upgrade_invalidates_persisted_legacy_nodes_and_preserves_new_reuse(
    tmp_path: Path, same_run: bool,
) -> None:
    current = build_paper_recipe()
    current = replace(current, nodes=current.nodes[:4])
    legacy = replace(
        current, recipe_version="paper-search-recipe-v2",
        nodes=tuple(
            replace(
                spec, capability_version=spec.capability_version.replace("-v2", "-v1"),
                input_key=_legacy_parse_key if index == 0 else spec.input_key,
            )
            for index, spec in enumerate(current.nodes)
        ),
    )
    inputs = RecipeInputs(
        account_id="account", conversation_id="conversation", run_id="old-run",
        user_message_id="user-message", user_content="找轴承故障诊断的论文",
        task_id=None, task_version=None, wait_identity=None, artifacts={}, prior_digest="same-day",
    )
    assert current.nodes[0].input_key(inputs) != legacy.nodes[0].input_key(inputs)
    calls: list[tuple[str, str]] = []
    database = BridgesDatabase(tmp_path / "upgrade.db")
    database.initialize()

    def execute(recipe: RecipeDefinition, request: RecipeInputs) -> KernelResult:
        def run(invocation: NodeInvocation) -> NodeExecution:
            calls.append((recipe.recipe_version, invocation.spec.name))
            artifact = NodeArtifact.build(
                account_id=request.account_id, conversation_id=request.conversation_id,
                run_id=request.run_id, task_id=None, task_version=None,
                recipe_id=recipe.recipe_id, recipe_version=recipe.recipe_version,
                node=invocation.spec.name, artifact_type=invocation.spec.artifact_type,
                capability_version=invocation.spec.capability_version,
                trust_state=ArtifactTrust.EVIDENCE_BOUND,
                input_key=invocation.spec.input_key(invocation.inputs), input_deps=(),
                source_refs=(), read_scope="none", requirement_coverage=(), unconfirmed=(),
                error=None, payload={"contract": recipe.recipe_version, "plans": []}, now=NOW,
            )
            return NodeExecution(
                artifact=artifact, verdict=QualityVerdict.PASS,
                status=NodeReceiptStatus.COMPLETED,
            )

        kernel = NodeKernel(
            registry=paper_recipe_registry(), repository=NodeKernelRepository(database),
            guard=RunCommitGuard(
                _Scope(), account_id=request.account_id, run_id=request.run_id,
                conversation_id=request.conversation_id, assistant_message_id="assistant",
                clock=lambda: NOW,
            ),
            gates={}, runner=run, clock=lambda: NOW,
        )
        return kernel.execute(recipe=recipe, inputs=request)

    try:
        first = execute(legacy, inputs)
        assert first.status is KernelStatus.COMPLETED
        assert len(NodeKernelRepository(database).list_artifacts("account", "conversation")) == 4
        upgraded_inputs = inputs if same_run else replace(inputs, run_id="new-run")
        upgraded = execute(current, upgraded_inputs)
        assert upgraded.status is KernelStatus.COMPLETED
        assert not any(node.reused for node in upgraded.nodes)
        assert len(calls) == 8, "新合同必须重新执行解析、规划、检索和筛选"
        assert all(item.recipe_version == current.recipe_version for item in upgraded.artifacts)

        restored = execute(current, replace(upgraded_inputs, run_id="restored-run"))
        assert restored.status is KernelStatus.COMPLETED
        assert all(node.reused for node in restored.nodes)
        assert len(calls) == 8, "同版本同内容的持久产物仍应跨运行恢复复用"
    finally:
        database.close()
