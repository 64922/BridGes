"""Issue 01：学习历史只读投影按记录版本解释，不进入教学执行。"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.lifecycle.catalog import export_rows
from tests.chat.test_v2_05_photo_attachments import _app
from tests.chat.test_v2_18_study_tutoring import _ask, _start
from tests.chat.test_v2_20_study_summary import SummaryGateway, _finish_review


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
    gateway = SummaryGateway()
    gateway.question_count = 1
    app.state.chat_service._gateway = gateway

    with TestClient(app) as client:
        endpoint = _start(client, app)
        _ask(client, app, endpoint, "请解释线性函数中的斜率。")
        completed = _finish_review(client, app, endpoint)
        assert completed["study"]["summary"] is not None
        before_messages = completed["messages"]
        account_id = client.get("/auth/session").json()["account"]["id"]
        conversation_id = endpoint.rsplit("/", 1)[-1]

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

        def reject_model_call(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("reading learning history must not call a model")

        monkeypatch.setattr(gateway, "invoke", reject_model_call)
        monkeypatch.setattr(gateway, "summarize", reject_model_call)
        response = client.get(endpoint)

        assert response.status_code == 200, response.text
        projection = response.json()
        assert projection["study_history_status"]["status"] == expected_status
        assert projection["study_history_status"]["version"] == expected_version
        assert projection["study_history_status"]["message"]
        assert projection["messages"] == before_messages
        if expected_status == "legacy":
            assert projection["study"]["state_version"] == 4
            assert projection["study"] == completed["study"]
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


def test_history_reports_deleted_material_without_rewriting_page_evidence(
    tmp_path: Any, monkeypatch: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = SummaryGateway()
    gateway.question_count = 1
    app.state.chat_service._gateway = gateway

    with TestClient(app) as client:
        endpoint = _start(client, app)
        _ask(client, app, endpoint, "请解释线性函数中的斜率。")
        completed = _finish_review(client, app, endpoint)
        account_id = client.get("/auth/session").json()["account"]["id"]
        conversation_id = endpoint.rsplit("/", 1)[-1]
        page = completed["study"]["pages"][0]
        source_message = next(
            message
            for message in completed["messages"]
            if any(
                attachment["object_id"] == page["object_id"]
                for attachment in message["attachments"]
            )
        )
        attachments = app.state.chat_service._attachments
        assert attachments is not None

        attachments.delete(
            account_id,
            conversation_id,
            page["object_id"],
            message_id=source_message["message_id"],
        )

        result = client.get(endpoint)
        assert result.status_code == 200, result.text
        projection = result.json()
        assert projection["study_materials"] == [
            {"object_id": page["object_id"], "status": "deleted"}
        ]
        assert projection["study"]["pages"] == completed["study"]["pages"]
        assert projection["study"]["summary"] == completed["study"]["summary"]
