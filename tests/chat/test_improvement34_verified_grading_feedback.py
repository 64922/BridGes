"""工单 34：幂等判定作答并执行一次覆盖反馈的验收测试。

覆盖：等价答案经独立复核通过且标准不漂移、争议/结构/模型失败保留当前题、
判定/反馈/下一题呈现各自提交边界、同来源消息重试不重判、最后一题判定与
总结分离、错题反馈继续原覆盖计划。确定性模型替身只证明机制。
"""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from bridges.contracts.ai import ModelCallResult, ModelCallStatus
from tests.chat.test_improvement33_preverified_questions import (
    _FROZEN_CANONICAL,
    Review33Gateway,
    _raw_state,
    _single_unit_map,
    _start_review,
)
from tests.chat.test_v2_05_photo_attachments import _app
from tests.chat.test_v2_18_study_tutoring import _ask, _retry

_FROZEN_POINTS = ["温度的定义", "冷热程度的度量"]


def _payload_data(payload: dict[str, Any]) -> dict[str, Any]:
    return next(
        json.loads(message["content"])
        for message in payload["messages"]
        if message["content"].startswith('{"')
    )


def _two_question_plan(data: dict[str, Any]) -> list[dict[str, Any]]:
    unit = data["units"][0]
    return [
        {
            "question": f"第{index}个角度：说明本节核心概念。",
            "coverage_units": [unit["unit_id"]],
            "fragment_ids": list(unit["fragment_ids"]),
            "core_points": list(_FROZEN_POINTS),
            "canonical_answer": _FROZEN_CANONICAL,
            "equivalents": ["意思相同的另一种说法"],
            "key_misconceptions": ["把定义说反"],
            "incomplete_basis": "只说出概念，未说明关系",
            "incorrect_basis": "结论与书页冲突",
            "conditions": "",
        }
        for index in (1, 2)
    ]


