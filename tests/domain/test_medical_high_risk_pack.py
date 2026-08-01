"""T043 医学高风险教育领域包的公开接缝测试。

覆盖研究文档 5.4 的确定性规则：适用/排除范围、PICO 完整性、医学禁止场景
（诊断/处方/剂量/停药/急症分流/个体预后）、关键来源状态未知或证据冲突时
的高置信发布闭锁，以及 H3 联合门（合资格领域专家 + 安全治理联合确认）。
"""

from __future__ import annotations

from bridges.ai.capability_registry import CapabilityRegistry
from bridges.contracts.ai import CapabilityKind, CapabilityRecord
from bridges.domain import DomainPackLoader, DomainPackValidationRuntime
from bridges.domain.medical_high_risk import MedicalHighRiskDomainPack


def _loaded_pack() -> tuple[MedicalHighRiskDomainPack, object]:
    pack = MedicalHighRiskDomainPack()
    loaded = DomainPackLoader(
        capability_registry=_capability_registry(),
        require_capability_contracts=True,
    ).load(pack)
    return pack, loaded


def _capability_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry()
    for name, input_schema in (
        ("pico_completeness_check", "pico/v1"),
        ("risk_expression_check", "risk-expression/v1"),
        ("trial_status_check", "trial-status/v1"),
    ):
        registry.register(
            CapabilityRecord(
                name=name,
                version="1.0.0",
                kind=CapabilityKind.TOOL,
                vendor="test",
                region="local",
                input_schema_version=input_schema,
                output_schema_version="medical-validation/v1",
            )
        )
    return registry


def _trial_claim(**overrides: object) -> dict[str, object]:
    claim: dict[str, object] = {
        "claim_id": "claim.t043.trial",
        "claim_type": "intervention_effect",
        "population": "成人心衰患者 NYHA II-III 级",
        "intervention": "SGLT2 抑制剂",
        "comparator": "安慰剂",
        "outcome": "心血管死亡或心衰住院复合终点",
        "time_window": "24 个月",
        "absolute_effect": 3.2,
        "absolute_effect_unit": "百分点的绝对风险降低",
        "relative_effect": 0.87,
        "evidence_certainty": "high",
        "applicability_limits": "试验人群以外不能外推",
        "trial_id": "NCT01234567",
        "guideline_source": {
            "organization": "示例指南委员会",
            "version": "2026",
            "status": "active",
            "region": "中国",
        },
        "definition_version": "evidence-based-medicine-v1",
    }
    claim.update(overrides)
    return claim


def test_manifest_declares_education_scope_medical_exclusions_and_h3_rule() -> None:
    pack = MedicalHighRiskDomainPack()
    manifest = pack.manifest

    assert "医学知识教育" in "".join(str(item) for item in manifest.scope)
    assert any("个体诊断" in str(item) for item in manifest.exclusions)
    assert any("处方" in str(item) for item in manifest.exclusions)
    assert {"intervention_effect", "guideline_recommendation", "patient_education"} <= set(
        manifest.question_types
    )
    assert any(
        "ClinicalTrials.gov" in requirement
        for policy in manifest.source_policies
        for requirement in policy.evidence_requirements
    )
    h3_rules = [rule for rule in manifest.rules if rule.human_gate == "H3"]
    assert any(rule.rule_id == "medical.individual-care" for rule in h3_rules)
    assert manifest.content_digest
    assert manifest.compatibility.preserves_runtime_contract is True


def test_manifest_explicitly_requires_h3_joint_confirmation() -> None:
    pack = MedicalHighRiskDomainPack()
    manifest = pack.manifest

    declarations = [item for item in manifest.publication_stage_rules if "H3" in str(item)]
    assert declarations, "Manifest 必须声明 H3 联合确认规则"
    stage = declarations[0]
    assert "qualified_domain_expert" in stage.get("requires", [])
    assert "safety_governance" in stage.get("requires", [])


def test_individual_diagnosis_is_blocked_with_medical_prohibition() -> None:
    pack, _ = _loaded_pack()

    result = pack.validate_claim(
        _trial_claim(
            claim_id="claim.individual-diagnosis",
            claim_type="patient_education",
            description="根据你的症状，你就是二型糖尿病",
            individual_instruction="diagnosis",
        ),
        [],
    )

    assert result["status"] == "blocked"
    assert "medical_prohibition" in result["reason_codes"]


