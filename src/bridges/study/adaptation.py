"""Issue 09：按学情调整后续教法与路线。

职责边界：

- 本轮唯一的采用快照：只经 06 提供的有效档案读取边界
  （``StudyRecordControlService.effective_records``）选择相关、已核验且
  有效的学情；不固定取最近五条，不注入整份档案。
- 学情影响实际教学动作：生成导师提示词区块与验证规划上下文（提示粒度、
  例子、前置知识补讲、迁移应用与必要复核）；必要时形成带版本与原因的
  路线修订建议。本模块不调用模型，普通教学不增加额外规划。
- 快照带记录身份、记录版本与目标/路线版本；后续模型调用前逐条复验，
  纠正、删除、来源失效或目标/路线版本变化使快照失效，不继续外发旧信息。
- 采用记录与审计只含必要引用/状态（记录标识、版本、调整方向），不记录
  学情正文、提示词或模型内部推理。
"""

from __future__ import annotations

import json
import re
import secrets
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from bridges.contracts.observability import AuditAction, AuditResult
from bridges.contracts.study_adaptation import (
    ADAPTATION_CONTRACT_VERSION,
    StudyAdaptationAdjustment,
    StudyAdaptationSnapshot,
    StudyAdoptedRecord,
    StudyAdoptionProjection,
)
from bridges.contracts.study_diagnosis import (
    StudyDiagnosisVerdict,
    StudyRecordBoundaryEntry,
)
from bridges.contracts.study_goals import StudyGoalProjection, StudyRouteProjection
from bridges.storage.database import BridgesDatabase
from bridges.study.goals import StudyGoalRepository
from bridges.study.record_controls import StudyRecordControlService

#: 正式采用的学情条数上限（有界；按相关性而不是最近时间选择）。
ADAPTED_RECORD_LIMIT = 3
#: 单个触发词集合允许匹配的最小重叠词数（中文按二字词）。
_TITLE_TERM_MIN = 1
#: 采用快照在运行配置中的键；重试沿用同一快照后仍按调用前复验。
STUDY_ADAPTATION_CONFIG_KEY = "study_adaptation"
#: 会话详情展示的采用记录条数上限。
ADOPTION_QUERY_LIMIT = 5
#: 相关但待进一步验证的学情标题上限。
_CONFLICT_TITLE_LIMIT = 3

#: 可正式采用的核验结论；未验证/无法判断不是已核验学情。
_ADOPTABLE_VERDICTS = frozenset(
    {
        StudyDiagnosisVerdict.INDEPENDENT_COMPLETION,
        StudyDiagnosisVerdict.HINTED_COMPLETION,
        StudyDiagnosisVerdict.MISCONCEPTION,
    }
)

#: 用户明确控制（跳过/直接讲解/继续/暂停等）时不做自动路线回退；
#: 这是确定性保守触发器，真正的用户控制语义仍由验证规划角色判定。
_CONTROL_MARKERS: tuple[str, ...] = (
    "跳过",
    "别讲",
    "不用讲",
    "不用补",
    "不要补",
    "不想",
    "直接讲",
    "直接说",
    "直接给",
    "继续",
    "接着",
    "往下",
    "下一步",
    "暂停",
    "今天先",
    "先到这里",
    "小结",
)

_VERDICT_LABEL = {
    StudyDiagnosisVerdict.INDEPENDENT_COMPLETION: "独立完成",
    StudyDiagnosisVerdict.HINTED_COMPLETION: "提示后完成",
    StudyDiagnosisVerdict.MISCONCEPTION: "存在具体误解",
}


@dataclass
class StudyAdaptationTurnState:
    """一轮生成内共享的采用状态（服务层与回合编排之间）。"""

    snapshot: StudyAdaptationSnapshot | None = None
    block: str = ""
    route_revision: RouteRevisionPlan | None = None
    applied: bool = False
    invalid_reason: str = ""


@dataclass(frozen=True)
class RouteRevisionPlan:
    """按有效学情回退到前置节点的路线修订计划（终态事务内按版本提交）。"""

    goal_id: str
    target_node_id: str
    expected_route_version: int
    reason: str


