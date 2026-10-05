"""工单 36：按本次实际表现总结；未答/未判单列，总结失败独立恢复。"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

import bridges.chat  # noqa: F401 - 先初始化聊天组合根，避免学习/聊天既有循环导入
from bridges.contracts.study import (
    StudyFragment,
    StudyPage,
    StudyReview,
    StudyReviewQuestion,
    StudyScope,
    StudyState,
)
from bridges.lifecycle.catalog import export_rows
from bridges.study.summary import build_summary
from tests.chat.test_v2_05_photo_attachments import PNG_BYTES, _app, _upload_draft
from tests.chat.test_v2_18_study_tutoring import _ask, _retry, _start
from tests.chat.test_v2_20_study_summary import SummaryGateway, _finish_review, _section


class Ticket36Gateway(SummaryGateway):
    """工单 36 替身：记录总结系统提示，其余沿用总结/复盘替身。"""

    def __init__(self) -> None:
        super().__init__()
        self.summary_system_prompts: list[str] = []

    def summarize(self, payload: dict[str, Any]) -> Any:
        self.summary_system_prompts.append(payload["messages"][0]["content"])
        return super().summarize(payload)


class _StubCompiler:
    def __init__(self) -> None:
        self.system_prompts: list[str] = []

    def compile_turn_context(
        self, run: Any, *, system_prompt: str, evidence: Any
    ) -> tuple[list[dict[str, str]], dict[str, Any]]:
        self.system_prompts.append(system_prompt)
        adopted = [item.evidence_id for item in evidence]
        return (
            [{"role": "system", "content": system_prompt}],
            {"budget_floor_exceeded": False, "adopted_evidence_ids": adopted},
        )


def _point(
    kind: str,
    text: str,
    *,
    question_ids: list[str] | None = None,
    fragment_ids: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "text": text,
        "question_ids": question_ids or [],
        "fragment_ids": fragment_ids or [],
    }


def _invoke(points: list[dict[str, Any]]) -> Any:
    def call(task: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"points": points}

    return call


def _unit_state(*, unanswered: bool = False, weak: bool = False) -> StudyState:
    questions = [
        StudyReviewQuestion(
            question_id="q1",
            question="第一题？",
            coverage_units=["u1"],
            fragment_ids=["obj-1:1"],
            asked=True,
            answer="学生答案",
            judgement="correct",
            canonical_answer="标准答案",
            explanation="解释",
        )
    ]
    if unanswered:
        questions.append(
            StudyReviewQuestion(
                question_id="q2",
                question="第二题？",
                coverage_units=["u1"],
                fragment_ids=["obj-1:1"],
                asked=True,
                unanswered=True,
            )
        )
    if weak:
        questions.append(
            StudyReviewQuestion(
                question_id="q2",
                question="第二题？",
                coverage_units=["u1"],
                fragment_ids=["obj-1:1"],
                asked=True,
                answer="错误答案",
                judgement="incorrect",
                canonical_answer="标准答案",
                explanation="解释",
            )
        )
    return StudyState(
        subsection_id="section-unit",
        stage="review",
        pages=[
            StudyPage(
                object_id="obj-1",
                ordinal=1,
                content_hash="hash-1",
                model_id="test-model",
                fragments=[
                    StudyFragment(
                        fragment_id="obj-1:1",
                        kind="text",
                        position="正文",
                        text="y=ax+b",
                        confidence=0.9,
                    )
                ],
            )
        ],
        scope=StudyScope(
            scope_version_id="scope-unit",
            material_hash="material-unit",
            page_object_ids=["obj-1"],
            fragment_ids=["obj-1:1"],
            verified=True,
        ),
        review=StudyReview(questions=questions),
    )


def _valid_points() -> list[dict[str, Any]]:
    return [
        _point("learned", "本节讲线性函数。", fragment_ids=["obj-1:1"]),
        _point("mastered", "能读懂斜率。", question_ids=["q1"]),
    ]


def test_summary_separates_mastery_gaps_and_unanswered_with_real_evidence(
    tmp_path: Any, monkeypatch: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = Ticket36Gateway()
    gateway.question_count = 3
    gateway.judgements = ["correct", "incorrect"]
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        endpoint = _start(client, app)
        # 第1题答对；暂停时第2题已呈现未答；继续后第3题答错并完成复盘。
        _ask(client, app, endpoint, "学完了")
        _ask(client, app, endpoint, "a 是斜率")
        paused = _ask(client, app, endpoint, "暂停复盘")
        assert paused["study"]["review"]["questions"][1]["unanswered"] is True
        resumed = _ask(client, app, endpoint, "继续复盘")
        assert "复盘第3题" in resumed["messages"][-1]["content"]
        finished = _ask(client, app, endpoint, "不知道")
        assert finished["messages"][-1]["status"] == "done", finished["messages"][-1]
        study = finished["study"]
        assert study["stage"] == "summary"
        questions = study["review"]["questions"]
        assert [item["judgement"] for item in questions] == ["correct", None, "incorrect"]
        summary = study["summary"]
        by_kind: dict[str, list[str]] = {kind: [] for kind in (
            "learned", "mastered", "gap", "unanswered",
        )}
        for point in summary["points"]:
            by_kind[point["kind"]].extend(point["question_ids"])
        assert by_kind["mastered"] == [questions[0]["question_id"]]
        assert by_kind["gap"] == [questions[2]["question_id"]]
        assert by_kind["unanswered"] == [questions[1]["question_id"]]
        # 结论绑定生成时的有效范围版本，作答的逐项评分记录进入生成材料。
        assert summary["scope_version_id"] == study["scope"]["scope_version_id"]
        material = gateway.summary_calls[-1]
        assert material["scope_version_id"] == study["scope"]["scope_version_id"]
        assert all(
            item["scope_version_id"] == study["scope"]["scope_version_id"]
            for item in material["questions"]
        )
        assert material["questions"][0]["checks"]
        assert material["questions"][0]["recheck_status"] in {"not_needed", "confirmed"}
        assert material["questions"][1]["judgement"] is None
        assert material["questions"][1]["unanswered"] is True
        content = finished["messages"][-1]["content"]
        mastered = _section(content, "复盘已掌握")
        gaps = _section(content, "还需补的点")
        pending = _section(content, "未作答或尚未判定")
        assert "第1题" in mastered and "判定为正确" in mastered
        assert "第3题" in gaps and "判定为错误" in gaps
        assert "第2题" in pending and "未作答（已呈现，未计入掌握）" in pending
        assert "第2题" not in mastered and "第2题" not in gaps
        assert "%" not in content and "％" not in content and "掌握度" not in content


def test_summary_failure_persists_pending_and_retries_only_summary(
    tmp_path: Any, monkeypatch: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = Ticket36Gateway()
    gateway.question_count = 1
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        endpoint = _start(client, app)
        _ask(client, app, endpoint, "学完了")
        gateway.fail_summary = "timeout"
        failed = _ask(client, app, endpoint, "a 是斜率")
        assert failed["messages"][-1]["status"] == "error"
        assert failed["messages"][-1]["error_code"] == "timeout"
        # 待总结状态持久：判定/反馈已提交，总结为空，复盘已完成。
        pending = failed["study"]
        assert pending["stage"] == "review"
        assert pending["review"]["complete"] is True
        assert pending["summary"] is None
        question = pending["review"]["questions"][0]
        assert question["judgement"] == "correct"
        assert question["answer"] == "a 是斜率"
        assert question["feedback"]
        assert [task for task, _ in gateway.review_calls].count("study.grade") == 1
        assert len(gateway.summary_calls) == 1
        # 只恢复总结：重试不重判最后一题、不重复反馈，也不重复作答。
        gateway.fail_summary = None
        result = _retry(client, app, endpoint)
        assert result["messages"][-1]["status"] == "done", result["messages"][-1]
        assert [task for task, _ in gateway.review_calls].count("study.grade") == 1
        assert len(gateway.summary_calls) == 2
        content = result["messages"][-1]["content"]
        assert content.count("回答正确") == 1
        assert "本节学习总结" in content
        graded = result["study"]["review"]["questions"][0]
        assert graded["judgement"] == "correct" and graded["answer"] == "a 是斜率"
        # 重放与导出一致：重复读取不重生成，导出含同一总结与版本。
        repeated = client.get(endpoint).json()
        assert repeated["study"]["summary"] == result["study"]["summary"]
        assert len(gateway.summary_calls) == 2
        account_id = client.get("/auth/session").json()["account"]["id"]
        rows = export_rows(app.state.bridges_database, account_id, "study_states")
        exported = json.loads(rows[0]["state_json"])
        assert exported["summary"] == result["study"]["summary"]


def test_appended_pages_renew_summary_scope_version_and_keep_history_readable(
    tmp_path: Any, monkeypatch: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = Ticket36Gateway()
    gateway.question_count = 1
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        endpoint = _start(client, app)
        before = _finish_review(client, app, endpoint)["study"]
        old_scope = before["scope"]["scope_version_id"]
        assert before["summary"]["scope_version_id"] == old_scope
        gateway.page_numbers = [12, 13]
        photo = _upload_draft(
            client, upload_id="issue36-append", content=PNG_BYTES + b"-2"
        ).json()
        appended = _ask(client, app, endpoint, "漏拍了一页", attachment_ids=[photo["object_id"]])
        assert appended["messages"][-1]["status"] == "done", appended["messages"][-1]
        study = appended["study"]
        assert study["summary"] is None
        history = study["summary_history"]
        assert len(history) == 1
        assert history[0]["scope_version_id"] == old_scope
        assert history[0]["summary"]["scope_version_id"] == old_scope
        assert history[0]["summary"] == before["summary"]
        new_scope = study["scope"]["scope_version_id"]
        assert new_scope != old_scope
        resumed = _ask(client, app, endpoint, "继续复盘")
        assert resumed["messages"][-1]["status"] == "done", resumed["messages"][-1]
        assert resumed["study"]["summary"] is None
        finished = _finish_review(client, app, endpoint)["study"]
        assert finished["summary"]["scope_version_id"] == new_scope
        assert len(finished["summary_history"]) == 1
        account_id = client.get("/auth/session").json()["account"]["id"]
        rows = export_rows(app.state.bridges_database, account_id, "study_states")
        exported = json.loads(rows[0]["state_json"])
        assert exported["summary"]["scope_version_id"] == new_scope
        assert exported["summary_history"][0]["scope_version_id"] == old_scope
        assert exported["summary_history"][0]["summary"]["scope_version_id"] == old_scope


def test_summary_creates_no_followup_work_and_no_profile_fact_from_summary(
    tmp_path: Any, monkeypatch: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = Ticket36Gateway()
    gateway.question_count = 1
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        endpoint = _start(client, app)
        before = _finish_review(client, app, endpoint)
        study = before["study"]
        assert study["stage"] == "summary"
        questions = study["review"]["questions"]
        assert len(questions) == 1
        # 主动追问才回辅导：不自动追加题目、补救课程或下一节任务。
        follow = _ask(client, app, endpoint, "再讲讲斜率")
        assert follow["study"]["stage"] == "tutoring"
        assert follow["study"]["summary"] == study["summary"]
        assert follow["study"]["review"]["questions"] == questions
        assert len(client.get("/chat/conversations").json()["conversations"]) == 1
        # 总结是助手产物，不进入用户原话提取：没有以总结消息为来源的画像任务。
        account_id = client.get("/auth/session").json()["account"]["id"]
        rows = app.state.bridges_database.scoped(account_id).execute(
            "SELECT message_id FROM profile_extraction_tasks WHERE account_id = ?",
            (account_id,),
        ).fetchall()
        task_ids = {row["message_id"] for row in rows}
        summary_message_id = before["messages"][-1]["message_id"]
        assert summary_message_id not in task_ids
        assistant_ids = {
            message["message_id"]
            for message in follow["messages"]
            if message["role"] == "assistant"
        }
        assert not (task_ids & assistant_ids)


def test_tutoring_without_review_never_generates_summary(
    tmp_path: Any, monkeypatch: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = Ticket36Gateway()
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        endpoint = _start(client, app)
        result = _ask(client, app, endpoint, "讲讲斜率")
        assert result["study"]["stage"] == "tutoring"
        assert result["study"]["review"] is None
        assert result["study"]["summary"] is None
        result = _ask(client, app, endpoint, "帮我总结一下本节")
        assert result["study"]["stage"] == "tutoring"
        assert result["study"]["review"] is None
        assert result["study"]["summary"] is None
        assert gateway.summary_calls == []


def test_summary_gate_rejects_percentage_and_global_labels() -> None:
    state = _unit_state()
    with pytest.raises(ValueError, match="百分比"):
        build_summary(
            _StubCompiler(), SimpleNamespace(config={}), state,
            invoke=_invoke([
                *_valid_points()[:-1],
                _point("mastered", "本节掌握度 80%。", question_ids=["q1"]),
            ]),
        )
    with pytest.raises(ValueError, match="能力标签"):
        build_summary(
            _StubCompiler(), SimpleNamespace(config={}), state,
            invoke=_invoke([
                *_valid_points()[:-1],
                _point("mastered", "整体能力较强。", question_ids=["q1"]),
            ]),
        )


def test_unanswered_must_be_listed_separately() -> None:
    state = _unit_state(unanswered=True)
    # 有未判题却完全没写：总结必须单独列出实际状态。
    with pytest.raises(ValueError, match="单独列出"):
        build_summary(
            _StubCompiler(), SimpleNamespace(config={}), state,
            invoke=_invoke(_valid_points()),
        )
    # 未判题写进“待补理解点”同样被拒：漏洞必须有判定依据。
    with pytest.raises(ValueError, match="待补理解点必须关联"):
        build_summary(
            _StubCompiler(), SimpleNamespace(config={}), state,
            invoke=_invoke([
                *_valid_points(),
                _point("gap", "未作答，需补。", question_ids=["q2"]),
            ]),
        )
    with pytest.raises(ValueError, match="必须对应未判定的题"):
        build_summary(
            _StubCompiler(), SimpleNamespace(config={}), state,
            invoke=_invoke([
                *_valid_points(),
                _point("unanswered", "未判定。", question_ids=["q1"]),
            ]),
        )
    summary = build_summary(
        _StubCompiler(), SimpleNamespace(config={}), state,
        invoke=_invoke([
            *_valid_points(),
            _point("unanswered", "未作答，不计掌握或错答。", question_ids=["q2"]),
        ]),
    )
    assert summary.scope_version_id == "scope-unit"


def test_gap_must_bind_weak_or_changed_questions() -> None:
    state = _unit_state(weak=True)
    # 漏洞只有书页支持、没有判定依据：书页支持只能说涉及/学过。
    with pytest.raises(ValueError, match="待补理解点必须关联"):
        build_summary(
            _StubCompiler(), SimpleNamespace(config={}), state,
            invoke=_invoke([
                *_valid_points()[:-1],
                _point("gap", "对概念理解不足。", fragment_ids=["obj-1:1"]),
                _point("mastered", "能读懂斜率。", question_ids=["q1"]),
            ]),
        )
    # 把判定正确且依据仍有效的题写成漏洞：把握与漏洞不得互相矛盾。
    with pytest.raises(ValueError, match="待补理解点必须关联"):
        build_summary(
            _StubCompiler(), SimpleNamespace(config={}), state,
            invoke=_invoke([
                *_valid_points(),
                _point("gap", "斜率还需补。", question_ids=["q1"]),
            ]),
        )
    # 判定不完整/错误的题未进待补：必须逐项覆盖。
    with pytest.raises(ValueError, match="未计入待补理解点"):
        build_summary(
            _StubCompiler(), SimpleNamespace(config={}), state,
            invoke=_invoke(_valid_points()),
        )
    summary = build_summary(
        _StubCompiler(), SimpleNamespace(config={}), state,
        invoke=_invoke([
            *_valid_points(),
            _point("gap", "第二题暴露的缺口。", question_ids=["q2"]),
        ]),
    )
    assert summary.scope_version_id == "scope-unit"


def test_learned_must_not_overclaim_mastery() -> None:
    state = _unit_state()
    with pytest.raises(ValueError, match="不得写成已掌握"):
        build_summary(
            _StubCompiler(), SimpleNamespace(config={}), state,
            invoke=_invoke([
                _point("learned", "已经掌握了本节全部内容。", fragment_ids=["obj-1:1"]),
                *_valid_points()[1:],
            ]),
        )
    # “要求掌握/学习目标”是书页陈述，不当作学习者掌握宣称。
    summary = build_summary(
        _StubCompiler(), SimpleNamespace(config={}), state,
        invoke=_invoke([
            _point("learned", "本节要求掌握线性函数的斜率。", fragment_ids=["obj-1:1"]),
            *_valid_points()[1:],
        ]),
    )
    assert summary.scope_version_id == "scope-unit"


def test_summary_reuses_persisted_expression_snapshot_when_available() -> None:
    state = _unit_state()
    policy = {"system_block": "【策略标记21/22】", "snapshot_complete": True}
    compiler = _StubCompiler()
    build_summary(
        compiler,
        SimpleNamespace(config={"global_writing_policy": policy}),
        state,
        invoke=_invoke(_valid_points()),
    )
    assert "【策略标记21/22】" in compiler.system_prompts[0]
    plain = _StubCompiler()
    build_summary(
        plain, SimpleNamespace(config={}), state, invoke=_invoke(_valid_points())
    )
    assert "【策略标记21/22】" not in plain.system_prompts[0]
    # 不完整/降级快照不复用：宁可无策略块，也不用降级表达规则。
    degraded = _StubCompiler()
    build_summary(
        degraded,
        SimpleNamespace(
            config={"global_writing_policy": {"system_block": "【降级策略】"}}
        ),
        state,
        invoke=_invoke(_valid_points()),
    )
    assert "【降级策略】" not in degraded.system_prompts[0]
