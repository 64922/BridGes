"""Integration tests for the real Qwen adapters using cassette playback.

These tests exercise the full model gateway + real adapter stack without
requiring network access. The cassettes in ``tests/ai/cassettes`` are synthetic
and safe to commit; setting ``SCIENCE_COMPANION_QWEN_RECORD_CASSETTES=true``
with a real key will re-record them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from science_companion.ai import (
    CapabilityRegistry,
    CassetteStore,
    ModelGateway,
    QwenApiClient,
    QwenStructuredOutputAdapter,
    QwenTextChatAdapter,
)
from science_companion.config import get_settings
from science_companion.contracts.ai import (
    CapabilityKind,
    CapabilityRecord,
    ModelCallStatus,
)
from science_companion.contracts.workflows import RunContextEnvelope


def _context() -> RunContextEnvelope:
    return RunContextEnvelope(
        run_id="run-cassette",
        account_id="account-1",
        project_id="project-1",
        workflow_name="generic_science_task",
        workflow_version="1",
        submitted_at=datetime.now(UTC),
    )


def _build_gateway() -> ModelGateway:
    registry = CapabilityRegistry()
    registry.register(
        CapabilityRecord(
            name="qwen_text_chat",
            version="1",
            kind=CapabilityKind.MODEL,
            vendor="qwen",
            region="cn-beijing",
            model_id="qwen3.7-plus",
            input_schema_version="chat-messages-v1",
            output_schema_version="chat-completion-v1",
        )
    )
    registry.register(
        CapabilityRecord(
            name="qwen_structured_output",
            version="1",
            kind=CapabilityKind.MODEL,
            vendor="qwen",
            region="cn-beijing",
            model_id="qwen3.6-flash",
            input_schema_version="structured-messages-v1",
            output_schema_version="json-schema-v1",
        )
    )

    settings = get_settings()
    cassette_dir = (
        Path(settings.qwen_cassette_dir)
        if settings.qwen_cassette_dir
        else Path(__file__).with_suffix("").parent / "cassettes"
    )
    client = QwenApiClient(
        api_key=settings.qwen_api_key,
        workspace_id=settings.qwen_workspace_id,
        region=settings.qwen_region,
        cassette_store=CassetteStore(cassette_dir),
        record_mode=settings.qwen_record_cassettes,
    )
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", QwenTextChatAdapter(client))
    gateway.register_adapter(
        "qwen_structured_output", "1", QwenStructuredOutputAdapter(client)
    )
    return gateway


def test_text_chat_with_cassette() -> None:
    gateway = _build_gateway()
    result = gateway.invoke(
        "qwen_text_chat",
        "1",
        _context(),
        payload={"node_id": "compile_context"},
    )

    assert result.status == ModelCallStatus.SUCCESS
    assert result.lock is not None
    assert result.lock.actual_model_id == "qwen3.7-plus"
    assert result.lock.region == "cn-beijing"
    assert result.output == {"content": "上下文已编译。"}


def test_structured_output_with_cassette() -> None:
    gateway = _build_gateway()
    result = gateway.invoke(
        "qwen_structured_output",
        "1",
        _context(),
        payload={"node_id": "produce_output"},
    )

    assert result.status == ModelCallStatus.SUCCESS
    assert result.lock is not None
    assert result.lock.actual_model_id == "qwen3.6-flash"
    assert result.output == {"summary": "科学解释", "claims": []}