class StudyAdaptationService:
    """采用快照的编译、复用、审计与持久化；账户作用域强制。"""

    def __init__(self, database: BridgesDatabase, *, observability: Any = None) -> None:
        self._database = database
        self._observability = observability

    # ------------------------------------------------------------------
    # 编译与复用
    # ------------------------------------------------------------------

    def load_or_compile(
        self,
        *,
        account_id: str,
        conversation_id: str,
        run_config: Mapping[str, Any] | None,
        goal: StudyGoalProjection | None,
        route: StudyRouteProjection | None,
        knowledge_context: str,
        current_focus: str,
        user_message: str,
    ) -> StudyAdaptationSnapshot:
        """读取本轮唯一采用快照；重试沿用已保存快照并逐条复验。"""

        saved = (run_config or {}).get(STUDY_ADAPTATION_CONFIG_KEY)
        if isinstance(saved, dict):
            try:
                candidate = StudyAdaptationSnapshot.model_validate(saved)
            except ValidationError:
                candidate = None
            if (
                candidate is not None
                and candidate.conversation_id == conversation_id
                and revalidate_snapshot(self._database, account_id, candidate) is None
            ):
                return candidate
        boundaries = StudyRecordControlService(self._database).effective_records(
            account_id
        )
        entries, uncertainty_note = select_relevant_records(
            boundaries,
            knowledge_context=knowledge_context,
            current_focus=current_focus,
            user_message=user_message,
            goal=goal,
            route=route,
        )
        return StudyAdaptationSnapshot(
            adoption_id=f"sa-{secrets.token_urlsafe(16)}",
            conversation_id=conversation_id,
            goal_id=goal.goal_id if goal is not None else "",
            goal_version=goal.version if goal is not None else 0,
            route_version=route.version if route is not None else 0,
            entries=entries,
            uncertainty_note=uncertainty_note,
            created_at=datetime.now(UTC),
        )

    # ------------------------------------------------------------------
    # 审计（只含必要引用/状态）
    # ------------------------------------------------------------------

    def audit_compiled(
        self,
        account_id: str,
        snapshot: StudyAdaptationSnapshot,
    ) -> None:
        """采用编译审计：记录采用了哪些学情与调整方向，不含学情正文。"""

        if self._observability is None or snapshot.is_empty():
            return
        self._observability.log_audit(
            actor_account_id=account_id,
            action=AuditAction.STUDY_ADAPTATION_APPLIED,
            result=AuditResult.SUCCESS,
            object_refs=[snapshot.adoption_id],
            reason="本轮正式采用有效学情调整教法。",
            details=audit_details(snapshot, status="compiled"),
        )

    def audit_invalidated(
        self,
        account_id: str,
        snapshot: StudyAdaptationSnapshot,
        reason: str,
    ) -> None:
        """调用前复验失败审计：旧采用已撤回，不继续外发。"""

        if self._observability is None:
            return
        details = audit_details(snapshot, status="invalidated")
        details["invalid_reason"] = reason
        self._observability.log_audit(
            actor_account_id=account_id,
            action=AuditAction.STUDY_ADAPTATION_APPLIED,
            result=AuditResult.BLOCKED,
            object_refs=[snapshot.adoption_id],
            reason="采用期间学情被纠正、删除或失效，本轮撤回采用。",
            details=details,
        )

    # ------------------------------------------------------------------
    # 终态持久化与用户投影
    # ------------------------------------------------------------------

    def commit_in_transaction(
        self,
        account_id: str,
        run_id: str,
        assistant_message_id: str,
        snapshot: StudyAdaptationSnapshot,
        *,
        now: datetime | None = None,
    ) -> None:
        """在消息成功终态事务内写入采用记录；失败整体回滚。"""

        timestamp = (now or datetime.now(UTC)).isoformat()
        self._database.scoped(account_id).execute(
            "INSERT INTO study_record_adoptions"
            " (adoption_id, account_id, conversation_id, run_id,"
            " assistant_message_id, goal_id, goal_version, route_version,"
            " status, invalid_reason, entries_json, uncertainty_note,"
            " contract_version, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'applied', ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(adoption_id) DO NOTHING",
            (
                snapshot.adoption_id,
                account_id,
                snapshot.conversation_id,
                run_id,
                assistant_message_id,
                snapshot.goal_id,
                snapshot.goal_version,
                snapshot.route_version,
                "",
                _entries_json(snapshot),
                snapshot.uncertainty_note,
                ADAPTATION_CONTRACT_VERSION,
                timestamp,
                timestamp,
            ),
        )

    def projections(
        self,
        account_id: str,
        conversation_id: str,
        *,
        limit: int = ADOPTION_QUERY_LIMIT,
    ) -> list[StudyAdoptionProjection]:
        """用户可见的采用依据：只返回真实生效的采用记录。"""

        rows = self._database.scoped(account_id).execute(
            "SELECT * FROM study_record_adoptions"
            " WHERE account_id = ? AND conversation_id = ? AND status = 'applied'"
            " ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (account_id, conversation_id, max(1, int(limit))),
        ).fetchall()
        projections: list[StudyAdoptionProjection] = []
        for row in rows:
            entries = parse_entries(str(row["entries_json"]))
            projections.append(
                StudyAdoptionProjection(
                    adoption_id=str(row["adoption_id"]),
                    goal_id=str(row["goal_id"]),
                    goal_version=int(row["goal_version"]),
                    route_version=int(row["route_version"]),
                    status=str(row["status"]),
                    invalid_reason=str(row["invalid_reason"]),
                    entries=entries,
                    uncertainty_note=str(row["uncertainty_note"]),
                    assistant_message_id=str(row["assistant_message_id"]),
                    created_at=_parse_time(str(row["created_at"])),
                )
            )
        return projections


