"""独立验收回归：覆盖端到端证据、真实裁决和运行预算边界。"""

from __future__ import annotations

import threading
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from bridges.chat.repository import ConversationRepository
from bridges.contracts.ai import ModelCallResult, ModelCallStatus
from bridges.kernel.contracts import KernelStatus, RecipeInputs
from bridges.kernel.executor import NodeKernel
from bridges.kernel.guard import RunCommitGuard
from bridges.kernel.repository import NodeKernelRepository
from bridges.paper.contracts import (
    PaperConstraints,
    PaperRecommendation,
    PaperRequirementEvidence,
)
from bridges.paper.kernel import (
    NODE_EVALUATE,
    NODE_PARSE,
    NODE_SEARCH,
    NODE_VERIFY,
    PAPER_GATE_HANDLERS,
    PaperFlowContext,
    PaperNodeFlow,
    build_paper_recipe,
    paper_recipe_registry,
)
from bridges.paper.presenting import PaperSummaryGenerator
from bridges.paper.reading import GOAL_COMPARE, ReadCoordinator
from bridges.paper.screening import screen_candidates
from bridges.paper.service import PaperSearchService
from bridges.paper.sources import (
    EnrichedMetadata,
    EnrichOutcome,
    MetadataEnricher,
)
from bridges.storage.database import BridgesDatabase
from tests.chat.test_chat_api import _create_conversation, _register
from tests.chat.test_improvement15_task_materials import _quota
from tests.paper.test_paper_issue24 import (
    NOW,
    _analysis,
    _candidate,
    _inputs,
    _kernel,
    _seed,
)
from tests.paper.test_paper_module_flow import (
    ATTENTION_CANDIDATES,
    _FakePaperSource,
    _send,
    _StagedPaperSource,
)


def _seed_run(
    database: BridgesDatabase,
    *,
    run_id: str,
    assistant_message_id: str,
    created_at: datetime,
) -> None:
    stamp = created_at.isoformat()
    with database.transaction():
        database.connection.execute(
            "INSERT OR IGNORE INTO messages"
            "(message_id, conversation_id, account_id, role, status, content,"
            " created_at, updated_at)"
            " VALUES (?, 'conv-1', 'acc-1', 'assistant', 'streaming', '', ?, ?)",
            (assistant_message_id, stamp, stamp),
        )
        database.connection.execute(
            "INSERT OR IGNORE INTO generation_runs"
            "(run_id, account_id, conversation_id, user_message_id,"
            " assistant_message_id, status, lease_owner, lease_expires_at,"
            " stop_requested, created_at, updated_at)"
            " VALUES (?, 'acc-1', 'conv-1', 'msg-user', ?, 'running',"
            " 'worker-2', ?, 0, ?, ?)",
            (
                run_id,
                assistant_message_id,
                (created_at + timedelta(minutes=5)).isoformat(),
                stamp,
                stamp,
            ),
        )


def _run(tmp_path: Path, flow: PaperNodeFlow, content: str | None = None) -> Any:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    _seed(database)
    inputs = _inputs()
    inputs = replace(inputs, prior_digest=flow.prior_digest)
    if content is not None:
        inputs = replace(inputs, user_content=content)
    try:
        return _kernel(database, flow.run_node).execute(recipe=build_paper_recipe(), inputs=inputs)
    finally:
        database.close()


def test_synonym_evidence_survives_evaluate(tmp_path: Path) -> None:
    """语义裁判的真实摘要片段须通过排序，并进入选定论文产物。"""
    candidate = _candidate("Distributed Training", "Devices train models without sharing data.")

    class Judge:
        def judge(self, analysis: Any, paper: Any) -> Any:
            return {"relevant": True, "evidence": [{
                "requirement": analysis.original_phrase, "source": "abstract",
                "quote": paper.abstract,
            }]}

    flow = PaperNodeFlow(
        source=_FakePaperSource(candidates=[candidate]), enricher=None, judge=Judge(),
    )
    result = _run(tmp_path, flow, "找联邦学习论文")
    assert result.status is KernelStatus.COMPLETED
    assert result.artifact(NODE_EVALUATE).payload["selection"][0]["arxiv_id"] == candidate.arxiv_id


