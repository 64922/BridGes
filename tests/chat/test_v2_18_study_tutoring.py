"""本节辅导通过聊天 API 保存问答、引用与追加页等待。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.chat.context_compiler import ContextEvidence, compile_turn_context
from bridges.contracts.ai import ModelCallResult, ModelCallStatus
from bridges.contracts.chat import ChatMessageRole, ChatMode
from bridges.retrieval.service import LayeredRetrievalService
from bridges.web_search.client import WebSearchError
from bridges.web_search.contracts import WebSearchResult, WebSearchVerification
from bridges.web_search.service import WebSearchService
from tests.chat.test_v2_03_conversation_context import _record
from tests.chat.test_v2_05_photo_attachments import PNG_BYTES, _app, _register, _upload_draft
from tests.chat.test_v2_17_study_pages import StudyGateway, _first
from tests.knowledge_base.test_knowledge_base_api import _upload, _worker_service


class TutorGateway(StudyGateway):
    def __init__(self) -> None:
        super().__init__(page_numbers=[12])
        self.tutor_payloads: list[dict[str, Any]] = []
        self.fail_tutor = False
        self.answer: dict[str, Any] | None = None
        self.extra_sources = False

    def invoke(
        self, capability: str, version: str, context: Any, payload: dict[str, Any], **kwargs: Any
    ) -> ModelCallResult:
        if payload.get("task") == "study.tutor":
            self.tutor_payloads.append(payload)
            if self.fail_tutor:
                return ModelCallResult(status=ModelCallStatus.BLOCKED, error_code="timeout")
            if self.answer is not None:
                return ModelCallResult(status=ModelCallStatus.SUCCESS, output=self.answer)
            sources = payload["sources"]
            return ModelCallResult(
                status=ModelCallStatus.SUCCESS,
                output={
                    "parts": [
                        {
                            "text": "a 就是斜率，x 每增加 1，y 就增加 a。",
                            "source_ids": [sources[0]["source_id"]],
                            "kind": "page",
                        },
                        *(
                            [
                                {
                                    "text": "这是额外解释。",
                                    "kind": source["kind"],
                                    "source_ids": [source["source_id"]],
                                }
                                for source in sources
                                if source["kind"] != "page"
                            ]
                            if self.extra_sources
                            else []
                        ),
                    ],
                    "gap": "",
                },
            )
        return super().invoke(capability, version, context, payload, **kwargs)


def test_question_uses_page_evidence_and_persists_one_exchange(
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = TutorGateway()
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        _register(client, "tutorowner")
        photo = _upload_draft(client).json()
        first = _first(client, [photo["object_id"]])
        conversation_id = first.json()["conversation"]["conversation_id"]
        app.state.generation_executor.run_tick()
        endpoint = f"/chat/conversations/{conversation_id}"
        question = {
            "content": "第12页 y=ax+b 里的 a 是什么意思？",
            "idempotency_key": "tutor-question-1",
        }
        response = client.post(endpoint + "/messages", json=question)
        assert response.status_code == 200, response.text
        app.state.generation_executor.run_tick()
        result = client.get(endpoint).json()
        assert result["messages"][-1]["status"] == "done", result["messages"][-1]
        assert "书上第12页" in result["messages"][-1]["content"]
        assert "y=ax+b" in result["messages"][-1]["content"]
        assert result["study"]["stage"] == "tutoring"
        exchange = result["study"]["tutoring"][0]
        assert exchange["question"] == question["content"]
        assert exchange["sources"][0]["object_id"] == photo["object_id"]
        assert exchange["sources"][0]["snippet"] == "y=ax+b"
        assert "data:image" not in str(gateway.tutor_payloads)
        assert client.post(endpoint + "/messages", json=question).status_code == 200
        app.state.generation_executor.run_tick()
        assert len(client.get(endpoint).json()["study"]["tutoring"]) == 1
        _register(client, "tutorother")
        assert client.get(endpoint).status_code == 404


def _start(client: TestClient, app: Any) -> str:
    _register(client, "tutorcase")
    photo = _upload_draft(client).json()
    first = _first(client, [photo["object_id"]])
    assert first.status_code == 201, first.text
    app.state.generation_executor.run_tick()
    return f"/chat/conversations/{first.json()['conversation']['conversation_id']}"


def _ask(
    client: TestClient, app: Any, endpoint: str, content: str, **fields: Any
) -> dict[str, Any]:
    response = client.post(endpoint + "/messages", json={"content": content, **fields})
    assert response.status_code == 200, response.text
    app.state.generation_executor.run_tick()
    return client.get(endpoint).json()


def _retry(client: TestClient, app: Any, endpoint: str) -> dict[str, Any]:
    message = client.get(endpoint).json()["messages"][-1]
    response = client.post(
        endpoint + f"/messages/{message['message_id']}/retry",
        json={"idempotency_key": f"retry-{message['message_id']}"},
    )
    assert response.status_code == 200, response.text
    app.state.generation_executor.run_tick()
    return client.get(endpoint).json()


@pytest.mark.parametrize("failure", ["timeout", "invented_source", "wrong_kind"])
def test_failed_answer_retry_saves_only_successful_exchange(
    tmp_path: Any,
    monkeypatch: Any,
    failure: str,
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = TutorGateway()
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        endpoint = _start(client, app)
        if failure == "timeout":
            gateway.fail_tutor = True
        else:
            source_id = client.get(endpoint).json()["study"]["pages"][0]["fragments"][0][
                "fragment_id"
            ]
            gateway.answer = {
                "parts": [
                    {
                        "text": "不可发布的回答",
                        "kind": "page" if failure == "invented_source" else "web",
                        "source_ids": ["invented" if failure == "invented_source" else source_id],
                    }
                ]
            }
        failed = _ask(client, app, endpoint, "a 是什么意思？")
        assert failed["messages"][-1]["status"] == "error"
        assert failed["study"]["stage"] == "tutoring"
        assert failed["study"]["tutoring"] == []
        gateway.fail_tutor = False
        gateway.answer = None
        result = _retry(client, app, endpoint)
        assert result["messages"][-1]["status"] == "done", result["messages"][-1]
        assert len(result["study"]["tutoring"]) == 1
        assert len(result["study"]["pages"]) == 1
        assert len([message for message in result["messages"] if message["role"] == "user"]) == 2


def test_missing_page_requests_supplement_and_labels_model_knowledge(
    tmp_path: Any,
    monkeypatch: Any,
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
        answer = result["messages"][-1]["content"]
        assert "书页缺口" in answer and "请补拍" in answer
        assert "模型知识补充" in answer
        assert result["study"]["tutoring"][0]["sources"] == []
        assert result["study"]["tutoring"][0]["gap"] == gateway.answer["gap"]


def test_appended_section_waits_for_exact_confirmation_and_keeps_history(
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = TutorGateway()
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        endpoint = _start(client, app)
        previous = _ask(client, app, endpoint, "a 是什么意思？")["study"]
        gateway.page_numbers = [12, 13]
        gateway.same_section = False
        photo = _upload_draft(client, upload_id="append", content=PNG_BYTES + b"-new").json()
        result = _ask(client, app, endpoint, "追加一页", attachment_ids=[photo["object_id"]])
        assert result["study"]["stage"] == "tutoring"
        assert result["study"]["pages"] == previous["pages"]
        assert result["study"]["units"] == previous["units"]
        assert len(result["study"]["page_update"]["pages"]) == 2
        for text in ["第2页整页是热力学", "确认第2页属于本节吗？"]:
            result = _ask(client, app, endpoint, text)
            assert len(result["study"]["pages"]) == 1
            assert result["study"]["tutoring"] == previous["tutoring"]
        result = _ask(client, app, endpoint, "确认第2页属于本节")
        assert result["messages"][-1]["status"] == "done"
        assert result["study"]["page_update"] is None
        assert len(result["study"]["pages"]) == 2
        assert result["study"]["tutoring"] == previous["tutoring"]
        assert "已更新本节书页" in result["messages"][-1]["content"]
        assert "暂不需要作答" not in result["messages"][-1]["content"]
        assert gateway.vision_count == 2


@pytest.mark.parametrize("failure", ["ocr", "map"])
def test_append_retry_preserves_scope_and_never_duplicates_pages(
    tmp_path: Any,
    monkeypatch: Any,
    failure: str,
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = TutorGateway()
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        endpoint = _start(client, app)
        previous = _ask(client, app, endpoint, "a 是什么意思？")["study"]
        gateway.page_numbers = [12, 13]
        gateway.fail_ocr = failure == "ocr"
        gateway.fail_map = failure == "map"
        photo = _upload_draft(client, upload_id="retry-page", content=PNG_BYTES + b"-2").json()
        result = _ask(client, app, endpoint, "追加一页", attachment_ids=[photo["object_id"]])
        assert result["messages"][-1]["status"] == "error"
        assert result["study"]["pages"] == previous["pages"]
        assert result["study"]["units"] == previous["units"]
        assert result["study"]["tutoring"] == previous["tutoring"]
        gateway.fail_ocr = gateway.fail_map = False
        result = _retry(client, app, endpoint)
        assert result["messages"][-1]["status"] == "done", result["messages"][-1]
        assert len(result["study"]["pages"]) == 2
        assert result["study"]["page_update"] is None
        assert result["study"]["tutoring"] == previous["tutoring"]
        assert gateway.vision_count == 2


def test_kb_supplement_uses_only_current_account_and_respects_opt_out(
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = TutorGateway()
    gateway.extra_sources = True
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        _register(client, "tutorforeign")
        assert (
            _upload(
                client, "别人的笔记.txt", "线性函数斜率是变化率，他人私有材料".encode()
            ).status_code
            == 201
        )
        _worker_service(app).process_pending()
        endpoint = _start(client, app)
        own = _upload(client, "我的笔记.txt", "线性函数斜率是变化率，本人补充材料".encode())
        assert own.status_code == 201, own.text
        _worker_service(app).process_pending()
        app.state.chat_service._retrieval = LayeredRetrievalService(
            database=app.state.bridges_database,
            object_repository=app.state.object_repository,
        )
        result = _ask(client, app, endpoint, "结合知识库笔记解释线性函数斜率")
        assert result["messages"][-1]["status"] == "done", result["messages"][-1]
        assert "知识库补充" in result["messages"][-1]["content"]
        sources = gateway.tutor_payloads[-1]["sources"]
        kb_sources = [source for source in sources if source["kind"] == "knowledge_base"]
        assert kb_sources and all(
            source["object_id"] == own.json()["object_id"] for source in kb_sources
        )
        assert "他人私有材料" not in str(gateway.tutor_payloads)
        result = _ask(
            client, app, endpoint, "结合知识库笔记解释线性函数斜率", use_knowledge_base=False
        )
        assert result["messages"][-1]["status"] == "done"
        assert all(source["kind"] == "page" for source in gateway.tutor_payloads[-1]["sources"])


class SearchClient:
    def __init__(self, fail: bool = False) -> None:
        self.queries: list[str] = []
        self.fail = fail

    def search(self, query: str, **kwargs: Any) -> list[WebSearchResult]:
        self.queries.append(query)
        if self.fail:
            raise WebSearchError("web_search_timeout", "搜索超时", retryable=False)
        return [
            WebSearchResult(
                result_id="web-1",
                title="斜率资料",
                site="example.org",
                url="https://example.org/slope",
                snippet="斜率表示变化率。",
                accessed_at=datetime.now(UTC),
                verification=WebSearchVerification.SUMMARY_ONLY,
            )
        ]


@pytest.mark.parametrize("fail", [False, True])
def test_web_supplement_is_labeled_and_failure_preserves_stage(
    tmp_path: Any,
    monkeypatch: Any,
    fail: bool,
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = TutorGateway()
    gateway.extra_sources = True
    app.state.chat_service._gateway = gateway
    search = SearchClient(fail=fail)
    app.state.chat_service._web_search = WebSearchService(client=search, sleeper=lambda _: None)
    with TestClient(app) as client:
        endpoint = _start(client, app)
        result = _ask(client, app, endpoint, "联网搜索斜率的应用。我的邮箱是 a@example.com。")
        assert search.queries and all("example.com" not in query for query in search.queries)
        assert all("y=ax+b" not in query for query in search.queries)
        assert result["study"]["stage"] == "tutoring"
        if fail:
            assert result["messages"][-1]["status"] == "error"
            assert result["study"]["tutoring"] == []
        else:
            assert result["messages"][-1]["status"] == "done", result["messages"][-1]
            assert "联网补充" in result["messages"][-1]["content"]
            assert "https://example.org/slope" in result["messages"][-1]["content"]
            assert "仅搜索摘要" in result["messages"][-1]["content"]
            assert any(
                source["kind"] == "web" for source in result["study"]["tutoring"][0]["sources"]
            )


def test_shared_budget_keeps_relevant_formula_and_drops_optional_material() -> None:
    compiled = compile_turn_context(
        messages=[_record("q1", ChatMessageRole.USER, "解释 y=ax+b")],
        current_user_message_id="q1",
        model_id=None,
        mode=ChatMode.STUDY,
        context_window=2400,
        system_prompt="只依据书页回答，补充另行标注。",
        evidence=[
            ContextEvidence("page-1", "第12页：y=ax+b"),
            ContextEvidence("kb-1", "不相关的历史补充" * 500),
        ],
    )
    assert compiled.adopted_evidence_ids == ["page-1"]
    assert compiled.budget_floor_exceeded is False
    assert compiled.input_token_estimate <= compiled.input_budget_tokens
    assert compiled.messages[-1]["content"] == "解释 y=ax+b"
    assert any(message["content"] == "第12页：y=ax+b" for message in compiled.messages)


def test_stop_during_tutoring_does_not_save_an_exchange(tmp_path: Any, monkeypatch: Any) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = TutorGateway()
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        endpoint = _start(client, app)
        response = client.post(endpoint + "/messages", json={"content": "解释斜率"})
        assistant_id = response.json()["assistant_message"]["message_id"]
        original = gateway.invoke

        def stopping_invoke(*args: Any, **kwargs: Any) -> ModelCallResult:
            result = original(*args, **kwargs)
            if kwargs.get("payload", {}).get("task") == "study.tutor":
                assert client.post(endpoint + f"/messages/{assistant_id}/stop").status_code == 200
            return result

        monkeypatch.setattr(gateway, "invoke", stopping_invoke)
        app.state.generation_executor.run_tick()
        result = client.get(endpoint).json()
        assert result["messages"][-1]["status"] == "stopped"
        assert result["study"]["stage"] == "tutoring"
        assert result["study"]["tutoring"] == []
