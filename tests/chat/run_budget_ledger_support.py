"""共享的持久运行预算账本测试 fixture 与数据构造器。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from bridges.chat.run_budget_ledger import (
    RunBudgetClass,
    RunBudgetLedgerRepository,
    derive_run_budget_plan,
)
from bridges.routing import CapabilityRoute, MainCapability, RouteStatus

_NOW = datetime.now(UTC).replace(microsecond=0)


def _freeze_normal(
    repo: RunBudgetLedgerRepository,
    run_id: str = "run-1",
    *,
    account_id: str = "acc-1",
    deadline: datetime | None = None,
    token_budget: int | None = None,
    now: datetime = _NOW,
) -> Any:
    plan = derive_run_budget_plan(
        RunBudgetClass.NORMAL,
        deadline_at=deadline or (now + timedelta(seconds=60)),
        token_budget=token_budget,
    )
    return repo.freeze_for_run(
        account_id=account_id,
        run_id=run_id,
        conversation_id="conv-1",
        plan=plan,
        now=now,
    )


def _route(capability: MainCapability) -> CapabilityRoute:
    """绕开与派生无关的论文计划载荷校验。"""
    return CapabilityRoute.model_construct(
        status=RouteStatus.MATCHED,
        main_capability=capability,
        confidence=0.9,
        reason="预算派生用例",
    )
