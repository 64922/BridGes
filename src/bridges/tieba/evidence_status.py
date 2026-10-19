"""贴吧读取阻断错误分类与用户可见短因。"""

from __future__ import annotations

from bridges.tieba.contracts import ReadStatus, TiebaReadResult

ACCOUNT_DELETING_CODE = "tieba_browser_account_deleting"

AUTH_BLOCKED_CODES = frozenset(
    {
        "tieba_browser_auth_required",
        "tieba_browser_auth_interrupted",
        "tieba_browser_auth_unverified",
    }
)

BUDGET_BLOCKED_CODES = frozenset(
    {
        "run_budget_exhausted",
        "tieba_browser_work_time_exhausted",
        "tieba_browser_request_queue_full",
    }
)

READ_BLOCK_LABELS: dict[ReadStatus, str] = {
    ReadStatus.ACCESS_RESTRICTED: "访问受限，未绕过",
    ReadStatus.UNRECOGNIZED: "页面结构无法解析",
    ReadStatus.NOT_FOUND: "帖子不存在或已删除",
    ReadStatus.TIMEOUT: "读取超时",
    ReadStatus.ERROR: "读取失败",
    ReadStatus.CANCELLED: "已停止读取",
}


def account_deleting(code: str | None) -> bool:
    return code == ACCOUNT_DELETING_CODE


def auth_blocked(code: str | None) -> bool:
    return code in AUTH_BLOCKED_CODES


def budget_blocked(code: str | None) -> bool:
    return code in BUDGET_BLOCKED_CODES


def read_block_reason(result: TiebaReadResult) -> str:
    """读取失败或页面未声明吧名时的中文短因（页面不可读一类）。"""
    if result.forum_name is None and result.status in {ReadStatus.READ, ReadStatus.PARTIAL}:
        return "页面不可读：页面未声明所属贴吧"
    label = READ_BLOCK_LABELS.get(result.status, result.status.value)
    return f"页面不可读：{label}"
