"""独立验收反例：补证顺序、真实来源及预算硬门。"""

from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from bridges.contracts.retrieval import (
    CitationAccessStatus,
    RetrievalSourceLayer,
    RetrievalSufficiency,
)
from bridges.contracts.study import StudySource, StudyState, StudySupplementAttempt
from bridges.study import evidence


def _gap(layer="knowledge_base", point="斜率应用"):
    return {"key_points": [point], "supported": [], "gaps": [
        {"point": point, "supplement": layer, "reason": "本节未覆盖"}
    ]}


def _supported(ref="kb-1"):
    return {"key_points": ["斜率应用"], "supported": [
        {"point": "斜率应用", "source_ids": [ref]}
    ], "gaps": []}


def _run(monkeypatch, assessments, *, kb_sources=True, question="解释斜率应用", budget=None):
    events = []
    page = StudySource(source_id="page-1", kind="page", label="书页", snippet="本节公式")
    kb = StudySource(source_id="kb-1", kind="knowledge_base", label="笔记", snippet="应用依据")
    web = StudySource(source_id="web-1", kind="web", label="公开来源", snippet="公开应用依据")
    service = SimpleNamespace()

    def compile_context(run, *, evidence, **kwargs):
        return [], {"budget_floor_exceeded": False,
                    "adopted_evidence_ids": [item.evidence_id for item in evidence]}

    service.compile_turn_context = compile_context

    def assess(capability, payload):
        events.append("评估")
        response = assessments.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def local(*args, **kwargs):
        events.append("知识库")
        return ([kb] if kb_sources else []), StudySupplementAttempt(
            layer="knowledge_base", status="used" if kb_sources else "empty",
        )

    def public(service, run, query, **kwargs):
        events.append(("公网", query))
        return [web], [], StudySupplementAttempt(layer="web", status="used")

    monkeypatch.setattr(evidence, "_knowledge_base_sources", local)
    monkeypatch.setattr(evidence, "_web_sources", public)
    result = evidence.gather_evidence(
        service, SimpleNamespace(config={}), StudyState(subsection_id="section"),
        question, [page], assess,
        budget=budget,
    )
    return result, events


def test_knowledge_base_resolves_gap_before_network(monkeypatch):
    result, events = _run(monkeypatch, [_gap(), _supported()])
    assert events == ["评估", "知识库", "评估"]
    assert result.assessment.sufficient
    assert result.assessment.supported_points == ["斜率应用"]


def test_remaining_gap_automatically_reaches_web(monkeypatch):
    result, events = _run(monkeypatch, [_gap(), _gap("web", "最新斜率实例"), _supported("web-1")])
    assert [item if isinstance(item, str) else item[0] for item in events] == [
        "评估", "知识库", "评估", "公网", "评估",
    ]
    assert "最新斜率实例" in events[3][1].replace(" ", "")
    assert result.assessment.sufficient


def test_empty_knowledge_base_falls_back_to_web(monkeypatch):
    result, events = _run(monkeypatch, [_gap(), _supported("web-1")], kb_sources=False)
    assert [item if isinstance(item, str) else item[0] for item in events] == [
        "评估", "知识库", "公网", "评估",
    ]
    assert result.assessment.sufficient


def test_initial_web_gap_checks_enabled_knowledge_base_first(monkeypatch):
    result, events = _run(monkeypatch, [_gap("web"), _supported()])
    assert events == ["评估", "知识库", "评估"]
    assert result.assessment.sufficient


@pytest.mark.parametrize("response", [{}, {"key_points": ["关键解释点"]}])
def test_empty_assessment_never_passes(monkeypatch, response):
    result, events = _run(monkeypatch, [response])
    assert not result.assessment.sufficient
    assert events == ["评估"]


def test_fabricated_support_is_not_promoted_to_generation_constraint(monkeypatch):
    result, _ = _run(monkeypatch, [_supported("invented")])
    assert result.assessment.supported_points == []
    assert not result.assessment.sufficient


def test_reassessment_failure_keeps_gap_and_uses_web(monkeypatch):
    result, events = _run(monkeypatch, [_gap(), ValueError("复查失败"), _supported("web-1")])
    assert any(isinstance(item, tuple) for item in events)
    assert "复查未完成" in "".join(result.notes)


