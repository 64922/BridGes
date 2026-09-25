"""Issue 20：分层本地检索测试基建（GQ-05 迁移）。

构造临时数据目录上的权威数据库 + 加密对象库，经真实摄取状态机把材料
写入三层作用域（聊天附件 / 项目文件 / 知识库），并挂载只读检索服务。
Embedding 使用全局确定性端口（GQ-05：不再播种账户探测状态，可用性
即端口已构造）。
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from bridges.chat.repository import ConversationRepository
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


def make_retrieval_env(storage: dict[str, Any]) -> dict[str, Any]:
    """完整检索环境：摄取服务（确定性 Embedding）+ 检索服务 + 对话仓库。"""
    database = storage["database"]
    repository = storage["repository"]
    embedding = DeterministicEmbeddingPort()
    ingestion = IngestionService(
        database=database,
        object_repository=repository,
        embedding=embedding,
        index=VersionedIndex(database, embedding),
    )
    retrieval = LayeredRetrievalService(
        database=database,
        embedding=embedding,
        object_repository=repository,
    )
    return {
        **storage,
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
    V2 Issue 06：聊天附件发送前是账户级草稿，上传即入队解析（不带
    conversation_id），作用域由发送时的消息绑定决定。
    """
    stored = env["repository"].create_object(
        account_id, filename, content.encode("utf-8"), media_type="text/plain"
    )
    if layer == "attachment":
        # V2 Issue 06：聊天附件发送前是账户级草稿，上传即入队解析（不带
        # conversation_id），作用域由发送时的消息绑定决定。
        env["ingestion"].enqueue(account_id, stored.object_id, source="chat_attachment")
        assert conversation_id is not None
        now = datetime.now(UTC).isoformat(timespec="seconds")
        if user_message_id is None:
            # 尚未发送：镜像真实草稿行（发送时由聊天流程迁移并绑定消息）。
            env["database"].connection.execute(
                "INSERT INTO chat_attachment_drafts"
                " (object_id, account_id, upload_id, original_filename, media_type,"
                "  content_length, content_hash, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, 'text/plain', ?, ?, ?, ?)",
                (
                    stored.object_id,
                    account_id,
                    f"upload-{secrets.token_urlsafe(6)}",
                    filename,
                    len(content.encode("utf-8")),
                    hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    now,
                    now,
                ),
            )
        else:
            # 已绑定到消息（直接调用检索服务的用例不经聊天发送流程）。
            env["database"].connection.execute(
                "INSERT INTO chat_attachments"
                " (object_id, account_id, conversation_id, message_id, upload_id,"
                "  media_type, status, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, 'text/plain', 'bound', ?, ?)",
                (
                    stored.object_id,
                    account_id,
                    conversation_id,
                    user_message_id,
                    f"upload-{secrets.token_urlsafe(6)}",
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