# ---------------------------------------------------------------------------
# 相关性选择（确定性；不调用模型、不注入整份档案）
# ---------------------------------------------------------------------------


def select_relevant_records(
    boundaries: list[StudyRecordBoundaryEntry],
    *,
    knowledge_context: str,
    current_focus: str,
    user_message: str,
    goal: StudyGoalProjection | None,
    route: StudyRouteProjection | None,
) -> tuple[list[StudyAdoptedRecord], str]:
    """按知识上下文、目标与证据适用性选择有界的相关学情。

    只有有效（未删除）、已核验（未被纠正且来源可用）且结论属于
    独立完成/提示后完成/具体误解的记录可进入正式采用；同名但知识
    上下文不同的记录不得互相顶替：存在同名多身份时必须有学科或用途
    上下文的二次匹配。待进一步验证的方向冲突记录不当作事实采用，
    但保留为不确定性说明。
    """

    target_text = _target_text(
        knowledge_context=knowledge_context,
        current_focus=current_focus,
        user_message=user_message,
        goal=goal,
        route=route,
    )
    target_terms = _terms(target_text)
    target_normalized = _normalize(target_text)
    titles = [
        _normalize(entry.title) for entry in boundaries if _normalize(entry.title)
    ]
    duplicated_titles = {
        title for title in titles if titles.count(title) > 1
    }
    candidates: list[tuple[int, StudyRecordBoundaryEntry]] = []
    conflicts: list[StudyRecordBoundaryEntry] = []
    for boundary in boundaries:
        if not boundary.effective or not boundary.verified:
            continue
        if boundary.verdict not in _ADOPTABLE_VERDICTS:
            continue
        normalized_title = _normalize(boundary.title)
        if not normalized_title:
            continue
        title_overlap = len(_terms(boundary.title) & target_terms)
        if title_overlap < _TITLE_TERM_MIN and normalized_title not in target_normalized:
            continue
        # 同名区分只认用途上下文（学科过宽，不能单独构成二次匹配）；
        # 没有上下文命中时不采用，避免同名不同概念互相顶替。
        secondary_overlap = len(_terms(boundary.usage_context) & target_terms)
        if normalized_title in duplicated_titles and not secondary_overlap:
            # 同名不同知识上下文：没有学科/用途二次匹配时不采用，避免串用。
            continue
        if boundary.pending_confirmation:
            conflicts.append(boundary)
            continue
        candidates.append((title_overlap * 2 + secondary_overlap, boundary))
    candidates.sort(key=lambda item: (item[0], item[1].updated_at), reverse=True)
    entries = [
        _adopted_record(boundary)
        for _, boundary in candidates[:ADAPTED_RECORD_LIMIT]
    ]
    uncertainty_note = ""
    if conflicts:
        titles_text = "」「".join(
            _short(entry.title, 40)
            for entry in conflicts[:_CONFLICT_TITLE_LIMIT]
            if entry.title.strip()
        )
        if titles_text:
            uncertainty_note = (
                f"相关学情「{titles_text}」存在方向冲突、待进一步验证："
                "不要据此宣称已掌握或已存在缺口，必要时安排一次独立复核。"
            )
    return entries, uncertainty_note


def _target_text(
    *,
    knowledge_context: str,
    current_focus: str,
    user_message: str,
    goal: StudyGoalProjection | None,
    route: StudyRouteProjection | None,
) -> str:
    parts = [knowledge_context, current_focus, user_message]
    if goal is not None:
        parts.extend([goal.statement, goal.observable_result, goal.scope])
    if route is not None:
        nodes = {node.node_id: node for node in route.nodes}
        current = nodes.get(route.current_node_id)
        if current is not None:
            parts.append(current.title)
            parts.extend(
                nodes[ref].title
                for ref in current.prerequisites
                if ref in nodes
            )
    return " ".join(part for part in parts if part)


