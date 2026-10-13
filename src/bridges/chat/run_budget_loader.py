"""从持久账本加载运行预算。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from bridges.ai.model_quota import RUN_MODEL_QUOTA_CONFIG_KEY, RunModelQuota
from bridges.chat.run_budget import RunBudget
from bridges.chat.run_budget_ledger import (
    RUN_BUDGET_INITIALS,
    RunBudgetLedgerRepository,
    derive_run_budget_class,
)


def load_run_budget(
    repo: Any,
    account_id: str,
    run_id: str,
    *,
    route_hint: Any = None,
    mode_hint: str | None = None,
) -> RunBudget:
    """从持久化账本加载运行预算控制器（工单 09 的统一只读入口）。

    账本行在运行创建时冻结（``ChatService._freeze_run_budget``）；本函数
    只读取，并对缺失行的运行按兼容纪律先补齐持久状态再使用——截止时间
    锚定运行创建时刻，绝不把截止向后延、绝不重置已耗计数。兼容补齐的
    类别从调用方给出的模式/路线信号派生（崩溃窗口内的运行缺失图片/视频
    信号时按轻量处理，影响仅限崩溃窗口与升级前遗留运行）。

    聊天父图与学习等子模块共用本入口，避免各自复制账本加载逻辑。
    """
    ledger = RunBudgetLedgerRepository(repo.database)
    ledger.recover_external_calls(account_id, run_id, now=datetime.now(UTC))
    snapshot = ledger.load(account_id, run_id)
    if snapshot is None:
        run = repo.get_generation_run(account_id, run_id)
        if run is None:
            # 运行已随会话/消息删除的竞态：不会进入生成阶段，内存预算兜底。
            return RunBudget(run_id)
        budget_class = derive_run_budget_class(mode=mode_hint, route=route_hint)
        quota = RunModelQuota.from_config(
            (run.config or {}).get(RUN_MODEL_QUOTA_CONFIG_KEY)
        )
        token_budget = (
            quota.max_input_tokens * RUN_BUDGET_INITIALS[budget_class].model_call_limit
            if quota is not None
            and quota.is_verified
            and quota.max_input_tokens is not None
            else None
        )
        snapshot = ledger.ensure_for_run(
            account_id=account_id,
            run_id=run_id,
            conversation_id=run.conversation_id,
            budget_class=budget_class,
            run_created_at=run.created_at,
            now=datetime.now(UTC),
            token_budget=token_budget,
        )
    return RunBudget.from_ledger_snapshot(
        run_id,
        total_budget_ms=snapshot.plan.total_budget_ms,
        deadline_utc=snapshot.plan.deadline_at,
        reserve_ms=snapshot.plan.verify_deliver_reserve_ms,
        ledger=ledger,
        account_id=account_id,
        active=snapshot.active,
    )
