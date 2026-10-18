"""Issue 15：退役模块历史结果经正式会话读取仍完整，且不能恢复执行。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bridges.api.main import create_app
from bridges.career_plan.contracts import CareerPlanProjection, CareerPlanStatus
from bridges.contracts.chat import ChatMessageRole, ChatMessageStatus
from bridges.github.contracts import (
    GithubProjectsProjection,
    GithubProjectStatus,
    GithubRateLimitState,
)
from bridges.resources.contracts import LearningResourcesProjection, ResourcesStatus
from tests.chat.test_chat_api import _register


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "issue15-historical-test-secret")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    from bridges.config import get_settings

    get_settings.cache_clear()
    return TestClient(create_app())


def _seed_retired_history(client: TestClient, account_id: str) -> str:
    from bridges.chat.repository import MessageRecord

    repo = client.app.state.chat_service._repo
    now = datetime.now(UTC)
    conversation_id = "conversation-issue15"
    repo.create_conversation(
        account_id=account_id,
        conversation_id=conversation_id,
        title="历史结果",
        mode="companion",
        created_at=now,
    )
    resources = LearningResourcesProjection(
        status=ResourcesStatus.ERROR,
        original_phrase="深度学习",
        normalized_term="深度学习",
        error_code="source_timeout",
        error_message="当时来源读取超时。",
    )
    career = CareerPlanProjection(
        status=CareerPlanStatus.EMPTY,
        topic="后端开发",
        original_request="我想找 Java 后端工作。",
        empty_reason="当时没有取到可核实的岗位。",
    )
    github = GithubProjectsProjection(
        status=GithubProjectStatus.METADATA_ONLY,
        scenario="校园二手书交换",
        original_request="帮我找校园二手书交换项目。",
        rate_limit=GithubRateLimitState(),
        empty_reason="当时只取得元数据。",
    )

    messages = [
        MessageRecord(
            message_id="retired-user",
            conversation_id=conversation_id,
            account_id=account_id,
            role=ChatMessageRole.USER,
            attempt_number=1,
            status=ChatMessageStatus.DONE,
            content="找学习资料、职业规划和 GitHub 项目",
            thinking=None,
            error_code=None,
            error_message=None,
            duration_ms=None,
            model_id=None,
            run_lock_id=None,
            created_at=now,
            updated_at=now,
        ),
    ]
    projection_fields = {
        "retired-resources": ("learning_resources", resources),
        "retired-career": ("career_plan", career),
        "retired-github": ("github_projects", github),
    }
    for attempt, message_id in enumerate(projection_fields, start=1):
        messages.append(
            MessageRecord(
                message_id=message_id,
                conversation_id=conversation_id,
                account_id=account_id,
                role=ChatMessageRole.ASSISTANT,
                attempt_number=attempt,
                status=ChatMessageStatus.DONE,
                content="",
                thinking=None,
                error_code=None,
                error_message=None,
                duration_ms=None,
                model_id=None,
                run_lock_id=None,
                created_at=now,
                updated_at=now,
            )
        )
    for message in messages:
        repo.insert_message(message)
    with repo.database.transaction():
        db = repo.database.scoped(account_id)
        db.execute(
            "UPDATE messages SET module_id = ? WHERE message_id = ? AND account_id = ?",
            ("resources", "retired-user", account_id),
        )
        for message_id, (field, projection) in projection_fields.items():
            db.execute(
                f"UPDATE messages SET {field} = ? WHERE message_id = ? AND account_id = ?",
                (
                    json.dumps(projection.model_dump(mode="json"), ensure_ascii=False),
                    message_id,
                    account_id,
                ),
            )
    return conversation_id


def test_retired_history_reads_without_running_and_retry_stays_disabled(
    client: TestClient,
) -> None:
    account_id = _register(client, "15")["id"]
    conversation_id = _seed_retired_history(client, account_id)
    repo = client.app.state.chat_service._repo
    before = client.get(f"/chat/conversations/{conversation_id}")
    assert before.status_code == 200, before.text
    messages = before.json()["messages"]

    assert [message["attempt_number"] for message in messages[1:]] == [1, 2, 3]
    assert messages[0]["module_id"] == "resources"
    assert messages[1]["learning_resources"]["status"] == "error"
    assert messages[1]["learning_resources"]["error_message"] == "当时来源读取超时。"
    assert messages[2]["career_plan"]["status"] == "empty"
    assert messages[2]["career_plan"]["empty_reason"] == "当时没有取到可核实的岗位。"
    assert messages[3]["github_projects"]["status"] == "metadata_only"
    assert messages[3]["github_projects"]["empty_reason"] == "当时只取得元数据。"
    assert all(message["content"] == "" for message in messages[1:])
    assert repo.list_active_runs(account_id, conversation_id) == []

    retry = client.post(
        f"/chat/conversations/{conversation_id}/messages/retired-resources/retry",
        json={},
    )
    assert retry.status_code == 400, retry.text
    assert retry.json()["detail"]["error"] == "module_not_allowed"

    after = client.get(f"/chat/conversations/{conversation_id}")
    assert after.status_code == 200
    assert after.json()["messages"] == messages
    assert repo.list_active_runs(account_id, conversation_id) == []
