"""Issue 21 AC6：横向回归收口门。

覆盖设计文档（``docs/v2/delivery-plan.md``）要求横向复验的反例/正例：
跨模块上下文、模式锁、画像删除、密钥失败保留、外部证据不可得降级。
每条只断言合同层的最小事实（谁能不能发生），模块内部细节仍由各模块
自己的套件覆盖——本文件的作用是让任何一条在收口时被改坏都会红：

- 跨模块上下文：``tests/chat/test_v2_03_conversation_context.py``
- 模式锁：``tests/chat/test_issue03_atomic_first_turn.py``
- 画像删除：``tests/profiles/test_v2_08_atomic_profile.py``
- 密钥失败保留：``tests/api/test_qwen_credential_settings.py``
- 外部证据不可得：``tests/chat/test_arxiv_search_chat.py``

原「学习错题与总结」一项随旧五阶段学习执行退役（工单 12）；退役后的
只读边界与续学入口由 ``tests/retirement/test_issue12_legacy_study_retirement.py``
与 ``tests/chat/test_issue01_history_read_boundary.py`` 覆盖。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from bridges.ai.fixed_models import CHAT_MODEL_ID
from bridges.api.main import create_app
from bridges.arxiv_mcp.service import ArxivSearchService
from bridges.chat.context_compiler import compile_turn_context
from bridges.chat.repository import MessageRecord
from bridges.config import get_settings
from bridges.contracts.chat import ChatMessageRole, ChatMessageStatus, ChatMode
from bridges.contracts.profiles import FourDimensionConfidence
from bridges.profiles.commit import ProfileFactSubmission
from tests.api.test_qwen_credential_settings import (
    _NEW_KEY,
    _OLD_KEY,
    _provider_client,
)
from tests.api.test_qwen_credential_settings import (
    _app as _credential_app,
)
from tests.api.test_qwen_credential_settings import (
    _register as _register_credentials,
)

_CONV = "conv-horizontal"
_ACC = "acc-horizontal"


# ---------------------------------------------------------------------------
# 1. 跨模块上下文
# ---------------------------------------------------------------------------


def _record(
    message_id: str,
    role: ChatMessageRole,
    content: str,
    *,
    minutes: int = 0,
) -> MessageRecord:
    created = datetime.now(UTC) + timedelta(minutes=minutes)
    return MessageRecord(
        message_id=message_id,
        conversation_id=_CONV,
        account_id=_ACC,
        role=role,
        attempt_number=1,
        status=ChatMessageStatus.DONE,
        content=content,
        thinking=None,
        error_code=None,
        error_message=None,
        duration_ms=None,
        model_id=None,
        run_lock_id=None,
        created_at=created,
        updated_at=created,
        image=None,
    )


def test_cross_module_context_uses_earlier_turn_and_stays_within_its_message() -> None:
    """论文轮解释 Transformer 后，GitHub 轮用「它」指代同一会话前文。"""
    records = [
        _record("u1", ChatMessageRole.USER, "解释一下 Transformer 的注意力机制"),
        _record("a1", ChatMessageRole.ASSISTANT, "Transformer 用自注意力建模序列关系。"),
        _record("u2", ChatMessageRole.USER, "找实现它的项目", minutes=3),
    ]

    compiled = compile_turn_context(
        messages=records,
        current_user_message_id="u2",
        model_id=CHAT_MODEL_ID,
        mode=ChatMode.COMPANION,
    )

    # 前文语境可用：本轮采用原文里含上一轮的用户问题与回答，且记录其消息 ID。
    adopted = [message["content"] for message in compiled.messages]
    assert "解释一下 Transformer 的注意力机制" in "\n".join(adopted)
    assert {"u1", "a1"} <= set(compiled.adopted_message_ids)
    # 指代词有前文可指，不宣称「找不到前文」。
    assert compiled.unresolved_reference is False


# ---------------------------------------------------------------------------
# 2. 模式锁
# ---------------------------------------------------------------------------


def _register(client: TestClient, tag: str) -> dict[str, Any]:
    response = client.post(
        "/auth/register",
        json={
            "username": f"horizontal_{tag}",
            "qq_email": f"{uuid4().int % 10**16:016d}@qq.com",
            "password": "Passw0rd123!",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["account"]


def test_first_turn_locks_mode_and_switching_is_retired(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "horizontal-regression-secret")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        _register(client, "modelock")
        first = client.post(
            "/chat/first-turn",
            json={"content": "聊两句", "idempotency_key": str(uuid4()), "mode": "companion"},
        )
        assert first.status_code == 201, first.text
        conversation_id = first.json()["conversation"]["conversation_id"]

        projection = client.get(f"/chat/conversations/{conversation_id}").json()
        assert projection["mode"] == "companion"
        # 首条消息提交后模式锁定。
        assert projection["mode_locked"] is True

        # 旧模式切换写入口已退役：稳定 410，不产生任何模式变更。
        switched = client.post(
            f"/chat/conversations/{conversation_id}/mode", json={"mode": "study"}
        )
        assert switched.status_code == 410, switched.text
        assert switched.json()["detail"]["error"] == "conversation_mode_switch_retired"
        assert client.get(f"/chat/conversations/{conversation_id}").json()["mode"] == "companion"

        # 也不能用「首轮」接口把已有会话改成别的模式。
        conflict = client.post(
            "/chat/first-turn",
            json={
                "content": "改成学习模式",
                "idempotency_key": str(uuid4()),
                "mode": "study",
                "conversation_id": conversation_id,
            },
        )
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["detail"]["error"] == "conversation_mode_locked"


# ---------------------------------------------------------------------------
# 3. 画像删除
# ---------------------------------------------------------------------------


def test_deleted_profile_item_does_not_come_back_from_old_messages() -> None:
    client = TestClient(create_app())
    account_id = _register(client, "profiledelete")["id"]
    service = client.app.state.atomic_profile_service
    item = service.remember(account_id, "我养了一只叫团子的猫")

    deleted = client.request(
        "DELETE", f"/profiles/items/{item.profile_item_id}", json={"version": item.version}
    )
    assert deleted.status_code == 204, deleted.text
    assert client.get("/profiles/items").json() == []

    # 旧消息重放/自动抽取不得让已删除条目复活。
    service.commit_fact(
        account_id,
        ProfileFactSubmission(
            text="我养了一只叫团子的猫",
            confidence=FourDimensionConfidence.MEDIUM,
            evidence_message_id="msg-deleted",
        ),
    )
    assert client.get("/profiles/items").json() == []


# ---------------------------------------------------------------------------
# 4. 密钥失败保留
# ---------------------------------------------------------------------------


def test_failed_key_probe_keeps_previous_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider_client(candidate_key=_NEW_KEY)
    app = _credential_app(monkeypatch, provider)
    client = TestClient(app)
    _register_credentials(client)

    response = client.put("/settings/credentials/qwen", json={"api_key": "sk-rejected"})

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["error"] == "credential_invalid"
    # 失败不覆盖旧配置：运行时凭据仍是原来的可用密钥。
    from bridges.credentials.ids import GLOBAL_QWEN_CREDENTIAL_ID

    stored = app.state.runtime_credential_store.get(GLOBAL_QWEN_CREDENTIAL_ID)
    assert stored is not None and stored.get_secret_value() == _OLD_KEY
    provider.close()


# ---------------------------------------------------------------------------
# 5. 外部证据不可得
# ---------------------------------------------------------------------------


class _UnavailableArxivClient:
    def search(self, query: str, *, max_results: int = 5, stop_event: Any | None = None) -> Any:
        from bridges.arxiv_mcp.errors import ArxivMcpError

        raise ArxivMcpError("arxiv_unavailable", "上游暂不可用")


def test_unavailable_external_evidence_degrades_without_fabrication() -> None:
    from bridges.arxiv_mcp.service import ArxivSearchPlan

    service = ArxivSearchService(client=_UnavailableArxivClient())
    plan = ArxivSearchPlan(
        should_search=True,
        reason="用户请求论文",
        query="Transformer",
        max_results=3,
    )

    projection = service.search("acc-horizontal", plan)

    assert projection is not None
    # 不可得即降级：状态为错误、零条论文、给出中文原因，不伪造任何结果。
    assert projection.status.value == "error"
    assert projection.papers == []
    assert projection.error_code
    assert projection.error_message
    # 降级投影里没有任何论文条目，聊天层就没有可宣称的论文证据
    # （聊天层展示与重试语义见 tests/chat/test_arxiv_search_chat.py）。
    assert all(not item.arxiv_id for item in projection.papers)
