"""工单 07 回归：回顾任务的既往经历进入真实聊天载荷。

过期条目在回顾用途被采用后，发送前的「采用快照失效核对」（工单 18）
曾把整张切片判为失效，导致历史永远不会到达模型；这里锁定修复后的
端到端行为，并用非回顾用途与工单 09 的敏感用途门对照。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from tests.chat.test_improvement19_purpose_aware_slice import ACCOUNT, _Env

EXAM_TEXT = "我下周有英语六级考试"
REVIEW_QUERY = "帮我复盘一下英语六级考试的经历。"


def _env_with_expired_exam(tmp_path: Path) -> _Env:
    env = _Env(tmp_path)
    env.atomic.remember(
        ACCOUNT, EXAM_TEXT, source_at=datetime.now(UTC) - timedelta(days=30)
    )
    return env


def test_review_turn_payload_injects_history(tmp_path: Path) -> None:
    env = _env_with_expired_exam(tmp_path)

    env.ask(REVIEW_QUERY)

    blocks = env.slice_blocks()
    assert any("既往经历（回顾用途）" in block for block in blocks), blocks


def test_plan_turn_does_not_read_expired_history(tmp_path: Path) -> None:
    env = _env_with_expired_exam(tmp_path)

    env.ask("帮我制定新的六级备考计划。")

    assert not any("既往经历（回顾用途）" in block for block in env.slice_blocks())


def test_review_does_not_bypass_sensitive_purpose_gate(tmp_path: Path) -> None:
    """工单 09 的用途门同样约束回顾用途：敏感经历没有匹配用途不注入。"""

    env = _Env(tmp_path)
    text = "我下周有过敏复查"
    env.atomic.remember(
        ACCOUNT,
        text,
        source_at=datetime.now(UTC) - timedelta(days=30),
        usage_purpose="健康提醒",
    )

    env.ask("帮我复盘一下过敏复查的经历。")

    assert not any(text in block for block in env.slice_blocks())

    env.ask("帮我复盘一下过敏复查的健康管理经历。")

    blocks = env.slice_blocks()
    assert any("既往经历（回顾用途）" in block and text in block for block in blocks)