def test_unrelated_survey_is_not_topic_evidence() -> None:
    outcome = screen_candidates(
        _analysis(constraints=PaperConstraints(prefer_survey=True)),
        [_candidate("Ocean Survey", "A survey of ocean temperatures.")],
    )
    assert not outcome.screened


@pytest.mark.parametrize("quote,source", [
    ("fabricated quote", "abstract"), ("", "abstract"), ("Some unrelated text", "full_text"),
])
def test_judge_cannot_invent_evidence(quote: str, source: str) -> None:
    class Judge:
        def judge(self, analysis: Any, paper: Any) -> Any:
            return {"relevant": True, "evidence": [{
                "requirement": analysis.original_phrase, "source": source, "quote": quote,
            }]}

    outcome = screen_candidates(
        _analysis(), [_candidate("Ocean Temperatures", "Some unrelated text")], judge=Judge(),
    )
    assert not outcome.screened


@pytest.mark.parametrize("verdict", [{"passed": False}, {"text": "通过"}])
def test_independent_review_veto_blocks(tmp_path: Path, verdict: dict[str, Any]) -> None:
    flow = PaperNodeFlow(
        source=_FakePaperSource(candidates=ATTENTION_CANDIDATES), enricher=None,
        context=PaperFlowContext(comparison_requested=True),
        reviewer=lambda papers, conflicts: verdict,
    )
    result = _run(tmp_path, flow)
    assert result.status is KernelStatus.BLOCKED
    assert result.failure.code == "paper_independent_review_failed"


def test_relevance_shortfall_adjusts_once(tmp_path: Path) -> None:
    irrelevant = [
        _candidate("Power Transformer", "Power grid voltage", arxiv_id=f"2401.0000{i}")
        for i in range(3)
    ]
    source = _StagedPaperSource(irrelevant, ATTENTION_CANDIDATES)
    result = _run(tmp_path, PaperNodeFlow(source=source, enricher=None))
    assert result.status is KernelStatus.COMPLETED
    assert len(source.queries) == 2
    assert result.artifact(NODE_EVALUATE).payload["selection"]


class Budget:
    def __init__(self, *, allowed: bool = True) -> None:
        self.allowed = allowed
        self.calls: list[str] = []
        self.outcomes: list[str] = []

    def register_external(self, key: str, *, purpose: str) -> bool:
        self.calls.append(key)
        return self.allowed

    def release_external(self, key: str, *, outcome_code: str) -> None:
        self.outcomes.append(outcome_code)

    def deadline_seconds(self) -> float:
        return 10.0


@pytest.mark.parametrize("boundary", ["refusal", "deadline", "stop"])
def test_enrich_does_not_call_past_boundary(boundary: str) -> None:
    calls: list[str] = []
    event = threading.Event()
    if boundary == "stop":
        event.set()
    budget = Budget(allowed=boundary != "refusal")
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: calls.append(str(request.url)) or httpx.Response(500)
    )) as client:
        result = MetadataEnricher(client=client).enrich(
            [_candidate("Federated Learning", "federated learning")],
            account_id="a", need_publication_info=True, budget=budget, stop_event=event,
            deadline=time.monotonic() - 1 if boundary == "deadline" else None,
        )
    assert not calls
    assert not result.metadata


def test_enrich_stop_between_sources_preserves_obtained_metadata() -> None:
    event = threading.Event()
    calls: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        event.set()
        return httpx.Response(200, json={"message": {"items": [{
            "title": ["Federated Learning"], "DOI": "10.1234/actual",
        }]}})

    budget = Budget()
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = MetadataEnricher(client=client).enrich(
            [_candidate("Federated Learning", "federated learning")],
            account_id="a", need_publication_info=True, budget=budget, stop_event=event,
        )
    assert calls == ["api.crossref.org"]
    assert result.metadata["2401.00001"].doi == "10.1234/actual"
    assert budget.outcomes == ["matched"]


def test_failed_body_reads_respect_attempt_limit_and_keep_failure_audit() -> None:
    class FailingReader:
        def read(self, candidate: Any, **kwargs: Any) -> Any:
            raise OSError("读取失败")

    screened = screen_candidates(_analysis(), [
        _candidate("Federated Learning", "federated learning", arxiv_id=f"2401.0000{i}")
        for i in range(4)
    ]).screened
    budget = Budget()
    outcome = ReadCoordinator(FailingReader(), deep_read_max=2).read(
        screened, goal=GOAL_COMPARE, account_id="a", budget=budget,
    )
    assert len(budget.calls) == 2
    assert budget.outcomes == ["failed", "failed"]
    assert all(reading.scope == "abstract" for reading in outcome.readings.values())


