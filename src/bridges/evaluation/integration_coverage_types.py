"""工单 43 覆盖对账的类型定义（数据模块与聚合模块共用）。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class CoverageStatus(StrEnum):
    """覆盖项的真实状态；只有 ``verified`` 表示该范围已开放。"""

    VERIFIED = "verified"
    MECHANISM = "mechanism"
    DEGRADED = "degraded"
    NOT_RELEASED = "not_released"
    GAP = "gap"


@dataclass(frozen=True)
class CoverageItem:
    """一条覆盖项：来源需求、负责票、证据锚点与真实状态。"""

    item_id: str
    requirement: str
    owner_tickets: tuple[int, ...]
    evidence: tuple[str, ...]
    status: CoverageStatus
    note: str = ""

    def __post_init__(self) -> None:
        if not self.item_id or not self.requirement:
            raise ValueError("覆盖项必须有标识与需求摘要。")
        if not self.owner_tickets:
            raise ValueError(f"{self.item_id} 必须登记负责票。")
        if not self.evidence:
            raise ValueError(f"{self.item_id} 必须登记至少一个证据锚点。")


__all__ = ["CoverageItem", "CoverageStatus"]
