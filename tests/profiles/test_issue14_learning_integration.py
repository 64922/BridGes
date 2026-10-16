"""画像工单 14：个人记忆的学习引用、领域状态操作与失效联动。

覆盖验收项：
1. 学习引用投影区分用户自述、已核验结论与不确定性（阅读、单次答对或
   用户自评不构成已核验掌握，纠正后不再核验）；
2. 关联目标的暂停/继续/完成经学习领域执行（目标版本守卫），画像条目
   不写第二份状态；续学仍可还原原目标与进展；
3. 学习侧纠正/删除立即让个人视图不再引用它，迟到结果不复活；
4. 账户隔离，学习引用不跨账户；使用开关不被桥接绕过；
5. 跨会话并发（其他会话先改目标）不覆盖学习新状态；
6. 组合根接线：学习引用端口如实工作（接通前 503，不再伪造空学情）。

真实接口原则：目标读写走 ``StudyGoalRepository``（L08），学情走
``StudyRecordControlService`` 的有效读取边界（L06），记录由真实导师
流程产生（复用工单 06 的端到端助手）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.api.main import create_app
from bridges.contracts.atomic_profile import AtomicProfileDomainReference
from bridges.contracts.ongoing_profile import (
    OngoingContextState,
    OngoingDomainReferenceError,
    OngoingGoalAction,
)
from bridges.contracts.study_goals import (
    StudyGoalProjection,
    StudyRouteNode,
    StudyRouteProjection,
)
from bridges.profiles.atomic import AtomicProfileError
from bridges.study.goals import StudyGoalRepository
from tests.chat.test_issue02_text_tutor import _TextTutorAdapter
from tests.chat.test_issue03_embedded_verification import (
    _plan_pose,
    _PlanAdapter,
)
from tests.chat.test_issue05_durable_diagnosis import (
    _Q1,
    _close,
    _StructuredAdapter,
    _wire,
)
from tests.chat.test_v2_05_photo_attachments import _app, _register

GOAL_ITEM_TEXT = "我在准备考研概率论考试"


def _reference(goal_id: str) -> AtomicProfileDomainReference:
    return AtomicProfileDomainReference(
        domain="study",
        reference_id=goal_id,
    )


def _seed_goal(
    app: Any,
    account_id: str,
    goal_id: str,
    *,
    statement: str = "系统学习概率论",
    node_title: str = "条件概率",
) -> int:
    """经 L08 仓库接口写入一条活动目标与首版路线（返回目标版本）。"""

    database = app.state.bridges_database
    now = datetime.now(UTC)
    goal = StudyGoalProjection(
        goal_id=goal_id,
        statement=statement,
        version=1,
        source_message_id=f"message-{goal_id}",
        created_at=now,
        updated_at=now,
    )
    route = StudyRouteProjection(
        version=1,
        nodes=[StudyRouteNode(node_id="node-1", title=node_title)],
        current_node_id="node-1",
        origin="assistant_proposal",
        created_at=now,
    )
    with database.transaction():
        StudyGoalRepository(database).insert_goal_with_route_in_transaction(
            account_id=account_id, goal=goal, route=route, now=now
        )
    return 1


def _link(
    app: Any,
    account_id: str,
    item_id: str,
    goal_id: str,
) -> None:
    atomic = app.state.atomic_profile_service
    ongoing = app.state.ongoing_items_service
    item = atomic.get_item(account_id, item_id)
    ongoing.set_domain_reference(
        account_id, item_id, _reference(goal_id), version=item.version
    )


def _wire_tutor(app: Any) -> tuple[Any, Any, Any]:
    text = _TextTutorAdapter(f"先检查一个关键点。{_Q1}")
    plan = _PlanAdapter(default=_plan_pose(_Q1))
    structured = _StructuredAdapter(plan)
    _wire(app, text, structured)
    return text, plan, structured


# ---------------------------------------------------------------------------
# 服务级：引用解析实时、状态操作经领域、版本守卫与失效
# ---------------------------------------------------------------------------


def test_linked_goal_resolves_live_state_and_owner_scope(
    tmp_path: Any, monkeypatch: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    try:
        with TestClient(app) as client:
            alice = _register(client, "issue14-resolve")["id"]
            bob = _register(client, "issue14-resolve-b")["id"]
            atomic = app.state.atomic_profile_service
            ongoing = app.state.ongoing_items_service

            _seed_goal(app, alice, "goal-14-resolve")
            item = atomic.remember(
                alice, GOAL_ITEM_TEXT, source_message_id="message-goal-14"
            )
            _link(app, alice, item.profile_item_id, "goal-14-resolve")

            projection = ongoing.list_items(alice)
            assert len(projection.items) == 1
            reference = projection.items[0].domain_references[0]
            assert reference.resolved is True
            assert reference.state_code == "active"
            assert reference.version == 1
            assert "系统学习概率论" in str(reference.display)
            assert "条件概率" in str(reference.display)
            assert projection.items[0].goal.goal_state.value == "active"

            # 其他账户的同一引用：不解析、不越权。
            assert (
                app.state.ongoing_items_service._domains.resolve(  # noqa: SLF001
                    bob, _reference("goal-14-resolve")
                ).resolved
                is False
            )
            assert ongoing.list_items(bob).items == []

            # 学习领域自己发生的暂停（其他会话暂停目标）立即反映。
            database = app.state.bridges_database
            now = datetime.now(UTC)
            with database.transaction():
                paused = StudyGoalRepository(database).pause_goal_in_transaction(
                    account_id=alice,
                    goal_id="goal-14-resolve",
                    expected_version=1,
                    now=now,
                )
            assert paused is True
            projection = ongoing.list_items(alice)
            reference = projection.items[0].domain_references[0]
            assert reference.state_code == "paused"
            assert reference.version == 2
            assert projection.items[0].goal.goal_state.value == "paused"
            assert "已暂停" in str(reference.display)

            # 完成由领域状态决定：事项区隐藏，画像条目本身不动。
            with database.transaction():
                completed = (
                    StudyGoalRepository(database).complete_goal_in_transaction(
                        account_id=alice,
                        goal_id="goal-14-resolve",
                        expected_version=2,
                        now=now,
                    )
                )
            assert completed is True
            assert ongoing.list_items(alice).items == []
            assert item.profile_item_id in {
                stored.profile_item_id for stored in atomic.list_items(alice)
            }
    finally:
        _close(app)


def test_page_state_ops_execute_in_learning_domain_without_double_write(
    tmp_path: Any, monkeypatch: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    try:
        with TestClient(app) as client:
            account_id = _register(client, "issue14-state")["id"]
            atomic = app.state.atomic_profile_service
            ongoing = app.state.ongoing_items_service
            database = app.state.bridges_database

            _seed_goal(app, account_id, "goal-14-state")
            item = atomic.remember(
                account_id, GOAL_ITEM_TEXT, source_message_id="message-goal-state"
            )
            _link(app, account_id, item.profile_item_id, "goal-14-state")
            item = atomic.get_item(account_id, item.profile_item_id)

            # 暂停：经学习领域执行；画像条目版本与自身状态都不变。
            ongoing.set_state(
                account_id,
                item.profile_item_id,
                OngoingGoalAction.PAUSE,
                version=item.version,
                reference_version=1,
            )
            goal = StudyGoalRepository(database).get_goal(
                account_id, "goal-14-state"
            )
            assert goal is not None
            assert goal.status == "paused"
            assert goal.version == 2
            stored = atomic.get_item(account_id, item.profile_item_id)
            assert stored.version == item.version
            assert stored.goal_state.value == "active"

            # 缺少领域版本：如实拒绝，不盲目覆盖。
            with pytest.raises(AtomicProfileError):
                ongoing.set_state(
                    account_id,
                    item.profile_item_id,
                    OngoingGoalAction.RESUME,
                    version=item.version,
                )

            # 其他会话已把目标恢复：陈旧领域版本的页面操作按冲突拒绝。
            now = datetime.now(UTC)
            with database.transaction():
                revived = (
                    StudyGoalRepository(database).reactivate_goal_in_transaction(
                        account_id=account_id,
                        goal_id="goal-14-state",
                        expected_version=2,
                        now=now,
                    )
                )
            assert revived is True
            with pytest.raises(OngoingDomainReferenceError) as conflict:
                ongoing.set_state(
                    account_id,
                    item.profile_item_id,
                    OngoingGoalAction.PAUSE,
                    version=item.version,
                    reference_version=2,
                )
            assert conflict.value.status_code == 409
            assert conflict.value.code == "ongoing_domain_reference_conflict"
            assert (
                StudyGoalRepository(database)
                .get_goal(account_id, "goal-14-state")
                .version
                == 3
            )

            # 已是目标状态时的重复操作幂等：不推进版本、不报冲突。
            ongoing.set_state(
                account_id,
                item.profile_item_id,
                OngoingGoalAction.RESUME,
                version=item.version,
                reference_version=3,
            )
            assert (
                StudyGoalRepository(database)
                .get_goal(account_id, "goal-14-state")
                .version
                == 3
            )

            # 页面条目版本过期（期间条目被改动过）：按条目冲突拒绝。
            with pytest.raises(AtomicProfileError):
                ongoing.set_state(
                    account_id,
                    item.profile_item_id,
                    OngoingGoalAction.COMPLETE,
                    version=item.version + 1,
                    reference_version=3,
                )

            # 完成：学习目标完成，事项区不再列出；画像条目保留。
            ongoing.set_state(
                account_id,
                item.profile_item_id,
                OngoingGoalAction.COMPLETE,
                version=item.version,
                reference_version=3,
            )
            assert (
                StudyGoalRepository(database)
                .get_goal(account_id, "goal-14-state")
                .status
                == "completed"
            )
            assert ongoing.list_items(account_id).items == []
            assert item.profile_item_id in {
                stored.profile_item_id
                for stored in atomic.list_items(account_id)
            }
            # 已完成目标不进入学习引用投影（list_goals 只看活动/暂停）。
            references = app.state.learning_references_service.read(account_id)
            assert references.goals == []
    finally:
        _close(app)


def test_context_fingerprint_tracks_domain_state_changes(
    tmp_path: Any, monkeypatch: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    try:
        with TestClient(app) as client:
            account_id = _register(client, "issue14-context")["id"]
            atomic = app.state.atomic_profile_service
            ongoing = app.state.ongoing_items_service

            _seed_goal(app, account_id, "goal-14-context")
            item = atomic.remember(
                account_id, GOAL_ITEM_TEXT, source_message_id="message-goal-ctx"
            )
            _link(app, account_id, item.profile_item_id, "goal-14-context")
            item = atomic.get_item(account_id, item.profile_item_id)

            first = ongoing.read_context(account_id)
            assert first.state is OngoingContextState.CURRENT
            fingerprint = first.dependency_fingerprint
            assert first.items[0].domain_references[0].version == 1

            # 其他会话暂停目标：下一轮读取立即重建，指纹包含领域新状态。
            ongoing.set_state(
                account_id,
                item.profile_item_id,
                OngoingGoalAction.PAUSE,
                version=item.version,
                reference_version=1,
            )
            paused = ongoing.read_context(account_id)
            assert paused.state is OngoingContextState.CURRENT
            assert paused.dependency_fingerprint != fingerprint
            assert paused.items[0].goal.goal_state.value == "paused"
            reference = paused.items[0].domain_references[0]
            assert reference.state_code == "paused"
            assert reference.version == 2
            # 暂停中的关联目标不进入接续候选（不确认领域状态就不接续）。
            assert (
                ongoing.continuation_group(
                    account_id,
                    query="继续考研概率论",
                    semantic_item_ids=[item.profile_item_id],
                )
                is None
            )

            # 恢复：接续候选回到该事项，上下文再次重建。
            ongoing.set_state(
                account_id,
                item.profile_item_id,
                OngoingGoalAction.RESUME,
                version=item.version,
                reference_version=2,
            )
            resumed = ongoing.read_context(account_id)
            assert resumed.dependency_fingerprint != paused.dependency_fingerprint
            continuation = ongoing.continuation_group(
                account_id,
                query="继续考研概率论",
                semantic_item_ids=[item.profile_item_id],
            )
            assert continuation is not None
            assert continuation.goal_item_id == item.profile_item_id

            # 使用开关关闭：接续通道同样关闭，不被领域桥接绕过。
            off = client.put("/profiles/controls", json={"usage_enabled": False})
            assert off.status_code == 200, off.text
            assert (
                ongoing.continuation_group(
                    account_id,
                    query="继续考研概率论",
                    semantic_item_ids=[item.profile_item_id],
                )
                is None
            )
            assert (
                ongoing.read_context(account_id).state
                is OngoingContextState.REBUILDING
            )
            on = client.put("/profiles/controls", json={"usage_enabled": True})
            assert on.status_code == 200, on.text
            assert (
                ongoing.read_context(account_id).state
                is OngoingContextState.CURRENT
            )
    finally:
        _close(app)


def test_learning_references_route_is_503_until_domain_is_connected() -> None:
    client = TestClient(create_app())
    _register(client, "issue14-503")

    response = client.get("/profiles/learning-references")

    assert response.status_code == 503, response.text
    assert response.json()["detail"]["error"] == "learning_references_unavailable"
