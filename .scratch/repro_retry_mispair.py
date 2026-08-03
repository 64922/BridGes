"""复现：重试旧轮次助手消息后，新尝试错位（渲染分组与模型历史配对错误）。

场景：U1 → A1(失败)；U2 → A2(成功)；随后重试 A1 → A1r。
期望：A1r 归入 U1 轮次（渲染在 U1 之后、U2 之前），模型历史配对为 U1→A1r、U2→A2。
实际（缺陷）：A1r 以 created_at=now 排在最后，渲染分组把 A1r 当作 U2 的回答，
模型历史把 A1r 当作 U2 的回答喂给模型。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path("C:/Users/33755/Desktop/try5").resolve()))
sys.path.insert(0, str(Path("C:/Users/33755/Desktop/try5/src").resolve()))
sys.path.insert(0, str(Path("C:/Users/33755/Desktop/try5/tests").resolve()))
sys.path.insert(0, str(Path("C:/Users/33755/Desktop/try5/tests/chat").resolve()))

import tempfile

from bridges.ai import ModelGateway
from bridges.ai.adapters import RateLimitError
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.ai.streaming import StreamChunk
from bridges.chat.repository import ConversationRepository
from bridges.chat.service import ChatService
from bridges.contracts.chat import ChatMessageRole, ChatMessageStatus
from bridges.storage.database import BridgesDatabase
from test_chat_service import (
    _chat_capability,
    _context,
    _ProgrammableStreamAdapter,
    _start,
    _with_chunks,
)


def main() -> None:
    tmp = tempfile.mkdtemp()
    database = BridgesDatabase(Path(tmp) / "bridges.db")
    database.initialize()
    repository = ConversationRepository(database)
    registry = CapabilityRegistry()
    registry.register(_chat_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", _ProgrammableStreamAdapter())
    service = ChatService(repository=repository, gateway=gateway)

    created = service.create_conversation("alice")

    # 第 1 轮：失败
    service._gateway = _with_chunks(service, connect_error=RateLimitError("slow"))
    _, failed = _start(service, created.conversation_id, "问题一")
    list(service.stream_generation("alice", created.conversation_id, failed.message_id, _context("run-1")))

    # 第 2 轮：成功
    service._gateway = _with_chunks(service, [StreamChunk(kind="delta", delta="回答二")])
    _, done2 = _start(service, created.conversation_id, "问题二")
    list(service.stream_generation("alice", created.conversation_id, done2.message_id, _context("run-2")))

    # 重试第 1 轮的失败消息
    service._gateway = _with_chunks(service, [StreamChunk(kind="delta", delta="回答一重试")])
    owner, retried = service.retry_generation("alice", created.conversation_id, failed.message_id)
    assert retried.attempt_number == 2
    list(service.stream_generation("alice", created.conversation_id, retried.message_id, _context("run-3")))

    projection = service.get_conversation("alice", created.conversation_id)
    assert projection is not None
    print("=== 服务端消息顺序（created_at 序）===")
    for m in projection.messages:
        print(f"  {m.role}  attempt={m.attempt_number}  status={m.status}  content={m.content!r}")

    # 前端 buildThreadMessages 的分组语义：助手消息必须与其所属用户消息相邻
    order = [(m.role, m.attempt_number, m.content) for m in projection.messages]
    expected = [
        ("user", 1, "问题一"),
        ("assistant", 1, ""),          # 失败尝试
        ("assistant", 2, "回答一重试"),  # 重试尝试应归入第 1 轮
        ("user", 1, "问题二"),
        ("assistant", 1, "回答二"),
    ]
    print("\n=== 期望顺序 ===")
    for item in expected:
        print(f"  {item}")
    print("\n=== 实际顺序 ===")
    for item in order:
        print(f"  {item}")
    if order != expected:
        print("\n[FAIL] 顺序错位：重试尝试未归入所属轮次")
        sys.exit(1)

    # _model_history 配对：U2 的回答必须是 A2（回答二），而不是 A1r（回答一重试）
    history = service._model_history("alice", created.conversation_id)
    print("\n=== 模型历史 ===")
    for turn in history:
        print(f"  {turn}")
    pairs = [
        (history[i]["content"], history[i + 1]["content"])
        for i in range(len(history) - 1)
        if history[i]["role"] == "user"
    ]
    if ("问题二", "回答一重试") in pairs:
        print("\n[FAIL] 模型历史配对错误：U2 的回答被喂成了 A1r（回答一重试）")
        sys.exit(1)
    print("\n[PASS] 顺序与配对均正确")


if __name__ == "__main__":
    main()
