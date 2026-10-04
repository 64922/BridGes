"""工单 34 独立验收：提交窗口、合法答案关联、证据与版本守卫。"""
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.contracts.chat import ChatMessageStatus
from tests.chat.test_improvement33_preverified_questions import (
    _raw_state,
    _single_unit_map,
    _start_review,
)
from tests.chat.test_improvement34_verified_grading_feedback import (
    Grade34Gateway,
    _two_question_plan,
)
from tests.chat.test_v2_05_photo_attachments import _app
from tests.chat.test_v2_18_study_tutoring import _ask, _retry


def test_received_answer_survives_failed_grading(tmp_path: Any, monkeypatch: Any) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = Grade34Gateway(["温度是表示物体冷热程度的物理量。"], map_fn=_single_unit_map)
    with TestClient(app) as client:
        account, endpoint = _start_review(client, app, gateway)
        _ask(client, app, endpoint, "开始复盘")
        gateway.fail_grade = "timeout"
        failed = _ask(client, app, endpoint, "冷热程度的量度")
        question = _raw_state(app, account["id"], endpoint).review.questions[0]
        assert failed["messages"][-1]["status"] == "error"
        assert question.answer == "冷热程度的量度"
        assert question.user_message_id == failed["messages"][-2]["message_id"]
        assert question.judgement is None and question.feedback is None


