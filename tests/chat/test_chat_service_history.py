"""聊天服务测试：持久化状态机、停止/重试、断流收敛与账户隔离（Issue 11）。

服务层直接使用临时 sqlite 数据库与可编程流式适配器，不经过网络；
生成器通过 ``list(...)`` 消费以模拟流式推进。
"""

from __future__ import annotations

import pytest

from bridges.ai.adapters import RateLimitError, StreamChunk
from bridges.chat.service import (
    ChatDomainError,
    ChatService,
)
from bridges.contracts.chat import ChatMessageRole, ChatMessageStatus, ChatMode
from tests.chat.chat_service_support import (
    _context,
    _start,
    _with_chunks,
)


def test_model_history_excludes_failed_and_stopped_attempts(service: ChatService) -> None:
    """失败/停止的尝试不进模型上下文，只带最新已完成回答。"""
    service._gateway = _with_chunks(
        service, connect_error=RateLimitError("slow")
    )
    created = service.create_conversation("alice")
    _, failed = _start(service, created.conversation_id, "第一个问题")
    list(
        service.stream_generation(
            "alice", created.conversation_id, failed.message_id, _context()
        )
    )
    service._gateway = _with_chunks(service, [StreamChunk(kind="delta", delta="正确答案")])
    _, retried, _ = service.retry_generation(
        "alice", created.conversation_id, failed.message_id
    )
    list(
        service.stream_generation(
            "alice", created.conversation_id, retried.message_id, _context("run-2")
        )
    )

    history = service._model_history("alice", created.conversation_id)
    # 首条为当前对话模式的系统角色合同（Issue 14），失败/停止尝试不进上下文
    assert history[0]["role"] == "system"
    assert "日常陪伴" in history[0]["content"]
    assert history[1:] == [
        {"role": "user", "content": "第一个问题"},
        {"role": "assistant", "content": "正确答案"},
    ]


def test_retry_of_older_turn_keeps_turn_position_and_truncates_context(
    service: ChatService,
) -> None:
    """重试旧轮次失败消息：新尝试归入所属轮次，模型上下文截断到该轮。

    回归：此前新尝试以 created_at=now 排在对话末尾，渲染分组把新尝试
    错配给后续轮次，模型历史也把后续轮次内容喂给重试生成。
    """
    created = service.create_conversation("alice")
    # 第 1 轮：失败
    service._gateway = _with_chunks(service, connect_error=RateLimitError("slow"))
    _, failed = _start(service, created.conversation_id, "问题一")
    list(
        service.stream_generation(
            "alice", created.conversation_id, failed.message_id, _context("run-1")
        )
    )
    # 第 2 轮：成功
    service._gateway = _with_chunks(
        service, [StreamChunk(kind="delta", delta="回答二")]
    )
    _, done2 = _start(service, created.conversation_id, "问题二")
    list(
        service.stream_generation(
            "alice", created.conversation_id, done2.message_id, _context("run-2")
        )
    )
    # 重试第 1 轮的失败消息
    service._gateway = _with_chunks(
        service, [StreamChunk(kind="delta", delta="回答一重试")]
    )
    owner, retried, _ = service.retry_generation(
        "alice", created.conversation_id, failed.message_id
    )
    assert retried.attempt_number == 2
    list(
        service.stream_generation(
            "alice",
            created.conversation_id,
            retried.message_id,
            _context("run-3"),
            until_user_message_id=owner.message_id,
        )
    )

    projection = service.get_conversation("alice", created.conversation_id)
    assert projection is not None
    order = [
        (m.role, m.attempt_number, m.content, m.status)
        for m in projection.messages
    ]
    # 重试尝试必须归入第 1 轮（渲染分组依赖相邻性），而不是排在对话末尾
    assert order == [
        (ChatMessageRole.USER, 1, "问题一", ChatMessageStatus.DONE),
        (ChatMessageRole.ASSISTANT, 1, "", ChatMessageStatus.ERROR),
        (ChatMessageRole.ASSISTANT, 2, "回答一重试", ChatMessageStatus.DONE),
        (ChatMessageRole.USER, 1, "问题二", ChatMessageStatus.DONE),
        (ChatMessageRole.ASSISTANT, 1, "回答二", ChatMessageStatus.DONE),
    ]

    # 重试生成的上下文截断到第 1 轮用户消息：不含第 2 轮的问题与回答，
    # 也不含该轮自身任何回答（旧尝试失败、新尝试尚在流式生成）。
    history = service._model_history(
        "alice", created.conversation_id, until_user_message_id=owner.message_id
    )
    assert history[1:] == [{"role": "user", "content": "问题一"}]

    # 后续轮次（发送第 2 轮消息）的完整上下文：重试尝试作为该轮最新
    # 已完成回答进入历史，顺序正确。
    full = service._model_history("alice", created.conversation_id)
    assert full[1:] == [
        {"role": "user", "content": "问题一"},
        {"role": "assistant", "content": "回答一重试"},
        {"role": "user", "content": "问题二"},
        {"role": "assistant", "content": "回答二"},
    ]


