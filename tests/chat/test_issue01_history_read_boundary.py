"""Issue 01：学习历史只读投影按记录版本解释，不进入教学执行。

工单 12 起旧五阶段执行已退役：本文件不再经旧图生成样本，而是直接以
合法 v4 合同写入 ``study_states``，验证历史读取边界本身——只读、按版本
解释、私有字段不泄露、材料状态如实投影。
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.contracts.study import (
    StudyExchange,
    StudyFragment,
    StudyPage,
    StudyPageUpdate,
    StudyQuestion,
    StudyReview,
    StudyReviewQuestion,
    StudyState,
    StudySummary,
    StudySummaryPoint,
    StudyUnit,
    upgrade_legacy_study_state,
)
from bridges.lifecycle.catalog import export_rows
from tests.chat.study_state_fixtures import seed_study_state
from tests.chat.test_v2_05_photo_attachments import (
    _app,
    _register,
    _upload_draft,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
HISTORY_OBJECT_ID = "history-page-1"


def _base_state(object_id: str = HISTORY_OBJECT_ID) -> StudyState:
    """构造合法 v4 学习状态：已识别页、已判题、已总结（历史样本）。"""

    page = StudyPage(
        object_id=object_id,
        ordinal=1,
        content_hash="hash-1",
        model_id="qwen-vision-1",
        page_number=12,
        fragments=[
            StudyFragment(
                fragment_id="f-1",
                kind="text",
                position="顶部",
                text="线性函数 y=ax+b，斜率 a 表示变化率。",
                confidence=0.95,
            )
        ],
    )
    judged = StudyReviewQuestion(
        question_id="q-1",
        question="斜率 a 表示什么？",
        coverage_units=["ku-linear"],
        fragment_ids=["f-1"],
        asked=True,
        answer="a 表示变化率。",
        judgement="correct",
        canonical_answer="a 表示函数的变化率。",
        explanation="斜率是 y 随 x 变化的速率。",
        user_message_id="m-answer-1",
        core_points=["变化率"],
    )
    return StudyState(
        subsection_id="study-section-1",
        stage="summary",
        pages=[page],
        units=[
            StudyUnit(
                unit_id="ku-linear",
                title="线性函数",
                kind="concept",
                fragment_ids=["f-1"],
            )
        ],
        questions=[
            StudyQuestion(
                question="斜率 a 表示什么？",
                unit_ids=["ku-linear"],
                unit_titles=["线性函数"],
                scope_version_id="scope-1",
            )
        ],
        tutoring=[
            StudyExchange(
                user_message_id="m-tutor-user",
                assistant_message_id="m-tutor-assistant",
                question="请解释线性函数中的斜率。",
                answer="斜率表示 y 随 x 变化的速率。",
                sources=[],
            )
        ],
        review=StudyReview(
            questions=[judged],
            complete=True,
            scope_version_id="scope-1",
        ),
        summary=StudySummary(
            points=[
                StudySummaryPoint(
                    kind="mastered",
                    text="能说出斜率的含义。",
                    question_ids=["q-1"],
                    fragment_ids=["f-1"],
                )
            ],
            scope_version_id="scope-1",
        ),
        state_version=4,
    )


def _start_legacy_conversation(
    app: Any, client: TestClient, tag: str, *, with_material: bool = False
) -> tuple[str, str, str | None]:
    """注册账户并创建学习会话；返回 (账户, 会话, 材料对象或 None)。"""

    account_id = str(_register(client, tag)["id"])
    object_id: str | None = None
    payload: dict[str, Any] = {
        "content": "创建旧学习会话",
        "mode": "study",
        "idempotency_key": f"{tag}-first",
    }
    if with_material:
        draft = _upload_draft(
            client, filename=f"{tag}.png", upload_id=f"{tag}-upload-1"
        )
        assert draft.status_code == 201, draft.text
        object_id = str(draft.json()["object_id"])
        payload["attachment_ids"] = [object_id]
    response = client.post("/chat/first-turn", json=payload)
    assert response.status_code == 201, response.text
    conversation_id = str(response.json()["conversation"]["conversation_id"])
    return account_id, conversation_id, object_id


def _seed_conversation_state(
    app: Any,
    account_id: str,
    conversation_id: str,
    state: StudyState,
) -> None:
    seed_study_state(app.state.chat_service, account_id, conversation_id, state)


@pytest.mark.parametrize(
    ("stored_version", "expected_status", "expected_version"),
    [(3, "legacy", 3), (5, "unsupported", 5), ("malformed", "unreadable", None)],
)
def test_history_read_explains_versions_without_running_or_writing(
    tmp_path: Any,
    monkeypatch: Any,
    stored_version: int | str,
    expected_status: str,
    expected_version: int | None,
) -> None:
    app = _app(tmp_path, monkeypatch)
    client = TestClient(app)
    account_id, conversation_id, _ = _start_legacy_conversation(
        app, client, f"history{stored_version}"
    )
    endpoint = f"/chat/conversations/{conversation_id}"
    _seed_conversation_state(app, account_id, conversation_id, _base_state())
    before_messages = client.get(endpoint).json()["messages"]

    database = app.state.bridges_database
    row = database.scoped(account_id).execute(
        "SELECT state_json FROM study_states"
        " WHERE account_id = ? AND conversation_id = ?",
        (account_id, conversation_id),
    ).fetchone()
    assert row is not None
    if stored_version == "malformed":
        original_json = "{not-json"
    else:
        assert isinstance(stored_version, int)
        stored = json.loads(row["state_json"])
        stored["state_version"] = stored_version
        if stored_version == 3:
            stored.pop("summary_history", None)
            for page in stored.get("pages", []):
                page.pop("replaced_object_ids", None)
            for question in stored.get("review", {}).get("questions", []):
                question.pop("unanswered", None)
        if stored_version > 4:
            stored["future_private_marker"] = "future-private-value"
        original_json = json.dumps(stored, ensure_ascii=False)
    database.scoped(account_id).execute(
        "UPDATE study_states SET state_json = ?"
        " WHERE account_id = ? AND conversation_id = ?",
        (original_json, account_id, conversation_id),
    )

    # 读取期间禁止写入学习状态、消息或运行终态/事件。
    with database.connection as connection:
        for table in ("study_states", "messages", "generation_runs", "generation_events"):
            for operation in ("INSERT", "UPDATE", "DELETE"):
                connection.execute(
                    f"CREATE TRIGGER forbid_history_{table}_{operation.lower()} "
                    f"BEFORE {operation} ON {table} "
                    "BEGIN SELECT RAISE(ABORT, 'history read attempted a write'); END"
                )

    response = client.get(endpoint)

    assert response.status_code == 200, response.text
    projection = response.json()
    assert projection["study_history_status"]["status"] == expected_status
    assert projection["study_history_status"]["version"] == expected_version
    assert projection["study_history_status"]["message"]
    assert projection["messages"] == before_messages
    if expected_status == "legacy":
        assert projection["study"]["state_version"] == 4
        baseline = upgrade_legacy_study_state(
            StudyState.model_validate(json.loads(original_json))
        )
        legacy_projection = baseline.public_view().model_dump(mode="json")
        assert {
            key: value for key, value in projection["study"].items()
            if key != "materials"
        } == legacy_projection
    elif expected_status == "unsupported":
        assert projection["study"] is None
        assert "future-private-value" not in response.text
    else:
        assert projection["study"] is None

    unchanged = database.scoped(account_id).execute(
        "SELECT state_json FROM study_states"
        " WHERE account_id = ? AND conversation_id = ?",
        (account_id, conversation_id),
    ).fetchone()
    assert unchanged is not None and unchanged["state_json"] == original_json
    exported = export_rows(database, account_id, "study_states")
    assert len(exported) == 1 and exported[0]["state_json"] == original_json


def test_history_projection_matches_public_view_with_unanswered_update_and_deleted_material(
    tmp_path: Any, monkeypatch: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    client = TestClient(app)
    account_id, conversation_id, object_id = _start_legacy_conversation(
        app, client, "historyview", with_material=True
    )
    assert object_id is not None
    endpoint = f"/chat/conversations/{conversation_id}"

    state = _base_state(object_id)
    assert state.review is not None and state.review.questions
    unanswered = state.review.questions[0].model_copy(
        update={
            "question_id": "history-unanswered",
            "question": "截距如何影响图像？",
            "answer": None,
            "judgement": None,
            "canonical_answer": None,
            "explanation": None,
            "feedback": None,
            "unanswered": True,
        }
    )
    state.review = state.review.model_copy(
        update={"questions": [*state.review.questions, unanswered]}
    )
    assert state.summary is not None
    state.summary = state.summary.model_copy(
        update={
            "points": [
                *state.summary.points,
                StudySummaryPoint(
                    kind="unanswered",
                    text="截距问题已呈现，尚未作答。",
                    question_ids=[unanswered.question_id],
                ),
            ]
        }
    )
    original_page = state.pages[0]
    original_fragment = original_page.fragments[0]
    updated_page = original_page.model_copy(
        update={
            "fragments": [
                original_fragment.model_copy(
                    update={"text": f"{original_fragment.text}（依据更新）"}
                ),
                *original_page.fragments[1:],
            ]
        }
    )
    updated_pages = list(state.pages)
    updated_pages[0] = updated_page
    state.page_update = StudyPageUpdate(pages=updated_pages, units=state.units)
    assert (
        state.page_update.pages[0].fragments[0].text
        != state.pages[0].fragments[0].text
    )
    baseline = state.public_view().model_dump(mode="json")
    _seed_conversation_state(app, account_id, conversation_id, state)

    detail = client.get(endpoint).json()
    source_message = next(
        message
        for message in detail["messages"]
        if any(
            attachment["object_id"] == object_id
            for attachment in message.get("attachments") or []
        )
    )
    attachments = app.state.chat_service._attachments
    assert attachments is not None
    attachments.delete(
        account_id,
        conversation_id,
        object_id,
        message_id=source_message["message_id"],
    )

    result = client.get(endpoint)
    assert result.status_code == 200, result.text
    projection = result.json()
    assert projection["study"]["materials"] == [
        {"object_id": object_id, "status": "deleted"}
    ]
    assert {
        key: value for key, value in projection["study"].items()
        if key != "materials"
    } == baseline
    assert projection["study"]["tutoring"]
    assert projection["study"]["review"]["questions"][-1]["unanswered"] is True
    assert projection["study"]["page_update"] is not None
    assert (
        projection["study"]["page_update"]["pages"][0]["fragments"][0]["text"]
        != projection["study"]["pages"][0]["fragments"][0]["text"]
    )
