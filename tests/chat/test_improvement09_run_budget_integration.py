"""改进工单 09：运行预算账本的服务与网关接线验收。

用真实 HTTP + SQLite + 确定性替身证明接缝：

1. 发送消息（续轮/首轮）即冻结账本：截止 = 运行创建时刻 + 类别总预算，
   普通文本运行冻结 normal 初值；
2. 网关唯一咽喉点把每次真实调用（invoke/流式/备选）登记进同一账本，
   超额调用被拒绝（BLOCKED ``run_budget_call_limit``），临时传输失败的
   重试受「每登记调用最多 1 次」账本守卫；
3. 用户停止后账本关闭、不再有新调用；重试创建新运行、新账本。

确定性替身只证明机制正确，不代表真实模型体验。
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.ai import ModelGateway
from bridges.ai.adapters import AdapterResult, RateLimitError, StreamChunk
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.chat.budget import RunBudget
from bridges.chat.run_budget_ledger import (
    RunBudgetClass,
    RunBudgetLedgerRepository,
    derive_run_budget_plan,
)
from bridges.contracts.ai import (
    CapabilityRecord,
    ModelCallStatus,
    ModelCapabilities,
    RetryPolicy,
)
from bridges.contracts.workflows import ObjectDomain, RunContextEnvelope
from tests.chat.test_chat_api import (
    _chat_capability,
    _create_conversation,
    _gateway_with,
    _ProgrammableStreamAdapter,
    _register,
)
from tests.chat.test_issue02_durable_generation import _send
from tests.chat.test_issue02_durable_generation import (  # noqa: F401 - 复用夹具
    client as client,
)
from tests.chat.test_issue02_durable_generation import (
    sqlite_app as sqlite_app,
)

# ---------------------------------------------------------------------------
# 账本替身：记录内核写入并按需拒绝（网关 seam 的机制验证）
# ---------------------------------------------------------------------------


class _RecordingLedger:
    """实现 RunBudget 账本协议的确定性替身（可配置拒绝点）。"""

    def __init__(
        self,
        *,
        allow_calls: int = 99,
        allow_retries: int = 1,
    ) -> None:
        self.calls: list[str] = []
        self.results: list[dict[str, Any]] = []
        self.retries: list[str] = []
        self.external: list[str] = []
        self.adjustments: list[str | None] = []
        self.exhausted: list[str] = []
        self._allow_calls = allow_calls
        self._allow_retries = allow_retries

    def register_model_call(
        self,
        *,
        account_id: str,
        run_id: str,
        call_key: str,
        purpose: str | None = None,
        now: datetime,
    ) -> bool:
        if len(self.calls) >= self._allow_calls:
            return False
        self.calls.append(call_key)
        return True

    def record_model_call_result(self, **kwargs: Any) -> None:
        self.results.append(kwargs)

    def register_transient_retry(
        self,
        *,
        account_id: str,
        run_id: str,
        call_key: str,
        error_code: str | None = None,
        backoff_ms: int | None = None,
        now: datetime,
    ) -> bool:
        if self.retries.count(call_key) >= self._allow_retries:
            return False
        self.retries.append(call_key)
        return True

    def register_external_call(self, **kwargs: Any) -> bool:
        self.external.append(str(kwargs.get("call_key")))
        return True

    def record_external_call_result(self, **kwargs: Any) -> None:
        return None

    def begin_adjustment(self, **kwargs: Any) -> bool:
        self.adjustments.append(kwargs.get("reason_code"))
        return True

    def mark_exhausted(self, **kwargs: Any) -> bool:
        self.exhausted.append(str(kwargs.get("reason_code")))
        return True

    def can_wait_until(self, account_id: str, run_id: str, until: datetime) -> bool:
        return True


def _run_context(account_id: str = "acc-1", run_id: str = "run-1") -> RunContextEnvelope:
    return RunContextEnvelope(
        run_id=run_id,
        account_id=account_id,
        project_id="conv-1",
        workflow_name="chat",
        workflow_version="1",
        object_domain=ObjectDomain.PERSONAL_VAULT,
        submitted_at=datetime.now(UTC),
    )


def _persistent_budget(database: Any, account_id: str = "acc-1") -> tuple[Any, RunBudget]:
    ledger = RunBudgetLedgerRepository(database)
    now = datetime.now(UTC)
    snapshot = ledger.freeze_for_run(
        account_id=account_id, run_id="run-1", conversation_id="conv-1", now=now,
        plan=derive_run_budget_plan(
            RunBudgetClass.LIGHTWEIGHT, deadline_at=now + timedelta(seconds=120)
        ),
    )
    return ledger, RunBudget.from_ledger_snapshot(
        "run-1", total_budget_ms=snapshot.plan.total_budget_ms,
        deadline_utc=snapshot.plan.deadline_at, reserve_ms=0, ledger=ledger,
        account_id=account_id,
    )


def test_separate_calls_to_same_capability_have_separate_retries(tmp_path: Any) -> None:
    """同能力两次调用各自最多重试一次，所有真实尝试计入共同调用上限。"""
    from bridges.storage.database import BridgesDatabase

    database = BridgesDatabase(tmp_path / "budget.db")
    database.initialize()
    ledger, budget = _persistent_budget(database)
    registry = CapabilityRegistry()
    registry.register(_retryable_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", _RateLimitedAdapter())
    for _ in range(2):
        result = gateway.invoke("qwen_text_chat", "1", _run_context(), budget=budget)
        assert result.status == ModelCallStatus.RETRYABLE_FAIL
    snapshot = ledger.load("acc-1", "run-1")
    assert snapshot.model_calls_used == 4
    assert snapshot.transient_retries_used == 2
    calls = [e.call_key for e in ledger.list_entries("acc-1", "run-1") if e.kind == "model_call"]
    assert len(set(calls)) == 2


def test_stop_during_backoff_prevents_next_transport_attempt(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    """用冷却回调模拟停止，避免真实等待并检查下一次外呼被拒绝。"""
    from bridges.storage.database import BridgesDatabase

    database = BridgesDatabase(tmp_path / "budget.db")
    database.initialize()
    ledger, budget = _persistent_budget(database)
    registry = CapabilityRegistry()
    registry.register(_retryable_capability().model_copy(update={
        "retry_policy": RetryPolicy(max_attempts=3, backoff_seconds=0.01, jitter=False)
    }))
    gateway = ModelGateway(registry)
    calls = []

    class Adapter:
        def call(self, *args: Any) -> Any:
            calls.append(1)
            raise RateLimitError("冷却")

    gateway.register_adapter("qwen_text_chat", "1", Adapter())
    monkeypatch.setattr(
        "bridges.ai.model_gateway.time.sleep",
        lambda _: ledger.close(account_id="acc-1", run_id="run-1", now=datetime.now(UTC)),
    )
    gateway.invoke("qwen_text_chat", "1", _run_context(), budget=budget)
    assert len(calls) == 1


def test_conversation_deletion_removes_budget_and_entries(
    sqlite_app: Any, client: TestClient,
) -> None:
    account = _register(client)
    conversation_id = _create_conversation(client)
    created = _send(client, conversation_id, "待删除")
    service = sqlite_app.state.chat_service
    service.stop_account_generations(account["id"])
    ledger = _ledger_repo(sqlite_app)
    assert ledger.load(account["id"], created["run_id"]).status == "closed"
    service._repo.delete_conversation("other-account", conversation_id)
    assert ledger.load(account["id"], created["run_id"]) is not None
    service._repo.delete_conversation(account["id"], conversation_id)
    assert ledger.load(account["id"], created["run_id"]) is None
    assert ledger.list_entries(account["id"], created["run_id"]) == []


@pytest.mark.parametrize("round_used", [False, True])
def test_existing_career_repair_claims_shared_adjustment(tmp_path: Any, round_used: bool) -> None:
    """验证现有生产结构修复入口，第二轮不能重新领取额度。"""
    from bridges.storage.database import BridgesDatabase
    from tests.career.test_career_service import (
        _good_output,
        _invalid_output,
        _ProgrammableStructuredAdapter,
        _run,
        _service_with_recorder,
    )

    database = BridgesDatabase(tmp_path / "budget.db")
    database.initialize()
    ledger, budget = _persistent_budget(database, "account-1")
    if round_used:
        assert budget.begin_adjustment(reason_code="content_fix")
    adapter = _ProgrammableStructuredAdapter(outputs=[_invalid_output(), _good_output()])
    service, _ = _service_with_recorder(adapter, database)
    _run(service, budget=budget)
    assert adapter.call_count == (1 if round_used else 2)
    assert ledger.load("account-1", "run-1").adjustment_rounds_used == 1
    if not round_used:
        assert any(e.kind == "adjustment_end" for e in ledger.list_entries("account-1", "run-1"))


def _retryable_capability() -> CapabilityRecord:
    base = _chat_capability()
    return base.model_copy(
        update={
            "retry_policy": RetryPolicy(max_attempts=3, backoff_seconds=0.0, jitter=False),
            "capabilities": ModelCapabilities(
                text=True, image=True, tool_calling=True, structured_output=True
            ),
        }
    )


class _RateLimitedAdapter:
    """每次调用都抛限流错误（真实走网关重试分类）。"""

    def call(
        self, capability: CapabilityRecord, run_context: Any, payload: dict[str, Any]
    ) -> AdapterResult:
        raise RateLimitError("上游限流。")


class _UsageStreamAdapter:
    """成功流式适配器：产出两块增量与 usage。"""

    def stream_call(
        self, capability: CapabilityRecord, run_context: Any, payload: dict[str, Any]
    ):
        yield StreamChunk(kind="delta", delta="你好")
        yield StreamChunk(
            kind="done",
            usage={"prompt_tokens": 30, "completion_tokens": 7},
            actual_model_id=capability.model_id,
        )


# ---------------------------------------------------------------------------
# 网关接缝（invoke / stream / 重试 / 超额拒绝）
# ---------------------------------------------------------------------------


def test_gateway_invoke_registers_call_and_result() -> None:
    ledger = _RecordingLedger()
    budget = RunBudget.from_ledger_snapshot(
        "run-1",
        total_budget_ms=60_000,
        deadline_utc=datetime.now(UTC).replace(microsecond=0)
        + timedelta(seconds=120),
        reserve_ms=0,
        ledger=ledger,  # type: ignore[arg-type]
        account_id="acc-1",
    )
    registry = CapabilityRegistry()
    registry.register(_chat_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter(
        "qwen_text_chat",
        "1",
        _ProgrammableStreamAdapter(chunks=[StreamChunk(kind="delta", delta="x")]),
    )
    result = gateway.invoke(
        "qwen_text_chat",
        "1",
        _run_context(),
        payload={"prompt": "你好"},
        budget=budget,
    )
    assert result.status == ModelCallStatus.SUCCESS
    assert len(ledger.calls) == 1
    assert ledger.calls[0].startswith("qwen_text_chat@1:")
    assert ledger.results[0]["call_key"] == ledger.calls[0]
    assert len(ledger.results) == 1
    recorded = ledger.results[0]
    assert recorded["outcome_code"] == "call_completed"
    assert budget.has_ledger


def test_gateway_refuses_over_limit_call_with_blocked_result() -> None:
    ledger = _RecordingLedger(allow_calls=0)
    budget = RunBudget.from_ledger_snapshot(
        "run-1",
        total_budget_ms=60_000,
        deadline_utc=datetime.now(UTC).replace(microsecond=0)
        + timedelta(seconds=120),
        reserve_ms=0,
        ledger=ledger,  # type: ignore[arg-type]
        account_id="acc-1",
    )
    registry = CapabilityRegistry()
    registry.register(_chat_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", _RateLimitedAdapter())
    result = gateway.invoke(
        "qwen_text_chat",
        "1",
        _run_context(),
        payload={"prompt": "你好"},
        budget=budget,
    )
    assert result.status == ModelCallStatus.BLOCKED
    assert result.error_code == "run_budget_call_limit"
    assert ledger.calls == []


def test_gateway_transient_retry_is_capped_once_per_call() -> None:
    ledger = _RecordingLedger(allow_retries=1)
    budget = RunBudget.from_ledger_snapshot(
        "run-1",
        total_budget_ms=120_000,
        deadline_utc=datetime.now(UTC).replace(microsecond=0)
        + timedelta(seconds=120),
        reserve_ms=0,
        ledger=ledger,  # type: ignore[arg-type]
        account_id="acc-1",
    )
    registry = CapabilityRegistry()
    registry.register(_retryable_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", _RateLimitedAdapter())
    result = gateway.invoke(
        "qwen_text_chat",
        "1",
        _run_context(),
        payload={"prompt": "你好"},
        budget=budget,
    )
    # max_attempts=3：首次 + 账本放行的 1 次重试；第二次重试被账本拒绝，
    # 以真实可重试错误终态收尾（不依赖真实睡眠：backoff=0）。
    assert result.status == ModelCallStatus.RETRYABLE_FAIL
    assert result.error_code == "rate_limit"
    assert ledger.retries == ledger.calls
    # 失败结果按真实尝试补记：首次 + 账本放行的一次重试；第二次重试在
    # 发起前已被账本拒绝（attempt 3 不会发生）。
    assert len(ledger.results) == 2


def test_gateway_stream_registers_call_and_records_usage() -> None:
    ledger = _RecordingLedger()
    budget = RunBudget.from_ledger_snapshot(
        "run-1",
        total_budget_ms=60_000,
        deadline_utc=datetime.now(UTC).replace(microsecond=0)
        + timedelta(seconds=120),
        reserve_ms=0,
        ledger=ledger,  # type: ignore[arg-type]
        account_id="acc-1",
    )
    registry = CapabilityRegistry()
    registry.register(_chat_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", _UsageStreamAdapter())
    events = list(
        gateway.stream(
            "qwen_text_chat",
            "1",
            _run_context(),
            payload={"prompt": "你好"},
            budget=budget,
        )
    )
    assert [event.kind for event in events][-1] == "done"
    assert len(ledger.calls) == 1
    assert ledger.calls[0].startswith("qwen_text_chat@1:")
    assert ledger.results[0]["call_key"] == ledger.calls[0]
    assert len(ledger.results) == 1
    assert ledger.results[0]["input_tokens"] == 30
    assert ledger.results[0]["output_tokens"] == 7


# ---------------------------------------------------------------------------
# 服务接线（发送即冻结 / 停止即关闭 / 重试即新账本）
# ---------------------------------------------------------------------------


def _ledger_repo(sqlite_app: Any) -> RunBudgetLedgerRepository:
    return RunBudgetLedgerRepository(sqlite_app.state.chat_service._repo.database)


def test_send_freezes_ledger_with_lightweight_initials(
    sqlite_app: Any, client: TestClient
) -> None:
    """普通轻量交流在发送时冻结 lightweight 账本：沿用已验证 120 秒前台
    硬上限、无预留（normal 类别留给配方驱动的检索编排，票 10/12/37）。"""
    account = _register(client)
    conversation_id = _create_conversation(client)
    created = _send(client, conversation_id, "普通文本消息")
    run_id = created["run_id"]
    snapshot = _ledger_repo(sqlite_app).load(account["id"], run_id)
    assert snapshot is not None
    assert snapshot.status == "active"
    assert snapshot.contract_version == "run-budget-v1"
    assert snapshot.plan.budget_class.value == "lightweight"
    assert snapshot.plan.total_budget_ms == 120_000
    assert snapshot.plan.verify_deliver_reserve_ms == 0
    run = sqlite_app.state.chat_service._repo.get_generation_run(
        account["id"], run_id
    )
    assert run is not None
    assert snapshot.plan.deadline_at >= run.created_at
    delta = (snapshot.plan.deadline_at - run.created_at).total_seconds()
    assert 115 <= delta <= 125


def test_stop_closes_ledger_and_rejects_new_calls(
    sqlite_app: Any, client: TestClient
) -> None:
    account = _register(client)
    conversation_id = _create_conversation(client)
    created = _send(client, conversation_id, "请停止我")
    message_id = created["assistant_message"]["message_id"]
    run_id = created["run_id"]
    # 无执行器运行：停止经兜底收敛（≤4 秒）后账本关闭
    response = client.post(f"/chat/conversations/{conversation_id}/messages/{message_id}/stop")
    assert response.status_code == 200, response.text
    repo = _ledger_repo(sqlite_app)
    snapshot = repo.load(account["id"], run_id)
    assert snapshot is not None
    assert snapshot.status == "closed"
    assert not repo.register_model_call(
        account_id=account["id"],
        run_id=run_id,
        call_key="qwen_text_chat@1",
        now=datetime.now(UTC),
    )


def test_retry_creates_new_budget_run(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    account = _register(client)
    conversation_id = _create_conversation(client)
    # 失败适配器：首轮执行失败进入终态（执行器收敛并关闭账本）
    class _FailingAdapter:
        def stream_call(self, capability: Any, run_context: Any, payload: dict[str, Any]):
            raise RateLimitError("上游限流。")

    sqlite_app.state.chat_service._gateway = _gateway_with(_FailingAdapter())
    created = _send(client, conversation_id, "第一轮会失败")
    first_run_id = created["run_id"]
    first_message_id = created["assistant_message"]["message_id"]
    stop_exec, exec_thread = generation_helpers["executor_thread"](sqlite_app)
    try:
        for _ in range(100):
            run = sqlite_app.state.chat_service._repo.get_generation_run(
                account["id"], first_run_id
            )
            if run is not None and run.status in {"done", "failed", "stopped"}:
                break
            time.sleep(0.1)
        first_snapshot = _ledger_repo(sqlite_app).load(
            account["id"], first_run_id
        )
        assert first_snapshot is not None
        assert first_snapshot.status == "closed"

        # 重试：新运行、新账本（失败用例沿用同一失败适配器，重试同样终态）
        response = client.post(
            f"/chat/conversations/{conversation_id}/messages/{first_message_id}/retry"
        )
        assert response.status_code == 200, response.text
        retried = response.json()
        second_run_id = (
            retried.get("assistant_message", {}).get("run_id")
            or retried.get("run_id")
        )
        assert second_run_id and second_run_id != first_run_id
        for _ in range(100):
            run = sqlite_app.state.chat_service._repo.get_generation_run(
                account["id"], second_run_id
            )
            if run is not None and run.status in {"done", "failed", "stopped"}:
                break
            time.sleep(0.1)
        repo = _ledger_repo(sqlite_app)
        second_snapshot = repo.load(account["id"], second_run_id)
        assert second_snapshot is not None
        # 新账本独立冻结：计数只反映新运行自身的调用
        assert second_snapshot.run_id == second_run_id
        assert first_snapshot.run_id == first_run_id
        entries_first = repo.list_entries(account["id"], first_run_id)
        entries_second = repo.list_entries(account["id"], second_run_id)
        assert entries_first and entries_second
        assert all(entry.run_id == first_run_id for entry in entries_first)
        assert all(entry.run_id == second_run_id for entry in entries_second)
    finally:
        stop_exec.set()
        exec_thread.join(timeout=5)
