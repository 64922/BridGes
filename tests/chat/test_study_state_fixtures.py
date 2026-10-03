"""旧学习夹具必须留下正式照片附件与通过质量门的持久节点证据。"""

from pathlib import Path

from bridges.contracts.chat import ChatMode
from bridges.kernel.contracts import NodeReceiptStatus, QualityVerdict
from bridges.kernel.repository import NodeKernelRepository
from tests.chat.study_state_fixtures import seed_recognized_study_state
from tests.chat.test_expression_fact_drift import _service_with_answer


def test_fixture_creates_bound_photo_and_verified_receipts(tmp_path: Path) -> None:
    service = _service_with_answer(tmp_path, "测试回答")
    conversation = service.create_conversation("alice", mode=ChatMode.STUDY)
    original_gateway = service._gateway
    state = seed_recognized_study_state(service, "alice", conversation.conversation_id)
    assert service._gateway is original_gateway
    page = state.pages[0]
    attachment, content = service._attachments.download(
        "alice", conversation.conversation_id, page.object_id
    )
    assert content.startswith(b"\x89PNG\r\n\x1a\n")
    assert attachment.content_hash == page.content_hash
    assert attachment.message_id is not None
    assert service._attachments.get("bob", conversation.conversation_id, page.object_id) is None
    artifacts = NodeKernelRepository(service._repo.database).list_artifacts(
        "alice", conversation.conversation_id
    )
    assert {item.node for item in artifacts} == {
        "study.validate_pages",
        "study.recognize_page",
        "study.verify_recognition",
        "study.map",
        "study.verify_scope",
        "study.preview",
    }
    receipts = NodeKernelRepository(service._repo.database).list_receipts(
        "alice", artifacts[0].run_id
    )
    assert len(receipts) == 6
    assert all(item.status == NodeReceiptStatus.COMPLETED for item in receipts)
    assert all(item.quality_verdict == QualityVerdict.PASS for item in receipts)
