"""Issue 23：学习模式在证据不足时的聊天编排集成测试。"""

from datetime import UTC, datetime
from pathlib import Path

from bridges.ai import ModelGateway
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.chat.repository import ConversationRepository
from bridges.chat.service import ChatService
from bridges.contracts.chat import ChatMessageStatus, ChatMode
from bridges.contracts.projects import ObjectDomain
from bridges.contracts.workflows import RunContextEnvelope
from bridges.storage.database import BridgesDatabase


def test_study_mode_persists_teaching_gate_and_does_not_use_model_without_evidence(
    tmp_path: Path,
) -> None:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    service = ChatService(
        repository=ConversationRepository(database),
        gateway=ModelGateway(CapabilityRegistry()),
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
    assert final.content == final.teaching.gap_response
    assert [event.kind for event in events] == ["delta", "done"]