def test_prescription_or_dose_adjustment_is_blocked() -> None:
    pack, _ = _loaded_pack()

    prescription = pack.validate_claim(
        _trial_claim(
            claim_id="claim.prescription",
            claim_type="patient_education",
            description="你应该开始服用二甲双胍 500 mg 每日两次",
            individual_instruction="prescription",
        ),
        [],
    )
    dose_change = pack.validate_claim(
        _trial_claim(
            claim_id="claim.dose-adjustment",
            claim_type="patient_education",
            description="你的血压偏高，请把氨氯地平加量到 10 mg",
            individual_instruction="dose_adjustment",
        ),
        [],
    )

    assert prescription["status"] == "blocked"
    assert "medical_prohibition" in prescription["reason_codes"]
    assert dose_change["status"] == "blocked"
    assert "medical_prohibition" in dose_change["reason_codes"]


def test_stop_medication_or_emergency_triage_is_blocked() -> None:
    pack, _ = _loaded_pack()

    stop = pack.validate_claim(
        _trial_claim(
            claim_id="claim.stop-medication",
            claim_type="patient_education",
            description="你可以立即停用阿司匹林，不会有风险",
            individual_instruction="stop_medication",
        ),
        [],
    )
    emergency = pack.validate_claim(
        _trial_claim(
            claim_id="claim.emergency-triage",
            claim_type="patient_education",
            description="胸痛不用去医院，在家观察即可",
            individual_instruction="emergency_triage",
        ),
        [],
    )

    assert stop["status"] == "blocked"
    assert "medical_prohibition" in stop["reason_codes"]
    assert emergency["status"] == "blocked"
    assert "medical_prohibition" in emergency["reason_codes"]


def test_individual_prognosis_is_blocked() -> None:
    pack, _ = _loaded_pack()

    result = pack.validate_claim(
        _trial_claim(
            claim_id="claim.individual-prognosis",
            claim_type="prognosis",
            description="根据你的情况，你只有六个月",
            individual_instruction="individual_prognosis",
        ),
        [],
    )

    assert result["status"] == "blocked"
    assert "medical_prohibition" in result["reason_codes"]


def test_education_content_with_disclaimer_stays_within_scope() -> None:
    """教育信息与医疗决策的边界：群体教育结论 + 免责声明可以进入 verified。"""
    pack, _ = _loaded_pack()

    result = pack.validate_claim(
        _trial_claim(
            claim_id="claim.education.correct",
            description="对符合研究纳入条件的人群，现有证据支持该干预降低复合终点风险，绝对获益取决于基线风险；这不能替代个体诊疗",
            not_individual_advice=True,
        ),
        [
            {
                "evidence_id": "e.guideline.1",
                "relation": "supports",
                "locator": "guideline:2026",
                "lifecycle_status": "active",
            }
        ],
    )

    assert result["status"] == "verified"
    assert result["details"]["validation_report"]["medical_decision_not_made"] is True


def test_unknown_critical_source_status_blocks_high_confidence_publish() -> None:
    pack, _ = _loaded_pack()

    result = pack.validate_claim(
        _trial_claim(claim_id="claim.unknown-source"),
        [
            {
                "evidence_id": "e.guideline.unknown",
                "relation": "supports",
                "locator": "guideline:2026",
                "lifecycle_status": "unknown",
            }
        ],
    )

    assert result["status"] in {"blocked", "needs_human"}
    assert "source_status_unknown" in result["reason_codes"]
    assert result["details"]["validation_report"]["high_confidence_publish_locked"] is True


def test_conflicting_evidence_locks_high_confidence_publish() -> None:
    pack, _ = _loaded_pack()

    result = pack.validate_claim(
        _trial_claim(claim_id="claim.conflict"),
        [
            {
                "evidence_id": "e.trial.1",
                "relation": "supports",
                "locator": "trial:NCT01234567",
                "lifecycle_status": "active",
            },
            {
                "evidence_id": "e.safety.1",
                "relation": "refutes",
                "locator": "safety-communication:2026",
                "lifecycle_status": "active",
            },
        ],
    )

    assert result["status"] == "conflicted"
    assert "evidence_conflict" in result["reason_codes"]


def test_incomplete_pico_is_blocked() -> None:
    pack, _ = _loaded_pack()

    result = pack.validate_claim(
        _trial_claim(
            claim_id="claim.pico-incomplete",
            population=None,
            intervention=None,
            comparator=None,
            outcome=None,
            time_window=None,
        ),
        [],
    )

    assert result["status"] == "blocked"
    assert "pico_incomplete" in result["reason_codes"]


def test_relative_risk_misrepresentation_is_blocked() -> None:
    pack, _ = _loaded_pack()

    result = pack.validate_claim(
        _trial_claim(
            claim_id="claim.relative-risk",
            relative_effect=0.87,
            absolute_effect=None,
            description="该药将心血管死亡风险降低 13%",
        ),
        [],
    )

    assert result["status"] == "blocked"
    assert "relative_risk_misuse" in result["reason_codes"]


