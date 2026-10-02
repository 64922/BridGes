"""改进工单 19：可解释的用途选择规则（确定性，不调用模型）。

规则回答三个问题，全部可由测试复现：

1. 当前任务是什么种类（解释 / 计划 / 推荐 / 练习 / 复盘 / 普通）？
2. 条目按关系与正文应改变哪项回答决策？
3. 本轮明确要求是否覆盖长期默认偏好？

跨主题默认偏好（回答篇幅、结构、例子、语气等）不要求与学科问题共享字词；
背景、目标与现实约束按任务召回；无关爱好只在词面相关时进入。语义检索或
向量索引是否增加由 41 实测决定，本模块只提供可解释规则。
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from bridges.contracts.atomic_profile import (
    AtomicProfileFactRelation,
)
from bridges.contracts.profile_adoption import (
    AdoptedProfileItem,
    ProfileSlicePurpose,
    ProfileTaskKind,
)

#: 回答决策标签：告诉生成链“这条信息允许改变什么”，而不是一句模糊偏好。
DECISION_EXPRESSION_LENGTH = "expression_length"
DECISION_EXPRESSION_ORDER = "explanation_order"
DECISION_EXPRESSION_TONE = "expression_tone"
DECISION_EXPRESSION_FORMAT = "expression_format"
DECISION_ADDRESS = "address"
DECISION_EXPLANATION_START = "explanation_start"
DECISION_PLAN_LEVEL = "plan_level"
DECISION_PLAN_FOCUS = "plan_focus"
DECISION_PLAN_TIME = "plan_time_budget"
DECISION_RECOMMENDATION_SCOPE = "recommendation_scope"

#: 默认表达偏好的细粒度标签。
PREF_BREVITY = "brevity"
PREF_DETAIL = "detail"
PREF_EXAMPLE_FIRST = "example_first"
PREF_CONCLUSION_FIRST = "conclusion_first"
PREF_FORMULA = "formula"
PREF_TONE = "tone"
PREF_FORMAT = "format"
PREF_ADDRESS = "address"

_PREF_TAG_TO_DECISION: dict[str, str] = {
    PREF_BREVITY: DECISION_EXPRESSION_LENGTH,
    PREF_DETAIL: DECISION_EXPRESSION_LENGTH,
    PREF_EXAMPLE_FIRST: DECISION_EXPRESSION_ORDER,
    PREF_CONCLUSION_FIRST: DECISION_EXPRESSION_ORDER,
    PREF_FORMULA: DECISION_EXPRESSION_ORDER,
    PREF_TONE: DECISION_EXPRESSION_TONE,
    PREF_FORMAT: DECISION_EXPRESSION_FORMAT,
    PREF_ADDRESS: DECISION_ADDRESS,
}

_PREF_TAG_LABELS: dict[str, str] = {
    PREF_BREVITY: "默认简短",
    PREF_DETAIL: "默认详细",
    PREF_EXAMPLE_FIRST: "默认先给例子",
    PREF_CONCLUSION_FIRST: "默认先给结论",
    PREF_FORMULA: "默认保留公式与推导",
    PREF_TONE: "默认语气",
    PREF_FORMAT: "默认结构格式",
    PREF_ADDRESS: "默认称呼",
}

_TASK_PATTERNS: tuple[tuple[ProfileTaskKind, re.Pattern[str]], ...] = (
    (
        ProfileTaskKind.PRACTICE,
        re.compile(r"出题|出几道|来几道|练习|测验|测试|做题|刷题|判分|批改|评分"),
    ),
    (
        ProfileTaskKind.PLAN,
        re.compile(
            r"计划|规划|安排|排期|时间表|日程|路线图|怎么分配|如何分配|制定|"
            r"(?:帮我|为我|给我)?(?:准备|备考|规划)"
        ),
    ),
    (
        ProfileTaskKind.RECOMMEND,
        re.compile(r"推荐|推荐资料|资料|书单|教材|课程|练习册|资源|习题|论文|文献"),
    ),
    (
        ProfileTaskKind.REVIEW,
        re.compile(r"复盘|总结|回顾|梳理"),
    ),
    (
        ProfileTaskKind.EXPLAIN,
        re.compile(
            r"解释|讲解|讲讲|说明|介绍|什么是|是什么|为什么|怎么理解|原理|"
            r"推导|证明|区别|区别是什么|含义"
        ),
    ),
)

_PLAN_KINDS = frozenset(
    {
        ProfileTaskKind.PLAN,
        ProfileTaskKind.RECOMMEND,
        ProfileTaskKind.PRACTICE,
        ProfileTaskKind.REVIEW,
    }
)
_BACKGROUND_KINDS = frozenset(
    {
        ProfileTaskKind.EXPLAIN,
        ProfileTaskKind.PLAN,
        ProfileTaskKind.RECOMMEND,
        ProfileTaskKind.PRACTICE,
        ProfileTaskKind.REVIEW,
    }
)
_BACKGROUND_RELATIONS = frozenset(
    {
        AtomicProfileFactRelation.IDENTITY,
        AtomicProfileFactRelation.GRADE,
        AtomicProfileFactRelation.MAJOR,
        AtomicProfileFactRelation.LEARNING,
        AtomicProfileFactRelation.RESEARCH,
    }
)

_CONSTRAINT_RE = re.compile(
    r"每天|每周|每月|每(?:天|周|月)|早上|晚上|周末|课后|下班|只有|只能|"
    r"时间|分钟|小时|预算|经费|成本|精力|抽不出|日均"
)

_EXPRESSION_SIGNAL_RE = re.compile(
    r"回答|解释|讲解|说明|表述|表达|回复|沟通|措辞|语气|称呼|风格|格式|"
    r"篇幅|字数|长度|简短|简洁|详细|精简|啰嗦|结论|要点|例子|举例|"
    r"图示|图表|比喻|类比|公式|推导|分点|列表|步骤|结构|专业|通俗|口语|"
    r"中文|英文|直接|先给"
)
_BREVITY_RE = re.compile(r"简短|简洁|精炼|简短回答|只说结论|直接给答案|不要展开|概括|三句话|一句话")
_DETAIL_RE = re.compile(r"详细|严谨|完整|深入|逐步|展开|不要省略")
_EXAMPLE_FIRST_RE = re.compile(
    r"先(?:看|给|讲|举|用)?例子|例子先行|先举例|直观例子|多举例子|比喻|类比"
)
_CONCLUSION_FIRST_RE = re.compile(r"先给结论|先说结论|结论先行|先结论")
_FORMULA_RE = re.compile(r"公式|推导|证明")
_TONE_RE = re.compile(r"幽默|严肃|亲切|温和|正式|轻松")
_FORMAT_RE = re.compile(r"格式|分点|列表|步骤|结构|篇幅|字数|长度")
_ADDRESS_RE = re.compile(r"称呼|叫我|名字")

_PRIORITY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("detailed", _DETAIL_RE),
    ("answer_only", re.compile(r"只给答案|直接给答案|不要解释|别解释|不用解释|不要展开")),
    ("concise", _BREVITY_RE),
    ("conclusion_first", _CONCLUSION_FIRST_RE),
    ("example_first", _EXAMPLE_FIRST_RE),
)

#: 本轮明确要求覆盖的默认偏好标签。
_OVERRIDES: dict[str, frozenset[str]] = {
    "detailed": frozenset({PREF_BREVITY, PREF_EXAMPLE_FIRST}),
    "answer_only": frozenset({PREF_DETAIL, PREF_EXAMPLE_FIRST, PREF_FORMULA}),
    "concise": frozenset({PREF_DETAIL}),
    "conclusion_first": frozenset({PREF_EXAMPLE_FIRST}),
    "example_first": frozenset(),
}


def classify_task_kind(text: str | None) -> ProfileTaskKind:
    """按请求原文识别任务种类；没有信号时返回 ``general``。"""

    value = (text or "").strip()
    if not value:
        return ProfileTaskKind.GENERAL
    for kind, pattern in _TASK_PATTERNS:
        if pattern.search(value):
            return kind
    return ProfileTaskKind.GENERAL


def detect_explicit_request(text: str | None) -> str | None:
    """识别本轮明确要求的确定性签名；多个信号时取最先出现者。"""

    value = (text or "").strip()
    if not value:
        return None
    earliest: tuple[int, str] | None = None
    for signature, pattern in _PRIORITY_PATTERNS:
        match = pattern.search(value)
        if match is None:
            continue
        if earliest is None or match.start() < earliest[0]:
            earliest = (match.start(), signature)
    return earliest[1] if earliest is not None else None


def expression_preference_tags(text: str) -> frozenset[str]:
    """识别一条事实是否表达了可跨主题适用的默认表达偏好。"""

    value = (text or "").strip()
    if not value or _EXPRESSION_SIGNAL_RE.search(value) is None:
        return frozenset()
    tags: set[str] = set()
    if _BREVITY_RE.search(value):
        tags.add(PREF_BREVITY)
    if _DETAIL_RE.search(value):
        tags.add(PREF_DETAIL)
    if _EXAMPLE_FIRST_RE.search(value):
        tags.add(PREF_EXAMPLE_FIRST)
    if _CONCLUSION_FIRST_RE.search(value):
        tags.add(PREF_CONCLUSION_FIRST)
    if _FORMULA_RE.search(value):
        tags.add(PREF_FORMULA)
    if _TONE_RE.search(value):
        tags.add(PREF_TONE)
    if _FORMAT_RE.search(value):
        tags.add(PREF_FORMAT)
    if _ADDRESS_RE.search(value):
        tags.add(PREF_ADDRESS)
    return frozenset(tags)


def is_resource_constraint(text: str) -> bool:
    """识别现实时间/资源约束；不把普通陈述误判成约束。"""

    return _CONSTRAINT_RE.search(text or "") is not None


def preference_decisions(tags: frozenset[str]) -> list[str]:
    """把偏好标签折算为允许改变的决策标签（去重且稳定排序）。"""

    return sorted({_PREF_TAG_TO_DECISION[tag] for tag in tags})


def is_overridden(tags: frozenset[str], explicit_request: str | None) -> bool:
    """本轮明确要求是否覆盖这条默认偏好（只影响本轮，不改长期值）。"""

    if not explicit_request or not tags:
        return False
    return bool(tags.intersection(_OVERRIDES.get(explicit_request, frozenset())))


def build_purpose(
    *,
    mode: str,
    query: str | None = None,
    module_id: str | None = None,
    learning_stage: str | None = None,
) -> ProfileSlicePurpose:
    """从模式、请求与模块/阶段信息编译用途语义。"""

    return ProfileSlicePurpose(
        mode=mode,
        task_kind=classify_task_kind(query),
        module_id=module_id,
        learning_stage=learning_stage,
        query=query,
        explicit_request=detect_explicit_request(query),
    )


def applicability(
    *,
    relation: AtomicProfileFactRelation,
    text: str,
    task_kind: ProfileTaskKind,
) -> list[str]:
    """按任务种类返回条目允许改变的决策；空列表表示规则不适用。"""

    tags = expression_preference_tags(text)
    if tags:
        return preference_decisions(tags)
    if is_resource_constraint(text) and task_kind in _PLAN_KINDS:
        return [DECISION_PLAN_TIME, DECISION_RECOMMENDATION_SCOPE]
    if relation is AtomicProfileFactRelation.GOAL and task_kind in _PLAN_KINDS:
        return [DECISION_PLAN_FOCUS, DECISION_RECOMMENDATION_SCOPE]
    if relation in _BACKGROUND_RELATIONS and task_kind in _BACKGROUND_KINDS:
        if task_kind is ProfileTaskKind.PLAN:
            return [DECISION_PLAN_LEVEL, DECISION_EXPLANATION_START]
        if task_kind is ProfileTaskKind.RECOMMEND:
            return [DECISION_RECOMMENDATION_SCOPE, DECISION_EXPLANATION_START]
        return [DECISION_EXPLANATION_START]
    return []


def adoption_reason(
    *,
    relation: AtomicProfileFactRelation,
    text: str,
    task_kind: ProfileTaskKind,
) -> str:
    """生成可解释的采用原因（进入模型上下文与披露，不含内部标识）。"""

    tags = expression_preference_tags(text)
    if tags:
        return "跨主题适用的默认表达偏好"
    if is_resource_constraint(text) and task_kind in _PLAN_KINDS:
        return "与当前任务相关的现实约束"
    if relation is AtomicProfileFactRelation.GOAL and task_kind in _PLAN_KINDS:
        return "与当前任务相关的目标"
    if relation in _BACKGROUND_RELATIONS and task_kind in _BACKGROUND_KINDS:
        return "与当前任务相关的用户背景（自述）"
    return "与你当前任务相关的已记住信息"


def constraint_conditions(
    *,
    text: str,
    task_kind: ProfileTaskKind,
    has_explicit_expiry: bool,
) -> list[str]:
    """没有明示期限的约束/目标用于制定计划时，提示先确认仍有效（R01）。"""

    if task_kind not in _PLAN_KINDS:
        return []
    if not (is_resource_constraint(text) or _is_goal_like(text)):
        return []
    if has_explicit_expiry:
        return []
    return ["无明确期限，依赖它制定具体计划前先确认仍有效"]


def _is_goal_like(text: str) -> bool:
    return bool(re.search(r"目标|计划|备考|准备", text or ""))


def application_summary(
    purpose: ProfileSlicePurpose,
    adopted: Sequence[AdoptedProfileItem],
) -> str | None:
    """把用途与采用条目编译为一句“本轮应用”说明（不新增模型调用）。"""

    if not adopted:
        return None
    parts: list[str] = []
    relations = {item.relation for item in adopted}
    decisions = {decision for item in adopted for decision in item.applicable_to}
    tags = {
        tag
        for item in adopted
        for tag in expression_preference_tags(item.fact_text)
    }
    if purpose.task_kind in _BACKGROUND_KINDS and (
        relations & _BACKGROUND_RELATIONS
    ):
        parts.append("按用户自述的基础选择解释起点，不推断未提供的掌握程度")
    if DECISION_PLAN_TIME in decisions:
        parts.append("按已声明的时间与资源约束安排可执行步骤")
    if DECISION_PLAN_FOCUS in decisions:
        parts.append("围绕当前目标组织重点与先后顺序")
    preferred = [label for tag, label in _PREF_TAG_LABELS.items() if tag in tags]
    if preferred:
        parts.append("、".join(preferred))
    parts.append("本轮明确要求优先，长期偏好只是默认")
    return "本轮应用：" + "；".join(parts) + "。"


__all__ = [
    "DECISION_ADDRESS",
    "DECISION_EXPLANATION_START",
    "DECISION_EXPRESSION_FORMAT",
    "DECISION_EXPRESSION_LENGTH",
    "DECISION_EXPRESSION_ORDER",
    "DECISION_EXPRESSION_TONE",
    "DECISION_PLAN_FOCUS",
    "DECISION_PLAN_LEVEL",
    "DECISION_PLAN_TIME",
    "DECISION_RECOMMENDATION_SCOPE",
    "PREF_ADDRESS",
    "PREF_BREVITY",
    "PREF_CONCLUSION_FIRST",
    "PREF_DETAIL",
    "PREF_EXAMPLE_FIRST",
    "PREF_FORMAT",
    "PREF_FORMULA",
    "PREF_TONE",
    "adoption_reason",
    "applicability",
    "application_summary",
    "build_purpose",
    "classify_task_kind",
    "constraint_conditions",
    "detect_explicit_request",
    "expression_preference_tags",
    "is_overridden",
    "is_resource_constraint",
    "preference_decisions",
]
