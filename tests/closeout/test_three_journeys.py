"""Issue 09：三条现场旅程的真实 API、SQLite 持久化与账户隔离收尾门。"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from bridges.api.main import create_app
from bridges.config import get_settings

LEARNING_QUERY = "我想学习Transformer架构的相关知识"
PROFILE_ACADEMIC_QUERY = (
    "我是一名人工智能专业的大三学生，目标是考一个211院校的相关专业，给我规划一下考研"
)
PAPER_QUERY = "给我找几篇Transformer方向相关的论文"


@pytest.fixture
def sqlite_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """用临时 SQLite 与显式协议 fixture 启动完整 API。"""

    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "api-test-secret-key")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    monkeypatch.setenv("BRIDGES_CLOSEOUT_FIXTURES", "true")
    get_settings.cache_clear()
    return create_app()


def _register(client: TestClient, tag: str) -> dict[str, Any]:
    digits = f"{abs(hash(tag))}{uuid4().int}"[-16:]
    response = client.post(
        "/auth/register",
        json={
            "username": f"closeout_{tag}",
            "qq_email": f"{digits}@qq.com",
            "password": "Passw0rd123!",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["account"]


def _first_turn(client: TestClient, content: str, *, mode: str = "companion") -> str:
    response = client.post(
        "/chat/first-turn",
        json={
            "content": content,
            "idempotency_key": str(uuid4()),
            "mode": mode,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["conversation"]["conversation_id"]


def _drive(app: Any, generation_helpers: dict[str, Any]) -> None:
    generation_helpers["drive"](app, timeout=15.0)


def _assistant(client: TestClient, conversation_id: str) -> dict[str, Any]:
    response = client.get(f"/chat/conversations/{conversation_id}")
    assert response.status_code == 200, response.text
    messages = response.json()["messages"]
    assistants = [message for message in messages if message["role"] == "assistant"]
    assert assistants
    return assistants[-1]


def test_three_journeys_persist_profile_and_isolate_accounts(
    sqlite_app: Any, generation_helpers: dict[str, Any]
) -> None:
    """学习、论文、画像三条旅程必须通过真实 API 并在重启后保持一致。"""

    with TestClient(sqlite_app) as client:
        account = _register(client, "primary")

        learning_conversation = _first_turn(client, LEARNING_QUERY, mode="study")
        _drive(sqlite_app, generation_helpers)
        learning_assistant = _assistant(client, learning_conversation)
        teaching = learning_assistant["teaching"]
        assert learning_assistant["status"] == "done"
        assert teaching["can_answer_reliably"] is True
        assert teaching["evidence_gate"]["external_sources"]
        assert teaching["evidence_gate"]["status"] == "sufficient"
        assert "本轮未联网核实" not in str(teaching)

        _first_turn(client, PROFILE_ACADEMIC_QUERY)
        _drive(sqlite_app, generation_helpers)

        paper_conversation = _first_turn(client, PAPER_QUERY)
        _drive(sqlite_app, generation_helpers)
        paper_assistant = _assistant(client, paper_conversation)
        paper_search = paper_assistant["arxiv_search"]
        assert paper_assistant["status"] == "done"
        assert paper_search["status"] == "success"
        assert paper_search["papers"]
        assert any("Transformer" in paper["title"] for paper in paper_search["papers"])

        profile_response = client.get("/profiles/four-dimensions")
        assert profile_response.status_code == 200, profile_response.text
        records = profile_response.json()
        dimensions = {record["dimension"] for record in records}
        assert {"knowledge_interest", "academic_status", "stage_goal"} <= dimensions
        assert "hobby" not in dimensions
        first_stable_times = {
            record["dimension"]: record["first_stable_recorded_at"] for record in records
        }
        assert client.get("/profiles/status").json()["status"] == "ready"
        session_cookie = client.cookies.get("bridges_session")
        assert session_cookie
        assert account["id"]

    # 关闭完整应用资源后重新装配同一 SQLite 文件，验证不是进程内缓存。
    sqlite_app.state.bridges_database.close()
    sqlite_app.state.state_store.close()
    restarted_app = create_app()
    try:
        with TestClient(restarted_app) as restarted:
            restarted.cookies.set("bridges_session", session_cookie)
            replayed = restarted.get("/profiles/four-dimensions")
            assert replayed.status_code == 200, replayed.text
            replayed_records = replayed.json()
            assert {record["dimension"] for record in replayed_records} >= {
                "knowledge_interest",
                "academic_status",
                "stage_goal",
            }
            assert {
                record["dimension"]: record["first_stable_recorded_at"]
                for record in replayed_records
            } == first_stable_times

            _register(restarted, "secondary")
            secondary_profile = restarted.get("/profiles/four-dimensions")
            assert secondary_profile.status_code == 200, secondary_profile.text
            assert secondary_profile.json() == []
    finally:
        restarted_app.state.bridges_database.close()
        restarted_app.state.state_store.close()
