"""资料入口复用既有术语消歧合同，仅采纳当前句和可靠用户前文。"""

from collections.abc import Sequence

from bridges.paper.parsing import (
    CONTEXT_LOOKBACK_MESSAGES,
    detect_ambiguous_term,
    parse_paper_request,
)


def resources_domain_question(
    content: str, *, messages: Sequence[object], current_message_id: str
) -> str | None:
    """无歧义词不加问题；有明确语境则沿用，孤立术语才问领域。"""
    if detect_ambiguous_term(content) is None:
        return None
    prior = []
    for message in messages:
        if getattr(message, "message_id", None) == current_message_id:
            break
        role = getattr(message, "role", None)
        if getattr(role, "value", role) == "user":
            prior.append(str(getattr(message, "content", "")))
    parsed = parse_paper_request(content, prior_context=prior[-CONTEXT_LOOKBACK_MESSAGES:])
    return parsed.clarification.question if parsed.clarification else None
