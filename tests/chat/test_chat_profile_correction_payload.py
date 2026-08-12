from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bridges.ai import ModelGateway
from bridges.ai.adapters import AdapterResult, StreamChunk
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.chat.repository import ConversationRepository
from bridges.chat.service import ChatService
from bridges.contracts.ai import CapabilityKind, CapabilityRecord
from bridges.contracts.profiles import FourDimension, FourDimensionConfidence
from bridges.contracts.projects import ObjectDomain
from bridges.contracts.workflows import RunContextEnvelope
from bridges.profiles import (
    AutomaticProfileService,
    FourDimensionProfileService,
    InMemoryAutomaticProfileRepository,
    InMemoryFourDimensionProfileRepository,
)
from bridges.storage.database import BridgesDatabase


def _capability() -> CapabilityRecord:
    return CapabilityRecord(
        name="qwen_text_chat",
        version="1",
        kind=CapabilityKind.MODEL,
        vendor="qwen",
        region="cn-beijing",
        model_id="qwen3.7-plus-2026-05-26",
        input_schema_version="chat-messages-v1",
        output_schema_version="chat-completion-v1",
    )


class _CaptureAdapter:
    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def call(
        self,
        capability: CapabilityRecord,
        run_context: RunContextEnvelope,
        payload: dict[str, Any],
    ) -> AdapterResult:
        return AdapterResult(actual_model_id=capability.model_id, output={"content": "已处理"})

    def stream_call(
        self,
        capability: CapabilityRecord,
        run_context: RunContextEnvelope,
        payload: dict[str, Any],
    ):
        self.payloads.append(payload)
        yield StreamChunk(kind="delta", delta="已处理")


def _context() -> RunContextEnvelope:
    return RunContextEnvelope(
        run_id="run-chat-correction",
        account_id="account-alice",
        project_id="conversation-1",
        workflow_name="chat",
        workflow_version="1",
        object_domain=ObjectDomain.PERSONAL_VAULT,
        submitted_at=datetime.now(UTC),
    )


def test_chat_persists_correction_before_model_payload(tmp_path: Path) -> None:
    database = BridgesDatabase(tmp_path / "chat-correction.db")
    database.initialize()
    four_dimensions = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=InMemoryFourDimensionProfileRepository(),
    )
    four_dimensions.upsert_automatic_record(
        "account-alice",
        dimension=FourDimension.KNOWLEDGE_INTEREST,
        content="Transformer",
        action="create",
        confidence=FourDimensionConfidence.HIGH,
    )
    automatic = AutomaticProfileService(
        four_dimension_service=four_dimensions,
        repository=InMemoryAutomaticProfileRepository(),
    )
    adapter = _CaptureAdapter()
    registry = CapabilityRegistry()
    registry.register(_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", adapter)
    service = ChatService(
        repository=ConversationRepository(database),
        gateway=gateway,
        automatic_profile_service=automatic,
        four_dimension_profile_service=four_dimensions,
    )

    conversation = service.create_conversation("account-alice")
    _, assistant = service.start_generation(
        "account-alice", conversation.conversation_id, "把我的关注点换成 CNN"
    )
    run = service._repo.get_run_by_message("account-alice", assistant.message_id)
    assert run is not None
    assert run.config["profile_correction"] == {
        "status": "written",
        "dimension": "感兴趣的知识",
    }

    list(
        service.stream_generation(
            "account-alice",
            conversation.conversation_id,
            assistant.message_id,
            _context(),
        )
    )

    assert len(adapter.payloads) == 1
    payload = adapter.payloads[0]
    correction_block = next(
        message["content"]
        for message in payload["messages"]
        if "本轮画像纠正结果" in message["content"]
    )
    assert "written" in correction_block
    assert "CNN" not in correction_block
    assert "record_id" not in correction_block
    assert any("CNN" in message["content"] for message in payload["messages"])
    assert "tools" not in payload
    database.close()