def test_presentation_and_feedback_survive_terminal_failure(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    from bridges.study import service as study_service
    app = _app(tmp_path, monkeypatch)
    gateway = Grade34Gateway(
        ["温度是表示物体冷热程度的物理量。"], map_fn=_single_unit_map, plan_fn=_two_question_plan,
    )
    with TestClient(app) as client:
        account, endpoint = _start_review(client, app, gateway)
        _ask(client, app, endpoint, "开始复盘")
        original = study_service.finalize_message
        def fail_done(*args: Any, **kwargs: Any) -> Any:
            if kwargs.get("status") == ChatMessageStatus.DONE:
                raise RuntimeError("模拟呈现提交后终态失败")
            return original(*args, **kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(study_service, "finalize_message", fail_done)
            failed = _ask(client, app, endpoint, "温度表示冷热程度")
        state = _raw_state(app, account["id"], endpoint)
        assert state.review.questions[1].asked
        assert "回答正确" in failed["messages"][-1]["content"]
        assert "复盘第2题" in failed["messages"][-1]["content"]
        assistant_id = failed["messages"][-1]["message_id"]
        replay = client.get(endpoint + f"/messages/{assistant_id}/events").text
        assert replay.count("回答正确") == 1 and replay.count("复盘第2题") == 1
        result = _retry(client, app, endpoint)
        assert result["messages"][-1]["status"] == "done"
        assert len(gateway.grade_calls) == 1


def test_sse_replay_delta_frames_carry_frontend_kind(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    """重放 delta 帧的 data.kind 必须为 delta：前端按 event.data.kind 收窄事件。"""
    app = _app(tmp_path, monkeypatch)
    gateway = Grade34Gateway(
        ["温度是表示物体冷热程度的物理量。"], map_fn=_single_unit_map, plan_fn=_two_question_plan,
    )
    with TestClient(app) as client:
        account, endpoint = _start_review(client, app, gateway)
        _ask(client, app, endpoint, "开始复盘")
        result = _ask(client, app, endpoint, "温度表示冷热程度")
        assistant_id = result["messages"][-1]["message_id"]

        replay = client.get(endpoint + f"/messages/{assistant_id}/events")
        assert replay.status_code == 200
        frames = []
        for block in replay.text.strip().split("\n\n"):
            header, _, data = block.partition("\n")
            assert header.startswith("event: ")
            frames.append(
                (header[len("event: "):], json.loads(data[len("data: "):]))
            )

        deltas = [payload["delta"] for kind, payload in frames if kind == "delta"]
        assert deltas, "判定反馈与下一题呈现必须以 delta 事件可回放"
        for kind, payload in frames:
            if kind == "delta":
                assert payload["kind"] == "delta"
                assert payload["message_id"] == assistant_id
        stitched = "".join(deltas)
        assert stitched == result["messages"][-1]["content"]
        assert stitched.count("回答正确") == 1
        assert stitched.count("复盘第2题") == 1


@pytest.mark.parametrize("phase", ["grade", "recheck"])
def test_empty_point_evidence_is_rejected(tmp_path: Any, monkeypatch: Any, phase: str) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = Grade34Gateway(["温度是表示物体冷热程度的物理量。"], map_fn=_single_unit_map)
    if phase == "recheck":
        gateway.initial_judgement = "incomplete"
    original = gateway.invoke
    def no_evidence(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        task = kwargs.get("payload", {}).get("task")
        if task == ("study.grade" if phase == "grade" else "study.recheck_grade"):
            for point in result.output["point_checks"]:
                point["fragment_ids"] = []
        return result
    monkeypatch.setattr(gateway, "invoke", no_evidence)
    with TestClient(app) as client:
        account, endpoint = _start_review(client, app, gateway)
        _ask(client, app, endpoint, "开始复盘")
        failed = _ask(client, app, endpoint, "温度表示冷热程度")
        assert failed["messages"][-1]["status"] == "error"
        assert _raw_state(app, account["id"], endpoint).review.questions[0].judgement is None


def test_scope_change_during_grading_rejects_late_result(tmp_path: Any, monkeypatch: Any) -> None:
    from bridges.study import service as study_service
    app = _app(tmp_path, monkeypatch)
    gateway = Grade34Gateway(["温度是表示物体冷热程度的物理量。"], map_fn=_single_unit_map)
    with TestClient(app) as client:
        account, endpoint = _start_review(client, app, gateway)
        _ask(client, app, endpoint, "开始复盘")
        original = gateway.invoke
        def change_scope(*args: Any, **kwargs: Any) -> Any:
            result = original(*args, **kwargs)
            if kwargs.get("payload", {}).get("task") == "study.grade":
                state = _raw_state(app, account["id"], endpoint)
                state.scope.scope_version_id = "new-scope"
                study_service.StudyRepository(app.state.bridges_database).save(
                    account["id"], endpoint.rsplit("/", 1)[-1], state,
                )
            return result
        monkeypatch.setattr(gateway, "invoke", change_scope)
        failed = _ask(client, app, endpoint, "温度表示冷热程度")
        state = _raw_state(app, account["id"], endpoint)
        assert failed["messages"][-1]["status"] == "error"
        assert state.scope.scope_version_id == "new-scope"
        assert state.review.questions[0].judgement is None


@pytest.mark.parametrize("user_text", ["这道题是什么意思？", "我想先问一下这题怎么理解"])
def test_explanation_request_is_not_graded(tmp_path: Any, monkeypatch: Any, user_text: str) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = Grade34Gateway(["温度是表示物体冷热程度的物理量。"], map_fn=_single_unit_map)
    with TestClient(app) as client:
        account, endpoint = _start_review(client, app, gateway)
        _ask(client, app, endpoint, "开始复盘")
        _ask(client, app, endpoint, user_text)
        question = _raw_state(app, account["id"], endpoint).review.questions[0]
        assert not gateway.grade_calls
        assert question.answer is None and question.judgement is None


def test_stop_after_kernel_commit_preserves_domain_and_feedback(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    from bridges.kernel.executor import NodeKernel
    from bridges.kernel.repository import NodeKernelRepository
    app = _app(tmp_path, monkeypatch)
    gateway = Grade34Gateway(
        ["温度是表示物体冷热程度的物理量。"], map_fn=_single_unit_map, plan_fn=_two_question_plan,
    )
    with TestClient(app) as client:
        account, endpoint = _start_review(client, app, gateway)
        _ask(client, app, endpoint, "开始复盘")
        original = NodeKernel._deliver_outbox
        def stop_after_commit(self: Any, account_id: str, run_id: str, sink: Any) -> None:
            original(self, account_id, run_id, sink)
            artifacts = NodeKernelRepository(app.state.bridges_database).list_artifacts(
                account_id, endpoint.rsplit("/", 1)[-1],
            )
            if any(item.node == "study.grade" for item in artifacts):
                with app.state.bridges_database.transaction():
                    app.state.bridges_database.connection.execute(
                        "UPDATE generation_runs SET stop_requested = 1 WHERE run_id = ?", (run_id,),
                    )
        with monkeypatch.context() as patch:
            patch.setattr(NodeKernel, "_deliver_outbox", stop_after_commit)
            stopped = _ask(client, app, endpoint, "温度表示冷热程度")
        question = _raw_state(app, account["id"], endpoint).review.questions[0]
        assert question.judgement == "correct" and question.feedback
        assert "回答正确" in stopped["messages"][-1]["content"]
        assistant_id = stopped["messages"][-1]["message_id"]
        replay = client.get(endpoint + f"/messages/{assistant_id}/events").text
        assert replay.count("回答正确") == 1
        continued = _ask(client, app, endpoint, "继续复盘")
        assert "复盘第2题" in continued["messages"][-1]["content"]
        assert len(gateway.grade_calls) == 1


def test_domain_write_failure_rolls_back_grade_artifact_and_receipt(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    from bridges.kernel.repository import NodeKernelRepository
    app = _app(tmp_path, monkeypatch)
    gateway = Grade34Gateway(["温度是表示物体冷热程度的物理量。"], map_fn=_single_unit_map)
    with TestClient(app) as client:
        account, endpoint = _start_review(client, app, gateway)
        _ask(client, app, endpoint, "开始复盘")
        repository = app.state.chat_service._repo
        original = repository.update_message_content_in_transaction
        def fail_feedback(account_id: str, message_id: str, content: str, now: Any) -> int:
            if "回答正确" in content:
                raise RuntimeError("模拟判定领域写入失败")
            return original(account_id, message_id, content, now)
        with monkeypatch.context() as patch:
            patch.setattr(repository, "update_message_content_in_transaction", fail_feedback)
            failed = _ask(client, app, endpoint, "温度表示冷热程度")
        assert failed["messages"][-1]["status"] == "error"
        question = _raw_state(app, account["id"], endpoint).review.questions[0]
        assert question.answer and question.judgement is None and question.feedback is None
        artifacts = NodeKernelRepository(app.state.bridges_database).list_artifacts(
            account["id"], endpoint.rsplit("/", 1)[-1],
        )
        assert not any(item.node == "study.grade" for item in artifacts)
        assert app.state.bridges_database.connection.execute(
            "SELECT count(*) FROM node_receipts WHERE node = 'study.grade'"
        ).fetchone()[0] == 0
        assert _retry(client, app, endpoint)["messages"][-1]["status"] == "done"


@pytest.mark.parametrize("invalid", ["scope", "unasked", "unverified_scope", "unverified_question"])
def test_only_presented_current_scope_question_receives_answer(
    tmp_path: Any, monkeypatch: Any, invalid: str,
) -> None:
    from bridges.study.service import StudyRepository
    app = _app(tmp_path, monkeypatch)
    gateway = Grade34Gateway(["温度是表示物体冷热程度的物理量。"], map_fn=_single_unit_map)
    with TestClient(app) as client:
        account, endpoint = _start_review(client, app, gateway)
        _ask(client, app, endpoint, "开始复盘")
        state = _raw_state(app, account["id"], endpoint)
        if invalid == "scope":
            state.scope.scope_version_id = "changed-before-answer"
        elif invalid == "unasked":
            state.review.questions[0].asked = False
        elif invalid == "unverified_scope":
            state.scope.verified = False
        else:
            state.review.questions[0].verification = None
        StudyRepository(app.state.bridges_database).save(
            account["id"], endpoint.rsplit("/", 1)[-1], state,
        )
        failed = _ask(client, app, endpoint, "温度表示冷热程度")
        question = _raw_state(app, account["id"], endpoint).review.questions[0]
        assert failed["messages"][-1]["status"] == "error"
        assert question.answer is None and question.user_message_id is None
        assert not gateway.grade_calls
