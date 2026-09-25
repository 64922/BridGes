"""学习书页首轮、页级证据、补拍等待与账户隔离。"""

from __future__ import annotations

import json
import re
from typing import Any

from fastapi.testclient import TestClient

from bridges.contracts.ai import ModelCallResult, ModelCallStatus
from tests.chat.test_v2_05_photo_attachments import PNG_BYTES, _app, _register, _upload_draft


class StudyGateway:
    def __init__(
        self, *, unclear: bool = False, fail_ocr: bool = False, fail_preview: bool = False
    ) -> None:
        self.unclear = unclear
        self.fail_ocr = fail_ocr
        self.fail_preview = fail_preview
        self.vision_count = 0
        self.calls: list[str] = []

    def invoke(
        self,
        capability: str,
        version: str,
        context: Any,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> ModelCallResult:
        del version, context, kwargs
        self.calls.append(capability)
        if capability == "qwen_ocr":
            if self.fail_ocr:
                return ModelCallResult(status=ModelCallStatus.BLOCKED, error_code="ocr_failed")
            return ModelCallResult(
                status=ModelCallStatus.SUCCESS, output={"content": "线性函数 y=ax+b"}
            )
        if capability == "qwen_vision":
            self.vision_count += 1
            return ModelCallResult(
                status=ModelCallStatus.SUCCESS,
                output={
                    "content": json.dumps(
                        {
                            "same_section": True,
                            "fragments": [
                                {
                                    "kind": "formula",
                                    "position": "中部公式",
                                    "text": "y=ax+b",
                                    "confidence": 0.9,
                                }
                            ],
                            "unclear": (
                                [{"position": "中部公式", "reason": "参数 a 模糊"}]
                                if self.unclear
                                else []
                            ),
                        },
                        ensure_ascii=False,
                    )
                },
            )
        if capability == "qwen_structured_output":
            if '"questions"' not in payload["prompt"]:
                refs = re.findall(r'"id":\s*"([^"]+:1)"', payload["prompt"])
                return ModelCallResult(
                    status=ModelCallStatus.SUCCESS,
                    output={
                        "units": [
                            {
                                "title": "线性函数",
                                "fragment_ids": refs,
                                "core": True,
                            }
                        ]
                    },
                )
            if self.fail_preview:
                return ModelCallResult(
                    status=ModelCallStatus.BLOCKED, error_code="preview_failed"
                )
            return ModelCallResult(
                status=ModelCallStatus.SUCCESS,
                output={
                    "questions": [
                        {
                            "question": "斜率如何影响图像？",
                            "unit_titles": ["线性函数"],
                        }
                    ]
                },
            )
        raise AssertionError(capability)


def _first(client: TestClient, attachments: list[str], key: str = "study-first-1") -> Any:
    return client.post(
        "/chat/first-turn",
        json={
            "mode": "study",
            "content": "",
            "attachment_ids": attachments,
            "idempotency_key": key,
        },
    )


def test_study_needs_photo_and_rejects_daily_module(tmp_path: Any, monkeypatch: Any) -> None:
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        _register(client, "studyphoto")
        text_only = client.post(
            "/chat/first-turn",
            json={"mode": "study", "content": "先讲这个", "idempotency_key": "study-text-1"},
        )
        assert text_only.status_code == 422
        draft = _upload_draft(client, upload_id="study-a").json()
        mixed = client.post(
            "/chat/first-turn",
            json={
                "mode": "study",
                "content": "",
                "attachment_ids": [draft["object_id"]],
                "module_id": "paper",
                "idempotency_key": "study-mixed-1",
            },
        )
        assert mixed.status_code == 422
        assert client.get("/chat/conversations").json()["conversations"] == []


def test_two_pages_preview_survives_reload_and_is_private(tmp_path: Any, monkeypatch: Any) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = StudyGateway()
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        _register(client, "studyowner")
        a = _upload_draft(client, upload_id="study-page-a").json()
        b = _upload_draft(client, upload_id="study-page-b", content=PNG_BYTES + b"-second").json()
        first = _first(client, [a["object_id"], b["object_id"]])
        assert first.status_code == 201, first.text
        conversation_id = first.json()["conversation"]["conversation_id"]
        assert first.json()["conversation"]["mode_locked"] is True
        app.state.generation_executor.run_tick()
        reloaded = client.get(f"/chat/conversations/{conversation_id}")
        assert reloaded.status_code == 200
        study = reloaded.json()["study"]
        assert study is not None, (
            gateway.calls,
            app.state.generation_executor._last_summary,
            reloaded.json()["messages"][-1],
        )
        assert study["stage"] == "tutoring", (
            gateway.calls,
            reloaded.json()["messages"][-1]["error_code"],
            reloaded.json()["messages"][-1]["error_message"],
        )
        assert [page["ordinal"] for page in study["pages"]] == [1, 2]
        assert study["questions"][0]["question"] == "斜率如何影响图像？"
        assert "暂不需要作答" in reloaded.json()["messages"][-1]["content"]
        assert gateway.calls == [
            "qwen_ocr",
            "qwen_vision",
            "qwen_ocr",
            "qwen_vision",
            "qwen_structured_output",
            "qwen_structured_output",
        ]
        _register(client, "studyother")
        assert client.get(f"/chat/conversations/{conversation_id}").status_code == 404


def test_unclear_page_waits_and_user_text_keeps_source(tmp_path: Any, monkeypatch: Any) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = StudyGateway(unclear=True)
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        _register(client, "studyunclear")
        draft = _upload_draft(client, upload_id="study-unclear").json()
        first = _first(client, [draft["object_id"]], "study-unclear-first")
        assert first.status_code == 201, first.text
        conversation_id = first.json()["conversation"]["conversation_id"]
        app.state.generation_executor.run_tick()
        waiting = client.get(f"/chat/conversations/{conversation_id}").json()["study"]
        assert waiting["stage"] == "awaiting_pages"
        assert waiting["wait_reason"] == "unclear_page"
        assert gateway.calls == ["qwen_ocr", "qwen_vision"]
        supplement = client.post(
            f"/chat/conversations/{conversation_id}/messages",
            json={"content": "第1页中部公式是 y=ax+b", "idempotency_key": "study-supplement"},
        )
        assert supplement.status_code == 200, supplement.text
        app.state.generation_executor.run_tick()
        final = client.get(f"/chat/conversations/{conversation_id}").json()["study"]
        assert final["stage"] == "tutoring"
        assert any(fragment["source"] == "user" for fragment in final["pages"][0]["fragments"])


def test_ocr_failure_does_not_publish_preview(tmp_path: Any, monkeypatch: Any) -> None:
    app = _app(tmp_path, monkeypatch)
    app.state.chat_service._gateway = StudyGateway(fail_ocr=True)
    with TestClient(app) as client:
        _register(client, "studyfailure")
        draft = _upload_draft(client, upload_id="study-fail").json()
        first = _first(client, [draft["object_id"]], "study-fail-first")
        assert first.status_code == 201, first.text
        conversation_id = first.json()["conversation"]["conversation_id"]
        app.state.generation_executor.run_tick()
        projection = client.get(f"/chat/conversations/{conversation_id}").json()
        assert projection["study"]["stage"] == "recognizing"
        assert projection["study"]["questions"] == []
        assert projection["messages"][-1]["status"] == "error"


def test_retake_replaces_unclear_page_and_keeps_revision(tmp_path: Any, monkeypatch: Any) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = StudyGateway(unclear=True)
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        _register(client, "studyretake")
        first_draft = _upload_draft(client, upload_id="retake-old").json()
        first = _first(client, [first_draft["object_id"]], "retake-first")
        assert first.status_code == 201, first.text
        conversation_id = first.json()["conversation"]["conversation_id"]
        app.state.generation_executor.run_tick()
        gateway.unclear = False
        new_draft = _upload_draft(
            client, upload_id="retake-new", content=PNG_BYTES + b"-clear"
        ).json()
        retake = client.post(
            f"/chat/conversations/{conversation_id}/messages",
            json={
                "content": "补拍第1页",
                "attachment_ids": [new_draft["object_id"]],
                "idempotency_key": "retake-second",
            },
        )
        assert retake.status_code == 200, retake.text
        app.state.generation_executor.run_tick()
        state = client.get(f"/chat/conversations/{conversation_id}").json()["study"]
        assert state["stage"] == "tutoring"
        assert len(state["pages"]) == 1
        assert state["pages"][0]["object_id"] == new_draft["object_id"]
        assert state["pages"][0]["replaced_object_ids"] == [first_draft["object_id"]]


def test_preview_failure_retries_without_reupload(tmp_path: Any, monkeypatch: Any) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = StudyGateway(fail_preview=True)
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        _register(client, "studypreviewretry")
        draft = _upload_draft(client, upload_id="preview-retry-photo").json()
        first = _first(client, [draft["object_id"]], "preview-retry-first")
        assert first.status_code == 201, first.text
        conversation_id = first.json()["conversation"]["conversation_id"]
        app.state.generation_executor.run_tick()
        failed = client.get(f"/chat/conversations/{conversation_id}").json()
        assert failed["study"]["stage"] == "preview"
        assert failed["messages"][-1]["status"] == "error"
        gateway.fail_preview = False
        retry = client.post(
            f"/chat/conversations/{conversation_id}/messages/"
            f"{failed['messages'][-1]['message_id']}/retry",
            json={"idempotency_key": "preview-retry-attempt"},
        )
        assert retry.status_code == 200, retry.text
        app.state.generation_executor.run_tick()
        reloaded = client.get(f"/chat/conversations/{conversation_id}").json()
        assert reloaded["study"]["stage"] == "tutoring"
