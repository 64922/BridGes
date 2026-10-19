"""同一任务内部分节点产物的安全续接选择。"""

from __future__ import annotations

from typing import Any, cast

from bridges.kernel.contracts import (
    ARTIFACT_SCHEMA_VERSION,
    NodeArtifact,
    NodeInvocation,
)
from bridges.tieba.recipe import (
    NODE_PARSE,
    NODE_PLAN,
    NODE_READ,
    NODE_SEARCH,
    TIEBA_RECIPE_ID,
    TIEBA_RECIPE_VERSION,
)


def resume_payload(
    repository: Any, invocation: NodeInvocation, node: str
) -> dict[str, Any] | None:
    """按输入键或同主题历史证据读取部分产物，供续作未完成检索/读取。

    同一运行内的精确输入键命中优先；跨运行（用户显式重试）则只接受
    任务版本、配方与能力版本一致，且解析/计划哈希未变的部分产物。
    完整产物会正常按收据/产物复用，不会走到这里。
    """
    if repository is None:
        return None
    input_key = invocation.spec.input_key(invocation.inputs)
    artifact = repository.find_artifact(
        invocation.account_id, invocation.conversation_id, node, input_key
    )
    if artifact is not None and _usable_partial(artifact, invocation):
        return dict(artifact.payload)
    if node not in {NODE_SEARCH, NODE_READ}:
        return None
    artifact = saved_resume_artifact(repository, invocation, node)
    if artifact is not None:
        return dict(artifact.payload)
    return None


def _usable_partial(artifact: NodeArtifact, invocation: NodeInvocation) -> bool:
    return (
        artifact.recipe_id == TIEBA_RECIPE_ID
        and artifact.recipe_version == TIEBA_RECIPE_VERSION
        and artifact.capability_version == invocation.spec.capability_version
        and artifact.artifact_type == invocation.spec.artifact_type
        and artifact.schema_version == ARTIFACT_SCHEMA_VERSION
        and artifact.task_id == invocation.inputs.task_id
        and artifact.task_version == invocation.inputs.task_version
        and artifact.payload.get("partial") is True
        and artifact.reusable
        and artifact.verify_hash()
    )


def saved_resume_artifact(
    repository: Any, invocation: NodeInvocation, node: str
) -> NodeArtifact | None:
    """从同主题历史部分产物中恢复检索/读取证据。

    只比较解析与计划内容哈希，因此认证等待或显式重试即使重建了上游
    产物，也能复用已确认页面；任务版本或配方能力变化则拒绝复用。
    """
    if repository is None:
        return None
    stable_dependencies = {
        dependency: invocation.dependencies.get(dependency)
        for dependency in (NODE_PARSE, NODE_PLAN)
    }
    if any(artifact is None for artifact in stable_dependencies.values()):
        return None
    expected_hashes = {
        dependency: artifact.content_hash
        for dependency, artifact in stable_dependencies.items()
        if artifact is not None
    }
    artifacts = repository.list_artifacts(
        invocation.account_id, invocation.conversation_id
    )
    for artifact in reversed(artifacts):
        if (
            artifact.node != node
            or artifact.task_id != invocation.inputs.task_id
            or artifact.task_version != invocation.inputs.task_version
            or not _usable_partial(artifact, invocation)
        ):
            continue
        dependency_hashes = {
            dependency.node: dependency.content_hash
            for dependency in artifact.input_deps
        }
        if all(
            dependency_hashes.get(dependency) == content_hash
            for dependency, content_hash in expected_hashes.items()
        ):
            return cast(NodeArtifact, artifact)
    return None
