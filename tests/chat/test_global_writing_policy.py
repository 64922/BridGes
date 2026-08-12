"""Issue 17：全局轻量有人味表达策略的公开边界。"""

from __future__ import annotations

from datetime import UTC, datetime

from bridges.chat.global_writing_policy import (
    SAFE_BASELINE_POLICY_VERSION,
    GlobalWritingPolicyCompiler,
    GlobalWritingPolicyResource,
    restore_protected_regions,
)
from bridges.chat.turn import assemble_payload
from bridges.contracts.chat import ChatMode
from bridges.contracts.profiles import ProfileSensitivityClass, ProfileSliceItem


def _profile_item(value: str, dimension: str = "expression_habit") -> ProfileSliceItem:
    return ProfileSliceItem(
        assertion_id="assertion-alice-1",
        dimension=dimension,
        value_or_rule=value,
        inclusion_reason="与当前问题相关",
        sensitivity_class=ProfileSensitivityClass.PREFERENCE,
        expires_at=datetime(2030, 1, 1, tzinfo=UTC),
    )


def test_compile_policy_uses_mode_and_only_authorized_profile_slice() -> None:
    compiler = GlobalWritingPolicyCompiler()

    companion = compiler.compile(
        ChatMode.COMPANION,
        profile_slice_id="slice-alice-1",
        profile_items=[
            _profile_item("回答喜欢简短直接", dimension="expression_habit")
        ],
    )
    study = compiler.compile(
        ChatMode.STUDY,
        profile_slice_id="slice-alice-1",
        profile_items=[
            _profile_item("回答喜欢简短直接", dimension="expression_habit")
        ],
    )

    assert companion.version != SAFE_BASELINE_POLICY_VERSION
    assert companion.mode == ChatMode.COMPANION.value
    assert study.mode == ChatMode.STUDY.value
    assert companion.profile_slice_id == "slice-alice-1"
    assert companion.profile_items == ("回答喜欢简短直接",)
    # Issue 07：轻量策略按模式编译，不再注入模式人格形容词。
    assert "对话模式：companion" in companion.system_block
    assert "对话模式：study" in study.system_block
    assert "可靠且有分寸的朋友" not in companion.system_block
    assert "因材施教的老师" not in study.system_block
    assert "assertion-alice-1" not in companion.system_block


def test_compile_policy_falls_back_to_safe_baseline_when_resource_is_missing() -> None:
    compiler = GlobalWritingPolicyCompiler(resource=None)

    snapshot = compiler.compile(
        ChatMode.STUDY,
        profile_slice_id="slice-alice-1",
        profile_items=[_profile_item("这条画像不应进入安全基线")],
    )

    assert snapshot.version == SAFE_BASELINE_POLICY_VERSION
    assert snapshot.fallback_reason == "policy_resource_unavailable"
    assert snapshot.profile_items == ()
    assert "代码、公式、JSON、引用、链接" in snapshot.system_block


def test_compile_policy_keeps_a_bound_snapshot_across_resource_updates() -> None:
    first = GlobalWritingPolicyCompiler(
        GlobalWritingPolicyResource(version="global-humanized-writing-v1")
    ).compile(ChatMode.COMPANION)
    updated = GlobalWritingPolicyCompiler(
        GlobalWritingPolicyResource(version="global-humanized-writing-v2")
    ).compile(ChatMode.COMPANION, existing_snapshot=first)

    assert updated.version == first.version
    assert "global-humanized-writing-v1" in updated.system_block


def test_restore_protected_regions_prefers_original_contract_fragments() -> None:
    original = (
        "请保留 `result = 42`，公式 $E=mc^2$，链接 https://example.com/a?q=1，"
        "以及 JSON {\"answer\": 42}。"
    )
    candidate = (
        "请保留 `result = 0`，公式 $E=mc^2$，链接 https://example.com/b，"
        "以及 JSON {\"answer\": 0}。"
    )

    restored = restore_protected_regions(original, candidate)

    assert "`result = 42`" in restored
    assert "$E=mc^2$" in restored
    assert "https://example.com/a?q=1" in restored
    assert '{"answer": 42}' in restored


def test_restore_protected_regions_can_bind_search_result_urls() -> None:
    restored = restore_protected_regions(
        "",
        "答案见 https://example.com/changed",
        additional_sources=("https://example.com/source",),
    )

    assert "https://example.com/source" in restored


def test_assemble_payload_places_policy_in_the_existing_generation_call() -> None:
    snapshot = GlobalWritingPolicyCompiler().compile(ChatMode.COMPANION)

    payload = assemble_payload(
        [{"role": "system", "content": "基础合同"}, {"role": "user", "content": "你好"}],
        writing_policy=snapshot,
    )

    assert len(payload["messages"]) == 3
    assert payload["messages"][1]["content"] == snapshot.system_block
    assert payload["global_writing_policy"]["version"] == snapshot.version


def test_assemble_payload_includes_only_deidentified_correction_result() -> None:
    payload = assemble_payload(
        [{"role": "user", "content": "把我的关注点换成 CNN"}],
        profile_correction_context=(
            "【本轮画像纠正结果】维度：感兴趣的知识；状态：written。"
            "仅根据当前画像切片回答，不要补造内部字段。"
        ),
    )

    correction_message = next(
        message for message in payload["messages"] if "本轮画像纠正结果" in message["content"]
    )
    assert correction_message["role"] == "system"
    assert "record_id" not in correction_message["content"]
    assert "CNN" not in correction_message["content"]
