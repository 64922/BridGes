"""复盘计划全部判定后生成可追溯的学习总结，并支持同节追问。"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.contracts.ai import ModelCallResult, ModelCallStatus
from bridges.lifecycle.catalog import export_rows
from tests.chat.test_v2_05_photo_attachments import PNG_BYTES, _app, _register, _upload_draft
from tests.chat.test_v2_17_study_pages import _first
from tests.chat.test_v2_18_study_tutoring import _ask, _retry, _start
from tests.chat.test_v2_19_study_review import ReviewGateway

_SECTIONS = ("学到了什么", "复盘已掌握", "还需补的点")


class SummaryGateway(ReviewGateway):
    """记录每次总结请求，并按需注入失败、结构不合法或超出判定的输出。"""

    def __init__(self) -> None:
        super().__init__()
        self.summary_calls: list[dict[str, Any]] = []
        self.judgements: list[str] = []
        self.fail_summary: str | None = None
        self.summary: dict[str, Any] | None = None

    def invoke(
        self,
        capability: str,
        version: str,
        context: Any,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> ModelCallResult:
        if payload.get("task") == "study.grade" and self.judgements:
            self.judgement = self.judgements.pop(0)
        return super().invoke(capability, version, context, payload, **kwargs)

    def summarize(self, payload: dict[str, Any]) -> ModelCallResult:
        data = next(
            json.loads(message["content"])
            for message in payload["messages"]
            if message["content"].startswith('{"')
        )
        self.summary_calls.append(data)
        if self.fail_summary == "timeout":
            return ModelCallResult(status=ModelCallStatus.BLOCKED, error_code="timeout")
        if self.summary is not None:
            return ModelCallResult(status=ModelCallStatus.SUCCESS, output=self.summary)
        if self.fail_summary == "structure":
            return ModelCallResult(status=ModelCallStatus.SUCCESS, output={"points": []})
        ids = [item["question_id"] for item in data["questions"]]
        correct = [
            item["question_id"] for item in data["questions"]
            if item["judgement"] == "correct"
        ]
        learned = {
            "kind": "learned",
            "text": "本节讲线性函数 y=ax+b 的斜率与截距。",
            "fragment_ids": [data["sources"][0]["fragment_id"]],
        }
        if self.fail_summary == "dangling":
            return ModelCallResult(
                status=ModelCallStatus.SUCCESS,
                output={"points": [{**learned, "question_ids": ["foreign-question"]}]},
            )
        if self.fail_summary == "overclaim":
            return ModelCallResult(
                status=ModelCallStatus.SUCCESS,
                output={
                    "points": [
                        learned,
                        {"kind": "mastered", "text": "本节内容已全部掌握。", "question_ids": ids},
                    ]
                },
            )
        if self.fail_summary == "uncited":
            return ModelCallResult(
                status=ModelCallStatus.SUCCESS,
                output={
                    "points": [
                        learned,
                        *([{"kind": "mastered", "text": "能读懂斜率与截距。",
                            "question_ids": correct}] if correct else []),
                    ]
                },
            )
        return super().summarize(payload)


def _section(content: str, title: str) -> str:
    """取聊天消息里某一段的正文（三段之间以空行分隔）。"""
    blocks = content.split(f"\n{title}：\n")
    return blocks[1].split("\n\n")[0] if len(blocks) > 1 else ""


def _finish_review(client: TestClient, app: Any, endpoint: str, limit: int = 8) -> dict[str, Any]:
    """逐题作答直到复盘结束并出现总结；题量与判定由工作流与替身决定。"""
    result = client.get(endpoint).json()
    if result["study"]["review"] is None:
        result = _ask(client, app, endpoint, "学完了")
    for _ in range(limit):
        if result["study"]["stage"] == "summary":
            return result
        result = _ask(client, app, endpoint, "不知道")
        assert result["messages"][-1]["status"] == "done", result["messages"][-1]
    raise AssertionError("复盘未在预期题量内结束")


def test_summary_starts_after_last_judgement_and_replays_without_rework(
    tmp_path: Any, monkeypatch: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = SummaryGateway()
    gateway.question_count = 2
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        endpoint = _start(client, app)
        question = "第12页 y=ax+b 里的 a 是什么意思？"
        tutor = _ask(client, app, endpoint, question)
        assert tutor["study"]["stage"] == "tutoring"
        _ask(client, app, endpoint, "学完了")
        graded = _ask(client, app, endpoint, "a 是斜率")
        assert graded["study"]["stage"] == "review"
        assert graded["study"]["summary"] is None
        assert gateway.summary_calls == []
        assert "复盘第2题" in graded["messages"][-1]["content"]
        result = _ask(client, app, endpoint, "b 是纵截距")
        assert result["messages"][-1]["status"] == "done", result["messages"][-1]
        assert result["study"]["stage"] == "summary"
        review = result["study"]["review"]
        assert review["complete"] is True
        assert [item["judgement"] for item in review["questions"]] == ["correct", "correct"]
        summary = result["study"]["summary"]
        assert {point["kind"] for point in summary["points"]} == {"learned", "mastered"}
        content = result["messages"][-1]["content"]
        assert "回答正确" in content
        assert all(title in content for title in _SECTIONS)
        assert "本次复盘的题目全部答对，暂无待补的理解点。" in _section(content, "还需补的点")
        assert "判定为正确" in _section(content, "复盘已掌握")
        assert "第1题「" in _section(content, "复盘已掌握")
        assert "上传第1页" in _section(content, "学到了什么")
        assert len(gateway.summary_calls) == 1
        material = gateway.summary_calls[0]
        assert [item["judgement"] for item in material["questions"]] == ["correct", "correct"]
        assert material["tutoring"][0]["question"] == question
        assert "data:image" not in str(material)
        repeated = _ask(client, app, endpoint, "继续复盘")
        assert repeated["study"]["summary"] == summary
        assert repeated["study"]["review"] == review
        assert len(gateway.summary_calls) == 1


def test_mastery_and_gaps_trace_to_real_judgements(tmp_path: Any, monkeypatch: Any) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = SummaryGateway()
    gateway.question_count = 3
    gateway.judgements = ["correct", "incorrect", "incomplete"]
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        endpoint = _start(client, app)
        result = _finish_review(client, app, endpoint)
        content = result["messages"][-1]["content"]
        mastered, gaps = _section(content, "复盘已掌握"), _section(content, "还需补的点")
        assert "第1题「" in mastered and "判定为错误" not in mastered
        assert "第2题「" in gaps and "判定为错误" in gaps
        assert "第3题「" in gaps and "判定为不完整" in gaps
        assert "已全部掌握" not in content
        summary = result["study"]["summary"]
        assert [point["kind"] for point in summary["points"]] == ["learned", "mastered", "gap"]
        assert len(summary["points"][1]["question_ids"]) == 1
        assert len(summary["points"][2]["question_ids"]) == 2
        assert [item["question"] for item in gateway.summary_calls[0]["questions"]] == [
            "第1个角度：解释线性函数。",
            "第2个角度：解释线性函数。",
            "第3个角度：解释线性函数。",
        ]
        account_id = client.get("/auth/session").json()["account"]["id"]
        rows = export_rows(app.state.bridges_database, account_id, "study_states")
        assert len(rows) == 1
        exported = json.loads(rows[0]["state_json"])
        assert exported["summary"] == summary
        assert [item["judgement"] for item in exported["review"]["questions"]] == [
            "correct",
            "incorrect",
            "incomplete",
        ]
        # 账户隔离：另一位使用者读不到本节，导出也只有自己的小节状态。
        _register(client, "summaryforeign")
        assert client.get(endpoint).status_code == 404
        foreign = _register(client, "summaryowner2")
        photo = _upload_draft(client, upload_id="foreign-photo").json()
        created = _first(client, [photo["object_id"]], key="foreign-study-first")
        assert created.status_code == 201, created.text
        app.state.generation_executor.run_tick()
        others = export_rows(app.state.bridges_database, foreign["id"], "study_states")
        assert [row["conversation_id"] for row in others] == [
            created.json()["conversation"]["conversation_id"]
        ]
        assert [row["conversation_id"] for row in
                export_rows(app.state.bridges_database, account_id, "study_states")] == [
            endpoint.rsplit("/", 1)[-1]
        ]


@pytest.mark.parametrize(
    "failure,judgement,code",
    [
        ("timeout", "correct", "timeout"),
        ("structure", "correct", "study_summary_invalid"),
        ("dangling", "correct", "study_summary_invalid"),
        ("overclaim", "incorrect", "study_summary_invalid"),
        ("uncited", "incorrect", "study_summary_invalid"),
    ],
)
def test_unverified_summary_keeps_judgements_and_retries_once(
    tmp_path: Any, monkeypatch: Any, failure: str, judgement: str, code: str
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = SummaryGateway()
    gateway.question_count = 1
    gateway.judgements = [judgement]
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        endpoint = _start(client, app)
        before = _ask(client, app, endpoint, "学完了")["study"]
        gateway.fail_summary = failure
        failed = _ask(client, app, endpoint, "a 是斜率")
        assert failed["messages"][-1]["status"] == "error"
        assert failed["messages"][-1]["error_code"] == code
        assert "study.summarize" in failed["messages"][-1]["error_message"]
        assert failed["study"] == before
        gateway.fail_summary = None
        result = _retry(client, app, endpoint)
        assert result["messages"][-1]["status"] == "done", result["messages"][-1]
        graded = result["study"]["review"]["questions"][0]
        assert graded["answer"] == "a 是斜率"
        assert graded["judgement"] == judgement
        assert result["study"]["summary"] is not None
        assert "a 是斜率，b 是纵截距。" in result["messages"][-1]["content"]
        assert len(gateway.summary_calls) == 2
        repeated = _ask(client, app, endpoint, "继续复盘")
        assert repeated["study"]["summary"] == result["study"]["summary"]
        assert len(gateway.summary_calls) == 2


def test_followup_after_summary_returns_to_tutoring(tmp_path: Any, monkeypatch: Any) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = SummaryGateway()
    gateway.question_count = 1
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        endpoint = _start(client, app)
        before = _finish_review(client, app, endpoint)["study"]
        assert before["stage"] == "summary"
        result = _ask(client, app, endpoint, "再讲讲斜率")
        assert result["study"]["stage"] == "tutoring"
        assert result["study"]["summary"] == before["summary"]
        assert result["study"]["review"] == before["review"]
        assert "本节书页" in result["messages"][-1]["content"]
        resumed = _ask(client, app, endpoint, "继续复盘")
        assert resumed["study"]["stage"] == "summary"
        assert resumed["study"]["summary"] == before["summary"]
        assert len(gateway.summary_calls) == 1


def test_appended_pages_replan_review_and_renew_summary(
    tmp_path: Any, monkeypatch: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = SummaryGateway()
    gateway.question_count = 1
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        endpoint = _start(client, app)
        before = _finish_review(client, app, endpoint)["study"]
        assert before["summary"] is not None
        assert len(before["review"]["questions"]) == 1
        gateway.page_numbers = [12, 13]
        photo = _upload_draft(
            client, upload_id="summary-extra", content=PNG_BYTES + b"-2"
        ).json()
        result = _ask(client, app, endpoint, "漏拍了一页", attachment_ids=[photo["object_id"]])
        assert result["messages"][-1]["status"] == "done", result["messages"][-1]
        assert result["study"]["stage"] == "tutoring"
        assert result["study"]["summary"] is None
        assert result["study"]["review"]["needs_replan"] is True
        assert result["study"]["review"]["questions"] == before["review"]["questions"]
        assert result["study"]["review"]["complete"] is False
        result = _ask(client, app, endpoint, "继续复盘")
        renewed = result["study"]["review"]["questions"]
        assert renewed[:1] == before["review"]["questions"]
        assert any(
            fragment_id.startswith(photo["object_id"])
            for item in renewed[1:]
            for fragment_id in item["fragment_ids"]
        )
        assert len(gateway.summary_calls) == 1
        result = _finish_review(client, app, endpoint)
        assert result["study"]["stage"] == "summary"
        assert result["study"]["review"]["questions"][:1] == before["review"]["questions"]
        assert len(gateway.summary_calls) == 2
        assert len(gateway.summary_calls[1]["questions"]) == len(
            result["study"]["review"]["questions"]
        )
        assert "还需补的点" in result["messages"][-1]["content"]


def test_failed_summary_cannot_regrade_a_later_question(tmp_path: Any, monkeypatch: Any) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = SummaryGateway()
    gateway.question_count = 2
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        endpoint = _start(client, app)
        _ask(client, app, endpoint, "学完了")
        _ask(client, app, endpoint, "a 是斜率")
        gateway.fail_summary = "timeout"
        failed = _ask(client, app, endpoint, "b 是纵截距")["messages"][-1]["message_id"]
        gateway.fail_summary = None
        before = _ask(client, app, endpoint, "再讲讲斜率")["study"]
        assert [item["judgement"] for item in before["review"]["questions"]] == ["correct", None]
        assert before["review"]["questions"][1]["answer"] is None
        response = client.post(endpoint + f"/messages/{failed}/retry", json={})
        assert response.status_code == 409
        assert client.get(endpoint).json()["study"] == before
        assert len(gateway.summary_calls) == 1
        assert before["review"]["questions"][0]["answer"] == "a 是斜率"


def test_stop_during_summary_keeps_review_complete(tmp_path: Any, monkeypatch: Any) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = SummaryGateway()
    gateway.question_count = 1
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        endpoint = _start(client, app)
        before = _ask(client, app, endpoint, "开始复盘")["study"]
        response = client.post(endpoint + "/messages", json={"content": "a 是斜率"})
        assistant_id = response.json()["assistant_message"]["message_id"]
        original = gateway.invoke

        def stopping(*args: Any, **kwargs: Any) -> ModelCallResult:
            result = original(*args, **kwargs)
            if kwargs.get("payload", {}).get("task") == "study.summarize":
                assert client.post(endpoint + f"/messages/{assistant_id}/stop").status_code == 200
            return result

        monkeypatch.setattr(gateway, "invoke", stopping)
        app.state.generation_executor.run_tick()
        result = client.get(endpoint).json()
        assert result["messages"][-1]["status"] == "stopped"
        assert result["study"] == before
        assert result["study"]["summary"] is None
