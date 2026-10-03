"""Issue 24：论文语义匹配、分层阅读与可恢复交付的验收合同。

覆盖工单验收要点：

- 同义相关候选通过「需求 → 标题/摘要证据」匹配；只命中原词的语境假阳性
  被排除；年份等硬条件由代码过滤，不静默放宽；
- 只有摘要时不宣称精读，方法/实验断言必须有正文依据；
- 必要身份缺失阻塞对应结论；可选 enrich 失败只留缺口；
- 恢复只执行未完成的有效节点（收据/产物复用，失败节点重跑）；
- 选定论文携带可核实身份（arXiv/标题/链接/内容哈希）；表达策略与输出
  额度可追溯；旧投影继续可读。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.chat.lightweight_policy import DEFAULT_OUTPUT_TOKENS
from bridges.chat.repository import ConversationRepository
from bridges.contracts.ai import ModelCallResult, ModelCallStatus
from bridges.contracts.modules import ModuleQueryStatus
from bridges.kernel.contracts import KernelStatus, RecipeInputs
from bridges.kernel.executor import NodeKernel
from bridges.kernel.guard import RunCommitGuard
from bridges.kernel.repository import NodeKernelRepository
from bridges.paper.contracts import (
    PaperConstraints,
    PaperSearchProjection,
    PaperTermAnalysis,
)
from bridges.paper.kernel import (
    NODE_ENRICH,
    NODE_EVALUATE,
    NODE_PARSE,
    NODE_PLAN,
    NODE_READ,
    NODE_SCREEN,
    NODE_SEARCH,
    NODE_VERIFY,
    PAPER_GATE_HANDLERS,
    PaperBudget,
    PaperFlowContext,
    PaperNodeFlow,
    build_paper_recipe,
    claims_have_evidence_gate,
    identity_present_gate,
    paper_recipe_registry,
)
from bridges.paper.presenting import PaperSummaryGenerator
from bridges.paper.ranking import rank_candidates
from bridges.paper.reading import (
    GOAL_COMPARE,
    PaperReading,
    PaperSection,
    ReadCoordinator,
    supported_claims,
)
from bridges.paper.screening import screen_candidates
from bridges.paper.service import PaperSearchService
from bridges.paper.sources import PaperCandidate
from bridges.storage.database import BridgesDatabase
from tests.chat.test_chat_api import _create_conversation, _register
from tests.paper.test_paper_module_flow import (
    ATTENTION_CANDIDATES,
    ATTENTION_PAPERS,
    _FakePaperSource,
    _send,
)

NOW = datetime(2026, 10, 1, tzinfo=UTC)


def _analysis(
    *,
    original_phrase: str = "联邦学习",
    normalized_term: str = "federated learning",
    final_query: str = "federated learning",
    expansions: list[str] | None = None,
    constraints: PaperConstraints | None = None,
) -> PaperTermAnalysis:
    return PaperTermAnalysis(
        original_phrase=original_phrase,
        normalized_term=normalized_term,
        expansions=expansions or [],
        confidence=0.9,
        context_key="machine_learning",
        context_label="机器学习",
        constraints=constraints or PaperConstraints(),
        final_query=final_query,
    )


def _candidate(
    title: str,
    abstract: str,
    *,
    arxiv_id: str = "2401.00001",
    year: int = 2024,
) -> PaperCandidate:
    return PaperCandidate(
        arxiv_id=arxiv_id,
        title=title,
        authors=["A. Author"],
        published_at=datetime(year, 1, 1, tzinfo=UTC),
        abs_url=f"https://arxiv.org/abs/{arxiv_id}",
        pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
        abstract=abstract,
        primary_category="cs.LG",
    )


# ---------------------------------------------------------------------------
# 证据匹配与硬条件
# ---------------------------------------------------------------------------


def test_synonym_evidence_passes_without_literal_primary_term() -> None:
    """原词不字面命中：扩展词的同义证据仍判相关，并记录证据来源。"""
    analysis = _analysis(
        original_phrase="隐私保护训练",
        normalized_term="privacy-preserving training",
        final_query="privacy-preserving training",
        expansions=["federated learning"],
    )
    outcome = screen_candidates(
        analysis,
        [
            _candidate(
                "Decentralized Model Training",
                "We propose a federated learning method across devices.",
            )
        ],
    )
    assert len(outcome.screened) == 1
    entry = outcome.screened[0]
    assert entry.strength == "synonym"
    assert "federated learning" in entry.matched_requirements
    assert any(item["source"] == "abstract" for item in entry.evidence)
    assert not outcome.topic_mismatch


def test_primary_keyword_only_false_positive_is_excluded() -> None:
    """只命中原词、语境不符的候选（电力 transformer）不得进入推荐。"""
    from bridges.paper.parsing import parse_paper_request
    from bridges.paper.planning import plan_queries

    analysis = parse_paper_request("Transformer 的注意力机制入门", now=NOW)
    plan = plan_queries(analysis)[0]
    outcome = rank_candidates(
        analysis,
        plan,
        [
            _candidate(
                "Power Transformer Fault Diagnosis in Substations",
                "Monitoring of power transformer and substation grid voltage.",
            )
        ],
        {},
    )
    assert outcome.recommendations == []
    assert outcome.topic_mismatch
    assert any("语境不匹配" in note for note in outcome.notes)


def test_year_hard_condition_blocks_instead_of_widening() -> None:
    """年份条件内没有结果就交付空结果，不静默放宽。"""
    analysis = _analysis(
        constraints=PaperConstraints(year_from=2023, year_to=None),
    )
    outcome = screen_candidates(
        analysis,
        [
            _candidate(
                "Old Federated Learning Notes",
                "federated learning basics",
                year=2019,
            )
        ],
    )
    assert outcome.screened == []
    assert outcome.hard_condition_blocked == "year"
    assert any("未放宽年份" in note for note in outcome.notes)


# ---------------------------------------------------------------------------
# 分层阅读：摘要级诚实降级 / 正文断言
# ---------------------------------------------------------------------------


def test_abstract_scope_never_claims_body_level_conclusions() -> None:
    """未读全文：范围标为 abstract，方法/实验断言不成立。"""
    screened = screen_candidates(
        _analysis(expansions=["federated learning"]),
        [
            _candidate(
                "Federated Learning Survey",
                "federated learning survey of methods",
            )
        ],
    ).screened
    outcome = ReadCoordinator(None).read(
        screened, goal=GOAL_COMPARE, account_id="acc"
    )
    reading = outcome.readings["2401.00001"]
    assert reading.scope == "abstract"
    assert supported_claims(reading) == []
    assert any("摘要" in note for note in outcome.notes)


def test_full_text_sections_support_method_and_experiment_claims() -> None:
    """取得正文小节后，方法与实验断言才有对应依据。"""

    class _Reader:
        def read(self, candidate, *, sections, deadline, stop_event):
            del candidate, deadline, stop_event
            return PaperReading(
                arxiv_id="2401.00001",
                scope="full_text",
                sections=tuple(
                    PaperSection(name=name, text=f"{name} 正文", locator=name)
                    for name in sections
                ),
            )

    screened = screen_candidates(
        _analysis(expansions=["federated learning"]),
        [
            _candidate(
                "Federated Learning Survey",
                "federated learning survey of methods",
            )
        ],
    ).screened
    outcome = ReadCoordinator(_Reader()).read(
        screened, goal=GOAL_COMPARE, account_id="acc"
    )
    reading = outcome.readings["2401.00001"]
    claims = supported_claims(reading)
    assert "方法" in claims
    assert "实验" in claims


# ---------------------------------------------------------------------------
# 质量门：身份缺失 / 断言无证据
# ---------------------------------------------------------------------------


def _execution(payload: dict[str, Any]) -> Any:
    return SimpleNamespace(artifact=SimpleNamespace(payload=payload))


def test_identity_missing_blocks_conclusion() -> None:
    result = identity_present_gate(
        SimpleNamespace(), _execution({"identity_missing": ["某篇论文"]})
    )
    assert not result.passed
    assert result.code == "paper_identity_missing"


def test_claims_without_body_evidence_blocks() -> None:
    result = claims_have_evidence_gate(
        SimpleNamespace(), _execution({"unsupported_claims": ["方法断言"]})
    )
    assert not result.passed
    assert result.code == "paper_claim_without_body_evidence"


# ---------------------------------------------------------------------------
# 恢复：只执行未完成的有效节点
# ---------------------------------------------------------------------------


def _seed(database: BridgesDatabase) -> None:
    stamp = NOW.isoformat()
    with database.transaction():
        database.connection.execute(
            "INSERT OR IGNORE INTO conversations"
            "(conversation_id, account_id, title, mode, created_at, updated_at)"
            " VALUES ('conv-1', 'acc-1', '', 'companion', ?, ?)",
            (stamp, stamp),
        )
        database.connection.execute(
            "INSERT OR IGNORE INTO messages"
            "(message_id, conversation_id, account_id, role, status, content,"
            " created_at, updated_at)"
            " VALUES ('msg-assistant', 'conv-1', 'acc-1', 'assistant',"
            " 'streaming', '', ?, ?)",
            (stamp, stamp),
        )
        database.connection.execute(
            "INSERT OR IGNORE INTO generation_runs"
            "(run_id, account_id, conversation_id, user_message_id,"
            " assistant_message_id, status, lease_owner, lease_expires_at,"
            " stop_requested, created_at, updated_at)"
            " VALUES ('run-1', 'acc-1', 'conv-1', 'msg-user', 'msg-assistant',"
            " 'running', 'worker-1', ?, 0, ?, ?)",
            ((NOW + timedelta(minutes=5)).isoformat(), stamp, stamp),
        )


def _kernel(database: BridgesDatabase, runner: Any) -> NodeKernel:
    return NodeKernel(
        registry=paper_recipe_registry(),
        repository=NodeKernelRepository(database),
        guard=RunCommitGuard(
            ConversationRepository(database),
            account_id="acc-1",
            run_id="run-1",
            conversation_id="conv-1",
            assistant_message_id="msg-assistant",
            clock=lambda: NOW,
        ),
        gates=PAPER_GATE_HANDLERS,
        runner=runner,
        clock=lambda: NOW,
    )


def _inputs() -> RecipeInputs:
    return RecipeInputs(
        account_id="acc-1",
        conversation_id="conv-1",
        run_id="run-1",
        user_message_id="msg-user",
        user_content="Transformer 的注意力机制入门",
        task_id=None,
        task_version=None,
        wait_identity=None,
        artifacts={},
        prior_digest="",
    )


def test_recovery_reruns_only_unfinished_nodes(tmp_path: Path) -> None:
    """检索首次失败后重跑：解析/规划命中收据直接复用，只从检索继续。"""
    database = BridgesDatabase(tmp_path / "bridges.db")
    assert database.initialize() > 0
    _seed(database)

    first_calls: list[str] = []
    failing_source = _FakePaperSource(
        status=ModuleQueryStatus.ERROR,
        error_code="arxiv_offline",
        error_message="当前无法连接 arXiv。",
        retryable=True,
    )
    first_flow = PaperNodeFlow(
        source=failing_source, enricher=None, context=PaperFlowContext()
    )

    def first_runner(invocation: Any) -> Any:
        first_calls.append(invocation.spec.name)
        return first_flow.run_node(invocation)

    recipe = build_paper_recipe()
    first = _kernel(database, first_runner).execute(recipe=recipe, inputs=_inputs())
    assert first.status is KernelStatus.FAILED
    assert first_calls == [NODE_PARSE, NODE_PLAN, NODE_SEARCH]

    second_calls: list[str] = []
    healthy_source = _FakePaperSource(candidates=ATTENTION_CANDIDATES)
    second_flow = PaperNodeFlow(
        source=healthy_source, enricher=None, context=PaperFlowContext()
    )

    def second_runner(invocation: Any) -> Any:
        second_calls.append(invocation.spec.name)
        return second_flow.run_node(invocation)

    second = _kernel(database, second_runner).execute(recipe=recipe, inputs=_inputs())
    assert second.status is KernelStatus.COMPLETED
    assert second_calls == [
        NODE_SEARCH,
        NODE_SCREEN,
        NODE_READ,
        NODE_ENRICH,
        NODE_EVALUATE,
        NODE_VERIFY,
    ]


# ---------------------------------------------------------------------------
# 预算与初筛上限：拒绝即明确失败，不伪装成节点异常
# ---------------------------------------------------------------------------


class _BudgetLedger:
    """可控外部调用额度账本替身：允许或拒绝登记。"""

    def __init__(self, *, allowed: bool = True) -> None:
        self.allowed = allowed
        self.registered: list[str] = []

    def register_external_call(self, **kwargs: Any) -> bool:
        self.registered.append(str(kwargs["call_key"]))
        return self.allowed

    def record_external_call_result(self, **kwargs: Any) -> None:
        del kwargs

    def begin_adjustment(self, **kwargs: Any) -> bool:
        return self.allowed

    def end_adjustment(self, **kwargs: Any) -> None:
        del kwargs


def test_search_budget_refusal_fails_explicitly(tmp_path: Path) -> None:
    """首次检索就被预算拒绝：报 run_budget_exhausted，不发起真实检索。"""
    database = BridgesDatabase(tmp_path / "bridges.db")
    assert database.initialize() > 0
    _seed(database)

    source = _FakePaperSource(candidates=ATTENTION_CANDIDATES)
    flow = PaperNodeFlow(
        source=source,
        enricher=None,
        context=PaperFlowContext(),
        budget=PaperBudget(
            ledger=_BudgetLedger(allowed=False),
            account_id="acc-1",
            run_id="run-1",
            work_deadline=NOW + timedelta(minutes=5),
        ),
    )

    result = _kernel(database, flow.run_node).execute(
        recipe=build_paper_recipe(), inputs=_inputs()
    )
    assert result.status is KernelStatus.BLOCKED
    assert result.failure is not None
    assert result.failure.code == "run_budget_exhausted"
    assert source.queries == []


def test_screen_respects_frozen_screen_max(tmp_path: Path) -> None:
    """初筛最多处理冻结上限内的候选，截断如实写入说明。"""
    database = BridgesDatabase(tmp_path / "bridges.db")
    assert database.initialize() > 0
    _seed(database)

    many = [
        _candidate(
            f"Transformer Attention Study {index}",
            "We study the transformer attention mechanism in depth.",
            arxiv_id=f"2410.0000{index}",
        )
        for index in range(1, 5)
    ]
    flow = PaperNodeFlow(
        source=_FakePaperSource(candidates=many),
        enricher=None,
        context=PaperFlowContext(),
        budget=PaperBudget(
            ledger=_BudgetLedger(),
            account_id="acc-1",
            run_id="run-1",
            work_deadline=NOW + timedelta(minutes=5),
            screen_max=2,
        ),
    )

    result = _kernel(database, flow.run_node).execute(
        recipe=build_paper_recipe(), inputs=_inputs()
    )
    assert result.status is KernelStatus.COMPLETED
    screen = result.artifact(NODE_SCREEN)
    assert screen is not None
    assert len(screen.payload["screened"]) <= 2
    assert any("初筛上限" in note for note in screen.payload["notes"])


def test_body_scope_without_located_evidence_blocks(tmp_path: Path) -> None:
    """读取适配器宣称全文却拿不出小节证据：不得冒充精读，交付被阻塞。"""
    database = BridgesDatabase(tmp_path / "bridges.db")
    assert database.initialize() > 0
    _seed(database)

    class _EmptyFullTextReader:
        def read(self, candidate: Any, *, sections: Any, deadline: Any, stop_event: Any) -> Any:
            del candidate, sections, deadline, stop_event
            return PaperReading(arxiv_id="", scope="full_text", sections=())

    flow = PaperNodeFlow(
        source=_FakePaperSource(candidates=ATTENTION_CANDIDATES),
        enricher=None,
        reader=ReadCoordinator(_EmptyFullTextReader()),
        context=PaperFlowContext(reading_goal=GOAL_COMPARE),
    )

    result = _kernel(database, flow.run_node).execute(
        recipe=build_paper_recipe(), inputs=_inputs()
    )
    assert result.status is KernelStatus.BLOCKED
    assert result.failure is not None
    assert result.failure.code == "paper_claim_without_body_evidence"


# ---------------------------------------------------------------------------
# 表达策略与输出额度（Issue 21 × Issue 04）
# ---------------------------------------------------------------------------


class _SummaryGateway:
    """记录概述调用载荷的网关替身（不发起真实模型调用）。"""

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def invoke(
        self,
        capability: str,
        version: str,
        run_context: Any,
        payload: dict[str, Any],
        *,
        model_override: str | None = None,
        model_quota: Any = None,
    ) -> ModelCallResult:
        del capability, version, run_context, model_override, model_quota
        self.payloads.append(payload)
        return ModelCallResult(
            status=ModelCallStatus.SUCCESS,
            output={
                "summaries": [
                    {
                        "arxiv_id": "2301.00774",
                        "summary_zh": "综述概述测试。",
                        "evidence_source": "abstract",
                        "evidence_quote": (
                            "This survey reviews the attention mechanism "
                            "in transformer models."
                        ),
                    }
                ]
            },
        )


def test_summary_generator_uses_policy_system_block_and_output_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Manifest:
        class gate:  # noqa: N801 - 仅承载 within_budget 的简单替身
            within_budget = True

    monkeypatch.setattr(
        "bridges.paper.presenting.evaluate_call_manifest",
        lambda *args, **kwargs: _Manifest(),
    )
    gateway = _SummaryGateway()
    generator = PaperSummaryGenerator(gateway)
    expression = SimpleNamespace(
        version="policy-v9", output_tokens=777, system_block="表达规则：简洁。"
    )
    papers = [
        _candidate(
            ATTENTION_PAPERS[1].title,
            ATTENTION_PAPERS[1].abstract,
            arxiv_id=ATTENTION_PAPERS[1].arxiv_id,
            year=ATTENTION_PAPERS[1].published_at.year,
        )
    ]
    # 生成器只读取 PaperRecommendation 的标题/标识；用排序结果构造真实推荐。
    from bridges.paper.parsing import parse_paper_request
    from bridges.paper.planning import plan_queries

    analysis = parse_paper_request("Transformer 的注意力机制入门", now=NOW)
    plan = plan_queries(analysis)[0]
    recommendations = rank_candidates(analysis, plan, papers, {}).recommendations
    outcome = generator.generate(
        None,
        recommendations,
        abstracts={papers[0].arxiv_id: papers[0].abstract},
        model_id=None,
        expression=expression,
    )
    assert outcome.summaries == {"2301.00774": "综述概述测试。"}
    payload = gateway.payloads[0]
    assert payload["max_tokens"] == 777
    assert "表达规则：简洁。" in payload["messages"][0]["content"]


# ---------------------------------------------------------------------------
# 端到端：身份产物、表达策略版本与旧投影兼容
# ---------------------------------------------------------------------------


@pytest.fixture
def sqlite_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    from bridges.api.main import create_app
    from bridges.config import get_settings

    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "api-test-secret-key")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    return create_app()


@pytest.fixture
def client(sqlite_app: Any) -> TestClient:
    return TestClient(sqlite_app)


def test_success_projection_carries_identity_artifacts_and_policy_version(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """成功交付：选定身份、八节点产物与表达策略版本都可追溯。"""
    _register(client)
    gateway = _SummaryGateway()
    source = _FakePaperSource(candidates=ATTENTION_CANDIDATES)
    service = PaperSearchService(
        source=source,
        enricher=None,
        summarizer=PaperSummaryGenerator(gateway),
    )
    sqlite_app.state.paper_search_service = service
    sqlite_app.state.chat_service._paper_search = service  # noqa: SLF001
    conversation_id = _create_conversation(client)
    _send(client, conversation_id, "Transformer 的注意力机制入门", module_id="paper")
    generation_helpers["drive"](sqlite_app)
    projection = client.get(f"/chat/conversations/{conversation_id}").json()
    assistant = [
        message for message in projection["messages"] if message["role"] == "assistant"
    ][-1]

    assert assistant["status"] == "done"
    paper = assistant["paper_search"]
    assert paper["status"] == "success"
    assert paper["selected"], "选定论文必须有可核实身份"
    for item in paper["selected"]:
        assert item["arxiv_id"]
        assert item["title"]
        assert item["abs_url"].startswith("https://arxiv.org/abs/")
        assert item["content_hash"]
    assert set(paper["artifacts"]) >= {
        NODE_PARSE,
        NODE_PLAN,
        NODE_SEARCH,
        NODE_SCREEN,
        NODE_READ,
        NODE_ENRICH,
        NODE_EVALUATE,
        NODE_VERIFY,
    }
    assert paper["expression_policy_version"]
    assert "综述概述测试。" in assistant["content"]
    assert gateway.payloads, "概述调用必须真实发生"
    assert gateway.payloads[0]["max_tokens"] == DEFAULT_OUTPUT_TOKENS


def test_old_projection_without_new_fields_stays_readable() -> None:
    """旧结果（工单 11/12 投影）继续可读：新字段都有安全默认值。"""
    old = {
        "status": "success",
        "original_phrase": "Transformer",
        "normalized_term": "Transformer",
        "expansions": [],
        "confidence": 0.9,
        "queries": [],
        "final_query": "transformer",
        "papers": [],
        "requested_count": 3,
        "evidence_notes": [],
    }
    restored = PaperSearchProjection.model_validate(old)
    assert restored.status.value == "success"
    assert restored.selected == []
    assert restored.artifacts == {}
    assert restored.expression_policy_version is None
