"""合法学习夹具：真实照片经双路径识别、持久质量门和预习进入辅导。"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any

from bridges.chat.attachments import ChatAttachmentService
from bridges.contracts.ai import ModelCallResult, ModelCallStatus
from bridges.contracts.study import StudyState
from bridges.storage import BridgesObjectRepository, EncryptedFileObjectStore
from bridges.study.service import StudyRepository, StudyWorkflow


class _RecognizedPageGateway:
    """仅替换外部模型响应；附件、识别节点、质量门和阶段迁移均执行正式代码。"""

    def __init__(self, text: str, page_number: int) -> None:
        self.text = text
        self.page_number = page_number
        self.calls: list[str] = []

    def invoke(
        self, capability: str, *args: Any, payload: dict[str, Any], **kwargs: Any
    ) -> ModelCallResult:
        self.calls.append(capability)
        if capability == "qwen_ocr":
            output = {"content": self.text}
        elif capability == "qwen_vision":
            output = {
                "content": json.dumps(
                    {
                        "same_section": True,
                        "page_number": self.page_number,
                        "fragments": [
                            {
                                "kind": "text",
                                "position": "顶部",
                                "text": self.text,
                                "confidence": 0.95,
                            }
                        ],
                        "unclear": [],
                    },
                    ensure_ascii=False,
                )
            }
        elif capability == "qwen_structured_output":
            if '"questions"' in payload["prompt"]:
                output = {
                    "questions": [
                        {
                            "question": "请阅读本节并思考核心概念的关系。",
                            "unit_titles": ["线性函数"],
                        }
                    ]
                }
            else:
                output = {
                    "units": [
                        {
                            "title": "线性函数",
                            "core": True,
                            "fragment_ids": re.findall(r'"id":\s*"([^"]+)"', payload["prompt"]),
                        }
                    ]
                }
        else:
            raise AssertionError(capability)
        return ModelCallResult(status=ModelCallStatus.SUCCESS, output=output)


def seed_recognized_study_state(
    service: Any,
    account_id: str,
    conversation_id: str,
    *,
    object_id: str = "study-photo-page-1",
    text: str = "线性函数 y=ax+b，斜率 a 表示变化率。",
    page_number: int = 12,
) -> StudyState:
    """上传真实照片并执行完整识别/预习；object_id 参数保留为照片名称标识。"""
    database = service._repo.database
    if service._attachments is None:
        objects = BridgesObjectRepository(
            database,
            EncryptedFileObjectStore(
                Path(database.path).parent / "study-fixture-objects",
                encryption_key="study-fixture-secret",
            ),
        )
        account = (
            database.scoped(account_id)
            .execute("SELECT email FROM accounts WHERE account_id = ?", (account_id,))
            .fetchone()
        )
        objects.ensure_account(
            account_id, str(account["email"]) if account else f"{account_id}@study-fixture.example"
        )
        service._attachments = ChatAttachmentService(database, objects)
    attachments = service._require_attachment_service()
    photo = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6"
        "NAAAAABJRU5ErkJggg=="
    )
    draft, _ = attachments.upload_draft(account_id, f"{object_id}.png", photo)
    gateway = _RecognizedPageGateway(text, page_number)
    original_gateway = service._gateway
    service._gateway = gateway
    try:
        _, assistant, _ = service.start_generation(
            account_id,
            conversation_id,
            "",
            attachment_ids=[draft.object_id],
            use_knowledge_base=False,
            use_profile=False,
        )
        run = service._repo.get_run_by_message(account_id, assistant.message_id)
        assert run is not None
        StudyWorkflow(service).run(run, on_event=lambda event: None, stop_event=None)
    finally:
        service._gateway = original_gateway
    state = StudyRepository(database).get(account_id, conversation_id)
    assert state is not None and state.stage == "tutoring"
    assert not state.pending_object_ids and not any(page.unclear for page in state.pages)
    assert gateway.calls == [
        "qwen_ocr",
        "qwen_vision",
        "qwen_structured_output",
        "qwen_structured_output",
    ]
    return state
