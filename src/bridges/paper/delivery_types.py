"""论文模块交付状态、错误与结果合同。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from bridges.contracts.ai import ModelRunLock
from bridges.contracts.chat import ChatMessageStatus
from bridges.contracts.modules import ModuleDelivery, ModuleQueryRecord
from bridges.paper.contracts import PaperSearchProjection, PaperSearchStatus

if TYPE_CHECKING:
    pass


SEARCH_DEADLINE_SECONDS = 25.0


ENRICH_DEADLINE_SECONDS = 12.0


WAIT_KIND_CLARIFICATION = "clarification"


PAPER_MODULE_ID = "paper"


WAIT_REASON_CLARIFICATION = "paper_clarification"


class PaperModuleError(Exception):
    """论文模块失败：节点位置、稳定错误码、可操作中文说明与是否可重试。"""

    def __init__(
        self, node: str, code: str, message: str, *, retryable: bool = True
    ) -> None:
        super().__init__(message)
        self.node = node
        self.code = code
        self.message = message
        self.retryable = retryable


class PaperSupersededError(Exception):
    """迟到/失效结果：本轮不再写交付终态，交给当前执行者收尾。"""


@dataclass(frozen=True)
class PaperRunOutcome:
    """一轮论文模块的收敛结果（父图据此写等待原因与判断终态）。"""

    status: PaperSearchStatus
    wait_reason: str | None = None
    queries: list[ModuleQueryRecord] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)
    #: 工单 37 复合接缝：``defer_finalization`` 模式下返回的完整交付。
    delivery: ModuleDelivery | None = None


@dataclass
class _PreparedDelivery:
    """事务外准备好的交付内容（事务内只做守卫复核与原子写入）。"""

    projection: PaperSearchProjection
    content: str
    message_status: ChatMessageStatus
    lock: ModelRunLock | None = None
    failure: PaperModuleError | None = None