def test_explicit_network_request_survives_exhausted_automatic_budget(monkeypatch):
    budget = Mock()
    budget.begin_adjustment.return_value = False
    result, events = _run(
        monkeypatch, [_gap(), _supported("web-1")], question="联网解释斜率应用", budget=budget,
    )
    assert "知识库" not in events
    assert any(isinstance(item, tuple) for item in events)
    assert result.assessment.sufficient
    budget.end_adjustment.assert_not_called()


def test_automatic_supplements_share_one_adjustment(monkeypatch):
    budget = Mock()
    budget.begin_adjustment.return_value = True
    _run(monkeypatch, [_gap(), _gap("web"), _supported("web-1")], budget=budget)
    budget.begin_adjustment.assert_called_once_with(reason_code="study_evidence_gap")
    budget.end_adjustment.assert_called_once()


@pytest.mark.parametrize("with_citation", [False, True])
def test_knowledge_base_conflict_is_not_an_adoptable_source(with_citation):
    retrieval = Mock()
    citation = SimpleNamespace(
        source_layer=RetrievalSourceLayer.KNOWLEDGE_BASE, citation_id="kb-conflict",
        page_number=1, filename="争议笔记", snippet="互相矛盾的定义", object_id="object",
    )
    retrieval.citation_detail.return_value = SimpleNamespace(
        access_status=CitationAccessStatus.ACCESSIBLE,
    )
    retrieval.run_round.return_value = SimpleNamespace(
        citations=[citation] if with_citation else [],
        sufficiency=RetrievalSufficiency.CONFLICT, note="两个定义不一致",
    )
    run = SimpleNamespace(account_id="a", conversation_id="c", assistant_message_id="m",
                          user_message_id="u")
    sources, attempt = evidence._knowledge_base_sources(
        SimpleNamespace(_retrieval=retrieval), run, "斜率", [],
    )
    assert sources == []
    assert attempt.status == "conflict"
    assert "两个定义不一致" in attempt.detail
    if with_citation:
        assert "争议笔记" in attempt.detail


def test_unjudged_key_point_never_counts_as_sufficient(monkeypatch):
    response = _supported("page-1")
    response["key_points"].append("未判断的关键公式")
    result, _ = _run(monkeypatch, [response])
    assert not result.assessment.sufficient
    assert any(gap.point == "未判断的关键公式" for gap in result.assessment.gaps)


@pytest.mark.parametrize("allowed", [False, True])
def test_public_call_obeys_budget_and_releases_slot_after_failure(allowed):
    web = Mock()
    web.plan.return_value = SimpleNamespace(should_search=True)
    web.search.side_effect = RuntimeError("不应输出内部异常正文")
    budget = Mock()
    budget.register_external_call.return_value = allowed
    budget.public_search_deadlines.return_value = SimpleNamespace(
        provider_deadline=12.0, stage_deadline=13.0,
    )
    sources, _, attempt = evidence._web_sources(
        SimpleNamespace(_web_search=web), SimpleNamespace(account_id="a", user_message_id="u"),
        "斜率应用", stop_event=None, budget=budget,
    )
    assert sources == []
    assert attempt.status == ("failed" if allowed else "budget_exhausted")
    if allowed:
        web.search.assert_called_once_with(
            "a", web.plan.return_value, stop_event=None, deadline=12.0, stage_deadline=13.0,
        )
        budget.release_external_call.assert_called_once()
        assert "内部异常" not in attempt.detail
    else:
        web.search.assert_not_called()
        budget.release_external_call.assert_not_called()


def test_stopped_search_result_is_not_written_to_message():
    stop = Event()
    web, repo = Mock(), Mock()
    web.plan.return_value = SimpleNamespace(should_search=True)

    def late_result(*args, **kwargs):
        stop.set()
        return Mock()

    web.search.side_effect = late_result
    sources, _, attempt = evidence._web_sources(
        SimpleNamespace(_web_search=web, _repo=repo),
        SimpleNamespace(account_id="a", user_message_id="u"),
        "斜率应用", stop_event=stop,
    )
    assert sources == []
    assert "迟到" in attempt.detail
    repo.update_message_web_search.assert_not_called()
