"""学习终态结果必须冻结，重新规划与状态替换不得改写历史交付。"""

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

import bridges.chat  # noqa: F401 - 先初始化既有聊天/学习组合根
from bridges.contracts.study import StudyState
from bridges.study.service import StudyRepository
from tests.chat.test_improvement36_evidence_bound_summary import Ticket36Gateway
from tests.chat.test_v2_05_photo_attachments import PNG_BYTES, _app, _upload_draft
from tests.chat.test_v2_18_study_tutoring import TutorGateway, _ask, _start


@pytest.mark.parametrize("summary_failure", [False, True])
def test_study_history_is_immutable_after_domain_state_replacement(
    tmp_path: Any, monkeypatch: Any, generation_helpers: dict[str, Any], summary_failure: bool,
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = Ticket36Gateway()
    gateway.question_count = 1
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        endpoint = _start(client, app)
        _ask(client, app, endpoint, "学完了")
        gateway.fail_summary = "timeout" if summary_failure else None
        result = _ask(client, app, endpoint, "a 是斜率")
        message = result["messages"][-1]
        expected = message["turn_result"]
        assert expected["delivered"]
        assert any(block["label"] == "学习总结" for block in expected["delivered"]) is (
            not summary_failure
        )
        account_id = client.get("/auth/session").json()["account"]["id"]
        conversation_id = endpoint.rsplit("/", 1)[-1]
        # 仓库领域状态是可替换写模型；只读历史应取本条终态冻结值。
        StudyRepository(app.state.bridges_database).save(
            account_id, conversation_id, StudyState(subsection_id="new-subsection"),
        )
        reloaded = client.get(endpoint).json()["messages"][-1]
        assert reloaded["turn_result"] == expected
        assert reloaded["content"] == message["content"]
        stored = app.state.chat_service._repo.get_message(account_id, message["message_id"])
        assert stored.turn_result == expected
        events = generation_helpers["subscribe"](client, conversation_id, message["message_id"])
        done = [payload["message"] for kind, payload in events if kind == "done"]
        if done:
            assert done[-1]["turn_result"] == expected
        assert "core_points" not in json.dumps(expected)


def test_real_append_and_replan_keep_old_judgement_result(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = Ticket36Gateway()
    gateway.question_count = 1
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        endpoint = _start(client, app)
        _ask(client, app, endpoint, "学完了")
        before = _ask(client, app, endpoint, "a 是斜率")
        original = before["messages"][-1]
        assert original["turn_result"]["delivered"]
        gateway.page_numbers = [12, 13]
        draft = _upload_draft(client, upload_id="issue38-snapshot-append",
                              content=PNG_BYTES + b"-appended").json()
        appended = _ask(client, app, endpoint, "漏拍了一页", attachment_ids=[draft["object_id"]])
        assert appended["messages"][-1]["status"] == "done"
        assert appended["study"]["review"]["needs_replan"]
        replanned = _ask(client, app, endpoint, "继续复盘")
        assert replanned["messages"][-1]["status"] == "done"
        # 既有重新规划保留已判题；只重排未问题，不能假定它删除旧判定。
        assert replanned["study"]["review"]["questions"][0]["judgement"] == "correct"
        old = next(message for message in replanned["messages"]
                   if message["message_id"] == original["message_id"])
        assert old["turn_result"] == original["turn_result"]


def test_snapshot_failure_rolls_back_learning_callback(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    from bridges.contracts.chat import ChatMessageStatus
    from bridges.study import turn_result as snapshots

    app = _app(tmp_path, monkeypatch)
    app.state.chat_service._gateway = TutorGateway()
    with TestClient(app) as client:
        endpoint = _start(client, app)
        original = snapshots.derive_turn_result

        def fail_done_snapshot(**kwargs: Any) -> Any:
            if kwargs["status"] is ChatMessageStatus.DONE:
                raise ValueError("模拟结果快照提交失败")
            return original(**kwargs)

        monkeypatch.setattr(snapshots, "derive_turn_result", fail_done_snapshot)
        failed = _ask(client, app, endpoint, "a 是什么意思？")
        message = failed["messages"][-1]
        assert message["status"] == "error"
        assert failed["study"]["tutoring"] == []
        assert message["turn_result"]["outcome"] == "failed"
        assert not message["turn_result"]["delivered"]


def test_explicit_continue_summary_has_qualified_summary_without_current_feedback(
    tmp_path: Any, monkeypatch: Any,
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
        gateway.fail_summary = None
        recovered = _ask(client, app, endpoint, "继续复盘")
        message = recovered["messages"][-1]
        assert message["status"] == "done"
        assert message["turn_result"]["trust"] == "qualified"
        assert message["turn_result"]["trust_label"]
        assert [block["label"] for block in message["turn_result"]["delivered"]] == ["学习总结"]


def test_unknown_learning_error_never_publishes_node_or_provider_secret(
    tmp_path: Any, monkeypatch: Any, generation_helpers: dict[str, Any],
) -> None:
    from bridges.study import service as workflow

    app = _app(tmp_path, monkeypatch)
    app.state.chat_service._gateway = TutorGateway()
    with TestClient(app) as client:
        endpoint = _start(client, app)

        def fail_provider(*args: Any, **kwargs: Any) -> Any:
            raise workflow.StudyWorkflowError("study.tutor", "unknown_vendor_failure",
                                               "token=provider-secret")

        monkeypatch.setattr(workflow, "tutor", fail_provider)
        failed = _ask(client, app, endpoint, "a 是什么意思？")
        message = failed["messages"][-1]
        assert message["status"] == "error"
        assert "本轮学习处理未完成" in message["error_message"]
        assert "study.tutor" not in message["error_message"]
        events = generation_helpers["subscribe"](
            client, endpoint.rsplit("/", 1)[-1], message["message_id"],
        )
        public = json.dumps(events, ensure_ascii=False)
        assert "provider-secret" not in public
        assert not any("study.tutor" in payload.get("error_message", "")
                       for _, payload in events)
