"""验收真实父图派发边界：历史提示、硬条件与恢复时的任务守卫。"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from bridges.career_plan.parsing import parse_career_request
from bridges.career_plan.service import CareerPlanService
from bridges.chat.graph import (
    AVAILABLE_MODULE_IDS,
    DailyTurnError,
    _node_invoke_subgraph_or_chat,
    _node_validate_turn,
)
from bridges.contracts.chat import ChatMessageStatus
from bridges.contracts.understanding import MainUnderstanding

_SERVICES = {
    "paper": "paper_search_service",
    "tieba": "tieba_research_service",
    "github": "github_projects_service",
    "resources": "learning_resources_service",
    "commute": "commute_service",
    "career": "career_plan_service",
}


def _deps(*, mode="companion", route=None, understanding=None, binding=None):
    service = MagicMock()
    service.stream_generation.return_value = iter(())
    for name in _SERVICES.values():
        getattr(service, name).run.return_value = SimpleNamespace(wait_reason=None)
    assistant = SimpleNamespace(
        conversation_id="conversation", status=ChatMessageStatus.STREAMING,
        route=route or {},
    )
    user = SimpleNamespace(conversation_id="conversation", module_id="github")
    repo = MagicMock()
    repo.get_message.side_effect = (
        lambda account, message: assistant if message == "assistant" else user
    )
    repo.get_conversation.return_value = SimpleNamespace(mode=mode)
    config = {}
    if understanding is not None:
        config["understanding"] = MainUnderstanding(
            user_message_id="user", mode=mode, reason="验收测试", **understanding,
        ).model_dump(mode="json")
    if binding is not None:
        config["task_binding"] = binding
    return SimpleNamespace(
        service=service, repo=repo, stop_event=None, emit=MagicMock(), emit_node=MagicMock(),
        run=SimpleNamespace(account_id="account", conversation_id="conversation",
                            user_message_id="user", assistant_message_id="assistant",
                            run_id="run", config=config),
    )


def _invoke(deps, module):
    return _node_invoke_subgraph_or_chat(
        {"module_dispatch": module, "assistant_message_id": "assistant"},
        {"configurable": {"deps": deps}},
    )


def _assert_no_modules(deps):
    for name in _SERVICES.values():
        getattr(deps.service, name).run.assert_not_called()


def test_body_route_dispatch_preserves_different_historical_hint():
    deps = _deps(route={"understanding_version": "main-understanding-v1", "module_id": "paper"})
    state = _node_validate_turn({}, {"configurable": {"deps": deps}})
    assert state["module_id"] == "paper"
    _invoke(deps, state["module_id"])
    deps.service.paper_search_service.run.assert_called_once()
    deps.service.github_projects_service.run.assert_not_called()


def test_legacy_route_still_dispatches_historical_hint():
    deps = _deps(route={"module_id": "paper"})
    state = _node_validate_turn({}, {"configurable": {"deps": deps}})
    assert state["module_id"] == "github"


def test_restored_checkpoint_uses_actual_suggestion_route():
    deps = _deps(route={
        "understanding_version": "main-understanding-v1", "module_id": "paper",
        "route_source": "suggestion_click",
    })
    _invoke(deps, "github")
    deps.service.paper_search_service.run.assert_called_once()
    deps.service.github_projects_service.run.assert_not_called()


def test_new_ordinary_route_does_not_reuse_checkpoint_module():
    deps = _deps(route={
        "understanding_version": "main-understanding-v1", "module_id": None,
        "main_capability": "ordinary_chat",
    })
    _invoke(deps, "github")
    _assert_no_modules(deps)
    deps.service.stream_generation.assert_called_once()


@pytest.mark.parametrize("status", ["clarify", "rejected"])
def test_restored_dispatch_cannot_bypass_route_feedback(status):
    deps = _deps(route={"status": status, "module_id": "paper"})
    _invoke(deps, "paper")
    _assert_no_modules(deps)
    deps.service.stream_generation.assert_called_once()


@pytest.mark.parametrize("relation", ["pause", "cancel"])
def test_task_control_never_starts_module_from_hint(relation):
    deps = _deps(understanding={"task_relation": relation})
    _invoke(deps, "github")
    _assert_no_modules(deps)
    deps.service.stream_generation.assert_called_once()


@pytest.mark.parametrize("module", sorted(AVAILABLE_MODULE_IDS))
@pytest.mark.parametrize("kind", ["no_network", "local_only"])
def test_network_hard_condition_blocks_every_external_module(module, kind):
    deps = _deps(understanding={"hard_conditions": [{"kind": kind, "text": "不要联网"}]})
    with pytest.raises(DailyTurnError, match="不联网") as error:
        _invoke(deps, module)
    assert error.value.code == "network_not_allowed"
    _assert_no_modules(deps)


def test_suggestion_click_cannot_escape_source_restriction():
    deps = _deps(
        route={"module_id": "github", "route_source": "suggestion_click"},
        understanding={"hard_conditions": [{"kind": "source_restriction", "text": "只查论文"}]},
    )
    with pytest.raises(DailyTurnError) as error:
        _invoke(deps, "github")
    assert error.value.code == "source_not_allowed"
    _assert_no_modules(deps)


@pytest.mark.parametrize("module", sorted(AVAILABLE_MODULE_IDS))
def test_learning_mode_cannot_start_daily_module(module):
    deps = _deps(mode="study")
    with pytest.raises(DailyTurnError) as error:
        _invoke(deps, module)
    assert error.value.code == "module_mode_conflict"
    _assert_no_modules(deps)


@pytest.mark.parametrize("conversation,version,status", [
    ("other", 3, "active"), ("conversation", 4, "active"),
    ("conversation", 3, "paused"), ("conversation", 3, "cancelled"),
])
def test_restored_task_binding_checks_conversation_version_and_status(
    conversation, version, status,
):
    deps = _deps(binding={"task_id": "task", "version": 3})
    deps.service._tasks.projection.return_value = SimpleNamespace(task=SimpleNamespace(
        conversation_id=conversation, current_version=version, status=status,
    ))
    with pytest.raises(DailyTurnError) as error:
        _invoke(deps, "paper")
    assert error.value.code == "task_state_conflict"
    deps.service._tasks.projection.assert_called_once_with("account", "task")
    _assert_no_modules(deps)


def test_missing_account_task_binding_blocks_module():
    deps = _deps(binding={"task_id": "other-account-task", "version": 3})
    deps.service._tasks.projection.return_value = None
    with pytest.raises(DailyTurnError) as error:
        _invoke(deps, "paper")
    assert error.value.code == "task_state_conflict"
    _assert_no_modules(deps)


def test_career_stage_answer_dispatches_accumulated_goal():
    goal = "帮我规划职业发展；想做数据分析师；大三"
    deps = _deps(understanding={"answer_fields": ["stage"], "goal": goal})
    _invoke(deps, "career")
    call = deps.service.career_plan_service.run.call_args
    assert call.kwargs["request_text"] == goal


def test_career_service_parses_accumulated_goal_without_changing_message(monkeypatch):
    goal = "帮我规划职业发展；想做数据分析师；大三"
    user = SimpleNamespace(content="大三")
    repo = MagicMock()
    repo.get_message.return_value = user
    repo.list_messages.return_value = []
    service = CareerPlanService(search=MagicMock(), reader=MagicMock())
    captured = []

    class ParsedRequestError(Exception):
        """解析后中止，测试无需执行外部搜索。"""

    def capture(content, **options):
        analysis = parse_career_request(content, **options)
        captured.append(analysis)
        raise ParsedRequestError

    monkeypatch.setattr("bridges.career_plan.service.parse_career_request", capture)
    with pytest.raises(ParsedRequestError):
        service.run(
            repo=repo, account_id="account", conversation_id="conversation",
            user_message_id="user", assistant_message_id="assistant",
            emit_node=MagicMock(), stop_event=None, request_text=goal,
        )
    assert captured[0].job_title == "数据分析师"
    assert captured[0].stage == "大三"
    assert captured[0].clarification is None
    assert user.content == "大三"
    service._search.assert_not_called()
