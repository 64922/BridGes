"""按话题与明确校区收敛已读发言的读取边界。"""

from __future__ import annotations

from bridges.tieba.contracts import (
    ReadStatus,
    TiebaQuestionAnalysis,
    TiebaReadResult,
    TiebaReply,
)
from bridges.tieba.lexicon import CAMPUS_TERMS, PLACE_EVENT_TERMS
from bridges.tieba.relevance import text_matches_topic

_SCOPE_REASON_LABELS = {
    "campus_conflict": "明确其他校区",
    "campus_unconfirmed": "校区未确认",
    "other_topic": "其他话题",
    "topic_mismatch": "话题不相关",
}


def scope_scoped_result(
    result: TiebaReadResult,
    analysis: TiebaQuestionAnalysis,
) -> TiebaReadResult:
    """按用户点名的明确校区收敛已读发言，保留无冲突的必要上下文。

    直接证据必须由发言正文确认该校区；同校区但明确谈论其他话题的发言排除。
    与直接证据相连的父回复或继续回复即使没有关键词也保留：父回复只作上下文、
    不能成为引用，继续回复（反驳、否定、纠正）仍是可核对的观点依据。
    """
    if not analysis.campus_terms:
        return result
    requested = set(analysis.campus_terms)
    reasons: dict[str, int] = {}
    kept_keys: set[int] = set()
    changed = True
    while changed:
        changed = False
        kept_ids = _kept_reply_ids(result, kept_keys)
        for index, reply in enumerate(result.replies):
            if index in kept_keys:
                continue
            verdict = _scope_verdict(reply, analysis, requested)
            if verdict == "direct" or (
                verdict == "context"
                and reply.parent_reply_id
                and reply.parent_reply_id in kept_ids
            ):
                kept_keys.add(index)
                changed = True
    by_id = {
        reply.reply_id: (index, reply)
        for index, reply in enumerate(result.replies)
        if reply.reply_id
    }
    context_keys: set[int] = set()
    pending_parents = [
        result.replies[index].parent_reply_id
        for index in kept_keys
        if result.replies[index].parent_reply_id
    ]
    while pending_parents:
        parent_id = pending_parents.pop()
        found = by_id.get(parent_id or "")
        if found is None:
            continue
        index, parent = found
        if index in kept_keys:
            continue
        if _scope_verdict(parent, analysis, requested) in {
            "campus_conflict",
            "other_topic",
        }:
            continue
        kept_keys.add(index)
        context_keys.add(index)
        if parent.parent_reply_id:
            pending_parents.append(parent.parent_reply_id)
    for index, reply in enumerate(result.replies):
        if index in kept_keys:
            continue
        verdict = _scope_verdict(reply, analysis, requested)
        if verdict == "context":
            verdict = (
                "campus_unconfirmed"
                if requested and not {term for term in CAMPUS_TERMS if term in reply.content}
                else "topic_mismatch"
            )
        _count_reason(reasons, verdict)
    ordered = [
        reply.model_copy(update={"context_only": True})
        if index in context_keys
        else reply
        for index, reply in enumerate(result.replies)
        if index in kept_keys
    ]
    excluded = len(result.replies) - len(ordered)
    if not excluded and not context_keys:
        return result
    parts = [result.error_message]
    if excluded:
        detail = "、".join(
            f"{_SCOPE_REASON_LABELS.get(reason, reason)} {count} 条"
            for reason, count in reasons.items()
        )
        parts.append(
            f"已按话题条件或明确校区条件排除 {excluded} 条不匹配的发言"
            + (f"（{detail}）。" if detail else "。")
        )
    if context_keys:
        parts.append(
            f"另有 {len(context_keys)} 条无冲突父回复仅作理解上下文，不作为引用依据。"
        )
    return result.model_copy(
        update={
            "replies": ordered,
            "status": ReadStatus.PARTIAL,
            "scope_excluded_reasons": reasons,
            "error_message": "；".join(filter(None, parts)),
        }
    )


def _scope_verdict(
    reply: TiebaReply, analysis: TiebaQuestionAnalysis, requested: set[str]
) -> str:
    named = {term for term in CAMPUS_TERMS if term in reply.content}
    topic_match = text_matches_topic(reply.content, analysis.topic_terms)
    other_topic = not topic_match and _mentions_other_topic(reply.content, analysis)
    if requested and named and not requested.issubset(named):
        return "campus_conflict"
    if requested and not requested.issubset(named):
        if named:
            return "campus_conflict"
        return "context" if not other_topic else "other_topic"
    if other_topic:
        return "other_topic"
    return "direct"


def _kept_reply_ids(result: TiebaReadResult, kept_keys: set[int]) -> set[str]:
    ids: set[str] = set()
    for index in kept_keys:
        reply_id = result.replies[index].reply_id
        if reply_id:
            ids.add(reply_id)
    return ids


def _count_reason(reasons: dict[str, int], reason: str) -> None:
    if reason in {"direct", "context"}:
        return
    reasons[reason] = reasons.get(reason, 0) + 1


def _mentions_other_topic(content: str, analysis: TiebaQuestionAnalysis) -> bool:
    """Reject an unrelated known campus topic from otherwise matching context."""
    requested = set(analysis.topic_terms) | set(analysis.campus_terms)
    return any(term in content for term in PLACE_EVENT_TERMS - requested)


__all__ = ["scope_scoped_result"]
