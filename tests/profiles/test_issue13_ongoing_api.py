"""画像工单 13：进行中事项区的公开读/管理合同与失效联动。"""

from __future__ import annotations

from typing import Any, cast

from fastapi.testclient import TestClient

from bridges.api.main import create_app
from bridges.profiles.commit import ProfileFactSubmission

GOAL_TEXT = "我在准备考研英语"
PROGRESS_TEXT = "考研英语的阅读已经刷完一轮"


def _register(client: TestClient, username: str, email: str) -> str:
    response = client.post(
        "/auth/register",
        json={
            "username": username,
            "qq_email": email,
            "password": "correct-horse-13",
        },
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())["account"]["id"]


def _seed(client: TestClient, account_id: str) -> dict[str, str]:
    service = client.app.state.atomic_profile_service
    goal = service.remember(account_id, GOAL_TEXT, source_message_id="message-goal")
    progress = service.commit_fact(
        account_id,
        ProfileFactSubmission(
            text=PROGRESS_TEXT, evidence_message_id="message-progress"
        ),
    )
    assert progress is not None
    return {"goal": goal.profile_item_id, "progress": progress.profile_item_id}


def test_ongoing_route_lists_goal_progress_and_pending() -> None:
    client = TestClient(create_app())
    account_id = _register(client, "issue13-list", "130001@qq.com")
    ids = _seed(client, account_id)

    response = client.get("/profiles/ongoing")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["usage_enabled"] is True
    assert body["notice"] is None
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["goal"]["profile_item_id"] == ids["goal"]
    assert item["goal"]["text"] == GOAL_TEXT
    assert item["goal"]["goal_state"] == "active"
    assert [entry["text"] for entry in item["progress"]] == [PROGRESS_TEXT]
    assert item["progress"][0]["role"] == "progress"
    assert item["pending_questions"] == []
    assert item["domain_references"] == []


def test_state_route_pause_complete_conflict_and_scope() -> None:
    client = TestClient(create_app())
    account_id = _register(client, "issue13-state", "130002@qq.com")
    ids = _seed(client, account_id)
    goal_id = ids["goal"]
    version = client.app.state.atomic_profile_service.get_item(
        account_id, goal_id
    ).version

    paused = client.post(
        f"/profiles/ongoing/{goal_id}/state",
        json={"action": "pause", "version": version},
    )
    assert paused.status_code == 200, paused.text
    assert paused.json()["items"][0]["goal"]["goal_state"] == "paused"

    stale = client.post(
        f"/profiles/ongoing/{goal_id}/state",
        json={"action": "complete", "version": version},
    )
    assert stale.status_code == 409, stale.text

    resumed = client.post(
        f"/profiles/ongoing/{goal_id}/state",
        json={"action": "resume", "version": version + 1},
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["items"][0]["goal"]["goal_state"] == "active"

    completed = client.post(
        f"/profiles/ongoing/{goal_id}/state",
        json={"action": "complete", "version": version + 2},
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["items"] == []
    # 完成只让事项区隐藏；条目本身仍在画像列表里。
    items = client.get("/profiles/items").json()
    assert goal_id in {item["profile_item_id"] for item in items}

    non_goal = client.post(
        f"/profiles/ongoing/{ids['progress']}/state",
        json={"action": "pause", "version": 1},
    )
    assert non_goal.status_code == 422, non_goal.text


def test_domain_reference_route_sets_and_clears() -> None:
    client = TestClient(create_app())
    account_id = _register(client, "issue13-domain", "130003@qq.com")
    ids = _seed(client, account_id)
    goal_id = ids["goal"]
    version = client.app.state.atomic_profile_service.get_item(
        account_id, goal_id
    ).version

    set_reference = client.put(
        f"/profiles/ongoing/{goal_id}/domain-reference",
        json={
            "reference": {"domain": "learning", "reference_id": "lp-1"},
            "version": version,
        },
    )
    assert set_reference.status_code == 200, set_reference.text
    references = set_reference.json()["items"][0]["domain_references"]
    assert references == [
        {
            "domain": "learning",
            "reference_id": "lp-1",
            "resolved": False,
            "display": None,
            "state_code": None,
        }
    ]

    stale = client.put(
        f"/profiles/ongoing/{goal_id}/domain-reference",
        json={"reference": None, "version": version},
    )
    assert stale.status_code == 409, stale.text

    cleared = client.put(
        f"/profiles/ongoing/{goal_id}/domain-reference",
        json={"reference": None, "version": version + 1},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["items"][0]["domain_references"] == []

    on_progress = client.put(
        f"/profiles/ongoing/{ids['progress']}/domain-reference",
        json={
            "reference": {"domain": "learning", "reference_id": "lp-9"},
            "version": 1,
        },
    )
    assert on_progress.status_code == 422, on_progress.text


def test_controls_route_invalidates_context_and_keeps_management() -> None:
    client = TestClient(create_app())
    account_id = _register(client, "issue13-controls", "130004@qq.com")
    _seed(client, account_id)
    ongoing = client.app.state.ongoing_items_service

    before = ongoing.read_context(account_id)
    assert before.version >= 1

    off = client.put("/profiles/controls", json={"usage_enabled": False})
    assert off.status_code == 200, off.text

    listing = client.get("/profiles/ongoing")
    assert listing.status_code == 200
    assert listing.json()["usage_enabled"] is False
    assert listing.json()["notice"]
    assert len(listing.json()["items"]) == 1  # 管理仍然可用
    assert ongoing.read_context(account_id).state.value == "rebuilding"

    on = client.put("/profiles/controls", json={"usage_enabled": True})
    assert on.status_code == 200, on.text
    after = ongoing.read_context(account_id)
    assert after.state.value == "current"
    assert after.version > before.version
