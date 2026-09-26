"""普通聊天中的职业规划建议（只建议，绝不暗中检索）。

日常对话里出现明确的求职／岗位请求时才给一键建议；点击后以原文启动模块，
不点击就不产生任何外部调用，也不改搜其他来源。
"""

from __future__ import annotations

from typing import Any

from bridges.career_plan.lexicon import extract_job_terms

#: 建议按钮的中文标签。
CAREER_SUGGESTION_LABEL = "使用职业规划"

#: 触发建议的请求词（必须命中其一；岗位词单独出现不足以建议）。
#: 「的工作」「工作机会」收的是「在找 Java 后端开发的工作」这类自然说法。
CAREER_REQUEST_HINTS: tuple[str, ...] = (
    "招聘",
    "岗位",
    "职位",
    "求职",
    "找工作",
    "找实习",
    "的工作",
    "工作机会",
    "投简历",
    "投递",
    "校招",
    "秋招",
    "春招",
    "社招",
    "就业",
    "面试",
    "offer",
    "简历",
    "薪资",
    "待遇",
    "职业规划",
    "岗位要求",
    "JD",
)


def detect_career_suggestion(content: str) -> dict[str, Any] | None:
    """识别普通聊天里的求职／岗位请求；未命中返回 None。

    只在出现明确求职语境时才建议：只说技术词（「我们用 Java 写后端」）不触发，
    避免把技术讨论当成求职请求。
    """
    text = content.strip()
    if not text:
        return None
    if not any(hint in text for hint in CAREER_REQUEST_HINTS):
        return None
    terms = extract_job_terms(text)
    if terms:
        reason = f"你想了解「{terms[0]}」的公开招聘岗位要求，建议用职业规划。"
    else:
        reason = "建议用职业规划来查公开岗位；会先问你一个目标岗位。"
    return {
        "module_id": "career",
        "label": CAREER_SUGGESTION_LABEL,
        "reason": reason,
        "text": text,
        "needs_disambiguation": not terms,
    }
