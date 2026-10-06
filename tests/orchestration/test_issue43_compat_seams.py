"""工单 43：不兼容配方/旧综合产物契约的安全处理（任务 3 / 验收标准 3）。

- 计划步骤引用过期配方或能力版本时被拒绝（RECIPE_VERSION_MISMATCH），
  不把新图套旧谱系；
- 旧综合产物契约版本不符时不复用，按新契约生成明确新运行；
- 内核旧收据在配方升级后不满足 ``_compatible``，重跑并保留旧收据
  （既有 ``test_contract_upgrade_reruns_nodes_and_preserves_old_receipts``
  覆盖）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from bridges.chat.repository import ConversationRepository
from bridges.kernel.contracts import ArtifactTrust, NodeArtifact
from bridges.kernel.repository import NodeKernelRepository
from bridges.orchestration.contracts import (
    COMPOSITE_ARTIFACT_TYPE,
    COMPOSITE_CONTRACT_VERSION,
    PlanViolationCode,
)
from bridges.orchestration.planner import CompositePlanner
from bridges.orchestration.production import load_prior_composite
from bridges.storage.database import BridgesDatabase

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
ACCOUNT = "acc-43-compat"
CONVERSATION = "conv-43-compat"


def test_planner_rejects_stale_recipe_and_capability_versions() -> None:
    planner = CompositePlanner()
    plan = planner.plan(
        goal="找入门论文和学习资料",
        user_message_id="msg-user-43",
        module_ids=["paper", "resources"],
        now=NOW,
    )
    assert plan is not None

    paper = plan.step("paper")
    paper.recipe_version = "stale-recipe-v0"
    validation = planner.validate(plan)
    codes = {violation.code for violation in validation.violations}
    assert PlanViolationCode.RECIPE_VERSION_MISMATCH in codes

    paper.recipe_version = planner.registry.get("paper").recipe_version
    plan.step("resources").capability_version = "stale-capability-v0"
    validation = planner.validate(plan)
    codes = {violation.code for violation in validation.violations}
    assert PlanViolationCode.RECIPE_VERSION_MISMATCH in codes


def _save_composite_artifact(
    database: BridgesDatabase, contract_version: str, *, now: datetime = NOW
) -> None:
    artifact = NodeArtifact.build(
        account_id=ACCOUNT,
        conversation_id=CONVERSATION,
        run_id="run-43-compat",
        task_id=None,
        task_version=None,
        recipe_id="composite",
        recipe_version="1.0",
        node="synthesis",
        artifact_type=COMPOSITE_ARTIFACT_TYPE,
        capability_version="cap-v1",
        trust_state=ArtifactTrust.QUALIFIED,
        input_key="input-key-43",
        input_deps=(),
        source_refs=(),
        read_scope="摘要级",
        requirement_coverage=(),
        unconfirmed=(),
        error=None,
        payload={"contract_version": contract_version, "goal": "旧产物"},
        now=now,
    )
    NodeKernelRepository(database).save_artifact(artifact)


def test_prior_composite_rejected_when_contract_version_mismatches(
    tmp_path: Path,
) -> None:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    conversations = ConversationRepository(database)

    _save_composite_artifact(database, "composite-orchestration-v0")
    assert (
        load_prior_composite(
            conversations, account_id=ACCOUNT, conversation_id=CONVERSATION
        )
        is None
    )

    _save_composite_artifact(
        database,
        COMPOSITE_CONTRACT_VERSION,
        now=datetime(2026, 10, 6, 12, 5, tzinfo=UTC),
    )
    reused = load_prior_composite(
        conversations, account_id=ACCOUNT, conversation_id=CONVERSATION
    )
    assert reused is not None
    assert reused["contract_version"] == COMPOSITE_CONTRACT_VERSION
