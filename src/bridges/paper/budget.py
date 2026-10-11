"""论文节点对共享运行预算账本的受限视图。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from bridges.paper.recipe import DEFAULT_DEEP_READ_MAX, DEFAULT_SCREEN_MAX


class PaperBudget:
    """论文外部调用与候选上限的共享运行预算视图。"""

    def __init__(
        self,
        *,
        ledger: Any,
        account_id: str,
        run_id: str,
        work_deadline: datetime,
        screen_max: int = DEFAULT_SCREEN_MAX,
        deep_read_max: int = DEFAULT_DEEP_READ_MAX,
    ) -> None:
        self._ledger = ledger
        self._account_id = account_id
        self._run_id = run_id
        self._work_deadline = work_deadline
        self.screen_max = max(1, screen_max)
        self.deep_read_max = max(0, deep_read_max)

    def remaining_work_ms(self, now: datetime | None = None) -> int:
        moment = now or datetime.now(UTC)
        return max(0, int((self._work_deadline - moment).total_seconds() * 1000))

    def deadline_seconds(self, now: datetime | None = None) -> float:
        return max(0.5, self.remaining_work_ms(now) / 1000.0)

    def register_external(self, call_key: str, *, purpose: str) -> bool:
        return bool(
            self._ledger.register_external_call(
                account_id=self._account_id,
                run_id=self._run_id,
                call_key=call_key,
                purpose=purpose,
                now=datetime.now(UTC),
            )
        )

    def release_external(self, call_key: str, *, outcome_code: str) -> None:
        self._ledger.record_external_call_result(
            account_id=self._account_id,
            run_id=self._run_id,
            call_key=call_key,
            outcome_code=outcome_code,
            now=datetime.now(UTC),
        )

    def begin_adjustment(self) -> bool:
        return bool(
            self._ledger.begin_adjustment(
                account_id=self._account_id,
                run_id=self._run_id,
                reason_code="paper_relevance_insufficient",
                now=datetime.now(UTC),
            )
        )

    def end_adjustment(self, outcome_code: str) -> None:
        self._ledger.end_adjustment(
            account_id=self._account_id,
            run_id=self._run_id,
            outcome_code=outcome_code,
            now=datetime.now(UTC),
        )
