"""对话级插件选择测试（Issue 36）。

覆盖：可用集合=当前账户已安装且启用（SKILL 内置/用户包 + MCP）；创建/
更新会话携带选择逐项校验（停用/卸载/未安装 422 中文原因）；停用后读取
会话清洗失效项并解释影响（removed_selections）；撤权后立即从选择移除；
显式 [] 清空；选择随会话持久化（刷新可恢复）；生成时有效选择注入「本
对话可用工具」上下文、清除后不再注入；跨账户选择/清洗互不可见。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.ai import CapabilityRegistry, ModelGateway
from bridges.ai.adapters import StreamChunk
from bridges.chat.repository import ConversationRepository
from bridges.chat.selections import ChatSelectionsService
from bridges.contracts.ai import (
    CapabilityKind,
    CapabilityRecord,
    CapabilityStatus,
    RetryPolicy,
)
from bridges.contracts.chat import ChatPluginSelectionItem
from bridges.contracts.workflows import RunContextEnvelope
from bridges.mcp.service import McpService
from bridges.plugins.service import PluginService
from bridges.skills.registry import create_builtin_registry
from bridges.storage.database import BridgesDatabase
from bridges.storage.object_store import EncryptedFileObjectStore
from bridges.storage.repository import BridgesObjectRepository
from tests.mcp.fixtures import ECHO_YAML, NOTE_YAML

HUMANIZER = "bridges-humanizer"
ECHO = "bridges-echo"
NOTE = "bridges-note"


def _chat_capability() -> CapabilityRecord:
    return CapabilityRecord(
        name="qwen_text_chat",
        version="1",
        kind=CapabilityKind.MODEL,
        vendor="qwen",
        region="cn-beijing",
        model_id="qwen3.7-plus-2026-05-26",
        input_schema_version="chat-messages-v1",
        output_schema_version="chat-completion-v1",
        status=CapabilityStatus.VERIFIED,
        retry_policy=RetryPolicy(max_attempts=3, backoff_seconds=0.01),
    )


class _CapturingStreamAdapter:
    """捕获模型载荷的流式适配器（验证工具上下文注入）。"""

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def stream_call(
        self,
        capability: CapabilityRecord,
        run_context: RunContextEnvelope,
        payload: dict[str, Any],
    ):
        self.payloads.append(payload)
        yield StreamChunk(kind="delta", delta="好的。")
        yield StreamChunk(kind="done")


def _install_mcp(client: TestClient, yaml_text: str, filename: str) -> None:
    response = client.post(
        "/mcp/install",
        content=yaml_text.encode("utf-8"),
        headers={"X-Bridges-Filename": filename},
    )
    assert response.status_code == 200, response.text


def _create_conversation(
    client: TestClient, selection: list[dict[str, str]] | None = None
) -> str:
    body: dict[str, Any] = {}
    if selection is not None:
        body["plugin_selection"] = selection
    response = client.post("/chat/conversations", json=body)
    assert response.status_code == 201, response.text
    return response.json()["conversation_id"]


# ---------------------------------------------------------------------------
# API 层：真实 sqlite 应用（内置 humanizer 插件 + 真实 MCP 安装）
# ---------------------------------------------------------------------------


@pytest.fixture
def sqlite_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    from bridges.api.main import create_app
    from bridges.config import get_settings

    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "api-test-secret-key")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    return create_app()


@pytest.fixture
def client(sqlite_app: Any) -> TestClient:
    return TestClient(sqlite_app)


def _register(client: TestClient, tag: str = "1") -> dict[str, Any]:
    response = client.post(
        "/auth/register",
        json={
            "username": f"selection_user_{tag}",
            "qq_email": f"12345678{tag}@qq.com",
            "password": "Passw0rd123!",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["account"]


def test_builtin_skill_selection_persists_on_create(client: TestClient) -> None:
    """内置已启用插件可随创建会话选择并持久化到投影。"""
    account = _register(client)
    conversation_id = _create_conversation(
        client, [{"kind": "skill", "plugin_id": HUMANIZER}]
    )
    response = client.get(f"/chat/conversations/{conversation_id}")
    assert response.status_code == 200, response.text
    projection = response.json()
    assert projection["plugin_selection"] == [
        {"kind": "skill", "plugin_id": HUMANIZER}
    ]
    assert projection["removed_selections"] == []
    assert account["id"]


def test_disabled_skill_rejected_on_create(client: TestClient) -> None:
    """停用后的插件不再属于可用集合：创建携带 422 中文原因。"""
    _register(client)
    client.post(f"/plugins/{HUMANIZER}/disable")
    response = client.post(
        "/chat/conversations",
        json={"plugin_selection": [{"kind": "skill", "plugin_id": HUMANIZER}]},
    )
    assert response.status_code == 422, response.text
    assert "已停用" in response.json()["detail"]["message"]


def test_unknown_plugin_rejected(client: TestClient) -> None:
    """未安装/不存在的插件拒绝选择。"""
    _register(client)
    response = client.post(
        "/chat/conversations",
        json={"plugin_selection": [{"kind": "skill", "plugin_id": "ghost-skill"}]},
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["error"] == "plugin_not_available"


def test_mcp_selection_and_disable_read_cleanup(client: TestClient) -> None:
    """MCP 选择持久化；停用后读取会话清洗失效项并解释影响。"""
    _register(client)
    _install_mcp(client, ECHO_YAML, "echo.yaml")
    conversation_id = _create_conversation(
        client, [{"kind": "mcp", "plugin_id": ECHO}]
    )
    response = client.get(f"/chat/conversations/{conversation_id}")
    assert response.status_code == 200, response.text
    assert response.json()["plugin_selection"] == [
        {"kind": "mcp", "plugin_id": ECHO}
    ]
    # 停用 → 立即从可用集合消失 → 读取时清洗 + 解释影响
    client.post(f"/mcp/{ECHO}/disable")
    response = client.get(f"/chat/conversations/{conversation_id}")
    assert response.status_code == 200, response.text
    projection = response.json()
    assert projection["plugin_selection"] == []
    assert projection["removed_selections"][0]["plugin_id"] == ECHO
    assert "已停用" in projection["removed_selections"][0]["reason"]


def test_patch_selection_and_clear(client: TestClient) -> None:
    """PATCH 全量替换选择；显式 [] 清空且持久化。"""
    _register(client)
    conversation_id = _create_conversation(client)
    response = client.patch(
        f"/chat/conversations/{conversation_id}",
        json={"plugin_selection": [{"kind": "skill", "plugin_id": HUMANIZER}]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["plugin_selection"] == [
        {"kind": "skill", "plugin_id": HUMANIZER}
    ]
    response = client.patch(
        f"/chat/conversations/{conversation_id}",
        json={"plugin_selection": []},
    )
    assert response.status_code == 200, response.text
    assert response.json()["plugin_selection"] == []
    # 刷新后仍为空（持久化清空，不复活旧上下文）
    response = client.get(f"/chat/conversations/{conversation_id}")
    assert response.json()["plugin_selection"] == []


def test_revoke_permissions_removes_selection(client: TestClient) -> None:
    """撤权后该 MCP 立即从账户全部会话选择中移除。"""
    _register(client)
    _install_mcp(client, NOTE_YAML.format(write_dir=str(Path("tmp").resolve())), "note.yaml")
    conversation_id = _create_conversation(
        client, [{"kind": "mcp", "plugin_id": NOTE}]
    )
    response = client.put(
        f"/mcp/{NOTE}/permissions",
        json={
            "network_domains": [],
            "filesystem_read": [],
            "filesystem_write": [],
            "external_commands": [],
            "data_categories": ["current_message_text"],
            "sensitive_operations": [],
        },
    )
    assert response.status_code == 200, response.text
    response = client.get(f"/chat/conversations/{conversation_id}")
    assert response.status_code == 200, response.text
    assert response.json()["plugin_selection"] == []


def test_selection_scoped_to_account(client: TestClient) -> None:
    """跨账户看不到其他账户会话的选择（统一 404）。"""
    account_a = _register(client, tag="1")
    conversation_id = _create_conversation(
        client, [{"kind": "skill", "plugin_id": HUMANIZER}]
    )
    # 注册 B（注册即登录）：读取 A 的会话 404，不泄漏选择存在性。
    account_b = _register(client, tag="2")
    response = client.get(f"/chat/conversations/{conversation_id}")
    assert response.status_code == 404
    assert account_a["id"] != account_b["id"]


# ---------------------------------------------------------------------------
# 工具上下文注入：内存 harness（捕获模型载荷）
# ---------------------------------------------------------------------------


@pytest.fixture
def chat_env(tmp_path: Path) -> dict[str, Any]:
    database = BridgesDatabase(tmp_path / "selections.db")
    database.initialize()
    repository = ConversationRepository(database)
    registry = CapabilityRegistry()
    registry.register(_chat_capability())
    gateway = ModelGateway(registry)
    adapter = _CapturingStreamAdapter()
    gateway.register_adapter("qwen_text_chat", "1", adapter)
    object_repository = BridgesObjectRepository(
        database, EncryptedFileObjectStore(tmp_path / "objects", encryption_key="test-key")
    )
    plugin_service = PluginService(
        database=database,
        object_repository=object_repository,
        observability_service=None,
        skill_registry=create_builtin_registry(),
    )
    mcp_service = McpService(
        database=database,
        object_repository=object_repository,
        observability_service=None,
        runtime=None,
    )
    selections = ChatSelectionsService(
        repository=repository,
        database=database,
        plugin_service=plugin_service,
        mcp_service=mcp_service,
    )
    from bridges.chat.service import ChatService

    service = ChatService(
        repository=repository,
        gateway=gateway,
        selections_service=selections,
        mcp_service=mcp_service,
    )
    return {
        "database": database,
        "chat": service,
        "adapter": adapter,
        "account": "alice",
    }


def _send(chat: Any, account: str, conversation_id: str, content: str) -> None:
    from datetime import UTC, datetime

    from bridges.contracts.projects import ObjectDomain

    user, assistant, _ = chat.start_generation(
        account, conversation_id, content
    )
    context = RunContextEnvelope(
        run_id=assistant.message_id,
        account_id=account,
        project_id=conversation_id,
        workflow_name="chat",
        workflow_version="1",
        object_domain=ObjectDomain.PERSONAL_VAULT,
        submitted_at=datetime.now(UTC),
    )
    events = list(
        chat.stream_generation(
            account,
            conversation_id,
            assistant.message_id,
            context,
            until_user_message_id=user.message_id,
        )
    )
    assert any(event.kind == "done" for event in events), events


def test_tools_context_injected_when_selected(chat_env: dict[str, Any]) -> None:
    """选中插件后生成请求携带「本对话可用工具」系统块。"""
    chat = chat_env["chat"]
    conversation_id = chat.create_conversation(chat_env["account"]).conversation_id
    chat.update_conversation(
        chat_env["account"],
        conversation_id,
        plugin_selection=[ChatPluginSelectionItem(kind="skill", plugin_id=HUMANIZER)],
    )
    _send(chat, chat_env["account"], conversation_id, "你好")
    payload = chat_env["adapter"].payloads[0]
    blocks = [
        message["content"]
        for message in payload["messages"]
        if message["role"] == "system"
    ]
    assert any("本对话启用了以下插件工具" in block for block in blocks)
    assert any("文章人味化" in block for block in blocks)
    assert any("bridges-humanizer" in block for block in blocks)


def test_tools_context_not_injected_after_clear(chat_env: dict[str, Any]) -> None:
    """清除选择后生成请求不再携带任何插件工具上下文。"""
    chat = chat_env["chat"]
    conversation_id = chat.create_conversation(chat_env["account"]).conversation_id
    chat.update_conversation(
        chat_env["account"],
        conversation_id,
        plugin_selection=[ChatPluginSelectionItem(kind="skill", plugin_id=HUMANIZER)],
    )
    chat.update_conversation(
        chat_env["account"],
        conversation_id,
        plugin_selection=[],
    )
    _send(chat, chat_env["account"], conversation_id, "你好")
    payload = chat_env["adapter"].payloads[0]
    blocks = [
        message["content"]
        for message in payload["messages"]
        if message["role"] == "system"
    ]
    assert not any("本对话启用了以下插件工具" in block for block in blocks)
    assert not any("bridges-humanizer" in block for block in blocks)