def test_withdrawn_critical_source_blocks_high_confidence_publish() -> None:
    """关键来源撤回/撤销时高置信发布闭锁，不得按已验证发布。"""
    pack, _ = _loaded_pack()

    result = pack.validate_claim(
        _trial_claim(claim_id="claim.withdrawn-source"),
        [
            {
                "evidence_id": "e.guideline.withdrawn",
                "relation": "supports",
                "locator": "guideline:2026",
                "lifecycle_status": "withdrawn",
            }
        ],
    )

    assert result["status"] in {"blocked", "needs_human"}
    assert "source_status_unknown" in result["reason_codes"]
    assert result["details"]["validation_report"]["high_confidence_publish_locked"] is True


def test_dose_instruction_without_complete_unit_route_is_blocked() -> None:
    """剂量表述缺少单位或给药途径时阻断，不能作为可执行剂量。"""
    pack, _ = _loaded_pack()

    result = pack.validate_claim(
        _trial_claim(
            claim_id="claim.dose-incomplete",
            claim_type="patient_education",
            description="根据指南，该药推荐剂量为 10 mg 每日一次，具体方案以医生评估为准。",
            dose_value=10,
            dose_unit="mg",
        ),
        [],
    )

    assert result["status"] == "blocked"
    assert "dose_unit_invalid" in result["reason_codes"]


def test_pico_is_checked_per_question_type() -> None:
    """PICO 完整性按问题类型映射字段检查，诊断类缺人群同样阻断。"""
    pack, _ = _loaded_pack()

    result = pack.validate_claim(
        _trial_claim(
            claim_id="claim.diagnostic-pico",
            claim_type="diagnostic_accuracy",
            population=None,
            index_test="高敏肌钙蛋白检测",
            reference_standard="冠状动脉造影",
            outcome="急性心肌梗死诊断",
            time_window="入院 3 小时",
        ),
        [],
    )

    assert result["status"] == "blocked"
    assert "pico_incomplete" in result["reason_codes"]


def test_completed_trial_without_published_result_is_blocked() -> None:
    pack, _ = _loaded_pack()

    result = pack.validate_claim(
        _trial_claim(
            claim_id="claim.trial-no-result",
            claim_type="trial_status",
            description="试验已完成，因此可以宣称该干预有效",
            trial_status="completed",
            results_published=False,
            trial_record={
                "status": "completed",
                "results_available": False,
                "primary_endpoint_changed": True,
            },
        ),
        [],
    )

    assert result["status"] == "blocked"
    assert "trial_result_unpublished" in result["reason_codes"]


def test_individual_care_request_enters_h3_joint_gate_with_confirmation() -> None:
    """个体化诊断/治疗请求必须进入 H3 联合门：合资格专家 + 安全治理联合确认。"""
    pack, _ = _loaded_pack()

    result = pack.validate_claim(
        _trial_claim(
            claim_id="claim.h3-gate",
            claim_type="patient_education",
            description="结合我的检查结果，应该怎么调整我的治疗方案",
            individual_instruction="treatment_plan",
            h3_review_requested=True,
        ),
        [],
    )

    assert result["status"] in {"blocked", "needs_human"}
    assert "h3_joint_gate" in result["reason_codes"]
    gate = result["details"]["validation_report"]["h3_joint_gate"]
    assert gate["required"] is True
    assert "qualified_domain_expert" in gate["required_approvers"]
    assert "safety_governance" in gate["required_approvers"]


def test_runtime_replays_education_and_high_risk_fixtures() -> None:
    pack, loaded = _loaded_pack()

    report = DomainPackValidationRuntime().run(
        loaded,
        fixtures=list(pack.manifest.fixtures),
        run_id="run-t043-medical",
    )

    assert report.status.value in {"passed", "needs_human"}
    results = {result.fixture_id: result for result in report.fixture_results}
    assert results["medical.intervention-effect.correct"].passed is True
    assert results["medical.guideline.correct"].passed is True
    assert results["medical.trial-status.correct"].passed is True
    assert results["medical.patient-education.correct"].passed is True
    assert results["medical.prohibition.individual-diagnosis"].passed is True
    assert results["medical.prohibition.prescription"].passed is True
    assert results["medical.prohibition.dose-adjustment"].passed is True
    assert results["medical.prohibition.stop-medication"].passed is True
    assert results["medical.prohibition.emergency-triage"].passed is True
    assert results["medical.prohibition.individual-prognosis"].passed is True
    assert results["medical.source-status.unknown"].passed is True
    assert results["medical.conflict.evidence"].passed is True
    assert results["medical.pico.incomplete"].passed is True
    assert results["medical.relative-risk.hidden-absolute"].passed is True
    assert results["medical.trial.completed-no-result"].passed is True
    assert results["medical.h3.joint-gate"].passed is True
    assert results["medical.guideline.region-difference"].passed is True
    assert results["medical.source-status.withdrawn"].passed is True
    assert results["medical.dose-unit.incomplete"].passed is True
