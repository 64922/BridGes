"""工单 38：公开回合结果投影（读取推导、复合精确持久化与迁移）。

只验证用户可见的交付面：实际能力、已交付/被阻塞结果块、可信状态、
恢复方式与等待语义；不触碰证据原文与内部步骤。
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from bridges.ai import ModelGateway
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.chat.repository import ConversationRepository, MessageRecord
from bridges.chat.service import ChatService
from bridges.chat.turn_result import (
    TURN_RESULT_VERSION,
    derive_turn_result,
    recovery_for_error,
)
from bridges.contracts.chat import (
    ChatMessageRole,
    ChatMessageStatus,
    ResultTrust,
    TurnOutcome,
)
from bridges.orchestration.contracts import (
    CompositeOutcome,
    CompositePlan,
    CompositeStatus,
    CompositeStep,
    StepFailure,
    StepResult,
    StepState,
)
from bridges.orchestration.production import turn_result_for_outcome
from bridges.storage.database import MIGRATIONS, SCHEMA_VERSION, BridgesDatabase

ACCOUNT = "acc-38"
CONVERSATION = "conv-38"


def _moment() -> datetime:
    return datetime.now(UTC)


def _plan(*module_ids: str) -> CompositePlan:
    return CompositePlan(
        plan_id="plan-38",
        goal="找资料并核验",
        user_message_id="msg-user-38",
        steps=[
            CompositeStep(
                step_id=module_id,
                module_id=module_id,
                recipe_id="recipe",
                recipe_version="1",
                capability=module_id,
                capability_version="1",
                purpose="完成子目标",
            )
            for module_id in module_ids
        ],
        created_at=_moment(),
    )


def _step(
    module_id: str,
    state: StepState,
    *,
    trust: str = "qualified",
    failure: StepFailure | None = None,
    blocked_reason: str | None = None,
) -> StepResult:
    return StepResult(
        step_id=module_id,
        module_id=module_id,
        state=state,
        trust_state=trust,
        summary=f"{module_id} 结论",
        failure=failure,
        blocked_reason=blocked_reason,
    )


def _outcome(
    status: CompositeStatus, *steps: StepResult, blocked: list[str] | None = None
) -> CompositeOutcome:
    return CompositeOutcome(
        status=status,
        plan=_plan(*[step.module_id for step in steps]),
        steps=list(steps),
        blocked_conclusions=blocked or [],
        created_at=_moment(),
    )


def _assistant_message(
    *,
    status: ChatMessageStatus = ChatMessageStatus.DONE,
    error_code: str | None = None,
    route: dict | None = None,
    turn_result: dict | None = None,
    paper_search: dict | None = None,
    learning_resources: dict | None = None,
    github_projects: dict | None = None,
    web_search: dict | None = None,
) -> MessageRecord:
    now = _moment()
    return MessageRecord(
        message_id="msg-assistant-38",
        conversation_id=CONVERSATION,
        account_id=ACCOUNT,
        role=ChatMessageRole.ASSISTANT,
        attempt_number=1,
        status=status,
        content="回答",
        thinking=None,
        error_code=error_code,
        error_message="失败" if error_code else None,
        duration_ms=10,
        model_id=None,
        run_lock_id=None,
        created_at=now,
        updated_at=now,
        route=route,
        turn_result=turn_result,
        paper_search=paper_search,
        learning_resources=learning_resources,
        github_projects=github_projects,
        web_search=web_search,
    )


def _bare_service(database: BridgesDatabase) -> ChatService:
    return ChatService(
        repository=ConversationRepository(database),
        gateway=ModelGateway(CapabilityRegistry()),
    )


# ---------------------------------------------------------------------------
# 读取推导：单模块 / 轻量聊天 / 学习路径与历史消息
# ---------------------------------------------------------------------------


def test_derive_success_is_complete_with_qualified_delivery() -> None:
    result = derive_turn_result(
        status=ChatMessageStatus.DONE,
        route={
            "status": "matched",
            "module_id": "paper",
            "capability_list": ["paper"],
            "route_source": "explicit_module",
        },
        projections={"paper_search": {"status": "success"}},
    )
    assert result.version == TURN_RESULT_VERSION
    assert result.outcome is TurnOutcome.COMPLETE
    assert result.trust is ResultTrust.QUALIFIED
    assert [block.module_id for block in result.delivered] == ["paper"]
    assert result.blocked == [] and result.recovery is None
    assert result.actual_module_id == "paper"
    assert result.route_source == "explicit_module"


def test_derive_partial_states_are_evidence_bound() -> None:
    result = derive_turn_result(
        status=ChatMessageStatus.DONE,
        route={"status": "matched", "capability_list": ["paper", "resources"]},
        projections={
            "paper_search": {"status": "success"},
            "learning_resources": {"status": "links_only"},
        },
    )
    assert result.outcome is TurnOutcome.PARTIAL
    assert result.trust is ResultTrust.EVIDENCE_BOUND
    assert {block.module_id for block in result.delivered} == {"paper", "resources"}
    # 多能力且无单模块实际值时不虚构唯一实际能力。
    assert result.actual_module_id is None


def test_derive_unrecoverable_failures_are_blocked() -> None:
    result = derive_turn_result(
        status=ChatMessageStatus.DONE,
        route={"status": "matched", "capability_list": ["github"]},
        projections={
            "github_projects": {"status": "error", "error_message": "接口限流"},
        },
    )
    assert result.outcome is TurnOutcome.BLOCKED
    assert result.delivered == []
    assert [block.detail for block in result.blocked] == ["接口限流"]
    assert "接口限流" in result.gaps


def test_derive_clarification_is_needs_input_with_wait_reason() -> None:
    result = derive_turn_result(
        status=ChatMessageStatus.DONE,
        route={"status": "clarify", "capability_list": ["commute"]},
        projections={"commute_route": {"status": "clarification"}},
        wait_reason="等待用户补充起终点",
    )
    assert result.outcome is TurnOutcome.NEEDS_INPUT
    assert result.wait_reason == "等待用户补充起终点"
    assert result.recovery is None


def test_derive_error_recovery_uses_real_cooldown_only() -> None:
    cooldown = _moment() + timedelta(minutes=30)
    result = derive_turn_result(
        status=ChatMessageStatus.ERROR,
        error_code="web_search_rate_limit",
        web_search={"cooldown_until": cooldown.isoformat()},
    )
    assert result.outcome is TurnOutcome.FAILED
    assert result.recovery is not None
    assert result.recovery.action == "wait"
    assert result.recovery.retryable is True
    assert result.recovery.available_after == cooldown
    # 没有真实时刻不编造等待时间。
    no_time = recovery_for_error("web_search_rate_limit")
    assert no_time is not None and no_time.available_after is None


def test_derive_stopped_is_cancelled_without_recovery() -> None:
    result = derive_turn_result(
        status=ChatMessageStatus.STOPPED, error_code=None, projections={}
    )
    assert result.outcome is TurnOutcome.CANCELLED
    assert result.recovery is None
    assert result.delivered == [] and result.blocked == []


# ---------------------------------------------------------------------------
# 复合运行：提交事务内的精确结果
# ---------------------------------------------------------------------------


def test_composite_result_keeps_only_gate_qualified_deliveries() -> None:
    result = turn_result_for_outcome(
        _outcome(
            CompositeStatus.PARTIAL,
            _step("paper", StepState.COMPLETED),
            _step("resources", StepState.COMPLETED, trust="draft"),
            _step(
                "github",
                StepState.FAILED,
                failure=StepFailure(
                    code="github_rate_limited", message="GitHub 接口限流",
                    retryable=True,
                ),
            ),
        ),
        task_id="task-38",
        task_version=3,
    )
    assert result.outcome is TurnOutcome.PARTIAL
    assert result.trust is ResultTrust.EVIDENCE_BOUND
    assert [block.module_id for block in result.delivered] == ["paper"]
    assert {block.module_id for block in result.blocked} == {"resources", "github"}
    assert result.task_id == "task-38" and result.task_version == 3
    assert any("GitHub 接口限流" in gap for gap in result.gaps)


def test_composite_stopped_is_cancelled_without_recovery() -> None:
    result = turn_result_for_outcome(
        _outcome(CompositeStatus.STOPPED, _step("paper", StepState.PENDING))
    )
    assert result.outcome is TurnOutcome.CANCELLED
    assert result.recovery is None


# ---------------------------------------------------------------------------
# 消息投影：复合持久值优先，历史行确定性推导
# ---------------------------------------------------------------------------


def test_projection_prefers_persisted_result_and_fills_route(tmp_path: Path) -> None:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    service = _bare_service(database)
    persisted = turn_result_for_outcome(
        _outcome(
            CompositeStatus.PARTIAL,
            _step("paper", StepState.COMPLETED),
            _step("github", StepState.FAILED,
                  failure=StepFailure(code="github_rate_limited", message="限流")),
        )
    ).model_dump(mode="json")
    message = _assistant_message(
        route={
            "requested_module_id": "github",
            "capability_list": ["paper", "github"],
        },
        turn_result=persisted,
    )
    projection = service._turn_result_projection(message, None)
    assert projection is not None
    assert projection.outcome is TurnOutcome.PARTIAL
    assert {block.module_id for block in projection.blocked} == {"github"}
    assert projection.requested_module_id == "github"
    assert projection.capability_list == ["paper", "github"]


def test_projection_derives_legacy_row_with_run_wait_reason(tmp_path: Path) -> None:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    service = _bare_service(database)
    message = _assistant_message(
        paper_search={"status": "success"},
        route={"status": "matched", "module_id": "paper"},
    )
    projection = service._turn_result_projection(
        message, SimpleNamespace(wait_reason=None)
    )
    assert projection is not None and projection.outcome is TurnOutcome.COMPLETE
    waiting = service._turn_result_projection(
        _assistant_message(), SimpleNamespace(wait_reason="等用户确认后继续")
    )
    assert waiting is not None and waiting.outcome is TurnOutcome.NEEDS_INPUT


def test_projection_ignores_corrupted_persisted_payload(tmp_path: Path) -> None:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    service = _bare_service(database)
    message = _assistant_message(
        turn_result={"version": TURN_RESULT_VERSION, "outcome": "不存在的分类"},
        paper_search={"status": "success"},
    )
    projection = service._turn_result_projection(message, None)
    assert projection is not None and projection.outcome is TurnOutcome.COMPLETE


# ---------------------------------------------------------------------------
# 迁移与持久化：终态同事务提交、历史行零迁移可读
# ---------------------------------------------------------------------------


def test_migration_69_adds_turn_result_column_and_keeps_rows(tmp_path: Path) -> None:
    path = tmp_path / "bridges.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        for version in range(1, 69):
            for statement in MIGRATIONS[version]:
                connection.execute(statement)
        connection.execute(
            "INSERT INTO conversations(conversation_id, account_id, title, mode,"
            " created_at, updated_at) VALUES (?, ?, '旧会话', 'companion', ?, ?)",
            (CONVERSATION, ACCOUNT, "2026-08-01T00:00:00Z", "2026-08-01T00:00:00Z"),
        )
        connection.execute(
            "INSERT INTO messages(message_id, conversation_id, account_id, role,"
            " status, attempt_number, content, created_at, updated_at)"
            " VALUES ('legacy-msg', ?, ?, 'assistant', 'done', 1, '旧回答', ?, ?)",
            (CONVERSATION, ACCOUNT, "2026-08-01T00:00:00Z", "2026-08-01T00:00:00Z"),
        )
        connection.execute(
            "INSERT INTO schema_meta(key, value) VALUES ('version', '68')"
        )
        connection.commit()

    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION
    assert SCHEMA_VERSION >= 69
    with sqlite3.connect(path) as connection:
        columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(messages)")
        }
        legacy = connection.execute(
            "SELECT content, turn_result FROM messages WHERE message_id = 'legacy-msg'"
        ).fetchone()
    assert "turn_result" in columns
    # 历史行内容保留、结果列为空；读取推导无需回填。
    assert legacy == ("旧回答", None)
    database.close()


def test_finalize_message_persists_turn_result_for_conversation_read(
    tmp_path: Path,
) -> None:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    repository = ConversationRepository(database)
    repository.create_conversation(
        account_id=ACCOUNT,
        conversation_id=CONVERSATION,
        title="结果投影",
        mode="companion",
        created_at=_moment(),
    )
    message = _assistant_message(status=ChatMessageStatus.STREAMING)
    repository.insert_message(message)
    exact = turn_result_for_outcome(
        _outcome(
            CompositeStatus.PARTIAL,
            _step("paper", StepState.COMPLETED),
            _step("github", StepState.FAILED,
                  failure=StepFailure(code="github_rate_limited", message="限流")),
        )
    ).model_dump(mode="json")
    assert repository.finalize_message(
        ACCOUNT,
        message.message_id,
        status=ChatMessageStatus.DONE,
        error_code=None,
        error_message=None,
        duration_ms=12,
        model_id=None,
        updated_at=_moment(),
        final_content="最终答案",
        turn_result=exact,
    ) == 1

    stored = repository.get_message(ACCOUNT, message.message_id)
    assert stored is not None and stored.turn_result == exact
    read = _bare_service(database).get_conversation(ACCOUNT, CONVERSATION)
    assert read is not None
    viewed = next(m for m in read.messages if m.message_id == message.message_id)
    assert viewed.turn_result is not None
    assert viewed.turn_result.outcome is TurnOutcome.PARTIAL
    assert [block.module_id for block in viewed.turn_result.blocked] == ["github"]
    database.close()
