"""改进工单 22：统一画像用途与表达策略采用快照。

覆盖验收行为：无类别的「简短直接」偏好进入表达策略且模型上下文不再同时
声称没有画像；不相关背景不进入策略，当前明确要求覆盖默认偏好且元数据与
实际输入一致；偏好修改/删除后后续调用与旧重试不再采用，两账户隔离。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from bridges.chat.global_writing_policy import GlobalWritingPolicyCompiler
from bridges.chat.lightweight_policy import (
    RELEASE_BASELINE_POLICY_VERSION,
    ChatLightweightPolicyCompiler,
)
from bridges.contracts.atomic_profile import AtomicProfileItemModifyRequest
from bridges.contracts.chat import ChatMessageStatus, ChatMode
from bridges.profiles.adapters import InMemoryProfileRepository
from bridges.profiles.atomic import (
    AtomicProfileService,
    InMemoryAtomicProfileRepository,
)
from bridges.profiles.four_dimensions import (
    FourDimensionProfileService,
    InMemoryFourDimensionProfileRepository,
)
from bridges.profiles.purpose import build_purpose
from tests.chat.test_improvement19_purpose_aware_slice import (
    ACCOUNT,
    BREVITY,
    HOBBY,
    _context,
    _Env,
)

DETAIL = "我喜欢详细回答"
TONE = "回答我的时候幽默一点"
_MIXED = "我喜欢简短回答，先看例子，先给结论，保留公式，以后称呼我老王"
_POLICY_MARKER = "【全局轻量有人味表达策略】"


# ---------------------------------------------------------------------------
# 编译单元：采用快照 → 少量表达约束
# ---------------------------------------------------------------------------


def _atomic_service() -> AtomicProfileService:
    four = FourDimensionProfileService(
        source_repository=InMemoryProfileRepository(),
        repository=InMemoryFourDimensionProfileRepository(),
    )
    return AtomicProfileService(four, InMemoryAtomicProfileRepository())


def _adopted(texts: list[str], query: str = "解释贝叶斯定理") -> Any:
    atomic = _atomic_service()
    for index, text in enumerate(texts):
        atomic.remember(ACCOUNT, text, source_message_id=f"m-{index}")
    return atomic.compile_adopted_slice(
        ACCOUNT,
        run_id="unit",
        purpose=build_purpose(mode="companion", query=query),
    )


def test_adopted_brevity_enters_policy_without_missing_profile_claim() -> None:
    adopted = _adopted([BREVITY])
    assert [item.fact_text for item in adopted.adopted_items] == [BREVITY]

    snapshot = ChatLightweightPolicyCompiler().compile(
        ChatMode.COMPANION,
        user_text="解释贝叶斯定理",
        adopted_slice=adopted,
    )

    assert "adopted-brevity-default" in snapshot.rule_ids
    assert "用户长期偏好简短直接" in snapshot.system_block
    # 同一采用结果下不再声称没有画像，也不把偏好原句倾倒成高权限指令。
    assert "本轮没有可用画像信息" not in snapshot.system_block
    assert BREVITY not in snapshot.system_block
    assert snapshot.profile_slice_id == adopted.slice_id
    assert snapshot.profile_revocation_version == adopted.revocation_version
    assert snapshot.profile_decisions == ("expression_length",)
    metadata = snapshot.metadata()
    assert metadata["profile_decisions"] == ["expression_length"]
    assert metadata["profile_slice_id"] == adopted.slice_id
    assert metadata["profile_revocation_version"] == adopted.revocation_version
    assert metadata["profile_item_count"] == len(adopted.adopted_items)


def test_fact_preference_rule_keeps_source_text_in_data_voice() -> None:
    adopted = _adopted([TONE])
    snapshot = ChatLightweightPolicyCompiler().compile(
        ChatMode.COMPANION,
        user_text="解释贝叶斯定理",
        adopted_slice=adopted,
    )

    assert "adopted-tone" in snapshot.rule_ids
    assert "用户声明的语气偏好" in snapshot.system_block
    assert TONE in snapshot.system_block
    assert snapshot.profile_decisions == ("expression_tone",)


def test_adopted_rules_are_capped_and_ordered() -> None:
    adopted = _adopted([_MIXED])
    snapshot = ChatLightweightPolicyCompiler().compile(
        ChatMode.COMPANION,
        user_text="解释贝叶斯定理",
        adopted_slice=adopted,
    )

    adopted_rule_ids = [rid for rid in snapshot.rule_ids if rid.startswith("adopted-")]
    assert len(adopted_rule_ids) == 4
    assert "adopted-brevity-default" in adopted_rule_ids
    assert "adopted-formula" in adopted_rule_ids
    # 单条多标签也受每轮上限约束：最后的称呼偏好不进入规则。
    assert "adopted-address" not in adopted_rule_ids


def test_unrelated_background_is_not_compiled_into_policy() -> None:
    adopted = _adopted([BREVITY, HOBBY])
    assert [item.fact_text for item in adopted.adopted_items] == [BREVITY]
    snapshot = ChatLightweightPolicyCompiler().compile(
        ChatMode.COMPANION,
        user_text="解释贝叶斯定理",
        adopted_slice=adopted,
    )

    assert HOBBY not in snapshot.system_block
    assert HOBBY not in snapshot.profile_items
    assert snapshot.profile_decisions == ("expression_length",)


def test_explicit_long_derivation_overrides_default_brevity() -> None:
    adopted = _adopted([BREVITY], query="请详细展开推导")
    assert adopted.adopted_items == ()
    assert adopted.excluded_items[0].fact_text == BREVITY

    snapshot = ChatLightweightPolicyCompiler().compile(
        ChatMode.COMPANION,
        user_text="请详细展开推导",
        adopted_slice=adopted,
    )

    assert "adopted-brevity-default" not in snapshot.rule_ids
    assert "detail-follows-request" in snapshot.rule_ids
    assert "用户明确要求详细" in snapshot.system_block


def test_profile_context_without_adopted_items_avoids_missing_claim() -> None:
    snapshot = ChatLightweightPolicyCompiler().compile(
        ChatMode.COMPANION,
        user_text="什么是黑洞？",
        profile_context="以下是本轮为你参考的已授权信息（仅包含与你当前任务相关的少量内容）",
    )

    assert "本轮没有可用画像信息" not in snapshot.system_block
    assert "以对话中的画像数据块为准" in snapshot.system_block


def test_failed_profile_falls_back_to_safe_baseline() -> None:
    adopted = _adopted([BREVITY])
    snapshot = ChatLightweightPolicyCompiler().compile(
        ChatMode.COMPANION,
        user_text="解释贝叶斯定理",
        adopted_slice=adopted,
        profile_failed=True,
    )

    assert snapshot.fallback_reason == "profile_slice_unavailable"
    assert snapshot.profile_decisions == ()
    assert "adopted-brevity-default" not in snapshot.rule_ids


# ---------------------------------------------------------------------------
# 集成：真实回合中的策略/画像/元数据一致性
# ---------------------------------------------------------------------------


@pytest.fixture
def env(tmp_path: Path) -> _Env:
    return _Env(tmp_path)


def _retry(env: _Env, message_id: str) -> Any:
    _, assistant, _ = env.chat.retry_generation(ACCOUNT, env.conversation_id, message_id)
    list(
        env.chat.stream_generation(
            ACCOUNT, env.conversation_id, assistant.message_id, _context("retry-run")
        )
    )
    return env.chat.message_projection(ACCOUNT, assistant.message_id)


def _policy_blocks(env: _Env) -> list[str]:
    return [
        message["content"]
        for message in env.payload()["messages"]
        if message["role"] == "system" and _POLICY_MARKER in message["content"]
    ]


def test_chat_policy_and_profile_block_share_adopted_snapshot(env: _Env) -> None:
    env.remember(BREVITY, "m-short")
    final = env.ask("解释贝叶斯定理")

    payload = env.payload()
    metadata = payload["global_writing_policy"]
    run = env.chat._repo.get_run_by_message(ACCOUNT, final.message_id)  # noqa: SLF001
    stored = run.config["adopted_profile_slice"]
    assert metadata["version"] == RELEASE_BASELINE_POLICY_VERSION
    assert metadata["profile_slice_id"] == stored["slice_id"]
    assert metadata["profile_revocation_version"] == stored["revocation_version"]
    assert metadata["profile_decisions"] == ["expression_length"]
    assert metadata["profile_item_count"] == 1

    blocks = _policy_blocks(env)
    assert len(blocks) == 1
    assert "用户长期偏好简短直接" in blocks[0]
    assert BREVITY not in blocks[0]
    assert "本轮没有可用画像信息" not in env.payload_text()
    assert BREVITY in env.slice_blocks()[0]


def test_retry_after_preference_modify_recompiles_policy_and_slice(env: _Env) -> None:
    env.remember(BREVITY, "m-short")
    final = env.ask("解释贝叶斯定理")
    first = env.chat._repo.get_run_by_message(  # noqa: SLF001
        ACCOUNT, final.message_id
    ).config["global_writing_policy"]

    item = env.atomic.list_items(ACCOUNT)[0]
    env.atomic.modify_item(
        ACCOUNT,
        item.profile_item_id,
        AtomicProfileItemModifyRequest(text=DETAIL, version=item.version),
    )
    retried = _retry(env, final.message_id)

    assert retried is not None and retried.status == ChatMessageStatus.DONE
    second = env.chat._repo.get_run_by_message(  # noqa: SLF001
        ACCOUNT, retried.message_id
    ).config["global_writing_policy"]
    assert "adopted-detail-default" in second["rule_ids"]
    assert "adopted-brevity-default" not in second["rule_ids"]
    assert second["system_block"] != first["system_block"]
    assert second["profile_decisions"] == ["expression_length"]

    assert DETAIL in env.slice_blocks()[0]
    assert BREVITY not in env.payload_text()
    assert "用户长期偏好详细" in _policy_blocks(env)[-1]


def test_retry_after_delete_drops_rules_and_claims_no_profile(env: _Env) -> None:
    env.remember(BREVITY, "m-short")
    final = env.ask("解释贝叶斯定理")
    item = env.atomic.list_items(ACCOUNT)[0]
    env.atomic.delete_item(ACCOUNT, item.profile_item_id, item.version)
    retried = _retry(env, final.message_id)

    assert retried is not None and retried.status == ChatMessageStatus.DONE
    assert env.slice_blocks() == []
    assert BREVITY not in env.payload_text()
    metadata = env.payload()["global_writing_policy"]
    assert metadata["profile_decisions"] == []
    assert metadata["profile_item_count"] == 0
    assert "本轮没有可用画像信息" in _policy_blocks(env)[-1]


def test_delete_between_compilation_and_send_drops_adopted_rules(
    env: _Env, monkeypatch: Any
) -> None:
    """发送前撤回不仅清数据块，也不能从采用对象重新编译出旧偏好。"""
    env.remember(BREVITY, "m-race")
    original = env.atomic.is_adopted_slice_current

    def revoke_before_check(account: str, adopted: Any) -> bool:
        item = env.atomic.list_items(account)[0]
        env.atomic.delete_item(account, item.profile_item_id, item.version)
        return original(account, adopted)

    monkeypatch.setattr(env.atomic, "is_adopted_slice_current", revoke_before_check)
    final = env.ask("解释贝叶斯定理")

    assert env.slice_blocks() == []
    assert "用户长期偏好简短直接" not in env.payload_text()
    assert env.payload()["global_writing_policy"]["profile_item_count"] == 0
    assert env.payload()["global_writing_policy"]["profile_decisions"] == []
    assert final.context_note.profile_item_count == 0


def test_retry_after_recreating_same_preference_rebinds_metadata(env: _Env) -> None:
    """正文相同不代表采用版本相同；删除后重新记住必须绑定新来源。"""
    env.remember(BREVITY, "m-old")
    final = env.ask("解释贝叶斯定理")
    item = env.atomic.list_items(ACCOUNT)[0]
    env.atomic.delete_item(ACCOUNT, item.profile_item_id, item.version)
    env.remember(BREVITY, "m-new")
    retried = _retry(env, final.message_id)
    config = env.chat._repo.get_run_by_message(  # noqa: SLF001
        ACCOUNT, retried.message_id
    ).config
    assert (
        config["global_writing_policy"]["profile_slice_id"]
        == config["adopted_profile_slice"]["slice_id"]
    )
    assert (
        config["global_writing_policy"]["profile_revocation_version"]
        == config["adopted_profile_slice"]["revocation_version"]
    )


def test_chat_policy_failure_drops_profile_and_updates_disclosure(
    env: _Env, monkeypatch: Any
) -> None:
    env.remember(BREVITY, "m-fallback")

    original = GlobalWritingPolicyCompiler.compile

    def fail_normal(self: Any, *args: Any, **kwargs: Any) -> Any:
        if kwargs.get("adopted_slice") is not None:
            raise RuntimeError("表达资源不可用")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(GlobalWritingPolicyCompiler, "compile", fail_normal)
    final = env.ask("解释贝叶斯定理")
    assert env.slice_blocks() == []
    assert BREVITY not in env.payload_text()
    assert env.payload()["global_writing_policy"]["profile_item_count"] == 0
    assert final.context_note.profile_item_count == 0
    assert env.slice_audits()[-1].details["item_count"] == 0
    config = env.chat._repo.get_run_by_message(ACCOUNT, final.message_id).config  # noqa: SLF001
    assert "adopted_profile_slice" not in config
