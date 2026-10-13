"""运行预算账本的稳定导入入口。"""

from __future__ import annotations

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
