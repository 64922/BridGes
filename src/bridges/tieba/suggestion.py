"""普通聊天中的贴吧模块建议（只建议，绝不暗中检索）。

日常对话里出现明确的贴吧请求时才给一键建议；点击后以原文启动模块，
不点击就不产生任何外部调用，也不改搜其他来源。
"""

from __future__ import annotations

from typing import Any

from bridges.tieba.lexicon import TARGET_FORUM_NAME, extract_topic_terms

#: 建议按钮的中文标签。
TIEBA_SUGGESTION_LABEL = "使用贴吧信息搜集"

#: 触发建议的请求词（需要同时出现贴吧指向词）。
TIEBA_REQUEST_HINTS: tuple[str, ...] = (
    "贴吧",
    "吧友",
    "华交吧",
    "华东交通大学吧",
    "学校论坛",
)


def detect_tieba_suggestion(content: str) -> dict[str, Any] | None:
    """识别普通聊天里的贴吧请求；未命中返回 None。"""
    text = content.strip()
    if not text:
        return None
    if not any(hint in text for hint in TIEBA_REQUEST_HINTS):
        return None
    terms = extract_topic_terms(text)
    return {
        "module_id": "tieba",
        "label": TIEBA_SUGGESTION_LABEL,
        "reason": (
            f"你想查的是{TARGET_FORUM_NAME}里的公开讨论，建议用贴吧信息搜集。"
            if terms
            else f"建议用贴吧信息搜集来查{TARGET_FORUM_NAME}；会先问你一个话题。"
        ),
        "text": text,
        "needs_disambiguation": not terms,
    }