def test_relative_year_uses_frozen_run_time(tmp_path: Path) -> None:
    reference = datetime(2025, 12, 31, tzinfo=UTC)
    flow = PaperNodeFlow(
        source=_FakePaperSource(), enricher=None,
        context=PaperFlowContext(reference_time=reference),
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )
    result = _run(tmp_path, flow, "找近三年联邦学习论文")
    constraints = result.artifact(NODE_PARSE).payload["analysis"]["constraints"]
    assert constraints["year_from"] == 2022
    newer = PaperNodeFlow(source=_FakePaperSource(), enricher=None,
        context=PaperFlowContext(reference_time=datetime(2026, 1, 1, tzinfo=UTC)))
    assert newer.prior_digest != flow.prior_digest


# ---------------------------------------------------------------------------
# 阻断复核 1：同义证据必须穿过 evaluate 与交付前原词覆盖核对
# ---------------------------------------------------------------------------


def test_service_cover_original_phrase_accepts_synonym_evidence() -> None:
    from bridges.paper.ranking import cover_original_phrase

    analysis = _analysis(
        original_phrase="隐私保护训练",
        normalized_term="privacy-preserving training",
        final_query="privacy-preserving training",
        expansions=["federated learning"],
    )
    recommendation = PaperRecommendation(
        order=1,
        arxiv_id="2401.00001",
        title="Distributed Training",
        source="arxiv",
        abs_url="https://arxiv.org/abs/2401.00001",
        role="recent",
        reason_zh="测试",
        match_basis="",
        match_evidence=[
            PaperRequirementEvidence(
                requirement="federated learning",
                source="abstract",
                quote="We propose a federated learning method.",
            )
        ],
    )
    assert cover_original_phrase(analysis, [recommendation]) is True


# ---------------------------------------------------------------------------
# 阻断复核 2：指定论文与来源硬条件
# ---------------------------------------------------------------------------


def test_specified_arxiv_id_is_the_only_result(tmp_path: Path) -> None:
    wanted = _candidate(
        "Distributed Training",
        "Devices train models without sharing data.",
        arxiv_id="2401.00001",
    )
    other = _candidate("Other Work", "Something unrelated.", arxiv_id="2401.00002")
    result = _run(
        tmp_path,
        PaperNodeFlow(source=_FakePaperSource(candidates=[other, wanted]), enricher=None),
        "帮我找 arXiv:2401.00001 这篇论文",
    )
    assert result.status is KernelStatus.COMPLETED
    selection = result.artifact(NODE_EVALUATE).payload["selection"]
    assert [item["arxiv_id"] for item in selection] == ["2401.00001"]
    search = result.artifact(NODE_SEARCH).payload
    assert len(search["attempts"]) == 1, "指定论文不做第二轮调整"


def test_specified_arxiv_id_not_found_stays_empty_without_substitute(
    tmp_path: Path,
) -> None:
    other = _candidate("Other Work", "Something unrelated.", arxiv_id="2401.00002")
    result = _run(
        tmp_path,
        PaperNodeFlow(source=_FakePaperSource(candidates=[other]), enricher=None),
        "帮我找 arXiv:2401.00001 这篇论文",
    )
    assert result.status is KernelStatus.COMPLETED
    evaluate = result.artifact(NODE_EVALUATE).payload
    assert evaluate["outcome"] == "empty"
    assert evaluate["selection"] == []
    assert any("指定论文" in note for note in evaluate["notes"])


def test_specified_title_selects_exact_paper(tmp_path: Path) -> None:
    titled = _candidate(
        "Attention Is All You Need",
        "We propose the Transformer.",
        arxiv_id="1706.03762",
    )
    other = _candidate("Different Work", "nothing here", arxiv_id="2401.00002")
    result = _run(
        tmp_path,
        PaperNodeFlow(source=_FakePaperSource(candidates=[other, titled]), enricher=None),
        "帮我找标题《Attention Is All You Need》的论文",
    )
    assert result.status is KernelStatus.COMPLETED
    selection = result.artifact(NODE_EVALUATE).payload["selection"]
    assert [item["arxiv_id"] for item in selection] == ["1706.03762"]


