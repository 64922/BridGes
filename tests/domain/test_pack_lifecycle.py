"""T047 领域包失效、撤销、重验证与受信回滚的单元测试。

建议接缝：撤销一个已被多项目使用的包版本，验证新运行闭锁、影响带完整、
重验证推进且回滚不能复活不受信版本。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from science_companion.contracts.domain import (
    AttestationConclusion,
    DomainClaimSchema,
    DomainManifestSignature,
    DomainMigrationSpec,
    DomainPackManifest,
    DomainPackStatus,
    DomainRule,
    DomainSourcePolicy,
    DomainWordingPolicy,
    FixtureCase,
    PackImpactAction,
    PackImpactCategory,
    PackImpactItem,
    PackInvalidationStage,
    PackInvalidationTrigger,
    PackRollbackStatus,
    QualificationRecord,
    RevalidationReportStatus,
    ReviewRole,
    ValidatorRequirement,
)
from science_companion.contracts.workflows import WorkflowRunStatus, WorkOrder
from science_companion.domain.loader import DomainPackLoader
from science_companion.domain.pack_lifecycle import (
    DomainPackLifecycleError,
    DomainPackLifecycleService,
)
from science_companion.domain.registry import DomainPackRegistry
from science_companion.domain.runtime import DomainPackValidationRuntime
from science_companion.domain.workbench import (
    DomainPackWorkbenchError,
    DomainPackWorkbenchService,
)
from science_companion.workflows import WorkflowError, WorkflowService


class MinimalDomainPack:
    """与 test_workbench 相同的确定性包实现。"""

    def __init__(self, manifest: DomainPackManifest) -> None:
        self.manifest = manifest

    def classify_question(self, question_context: Any) -> str:
        return str(question_context.get("question_type", "measurement"))

    def plan_sources(self, question_type: str, risk_tier: str) -> dict[str, Any]:
        return {"question_type": question_type, "risk_tier": risk_tier}

    def normalize_metadata(self, adapter_record: Any) -> dict[str, Any]:
        return dict(adapter_record or {})

    def resolve_version_status(self, records: Any) -> dict[str, Any]:
        return {"status": "active", "records": list(records or [])}

    def parse_domain_structure(self, document: Any) -> dict[str, Any]:
        return dict(document or {})

    def extract_claim_schema(self, content: Any) -> dict[str, Any]:
        return {"content": content}

    def assess_evidence(self, claim: Any, evidence_set: Any) -> dict[str, Any]:
        return {"claim": claim, "evidence": list(evidence_set or [])}

    def detect_conflicts(self, claim_set: Any, evidence_set: Any) -> list[Any]:
        return []

    def constrain_wording(self, assessment: Any, audience: str, genre: str) -> dict[str, Any]:
        return {"assessment": assessment, "audience": audience, "genre": genre}

    def validate_claim(self, claim: Any, evidence_set: Any) -> dict[str, Any]:
        return {"status": "verified", "claim": claim}

    def evaluate(
        self, run_artifacts: dict[str, Any], fixture_set: list[FixtureCase]
    ) -> dict[str, Any]:
        fixture = fixture_set[0]
        return {
            "status": fixture.expected_status,
            "reason_codes": list(fixture.expected_reason_codes),
            "rule_ids": list(fixture.expected_rule_ids),
            "validator_ids": list(fixture.expected_validator_ids),
        }


def _fixture(
    fixture_id: str,
    expected_status: str,
    *,
    kind: str = "positive",
    requires_human: bool = False,
) -> FixtureCase:
    return FixtureCase(
        fixture_id=fixture_id,
        kind=kind,
        question_type="measurement",
        risk_tier="general_education",
        input_snapshot={
            "question_type": "measurement",
            "content": "1 m = 100 cm",
            "claim": {"text": "1 m = 100 cm"},
            "evidence_set": [{"relation": "supports"}],
        },
        expected_status=expected_status,
        expected_rule_ids=["rule.units"],
        requires_human=requires_human,
        rationale="生命周期测试夹具",
    )


def build_manifest(
    *,
    pack_id: str = "lifecycle.test-pack",
    version: str = "1.0.0",
    rollback_versions: list[str] | None = None,
    compatible_with: list[str] | None = None,
    dependencies: list[dict[str, Any]] | None = None,
) -> DomainPackManifest:
    """构造通过加载器预检的最小包 Manifest。"""
    fixtures = [
        _fixture("fx.pass", "verified"),
        _fixture("fx.blocked", "blocked", kind="boundary"),
        _fixture("fx.human", "needs_human", requires_human=True),
    ]
    manifest = DomainPackManifest(
        manifest_schema="domain-pack-manifest/v1",
        id=pack_id,
        version=version,
        platform_api=">=1.0,<2.0",
        pack_api="domain-pack/v1",
        scope=["测量与单位"],
        exclusions=["危险实验指导"],
        risk_tiers=["general_education"],
        question_types=["measurement"],
        source_policies=[
            DomainSourcePolicy(
                policy_id="source.measurement",
                applies_to=["measurement"],
                allowed_source_roles=["standard", "handbook"],
                on_failure="block",
            )
        ],
        claim_schemas=[
            DomainClaimSchema(
                schema_id="claim.measurement",
                applies_to=["measurement"],
                required_fields=["value", "unit"],
            )
        ],
        wording_policy=[
            DomainWordingPolicy(
                policy_id="wording.measurement",
                applies_to=["measurement"],
                allowed_statuses=["verified", "qualified"],
                forbidden_strengths=["精确等于"],
                required_disclosures=["不确定度"],
            )
        ],
        validators=[
            ValidatorRequirement(
                validator_id="validator.units",
                capability_name="unit_dimension_check",
                capability_version="1",
                input_schema_version="1",
                output_schema_version="1",
            )
        ],
        rules=[
            DomainRule(
                rule_id="rule.units",
                applies_to=["measurement"],
                conditions={"dimension": "length"},
                outputs={"status": "verified"},
                rejection_code="unit_mismatch",
                explanation="单位必须量纲一致",
                fixture_ids=[fixture.fixture_id for fixture in fixtures],
            )
        ],
        fixtures=fixtures,
        lifecycle_status="draft",
        platform_floor={"authorization_required": True, "allow_network_access": False},
        compatibility={
            "compatible_with": compatible_with or ["1.0.0"],
            "preserves_runtime_contract": True,
            "requires_revalidation": True,
        },
        rollback_versions=rollback_versions or ["1.0.0"],
        rollback_policy={
            "trusted_versions": rollback_versions or ["1.0.0"],
            "requires_fixture_replay": True,
            "preserves_history": True,
        },
        dependencies=dependencies or [],
        migrations=(
            [
                DomainMigrationSpec(
                    migration_id="mig.1-2",
                    from_version="1.0.0",
                    to_version=version,
                    strategy="transform",
                )
            ]
            if version == "2.0.0"
            else []
        ),
    )
    digest = DomainPackLoader.manifest_digest(manifest)
    return manifest.model_copy(update={"content_digest": digest})


def _qualify(
    service: DomainPackWorkbenchService, person_id: str, manifest: DomainPackManifest
) -> None:
    service.register_qualification(
        QualificationRecord(
            qualification_id=f"q-{person_id}-{manifest.id}-{manifest.version}",
            person_id=person_id,
            qualification_type="domain_expert",
            verifier_id="verifier",
            disciplines=list(manifest.disciplines),
            verified_at=datetime.now(UTC),
            valid_until=datetime.now(UTC) + timedelta(days=365),
        )
    )


def _declare_all(
    service: DomainPackWorkbenchService,
    manifest: DomainPackManifest,
    persons: list[str],
) -> None:
    for person in persons:
        service.declare_conflict_of_interest(
            person, manifest.id, manifest.version, ["无相关利益"]
        )


def _sign_and_release(
    service: DomainPackWorkbenchService,
    manifest: DomainPackManifest,
    maintainer: str,
    reviewer: str,
    releaser: str,
) -> None:
    """维护者、复核者、发行者走完三签、灰度与发行。"""
    _qualify(service, reviewer, manifest)
    _declare_all(service, manifest, [maintainer, reviewer, releaser])
    service.submit_content_signature(
        maintainer,
        manifest.id,
        manifest.version,
        conclusion=AttestationConclusion.APPROVE,
        opinion="内容完整",
    )
    service.assign_reviewer(maintainer, manifest.id, manifest.version, reviewer)
    service.assign_releaser(maintainer, manifest.id, manifest.version, releaser)
    service.submit_independent_signature(
        reviewer,
        manifest.id,
        manifest.version,
        conclusion=AttestationConclusion.APPROVE,
        opinion="独立复核通过",
    )
    service.prepare_gray_release(reviewer, manifest.id, manifest.version)
    service.release(releaser, manifest.id, manifest.version)


class LifecycleEnv:
    """登记一个（或两个）包版本的工作台 + 生命周期环境。"""

    def __init__(self) -> None:
        self.loader = DomainPackLoader(require_capability_contracts=False)
        self.registry = DomainPackRegistry(self.loader)
        self.workbench = DomainPackWorkbenchService(
            self.registry,
            runtime=DomainPackValidationRuntime(self.loader),
            loader=self.loader,
        )
        self.lifecycle = DomainPackLifecycleService(
            workbench=self.workbench,
            loader=self.loader,
        )

    def add_pack(self, manifest: DomainPackManifest) -> DomainPackManifest:
        loaded = self.registry.register(MinimalDomainPack(manifest))
        self.workbench.register_pack("maintainer", loaded)
        return manifest


@pytest.fixture
def env() -> LifecycleEnv:
    return LifecycleEnv()


def _run_workflow_service(
    env: LifecycleEnv,
) -> WorkflowService:
    """带领域包门的工作流服务（模拟 main.py 装配）。"""

    def _gate(refs: list[str]) -> None:
        try:
            env.lifecycle.require_packs_usable(refs)
        except DomainPackLifecycleError as exc:
            raise WorkflowError(str(exc)) from exc

    service = WorkflowService()
    service.set_pack_gate(_gate)
    service.register_workflow(
        name="generic_science_task",
        version="1",
        nodes=[{"node_id": "compile", "node_name": "编译"}],
        terminal_states=[WorkflowRunStatus.SUCCEEDED, WorkflowRunStatus.BLOCKED],
    )
    return service


def _work_order(project_id: str, pack_ref: str) -> WorkOrder:
    return WorkOrder(
        workflow_name="generic_science_task",
        workflow_version="1",
        project_id=project_id,
        objective="使用领域包",
        success_criteria="运行成功",
        risk_statement="无",
        domain_pack_refs=[pack_ref],
    )


class TestInvalidationStageMachine:
    def test_event_progression_and_close_gate(self, env: LifecycleEnv) -> None:
        manifest = env.add_pack(build_manifest())
        event = env.lifecycle.record_invalidation_event(
            "maintainer",
            manifest.id,
            manifest.version,
            PackInvalidationTrigger.SOURCE_RETRACTED,
            "来源撤回",
        )
        assert event.stage == PackInvalidationStage.DETECTED

        event = env.lifecycle.advance_stage(
            "maintainer", event.event_id, PackInvalidationStage.TRIAGED
        )
        event = env.lifecycle.advance_stage(
            "maintainer", event.event_id, PackInvalidationStage.CONTAINED
        )
        # 定位影响前必须先生成影响集。
        with pytest.raises(DomainPackLifecycleError, match="影响集"):
            env.lifecycle.advance_stage(
                "maintainer",
                event.event_id,
                PackInvalidationStage.IMPACTED_OBJECTS_FOUND,
            )

        env.lifecycle.register_pack_impact_source(
            PackImpactCategory.RUN,
            lambda pack_id, version: [
                PackImpactItem(
                    item_id="run:r1",
                    category=PackImpactCategory.RUN,
                    ref_id="r1",
                    label="运行 r1",
                    action=PackImpactAction.PRESERVE_AND_MARK,
                )
            ],
        )
        env.lifecycle.resolve_impact("maintainer", event.event_id)
        event = env.lifecycle.advance_stage(
            "maintainer",
            event.event_id,
            PackInvalidationStage.IMPACTED_OBJECTS_FOUND,
        )
        event = env.lifecycle.advance_stage(
            "maintainer", event.event_id, PackInvalidationStage.REMEDIATING
        )
        event = env.lifecycle.advance_stage(
            "maintainer", event.event_id, PackInvalidationStage.REVALIDATING
        )
        # 重验证未完成前不能关闭。
        with pytest.raises(DomainPackLifecycleError, match="重验证未完成"):
            env.lifecycle.advance_stage(
                "maintainer", event.event_id, PackInvalidationStage.CLOSED
            )

        report = env.lifecycle.report_revalidated(
            "maintainer", event.event_id, PackImpactCategory.RUN, ["r1"]
        )
        assert report.status == RevalidationReportStatus.COMPLETED
        event = env.lifecycle.advance_stage(
            "maintainer", event.event_id, PackInvalidationStage.CLOSED
        )
        assert event.stage == PackInvalidationStage.CLOSED
        assert event.closed_at is not None

    def test_illegal_transition_rejected(self, env: LifecycleEnv) -> None:
        manifest = env.add_pack(build_manifest())
        event = env.lifecycle.record_invalidation_event(
            "maintainer",
            manifest.id,
            manifest.version,
            PackInvalidationTrigger.RULE_DEFECT,
            "规则缺陷",
        )
        # 不能跨阶段跳过（非紧急）。
        with pytest.raises(DomainPackLifecycleError, match="不允许从"):
            env.lifecycle.advance_stage(
                "maintainer", event.event_id, PackInvalidationStage.CONTAINED
            )
        # 不能回退。
        env.lifecycle.advance_stage(
            "maintainer", event.event_id, PackInvalidationStage.TRIAGED
        )
        with pytest.raises(DomainPackLifecycleError, match="不允许从"):
            env.lifecycle.advance_stage(
                "maintainer", event.event_id, PackInvalidationStage.DETECTED
            )

    def test_emergency_skip_requires_security_admin(self, env: LifecycleEnv) -> None:
        manifest = env.add_pack(build_manifest())
        env.lifecycle.register_security_admin("sec-admin")
        # 非管理员不能发起紧急失效。
        with pytest.raises(DomainPackLifecycleError, match="只有安全管理员"):
            env.lifecycle.record_invalidation_event(
                "maintainer",
                manifest.id,
                manifest.version,
                PackInvalidationTrigger.SECURITY_EVENT,
                "安全事件",
                emergency=True,
            )
        event = env.lifecycle.record_invalidation_event(
            "sec-admin",
            manifest.id,
            manifest.version,
            PackInvalidationTrigger.SECURITY_EVENT,
            "安全事件",
            emergency=True,
        )
        assert event.stage == PackInvalidationStage.CONTAINED
        # 非管理员不能跳过；管理员可以。
        event2 = env.lifecycle.record_invalidation_event(
            "maintainer",
            manifest.id,
            manifest.version,
            PackInvalidationTrigger.EVALUATION_REGRESSION,
            "评测回归",
        )
        with pytest.raises(DomainPackLifecycleError, match="不允许从"):
            env.lifecycle.advance_stage(
                "maintainer", event2.event_id, PackInvalidationStage.CONTAINED
            )
        env.lifecycle.advance_stage(
            "sec-admin", event2.event_id, PackInvalidationStage.CONTAINED
        )

    def test_outsider_cannot_handle_event(self, env: LifecycleEnv) -> None:
        manifest = env.add_pack(build_manifest())
        with pytest.raises(DomainPackLifecycleError, match="只有该包版本"):
            env.lifecycle.record_invalidation_event(
                "outsider",
                manifest.id,
                manifest.version,
                PackInvalidationTrigger.RULE_DEFECT,
                "越权",
            )


class TestEmergencyRevocation:
    def test_emergency_revoke_blocks_new_runs(self, env: LifecycleEnv) -> None:
        manifest = env.add_pack(build_manifest())
        _sign_and_release(env.workbench, manifest, "maintainer", "reviewer", "releaser")
        env.lifecycle.register_security_admin("sec-admin")

        workflow = _run_workflow_service(env)
        draft = workflow.submit_work_order(
            "alice", _work_order("project-1", f"{manifest.id}@1.0.0")
        )
        assert draft.run_status == WorkflowRunStatus.DRAFT

        event, revocation = env.lifecycle.emergency_revoke(
            "sec-admin",
            manifest.id,
            manifest.version,
            PackInvalidationTrigger.SECURITY_EVENT,
            "签名密钥泄漏",
            second_factor="token-123",
        )
        assert event.stage == PackInvalidationStage.CONTAINED
        assert revocation.blocks_new_runs
        assert revocation.follow_up_required
        assert env.workbench.get_record(manifest.id, manifest.version).lifecycle_status == (
            DomainPackStatus.REVOKED
        )

        # 新运行闭锁：提交与确认都被拒绝。
        with pytest.raises(WorkflowError, match="领域包已撤销或失效"):
            workflow.submit_work_order(
                "alice", _work_order("project-1", f"{manifest.id}@1.0.0")
            )
        with pytest.raises(WorkflowError, match="领域包已撤销或失效"):
            workflow.confirm_work_order("alice", draft.run_id)

    def test_revoke_requires_admin_and_second_factor(self, env: LifecycleEnv) -> None:
        manifest = env.add_pack(build_manifest())
        _sign_and_release(env.workbench, manifest, "maintainer", "reviewer", "releaser")
        with pytest.raises(DomainPackLifecycleError, match="只有安全管理员"):
            env.lifecycle.emergency_revoke(
                "maintainer",
                manifest.id,
                manifest.version,
                PackInvalidationTrigger.SECURITY_EVENT,
                "越权撤销",
                second_factor="token",
            )
        env.lifecycle.register_security_admin("sec-admin")
        with pytest.raises(DomainPackLifecycleError, match="二次认证"):
            env.lifecycle.emergency_revoke(
                "sec-admin",
                manifest.id,
                manifest.version,
                PackInvalidationTrigger.SECURITY_EVENT,
                "缺少二次认证",
            )

    def test_security_admin_cannot_sign_or_release(self, env: LifecycleEnv) -> None:
        """安全管理员可以撤销，但不能编辑规则或直接发布替代版本。"""
        manifest = env.add_pack(build_manifest())
        env.lifecycle.register_security_admin("sec-admin")
        record = env.workbench.get_record(manifest.id, manifest.version)
        # 安全管理员不是维护者：内容签名被拒绝。
        with pytest.raises(DomainPackWorkbenchError, match="只有内容维护者"):
            env.workbench.submit_content_signature(
                "sec-admin",
                manifest.id,
                manifest.version,
                conclusion=AttestationConclusion.APPROVE,
                opinion="冒充维护者",
            )
        # 安全管理员不是发行者：发行签名被拒绝。
        _sign_and_release(env.workbench, manifest, "maintainer", "reviewer", "releaser")
        with pytest.raises(DomainPackWorkbenchError, match="只有平台发行者"):
            env.workbench.release("sec-admin", manifest.id, manifest.version)
        assert record.lifecycle_status == DomainPackStatus.DRAFT

    def test_unknown_pack_version_fails_closed(self, env: LifecycleEnv) -> None:
        assert not env.lifecycle.is_pack_usable("unknown.pack", "1.0.0")
        with pytest.raises(DomainPackLifecycleError, match="领域包已撤销或失效"):
            env.lifecycle.require_packs_usable(["unknown.pack@1.0.0"])


class TestImpactSet:
    def test_impact_set_covers_all_categories(self, env: LifecycleEnv) -> None:
        manifest = env.add_pack(build_manifest(version="1.0.0"))
        env.add_pack(
            build_manifest(
                pack_id="lifecycle.dependent-pack",
                version="1.0.0",
                dependencies=[
                    {
                        "pack_id": manifest.id,
                        "version": "1.0.0",
                        "digest": "0" * 64,
                        "dependency_type": "domain_pack",
                    }
                ],
            )
        )

        def _run_source(pack_id: str, version: str) -> list[PackImpactItem]:
            return [
                PackImpactItem(
                    item_id=f"run:r-{pack_id}",
                    category=PackImpactCategory.RUN,
                    ref_id="run-1",
                    label="运行 run-1",
                    action=PackImpactAction.PRESERVE_AND_MARK,
                    account_id="alice",
                    project_id="project-1",
                )
            ]

        def _claim_source(pack_id: str, version: str) -> list[PackImpactItem]:
            return [
                PackImpactItem(
                    item_id="claim:c1",
                    category=PackImpactCategory.CLAIM,
                    ref_id="claim-1",
                    label="Claim claim-1",
                )
            ]

        def _evidence_source(pack_id: str, version: str) -> list[PackImpactItem]:
            return [
                PackImpactItem(
                    item_id="evidence:e1",
                    category=PackImpactCategory.EVIDENCE,
                    ref_id="evidence-1",
                    label="Evidence evidence-1",
                )
            ]

        def _wording_source(pack_id: str, version: str) -> list[PackImpactItem]:
            return [
                PackImpactItem(
                    item_id="wording:w1",
                    category=PackImpactCategory.WORDING,
                    ref_id="wording-1",
                    label="措辞约束 wording-1",
                )
            ]

        def _artifact_source(pack_id: str, version: str) -> list[PackImpactItem]:
            return [
                PackImpactItem(
                    item_id="artifact:a1",
                    category=PackImpactCategory.ARTIFACT,
                    ref_id="artifact-1",
                    label="产物 artifact-1",
                )
            ]

        def _project_source(pack_id: str, version: str) -> list[PackImpactItem]:
            return [
                PackImpactItem(
                    item_id="project:project-1",
                    category=PackImpactCategory.PROJECT,
                    ref_id="project-1",
                    label="项目 project-1",
                    project_id="project-1",
                )
            ]

        def _user_action_source(pack_id: str, version: str) -> list[PackImpactItem]:
            return [
                PackImpactItem(
                    item_id="user_action:alice:run-1",
                    category=PackImpactCategory.USER_ACTION,
                    ref_id="run-1",
                    label="用户动作：alice 提交运行",
                    account_id="alice",
                )
            ]

        for category, source in (
            (PackImpactCategory.RUN, _run_source),
            (PackImpactCategory.CLAIM, _claim_source),
            (PackImpactCategory.EVIDENCE, _evidence_source),
            (PackImpactCategory.WORDING, _wording_source),
            (PackImpactCategory.ARTIFACT, _artifact_source),
            (PackImpactCategory.PROJECT, _project_source),
            (PackImpactCategory.USER_ACTION, _user_action_source),
        ):
            env.lifecycle.register_pack_impact_source(category, source)

        event = env.lifecycle.record_invalidation_event(
            "maintainer",
            manifest.id,
            manifest.version,
            PackInvalidationTrigger.DEPENDENCY_REVOKED,
            "依赖失效",
        )
        impact = env.lifecycle.resolve_impact("maintainer", event.event_id)
        grouped = impact.by_category()
        assert set(grouped) == {
            "pack",
            "run",
            "claim",
            "evidence",
            "wording",
            "artifact",
            "project",
            "user_action",
        }
        # 包影响带包含依赖它的包。
        assert any(
            item.category == PackImpactCategory.PACK and "dependent-pack" in item.label
            for item in impact.items
        )
        assert any(
            item.category == PackImpactCategory.PACK
            and item.action == PackImpactAction.BLOCK_NEW_USE
            for item in impact.items
        )

    def test_revalidation_ref_must_belong_to_impact(self, env: LifecycleEnv) -> None:
        manifest = env.add_pack(build_manifest())
        event = env.lifecycle.record_invalidation_event(
            "maintainer",
            manifest.id,
            manifest.version,
            PackInvalidationTrigger.RULE_DEFECT,
            "规则缺陷",
        )
        env.lifecycle.register_pack_impact_source(
            PackImpactCategory.RUN,
            lambda pack_id, version: [
                PackImpactItem(
                    item_id="run:run-1",
                    category=PackImpactCategory.RUN,
                    ref_id="run-1",
                    label="运行 run-1",
                )
            ],
        )
        env.lifecycle.resolve_impact("maintainer", event.event_id)
        for stage in (
            PackInvalidationStage.TRIAGED,
            PackInvalidationStage.CONTAINED,
            PackInvalidationStage.IMPACTED_OBJECTS_FOUND,
            PackInvalidationStage.REMEDIATING,
            PackInvalidationStage.REVALIDATING,
        ):
            env.lifecycle.advance_stage("maintainer", event.event_id, stage)
        with pytest.raises(DomainPackLifecycleError, match="不在该失效事件"):
            env.lifecycle.report_revalidated(
                "maintainer",
                event.event_id,
                PackImpactCategory.RUN,
                ["run-999"],
            )


def _two_version_env(env: LifecycleEnv) -> tuple[DomainPackManifest, DomainPackManifest]:
    v1 = env.add_pack(build_manifest(version="1.0.0"))
    _sign_and_release(env.workbench, v1, "maintainer", "reviewer", "releaser")
    v2 = env.add_pack(
        build_manifest(
            version="2.0.0",
            rollback_versions=["1.0.0"],
            compatible_with=["1.0.0"],
        )
    )
    _sign_and_release(env.workbench, v2, "maintainer", "reviewer", "releaser")
    env.lifecycle.register_security_admin("sec-admin")
    return v1, v2


class TestTrustedRollback:

    def test_rollback_to_trusted_version(self, env: LifecycleEnv) -> None:
        v1, v2 = _two_version_env(env)
        event, revocation = env.lifecycle.emergency_revoke(
            "sec-admin",
            v2.id,
            v2.version,
            PackInvalidationTrigger.SIGNATURE_INVALID,
            "签名失效",
            second_factor="token",
        )
        assert not env.lifecycle.is_pack_usable(v2.id, v2.version)
        assert env.lifecycle.is_pack_usable(v1.id, v1.version)

        rollback = env.lifecycle.propose_rollback(
            "maintainer", v2.id, v2.version, "回滚到受信旧版"
        )
        assert rollback.to_version == "1.0.0"
        assert rollback.fixture_passed
        assert rollback.status == PackRollbackStatus.PROPOSED

        # 未双方确认前不能执行。
        with pytest.raises(DomainPackLifecycleError, match="双方确认"):
            env.lifecycle.execute_rollback("releaser", rollback.rollback_id)

        rollback = env.lifecycle.confirm_rollback(
            "reviewer",
            rollback.rollback_id,
            role=ReviewRole.INDEPENDENT,
            conclusion=AttestationConclusion.APPROVE,
            opinion="影响可接受",
        )
        assert rollback.status == PackRollbackStatus.PROPOSED
        rollback = env.lifecycle.confirm_rollback(
            "releaser",
            rollback.rollback_id,
            role=ReviewRole.PLATFORM,
            conclusion=AttestationConclusion.APPROVE,
            opinion="依赖兼容",
        )
        assert rollback.status == PackRollbackStatus.APPROVED

        rollback = env.lifecycle.execute_rollback("releaser", rollback.rollback_id)
        assert rollback.status == PackRollbackStatus.EXECUTED
        assert env.workbench.get_default_active_version(v2.id) == "1.0.0"
        # 被撤销版本不能通过回滚复活。
        assert not env.lifecycle.is_pack_usable(v2.id, v2.version)
        assert env.workbench.get_record(v2.id, v2.version).lifecycle_status == (
            DomainPackStatus.REVOKED
        )

    def test_rollback_cannot_revive_revoked_or_unknown(self, env: LifecycleEnv) -> None:
        manifest = env.add_pack(build_manifest(version="1.0.0"))
        _sign_and_release(env.workbench, manifest, "maintainer", "reviewer", "releaser")
        env.lifecycle.register_security_admin("sec-admin")
        env.lifecycle.emergency_revoke(
            "sec-admin",
            manifest.id,
            manifest.version,
            PackInvalidationTrigger.SECURITY_EVENT,
            "安全事件",
            second_factor="token",
        )
        # 只有一个版本且已撤销：没有可用的受信回滚目标。
        with pytest.raises(DomainPackLifecycleError, match="没有可用的受信回滚目标"):
            env.lifecycle.propose_rollback(
                "maintainer", manifest.id, manifest.version, "尝试回滚"
            )

    def test_rollback_rejects_signature_invalid_target(self, env: LifecycleEnv) -> None:
        v1, v2 = _two_version_env(env)
        # 让 1.0.0 的签名无效：挂上未绑定摘要的签名。
        loaded_v1 = env.registry.get(v1.id, v1.version)
        loaded_v1.manifest.signatures = [
            DomainManifestSignature(
                signer_id="forged",
                signed_digest="0" * 64,
                signature="forged",
                scope=["manifest", "manifest_digest", "content_digest"],
            )
        ]
        env.lifecycle.register_security_admin("sec-admin")
        env.lifecycle.emergency_revoke(
            "sec-admin",
            v2.id,
            v2.version,
            PackInvalidationTrigger.SIGNATURE_INVALID,
            "签名失效",
            second_factor="token",
        )
        with pytest.raises(DomainPackLifecycleError, match="没有可用的受信回滚目标"):
            env.lifecycle.propose_rollback("maintainer", v2.id, v2.version, "回滚")

    def test_rollback_rejects_incompatible_target(self, env: LifecycleEnv) -> None:
        """依赖已失效的旧版不再是兼容回滚目标。"""
        dependency = env.add_pack(
            build_manifest(pack_id="lifecycle.dep-pack", version="1.0.0")
        )
        _sign_and_release(
            env.workbench, dependency, "maintainer", "reviewer", "releaser"
        )
        v1 = env.add_pack(
            build_manifest(
                version="1.0.0",
                dependencies=[
                    {
                        "pack_id": dependency.id,
                        "version": "1.0.0",
                        "digest": "0" * 64,
                        "dependency_type": "domain_pack",
                    }
                ],
            )
        )
        _sign_and_release(env.workbench, v1, "maintainer", "reviewer", "releaser")
        v2 = env.add_pack(
            build_manifest(
                version="2.0.0",
                rollback_versions=["1.0.0"],
                compatible_with=["1.0.0"],
            )
        )
        _sign_and_release(env.workbench, v2, "maintainer", "reviewer", "releaser")
        env.lifecycle.register_security_admin("sec-admin")
        # 依赖包先被撤销 → 1.0.0 的依赖锁不再受信，回滚被阻断。
        env.lifecycle.emergency_revoke(
            "sec-admin",
            dependency.id,
            dependency.version,
            PackInvalidationTrigger.SIGNATURE_INVALID,
            "依赖签名失效",
            second_factor="token",
        )
        env.lifecycle.emergency_revoke(
            "sec-admin",
            v2.id,
            v2.version,
            PackInvalidationTrigger.RULE_DEFECT,
            "规则缺陷",
            second_factor="token",
        )
        with pytest.raises(DomainPackLifecycleError, match="没有可用的受信回滚目标"):
            env.lifecycle.propose_rollback("maintainer", v2.id, v2.version, "回滚")

    def test_rollback_rejected_by_reviewer_cannot_execute(self, env: LifecycleEnv) -> None:
        v1, v2 = _two_version_env(env)
        env.lifecycle.emergency_revoke(
            "sec-admin",
            v2.id,
            v2.version,
            PackInvalidationTrigger.EVALUATION_REGRESSION,
            "评测回归",
            second_factor="token",
        )
        rollback = env.lifecycle.propose_rollback(
            "maintainer", v2.id, v2.version, "回滚"
        )
        rollback = env.lifecycle.confirm_rollback(
            "reviewer",
            rollback.rollback_id,
            role=ReviewRole.INDEPENDENT,
            conclusion=AttestationConclusion.REJECT,
            opinion="旧版同样存在回归",
        )
        assert rollback.status == PackRollbackStatus.REJECTED
        with pytest.raises(DomainPackLifecycleError, match="双方确认"):
            env.lifecycle.execute_rollback("releaser", rollback.rollback_id)

    def test_rollback_confirm_requires_assigned_role(self, env: LifecycleEnv) -> None:
        v1, v2 = _two_version_env(env)
        env.lifecycle.emergency_revoke(
            "sec-admin",
            v2.id,
            v2.version,
            PackInvalidationTrigger.SECURITY_EVENT,
            "安全事件",
            second_factor="token",
        )
        rollback = env.lifecycle.propose_rollback(
            "maintainer", v2.id, v2.version, "回滚"
        )
        with pytest.raises(DomainPackLifecycleError, match="只有被分配"):
            env.lifecycle.confirm_rollback(
                "maintainer",
                rollback.rollback_id,
                role=ReviewRole.INDEPENDENT,
                conclusion=AttestationConclusion.APPROVE,
            )


class TestLifecycleGates:
    """T047 代码审查修复后的新增门测试。"""

    def test_contained_event_blocks_new_runs_and_closed_recovers(
        self, env: LifecycleEnv
    ) -> None:
        """非紧急失效进入控制阶段后新运行闭锁；事件关闭后恢复可用。"""
        manifest = env.add_pack(build_manifest())
        _sign_and_release(env.workbench, manifest, "maintainer", "reviewer", "releaser")
        workflow = _run_workflow_service(env)

        event = env.lifecycle.record_invalidation_event(
            "maintainer",
            manifest.id,
            manifest.version,
            PackInvalidationTrigger.EVALUATION_REGRESSION,
            "评测回归",
        )
        env.lifecycle.advance_stage(
            "maintainer", event.event_id, PackInvalidationStage.TRIAGED
        )
        # 分诊阶段未控制：新运行仍可提交。
        draft = workflow.submit_work_order(
            "alice", _work_order("project-1", f"{manifest.id}@1.0.0")
        )
        assert draft.run_status == WorkflowRunStatus.DRAFT

        env.lifecycle.advance_stage(
            "maintainer", event.event_id, PackInvalidationStage.CONTAINED
        )
        # 控制阶段后新运行失败闭锁。
        with pytest.raises(WorkflowError, match="领域包已撤销或失效"):
            workflow.submit_work_order(
                "alice", _work_order("project-1", f"{manifest.id}@1.0.0")
            )
        with pytest.raises(WorkflowError, match="领域包已撤销或失效"):
            workflow.confirm_work_order("alice", draft.run_id)

        # 事件关闭后恢复可用。
        env.lifecycle.resolve_impact("maintainer", event.event_id)
        for stage in (
            PackInvalidationStage.IMPACTED_OBJECTS_FOUND,
            PackInvalidationStage.REMEDIATING,
            PackInvalidationStage.REVALIDATING,
            PackInvalidationStage.CLOSED,
        ):
            env.lifecycle.advance_stage("maintainer", event.event_id, stage)
        draft = workflow.submit_work_order(
            "alice", _work_order("project-1", f"{manifest.id}@1.0.0")
        )
        assert draft.run_status == WorkflowRunStatus.DRAFT

    def test_revalidation_failed_can_be_recovered(self, env: LifecycleEnv) -> None:
        """重验证失败的对象修复后重新登记通过，解除失败并允许关闭。"""
        manifest = env.add_pack(build_manifest())
        env.lifecycle.register_pack_impact_source(
            PackImpactCategory.RUN,
            lambda pack_id, version: [
                PackImpactItem(
                    item_id="run:run-1",
                    category=PackImpactCategory.RUN,
                    ref_id="run-1",
                    label="运行 run-1",
                )
            ],
        )
        event = env.lifecycle.record_invalidation_event(
            "maintainer",
            manifest.id,
            manifest.version,
            PackInvalidationTrigger.RULE_DEFECT,
            "规则缺陷",
        )
        env.lifecycle.resolve_impact("maintainer", event.event_id)
        for stage in (
            PackInvalidationStage.TRIAGED,
            PackInvalidationStage.CONTAINED,
            PackInvalidationStage.IMPACTED_OBJECTS_FOUND,
            PackInvalidationStage.REMEDIATING,
            PackInvalidationStage.REVALIDATING,
        ):
            env.lifecycle.advance_stage("maintainer", event.event_id, stage)

        report = env.lifecycle.report_revalidated(
            "maintainer",
            event.event_id,
            PackImpactCategory.RUN,
            [],
            failed=["run-1"],
        )
        assert report.status == RevalidationReportStatus.FAILED
        with pytest.raises(DomainPackLifecycleError, match="重验证未完成"):
            env.lifecycle.advance_stage(
                "maintainer", event.event_id, PackInvalidationStage.CLOSED
            )
        # 修复后重做：run-1 重新登记为重验证通过，失败状态解除。
        report = env.lifecycle.report_revalidated(
            "maintainer",
            event.event_id,
            PackImpactCategory.RUN,
            ["run-1"],
        )
        assert report.status == RevalidationReportStatus.COMPLETED
        event = env.lifecycle.advance_stage(
            "maintainer", event.event_id, PackInvalidationStage.CLOSED
        )
        assert event.stage == PackInvalidationStage.CLOSED

    def test_emergency_close_requires_participant_follow_up(self, env: LifecycleEnv) -> None:
        """安全管理员不能单人全程闭环：关闭前必须由包参与者推进过处置。"""
        manifest = env.add_pack(build_manifest())
        _sign_and_release(env.workbench, manifest, "maintainer", "reviewer", "releaser")
        env.lifecycle.register_security_admin("sec-admin")
        event, _ = env.lifecycle.emergency_revoke(
            "sec-admin",
            manifest.id,
            manifest.version,
            PackInvalidationTrigger.SECURITY_EVENT,
            "安全事件",
            second_factor="token",
        )
        env.lifecycle.resolve_impact("sec-admin", event.event_id)
        for stage in (
            PackInvalidationStage.IMPACTED_OBJECTS_FOUND,
            PackInvalidationStage.REMEDIATING,
            PackInvalidationStage.REVALIDATING,
        ):
            env.lifecycle.advance_stage("sec-admin", event.event_id, stage)
        with pytest.raises(DomainPackLifecycleError, match="双人复核"):
            env.lifecycle.advance_stage(
                "sec-admin", event.event_id, PackInvalidationStage.CLOSED
            )

    def test_emergency_close_ok_when_participant_advanced(self, env: LifecycleEnv) -> None:
        """包参与者（非安全管理员）推进过处置后，紧急事件可以关闭。"""
        manifest = env.add_pack(build_manifest())
        _sign_and_release(env.workbench, manifest, "maintainer", "reviewer", "releaser")
        env.lifecycle.register_security_admin("sec-admin")
        event, _ = env.lifecycle.emergency_revoke(
            "sec-admin",
            manifest.id,
            manifest.version,
            PackInvalidationTrigger.SECURITY_EVENT,
            "安全事件",
            second_factor="token",
        )
        env.lifecycle.resolve_impact("sec-admin", event.event_id)
        env.lifecycle.advance_stage(
            "sec-admin", event.event_id, PackInvalidationStage.IMPACTED_OBJECTS_FOUND
        )
        env.lifecycle.advance_stage(
            "sec-admin", event.event_id, PackInvalidationStage.REMEDIATING
        )
        env.lifecycle.advance_stage(
            "maintainer", event.event_id, PackInvalidationStage.REVALIDATING
        )
        event = env.lifecycle.advance_stage(
            "sec-admin", event.event_id, PackInvalidationStage.CLOSED
        )
        assert event.stage == PackInvalidationStage.CLOSED

    def test_rollback_confirm_rejects_content_role(self, env: LifecycleEnv) -> None:
        """内容签名角色不能确认回滚；平台发行者不能以其他角色冒充。"""
        v1, v2 = _two_version_env(env)
        env.lifecycle.emergency_revoke(
            "sec-admin",
            v2.id,
            v2.version,
            PackInvalidationTrigger.SECURITY_EVENT,
            "安全事件",
            second_factor="token",
        )
        rollback = env.lifecycle.propose_rollback(
            "maintainer", v2.id, v2.version, "回滚"
        )
        with pytest.raises(DomainPackLifecycleError, match="只允许独立复核者或平台发行者"):
            env.lifecycle.confirm_rollback(
                "maintainer",
                rollback.rollback_id,
                role=ReviewRole.CONTENT,
                conclusion=AttestationConclusion.APPROVE,
            )
        with pytest.raises(DomainPackLifecycleError, match="只允许独立复核者或平台发行者"):
            env.lifecycle.confirm_rollback(
                "releaser",
                rollback.rollback_id,
                role=ReviewRole.CONTENT,
                conclusion=AttestationConclusion.APPROVE,
            )

    def test_execute_rollback_rechecks_target_trust(self, env: LifecycleEnv) -> None:
        """批准与执行之间目标被撤销时，执行被拒绝。"""
        v1, v2 = _two_version_env(env)
        env.lifecycle.emergency_revoke(
            "sec-admin",
            v2.id,
            v2.version,
            PackInvalidationTrigger.RULE_DEFECT,
            "规则缺陷",
            second_factor="token",
        )
        rollback = env.lifecycle.propose_rollback(
            "maintainer", v2.id, v2.version, "回滚"
        )
        env.lifecycle.confirm_rollback(
            "reviewer",
            rollback.rollback_id,
            role=ReviewRole.INDEPENDENT,
            conclusion=AttestationConclusion.APPROVE,
            opinion="通过",
        )
        env.lifecycle.confirm_rollback(
            "releaser",
            rollback.rollback_id,
            role=ReviewRole.PLATFORM,
            conclusion=AttestationConclusion.APPROVE,
            opinion="通过",
        )
        # 执行前目标 1.0.0 被紧急撤销：信任门不满足。
        env.lifecycle.emergency_revoke(
            "sec-admin",
            v1.id,
            v1.version,
            PackInvalidationTrigger.SECURITY_EVENT,
            "目标被撤销",
            second_factor="token",
        )
        with pytest.raises(DomainPackLifecycleError, match="目标已撤销或进入失效控制"):
            env.lifecycle.execute_rollback("releaser", rollback.rollback_id)
