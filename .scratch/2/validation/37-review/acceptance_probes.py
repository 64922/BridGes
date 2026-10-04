"""未通过的生产验收探针，独立运行；不能当作已退役功能基线失败。

PYTHONPATH=src python -m pytest .scratch/2/validation/37-review/acceptance_probes.py
"""

from types import SimpleNamespace
from unittest.mock import patch

from bridges.chat.graph import _composite_github_requirement, _node_invoke_subgraph_or_chat
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
