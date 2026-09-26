"""``tieba.parse``：把用户问题拆成原始名词、事件／地点与时间要求。

原词逐字保留：检索查询词由原始名词组成，不把用户说法替换成别的词。信息
不足到无法检索时只问一个问题，并把恢复载荷写回消息，下一条回复从该处继续。
"""

from __future__ import annotations

from typing import Any

from bridges.tieba.contracts import TiebaClarification, TiebaQuestionAnalysis
from bridges.tieba.lexicon import (
    TARGET_FORUM_NAME,
    detect_official_topics,
    detect_place_or_event,
    detect_time_requirement,
    detect_time_year,
    extract_topic_terms,
)

#: 澄清问题的恢复载荷键（随消息持久化，跨会话重开仍然有效）。恢复时只需
#: 原始问题：时间条件每次都从原始问题重新解析，不另存一份可能过期的副本。
PENDING_ORIGINAL_QUESTION = "original_question"

CLARIFICATION_QUESTION = (
    f"你想在{TARGET_FORUM_NAME}里查什么话题？（例如：宿舍条件、转专业流程、食堂怎么样）"
)


def parse_tieba_request(
    content: str,
    *,
    pending: dict[str, Any] | None = None,
) -> TiebaQuestionAnalysis:
    """解析本轮问题；``pending`` 是上一轮等待中的恢复载荷。

    恢复轮里这一条消息就是用户对上一轮提问的回答，主题取自它，而原始问题
    与时间条件仍以首次提问为准。
    """
    current = content.strip()
    resumed_question = ""
    if pending:
        resumed_question = str(pending.get(PENDING_ORIGINAL_QUESTION) or "").strip()
    original_question = resumed_question or current

    terms = extract_topic_terms(current)
    time_requirement = detect_time_requirement(original_question)
    if time_requirement is None:
        time_requirement = detect_time_requirement(current)
    year = detect_time_year(original_question)
    if year is None:
        year = detect_time_year(current)
    official_topics = detect_official_topics(original_question, terms)

    analysis = TiebaQuestionAnalysis(
        original_question=original_question,
        topic_terms=terms,
        place_or_event=detect_place_or_event(terms),
        time_requirement=time_requirement,
        time_year=year,
        needs_official_check=bool(official_topics),
        official_topics=official_topics,
    )
    if not terms:
        return analysis.model_copy(
            update={
                "clarification": TiebaClarification(
                    question=CLARIFICATION_QUESTION,
                    reason="没有可检索的主题词，先确认要查的话题才能发起检索。",
                )
            }
        )
    return analysis


def pending_payload(analysis: TiebaQuestionAnalysis) -> dict[str, Any]:
    """把等待中的问题写成可持久化的恢复载荷。"""
    return {PENDING_ORIGINAL_QUESTION: analysis.original_question}
