"""论文角色装配、任务恢复、等待与共享预算读取。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from bridges.ai.model_quota import RunModelQuota
from bridges.ai.payload_budget import CallMaterialManifest
from bridges.contracts.modules import ModuleWaitState
from bridges.contracts.workflows import RunContextEnvelope
from bridges.paper.assessment import PaperEvidenceRole
from bridges.paper.budget import PaperBudget
from bridges.paper.contracts import PaperSearchProjection, PaperSearchStatus

if TYPE_CHECKING:
    from bridges.chat.repository import ConversationRepository
    from bridges.paper.service import PaperSearchService



def _resolve_assessors(
    self: PaperSearchService,
    repo: ConversationRepository,
    *,
    account_id: str,
    run_id: str,
    run_context: RunContextEnvelope,
    run_model_id: str | None,
    model_quota: RunModelQuota | None,
    assessment_manifest_sink: Callable[[CallMaterialManifest], None] | None,
) -> tuple[Any, Any]:
    """装配本轮 judge/reviewer：优先注入接缝，否则用登记模型网关角色。

    生产角色复用父图同一 ``RunBudget``（持久账本）、``RunModelQuota``
    与材料清单审计；每批候选一次判断、比较/冲突时独立复核；模型证据
    由调用方再次逐字核对原文，不把模型输出当作未经核验的事实。
    """
    if self._gateway is None:
        return self._judge, self._reviewer
    # 局部导入：chat 包属主预算账本，模块级导入形成包级循环。
    from bridges.chat.budget import load_run_budget

    run_budget = load_run_budget(repo, account_id, run_id)
    role = PaperEvidenceRole(
        self._gateway,
        run_context=run_context,
        model_id=run_model_id,
        quota=model_quota,
        budget=run_budget,
        manifest_sink=assessment_manifest_sink,
    )
    return self._judge or role, self._reviewer or role.review


def _resolve_intent_analyzer(
    self: PaperSearchService,
    repo: ConversationRepository,
    *,
    account_id: str,
    run_id: str,
    run_context: RunContextEnvelope,
    run_model_id: str | None,
    model_quota: RunModelQuota | None,
    manifest_sink: Callable[[CallMaterialManifest], None] | None,
) -> Any | None:
    if self._intent_analyzer_factory is None or self._gateway is None:
        return None
    from bridges.chat.budget import load_run_budget

    return self._intent_analyzer_factory(
        self._gateway,
        run_context=run_context,
        model_id=run_model_id,
        quota=model_quota,
        budget=load_run_budget(repo, account_id, run_id),
        manifest_sink=manifest_sink,
    )


def _pending_wait(
    self: PaperSearchService, repo: ConversationRepository, account_id: str, conversation_id: str
) -> ModuleWaitState | None:
    """读取本会话最近一次尚未被后续结果取代的澄清等待状态。"""
    for message in reversed(repo.list_messages(account_id, conversation_id)):
        projection = message.paper_search
        if not isinstance(projection, dict):
            continue
        try:
            stored = PaperSearchProjection.model_validate(projection)
        except ValueError:
            continue
        if stored.pending is not None:
            return stored.pending
        if stored.status in {PaperSearchStatus.SUCCESS, PaperSearchStatus.EMPTY}:
            # 更晚的完成结果已经取代等待状态：不再恢复。
            return None
    return None


def _prior_user_messages(
    self: PaperSearchService,
    repo: ConversationRepository,
    account_id: str,
    conversation_id: str,
    user_message_id: str,
) -> list[str]:
    """已确认的会话前文（最近若干条用户消息），只用于语境消歧。"""
    prior: list[str] = []
    for message in repo.list_messages(account_id, conversation_id):
        if message.message_id == user_message_id:
            break
        if message.role.value == "user" and message.content.strip():
            prior.append(message.content)
    return prior[-6:]


def _current_task_ref(
    self: PaperSearchService, account_id: str, conversation_id: str
) -> tuple[str | None, int | None] | None:
    if self._task_version_provider is None:
        return None
    return self._task_version_provider(account_id, conversation_id)


def _wait_identity(self: PaperSearchService, pending: ModuleWaitState | None) -> str | None:
    if pending is None:
        return None
    return (
        f"{pending.module_id}:{pending.kind}:{pending.origin_message_id}:"
        f"{pending.created_at.isoformat()}"
    )


def _load_budget(
    self: PaperSearchService, repo: ConversationRepository, account_id: str, run_id: str
) -> PaperBudget | None:
    """从持久账本加载共享预算（缺失行时按无预算模式保留本地截止）。"""
    # 局部导入：chat 包（预算账本属主）在模块级导入会与父图形成循环。
    from bridges.chat.run_budget_ledger import RunBudgetLedgerRepository

    ledger = RunBudgetLedgerRepository(repo.database)
    snapshot = ledger.load(account_id, run_id)
    if snapshot is None:
        return None
    ledger.recover_external_calls(account_id, run_id, now=datetime.now(UTC))
    work_deadline = snapshot.plan.deadline_at - timedelta(
        milliseconds=snapshot.plan.verify_deliver_reserve_ms
    )
    return PaperBudget(
        ledger=ledger,
        account_id=account_id,
        run_id=run_id,
        work_deadline=work_deadline,
        screen_max=snapshot.plan.candidate_screen_max,
        deep_read_max=snapshot.plan.deep_read_max,
    )
