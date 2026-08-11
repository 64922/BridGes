"""Issue 02：轻量学习进度的公开行为。"""

import sqlite3
from pathlib import Path

from bridges.learning.progress import LearningProgressService
from bridges.storage.database import SCHEMA_VERSION, BridgesDatabase


def test_schema_adds_only_lightweight_progress_and_preserves_legacy_teaching_tables(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "bridges.db"
    database = BridgesDatabase(database_path)

    assert database.initialize() == SCHEMA_VERSION
    with sqlite3.connect(database_path) as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    assert "learning_progress" in tables
    assert {
        "teaching_plans",
        "teaching_lessons",
        "teaching_quizzes",
        "teaching_attempts",
        "teaching_assessments",
        "teaching_next_actions",
        "teaching_plan_adjustments",
    } <= tables


def test_轻量进度按会话恢复目标和去重后的已覆盖主题(tmp_path: Path) -> None:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    service = LearningProgressService(database)

    first = service.save_learning_progress(
        "alice",
        "conversation-1",
        "message-1",
        "学习卷积神经网络的基础知识",
        "## 核心思想\n## 组件拆解\n## 核心思想",
    )
    second = service.save_learning_progress(
        "alice",
        "conversation-1",
        "message-2",
        "学习卷积神经网络的基础知识",
        "## 实战要点",
    )

    assert first.goal == "学习卷积神经网络的基础知识"
    assert second.source_message_id == "message-2"
    assert second.covered_topics == ["核心思想", "组件拆解", "实战要点"]
    restored = service.get_learning_progress("alice", "conversation-1")
    assert restored == second
    assert service.get_learning_progress("bob", "conversation-1") is None


def test_新目标替换旧目标且不迁移旧主题(tmp_path: Path) -> None:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    service = LearningProgressService(database)

    service.save_learning_progress(
        "alice", "conversation-1", "message-1", "学习 Transformer", "## 注意力"
    )
    replaced = service.save_learning_progress(
        "alice", "conversation-1", "message-2", "学习 Python", "## 变量"
    )

    assert replaced.goal == "学习 Python"
    assert replaced.covered_topics == ["变量"]
