"""工单 25：领域节点持久恢复、版本升级和账户生命周期验收。"""

from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from bridges.chat.repository import ConversationRepository
from bridges.kernel.contracts import KernelStatus
from bridges.kernel.executor import NodeKernel
from bridges.kernel.guard import RunCommitGuard
from bridges.kernel.repository import NodeKernelRepository
from bridges.lifecycle.catalog import EXPORT_CATEGORIES, delete_account_rows, export_rows
from bridges.resources.kernel import (
    RESOURCES_GATE_HANDLERS,
    RESOURCES_RECIPE_VERSION,
    ResourcesNodeFlow,
    build_resources_recipe,
    resources_recipe_registry,
)
from bridges.storage.database import BridgesDatabase
from tests.chat.test_chat_api import _create_conversation, _register
from tests.kernel.test_node_kernel import ACCOUNT, ASSISTANT, CONVERSATION, NOW, RUN, _inputs, _seed
from tests.resources.test_resources_module_flow import (
    BEGINNER_BOOK,
    _FakeBookSource,
    _FakeDiscoverer,
    _FakeInsightReader,
    _FakeVerifier,
    _install_resources_service,
    _run_and_read,
    _send,
    client,  # noqa: F401 - 注册已有端到端夹具
    sqlite_app,  # noqa: F401 - 注册已有端到端夹具
)


def execute(database: BridgesDatabase, source: _FakeBookSource):
    flow = ResourcesNodeFlow(
        books=[source], reader=_FakeInsightReader(description="深度学习入门，适合零基础学习者"),
        clock=lambda: NOW,
    )
    kernel = NodeKernel(
        registry=resources_recipe_registry(), repository=NodeKernelRepository(database),
        guard=RunCommitGuard(
            ConversationRepository(database), account_id=ACCOUNT, run_id=RUN,
            conversation_id=CONVERSATION, assistant_message_id=ASSISTANT, clock=lambda: NOW,
        ),
        gates=RESOURCES_GATE_HANDLERS, runner=flow.run_node, clock=lambda: NOW,
    )
    return kernel.execute(
        recipe=build_resources_recipe(),
        inputs=_inputs(user_content="我是零基础，快速了解深度学习，只要书"),
    )


def test_resources_resume_without_calls_and_reject_old_recipe(tmp_path: Path) -> None:
    path = tmp_path / "bridges.db"
    database = BridgesDatabase(path)
    database.initialize()
    _seed(database)
    source = _FakeBookSource(candidates=[BEGINNER_BOOK])
    original = execute(database, source)
    assert original.status is KernelStatus.COMPLETED
    assert original.delivery.payload["projection"]["path_verified"] is True
    database.close()
    database = BridgesDatabase(path)
    database.initialize()
    restored = execute(database, source)
    assert restored.status is KernelStatus.COMPLETED
    assert all(node.reused for node in restored.nodes)
    assert len(source.queries) == 1
    assert restored.delivery.content_hash == original.delivery.content_hash
    # 仿真历史语义：同一输入和完成收据仍不得复用旧配方产物。
    with database.transaction():
        database.connection.execute(
            "UPDATE node_artifacts SET recipe_version = 'learning-resources-recipe-v1'"
        )
    upgraded = execute(database, source)
    assert upgraded.status is KernelStatus.COMPLETED
    assert not any(node.reused for node in upgraded.nodes)
    assert all(item.recipe_version == RESOURCES_RECIPE_VERSION for item in upgraded.artifacts)
    assert len(source.queries) == 2
    database.close()


def test_resources_export_and_delete_are_account_scoped(tmp_path: Path) -> None:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    _seed(database)
    result = execute(database, _FakeBookSource(candidates=[BEGINNER_BOOK]))
    unrelated = replace(
        result.artifacts[0], account_id="another-account", artifact_id="another-artifact",
    )
    with database.transaction():
        NodeKernelRepository(database).save_artifact(unrelated)
    tables = next(
        category.tables for category in EXPORT_CATEGORIES if category.key == "node_kernel"
    )
    for table in tables:
        rows = export_rows(database, ACCOUNT, table)
        assert rows
        assert all(row["account_id"] == ACCOUNT for row in rows)
        other_rows = export_rows(database, "another-account", table)
        assert all(row["account_id"] == "another-account" for row in other_rows)
    artifacts = export_rows(database, ACCOUNT, "node_artifacts")
    assert {row["node"] for row in artifacts} == {
        "resources.parse", "resources.plan", "resources.search_books", "resources.search_videos",
        "resources.read", "resources.match", "resources.organize", "resources.verify",
    }
    delete_account_rows(database, ACCOUNT)
    for table in tables:
        assert export_rows(database, ACCOUNT, table) == []
    assert export_rows(database, "another-account", "node_artifacts")[0]["artifact_id"] == (
        "another-artifact"
    )
    database.close()


def test_explicit_network_prohibition_blocks_all_resources_sources(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any],  # noqa: F811 - pytest 夹具
) -> None:
    """A09–A11：用户明确不联网时，模块派发也不能绕过父图边界。"""
    _register(client)
    source = _FakeBookSource(candidates=[BEGINNER_BOOK])
    discoverer = _FakeDiscoverer()
    verifier = _FakeVerifier()
    reader = _FakeInsightReader()
    _install_resources_service(
        sqlite_app, books=[source], discoverer=discoverer, verifier=verifier,
        insight_reader=reader,
    )
    conversation_id = _create_conversation(client)
    _send(
        client, conversation_id, "零基础推荐机器学习资料，不联网", module_id="resources",
    )
    assistant = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)
    assert source.queries == []
    assert discoverer.queries == []
    assert verifier.pages == []
    assert reader.urls == []
    assert assistant["learning_resources"] is None
    assert assistant["error_code"] == "network_not_allowed"
