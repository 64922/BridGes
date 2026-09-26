"""普通聊天里的资料模块建议：只建议，绝不暗中检索。

ADR-0030 与 ``docs/v2/interaction.md`` 第 3 节：普通聊天中「明显想学某个方向」
的消息可以给出「使用学习资料推荐」一键建议；点击后复用原始请求（同一条用户
消息的原文）并以 ``module_id=resources`` 显式启动，因此建议本身不产生任何外部
检索。

判定保持确定性（规则 + 词表），只认真正明显的资料请求：出现「想学／入门／
教程／教材／推荐几本书」等学习诉求词，且原文里存在可检索的专业名词。
「帮我看看 Transformer」这类没有学习诉求的话不给出建议。
"""

from __future__ import annotations

from bridges.resources.parsing import extract_topic_phrase

RESOURCES_SUGGESTION_LABEL = "使用学习资料推荐"
RESOURCES_SUGGESTION_REASON = (
    "这条消息看起来想学一个方向，可一键用学习资料推荐以原文启动，不会自动检索。"
)

#: 明确的学习诉求词（出现任一即视为想学某个方向）。
RESOURCES_REQUEST_HINTS: tuple[str, ...] = (
    "想学", "要学", "打算学", "学习", "入门", "自学", "怎么学", "如何学",
    "教材", "教程", "课程", "学习资料", "资料推荐", "推荐几本", "推荐本书",
    "推荐点书", "书单", "网课", "公开课", "learn", "tutorial", "course",
)

#: 歧义过大的信号：命中时不给建议（让用户自己选模块）。
AMBIGUOUS_SIGNALS: tuple[str, ...] = ("随便", "都行", "不知道", "看看有什么", "有什么好")


def detect_resources_suggestion(content: str) -> dict[str, object] | None:
    """返回建议载荷（模块 ID、中文标签、原因与原文）；不建议时返回 None。"""
    text = content.strip()
    if not text or len(text) > 300:
        return None
    lowered = text.lower()
    if any(signal in text for signal in AMBIGUOUS_SIGNALS):
        return None
    if not any(hint.lower() in lowered for hint in RESOURCES_REQUEST_HINTS):
        return None
    if extract_topic_phrase(text) is None:
        # 只有「想学点什么」这类诉求词、没有可检索的主题时不给建议（歧义大）。
        return None
    return {
        "module_id": "resources",
        "label": RESOURCES_SUGGESTION_LABEL,
        "reason": RESOURCES_SUGGESTION_REASON,
        "text": text,
        "needs_disambiguation": False,
    }
