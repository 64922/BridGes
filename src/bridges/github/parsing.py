"""``github.parse``：抽取 idea 的核心场景、必要/可选功能与用户限制。

原词逐字保留：检索查询词由用户原文里的场景与要点拼装，不翻译、不同义替换。
本轮消息**没有自己的主题**（「找实现它的项目」「有没有开源的实现」）时，从
已确认的会话前文里取最近一条可追溯的原词（上一轮论文搜索的原始术语，或上一轮
GitHub 请求的场景），或使用工单 26 接入的**类型化需求产物**（选定论文标识／
岗位需求），把来源与原词一起写进解析结果——用户看到的依据与真正用于检索的
词是同一个。

用户原文里的可选功能、技术/许可/运行限制也在这里登记：可选项只补充说明，
限制逐字保留为约束行；身份未确认的需求来源只按原词检索，不宣称仓库对应它。

前文里也找不到原词时只问一个问题（要找什么项目／功能），恢复载荷随消息持久化。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from bridges.contracts.modules import ModuleWaitState
from bridges.github.contracts import (
    GithubClarification,
    GithubConstraintSet,
    GithubContextSource,
    GithubIdeaAnalysis,
    GithubRequirementInput,
)
from bridges.github.lexicon import (
    extract_excluded_terms,
    extract_features,
    extract_license_terms,
    extract_optional_features,
    extract_runtime_terms,
    extract_scenario,
    extract_tech_constraints,
    extract_tech_terms,
    has_own_subject,
    wants_whole_idea,
)

if TYPE_CHECKING:
    from bridges.chat.task_materials import EffectiveCondition


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
    task_conditions: Sequence[EffectiveCondition] = (),
    requirement: GithubRequirementInput | None = None,
) -> GithubIdeaAnalysis:
    """解析一轮 GitHub 项目请求；缺失主题或指代不明时返回单一澄清问题。

    ``pending`` 非空表示上一轮已提问、本轮 ``content`` 是该问题的回答：
    首次请求的原文仍然保留，而场景与要点取自回答本身。
    ``requirement`` 是本轮接入的类型化需求产物（选定论文标识／岗位需求），
    本轮消息没有自己的主题时优先使用它；它只用于检索与展示。
    """
    current = content.strip()
    resumed = ""
    if pending is not None and pending.kind == "clarification":
        resumed = str(pending.context.get(PENDING_ORIGINAL_REQUEST) or "").strip()
    original_request = resumed or current
    anchor = _latest_anchor(prior_context)
    typed = requirement if requirement is not None and requirement.phrase.strip() else None

    if not has_own_subject(current) and anchor is None and typed is None:
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

    if not has_own_subject(current) and (anchor is not None or typed is not None):
        # 指向前文/需求产物：场景与要点都用来源原词**逐字**照抄，不再剥意图词——
        # 来源原词已经是确认过的术语（「深度学习」这类词一旦被当成请求壳剥掉，
        # 指向就变成了另一件事），检索词因此可追溯到具体来源。
        source_phrase = (typed.phrase if typed is not None else anchor.phrase).strip()  # type: ignore[union-attr]
        source_text = source_phrase
        scenario = source_phrase
        whole = wants_whole_idea(source_phrase)
        analysis = GithubIdeaAnalysis(
            original_request=original_request,
            scenario=scenario,
            features=[scenario],
            tech_terms=extract_tech_terms(source_text),
            whole_idea=whole,
            component_terms=[] if whole else [scenario],
            context_source=anchor if typed is None else None,
            requirement_source=typed,
        )
    else:
        source_text = current if has_own_subject(current) else original_request
        scenario = extract_scenario(source_text)
        whole = wants_whole_idea(source_text)
        analysis = GithubIdeaAnalysis(
            original_request=original_request,
            scenario=scenario,
            features=extract_features(source_text, scenario=scenario),
            tech_terms=extract_tech_terms(source_text),
            whole_idea=whole,
            component_terms=[] if whole else [scenario],
            requirement_source=typed,
        )
    _apply_conditions(analysis, task_conditions)
    _apply_limits(analysis, source_text, task_conditions)
    return analysis


def _apply_conditions(
    analysis: GithubIdeaAnalysis, task_conditions: Sequence[EffectiveCondition]
) -> None:
    """把本轮有效的任务条件并入解析结果（公开词才成为查询）。"""
    for condition in task_conditions:
        if condition.kind in {"feature", "requirement"}:
            # 要点会成为公开查询；本地任务条件的私人段落不能进入网络参数。
            from bridges.chat.task_materials import public_query_from_context

            public_term = public_query_from_context(None, condition.text)
            if public_term and public_term not in analysis.features:
                analysis.features.append(public_term)
        elif condition.kind == "tech":
            for term in extract_tech_terms(condition.text):
                if term not in analysis.tech_terms:
                    analysis.tech_terms.append(term)
            for term in extract_tech_constraints(condition.text):
                if term not in analysis.constraints.technical:
                    analysis.constraints.technical.append(term)


def _apply_limits(
    analysis: GithubIdeaAnalysis,
    source_text: str,
    task_conditions: Sequence[EffectiveCondition],
) -> None:
    """登记可选功能与技术/许可/运行限制（逐字保留，可选项不参与匹配门）。"""
    optional = extract_optional_features(
        source_text, scenario=analysis.scenario, required=analysis.features
    )
    if optional:
        analysis.features = [item for item in analysis.features if item not in optional]
        analysis.optional_features = optional
    constraints = GithubConstraintSet(
        technical=[*analysis.constraints.technical, *extract_tech_constraints(source_text)],
        license=extract_license_terms(source_text),
        runtime=extract_runtime_terms(source_text),
        excluded=extract_excluded_terms(source_text),
    )
    for condition in task_conditions:
        if condition.kind == "exclusion" and condition.text.strip():
            term = condition.text.strip()
            if term not in constraints.excluded:
                constraints.excluded.append(term)
    # 去重但保持首次出现顺序。
    constraints.technical = list(dict.fromkeys(constraints.technical))
    analysis.constraints = constraints


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
