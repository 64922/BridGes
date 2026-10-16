"""画像工单 12：反馈修正闭环的聊天端到端行为。

覆盖验收：
- 身份纠错后修正回合直接重写上一轮回答，旧结论不再进入后续上下文；
- 「今天简短」不写长期偏好，「以后先给结论」写入且当轮切片已包含；
- 表达投诉只调表达、零记忆写入；指向不明只澄清；
- 记忆保存失败时回答照常修正并如实反馈（不声称已更新）；
- 生成重试不重复写记忆，重试回答仍渲染同一反馈修正关系；
- 反馈与运行的关系（原回答/原运行/原切片/记忆结果）随消息投影可读。
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
from bridges.contracts.chat import ChatMessageStatus
from bridges.contracts.feedback_correction import (
    FeedbackCorrectionKind,
    FeedbackMemoryStatus,
)
from bridges.contracts.observability import AuditAction
from bridges.contracts.projects import ObjectDomain
from bridges.contracts.workflows import RunContextEnvelope
from bridges.observability.service import ObservabilityService
from bridges.profiles.adapters import InMemoryProfileRepository
from bridges.profiles.atomic import (
    AtomicProfileError,
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
from bridges.profiles.service import ProfileService
from bridges.storage.database import BridgesDatabase

ACCOUNT = "alice"
FEEDBACK_MARKER = "【本轮是对上一轮回答的反馈修正】"


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
    def __init__(self, tmp_path: Path) -> None:
        self.database_path = tmp_path / "bridges-feedback.db"
        database = BridgesDatabase(self.database_path)
        database.initialize()
        self.database = database
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
        self.conversation_id = self.chat.create_conversation(ACCOUNT).conversation_id
        self.turn = 0

    def ask(self, content: str, **kwargs: Any) -> Any:
        _, assistant, _ = self.chat.start_generation(
            ACCOUNT, self.conversation_id, content
        )
        self.turn += 1
        list(
            self.chat.stream_generation(
                ACCOUNT,
                self.conversation_id,
                assistant.message_id,
                _context(f"run-{self.turn}"),
                **kwargs,
            )
        )
        final = self.chat.message_projection(ACCOUNT, assistant.message_id)
        assert final is not None and final.status == ChatMessageStatus.DONE
        return final

    def retry(self, message_id: str, **kwargs: Any) -> Any:
        _, assistant, _ = self.chat.retry_generation(
            ACCOUNT, self.conversation_id, message_id
        )
        list(
            self.chat.stream_generation(
                ACCOUNT,
                self.conversation_id,
                assistant.message_id,
                _context("retry-run"),
                **kwargs,
            )
        )
        projection = self.chat.message_projection(ACCOUNT, assistant.message_id)
        assert projection is not None
        return projection

    def config(self, message_id: str) -> dict[str, Any]:
        run = self.chat._repo.get_run_by_message(  # noqa: SLF001
            ACCOUNT, message_id
        )
        assert run is not None
        return dict(run.config or {})

    def payload_text(self) -> str:
        return "\n".join(
            message["content"] for message in self.adapter.payloads[-1]["messages"]
        )

    def last_run_audits(self) -> list[Any]:
        return self.observability.list_audit_events(
            account_id=ACCOUNT, action=AuditAction.PROFILE_AUTO_WRITE
        )


@pytest.fixture
def env(tmp_path: Path) -> _Env:
    return _Env(tmp_path)


def test_identity_correction_rewrites_answer_and_memory(env: _Env) -> None:
    env.atomic.remember(ACCOUNT, "我是研究生", source_message_id="m-seed")
    first = env.ask("我研究生读得怎么样？")
    assert "我是研究生" in env.payload_text()

    corrected = env.ask("你记错了，我不是研究生，我是本科生。")

    assert FEEDBACK_MARKER in env.payload_text()
    assert "记忆结果：本次修改已写入长期记忆" in env.payload_text()
    texts = {item.text for item in env.atomic.list_items(ACCOUNT)}
    assert texts == {"我是本科生"}
    config = env.config(corrected.message_id)
    metadata = config["feedback_correction"]
    assert metadata["kind"] == FeedbackCorrectionKind.FACT_CORRECTION.value
    assert metadata["memory_status"] == FeedbackMemoryStatus.WRITTEN.value
    assert metadata["original_message_id"] == first.message_id
    assert metadata["original_run_id"]
    assert metadata["original_slice_id"]
    assert corrected.feedback_correction is not None
    assert (
        corrected.feedback_correction.kind
        is FeedbackCorrectionKind.FACT_CORRECTION
    )
    assert corrected.feedback_correction.original_message_id == first.message_id

    env.ask("我本科生读得怎么样？")
    follow_up = env.payload_text()
    assert "我是本科生" in follow_up
    assert "我是研究生" not in follow_up


def test_state_change_keeps_history_and_updates_current_state(env: _Env) -> None:
    env.atomic.remember(ACCOUNT, "我是在读大三学生", source_message_id="m-seed")
    env.ask("我现在读几年级？")

    changed = env.ask("现在升大四了。")

    assert FEEDBACK_MARKER in env.payload_text()
    metadata = env.config(changed.message_id)["feedback_correction"]
    assert metadata["kind"] == FeedbackCorrectionKind.STATE_CHANGE.value
    assert metadata["memory_status"] == FeedbackMemoryStatus.WRITTEN.value
    texts = {item.text for item in env.atomic.list_items(ACCOUNT)}
    # 旧状态作为真实历史保留，新状态成为当前值。
    assert "我是在读大三学生" in texts
    assert any("大四" in text for text in texts)


def test_session_request_and_expression_never_write_long_term(env: _Env) -> None:
    env.ask("解释一下贝叶斯定理。")

    session = env.ask("今天赶时间，简短一点。")
    metadata = env.config(session.message_id)["feedback_correction"]
    assert metadata["kind"] == "turn_request"
    assert metadata["memory_status"] == FeedbackMemoryStatus.NOT_APPLICABLE.value
    assert env.atomic.list_items(ACCOUNT) == []

    expression = env.ask("身份没错，但解释太难了。")
    metadata = env.config(expression.message_id)["feedback_correction"]
    assert metadata["kind"] == "expression_quality"
    assert "用更简单、更通俗的表达" in env.payload_text()
    assert env.atomic.list_items(ACCOUNT) == []


def test_long_term_preference_is_written_and_used_this_turn(env: _Env) -> None:
    env.ask("解释一下贝叶斯定理。")

    preference = env.ask("以后都先给结论。")

    metadata = env.config(preference.message_id)["feedback_correction"]
    assert metadata["kind"] == "long_term_preference"
    assert metadata["memory_status"] == FeedbackMemoryStatus.WRITTEN.value
    texts = {item.text for item in env.atomic.list_items(ACCOUNT)}
    assert "以后都先给结论。" in texts
    payload = env.payload_text()
    assert FEEDBACK_MARKER in payload
    assert "以后都先给结论。" in payload


def test_unclear_feedback_only_clarifies_without_memory(env: _Env) -> None:
    env.ask("解释一下贝叶斯定理。")

    unclear = env.ask("不是这个意思。")

    payload = env.payload_text()
    assert FEEDBACK_MARKER in payload
    assert "请用一两句话请用户明确指出" in payload
    metadata = env.config(unclear.message_id)["feedback_correction"]
    assert metadata["clarification_required"] is True
    assert metadata["memory_status"] == FeedbackMemoryStatus.NOT_APPLICABLE.value
    assert env.atomic.list_items(ACCOUNT) == []


def test_memory_failure_still_answers_honestly(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    env.atomic.remember(ACCOUNT, "我是研究生", source_message_id="m-seed")
    env.ask("我研究生读得怎么样？")

    def _fail(*args: object, **kwargs: object):
        raise AtomicProfileError("storage unavailable")

    monkeypatch.setattr(env.atomic, "correct_item", _fail)
    corrected = env.ask("你记错了，我不是研究生，我是本科生。")

    payload = env.payload_text()
    assert FEEDBACK_MARKER in payload
    assert "不得声称已经记住或已经更新画像" in payload
    metadata = env.config(corrected.message_id)["feedback_correction"]
    assert metadata["memory_status"] == FeedbackMemoryStatus.FAILED.value
    assert "我是研究生" in {item.text for item in env.atomic.list_items(ACCOUNT)}


def test_retry_reuses_feedback_relation_without_rewriting_memory(env: _Env) -> None:
    env.atomic.remember(ACCOUNT, "我是研究生", source_message_id="m-seed")
    env.ask("我研究生读得怎么样？")
    corrected = env.ask("你记错了，我不是研究生，我是本科生。")
    item = next(iter(env.atomic.list_items(ACCOUNT)))
    version = item.version

    retried = env.retry(corrected.message_id)

    assert retried.status == ChatMessageStatus.DONE
    assert FEEDBACK_MARKER in env.payload_text()
    assert "记忆结果：本次修改已写入长期记忆" in env.payload_text()
    metadata = env.config(retried.message_id)["feedback_correction"]
    assert metadata["memory_status"] == FeedbackMemoryStatus.WRITTEN.value
    assert metadata["original_message_id"] is not None
    remaining = env.atomic.list_items(ACCOUNT)
    assert len(remaining) == 1 and remaining[0].version == version


def test_repeat_of_identical_feedback_is_idempotent(env: _Env) -> None:
    env.atomic.remember(ACCOUNT, "我是研究生", source_message_id="m-seed")
    env.ask("我研究生读得怎么样？")
    env.ask("你记错了，我不是研究生，我是本科生。")
    item = next(iter(env.atomic.list_items(ACCOUNT)))
    version = item.version

    env.ask("你记错了，我不是研究生，我是本科生。")

    remaining = env.atomic.list_items(ACCOUNT)
    assert len(remaining) == 1
    assert remaining[0].version == version
    assert remaining[0].text == "我是本科生"


def test_corrected_fact_is_used_in_new_conversation(env: _Env) -> None:
    env.atomic.remember(ACCOUNT, "我是研究生", source_message_id="m-seed")
    env.ask("我研究生读得怎么样？")
    env.ask("你记错了，我不是研究生，我是本科生。")

    other = env.chat.create_conversation(ACCOUNT).conversation_id
    _, assistant, _ = env.chat.start_generation(
        ACCOUNT, other, "我本科生读得怎么样？"
    )
    env.turn += 1
    list(
        env.chat.stream_generation(
            ACCOUNT, other, assistant.message_id, _context(f"run-{env.turn}")
        )
    )

    payload = env.payload_text()
    assert "我是本科生" in payload
    assert "我是研究生" not in payload


def test_feedback_relation_survives_restart(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    env.atomic.remember(ACCOUNT, "我是研究生", source_message_id="m-seed")
    env.ask("我研究生读得怎么样？")
    corrected = env.ask("你记错了，我不是研究生，我是本科生。")
    env.database.close()

    reopened = BridgesDatabase(env.database_path)
    registry = CapabilityRegistry()
    registry.register(_chat_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", _CapturingStreamAdapter())
    four_dimensions = FourDimensionProfileService(
        source_repository=InMemoryProfileRepository(),
        repository=InMemoryFourDimensionProfileRepository(),
    )
    atomic = AtomicProfileService(
        four_dimensions, InMemoryAtomicProfileRepository()
    )
    automatic = AutomaticProfileService(
        four_dimension_service=four_dimensions,
        repository=InMemoryAutomaticProfileRepository(),
        atomic_profile_service=atomic,
    )
    restored = ChatService(
        repository=ConversationRepository(reopened),
        gateway=gateway,
        profile_service=ProfileService(repository=InMemoryProfileRepository()),
        automatic_profile_service=automatic,
        four_dimension_profile_service=four_dimensions,
        atomic_profile_service=atomic,
        observability_service=ObservabilityService(),
    )
    projection = restored.message_projection(ACCOUNT, corrected.message_id)

    assert projection is not None
    assert projection.feedback_correction is not None
    assert projection.feedback_correction.kind is FeedbackCorrectionKind.FACT_CORRECTION
    assert (
        projection.feedback_correction.memory_status
        is FeedbackMemoryStatus.WRITTEN
    )
    reopened.close()


def test_generation_failure_does_not_roll_back_correction(env: _Env) -> None:
    from tests.chat.chat_service_support import _with_chunks

    env.atomic.remember(ACCOUNT, "我是研究生", source_message_id="m-seed")
    env.ask("我研究生读得怎么样？")
    env.chat._gateway = _with_chunks(  # noqa: SLF001
        env.chat,
        [
            StreamChunk(kind="delta", delta="部分"),
            StreamChunk(
                kind="error", error_code="rate_limit", error_message="vendor msg"
            ),
        ],
    )
    _, assistant, _ = env.chat.start_generation(
        ACCOUNT, env.conversation_id, "你记错了，我不是研究生，我是本科生。"
    )
    list(
        env.chat.stream_generation(
            ACCOUNT, env.conversation_id, assistant.message_id, _context("fail-run")
        )
    )

    failed = env.chat.message_projection(ACCOUNT, assistant.message_id)
    assert failed is not None and failed.status == ChatMessageStatus.ERROR
    # 改答失败不回滚已成功纠正；投影仍如实保留反馈关系与记忆结果。
    assert "我是本科生" in {item.text for item in env.atomic.list_items(ACCOUNT)}
    assert failed.feedback_correction is not None
    assert (
        failed.feedback_correction.memory_status is FeedbackMemoryStatus.WRITTEN
    )


def test_feedback_relation_is_exported_without_user_text(env: _Env) -> None:
    from bridges.lifecycle.catalog import export_rows

    env.atomic.remember(ACCOUNT, "我是研究生", source_message_id="m-seed")
    env.ask("我研究生读得怎么样？")
    env.ask("你记错了，我不是研究生，我是本科生。")

    rows = export_rows(env.database, ACCOUNT, "generation_runs")
    exported = [
        row["feedback_correction"]
        for row in rows
        if isinstance(row.get("feedback_correction"), dict)
    ]
    assert len(exported) == 1
    payload = str(exported[0])
    assert exported[0]["kind"] == FeedbackCorrectionKind.FACT_CORRECTION.value
    assert exported[0]["memory_status"] == FeedbackMemoryStatus.WRITTEN.value
    assert "研究生" not in payload
    assert "本科生" not in payload
