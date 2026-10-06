"""资料候选成功返回不等于完整主线已核实；保留旧投影缺字段语义。"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.chat.turn_result import derive_turn_result
from bridges.contracts.chat import ChatMessageStatus, ResultTrust, TurnOutcome
from tests.chat.test_chat_api import _create_conversation, _gateway_with
from tests.chat.test_v2_05_photo_attachments import _app, _register
from tests.resources.test_resources_module_flow import (
    BEGINNER_BOOK,
    _FakeBookSource,
    _FakeInsightReader,
    _install_resources_service,
    _SilentAdapter,
)


@pytest.mark.parametrize("payload, expected", [
    ({"status": "success", "path_verified": False}, TurnOutcome.PARTIAL),
    ({"status": "success", "path_verified": True}, TurnOutcome.COMPLETE),
    ({"status": "success"}, TurnOutcome.COMPLETE),
])
def test_resource_trust_follows_explicit_path_verification(payload: dict, expected: TurnOutcome):
    result = derive_turn_result(status=ChatMessageStatus.DONE,
                                projections={"learning_resources": payload})
    assert result.outcome is expected
    assert result.trust is (
        ResultTrust.EVIDENCE_BOUND if expected is TurnOutcome.PARTIAL else ResultTrust.QUALIFIED
    )


def test_abstract_only_paper_is_not_arbitrarily_downgraded() -> None:
    result = derive_turn_result(status=ChatMessageStatus.DONE, projections={
        "paper_search": {"status": "success", "papers": [{"read_scope": "abstract",
                           "unverified": ["未通读全文"]}]},
    })
    assert result.outcome is TurnOutcome.COMPLETE and result.trust is ResultTrust.QUALIFIED


def test_formal_resources_graph_returns_candidate_success_with_evidence_bound_result(
    tmp_path: Any, monkeypatch: Any, generation_helpers: dict[str, Any],
) -> None:
    app = _app(tmp_path, monkeypatch)
    app.state.chat_service._gateway = _gateway_with(_SilentAdapter())
    _install_resources_service(app, books=[_FakeBookSource(candidates=[BEGINNER_BOOK])],
        insight_reader=_FakeInsightReader(description="深度学习入门教材，需要线性代数基础"))
    with TestClient(app) as client:
        _register(client, "resources38")
        conversation_id = _create_conversation(client)
        endpoint = f"/chat/conversations/{conversation_id}"
        response = client.post(endpoint + "/messages", json={
            "content": "零基础，快速了解深度学习，只要1本书", "module_id": "resources",
        })
        assert response.status_code == 200, response.text
        generation_helpers["drive"](app)
        message = client.get(endpoint).json()["messages"][-1]
        resources = message["learning_resources"]
        assert resources["status"] == "success" and resources["items"], message
        assert resources["path_verified"] is False
        assert message["turn_result"]["outcome"] == "partial"
        assert message["turn_result"]["trust"] == "evidence_bound"
        assert ("完整路径已核实" not in message["content"]
                or "不称完整路径已核实" in message["content"])
        events = generation_helpers["subscribe"](client, conversation_id, message["message_id"])
        done = [payload["message"] for kind, payload in events if kind == "done"][-1]
        assert done["turn_result"] == message["turn_result"]
