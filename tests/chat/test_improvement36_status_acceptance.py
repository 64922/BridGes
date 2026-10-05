"""独立验收：模型措辞不得把系统状态改写为学生表现。"""

from types import SimpleNamespace

import pytest

import bridges.chat  # noqa: F401 - 先初始化既有聊天/学习组合根
from bridges.study.summary import build_summary, render_summary
from tests.chat.test_improvement36_evidence_bound_summary import (
    _invoke,
    _point,
    _StubCompiler,
    _unit_state,
    _valid_points,
)


@pytest.mark.parametrize("answer", [None, "尚未评分的答案"])
@pytest.mark.parametrize("text", ["本题答错，仍需补救。", "回答正确，已经掌握了全部内容。"])
def test_pending_text_comes_from_actual_state(answer: str | None, text: str) -> None:
    state = _unit_state(unanswered=True)
    assert state.review is not None
    state.review.questions[1].answer = answer
    summary = build_summary(
        _StubCompiler(), SimpleNamespace(config={}), state,
        _invoke([*_valid_points(), _point("unanswered", text, question_ids=["q2"])]),
    )
    pending = next(point for point in summary.points if point.kind == "unanswered")
    assert pending.text == (
        "本题未作答，不计为答对、错答或掌握。" if answer is None
        else "本题已作答，尚未判定，不计为答对、错答或掌握。"
    )
    assert text not in render_summary(summary, state)


def test_changed_basis_is_not_a_new_performance_gap() -> None:
    state = _unit_state()
    assert state.review is not None
    state.review.questions[0].fragment_ids = ["replaced:1"]
    summary = build_summary(
        _StubCompiler(), SimpleNamespace(config={}), state,
        _invoke([
            _valid_points()[0],
            _point("gap", "完全不懂斜率。", question_ids=["q1"]),
        ]),
    )
    gap = next(point for point in summary.points if point.kind == "gap")
    assert gap.text == "本题依据已更新，需重新确认；原判定保留为历史表现。"
    assert "完全不懂" not in render_summary(summary, state)
    assert state.review.questions[0].judgement == "correct"
