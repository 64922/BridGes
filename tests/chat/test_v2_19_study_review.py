"""逐题复盘通过聊天 API 验证持久等待、判定事务与恢复。"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.contracts.ai import ModelCallResult, ModelCallStatus
from tests.chat.test_v2_05_photo_attachments import PNG_BYTES, _app, _upload_draft
from tests.chat.test_v2_18_study_tutoring import TutorGateway, _ask, _retry, _start


class ReviewGateway(TutorGateway):
    def __init__(self) -> None:
        super().__init__()
        self.review_calls: list[tuple[str, dict[str, Any]]] = []
        self.failure: str | None = None
        self.question_count = 3
        self.judgement = "correct"

    def summarize(self, payload: dict[str, Any]) -> ModelCallResult:
        """最后一题判定后触发的学习总结替身（工单 20）：三段均按实际判定写。"""
        data = next(
            json.loads(message["content"])
            for message in payload["messages"]
            if message["content"].startswith('{"')
        )
        correct = [
            item["question_id"] for item in data["questions"]
            if item["judgement"] == "correct"
        ]
        weak = [
            item["question_id"] for item in data["questions"]
            if item["judgement"] != "correct"
        ]
        points: list[dict[str, Any]] = [
            {
                "kind": "learned",
                "text": "本节讲线性函数 y=ax+b 的斜率与截距。",
                "fragment_ids": [data["sources"][0]["fragment_id"]],
            }
        ]
        if correct:
            points.append(
                {"kind": "mastered", "text": "能读懂斜率与截距。", "question_ids": correct}
            )
        if weak:
            points.append(
                {"kind": "gap", "text": "截距的几何意义还需要补。", "question_ids": weak}
            )
        return ModelCallResult(status=ModelCallStatus.SUCCESS, output={"points": points})

    def invoke(
        self,
        capability: str,
        version: str,
        context: Any,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> ModelCallResult:
        task = payload.get("task")
        if task == "study.summarize":
            return self.summarize(payload)
        if task not in {
            "study.plan_review", "study.verify_questions", "study.grade",
            "study.recheck_grade",
        }:
            return super().invoke(capability, version, context, payload, **kwargs)
        data = next(
            json.loads(message["content"])
            for message in payload["messages"]
            if message["content"].startswith('{"')
        )
        self.review_calls.append((task, data))
        if self.failure == "timeout":
            return ModelCallResult(status=ModelCallStatus.BLOCKED, error_code="timeout")
        if task == "study.plan_review":
            output = {
                "questions": [
                    {
                        "question": f"第{len(data['asked']) + index + 1}个角度：解释线性函数。",
                        "coverage_units": [unit["unit_id"] for unit in data["units"]],
                        "fragment_ids": [source["source_id"] for source in data["sources"]],
                        "core_points": ["a 是斜率", "b 是纵截距"],
                        "canonical_answer": "a 是斜率，b 是纵截距。",
                        "equivalents": ["a 表示 x 每增加 1 时 y 的变化量"],
                        "key_misconceptions": ["把 a 当作截距"],
                        "incomplete_basis": "只说出斜率，未说明截距",
                        "incorrect_basis": "把 a 与 b 的角色对调",
                        "conditions": "",
                    }
                    for index in range(self.question_count)
                ]
            }
            if self.failure == "coverage":
                output = {"questions": []}
            if self.failure == "foreign":
                output["questions"][0]["fragment_ids"] = ["foreign-fragment"]
            return ModelCallResult(status=ModelCallStatus.SUCCESS, output=output)
        if task == "study.verify_questions":
            checks = [
                {
                    "question_id": question["question_id"],
                    "question_matches_knowledge": True,
                    "rubric_supported": True,
                    "answer_consistent": True,
                    "requires_calculation": False,
                    "status": "consistent",
                    "detail": "",
                }
                for question in data["questions"]
            ]
            if self.failure == "verify_conflict":
                checks[0]["status"] = "conflict"
                checks[0]["detail"] = "题干未覆盖 b 的几何意义"
            if self.failure == "verify_insufficient":
                checks[0]["status"] = "insufficient"
            if self.failure == "calculation":
                checks[0]["calculation"] = {
                    "expression": "2 + 2",
                    "variables": {},
                    "expected": 5,
                }
            return ModelCallResult(status=ModelCallStatus.SUCCESS, output={"checks": checks})
        if task == "study.recheck_grade":
            question = data["question"]
            return ModelCallResult(
                status=ModelCallStatus.SUCCESS,
                output={
                    "question_id": question["question_id"],
                    "status": "confirmed",
                    "judgement": self.judgement,
                    "explanation": "x 每增加 1，y 增加 a。",
                    "point_checks": [
                        {
                            "point": point,
                            "status": "hit" if self.judgement == "correct" else "missing",
                            "fragment_ids": list(question["fragment_ids"]),
                        }
                        for point in question["core_points"]
                    ],
                    "detail": "复核维持原判定。",
                },
            )
        question = data["question"]
        output = {
            "question_id": question["question_id"],
            "judgement": self.judgement,
            "explanation": "x 每增加 1，y 增加 a。",
            "point_checks": [
                {
                    "point": point,
                    "status": "hit" if self.judgement == "correct" else "missing",
                    "fragment_ids": list(question["fragment_ids"]),
                }
                for point in question["core_points"]
            ],
        }
        if self.failure == "wrong_id":
            output["question_id"] = "another-question"
        if self.failure == "invalid":
            output["judgement"] = "maybe"
        return ModelCallResult(status=ModelCallStatus.SUCCESS, output=output)


@pytest.mark.parametrize("count", [1, 4])
def test_review_exposes_one_question_and_grades_once(
    tmp_path: Any, monkeypatch: Any, count: int
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = ReviewGateway()
    gateway.question_count = count
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        endpoint = _start(client, app)
        result = _ask(client, app, endpoint, "我已经学完本节了，开始复盘")
        assert result["messages"][-1]["status"] == "done", result["messages"][-1]
        assert result["study"]["stage"] == "review"
        review = result["study"]["review"]
        assert len(review["questions"]) == 1
        assert review["questions"][0]["canonical_answer"] is None
        first_id = review["active_question_id"]
        repeated = _ask(client, app, endpoint, "开始复盘")
        assert repeated["study"]["review"] == review
        result = _ask(client, app, endpoint, "a 是斜率", idempotency_key="answer-1")
        judged = result["study"]["review"]["questions"][0]
        assert judged["question_id"] == first_id
        assert judged["answer"] == "a 是斜率"
        assert judged["judgement"] == "correct"
        assert judged["canonical_answer"] == "a 是斜率，b 是纵截距。"
        assert result["study"]["review"]["complete"] is (count == 1)
        assert "回答正确" in result["messages"][-1]["content"]
        assert (
            client.post(
                endpoint + "/messages",
                json={
                    "content": "a 是斜率",
                    "idempotency_key": "answer-1",
                },
            ).status_code
            == 200
        )
        app.state.generation_executor.run_tick()
        assert client.get(endpoint).json()["study"] == result["study"]
        assert [task for task, _ in gateway.review_calls].count("study.grade") == 1


@pytest.mark.parametrize("judgement,answer", [("incorrect", "不知道"), ("incomplete", "a 是参数")])
def test_wrong_or_partial_answer_gets_solution_then_next_question(
    tmp_path: Any,
    monkeypatch: Any,
    judgement: str,
    answer: str,
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = ReviewGateway()
    gateway.judgement = judgement
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        endpoint = _start(client, app)
        first = _ask(client, app, endpoint, "学完了")
        result = _ask(client, app, endpoint, answer)
        review = result["study"]["review"]
        assert review["questions"][0]["judgement"] == judgement
        assert review["active_question_id"] != first["study"]["review"]["active_question_id"]
        assert "正确答案：a 是斜率，b 是纵截距。" in result["messages"][-1]["content"]
        assert "复盘第2题" in result["messages"][-1]["content"]


@pytest.mark.parametrize("failure", ["timeout", "invalid", "wrong_id"])
def test_failed_grade_retry_stays_on_same_question(
    tmp_path: Any,
    monkeypatch: Any,
    failure: str,
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = ReviewGateway()
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        endpoint = _start(client, app)
        before = _ask(client, app, endpoint, "开始复盘")["study"]
        gateway.failure = failure
        result = _ask(client, app, endpoint, "不知道")
        assert result["messages"][-1]["status"] == "error"
        # 收答案单独保存；系统失败不写判定、不推进其余教学状态。
        before["review"]["questions"][0].update({
            "answer": "不知道", "user_message_id": result["messages"][-2]["message_id"],
        })
        assert result["study"] == before
        gateway.failure = None
        result = _retry(client, app, endpoint)
        assert result["messages"][-1]["status"] == "done", result["messages"][-1]
        assert result["study"]["review"]["questions"][0]["answer"] == "不知道"
        assert len(result["study"]["review"]["questions"]) == 2


@pytest.mark.parametrize("failure", ["coverage", "foreign", "timeout"])
def test_invalid_plan_never_starts_review(tmp_path: Any, monkeypatch: Any, failure: str) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = ReviewGateway()
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        endpoint = _start(client, app)
        before = client.get(endpoint).json()["study"]
        gateway.failure = failure
        result = _ask(client, app, endpoint, "开始复盘")
        assert result["messages"][-1]["status"] == "error"
        assert result["study"] == before
        gateway.failure = None
        result = _retry(client, app, endpoint)
        assert result["study"]["stage"] == "review"


def test_pause_tutor_and_resume_keep_asked_questions(tmp_path: Any, monkeypatch: Any) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = ReviewGateway()
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        endpoint = _start(client, app)
        _ask(client, app, endpoint, "学完了")
        before = _ask(client, app, endpoint, "a 是斜率")["study"]["review"]
        result = _ask(client, app, endpoint, "暂停复盘")
        assert result["study"]["stage"] == "tutoring"
        assert result["study"]["review"]["questions"] == before["questions"]
        _ask(client, app, endpoint, "再解释一下斜率")
        result = _ask(client, app, endpoint, "继续复盘")
        assert result["study"]["review"]["questions"][:2] == before["questions"]
        assert "复盘第3题" in result["messages"][-1]["content"]
        assert len(gateway.tutor_payloads) == 1
        assert [task for task, _ in gateway.review_calls].count("study.plan_review") == 1


def test_append_pages_replans_only_unasked_and_preserves_grades(
    tmp_path: Any, monkeypatch: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = ReviewGateway()
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        endpoint = _start(client, app)
        _ask(client, app, endpoint, "学完了")
        before = _ask(client, app, endpoint, "a 是斜率")["study"]["review"]
        gateway.page_numbers = [12, 13]
        photo = _upload_draft(client, upload_id="review-extra", content=PNG_BYTES + b"-2").json()
        result = _ask(client, app, endpoint, "漏拍了一页", attachment_ids=[photo["object_id"]])
        assert result["study"]["stage"] == "tutoring"
        assert result["study"]["review"]["needs_replan"] is True
        assert result["study"]["review"]["questions"] == before["questions"]
        result = _ask(client, app, endpoint, "继续复盘")
        assert result["messages"][-1]["status"] == "done", result["messages"][-1]
        review = result["study"]["review"]
        assert review["questions"][:2] == before["questions"]
        assert any(
            ref.startswith(photo["object_id"]) for ref in review["questions"][-1]["fragment_ids"]
        )
        plans = [data for task, data in gateway.review_calls if task == "study.plan_review"]
        assert len(plans) == 2
        assert len(plans[-1]["asked"]) == 2


@pytest.mark.parametrize("content", ["我还没学完", "开始复盘吗？", "解释“学完了”是什么意思"])
def test_only_explicit_request_starts_review(tmp_path: Any, monkeypatch: Any, content: str) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = ReviewGateway()
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        endpoint = _start(client, app)
        result = _ask(client, app, endpoint, content)
        assert result["study"]["stage"] == "tutoring"
        assert result["study"]["review"] is None
        assert not gateway.review_calls


@pytest.mark.parametrize("task", ["study.plan_review", "study.grade"])
def test_stop_during_review_does_not_advance(
    tmp_path: Any,
    monkeypatch: Any,
    task: str,
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = ReviewGateway()
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        endpoint = _start(client, app)
        if task == "study.grade":
            _ask(client, app, endpoint, "开始复盘")
        before = client.get(endpoint).json()["study"]
        response = client.post(
            endpoint + "/messages",
            json={
                "content": "开始复盘" if task == "study.plan_review" else "不知道",
            },
        )
        assistant_id = response.json()["assistant_message"]["message_id"]
        original = gateway.invoke

        def stopping(*args: Any, **kwargs: Any) -> ModelCallResult:
            result = original(*args, **kwargs)
            if kwargs.get("payload", {}).get("task") == task:
                assert client.post(endpoint + f"/messages/{assistant_id}/stop").status_code == 200
            return result

        monkeypatch.setattr(gateway, "invoke", stopping)
        app.state.generation_executor.run_tick()
        result = client.get(endpoint).json()
        assert result["messages"][-1]["status"] == "stopped"
        if task == "study.grade":
            before["review"]["questions"][0].update({
                "answer": "不知道",
                "user_message_id": response.json()["user_message"]["message_id"],
            })
        assert result["study"] == before


def test_restart_and_sse_replay_preserve_pending_question_and_isolation(
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    from tests.chat.test_v2_05_photo_attachments import _register

    app = _app(tmp_path, monkeypatch)
    gateway = ReviewGateway()
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        endpoint = _start(client, app)
        _ask(client, app, endpoint, "学完了")
        before = _ask(client, app, endpoint, "a 是斜率")
        cookies = dict(client.cookies)
    restarted = _app(tmp_path, monkeypatch)
    restarted.state.chat_service._gateway = gateway
    with TestClient(restarted) as client:
        client.cookies.update(cookies)
        assert client.get(endpoint).json()["study"] == before["study"]
        assistant_id = before["messages"][-1]["message_id"]
        events_url = endpoint + f"/messages/{assistant_id}/events"
        calls_before = len(gateway.review_calls)
        for _ in range(2):
            events = client.get(events_url)
            assert events.status_code == 200
            assert "复盘第2题" in events.text
        assert len(gateway.review_calls) == calls_before
        assert client.get(endpoint).json()["study"] == before["study"]
        result = _ask(client, restarted, endpoint, "不知道")
        assert result["study"]["review"]["questions"][1]["answer"] == "不知道"
        assert client.post(endpoint + f"/messages/{assistant_id}/retry", json={}).status_code == 409
        _register(client, "reviewforeign")
        assert client.get(endpoint).status_code == 404
        assert client.get(events_url).status_code == 404
        assert client.post(endpoint + "/messages", json={"content": "继续复盘"}).status_code == 404


def test_retry_old_failed_answer_cannot_grade_a_later_question(
    tmp_path: Any, monkeypatch: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = ReviewGateway()
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        endpoint = _start(client, app)
        _ask(client, app, endpoint, "学完了")
        gateway.failure = "timeout"
        failed = _ask(client, app, endpoint, "不知道")["messages"][-1]["message_id"]
        gateway.failure = None
        before = _ask(client, app, endpoint, "a 是斜率")["study"]
        response = client.post(endpoint + f"/messages/{failed}/retry", json={})
        assert response.status_code == 409
        assert client.get(endpoint).json()["study"] == before


def test_failed_commit_rolls_back_grade_and_next_question(tmp_path: Any, monkeypatch: Any) -> None:
    from bridges.study.service import StudyRepository

    app = _app(tmp_path, monkeypatch)
    gateway = ReviewGateway()
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        endpoint = _start(client, app)
        before = _ask(client, app, endpoint, "学完了")["study"]
        original = StudyRepository.save_in_transaction

        def fail_commit(*args: Any, **kwargs: Any) -> None:
            original(*args, **kwargs)
            raise RuntimeError("模拟提交中断")

        with monkeypatch.context() as patch:
            patch.setattr(StudyRepository, "save_in_transaction", fail_commit)
            failed = _ask(client, app, endpoint, "a 是斜率")
        assert failed["messages"][-1]["status"] == "error"
        assert failed["messages"][-1]["content"] == ""
        assert failed["study"] == before
        result = _retry(client, app, endpoint)
        assert result["messages"][-1]["status"] == "done"
        assert result["study"]["review"]["questions"][0]["answer"] == "a 是斜率"
        assert len(result["study"]["review"]["questions"]) == 2


@pytest.mark.parametrize("fail", [False, True])
def test_request_to_explain_returns_to_tutoring_without_grading(
    tmp_path: Any,
    monkeypatch: Any,
    fail: bool,
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = ReviewGateway()
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        endpoint = _start(client, app)
        before = _ask(client, app, endpoint, "学完了")["study"]
        gateway.fail_tutor = fail
        result = _ask(client, app, endpoint, "先给我再讲一下斜率")
        assert not any(task == "study.grade" for task, _ in gateway.review_calls)
        if fail:
            assert result["messages"][-1]["status"] == "error"
            assert result["study"] == before
        else:
            assert result["study"]["stage"] == "tutoring"
            assert result["study"]["review"]["active_question_id"] is None
            assert result["study"]["review"]["questions"] == before["review"]["questions"]
            resumed = _ask(client, app, endpoint, "继续复盘")
            assert "复盘第2题" in resumed["messages"][-1]["content"]
