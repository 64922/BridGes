"""Issue 20：分层本地检索测试基建。

构造临时数据目录上的权威数据库 + 加密对象库，经真实摄取状态机把材料
写入三层作用域（聊天附件 / 项目文件 / 知识库），并挂载只读检索服务
（确定性 Embedding + 账户级探测）。
"""

from __future__ import annotations

import secrets
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
from bridges.retrieval.service import LayeredRetrievalService
from bridges.storage import (
    BridgesDatabase,
    BridgesObjectRepository,
    EncryptedFileObjectStore,
)

SECRET_KEY = "retrieval-test-secret-key"


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
    account_a = repository.register_account("33333333@qq.com")
    account_b = repository.register_account("44444444@qq.com")
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


def _probe_available(
    probe_service: CapabilityProbeService, account_id: str
) -> None:
    """为账户写入 Embedding 可用探测状态（供 availability 门使用）。"""
    from tests.ingestion.conftest import seed_probe_for_account

    seed_probe_for_account(probe_service, account_id, available=True)


def make_retrieval_env(storage: dict[str, Any]) -> dict[str, Any]:
    """完整检索环境：摄取服务（确定性 Embedding）+ 检索服务 + 对话仓库。"""
    database = storage["database"]
    repository = storage["repository"]
    probe_service = CapabilityProbeService(state_store=None)
    embedding = DeterministicEmbeddingPort()
    for account_id in (storage["account_a"], storage["account_b"]):
        _probe_available(probe_service, account_id)
    ingestion = IngestionService(
        database=database,
        object_repository=repository,
        probe_service=probe_service,
        embedding=embedding,
        index=VersionedIndex(database, embedding),
    )
    retrieval = LayeredRetrievalService(
        database=database,
        embedding=embedding,
        probe_service=probe_service,
        object_repository=repository,
    )
    return {
        **storage,
        "probe_service": probe_service,
        "embedding": embedding,
        "ingestion": ingestion,
        "retrieval": retrieval,
        "conversations": ConversationRepository(database),
    }


@pytest.fixture()
def env(storage: dict[str, Any]) -> dict[str, Any]:
    """完整检索环境：摄取服务（确定性 Embedding）+ 检索服务 + 对话仓库。"""
    return make_retrieval_env(storage)


def seed_conversation(
    env: dict[str, Any], account_id: str, *, project_id: str | None = None
) -> str:
    """创建一条对话（可选归属学习项目），返回 conversation_id。"""
    conversation_id = f"conversation-{secrets.token_urlsafe(8)}"
    now = datetime.now(UTC).isoformat(timespec="seconds")
    env["database"].connection.execute(
        "INSERT INTO conversations"
        " (conversation_id, account_id, title, mode, project_id, created_at, updated_at)"
        " VALUES (?, ?, ?, 'companion', ?, ?, ?)",
        (conversation_id, account_id, "测试对话", project_id, now, now),
    )
    return conversation_id


def seed_project(
    env: dict[str, Any], account_id: str, name: str = "测试项目"
) -> str:
    """创建学习项目，返回 project_id。"""
    service = LearningProjectService(
        env["database"],
        env["repository"],
        env["ingestion"],
        env["conversations"],
    )
    return service.create_project(account_id, name, None).project_id


def add_material(
    env: dict[str, Any],
    account_id: str,
    filename: str,
    content: str,
    *,
    layer: str,
    conversation_id: str | None = None,
    project_id: str | None = None,
    user_message_id: str | None = None,
    process: bool = True,
) -> str:
    """上传文本材料到指定层并入队摄取，返回 object_id。

    layer 取 ``attachment`` / ``project`` / ``knowledge_base``；附件层需要
    ``conversation_id`` 与 ``user_message_id`` 以写入 chat_attachments 绑定。
    """
    stored = env["repository"].create_object(
        account_id, filename, content.encode("utf-8"), media_type="text/plain"
    )
    if layer == "attachment":
        env["ingestion"].enqueue(account_id, stored.object_id, conversation_id)
        assert conversation_id is not None
        now = datetime.now(UTC).isoformat(timespec="seconds")
        # 镜像真实上传流程：先建 uploaded 未绑定行（供发送时校验绑定），
        # 传入 user_message_id 时直接绑定为 bound。
        env["database"].connection.execute(
            "INSERT INTO chat_attachments"
            " (object_id, account_id, conversation_id, message_id, upload_id,"
            "  media_type, status, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, 'text/plain', ?, ?, ?)",
            (
                stored.object_id,
                account_id,
                conversation_id,
                user_message_id,
                f"upload-{secrets.token_urlsafe(6)}",
                "bound" if user_message_id is not None else "uploaded",
                now,
                now,
            ),
        )
    elif layer == "project":
        env["ingestion"].enqueue(account_id, stored.object_id, project_id=project_id)
    else:
        env["ingestion"].enqueue(account_id, stored.object_id)
    if process:
        env["ingestion"].process_pending()
    return stored.object_id


def add_user_message(
    env: dict[str, Any],
    account_id: str,
    conversation_id: str,
    content: str,
    *,
    attachment_object_ids: list[str] | None = None,
) -> str:
    """插入一条已完成用户消息（附件层绑定需要），返回 message_id。"""
    message_id = f"user-msg-{secrets.token_urlsafe(8)}"
    now = datetime.now(UTC).isoformat(timespec="seconds")
    env["database"].connection.execute(
        "INSERT INTO messages"
        " (message_id, conversation_id, account_id, role, attempt_number, status,"
        "  content, created_at, updated_at)"
        " VALUES (?, ?, ?, 'user', 1, 'done', ?, ?, ?)",
        (message_id, conversation_id, account_id, content, now, now),
    )
    for object_id in attachment_object_ids or []:
        binding_id = f"bind-{secrets.token_urlsafe(8)}"
        now2 = datetime.now(UTC).isoformat(timespec="seconds")
        env["database"].connection.execute(
            "INSERT INTO chat_attachments"
            " (object_id, account_id, conversation_id, message_id, upload_id,"
            "  media_type, status, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, 'text/plain', 'bound', ?, ?)",
            (
                object_id,
                account_id,
                conversation_id,
                message_id,
                binding_id,
                now2,
                now2,
            ),
        )
    return message_id
