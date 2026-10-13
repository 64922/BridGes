"""Issue 02：纯文字学习问题走统一的聊天发送入口。"""

from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.ai.adapters import AdapterResult, RateLimitError, StreamChunk
from bridges.contracts.chat import ChatMode
from tests.chat.test_v2_05_photo_attachments import (
    _app,
    _gateway_with,
    _register,
)


class _TextTutorAdapter:
    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.payloads: list[dict[str, Any]] = []
        self.failure: Exception | None = None
        self.slow = False

    def call(self, capability: Any, run_context: Any, payload: dict[str, Any]) -> AdapterResult:
        self.payloads.append(payload)
        return AdapterResult(actual_model_id=capability.model_id, output={"content": self.answer})

    def stream_call(self, capability: Any, run_context: Any, payload: dict[str, Any]) -> Any:
        self.payloads.append(payload)
        if self.failure is not None:
            raise self.failure
        if self.slow:
            for character in self.answer:
                time.sleep(0.01)
                yield StreamChunk(kind="delta", delta=character)
        else:
            yield StreamChunk(kind="delta", delta=self.answer)
        yield StreamChunk(kind="done", actual_model_id=capability.model_id)


@pytest.mark.parametrize(
    ("question", "answer"),
    [
        ("互斥是不是独立？", "互斥表示不能同时发生；独立表示一个事件不改变另一个事件的概率。"),
        ("函数里的自变量是什么？", "自变量是输入，函数规则据此确定输出。"),
        ("编程里的递归怎么理解？", "递归是函数在处理问题时调用自身，并逐步走向终止条件。"),
    ],
)
def test_text_only_study_first_turn_is_atomic_and_idempotent(
    tmp_path: Any,
    monkeypatch: Any,
    generation_helpers: Any,
    question: str,
    answer: str,
) -> None:
    app = _app(tmp_path, monkeypatch)
    adapter = _TextTutorAdapter(answer)
    app.state.chat_service._gateway = _gateway_with(adapter)
    body = {
        "mode": ChatMode.STUDY.value,
        "content": question,
        "idempotency_key": "issue02-text-first-001",
    }

    try:
        with TestClient(app) as client:
            account = _register(client, "issue02-text-first")

            first = client.post("/chat/first-turn", json=body)

            assert first.status_code == 201, first.text
            conversation_id = first.json()["conversation"]["conversation_id"]
            assert first.json()["conversation"]["mode_locked"] is True

            replay = client.post("/chat/first-turn", json=body)

            assert replay.status_code == 200, replay.text
            assert replay.json()["idempotent_replay"] is True
            generation_helpers["drive"](app)

            detail = client.get(f"/chat/conversations/{conversation_id}").json()
            assert detail["messages"][-1]["status"] == "done"
            assert detail["messages"][-1]["content"] == answer
            assistant_message_id = detail["messages"][-1]["message_id"]
            run = app.state.chat_service._repo.get_run_by_message(
                account["id"], assistant_message_id
            )
            assert run is not None and run.graph_version == "study-local-tutor-v1"
            assert app.state.bridges_database.connection.execute(
                "SELECT COUNT(*) FROM study_local_progress"
            ).fetchone()[0] == 1
            assert app.state.bridges_database.connection.execute(
                "SELECT COUNT(*) FROM study_states WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()[0] == 0
            progress = detail["study_local_progress"]
            assert progress["topic"] == question
            assert progress["current_focus"] == question
            assert progress["knowledge_context"] == answer
            assert progress["version"] == 1
            assert progress["runtime_run_id"]
            assert len(progress["source_message_ids"]) == 2
            assert detail["messages"][-1]["teaching"] is None
            assert detail["messages"][-1]["web_search"] is None

            events = generation_helpers["subscribe"](
                client,
                conversation_id,
                detail["messages"][-1]["message_id"],
            )
            assert any(kind == "delta" for kind, _ in events)
            model_messages = adapter.payloads[-1]["messages"]
            assert model_messages[0]["role"] == "system"
            assert "可以直接讲解" in model_messages[0]["content"]
            assert "原始内部推理" in model_messages[0]["content"]
            assert model_messages[-1]["content"] == question

            followup = client.post(
                f"/chat/conversations/{conversation_id}/messages",
                json={"content": "能给一个反例吗？", "idempotency_key": "issue02-followup-001"},
            )
            assert followup.status_code == 200, followup.text
            generation_helpers["drive"](app)
            detail = client.get(f"/chat/conversations/{conversation_id}").json()
            assert detail["study_local_progress"]["version"] == 2
            assert detail["study_local_progress"]["topic"] == question
            assert detail["study_local_progress"]["current_focus"] == "能给一个反例吗？"
            assert detail["study_local_progress"]["runtime_run_id"] != progress["runtime_run_id"]
            assert question in str(adapter.payloads[-1]["messages"])
            assert answer in str(adapter.payloads[-1]["messages"])

            conversations = client.get("/chat/conversations").json()["conversations"]
            assert [item["conversation_id"] for item in conversations] == [conversation_id]
    finally:
        app.state.bridges_database.close()
        app.state.state_store.close()


def test_failed_turn_does_not_advance_progress_and_retry_commits_once(
    tmp_path: Any,
    monkeypatch: Any,
    generation_helpers: Any,
) -> None:
    app = _app(tmp_path, monkeypatch)
    adapter = _TextTutorAdapter("这是一次关于概率的解释。")
    app.state.chat_service._gateway = _gateway_with(adapter)

    try:
        with TestClient(app) as client:
            account = _register(client, "issue02-retry")
            first = client.post(
                "/chat/first-turn",
                json={
                    "mode": ChatMode.STUDY.value,
                    "content": "条件概率是什么？",
                    "idempotency_key": "issue02-retry-first",
                },
            )
            assert first.status_code == 201, first.text
            conversation_id = first.json()["conversation"]["conversation_id"]
            generation_helpers["drive"](app)

            adapter.failure = RateLimitError("temporary network interruption")
            failed = client.post(
                f"/chat/conversations/{conversation_id}/messages",
                json={
                    "content": "再给一个简单例子。",
                    "idempotency_key": "issue02-retry-followup",
                },
            )
            assert failed.status_code == 200, failed.text
            generation_helpers["drive"](app)
            failed_id = failed.json()["assistant_message"]["message_id"]
            detail = client.get(f"/chat/conversations/{conversation_id}").json()
            assert detail["messages"][-1]["status"] == "error"
            assert detail["study_local_progress"]["version"] == 1

            adapter.failure = None
            retried = client.post(
                f"/chat/conversations/{conversation_id}/messages/{failed_id}/retry"
            )
            assert retried.status_code == 200, retried.text
            generation_helpers["drive"](app)

            detail = client.get(f"/chat/conversations/{conversation_id}").json()
            assert detail["study_local_progress"]["version"] == 2
            assert detail["study_local_progress"]["runtime_run_id"] == retried.json()["run_id"]
            assert sum(message["role"] == "user" for message in detail["messages"]) == 2
            assert sum(message["role"] == "assistant" for message in detail["messages"]) == 3
            assert app.state.chat_service._repo.get_generation_run(
                account["id"], retried.json()["run_id"]
            ).graph_version == "study-local-tutor-v1"
    finally:
        app.state.bridges_database.close()
        app.state.state_store.close()


def test_stopped_turn_does_not_advance_local_progress(
    tmp_path: Any,
    monkeypatch: Any,
    generation_helpers: Any,
) -> None:
    app = _app(tmp_path, monkeypatch)
    adapter = _TextTutorAdapter("正在逐字生成的回答。" * 20)
    app.state.chat_service._gateway = _gateway_with(adapter)

    try:
        with TestClient(app) as client:
            _register(client, "issue02-stop")
            first = client.post(
                "/chat/first-turn",
                json={
                    "mode": ChatMode.STUDY.value,
                    "content": "函数的输入和输出是什么？",
                    "idempotency_key": "issue02-stop-first",
                },
            )
            assert first.status_code == 201, first.text
            conversation_id = first.json()["conversation"]["conversation_id"]
            adapter.answer = "已完成的回答。"
            generation_helpers["drive"](app)

            adapter.answer = "正在逐字生成的回答。" * 20
            adapter.slow = True
            stop_executor, executor_thread = generation_helpers["executor_thread"](app)
            control_client = TestClient(app)
            control_client.cookies.set(
                "bridges_session", client.cookies.get("bridges_session")
            )
            try:
                sent = client.post(
                    f"/chat/conversations/{conversation_id}/messages",
                    json={"content": "我再补充一点。", "idempotency_key": "issue02-stop-second"},
                )
                assert sent.status_code == 200, sent.text
                message_id = sent.json()["assistant_message"]["message_id"]
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    message = control_client.get(
                        f"/chat/conversations/{conversation_id}"
                    ).json()["messages"][-1]
                    if message["content"]:
                        break
                    time.sleep(0.02)
                stopped = control_client.post(
                    f"/chat/conversations/{conversation_id}/messages/{message_id}/stop"
                )
                assert stopped.status_code == 200, stopped.text
                assert stopped.json()["message"]["status"] == "stopped"
                detail = control_client.get(
                    f"/chat/conversations/{conversation_id}"
                ).json()
                assert detail["study_local_progress"]["version"] == 1
            finally:
                stop_executor.set()
                executor_thread.join(timeout=5)
                control_client.close()
    finally:
        app.state.bridges_database.close()
        app.state.state_store.close()
