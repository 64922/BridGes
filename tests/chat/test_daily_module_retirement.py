"""日常模块收敛：新入口拒绝退役模块，历史可读且不能恢复执行。"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from bridges.api.main import create_app
from bridges.chat.graph_contracts import AVAILABLE_MODULE_IDS
from bridges.chat.service_contract import ChatDomainError
from bridges.chat.understanding import MainAgentUnderstanding
from bridges.config import get_settings
from bridges.contracts.chat import (
    ChatFirstTurnRequest,
    ChatMessageCreateRequest,
    ChatRetryRequest,
)
from bridges.orchestration.registry import ModuleRegistry
from bridges.routing.contracts import MODULE_CAPABILITIES
from tests.chat.test_chat_api import _create_conversation, _register, _send

RETIRED = ("resources", "career", "github")
ACTIVE = frozenset({"paper", "commute", "tieba"})


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "module-retirement-test-secret")
    get_settings.cache_clear()
    return TestClient(create_app())


@pytest.mark.parametrize("module_id", RETIRED)
@pytest.mark.parametrize(
    "request_type", [ChatMessageCreateRequest, ChatFirstTurnRequest, ChatRetryRequest]
)
def test_requests_reject_retired_modules(module_id: str, request_type: Any) -> None:
    with pytest.raises(ValidationError):
        request_type.model_validate({"module_id": module_id, "idempotency_key": "retired-test"})


@pytest.mark.parametrize("module_id", sorted(ACTIVE))
def test_current_module_requests_are_valid(module_id: str) -> None:
    assert ChatMessageCreateRequest(module_id=module_id).module_id == module_id
    assert ChatFirstTurnRequest(
        module_id=module_id, idempotency_key="active-test"
    ).module_id == module_id
    assert ChatRetryRequest(module_id=module_id).module_id == module_id


def test_production_only_registers_three_modules(client: TestClient) -> None:
    assert ModuleRegistry.production().modules == ACTIVE
    assert AVAILABLE_MODULE_IDS == ACTIVE
    assert frozenset(MODULE_CAPABILITIES) == ACTIVE
    assert ModuleRegistry.production().combination(frozenset({"paper", "github"})) is None
    for name in ("career_plan_service", "github_projects_service", "learning_resources_service"):
        assert not hasattr(client.app.state, name)
    for name in ("paper_search_service", "commute_service", "tieba_research_service"):
        assert getattr(client.app.state, name) is not None


@pytest.mark.parametrize("content", [
    "推荐 Python 学习资料和教程",
    "我想找南昌的 Java 后端实习，帮我做职业规划",
    "帮我推荐 GitHub 上的校园二手书交换项目",
    "找学习资料再推荐 GitHub 练手项目",
])
def test_retired_intents_use_ordinary_chat(content: str) -> None:
    understanding = MainAgentUnderstanding().understand(
        conversation_id="conversation", user_message_id="message",
        content=content, mode="companion",
    )
    assert understanding.actual_module_id is None
    assert understanding.capability_list == []
    assert understanding.clarification_question is None
    assert understanding.task_relation is None


@pytest.mark.parametrize(("content", "module_id"), [
    ("找深度学习的入门论文", "paper"),
    ("从图书馆步行到教学楼怎么走", "commute"),
    ("查华东交通大学吧关于宿舍的讨论", "tieba"),
])
def test_remaining_intents_still_route(content: str, module_id: str) -> None:
    understanding = MainAgentUnderstanding().understand(
        conversation_id="conversation", user_message_id="message",
        content=content, mode="companion",
    )
    assert understanding.actual_module_id == module_id


@pytest.mark.parametrize("module_id", sorted(ACTIVE))
def test_active_module_can_be_sent_through_http(client: TestClient, module_id: str) -> None:
    _register(client)
    conversation_id = _create_conversation(client)
    created = _send(client, conversation_id, "深度学习", module_id=module_id)
    assert created["user_message"]["module_id"] == module_id


@pytest.mark.parametrize("module_id", RETIRED)
def test_http_and_service_reject_retired_module_without_creating_messages(
    client: TestClient, module_id: str,
) -> None:
    account = _register(client)
    conversation_id = _create_conversation(client)
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": "原始请求", "module_id": module_id},
    )
    assert response.status_code == 422
    with pytest.raises(ChatDomainError, match="该模块已取消"):
        client.app.state.chat_service.start_generation(
            account["id"], conversation_id, "原始请求", module_id=module_id,
        )
    assert client.get(f"/chat/conversations/{conversation_id}").json()["messages"] == []


@pytest.mark.parametrize("module_id", RETIRED)
def test_history_is_readable_but_retired_suggestions_and_retry_are_disabled(
    client: TestClient, module_id: str,
) -> None:
    account = _register(client)
    conversation_id = _create_conversation(client)
    created = _send(client, conversation_id)
    client.app.state.generation_executor.run_tick()
    user_id = created["user_message"]["message_id"]
    assistant_id = created["assistant_message"]["message_id"]
    repo = client.app.state.chat_service._repo
    with repo.database.transaction():
        db = repo.database.scoped(account["id"])
        db.execute(
            "UPDATE messages SET module_id = ? WHERE message_id = ? AND account_id = ?",
            (module_id, user_id, account["id"]),
        )
    repo.update_message_module_suggestion(account["id"], assistant_id, {
        "module_id": module_id, "label": "历史建议", "reason": "历史原因", "text": "你好",
    }, datetime.now(UTC))
    history = client.get(f"/chat/conversations/{conversation_id}")
    assert history.status_code == 200
    messages = history.json()["messages"]
    assert messages[0]["module_id"] == module_id
    assert messages[-1]["module_suggestion"] is None
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages/{assistant_id}/retry", json={},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "module_not_allowed"
    assert len(client.get(f"/chat/conversations/{conversation_id}").json()["messages"]) == 2
