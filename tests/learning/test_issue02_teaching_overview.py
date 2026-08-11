"""Issue 02：学习模式直接交付全面介绍的教学投影 seam。"""

from datetime import UTC, datetime

from bridges.chat.turn import teaching_citation_error
from bridges.contracts.retrieval import (
    CitationProjection,
    RetrievalRoundProjection,
    RetrievalSourceLayer,
    RetrievalSufficiency,
)
from bridges.contracts.teaching import TeachingTurnProjection
from bridges.learning.teaching_gate import TeachingTurnService


def _turn_with_local_source() -> TeachingTurnProjection:
    return TeachingTurnService().prepare(
        "我想学习卷积神经网络的基础知识",
        retrieval=RetrievalRoundProjection(
            round_id="round-1",
            message_id="assistant-1",
            conversation_id="conversation-1",
            use_knowledge_base=True,
            sufficiency=RetrievalSufficiency.SUFFICIENT,
            layers=[],
            citations=[
                CitationProjection(
                    citation_id="citation-1",
                    source_layer=RetrievalSourceLayer.ATTACHMENT,
                    object_id="object-1",
                    filename="cnn.txt",
                    media_type="text/plain",
                    snippet="卷积神经网络的核心机制。",
                    rank=1,
                )
            ],
            created_at=datetime.now(UTC),
        ),
    )


def test_可执行目标不再生成计划课时或强制测验() -> None:
    turn = _turn_with_local_source()

    assert turn.mission is None
    assert turn.plan is None
    assert turn.lesson is None
    assert turn.quiz is None
    assert "不强制测验" in turn.check_method
    assert "全面介绍" in "".join(turn.steps)


def test_学习模式拒绝旧式或越界引用() -> None:
    turn = _turn_with_local_source()

    assert teaching_citation_error("正文 [reference:1] [web-999]", turn)
    assert teaching_citation_error("正文 [reference:2]", turn)
