"""画像工单 09：敏感自述、用途与凭据的写入/采用边界（本票新增路径）。

覆盖验收：心理学兴趣可正常保存；答辩焦虑只属于会话、不进长期记忆；
明确记住的过敏信息按用途使用（户外建议才携带，其他问题不带）；未说明用途
的敏感自述如实拒绝并请用户补充用途；密码/验证码/密钥即使明确要求也不进入
记忆与派生存储；缩小/清除用途后旧采用与迟到提交失效，恢复不扩大范围；
失败不虚假声称已保存，也不新增逐条确认流程。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bridges.chat.turn import profile_memory_context
from bridges.contracts.atomic_profile import (
    AtomicProfileItemModifyRequest,
    AtomicProfileMemoryBlockedReason,
    AtomicProfileMemoryStatus,
)
from bridges.contracts.profile_extraction import (
    ProfileExtractionOutcome,
    ProfileExtractionOutput,
    ProfileExtractionStatus,
)
from bridges.contracts.profiles import FourDimensionConfidence
from bridges.profiles import (
    AtomicProfileService,
    AutomaticProfileService,
    FourDimensionProfileService,
    InMemoryAtomicProfileRepository,
    InMemoryAutomaticProfileRepository,
    InMemoryFourDimensionProfileRepository,
    SqliteAtomicProfileRepository,
    SqliteFourDimensionProfileRepository,
)
from bridges.profiles.adapters import InMemoryProfileRepository
from bridges.profiles.atomic import (
    RECALL_PURPOSE_MISMATCH_REASON,
    RECALL_PURPOSE_REQUIRED_REASON,
)
from bridges.profiles.commit import ProfileFactSubmission
from bridges.profiles.purpose import build_purpose
from bridges.profiles.sensitive import (
    MemorySensitivity,
    classify_memory_sensitivity,
    parse_usage_purpose,
    redact_credentials,
    usage_purpose_matches,
)
from bridges.storage.database import BridgesDatabase

ACCOUNT = "account-alice"
OUTDOOR_QUERY = "我花粉过敏，周末想去户外徒步，有什么建议？"
PLAN_QUERY = "帮我制定下周的学习计划"


def _memory_stack() -> tuple[AtomicProfileService, AutomaticProfileService]:
    four = FourDimensionProfileService(
        source_repository=InMemoryProfileRepository(),
        repository=InMemoryFourDimensionProfileRepository(),
    )
    atomic = AtomicProfileService(four, InMemoryAtomicProfileRepository())
    automatic = AutomaticProfileService(
        four_dimension_service=four,
        repository=InMemoryAutomaticProfileRepository(),
        atomic_profile_service=atomic,
    )
    return atomic, automatic


def _remember_directive(
    automatic: AutomaticProfileService,
    content: str,
    *,
    message_id: str = "message-1",
    run_id: str = "run-1",
):
    return automatic.process_synchronous_controls(
        ACCOUNT,
        conversation_id="conversation-1",
        message_id=message_id,
        content=content,
        run_id=run_id,
        mode="companion",
    )


def _adopted_texts(atomic: AtomicProfileService, query: str) -> list[str]:
    slice_ = atomic.compile_adopted_slice(
        ACCOUNT,
        run_id=f"slice-{query}",
        purpose=build_purpose(mode="companion", query=query),
    )
    return [item.fact_text for item in slice_.adopted_items]


def _exclusion_reasons(atomic: AtomicProfileService, query: str) -> dict[str, str]:
    slice_ = atomic.compile_adopted_slice(
        ACCOUNT,
        run_id=f"slice-{query}",
        purpose=build_purpose(mode="companion", query=query),
    )
    return {item.fact_text: item.exclusion_reason for item in slice_.excluded_items}


# ---------------------------------------------------------------------------
# 分类、用途解析与凭据遮罩（纯规则）
# ---------------------------------------------------------------------------


def test_sensitivity_classification_rules() -> None:
    assert classify_memory_sensitivity("我对心理学很感兴趣") is MemorySensitivity.ORDINARY
    assert classify_memory_sensitivity("我在学习密码学") is MemorySensitivity.ORDINARY
    assert classify_memory_sensitivity("我对花生过敏") is MemorySensitivity.SENSITIVE
    assert classify_memory_sensitivity("我有点失眠") is MemorySensitivity.SENSITIVE
    assert (
        classify_memory_sensitivity("我的密码是 abc123")
        is MemorySensitivity.CREDENTIAL
    )
    assert (
        classify_memory_sensitivity("验证码是 123456")
        is MemorySensitivity.CREDENTIAL
    )
    assert (
        classify_memory_sensitivity("访问密钥是 sk-demo-123")
        is MemorySensitivity.CREDENTIAL
    )


def test_usage_purpose_parsing_and_matching() -> None:
    assert parse_usage_purpose("我有花粉过敏，户外建议要注意") == "户外"
    assert parse_usage_purpose("我对花生过敏，用于户外活动提醒") == "户外活动"
    assert parse_usage_purpose("我有点焦虑，用途是答辩准备") == "答辩准备"
    assert parse_usage_purpose("我对花生过敏") is None
    assert usage_purpose_matches("户外", OUTDOOR_QUERY)
    assert usage_purpose_matches("户外活动", OUTDOOR_QUERY)
    assert not usage_purpose_matches("户外", PLAN_QUERY)
    assert not usage_purpose_matches("", OUTDOOR_QUERY)


def test_credential_assignment_is_redacted_before_storage() -> None:
    redacted = redact_credentials("我的密码是 abc123，请保密")
    assert "abc123" not in redacted
    assert "<已隐藏>" in redacted
    # 非凭据文本原样保留。
    assert redact_credentials("我住在合肥") == "我住在合肥"


# ---------------------------------------------------------------------------
# 心理学兴趣：普通事实，正常保存与采用
# ---------------------------------------------------------------------------


def test_psychology_interest_remains_ordinary() -> None:
    atomic, automatic = _memory_stack()
    result = _remember_directive(automatic, "记住，我对心理学很感兴趣")

    assert result is not None and result.memory is not None
    assert result.memory.status is AtomicProfileMemoryStatus.REMEMBERED
    items = atomic.list_items(ACCOUNT)
    assert [item.text for item in items] == ["我对心理学很感兴趣"]
    assert items[0].usage_purpose is None
    assert "我对心理学很感兴趣" in _adopted_texts(atomic, "推荐几本心理学科普书")


# ---------------------------------------------------------------------------
# 答辩焦虑：会话内情绪，不进长期记忆（自动路径零写入）
# ---------------------------------------------------------------------------


def test_sensitive_facts_never_enter_automatic_long_term_memory() -> None:
    atomic, _ = _memory_stack()
    assert (
        atomic.commit_fact(
            ACCOUNT,
            ProfileFactSubmission(
                text="我最近很焦虑",
                confidence=FourDimensionConfidence.MEDIUM,
                evidence_message_id="message-anxious",
            ),
        )
        is None
    )
    assert (
        atomic.commit_fact(
            ACCOUNT,
            ProfileFactSubmission(
                text="我对花生过敏",
                confidence=FourDimensionConfidence.HIGH,
                evidence_message_id="message-allergy",
            ),
        )
        is None
    )
    assert (
        atomic.commit_fact(
            ACCOUNT,
            ProfileFactSubmission(
                text="我的验证码是 123456",
                confidence=FourDimensionConfidence.HIGH,
                evidence_message_id="message-code",
            ),
        )
        is None
    )
    assert atomic.list_items(ACCOUNT) == []


class _AllergyExtractor:
    """抽取器提出敏感候选：服务端必须在候选级拦截（零写入）。"""

    version = "issue09-sensitive-v1"

    def extract(self, *, message_id: str, **_: object) -> ProfileExtractionOutput:
        return ProfileExtractionOutput.model_validate(
            {
                "items": [
                    {
                        "normalized_value": "对花生过敏",
                        "fact_text": "对花生过敏",
                        "evidence_ref": message_id,
                        "reliability": 0.95,
                        "action": "create",
                    }
                ]
            }
        )


def test_automatic_candidate_gate_ignores_sensitive_values() -> None:
    four = FourDimensionProfileService(
        source_repository=InMemoryProfileRepository(),
        repository=InMemoryFourDimensionProfileRepository(),
    )
    atomic = AtomicProfileService(four, InMemoryAtomicProfileRepository())
    automatic = AutomaticProfileService(
        four_dimension_service=four,
        repository=InMemoryAutomaticProfileRepository(),
        extractor=_AllergyExtractor(),
        atomic_profile_service=atomic,
    )

    result = automatic.preprocess_message(
        ACCOUNT,
        conversation_id="conversation-1",
        message_id="message-1",
        content="我对花生过敏",
        run_id="run-1",
        mode="companion",
    )

    assert result is not None
    assert result.run.status == ProfileExtractionStatus.SUCCEEDED
    assert atomic.list_items(ACCOUNT) == []


# ---------------------------------------------------------------------------
# 明确记住的过敏信息：按用途保存、按用途采用
# ---------------------------------------------------------------------------


def test_explicit_allergy_remember_saves_with_purpose_and_gates_adoption() -> None:
    atomic, automatic = _memory_stack()
    result = _remember_directive(automatic, "记住我有花粉过敏，户外建议要注意")

    assert result is not None and result.memory is not None
    assert result.memory.status is AtomicProfileMemoryStatus.REMEMBERED
    items = atomic.list_items(ACCOUNT)
    assert len(items) == 1
    assert items[0].usage_purpose == "户外"

    # 用途匹配的问题携带这条信息；其他问题不带（历史用途只约束采用）。
    assert items[0].text in _adopted_texts(atomic, OUTDOOR_QUERY)
    reasons = _exclusion_reasons(atomic, PLAN_QUERY)
    assert reasons[items[0].text] == RECALL_PURPOSE_MISMATCH_REASON


def test_sensitive_remember_without_purpose_asks_for_one_and_saves_nothing() -> None:
    atomic, automatic = _memory_stack()
    result = _remember_directive(automatic, "记住我对花生过敏")

    assert result is not None and result.memory is not None
    assert result.memory.status is AtomicProfileMemoryStatus.UNRESOLVED
    assert (
        result.memory.blocked_reason
        is AtomicProfileMemoryBlockedReason.PURPOSE_REQUIRED
    )
    assert result.run.outcome is ProfileExtractionOutcome.SUCCEEDED_MEMORY_UNRESOLVED
    assert result.run.status == ProfileExtractionStatus.SUCCEEDED
    assert atomic.list_items(ACCOUNT) == []
    # 被拒绝的原文不留在抽取运行记账里；来源哈希保留供重放去重。
    assert "花生" not in result.run.source_snapshot
    assert result.run.source_hash

    # 重放同一消息：结论一致、仍然零写入、不进入重试。
    replay = _remember_directive(automatic, "记住我对花生过敏")
    assert replay is not None and replay.memory is not None
    assert replay.memory.status is AtomicProfileMemoryStatus.UNRESOLVED
    assert atomic.list_items(ACCOUNT) == []

    # 补充用途后可以保存；用途兜底与显式用途同一条路径。
    saved = atomic.remember(ACCOUNT, "我对花生过敏", usage_purpose="体检提醒")
    assert saved.usage_purpose == "体检"
    assert "我对花生过敏" in _adopted_texts(
        atomic, "我对花生过敏，体检要注意什么？"
    )


@pytest.mark.parametrize(
    "content,secret",
    [
        ("记住我的密码是 abc123456", "abc123456"),
        ("记住我的验证码是 246810", "246810"),
        ("记住我的访问密钥是 sk-demo-98765", "sk-demo-98765"),
    ],
)
def test_credentials_are_refused_even_when_explicitly_requested(
    content: str, secret: str
) -> None:
    atomic, automatic = _memory_stack()
    result = _remember_directive(automatic, content)

    assert result is not None and result.memory is not None
    assert result.memory.status is AtomicProfileMemoryStatus.UNRESOLVED
    assert result.memory.blocked_reason is AtomicProfileMemoryBlockedReason.CREDENTIAL
    assert result.run.outcome is ProfileExtractionOutcome.SUCCEEDED_MEMORY_UNRESOLVED
    assert atomic.list_items(ACCOUNT) == []
    # 凭据值不进入记忆与派生存储（运行记账整体不含明文）。
    assert secret not in result.run.source_snapshot
    assert secret not in result.run.model_dump_json()


# ---------------------------------------------------------------------------
# 用途收窄/清除：旧采用与迟到提交失效；恢复不扩大范围
# ---------------------------------------------------------------------------


def test_purpose_clear_invalidates_adoption_and_late_commits() -> None:
    atomic, _ = _memory_stack()
    item = atomic.remember(
        ACCOUNT, "我对花生过敏，户外建议要注意", usage_purpose="户外"
    )
    original_version = item.version
    assert item.text in _adopted_texts(atomic, OUTDOOR_QUERY)

    cleared = atomic.modify_item(
        ACCOUNT,
        item.profile_item_id,
        AtomicProfileItemModifyRequest(
            text=item.text, version=original_version, usage_purpose=""
        ),
    )
    assert cleared.usage_purpose is None
    assert cleared.version == original_version + 1
    # 清除用途后不再进入任何回答；迟到提交也不落地（敏感事实自动路径拒绝）。
    reasons = _exclusion_reasons(atomic, OUTDOOR_QUERY)
    assert reasons[item.text] == RECALL_PURPOSE_REQUIRED_REASON
    assert (
        atomic.commit_fact(
            ACCOUNT,
            ProfileFactSubmission(
                text=item.text,
                confidence=FourDimensionConfidence.HIGH,
                evidence_message_id="message-late",
            ),
        )
        is None
    )
    assert [entry.text for entry in atomic.list_items(ACCOUNT)] == [item.text]

    # 恢复用途：只在匹配用途时重新采用，不扩大范围。
    restored = atomic.modify_item(
        ACCOUNT,
        item.profile_item_id,
        AtomicProfileItemModifyRequest(
            text=item.text, version=cleared.version, usage_purpose="户外"
        ),
    )
    assert restored.usage_purpose == "户外"
    assert item.text in _adopted_texts(atomic, OUTDOOR_QUERY)
    assert item.text not in _adopted_texts(atomic, PLAN_QUERY)


def test_single_authorized_sensitive_item_does_not_authorize_others() -> None:
    atomic, _ = _memory_stack()
    allergy = atomic.remember(
        ACCOUNT, "我有花粉过敏，户外建议要注意", usage_purpose="户外"
    )
    sleep = atomic.remember(ACCOUNT, "我有点失眠，作息建议要注意", usage_purpose="作息")

    outdoor = _adopted_texts(atomic, OUTDOOR_QUERY)
    assert allergy.text in outdoor
    assert sleep.text not in outdoor
    assert (
        _exclusion_reasons(atomic, OUTDOOR_QUERY)[sleep.text]
        == RECALL_PURPOSE_MISMATCH_REASON
    )
    assert sleep.text in _adopted_texts(atomic, "我失眠，作息建议怎么安排？")


# ---------------------------------------------------------------------------
# 聊天侧诚实回复约束（不声称已保存、不回显凭据）
# ---------------------------------------------------------------------------


def test_chat_copy_for_blocked_remember_is_honest() -> None:
    credential = profile_memory_context(
        {
            "memory_kind": "remember",
            "status": "unresolved",
            "matched_count": 0,
            "blocked_reason": "credential",
        }
    )
    assert credential is not None
    assert "不得声称已记住" in credential
    assert "不会长期保存" in credential

    purpose_required = profile_memory_context(
        {
            "memory_kind": "remember",
            "status": "unresolved",
            "matched_count": 0,
            "blocked_reason": "purpose_required",
        }
    )
    assert purpose_required is not None
    assert "不得声称已记住" in purpose_required
    assert "用途" in purpose_required

    # 常规记住路径的既有语义保持不变。
    remembered = profile_memory_context(
        {"memory_kind": "remember", "status": "remembered", "matched_count": 1}
    )
    assert remembered is not None and "已真实写入" in remembered


# ---------------------------------------------------------------------------
# 持久化：用途落库、迁移后旧行为默认无用途
# ---------------------------------------------------------------------------


def test_usage_purpose_persists_across_sqlite_restart(tmp_path: Path) -> None:
    path = tmp_path / "issue09-purpose.db"
    database = BridgesDatabase(path)
    database.initialize()
    four = FourDimensionProfileService(
        source_repository=InMemoryProfileRepository(),
        repository=SqliteFourDimensionProfileRepository(database),
    )
    atomic = AtomicProfileService(four, SqliteAtomicProfileRepository(database))
    item = atomic.remember(
        ACCOUNT, "我有花粉过敏，户外建议要注意", usage_purpose="户外"
    )
    database.close()

    reopened = BridgesDatabase(path)
    reopened.initialize()
    four = FourDimensionProfileService(
        source_repository=InMemoryProfileRepository(),
        repository=SqliteFourDimensionProfileRepository(reopened),
    )
    atomic = AtomicProfileService(four, SqliteAtomicProfileRepository(reopened))
    restored = atomic.get_item(ACCOUNT, item.profile_item_id)
    assert restored.usage_purpose == "户外"
    assert restored.text == item.text
    reopened.close()