def _adopted_record(boundary: StudyRecordBoundaryEntry) -> StudyAdoptedRecord:
    adjustments, reason = _adjustments_for(boundary)
    return StudyAdoptedRecord(
        record_id=boundary.record_id,
        record_version=boundary.record_version,
        subject=boundary.subject,
        title=boundary.title,
        usage_context=boundary.usage_context,
        verdict=boundary.verdict,
        performance=boundary.performance,
        adjustments=adjustments,
        reason=reason,
    )


def _adjustments_for(
    boundary: StudyRecordBoundaryEntry,
) -> tuple[list[StudyAdaptationAdjustment], str]:
    if boundary.verdict == StudyDiagnosisVerdict.MISCONCEPTION:
        return (
            [
                StudyAdaptationAdjustment.PREREQUISITE_REVIEW,
                StudyAdaptationAdjustment.HINT_GRANULARITY,
            ],
            "已核验存在具体误解：先检查或补讲这个知识点，改用更小步的提示或反例，"
            "不要重复同类追问。",
        )
    if boundary.verdict == StudyDiagnosisVerdict.HINTED_COMPLETION:
        return (
            [StudyAdaptationAdjustment.INDEPENDENT_APPLICATION],
            "此前是提示后完成：安排一次独立应用，不要当作已稳定掌握。",
        )
    adjustments = [StudyAdaptationAdjustment.TRANSFER_APPLICATION]
    reason = "已独立完成过一次：改用迁移应用或更高一层的检查，不重复同类基础题。"
    if boundary.uncertainty:
        adjustments.append(StudyAdaptationAdjustment.RECHECK)
        reason = "已独立完成过一次：改用迁移应用；结论仍有不确定性，在合适位置安排复核。"
    return adjustments, reason


# ---------------------------------------------------------------------------
# 调用前复验（纠正/删除/来源失效/目标与路线版本变化使快照失效）
# ---------------------------------------------------------------------------


def revalidate_snapshot(
    database: BridgesDatabase, account_id: str, snapshot: StudyAdaptationSnapshot
) -> str | None:
    """返回快照失效原因；完全有效时返回 None。"""

    if snapshot.goal_id:
        goals = StudyGoalRepository(database)
        goal = goals.get_goal(account_id, snapshot.goal_id)
        if goal is None or goal.version != snapshot.goal_version:
            return "goal_version_changed"
        route = goals.current_route(account_id, snapshot.goal_id)
        if (route.version if route is not None else 0) != snapshot.route_version:
            return "route_version_changed"
    controls = StudyRecordControlService(database)
    for entry in snapshot.entries:
        boundary = controls.record_boundary(account_id, entry.record_id)
        if boundary is None:
            return "record_missing"
        if not boundary.effective:
            return "record_deleted"
        if not boundary.verified:
            return "record_changed"
        if boundary.record_version != entry.record_version:
            return "record_changed"
    return None


# ---------------------------------------------------------------------------
# 模型载荷渲染
# ---------------------------------------------------------------------------


def render_directive(snapshot: StudyAdaptationSnapshot) -> str:
    """导师提示词区块：把采用学情落实到可观察的教法调整。"""

    if snapshot.is_empty():
        return ""
    lines = [
        "【学情采用（内部依据，不得原样展示给用户）】",
        "以下相关、已核验且有效的学情已按记录版本正式采用，用于调整本轮教法：",
    ]
    for index, entry in enumerate(snapshot.entries, 1):
        label = entry.title
        if entry.subject:
            label = f"{entry.subject} · {label}"
        if entry.usage_context:
            label += f"（{entry.usage_context}）"
        state = _VERDICT_LABEL.get(entry.verdict, entry.verdict.value)
        lines.append(
            f"{index}. {label}，上次核验：{state}"
            f"（记录版本 {entry.record_version}）。表现：{_short(entry.performance, 200)}"
        )
        lines.append(f"   调整：{entry.reason}")
    lines.append(
        "使用要求：把上述学情落实到提示粒度、例子选择、前置知识补讲、"
        "迁移应用或必要复核；不要只在结尾补一句「已结合画像」。"
        "提示后完成不得称为已稳定掌握，单次独立完成也不得称为长期掌握；"
        "不得把学情写成固定能力标签。用户明确要求直接讲解、跳过、继续或"
        "改变方向时一律优先，补讲建议不得阻止继续；不得用学情修改学习目标。"
    )
    if snapshot.uncertainty_note:
        lines.append(snapshot.uncertainty_note)
    lines.append(
        "可以在回复中用一句话说明为什么先补这个前置点"
        "（如「之前的核验显示你在这里有一个具体误解，我们先补这个前置点」）；"
        "不要展示本区块原文、记录版本号或内部判定线索。"
    )
    return "\n".join(lines)


