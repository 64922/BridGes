"""聊天服务与运行预算账本共享 fixture。"""

from __future__ import annotations

from pathlib import Path

import pytest

from bridges.ai import ModelGateway
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.chat.repository import ConversationRepository
from bridges.chat.run_budget_ledger import RunBudgetLedgerRepository
from bridges.chat.service import ChatService
from bridges.storage.database import BridgesDatabase
from tests.chat.chat_service_support import _chat_capability, _ProgrammableStreamAdapter


@pytest.fixture
def database(tmp_path: Path) -> BridgesDatabase:
    db = BridgesDatabase(str(tmp_path / "bridges.db"))
    db.initialize()
    return db


@pytest.fixture
def repo(database: BridgesDatabase) -> RunBudgetLedgerRepository:
    return RunBudgetLedgerRepository(database)


@pytest.fixture
def service(tmp_path: Path) -> ChatService:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    repository = ConversationRepository(database)
    registry = CapabilityRegistry()
    registry.register(_chat_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter(
        "qwen_text_chat", "1", _ProgrammableStreamAdapter()
    )
    return ChatService(repository=repository, gateway=gateway)
