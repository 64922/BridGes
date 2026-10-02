"""工单 15 验收：模块真正消费有效任务字段，并隔离旧任务锚点。"""

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from bridges.chat.task_materials import EffectiveCondition, ModuleTaskContext
from bridges.commute.kernel import CommuteNodeFlow
from bridges.commute.parsing import parse_commute_request
from bridges.github.parsing import parse_github_request
from bridges.github.service import GithubProjectsService
from bridges.paper.parsing import parse_paper_request
from bridges.resources.contracts import ResourcesLevel
from bridges.resources.parsing import parse_resources_request


def _context(module: str, topic: str = "深度学习", **fields: str) -> ModuleTaskContext:
    return ModuleTaskContext(
        version="module-context-v1", module_id=module, purpose=module,
        task_id="current-task", task_goal=topic, topic_hint=topic,
        effective_conditions=tuple(
            EffectiveCondition(kind, kind, text, "current-source")
            for kind, text in fields.items()
        ),
        excluded_condition_ids=("old-condition",),
        prior_messages=("2020年入门论文，从旧宿舍到旧教室步行",),
        source_message_ids=("current-source",), evidence_scope="当前任务字段",
        used_task_scope=True,
    )


@pytest.mark.parametrize("prompt", ["找几篇论文", "继续解释"])
def test_paper_continuation_adopts_effective_year_and_type(prompt: str) -> None:
    context = _context("paper_search", year="2024年以后", paper_type="综述")
    parsed = parse_paper_request(
        prompt, task_topic_hint=context.topic_hint,
        task_conditions=context.effective_conditions,
    )
    assert parsed.original_phrase == "深度学习"
    assert parsed.constraints.year_from == 2024
    assert parsed.constraints.prefer_survey


def test_github_new_task_does_not_reuse_old_paper_anchor() -> None:
    repo = Mock()
    repo.list_messages.return_value = [
        SimpleNamespace(message_id="old-paper", paper_search={"original_phrase": "旧目标"}),
        SimpleNamespace(message_id="current-source"),
        SimpleNamespace(message_id="current-message"),
    ]
    service = GithubProjectsService(search=Mock(), reader=Mock())
    context = _context("github_projects", topic="校园地图", feature="路线规划", tech="Python")
    anchors = service._prior_context(
        repo, "account", "conversation", "current-message", module_context=context,
    )
    parsed = parse_github_request(
        "找实现它的项目", prior_context=anchors, task_conditions=context.effective_conditions,
    )
    assert parsed.scenario == "校园地图"
    assert "路线规划" in parsed.features
    assert "python" in parsed.tech_terms
    assert all(anchor.message_id != "old-paper" for anchor in anchors)


def test_github_current_task_paper_result_keeps_explicit_anchor_priority() -> None:
    repo = Mock()
    repo.list_messages.return_value = [
        SimpleNamespace(message_id="old-user", role="user"),
        SimpleNamespace(
            message_id="old-paper", role="assistant", paper_search={"original_phrase": "旧目标"}
        ),
        SimpleNamespace(message_id="current-source", role="user"),
        SimpleNamespace(
            message_id="current-paper", role="assistant",
            paper_search={"original_phrase": "当前论文的明确主题"},
        ),
        SimpleNamespace(message_id="current-message", role="user"),
    ]
    service = GithubProjectsService(search=Mock(), reader=Mock())
    anchors = service._prior_context(
        repo, "account", "conversation", "current-message",
        module_context=_context("github_projects"),
    )
    parsed = parse_github_request("找实现它的项目", prior_context=anchors)
    assert parsed.scenario == "当前论文的明确主题"
    assert parsed.context_source is not None
    assert parsed.context_source.message_id == "current-paper"
    assert all(anchor.message_id != "old-paper" for anchor in anchors)


@pytest.mark.parametrize("prompt", ["推荐资料", "继续"])
def test_resources_uses_effective_level_instead_of_old_source_text(prompt: str) -> None:
    context = _context("learning_resources", level="进阶", goal="项目实战")
    parsed = parse_resources_request(
        prompt, prior_context=context.prior_messages, module_context=context,
    )
    assert parsed.original_phrase == "深度学习"
    assert parsed.level == ResourcesLevel.ADVANCED
    assert parsed.goal == "项目实战"
    assert parsed.clarification is None


def test_resources_revoked_level_is_not_backfilled_from_history() -> None:
    context = _context("learning_resources")
    parsed = parse_resources_request(
        "推荐资料", prior_context=context.prior_messages, module_context=context,
    )
    assert parsed.level is None
    assert parsed.clarification is not None
    assert parsed.clarification.missing == "level"


def test_paper_current_year_correction_precedes_task_snapshot() -> None:
    context = _context("paper_search", year="2024年", paper_type="综述")
    parsed = parse_paper_request(
        "找2025年的深度学习论文", task_topic_hint=context.topic_hint,
        task_conditions=context.effective_conditions,
    )
    assert parsed.constraints.year_from == 2025
    assert parsed.constraints.year_to == 2025


def test_resources_current_level_correction_precedes_task_snapshot() -> None:
    parsed = parse_resources_request(
        "找深度学习进阶资料", module_context=_context("learning_resources", level="零基础"),
    )
    assert parsed.level == ResourcesLevel.ADVANCED


def test_commute_current_route_correction_precedes_task_snapshot() -> None:
    parsed = parse_commute_request(
        "从南门步行到体育馆",
        module_context=_context("commute", origin="北门", destination="图书馆", mode="骑行"),
    )
    assert parsed.origin_phrase == "南门"
    assert parsed.destination_phrase == "体育馆"
    assert parsed.mode_phrase == "步行"


def test_commute_adopts_separate_effective_place_and_mode_fields() -> None:
    context = _context("commute", origin="北门", destination="图书馆", mode="骑行")
    parsed = parse_commute_request(
        "继续", prior_context=context.prior_messages, module_context=context,
    )
    assert parsed.origin_phrase == "北门"
    assert parsed.destination_phrase == "图书馆"
    assert parsed.mode_phrase == "骑行"
    assert parsed.clarification is None


def test_commute_digest_changes_when_effective_destination_changes() -> None:
    def clock() -> datetime:
        return datetime(2026, 10, 2, tzinfo=UTC)
    first = CommuteNodeFlow(
        amap=Mock(), clock=clock, module_context=_context("commute", destination="图书馆")
    )
    second = CommuteNodeFlow(
        amap=Mock(), clock=clock, module_context=_context("commute", destination="体育馆")
    )
    assert first.prior_digest != second.prior_digest


@pytest.mark.parametrize("module", ["paper", "github", "resources", "commute"])
def test_module_service_imports_in_fresh_process(module: str) -> None:
    """独立模块启动不依赖 chat 已先导入，防止 pytest 的导入顺序掩盖循环。"""
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2] / "src"))
    result = subprocess.run(
        [sys.executable, "-c", f"import bridges.{module}.service"],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
