"""任务材料选择入口（改进工单 15）。

``docs/上下文工程/改进方案.md`` 第 4 节要求各模块不再各自把「最近 6 条
用户消息」当成全部上下文，而是共享一个可追溯的任务材料选择入口：
理解阶段只用当前原文、固定模式、任务快照与少量消歧前文；确定动作后按
节点目的选择材料。本模块把该入口固化为确定性纯函数，供编译、检索与模块
消费者共用：

1. **知识库、会话附件、画像、公开检索各有最小查询语义**：本地查询使用
   已解析主题/对象、当前有效条件与必要前文；公开查询只含必要的公开主题
   词，绝不携带任务条件、私人历史或画像正文。
2. **有效条件以任务快照为准**：只采用 ``effective`` 条件；被取代
   （``superseded``）、撤销（``revoked``）、草案（``draft``）与线索
   （``clue``）只记录排除 ID，不进入查询与模块上下文——无关旧条件因此
   不会污染新任务。
3. **模块声明所需任务字段、背景与证据范围**：:class:`ModuleContextDeclaration`
   是模块侧的只读声明；:func:`build_module_context` 按声明从当前任务与
   会话原文中选择有来源、有界的上下文，固定窗口只作为无任务时的兼容回退。

选择结果是**只读定位**：它不产生新条件、不改写消息，也不授予任何工具
权限。检索许可与来源有效性仍由既有的运行布尔开关、检索决策（工单 04/12）
与账户/会话作用域裁决；本模块只修查询与选择口径。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from bridges.contracts.references import (
    AnchorKind,
    ReferenceResolution,
    ReferenceTaskContext,
)
from bridges.contracts.tasks import ConditionStatus
from bridges.web_search.service import LocalQueryPlanner

#: 本模块的选择合同版本（字段或口径变化时递增）。
TASK_MATERIALS_VERSION = "task-materials-v2"
#: 模块上下文合同版本。
MODULE_CONTEXT_VERSION = "module-context-v2"

#: 单条本地查询的最大字符数（与检索清洗上限同量级，保持最小查询）。
LOCAL_QUERY_MAX_CHARS = 160
#: 公开查询的最大字符数（最小公开术语；具体脱敏仍由查询规划器执行）。
PUBLIC_QUERY_MAX_CHARS = 120
#: 查询中的单条条件正文上限（保留限定条件，不整段搬运）。
CONDITION_TERM_MAX_CHARS = 80

#: 指代/续接词：仅由这些词构成的请求没有自身主题，必须借助解析对象或
#: 少量消歧前文，而不是把空指代当成查询词。
_ANAPHORA_TERMS: tuple[str, ...] = (
    "第二个",
    "第三个",
    "第一个",
    "这一篇",
    "那一个",
    "这些",
    "那些",
    "它",
    "这个",
    "那个",
    "继续",
    "接着",
    "再讲讲",
    "再讲",
    "解释",
    "有什么区别",
    "什么区别",
    "区别",
    "上述",
    "上面",
    "刚才",
    "之前",
    "照之前",
    "按刚才",
    "同样",
)

_STRIP_CHARS = " ，。！？、；：,.!?;:-—~～「」“”\"'（）()【】[]"


class MaterialDomain(StrEnum):
    """一次任务材料选择的查询域（各自最小语义）。"""

    KNOWLEDGE_BASE = "knowledge_base"
    CONVERSATION_ATTACHMENT = "conversation_attachment"
    PROFILE = "profile"
    PUBLIC_SEARCH = "public_search"


@dataclass(frozen=True)
class EffectiveCondition:
    """选择结果中的一条当前有效条件（只读回显）。"""

    condition_id: str
    kind: str
    text: str
    source_message_id: str


@dataclass(frozen=True)
class TaskMaterialSelection:
    """共同任务材料选择结果（含各域最小查询与脱敏记录）。

    ``queries`` 的键为 :class:`MaterialDomain` 值；查询文本只在进程内
    用于检索，不进审计：``audit_record()`` 只输出 ID、计数与查询指纹。
    """

    version: str
    purpose: str
    topic_terms: tuple[str, ...]
    effective_conditions: tuple[EffectiveCondition, ...]
    excluded_condition_ids: tuple[str, ...]
    adopted_object_ids: tuple[str, ...]
    source_message_ids: tuple[str, ...]
    request_has_own_topic: bool
    queries: Mapping[str, str]
    used_continuation_fallback: bool = False

    def query_for(self, domain: MaterialDomain) -> str:
        """返回某域的最小查询；无内容时返回空串（调用方回退原文）。"""
        return self.queries.get(domain.value, "")

    def audit_record(self) -> dict[str, Any]:
        """审计记录（只含 ID、计数与查询指纹，不含任何查询/条件正文）。"""
        return {
            "version": self.version,
            "purpose": self.purpose,
            "topic_term_count": len(self.topic_terms),
            "effective_condition_ids": [c.condition_id for c in self.effective_conditions],
            "excluded_condition_ids": list(self.excluded_condition_ids),
            "adopted_object_ids": list(self.adopted_object_ids),
            "source_message_ids": list(self.source_message_ids),
            "request_has_own_topic": self.request_has_own_topic,
            "used_continuation_fallback": self.used_continuation_fallback,
            "query_fingerprint": _fingerprint(self.queries),
        }


def record_queries(context_budget: Mapping[str, Any] | None) -> Mapping[str, str]:
    """从编译执行记录读出各域查询（缺失或形状异常返回空映射）。"""
    if not context_budget:
        return {}
    queries = context_budget.get("task_queries")
    if not isinstance(queries, Mapping):
        return {}
    return {str(key): value for key, value in queries.items() if isinstance(value, str)}


def public_query_from_context(
    context_budget: Mapping[str, Any] | None, fallback: str
) -> str:
    """公开检索查询：保留编译期空查询，旧检查点也先提取公开术语。"""
    queries = record_queries(context_budget)
    if MaterialDomain.PUBLIC_SEARCH.value in queries:
        return queries[MaterialDomain.PUBLIC_SEARCH.value]
    # 旧检查点也先在本地提取公开术语，不把原文当作安全查询。
    return _public_query(fallback)


def _public_query(text: str) -> str:
    """复用本地公开术语规范化；先排除简历、书页及身份段落再限长。"""
    text = re.sub(
        r"(?:简历|履历|书页|个人经历|工作经历|教育经历|\bCV\b|\bresume\b)[^。！？；;\n]*[。！？；;]?",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    query = LocalQueryPlanner().plan(text, force=True).query
    return "" if query == "公开信息" else _clip(query, PUBLIC_QUERY_MAX_CHARS)


def _fingerprint(queries: Mapping[str, str]) -> str:
    """查询指纹：审计可核对两次选择是否同源，不暴露正文。"""
    normalized = "\n".join(f"{key}={queries[key]}" for key in sorted(queries))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _request_topic(request: str) -> tuple[str, ...]:
    """剥离指代/续接词后的请求自身主题；空元组表示请求只有指代。"""
    stripped = request
    for word in sorted(_ANAPHORA_TERMS, key=len, reverse=True):
        stripped = stripped.replace(word, " ")
    stripped = " ".join(stripped.split()).strip(_STRIP_CHARS)
    return (stripped,) if len(stripped) >= 2 else ()


def _clip(text: str, limit: int) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"


def _query_excerpt(text: str, limit: int) -> str:
    """查询节选保留开头主题和末尾限定；完整原文仍由任务快照持有。"""
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    head = limit // 2
    return compact[:head] + "…" + compact[-(limit - head - 1):]


def _conditions_from_task(
    resolution: ReferenceResolution | None,
    task: ReferenceTaskContext | None,
) -> tuple[tuple[EffectiveCondition, ...], tuple[str, ...]]:
    """从任务快照返回（有效条件, 排除条件 ID）。

    解析产出（``reference-v1`` 的 ``ReferenceTaskBrief``）优先：它已经
    携带原值→纠正→撤销关系；无解析任务时回退到工单 08 的任务快照。
    """
    effective: list[EffectiveCondition] = []
    excluded: list[str] = []
    seen: set[str] = set()
    brief = resolution.task if resolution is not None else None
    if brief is not None and brief.conditions:
        for condition in brief.conditions:
            if condition.condition_id in seen:
                continue
            seen.add(condition.condition_id)
            if condition.status == ConditionStatus.EFFECTIVE.value:
                effective.append(
                    EffectiveCondition(
                        condition_id=condition.condition_id,
                        kind=condition.kind,
                        text=condition.text,
                        source_message_id=condition.source_message_id,
                    )
                )
            else:
                excluded.append(condition.condition_id)
        return tuple(effective), tuple(excluded)
    if task is not None:
        for task_condition in task.conditions:
            if task_condition.condition_id in seen:
                continue
            seen.add(task_condition.condition_id)
            if task_condition.status is ConditionStatus.EFFECTIVE:
                effective.append(
                    EffectiveCondition(
                        condition_id=task_condition.condition_id,
                        kind=task_condition.kind,
                        text=task_condition.text,
                        source_message_id=task_condition.source_message_id,
                    )
                )
            else:
                excluded.append(task_condition.condition_id)
    return tuple(effective), tuple(excluded)


def _topic_terms_from_resolution(
    resolution: ReferenceResolution | None,
) -> tuple[str, ...]:
    """从解析锚点提取紧凑主题词（结果列表/列表项的短标签）。

    任务锚点的标签是内部标识（``任务 {id}（版本 {n}）``），不是用户主题，
    因此不作为查询词——否则纯续接会把任务 ID 带进本地与公开查询。
    """
    if resolution is None:
        return ()
    terms: list[str] = []
    has_item = any(
        anchor.adopted and anchor.kind is AnchorKind.LIST_ITEM for anchor in resolution.anchors
    )
    for anchor in resolution.anchors:
        if has_item and anchor.kind is AnchorKind.RESULT_LIST:
            continue
        if not anchor.adopted or anchor.kind not in {
            AnchorKind.LIST_ITEM,
            AnchorKind.RESULT_LIST,
        }:
            continue
        label = anchor.label.strip()
        if len(label) >= 2 and label not in terms:
            terms.append(label)
    return tuple(terms[:4])


def select_task_materials(
    request: str,
    *,
    resolution: ReferenceResolution | None = None,
    task: ReferenceTaskContext | None = None,
    current_message_id: str | None = None,
    recent_user_texts: Sequence[str] = (),
    purpose: str = "answer",
) -> TaskMaterialSelection:
    """按任务快照与指代解析选择各域最小查询。

    ``recent_user_texts`` 是当前消息之前最近的用户原文（新的在前），只在
    请求本身没有主题时作为**少量消歧前文**回退；它只在本进程参与查询
    构造，不写审计、不外发。
    """
    own_terms = _request_topic(request)
    topic_terms = _topic_terms_from_resolution(resolution)
    effective, excluded = _conditions_from_task(resolution, task)
    used_fallback = False
    task_goal = resolution.task.goal if resolution is not None and resolution.task else (
        task.goal if task is not None else ""
    )
    if not topic_terms:
        topic_terms = tuple(
            condition.text for condition in effective if condition.kind in {"topic", "scenario"}
        ) or ((task_goal,) if task_goal else ())
    if not topic_terms and not own_terms and not task_goal and not effective:
        # 请求只有指代且没有任务条件：允许用最近的少量消歧前文定位主题。
        for text in recent_user_texts:
            fallback = _request_topic(text)
            if fallback:
                topic_terms = fallback
                used_fallback = True
                break
    adopted_ids: list[str] = []
    if resolution is not None:
        for message_id in resolution.adopted_message_ids:
            if message_id != current_message_id and message_id not in adopted_ids:
                adopted_ids.append(message_id)
    source_ids: list[str] = []
    for message_id in adopted_ids:
        if message_id not in source_ids:
            source_ids.append(message_id)
    for condition in effective:
        if condition.source_message_id and condition.source_message_id not in source_ids:
            source_ids.append(condition.source_message_id)
    if task is not None:
        for message_id in task.source_message_ids:
            if message_id not in source_ids:
                source_ids.append(message_id)

    request_part = " ".join(own_terms)
    topic_part = " ".join(topic_terms)
    condition_part = " ".join(
        _query_excerpt(condition.text, CONDITION_TERM_MAX_CHARS)
        for condition in reversed(effective)
    )
    # 为对象和本轮纠正分别留空间，条件按最新顺序优先；避免前文长标签
    # 挤掉末尾纠正。完整有效条件仍保存在选择结果，不把查询当作条件权威。
    request_query = _query_excerpt(request_part, 40)
    local_parts = [
        part for part in (_clip(topic_part, 40), request_query, condition_part) if part
    ]
    profile_parts = [part for part in (topic_part, request_part) if part]
    public_parts = [part for part in (topic_part, request_part) if part]
    queries = {
        MaterialDomain.KNOWLEDGE_BASE.value: _clip(" ".join(local_parts), LOCAL_QUERY_MAX_CHARS),
        MaterialDomain.CONVERSATION_ATTACHMENT.value: _clip(
            " ".join(local_parts), LOCAL_QUERY_MAX_CHARS
        ),
        MaterialDomain.PROFILE.value: _clip(" ".join(profile_parts), LOCAL_QUERY_MAX_CHARS),
        MaterialDomain.PUBLIC_SEARCH.value: (
            _public_query("。".join(public_parts)) if not used_fallback else ""
        ),
    }
    return TaskMaterialSelection(
        version=TASK_MATERIALS_VERSION,
        purpose=purpose,
        topic_terms=topic_terms,
        effective_conditions=effective,
        excluded_condition_ids=excluded,
        adopted_object_ids=(
            tuple(resolution.adopted_object_ids) if resolution is not None else ()
        ),
        source_message_ids=tuple(source_ids),
        request_has_own_topic=bool(own_terms),
        queries=queries,
        used_continuation_fallback=used_fallback,
    )


# ---------------------------------------------------------------------------
# 模块声明与模块任务上下文
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModuleContextDeclaration:
    """一个模块声明的任务字段、背景与证据范围（只读声明，不授予权限）。"""

    module_id: str
    purpose: str
    #: 允许进入模块上下文的有效条件 ``kind`` 白名单；空元组表示全部有效条件。
    task_fields: tuple[str, ...] = ()
    #: 无任务时兼容回退的前文条数上限（有任务时只取有来源的相关前文）。
    lookback: int = 6
    #: 证据范围说明（审计与测试用，不改变运行时数据选择）。
    evidence_scope: str = ""


#: 已接入共同入口的模块声明（逐步替代固定最近 6 条用户消息与只看论文/
#: GitHub 投影的锚点；确定性模块不被强迫使用相同提示词）。
MODULE_DECLARATIONS: dict[str, ModuleContextDeclaration] = {
    "paper_search": ModuleContextDeclaration(
        module_id="paper_search",
        purpose="paper",
        task_fields=("topic", "domain", "year", "paper_type", "time", "exclusion"),
        evidence_scope="任务主题、研究条件与有来源的相关前文；不读取画像正文。",
    ),
    "github_projects": ModuleContextDeclaration(
        module_id="github_projects",
        purpose="github",
        task_fields=("scenario", "feature", "tech", "requirement", "exclusion"),
        evidence_scope="任务目标/必需功能条件与已保存的论文、仓库锚点。",
    ),
    "learning_resources": ModuleContextDeclaration(
        module_id="learning_resources",
        purpose="resources",
        task_fields=("topic", "level", "goal", "language", "medium", "exclusion"),
        evidence_scope="任务主题、基础水平与媒介条件；不读取画像正文。",
    ),
    "commute": ModuleContextDeclaration(
        module_id="commute",
        purpose="commute",
        task_fields=("origin", "destination", "mode"),
        evidence_scope="已确认起终点与方式条件；不扩散到无关话题。",
    ),
    "career": ModuleContextDeclaration(
        module_id="career",
        purpose="career",
        task_fields=("city", "other", "count"),
        evidence_scope="任务目标与已确认城市/条件；不读取画像正文，也不继承无关话题。",
    ),
}


@dataclass(frozen=True)
class ModuleTaskContext:
    """按模块声明选择的上下文（任务字段 + 背景 + 证据范围，有界且有来源）。"""

    version: str
    module_id: str
    purpose: str
    task_id: str | None
    task_goal: str
    topic_hint: str
    effective_conditions: tuple[EffectiveCondition, ...]
    excluded_condition_ids: tuple[str, ...]
    prior_messages: tuple[str, ...]
    source_message_ids: tuple[str, ...]
    evidence_scope: str
    used_task_scope: bool

    def audit_record(self) -> dict[str, Any]:
        """审计记录（ID、计数与范围说明，不含消息/条件正文）。"""
        return {
            "version": self.version,
            "module_id": self.module_id,
            "purpose": self.purpose,
            "task_id": self.task_id,
            "effective_condition_ids": [c.condition_id for c in self.effective_conditions],
            "excluded_condition_ids": list(self.excluded_condition_ids),
            "prior_message_count": len(self.prior_messages),
            "source_message_ids": list(self.source_message_ids),
            "evidence_scope": self.evidence_scope,
            "used_task_scope": self.used_task_scope,
        }


def _mentions(text: str, needles: Sequence[str]) -> bool:
    lowered = text.lower()
    return any(
        needle.lower() in lowered
        for needle in needles
        if len(needle.strip()) >= 2
    )


def build_module_context(
    *,
    declaration: ModuleContextDeclaration,
    task: ReferenceTaskContext | None,
    messages: Sequence[tuple[str, str, str]],
    current_user_message_id: str,
) -> ModuleTaskContext:
    """按模块声明构建当前任务上下文（确定性、只读、有界）。

    ``messages`` 是 ``(message_id, role, content)`` 的会话时间序序列。
    有任务时：只取任务来源消息与命中任务主题/条件的前文，**不**用无关
    旧消息填充；无任务时回退最近 ``declaration.lookback`` 条用户消息，
    保持既有兼容行为。
    """
    effective: tuple[EffectiveCondition, ...]
    excluded: tuple[str, ...]
    if task is not None:
        conditions: list[EffectiveCondition] = []
        excluded_list: list[str] = []
        for condition in task.conditions:
            if (
                condition.status is ConditionStatus.EFFECTIVE
                and (
                    not declaration.task_fields
                    or condition.kind in declaration.task_fields
                )
            ):
                conditions.append(
                    EffectiveCondition(
                        condition_id=condition.condition_id,
                        kind=condition.kind,
                        text=condition.text,
                        source_message_id=condition.source_message_id,
                    )
                )
            else:
                excluded_list.append(condition.condition_id)
        effective = tuple(conditions)
        excluded = tuple(excluded_list)
    else:
        effective = ()
        excluded = ()
    topic_needles: list[str] = [task.goal if task is not None else ""]
    topic_needles.extend(condition.text for condition in effective)
    topic_needles = [needle for needle in topic_needles if needle]
    source_ids: set[str] = set()
    excluded_source_ids: set[str] = set()
    if task is not None:
        excluded_source_ids.update(
            condition.source_message_id for condition in task.conditions
            if condition.condition_id in excluded
        )
        source_ids.update(task.source_message_ids)
        source_ids.update(condition.source_message_id for condition in effective)
    # 只取当前消息之前、且属于任务来源或命中任务主题/条件的前文；条数仍以
    # ``lookback`` 封顶（有来源且有界），当前消息之后的消息绝不进入模块上下文。
    prior: list[str] = []
    adopted_prior_ids: list[str] = []
    for message_id, role, content in messages:
        if message_id == current_user_message_id:
            break
        if role != "user" or not content.strip():
            continue
        # 原文中被撤销/取代或不属本模块的条件不可绕回背景再次生效。
        if message_id in excluded_source_ids:
            continue
        if task is not None and not (
            message_id in source_ids or _mentions(content, topic_needles)
        ):
            continue
        prior.append(content)
        adopted_prior_ids.append(message_id)
    limit = max(0, declaration.lookback)
    prior = prior[-limit:] if limit else []
    adopted_prior_ids = adopted_prior_ids[-limit:] if limit else []
    topic_hint = ""
    for effective_condition in effective:
        if effective_condition.kind in {"topic", "scenario"} and effective_condition.text.strip():
            topic_hint = effective_condition.text.strip()
            break
    if not topic_hint and task is not None:
        topic_hint = task.goal.strip()
    if declaration.purpose in {"paper", "github", "resources"}:
        # 主题提示会进入公开模块查询，任务目标里的私人段落只留在本地。
        topic_hint = _public_query(topic_hint)
    adopted_source_ids = list(adopted_prior_ids)
    condition_source_ids = {condition.source_message_id for condition in effective}
    for message_id, role, content in messages:
        if (
            role == "user"
            and message_id in condition_source_ids
            and content.strip()
            and message_id not in adopted_source_ids
        ):
            adopted_source_ids.append(message_id)
        if message_id == current_user_message_id:
            break
    return ModuleTaskContext(
        version=MODULE_CONTEXT_VERSION,
        module_id=declaration.module_id,
        purpose=declaration.purpose,
        task_id=task.task_id if task is not None else None,
        task_goal=task.goal if task is not None else "",
        topic_hint=topic_hint,
        effective_conditions=effective,
        excluded_condition_ids=excluded,
        prior_messages=tuple(prior),
        source_message_ids=tuple(adopted_source_ids),
        evidence_scope=declaration.evidence_scope,
        used_task_scope=task is not None,
    )


__all__ = [
    "CONDITION_TERM_MAX_CHARS",
    "LOCAL_QUERY_MAX_CHARS",
    "MODULE_CONTEXT_VERSION",
    "MODULE_DECLARATIONS",
    "PUBLIC_QUERY_MAX_CHARS",
    "TASK_MATERIALS_VERSION",
    "EffectiveCondition",
    "MaterialDomain",
    "ModuleContextDeclaration",
    "ModuleTaskContext",
    "TaskMaterialSelection",
    "build_module_context",
    "public_query_from_context",
    "record_queries",
    "select_task_materials",
]
