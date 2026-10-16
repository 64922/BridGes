"""画像工单 08：聊天回合的「本轮不用」与发送前使用控制核对。

覆盖验收：本轮不用只影响当前回合（不编译、不注入、不删除，下一轮恢复）；
编译之后发送之前关闭长期画像使用、或使用控制发生真实变化（关闭后重开）时，
尚未发送的旧切片按最新控制失效，披露如实降级。
"""

from __future__ import annotations

from pathlib import Path

from test_v2_08_profile_immediate_effect import (
    _SLICE_MARKER,
    ACCOUNT,
    STUDY_TEXT,
    _Env,
)

from bridges.contracts.chat import ContextNoteState

_DIRECTIVE_TURN = "这轮先不用我的画像，帮我安排复习计划"
_RELATED_QUESTION = "我在准备雅思考试，帮我安排复习"
_RESTRICTION_MARKER = "【本轮长期信息使用限制】"


def test_turn_directive_disables_profile_for_this_turn_only(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    env.atomic.remember(ACCOUNT, STUDY_TEXT, source_message_id="m-study")

    _, directive = env.start(_DIRECTIVE_TURN)
    final = env.run(directive)

    assert env.system_blocks(_SLICE_MARKER) == []
    note = final.context_note
    assert note is not None
    assert note.state == ContextNoteState.OFF
    assert "本轮不用" in note.note
    # 信息未被删除；控制语义进入模型合同（停用旧历史的间接个性化）。
    assert env.items() == [STUDY_TEXT]
    assert _RESTRICTION_MARKER in env.payload()["messages"][0]["content"]

    # 只影响当前回合：下一轮同一话题照常注入画像切片。
    _, question = env.start(_RELATED_QUESTION)
    env.run(question)
    blocks = env.system_blocks(_SLICE_MARKER)
    assert len(blocks) == 1
    assert STUDY_TEXT in blocks[0]


def test_usage_disabled_between_compile_and_send_invalidates_slice(
    tmp_path: Path,
) -> None:
    env: _Env

    def disable_usage() -> None:
        env.automatic.set_account_controls(ACCOUNT, usage_enabled=False)

    env = _Env(tmp_path, on_context_note=disable_usage)
    env.atomic.remember(ACCOUNT, STUDY_TEXT, source_message_id="m-study")

    _, assistant = env.start(_RELATED_QUESTION)
    final = env.run(assistant)

    assert env.system_blocks(_SLICE_MARKER) == []
    assert final.context_note is not None
    assert final.context_note.state == ContextNoteState.EMPTY
    assert final.context_note.profile_item_count == 0
    # 关闭使用不删除信息。
    assert env.items() == [STUDY_TEXT]


def test_usage_toggle_between_compile_and_send_invalidates_stale_slice(
    tmp_path: Path,
) -> None:
    """关闭后重开：使用重新开启，但编译时的控制序号已过期，旧切片仍失效。"""

    env: _Env

    def toggle_usage() -> None:
        env.automatic.set_account_controls(ACCOUNT, usage_enabled=False)
        env.automatic.set_account_controls(ACCOUNT, usage_enabled=True)

    env = _Env(tmp_path, on_context_note=toggle_usage)
    env.atomic.remember(ACCOUNT, STUDY_TEXT, source_message_id="m-study")

    _, assistant = env.start(_RELATED_QUESTION)
    final = env.run(assistant)

    assert env.automatic.is_profile_usage_enabled(ACCOUNT), "前置：使用已重新开启"
    assert env.system_blocks(_SLICE_MARKER) == []
    assert final.context_note is not None
    assert final.context_note.profile_item_count == 0
    assert env.items() == [STUDY_TEXT]
