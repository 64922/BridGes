"""节点内核与跨领域集成测试共用的运行样例。"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from bridges.chat.repository import ConversationRepository
from bridges.kernel.contracts import (
    ArtifactTrust,
    InputDependency,
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
from bridges.kernel.registry import RecipeRegistry
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


def recipe_inputs(
    *,
    user_content: str = "hello",
    run_id: str = RUN,
    account_id: str = ACCOUNT,
    conversation_id: str = CONVERSATION,
) -> RecipeInputs:
    return RecipeInputs(
        account_id=account_id,
        conversation_id=conversation_id,
        run_id=run_id,
        user_message_id="msg-user",
        user_content=user_content,
        task_id=None,
        task_version=None,
        wait_identity=None,
        artifacts={},
    )


def seed_generation_run(
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


def node_spec(name: str, *, depends_on: tuple[str, ...] = ()) -> NodeSpec:
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


def node_recipe() -> RecipeDefinition:
    return RecipeDefinition(
        recipe_id="test-recipe",
        recipe_version="v1",
        nodes=(
            node_spec("a"),
            node_spec("b", depends_on=("a",)),
            node_spec("c", depends_on=("b",)),
        ),
    )


def node_artifact(
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


class Runner:
    """可注入失败/副作用的节点执行体：记录调用顺序并返回类型化产物。"""

    def __init__(self, on_call: Any = None, partial_nodes: tuple[str, ...] = ()) -> None:
        self.calls: list[str] = []
        self.fail_next: dict[str, int] = {}
        self.invalidate: dict[str, tuple[str, ...]] = {}
        self.on_call = on_call
        self.partial_nodes = set(partial_nodes)

    def __call__(self, invocation: NodeInvocation) -> NodeExecution:
        name = invocation.spec.name
        self.calls.append(name)
        if self.on_call is not None:
            self.on_call(name)
        if self.fail_next.get(name, 0) > 0:
            self.fail_next[name] -= 1
            return NodeExecution(
                artifact=node_artifact(
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
            artifact=node_artifact(
                invocation,
                payload=(
                    {"node": name, "partial": True}
                    if name in self.partial_nodes
                    else None
                ),
            ),
            verdict=QualityVerdict.PASS,
            status=NodeReceiptStatus.COMPLETED,
            invalidate_nodes=self.invalidate.get(name, ()),
        )


def node_kernel(
    database: BridgesDatabase,
    runner: Any,
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


def execute_node_recipe(
    kernel: NodeKernel,
    events: list[Any] | None = None,
    *,
    stop_event: Any = None,
):
    return kernel.execute(
        recipe=node_recipe(),
        inputs=recipe_inputs(),
        event_sink=(
            (lambda node, status, duration: events.append((node, status, duration)))
            if events is not None
            else None
        ),
        stop_event=stop_event,
    )


def parallel_recipe(*, parallel_limit: int = 2) -> RecipeDefinition:
    return RecipeDefinition(
        recipe_id="test-recipe",
        recipe_version="v1",
        nodes=(
            node_spec("a"),
            replace(node_spec("b", depends_on=("a",)), parallel_group="search"),
            replace(node_spec("c", depends_on=("a",)), parallel_group="search"),
        ),
        parallel_limit=parallel_limit,
    )
