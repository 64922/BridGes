"""画像工单 11：聊天管线中的混合召回采用记录与显式降级。

覆盖验收：
- `adopted_profile_slice` 只记录实际发送的条目（预算整条裁剪与最终载荷门
  整体未采用都会同步改写），`adopted_profile_slice_compiled` 保留冻结的
  完整编译快照供重试复用；
- 语义召回降级在用户披露与审计里如实说明，不伪装成「没有相关记忆」；
- 策略失败清空两套记录，安全基线不会从旧快照重编译。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from bridges.ai import ModelGateway
from bridges.ai.adapters import StreamChunk
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.ai.model_quota import QuotaVerificationBasis, RunModelQuota
from bridges.ai.payload_budget import estimate_payload_tokens, estimate_tokens
from bridges.chat.repository import ConversationRepository
from bridges.chat.service import ChatService
from bridges.chat.turn import adopted_profile_context
from bridges.contracts.ai import CapabilityKind, CapabilityRecord
from bridges.contracts.chat import ChatMessageStatus, ContextNoteState
from bridges.contracts.observability import AuditAction
from bridges.contracts.projects import ObjectDomain
from bridges.contracts.workflows import RunContextEnvelope
from bridges.observability.service import ObservabilityService
from bridges.profiles.adapters import InMemoryProfileRepository
from bridges.profiles.atomic import (
    AtomicProfileService,
    InMemoryAtomicProfileRepository,
)
from bridges.profiles.automatic import (
    AutomaticProfileService,
    InMemoryAutomaticProfileRepository,
)
from bridges.profiles.four_dimensions import (
    FourDimensionProfileService,
    InMemoryFourDimensionProfileRepository,
)
from bridges.profiles.purpose import build_purpose
from bridges.profiles.recall import ProfileRecallOutcome
from bridges.profiles.service import ProfileService
from bridges.storage.database import BridgesDatabase

ACCOUNT = "alice"
PREFERENCE = "我习惯先看例子再看公式"
BREVITY = "我喜欢简短回答"

_SLICE_MARKER = "以下是本轮为你参考的已授权信息"


def _context(run_id: str) -> RunContextEnvelope:
    return RunContextEnvelope(
        run_id=run_id,
        account_id=ACCOUNT,
        project_id="conversation-1",
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


class _CapturingStreamAdapter:
    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def stream_call(
        self,
        capability: CapabilityRecord,
        run_context: RunContextEnvelope,
        payload: dict[str, Any],
    ):
        self.payloads.append(payload)
        yield StreamChunk(kind="delta", delta="好的。")
        yield StreamChunk(kind="done")


class _DegradingRecallIndex:
    """模拟生产未配置凭据时的显式降级（不实际调用模型）。"""

    def recall(
        self,
        account_id: str,
        *,
        items: Any,
        query: str | None,
        run_id: str,
    ) -> ProfileRecallOutcome:
        return ProfileRecallOutcome(
            degradation_reason="语义召回暂不可用：未配置全局百炼运行凭据"
        )


class _Env:
    def __init__(self, tmp_path: Path, account: str = ACCOUNT) -> None:
        self.account = account
        database = BridgesDatabase(tmp_path / f"bridges-{account}.db")
        database.initialize()
        self.four_dimensions = FourDimensionProfileService(
            source_repository=InMemoryProfileRepository(),
            repository=InMemoryFourDimensionProfileRepository(),
        )
        self.atomic = AtomicProfileService(
            self.four_dimensions, InMemoryAtomicProfileRepository()
        )
        self.automatic = AutomaticProfileService(
            four_dimension_service=self.four_dimensions,
            repository=InMemoryAutomaticProfileRepository(),
            atomic_profile_service=self.atomic,
        )
        registry = CapabilityRegistry()
        registry.register(_chat_capability())
        gateway = ModelGateway(registry)
        self.adapter = _CapturingStreamAdapter()
        gateway.register_adapter("qwen_text_chat", "1", self.adapter)
        self.observability = ObservabilityService()
        self.chat = ChatService(
            repository=ConversationRepository(database),
            gateway=gateway,
            profile_service=ProfileService(repository=InMemoryProfileRepository()),
            automatic_profile_service=self.automatic,
            four_dimension_profile_service=self.four_dimensions,
            atomic_profile_service=self.atomic,
            observability_service=self.observability,
        )
        self.conversation_id = self.chat.create_conversation(
            self.account
        ).conversation_id
        self.turn = 0

    def remember(self, text: str, source_message_id: str) -> None:
        self.atomic.remember(self.account, text, source_message_id=source_message_id)

    def ask(self, content: str, **kwargs: Any) -> Any:
        user, assistant, _ = self.chat.start_generation(
            self.account, self.conversation_id, content
        )
        self.turn += 1
        list(
            self.chat.stream_generation(
                self.account,
                self.conversation_id,
                assistant.message_id,
                _context(f"run-{self.turn}"),
                **kwargs,
            )
        )
        self.automatic.run_retry_tick()
        final = self.chat.message_projection(self.account, assistant.message_id)
        assert final is not None and final.status == ChatMessageStatus.DONE
        return final

    def retry(self, message_id: str, **kwargs: Any) -> Any:
        _, assistant, _ = self.chat.retry_generation(
            self.account, self.conversation_id, message_id
        )
        list(
            self.chat.stream_generation(
                self.account,
                self.conversation_id,
                assistant.message_id,
                _context("retry-run"),
                **kwargs,
            )
        )
        return self.chat.message_projection(self.account, assistant.message_id)

    def config(self, message_id: str) -> dict[str, Any]:
        run = self.chat._repo.get_run_by_message(  # noqa: SLF001
            self.account, message_id
        )
        return dict(run.config or {})

    def payload(self) -> dict[str, Any]:
        return self.adapter.payloads[-1]

    def payload_text(self) -> str:
        return "\n".join(message["content"] for message in self.payload()["messages"])

    def slice_blocks(self) -> list[str]:
        return [
            message["content"]
            for message in self.payload()["messages"]
            if message["role"] == "system" and _SLICE_MARKER in message["content"]
        ]

    def slice_audits(self) -> list[Any]:
        return self.observability.list_audit_events(
            account_id=self.account, action=AuditAction.PROFILE_SLICE_USED
        )


@pytest.fixture
def env(tmp_path: Path) -> _Env:
    return _Env(tmp_path)


def test_trimmed_adoption_record_matches_sent_block(env: _Env) -> None:
    """预算整条裁剪后：记录与载荷一致，冻结快照保留完整编译结果。"""

    env.remember(PREFERENCE, "m-pref")
    env.remember(BREVITY, "m-brevity")
    snapshot = env.atomic.compile_adopted_slice(
        ACCOUNT,
        run_id="budget-baseline",
        purpose=build_purpose(mode="companion", query="解释贝叶斯定理"),
    )
    assert len(snapshot.adopted_items) == 2
    first_item = snapshot.adopted_items[0]
    cost_one = estimate_tokens(
        adopted_profile_context(snapshot.with_items([first_item]))
    )
    final = env.ask(
        "解释贝叶斯定理",
        context_budget={"input_budget_tokens": cost_one + 5, "input_token_estimate": 0},
    )

    blocks = env.slice_blocks()
    assert len(blocks) == 1
    assert first_item.fact_text in blocks[0]
    other = next(
        item for item in snapshot.adopted_items if item is not first_item
    )
    assert other.fact_text not in env.payload_text()

    config = env.config(final.message_id)
    sent = config["adopted_profile_slice"]
    sent_texts = [item["fact_text"] for item in sent["adopted_items"]]
    assert sent_texts == [first_item.fact_text]
    trimmed = [
        item
        for item in sent["excluded_items"]
        if item["exclusion_reason"] == "超出本轮模型输入预算，整条不采用"
    ]
    assert len(trimmed) == 1
    compiled = config["adopted_profile_slice_compiled"]
    assert len(compiled["adopted_items"]) == 2
    assert env.slice_audits()[-1].details["item_count"] == 1


def test_final_gate_drop_rewrites_adoption_record_only(env: _Env) -> None:
    """最终载荷门整体未采用画像块：发送记录改写为未采用，冻结快照不变。"""

    env.remember(PREFERENCE, "m-pref")
    final = env.ask("解释贝叶斯定理")
    base = dict(env.payload())
    base["messages"] = [
        message for message in base["messages"] if _SLICE_MARKER not in message["content"]
    ]
    quota = RunModelQuota(
        model_id="qwen3.7-plus-2026-05-26",
        context_window=100000,
        max_input_tokens=estimate_payload_tokens(base) + 30,
        verification_basis=QuotaVerificationBasis.SETTINGS_ACTIVATION,
    )
    retried = env.retry(final.message_id, model_quota=quota.model_dump(mode="json"))

    assert env.slice_blocks() == []
    assert PREFERENCE not in env.payload_text()
    assert retried is not None and retried.status == ChatMessageStatus.DONE
    assert retried.context_note is not None
    assert retried.context_note.profile_item_count == 0

    config = env.config(retried.message_id)
    sent = config["adopted_profile_slice"]
    assert sent["adopted_items"] == []
    assert any(
        item["exclusion_reason"] == "最终输入预算不足，本轮未采用"
        for item in sent["excluded_items"]
    )
    compiled = config["adopted_profile_slice_compiled"]
    assert len(compiled["adopted_items"]) == 1
    assert compiled["adopted_items"][0]["fact_text"] == PREFERENCE


def test_recall_degradation_is_disclosed_in_note_and_audit(env: _Env) -> None:
    """语义召回降级时披露如实说明，关键词通道仍采纳条目。"""

    env.remember("我在学线性代数", "m-math")
    env.atomic.set_recall_index(_DegradingRecallIndex())

    final = env.ask("帮我复习线性代数")

    blocks = env.slice_blocks()
    assert len(blocks) == 1
    assert "我在学线性代数" in blocks[0]
    assert final.context_note is not None
    assert final.context_note.profile_item_count == 1
    assert "语义召回暂不可用" in final.context_note.note
    details = env.slice_audits()[-1].details
    assert details["recall_degraded"] is True
    assert details["item_count"] == 1


def test_recall_degradation_never_claims_no_relevant_memory(env: _Env) -> None:
    """降级且没有命中时，披露不得说成「没有与你当前任务匹配」。"""

    env.remember("我平时喜欢跑步", "m-hobby")
    env.atomic.set_recall_index(_DegradingRecallIndex())

    final = env.ask("解释贝叶斯定理")

    assert env.slice_blocks() == []
    assert final.context_note is not None
    assert final.context_note.state == ContextNoteState.EMPTY
    assert "语义召回暂不可用" in final.context_note.note
    assert "没有与你当前任务匹配" not in final.context_note.note
    details = env.slice_audits()[-1].details
    assert details["recall_degraded"] is True
    assert details["item_count"] == 0
