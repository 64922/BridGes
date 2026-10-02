"""改进工单 19：聊天管线中的用途明确画像切片。

验证采用快照在真实回合里的验收行为：跨主题默认表达偏好无词面交集也适用；
背景/目标/约束按任务召回，无关爱好不注入；本轮明确要求覆盖默认偏好且不改
长期值；整条采用或整条排除，预算不足不截断正文；关闭使用不读取长期正文；
采用条数与审计/模型输入一致。
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
from bridges.chat.turn import adopted_profile_block_within_budget, adopted_profile_context
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
from bridges.profiles.service import ProfileService
from bridges.storage.database import BridgesDatabase

ACCOUNT = "alice"
PREFERENCE = "我习惯先看例子再看公式"
BACKGROUND = "我是金融专业大学生，数学基础比较薄弱"
HOBBY = "我平时喜欢跑步"
CONSTRAINT = "我每天只有30分钟学习时间"
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


class _Env:
    """被测组合：聊天 + 四维记录 + 自动抽取 + 原子列表（生产接线同形）。"""

    def __init__(self, tmp_path: Path, account: str = ACCOUNT) -> None:
        self.account = account
        database = BridgesDatabase(tmp_path / f"bridges-{account}.db")
        database.initialize()
        self.four_dimensions = FourDimensionProfileService(
            source_repository=InMemoryProfileRepository(),
            repository=InMemoryFourDimensionProfileRepository(),
        )
        self.atomic = AtomicProfileService(self.four_dimensions, InMemoryAtomicProfileRepository())
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
        self.conversation_id = self.chat.create_conversation(self.account).conversation_id
        self.turn = 0

    def remember(self, text: str, source_message_id: str) -> None:
        self.atomic.remember(self.account, text, source_message_id=source_message_id)

    def ask(self, content: str, **kwargs: Any) -> Any:
        user, assistant, _ = self.chat.start_generation(self.account, self.conversation_id, content)
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

    def items(self) -> list[str]:
        return [item.text for item in self.atomic.projections(self.account)]

    def slice_audits(self) -> list[Any]:
        return self.observability.list_audit_events(
            account_id=self.account, action=AuditAction.PROFILE_SLICE_USED
        )


@pytest.fixture
def env(tmp_path: Path) -> _Env:
    return _Env(tmp_path)


def test_cross_topic_preference_applies_without_word_overlap(env: _Env) -> None:
    """默认表达偏好跨主题适用：与「贝叶斯」没有词面交集也进入本轮。"""

    env.remember(PREFERENCE, "m-pref")
    final = env.ask("贝叶斯定理怎么理解")

    blocks = env.slice_blocks()
    assert len(blocks) == 1
    assert PREFERENCE in blocks[0]
    assert "跨主题适用的默认表达偏好" in blocks[0]
    assert "本轮应用：" in blocks[0]
    assert "（类别：" not in blocks[0]
    # 采用清单与模型输入、审计一致：条目行数等于审计 item_count。
    assert blocks[0].count("用途：") == 1
    assert env.slice_audits()[-1].details["item_count"] == 1
    assert final.context_note is not None
    assert final.context_note.state == ContextNoteState.READY


def test_background_by_purpose_and_hobby_excluded(env: _Env) -> None:
    """背景按任务召回、爱好默认不注入：同一条切片的两种处理。"""

    env.remember(BACKGROUND, "m-bg")
    env.remember(HOBBY, "m-hobby")
    final = env.ask("帮我安排概率考试的复习计划")

    blocks = env.slice_blocks()
    assert len(blocks) == 1
    assert "数学基础比较薄弱" in blocks[0]
    assert "与当前任务相关的用户背景（自述）" in blocks[0]
    assert "按用户自述的基础选择解释起点" in blocks[0]
    assert HOBBY not in blocks[0]
    assert env.slice_audits()[-1].details["item_count"] == 1
    assert env.slice_audits()[-1].details["excluded_count"] == 1
    assert final.context_note is not None
    assert final.context_note.profile_item_count == 1


def test_constraint_shapes_plan_and_explicit_request_overrides_default(
    env: _Env,
) -> None:
    """「每天 30 分钟」进入计划用途；本轮「详细展开」覆盖「简短回答」默认。"""

    long_term = (CONSTRAINT, BREVITY)
    for index, text in enumerate(long_term):
        env.remember(text, f"m-{index}")
    env.ask("帮我安排雅思复习计划，请详细展开")

    block = env.slice_blocks()[0]
    assert CONSTRAINT in block
    assert "按已声明的时间与资源约束安排可执行步骤" in block
    # 尾部适用条件保留：无期限的现实约束提示先确认。
    assert "先确认仍有效" in block
    # 本轮明确要求优先：简短默认本轮不采用。
    assert BREVITY not in block
    assert "本轮明确要求优先，长期偏好只是默认" in block
    assert env.slice_audits()[-1].details["excluded_count"] == 1
    # 覆盖只影响本轮，长期值原样保留。
    assert CONSTRAINT in env.items()
    assert BREVITY in env.items()


def test_long_item_is_excluded_whole_when_budget_is_tight(tmp_path: Path) -> None:
    """预算放不下时整条排除：正文不截断、不进入模型载荷。"""

    env = _Env(tmp_path)
    filler = "需要完整保留的补充说明" * 12
    env.remember(f"我习惯先看例子再看公式，{filler}", "m-long")
    env.ask(
        "贝叶斯定理怎么理解",
        context_budget={"input_budget_tokens": 100, "input_token_estimate": 100},
    )

    assert env.slice_blocks() == []
    assert "需要完整保留的补充说明" not in env.payload_text()
    audit = env.slice_audits()[-1].details
    assert audit["item_count"] == 0
    assert audit["excluded_count"] == 1


def test_disabled_usage_does_not_read_long_term_content(env: _Env) -> None:
    """关闭长期画像使用时：不读取正文、不注入，回答照常。"""

    env.remember(BACKGROUND, "m-bg")
    env.automatic.set_account_controls(ACCOUNT, usage_enabled=False)
    final = env.ask("帮我安排概率考试的复习计划")

    assert env.slice_blocks() == []
    assert "数学基础比较薄弱" not in env.payload_text()
    assert final.context_note is not None
    assert final.context_note.state == ContextNoteState.OFF
    assert final.context_note.profile_item_count == 0


def test_budget_counts_rendered_purpose_conditions_and_summary(env: _Env) -> None:
    env.remember(CONSTRAINT, "m-time")
    snapshot = env.atomic.compile_adopted_slice(
        ACCOUNT, run_id="budget", purpose=build_purpose(mode="companion", query="制定学习计划")
    )
    full_cost = estimate_tokens(adopted_profile_context(snapshot))
    trimmed, block, items = adopted_profile_block_within_budget(
        snapshot, remaining_tokens=full_cost - 1
    )
    assert block is None
    assert items == []
    assert trimmed.adopted_items == ()
    assert trimmed.excluded_items[-1].fact_text == CONSTRAINT
    assert "模型输入预算" in trimmed.excluded_items[-1].exclusion_reason
    exact, block, items = adopted_profile_block_within_budget(snapshot, remaining_tokens=full_cost)
    assert exact.adopted_items == snapshot.adopted_items
    assert block is not None and estimate_tokens(block) == full_cost
    assert len(items) == 1


def test_budget_skips_large_item_and_keeps_later_complete_item(env: _Env) -> None:
    env.remember(BREVITY, "m-short")
    large_text = "我喜欢详细回答，" + "必须完整保留的条件" * 40
    env.remember(large_text, "m-large")
    snapshot = env.atomic.compile_adopted_slice(
        ACCOUNT, run_id="budget", purpose=build_purpose(mode="companion", query="解释贝叶斯")
    )
    short = snapshot.with_items(
        [item for item in snapshot.adopted_items if item.fact_text == BREVITY]
    )
    cost = estimate_tokens(adopted_profile_context(short))
    trimmed, block, items = adopted_profile_block_within_budget(snapshot, remaining_tokens=cost)
    assert [item.fact_text for item in trimmed.adopted_items] == [BREVITY]
    assert block is not None and large_text not in block
    assert len(items) == 1
    assert [item.fact_text for item in trimmed.excluded_items] == [large_text]


def _retry(env: _Env, message_id: str, **kwargs: Any) -> Any:
    _, assistant, _ = env.chat.retry_generation(ACCOUNT, env.conversation_id, message_id)
    list(
        env.chat.stream_generation(
            ACCOUNT, env.conversation_id, assistant.message_id, _context("retry-run"), **kwargs
        )
    )
    return env.chat.message_projection(ACCOUNT, assistant.message_id)


def test_retry_reuses_frozen_adoption_after_background_addition(env: _Env) -> None:
    env.remember(PREFERENCE, "m-pref")
    final = env.ask("解释贝叶斯定理")
    original = env.slice_blocks()[0]
    env.remember(BREVITY, "m-late")
    retried = _retry(env, final.message_id)
    assert retried is not None and retried.status == ChatMessageStatus.DONE
    assert env.slice_blocks() == [original]
    assert BREVITY not in env.payload_text()
    assert env.slice_audits()[-1].details["item_count"] == 1


def test_retry_after_deletion_never_restores_old_policy_profile(env: _Env) -> None:
    env.remember(PREFERENCE, "m-pref")
    final = env.ask("解释贝叶斯定理")
    item = env.atomic.list_items(ACCOUNT)[0]
    env.atomic.delete_item(ACCOUNT, item.profile_item_id, item.version)
    retried = _retry(env, final.message_id)
    assert retried is not None and retried.status == ChatMessageStatus.DONE
    assert env.slice_blocks() == []
    assert PREFERENCE not in env.payload_text()
    assert env.slice_audits()[-1].details["item_count"] == 0
    assert retried.context_note is not None
    assert retried.context_note.profile_item_count == 0


def test_final_gate_drop_matches_disclosure_and_audit(env: _Env) -> None:
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
    retried = _retry(env, final.message_id, model_quota=quota.model_dump(mode="json"))
    assert retried is not None and retried.status == ChatMessageStatus.DONE
    assert env.slice_blocks() == []
    assert retried.context_note is not None and retried.context_note.profile_item_count == 0
    assert env.slice_audits()[-1].details["item_count"] == 0


def test_required_constraint_budget_failure_stays_closed_on_retry(env: _Env) -> None:
    env.remember(CONSTRAINT, "m-time")
    _, assistant, _ = env.chat.start_generation(ACCOUNT, env.conversation_id, "制定学习计划")
    list(
        env.chat.stream_generation(
            ACCOUNT,
            env.conversation_id,
            assistant.message_id,
            _context("limited"),
            context_budget={"input_budget_tokens": 100, "input_token_estimate": 100},
        )
    )
    final = env.chat.message_projection(ACCOUNT, assistant.message_id)
    assert final is not None and final.status == ChatMessageStatus.ERROR
    assert final.error_code == "payload_budget_exceeded"
    retried = _retry(
        env,
        final.message_id,
        context_budget={"input_budget_tokens": 100, "input_token_estimate": 100},
    )
    assert retried is not None and retried.error_code == "payload_budget_exceeded"
    assert env.adapter.payloads == []
    recovered = _retry(env, retried.message_id)
    assert recovered is not None and recovered.status == ChatMessageStatus.DONE
    assert CONSTRAINT in env.slice_blocks()[0]


def test_saved_slice_round_trip_and_corrupt_cache_recovery(env: _Env) -> None:
    from bridges.contracts.profile_adoption import AdoptedProfileSlice

    env.remember(PREFERENCE, "m-pref")
    env.remember(HOBBY, "m-hobby")
    final = env.ask("解释贝叶斯定理")
    run = env.chat._repo.get_run_by_message(ACCOUNT, final.message_id)
    assert run is not None
    config = dict(run.config or {})
    restored = AdoptedProfileSlice.model_validate(config["adopted_profile_slice"])
    assert restored.compiled_policy_version == "purpose-slice-1.1"
    assert restored.purpose.query is None
    assert [item.fact_text for item in restored.select_subset()] == [PREFERENCE]
    assert all(item.fact_text == "" for item in restored.excluded_items)
    config["adopted_profile_slice"] = {"compiled_policy_version": "broken"}
    env.chat._repo.update_generation_config(ACCOUNT, run.run_id, config)
    retried = _retry(env, final.message_id)
    assert retried is not None and retried.status == ChatMessageStatus.DONE
    assert PREFERENCE in env.slice_blocks()[0]
    assert HOBBY not in env.slice_blocks()[0]
