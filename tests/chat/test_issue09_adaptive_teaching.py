"""Issue 09：按学情调整后续教法与路线（真实服务链路）。

覆盖验收项：
1. 正式采用只含相关、已核验且有效的学情，不固定最近五条或整份注入；
2. 相同目标和输入在不同已核验表现下产生可观察的教法差异（实际模型载荷）；
3. 提示后完成与独立完成的调整合理，单次完成不晋升长期掌握；
4. 用户跳过/直接讲解优先，补讲建议不强制锁住路线；
5. 编译后、调用前的纠正使旧采用失效，后续调用不携带旧判断；
6. 未完成诊断与失效来源不入正式采用，无档案仍能继续学习；
7. 真实矛盾保留不确定性，同名不同知识上下文不串用；
8. 路线修订有版本与原因，普通教学不额外每轮完整规划；
9. 采用记录可见、审计只含必要引用，重试沿用同一快照。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient

from bridges.ai.adapters import AdapterResult
from bridges.contracts.observability import AuditAction, AuditResult
from bridges.contracts.study_diagnosis import (
    StudyDiagnosisVerdict,
    StudyRecordBoundaryEntry,
    StudyRecordState,
)
from bridges.study.adaptation import select_relevant_records
from bridges.study.goals import StudyGoalRepository
from bridges.study.record_controls import StudyRecordControlService
from tests.chat.test_issue02_text_tutor import _TextTutorAdapter
from tests.chat.test_issue03_embedded_verification import (
    _plan,
    _plan_evaluate,
    _plan_pose,
    _PlanAdapter,
)
from tests.chat.test_issue05_durable_diagnosis import (
    _ANSWER,
    _Q1,
    _close,
    _detail,
    _diagnosis_output,
    _send,
    _start_study,
    _StructuredAdapter,
    _wire,
)
from tests.chat.test_v2_05_photo_attachments import _app, _register

_NOW = datetime(2026, 10, 14, tzinfo=UTC)
_FOLLOW_UP = "然后我们看一道条件概率的练习吧"
_GOAL_TEXT = "我想系统学概率论，先打牢事件关系的基础"


class _GoalStructuredAdapter(_StructuredAdapter):
    """05 替身 + 可脚本化目标规划：只在前几轮消费脚本，之后按局部问题处理。"""

    def __init__(
        self,
        planner: _PlanAdapter | None = None,
        goal_responses: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(planner)
        self.goal_responses = list(goal_responses or [])
        self.goal_payloads: list[dict[str, Any]] = []

    def call(
        self, capability: Any, run_context: Any, payload: dict[str, Any]
    ) -> AdapterResult:
        if payload.get("task") == "study.goal_planning":
            self.goal_payloads.append(payload)
            if self.goal_responses:
                return AdapterResult(
                    actual_model_id=capability.model_id,
                    output=self.goal_responses.pop(0),
                )
        return super().call(capability, run_context, payload)


def _goal_output() -> dict[str, Any]:
    return {
        "kind": "systematic_goal",
        "observable_result": "能独立完成条件概率练习",
        "scope": "概率论基础",
        "constraints": "",
        "clarification_question": "",
        "route": {
            "nodes": [
                {
                    "node_id": "n1",
                    "title": "互斥与独立",
                    "prerequisites": [],
                    "depth": "基础",
                },
                {
                    "node_id": "n2",
                    "title": "条件概率",
                    "prerequisites": ["n1"],
                    "depth": "基础",
                },
            ],
            "current_node_id": "n2",
            "revision_reason": "先巩固事件关系再进入条件概率",
        },
        "note": "",
    }


def _seed_verified_record(
    app: Any,
    client: TestClient,
    generation_helpers: Any,
    text: _TextTutorAdapter,
    plan: _PlanAdapter,
    structured: _GoalStructuredAdapter,
    *,
    tag: str,
    verdict: StudyDiagnosisVerdict = StudyDiagnosisVerdict.INDEPENDENT_COMPLETION,
    hinted: bool = False,
    first_turn: str = "互斥是不是独立？",
    goal_responses: list[dict[str, Any]] | None = None,
    pose_question: str = _Q1,
    pose_context: str = "互斥与独立",
) -> tuple[str, str]:
    """走真实链路形成一条已核验档案，返回（对话标识, 记录标识）。

    作答证据来自真实终态提交；诊断结论由确定性诊断替身给出（可指定
    独立完成/提示后完成/具体误解）。``hinted=True`` 时先经一次真实
    提示，再作答，使证据的独立程度为「提示后作答」。
    """

    if goal_responses is not None:
        structured.goal_responses = list(goal_responses)
    plan.default = _plan_pose(pose_question, context=pose_context)
    text.answer = f"先检查一个关键点。{pose_question}"
    conversation_id = _start_study(client, first_turn, f"{tag}-1")
    generation_helpers["drive"](app)
    if hinted:
        plan.default = _plan(
            action="help",
            intent="hint_request",
            feedback_direction="给出一个小提示，不判对错。",
        )
        text.answer = "提示：先想互斥意味着两个事件不能同时发生。"
        _send(client, conversation_id, "先给我一点提示", f"{tag}-2h")
        generation_helpers["drive"](app)
    plan.default = _plan_evaluate()
    text.answer = "互斥时交集为空，所以 P(A∩B)=0。"
    structured.diagnosis_default = _diagnosis_output(
        verdict=verdict.value,
        knowledge_title="互斥与独立",
        subject="概率论",
        usage_context="理解事件关系",
        performance="能说明互斥不等于独立。",
    )
    _send(client, conversation_id, _ANSWER, f"{tag}-2")
    generation_helpers["drive"](app)
    database = app.state.bridges_database
    row = database.connection.execute(
        "SELECT record_id FROM study_learning_records"
    ).fetchone()
    assert row is not None, "诊断未形成学习档案"
    return conversation_id, str(row["record_id"])


def _planner_request(adapter: _PlanAdapter) -> dict[str, Any]:
    payload = adapter.payloads[-1]
    return json.loads(str(payload["messages"][1]["content"]))


def _tutor_system_prompt(adapter: _TextTutorAdapter) -> str:
    return str(adapter.payloads[-1]["messages"][0]["content"])


def _adoption_count(database: Any) -> int:
    return int(
        database.connection.execute(
            "SELECT COUNT(*) AS count FROM study_record_adoptions"
        ).fetchone()["count"]
    )


def _boundary(
    record_id: str,
    *,
    title: str,
    usage_context: str = "",
    subject: str = "概率论",
    verdict: StudyDiagnosisVerdict = StudyDiagnosisVerdict.INDEPENDENT_COMPLETION,
    pending_confirmation: bool = False,
    effective: bool = True,
    verified: bool = True,
) -> StudyRecordBoundaryEntry:
    return StudyRecordBoundaryEntry(
        record_id=record_id,
        record_version=1,
        subject=subject,
        title=title,
        usage_context=usage_context,
        verdict=verdict,
        performance="能说明互斥不等于独立。",
        state=(
            StudyRecordState.ACTIVE
            if effective
            else StudyRecordState.DELETED
        ),
        effective=effective,
        verified=verified,
        pending_confirmation=pending_confirmation,
        updated_at=_NOW,
    )


# ---------------------------------------------------------------------------
# 完整反馈旅程：作答—正式诊断—后续教学改变（实际模型载荷）
# ---------------------------------------------------------------------------


def test_verified_record_changes_actual_teaching_and_stays_visible(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _TextTutorAdapter("先检查一个关键点。")
    plan = _PlanAdapter()
    structured = _GoalStructuredAdapter(plan)
    _wire(app, text, structured)

    try:
        with TestClient(app) as client:
            account = _register(client, "issue09-journey")
            conversation_id, record_id = _seed_verified_record(
                app, client, generation_helpers, text, plan, structured,
                tag="issue09-journey",
            )
            database = app.state.bridges_database
            assert _adoption_count(database) == 0

            # 后续教学轮：学情进入规划请求、导师提示词与用户可见依据。
            plan.default = _plan()
            text.answer = "我们换一道迁移题，检查条件概率的应用。"
            assistant_message_id = _send(
                client, conversation_id, _FOLLOW_UP, "issue09-journey-3"
            )
            generation_helpers["drive"](app)

            detail = _detail(client, conversation_id)
            assert detail["messages"][-1]["status"] == "done"

            request = _planner_request(plan)
            adopted = request["learning_adaptation"]["adopted"]
            assert len(adopted) == 1
            assert adopted[0]["title"] == "互斥与独立"
            assert adopted[0]["verdict"] == "independent_completion"
            # 单次完成不晋升长期掌握：迁移应用 + 因不确定性安排复核。
            assert adopted[0]["adjustments"] == ["transfer_application", "recheck"]

            prompt = _tutor_system_prompt(text)
            assert "【学情采用" in prompt
            assert "互斥与独立" in prompt
            assert "改用迁移应用" in prompt

            # 用户可见依据：只展示真实生效的采用记录与调整方向。
            adoptions = detail["study_record_adoptions"]
            assert len(adoptions) == 1
            assert adoptions[0]["assistant_message_id"] == assistant_message_id
            entry = adoptions[0]["entries"][0]
            assert entry["record_id"] == record_id
            assert entry["adjustments"] == ["transfer_application", "recheck"]
            assert "不要当作已稳定掌握" not in json.dumps(adoptions, ensure_ascii=False)
            row = database.connection.execute(
                "SELECT status, entries_json, goal_id, route_version"
                " FROM study_record_adoptions"
            ).fetchone()
            assert row["status"] == "applied"
            assert record_id in str(row["entries_json"])
            assert row["goal_id"] == ""
            assert int(row["route_version"]) == 0

            # 重试沿用同一采用快照（运行配置冻结），并逐条复验。
            run = app.state.chat_service._repo.get_run_by_message(
                account["id"], assistant_message_id
            )
            assert run is not None
            frozen = (run.config or {})["study_adaptation"]
            assert frozen["adoption_id"] == adoptions[0]["adoption_id"]
            assert frozen["entries"][0]["record_id"] == record_id

            # 审计只含必要引用/状态，不含学情正文或提示词。
            events = app.state.observability_service.list_audit_events(
                account_id=account["id"],
                action=AuditAction.STUDY_ADAPTATION_APPLIED,
            )
            assert len(events) == 1
            assert events[0].result == AuditResult.SUCCESS
            assert events[0].details["status"] == "compiled"
            assert events[0].details["adopted"][0]["record_id"] == record_id
            audit_text = json.dumps(events[0].details, ensure_ascii=False)
            assert "能说明互斥不等于独立" not in audit_text
            assert "【学情采用" not in audit_text
    finally:
        _close(app)


def test_hinted_and_independent_completion_produce_different_teaching(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _TextTutorAdapter("先检查一个关键点。")
    plan = _PlanAdapter()
    structured = _GoalStructuredAdapter(plan)
    _wire(app, text, structured)

    try:
        with TestClient(app) as independent_client:
            independent = _register(independent_client, "issue09-independent")
            independent_conversation, _ = _seed_verified_record(
                app, independent_client, generation_helpers, text, plan, structured,
                tag="issue09-independent",
            )
            with TestClient(app) as hinted_client:
                hinted = _register(hinted_client, "issue09-hinted")
                hinted_conversation, _ = _seed_verified_record(
                    app, hinted_client, generation_helpers, text, plan, structured,
                    tag="issue09-hinted",
                    verdict=StudyDiagnosisVerdict.HINTED_COMPLETION,
                    hinted=True,
                )

                # 相同目标与输入、不同已核验表现：实际模型载荷出现可观察差异。
                plan.default = _plan()
                text.answer = "我们继续看这道条件概率练习。"
                _send(
                    independent_client,
                    independent_conversation,
                    _FOLLOW_UP,
                    "issue09-independent-3",
                )
                generation_helpers["drive"](app)
                independent_request = _planner_request(plan)
                independent_prompt = _tutor_system_prompt(text)

                text.answer = "我们继续看这道条件概率练习。"
                _send(
                    hinted_client,
                    hinted_conversation,
                    _FOLLOW_UP,
                    "issue09-hinted-3",
                )
                generation_helpers["drive"](app)
                hinted_request = _planner_request(plan)
                hinted_prompt = _tutor_system_prompt(text)

                independent_adjustments = independent_request["learning_adaptation"][
                    "adopted"
                ][0]["adjustments"]
                hinted_adjustments = hinted_request["learning_adaptation"]["adopted"][
                    0
                ]["adjustments"]
                assert independent_adjustments == ["transfer_application", "recheck"]
                assert hinted_adjustments == ["independent_application"]
                assert "改用迁移应用" in independent_prompt
                assert "安排一次独立应用" in hinted_prompt
                assert "不要当作已稳定掌握" in hinted_prompt

                # 单次完成不被提升为长期掌握：提示后完成不宣称稳定掌握。
                hinted_adoption = _detail(hinted_client, hinted_conversation)[
                    "study_record_adoptions"
                ][0]
                assert hinted_adoption["entries"][0]["verdict"] == "hinted_completion"
                assert "不要当作已稳定掌握" in hinted_adoption["entries"][0]["reason"]

                # 账户隔离：采用记录与用户可见依据都按账户作用域。
                independent_adoptions = _detail(
                    independent_client, independent_conversation
                )["study_record_adoptions"]
                hinted_adoptions = _detail(
                    hinted_client, hinted_conversation
                )["study_record_adoptions"]
                assert {
                    item["adoption_id"] for item in independent_adoptions
                }.isdisjoint({item["adoption_id"] for item in hinted_adoptions})
                database = app.state.bridges_database
                assert _adoption_count(database) == 2
                assert (
                    int(
                        database.connection.execute(
                            "SELECT COUNT(*) AS count FROM study_record_adoptions"
                            " WHERE account_id = ?",
                            (independent["id"],),
                        ).fetchone()["count"]
                    )
                    == 1
                )
                assert (
                    int(
                        database.connection.execute(
                            "SELECT COUNT(*) AS count FROM study_record_adoptions"
                            " WHERE account_id = ?",
                            (hinted["id"],),
                        ).fetchone()["count"]
                    )
                    == 1
                )
    finally:
        _close(app)


# ---------------------------------------------------------------------------
# 调用前复验：编译后、调用前的纠正使旧采用失效
# ---------------------------------------------------------------------------


class _CorrectingPlanAdapter(_PlanAdapter):
    """验证规划调用期间执行一次注入操作（模拟并发纠正/删除）。"""

    def __init__(self, hook: Any) -> None:
        super().__init__()
        self._hook = hook

    def call(
        self, capability: Any, run_context: Any, payload: dict[str, Any]
    ) -> AdapterResult:
        self._hook()
        return super().call(capability, run_context, payload)


def test_correction_between_compile_and_send_withdraws_old_adoption(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _TextTutorAdapter("先检查一个关键点。")
    plan = _PlanAdapter()
    structured = _GoalStructuredAdapter(plan)
    _wire(app, text, structured)

    try:
        with TestClient(app) as client:
            account = _register(client, "issue09-correction")
            conversation_id, record_id = _seed_verified_record(
                app, client, generation_helpers, text, plan, structured,
                tag="issue09-correction",
            )
            database = app.state.bridges_database
            controls = StudyRecordControlService(database)
            first = _plan()
            corrected: list[bool] = []

            def hook() -> None:
                if corrected:
                    return
                corrected.append(True)
                row = database.connection.execute(
                    "SELECT record_version FROM study_learning_records"
                    " WHERE record_id = ?",
                    (record_id,),
                ).fetchone()
                controls.correct_record(
                    account["id"],
                    conversation_id,
                    record_id,
                    "我当时理解的是另一个方向。",
                    int(row["record_version"]),
                )

            correcting = _CorrectingPlanAdapter(hook)
            structured.planner = correcting
            plan = correcting
            plan.payloads.clear()

            text.answer = "我们看下一题。"
            _send(client, conversation_id, _FOLLOW_UP, "issue09-correction-3")
            generation_helpers["drive"](app)

            # 规划请求构建于纠正之前，确曾携带旧采用；主生成前复验撤回：
            # 导师载荷不再包含学情区块，也没有写入采用记录。
            request = _planner_request(correcting)
            assert request["learning_adaptation"]["adopted"][0]["title"] == "互斥与独立"
            prompt = _tutor_system_prompt(text)
            assert "【学情采用" not in prompt
            assert "互斥与独立" not in prompt
            assert _adoption_count(database) == 0
            detail = _detail(client, conversation_id)
            assert detail["messages"][-1]["status"] == "done"
            assert detail["study_record_adoptions"] == []

            events = app.state.observability_service.list_audit_events(
                account_id=account["id"],
                action=AuditAction.STUDY_ADAPTATION_APPLIED,
            )
            blocked = [event for event in events if event.result == AuditResult.BLOCKED]
            assert len(blocked) == 1
            assert blocked[0].details["invalid_reason"] == "record_changed"

            # 后续请求不再携带旧判断（纠正后的有效边界不可正式采用）。
            plan.payloads.clear()
            text.answer = "继续这道题的关键步骤。"
            _send(client, conversation_id, "接着讲这道题", "issue09-correction-4")
            generation_helpers["drive"](app)
            assert _planner_request(plan)["learning_adaptation"] == {}
            assert "【学情采用" not in _tutor_system_prompt(text)
    finally:
        _close(app)


# ---------------------------------------------------------------------------
# 无档案、待验证与同名不同上下文
# ---------------------------------------------------------------------------


def test_without_formal_records_teaching_continues_without_adaptation(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _TextTutorAdapter("我们先从事件关系开始。")
    plan = _PlanAdapter()
    structured = _GoalStructuredAdapter(plan)
    _wire(app, text, structured)

    try:
        with TestClient(app) as client:
            _register(client, "issue09-empty")
            conversation_id = _start_study(client, "互斥是不是独立？", "issue09-empty-1")
            generation_helpers["drive"](app)
            plan.payloads.clear()

            text.answer = "互斥表示不能同时发生；独立表示概率不受影响。"
            _send(client, conversation_id, "再讲一遍它们的区别", "issue09-empty-2")
            generation_helpers["drive"](app)

            assert _planner_request(plan)["learning_adaptation"] == {}
            prompt = _tutor_system_prompt(text)
            assert "【学情采用" not in prompt
            detail = _detail(client, conversation_id)
            assert detail["messages"][-1]["status"] == "done"
            assert detail["study_record_adoptions"] == []
            assert _adoption_count(app.state.bridges_database) == 0
    finally:
        _close(app)


def test_relevance_selection_excludes_pending_and_cross_context() -> None:
    # 同名不同上下文：只有与当前上下文二次匹配的那条可被采用。
    same_title_other = _boundary(
        "lr-a", title="互斥与独立", usage_context="判断题对比"
    )
    same_title_match = _boundary(
        "lr-b", title="互斥与独立", usage_context="计算题应用"
    )
    chosen, note = select_relevant_records(
        [same_title_other, same_title_match],
        knowledge_context="我们做条件概率的计算题应用",
        current_focus="互斥与独立",
        user_message="这道题怎么算",
        goal=None,
        route=None,
    )
    assert [entry.record_id for entry in chosen] == ["lr-b"]
    assert note == ""

    # 方向冲突、待进一步验证的记录不当作正式事实，只保留不确定性说明。
    conflict = _boundary(
        "lr-c",
        title="互斥与独立",
        pending_confirmation=True,
    )
    chosen, note = select_relevant_records(
        [conflict],
        knowledge_context="互斥与独立",
        current_focus="互斥",
        user_message="继续",
        goal=None,
        route=None,
    )
    assert chosen == []
    assert "互斥与独立" in note
    assert "待进一步验证" in note

    # 已删除与来源失效的记录不进入正式采用（也不产生不确定性噪声）。
    deleted = _boundary("lr-d", title="互斥与独立", effective=False)
    unavailable = _boundary("lr-e", title="条件概率", verified=False)
    chosen, note = select_relevant_records(
        [deleted, unavailable],
        knowledge_context="互斥与独立",
        current_focus="条件概率",
        user_message="讲一下",
        goal=None,
        route=None,
    )
    assert chosen == []
    assert note == ""


# ---------------------------------------------------------------------------
# 路线修订：版本与原因、用户控制优先、守卫不虚报
# ---------------------------------------------------------------------------


def test_route_revision_has_version_reason_and_respects_user_control(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _TextTutorAdapter("先检查一个关键点。")
    plan = _PlanAdapter()
    structured = _GoalStructuredAdapter(plan)
    _wire(app, text, structured)

    try:
        with TestClient(app) as client:
            account = _register(client, "issue09-route")
            conversation_id, record_id = _seed_verified_record(
                app, client, generation_helpers, text, plan, structured,
                tag="issue09-route",
                verdict=StudyDiagnosisVerdict.MISCONCEPTION,
                first_turn=_GOAL_TEXT,
                goal_responses=[_goal_output()],
            )
            database = app.state.bridges_database
            goals = StudyGoalRepository(database)
            goal = goals.get_linked_goal(account["id"], conversation_id)
            assert goal is not None and goal.route is not None
            assert goal.route.version == 1
            goal_id = goal.goal_id

            # 普通教学轮：已核验误解命中当前节点的前置链，形成带版本与
            # 原因的路线修订；目标本身不被修改。
            plan.default = _plan()
            text.answer = "我们先补一下互斥与独立的前置点。"
            _send(client, conversation_id, _FOLLOW_UP, "issue09-route-3")
            generation_helpers["drive"](app)

            revisions = goals.list_route_revisions(account["id"], goal_id)
            assert [revision.version for revision in revisions] == [1, 2]
            latest = revisions[-1]
            assert latest.current_node_id == "n1"
            assert latest.origin == "assistant_proposal"
            assert record_id in latest.revision_reason or "互斥" in latest.revision_reason
            assert "先补讲该前置知识点" in latest.revision_reason
            assert goals.get_goal(account["id"], goal_id).version == 1  # type: ignore[union-attr]
            request = _planner_request(plan)
            assert request["learning_adaptation"]["adopted"][0]["adjustments"] == [
                "prerequisite_review",
                "hint_granularity",
            ]
            assert "用户明确要求直接讲解、跳过、继续或改变方向时一律优先" in (
                _tutor_system_prompt(text)
            )

            # 用户控制优先：明确跳过/直接讲解时不再自动回退路线。
            text.answer = "好，直接讲条件概率。"
            _send(client, conversation_id, "跳过补讲，直接讲条件概率", "issue09-route-4")
            generation_helpers["drive"](app)
            revisions = goals.list_route_revisions(account["id"], goal_id)
            assert [revision.version for revision in revisions] == [1, 2]
            assert goals.current_route(account["id"], goal_id).version == 2  # type: ignore[union-attr]

            # 普通教学不额外调用整套目标规划：全程只有首轮一次。
            assert len(structured.goal_payloads) == 1

            # 路线保存冲突：过期版本守卫拒绝写入，不覆盖新状态也不虚报。
            current = goals.current_route(account["id"], goal_id)
            assert current is not None
            stale = current.model_copy(update={"version": current.version + 1})
            saved = goals.save_route_revision_in_transaction(
                account_id=account["id"],
                goal_id=goal_id,
                route=stale,
                expected_version=0,
            )
            assert saved is False
            assert goals.current_route(account["id"], goal_id).version == 2  # type: ignore[union-attr]
    finally:
        _close(app)
