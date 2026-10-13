"""Internal state shared by the persistent node kernel method modules."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from bridges.kernel.contracts import (
    KernelResult,
    KernelStatus,
    NodeArtifact,
    NodeExecution,
    NodeInvocation,
    QualityGateResult,
)
from bridges.kernel.guard import RunCommitGuard
from bridges.kernel.registry import RecipeRegistry
from bridges.kernel.repository import NodeKernelRepository


class NodeKernelContract:
    """Typed kernel state; method lookup is completed by sibling mixins."""

    _registry: RecipeRegistry
    _repository: NodeKernelRepository
    _guard: RunCommitGuard
    _gates: Mapping[str, Callable[[NodeInvocation, NodeExecution], QualityGateResult]]
    _runner: Callable[[NodeInvocation], NodeExecution]
    _commit_effect: Callable[[NodeArtifact], None] | None
    _clock: Callable[[], datetime]

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(name)

    def _terminal_result(self, *args: Any, **kwargs: Any) -> KernelResult:
        raise NotImplementedError

    def _commit_execution(
        self, *args: Any, **kwargs: Any
    ) -> KernelResult | None:
        raise NotImplementedError

    def _run_with_gates(self, *args: Any, **kwargs: Any) -> NodeExecution:
        raise NotImplementedError

    def _status_for(self, *args: Any, **kwargs: Any) -> KernelStatus:
        raise NotImplementedError
