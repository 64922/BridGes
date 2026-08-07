"""Issue 24：统一桌面搜索 API 端到端测试。

经 TestClient 走完整认证与路由装配；种子数据直接写入应用挂载的
权威数据库（与运行时同一写入路径），验证响应契约、筛选参数校验
与跨账户安全拒绝。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.api.main import create_app
from bridges.chat.repository import ConversationRepository
from bridges.config import get_settings
from bridges.ingestion.embedding import DeterministicEmbeddingPort
from bridges.ingestion.index import VersionedIndex
from bridges.ingestion.service import IngestionService
from bridges.search import SearchService
from tests.search.conftest import (
    add_image,
    add_material,
    add_message,
    seed_conversation,
    seed_project,
)


@pytest.fixture()
def api_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """挂载 sqlite bridges.db 的应用 + 两个已登录客户端 + 搜索种子环境。"""
    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "search-api-test-secret")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    app = create_app()
    database = app.state.bridges_database
    repository = app.state.object_repository
    embedding = DeterministicEmbeddingPort()
    env: dict[str, Any] = {
        "database": database,
        "repository": repository,
        "ingestion": IngestionService(
            database=database,
            object_repository=repository,
            embedding=embedding,
            index=VersionedIndex(database, embedding),
        ),
        "search": SearchService(database),
        "conversations": ConversationRepository(database),
        "path": tmp_path,
    }
    client_a = TestClient(app)
    client_b = TestClient(app)
    account_a = _register(client_a, "搜索甲", "10101010@qq.com")
    account_b = _register(client_b, "搜索乙", "20202020@qq.com")
    return {
        **env,
        "app": app,
        "client_a": client_a,
        "client_b": client_b,
        "account_a": account_a,
        "account_b": account_b,
    }


def _register(client: TestClient, username: str, qq_email: str) -> str:
    response = client.post(
        "/auth/register",
        json={"username": username, "qq_email": qq_email, "password": "pass-word-123"},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["account"]["id"])


class TestUnifiedSearchApi:
    def test_multi_type_end_to_end(self, api_env: dict[str, Any]) -> None:
        account = api_env["account_a"]
        conversation_id = seed_conversation(api_env, account, "引力波会议记录")
        add_message(api_env, account, conversation_id, "今晚复习引力波公式")
        add_material(api_env, account, "引力波讲义.txt", "激光干涉测量")
        add_image(api_env, account, "引力波示意图.png")
        project_id = seed_project(api_env, account, "引力波专题")

        response = api_env["client_a"].get("/search", params={"q": "引力波"})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["query"] == "引力波"
        assert payload["index_ready"] is True
        assert payload["counts"] == {"chat": 2, "image": 1, "document": 1, "project": 1}
        by_type = {item["result_type"]: item for item in payload["results"][:20]}
        # 四类结果都带跳转锚点。
        chat_items = [
            item for item in payload["results"] if item["result_type"] == "chat"
        ]
        message_hit = next(item for item in chat_items if item["message_id"] is not None)
        assert message_hit["conversation_id"] == conversation_id
        assert by_type["image"]["object_id"] is not None
        assert by_type["document"]["object_id"] is not None
        assert by_type["project"]["project_id"] == project_id
        # 高亮片段：matched 段等于查询词。
        for item in payload["results"]:
            for segment in item["snippet"]:
                if segment["matched"]:
                    assert segment["text"] == "引力波"

    def test_type_and_project_filters(self, api_env: dict[str, Any]) -> None:
        account = api_env["account_a"]
        project_id = seed_project(api_env, account, "筛选项目")
        seed_conversation(api_env, account, "筛选对话", project_id=project_id)
        client = api_env["client_a"]
        response = client.get(
            "/search", params=[("q", "筛选"), ("types", "chat,document")]
        )
        assert response.status_code == 200, response.text
        assert {item["result_type"] for item in response.json()["results"]} == {"chat"}
        scoped = client.get(
            "/search",
            params={"q": "筛选", "types": "project", "project_id": project_id},
        )
        assert scoped.status_code == 200, scoped.text
        assert [
            item["project_id"] for item in scoped.json()["results"]
        ] == [project_id]

    def test_invalid_types_is_422(self, api_env: dict[str, Any]) -> None:
        response = api_env["client_a"].get(
            "/search", params={"q": "任意", "types": "chat,video"}
        )
        assert response.status_code == 422
        assert "video" in response.text

    def test_other_account_project_id_is_404(self, api_env: dict[str, Any]) -> None:
        project_b = seed_project(api_env, api_env["account_b"], "乙的私有项目")
        response = api_env["client_a"].get(
            "/search", params={"q": "任意", "project_id": project_b}
        )
        assert response.status_code == 404

    def test_cross_account_results_never_leak(self, api_env: dict[str, Any]) -> None:
        seed_conversation(api_env, api_env["account_b"], "绝密研究计划")
        response = api_env["client_a"].get("/search", params={"q": "绝密"})
        assert response.status_code == 200
        assert response.json()["results"] == []

    def test_unauthenticated_is_401(self, api_env: dict[str, Any]) -> None:
        client = TestClient(api_env["app"])
        response = client.get("/search", params={"q": "任意"})
        assert response.status_code == 401

    def test_blank_query_returns_empty(self, api_env: dict[str, Any]) -> None:
        seed_conversation(api_env, api_env["account_a"], "任意对话")
        response = api_env["client_a"].get("/search", params={"q": "  "})
        assert response.status_code == 200
        payload = response.json()
        assert payload["results"] == []
        assert payload["index_ready"] is True
