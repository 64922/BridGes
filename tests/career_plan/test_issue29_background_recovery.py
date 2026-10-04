"""工单 29：冻结画像与背景收据恢复回归。"""

from datetime import timedelta
from types import SimpleNamespace

import pytest

from bridges.api.main import CareerBackgroundLoader
from bridges.career_plan.background import snapshot_from_adopted_slice, unavailable_snapshot
from bridges.career_plan.kernel import CareerNodeFlow, build_career_recipe
from bridges.kernel.contracts import KernelStatus
from bridges.storage.database import BridgesDatabase
from tests.career_plan.test_career_plan_kernel_acceptance import (
    ACCOUNT,
    NOW,
    _inputs,
    _kernel,
    _Ports,
    _seed,
)
from tests.career_plan.test_issue29_personal_career_gap import (
    _adopted_item,
    _adopted_slice,
    _FakeAtomicProfile,
    _loader_app,
)


def test_recovery_checks_background_and_preserves_public_receipts(tmp_path):
    """同消息恢复重新核验背景，删除后仅重算个人节点。"""
    database = BridgesDatabase(tmp_path / "恢复.db")
    database.initialize()
    _seed(database)
    ports = _Ports()
    state = SimpleNamespace(revoked=False, calls=0)

    class Provider:
        def load(self, account_id, **kwargs):
            state.calls += 1
            if state.revoked:
                return unavailable_snapshot("画像已删除", checked_at=NOW)
            return snapshot_from_adopted_slice(
                _adopted_slice(_adopted_item("我不会 MySQL"), owner_account_id=account_id),
                checked_at=NOW + timedelta(seconds=state.calls),
            )

    inputs = _inputs("帮我规划准备 Java 后端开发，城市南昌，经验 1-3年")

    def execute():
        kernel, _ = _kernel(database, ports)
        flow = CareerNodeFlow(
            search=ports, reader=ports, clock=lambda: NOW, background_provider=Provider()
        )
        kernel._runner = flow.run_node
        return kernel.execute(
            recipe=build_career_recipe(background_input_key=flow.background_input_key),
            inputs=inputs,
        )

    first = execute()
    assert first.status is KernelStatus.COMPLETED
    assert any(
        gap["category"] == "to_improve" for gap in first.delivery.payload["projection"]["gaps"]
    )
    public_calls = (len(ports.queries), len(ports.reads))
    normal = execute()
    assert all(node.reused for node in normal.nodes)
    assert state.calls == 2
    state.revoked = True
    revoked = execute()
    assert revoked.status is KernelStatus.COMPLETED
    assert [node.reused for node in revoked.nodes] == [True] * 5 + [False] * 4
    assert all(
        gap["category"] == "to_confirm" for gap in revoked.delivery.payload["projection"]["gaps"]
    )
    assert (len(ports.queries), len(ports.reads)) == public_calls
    database.close()


def test_loader_reuses_frozen_run_slice_without_recompiling(monkeypatch):
    """正常恢复不吸收同轮新画像，失效后重编且保存脱敏快照。"""
    adopted = _adopted_slice(_adopted_item("我熟悉 Java"), owner_account_id=ACCOUNT)
    atomic = _FakeAtomicProfile(adopted)
    run = SimpleNamespace(config={"adopted_profile_slice": adopted.model_dump(mode="json")})

    class Repo:
        def __init__(self, database):
            pass

        def get_generation_run(self, account_id, run_id):
            return run

        def update_generation_config(self, account_id, run_id, config):
            run.config = config
            return 1

    monkeypatch.setattr("bridges.api.main.ConversationRepository", Repo)
    app = _loader_app(atomic)
    app.state.bridges_database = object()
    loader = CareerBackgroundLoader(app)
    first = loader.load(ACCOUNT, run_id="run-29", query="帮我准备", now=NOW)
    atomic.adopted = _adopted_slice(_adopted_item("我熟悉 Redis"), owner_account_id=ACCOUNT)
    second = loader.load(ACCOUNT, run_id="run-29", query="帮我准备", now=NOW)
    assert (
        [item.text for item in first.items]
        == [item.text for item in second.items]
        == ["我熟悉 Java"]
    )
    assert not atomic.compile_calls
    run.config["adopted_profile_slice"] = {"损坏": True}
    third = loader.load(ACCOUNT, run_id="run-29", query="私人请求", now=NOW)
    assert third.items[0].text == "我熟悉 Redis"
    assert len(atomic.compile_calls) == 1
    assert run.config["adopted_profile_slice"]["purpose"]["query"] is None


