"""Issue 08：对话式教学状态机的五轮契约测试与来源隔离验收。

反馈环要求的固定对话：建立 Transformer 目标 → 初学者开始 → 部分正确
→ 追问 → 跳过。当前实现（无状态教学）会在第一轮被证据门截断；修复后
断言：状态转移（mission_setup → micro_lesson → adaptation）、每轮最多
一道理解检查题、追问不形成答题证据、跳过不判错、证据绑定真实来源。

确定性环境：无本地材料 + 可编程公网搜索客户端。搜索客户端按查询内容
区分行为——含整句意图（"我想学习"）的查询失败（锁定旧实现的整句检索
回归），含规范概念（"Transformer"）的查询成功（验证 micro_lesson 用
规范主题构造查询，不拿整句意图搜索）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bridges.ai import ModelGateway
from bridges.ai.adapters import StreamChunk
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.chat.repository import ConversationRepository
from bridges.chat.service import ChatService
from bridges.contracts.ai import CapabilityKind, CapabilityRecord
from bridges.contracts.chat import ChatMessageStatus, ChatMode
from bridges.contracts.projects import ObjectDomain
from bridges.contracts.teaching import (
    TeachingAnswerEvidence,
    TeachingStage,
    TeachingTurnProjection,
)
from bridges.contracts.workflows import RunContextEnvelope
from bridges.storage.database import BridgesDatabase
from bridges.web_search.contracts import WebSearchResult
from bridges.web_search.service import WebSearchService


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
    """捕获模型载荷的流式适配器（验证何时真正调用教学模型）。"""

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def stream_call(
        self,
        capability: CapabilityRecord,
        run_context: Any,
        payload: dict[str, Any],
    ):
        self.payloads.append(payload)
        yield StreamChunk(kind="delta", delta="基于合格来源讲解 [web-1]。")
        yield StreamChunk(kind="done")


class _IntentAwareSearchClient:
    """按查询内容分派行为的搜索客户端。

    - 含整句意图（"我想学习"）的查询 → 失败（旧实现整句检索的锁定回归）；
    - 含规范概念（"Transformer"）的查询 → 成功返回公开结果。
    """

    def __init__(self) -> None:
        self.queries: list[str] = []
        self.fail_queries: list[str] = []

    def search(self, query: str) -> list[WebSearchResult]:
        self.queries.append(query)
        if "我想学习" in query:
            self.fail_queries.append(query)
            raise RuntimeError("整句意图查询被拒绝。")
        if "Transformer" in query:
            return [
                WebSearchResult(
                    result_id="web-transformer",
                    title="Transformer 架构公开讲义",
                    site="example.com",
                    url="https://example.com/transformer",
                    snippet="Transformer 使用自注意力机制并行处理序列。",
                    accessed_at=datetime.now(UTC),
                )
            ]
        return []


def _context(run_id: str = "run-issue08") -> RunContextEnvelope:
    return RunContextEnvelope(
        run_id=run_id,
        account_id="alice",
        project_id="conversation-issue08",
        workflow_name="chat",
        workflow_version="1",
        object_domain=ObjectDomain.PERSONAL_VAULT,
        submitted_at=datetime.now(UTC),
    )


def _make_service(
    tmp_path: Path,
    client: _IntentAwareSearchClient,
    adapter: _CapturingStreamAdapter,
) -> tuple[ChatService, ConversationRepository]:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    repository = ConversationRepository(database)
    registry = CapabilityRegistry()
    registry.register(_chat_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", adapter)
    return (
        ChatService(
            repository=repository,
            gateway=gateway,
            web_search_service=WebSearchService(client=client),
        ),
        repository,
    )


def _send(
    service: ChatService,
    conversation_id: str,
    content: str,
    *,
    account_id: str = "alice",
    run_id: str = "run-issue08",
) -> tuple[Any, Any, list[Any]]:
    """发送一条消息并消费全部流事件，返回 (用户消息, 最终助手投影, 事件)。"""
    user, assistant = service.start_generation(
        account_id, conversation_id, content
    )
    events = list(
        service.stream_generation(
            account_id,
            conversation_id,
            assistant.message_id,
            _context(run_id),
            until_user_message_id=user.message_id,
        )
    )
    final = service.message_projection(account_id, assistant.message_id)
    assert final is not None
    return user, final, events


def _teaching(final: Any) -> TeachingTurnProjection:
    assert final.teaching is not None, "study 模式每轮必须有教学投影。"
    return TeachingTurnProjection.model_validate(final.teaching)


def _last_answer(teaching: TeachingTurnProjection) -> TeachingAnswerEvidence:
    assert teaching.evidence, "本轮应有作答证据。"
    return teaching.evidence[-1]


def test_five_round_contract_mission_confirm_teach_adapt_follow_up_skip(
    tmp_path: Path,
) -> None:
    """五轮契约：目标确认 → 初学者开始 → 部分正确 → 追问 → 跳过。"""
    client = _IntentAwareSearchClient()
    adapter = _CapturingStreamAdapter()
    service, _ = _make_service(tmp_path, client, adapter)
    conversation = service.create_conversation("alice", mode=ChatMode.STUDY)

    # ------------------------------------------------------------
    # 第 1 轮：建立目标。不检索、不过证据门、不调模型。
    # ------------------------------------------------------------
    _, final_r1, events_r1 = _send(service, conversation.conversation_id, "我想学习 Transformer")
    assert final_r1.status == ChatMessageStatus.DONE
    t1 = _teaching(final_r1)
    assert t1.mission is not None
    assert t1.mission.stage == TeachingStage.MISSION_SETUP
    # 第一轮不被证据门截断：没有 gap 缺口文案。
    assert t1.evidence_gate.gap is None
    assert t1.gap_response is None
    assert "Transformer" in t1.mission.goal
    # 只问一个关键问题（水平确认），且允许按初学者开始。
    assert "初学者" in t1.mission.next_action or "初学者" in t1.goal
    assert [e.kind for e in events_r1] == ["delta", "done"]
    # 尚未调用教学模型（mission 未确认不生成正式教学回答）。
    assert adapter.payloads == []
    # mission_setup 阶段没有发起整句意图搜索。
    assert client.queries == []

    # ------------------------------------------------------------
    # 第 2 轮：按初学者开始 → mission 确认 → 首个微课。
    # ------------------------------------------------------------
    _, final_r2, _ = _send(service, conversation.conversation_id, "按初学者开始")
    assert final_r2.status == ChatMessageStatus.DONE
    t2 = _teaching(final_r2)
    assert t2.mission is not None
    # 微课出题后进入理解检查阶段（等待作答）。
    assert t2.mission.stage == TeachingStage.UNDERSTANDING_CHECK
    assert t2.mission.current_concept is not None
    assert "Transformer" in t2.mission.current_concept
    # 微课检索用规范概念，不拿整句意图搜索。
    assert client.queries, "micro_lesson 必须发起公开搜索（本地无材料）。"
    assert all("我想学习" not in q for q in client.queries)
    assert all("Transformer" in q for q in client.queries)
    # 每轮最多一道理解检查题，且证据绑定真实来源。
    assert t2.quiz is not None
    assert t2.quiz.evidence_refs, "理解检查题必须绑定本轮合格来源。"
    # 教学正文由模型基于合格来源生成（证据门通过）。
    assert adapter.payloads
    assert "web-transformer" in str(adapter.payloads[-1])

    # ------------------------------------------------------------
    # 第 3 轮：部分正确回答 → adaptation（补讲），仍最多一题。
    # ------------------------------------------------------------
    _, final_r3, _ = _send(
        service, conversation.conversation_id, "Transformer 用注意力机制处理序列，其他细节我还不清楚"
    )
    assert final_r3.status == ChatMessageStatus.DONE
    t3 = _teaching(final_r3)
    evidence3 = _last_answer(t3)
    assert evidence3.evaluated_state.value == "partial"
    assert evidence3.knowledge_state.value == "emerging_candidate"
    # 部分正确只形成候选理解状态，不宣称掌握。
    assert evidence3.mastery_claim_allowed is False
    assert t3.mission is not None
    assert t3.mission.stage == TeachingStage.ADAPTATION
    # 补讲路径：下一问（若出题）仍聚焦当前概念且绑定来源，最多一道。
    if t3.quiz is not None:
        assert t3.quiz.concept == t3.mission.current_concept
        assert t3.quiz.evidence_refs

    # ------------------------------------------------------------
    # 第 4 轮：追问 → 优先回答追问并保持当前概念，不判为答题证据。
    # ------------------------------------------------------------
    _, final_r4, _ = _send(
        service, conversation.conversation_id, "为什么需要注意力机制？"
    )
    assert final_r4.status == ChatMessageStatus.DONE
    t4 = _teaching(final_r4)
    # 追问不形成新的答题证据：evidence 数量不增长。
    assert len(t4.evidence) == len(t3.evidence)
    # 当前概念保持不变。
    assert t4.mission is not None
    assert t4.mission.current_concept == t3.mission.current_concept
    # 追问仍然获得教学回答（模型被调用）。
    assert len(adapter.payloads) > 1

    # ------------------------------------------------------------
    # 第 5 轮：跳过（用前端按钮的实际文案）→ 不判错，且不终止 mission。
    # ------------------------------------------------------------
    _, final_r5, _ = _send(
        service, conversation.conversation_id, "跳过这道理解检查，我想继续学习。"
    )
    assert final_r5.status == ChatMessageStatus.DONE
    t5 = _teaching(final_r5)
    assert t5.mission is not None
    # 跳过被记录为 needs_review，而不是判错（确定产生证据）。
    skip_evidence = _last_answer(t5)
    assert skip_evidence.evaluated_state.value == "needs_review"
    assert skip_evidence.source_message_id != _last_answer(t4).source_message_id
    # 跳过不终止 mission：进入调整阶段，有下一步动作。
    assert t5.mission.stage == TeachingStage.ADAPTATION
    assert t5.next_prompt
    assert [e.kind for e in events_r1] == ["delta", "done"]


def test_mission_persists_across_refresh_and_new_turn(
    tmp_path: Path,
) -> None:
    """mission、当前概念与理解证据跨轮持久化（刷新后恢复同一进度）。"""
    client = _IntentAwareSearchClient()
    adapter = _CapturingStreamAdapter()
    service, repository = _make_service(tmp_path, client, adapter)
    conversation = service.create_conversation("alice", mode=ChatMode.STUDY)

    _, final_r1, _ = _send(service, conversation.conversation_id, "我想学习 Transformer")
    mission_r1 = _teaching(final_r1).mission
    assert mission_r1 is not None

    _, final_r2, _ = _send(service, conversation.conversation_id, "按初学者开始")
    t2 = _teaching(final_r2)
    assert t2.mission is not None
    assert t2.mission.stage == TeachingStage.UNDERSTANDING_CHECK

    # 模拟刷新：重新读取同一对话的消息历史，最新教学投影恢复同一 mission。
    messages = repository.list_messages("alice", conversation.conversation_id)
    assert messages
    latest_teaching = None
    for message in messages:
        if message.teaching is not None:
            latest_teaching = TeachingTurnProjection.model_validate(message.teaching)
    assert latest_teaching is not None
    assert latest_teaching.mission is not None
    assert latest_teaching.mission.mission_id == t2.mission.mission_id
    assert latest_teaching.mission.stage == t2.mission.stage
    assert latest_teaching.mission.current_concept == t2.mission.current_concept

    # 新轮次（作答）延续同一 mission。
    _, final_r3, _ = _send(
        service, conversation.conversation_id, "Transformer 用注意力机制处理序列，其他细节我还不清楚"
    )
    t3 = _teaching(final_r3)
    assert t3.mission is not None
    assert t3.mission.mission_id == t2.mission.mission_id
    assert t3.evidence and t3.evidence[-1].evaluated_state.value == "partial"


def test_blocked_mission_recovers_when_sources_become_available(
    tmp_path: Path,
) -> None:
    """受阻后来源恢复：重试成功轮恢复教学阶段，不永久卡在 blocked。"""
    class _ToggleClient:
        def __init__(self) -> None:
            self.queries: list[str] = []
            self.failing = True

        def search(self, query: str) -> list[WebSearchResult]:
            self.queries.append(query)
            if self.failing or "Transformer" not in query:
                return []
            return [
                WebSearchResult(
                    result_id="web-recovered",
                    title="Transformer 恢复后的公开讲义",
                    site="example.com",
                    url="https://example.com/recovered",
                    snippet="自注意力机制。",
                    accessed_at=datetime.now(UTC),
                )
            ]

    client = _ToggleClient()
    adapter = _CapturingStreamAdapter()
    service, _ = _make_service(tmp_path, client, adapter)
    conversation = service.create_conversation("alice", mode=ChatMode.STUDY)

    _, final_r1, _ = _send(service, conversation.conversation_id, "我想学习 Transformer")
    assert _teaching(final_r1).mission is not None
    _, final_r2, _ = _send(service, conversation.conversation_id, "按初学者开始")
    t2 = _teaching(final_r2)
    assert t2.mission is not None
    assert t2.mission.stage == TeachingStage.BLOCKED
    assert t2.mission.blocked_reason

    # 来源恢复后重试：同一轮新尝试从 blocked 回到教学阶段。
    client.failing = False
    _, final_r3, _ = _send(
        service, conversation.conversation_id, "重试"
    )
    assert final_r3.status == ChatMessageStatus.DONE
    t3 = _teaching(final_r3)
    assert t3.mission is not None
    assert t3.mission.stage != TeachingStage.BLOCKED
    assert t3.mission.blocked_reason is None
    assert t3.mission.stage in {
        TeachingStage.MICRO_LESSON,
        TeachingStage.UNDERSTANDING_CHECK,
    }
    assert t3.mission.mission_id == t2.mission.mission_id
    if t3.quiz is not None:
        assert t3.quiz.evidence_refs


def test_modify_mission_restarts_goal_confirmation(
    tmp_path: Path,
) -> None:
    """确认阶段换主题：重新确认目标，不被当作原主题的确认输入。"""
    client = _IntentAwareSearchClient()
    adapter = _CapturingStreamAdapter()
    service, _ = _make_service(tmp_path, client, adapter)
    conversation = service.create_conversation("alice", mode=ChatMode.STUDY)

    _, final_r1, _ = _send(service, conversation.conversation_id, "我想学习 Transformer")
    mission_r1 = _teaching(final_r1).mission
    assert mission_r1 is not None

    # 确认阶段换主题 → 重新进入目标确认（不改原主题首概念）。
    _, final_r2, _ = _send(service, conversation.conversation_id, "换个主题学 Python")
    assert final_r2.status == ChatMessageStatus.DONE
    t2 = _teaching(final_r2)
    assert t2.mission is not None
    assert t2.mission.stage == TeachingStage.MISSION_SETUP
    assert "Python" in t2.mission.goal
    assert "换个主题" not in t2.mission.goal
    assert t2.mission.mission_id == mission_r1.mission_id  # 同一任务延续
    assert adapter.payloads == []  # 换主题不触发教学生成


def test_public_source_failure_keeps_mission_and_offers_recovery(
    tmp_path: Path,
) -> None:
    """本地无材料且公开来源失败：保留 mission，明确受阻，不回退无关回答。"""
    class _AlwaysFailingClient:
        def __init__(self) -> None:
            self.queries: list[str] = []

        def search(self, query: str) -> list[WebSearchResult]:
            self.queries.append(query)
            return []

    client = _AlwaysFailingClient()
    adapter = _CapturingStreamAdapter()
    service, _ = _make_service(tmp_path, client, adapter)
    conversation = service.create_conversation("alice", mode=ChatMode.STUDY)

    _, final_r1, _ = _send(service, conversation.conversation_id, "我想学习 Transformer")
    assert _teaching(final_r1).mission is not None

    _, final_r2, _ = _send(service, conversation.conversation_id, "按初学者开始")
    assert final_r2.status == ChatMessageStatus.DONE
    t2 = _teaching(final_r2)
    assert t2.mission is not None
    # 来源全部不可用：保留 mission 并明确当前阶段受阻。
    assert t2.mission.stage == TeachingStage.BLOCKED
    assert t2.mission.blocked_reason
    assert t2.mission.recovery_steps
    # 教学正文说明受阻与恢复动作，而不是输出与学习无关的内容。
    assert "重试" in final_r2.content or "上传" in final_r2.content
    assert not adapter.payloads  # 来源不可用时不得调用模型冒充来源。


def test_irrelevant_image_never_becomes_teaching_source(
    tmp_path: Path,
) -> None:
    """知识库只有无关图片（无 OCR/文本证据）时，不进入教学引用与依据。"""
    from tests.retrieval.conftest import (
        add_material,
        make_retrieval_env,
        make_storage,
    )

    env = make_retrieval_env(make_storage(tmp_path))
    database: BridgesDatabase = env["database"]
    retrieval = env["retrieval"]

    client = _IntentAwareSearchClient()
    adapter = _CapturingStreamAdapter()
    registry = CapabilityRegistry()
    registry.register(_chat_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", adapter)
    service = ChatService(
        repository=ConversationRepository(database),
        gateway=gateway,
        retrieval_service=retrieval,
        web_search_service=WebSearchService(client=client),
    )

    account = env["account_a"]
    # 知识库只有一张与主题无关的图片：无 OCR/文本证据，摄取不入队，
    # 因此该层没有任何就绪文档，图片绝不能成为教学引用。
    stored = env["repository"].create_object(
        account, "巴巴博一.jpg", b"\xff\xd8\xff\xe0", media_type="image/jpeg"
    )
    assert env["ingestion"] is not None
    env["ingestion"].enqueue(account, stored.object_id)
    env["ingestion"].process_pending()

    conversation = service.create_conversation(account, mode=ChatMode.STUDY)
    _, final_r1, _ = _send(
        service, conversation.conversation_id, "我想学习 Transformer",
        account_id=account, run_id="run-img",
    )
    assert _teaching(final_r1).mission is not None
    _, final_r2, _ = _send(
        service, conversation.conversation_id, "按初学者开始",
        account_id=account, run_id="run-img",
    )
    t2 = _teaching(final_r2)
    assert t2.mission is not None
    # 图片来源不能出现在引用、原文或教学依据中。
    gate = t2.evidence_gate
    all_titles = [
        *(s.title for s in gate.local_sources),
        *(s.title for s in gate.external_sources),
    ]
    assert all("巴巴博一" not in title for title in all_titles)
    if t2.quiz is not None:
        assert t2.quiz.evidence_refs, "微课来源仍可追溯（公开来源）。"
    assert t2.mission.stage == TeachingStage.UNDERSTANDING_CHECK
