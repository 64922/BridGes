"""``github.parse``：抽取 idea 的核心场景、必要功能与可选技术词。

原词逐字保留：检索查询词由用户原文里的场景与要点拼装，不翻译、不同义替换。
本轮消息**没有自己的主题**（「找实现它的项目」「有没有开源的实现」）时，从
已确认的会话前文里取最近一条可追溯的原词（上一轮论文搜索的原始术语，或上一轮
GitHub 请求的场景），并把来源模块、原词与前文消息 ID 一起写进解析结果——
用户看到的「前文依据」与真正用于检索的词是同一个。

前文里也找不到原词时只问一个问题（要找什么项目／功能），恢复载荷随消息持久化。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from bridges.contracts.modules import ModuleWaitState
from bridges.github.contracts import (
    GithubClarification,
    GithubContextSource,
    GithubIdeaAnalysis,
)
from bridges.github.lexicon import (
    extract_features,
    extract_scenario,
    extract_tech_terms,
    has_own_subject,
    wants_whole_idea,
)

#: 澄清问题的恢复载荷键（随消息持久化，跨会话重开仍然有效）。
PENDING_ORIGINAL_REQUEST = "original_request"

CLARIFICATION_QUESTION = (
    "你想找哪个项目或哪个功能的公开仓库？（例如：校园二手书交换平台、"
    "登录认证组件、Markdown 编辑器）"
)


def parse_github_request(
    content: str,
    *,
    prior_context: Sequence[GithubContextSource] = (),
    pending: ModuleWaitState | None = None,
) -> GithubIdeaAnalysis:
    """解析一轮 GitHub 项目请求；缺失主题或指代不明时返回单一澄清问题。

    ``pending`` 非空表示上一轮已提问、本轮 ``content`` 是该问题的回答：
    首次请求的原文仍然保留，而场景与要点取自回答本身。
    """
    current = content.strip()
    resumed = ""
    if pending is not None and pending.kind == "clarification":
        resumed = str(pending.context.get(PENDING_ORIGINAL_REQUEST) or "").strip()
    original_request = resumed or current
    source_text = current if has_own_subject(current) else original_request
    anchor = _latest_anchor(prior_context)

    if not has_own_subject(current) and anchor is None:
        return GithubIdeaAnalysis(
            original_request=original_request,
            scenario="",
            features=[],
            tech_terms=[],
            whole_idea=True,
            component_terms=[],
            context_source=None,
            clarification=GithubClarification(
                question=CLARIFICATION_QUESTION,
                reason="本轮没有说清要找什么项目或功能，也没有可追溯的前文原词。",
            ),
        )

    if not has_own_subject(current) and anchor is not None:
        # 指向前文：场景与要点都用前文原词**逐字**照抄，不再剥意图词——前文原词
        # 已经是上一轮确认过的术语（「深度学习」这类词一旦被当成请求壳剥掉，
        # 指向就变成了另一件事），检索词因此可追溯到具体那条消息。
        scenario = anchor.phrase.strip()
        whole = wants_whole_idea(anchor.phrase)
        return GithubIdeaAnalysis(
            original_request=original_request,
            scenario=scenario,
            features=[scenario],
            tech_terms=extract_tech_terms(original_request),
            whole_idea=whole,
            component_terms=[] if whole else [scenario],
            context_source=anchor,
        )

    scenario = extract_scenario(source_text)
    whole = wants_whole_idea(source_text)
    return GithubIdeaAnalysis(
        original_request=original_request,
        scenario=scenario,
        features=extract_features(source_text, scenario=scenario),
        tech_terms=extract_tech_terms(source_text),
        whole_idea=whole,
        component_terms=[] if whole else [scenario],
    )


def pending_payload(analysis: GithubIdeaAnalysis) -> dict[str, Any]:
    """把等待中的首次请求写成可持久化的恢复载荷。"""
    return {PENDING_ORIGINAL_REQUEST: analysis.original_request}


def _latest_anchor(
    prior_context: Sequence[GithubContextSource],
) -> GithubContextSource | None:
    """最近一条带原词的前文依据（顺序即时间顺序，取最后一条）。"""
    for anchor in reversed(list(prior_context)):
        if anchor.phrase.strip():
            return anchor
    return None