def test_excluded_arxiv_source_blocks_before_any_search(tmp_path: Path) -> None:
    source = _FakePaperSource(candidates=ATTENTION_CANDIDATES)
    result = _run(
        tmp_path,
        PaperNodeFlow(source=source, enricher=None),
        "不要用 arxiv，帮我找联邦学习论文",
    )
    assert result.status is KernelStatus.NEEDS_INPUT
    assert source.queries == [], "来源硬条件在检索前阻塞，不静默改用 arXiv"
    clarification = result.artifact(NODE_PARSE).payload["clarification"]
    assert clarification["missing"] == "source"


def test_metadata_sources_respect_excluded_constraints(tmp_path: Path) -> None:
    class _RecordingEnricher:
        def __init__(self) -> None:
            self.calls: list[Any] = []

        def enrich(self, candidates: Any, **kwargs: Any) -> EnrichOutcome:
            del candidates
            self.calls.append(kwargs.get("sources"))
            return EnrichOutcome()

    enricher = _RecordingEnricher()
    result = _run(
        tmp_path,
        PaperNodeFlow(
            source=_FakePaperSource(candidates=_FEDERATED_CANDIDATES),
            enricher=enricher,  # type: ignore[arg-type]
        ),
        "找联邦学习论文，不要用 crossref 和 openalex 补充",
    )
    assert result.status is KernelStatus.COMPLETED
    assert enricher.calls == [], "被禁止的元数据来源不得调用"
    notes = result.artifact("paper.enrich").payload["notes"]
    assert any("来源条件未允许" in note for note in notes)


def test_enricher_only_calls_selected_sources() -> None:
    calls: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        return httpx.Response(200, json={"results": []})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        MetadataEnricher(client=client).enrich(
            [_candidate("Federated Learning", "federated learning")],
            account_id="a",
            need_publication_info=True,
            sources=["openalex"],
        )
    assert calls == ["api.openalex.org"]


# ---------------------------------------------------------------------------
# 阻断复核 3/5：独立复核触发、通过与未装配语义
# ---------------------------------------------------------------------------


