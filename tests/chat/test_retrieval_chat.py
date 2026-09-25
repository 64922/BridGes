"""聊天 × 分层检索集成测试：生成前检索、最小上下文注入与引用持久化（Issue 20）。

验证：发送消息时生成前执行检索并把引用固化为消息投影；检索材料以独立
system 块注入模型（不编造文件/页码）；思考摘要区分引用依据与模型组织
说明；重试生成新检索轮次；关闭知识库时请求与引用不含其候选；无检索
作用域时不产生轮次。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from bridges.ai import ModelGateway
from bridges.ai.adapters import StreamChunk
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.chat.repository import ConversationRepository
from bridges.chat.service import ChatService
from bridges.contracts.ai import CapabilityKind, CapabilityRecord
from bridges.contracts.chat import ChatMessageRole, ChatMessageStatus
from bridges.contracts.projects import ObjectDomain
from bridges.contracts.workflows import RunContextEnvelope
from bridges.storage.database import BridgesDatabase
from tests.retrieval.conftest import (
    add_material,
    seed_conversation,
)


def _chat_capability() -> CapabilityRecord:
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


class _CapturingStreamAdapter:
    """捕获模型载荷的流式适配器（验证检索上下文注入）。"""

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def stream_call(
        self,
        capability: CapabilityRecord,
        run_context: Any,
        payload: dict[str, Any],
    ):
        self.payloads.append(payload)
        yield StreamChunk(kind="delta", delta="基于材料回答。")
        yield StreamChunk(kind="done")


def _context(run_id: str = "run-1") -> RunContextEnvelope:
    return RunContextEnvelope(
        run_id=run_id,
        account_id="alice",
        project_id="conversation-1",
        workflow_name="chat",
        workflow_version="1",
        object_domain=ObjectDomain.PERSONAL_VAULT,
        submitted_at=datetime.now(UTC),
    )


@pytest.fixture
def chat_env(tmp_path: Path) -> dict[str, Any]:
    """聊天 + 检索组合环境（确定性 Embedding 与账户级探测）。"""
    from bridges.chat.attachments import ChatAttachmentService
    from tests.retrieval.conftest import make_retrieval_env, make_storage

    env = make_retrieval_env(make_storage(tmp_path))
    database: BridgesDatabase = env["database"]
    retrieval = env["retrieval"]
    attachment_service = ChatAttachmentService(database, env["repository"])
    registry = CapabilityRegistry()
    registry.register(_chat_capability())
    gateway = ModelGateway(registry)
    adapter = _CapturingStreamAdapter()
    gateway.register_adapter("qwen_text_chat", "1", adapter)
    service = ChatService(
        repository=ConversationRepository(database),
        gateway=gateway,
        attachment_service=attachment_service,
        retrieval_service=retrieval,
    )
    return {
        "database": database,
        "repository": env["repository"],
        "account_a": env["account_a"],
        "account_b": env["account_b"],
        "ingestion": env["ingestion"],
        "retrieval": retrieval,
        "chat": service,
        "adapter": adapter,
    }


def _send(chat_env: dict[str, Any], conversation_id: str, content: str, *,
          use_knowledge_base: bool = True,
          attachment_ids: list[str] | None = None) -> tuple[Any, Any, list[Any]]:
    """发送一条消息并消费全部流事件，返回 (用户消息, 助手消息, 事件列表)。"""
    user, assistant, _ = chat_env["chat"].start_generation(
        chat_env["account_a"], conversation_id, content,
        attachment_ids=attachment_ids or [],
        use_knowledge_base=use_knowledge_base,
    )
    events = list(
        chat_env["chat"].stream_generation(
            chat_env["account_a"],
            conversation_id,
            assistant.message_id,
            _context(),
            until_user_message_id=user.message_id,
            use_knowledge_base=use_knowledge_base,
        )
    )
    final = chat_env["chat"].message_projection(
        chat_env["account_a"], assistant.message_id
    )
    assert final is not None
    return user, final, events


def test_generation_runs_retrieval_injects_context_and_persists_citations(
    chat_env: dict[str, Any],
) -> None:
    account = chat_env["account_a"]
    conversation_id = seed_conversation(chat_env, account)
    object_id = add_material(
        chat_env, account, "热力学讲义.txt", "热力学第二定律：熵在孤立系统中永不减少。",
        layer="attachment", conversation_id=conversation_id,
    )

    _, final, events = _send(
        chat_env, conversation_id, "热力学第二定律是什么",
        attachment_ids=[object_id],
    )
    assert final.status == ChatMessageStatus.DONE
    assert final.retrieval is not None
    assert len(final.retrieval.citations) == 1
    citation = final.retrieval.citations[0]
    assert citation.filename == "热力学讲义.txt"
    assert citation.source_layer.value == "attachment"
    assert "熵在孤立系统中永不减少" in citation.snippet

    # 最小上下文注入：独立 system 块，明确标注材料来源
    payload = chat_env["adapter"].payloads[0]
    system_contents = [
        m["content"] for m in payload["messages"] if m["role"] == "system"
    ]
    assert any("热力学讲义.txt" in content for content in system_contents)
    assert any("不得声称存在未提供的文件或页码" in content for content in system_contents)

    # 思考摘要区分引用依据（evidence）与模型组织说明（steps 不变）
    assert final.thinking is not None
    assert any("引用了「热力学讲义.txt」" in e for e in final.thinking.evidence)
    assert any("已检索当前附件" in t for t in final.thinking.tools)
    assert final.thinking.steps


def test_retry_reuses_retrieval_round_and_citations(chat_env: dict[str, Any]) -> None:
    account = chat_env["account_a"]
    conversation_id = seed_conversation(chat_env, account)
    add_material(chat_env, account, "知识库.txt", "热力学第二定律内容。", layer="knowledge_base")

    # Issue 07 起裸学科名词不再单独触发检索，改用点名材料的请求形态。
    user, first, _ = _send(chat_env, conversation_id, "根据我的材料，热力学第二定律讲了什么？")
    assert first.retrieval is not None
    first_round_id = first.retrieval.round_id

    # 重试：新助手尝试 + 新检索轮次，历史尝试与轮次原样保留
    _, retried, _ = chat_env["chat"].retry_generation(
        account, conversation_id, first.message_id
    )
    assert retried.message_id != first.message_id
    list(
        chat_env["chat"].stream_generation(
            account,
            conversation_id,
            retried.message_id,
            _context(),
            until_user_message_id=user.message_id,
        )
    )
    final = chat_env["chat"].message_projection(account, retried.message_id)
    assert final is not None
    assert final.retrieval is not None
    assert final.retrieval.round_id == first_round_id
    assert final.retrieval_decision is not None
    assert first.retrieval_decision is not None
    assert final.retrieval_decision.decision_id == first.retrieval_decision.decision_id
    # 同作用域同结果（仅引用标识为新轮次生成，展示数据一致）
    def citation_facts(round_) -> list[tuple[str, str, str]]:
        return [(c.filename, c.object_id, c.snippet) for c in round_.citations]

    assert citation_facts(final.retrieval) == citation_facts(first.retrieval)
    # 旧尝试的轮次仍在
    old = chat_env["chat"].message_projection(account, first.message_id)
    assert old is not None and old.retrieval is not None
    assert old.retrieval.round_id == first_round_id


def test_kb_disabled_round_has_no_kb_candidates(chat_env: dict[str, Any]) -> None:
    account = chat_env["account_a"]
    conversation_id = seed_conversation(chat_env, account)
    object_id = add_material(
        chat_env, account, "附件.txt", "热力学第二定律附件内容。",
        layer="attachment", conversation_id=conversation_id,
    )
    add_material(
        chat_env, account, "知识库.txt", "热力学第二定律知识库内容。",
        layer="knowledge_base",
    )

    _, final, _ = _send(
        chat_env, conversation_id, "热力学第二定律",
        use_knowledge_base=False, attachment_ids=[object_id],
    )
    assert final.retrieval is not None
    assert final.retrieval.use_knowledge_base is False
    assert all(c.source_layer.value != "knowledge_base" for c in final.retrieval.citations)
    # 注入模型的上下文不包含知识库材料（附件材料保留）
    payload = chat_env["adapter"].payloads[0]
    system_contents = [m["content"] for m in payload["messages"] if m["role"] == "system"]
    assert all("知识库.txt" not in content for content in system_contents)
    assert any("附件.txt" in content for content in system_contents)


def test_no_scope_yields_no_retrieval_round(chat_env: dict[str, Any]) -> None:
    account = chat_env["account_a"]
    conversation_id = seed_conversation(chat_env, account)
    _, final, _ = _send(chat_env, conversation_id, "你好")
    assert final.retrieval is None
    assert final.retrieval_decision is not None
    assert final.retrieval_decision.action.value == "skip"
    payload = chat_env["adapter"].payloads[0]
    assert all(m["role"] != "system" or "以下是本轮检索到的本地材料" not in m["content"]
               for m in payload["messages"])


def test_message_projection_survives_reload(chat_env: dict[str, Any]) -> None:
    account = chat_env["account_a"]
    conversation_id = seed_conversation(chat_env, account)
    add_material(chat_env, account, "知识库.txt", "热力学内容。", layer="knowledge_base")
    # 同上：请求形态点名材料，检索才会触发（裸名词在 Issue 07 后不触发）。
    _, final, _ = _send(chat_env, conversation_id, "根据我的材料，热力学讲了什么？")
    assert final.retrieval is not None

    # 重新打开对话（模拟刷新/重启）后引用仍指向相同来源
    conversation = chat_env["chat"].get_conversation(account, conversation_id)
    assert conversation is not None
    assistant = next(
        m for m in conversation.messages if m.role == ChatMessageRole.ASSISTANT
    )
    assert assistant.retrieval is not None
    assert assistant.retrieval.citations == final.retrieval.citations