class Grade34Gateway(Review33Gateway):
    """在工单 33 替身上分离初次判定与独立复核的确定性响应。"""

    def __init__(
        self,
        texts: list[str],
        *,
        map_fn: Any,
        plan_fn: Any = None,
        verify_fn: Any = None,
    ) -> None:
        super().__init__(
            texts, map_fn=map_fn, plan_fn=plan_fn, verify_fn=verify_fn
        )
        #: 初次判定结论；独立复核默认维持它。
        self.initial_judgement = "correct"
        self.fail_grade: str | None = None
        self.recheck_calls: list[dict[str, Any]] = []
        self.recheck_fn: Any = None
        self.summary_calls = 0
        self.summary_invalid_rounds = 0

    def _checks(self, data: dict[str, Any], judgement: str) -> list[dict[str, Any]]:
        question = data["question"]
        return [
            {
                "point": point,
                "status": "hit" if judgement == "correct" else "missing",
                "fragment_ids": list(question["fragment_ids"]),
            }
            for point in question["core_points"]
        ]

    def _default_recheck(self, data: dict[str, Any]) -> dict[str, Any]:
        return {
            "question_id": data["question"]["question_id"],
            "status": "confirmed",
            "judgement": self.initial_judgement,
            "explanation": "独立复核维持原判定。",
            "point_checks": self._checks(data, self.initial_judgement),
            "detail": "",
        }

    def invoke(
        self,
        capability: str,
        version: str,
        context: Any,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> ModelCallResult:
        task = payload.get("task")
        if task == "study.grade":
            if self.fail_grade == "timeout":
                return ModelCallResult(status=ModelCallStatus.BLOCKED, error_code="timeout")
            self.grade_judgement = self.initial_judgement
        if task == "study.recheck_grade":
            data = _payload_data(payload)
            self.recheck_calls.append(data)
            output = (
                self.recheck_fn(data) if self.recheck_fn is not None
                else self._default_recheck(data)
            )
            if isinstance(output, ModelCallResult):
                return output
            return ModelCallResult(status=ModelCallStatus.SUCCESS, output=output)
        return super().invoke(capability, version, context, payload, **kwargs)

    def summarize(self, payload: dict[str, Any]) -> ModelCallResult:
        self.summary_calls += 1
        if self.summary_invalid_rounds > 0:
            self.summary_invalid_rounds -= 1
            return ModelCallResult(status=ModelCallStatus.SUCCESS, output={"points": []})
        return super().summarize(payload)


def _steal_lease(app: Any, account_id: str, assistant_id: str) -> None:
    run = app.state.chat_service._repo.get_run_by_message(account_id, assistant_id)
    assert run is not None, "缺少运行记录"
    database = app.state.bridges_database
    with database.transaction():
        database.connection.execute(
            "UPDATE generation_runs SET lease_owner = 'other-worker' WHERE run_id = ?",
            (run.run_id,),
        )


def test_equivalent_answer_passes_recheck_and_frozen_standard_does_not_drift(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = Grade34Gateway(
        ["温度是表示物体冷热程度的物理量。"], map_fn=_single_unit_map
    )
    gateway.initial_judgement = "incorrect"

    def recheck_fn(data: dict[str, Any]) -> dict[str, Any]:
        return {
            "question_id": data["question"]["question_id"],
            "status": "revised",
            "judgement": "correct",
            "explanation": "学生用等价表述完整命中核心要点。",
            "point_checks": gateway._checks(data, "correct"),
            "detail": "措辞不同但语义等价，按冻结要点应判正确。",
        }

    gateway.recheck_fn = recheck_fn
    with TestClient(app) as client:
        account, endpoint = _start_review(client, app, gateway)
        _ask(client, app, endpoint, "开始复盘")
        frozen = _raw_state(app, account["id"], endpoint).review.questions[0]
        assert frozen.canonical_answer == _FROZEN_CANONICAL

        result = _ask(client, app, endpoint, "冷热程度的量度")
        assert result["messages"][-1]["status"] == "done", result["messages"][-1]
        question = result["study"]["review"]["questions"][0]
        assert question["judgement"] == "correct"
        # 标准答案取自作答前冻结值，不被判定/复核输出改写。
        assert question["canonical_answer"] == _FROZEN_CANONICAL
        assert "回答正确" in result["messages"][-1]["content"]
        assert len(gateway.grade_calls) == 1
        assert len(gateway.recheck_calls) == 1

        raw = _raw_state(app, account["id"], endpoint).review.questions[0]
        assert raw.canonical_answer == _FROZEN_CANONICAL
        assert raw.feedback and "回答正确" in raw.feedback
        assert raw.grade_record is not None
        assert raw.grade_record.recheck_status == "revised"
        assert {item.status for item in raw.grade_record.point_checks} == {"hit"}


def test_disputed_recheck_keeps_question_without_student_error(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = Grade34Gateway(
        ["温度是表示物体冷热程度的物理量。"], map_fn=_single_unit_map
    )
    gateway.initial_judgement = "incorrect"

    def disputed(data: dict[str, Any]) -> dict[str, Any]:
        return {
            "question_id": data["question"]["question_id"],
            "status": "conflict",
            "judgement": "incorrect",
            "explanation": "书页证据与作答解释存在冲突。",
            "point_checks": gateway._checks(data, "incorrect"),
            "detail": "核心证据冲突，无法裁决。",
        }

    gateway.recheck_fn = disputed
    with TestClient(app) as client:
        account, endpoint = _start_review(client, app, gateway)
        before = _ask(client, app, endpoint, "开始复盘")["study"]
        result = _ask(client, app, endpoint, "不知道")
        assert result["messages"][-1]["status"] == "error"
        assert result["messages"][-1]["error_code"] == "study_review_disputed"
        # 复核未决：保留当前题，不记学生错答，也不推进游标。
        assert (
            result["study"]["review"]["active_question_id"]
            == before["review"]["active_question_id"]
        )
        raw = _raw_state(app, account["id"], endpoint).review.questions[0]
        assert raw.judgement is None
        assert raw.answer == "不知道"
        assert raw.user_message_id

        gateway.recheck_fn = None
        result = _retry(client, app, endpoint)
        assert result["messages"][-1]["status"] == "done", result["messages"][-1]
        judged = result["study"]["review"]["questions"][0]
        assert judged["judgement"] == "incorrect"
        assert judged["answer"] == "不知道"
        assert "正确答案：" in result["messages"][-1]["content"]
        assert len(gateway.recheck_calls) == 2


def test_grade_failure_keeps_question_then_retry_commits_once(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = Grade34Gateway(
        ["温度是表示物体冷热程度的物理量。"], map_fn=_single_unit_map
    )
    gateway.fail_grade = "timeout"
    with TestClient(app) as client:
        account, endpoint = _start_review(client, app, gateway)
        before = _ask(client, app, endpoint, "开始复盘")["study"]
        failed = _ask(client, app, endpoint, "温度表示冷热程度")
        assert failed["messages"][-1]["status"] == "error"
        assert (
            failed["study"]["review"]["active_question_id"]
            == before["review"]["active_question_id"]
        )

        gateway.fail_grade = None
        result = _retry(client, app, endpoint)
        assert result["messages"][-1]["status"] == "done", result["messages"][-1]
        question = result["study"]["review"]["questions"][0]
        assert question["judgement"] == "correct"
        assert question["answer"] == "温度表示冷热程度"
        assert question["user_message_id"]
        assert len(gateway.grade_calls) == 1

        # 已提交合法判定不因重试重新生成。
        assistant_id = result["messages"][-1]["message_id"]
        response = client.post(endpoint + f"/messages/{assistant_id}/retry", json={})
        assert response.status_code == 409
        assert client.get(endpoint).json()["study"] == result["study"]


def test_judgement_and_next_question_have_separate_commit_boundaries(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    from bridges.study import service as study_service

    app = _app(tmp_path, monkeypatch)
    gateway = Grade34Gateway(
        ["温度是表示物体冷热程度的物理量。"],
        map_fn=_single_unit_map,
        plan_fn=_two_question_plan,
    )
    original = study_service.advance_question
    calls = {"count": 0}

    def flaky(review: Any) -> str | None:
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("模拟下一题呈现提交中断")
        return original(review)

    with TestClient(app) as client:
        account, endpoint = _start_review(client, app, gateway)
        _ask(client, app, endpoint, "开始复盘")
        with monkeypatch.context() as patch:
            patch.setattr(study_service, "advance_question", flaky)
            failed = _ask(client, app, endpoint, "温度表示冷热程度")
        assert failed["messages"][-1]["status"] == "error"
        # 判定与反馈已在自己的提交边界落库；下一题尚未呈现。
        raw = _raw_state(app, account["id"], endpoint)
        question = raw.review.questions[0]
        assert question.judgement == "correct"
        assert question.answer == "温度表示冷热程度"
        assert question.feedback and _FROZEN_CANONICAL in question.feedback
        assert len(raw.review.questions) == 2
        assert raw.review.questions[1].asked is False
        # 呈现提交中断时激活题仍是已判定题，重试时应补提交下一题。
        assert raw.review.active_question_id == question.question_id

        result = _retry(client, app, endpoint)
        assert result["messages"][-1]["status"] == "done", result["messages"][-1]
        # 重试重放已提交反馈，不重新判定；下一题恰好呈现一次。
        content = result["messages"][-1]["content"]
        assert "回答正确" in content and "复盘第2题" in content
        assert len(gateway.grade_calls) == 1
        raw = _raw_state(app, account["id"], endpoint)
        assert raw.review.active_question_id == raw.review.questions[1].question_id
        assert raw.review.questions[1].asked is True


def test_last_judgement_survives_summary_failure_and_retry_does_not_regrade(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = Grade34Gateway(
        ["温度是表示物体冷热程度的物理量。"], map_fn=_single_unit_map
    )
    gateway.summary_invalid_rounds = 1
    with TestClient(app) as client:
        account, endpoint = _start_review(client, app, gateway)
        _ask(client, app, endpoint, "开始复盘")
        failed = _ask(client, app, endpoint, "温度表示冷热程度")
        assert failed["messages"][-1]["status"] == "error"
        assert failed["messages"][-1]["error_code"] == "study_summary_invalid"
        # 最后一题判定先提交：总结失败不丢判定与反馈。
        raw = _raw_state(app, account["id"], endpoint)
        question = raw.review.questions[0]
        assert question.judgement == "correct"
        assert question.feedback
        assert raw.review.complete is True
        assert raw.summary is None
        assert raw.stage == "review"

        result = _retry(client, app, endpoint)
        assert result["messages"][-1]["status"] == "done", result["messages"][-1]
        assert result["study"]["summary"] is not None
        content = result["messages"][-1]["content"]
        assert "回答正确" in content and "学到了什么" in content
        assert len(gateway.grade_calls) == 1
        assert gateway.summary_calls == 2


def test_wrong_feedback_continues_frozen_plan_without_remediation(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = Grade34Gateway(
        ["温度是表示物体冷热程度的物理量。"],
        map_fn=_single_unit_map,
        plan_fn=_two_question_plan,
    )
    gateway.initial_judgement = "incomplete"
    with TestClient(app) as client:
        account, endpoint = _start_review(client, app, gateway)
        _ask(client, app, endpoint, "开始复盘")
        raw_before = _raw_state(app, account["id"], endpoint)
        question_ids = [item.question_id for item in raw_before.review.questions]

        result = _ask(client, app, endpoint, "温度是冷热程度")
        content = result["messages"][-1]["content"]
        assert "回答不完整" in content
        assert "正确答案：" in content and _FROZEN_CANONICAL in content
        assert "复盘第2题" in content
        # 继续原覆盖计划：题量不变、不追加补救题、不要求补答原题。
        raw = _raw_state(app, account["id"], endpoint)
        assert [item.question_id for item in raw.review.questions] == question_ids
        assert raw.review.questions[0].judgement == "incomplete"
        assert raw.review.active_question_id == question_ids[1]
        assert len(gateway.plan_calls) == 1
        assert len(gateway.recheck_calls) == 1


def test_late_lease_loss_rejects_grade_without_advancing(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = Grade34Gateway(
        ["温度是表示物体冷热程度的物理量。"], map_fn=_single_unit_map
    )
    with TestClient(app) as client:
        account, endpoint = _start_review(client, app, gateway)
        before = _ask(client, app, endpoint, "开始复盘")["study"]
        response = client.post(endpoint + "/messages", json={"content": "温度表示冷热程度"})
        assistant_id = response.json()["assistant_message"]["message_id"]
        original = gateway.invoke

        def stealing(*args: Any, **kwargs: Any) -> ModelCallResult:
            result = original(*args, **kwargs)
            if kwargs.get("payload", {}).get("task") == "study.grade":
                _steal_lease(app, account["id"], assistant_id)
            return result

        monkeypatch.setattr(gateway, "invoke", stealing)
        app.state.generation_executor.run_tick()
        result = client.get(endpoint).json()
        assert result["messages"][-1]["status"] == "error"
        assert result["messages"][-1]["error_code"] == "lease_lost"
        # 失去执行权后的迟到判定不得覆盖任何状态。
        assert (
            result["study"]["review"]["active_question_id"]
            == before["review"]["active_question_id"]
        )
        raw = _raw_state(app, account["id"], endpoint).review.questions[0]
        assert raw.judgement is None
        assert raw.answer == "温度表示冷热程度"
