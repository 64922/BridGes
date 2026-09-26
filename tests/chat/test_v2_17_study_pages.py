"""学习书页首轮、页级证据、补拍等待与账户隔离。"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.contracts.ai import ModelCallResult, ModelCallStatus
from tests.chat.test_v2_05_photo_attachments import PNG_BYTES, _app, _register, _upload_draft


class StudyGateway:
    def __init__(
        self,
        *,
        unclear: bool = False,
        extra_unclear: bool = False,
        low_confidence_kind: str | None = None,
        fail_ocr: bool = False,
        fail_preview: bool = False,
        page_numbers: list[int] | None = None,
        same_section: bool = True,
        fail_map: bool = False,
    ) -> None:
        self.unclear = unclear
        self.extra_unclear = extra_unclear
        self.low_confidence_kind = low_confidence_kind
        self.fail_ocr = fail_ocr
        self.fail_preview = fail_preview
        self.vision_count = 0
        self.calls: list[str] = []
        self.page_numbers = page_numbers
        self.same_section = same_section
        self.fail_map = fail_map

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
                            "same_section": self.same_section,
                            "page_number": (
                                self.page_numbers[self.vision_count - 1]
                                if self.page_numbers else None
                            ),
                            "fragments": [
                                {
                                    "kind": self.low_confidence_kind or "formula",
                                    "position": "中部公式",
                                    "text": "y=ax+b",
                                    "confidence": 0.5 if self.low_confidence_kind else 0.9,
                                }
                            ],
                            "unclear": (
                                [
                                    {"position": "中部公式", "reason": "参数 a 模糊"},
                                    *(
                                        [{"position": "右侧图表", "reason": "坐标模糊"}]
                                        if self.extra_unclear
                                        else []
                                    ),
                                ]
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
                if self.fail_map:
                    return ModelCallResult(status=ModelCallStatus.BLOCKED, error_code="map_failed")
                refs = re.findall(r'"id":\s*"([^"]+)"', payload["prompt"])
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
        assert client.get(
            f"/chat/conversations/{conversation_id}/attachments/{a['object_id']}/content"
        ).status_code == 404


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


def test_multiple_unclear_positions_are_resolved_one_at_a_time(
    tmp_path: Any, monkeypatch: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    app.state.chat_service._gateway = StudyGateway(unclear=True, extra_unclear=True)
    with TestClient(app) as client:
        _register(client, "studymultiunclear")
        draft = _upload_draft(client, upload_id="multi-unclear").json()
        first = _first(client, [draft["object_id"]], "multi-unclear-first")
        conversation_id = first.json()["conversation"]["conversation_id"]
        app.state.generation_executor.run_tick()
        supplement = client.post(
            f"/chat/conversations/{conversation_id}/messages",
            json={"content": "第1页中部公式是 y=ax+b", "idempotency_key": "multi-formula"},
        )
        assert supplement.status_code == 200, supplement.text
        app.state.generation_executor.run_tick()
        waiting = client.get(f"/chat/conversations/{conversation_id}").json()["study"]
        assert waiting["stage"] == "awaiting_pages"
        assert [issue["position"] for issue in waiting["pages"][0]["unclear"]] == ["右侧图表"]
        supplement = client.post(
            f"/chat/conversations/{conversation_id}/messages",
            json={"content": "第1页右侧图表横轴是时间", "idempotency_key": "multi-chart"},
        )
        assert supplement.status_code == 200, supplement.text
        app.state.generation_executor.run_tick()
        study = client.get(f"/chat/conversations/{conversation_id}").json()["study"]
        assert study["stage"] == "tutoring"


def test_low_confidence_text_waits_for_confirmation(tmp_path: Any, monkeypatch: Any) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = StudyGateway(low_confidence_kind="text")
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        _register(client, "studylowtext")
        draft = _upload_draft(client, upload_id="low-text").json()
        first = _first(client, [draft["object_id"]], "low-text-first")
        conversation_id = first.json()["conversation"]["conversation_id"]
        app.state.generation_executor.run_tick()
        study = client.get(f"/chat/conversations/{conversation_id}").json()["study"]
        assert study["stage"] == "awaiting_pages"
        assert study["pages"][0]["unclear"][0]["position"] == "中部公式"
        assert gateway.calls == ["qwen_ocr", "qwen_vision"]


def test_mixed_duplicate_page_is_reported(tmp_path: Any, monkeypatch: Any) -> None:
    app = _app(tmp_path, monkeypatch)
    app.state.chat_service._gateway = StudyGateway()
    with TestClient(app) as client:
        _register(client, "studyduplicate")
        first_draft = _upload_draft(client, upload_id="duplicate-first").json()
        first = _first(client, [first_draft["object_id"]], "duplicate-first-turn")
        conversation_id = first.json()["conversation"]["conversation_id"]
        app.state.generation_executor.run_tick()
        repeated = _upload_draft(client, upload_id="duplicate-repeated").json()
        new_page = _upload_draft(
            client, upload_id="duplicate-new", content=PNG_BYTES + b"-new"
        ).json()
        next_turn = client.post(
            f"/chat/conversations/{conversation_id}/messages",
            json={
                "content": "",
                "attachment_ids": [repeated["object_id"], new_page["object_id"]],
                "idempotency_key": "duplicate-next-turn",
            },
        )
        assert next_turn.status_code == 200, next_turn.text
        app.state.generation_executor.run_tick()
        projection = client.get(f"/chat/conversations/{conversation_id}").json()
        assert len(projection["study"]["pages"]) == 2
        assert "重复书页" in projection["messages"][-1]["content"]


def test_files_cannot_start_or_extend_study_and_remain_drafts(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    app = _app(tmp_path, monkeypatch)
    app.state.chat_service._gateway = StudyGateway()
    with TestClient(app) as client:
        _register(client, "studyfile")
        document = _upload_draft(
            client, filename="notes.txt", upload_id="study-file", content=b"notes",
        ).json()
        rejected = _first(client, [document["object_id"]])
        assert rejected.status_code == 422
        assert client.get("/chat/conversations").json()["conversations"] == []
        photo = _upload_draft(client, upload_id="study-photo").json()
        first = _first(client, [photo["object_id"]], "study-photo-first")
        conversation_id = first.json()["conversation"]["conversation_id"]
        app.state.generation_executor.run_tick()
        rejected = client.post(
            f"/chat/conversations/{conversation_id}/messages",
            json={"content": "", "attachment_ids": [document["object_id"]]},
        )
        assert rejected.status_code == 422
        assert client.get(
            f"/chat/attachment-drafts/{document['object_id']}/content"
        ).status_code == 200


def test_appended_page_failure_preserves_confirmed_scope(tmp_path: Any, monkeypatch: Any) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = StudyGateway()
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        _register(client, "studyappendfail")
        photo = _upload_draft(client, upload_id="append-first").json()
        first = _first(client, [photo["object_id"]])
        conversation_id = first.json()["conversation"]["conversation_id"]
        app.state.generation_executor.run_tick()
        gateway.fail_ocr = True
        extra = _upload_draft(client, upload_id="append-extra", content=PNG_BYTES + b"2").json()
        client.post(
            f"/chat/conversations/{conversation_id}/messages",
            json={"content": "", "attachment_ids": [extra["object_id"]]},
        )
        app.state.generation_executor.run_tick()
        projection = client.get(f"/chat/conversations/{conversation_id}").json()
        assert projection["study"]["stage"] == "tutoring"
        assert projection["study"]["questions"]
        assert projection["study"]["units"]
        assert len(projection["study"]["pages"]) == 1
        assert projection["study"]["page_update"] is not None
        assert "暂不需要作答" in projection["messages"][1]["content"]


@pytest.mark.parametrize("content", [
    "第1页还是看不清", "第1页中部公式", "第1页右侧图表是直线", "第1页中部公式看不清",
    "第1页中部公式是不是 y=ax+b？", "第1页中部公式是啥？",
])
def test_unrelated_text_does_not_resolve_unclear_formula(
    tmp_path: Any, monkeypatch: Any, content: str,
) -> None:
    app = _app(tmp_path, monkeypatch)
    app.state.chat_service._gateway = StudyGateway(unclear=True)
    with TestClient(app) as client:
        _register(client, "studynotsupplement")
        photo = _upload_draft(client).json()
        first = _first(client, [photo["object_id"]])
        conversation_id = first.json()["conversation"]["conversation_id"]
        app.state.generation_executor.run_tick()
        client.post(f"/chat/conversations/{conversation_id}/messages", json={"content": content})
        app.state.generation_executor.run_tick()
        study = client.get(f"/chat/conversations/{conversation_id}").json()["study"]
        assert study["stage"] == "awaiting_pages"
        assert study["questions"] == []


def test_reversed_book_pages_wait_for_persisted_order_correction(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = StudyGateway(page_numbers=[12, 11])
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        _register(client, "studyorder")
        a = _upload_draft(client, upload_id="order-a").json()
        b = _upload_draft(client, upload_id="order-b", content=PNG_BYTES + b"b").json()
        first = _first(client, [a["object_id"], b["object_id"]])
        conversation_id = first.json()["conversation"]["conversation_id"]
        app.state.generation_executor.run_tick()
        state = client.get(f"/chat/conversations/{conversation_id}").json()["study"]
        assert state["wait_reason"] == "page_order"
        assert state["questions"] == []
        client.post(
            f"/chat/conversations/{conversation_id}/messages", json={"content": "页序：2,1"},
        )
        app.state.generation_executor.run_tick()
        state = client.get(f"/chat/conversations/{conversation_id}").json()["study"]
        assert state["stage"] == "tutoring"
        assert [page["object_id"] for page in state["pages"]] == [b["object_id"], a["object_id"]]
        assert [page["ordinal"] for page in state["pages"]] == [1, 2]
        assert [page["page_number"] for page in state["pages"]] == [11, 12]
        assert gateway.vision_count == 2


def test_order_correction_is_not_reapplied_after_map_failure(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = StudyGateway(page_numbers=[12, 11], fail_map=True)
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        _register(client, "studyorderretry")
        a = _upload_draft(client, upload_id="retry-a").json()
        b = _upload_draft(client, upload_id="retry-b", content=PNG_BYTES + b"b").json()
        first = _first(client, [a["object_id"], b["object_id"]])
        conversation_id = first.json()["conversation"]["conversation_id"]
        app.state.generation_executor.run_tick()
        client.post(
            f"/chat/conversations/{conversation_id}/messages", json={"content": "页序：2,1"},
        )
        app.state.generation_executor.run_tick()
        failed = client.get(f"/chat/conversations/{conversation_id}").json()
        assert failed["messages"][-1]["status"] == "error"
        gateway.fail_map = False
        retry = client.post(
            f"/chat/conversations/{conversation_id}/messages/"
            f"{failed['messages'][-1]['message_id']}/retry",
            json={"idempotency_key": "order-map-retry"},
        )
        assert retry.status_code == 200
        app.state.generation_executor.run_tick()
        study = client.get(f"/chat/conversations/{conversation_id}").json()["study"]
        assert study["stage"] == "tutoring"
        assert [page["page_number"] for page in study["pages"]] == [11, 12]


def test_multiline_formula_supplement_preserves_source(tmp_path: Any, monkeypatch: Any) -> None:
    app = _app(tmp_path, monkeypatch)
    app.state.chat_service._gateway = StudyGateway(unclear=True)
    with TestClient(app) as client:
        _register(client, "studymultiline")
        photo = _upload_draft(client).json()
        first = _first(client, [photo["object_id"]])
        conversation_id = first.json()["conversation"]["conversation_id"]
        app.state.generation_executor.run_tick()
        content = "第1页中部公式：y=ax+b\na=2，b=1"
        client.post(f"/chat/conversations/{conversation_id}/messages", json={"content": content})
        app.state.generation_executor.run_tick()
        study = client.get(f"/chat/conversations/{conversation_id}").json()["study"]
        assert study["stage"] == "tutoring"
        assert study["pages"][0]["fragments"][-1]["source"] == "user"
        assert study["pages"][0]["fragments"][-1]["text"] == content


def test_different_section_cannot_be_cleared_by_arbitrary_text(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = StudyGateway(same_section=False)
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        _register(client, "studysection")
        a = _upload_draft(client, upload_id="section-a").json()
        b = _upload_draft(client, upload_id="section-b", content=PNG_BYTES + b"b").json()
        first = _first(client, [a["object_id"], b["object_id"]])
        conversation_id = first.json()["conversation"]["conversation_id"]
        app.state.generation_executor.run_tick()
        client.post(
            f"/chat/conversations/{conversation_id}/messages",
            json={"content": "第2页整页是热力学"},
        )
        app.state.generation_executor.run_tick()
        state = client.get(f"/chat/conversations/{conversation_id}").json()["study"]
        assert state["stage"] == "awaiting_pages"
        assert state["questions"] == []


def test_stop_during_preview_does_not_advance_stage(tmp_path: Any, monkeypatch: Any) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = StudyGateway()
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        _register(client, "studystop")
        photo = _upload_draft(client).json()
        first = _first(client, [photo["object_id"]]).json()
        conversation_id = first["conversation"]["conversation_id"]
        assistant_id = first["assistant_message"]["message_id"]
        original = gateway.invoke

        def stopping_invoke(*args: Any, **kwargs: Any) -> ModelCallResult:
            result = original(*args, **kwargs)
            if '"questions"' in kwargs.get("payload", {}).get("prompt", ""):
                stopped = client.post(
                    f"/chat/conversations/{conversation_id}/messages/{assistant_id}/stop",
                )
                assert stopped.status_code == 200
            return result

        monkeypatch.setattr(gateway, "invoke", stopping_invoke)
        app.state.generation_executor.run_tick()
        projection = client.get(f"/chat/conversations/{conversation_id}").json()
        assert projection["messages"][-1]["status"] == "stopped"
        assert projection["study"]["stage"] == "preview"
        assert projection["study"]["questions"] == []
