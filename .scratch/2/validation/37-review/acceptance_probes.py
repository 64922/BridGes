"""生产验收探针，独立运行；覆盖此前验收发现的三项缺失与自然顺序组合。

PYTHONPATH=src python -m pytest .scratch/2/validation/37-review/acceptance_probes.py
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

from bridges.chat.graph import _composite_github_requirement, _node_invoke_subgraph_or_chat
from bridges.chat.repository import MessageRecord
from bridges.chat.understanding import MainAgentUnderstanding
from bridges.contracts.chat import (
    ChatMessageRole,
    ChatMessageStatus,
    ChatMode,
)
from bridges.contracts.modules import ModuleDelivery
from bridges.orchestration import CompositePlanner
from bridges.orchestration.production import (
    CompositeOrchestrationService,
    step_result_from_delivery,
)
from bridges.paper.contracts import PaperIdentity
from bridges.storage.database import BridgesDatabase
from tests.chat.test_issue37_composite_dispatch import (
    _deps,
    _seed,
    _understanding,
)


def test_natural_understanding_order_is_accepted_by_planner() -> None:
    """真实主理解按检测器顺序给出 [resources, career]，计划必须自行排序。"""
    text = "帮我找 Java 后端实习岗位，还要学习资料和练手项目"
    moment = datetime.now(UTC)
    message = MessageRecord(
        message_id="user-natural",
        conversation_id="conv-natural",
        account_id="acc-natural",
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
        user_message_id="user-natural",
        content=text,
        mode=ChatMode.COMPANION,
        messages=[message],
    )
    assert understanding.capability_list == ["resources", "career"]
    plan = CompositePlanner().plan(
        goal=text,
        user_message_id="user-natural",
        module_ids=understanding.capability_list,
    )
    assert plan is not None
    assert [step.step_id for step in plan.steps] == ["career", "resources"]


def test_done_does_not_prove_qualified_claims():
    plan = CompositePlanner().plan(
        goal="查论文和资料", user_message_id="user", module_ids=["paper", "resources"]
    )
    result = step_result_from_delivery(plan.steps[0], ModuleDelivery(
        module_id="paper", status="success", projection_field="paper_search",
        projection={}, content="该方法显著优于全部现有方法。", message_status="done",
        artifact_refs={"paper.verify": "不存在的产物"},
    ))
    assert result.trust_state != "qualified"


def test_parent_graph_loads_prior_results_for_reuse(tmp_path):
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    understanding = _understanding()
    _seed(database, understanding.model_dump(mode="json"))
    deps = _deps(database, understanding)
    # 检查编排层接缝；模块内核可能复用局部收据，本探针不冒充完整跨轮测量。
    state = {"module_dispatch": "chat", "assistant_message_id": deps.run.assistant_message_id}
    original = CompositeOrchestrationService.run
    with patch.object(
        CompositeOrchestrationService, "run", autospec=True, side_effect=original
    ) as call:
        _node_invoke_subgraph_or_chat(state, {"configurable": {"deps": deps}})
    assert "prior_results" in call.call_args.kwargs


def test_selected_paper_identity_reaches_github():
    paper = SimpleNamespace(
        artifact_refs={"paper.verify": "paper-verified"},
        trust_state="qualified",
        delivery=SimpleNamespace(projection={
            "selected": [PaperIdentity(
                order=2, title="选定论文", doi="10.1234/example",
                abs_url="https://example.org/paper", content_hash="selected-hash",
            ).model_dump(mode="json")],
        }),
    )
    requirement = _composite_github_requirement(
        SimpleNamespace(upstream={"paper": paper}), "第二篇论文有没有对应代码"
    )
    assert requirement.identity_confirmed
