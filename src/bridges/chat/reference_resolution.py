"""确定性指代解析（改进工单 11）。

定位顺序与产出的共享合同见 :mod:`bridges.contracts.references`。本模块只做
确定性定位：

1. **明确任务/产物指代**：结果列表（论文/仓库投影）上的「第 N 个/最后
   一个」与显式任务指代；
2. **当前任务**：工单 08 的任务版本与条件（含被取代/撤销关系）；
3. **相关近期前文**：按中文关键词/连续串在近期原文里先找；
4. **同会话原文检索**：只在本会话（调用方已按账户/会话过滤）的原文里检索，
   补回时携带必要相邻轮次并保留原值→纠正→撤销关系。

先使用确定性结构锚点、中文关键词与检索；只有工单 40 的漏召回证据证明不足
时才考虑语义召回/重排（本模块不实现）。解析是**只读**的：不写任务条件、
不改原文、不猜测用户未说过的话。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from bridges.chat.turn import history_items
from bridges.contracts.chat import ChatMessageRole
from bridges.contracts.references import (
    AnchorKind,
    ReferenceAnchor,
    ReferenceClarification,
    ReferenceResolution,
    ReferenceStatus,
    ReferenceTaskBrief,
    ReferenceTaskContext,
    TaskBriefCondition,
)
from bridges.contracts.tasks import ConditionStatus, TaskCondition

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bridges.chat.repository import MessageRecord

#: 单轮最多补回的原始消息数（与编译器裁剪循环共用同一上限）。
RECOVERED_MAX_MESSAGES = 4
#: 未定位缺口最多列出的短标签条数。
MISSING_MAX_LABELS = 3
#: 无引用信号时不做任何回补：普通新话题不受旧内容污染。
_BACK_REFERENCE_MARKERS = (
    "之前",
    "先前",
    "早先",
    "上次",
    "刚才",
    "前面",
    "开头",
    "说好的",
    "约定的",
    "记得",
    "答应",
)
#: 续接词：需要沿用原任务/上一轮条件（「继续推荐」「接着上次的来」「照之前
#: 的条件」）。
_CONTINUATION_MARKERS = (
    "继续",
    "接着",
    "往下",
    "再到",
    "照旧",
    "照之前",
    "照刚才",
    "按之前",
    "按刚才",
    "同样的要求",
    "一样的要求",
)
#: 代词：指向最近一轮的可定位对象；有唯一对象就续接。
_PRONOUN_MARKERS = ("它", "这个", "那个")
#: 显式任务指代。
_TASK_REFERENCE_MARKERS = (
    "那个任务",
    "这个任务",
    "刚才的任务",
    "之前的任务",
    "那个课题",
    "该任务",
    "继续任务",
)
#: 判断「最近一条带条件的用户请求」用的约束词（续接回补的确定性线索）。
_CONSTRAINT_MARKERS = (
    "预算",
    "不得超过",
    "不超过",
    "不要",
    "排除",
    "必须",
    "要求",
    "限制",
    "只能",
    "优先",
    "至少",
    "最多",
    "以内",
    "条件",
)
#: 引号原文（含直角、双角、弯引号与直引号）。
_QUOTED_SPAN_RE = re.compile(r"[「『“\"]([^「」『』”\"]{1,64})[」』”\"]")
#: CJK 连续串与拉丁/数字词。
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")
_LATIN_RUN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}")
#: 序数指代：必须带量词，避免把「第二点想法」之类普通表述当成列表指代。
_ORDINAL_RE = re.compile(
    r"第\s*([0-9]{1,2}|[一二两三四五六七八九十]{1,3})\s*"
    r"(个|篇|本|条|项|款|台|只|张|份|部|位)"
)
_LAST_ORDINAL_RE = re.compile(r"最后\s*(?:一)?\s*(个|篇|本|条|项|款|台|只|张|份|部|位)")
#: 汉语数词。
_CN_DIGITS = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
#: 不作为线索的功能字（用于过滤中文连续串切出的虚词片段）。
_FUNCTION_CHARS = frozenset(
    "的了是有在不我你他她它们这那么什吗呢吧啊嘛哦嗯和与及或但而就都也还很再又才"
    "只把被让给对从向到在于为以之其所好继续说想要看"
)
#: 明确的回指虚词/套话，不作为检索线索。
_STOP_TERMS = frozenset(
    {
        "之前",
        "先前",
        "早先",
        "上次",
        "刚才",
        "前面",
        "开头",
        "说好",
        "好的",
        "约定",
        "记得",
        "答应",
        "继续",
        "接着",
        "往下",
        "再到",
        "照旧",
        "然后",
        "什么",
        "多少",
        "怎么",
        "怎样",
        "如何",
        "可以",
        "能否",
        "帮我",
        "给我",
        "我们",
        "你们",
        "他们",
        "这个",
        "那个",
        "这些",
        "那些",
        "一个",
        "现在",
        "今天",
        "之后",
        "后来",
        "一样",
        "同样",
        "按照",
        "根据",
        "要求",
        "推荐",
        "建议",
        "介绍",
        "看看",
        "讲讲",
        "东西",
        "内容",
        "情况",
        "一下",
        "一点",
        "是不是",
    }
)
#: 结构锚点：模块结果列表的种类、关键词、中文标签与量词。
_KIND_KEYWORDS: dict[str, tuple[str, ...]] = {
    "paper": ("论文", "文章", "文献", "paper"),
    "github": ("仓库", "代码库", "repo", "github"),
}
_KIND_LABELS = {"paper": "论文", "github": "仓库"}
_KIND_UNITS = {"paper": "篇", "github": "个"}
_CLASSIFIER_KINDS = {"篇": "paper", "本": "paper"}
#: 单轮用于检索的线索词上限。
_MAX_TERMS = 12
#: 单轮按线索采用的消息条数上限（其余命中进入缺口说明）。
_MAX_TERM_MESSAGES = 3


@dataclass(frozen=True)
class _Ordinal:
    """一次序数指代：第 N 个/篇/本……"""

    number: int
    classifier: str
    is_last: bool = False


@dataclass(frozen=True)
class _ResultItem:
    ordinal: int
    object_id: str
    label: str


@dataclass(frozen=True)
class _ResultList:
    list_id: str
    group_id: str
    goal: str
    kind: str
    message_id: str
    owner_message_id: str | None
    version: int
    items: tuple[_ResultItem, ...]

    @property
    def label(self) -> str:
        return f"{_KIND_LABELS.get(self.kind, self.kind)}列表（第 {self.version} 版）"


@dataclass(frozen=True)
class _MessageMatch:
    index: int
    record: MessageRecord
    terms: tuple[str, ...]
    score: int


def _distinct_labels(terms: Sequence[str]) -> list[str]:
    """按长度取最长代表项，去掉被更长项包含的碎片（保持去重稳定）。"""
    labels: list[str] = []
    for term in sorted(terms, key=len, reverse=True):
        if any(term in longer for longer in labels):
            continue
        labels.append(term)
    return labels


def resolve_references(
    *,
    request: str,
    messages: Sequence[MessageRecord],
    current_user_message_id: str,
    recent_message_ids: Sequence[str] = (),
    task: ReferenceTaskContext | None = None,
) -> ReferenceResolution:
    """解析当前请求的任务/对象指代（只读；详见模块说明）。

    ``messages`` 必须由调用方按账户与会话作用域过滤（本函数不再扩大范围）；
    ``recent_message_ids`` 是本轮已在近期原文中的消息，用于区分「已可见」与
    「需要按原文补回」；``task`` 是工单 08 的当前任务快照（全部状态条件）。
    """
    # 与编译器共用轮次截断和已完成回答配对，重试不得读取未来或失败尝试。
    visible_ids = {
        item.message_id
        for item in history_items(messages, until_user_message_id=current_user_message_id)
        if item.message_id != current_user_message_id
    }
    ordered = [record for record in messages if record.message_id in visible_ids]
    recent = set(recent_message_ids)
    excluded_ids = {
        record.message_id for record in messages
        if record.message_id not in visible_ids
        and record.message_id != current_user_message_id
    }
    if task is not None and excluded_ids.intersection(
        [*task.source_message_ids, *(condition.source_message_id for condition in task.conditions)]
    ):
        # 当前任务已被后续轮次更新时，旧轮重试只沿其可见原文解析。
        task = None
    brief = _task_brief(task)
    lists = _extract_result_lists(ordered)

    # 步骤 1：明确产物指代（结果列表上的序数）优先于其他信号。
    ordinal = _extract_ordinal(request)
    if ordinal is not None:
        return _resolve_ordinal(ordinal, request, lists, brief, task, recent)

    quoted = [span.strip() for span in _QUOTED_SPAN_RE.findall(request)]
    quoted = [span for span in quoted if span]
    has_marker = any(marker in request for marker in _BACK_REFERENCE_MARKERS)
    has_continuation = any(marker in request for marker in _CONTINUATION_MARKERS)
    has_pronoun = any(marker in request for marker in _PRONOUN_MARKERS)
    has_task_reference = any(marker in request for marker in _TASK_REFERENCE_MARKERS)
    if not (quoted or has_marker or has_continuation or has_pronoun or has_task_reference):
        return ReferenceResolution(status=ReferenceStatus.NONE, task=brief)
    terms = _reference_terms(request, quoted=quoted)
    builder = _ResolutionBuilder(recent=recent, ordered=ordered, brief=brief)

    # 步骤 2：当前任务。显式任务指代或续接词采用当前任务；关键词命中
    # 条件（含已被取代/撤销的旧值）也在这里处理纠正关系。
    if task is not None:
        if has_task_reference or has_continuation:
            builder.adopt_task(task)
            builder.adopt_effective_conditions(task)
        matched = [
            condition
            for condition in task.conditions
            if terms and _condition_matches(condition, terms)
        ]
        builder.adopt_condition_matches(task, matched, terms)

    # 步骤 3+4：近期前文优先，再按会话原文检索；命中的消息按补齐必要
    # 相邻轮次补回（结果消息带其直接来源用户消息）。
    matched_messages = _match_messages(terms, ordered)
    matched_messages.sort(key=lambda match: match.record.message_id not in recent)
    builder.adopt_message_matches(matched_messages)

    # 续接回退：没有任务快照也没有任务/列表锚点时，采用最近一次结果列表
    # 或最近一条带条件的用户请求（「继续推荐」在无任务状态下的确定性线索）；
    # 有任务快照时信任任务范围，不用旧话题材料兜底。
    if has_continuation and not builder.has_adoption and task is None:
        if lists:
            builder.adopt_result_list(lists[-1], reason="续接：最近一次结果列表")
        else:
            builder.adopt_continuation_source(ordered)

    # 代词：有词面命中或结构锚点就不用回退；否则采用最近一轮的可指对象。
    if has_pronoun and not builder.has_adoption:
        builder.adopt_nearest_exchange(ordered)

    # 缺口与状态：词面部分命中时必须如实列出未定位项，不能被部分命中掩盖。
    # 纯续接请求（「继续推荐设备」）的词面碎片不算独立缺失——续接对象由结构
    # 锚点（任务/结果列表/最近约束）确定，避免把虚词碎片误报成缺口。
    continuation_only = has_continuation and not (quoted or has_marker)
    if terms and builder.term_driven_adoption and not continuation_only:
        builder.compute_missing_terms(terms)
    if not builder.has_adoption:
        builder.mark_unresolved(terms)
    return builder.build()


# ---------------------------------------------------------------------------
# 解析内部：构造器
# ---------------------------------------------------------------------------


class _ResolutionBuilder:
    """收集锚点、采用/排除原因、缺口与纠正关系，最后产出合同对象。"""

    def __init__(
        self,
        *,
        recent: set[str],
        ordered: Sequence[MessageRecord],
        brief: ReferenceTaskBrief | None,
    ) -> None:
        self._recent = recent
        self._ordered = ordered
        self._order_index = {record.message_id: index for index, record in enumerate(ordered)}
        self._brief = brief
        self._anchors: list[ReferenceAnchor] = []
        self._rejected: list[ReferenceAnchor] = []
        self._adopted: list[str] = []
        self._objects: list[str] = []
        self._corrections: dict[str, str] = {}
        self._missing: list[str] = []
        self._adopted_ids: set[str] = set()
        self._object_ids: set[str] = set()
        self._term_driven = False
        self._matched_terms: list[str] = []
        self.adopted_condition_ids: set[str] = set()
        #: 已撤销条件的来源消息：词面命中也不得把撤销值补回（不复活）。
        self._revoked_source_ids: set[str] = set()

    # -- 状态 ---------------------------------------------------------------

    @property
    def has_adoption(self) -> bool:
        return bool(self._adopted)

    @property
    def term_driven_adoption(self) -> bool:
        return self._term_driven

    @property
    def missing(self) -> list[str]:
        return self._missing

    # -- 采用消息 -----------------------------------------------------------

    def adopt_message(
        self,
        message_id: str,
        *,
        kind: AnchorKind = AnchorKind.MESSAGE,
        label: str,
        reason: str,
        object_id: str | None = None,
        list_version: int | None = None,
        correction_note: str | None = None,
        term_driven: bool = False,
    ) -> None:
        if term_driven:
            self._term_driven = True
        if correction_note:
            self._corrections[message_id] = correction_note
        if message_id in self._adopted_ids:
            return
        anchor = ReferenceAnchor(
            anchor_id=f"{kind.value}:{message_id}" + (f":{object_id}" if object_id else ""),
            kind=kind,
            label=label,
            message_ids=[message_id],
            object_id=object_id,
            list_version=list_version,
            adopted=True,
            reason=reason,
        )
        self._anchors.append(anchor)
        self._adopted.append(message_id)
        self._adopted_ids.add(message_id)
        if object_id and object_id not in self._object_ids:
            self._object_ids.add(object_id)
            self._objects.append(object_id)

    def note_terms_matched(self, terms: Sequence[str]) -> None:
        """记录已被词面命中的线索（缺口计算不得把它们算成缺失）。"""
        for term in terms:
            if term not in self._matched_terms:
                self._matched_terms.append(term)

    def reject_message(self, *, anchor_id: str, label: str, reason: str) -> None:
        self._rejected.append(
            ReferenceAnchor(
                anchor_id=anchor_id,
                kind=AnchorKind.MESSAGE,
                label=label,
                adopted=False,
                reason=reason,
            )
        )

    # -- 任务条件 -----------------------------------------------------------

    def adopt_task(self, task: ReferenceTaskContext) -> None:
        """显式任务指代/续接：采用任务锚点与当前版本来源消息。"""
        self._anchors.append(
            ReferenceAnchor(
                anchor_id=f"task:{task.task_id}",
                kind=AnchorKind.TASK,
                label=f"任务 {task.task_id}（版本 {task.version}）",
                message_ids=[message_id for message_id in task.source_message_ids if message_id],
                list_version=None,
                adopted=True,
                reason="当前任务（目标与版本可追溯）。",
            )
        )
        for message_id in task.source_message_ids:
            if message_id:
                self.adopt_message(
                    message_id,
                    label=f"消息 {message_id}",
                    reason="当前任务版本的来源消息。",
                )

    def adopt_effective_conditions(self, task: ReferenceTaskContext) -> None:
        """续接/显式任务指代：采用全部有效条件及其纠正链。"""
        effective = [
            condition
            for condition in task.conditions
            if condition.status == ConditionStatus.EFFECTIVE
        ]
        for condition in effective:
            self._adopt_condition(task, condition, reason="当前任务的有效条件（续接）")

    def adopt_condition_matches(
        self,
        task: ReferenceTaskContext,
        matched: Sequence[TaskCondition],
        terms: Sequence[str],
    ) -> None:
        """按关键词命中的条件：有效值采用，旧值/撤销值如实标注，不复活。"""
        for condition in matched:
            self.note_terms_matched(
                [term for term in terms if _condition_matches(condition, [term])]
            )
        kinds: list[str] = []
        for condition in matched:
            if condition.kind not in kinds:
                kinds.append(condition.kind)
        for kind in kinds:
            # 按类别读取任务快照的全部条件：命中的可能只是被取代的旧值，
            # 当前有效值仍在快照里，必须采用（不复活旧值，也不误报缺口）。
            group = [condition for condition in task.conditions if condition.kind == kind]
            effective = [
                condition for condition in group if condition.status == ConditionStatus.EFFECTIVE
            ]
            if not effective:
                self.note_condition_gap(group, kind)
                continue
            for condition in effective:
                self._adopt_condition(
                    task,
                    condition,
                    reason="关键词命中同类条件：采用当前有效值（旧值只作纠正说明）。",
                )
            for condition in matched:
                if condition.kind != kind or condition.status == ConditionStatus.EFFECTIVE:
                    continue
                if condition.status == ConditionStatus.SUPERSEDED:
                    self._note_corrected_condition(task, condition)
                else:
                    self._reject_condition(condition)

    def note_condition_gap(self, group: Sequence[TaskCondition], kind: str) -> None:
        """一组同类别条件没有有效值：撤销/折算为真实缺口，不复活旧值。"""
        for condition in group:
            if condition.status == ConditionStatus.REVOKED:
                self._reject_condition(condition, reason="条件已撤销，不复活旧值。")
            elif condition.status == ConditionStatus.SUPERSEDED:
                self._note_corrected_condition(None, condition)
            elif condition.status in {
                ConditionStatus.DRAFT,
                ConditionStatus.CLUE,
            }:
                self._reject_condition(condition, reason="草案/推测不构成用户条件，不作为约束。")
        label = f"「{group[0].kind}」条件"
        if all(condition.status == ConditionStatus.REVOKED for condition in group):
            self._missing.append(f"{label}已被撤销，不能作为当前条件。")
        else:
            self._missing.append(f"{label}没有仍有效的值。")

    def _adopt_condition(
        self, task: ReferenceTaskContext, condition: TaskCondition, *, reason: str
    ) -> None:
        self.adopted_condition_ids.add(condition.condition_id)
        self.adopt_message(
            condition.source_message_id,
            kind=AnchorKind.CONDITION,
            label=f"{condition.kind}:{_shorten(condition.text)}",
            reason=reason,
            object_id=None,
        )
        # 纠正关系：沿 supersedes 链回补直接旧值，但旧值只作纠正说明。
        for ancestor in _superseded_chain(task, condition):
            if ancestor.status == ConditionStatus.SUPERSEDED:
                self._note_corrected_condition(task, ancestor)
            elif ancestor.status == ConditionStatus.REVOKED:
                self._reject_condition(ancestor, reason="旧值已撤销，不复活；仅作撤销关系记录。")

    def _note_corrected_condition(
        self, task: ReferenceTaskContext | None, condition: TaskCondition
    ) -> None:
        """被后续条件取代的旧值：作为纠正邻接原文补回，并注明不采用。"""
        note = "该值已被后续条件纠正；以最新有效值来源为准，不采用旧值。"
        self.adopt_message(
            condition.source_message_id,
            kind=AnchorKind.CONDITION,
            label=f"{condition.kind}:{_shorten(condition.text)}",
            reason="纠正关系：仅用于说明原值→新值的取代，不作为当前条件。",
            correction_note=note,
        )

    def _reject_condition(self, condition: TaskCondition, *, reason: str | None = None) -> None:
        if condition.status == ConditionStatus.REVOKED:
            self._revoked_source_ids.add(condition.source_message_id)
        self._rejected.append(
            ReferenceAnchor(
                anchor_id=f"condition:{condition.condition_id}",
                kind=AnchorKind.CONDITION,
                label=f"{condition.kind}:{_shorten(condition.text)}",
                message_ids=[condition.source_message_id],
                adopted=False,
                reason=reason or "条件不是当前有效用户约束，不采用。",
            )
        )

    # -- 结果列表 -----------------------------------------------------------

    def adopt_result_list(self, result_list: _ResultList, *, reason: str) -> None:
        self._anchors.append(
            ReferenceAnchor(
                anchor_id=f"result_list:{result_list.list_id}",
                kind=AnchorKind.RESULT_LIST,
                label=result_list.label,
                message_ids=[result_list.message_id],
                list_version=result_list.version,
                adopted=True,
                reason=reason,
            )
        )
        self._adopt_with_owner(result_list.message_id, reason=reason)
        if result_list.owner_message_id:
            self._adopt_with_owner(
                result_list.owner_message_id,
                reason="结果列表的直接来源用户消息（必要相邻轮次）。",
            )

    # -- 消息匹配 -----------------------------------------------------------

    def adopt_message_matches(self, matches: Sequence[_MessageMatch]) -> None:
        if not matches:
            return
        selected: list[_MessageMatch] = []
        covered: set[str] = set()
        for match in matches:
            if match.record.message_id in self._revoked_source_ids:
                # 已撤销条件的来源不因词面命中而复活。
                self.reject_message(
                    anchor_id=f"message:{match.record.message_id}",
                    label=f"消息 {match.record.message_id}",
                    reason="该消息对应的条件已被撤销，不复活旧值。",
                )
                continue
            fresh = [
                term
                for term in match.terms
                if not any(term in existing or existing in term for existing in covered)
            ]
            if not fresh:
                continue
            selected.append(match)
            covered.update(match.terms)
            if len(selected) >= _MAX_TERM_MESSAGES:
                break
        for match in selected:
            self._adopt_match(match)

    def _adopt_match(self, match: _MessageMatch) -> None:
        record = match.record
        in_recent = record.message_id in self._recent
        self.note_terms_matched(match.terms)
        reason = "词面命中：" + "、".join(_shorten(term, 12) for term in match.terms[:3])
        if in_recent:
            reason = (
                "近期原文已包含引用对象（"
                + "、".join(_shorten(term, 12) for term in match.terms[:3])
                + "）"
            )
        self.adopt_message(
            record.message_id,
            label=f"消息 {record.message_id}",
            reason=reason,
            term_driven=True,
        )
        # 结果消息带其直接来源用户消息：末尾约束等必要条件在相邻轮次里。
        if record.role == ChatMessageRole.ASSISTANT:
            owner_id = _previous_user_message_id(
                self._ordered, self._order_index[record.message_id]
            )
            if owner_id:
                self._adopt_with_owner(
                    owner_id,
                    reason="结果消息的直接来源轮次（必要相邻原文）。",
                )
        elif self._brief is None:
            self._adopt_adjacent_corrections(record.message_id)

    def _adopt_adjacent_corrections(self, message_id: str) -> None:
        """无任务快照时保留直接相邻的明示纠正，不推测跨话题关系。"""
        users = [record for record in self._ordered if record.role == ChatMessageRole.USER]
        index = next(i for i, record in enumerate(users) if record.message_id == message_id)
        if index > 0 and _is_adjacent_correction(users[index - 1].content, users[index].content):
            index -= 1
            self.adopt_message(
                users[index].message_id,
                label=f"消息 {users[index].message_id}",
                reason="纠正之前的相邻用户原文。",
            )
        while index + 1 < len(users):
            correction = users[index + 1]
            if not _is_adjacent_correction(users[index].content, correction.content):
                break
            revoked = correction.content.startswith(("取消", "撤销", "不再", "不用"))
            self._corrections[users[index].message_id] = (
                "该值已被后续原文撤销，不作为当前条件。" if revoked
                else "该值已被后续相邻原文纠正，以较新来源为准，不采用旧值。"
            )
            self.adopt_message(
                correction.message_id,
                label=f"消息 {correction.message_id}",
                reason="明示纠正/撤销的必要相邻原文。",
            )
            if revoked:
                self._missing.append("所引用值已在后续原文撤销，不能作为当前条件。")
            index += 1

    # -- 回退路径 -----------------------------------------------------------

    def adopt_continuation_source(self, ordered: Sequence[MessageRecord]) -> None:
        """续接回退：最近一条带条件的用户请求，否则最近一条用户请求。"""
        fallback: MessageRecord | None = None
        for record in reversed(ordered):
            if record.role != ChatMessageRole.USER:
                continue
            if fallback is None and record.content.strip():
                fallback = record
            if any(marker in record.content for marker in _CONSTRAINT_MARKERS):
                self.adopt_message(
                    record.message_id,
                    label=f"消息 {record.message_id}",
                    reason="续接：最近一条带条件的用户请求（沿用其仍有效条件）。",
                )
                return
        if fallback is not None:
            self.adopt_message(
                fallback.message_id,
                label=f"消息 {fallback.message_id}",
                reason="续接：最近一条用户请求（作为继续对象）。",
            )

    def adopt_nearest_exchange(self, ordered: Sequence[MessageRecord]) -> None:
        """代词回退：采用最近一轮有内容的助手消息及其直接来源用户消息。"""
        for record in reversed(ordered):
            if record.role != ChatMessageRole.ASSISTANT or not record.content.strip():
                continue
            self.adopt_message(
                record.message_id,
                label=f"消息 {record.message_id}",
                reason="代词指代：最近一轮可指对象。",
            )
            owner_id = _previous_user_message_id(
                self._ordered, self._order_index[record.message_id]
            )
            if owner_id:
                self._adopt_with_owner(
                    owner_id,
                    reason="代词所指轮次的用户来源（必要相邻原文）。",
                )
            return
        for record in reversed(ordered):
            if record.role == ChatMessageRole.USER and record.content.strip():
                self.adopt_message(
                    record.message_id,
                    label=f"消息 {record.message_id}",
                    reason="代词指代：最近一条用户消息。",
                )
                return

    def _adopt_with_owner(self, message_id: str, *, reason: str) -> None:
        if message_id in self._adopted_ids:
            return
        self._anchors.append(
            ReferenceAnchor(
                anchor_id=f"message:{message_id}",
                kind=AnchorKind.MESSAGE,
                label=f"消息 {message_id}",
                message_ids=[message_id],
                adopted=True,
                reason=reason,
            )
        )
        self._adopted.append(message_id)
        self._adopted_ids.add(message_id)

    # -- 缺口 ---------------------------------------------------------------

    def compute_missing_terms(self, terms: Sequence[str]) -> None:
        """已定位部分锚点时，列出仍未命中的引用内容（不得掩盖缺失）。"""
        # 被已命中的更长候选包含的碎片不算独立缺失（如「排除」命中时，
        # 「排除项/除项」是对同一片段的切分）。
        covered_spans = [
            term for term in terms if any(matched in term for matched in self._matched_terms)
        ]
        unmatched = [
            term
            for term in terms
            if not any(term in m or m in term for m in self._matched_terms)
            and not any(term in span for span in covered_spans)
        ]
        for label in _distinct_labels(unmatched)[:MISSING_MAX_LABELS]:
            self._missing.append(f"引用内容「{label}」本轮未定位。")

    def mark_unresolved(self, terms: Sequence[str]) -> None:
        if self._missing:
            # 已有结构化缺口（如条件被撤销）时不再叠加词面噪声。
            return
        labels = _distinct_labels(terms)
        if not labels:
            labels = ["所指对象"]
        for label in labels[:MISSING_MAX_LABELS]:
            self._missing.append(f"引用内容「{label}」本轮未在会话历史中找到。")

    # -- 产出 ---------------------------------------------------------------

    def build(self) -> ReferenceResolution:
        recovery_candidates = [
            message_id for message_id in self._adopted if message_id not in self._recent
        ]
        recovered = recovery_candidates[:RECOVERED_MAX_MESSAGES]
        if len(recovery_candidates) > RECOVERED_MAX_MESSAGES:
            self._missing.append("需要补回的原文超过单轮上限，其余未补回。")
            for message_id in recovery_candidates[RECOVERED_MAX_MESSAGES:]:
                self.reject_message(
                    anchor_id=f"message:{message_id}", label=f"消息 {message_id}",
                    reason="超过单轮原文补回上限，本轮未提供原文。",
                )
        ordered_adopted = sorted(
            self._adopted,
            key=lambda message_id: self._order_index.get(message_id, 0),
        )
        status = ReferenceStatus.RESOLVED
        if self._missing:
            status = ReferenceStatus.PARTIAL if self._adopted else ReferenceStatus.UNRESOLVED
        if not self._adopted and not self._missing:
            status = ReferenceStatus.UNRESOLVED
        return ReferenceResolution(
            status=status,
            task=self._brief,
            anchors=self._anchors,
            adopted_message_ids=ordered_adopted,
            recovered_message_ids=recovered,
            adopted_object_ids=self._objects,
            correction_notes=self._corrections,
            missing_requirements=self._missing,
            rejected=self._rejected,
        )


# ---------------------------------------------------------------------------
# 序数 / 结果列表
# ---------------------------------------------------------------------------


def _resolve_ordinal(
    ordinal: _Ordinal,
    request: str,
    lists: Sequence[_ResultList],
    brief: ReferenceTaskBrief | None,
    task: ReferenceTaskContext | None,
    recent: set[str],
) -> ReferenceResolution:
    """结果列表上的「第 N 个/最后一个」：唯一列表直接续接，多列表才澄清。"""
    kind_hint = _kind_hint(request, ordinal.classifier)
    if not lists and kind_hint is None:
        # 没有结果列表就不是产物指代（避免把「第六个问题」当列表引用）。
        return ReferenceResolution(status=ReferenceStatus.NONE, task=brief)
    candidates = [
        result_list for result_list in lists if (kind_hint is None or result_list.kind == kind_hint)
    ]
    # 先取每个目标的最新列表，再判断项数；新版删掉的项不能从旧版复活。
    latest_by_group = {result_list.group_id: result_list for result_list in candidates}
    candidates = list(latest_by_group.values())
    terms = _reference_terms(request, quoted=[])
    scores = [
        sum(len(term) for term in terms if term in result_list.goal.lower())
        for result_list in candidates
    ]
    if scores and max(scores) > 0:
        candidates = [
            result_list for result_list, score in zip(candidates, scores, strict=True)
            if score == max(scores)
        ]
    eligible = [
        result_list
        for result_list in candidates
        if ordinal.is_last or len(result_list.items) >= ordinal.number
    ]
    if not eligible:
        if kind_hint:
            label = f"{_ordinal_phrase(ordinal, kind_hint)}对应的候选列表"
        else:
            label = f"{_ordinal_phrase(ordinal, None)}（现有列表项数不足）"
        return ReferenceResolution(
            status=ReferenceStatus.UNRESOLVED,
            task=brief,
            missing_requirements=[f"{label}本轮未定位。"],
        )
    if len(candidates) > 1:
        # 独立目标的同类或不同类列表会改变结果；只问一个必要问题，
        # 不用项数筛选掩盖版本关系不明的情况。
        options: list[str] = []
        rejected: list[ReferenceAnchor] = []
        kinds = {result_list.kind for result_list in candidates}
        ordered_kinds = [kind for kind in _KIND_LABELS if kind in kinds]
        ordered_kinds += [kind for kind in sorted(kinds) if kind not in ordered_kinds]
        for latest in sorted(candidates, key=lambda lst: ordered_kinds.index(lst.kind)):
            option = _ordinal_phrase(ordinal, latest.kind)
            if sum(lst.kind == latest.kind for lst in candidates) > 1:
                option += f"（{_shorten(latest.goal)}，来源消息 {latest.owner_message_id}）"
            options.append(option)
            rejected.append(
                ReferenceAnchor(
                    anchor_id=f"result_list:{latest.list_id}",
                    kind=AnchorKind.RESULT_LIST,
                    label=latest.label,
                    message_ids=[latest.message_id],
                    list_version=latest.version,
                    adopted=False,
                    reason="存在多个同样合理的列表，等待用户澄清后再选择。",
                )
            )
        question = "你指的是" + ("，还是".join(options)) + "？"
        return ReferenceResolution(
            status=ReferenceStatus.AMBIGUOUS,
            task=brief,
            rejected=rejected,
            clarification=ReferenceClarification(
                question=question,
                options=options,
                task_id=task.task_id if task is not None else None,
                expected_version=task.version if task is not None else None,
            ),
            missing_requirements=[],
        )
    latest = max(eligible, key=lambda lst: lst.version)
    if ordinal.is_last:
        item = latest.items[-1]
    else:
        item = next(
            (candidate for candidate in latest.items if candidate.ordinal == ordinal.number),
            latest.items[ordinal.number - 1],
        )
    owner_ids = [
        message_id for message_id in (latest.owner_message_id, latest.message_id) if message_id
    ]
    anchors = [
        ReferenceAnchor(
            anchor_id=f"result_list:{latest.list_id}",
            kind=AnchorKind.RESULT_LIST,
            label=latest.label,
            message_ids=[latest.message_id],
            list_version=latest.version,
            adopted=True,
            reason="唯一列表（按同类最新版本）直接续接。",
        ),
        ReferenceAnchor(
            anchor_id=f"list_item:{latest.list_id}:{item.ordinal}",
            kind=AnchorKind.LIST_ITEM,
            label=item.label,
            message_ids=[
                message_id
                for message_id in (latest.message_id, latest.owner_message_id)
                if message_id
            ],
            object_id=item.object_id,
            list_version=latest.version,
            adopted=True,
            reason=f"列表第 {item.ordinal} 项（最新版本）。",
        ),
    ]
    return ReferenceResolution(
        status=ReferenceStatus.RESOLVED,
        task=brief,
        anchors=anchors,
        adopted_message_ids=owner_ids,
        recovered_message_ids=[message_id for message_id in owner_ids if message_id not in recent],
        adopted_object_ids=[item.object_id],
    )


def _extract_ordinal(request: str) -> _Ordinal | None:
    last_match = _LAST_ORDINAL_RE.search(request)
    if last_match:
        return _Ordinal(number=0, classifier=last_match.group(1), is_last=True)
    match = _ORDINAL_RE.search(request)
    if match is None:
        return None
    number = _parse_number(match.group(1))
    if number is None or number < 1:
        return None
    return _Ordinal(number=number, classifier=match.group(2))


def _ordinal_phrase(ordinal: _Ordinal, kind: str | None) -> str:
    """生成面向用户的序数短语（不携带私人正文）。"""
    label = _KIND_LABELS.get(kind or "", "")
    unit = _KIND_UNITS.get(kind or "", "个")
    if ordinal.is_last:
        return f"最后一{unit}{label}"
    return f"第 {ordinal.number} {unit}{label}"


def _parse_number(token: str) -> int | None:
    if token.isdigit():
        return int(token)
    if token == "十":
        return 10
    if "十" in token:
        head, _, tail = token.partition("十")
        tens = _CN_DIGITS.get(head, 1) if head else 1
        units = _CN_DIGITS.get(tail, 0) if tail else 0
        return tens * 10 + units
    return _CN_DIGITS.get(token)


def _kind_hint(request: str, classifier: str) -> str | None:
    # 先定位序数紧邻的对象，不能让“第二个仓库对应哪篇论文”被后半句改成论文。
    ordinal_match = _ORDINAL_RE.search(request) or _LAST_ORDINAL_RE.search(request)
    if ordinal_match is not None:
        tail = request[ordinal_match.end():].lstrip(" 的")
        for kind, keywords in _KIND_KEYWORDS.items():
            if any(tail.startswith(keyword) for keyword in keywords):
                return kind
    classifier_kind = _CLASSIFIER_KINDS.get(classifier)
    if classifier_kind is not None:
        return classifier_kind
    hints = []
    for kind, keywords in _KIND_KEYWORDS.items():
        if any(keyword in request for keyword in keywords):
            hints.append(kind)
    return hints[0] if len(hints) == 1 else None


def _extract_result_lists(messages: Sequence[MessageRecord]) -> list[_ResultList]:
    """从助手消息的结构化投影提取结果列表（论文/仓库），按版本编号。"""
    lists: list[_ResultList] = []
    counters: dict[str, int] = {}
    last_group: dict[str, str] = {}
    goals: dict[str, str] = {}
    for index, record in enumerate(messages):
        if record.role != ChatMessageRole.ASSISTANT:
            continue
        extracted = _result_items(record)
        if extracted is None:
            continue
        kind, items = extracted
        owner_id = _previous_user_message_id(messages, index)
        owner = next((message for message in messages if message.message_id == owner_id), None)
        # 仅明确续接/更新才算同一目标的新版；独立主题列表保留歧义。
        continues = owner is not None and owner.content.startswith(
            (
                "再找", "再多找", "继续", "换一批", "更新", "重新找", "再来",
                "只留", "只保留", "缩减为",
            )
        )
        group_id = last_group.get(kind) if continues else None
        group_id = group_id or f"{kind}:{owner_id or record.message_id}"
        last_group[kind] = group_id
        counters[group_id] = counters.get(group_id, 0) + 1
        goals.setdefault(group_id, owner.content if owner is not None else "")
        lists.append(
            _ResultList(
                list_id=f"{kind}:{record.message_id}",
                group_id=group_id,
                goal=goals[group_id],
                kind=kind,
                message_id=record.message_id,
                owner_message_id=owner_id,
                version=counters[group_id],
                items=tuple(items),
            )
        )
    return lists


def _result_items(record: MessageRecord) -> tuple[str, list[_ResultItem]] | None:
    paper = record.paper_search or {}
    papers = paper.get("papers") if isinstance(paper, dict) else None
    if papers:
        items = [
            _ResultItem(
                ordinal=int(_field(entry, "order") or position),
                object_id=str(_field(entry, "arxiv_id") or _field(entry, "title") or ""),
                label=str(_field(entry, "title") or _field(entry, "arxiv_id") or ""),
            )
            for position, entry in enumerate(papers, start=1)
        ]
        if items:
            return "paper", items
    github = record.github_projects or {}
    recommendations = github.get("recommendations") if isinstance(github, dict) else None
    if recommendations:
        items = [
            _ResultItem(
                ordinal=int(_field(entry, "rank") or position),
                object_id=str(_field(entry, "full_name") or _field(entry, "html_url") or ""),
                label=str(_field(entry, "full_name") or ""),
            )
            for position, entry in enumerate(recommendations, start=1)
        ]
        if items:
            return "github", items
    return None


def _field(entry: Any, name: str) -> Any:
    if isinstance(entry, dict):
        return entry.get(name)
    return getattr(entry, name, None)


# ---------------------------------------------------------------------------
# 词面线索与消息匹配
# ---------------------------------------------------------------------------


def _reference_terms(request: str, *, quoted: Sequence[str]) -> list[str]:
    """提取检索线索：引号原文优先；否则用中文二/三元串与拉丁词。"""
    if quoted:
        return list(dict.fromkeys(quoted))
    terms: list[str] = []
    for latin in _LATIN_RUN_RE.findall(request):
        term = latin.lower()
        if term not in _STOP_TERMS:
            terms.append(term)
    for run in _CJK_RUN_RE.findall(request):
        for size in (3, 2):
            for start in range(0, len(run) - size + 1):
                gram = run[start : start + size]
                if gram in _STOP_TERMS:
                    continue
                if any(char in _FUNCTION_CHARS for char in gram):
                    continue
                terms.append(gram)
    return list(dict.fromkeys(terms))[:_MAX_TERMS]


def _match_messages(terms: Sequence[str], messages: Sequence[MessageRecord]) -> list[_MessageMatch]:
    # 同一关键词往往重复出现在原值与纠正轮，不能按出现频率删除它。
    effective = list(terms)
    if not effective:
        return []
    matches: list[_MessageMatch] = []
    for index, record in enumerate(messages):
        content = record.content.lower()
        hit = tuple(term for term in effective if term in content)
        if hit:
            matches.append(
                _MessageMatch(
                    index=index,
                    record=record,
                    terms=hit,
                    score=sum(len(term) for term in hit),
                )
            )
    matches.sort(key=lambda match: (-len(match.terms), -match.score, -match.index))
    return matches


def _condition_matches(condition: TaskCondition, terms: Sequence[str]) -> bool:
    haystack = f"{condition.kind} {condition.text}".lower()
    return any(term.lower() in haystack for term in terms)


def _is_adjacent_correction(previous: str, current: str) -> bool:
    """无主语的直接改值可续接；有对象的纠正/撤销须匹配原文对象。"""
    if current.startswith(("改成", "改为", "改到", "更正为")):
        return True
    markers = ("改成", "改为", "改到", "更正", "纠正", "取消", "撤销", "不再", "不用")
    if not any(marker in current for marker in markers):
        return False
    if current.strip("。！! ") in {"取消", "撤销", "不用了"}:
        return True
    return any(term in previous.lower() for term in _reference_terms(current, quoted=[]))


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def _task_brief(task: ReferenceTaskContext | None) -> ReferenceTaskBrief | None:
    if task is None:
        return None
    return ReferenceTaskBrief(
        task_id=task.task_id,
        goal=task.goal,
        version=task.version,
        status=task.status,
        conditions=[
            TaskBriefCondition(
                condition_id=condition.condition_id,
                kind=condition.kind,
                text=condition.text,
                status=condition.status.value,
                effective=condition.status == ConditionStatus.EFFECTIVE,
                source_message_id=condition.source_message_id,
                supersedes_condition_id=condition.supersedes_condition_id,
            )
            for condition in task.conditions
        ],
    )


def _superseded_chain(task: ReferenceTaskContext, condition: TaskCondition) -> list[TaskCondition]:
    by_id = {item.condition_id: item for item in task.conditions}
    chain: list[TaskCondition] = []
    cursor = by_id.get(condition.supersedes_condition_id or "")
    seen: set[str] = set()
    while cursor is not None and cursor.condition_id not in seen:
        seen.add(cursor.condition_id)
        chain.append(cursor)
        cursor = by_id.get(cursor.supersedes_condition_id or "")
    return chain


def _previous_user_message_id(messages: Sequence[MessageRecord], index: int) -> str | None:
    for record in reversed(messages[:index]):
        if record.role == ChatMessageRole.USER:
            return record.message_id
    return None


def _shorten(text: str, limit: int = 24) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"
