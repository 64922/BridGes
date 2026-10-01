"""Issue 07：长期画像使用开关与「停止记录后忘掉」探针在真实聊天回合的行为。

- 关闭使用：模型输入不再包含画像切片，当前用户原文照常参与回答；
  重新开启后未删除的有效信息恢复可用。
- 记住跑步 → 不要记录 → 忘掉跑步：探针接入正式聊天路径，删除在生成前
  真实生效，后续模型输入不含该条目。
"""

from __future__ import annotations

import pytest
from test_v2_08_profile_immediate_effect import (
    _MEMORY_MARKER,
    _SLICE_MARKER,
    ACCOUNT,
    GOAL_ITEM,
    GOAL_MESSAGE,
    _Env,
)

_QUESTION = "帮我安排雅思考试的复习计划"


@pytest.fixture
def env(tmp_path) -> _Env:
    return _Env(tmp_path)


def test_usage_switch_off_stops_profile_injection_but_keeps_original_text(
    env: _Env,
) -> None:
    """使用关闭：切片不注入，但当前用户原文与任务材料照常进入模型输入。"""
    _, seeded = env.start(GOAL_MESSAGE)
    env.run(seeded)
    assert env.items() == [GOAL_ITEM]

    _, before = env.start(_QUESTION)
    env.run(before)
    assert len(env.system_blocks(_SLICE_MARKER)) == 1, "前置：使用开启时切片正常注入"

    env.automatic.set_account_controls(ACCOUNT, usage_enabled=False)

    _, after = env.start(_QUESTION)
    final = env.run(after)

    assert env.system_blocks(_SLICE_MARKER) == [], "关闭使用后不得注入画像正文"
    assert env.items() == [GOAL_ITEM], "关闭使用不删除信息"
    payload = env.payload()
    user_texts = [
        str(message.get("content"))
        for message in payload["messages"]
        if message.get("role") == "user"
    ]
    assert any(_QUESTION in text for text in user_texts), "当前用户原文仍参与回答"
    note = final.context_note
    assert note is not None
    assert note.state.value == "off"
    assert note.profile_enabled is False
    assert "长期画像使用已关闭" in note.note


def test_usage_switch_reenable_recovers_undeleted_information(env: _Env) -> None:
    """重新开启使用后，未删除且有效的信息恢复进入切片。"""
    _, seeded = env.start(GOAL_MESSAGE)
    env.run(seeded)
    env.automatic.set_account_controls(ACCOUNT, usage_enabled=False)

    _, during = env.start(_QUESTION)
    env.run(during)
    assert env.system_blocks(_SLICE_MARKER) == []

    env.automatic.set_account_controls(ACCOUNT, usage_enabled=True)
    _, after = env.start(_QUESTION)
    env.run(after)
    blocks = env.system_blocks(_SLICE_MARKER)
    assert len(blocks) == 1
    assert GOAL_ITEM in blocks[0]


def test_stop_recording_then_forget_probe_through_chat(env: _Env) -> None:
    """工单 07 验收探针走正式聊天：停止记录后忘掉仍真实删除并如实反馈。"""
    _, remember = env.start("记住我喜欢跑步")
    env.run(remember)
    assert env.items() == ["我喜欢跑步"]

    _, stop = env.start("不要记录")
    env.run(stop)

    _, forget = env.start("忘掉跑步")
    assert env.items() == [], "忘掉在生成前真实删除，不被停止记录阻止"
    final = env.run(forget)
    memory_blocks = env.system_blocks(_MEMORY_MARKER)
    assert len(memory_blocks) == 1
    assert "已从用户画像中删除匹配的条目" in memory_blocks[0], "如实反馈删除成功"
    note = final.context_note
    assert note is not None
    assert note.profile_item_count == 0

    _, next_turn = env.start("今天聊聊天气")
    env.run(next_turn)
    # 历史原文照常参与续接（R07 保留任务必要前文）；被删的是长期画像：
    # 本轮画像切片块不得再包含该条目。
    slice_blocks = env.system_blocks(_SLICE_MARKER)
    assert all("跑步" not in block for block in slice_blocks)