def test_comparison_request_from_body_triggers_reviewer(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    def reviewer(papers: Any, conflicts: Any) -> Any:
        seen["called"] = True
        seen["conflicts"] = list(conflicts)
        return {"passed": True, "reason": "ok"}

    flow = PaperNodeFlow(
        source=_FakePaperSource(candidates=ATTENTION_CANDIDATES),
        enricher=None,
        reviewer=reviewer,
    )
    result = _run(tmp_path, flow, "比较 Transformer 的注意力机制")
    assert seen.get("called") is True, "正文比较请求必须触发独立复核"
    review = result.artifact(NODE_VERIFY).payload["independent_review"]
    assert review["triggered"] is True and review["executed"] is True
    assert result.status is KernelStatus.COMPLETED


def test_source_conflict_triggers_reviewer(tmp_path: Path) -> None:
    class _ConflictEnricher:
        def enrich(self, candidates: Any, **kwargs: Any) -> EnrichOutcome:
            del kwargs
            return EnrichOutcome(
                metadata={
                    candidate.arxiv_id: EnrichedMetadata(
                        source="crossref",
                        year=candidate.published_at.year - 5,
                        doi="10.1/conflict",
                    )
                    for candidate in candidates
                }
            )

    seen: dict[str, Any] = {}

    def reviewer(papers: Any, conflicts: Any) -> Any:
        seen["conflicts"] = list(conflicts)
        return {"passed": True, "reason": "ok"}

    flow = PaperNodeFlow(
        source=_FakePaperSource(candidates=ATTENTION_CANDIDATES),
        enricher=_ConflictEnricher(),  # type: ignore[arg-type]
        reviewer=reviewer,
    )
    result = _run(tmp_path, flow, "Transformer 的注意力机制入门")
    assert result.status is KernelStatus.COMPLETED
    assert seen["conflicts"], "来源年份冲突必须进入独立复核材料"
    review = result.artifact(NODE_VERIFY).payload["independent_review"]
    assert review["executed"] is True


def test_reviewer_absent_is_marked_unreviewed_not_passed(tmp_path: Path) -> None:
    flow = PaperNodeFlow(
        source=_FakePaperSource(candidates=ATTENTION_CANDIDATES),
        enricher=None,
        context=PaperFlowContext(reading_goal=GOAL_COMPARE),
    )
    result = _run(tmp_path, flow, "Transformer 的注意力机制入门")
    assert result.status is KernelStatus.COMPLETED
    verify = result.artifact(NODE_VERIFY).payload
    assert verify["independent_review"] == {"triggered": True, "executed": False}
    assert any("未装配" in note for note in verify["notes"])
    assert any("独立复核未执行" in item for item in verify["unconfirmed"])


# ---------------------------------------------------------------------------
# 阻断复核 4：概述必须绑定逐字来源证据
# ---------------------------------------------------------------------------


class _OutputGateway:
    def __init__(self, output: dict[str, Any]) -> None:
        self.output = output
        self.payloads: list[dict[str, Any]] = []

    def invoke(
        self,
        capability: str,
        version: str,
        context: Any,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> ModelCallResult:
        del capability, version, context, kwargs
        self.payloads.append(payload)
        return ModelCallResult(status=ModelCallStatus.SUCCESS, output=self.output)


def _summary_paper() -> PaperRecommendation:
    return PaperRecommendation(
        order=1,
        arxiv_id="2401.00001",
        title="Federated Learning Systems",
        source="arxiv",
        abs_url="https://arxiv.org/abs/2401.00001",
        role="recent",
        reason_zh="测试",
        match_basis="federated learning",
    )


def test_summary_without_locatable_evidence_is_dropped() -> None:
    abstract = "We study federated learning across devices."
    gateway = _OutputGateway({"summaries": [{
        "arxiv_id": "2401.00001",
        "summary_zh": "该工作研究联邦学习。",
        "evidence_source": "abstract",
        "evidence_quote": "fabricated quote",
    }]})
    outcome = PaperSummaryGenerator(gateway).generate(
        None,
        [_summary_paper()],
        abstracts={"2401.00001": abstract},
        model_id=None,
        model_quota=_quota(32000),
    )
    assert outcome.summaries == {}
    assert outcome.evidence == {}
    assert outcome.dropped == 1
    assert "丢弃" in (outcome.note or "")


def test_summary_with_verbatim_evidence_keeps_binding() -> None:
    abstract = "We study federated learning across devices."
    gateway = _OutputGateway({"summaries": [{
        "arxiv_id": "2401.00001",
        "summary_zh": "该工作研究联邦学习。",
        "evidence_source": "abstract",
        "evidence_quote": "federated learning across devices",
    }]})
    outcome = PaperSummaryGenerator(gateway).generate(
        None,
        [_summary_paper()],
        abstracts={"2401.00001": abstract},
        model_id=None,
        model_quota=_quota(32000),
    )
    assert outcome.summaries == {"2401.00001": "该工作研究联邦学习。"}
    assert outcome.evidence["2401.00001"].source == "abstract"
    assert outcome.evidence["2401.00001"].quote in abstract


# ---------------------------------------------------------------------------
# 阻断复核 6：无扩展词时的一轮调整可追溯
# ---------------------------------------------------------------------------


def test_adjustment_without_expansions_is_traceable(tmp_path: Path) -> None:
    sparse = [
        _candidate("Power Transformer", "Power grid voltage", arxiv_id=f"2401.3000{i}")
        for i in range(3)
    ]
    federated = [
        _candidate(
            f"Federated Learning Study {index}",
            "federated learning methods for devices",
            arxiv_id=f"2401.4000{index}",
        )
        for index in range(3)
    ]
    source = _StagedPaperSource(sparse, federated)
    result = _run(
        tmp_path,
        PaperNodeFlow(source=source, enricher=None),
        "帮我找联邦学习论文",
    )
    assert result.status is KernelStatus.COMPLETED
    assert len(source.queries) == 2, "整次运行最多一轮调整"
    search = result.artifact(NODE_SEARCH).payload
    assert search["adjustment"]["used"] is True
    assert search["adjustment"]["reason"] == "paper_relevance_insufficient"
    assert search["adjustment"]["outcome"] == "success"
    assert [item["adopted"] for item in search["attempts"]] == [False, True]
    assert search["attempts"][1]["relevant_count"] >= 3


# ---------------------------------------------------------------------------
# 阻断复核 7：恢复失效策略（冻结日期 + 产物复用/失效）
# ---------------------------------------------------------------------------

_FEDERATED_CANDIDATES = [
    _candidate(
        f"Federated Learning Study {index}",
        "federated learning methods for devices",
        arxiv_id=f"2401.5000{index}",
        year=2023,
    )
    for index in range(3)
]


def _inputs_for(run_id: str, assistant_message_id: str, flow: PaperNodeFlow) -> Any:
    return RecipeInputs(
        account_id="acc-1",
        conversation_id="conv-1",
        run_id=run_id,
        user_message_id="msg-user",
        user_content="找近三年联邦学习论文",
        task_id=None,
        task_version=None,
        wait_identity=None,
        artifacts={},
        prior_digest=flow.prior_digest,
    )


def _execute(
    database: BridgesDatabase,
    flow: PaperNodeFlow,
    *,
    run_id: str,
    assistant_message_id: str,
    clock: datetime = NOW,
) -> Any:
    kernel = NodeKernel(
        registry=paper_recipe_registry(),
        repository=NodeKernelRepository(database),
        guard=RunCommitGuard(
            ConversationRepository(database),
            account_id="acc-1",
            run_id=run_id,
            conversation_id="conv-1",
            assistant_message_id=assistant_message_id,
            clock=lambda: clock,
        ),
        gates=PAPER_GATE_HANDLERS,
        runner=flow.run_node,
        clock=lambda: clock,
    )
    return kernel.execute(
        recipe=build_paper_recipe(),
        inputs=_inputs_for(run_id, assistant_message_id, flow),
    )


def test_same_run_keeps_frozen_year_and_reuses_artifacts(tmp_path: Path) -> None:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    _seed(database)
    source = _FakePaperSource(candidates=_FEDERATED_CANDIDATES)
    frozen = datetime(2025, 12, 31, tzinfo=UTC)
    first = _execute(
        database,
        PaperNodeFlow(
            source=source, enricher=None, context=PaperFlowContext(reference_time=frozen)
        ),
        run_id="run-1",
        assistant_message_id="msg-assistant",
    )
    assert first.status is KernelStatus.COMPLETED
    queries_after_first = list(source.queries)
    assert first.artifact(NODE_PARSE).payload["analysis"]["constraints"]["year_from"] == 2022

    second = _execute(
        database,
        PaperNodeFlow(
            source=source, enricher=None,
            context=PaperFlowContext(reference_time=frozen),
            clock=lambda: datetime(2026, 6, 1, tzinfo=UTC),
        ),
        run_id="run-1",
        assistant_message_id="msg-assistant",
    )
    assert second.status is KernelStatus.COMPLETED
    assert source.queries == queries_after_first, "同一运行跨年只用冻结结果"
    constraints = second.artifact(NODE_PARSE).payload["analysis"]["constraints"]
    assert constraints["year_from"] == 2022, "冻结年份不随墙钟漂移"
    assert all(node.reused for node in second.nodes)
    database.close()


def test_new_run_same_day_reuses_search_artifact(tmp_path: Path) -> None:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    _seed(database)
    _seed_run(
        database,
        run_id="run-2",
        assistant_message_id="msg-assistant-2",
        created_at=NOW,
    )
    source = _FakePaperSource(candidates=_FEDERATED_CANDIDATES)
    first = _execute(
        database,
        PaperNodeFlow(source=source, enricher=None, context=PaperFlowContext(reference_time=NOW)),
        run_id="run-1",
        assistant_message_id="msg-assistant",
    )
    assert first.status is KernelStatus.COMPLETED
    queries_after_first = list(source.queries)

    second = _execute(
        database,
        PaperNodeFlow(source=source, enricher=None, context=PaperFlowContext(reference_time=NOW)),
        run_id="run-2",
        assistant_message_id="msg-assistant-2",
    )
    assert second.status is KernelStatus.COMPLETED
    assert source.queries == queries_after_first, "同日新运行按内容与日期复用检索产物"
    assert any(node.reused for node in second.nodes)
    database.close()


def test_new_run_next_day_invalidates_search_and_reparses_year(tmp_path: Path) -> None:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    _seed(database)
    next_day = datetime(2026, 1, 1, tzinfo=UTC)
    _seed_run(
        database,
        run_id="run-3",
        assistant_message_id="msg-assistant-3",
        created_at=next_day,
    )
    source = _FakePaperSource(candidates=_FEDERATED_CANDIDATES)
    reference = datetime(2025, 12, 31, tzinfo=UTC)
    first = _execute(
        database,
        PaperNodeFlow(
            source=source, enricher=None, context=PaperFlowContext(reference_time=reference)
        ),
        run_id="run-1",
        assistant_message_id="msg-assistant",
    )
    assert first.status is KernelStatus.COMPLETED
    queries_after_first = list(source.queries)
    assert first.artifact(NODE_PARSE).payload["analysis"]["constraints"]["year_from"] == 2022

    second = _execute(
        database,
        PaperNodeFlow(
            source=source, enricher=None,
            context=PaperFlowContext(reference_time=next_day),
        ),
        run_id="run-3",
        assistant_message_id="msg-assistant-3",
        clock=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
    )
    assert second.status is KernelStatus.COMPLETED
    assert source.queries != queries_after_first, "跨日新运行必须重新检索，不复用过期结果"
    constraints = second.artifact(NODE_PARSE).payload["analysis"]["constraints"]
    assert constraints["year_from"] == 2023, "跨年相对年份按新运行冻结时间重解析"
    database.close()


# ---------------------------------------------------------------------------
# 阻断复核 3：生产 judge/reviewer 经登记网关装配并走真实预算/清单/复核
# ---------------------------------------------------------------------------


class _StructuredGateway:
    """按调用 schema 返回结构化结果的登记网关替身（记录每次调用）。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def invoke(
        self,
        capability: str,
        version: str,
        context: Any,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> ModelCallResult:
        del capability, version, context, kwargs
        schema = payload.get("json_schema") or {}
        properties = set((schema.get("properties") or {}).keys())
        if "matches" in properties:
            self.calls.append("judge")
            output: dict[str, Any] = {
                "matches": [
                    {
                        "arxiv_id": "2401.00001",
                        "relevant": True,
                        "evidence": [
                            {
                                "requirement": "联邦学习",
                                "source": "abstract",
                                "quote": (
                                    "Devices train models without sharing data."
                                ),
                            }
                        ],
                    }
                ]
            }
        elif "summaries" in properties:
            self.calls.append("summary")
            output = {
                "summaries": [
                    {
                        "arxiv_id": "2401.00001",
                        "summary_zh": "该工作研究设备协同训练。",
                        "evidence_source": "abstract",
                        "evidence_quote": (
                            "Devices train models without sharing data."
                        ),
                    }
                ]
            }
        else:
            self.calls.append("review")
            output = {"passed": True, "reason": "ok", "evidence": []}
        return ModelCallResult(status=ModelCallStatus.SUCCESS, output=output)


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


def test_production_gateway_judge_and_summary_end_to_end(
    sqlite_app: Any,
    client: TestClient,
    generation_helpers: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register(client)
    candidate = _candidate(
        "Distributed Training",
        "Devices train models without sharing data.",
        arxiv_id="2401.00001",
    )
    gateway = _StructuredGateway()
    service = PaperSearchService(
        source=_FakePaperSource(candidates=[candidate]),
        enricher=None,
        summarizer=PaperSummaryGenerator(gateway),
        gateway=gateway,
    )
    sqlite_app.state.paper_search_service = service
    sqlite_app.state.chat_service._paper_search = service  # noqa: SLF001
    monkeypatch.setattr(
        sqlite_app.state.chat_service,
        "run_model_quota",
        lambda run: _quota(32000),
        raising=False,
    )
    conversation_id = _create_conversation(client)
    _send(client, conversation_id, "帮我找联邦学习方向的论文", module_id="paper")
    generation_helpers["drive"](sqlite_app)
    projection = client.get(f"/chat/conversations/{conversation_id}").json()
    assistant = [
        message for message in projection["messages"] if message["role"] == "assistant"
    ][-1]

    assert assistant["status"] == "done", assistant.get("error_message")
    paper = assistant["paper_search"]
    assert paper["status"] == "success"
    assert paper["papers"], "同义证据候选应端到端交付"
    first = paper["papers"][0]
    assert first["match_evidence"], "模型证据经原文复核后必须保留"
    assert first["summary_zh"]
    assert first["summary_evidence"]["quote"] in candidate.abstract
    assert "judge" in gateway.calls, "生产 judge 必须装配并调用批准能力"
    assert "summary" in gateway.calls
