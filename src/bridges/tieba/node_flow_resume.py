"""同一认证等待任务中部分节点产物的安全续接选择。"""

from __future__ import annotations

from typing import Any, cast

from bridges.kernel.contracts import NodeArtifact, NodeInvocation
from bridges.tieba.recipe import (
    NODE_PARSE,
    NODE_PLAN,
    NODE_READ,
    NODE_SEARCH,
    TIEBA_RECIPE_ID,
    TIEBA_RECIPE_VERSION,
)


def resume_payload(
    repository: Any, pending_wait: Any, invocation: NodeInvocation, node: str
) -> dict[str, Any] | None:
    """按同一输入键读取上一轮的部分产物，供续作未完成检索/读取。

    只回填与当前能力版本一致、且确实是部分完成的产物；完整产物会正常
    按收据/产物复用，不会走到这里。
    """
    if repository is None:
        return None
    input_key = invocation.spec.input_key(invocation.inputs)
    artifact = repository.find_artifact(
        invocation.account_id, invocation.conversation_id, node, input_key
    )
    if artifact is None:
        if node == NODE_SEARCH:
            artifact = auth_resume_artifact(repository, pending_wait, invocation, NODE_SEARCH)
        elif node == NODE_READ:
            artifact = auth_resume_artifact(repository, pending_wait, invocation, NODE_READ)
    if artifact is None or artifact.capability_version != invocation.spec.capability_version:
        return None
    if artifact.payload.get("partial") is not True:
        return None
    return dict(artifact.payload)


def auth_resume_artifact(
    repository: Any, pending_wait: Any, invocation: NodeInvocation, node: str
) -> NodeArtifact | None:
    """只从同一认证等待任务内恢复依赖解析和计划未变的部分产物。"""
    if getattr(pending_wait, "kind", None) != "authentication":
        return None
    if repository is None:
        return None
    stable_dependencies = {
        node: invocation.dependencies.get(node) for node in (NODE_PARSE, NODE_PLAN)
    }
    if any(artifact is None for artifact in stable_dependencies.values()):
        return None
    expected_hashes = {
        node: artifact.content_hash
        for node, artifact in stable_dependencies.items()
        if artifact is not None
    }
    artifacts = repository.list_artifacts(invocation.account_id, invocation.conversation_id)
    for artifact in reversed(artifacts):
        if (
            artifact.node != node
            or artifact.recipe_id != TIEBA_RECIPE_ID
            or artifact.recipe_version != TIEBA_RECIPE_VERSION
            or artifact.capability_version != invocation.spec.capability_version
            or artifact.artifact_type != invocation.spec.artifact_type
            or artifact.task_id != invocation.inputs.task_id
            or artifact.task_version != invocation.inputs.task_version
            or artifact.payload.get("partial") is not True
            or not artifact.verify_hash()
        ):
            continue
        dependency_hashes = {dep.node: dep.content_hash for dep in artifact.input_deps}
        if all(
            dependency_hashes.get(node) == content_hash
            for node, content_hash in expected_hashes.items()
        ):
            return cast(NodeArtifact, artifact)
    return None
