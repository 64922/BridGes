"""持久节点执行内核：收据优先恢复、质量门与统一提交。

执行语义（与 ``docs/workflow/orchestration.md`` 第 8 节一致）：

- 每个节点在局部事务里提交产物、输入依赖/哈希、质量裁决、完成收据与
  待投递事件；检查点只保存引用；
- 恢复先读完成收据：已完成且输入一致则回填产物引用（不重复执行、
  不重复本地效果），未完成或输入变化则安全重试该节点；
- 提交前在同一事务内校验租约、停止、运行终态、任务版本与消息归属；
  旧租约或停止后的迟到结果被拒绝，不覆盖新状态；
- 只有配置内的节点与质量门可以执行；必要门未通过则节点产物失效并
  按声明的恢复分支收敛。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime

from bridges.kernel.contracts import (
    NodeArtifact,
    NodeExecution,
    NodeInvocation,
    QualityGateResult,
)
from bridges.kernel.executor_commits import _NodeKernelCommitsMixin
from bridges.kernel.executor_execution import _NodeKernelExecutionMixin
from bridges.kernel.executor_receipts import _NodeKernelReceiptsMixin
from bridges.kernel.guard import RunCommitGuard
from bridges.kernel.registry import RecipeRegistry
from bridges.kernel.repository import (
    NodeKernelRepository,
)

#: 质量门处理器：读取节点调用与该次执行结果，返回结构化裁决。
GateHandler = Callable[[NodeInvocation, NodeExecution], QualityGateResult]

#: 节点执行体：返回类型化产物、裁决与待投递事件。
NodeRunner = Callable[[NodeInvocation], NodeExecution]

#: 事件汇：把真实开始/完成的节点事件投递给进度持久化（SSE 回放）。
EventSink = Callable[[str, str, int | None], None]


class NodeKernel(
    _NodeKernelExecutionMixin,
    _NodeKernelCommitsMixin,
    _NodeKernelReceiptsMixin,
):
    """受控的持久节点执行器（配方 + 收据 + 质量门 + 提交守卫）。"""

    def __init__(
        self,
        *,
        registry: RecipeRegistry,
        repository: NodeKernelRepository,
        guard: RunCommitGuard,
        gates: Mapping[str, GateHandler],
        runner: NodeRunner,
        clock: Callable[[], datetime] | None = None,
        commit_effect: Callable[[NodeArtifact], None] | None = None,
    ) -> None:
        self._registry = registry
        self._repository = repository
        self._guard = guard
        self._gates = dict(gates)
        self._runner = runner
        self._commit_effect = commit_effect
        self._clock = clock or (lambda: datetime.now(UTC))


    # ------------------------------------------------------------------







    # ------------------------------------------------------------------










__all__ = ["EventSink", "GateHandler", "NodeKernel", "NodeRunner"]
