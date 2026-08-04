"""Issue 24：跨内容统一桌面搜索测试基建。

复用检索测试的存储模式（临时数据目录 + 加密对象库 + 双账户 + 真实
摄取状态机），并挂载统一搜索服务。全部种子数据经权威数据库写入，
搜索服务只读实时查询。
"""

from __future__ import annotations

import secrets
import struct
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from bridges.chat.repository import ConversationRepository
from bridges.credentials.probes import CapabilityProbeService
from bridges.ingestion.embedding import DeterministicEmbeddingPort
from bridges.ingestion.index import VersionedIndex
from bridges.ingestion.service import IngestionService
from bridges.learning_projects.service import LearningProjectService
from bridges.search import SearchService
from bridges.storage import (
    BridgesDatabase,
    BridgesObjectRepository,
    EncryptedFileObjectStore,
)

SECRET_KEY = "search-test-secret-key"


def make_storage(tmp_path: Path) -> dict[str, Any]:
    """临时数据目录上的权威数据库 + 加密对象库 + 两个测试账户。"""
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    repository = BridgesObjectRepository(
        database,
        EncryptedFileObjectStore(
            tmp_path / "objects", encryption_key=SecretStr(SECRET_KEY)
        ),
    )
    account_a = repository.register_account("77777777@qq.com")
    account_b = repository.register_account("88888888@qq.com")
    return {
        "database": database,
        "repository": repository,
        "account_a": account_a,
        "account_b": account_b,
        "path": tmp_path,
    }


@pytest.fixture()
def storage(tmp_path: Path) -> dict[str, Any]:
    """临时数据目录上的权威数据库 + 加密对象库 + 两个测试账户。"""
    return make_storage(tmp_path)


def make_search_env(storage: dict[str, Any]) -> dict[str, Any]:
    """完整搜索环境：摄取服务（确定性 Embedding）+ 统一搜索服务。"""
    from tests.ingestion.conftest import seed_probe_for_account

    database = storage["database"]
    repository = storage["repository"]
    probe_service = CapabilityProbeService(state_store=None)
    embedding = DeterministicEmbeddingPort()
    for account_id in (storage["account_a"], storage["account_b"]):
        seed_probe_for_account(probe_service, account_id, available=True)
    ingestion = IngestionService(
        database=database,
        object_repository=repository,
        probe_service=probe_service,
        embedding=embedding,
        index=VersionedIndex(database, embedding),
    )
    return {
        **storage,
        "probe_service": probe_service,
        "ingestion": ingestion,
        "search": SearchService(database),
        "conversations": ConversationRepository(database),
    }


@pytest.fixture()
def env(storage: dict[str, Any]) -> dict[str, Any]:
    """完整搜索环境：摄取服务（确定性 Embedding）+ 统一搜索服务。"""
    return make_search_env(storage)


def seed_conversation(
    env: dict[str, Any],
    account_id: str,
    title: str,
    *,
    project_id: str | None = None,
    updated_at: str | None = None,
) -> str:
    """创建一条对话（可选归属学习项目），返回 conversation_id。"""
    conversation_id = f"conversation-{secrets.token_urlsafe(8)}"
    now = updated_at or datetime.now(UTC).isoformat(timespec="seconds")
    env["database"].connection.execute(
        "INSERT INTO conversations"
        " (conversation_id, account_id, title, mode, project_id, created_at, updated_at)"
        " VALUES (?, ?, ?, 'companion', ?, ?, ?)",
        (conversation_id, account_id, title, project_id, now, now),
    )
    return conversation_id


def add_message(
    env: dict[str, Any], account_id: str, conversation_id: str, content: str
) -> str:
    """插入一条已完成用户消息，返回 message_id。"""
    message_id = f"user-msg-{secrets.token_urlsafe(8)}"
    now = datetime.now(UTC).isoformat(timespec="seconds")
    env["database"].connection.execute(
        "INSERT INTO messages"
        " (message_id, conversation_id, account_id, role, attempt_number, status,"
        "  content, created_at, updated_at)"
        " VALUES (?, ?, ?, 'user', 1, 'done', ?, ?, ?)",
        (message_id, conversation_id, account_id, content, now, now),
    )
    return message_id


def seed_project(
    env: dict[str, Any], account_id: str, name: str, description: str | None = None
) -> str:
    """创建学习项目，返回 project_id。"""
    service = LearningProjectService(
        env["database"],
        env["repository"],
        env["ingestion"],
        env["conversations"],
    )
    return service.create_project(account_id, name, description).project_id


def add_material(
    env: dict[str, Any],
    account_id: str,
    filename: str,
    content: str,
    *,
    project_id: str | None = None,
    process: bool = True,
) -> str:
    """上传文本材料到知识库或项目并入队摄取，返回 object_id。"""
    stored = env["repository"].create_object(
        account_id, filename, content.encode("utf-8"), media_type="text/plain"
    )
    if project_id is not None:
        env["ingestion"].enqueue(account_id, stored.object_id, project_id=project_id)
    else:
        env["ingestion"].enqueue(account_id, stored.object_id)
    if process:
        env["ingestion"].process_pending()
    return stored.object_id


def minimal_png(width: int = 640, height: int = 480) -> bytes:
    """构造解析器可识别尺寸的最小 PNG 头（无需真实像素数据）。"""
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + struct.pack(">II", width, height)


def add_image(
    env: dict[str, Any],
    account_id: str,
    filename: str,
    *,
    project_id: str | None = None,
    process: bool = True,
) -> str:
    """上传图片（经同一摄取状态机产生元数据文本分块），返回 object_id。"""
    stored = env["repository"].create_object(
        account_id, filename, minimal_png(), media_type="image/png"
    )
    if project_id is not None:
        env["ingestion"].enqueue(account_id, stored.object_id, project_id=project_id)
    else:
        env["ingestion"].enqueue(account_id, stored.object_id)
    if process:
        env["ingestion"].process_pending()
    return stored.object_id
