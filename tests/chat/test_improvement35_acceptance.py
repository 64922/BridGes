"""工单 35 独立验收：候选写竞争、迟到提交和旧检查点合同。"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.contracts.study import StudyExchange
from tests.chat.test_improvement35_study_pause_and_appended_versions import Ticket35Gateway
from tests.chat.test_v2_05_photo_attachments import PNG_BYTES, _app, _upload_draft
from tests.chat.test_v2_18_study_tutoring import _ask, _retry, _start


@pytest.mark.parametrize("boundary", ["recognition", "mapping"])
def test_candidate_write_preserves_concurrent_effective_state(
    tmp_path: Any, monkeypatch: Any, boundary: str,
) -> None:
    from bridges.study.service import StudyRepository

    app = _app(tmp_path, monkeypatch)
    gateway = Ticket35Gateway()
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        endpoint = _start(client, app)
        account_id = client.get("/auth/session").json()["account"]["id"]
        conversation_id = endpoint.rsplit("/", 1)[-1]
        repository = StudyRepository(app.state.bridges_database)
        before = repository.get(account_id, conversation_id)
        assert before is not None
        original = gateway.invoke
        changed = False

        def concurrent(*args: Any, **kwargs: Any) -> Any:
            nonlocal changed
            result = original(*args, **kwargs)
            matches = (
                args[0] == "qwen_vision" if boundary == "recognition"
                else kwargs.get("payload", {}).get("task") == "study.map"
            )
            if matches and not changed:
                changed = True
                current = repository.get(account_id, conversation_id)
                assert current is not None
                current.tutoring.append(StudyExchange(
                    user_message_id="concurrent-user", assistant_message_id="concurrent-assistant",
                    question="并发提问", answer="必须保留的并发回答", sources=[],
                ))
                repository.save(account_id, conversation_id, current)
            return result

        monkeypatch.setattr(gateway, "invoke", concurrent)
        gateway.page_numbers = [12, 13]
        photo = _upload_draft(client, upload_id="race", content=PNG_BYTES + b"-2").json()
        result = _ask(client, app, endpoint, "追加一页", attachment_ids=[photo["object_id"]])
        assert changed
        assert result["messages"][-1]["error_code"] == "study_page_update_changed"
        state = repository.get(account_id, conversation_id)
        assert state is not None
        assert state.tutoring[-1].answer == "必须保留的并发回答"
        assert state.pages == before.pages and state.scope == before.scope


@pytest.mark.parametrize("change", ["stop", "lease"])
def test_append_final_transaction_rejects_late_authority_change(
    tmp_path: Any, monkeypatch: Any, change: str,
) -> None:
    from bridges.study import service as study_service

    app = _app(tmp_path, monkeypatch)
    gateway = Ticket35Gateway()
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        endpoint = _start(client, app)
        account_id = client.get("/auth/session").json()["account"]["id"]
        before = client.get(endpoint).json()["study"]
        original = study_service.finalize_message
        changed = False

        def late(*args: Any, **kwargs: Any) -> Any:
            nonlocal changed
            if kwargs.get("status").value == "done" and not changed:
                changed = True
                run = app.state.chat_service._repo.get_run_by_message(account_id, args[2])
                assert run is not None
                # 只设置持久停止，不依赖进程内 Event；模拟跨进程停止/租约转移。
                assignment = (
                    "stop_requested = 1" if change == "stop" else "lease_owner = 'new-worker'"
                )
                app.state.bridges_database.connection.execute(
                    f"UPDATE generation_runs SET {assignment} WHERE run_id = ?", (run.run_id,),
                )
            return original(*args, **kwargs)

        monkeypatch.setattr(study_service, "finalize_message", late)
        gateway.page_numbers = [12, 13]
        photo = _upload_draft(client, upload_id="late", content=PNG_BYTES + b"-2").json()
        result = _ask(client, app, endpoint, "追加一页", attachment_ids=[photo["object_id"]])
        assert changed
        assert result["study"]["pages"] == before["pages"]
        assert result["study"]["scope"] == before["scope"]
        assert result["messages"][-1]["status"] == ("stopped" if change == "stop" else "error")


def test_previous_graph_cannot_replay_pre_versioned_update(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = Ticket35Gateway()
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        endpoint = _start(client, app)
        account_id = client.get("/auth/session").json()["account"]["id"]
        before = client.get(endpoint).json()["study"]
        photo = _upload_draft(client, upload_id="old-graph", content=PNG_BYTES + b"-2").json()
        client.post(endpoint + "/messages", json={
            "content": "追加一页", "attachment_ids": [photo["object_id"]],
        })
        app.state.bridges_database.scoped(account_id).execute(
            "UPDATE generation_runs SET graph_version = 'study-tutoring-review-v5'"
            " WHERE account_id = ? AND conversation_id = ? AND status = 'queued'",
            (account_id, endpoint.rsplit("/", 1)[-1]),
        )
        app.state.generation_executor.run_tick()
        result = client.get(endpoint).json()
        assert result["messages"][-1]["error_code"] == "study_graph_version_changed"
        assert result["study"] == before
        assert gateway.vision_count == 1
        gateway.page_numbers = [12, 13]
        resumed = _retry(client, app, endpoint)
        assert resumed["messages"][-1]["status"] == "done", resumed["messages"][-1]
        assert len(resumed["study"]["pages"]) == 2


def test_summary_distinguishes_received_answer_from_unanswered() -> None:
    from bridges.contracts.study import StudyState, StudySummary
    from bridges.study.summary import render_summary

    state = StudyState.model_validate({
        "subsection_id": "section", "review": {"questions": [{
            "question_id": "received", "question": "已答题", "asked": True,
            "answer": "学生已提交的答案", "coverage_units": [], "fragment_ids": [],
        }, {
            "question_id": "unanswered", "question": "未答题", "asked": True,
            "unanswered": True, "coverage_units": [], "fragment_ids": [],
        }]},
    })
    summary = StudySummary.model_validate({"points": [{
        "kind": "gap", "text": "本次仍待确认的内容", "question_ids": ["received", "unanswered"],
    }]})
    rendered = render_summary(summary, state)
    assert "第1题「已答题」已作答（尚未判定，未计入掌握）" in rendered
    assert "第2题「未答题」未作答（已呈现，未计入掌握）" in rendered
