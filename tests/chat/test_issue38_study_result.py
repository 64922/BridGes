"""独立验收：学习公开结果不能把缺口、等待或总结失败称为完成。"""

from types import SimpleNamespace

import pytest

from bridges.chat.service import ChatService
from bridges.chat.turn_result import derive_turn_result
from bridges.contracts.chat import ChatMessageRole, ChatMessageStatus, TurnOutcome
from bridges.contracts.study import StudyExchange, StudyReview, StudyReviewQuestion, StudyState


def test_tutor_gap_is_partial_and_other_message_is_not_reused() -> None:
    state = StudyState(subsection_id="section", tutoring=[StudyExchange(
        user_message_id="user", assistant_message_id="assistant",
        question="问题", answer="有效部分", sources=[], gap="缺少依据",
    )])
    result = derive_turn_result(status=ChatMessageStatus.DONE, study=state.public_view(),
                                assistant_message_id="assistant", study_node="study.tutor")
    assert result.outcome is TurnOutcome.PARTIAL
    assert result.delivered and result.gaps
    other = derive_turn_result(status=ChatMessageStatus.DONE, study=state.public_view(),
                               assistant_message_id="other", study_node="study.tutor")
    assert not other.delivered and not other.gaps


@pytest.mark.parametrize("node", ["study.recognize", "study.plan_review", "study.grade"])
def test_study_wait_is_not_complete(node: str) -> None:
    result = derive_turn_result(status=ChatMessageStatus.DONE,
                                study=StudyState(subsection_id="section"), study_node=node)
    assert result.outcome is TurnOutcome.NEEDS_INPUT
    assert result.wait_reason


def test_summary_failure_preserves_only_this_answer_feedback() -> None:
    state = StudyState(subsection_id="section", review=StudyReview(questions=[
        StudyReviewQuestion(question_id="judged", question="当前题", coverage_units=[],
                            fragment_ids=[], asked=True, user_message_id="user",
                            judgement="correct", core_points=["私有依据"]),
        StudyReviewQuestion(question_id="future", question="未来题", coverage_units=[],
                            fragment_ids=[], canonical_answer="提前答案"),
    ]))
    result = derive_turn_result(status=ChatMessageStatus.ERROR,
                                error_code="study_summary_invalid", study=state.public_view(),
                                study_node="study.summarize", user_message_id="user")
    assert result.outcome is TurnOutcome.PARTIAL
    assert result.delivered and result.gaps
    assert result.recovery and result.recovery.retryable
    serialized = result.model_dump_json()
    assert all(private not in serialized for private in ["私有依据", "未来题", "提前答案"])


def test_ended_run_wait_reason_survives_history_projection() -> None:
    service = ChatService.__new__(ChatService)
    service._repo = SimpleNamespace(get_run_by_message=lambda *args: SimpleNamespace(
        wait_reason="请确认目的地", graph_version="daily-v1", current_node="commute.resolve",
        user_message_id="user",
    ))
    message = SimpleNamespace(role=ChatMessageRole.ASSISTANT, status=ChatMessageStatus.DONE,
        account_id="account", conversation_id="conversation", message_id="assistant",
        turn_result=None, route=None, error_code=None, paper_search=None, tieba_research=None,
        career_plan=None, learning_resources=None, commute_route=None, github_projects=None,
        web_search=None)
    result = service._turn_result_projection(message, None)
    assert result and result.outcome is TurnOutcome.NEEDS_INPUT
    assert result.wait_reason == "请确认目的地"


def test_external_error_details_do_not_enter_new_result() -> None:
    result = derive_turn_result(status=ChatMessageStatus.DONE, projections={
        "paper_search": {"status": "error", "error_message": "token=secret"},
    })
    assert "secret" not in result.model_dump_json()