def test_background_budget_excludes_whole_fact_and_unused_time():
    """预算排除整条事实，不能把未采用或被覆盖条目的时间带进计划。"""
    from bridges.career_plan.background import MAX_BACKGROUND_CHARACTERS, finalize_snapshot
    from bridges.career_plan.parsing import parse_career_request
    from tests.career_plan.test_issue29_personal_career_gap import _background, _profile_item

    long_fact = _profile_item(
        "每天 60 分钟" + "完整背景" * MAX_BACKGROUND_CHARACTERS,
        applicable_to=["plan_time_budget"],
    )
    overridden = _profile_item("每天 45 分钟", applicable_to=["plan_time_budget"])
    overridden.overridden = True
    snapshot = finalize_snapshot(
        analysis=parse_career_request("帮我准备 Java 后端"),
        statement_items=[],
        profile_snapshot=_background(long_fact, overridden),
    )
    assert snapshot.items == []
    assert snapshot.used_profile is False
    assert snapshot.time_budget_minutes is None
    assert "整条未采用" in snapshot.excluded_notes[-1]


def test_unadopted_current_time_does_not_fall_back_to_profile_default():
    """当前时间原话整条超预算后，不借旧默认生成没有依据的时间行动。"""
    from bridges.career_plan.background import (
        MAX_BACKGROUND_CHARACTERS,
        build_statement_items,
        finalize_snapshot,
    )
    from bridges.career_plan.parsing import parse_career_request
    from tests.career_plan.test_issue29_personal_career_gap import _background, _profile_item

    content = "帮我准备 Java 后端，每天 30 分钟。" + "背景" * MAX_BACKGROUND_CHARACTERS
    analysis = parse_career_request(content)
    snapshot = finalize_snapshot(
        analysis=analysis,
        statement_items=build_statement_items(
            user_content=content,
            user_message_id="当前消息",
        ),
        profile_snapshot=_background(
            _profile_item("每天 45 分钟", applicable_to=["plan_time_budget"]),
        ),
    )
    assert snapshot.time_budget_minutes is None
    assert all(item.text != content for item in snapshot.items)


@pytest.mark.parametrize("continuous", [False, True])
def test_revocation_after_verify_starts_cannot_publish_old_conclusion(tmp_path, continuous):
    """核验开始后撤回也会在最终事务复核，有限重算不重读公开来源。"""
    from bridges.career_plan.kernel import NODE_VERIFY
    from bridges.career_plan.service import CareerPlanService, CareerSupersededError
    from bridges.chat.repository import ConversationRepository
    from tests.career_plan.test_career_plan_kernel_acceptance import (
        ASSISTANT,
        CONVERSATION,
        RUN,
    )

    database = BridgesDatabase(tmp_path / "交付撤回.db")
    database.initialize()
    _seed(database)
    with database.transaction():
        database.connection.execute(
            "UPDATE generation_runs SET lease_expires_at = NULL WHERE run_id = ?",
            (RUN,),
        )
        database.connection.execute(
            "INSERT INTO messages (message_id, conversation_id, account_id, role, status,"
            " content, created_at, updated_at) VALUES (?, ?, ?, 'user', 'done', ?, ?, ?)",
            (
                "msg-user-28",
                CONVERSATION,
                ACCOUNT,
                "帮我规划准备 Java 后端开发，城市南昌，经验 1-3年",
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )
    state = SimpleNamespace(revoked=False, calls=0)

    class Provider:
        def load(self, account_id, **kwargs):
            state.calls += 1
            if state.revoked and not continuous:
                return unavailable_snapshot("画像已撤回", checked_at=NOW)
            adopted = _adopted_slice(
                _adopted_item("我不会 MySQL"),
                owner_account_id=account_id,
            )
            if state.revoked:
                adopted = adopted.model_copy(update={"revocation_version": str(state.calls)})
            return snapshot_from_adopted_slice(adopted, checked_at=NOW)

    ports = _Ports()
    repo = ConversationRepository(database)
    service = CareerPlanService(search=ports, reader=ports, background_provider=Provider())

    def revoke(node, event, duration):
        if node == NODE_VERIFY:
            state.revoked = True

    kwargs = {
        "repo": repo,
        "account_id": ACCOUNT,
        "conversation_id": CONVERSATION,
        "user_message_id": "msg-user-28",
        "assistant_message_id": ASSISTANT,
        "run_context": SimpleNamespace(run_id=RUN),
        "emit_node": revoke,
        "stop_event": None,
    }
    if continuous:
        with pytest.raises(CareerSupersededError, match="career_background_changed"):
            service.run(**kwargs)
        assert repo.get_message(ACCOUNT, ASSISTANT).career_plan is None
        assert state.calls == 4, "持续变化只修复一次，不能无限重试"
    else:
        service.run(**kwargs)
        projection = repo.get_message(ACCOUNT, ASSISTANT).career_plan
        assert projection["background"]["used_profile"] is False
        assert all(gap["category"] == "to_confirm" for gap in projection["gaps"])
    assert len(ports.reads) == 1
    database.close()
