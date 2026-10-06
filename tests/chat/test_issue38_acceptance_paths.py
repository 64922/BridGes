"""工单 38：正式 HTTP、执行器与现行图的独立验收；模型/来源边界固定，无外呼。"""

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.chat.test_chat_api import _create_conversation, _gateway_with
from tests.chat.test_improvement33_preverified_questions import _single_unit_map, _start_review
from tests.chat.test_improvement34_verified_grading_feedback import (
    Grade34Gateway,
    _two_question_plan,
)
from tests.chat.test_improvement36_evidence_bound_summary import Ticket36Gateway
from tests.chat.test_v2_05_photo_attachments import _app, _register
from tests.chat.test_v2_18_study_tutoring import TutorGateway, _ask, _retry, _start
from tests.github.test_github_module_flow import (
    WHOLE_IDEA,
    WHOLE_QUERY,
    _candidate,
    _evidence,
    _FakeReader,
    _FakeSearchPort,
    _install_github_service,
)
from tests.paper.test_paper_module_flow import (
    ATTENTION_CANDIDATES,
    _FakePaperSource,
    _install_paper_source,
    _SilentAdapter,
)


def _replay(
    client: TestClient, endpoint: str, message: dict[str, Any], helpers: dict[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    """终态重放与重复历史读取必须同正文、同结果，不生成第二次效果。"""
    events = helpers["subscribe"](client, endpoint.rsplit("/", 1)[-1], message["message_id"])
    done = [payload["message"] for kind, payload in events if kind == "done"]
    assert len(done) == 1
    assert done[0]["content"] == message["content"]
    assert done[0]["turn_result"] == message["turn_result"]
    repeated = client.get(endpoint).json()["messages"][-1]
    assert repeated["content"] == message["content"]
    assert repeated["turn_result"] == message["turn_result"]
    return events


def test_review_wait_and_private_fields_use_real_graph(
    tmp_path: Any, monkeypatch: Any, generation_helpers: dict[str, Any],
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = Grade34Gateway(["温度是表示物体冷热程度的物理量。"],
                             map_fn=_single_unit_map, plan_fn=_two_question_plan)
    with TestClient(app) as client:
        _, endpoint = _start_review(client, app, gateway)
        result = _ask(client, app, endpoint, "开始复盘")
        message = result["messages"][-1]
        assert message["turn_result"]["outcome"] == "needs_input"
        assert message["turn_result"]["wait_reason"]
        questions = result["study"]["review"]["questions"]
        assert len(questions) == 1 and questions[0]["canonical_answer"] is None
        assert not questions[0]["core_points"] and questions[0]["grade_record"] is None
        events = _replay(client, endpoint, message, generation_helpers)
        public = json.dumps(events, ensure_ascii=False)
        assert "第2个角度" not in public and "标准答案：温度表示物体冷热程度" not in public
        assert "冷热程度的度量" not in public
        paused = _ask(client, app, endpoint, "暂停复盘")
        message = paused["messages"][-1]
        assert "已暂停复盘" in message["content"]
        assert paused["study"]["stage"] == "tutoring"
        _replay(client, endpoint, message, generation_helpers)


def test_tutoring_partial_evidence_uses_real_graph(
    tmp_path: Any, monkeypatch: Any, generation_helpers: dict[str, Any],
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = TutorGateway()
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        endpoint = _start(client, app)
        gateway.answer = {
            "parts": [{"kind": "model", "text": "积分可以理解为累积。"}],
            "gap": "目前的线性函数书页没有积分定义",
        }
        result = _ask(client, app, endpoint, "书上如何定义积分？")
        message = result["messages"][-1]
        assert message["status"] == "done"
        assert message["turn_result"]["outcome"] == "partial"
        assert message["turn_result"]["trust"] == "evidence_bound"
        assert message["turn_result"]["gaps"]
        assert "书页缺口" in message["content"] and "模型知识补充" in message["content"]
        assert result["study"]["review"] is None
        _replay(client, endpoint, message, generation_helpers)


@pytest.mark.parametrize("failure", ["timeout", "structure"])
def test_summary_failure_keeps_feedback_and_recovers_only_summary(
    tmp_path: Any, monkeypatch: Any, generation_helpers: dict[str, Any], failure: str,
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = Ticket36Gateway()
    gateway.question_count = 1
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        endpoint = _start(client, app)
        _ask(client, app, endpoint, "学完了")
        gateway.fail_summary = failure
        failed = _ask(client, app, endpoint, "a 是斜率")
        message = failed["messages"][-1]
        assert message["status"] == "error"
        assert message["turn_result"]["outcome"] == "partial"
        assert message["turn_result"]["delivered"] and message["turn_result"]["gaps"]
        assert message["turn_result"]["recovery"]["retryable"]
        assert "回答正确" in message["content"]
        grade_count = [task for task, _ in gateway.review_calls].count("study.grade")
        assert grade_count == 1
        gateway.fail_summary = None
        recovered = _retry(client, app, endpoint)
        message = recovered["messages"][-1]
        assert message["status"] == "done" and "本节学习总结" in message["content"]
        assert [task for task, _ in gateway.review_calls].count("study.grade") == grade_count
        assert len(gateway.summary_calls) == 2
        assert message["content"].count("回答正确") == 1
        _replay(client, endpoint, message, generation_helpers)


@pytest.mark.parametrize("module", ["paper", "github"])
def test_domain_clarification_and_source_disclosure_use_real_graph(
    tmp_path: Any, monkeypatch: Any, generation_helpers: dict[str, Any], module: str,
) -> None:
    app = _app(tmp_path, monkeypatch)
    app.state.chat_service._gateway = _gateway_with(_SilentAdapter())
    with TestClient(app) as client:
        _register(client, f"paths38{module}")
        if module == "paper":
            source = _install_paper_source(app, _FakePaperSource(candidates=ATTENTION_CANDIDATES))
            initial, continuation, field = (
                "帮我找 Transformer 的论文", "机器学习方向的，入门", "paper_search",
            )
        else:
            source = _FakeSearchPort(per_query={
                WHOLE_QUERY: [_candidate("demo/books", description="校园二手书交换")],
            })
            _install_github_service(app, port=source, reader=_FakeReader({
                "demo/books": _evidence("demo/books", description="校园二手书交换平台"),
            }))
            initial, continuation, field = "有没有人做过类似的项目", WHOLE_IDEA, "github_projects"
        conversation_id = _create_conversation(client)
        endpoint = f"/chat/conversations/{conversation_id}"
        waiting = _ask(client, app, endpoint, initial, module_id=module)
        message = waiting["messages"][-1]
        assert message[field]["status"] == "clarification"
        assert message["turn_result"]["outcome"] == "needs_input"
        assert message["turn_result"]["wait_reason"]
        assert not source.queries
        _replay(client, endpoint, message, generation_helpers)
        completed = _ask(client, app, endpoint, continuation, module_id=module)
        message = completed["messages"][-1]
        assert message[field]["status"] == "success", message
        assert source.queries
        assert message["turn_result"]["delivered"]
        assert message["turn_result"]["actual_module_id"] == module
        assert completed["messages"][-2]["module_id"] == module
        assert message[field]["queries"]
        assert "https://" in message["content"]
        _replay(client, endpoint, message, generation_helpers)