def planner_context(snapshot: StudyAdaptationSnapshot) -> dict[str, Any] | None:
    """验证规划请求中的采用上下文（有界、只含教学所需字段）。"""

    if snapshot.is_empty():
        return None
    return {
        "adopted": [
            {
                "title": entry.title,
                "subject": entry.subject,
                "usage_context": entry.usage_context,
                "verdict": entry.verdict.value,
                "performance": _short(entry.performance, 160),
                "adjustments": [item.value for item in entry.adjustments],
            }
            for entry in snapshot.entries
        ],
        "uncertainty": snapshot.uncertainty_note,
    }


def audit_details(
    snapshot: StudyAdaptationSnapshot, *, status: str
) -> dict[str, Any]:
    """审计详情：只含采用标识、版本、结论与调整方向，不含学情正文。"""

    return {
        "status": status,
        "adoption_id": snapshot.adoption_id,
        "goal_id": snapshot.goal_id,
        "goal_version": snapshot.goal_version,
        "route_version": snapshot.route_version,
        "adopted": [
            {
                "record_id": entry.record_id,
                "record_version": entry.record_version,
                "verdict": entry.verdict.value,
                "adjustments": [item.value for item in entry.adjustments],
            }
            for entry in snapshot.entries
        ],
        "has_uncertainty": bool(snapshot.uncertainty_note),
    }


# ---------------------------------------------------------------------------
# 路线修订建议（必要回退到前置节点；不每轮调用完整规划）
# ---------------------------------------------------------------------------


def suggest_route_revision(
    snapshot: StudyAdaptationSnapshot,
    *,
    route: StudyRouteProjection | None,
    user_message: str,
) -> RouteRevisionPlan | None:
    """已核验误解命中当前节点的前置链时建议回退到该前置节点。

    只在确有需要时生成：存在已核验误解、命中路线中当前节点的前置节点、
    且用户本轮没有表达跳过/直接讲解/继续等控制语义。修订按既有路线版本
    守卫提交，冲突时不覆盖并如实跳过。
    """

    if route is None or not snapshot.entries or not snapshot.goal_id:
        return None
    if any(marker in user_message for marker in _CONTROL_MARKERS):
        return None
    nodes = {node.node_id: node for node in route.nodes}
    current = nodes.get(route.current_node_id)
    if current is None:
        return None
    prerequisite_ids = _prerequisite_chain(current.node_id, nodes)
    for entry in snapshot.entries:
        if StudyAdaptationAdjustment.PREREQUISITE_REVIEW not in entry.adjustments:
            continue
        for node_id in prerequisite_ids:
            node = nodes[node_id]
            if not _title_matches(node.title, entry.title):
                continue
            return RouteRevisionPlan(
                goal_id=snapshot.goal_id,
                target_node_id=node_id,
                expected_route_version=route.version,
                reason=_short(
                    f"已核验学情显示「{entry.title}」存在具体误解，先补讲该前置知识点"
                    f"（记录版本 {entry.record_version}）。",
                    200,
                ),
            )
    return None


def _prerequisite_chain(node_id: str, nodes: dict[str, Any]) -> list[str]:
    """当前节点的传递前置节点（按先讲顺序；不含当前节点本身）。"""

    chain: list[str] = []
    pending = list(nodes[node_id].prerequisites)
    while pending:
        ref = pending.pop(0)
        if ref in chain or ref not in nodes:
            continue
        chain.append(ref)
        pending.extend(nodes[ref].prerequisites)
    return list(reversed(chain))


def _title_matches(node_title: str, record_title: str) -> bool:
    node = _normalize(node_title)
    record = _normalize(record_title)
    if not node or not record:
        return False
    if node in record or record in node:
        return True
    return len(_terms(node_title) & _terms(record_title)) >= 2


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _entries_json(snapshot: StudyAdaptationSnapshot) -> str:
    return json.dumps(
        [entry.model_dump(mode="json") for entry in snapshot.entries],
        ensure_ascii=False,
    )


def parse_entries(value: str) -> list[StudyAdoptedRecord]:
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    entries: list[StudyAdoptedRecord] = []
    for item in parsed:
        try:
            entries.append(StudyAdoptedRecord.model_validate(item))
        except ValidationError:
            continue
    return entries


def _terms(text: str) -> set[str]:
    words = set(re.findall(r"[a-z0-9_]+", text.lower()))
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        words.update(run[index : index + 2] for index in range(len(run) - 1))
    return words


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(normalized.split())


def _short(value: str, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed
