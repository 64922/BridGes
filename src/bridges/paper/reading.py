"""``paper.read``：按结论需要分层读取摘要或正文片段。

工单 24 的阅读合同：

- 先按标题/摘要/作者/年份筛选，只有重点候选才按本轮结论需要读取正文；
- 方法、实验、局限、复现断言必须有对应正文依据；只有摘要时只交付摘要级
  概述，并逐篇标注实际读取范围，绝不宣称精读；
- 全文读取是**可选登记适配器**：未接入或失败时如实降级为摘要级，不伪造
  读取结果，也不阻断其余候选。

``FullTextReader`` 是登记接缝：真实全文能力通过该协议接入后才会产生正文
证据；默认未装配时为纯摘要读取。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol, cast

from bridges.paper.screening import ScreenedCandidate
from bridges.paper.sources import PaperCandidate

#: 阅读目的：找候选 / 解释 / 比较。比较与解释才需要正文小节。
GOAL_CANDIDATES = "candidates"
GOAL_EXPLAIN = "explain"
GOAL_COMPARE = "compare"

#: 不同阅读目的需要的正文小节（英文规范名，正文证据按此归档）。
SECTIONS_BY_GOAL: dict[str, tuple[str, ...]] = {
    GOAL_CANDIDATES: (),
    GOAL_EXPLAIN: ("introduction", "methods", "conclusion"),
    GOAL_COMPARE: ("methods", "experiments", "limitations"),
}

#: 正文断言（方法/实验/局限/复现）所需的证据等级。
BODY_SECTION_LABELS: dict[str, str] = {
    "methods": "方法",
    "experiments": "实验",
    "limitations": "局限",
    "conclusion": "结论",
    "introduction": "引言",
}


@dataclass(frozen=True)
class PaperSection:
    """一段真实取得的正文小节。"""

    name: str
    text: str
    locator: str | None = None


@dataclass(frozen=True)
class PaperReading:
    """一篇论文的实际读取结果与范围。"""

    arxiv_id: str
    scope: str
    sections: tuple[PaperSection, ...] = ()
    notes: tuple[str, ...] = ()


class FullTextReader(Protocol):
    """登记的全文读取适配器：只接收最小参数，返回真实取得的小节。"""

    def read(
        self,
        candidate: PaperCandidate,
        *,
        sections: Sequence[str],
        deadline: float | None,
        stop_event: threading.Event | None,
    ) -> PaperReading: ...


@dataclass(frozen=True)
class ReadOutcome:
    """一轮读取结果：按 arxiv_id 归档的范围与证据。"""

    readings: dict[str, PaperReading] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    deep_read_count: int = 0


class ReadCoordinator:
    """按阅读目的与预算选择重点候选读取正文；失败只降级不阻断。"""

    def __init__(
        self,
        reader: FullTextReader | None = None,
        *,
        deep_read_max: int = 3,
        deadline_seconds: float = 12.0,
    ) -> None:
        self._reader = reader
        self._deep_read_max = max(0, deep_read_max)
        self._deadline_seconds = deadline_seconds

    @property
    def full_text_supported(self) -> bool:
        return self._reader is not None

    def read(
        self,
        screened: Sequence[ScreenedCandidate],
        *,
        goal: str,
        account_id: str,
        budget: object | None = None,
        stop_event: threading.Event | None = None,
    ) -> ReadOutcome:
        del account_id  # 读取适配器按候选最小参数工作，不接收账户正文。
        wanted = SECTIONS_BY_GOAL.get(goal, ())
        readings: dict[str, PaperReading] = {}
        selected = list(screened)
        if not wanted or self._reader is None:
            # 纯摘要读取：范围如实标为 abstract。
            for item in selected:
                readings[item.candidate.arxiv_id] = PaperReading(
                    arxiv_id=item.candidate.arxiv_id,
                    scope="abstract",
                )
            early_notes = []
            if not wanted:
                early_notes.append(
                    "本轮只做摘要级概述；方法/实验/局限断言需要正文依据，未作断言。"
                )
            else:
                early_notes.append(
                    "全文读取能力未接入，本轮只依据标题与摘要，未宣称精读。"
                )
            return ReadOutcome(readings=readings, notes=early_notes)

        budget_view = _as_budget(budget)
        deadline = time.monotonic() + self._deadline_seconds
        if budget_view is not None:
            deadline = min(deadline, time.monotonic() + budget_view.deadline_seconds())
        notes: list[str] = []
        deep_reads = 0
        for item in selected:
            candidate = item.candidate
            if deep_reads >= self._deep_read_max:
                break
            if stop_event is not None and stop_event.is_set():
                break
            call_key = f"paper.read:{candidate.arxiv_id}"
            if budget_view is not None and not budget_view.register_external(
                call_key, purpose="full_text_read"
            ):
                continue
            try:
                reading = self._reader.read(
                    candidate,
                    sections=wanted,
                    deadline=deadline,
                    stop_event=stop_event,
                )
            except Exception:  # noqa: BLE001 - 可选读取失败只降级，不阻断其余候选
                reading = PaperReading(
                    arxiv_id=candidate.arxiv_id,
                    scope="abstract",
                    notes=("全文读取失败，本篇降级为摘要级。",),
                )
            finally:
                if budget_view is not None:
                    budget_view.release_external(call_key, outcome_code="done")
            readings[candidate.arxiv_id] = reading
            if reading.scope in {"full_text", "partial"}:
                deep_reads += 1
        for item in selected:
            arxiv_id = item.candidate.arxiv_id
            if arxiv_id not in readings:
                readings[arxiv_id] = PaperReading(arxiv_id=arxiv_id, scope="abstract")
        if deep_reads:
            notes.append(
                f"已对 {deep_reads} 篇重点候选按本轮结论需要读取正文；"
                "其余为摘要级结果。"
            )
        elif selected:
            notes.append("没有候选取得正文，本轮为摘要级结果。")
        return ReadOutcome(readings=readings, notes=notes, deep_read_count=deep_reads)


class _BudgetView(Protocol):
    """运行预算视图的最小协议（避免导入 chat 包形成循环）。"""

    def register_external(self, call_key: str, *, purpose: str) -> bool: ...

    def release_external(self, call_key: str, *, outcome_code: str) -> None: ...

    def deadline_seconds(self) -> float: ...


def _as_budget(budget: object | None) -> _BudgetView | None:
    """只接受实现了预算视图协议的对象（避免导入 chat 包形成循环）。"""
    if budget is None:
        return None
    if hasattr(budget, "register_external") and hasattr(budget, "deadline_seconds"):
        return cast(_BudgetView, budget)
    return None


def supported_claims(reading: PaperReading | None) -> list[str]:
    """把实际读到的小节映射为有正文依据的断言标签。"""
    if reading is None or reading.scope not in {"full_text", "partial"}:
        return []
    claims: list[str] = []
    for section in reading.sections:
        label = BODY_SECTION_LABELS.get(section.name)
        if label and section.text.strip():
            claims.append(label)
    return list(dict.fromkeys(claims))


__all__ = [
    "BODY_SECTION_LABELS",
    "GOAL_CANDIDATES",
    "GOAL_COMPARE",
    "GOAL_EXPLAIN",
    "SECTIONS_BY_GOAL",
    "FullTextReader",
    "PaperReading",
    "PaperSection",
    "ReadCoordinator",
    "ReadOutcome",
    "supported_claims",
]
