"""贴吧服务对外状态、等待标识与模块预算。"""

from __future__ import annotations

from dataclasses import dataclass, field

from bridges.contracts.modules import ModuleQueryRecord
from bridges.tieba.contracts import TiebaResearchStatus

NODE_AUTH_WAIT = "tieba.auth"

TIEBA_NODE_LABELS: dict[str, str] = {
    "tieba.parse": "贴吧解析",
    "tieba.plan": "取证计划",
    "tieba.verify_official": "官方核验",
    "tieba.search": "贴吧搜索",
    "tieba.read": "贴吧读取",
    "tieba.synthesize": "贴吧归纳",
    "tieba.verify": "证据核验",
    NODE_AUTH_WAIT: "等待贴吧登录或安全验证",
}

TIEBA_MODULE_ID = "tieba"

WAIT_KIND_CLARIFICATION = "clarification"

WAIT_KIND_AUTH = "authentication"

WAIT_REASON_CLARIFICATION = "tieba_clarification"

WAIT_REASON_AUTH = "tieba_authentication"

TIEBA_TOTAL_BUDGET_MS = 300_000

_AUTH_WAIT_ERROR_CODES = frozenset(
    {"tieba_browser_auth_required", "tieba_browser_auth_interrupted"}
)


class TiebaModuleError(Exception):
    """模块内的稳定失败（定位到具体节点）。"""

    def __init__(self, node: str, code: str, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.node = node
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class TiebaRunOutcome:
    """一轮贴吧模块的收敛结果（父图据此写等待原因与判断终态）。"""

    status: TiebaResearchStatus
    wait_reason: str | None = None
    queries: list[ModuleQueryRecord] = field(default_factory=list)


def is_auth_wait_error(code: str | None) -> bool:
    return code in _AUTH_WAIT_ERROR_CODES
