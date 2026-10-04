"""工单 28：职业样本节点内核验收（配方、收据复用与质量门）。

- 配方登记九个必经节点与四个必要质量门；
- 一次真实内核执行提交九个节点的产物与收据，重放只回填不重跑；
- 质量门按真实投影裁决：样本证据、统计口径、条件核对与个人证据缺一即拦截。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bridges.career_plan.collecting import JobPageReadResult, ParsedJobPage
from bridges.career_plan.contracts import JobReadStatus
from bridges.career_plan.kernel import (
    CAREER_GATE_HANDLERS,
    CAREER_GATES,
    CAREER_RECIPE_ID,
    CAREER_RECIPE_VERSION,
    CareerNodeFlow,
    build_career_recipe,
    career_recipe_registry,
)
from bridges.career_plan.searching import (
    CareerSearchHit,
    CareerSearchOutcome,
    query_record,
)
from bridges.career_plan.service import CareerModuleError, CareerPlanService, CareerSupersededError
from bridges.chat.repository import ConversationRepository
from bridges.contracts.modules import ModuleQueryStatus
from bridges.kernel.contracts import (
    KernelStatus,
    NodeReceiptStatus,
    QualityVerdict,
    RecipeInputs,
)
from bridges.kernel.executor import NodeKernel
from bridges.kernel.guard import RunCommitGuard
from bridges.kernel.repository import NodeKernelRepository
from bridges.storage.database import BridgesDatabase

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
ACCOUNT = "acc-28"
CONVERSATION = "conv-28"
RUN = "run-28"
ASSISTANT = "msg-assistant-28"
BOSS_URL = "https://www.zhipin.com/job_detail/a.html"

NODE_ORDER = (
    "career.parse",
    "career.plan",
    "career.collect",
    "career.filter",
    "career.analyze",
    "career.background",
    "career.gap",
    "career.advise",
    "career.verify",
)


class _Ports:
    """无外网替身：记录检索与读取调用，返回一个可核实的后台岗位页。"""

    def __init__(self) -> None:
        self.queries: list[tuple[str, str]] = []
        self.reads: list[str] = []

    def search_public(
        self,
        account_id: str,
        *,
        query: str,
        reason: str,
        source: str,
        stop_event: Any = None,
        deadline: float | None = None,
    ) -> CareerSearchOutcome:
        del account_id, reason, stop_event, deadline
        self.queries.append((query, source))
        hits = (
            (CareerSearchHit(url=BOSS_URL, title="Java后端开发工程师", snippet="", source="boss"),)
            if source == "boss"
            else ()
        )
        return CareerSearchOutcome(
            record=query_record(
                query=query,
                status=ModuleQueryStatus.SUCCESS,
                evidence_count=len(hits),
            ),
            hits=hits,
        )

    def read(
        self,
        url: str,
        *,
        stop_event: Any = None,
        deadline: float | None = None,
    ) -> JobPageReadResult:
        del stop_event, deadline
        self.reads.append(url)
        return JobPageReadResult(
            url=url,
            status=JobReadStatus.READ,
            page=ParsedJobPage(
                title="Java后端开发工程师",
                company="某某科技",
                city="南昌",
                salary_raw="15-25K·15薪",
                published_raw="2026-09-20",
                published_date=date(2026, 9, 20),
                experience="1-3年",
                requirements=["熟悉 Java、Spring Boot 与 MySQL"],
                is_job_posting=True,
                structure_found=True,
            ),
            retrieved_at=NOW,
        )


def _seed(database: BridgesDatabase) -> None:
    stamp = NOW.isoformat()
    with database.transaction():
        database.connection.execute(
            "INSERT OR IGNORE INTO conversations"
            "(conversation_id, account_id, title, mode, created_at, updated_at)"
            " VALUES (?, ?, '', 'companion', ?, ?)",
            (CONVERSATION, ACCOUNT, stamp, stamp),
        )
        database.connection.execute(
            "INSERT OR IGNORE INTO messages"
            "(message_id, conversation_id, account_id, role, status, content,"
            " created_at, updated_at)"
            " VALUES (?, ?, ?, 'assistant', 'streaming', '', ?, ?)",
            (ASSISTANT, CONVERSATION, ACCOUNT, stamp, stamp),
        )
        database.connection.execute(
            "INSERT OR IGNORE INTO generation_runs"
            "(run_id, account_id, conversation_id, user_message_id,"
            " assistant_message_id, status, lease_owner, lease_expires_at,"
            " stop_requested, created_at, updated_at)"
            " VALUES (?, ?, ?, 'msg-user-28', ?, 'running', 'worker-28', ?, 0, ?, ?)",
            (
                RUN,
                ACCOUNT,
                CONVERSATION,
                ASSISTANT,
                (NOW + timedelta(minutes=5)).isoformat(),
                stamp,
                stamp,
            ),
        )


def _inputs(content: str) -> RecipeInputs:
    return RecipeInputs(
        account_id=ACCOUNT,
        conversation_id=CONVERSATION,
        run_id=RUN,
        user_message_id="msg-user-28",
        user_content=content,
        task_id=None,
        task_version=None,
        wait_identity=None,
        artifacts={},
    )


def _kernel(
    database: BridgesDatabase, ports: _Ports, module_context: Any = None,
) -> tuple[NodeKernel, CareerNodeFlow]:
    flow = CareerNodeFlow(
        search=ports, reader=ports, clock=lambda: NOW, module_context=module_context,
    )
    kernel = NodeKernel(
        registry=career_recipe_registry(),
        repository=NodeKernelRepository(database),
        guard=RunCommitGuard(
            ConversationRepository(database),
            account_id=ACCOUNT,
            run_id=RUN,
            conversation_id=CONVERSATION,
            assistant_message_id=ASSISTANT,
            clock=lambda: NOW,
        ),
        gates=CAREER_GATE_HANDLERS,
        runner=flow.run_node,
        clock=lambda: NOW,
    )
    return kernel, flow


def test_recipe_registers_nine_ordered_nodes_and_required_gates() -> None:
    recipe = build_career_recipe()
    assert recipe.recipe_id == CAREER_RECIPE_ID
    assert recipe.recipe_version == CAREER_RECIPE_VERSION
    assert tuple(node.name for node in recipe.nodes) == NODE_ORDER
    verify = recipe.nodes[-1]
    assert set(verify.required_gates) == {
        "career.sample_evidence",
        "career.stats_caliber",
        "career.conditions_hold",
        "career.personal_evidence",
        "career.personal_review",
    }
    assert set(verify.optional_gates) == {
        "career.independent_review",
    }
    assert set(CAREER_GATE_HANDLERS) == CAREER_GATES
    # 装配即校验：能力、门与依赖都必须是登记过的真实定义。
    assert career_recipe_registry().get(CAREER_RECIPE_ID) is not None


def test_full_run_persists_receipts_and_replay_reuses_without_refetch(
    tmp_path: Path,
) -> None:
    database = BridgesDatabase(tmp_path / "career-kernel.db")
    database.initialize()
    _seed(database)
    ports = _Ports()
    kernel, _flow = _kernel(database, ports)
    first = kernel.execute(
        recipe=build_career_recipe(),
        inputs=_inputs("我想找 Java 后端开发，城市南昌，经验 1-3年"),
    )
    assert first.status is KernelStatus.COMPLETED
    assert [state.reused for state in first.nodes] == [False] * 9
    assert [state.node for state in first.nodes] == list(NODE_ORDER)
    assert ports.queries and ports.reads == [BOSS_URL]

    repository = NodeKernelRepository(database)
    receipts = repository.list_receipts(ACCOUNT, RUN)
    assert {receipt.node for receipt in receipts} == set(NODE_ORDER)
    assert all(receipt.status is NodeReceiptStatus.COMPLETED for receipt in receipts)
    artifacts = repository.list_artifacts(ACCOUNT, CONVERSATION)
    assert {artifact.node for artifact in artifacts} == set(NODE_ORDER)
    delivery = first.delivery
    assert delivery is not None and delivery.node == "career.verify"
    assert delivery.payload["projection"]["samples"]

    replay_ports = _Ports()
    replay_kernel, _replay_flow = _kernel(database, replay_ports)
    replay = replay_kernel.execute(
        recipe=build_career_recipe(),
        inputs=_inputs("我想找 Java 后端开发，城市南昌，经验 1-3年"),
    )
    assert replay.status is KernelStatus.COMPLETED
    assert all(state.reused for state in replay.nodes)
    assert replay_ports.queries == [] and replay_ports.reads == [], "重放不得再次调用外部来源"
    assert len(repository.list_artifacts(ACCOUNT, CONVERSATION)) == 9


def _execution(projection: dict[str, Any]) -> Any:
    return SimpleNamespace(artifact=SimpleNamespace(payload={"projection": projection}))


def _sample(**overrides: Any) -> dict[str, Any]:
    sample = {
        "url": BOSS_URL,
        "title": "Java后端开发工程师",
        "city": "南昌",
        "salary_raw": "15-25K",
        "read_status": "read",
        "title_evidence": "岗位名命中目标岗位。",
        "city_evidence": "页面城市与要求一致。",
        "match_basis": "title",
        "duty_evidence": [],
    }
    sample.update(overrides)
    return sample


def test_gates_block_missing_evidence_instead_of_delivering() -> None:
    sample_gate = CAREER_GATE_HANDLERS["career.sample_evidence"]
    blocked = sample_gate(
        None,
        _execution({"samples": [_sample(match_basis="duty", duty_evidence=[])]}),
    )
    assert blocked.verdict is QualityVerdict.BLOCKED
    assert blocked.code == "career_duty_evidence_missing"

    experience_blocked = sample_gate(
        None,
        _execution({"experience_hint": "1-3年", "samples": [_sample(experience_evidence=None)]}),
    )
    assert experience_blocked.verdict is QualityVerdict.BLOCKED
    assert experience_blocked.code == "career_experience_evidence_missing"

    stats_gate = CAREER_GATE_HANDLERS["career.stats_caliber"]
    mismatch = stats_gate(
        None,
        _execution(
            {
                "samples": [_sample()],
                "analysis": {
                    "sample_count": 2,
                    "missing_salary_count": 0,
                    "overall_inference_stopped": True,
                    "salary_intervals": [{"unit": "元/月", "currency": "CNY"}],
                },
            }
        ),
    )
    assert mismatch.verdict is QualityVerdict.BLOCKED
    assert mismatch.code == "career_stats_sample_mismatch"

    conditions = CAREER_GATE_HANDLERS["career.conditions_hold"]
    violated = conditions(
        None,
        _execution({"cities": ["杭州"], "experience_hint": None, "samples": [_sample()]}),
    )
    assert violated.verdict is QualityVerdict.BLOCKED
    assert violated.code == "career_city_condition_violated"
    unknown_city = conditions(
        None, _execution({"cities": [], "samples": [_sample(city=None)]}),
    )
    assert unknown_city.verdict is QualityVerdict.BLOCKED
    assert unknown_city.code == "career_city_unverified"


def test_gates_pass_on_consistent_projection() -> None:
    projection = {
        "cities": ["南昌"],
        "experience_hint": "1-3年",
        "samples": [_sample(experience="1-3年", experience_evidence="页面经验一致。")],
        "analysis": {
            "sample_count": 1,
            "missing_salary_count": 0,
            "overall_inference_stopped": True,
            "salary_intervals": [{"unit": "元/月", "currency": "CNY"}],
        },
    }
    for gate in ("career.sample_evidence", "career.stats_caliber", "career.conditions_hold"):
        result = CAREER_GATE_HANDLERS[gate](None, _execution(projection))
        assert result.verdict is QualityVerdict.PASS, gate


def _seed_service(database: BridgesDatabase) -> ConversationRepository:
    _seed(database)
    with database.transaction():
        database.connection.execute(
            "UPDATE generation_runs SET lease_expires_at = '2099-01-01T00:00:00+00:00'"
        )
        database.connection.execute(
            "INSERT INTO messages"
            "(message_id, conversation_id, account_id, role, status, content,"
            " created_at, updated_at) VALUES (?, ?, ?, 'user', 'done', ?, ?, ?)",
            ("msg-user-28", CONVERSATION, ACCOUNT, "查询 Java 后端开发岗位，城市南昌",
             NOW.isoformat(), NOW.isoformat()),
        )
    return ConversationRepository(database)


def _run_service(service: CareerPlanService, repo: ConversationRepository) -> Any:
    return service.run(
        repo=repo, account_id=ACCOUNT, conversation_id=CONVERSATION,
        user_message_id="msg-user-28", assistant_message_id=ASSISTANT,
        run_context=SimpleNamespace(run_id=RUN),
        emit_node=lambda *_args: None, stop_event=None,
    )


def test_source_failure_is_finalized_atomically_inside_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """失败投影、正文和终态必须与守卫复核共用同一事务。"""
    database = BridgesDatabase(tmp_path / "failure-atomic.db")
    database.initialize()
    repo = _seed_service(database)
    ports = _Ports()

    def fail_search(*_args: Any, **kwargs: Any) -> CareerSearchOutcome:
        return CareerSearchOutcome(record=query_record(
            query=kwargs["query"], status=ModuleQueryStatus.ERROR,
            evidence_count=0, error_code="source_unavailable", retryable=True,
        ), hits=())

    monkeypatch.setattr(ports, "search_public", fail_search)
    original_finalize = repo.finalize_message
    observed: list[bool] = []

    def checked_finalize(*args: Any, **kwargs: Any) -> int:
        observed.append(database.connection.in_transaction)
        return original_finalize(*args, **kwargs)

    monkeypatch.setattr(repo, "finalize_message", checked_finalize)
    with pytest.raises(CareerModuleError):
        _run_service(CareerPlanService(search=ports, reader=ports), repo)
    message = repo.get_message(ACCOUNT, ASSISTANT)
    assert observed == [True]
    assert message is not None and message.status.value == "error"
    assert message.career_plan is not None and message.career_plan["status"] == "error"
    assert "查询" in message.content and message.error_code


def test_source_failure_after_lease_transfer_cannot_write_a_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """来源返回失败时已转移的租约，拒绝旧执行者的失败交付。"""
    database = BridgesDatabase(tmp_path / "failure-superseded.db")
    database.initialize()
    repo = _seed_service(database)
    ports = _Ports()

    def transfer_then_fail(*_args: Any, **kwargs: Any) -> CareerSearchOutcome:
        with database.transaction():
            database.connection.execute(
                "UPDATE generation_runs SET lease_owner = 'worker-new' WHERE run_id = ?",
                (RUN,),
            )
        return CareerSearchOutcome(record=query_record(
            query=kwargs["query"], status=ModuleQueryStatus.ERROR, evidence_count=0,
        ), hits=())

    monkeypatch.setattr(ports, "search_public", transfer_then_fail)
    with pytest.raises(CareerSupersededError):
        _run_service(CareerPlanService(search=ports, reader=ports), repo)
    message = repo.get_message(ACCOUNT, ASSISTANT)
    assert message is not None and message.career_plan is None and message.content == ""


def test_same_continuation_with_changed_task_goal_cannot_reuse_old_parse(tmp_path: Path) -> None:
    """条件相同但岗位目标改变时，同一个「继续」不能命中旧目标的产物。"""
    database = BridgesDatabase(tmp_path / "goal-reuse.db")
    database.initialize()
    _seed(database)
    first_kernel, first_flow = _kernel(database, _Ports(), SimpleNamespace(
        used_task_scope=True, topic_hint="查询 Java 后端开发岗位，城市南昌",
        task_goal="", effective_conditions=(),
    ))
    first = first_kernel.execute(
        recipe=build_career_recipe(),
        inputs=replace(_inputs("继续"), prior_digest=first_flow.prior_digest),
    )
    assert first.status is KernelStatus.COMPLETED
    second_kernel, second_flow = _kernel(database, _Ports(), SimpleNamespace(
        used_task_scope=True, topic_hint="查询前端开发岗位，城市南昌",
        task_goal="", effective_conditions=(),
    ))
    second = second_kernel.execute(
        recipe=build_career_recipe(),
        inputs=replace(_inputs("继续"), prior_digest=second_flow.prior_digest),
    )
    assert first_flow.prior_digest != second_flow.prior_digest
    assert not second.nodes[0].reused
    parsed = second.artifact("career.parse")
    assert parsed is not None and parsed.payload["analysis"]["family_title"] == "前端开发工程师"
