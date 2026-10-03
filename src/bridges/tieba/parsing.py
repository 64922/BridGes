"""``tieba.parse``：把用户问题拆成原始名词、事件／地点、时间要求与问题类型。

原词逐字保留：检索查询词由原始名词组成，不把用户说法替换成别的词。信息
不足到无法检索时只问一个问题，并把恢复载荷写回消息，下一条回复从该处继续。
问题类型（规定／体验／混合）只由确定性词表判定：命中官方触发词且没有体验
信号是规定类，两者都有是混合类，其余是体验类。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from bridges.tieba.contracts import (
    TiebaClarification,
    TiebaQuestionAnalysis,
    TiebaQuestionKind,
)
from bridges.tieba.lexicon import (
    EXPERIENCE_SIGNALS,
    OFFICIAL_TRIGGERS,
    TARGET_FORUM_NAME,
    detect_campus_terms,
    detect_experience_signals,
    detect_official_topics,
    detect_place_or_event,
    detect_time_requirement,
    detect_time_year,
    extract_topic_terms,
)

if TYPE_CHECKING:
    from bridges.chat.task_materials import ModuleTaskContext

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
    module_context: ModuleTaskContext | None = None,
) -> TiebaQuestionAnalysis:
    """解析本轮问题；``pending`` 是上一轮等待中的恢复载荷。

    恢复轮里这一条消息就是用户对上一轮提问的回答，主题取自它，而原始问题
    与时间条件仍以首次提问为准。``module_context`` 是任务范围内的只读上下文：
    当前消息只有触发词（如「那规定呢」）时，用任务主题补足检索材料；当前
    消息自带实质名词时完全以消息为准。
    """
    current = content.strip()
    resumed_question = ""
    if pending:
        resumed_question = str(pending.get(PENDING_ORIGINAL_QUESTION) or "").strip()
    original_question = resumed_question or current

    terms = extract_topic_terms(current)
    task_topic = _task_topic(module_context)
    if task_topic and not _has_substantive(terms):
        merged = list(terms)
        for term in extract_topic_terms(task_topic):
            if term not in merged:
                merged.append(term)
        terms = merged
    time_requirement = detect_time_requirement(original_question)
    if time_requirement is None:
        time_requirement = detect_time_requirement(current)
    if time_requirement is None:
        time_requirement = _task_time_requirement(module_context)
    year = detect_time_year(original_question)
    if year is None:
        year = detect_time_year(current)
    if year is None and time_requirement:
        year = detect_time_year(time_requirement)
    official_topics = detect_official_topics(original_question, terms)
    experience_signals = detect_experience_signals(original_question, terms)
    question_kind = _classify(official_topics, experience_signals)

    analysis = TiebaQuestionAnalysis(
        original_question=original_question,
        topic_terms=terms,
        place_or_event=detect_place_or_event(terms),
        campus_terms=detect_campus_terms(original_question, terms),
        question_kind=question_kind,
        experience_signals=experience_signals,
        time_requirement=time_requirement,
        time_year=year,
        needs_official_check=question_kind is not TiebaQuestionKind.EXPERIENCE,
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


def _classify(
    official_topics: list[str], experience_signals: list[str]
) -> TiebaQuestionKind:
    if official_topics and experience_signals:
        return TiebaQuestionKind.MIXED
    if official_topics:
        return TiebaQuestionKind.POLICY
    return TiebaQuestionKind.EXPERIENCE


def _has_substantive(terms: list[str]) -> bool:
    """当前消息是否自带实质名词（只有官方触发词／体验信号时视为没有）。"""
    return any(
        term not in OFFICIAL_TRIGGERS and term not in EXPERIENCE_SIGNALS
        for term in terms
    )


def _task_topic(module_context: ModuleTaskContext | None) -> str:
    """任务范围内可用的主题文本（未使用任务范围时为空）。"""
    if module_context is None or not module_context.used_task_scope:
        return ""
    if module_context.topic_hint.strip():
        return module_context.topic_hint.strip()
    return module_context.task_goal.strip()


def _task_time_requirement(module_context: ModuleTaskContext | None) -> str | None:
    """任务条件里的时间原话（消息本身没有时间条件时补足）。"""
    if module_context is None or not module_context.used_task_scope:
        return None
    for condition in module_context.effective_conditions:
        if condition.kind in {"time", "year", "year_range"} and condition.text.strip():
            return condition.text.strip()
    return None
