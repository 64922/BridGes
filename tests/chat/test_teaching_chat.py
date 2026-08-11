"""Issue 23：学习模式在证据不足时的聊天编排集成测试。"""

from datetime import UTC, datetime
from pathlib import Path

from bridges.ai import ModelGateway
from bridges.ai.adapters import StreamChunk
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.chat.repository import ConversationRepository
from bridges.chat.service import ChatService
from bridges.contracts.ai import CapabilityKind, CapabilityRecord
from bridges.contracts.chat import ChatMessageStatus, ChatMode
from bridges.contracts.projects import ObjectDomain
from bridges.contracts.workflows import RunContextEnvelope
from bridges.storage.database import BridgesDatabase


class _FallbackAdapter:
    def __init__(self) -> None:
        self.payloads: list[dict[str, object]] = []

    def stream_call(
        self,
        capability: CapabilityRecord,
        run_context: object,
        payload: dict[str, object],
    ):
        self.payloads.append(payload)
        yield StreamChunk(
            kind="delta",
            delta=(
                "卷积神经网络通常用于从局部模式开始提取特征。"
                "[web-1] https://fake.example/source [arxiv-1] https://arxiv.org/abs/1"
            ),
        )
        yield StreamChunk(kind="done")


def test_study_mode_marks_unverified_fallback_and_calls_model_without_evidence(
    tmp_path: Path,
) -> None:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    adapter = _FallbackAdapter()
    registry = CapabilityRegistry()
    registry.register(
        CapabilityRecord(
            name="qwen_text_chat",
            version="1",
            kind=CapabilityKind.MODEL,
            vendor="qwen",
            region="cn-beijing",
            model_id="qwen3.7-plus-2026-05-26",
            input_schema_version="chat-messages-v1",
            output_schema_version="chat-completion-v1",
        )
    )
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", adapter)
    service = ChatService(
        repository=ConversationRepository(database),
        gateway=gateway,
    )
    conversation = service.create_conversation("alice", mode=ChatMode.STUDY)
    _, assistant = service.start_generation(
        "alice", conversation.conversation_id, "解释量子纠缠"
    )

    events = list(
        service.stream_generation(
            "alice",
            conversation.conversation_id,
            assistant.message_id,
            RunContextEnvelope(
                run_id="run-1",
                account_id="alice",
                project_id="conversation-1",
                workflow_name="chat",
                workflow_version="1",
                object_domain=ObjectDomain.PERSONAL_VAULT,
                submitted_at=datetime.now(UTC),
            ),
        )
    )

    final = service.message_projection("alice", assistant.message_id)
    assert final is not None
    assert final.status == ChatMessageStatus.DONE
    assert final.teaching is not None
    assert final.teaching.can_answer_reliably is False
    assert final.teaching.evidence_gate.gap
    assert final.teaching.evidence_gate.allow_model_knowledge is True
    assert final.content.startswith("本轮未联网核实：")
    assert "卷积神经网络" in final.content
    assert "[web-1]" not in final.content
    assert "https://fake.example/source" not in final.content
    assert "[arxiv-1]" not in final.content
    assert "https://arxiv.org/abs/1" not in final.content
    assert adapter.payloads
    assert "允许使用模型一般知识" in str(adapter.payloads[0])
    assert final.content != final.teaching.gap_response
    assert [event.kind for event in events if event.kind != "stage"] == [
        "delta",
        "delta",
        "done",
    ]
