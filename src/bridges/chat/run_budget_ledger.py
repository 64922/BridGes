"""运行预算账本的稳定导入入口。"""

from __future__ import annotations

from datetime import datetime

from bridges.chat.run_budget_ledger_batches import _RunBudgetLedgerBatches
from bridges.chat.run_budget_ledger_calls import _RunBudgetLedgerCalls
from bridges.chat.run_budget_ledger_types import (
    _EXECUTION_PROCESS,
    RUN_BUDGET_CONTRACT_VERSION,
    RUN_BUDGET_INITIALS,
    STUDY_FOLLOW_UP_BUDGET_MS,
    STUDY_FOLLOW_UP_CALLS,
    STUDY_MAX_RUN_BUDGET_MS,
    STUDY_PAGE_BUDGET_MS,
    TIEBA_RUN_TOTAL_BUDGET_MS,
    TIEBA_RUN_VERIFY_RESERVE_MS,
    RunBudgetClass,
    RunBudgetEntry,
    RunBudgetInitials,
    RunBudgetPlan,
    RunBudgetRecipeCosts,
    RunBudgetSnapshot,
    anchor_run_budget_deadline,
    derive_run_budget_class,
    derive_run_budget_plan,
    derive_study_run_budget_plan,
    derive_tieba_run_budget_plan,
)


class RunBudgetLedgerRepository(_RunBudgetLedgerCalls, _RunBudgetLedgerBatches):
    """持久化运行预算，并公开原有账本读写接口。"""

    def _execution_process_token(self) -> str:
        return _EXECUTION_PROCESS

    def record_manual_auth_wait(
        self,
        *,
        account_id: str,
        run_id: str,
        wait_ms: int,
        now: datetime,
    ) -> None:
        """人工认证等待只延长截止，不占外部调用并发位或计数。"""
        wait_ms = max(0, int(wait_ms))
        if wait_ms <= 0:
            return
        self._mutate(
            account_id,
            run_id,
            now,
            kind="external_call_result",
            call_key=f"manual-auth-wait:{now.isoformat()}",
            outcome_code="manual_auth_wait",
            counter_updates={},
            gate=lambda _snapshot: True,
            extra_detail_values={"excluded_wait_ms": wait_ms},
            deadline_extension_ms=wait_ms,
            allow_closed=True,
        )


__all__ = [
    "RUN_BUDGET_CONTRACT_VERSION",
    "RUN_BUDGET_INITIALS",
    "STUDY_FOLLOW_UP_BUDGET_MS",
    "STUDY_FOLLOW_UP_CALLS",
    "STUDY_MAX_RUN_BUDGET_MS",
    "STUDY_PAGE_BUDGET_MS",
    "TIEBA_RUN_TOTAL_BUDGET_MS",
    "TIEBA_RUN_VERIFY_RESERVE_MS",
    "RunBudgetClass",
    "RunBudgetEntry",
    "RunBudgetInitials",
    "RunBudgetLedgerRepository",
    "RunBudgetPlan",
    "RunBudgetRecipeCosts",
    "RunBudgetSnapshot",
    "anchor_run_budget_deadline",
    "derive_run_budget_class",
    "derive_run_budget_plan",
    "derive_study_run_budget_plan",
    "derive_tieba_run_budget_plan",
]
