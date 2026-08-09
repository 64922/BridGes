"""Issue 11：摄取服务只接受全局知识库来源。"""

from __future__ import annotations

import pytest

from bridges.ingestion.service import IngestionError

from tests.ingestion.conftest import make_ingestion, upload_text


def test_ingestion_rejects_legacy_sources_and_keeps_knowledge_base_source(storage) -> None:
    service, _ = make_ingestion(storage, embedding_available=True)
    account_id = storage["account_a"]
    object_id = upload_text(storage, account_id, "材料.txt", "知识库材料".encode())

    with pytest.raises(IngestionError) as conversation_error:
        service.enqueue(account_id, object_id, "conversation-legacy")
    assert conversation_error.value.status_code == 410
    assert conversation_error.value.code == "legacy_file_source_retired"

    with pytest.raises(IngestionError) as project_error:
        service.enqueue(account_id, object_id, None, project_id="project-legacy")
    assert project_error.value.status_code == 410
    assert project_error.value.code == "legacy_file_source_retired"

    service.enqueue(account_id, object_id, None)
    row = storage["database"].scoped(account_id).execute(
        "SELECT source, conversation_id, project_id FROM document_records"
        " WHERE account_id = ? AND object_id = ?",
        (account_id, object_id),
    ).fetchone()
    assert row is not None
    assert row["source"] == "knowledge_base"
    assert row["conversation_id"] is None
    assert row["project_id"] is None
