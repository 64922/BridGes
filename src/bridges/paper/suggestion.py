"""普通聊天里的模块建议：只建议，绝不暗中检索。

ADR-0030 与 ``docs/v2/interaction.md`` 第 3 节：普通聊天中「明显需要专用模块」
的请求可以给出「使用论文搜索」一键建议；建议必须依据用户原文，歧义大时不显示。
点击建议复用原始请求（同一条用户消息的原文）并显式以 ``module_id=paper`` 启动，
因此建议本身不产生任何外部检索。

判定保持确定性（规则 + 词表），只认真正明显的论文请求：出现「论文/文献/paper/
arXiv/综述」等请求词，且原文里存在研究主题（引号术语、英文术语或词表术语）。
「帮我看看 Transformer」这类没有检索诉求的话不给出建议。
"""

from __future__ import annotations

from bridges.paper.parsing import detect_ambiguous_term, extract_topic_phrase

PAPER_SUGGESTION_LABEL = "使用论文搜索"
PAPER_SUGGESTION_REASON = "这条消息看起来要找论文，可一键用论文搜索以原文启动，不会自动检索。"

#: 明确的论文请求词（出现任一即视为有检索诉求）。
PAPER_REQUEST_HINTS: tuple[str, ...] = (
    "论文", "文献", "paper", "papers", "arxiv", "综述", "survey", "文献综述",
    "找几篇", "推荐几篇", "有没有关于", "研究进展", "sci-hub", "文献检索",
)

#: 歧义过大的信号：命中时不给建议（让用户自己选模块）。
AMBIGUOUS_SIGNALS: tuple[str, ...] = ("随便", "都行", "不知道", "看看有什么", "有什么好")


def detect_paper_suggestion(content: str) -> dict[str, object] | None:
    """返回建议载荷（模块 ID、中文标签、原因与原文）；不建议时返回 None。"""
    text = content.strip()
    if not text or len(text) > 300:
        return None
    lowered = text.lower()
    if any(signal in text for signal in AMBIGUOUS_SIGNALS):
        return None
    if not any(hint.lower() in lowered for hint in PAPER_REQUEST_HINTS):
        return None
    topic = extract_topic_phrase(text)
    ambiguous = detect_ambiguous_term(text)
    if topic is None and ambiguous is None:
        # 只有"找论文"这类请求词、没有可检索的主题时不给建议（歧义大）。
        return None
    return {
        "module_id": "paper",
        "label": PAPER_SUGGESTION_LABEL,
        "reason": PAPER_SUGGESTION_REASON,
        "text": text,
        "needs_disambiguation": ambiguous is not None,
    }