# ---------------------------------------------------------------------------
# 重启恢复（同一数据库文件重新打开）
# ---------------------------------------------------------------------------




def test_create_conversation_defaults_to_companion_mode(service: ChatService) -> None:
    created = service.create_conversation("alice", title="日常对话")
    assert created.mode == ChatMode.COMPANION
    assert created.mode_events == []
    assert service.list_conversations("alice").conversations[0].mode == ChatMode.COMPANION


def test_create_conversation_supports_study_mode(service: ChatService) -> None:
    created = service.create_conversation("alice", title="学习对话", mode=ChatMode.STUDY)
    assert created.mode == ChatMode.STUDY
    listing = service.list_conversations("alice").conversations
    assert listing[0].mode == ChatMode.STUDY


def test_switch_mode_is_rejected_after_contract_change(service: ChatService) -> None:
    created = service.create_conversation("alice")
    with pytest.raises(ChatDomainError) as exc_info:
        service.set_conversation_mode("alice", created.conversation_id, ChatMode.STUDY)
    assert exc_info.value.code == "conversation_mode_switch_retired"
    assert exc_info.value.status_code == 410
    projection = service.get_conversation("alice", created.conversation_id)
    assert projection is not None
    assert projection.mode == ChatMode.COMPANION
    assert projection.mode_events == []


def test_mode_switch_does_not_change_persisted_mode(service: ChatService) -> None:
    created = service.create_conversation("alice")
    with pytest.raises(ChatDomainError):
        service.set_conversation_mode("alice", created.conversation_id, ChatMode.STUDY)
    projection = service.get_conversation("alice", created.conversation_id)
    assert projection is not None and projection.mode == ChatMode.COMPANION


def test_switch_mode_same_mode_is_also_retired(service: ChatService) -> None:
    created = service.create_conversation("alice")
    with pytest.raises(ChatDomainError) as exc_info:
        service.set_conversation_mode("alice", created.conversation_id, ChatMode.COMPANION)
    assert exc_info.value.status_code == 410


def test_switch_mode_unknown_conversation_is_still_retired(service: ChatService) -> None:
    with pytest.raises(ChatDomainError) as exc_info:
        service.set_conversation_mode("alice", "missing", ChatMode.STUDY)
    assert exc_info.value.status_code == 410


def test_done_message_carries_public_thinking_summary(service: ChatService) -> None:
    """思考摘要由结构化进度事件构造：初始步骤 + 生成步骤 + 质量结论。"""
    created = service.create_conversation("alice")
    _, assistant = _start(service, created.conversation_id, "帮我理解量子纠错")
    service._gateway = _with_chunks(service, [StreamChunk(kind="delta", delta="纠错码")])
    list(
        service.stream_generation(
            "alice", created.conversation_id, assistant.message_id, _context()
        )
    )

    final = service.message_projection("alice", assistant.message_id)
    assert final is not None and final.thinking is not None
    assert final.thinking.steps == [
        "理解你的问题与当前语境",
        "组织并生成回答",
    ]
    assert final.thinking.evidence == []
    assert final.thinking.tools == []
    assert final.thinking.quality == ["回答已完整生成并保存"]
    assert final.duration_ms is not None and final.duration_ms >= 1  # 真实生命周期耗时


def test_failed_generation_keeps_steps_with_chinese_quality(service: ChatService) -> None:
    """失败/断流时保留已完成摘要并显示中文状态。"""
    created = service.create_conversation("alice")
    _, assistant = _start(service, created.conversation_id, "触发失败")
    service._gateway = _with_chunks(
        service, connect_error=RateLimitError("slow down")
    )
    list(
        service.stream_generation(
            "alice", created.conversation_id, assistant.message_id, _context()
        )
    )

    final = service.message_projection("alice", assistant.message_id)
    assert final is not None and final.thinking is not None
    assert final.thinking.steps == [
        "理解你的问题与当前语境",
        "组织并生成回答",
    ]
    assert final.thinking.quality and "限流" in final.thinking.quality[0]
    assert final.duration_ms is not None and final.duration_ms >= 1


def test_stopped_generation_keeps_steps_with_chinese_quality(service: ChatService) -> None:
    created = service.create_conversation("alice")
    _, assistant = _start(service, created.conversation_id, "触发停止")
    service._gateway = _with_chunks(
        service, [StreamChunk(kind="delta", delta="部分内容")], slow=True
    )
    generator = service.stream_generation(
        "alice", created.conversation_id, assistant.message_id, _context()
    )
    next(generator)
    service.stop_generation("alice", created.conversation_id, assistant.message_id)
    list(generator)

    final = service.message_projection("alice", assistant.message_id)
    assert final is not None and final.thinking is not None
    assert final.thinking.steps == [
        "理解你的问题与当前语境",
        "组织并生成回答",
    ]
    assert final.thinking.quality == ["已停止生成，保留已生成内容。"]


# ---------------------------------------------------------------------------
