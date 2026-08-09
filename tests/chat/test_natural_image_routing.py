"""Issue 08：普通自然语言图片路由的确定性合同测试。"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest
from pydantic import ValidationError
from pydantic import SecretStr

from bridges.chat.routing import NaturalLanguageImageRouter, image_request_from_decision
from bridges.contracts.chat import ImageRequestPayload
from bridges.contracts.routing import ImageRouteContract, RouteOperation
from bridges.ingestion.embedding import DeterministicEmbeddingPort
from bridges.ingestion.index import VersionedIndex
from bridges.ingestion.service import IngestionService
from bridges.storage import (
    BridgesDatabase,
    BridgesObjectRepository,
    EncryptedFileObjectStore,
)


def _png() -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + struct.pack(">II", 640, 480)


def _environment(tmp_path: Path) -> tuple[BridgesDatabase, BridgesObjectRepository, IngestionService, str, str]:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    objects = BridgesObjectRepository(
        database,
        EncryptedFileObjectStore(
            tmp_path / "objects", encryption_key=SecretStr("route-test-secret")
        ),
    )
    account_a = objects.register_account("route-a@example.com")
    account_b = objects.register_account("route-b@example.com")
    embedding = DeterministicEmbeddingPort()
    ingestion = IngestionService(
        database=database,
        object_repository=objects,
        embedding=embedding,
        index=VersionedIndex(database, embedding),
    )
    return database, objects, ingestion, account_a, account_b


def _add(
    objects: BridgesObjectRepository,
    ingestion: IngestionService,
    account_id: str,
    filename: str,
    content: bytes,
    media_type: str,
) -> str:
    stored = objects.create_object(account_id, filename, content, media_type=media_type)
    ingestion.enqueue(account_id, stored.object_id)
    ingestion.process_pending()
    return stored.object_id


def test_generation_and_edit_compile_versioned_contracts(tmp_path: Path) -> None:
    database, objects, ingestion, account_a, account_b = _environment(tmp_path)
    image_id = _add(objects, ingestion, account_a, "实验室.png", _png(), "image/png")
    _add(
        objects,
        ingestion,
        account_a,
        "论文.pdf",
        b"%PDF-1.7\nprivate paper",
        "application/pdf",
    )
    router = NaturalLanguageImageRouter(database)

    generated = router.route(account_a, "生成一张小猫的图")
    assert generated is not None
    assert generated.operation == RouteOperation.GENERATE
    assert generated.contract is not None
    assert generated.contract.source_object_id is None
    wanted = router.route(account_a, "我想要一张图")
    assert wanted is not None
    assert wanted.operation == RouteOperation.GENERATE
    assert "小猫" in generated.contract.prompt
    assert image_request_from_decision(generated)["source_scope"] is None  # type: ignore[index]

    edited = router.route(account_a, "把实验室.png的背景换成实验室")
    assert edited is not None
    assert edited.operation == RouteOperation.EDIT
    assert edited.contract is not None
    assert edited.contract.source_object_id == image_id
    assert image_request_from_decision(edited)["source_scope"] == "knowledge_base"  # type: ignore[index]

    # 路由只读取当前账户的知识库对象，不能从另一账户推断或泄漏来源。
    other_account = router.route(account_b, "把实验室.png的背景换成实验室")
    assert other_account is not None
    assert other_account.operation == RouteOperation.CLARIFY
    assert "实验室.png" not in (other_account.clarification_question or "")


def test_ambiguous_unsupported_and_conflicting_requests_do_not_fall_back(
    tmp_path: Path,
) -> None:
    database, objects, ingestion, account_a, _ = _environment(tmp_path)
    _add(objects, ingestion, account_a, "一号.png", _png(), "image/png")
    _add(objects, ingestion, account_a, "二号.png", _png(), "image/png")
    _add(
        objects,
        ingestion,
        account_a,
        "论文.pdf",
        b"%PDF-1.7\nprivate paper",
        "application/pdf",
    )
    router = NaturalLanguageImageRouter(database)

    ambiguous = router.route(account_a, "把这张图的背景换成实验室")
    assert ambiguous is not None
    assert ambiguous.operation == RouteOperation.CLARIFY
    assert ambiguous.clarification_question

    missing_source = router.route(account_a, "把背景换成实验室")
    assert missing_source is not None
    assert missing_source.operation == RouteOperation.CLARIFY

    unsupported = router.route(account_a, "编辑论文.pdf")
    assert unsupported is not None
    assert unsupported.operation == RouteOperation.REJECT
    assert unsupported.error_code == "unsupported_source"

    conflict = router.route(account_a, "生成一张小猫的图，同时生成视频")
    assert conflict is not None
    assert conflict.operation == RouteOperation.CLARIFY
    assert conflict.contract is None

    assert router.route(account_a, "图片格式有哪些？") is None


def test_image_payload_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ImageRequestPayload.model_validate(
            {"kind": "generate", "prompt": "一只猫", "unknown": "x"}
        )


def test_image_route_contract_rejects_mutually_inconsistent_sources() -> None:
    with pytest.raises(ValidationError):
        ImageRouteContract(
            operation=RouteOperation.GENERATE,
            prompt="一只猫",
            source_object_id="object-1",
        )
    with pytest.raises(ValidationError):
        ImageRouteContract(operation=RouteOperation.EDIT, prompt="换成夜景")
