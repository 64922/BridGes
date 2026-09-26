"""普通聊天中的 GitHub 项目推荐建议（只建议，绝不暗中检索）。

日常对话里出现明确要「找现成开源实现」的请求时才给一键建议；点击后以原文
启动模块，不点击就不产生任何外部调用，也不改搜其他来源。
"""

from __future__ import annotations

import re
from typing import Any

from bridges.github.lexicon import has_own_subject

#: 建议按钮的中文标签。
GITHUB_SUGGESTION_LABEL = "使用 GitHub 项目推荐"

#: 触发建议的请求词（指向公开代码仓库的请求；不含泛化的「仓库」以免误触发）。
GITHUB_REQUEST_HINTS: tuple[str, ...] = (
    "github",
    "开源项目",
    "开源仓库",
    "开源实现",
    "开源代码",
    "开源的",
    "现成的项目",
    "现成项目",
    "有没有人做过",
    "别人做过",
    "类似的项目",
    "参考项目",
    "参考实现",
    "实现它的项目",
    "轮子",
)

#: 「找实现它的项目」这类指向前文的请求形态。
_IMPLEMENT_REFERENCE = re.compile(
    r"实现(?:它|他|这个|那个|该|这些|那些|此)的?(?:项目|仓库|代码|轮子)?"
)


def detect_github_suggestion(content: str) -> dict[str, Any] | None:
    """识别普通聊天里的 GitHub 项目请求；未命中返回 None。"""
    text = content.strip()
    if not text:
        return None
    lowered = text.lower()
    hit = any(hint.lower() in lowered for hint in GITHUB_REQUEST_HINTS)
    if not hit and _IMPLEMENT_REFERENCE.search(text) is None:
        return None
    if has_own_subject(text):
        return {
            "module_id": "github",
            "label": GITHUB_SUGGESTION_LABEL,
            "reason": "你想找现成的公开仓库做参考，建议用 GitHub 项目推荐按你的 idea 检索。",
            "text": text,
            "needs_disambiguation": False,
        }
    return {
        "module_id": "github",
        "label": GITHUB_SUGGESTION_LABEL,
        "reason": (
            "建议用 GitHub 项目推荐；会先用你前面提到的内容作为检索词，"
            "找不到就问你一个必要问题。"
        ),
        "text": text,
        "needs_disambiguation": True,
    }
