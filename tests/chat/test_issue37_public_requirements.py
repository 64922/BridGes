"""工单 37：论文身份与岗位公开需求进入 GitHub/资料的接线（私人正文不外发）。"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from bridges.chat.graph import (
    _composite_github_requirement,
    _node_invoke_subgraph_or_chat,
)
from bridges.chat.repository import ConversationRepository
from bridges.chat.run_budget_ledger import (
    RunBudgetClass,
    RunBudgetLedgerRepository,
    derive_run_budget_plan,
)
from bridges.contracts.modules import ModuleDelivery
from bridges.contracts.understanding import MainUnderstanding
from bridges.orchestration.contracts import StepResult, StepState
from bridges.paper.contracts import PaperIdentity
from bridges.storage.database import BridgesDatabase
from tests.orchestration.issue37_chains import seed_career

ACCOUNT = "acc-37-public"
CONVERSATION = "conv-37-public"
PRIVATE = "我的简历：曾在一家小公司实习，联系方式 13800000000"


def _paper_step(selected: list[dict], trust_state: str = "qualified") -> StepResult:
    delivery = ModuleDelivery(
        module_id="paper",
        status="success",
        projection_field="paper_search",
        projection={"selected": selected},
        content="",
        message_status="done",
        artifact_refs={"paper.verify": "art-paper"},
    )
    return StepResult(
        step_id="paper",
        module_id="paper",
        state=StepState.COMPLETED,
        trust_state=trust_state,
        delivery=delivery,
        artifact_refs={"paper.verify": "art-paper"},
    )


def _identity(order: int, title: str, arxiv_id: str) -> dict:
    return PaperIdentity(
        order=order,
        arxiv_id=arxiv_id,
        title=title,
        abs_url=f"https://arxiv.org/abs/{arxiv_id}",
        content_hash=f"hash-{arxiv_id}",
    ).model_dump(mode="json")


def _career_step(projection: dict) -> StepResult:
    delivery = ModuleDelivery(
        module_id="career",
        status="success",
        projection_field="career_plan",
        projection=projection,
        content="",
        message_status="done",
        artifact_refs={"career.verify": "art-career"},
    )
    return StepResult(
        step_id="career",
        module_id="career",
        state=StepState.COMPLETED,
        trust_state="qualified",
        delivery=delivery,
        artifact_refs={"career.verify": "art-career"},
    )


def _context(upstream: dict) -> SimpleNamespace:
    return SimpleNamespace(upstream=upstream)


def test_single_selected_identity_confirms_paper_requirement() -> None:
    paper = _paper_step([_identity(2, "选定论文", "2402.00002")])
    requirement = _composite_github_requirement(
        _context({"paper": paper}), "第二篇论文有没有对应代码"
    )
    assert requirement is not None
    assert requirement.identity_confirmed is True
    assert requirement.identifier == "2402.00002"
    assert "选定论文" in requirement.identity_note


def test_unqualified_paper_does_not_confirm_identity() -> None:
    paper = _paper_step(
        [_identity(1, "候选论文", "2401.00001")], trust_state="draft"
    )
    requirement = _composite_github_requirement(
        _context({"paper": paper}), "这篇论文有没有对应代码"
    )
    assert requirement is not None
    assert requirement.identity_confirmed is False
    assert "不宣称" in requirement.identity_note


def test_ambiguous_selection_requires_ordinal() -> None:
    paper = _paper_step(
        [_identity(1, "论文甲", "2401.00001"), _identity(2, "论文乙", "2402.00002")]
    )
    ambiguous = _composite_github_requirement(
        _context({"paper": paper}), "这两篇论文有没有对应代码"
    )
    assert ambiguous.identity_confirmed is False
    ordinal = _composite_github_requirement(
        _context({"paper": paper}), "第二篇论文有没有对应代码"
    )
    assert ordinal.identity_confirmed is True
    assert ordinal.identifier == "2402.00002"


def test_career_public_requirements_exclude_private_background() -> None:
    projection = {
        "topic": "Java 后端开发",
        "original_request": PRIVATE,
        "background": {"items": [{"text": PRIVATE}]},
        "combination_requirements": [
            {
                "kind": "github",
                "topic": "Java 后端开发",
                "goal": "找练手项目",
                "skills": ["Java", "Spring Boot"],
                "basis": ["负责 Java 后端开发"],
                "inference": True,
            }
        ],
    }
    requirement = _composite_github_requirement(
        _context({"career": _career_step(projection)}), "找 Java 后端项目"
    )
    assert requirement is not None
    assert requirement.identity_confirmed is True
    assert "Java" in requirement.phrase
    assert PRIVATE not in requirement.phrase
    assert PRIVATE not in (requirement.identity_note or "")


def _seed_graph_run(
    database: BridgesDatabase, *, run_id: str, assistant_id: str, user_id: str
) -> None:
    import json

    stamp = datetime.now(UTC).isoformat()
    lease = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    understanding = MainUnderstanding(
        user_message_id=user_id,
        mode="companion",
        reason="测试",
        actual_module_id=None,
        route_source="body_intent",
        # 真实主理解的检测顺序（github 在 resources/career 之前）：
        # 复合计划必须自行按依赖排序，而不是拒绝自然顺序。
        capability_list=["github", "resources", "career"],
        goal=f"Java 后端实习 {PRIVATE}",
    )
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
            (user_id, CONVERSATION, ACCOUNT, understanding.goal, stamp, stamp),
        )
        database.connection.execute(
            "INSERT OR IGNORE INTO messages"
            "(message_id, conversation_id, account_id, role, status, content,"
            " created_at, updated_at)"
            " VALUES (?, ?, ?, 'assistant', 'streaming', '', ?, ?)",
            (assistant_id, CONVERSATION, ACCOUNT, stamp, stamp),
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
                user_id,
                assistant_id,
                lease,
                json.dumps({"understanding": understanding.model_dump(mode="json")}),
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


def test_java_composite_keeps_private_resume_local(tmp_path: Path) -> None:
    database = BridgesDatabase(tmp_path / "bridges.db")
    assert database.initialize() > 0
    run_id = "run-public-1"
    assistant_id = "msg-assistant-public"
    user_id = "msg-user-public"
    _seed_graph_run(
        database, run_id=run_id, assistant_id=assistant_id, user_id=user_id
    )
    _, career = seed_career(
        database, run_id=run_id, account_id=ACCOUNT, conversation_id=CONVERSATION
    )
    github_delivery = ModuleDelivery(
        module_id="github",
        status="success",
        projection_field="github_projects",
        projection={"status": "success", "recommendations": []},
        content="",
        message_status="done",
        artifact_refs={"github.present": "art-github"},
    )
    repo = ConversationRepository(database)
    run = repo.get_generation_run(ACCOUNT, run_id)
    assert run is not None
    service = MagicMock()
    service.module_task_context.return_value = None
    service.run_model_quota.return_value = None
    service.career_plan_service.run.return_value = SimpleNamespace(
        delivery=career, wait_reason=None
    )
    service.learning_resources_service.run.return_value = SimpleNamespace(
        delivery=ModuleDelivery(
            module_id="resources",
            status="success",
            projection_field="learning_resources",
            projection={"status": "success", "items": []},
            content="",
            message_status="done",
            artifact_refs={"resources.verify": "art-resources"},
        ),
        wait_reason=None,
    )
    service.github_projects_service.run.return_value = SimpleNamespace(
        delivery=github_delivery, wait_reason=None
    )
    deps = SimpleNamespace(
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
    state = {"module_dispatch": "chat", "assistant_message_id": assistant_id}
    _node_invoke_subgraph_or_chat(state, {"configurable": {"deps": deps}})

    resources_kwargs = (
        service.learning_resources_service.run.call_args.kwargs
    )
    assert PRIVATE not in (resources_kwargs.get("request_text") or "")
    assert "Java" in (resources_kwargs.get("request_text") or "")
    github_kwargs = service.github_projects_service.run.call_args.kwargs
    requirement = github_kwargs.get("requirement")
    assert requirement is not None
    assert PRIVATE not in requirement.phrase
    assert "Java" in requirement.phrase
    assert requirement.identity_confirmed is True


def test_natural_understanding_order_is_accepted_by_planner() -> None:
    from bridges.chat.repository import MessageRecord
    from bridges.chat.understanding import MainAgentUnderstanding
    from bridges.contracts.chat import (
        ChatMessageRole,
        ChatMessageStatus,
        ChatMode,
    )
    from bridges.orchestration.planner import CompositePlanner

    text = "帮我找 Java 后端实习岗位，还要学习资料和练手项目"
    moment = datetime.now(UTC)
    message = MessageRecord(
        message_id="msg-natural",
        conversation_id="conv-natural",
        account_id=ACCOUNT,
        role=ChatMessageRole.USER,
        attempt_number=1,
        status=ChatMessageStatus.DONE,
        content=text,
        thinking=None,
        error_code=None,
        error_message=None,
        duration_ms=None,
        model_id=None,
        run_lock_id=None,
        created_at=moment,
        updated_at=moment,
    )
    understanding = MainAgentUnderstanding().understand(
        conversation_id="conv-natural",
        user_message_id="msg-natural",
        content=text,
        mode=ChatMode.COMPANION,
        messages=[message],
    )
    assert understanding.capability_list == ["resources", "career"]
    plan = CompositePlanner().plan(
        goal=text,
        user_message_id="msg-natural",
        module_ids=understanding.capability_list,
    )
    assert plan is not None
    assert [step.step_id for step in plan.steps] == ["career", "resources"]


def test_draft_career_requirements_are_not_consumed() -> None:
    projection = {
        "topic": "Java 后端开发",
        "combination_requirements": [
            {
                "kind": "github",
                "topic": "Java 后端开发",
                "goal": "找练手项目",
                "skills": ["Java"],
                "basis": ["负责 Java 后端开发"],
                "inference": True,
            }
        ],
    }
    draft = _career_step(projection).model_copy(update={"trust_state": "draft"})
    requirement = _composite_github_requirement(
        _context({"career": draft}), "找 Java 后端项目"
    )
    assert requirement is None
