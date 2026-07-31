"""医学高风险教育领域包。

该包只支持医学知识教育、证据解释、指南比较、临床试验阅读和就医沟通准备，
并明确排除替代医生作个体诊断、处方、剂量调整、停药、急症分流保证和根据不
完整资料给出个体预后。产品必须清楚区分"教育信息"与"医疗决策"：系统不得
越权执行医疗行为；任何个体化诊断治疗、处方剂量、重大风险比较、公开健康传
播或证据冲突 override 均需合资格临床/循证专家与安全治理责任人联合确认
（H3 联合门）。它不调用网络、模型或未登记的校验工具；PICO、风险表达和
试验状态检查以逻辑能力登记在 Manifest 中，实际执行仍由平台通过受限能力
注册表提供。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from science_companion.contracts.domain import (
    DomainClaimSchema,
    DomainCompatibility,
    DomainEvaluationResult,
    DomainPackManifest,
    DomainRule,
    DomainSourcePolicy,
    DomainWordingPolicy,
    FixtureCase,
    PlatformSafetyFloor,
    ValidatorRequirement,
)
from science_companion.domain.loader import DomainPackLoader

__all__ = [
    "MedicalHighRiskDomainPack",
    "create_medical_high_risk_pack",
]

_QUESTION_TYPES = (
    "intervention_effect",
    "diagnostic_accuracy",
    "prognosis",
    "harm",
    "screening",
    "guideline_recommendation",
    "trial_status",
    "patient_education",
)

# 医学禁止场景：系统不得形成个体诊断、处方、剂量调整、停药、急症分流保证
# 或个体预后，也不能替代专业医疗决策。
_PROHIBITED_INSTRUCTIONS = {
    "diagnosis": "不能代替医生作出个体诊断。",
    "prescription": "不能给出个体处方。",
    "dose_adjustment": "不能给出剂量调整建议。",
    "stop_medication": "不能建议停药或自行加量。",
    "emergency_triage": "不能保证急症分流，紧急症状必须就医。",
    "individual_prognosis": "不能根据不完整资料给出个体预后。",
    "treatment_plan": "不能替代专业医疗决策制定治疗方案。",
}

_FORBIDDEN_PHRASES = (
    "你就是",
    "立即停用",
    "立即停药",
    "自行加量",
    "不用去医院",
    "只有六个月",
    "我给你开",
)

# 可执行剂量指令模式：向特定主体下达服用/加量/减量并带剂量单位的表述。
_DOSE_INSTRUCTION_PATTERN = re.compile(
    r"(?:你应该|你可以|建议你|我建议)[^。；，！？\n]{0,12}"
    r"(?:服用|加量到|减量到|改为)[^。；！？\n]{0,12}"
    r"(?:mg|毫克|g|克|ml|毫升|片|粒|单位|治疗)"
)

# 各问题类型对应的 PICO 形态：干预效果为字面 PICO，其余类型按各自证据结构映射。
_PICO_FIELD_SETS: dict[str, tuple[str, ...]] = {
    "intervention_effect": (
        "population",
        "intervention",
        "comparator",
        "outcome",
        "time_window",
    ),
    "diagnostic_accuracy": (
        "population",
        "index_test",
        "reference_standard",
        "outcome",
        "time_window",
    ),
    "prognosis": ("population", "prognostic_factor", "outcome", "time_window"),
    "harm": ("population", "intervention", "outcome", "time_window"),
    "screening": ("population", "screening_test", "outcome", "time_window"),
}

_H3_REQUIRED_APPROVERS = ["qualified_domain_expert", "safety_governance"]

_REASON_MESSAGES = {
    "claim_schema_incomplete": (
        "医学 Claim 缺少人群、干预、比较、结局、时间窗、证据确信度或适用限制等必填字段。"
    ),
    "pico_incomplete": "PICO 字段（人群、干预/暴露、比较、结局、时间窗）不完整，无法评价证据。",
    "medical_prohibition": (
        "医学高风险内容不能形成个体诊断、处方、剂量调整、停药、急症分流保证或个体预后，"
        "不能替代专业医疗决策。"
    ),
    "h3_joint_gate": (
        "个体化诊断治疗、处方剂量、重大风险比较、公开健康传播或证据冲突 override "
        "需要合资格领域专家与安全治理责任人联合确认。"
    ),
    "source_status_unknown": (
        "关键来源（指南、试验注册或安全通信）状态未知时，高置信发布闭锁。"
    ),
    "evidence_conflict": "同一医学命题同时存在支持与反驳证据，不得单边发布。",
    "relative_risk_misuse": "相对风险不能隐藏绝对效应和基线风险。",
    "trial_result_unpublished": "试验完成不表示结果已发布，注册终点变化必须可见。",
    "guideline_region_difference": (
        "不同地区指南不得多数表决，必须说明人群、资源和价值差异后人工裁决。"
    ),
    "dose_unit_invalid": "剂量或单位不完整，不能作为可执行剂量表述。",
}


class MedicalHighRiskDomainPack:
    """面向医学高风险教育的可重放领域包实现。"""

    def __init__(self, manifest: DomainPackManifest | None = None) -> None:
        self.manifest = manifest or _build_manifest()

    def classify_question(self, question_context: Any) -> str:
        data = _as_dict(question_context)
        requested = str(data.get("question_type", "")).strip()
        if requested in _QUESTION_TYPES:
            return requested
        if data.get("trial_id") is not None or data.get("trial_status") is not None:
            return "trial_status"
        if data.get("guideline_source") is not None:
            return "guideline_recommendation"
        if data.get("screening_test") is not None or data.get("screen") is not None:
            return "screening"
        if data.get("harm") is not None or data.get("adverse") is not None:
            return "harm"
        if data.get("accuracy") is not None or data.get("sensitivity") is not None:
            return "diagnostic_accuracy"
        if data.get("population") is not None and data.get("intervention") is not None:
            return "intervention_effect"
        if data.get("prognosis") is not None or data.get("survival") is not None:
            return "prognosis"
        return "patient_education"

    def plan_sources(self, question_type: str, risk_tier: str) -> dict[str, Any]:
        policy = next(
            (item for item in self.manifest.source_policies if question_type in item.applies_to),
            None,
        )
        return {
            "question_type": question_type,
            "risk_tier": risk_tier,
            "policy_id": policy.policy_id if policy else None,
            "source_order": [
                "who_guideline_handbook",
                "systematic_review_or_guideline",
                "clinicaltrials_gov",
                "direct_clinical_study",
                "regulatory_safety_communication",
                "abstract_news_forum",
            ],
            "network_performed": False,
        }

    def normalize_metadata(self, adapter_record: Any) -> dict[str, Any]:
        data = _as_dict(adapter_record)
        return {
            "canonical_id": data.get("canonical_id", data.get("id")),
            "title": data.get("title"),
            "publisher": data.get("publisher", data.get("organization")),
            "version": data.get("version", data.get("edition", data.get("release_date"))),
            "release_date": data.get("release_date"),
            "region": data.get("region"),
            "status": data.get("status", "unknown"),
            "updated_at": data.get("updated_at"),
            "superseded": data.get("superseded", "unknown"),
            "source_role": data.get("source_role", "systematic_review_or_guideline"),
            "content_hash": data.get("content_hash"),
            "metadata_only": bool(data.get("metadata_only", False)),
        }

    def resolve_version_status(self, records: Any) -> dict[str, Any]:
        normalized = [_as_dict(record) for record in (records or [])]
        statuses = {str(record.get("status", "unknown")) for record in normalized}
        if statuses & {"retracted", "withdrawn", "superseded", "stale"}:
            status = "stale_or_updated"
        elif normalized and statuses == {"active"}:
            status = "active"
        else:
            status = "unknown"
        return {
            "status": status,
            "records": normalized,
            "preserved_unknown": "unknown" in statuses or not normalized,
        }

    def parse_domain_structure(self, document: Any) -> dict[str, Any]:
        data = _as_dict(document)
        return {
            "population": data.get("population"),
            "intervention": data.get("intervention"),
            "comparator": data.get("comparator"),
            "outcome": data.get("outcome"),
            "time_window": data.get("time_window"),
            "guideline_source": data.get("guideline_source"),
            "trial_record": data.get("trial_record"),
            "source_locator": data.get("source_locator", data.get("locator")),
        }

    def extract_claim_schema(self, content: Any) -> dict[str, Any]:
        data = _as_dict(content)
        claim = data.get("claim")
        if claim is not None:
            return _as_dict(claim)
        return {
            key: data[key]
            for key in (
                "claim_id",
                "claim_type",
                "population",
                "intervention",
                "comparator",
                "outcome",
                "time_window",
                "absolute_effect",
                "relative_effect",
                "evidence_certainty",
                "applicability_limits",
                "guideline_source",
                "trial_id",
                "trial_status",
                "trial_record",
                "description",
                "individual_instruction",
            )
            if key in data
        }

    def assess_evidence(self, claim: Any, evidence_set: Any) -> dict[str, Any]:
        evidence = [_as_dict(item) for item in (evidence_set or [])]
        relations = {str(item.get("relation", "unknown")) for item in evidence}
        if "supports" in relations and "refutes" in relations:
            status = "conflicted"
        elif any(
            str(item.get("lifecycle_status", item.get("status", "unknown")))
            == "unknown"
            for item in evidence
        ):
            status = "unknown"
        elif any(
            str(item.get("lifecycle_status", item.get("status", "active")))
            in {"retracted", "withdrawn", "superseded", "stale"}
            for item in evidence
        ):
            status = "blocked"
        elif evidence and "supports" in relations:
            status = "verified"
        else:
            status = "unknown"
        claim_data = _as_dict(claim)
        return {
            "status": status,
            "claim_id": _claim_id(claim),
            "evidence_ids": _evidence_ids(evidence),
            "dimensions": {
                "pico_completeness": _pico_dimension(claim_data),
                "absolute_effect_declared": (
                    "complete"
                    if claim_data.get("absolute_effect") is not None
                    else "missing"
                ),
                "source_lifecycle": "active" if status == "verified" else status,
                "applicability": (
                    "limited" if claim_data.get("applicability_limits") else "missing"
                ),
            },
            "reason": (
                "医学教育 Claim 优先依据 PICO 完整性、偏倚/不一致/间接/不精确评价、"
                "绝对效应与基线风险、来源状态和地区适用性；来源声誉与试验完成状态"
                "不替代已发布的结果证据。"
            ),
        }

    def detect_conflicts(self, claim_set: Any, evidence_set: Any) -> list[dict[str, Any]]:
        claims = [_as_dict(item) for item in (claim_set or [])]
        evidence = [_as_dict(item) for item in (evidence_set or [])]
        conflicts: list[dict[str, Any]] = []
        evidence_relations = {str(item.get("relation")) for item in evidence}
        if "supports" in evidence_relations and "refutes" in evidence_relations:
            conflicts.append(
                {
                    "conflict_id": "medical-conflict:evidence",
                    "type": "true_disagreement",
                    "claim_ids": [_claim_id(claim) for claim in claims],
                    "evidence_ids": _evidence_ids(evidence),
                    "status": "open",
                    "reason": "同一医学命题同时存在支持与反驳证据，不得单边发布。",
                }
            )
        return conflicts

    def constrain_wording(self, assessment: Any, audience: str, genre: str) -> dict[str, Any]:
        data = _as_dict(assessment)
        status = str(data.get("status", "unknown"))
        if status == "verified":
            return {
                "strength": "qualified",
                "wording_ceiling": "qualified",
                "medical_education_only": True,
                "allowed": (
                    "对符合研究纳入条件的人群，现有证据支持……，绝对获益取决于"
                    "基线风险；这不能替代个体诊疗。"
                ),
                "required_disclosures": [
                    "人群与适用条件",
                    "绝对效应与基线风险",
                    "证据确信度",
                    "地区适用性",
                    "不替代个体诊疗",
                ],
                "forbidden": ["你就是某病", "一定有效/无风险", "立即停药或自行加量"],
                "audience": audience,
                "genre": genre,
            }
        if status in {"conflicted", "unknown"}:
            return {
                "strength": "unassessable",
                "wording_ceiling": "unassessable",
                "medical_education_only": True,
                "allowed": "现有证据冲突或来源状态未知，不能给出高置信结论。",
                "required_disclosures": ["保留未决冲突或来源状态", "需要人工复核"],
                "forbidden": ["已证实", "一定有效", "无风险"],
                "audience": audience,
                "genre": genre,
            }
        return {
            "strength": "none",
            "wording_ceiling": "none",
            "medical_education_only": True,
            "allowed": "该内容不能形成诊断、处方或替代专业医疗决策；系统不得越权执行医疗行为。",
            "required_disclosures": ["医学教育边界", "人工医疗决策", "紧急情况就医"],
            "forbidden": ["诊断", "处方", "剂量", "停药", "个体预后"],
            "audience": audience,
            "genre": genre,
        }

    def validate_claim(self, claim: Any, evidence_set: Any) -> dict[str, Any]:
        data = _as_dict(claim)
        evidence = [_as_dict(item) for item in (evidence_set or [])]
        question_type = str(
            data.get("claim_type", data.get("question_type", "patient_education"))
        )
        if question_type not in _QUESTION_TYPES:
            question_type = "patient_education"

        reasons: list[str] = []
        checks: list[dict[str, Any]] = []
        rule_ids = _rule_ids_for(question_type)
        validator_ids = _validator_ids_for(question_type)

        self._validate_claim_shape(data, question_type, reasons, checks)
        self._validate_pico(data, question_type, reasons, checks)
        self._validate_prohibition(data, reasons, checks)
        self._validate_h3_joint_gate(data, reasons, checks)
        self._validate_source_status(data, evidence, reasons, checks)
        self._validate_evidence_conflict(evidence, reasons, checks)
        self._validate_risk_expression(data, reasons, checks)
        self._validate_dose_unit(data, reasons, checks)
        self._validate_trial_status(data, question_type, reasons, checks)
        self._validate_guideline_region(data, evidence, reasons, checks)

        unique_reasons = _unique(reasons)
        if "medical_prohibition" in unique_reasons:
            status = "blocked"
        elif "h3_joint_gate" in unique_reasons:
            status = "needs_human"
        elif "evidence_conflict" in unique_reasons:
            status = "conflicted"
        elif "guideline_region_difference" in unique_reasons:
            status = "needs_human"
        elif reasons:
            status = "blocked"
        else:
            status = "verified"

        claim_id = _claim_id(data)
        evidence_ids = _evidence_ids(evidence)
        citation_ids = _citation_ids(data, evidence)
        report_id = _stable_id(
            "medical-validation",
            {"claim": claim_id, "status": status},
        )
        upstream_fact_lock = data.get("fact_lock_set", data.get("fact_lock"))
        fact_lock_reference = {
            "claim_id": claim_id,
            "evidence_ids": evidence_ids,
            "citation_ids": citation_ids,
            "field_paths": sorted(data),
            "upstream_lock_present": upstream_fact_lock is not None,
            "owner": "platform.science.fact_lock",
        }
        h3_joint_gate = {
            "required": "h3_joint_gate" in unique_reasons
            or "medical_prohibition" in unique_reasons,
            "required_approvers": list(_H3_REQUIRED_APPROVERS),
            "automation_boundary": "blocked",
            "failure_semantics": "blocked_and_no_ordinary_education_fallback",
        }
        details = {
            "claim": {
                **data,
                "claim_id": claim_id,
                "claim_type": question_type,
                "text": data.get("text", data.get("description")),
                "definition_version": data.get("definition_version"),
                "population": data.get("population"),
            },
            "evidence": evidence,
            "evidence_ids": evidence_ids,
            "fact_lock_reference": fact_lock_reference,
            "fact_lock": upstream_fact_lock,
            "fact_lock_set": upstream_fact_lock,
            "validation_report": {
                "report_id": report_id,
                "status": status,
                "claim_id": claim_id,
                "reason_codes": unique_reasons,
                "evidence_ids": evidence_ids,
                "citation_ids": citation_ids,
                "fact_lock_set_id": _as_dict(upstream_fact_lock).get("set_id")
                if upstream_fact_lock is not None
                else None,
                "checks": checks,
                "medical_education_only": True,
                "medical_decision_not_made": status == "verified",
                "high_confidence_publish_locked": (
                    "source_status_unknown" in unique_reasons
                    or "evidence_conflict" in unique_reasons
                ),
                "h3_joint_gate": h3_joint_gate,
                "provenance": {
                    "pack_id": self.manifest.id,
                    "pack_version": self.manifest.version,
                    "validator_ids": validator_ids,
                    "guideline_source": data.get("guideline_source"),
                    "trial_id": data.get("trial_id"),
                    "definition_version": data.get("definition_version"),
                },
            },
        }
        return {
            "status": status,
            "reason_codes": unique_reasons,
            "rule_ids": _unique(rule_ids),
            "validator_ids": _unique(validator_ids),
            "details": details,
        }

    def evaluate(
        self,
        run_artifacts: dict[str, Any],
        fixture_set: list[FixtureCase],
    ) -> DomainEvaluationResult:
        validation = _as_dict(run_artifacts.get("validation"))
        status = str(validation.get("status", "blocked"))
        wording = dict(_as_dict(run_artifacts.get("wording")))
        wording.update(self.constrain_wording({"status": status}, "general", "explanation"))
        wording.update(
            {
                "medical_education_only": True,
                "validation_status": status,
                "validation_report_id": _as_dict(validation.get("details"))
                .get("validation_report", {})
                .get("report_id"),
            }
        )
        details = _as_dict(validation.get("details"))
        return DomainEvaluationResult(
            status=status,
            reason_codes=list(validation.get("reason_codes", [])),
            rule_ids=list(validation.get("rule_ids", [])),
            validator_ids=list(validation.get("validator_ids", [])),
            wording=wording,
            details={
                "claim": details.get("claim", {}),
                "evidence": details.get("evidence", []),
                "citation_ids": details.get("validation_report", {}).get("citation_ids", []),
                "fact_lock": details.get("fact_lock", {}),
                "fact_lock_set": details.get("fact_lock_set", {}),
                "fact_lock_reference": details.get("fact_lock_reference", {}),
                "validation_report": details.get("validation_report", {}),
                "fixture_ids": [fixture.fixture_id for fixture in fixture_set],
                "medical_decision_not_made": status == "verified",
                "high_confidence_publish_locked": status in {
                    "blocked",
                    "conflicted",
                },
            },
        )

    def _validate_claim_shape(
        self,
        claim: Mapping[str, Any],
        question_type: str,
        reasons: list[str],
        checks: list[dict[str, Any]],
    ) -> None:
        required: dict[str, tuple[str, ...]] = {
            "intervention_effect": (
                "claim_id",
                "population",
                "intervention",
                "comparator",
                "outcome",
                "time_window",
                "absolute_effect",
                "relative_effect",
                "evidence_certainty",
                "applicability_limits",
                "definition_version",
            ),
            "diagnostic_accuracy": (
                "claim_id",
                "population",
                "index_test",
                "reference_standard",
                "outcome",
                "time_window",
                "evidence_certainty",
                "applicability_limits",
                "definition_version",
            ),
            "prognosis": (
                "claim_id",
                "population",
                "prognostic_factor",
                "outcome",
                "time_window",
                "evidence_certainty",
                "applicability_limits",
                "definition_version",
            ),
            "harm": (
                "claim_id",
                "population",
                "intervention",
                "outcome",
                "time_window",
                "absolute_effect",
                "relative_effect",
                "evidence_certainty",
                "applicability_limits",
                "definition_version",
            ),
            "screening": (
                "claim_id",
                "population",
                "screening_test",
                "outcome",
                "time_window",
                "evidence_certainty",
                "applicability_limits",
                "definition_version",
            ),
            "guideline_recommendation": (
                "claim_id",
                "guideline_source",
                "recommendation_strength",
                "definition_version",
            ),
            "trial_status": (
                "claim_id",
                "trial_id",
                "trial_status",
                "trial_record",
                "definition_version",
            ),
            "patient_education": (
                "claim_id",
                "description",
                "definition_version",
            ),
        }
        missing = [
            field for field in required[question_type] if claim.get(field) in (None, "", [])
        ]
        if missing:
            reasons.append("claim_schema_incomplete")
            checks.append(
                _check(
                    "claim_schema",
                    False,
                    f"缺少必填字段：{', '.join(missing)}。",
                )
            )
        else:
            checks.append(_check("claim_schema", True, "医学 Claim 必填字段齐全。"))

    def _validate_pico(
        self,
        claim: Mapping[str, Any],
        question_type: str,
        reasons: list[str],
        checks: list[dict[str, Any]],
    ) -> None:
        pico_fields = _PICO_FIELD_SETS.get(question_type)
        if pico_fields is None:
            checks.append(_check("pico_completeness", True, "该问题类型不要求完整 PICO。"))
            return
        missing = [
            field for field in pico_fields if claim.get(field) in (None, "", [])
        ]
        if missing:
            reasons.append("pico_incomplete")
            checks.append(
                _check(
                    "pico_completeness",
                    False,
                    f"{_REASON_MESSAGES['pico_incomplete']} 缺失：{', '.join(missing)}。",
                )
            )
        else:
            checks.append(_check("pico_completeness", True, "PICO 字段完整。"))

    def _validate_prohibition(
        self,
        claim: Mapping[str, Any],
        reasons: list[str],
        checks: list[dict[str, Any]],
    ) -> None:
        if self._prohibition_triggered(claim):
            reasons.append("medical_prohibition")
        if "medical_prohibition" in reasons:
            checks.append(
                _check(
                    "medical_prohibition",
                    False,
                    _REASON_MESSAGES["medical_prohibition"],
                )
            )
        else:
            checks.append(
                _check("medical_prohibition", True, "内容保持在医学教育边界内。")
            )

    def _prohibition_triggered(self, claim: Mapping[str, Any]) -> bool:
        instruction = str(claim.get("individual_instruction", "")).strip()
        description = str(claim.get("description", ""))
        return instruction in _PROHIBITED_INSTRUCTIONS or _forbidden_phrase_hit(description)

    def _validate_h3_joint_gate(
        self,
        claim: Mapping[str, Any],
        reasons: list[str],
        checks: list[dict[str, Any]],
    ) -> None:
        if claim.get("h3_review_requested") is True or self._prohibition_triggered(claim):
            reasons.append("h3_joint_gate")
        if "h3_joint_gate" in reasons:
            checks.append(
                _check(
                    "h3_joint_gate",
                    False,
                    _REASON_MESSAGES["h3_joint_gate"],
                )
            )
        else:
            checks.append(
                _check("h3_joint_gate", True, "未触发 H3 联合门。")
            )

    def _validate_source_status(
        self,
        claim: Mapping[str, Any],
        evidence: Sequence[Mapping[str, Any]],
        reasons: list[str],
        checks: list[dict[str, Any]],
    ) -> None:
        guideline = _as_dict(claim.get("guideline_source", {}))
        guideline_status = str(guideline.get("status", "unknown"))
        if guideline and guideline_status in {"unknown", "withdrawn", "retracted"}:
            reasons.append("source_status_unknown")
        if any(
            str(item.get("lifecycle_status", item.get("status", "unknown")))
            in {"unknown", "withdrawn", "retracted", "superseded", "stale"}
            for item in evidence
        ):
            reasons.append("source_status_unknown")
        if "source_status_unknown" in reasons:
            checks.append(
                _check(
                    "source_status",
                    False,
                    _REASON_MESSAGES["source_status_unknown"],
                )
            )
        else:
            checks.append(_check("source_status", True, "关键来源状态已知且有效。"))

    def _validate_evidence_conflict(
        self,
        evidence: Sequence[Mapping[str, Any]],
        reasons: list[str],
        checks: list[dict[str, Any]],
    ) -> None:
        relations = {str(item.get("relation")) for item in evidence}
        if "supports" in relations and "refutes" in relations:
            reasons.append("evidence_conflict")
            checks.append(
                _check(
                    "evidence_conflict",
                    False,
                    _REASON_MESSAGES["evidence_conflict"],
                )
            )
        else:
            checks.append(_check("evidence_conflict", True, "证据关系无冲突。"))

    def _validate_risk_expression(
        self,
        claim: Mapping[str, Any],
        reasons: list[str],
        checks: list[dict[str, Any]],
    ) -> None:
        relative_effect = claim.get("relative_effect")
        absolute_effect = claim.get("absolute_effect")
        if relative_effect is not None and absolute_effect is None:
            reasons.append("relative_risk_misuse")
            checks.append(
                _check(
                    "risk_expression",
                    False,
                    _REASON_MESSAGES["relative_risk_misuse"],
                )
            )
        else:
            checks.append(_check("risk_expression", True, "风险表达包含绝对效应与基线风险。"))

    def _validate_dose_unit(
        self,
        claim: Mapping[str, Any],
        reasons: list[str],
        checks: list[dict[str, Any]],
    ) -> None:
        dose_value = claim.get("dose_value")
        dose_unit = claim.get("dose_unit")
        dose_route = claim.get("dose_route")
        if dose_value is not None and (dose_unit in (None, "") or dose_route in (None, "")):
            reasons.append("dose_unit_invalid")
            checks.append(
                _check("dose_unit", False, _REASON_MESSAGES["dose_unit_invalid"])
            )
        else:
            checks.append(_check("dose_unit", True, "剂量表述不构成可执行处方。"))

    def _validate_trial_status(
        self,
        claim: Mapping[str, Any],
        question_type: str,
        reasons: list[str],
        checks: list[dict[str, Any]],
    ) -> None:
        if question_type != "trial_status":
            checks.append(_check("trial_status", True, "该问题类型不涉及试验状态。"))
            return
        trial_record = _as_dict(claim.get("trial_record", {}))
        trial_status = str(
            trial_record.get("status", claim.get("trial_status", ""))
        ).lower()
        if trial_status == "completed" and (
            claim.get("results_published") is False
            or trial_record.get("results_available") is False
        ):
            reasons.append("trial_result_unpublished")
            checks.append(
                _check(
                    "trial_status",
                    False,
                    _REASON_MESSAGES["trial_result_unpublished"],
                )
            )
        elif trial_record.get("primary_endpoint_changed") is True:
            reasons.append("trial_result_unpublished")
            checks.append(
                _check(
                    "trial_status",
                    False,
                    "注册终点变化必须可见，不能按原终点宣称结果。",
                )
            )
        else:
            checks.append(_check("trial_status", True, "试验状态与已发布结果一致。"))

    def _validate_guideline_region(
        self,
        claim: Mapping[str, Any],
        evidence: Sequence[Mapping[str, Any]],
        reasons: list[str],
        checks: list[dict[str, Any]],
    ) -> None:
        guideline = _as_dict(claim.get("guideline_source", {}))
        claim_region = str(guideline.get("region", ""))
        regions = {
            str(_as_dict(item).get("region", ""))
            for item in evidence
            if _as_dict(item).get("region")
        }
        if claim_region and regions and regions != {claim_region}:
            reasons.append("guideline_region_difference")
            checks.append(
                _check(
                    "guideline_region",
                    False,
                    _REASON_MESSAGES["guideline_region_difference"],
                )
            )
        else:
            checks.append(_check("guideline_region", True, "指南地区适用性一致。"))


def create_medical_high_risk_pack() -> MedicalHighRiskDomainPack:
    """创建医学高风险教育领域包。"""
    return MedicalHighRiskDomainPack()


def _build_manifest() -> DomainPackManifest:
    fixture_ids = [
        "medical.intervention-effect.correct",
        "medical.guideline.correct",
        "medical.trial-status.correct",
        "medical.patient-education.correct",
        "medical.prohibition.individual-diagnosis",
        "medical.prohibition.prescription",
        "medical.prohibition.dose-adjustment",
        "medical.prohibition.stop-medication",
        "medical.prohibition.emergency-triage",
        "medical.prohibition.individual-prognosis",
        "medical.source-status.unknown",
        "medical.conflict.evidence",
        "medical.pico.incomplete",
        "medical.relative-risk.hidden-absolute",
        "medical.trial.completed-no-result",
        "medical.h3.joint-gate",
        "medical.guideline.region-difference",
        "medical.diagnostic-accuracy.correct",
        "medical.harm.correct",
        "medical.screening.correct",
        "medical.source-status.withdrawn",
        "medical.dose-unit.incomplete",
    ]
    source_policy = DomainSourcePolicy(
        policy_id="medical.authoritative-sources",
        applies_to=list(_QUESTION_TYPES),
        evidence_requirements=[
            "WHO 指南制定手册的发布机构、版本、发布日期和适用地区",
            "ClinicalTrials.gov API 的 NCT 标识、状态和历史版本（注册记录不等于结果证据）",
            "系统综述/指南、直接临床研究和监管安全通信的版本化定位",
        ],
        allowed_source_roles=[
            "who_guideline_handbook",
            "systematic_review_or_guideline",
            "clinicaltrials_gov",
            "direct_clinical_study",
            "regulatory_safety_communication",
            "abstract_news_forum",
        ],
        on_failure="block",
    )
    claim_schema = DomainClaimSchema(
        schema_id="medical.claim.v1",
        applies_to=list(_QUESTION_TYPES),
        required_fields=[
            "claim_id",
            "claim_type",
            "population",
            "intervention_or_exposure",
            "comparator",
            "outcome",
            "time_window",
            "absolute_and_relative_effect",
            "evidence_certainty",
            "applicability_limits",
            "definition_version",
        ],
        evidence_requirements=[
            "guideline_version_and_status",
            "trial_registration_or_result",
            "absolute_effect_and_baseline_risk",
        ],
        wording_policy_ids=["medical.wording.v1"],
        unknown_behavior="block",
    )
    wording = DomainWordingPolicy(
        policy_id="medical.wording.v1",
        applies_to=list(_QUESTION_TYPES),
        allowed_statuses=[
            "verified",
            "qualified",
            "needs_human",
            "conflicted",
            "blocked",
        ],
        forbidden_strengths=[
            "individual_diagnosis",
            "prescription_or_dose",
            "stop_medication",
            "emergency_triage_guarantee",
            "individual_prognosis",
        ],
        required_disclosures=[
            "人群与适用条件",
            "绝对效应与基线风险",
            "证据确信度",
            "地区适用性",
            "不替代个体诊疗",
        ],
        requires_human_gate=True,
    )
    rules = [
        DomainRule(
            rule_id="medical.individual-care",
            applies_to=list(_QUESTION_TYPES),
            explanation=(
                "医学高风险内容不能形成个体诊断、处方、剂量调整、停药、急症分流"
                "保证或个体预后，不能替代专业医疗决策；任何个体化处理需要合资格"
                "领域专家与安全治理责任人联合确认。"
            ),
            fixture_ids=[
                "medical.prohibition.individual-diagnosis",
                "medical.prohibition.prescription",
                "medical.prohibition.dose-adjustment",
                "medical.prohibition.stop-medication",
                "medical.prohibition.emergency-triage",
                "medical.prohibition.individual-prognosis",
                "medical.h3.joint-gate",
                "medical.patient-education.correct",
            ],
            human_gate="H3",
        ),
        DomainRule(
            rule_id="medical.pico.completeness",
            applies_to=[
                "intervention_effect",
                "diagnostic_accuracy",
                "prognosis",
                "harm",
                "screening",
            ],
            explanation="PICO 字段（人群、干预/暴露、比较、结局、时间窗）必须完整。",
            fixture_ids=[
                "medical.intervention-effect.correct",
                "medical.pico.incomplete",
            ],
        ),
        DomainRule(
            rule_id="medical.risk.expression",
            applies_to=[
                "intervention_effect",
                "harm",
                "patient_education",
                "guideline_recommendation",
            ],
            explanation="相对风险不能隐藏绝对效应和基线风险。",
            fixture_ids=[
                "medical.relative-risk.hidden-absolute",
            ],
        ),
        DomainRule(
            rule_id="medical.trial.status",
            applies_to=["trial_status", "intervention_effect"],
            explanation="试验完成不表示结果已发布，注册终点变化必须可见。",
            fixture_ids=[
                "medical.trial-status.correct",
                "medical.trial.completed-no-result",
            ],
        ),
        DomainRule(
            rule_id="medical.source.status",
            applies_to=list(_QUESTION_TYPES),
            explanation="关键来源（指南、试验注册或安全通信）状态未知、撤回或失效时高置信发布闭锁。",
            fixture_ids=[
                "medical.source-status.unknown",
                "medical.source-status.withdrawn",
                "medical.intervention-effect.correct",
            ],
        ),
        DomainRule(
            rule_id="medical.dose.unit",
            applies_to=["patient_education", "guideline_recommendation"],
            explanation="可执行剂量表述必须包含完整单位与给药途径，缺失时阻断。",
            fixture_ids=[
                "medical.dose-unit.incomplete",
            ],
        ),
        DomainRule(
            rule_id="medical.evidence.conflict",
            applies_to=list(_QUESTION_TYPES),
            explanation="支持与反驳证据并存时进入 conflicted，不得单边发布。",
            fixture_ids=[
                "medical.conflict.evidence",
            ],
        ),
        DomainRule(
            rule_id="medical.guideline.region",
            applies_to=["guideline_recommendation", "patient_education"],
            explanation="不同地区指南不得多数表决，必须说明人群、资源和价值差异后人工裁决。",
            fixture_ids=[
                "medical.guideline.correct",
                "medical.guideline.region-difference",
            ],
        ),
    ]
    validators = [
        ValidatorRequirement(
            validator_id="validator.medical.pico",
            capability_name="pico_completeness_check",
            capability_version="1.0.0",
            input_schema_version="pico/v1",
            output_schema_version="medical-validation/v1",
            fixture_ids=[
                "medical.intervention-effect.correct",
                "medical.pico.incomplete",
            ],
        ),
        ValidatorRequirement(
            validator_id="validator.medical.risk",
            capability_name="risk_expression_check",
            capability_version="1.0.0",
            input_schema_version="risk-expression/v1",
            output_schema_version="medical-validation/v1",
            fixture_ids=[
                "medical.relative-risk.hidden-absolute",
            ],
        ),
        ValidatorRequirement(
            validator_id="validator.medical.trial",
            capability_name="trial_status_check",
            capability_version="1.0.0",
            input_schema_version="trial-status/v1",
            output_schema_version="medical-validation/v1",
            fixture_ids=[
                "medical.trial-status.correct",
                "medical.trial.completed-no-result",
            ],
        ),
    ]
    fixtures = _build_fixtures()
    manifest = DomainPackManifest(
        id="medical.high-risk-education",
        version="1.0.0",
        platform_api=">=1.0,<2.0",
        pack_api="domain-pack/v1",
        scope=[
            "医学知识教育",
            "证据解释与指南比较",
            "临床试验阅读",
            "就医沟通准备",
        ],
        exclusions=[
            "替代医生作个体诊断",
            "处方、剂量调整或停药建议",
            "急症分流保证",
            "根据不完整资料给出个体预后",
            "替代专业医疗决策",
            "公开健康传播中的个体化断言",
        ],
        languages=["zh-CN", "en"],
        disciplines=["medicine", "evidence_based_medicine", "clinical_education"],
        risk_tiers=["general_education"],
        question_types=list(_QUESTION_TYPES),
        source_policies=[source_policy],
        source_adapters=[
            {
                "adapter_id": "medical.reference.snapshot",
                "authority": "guideline_body_or_registry",
                "status_fields": [
                    "version",
                    "release_date",
                    "region",
                    "lifecycle_status",
                    "updated_at",
                    "content_hash",
                ],
                "failure_semantics": "preserve_unknown_and_block_high_risk",
            }
        ],
        identifier_rules=[
            {
                "rule_id": "medical.guideline-id",
                "required": ["organization", "version", "region"],
            },
            {
                "rule_id": "medical.trial-id",
                "required": ["trial_id", "trial_status"],
            },
        ],
        publication_stage_rules=[
            {
                "stage": "h3_joint_gate",
                "gate_level": "H3",
                "rule": "medical.individual-care",
                "trigger": "individual_care_or_risk_boundary_change",
                "requires": ["qualified_domain_expert", "safety_governance"],
                "failure_semantics": "blocked_and_no_ordinary_education_fallback",
            }
        ],
        evidence_dimensions=[
            {"id": "pico_completeness", "values": ["complete", "incomplete", "unknown"]},
            {"id": "absolute_effect_declared", "values": ["complete", "missing"]},
            {"id": "source_lifecycle", "values": ["active", "unknown", "stale"]},
            {"id": "applicability", "values": ["limited", "missing"]},
        ],
        certainty_mappings=[
            {
                "when": "pico_complete_and_sources_active_and_no_conflict",
                "status": "verified",
            },
            {"when": "source_status_unknown", "status": "blocked"},
            {"when": "supports_and_refutes", "status": "conflicted"},
            {"when": "individual_care_request", "status": "blocked_or_needs_human"},
        ],
        wording_policy=[wording],
        claim_schemas=[claim_schema],
        unit_and_formula_rules=[
            {
                "rule_id": "medical.dose.unit",
                "dose_requires_unit_and_route": True,
                "missing_dose_fields": "block",
            }
        ],
        ontologies=[
            {
                "ontology_id": "medical.pico.v1",
                "terms": [
                    "population",
                    "intervention",
                    "comparator",
                    "outcome",
                    "time_window",
                ],
            },
            {
                "ontology_id": "medical.evidence-state.v1",
                "states": ["verified", "blocked", "needs_human", "conflicted"],
            },
        ],
        tools=[
            {"capability_name": "pico_completeness_check", "sandbox": True, "network": False},
            {"capability_name": "risk_expression_check", "sandbox": True, "network": False},
            {"capability_name": "trial_status_check", "sandbox": True, "network": False},
        ],
        rules=rules,
        validators=validators,
        conflict_rules=[
            {
                "rule_id": "medical.conflict.evidence",
                "when": "supports_and_refutes",
                "status": "conflicted",
            },
        ],
        fixtures=fixtures,
        evaluation_sets=[
            {
                "set_id": "medical.t043.core",
                "fixture_ids": fixture_ids,
                "requires_trace": True,
            }
        ],
        compatibility=DomainCompatibility(
            compatible_with=["1.0.0"],
            preserves_runtime_contract=True,
            requires_revalidation=True,
        ),
        content_files={"rules/medical-high-risk-v1.json": "embedded"},
        build_provenance={"builder": "science-companion", "source": "T043", "reproducible": True},
        platform_floor=PlatformSafetyFloor(),
    )
    manifest.content_digest = DomainPackLoader.manifest_digest(manifest)
    return manifest


def _build_fixtures() -> list[FixtureCase]:
    trial_base: dict[str, Any] = {
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
    return [
        _fixture(
            "medical.intervention-effect.correct",
            "intervention_effect",
            {
                **trial_base,
                "claim_id": "claim.t043.intervention.correct",
                "description": (
                    "对符合研究纳入条件的人群，现有证据支持该干预降低复合终点"
                    "风险，绝对获益取决于基线风险；这不能替代个体诊疗"
                ),
                "not_individual_advice": True,
            },
            "verified",
            [
                "medical.pico.completeness",
                "medical.source.status",
                "medical.individual-care",
            ],
            ["validator.medical.pico", "validator.medical.trial"],
            evidence_relations=["supports"],
        ),
        _fixture(
            "medical.guideline.correct",
            "guideline_recommendation",
            {
                "claim_id": "claim.t043.guideline.correct",
                "claim_type": "guideline_recommendation",
                "guideline_source": {
                    "organization": "示例指南委员会",
                    "version": "2026",
                    "status": "active",
                    "region": "中国",
                },
                "recommendation_strength": "conditional",
                "definition_version": "evidence-based-medicine-v1",
                "description": "该指南在适用地区与人群内给出有条件推荐；这不能替代个体诊疗",
                "not_individual_advice": True,
            },
            "verified",
            ["medical.guideline.region", "medical.source.status", "medical.individual-care"],
            ["validator.medical.pico"],
            evidence_relations=["supports"],
        ),
        _fixture(
            "medical.trial-status.correct",
            "trial_status",
            {
                "claim_id": "claim.t043.trial-status.correct",
                "claim_type": "trial_status",
                "trial_id": "NCT01234567",
                "trial_status": "completed",
                "trial_record": {
                    "status": "completed",
                    "results_available": True,
                    "primary_endpoint_changed": False,
                },
                "results_published": True,
                "definition_version": "evidence-based-medicine-v1",
                "description": "试验已完成且结果已发布，注册终点未变化。",
            },
            "verified",
            ["medical.trial.status"],
            ["validator.medical.trial"],
        ),
        _fixture(
            "medical.patient-education.correct",
            "patient_education",
            {
                **trial_base,
                "claim_id": "claim.t043.patient-education.correct",
                "claim_type": "patient_education",
                "description": (
                    "对符合研究纳入条件的人群，现有证据支持……，绝对获益取决于"
                    "基线风险；这不能替代个体诊疗"
                ),
                "not_individual_advice": True,
            },
            "verified",
            ["medical.individual-care"],
            ["validator.medical.pico"],
            evidence_relations=["supports"],
        ),
        _fixture(
            "medical.prohibition.individual-diagnosis",
            "patient_education",
            {
                **trial_base,
                "claim_id": "claim.t043.prohibition.diagnosis",
                "claim_type": "patient_education",
                "description": "根据你的症状，你就是二型糖尿病",
                "individual_instruction": "diagnosis",
            },
            "blocked",
            ["medical.individual-care"],
            ["validator.medical.pico"],
            expected_reason_codes=["medical_prohibition"],
        ),
        _fixture(
            "medical.prohibition.prescription",
            "patient_education",
            {
                **trial_base,
                "claim_id": "claim.t043.prohibition.prescription",
                "claim_type": "patient_education",
                "description": "你应该开始服用二甲双胍 500 mg 每日两次",
                "individual_instruction": "prescription",
            },
            "blocked",
            ["medical.individual-care"],
            ["validator.medical.pico"],
            expected_reason_codes=["medical_prohibition"],
        ),
        _fixture(
            "medical.prohibition.dose-adjustment",
            "patient_education",
            {
                **trial_base,
                "claim_id": "claim.t043.prohibition.dose-adjustment",
                "claim_type": "patient_education",
                "description": "你的血压偏高，请把氨氯地平加量到 10 mg",
                "individual_instruction": "dose_adjustment",
            },
            "blocked",
            ["medical.individual-care"],
            ["validator.medical.pico"],
            expected_reason_codes=["medical_prohibition"],
        ),
        _fixture(
            "medical.prohibition.stop-medication",
            "patient_education",
            {
                **trial_base,
                "claim_id": "claim.t043.prohibition.stop-medication",
                "claim_type": "patient_education",
                "description": "你可以立即停用阿司匹林，不会有风险",
                "individual_instruction": "stop_medication",
            },
            "blocked",
            ["medical.individual-care"],
            ["validator.medical.pico"],
            expected_reason_codes=["medical_prohibition"],
        ),
        _fixture(
            "medical.prohibition.emergency-triage",
            "patient_education",
            {
                **trial_base,
                "claim_id": "claim.t043.prohibition.emergency-triage",
                "claim_type": "patient_education",
                "description": "胸痛不用去医院，在家观察即可",
                "individual_instruction": "emergency_triage",
            },
            "blocked",
            ["medical.individual-care"],
            ["validator.medical.pico"],
            expected_reason_codes=["medical_prohibition"],
        ),
        _fixture(
            "medical.prohibition.individual-prognosis",
            "prognosis",
            {
                **trial_base,
                "claim_id": "claim.t043.prohibition.prognosis",
                "claim_type": "prognosis",
                "prognostic_factor": "病情进展",
                "description": "根据你的情况，你只有六个月",
                "individual_instruction": "individual_prognosis",
            },
            "blocked",
            ["medical.individual-care"],
            ["validator.medical.pico"],
            expected_reason_codes=["medical_prohibition"],
        ),
        _fixture(
            "medical.source-status.unknown",
            "intervention_effect",
            {
                **trial_base,
                "claim_id": "claim.t043.source-status.unknown",
            },
            "blocked",
            ["medical.source.status", "medical.pico.completeness"],
            ["validator.medical.pico", "validator.medical.trial"],
            expected_reason_codes=["source_status_unknown"],
            evidence_relations=["supports"],
            evidence_lifecycle="unknown",
        ),
        _fixture(
            "medical.conflict.evidence",
            "intervention_effect",
            {
                **trial_base,
                "claim_id": "claim.t043.conflict.evidence",
            },
            "conflicted",
            ["medical.evidence.conflict", "medical.pico.completeness"],
            ["validator.medical.pico", "validator.medical.trial"],
            expected_reason_codes=["evidence_conflict"],
            evidence_relations=["supports", "refutes"],
        ),
        _fixture(
            "medical.pico.incomplete",
            "intervention_effect",
            {
                **trial_base,
                "claim_id": "claim.t043.pico.incomplete",
                "population": None,
                "intervention": None,
                "comparator": None,
                "outcome": None,
                "time_window": None,
            },
            "blocked",
            ["medical.pico.completeness"],
            ["validator.medical.pico"],
            expected_reason_codes=["pico_incomplete"],
        ),
        _fixture(
            "medical.relative-risk.hidden-absolute",
            "intervention_effect",
            {
                **trial_base,
                "claim_id": "claim.t043.relative-risk.hidden-absolute",
                "absolute_effect": None,
                "description": "该药将心血管死亡风险降低 13%",
            },
            "blocked",
            ["medical.risk.expression", "medical.pico.completeness"],
            ["validator.medical.risk", "validator.medical.pico"],
            expected_reason_codes=["relative_risk_misuse"],
        ),
        _fixture(
            "medical.trial.completed-no-result",
            "trial_status",
            {
                "claim_id": "claim.t043.trial.completed-no-result",
                "claim_type": "trial_status",
                "trial_id": "NCT87654321",
                "trial_status": "completed",
                "trial_record": {
                    "status": "completed",
                    "results_available": False,
                    "primary_endpoint_changed": True,
                },
                "results_published": False,
                "definition_version": "evidence-based-medicine-v1",
                "description": "试验已完成，因此可以宣称该干预有效",
            },
            "blocked",
            ["medical.trial.status"],
            ["validator.medical.trial"],
            expected_reason_codes=["trial_result_unpublished"],
        ),
        _fixture(
            "medical.h3.joint-gate",
            "patient_education",
            {
                **trial_base,
                "claim_id": "claim.t043.h3.joint-gate",
                "claim_type": "patient_education",
                "description": "结合我的检查结果，我需要个体化医疗建议",
                "h3_review_requested": True,
            },
            "needs_human",
            ["medical.individual-care"],
            ["validator.medical.pico"],
            expected_reason_codes=["h3_joint_gate"],
            requires_human=True,
        ),
        _fixture(
            "medical.guideline.region-difference",
            "guideline_recommendation",
            {
                "claim_id": "claim.t043.guideline.region-difference",
                "claim_type": "guideline_recommendation",
                "guideline_source": {
                    "organization": "示例指南委员会",
                    "version": "2026",
                    "status": "active",
                    "region": "中国",
                },
                "recommendation_strength": "strong",
                "definition_version": "evidence-based-medicine-v1",
                "description": "比较中美两国指南对该干预的推荐",
                "not_individual_advice": True,
            },
            "needs_human",
            ["medical.guideline.region"],
            ["validator.medical.pico"],
            expected_reason_codes=["guideline_region_difference"],
            requires_human=True,
            evidence_regions=["美国"],
        ),
        _fixture(
            "medical.diagnostic-accuracy.correct",
            "diagnostic_accuracy",
            {
                "claim_id": "claim.t043.diagnostic-accuracy.correct",
                "claim_type": "diagnostic_accuracy",
                "population": "疑似急性心肌梗死成人",
                "index_test": "高敏肌钙蛋白检测",
                "reference_standard": "冠状动脉造影",
                "outcome": "急性心肌梗死诊断",
                "time_window": "入院 3 小时",
                "evidence_certainty": "high",
                "applicability_limits": "仅适用于胸痛急诊人群",
                "definition_version": "evidence-based-medicine-v1",
                "description": (
                    "在符合纳入条件的人群中该检测灵敏度与特异度报告……；这不能"
                    "替代个体诊疗"
                ),
                "not_individual_advice": True,
            },
            "verified",
            ["medical.pico.completeness", "medical.source.status", "medical.individual-care"],
            ["validator.medical.pico"],
            evidence_relations=["supports"],
        ),
        _fixture(
            "medical.harm.correct",
            "harm",
            {
                **trial_base,
                "claim_id": "claim.t043.harm.correct",
                "claim_type": "harm",
                "description": (
                    "对符合研究纳入条件的人群，现有证据报告该干预的严重不良事件"
                    "发生率；这不能替代个体诊疗"
                ),
                "not_individual_advice": True,
            },
            "verified",
            [
                "medical.pico.completeness",
                "medical.risk.expression",
                "medical.source.status",
                "medical.individual-care",
            ],
            ["validator.medical.pico", "validator.medical.risk"],
            evidence_relations=["supports"],
        ),
        _fixture(
            "medical.screening.correct",
            "screening",
            {
                "claim_id": "claim.t043.screening.correct",
                "claim_type": "screening",
                "population": "45-75 岁一般风险人群",
                "screening_test": "结直肠癌粪便免疫化学检测",
                "outcome": "结直肠癌检出",
                "time_window": "每 2 年一次",
                "evidence_certainty": "moderate",
                "applicability_limits": "适用于一般风险人群，高风险人群另议",
                "definition_version": "evidence-based-medicine-v1",
                "description": "对符合纳入条件的人群，现有证据支持该筛查策略；这不能替代个体诊疗",
                "not_individual_advice": True,
            },
            "verified",
            ["medical.pico.completeness", "medical.source.status", "medical.individual-care"],
            ["validator.medical.pico"],
            evidence_relations=["supports"],
        ),
        _fixture(
            "medical.source-status.withdrawn",
            "intervention_effect",
            {
                **trial_base,
                "claim_id": "claim.t043.source-status.withdrawn",
                "description": (
                    "对符合研究纳入条件的人群，现有证据支持该干预降低复合终点"
                    "风险；这不能替代个体诊疗"
                ),
                "not_individual_advice": True,
            },
            "blocked",
            ["medical.source.status", "medical.pico.completeness"],
            ["validator.medical.pico", "validator.medical.trial"],
            expected_reason_codes=["source_status_unknown"],
            evidence_relations=["supports"],
            evidence_lifecycle="withdrawn",
        ),
        _fixture(
            "medical.dose-unit.incomplete",
            "patient_education",
            {
                "claim_id": "claim.t043.dose-unit.incomplete",
                "claim_type": "patient_education",
                "description": "根据指南，该药推荐剂量为 10 mg 每日一次，具体方案以医生评估为准。",
                "dose_value": 10,
                "dose_unit": "mg",
                "definition_version": "evidence-based-medicine-v1",
            },
            "blocked",
            ["medical.dose.unit"],
            ["validator.medical.pico"],
            expected_reason_codes=["dose_unit_invalid"],
        ),
    ]


def _fixture(
    fixture_id: str,
    question_type: str,
    claim: dict[str, Any],
    expected_status: str,
    expected_rule_ids: list[str],
    expected_validator_ids: list[str],
    *,
    expected_reason_codes: list[str] | None = None,
    requires_human: bool = False,
    evidence_relations: list[str] | None = None,
    evidence_lifecycle: str = "active",
    evidence_regions: list[str] | None = None,
) -> FixtureCase:
    regions = evidence_regions or []
    evidence: list[dict[str, Any]] = []
    for index, relation in enumerate(evidence_relations or ["supports"]):
        item: dict[str, Any] = {
            "evidence_id": f"evidence:{fixture_id}:{index}",
            "relation": relation,
            "locator": f"fixture:{fixture_id}",
            "lifecycle_status": evidence_lifecycle,
        }
        if index < len(regions):
            item["region"] = regions[index]
        evidence.append(item)
    return FixtureCase(
        fixture_id=fixture_id,
        name=fixture_id,
        kind="positive" if expected_status == "verified" else "boundary",
        question_type=question_type,
        risk_tier="general_education",
        input_snapshot={
            "question_type": question_type,
            "claim": claim,
            "content": claim,
            "evidence_set": evidence,
        },
        expected_status=expected_status,
        expected_reason_codes=expected_reason_codes or [],
        expected_rule_ids=expected_rule_ids,
        expected_validator_ids=expected_validator_ids,
        requires_human=requires_human,
        rationale="T043 医学高风险教育领域包可重放夹具。",
    )


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="python")
        return dict(dumped) if isinstance(dumped, Mapping) else {}
    return {}


def _claim_id(claim: Any) -> str:
    data = _as_dict(claim)
    return str(data.get("claim_id", data.get("id", "claim:anonymous")))


def _evidence_ids(evidence: Sequence[Mapping[str, Any]]) -> list[str]:
    return [str(item["evidence_id"]) for item in evidence if item.get("evidence_id")]


def _citation_ids(claim: Mapping[str, Any], evidence: Sequence[Mapping[str, Any]]) -> list[str]:
    values: list[str] = [str(item) for item in _list_value(claim.get("citation_ids"))]
    for item in evidence:
        values.extend(str(value) for value in _list_value(item.get("citation_ids")))
    return _unique(values)


def _list_value(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _unique(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if value))


def _check(check_id: str, passed: bool, reason: str) -> dict[str, Any]:
    return {"check_id": check_id, "passed": passed, "reason": reason}


def _stable_id(prefix: str, value: Mapping[str, Any]) -> str:
    encoded = json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{prefix}:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:16]}"


def _pico_dimension(claim: Mapping[str, Any]) -> str:
    """返回证据画像中的 PICO 完整性维度；不适用的问题类型保留 unknown。"""
    fields = _PICO_FIELD_SETS.get(str(claim.get("claim_type", "")))
    if fields is None:
        return "unknown"
    return (
        "complete"
        if all(claim.get(field) not in (None, "", []) for field in fields)
        else "incomplete"
    )


def _forbidden_phrase_hit(description: str) -> bool:
    return any(phrase in description for phrase in _FORBIDDEN_PHRASES) or bool(
        _DOSE_INSTRUCTION_PATTERN.search(description)
    )


def _rule_ids_for(question_type: str) -> list[str]:
    mapping = {
        "intervention_effect": [
            "medical.pico.completeness",
            "medical.risk.expression",
            "medical.trial.status",
            "medical.source.status",
            "medical.evidence.conflict",
            "medical.individual-care",
        ],
        "diagnostic_accuracy": [
            "medical.pico.completeness",
            "medical.source.status",
            "medical.individual-care",
        ],
        "prognosis": [
            "medical.pico.completeness",
            "medical.source.status",
            "medical.individual-care",
        ],
        "harm": [
            "medical.pico.completeness",
            "medical.risk.expression",
            "medical.source.status",
            "medical.individual-care",
        ],
        "screening": [
            "medical.pico.completeness",
            "medical.source.status",
            "medical.individual-care",
        ],
        "guideline_recommendation": [
            "medical.guideline.region",
            "medical.source.status",
            "medical.individual-care",
            "medical.dose.unit",
        ],
        "trial_status": [
            "medical.trial.status",
            "medical.source.status",
        ],
        "patient_education": [
            "medical.individual-care",
            "medical.risk.expression",
            "medical.guideline.region",
            "medical.dose.unit",
        ],
    }
    return mapping.get(question_type, ["medical.individual-care"])


def _validator_ids_for(question_type: str) -> list[str]:
    if question_type == "intervention_effect":
        return ["validator.medical.pico", "validator.medical.risk", "validator.medical.trial"]
    if question_type in {"diagnostic_accuracy", "prognosis", "screening"}:
        return ["validator.medical.pico"]
    if question_type in {"harm", "patient_education"}:
        return ["validator.medical.pico", "validator.medical.risk"]
    if question_type == "guideline_recommendation":
        return ["validator.medical.pico"]
    if question_type == "trial_status":
        return ["validator.medical.trial"]
    return ["validator.medical.pico"]
