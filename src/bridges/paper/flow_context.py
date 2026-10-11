"""论文节点运行所需的不可变回合上下文。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from bridges.paper.source_types import PaperCandidate


@dataclass(frozen=True)
class PaperFlowContext:
    """构造执行体所需的不可变回合上下文（服务层提供）。"""

    prior_context: tuple[str, ...] = ()
    topic_hint: str | None = None
    task_conditions: tuple[Any, ...] = ()
    reading_goal: str | None = None
    comparison_requested: bool = False
    task_scope_used: bool = False
    reference_time: datetime | None = None
    citation_seed: PaperCandidate | None = None
    continuation_error: str | None = None
