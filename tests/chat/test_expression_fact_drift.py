"""自然表达事实漂移回归（V2 issue 04 AC4）。

用包含精确事实、来源和代码的例子验证：轻量表达策略与确定性保护区
恢复共同保证「表达调整不改写事实」——模型把用户原句中的数值、单位、
行内代码、代码围栏、公式、链接与引用改写或换写时，最终落库正文按原句
逐字恢复；表达规则本身不改变这些受保护片段。

覆盖两层：
1. 链路级（``ChatService`` 真实生成链，两种模式）：流式增量与终态两次
   恢复后，助手消息内容与用户原句的受保护片段逐字一致。
2. 策略级：两种模式（日常陪伴/学习）的表达合同都声明受保护区与
   「不得改写证据」边界。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from bridges.ai import ModelGateway
from bridges.ai.adapters import StreamChunk
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.chat.global_writing_policy import GlobalWritingPolicyCompiler
from bridges.chat.repository import ConversationRepository
from bridges.chat.service import ChatService
from bridges.contracts.ai import CapabilityKind, CapabilityRecord
from bridges.contracts.chat import ChatMessageStatus, ChatMode
from bridges.contracts.projects import ObjectDomain
from bridges.contracts.workflows import RunContextEnvelope
from bridges.storage.database import BridgesDatabase

#: 用户原句：包含精确事实（带单位数值）、来源（链接与编号引用）、
#: 行内代码、代码围栏与公式——全部属于受保护区。
USER_QUERY = (
    "帮我自然地讲解下面这段实验记录，不要改动事实：\n"
    "光速取 299792.458 km/s；质量能量关系是 $E=mc^2$；\n"
    "采样脚本里 `result = 42` 这行是校验值，完整脚本如下：\n"
    "```python\n"
    "result = 42\n"
    "print(result)\n"
    "```\n"
    "数据来源见 https://example.com/a?q=1 （报告第 3 节 [1]）。"
)

#: 原句中必须逐字保留的受保护片段。
PROTECTED_FRAGMENTS = (
    "299792.458 km/s",
    "$E=mc^2$",
    "`result = 42`",
    "```python\nresult = 42\nprint(result)\n```",
    "https://example.com/a?q=1",
    "[1]",
)

#: 模型「表达调整」时产生的漂移写法（同类片段被换写）。
DRIFTED_ANSWER = (
    "好的，帮你顺一下：光速大约是 299792.459 km/s；质量能量关系是 $E=mc^3$；\n"
    "采样脚本里 `result = 0` 这行是校验值，完整脚本如下：\n"
    "```python\n"
    "result = 0\n"
    "print(0)\n"
    "```\n"
    "数据来源见 https://example.com/b （报告第 4 节 [2]）。"
)


def _context(run_id: str = "run-drift-1") -> RunContextEnvelope:
    return RunContextEnvelope(
        run_id=run_id,
        account_id="alice",
        project_id="conversation-drift",
        workflow_name="chat",
        workflow_version="1",
        object_domain=ObjectDomain.PERSONAL_VAULT,
        submitted_at=datetime.now(UTC),
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


class _DriftingStreamAdapter:
    """把受保护片段改写成漂移写法的流式适配器（模拟表达调整失控）。"""

    def __init__(self, answer: str) -> None:
        self._answer = answer
        self.payloads: list[dict[str, Any]] = []

    def call(self, capability: CapabilityRecord, run_context: Any, payload: dict[str, Any]) -> Any:
        from bridges.ai.adapters import AdapterResult

        return AdapterResult(
            actual_model_id=capability.model_id, output={"content": self._answer}
        )

    def stream_call(
        self,
        capability: CapabilityRecord,
        run_context: Any,
        payload: dict[str, Any],
    ):
        self.payloads.append(payload)
        # 按 12 字符分块流式输出，覆盖「流式增量恢复」路径。
        for index in range(0, len(self._answer), 12):
            yield StreamChunk(kind="delta", delta=self._answer[index : index + 12])


@pytest.fixture
def service(tmp_path: Path) -> ChatService:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    repository = ConversationRepository(database)
    registry = CapabilityRegistry()
    registry.register(_chat_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter(
        "qwen_text_chat", "1", _DriftingStreamAdapter(DRIFTED_ANSWER)
    )
    return ChatService(repository=repository, gateway=gateway)


@pytest.mark.parametrize("mode", [ChatMode.COMPANION, ChatMode.STUDY])
def test_drifted_facts_are_restored_verbatim_in_generation_chain(
    service: ChatService, mode: ChatMode
) -> None:
    """链路级：两种模式下模型换写受保护片段后，落库正文按用户原句逐字恢复。"""
    created = service.create_conversation("alice", mode=mode)
    _, assistant = service.start_generation("alice", created.conversation_id, USER_QUERY)
    list(
        service.stream_generation(
            "alice", created.conversation_id, assistant.message_id, _context()
        )
    )

    message = service._repo.get_message("alice", assistant.message_id)  # noqa: SLF001
    assert message is not None
    assert message.status == ChatMessageStatus.DONE
    for fragment in PROTECTED_FRAGMENTS:
        assert fragment in message.content, f"受保护片段被漂移：{fragment}"
    # 漂移写法不得残留在最终正文。
    assert "299792.459" not in message.content
    assert "$E=mc^3$" not in message.content
    assert "`result = 0`" not in message.content
    assert "https://example.com/b" not in message.content
    assert "[2]" not in message.content


def test_expression_policy_protects_facts_in_both_modes() -> None:
    """策略级：两种模式的表达合同都声明受保护区并让位于用户与任务合同。"""
    for mode in (ChatMode.COMPANION, ChatMode.STUDY):
        snapshot = GlobalWritingPolicyCompiler().compile(
            mode, user_text=USER_QUERY
        )
        assert "引用、数值、代码、公式、链接" in snapshot.system_block
        assert "必须原样保留，保持准确" in snapshot.system_block
        assert "不得改写证据" in snapshot.system_block
        assert "优先级低于用户本轮明确表达的语气与篇幅要求" in snapshot.system_block
