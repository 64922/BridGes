"""工单 37：父图跨轮复用公共材料、条件变化沿输入依赖失效。"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from bridges.chat.graph import (
    _node_invoke_subgraph_or_chat,
    _node_persist_result,
    _node_verify_output,
)
from bridges.chat.repository import ConversationRepository
from bridges.chat.run_budget_ledger import (
    RunBudgetClass,
    RunBudgetLedgerRepository,
    derive_run_budget_plan,
)
from bridges.contracts.modules import ModuleDelivery
from bridges.contracts.understanding import (
    HardCondition,
    HardConditionKind,
    MainUnderstanding,
)
from bridges.storage.database import BridgesDatabase
from tests.orchestration.issue37_chains import seed_career, seed_resources

ACCOUNT = "acc-37-reuse"
CONVERSATION = "conv-37-reuse"


def _seed_run(
    database: BridgesDatabase,
    *,
    run_id: str,
    assistant_message_id: str,
    user_message_id: str,
    understanding: MainUnderstanding,
) -> None:
    stamp = datetime.now(UTC).isoformat()
    lease = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    import json

    with database.transaction():
        database.connection.execute(
            "INSERT OR IGNORE INTO conversations"
            "(conversation_id, account_id, title, mode, created_at, updated_at)"
            " VALUES (?, ?, '', 'companion', ?, ?)",
            (CONVERSATION, ACCOUNT, stamp, stamp),
        )
        database.connection.execute(
            "INSERT OR IGNORE INTO messages"
            "(message_id, conversation_id, account_id, role, status, content,"
            " created_at, updated_at)"
            " VALUES (?, ?, ?, 'user', 'done', ?, ?, ?)",
            (user_message_id, CONVERSATION, ACCOUNT, understanding.goal or "", stamp, stamp),
        )
        database.connection.execute(
            "INSERT OR IGNORE INTO messages"
            "(message_id, conversation_id, account_id, role, status, content,"
            " created_at, updated_at)"
            " VALUES (?, ?, ?, 'assistant', 'streaming', '', ?, ?)",
            (assistant_message_id, CONVERSATION, ACCOUNT, stamp, stamp),
        )
        database.connection.execute(
            "INSERT OR IGNORE INTO generation_runs"
            "(run_id, account_id, conversation_id, user_message_id,"
            " assistant_message_id, status, lease_owner, lease_expires_at,"
            " stop_requested, config_json, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, 'running', 'worker-37', ?, 0, ?, ?, ?)",
            (
                run_id,
                ACCOUNT,
                CONVERSATION,
                user_message_id,
                assistant_message_id,
                lease,
                json.dumps(
                    {"understanding": understanding.model_dump(mode="json")},
                    ensure_ascii=False,
                ),
                stamp,
                stamp,
            ),
        )
    now = datetime.now(UTC)
    RunBudgetLedgerRepository(database).freeze_for_run(
        account_id=ACCOUNT,
        run_id=run_id,
        conversation_id=CONVERSATION,
        plan=derive_run_budget_plan(
            RunBudgetClass.NORMAL, deadline_at=now + timedelta(minutes=5)
        ),
        now=now,
    )


def _understanding(
    *,
    goal: str,
    conditions: list[HardCondition] | None = None,
    capability_list: list[str] | None = None,
    task_relation=None,
) -> MainUnderstanding:
    return MainUnderstanding(
        user_message_id="unused",
        mode="companion",
        reason="测试复合意图",
        actual_module_id=None,
        route_source="body_intent",
        capability_list=(
            capability_list if capability_list is not None else ["career", "resources"]
        ),
        goal=goal,
        hard_conditions=conditions or [],
        task_relation=task_relation,
    )


def _deps(
    database: BridgesDatabase,
    *,
    run_id: str,
    assistant_message_id: str,
    career_delivery,
    resources_delivery=None,
):
    repo = ConversationRepository(database)
    run = repo.get_generation_run(ACCOUNT, run_id)
    assert run is not None
    service = MagicMock()
    service.module_task_context.return_value = None
    service.run_model_quota.return_value = None
    service.career_plan_service.run.return_value = SimpleNamespace(
        delivery=career_delivery, wait_reason=None
    )
    if resources_delivery is not None:
        service.learning_resources_service.run.return_value = SimpleNamespace(
            delivery=resources_delivery, wait_reason=None
        )
    return SimpleNamespace(
        service=service,
        repo=repo,
        run=run,
        stop_event=None,
        emit=MagicMock(),
        emit_node=MagicMock(),
        current_node=None,
        started=time.monotonic(),
        terminal=MagicMock(),
    )


def _turn(deps, *, assistant_message_id: str, state: dict | None = None):
    state = state or {}
    state.update(
        {"module_dispatch": "chat", "assistant_message_id": assistant_message_id}
    )
    config = {"configurable": {"deps": deps}}
    updates = _node_invoke_subgraph_or_chat(state, config)
    state.update(updates)
    state.update(_node_verify_output(state, config))
    assert _node_persist_result(state, config) == {}
    return updates


def test_city_change_reruns_career_and_reuses_resources(tmp_path: Path) -> None:
    database = BridgesDatabase(tmp_path / "bridges.db")
    assert database.initialize() > 0
    # 第一轮：无城市硬条件。
    run1 = "run-reuse-1"
    understanding1 = _understanding(goal="我想找 Java 后端开发")
    _seed_run(
        database,
        run_id=run1,
        assistant_message_id="msg-assistant-1",
        user_message_id="msg-user-1",
        understanding=understanding1,
    )
    _, career1 = seed_career(
        database, run_id=run1, account_id=ACCOUNT, conversation_id=CONVERSATION
    )
    _, resources1 = seed_resources(
        database, run_id=run1, account_id=ACCOUNT, conversation_id=CONVERSATION
    )
    deps1 = _deps(
        database,
        run_id=run1,
        assistant_message_id="msg-assistant-1",
        career_delivery=career1,
        resources_delivery=resources1,
    )
    updates1 = _turn(deps1, assistant_message_id="msg-assistant-1")
    assert updates1["composite_gate"]["passed"] is True
    assert deps1.service.learning_resources_service.run.call_count == 1

    # 第二轮：换成杭州；岗位随条件重算，资料指纹未变应复用。
    run2 = "run-reuse-2"
    understanding2 = _understanding(
        goal="我想找 Java 后端开发",
        conditions=[HardCondition(kind=HardConditionKind.CITY, text="杭州")],
    )
    _seed_run(
        database,
        run_id=run2,
        assistant_message_id="msg-assistant-2",
        user_message_id="msg-user-2",
        understanding=understanding2,
    )
    _, career2 = seed_career(
        database, run_id=run2, account_id=ACCOUNT, conversation_id=CONVERSATION
    )
    deps2 = _deps(
        database,
        run_id=run2,
        assistant_message_id="msg-assistant-2",
        career_delivery=career2,
    )
    updates2 = _turn(deps2, assistant_message_id="msg-assistant-2")

    assert deps2.service.career_plan_service.run.call_count == 1
    assert deps2.service.learning_resources_service.run.call_count == 0
    steps = {step["step_id"]: step for step in updates2["composite_outcome"]["steps"]}
    assert steps["resources"]["reused"] is True
    assert steps["resources"]["trust_state"] == "qualified"
    message = ConversationRepository(database).get_message(ACCOUNT, "msg-assistant-2")
    assert message is not None
    assert "入门资料" in message.content


def test_goal_change_recomputes_public_materials(tmp_path: Path) -> None:
    database = BridgesDatabase(tmp_path / "bridges.db")
    assert database.initialize() > 0
    run1 = "run-goal-1"
    understanding1 = _understanding(goal="我想找 Java 后端开发")
    _seed_run(
        database,
        run_id=run1,
        assistant_message_id="msg-assistant-1",
        user_message_id="msg-user-1",
        understanding=understanding1,
    )
    _, career1 = seed_career(
        database, run_id=run1, account_id=ACCOUNT, conversation_id=CONVERSATION
    )
    _, resources1 = seed_resources(
        database, run_id=run1, account_id=ACCOUNT, conversation_id=CONVERSATION
    )
    deps1 = _deps(
        database,
        run_id=run1,
        assistant_message_id="msg-assistant-1",
        career_delivery=career1,
        resources_delivery=resources1,
    )
    _turn(deps1, assistant_message_id="msg-assistant-1")

    # 目标变了：公开需求不同，资料不得复用旧指纹。
    run2 = "run-goal-2"
    understanding2 = _understanding(goal="我想找前端开发")
    _seed_run(
        database,
        run_id=run2,
        assistant_message_id="msg-assistant-2",
        user_message_id="msg-user-2",
        understanding=understanding2,
    )
    _, career2 = seed_career(
        database,
        run_id=run2,
        account_id=ACCOUNT,
        conversation_id=CONVERSATION,
        topic="前端开发",
        requirements=[
            {
                "kind": "resources",
                "topic": "前端开发",
                "goal": "学习前端开发",
                "skills": ["JavaScript", "React"],
                "basis": ["负责前端开发"],
                "inference": True,
            }
        ],
    )
    _, resources2 = seed_resources(
        database, run_id=run2, account_id=ACCOUNT, conversation_id=CONVERSATION
    )
    deps2 = _deps(
        database,
        run_id=run2,
        assistant_message_id="msg-assistant-2",
        career_delivery=career2,
        resources_delivery=resources2,
    )
    updates2 = _turn(deps2, assistant_message_id="msg-assistant-2")

    assert deps2.service.learning_resources_service.run.call_count == 1
    request_text = (
        deps2.service.learning_resources_service.run.call_args.kwargs["request_text"]
    )
    assert "前端开发" in request_text
    steps = {step["step_id"]: step for step in updates2["composite_outcome"]["steps"]}
    assert steps["resources"]["reused"] is False


def test_revision_without_module_signal_restores_prior_plan(tmp_path: Path) -> None:
    from bridges.contracts.tasks import TaskRelation

    database = BridgesDatabase(tmp_path / "bridges.db")
    assert database.initialize() > 0
    run1 = "run-revise-1"
    _seed_run(
        database,
        run_id=run1,
        assistant_message_id="msg-assistant-revise-1",
        user_message_id="msg-user-revise-1",
        understanding=_understanding(goal="我想找 Java 后端开发"),
    )
    _, career1 = seed_career(
        database, run_id=run1, account_id=ACCOUNT, conversation_id=CONVERSATION
    )
    _, resources1 = seed_resources(
        database, run_id=run1, account_id=ACCOUNT, conversation_id=CONVERSATION
    )
    deps1 = _deps(
        database,
        run_id=run1,
        assistant_message_id="msg-assistant-revise-1",
        career_delivery=career1,
        resources_delivery=resources1,
    )
    _turn(deps1, assistant_message_id="msg-assistant-revise-1")

    # 修订轮只给条件（“换成杭州”），没有任何能力信号；父图应从上一轮
    # 综合产物恢复计划：岗位重算、资料复用。
    run2 = "run-revise-2"
    _seed_run(
        database,
        run_id=run2,
        assistant_message_id="msg-assistant-revise-2",
        user_message_id="msg-user-revise-2",
        understanding=_understanding(
            goal="换成杭州，继续看 Java 后端岗位",
            conditions=[HardCondition(kind=HardConditionKind.CITY, text="杭州")],
            capability_list=[],
            task_relation=TaskRelation.REVISE,
        ),
    )
    _, career2 = seed_career(
        database, run_id=run2, account_id=ACCOUNT, conversation_id=CONVERSATION
    )
    deps2 = _deps(
        database,
        run_id=run2,
        assistant_message_id="msg-assistant-revise-2",
        career_delivery=career2,
    )
    updates2 = _turn(deps2, assistant_message_id="msg-assistant-revise-2")

    assert deps2.service.career_plan_service.run.call_count == 1
    assert deps2.service.learning_resources_service.run.call_count == 0
    steps = {step["step_id"]: step for step in updates2["composite_outcome"]["steps"]}
    assert steps["resources"]["reused"] is True
    assert updates2["composite_gate"]["passed"] is True


def test_career_private_projection_absent_from_state_but_delivered(
    tmp_path: Path,
) -> None:
    import json

    from bridges.career_plan.contracts import CareerPlanProjection
    from bridges.kernel.contracts import ArtifactTrust, NodeArtifact
    from bridges.kernel.repository import NodeKernelRepository

    private = "私人简历正文-绝密：联系方式 13800000000"
    database = BridgesDatabase(tmp_path / "bridges.db")
    assert database.initialize() > 0
    run_id = "run-private-1"
    _seed_run(
        database,
        run_id=run_id,
        assistant_message_id="msg-assistant-private-1",
        user_message_id="msg-user-private-1",
        understanding=_understanding(goal="我想找 Java 后端开发"),
    )
    chain, _ = seed_career(
        database,
        run_id=run_id,
        account_id=ACCOUNT,
        conversation_id=CONVERSATION,
        requirements=[],
    )
    projection = CareerPlanProjection(
        status="success",
        topic="Java 后端开发",
        original_request=private,
        samples=[],
        gaps=[],
        personal_advices=[],
        combination_requirements=[],
        background={
            "checked_at": datetime.now(UTC),
            "items": [{"source": "resume", "text": private, "source_ref": "resume:1"}],
        },
        personal_boundary=["私人背景只在本地参与对照。"],
    ).model_dump(mode="json")
    verify = chain["career.verify"]
    rebuilt = NodeArtifact.build(
        account_id=ACCOUNT,
        conversation_id=CONVERSATION,
        run_id=run_id,
        task_id=None,
        task_version=None,
        recipe_id=verify.recipe_id,
        recipe_version=verify.recipe_version,
        node=verify.node,
        artifact_type=verify.artifact_type,
        capability_version=verify.capability_version,
        trust_state=ArtifactTrust.QUALIFIED,
        input_key="test:career:private-state",
        input_deps=verify.input_deps,
        source_refs=(),
        read_scope=verify.read_scope,
        requirement_coverage=(),
        unconfirmed=("私人背景只在本地参与对照。",),
        error=None,
        payload={"projection": projection},
        now=datetime.now(UTC),
    )
    NodeKernelRepository(database).save_artifact(rebuilt)
    career = ModuleDelivery(
        module_id="career",
        status="success",
        projection_field="career_plan",
        projection=projection,
        content="岗位正文。",
        message_status="done",
        artifact_refs={"career.verify": rebuilt.artifact_id},
    )
    _, resources = seed_resources(
        database, run_id=run_id, account_id=ACCOUNT, conversation_id=CONVERSATION
    )
    deps = _deps(
        database,
        run_id=run_id,
        assistant_message_id="msg-assistant-private-1",
        career_delivery=career,
        resources_delivery=resources,
    )
    updates = _turn(deps, assistant_message_id="msg-assistant-private-1")

    # 检查点载荷：证据与 career 投影都不含私人正文（只留产物引用）。
    state_payload = json.dumps(updates["composite_outcome"], ensure_ascii=False)
    assert private not in state_payload
    assert updates["composite_gate"]["passed"] is True
    # 提交时按引用回填，助手消息仍交付完整 career 投影。
    message = ConversationRepository(database).get_message(
        ACCOUNT, "msg-assistant-private-1"
    )
    assert message is not None
    assert message.career_plan is not None
    assert private in json.dumps(message.career_plan, ensure_ascii=False)


def test_unqualified_career_content_not_in_state(tmp_path: Path) -> None:
    import json

    private = "私人简历正文-绝密：联系方式 13800000000"
    database = BridgesDatabase(tmp_path / "bridges.db")
    assert database.initialize() > 0
    run_id = "run-private-draft"
    _seed_run(
        database,
        run_id=run_id,
        assistant_message_id="msg-assistant-private-draft",
        user_message_id="msg-user-private-draft",
        understanding=_understanding(goal="我想找 Java 后端开发"),
    )
    career = ModuleDelivery(
        module_id="career",
        status="success",
        projection_field="career_plan",
        projection={},
        content=private,
        message_status="done",
        artifact_refs={"career.verify": "art-missing"},
    )
    _, resources = seed_resources(
        database, run_id=run_id, account_id=ACCOUNT, conversation_id=CONVERSATION
    )
    deps = _deps(
        database,
        run_id=run_id,
        assistant_message_id="msg-assistant-private-draft",
        career_delivery=career,
        resources_delivery=resources,
    )
    updates = _turn(deps, assistant_message_id="msg-assistant-private-draft")

    assert private not in json.dumps(updates, ensure_ascii=False)
    message = ConversationRepository(database).get_message(
        ACCOUNT, "msg-assistant-private-draft"
    )
    assert message is not None
    assert private not in (message.content or "")
    assert message.career_plan is None
