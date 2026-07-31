"""物理与化学实验测量领域包。

该包处理物理量、SI 单位、实验条件、测量不确定度、有效数字、常数、化学术语、
物性和可复现实验解释，并阻止危险实验的无防护操作指引。它不调用网络、模型或
未登记的校验工具；单位、不确定度和化学计量检查以逻辑能力登记在 Manifest 中，
实际执行仍由平台通过受限能力注册表提供。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
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
    "PhysicsChemistryDomainPack",
    "create_physics_chemistry_pack",
]

_QUESTION_TYPES = (
    "quantity_definition",
    "unit_conversion",
    "measurement_result",
    "constant_value",
    "calibration",
    "experimental_comparison",
    "chemical_term",
    "mechanism_under_conditions",
)

# 简单量纲映射：仅用于确定性规则检查，不替代完整单位库。
_BASE_DIMENSIONS: dict[str, str] = {
    "m": "length",
    "cm": "length",
    "mm": "length",
    "km": "length",
    "nm": "length",
    "um": "length",
    "μm": "length",
    "g": "mass",
    "kg": "mass",
    "mg": "mass",
    "t": "mass",
    "s": "time",
    "min": "time",
    "h": "time",
    "k": "temperature",
    "°c": "temperature",
    "celsius": "temperature",
    "°f": "temperature",
    "fahrenheit": "temperature",
    "mol": "amount_of_substance",
    "a": "electric_current",
    "cd": "luminous_intensity",
    "n": "force",
    "pa": "pressure",
    "j": "energy",
    "ev": "energy",
    "w": "power",
    "v": "voltage",
    "ohm": "resistance",
    "c": "electric_charge",
    "hz": "frequency",
    "l": "volume",
    "ml": "volume",
    "m3": "volume",
    "m^3": "volume",
    "m/s": "speed",
    "m/s2": "acceleration",
    "m/s^2": "acceleration",
    "kg/m3": "density",
    "kg/m^3": "density",
    "g/cm3": "density",
    "g/cm^3": "density",
}

_DANGEROUS_HAZARDS = {
    "explosive",
    "high_pressure",
    "high_voltage",
    "radioactive",
    "toxic",
    "corrosive",
    "flammable",
    "pyrophoric",
    "strong_oxidizer",
    "human_exposure",
}

_REASON_MESSAGES = {
    "claim_schema_incomplete": "物理/化学 Claim 缺少量名、值、单位、条件或定义版本等必填字段。",
    "unit_dimension_mismatch": "单位量纲不一致，不能在不同量纲间直接换算或比较。",
    "temperature_scale_misuse": "对摄氏/华氏温标执行了只能在线性温标上进行的乘除运算。",
    "measurement_uncertainty_missing": (
        "测量结果声明缺少不确定度、覆盖因子或条件，无法评估可信区间。"
    ),
    "significant_figures_inconsistent": "有效数字与仪器分辨率或不确定度声明不一致。",
    "mass_weight_confusion": "质量与重量（力）概念混用或单位错误。",
    "mol_molecule_confusion": "物质的量（mol）与粒子数（molecule）概念混用。",
    "chemical_equation_unbalanced": "化学方程式原子或电荷不守恒。",
    "reaction_conditions_missing": "化学机理或反应缺少温度、压力、浓度等必要条件。",
    "ppm_basis_missing": "ppm 单位使用必须声明基准（如 w/w、v/v、mg/kg），否则无法解释浓度。",
    "error_uncertainty_confused": (
        "把误差（error）直接当成测量不确定度使用，未给出标准不确定度或覆盖因子。"
    ),
    "codata_year_outdated": "物理常数 CODATA 年份未声明或已明显过期。",
    "source_stale": "来源版本已撤回、取代或失效。",
    "dangerous_experiment": (
        "涉及危险实验、高能、高压、强毒、爆炸性、放射性或人体暴露操作，"
        "必须拒绝或转人工安全门。"
    ),
    "evidence_conflict": "同一物理/化学命题同时存在支持与反驳证据。",
}


class PhysicsChemistryDomainPack:
    """面向物理与化学实验测量的可重放领域包实现。"""

    def __init__(self, manifest: DomainPackManifest | None = None) -> None:
        self.manifest = manifest or _build_manifest()

    def classify_question(self, question_context: Any) -> str:
        data = _as_dict(question_context)
        requested = str(data.get("question_type", "")).strip()
        if requested in _QUESTION_TYPES:
            return requested
        if data.get("from_unit") is not None or data.get("to_unit") is not None:
            return "unit_conversion"
        if data.get("constant_name") is not None:
            return "constant_value"
        if data.get("equation") is not None:
            return "mechanism_under_conditions"
        if data.get("chemical_term") is not None or data.get("iupac_term") is not None:
            return "chemical_term"
        if data.get("calibration_certificate") is not None or data.get("calibration") is not None:
            return "calibration"
        if data.get("experiment") is not None or data.get("hazard_flags") is not None:
            return "experimental_comparison"
        return "measurement_result"

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
                "si_brochure",
                "codata_constants",
                "iupac_gold_book",
                "calibration_certificate",
                "standard_method",
                "experimental_raw_data",
                "peer_reviewed_result",
                "textbook",
            ],
            "network_performed": False,
        }

    def normalize_metadata(self, adapter_record: Any) -> dict[str, Any]:
        data = _as_dict(adapter_record)
        return {
            "canonical_id": data.get("canonical_id", data.get("id")),
            "title": data.get("title"),
            "version": data.get("version", data.get("edition", data.get("release_year"))),
            "edition": data.get("edition"),
            "release_year": data.get("release_year"),
            "entry_id": data.get("entry_id"),
            "status": data.get("status", "unknown"),
            "superseded": data.get("superseded", "unknown"),
            "source_role": data.get("source_role", "authoritative_reference"),
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
            "quantities": data.get("quantities", []),
            "units": data.get("units", []),
            "conditions": data.get("conditions", {}),
            "equations": data.get("equations", []),
            "instrument": data.get("instrument"),
            "sample": data.get("sample"),
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
                "quantity",
                "value",
                "unit",
                "uncertainty",
                "coverage_factor",
                "conditions",
                "sample",
                "instrument",
                "significant_figures",
                "from_unit",
                "to_unit",
                "expected_value",
                "constant_name",
                "codata_year",
                "equation",
                "chemical_term",
                "description",
                "hazard_flags",
            )
            if key in data
        }

    def assess_evidence(self, claim: Any, evidence_set: Any) -> dict[str, Any]:
        evidence = [_as_dict(item) for item in (evidence_set or [])]
        relations = {str(item.get("relation", "unknown")) for item in evidence}
        if "supports" in relations and "refutes" in relations:
            status = "conflicted"
        elif any(
            str(item.get("lifecycle_status", item.get("status", "active")))
            in {"retracted", "withdrawn", "superseded"}
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
                "metrological_traceability": "traceable"
                if any(item.get("calibration_chain") for item in evidence)
                else "unknown",
                "source_lifecycle": "active" if status == "verified" else status,
                "reproducibility": "materials_available"
                if any(item.get("locator") or item.get("quoted_span") for item in evidence)
                else "unknown",
                "uncertainty_declared": (
                    "complete"
                    if claim_data.get("uncertainty") is not None
                    else "missing"
                ),
                "conditions_specified": (
                    "complete"
                    if claim_data.get("conditions")
                    else "missing"
                ),
            },
            "reason": (
                "物理/化学 Claim 优先依据计量溯源、校准链、测量模型、条件、"
                "不确定度和独立复现；来源声誉不替代实验条件与误差分析。"
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
                    "conflict_id": "physics-chemistry-conflict:evidence",
                    "type": "true_disagreement",
                    "claim_ids": [_claim_id(claim) for claim in claims],
                    "evidence_ids": _evidence_ids(evidence),
                    "status": "open",
                    "reason": "同一物理/化学命题同时存在支持与反驳证据。",
                }
            )
        # 不同 CODATA 调整年份不是真冲突（版本差异）；未声明年份由
        # codata_year_outdated 规则处理，这里不再报告常数版本冲突。
        return conflicts

    def constrain_wording(self, assessment: Any, audience: str, genre: str) -> dict[str, Any]:
        data = _as_dict(assessment)
        status = str(data.get("status", "unknown"))
        if status == "verified":
            return {
                "strength": "qualified",
                "wording_ceiling": "qualified",
                "measurement_checked": True,
                "allowed": "在声明的实验条件、仪器、样品和不确定度下，测量或换算结果可接受。",
                "required_disclosures": ["实验条件", "仪器", "不确定度", "CODATA 年份或来源版本"],
                "forbidden": ["精确等于", "理论值证明仪器无误", "对所有条件都成立"],
                "audience": audience,
                "genre": genre,
            }
        if status in {"conflicted", "unknown"}:
            return {
                "strength": "unassessable",
                "wording_ceiling": "unassessable",
                "measurement_checked": False,
                "allowed": "现有材料不足以独立判断该测量或化学结论。",
                "required_disclosures": ["保留未决冲突或人工复核要求"],
                "forbidden": ["已验证", "必然成立", "精确等于"],
                "audience": audience,
                "genre": genre,
            }
        return {
            "strength": "none",
            "wording_ceiling": "none",
            "measurement_checked": False,
            "allowed": "测量、单位、化学计量或安全条件未通过验证，不能按可信结论发布。",
            "required_disclosures": ["失败原因和待补条件"],
            "forbidden": ["实验结果已证明", "可安全操作", "精确等于"],
            "audience": audience,
            "genre": genre,
        }

    def validate_claim(self, claim: Any, evidence_set: Any) -> dict[str, Any]:
        data = _as_dict(claim)
        evidence = [_as_dict(item) for item in (evidence_set or [])]
        question_type = str(
            data.get("claim_type", data.get("question_type", "measurement_result"))
        )
        if question_type not in _QUESTION_TYPES:
            question_type = "measurement_result"

        reasons: list[str] = []
        checks: list[dict[str, Any]] = []
        rule_ids = _rule_ids_for(question_type)
        validator_ids = _validator_ids_for(question_type)

        self._validate_claim_shape(data, question_type, reasons, checks)

        if any(
            str(item.get("relation")) == "supports" for item in evidence
        ) and any(str(item.get("relation")) == "refutes" for item in evidence):
            reasons.append("evidence_conflict")
        if any(
            str(item.get("lifecycle_status", item.get("status", "active")))
            in {"retracted", "withdrawn", "superseded", "stale"}
            for item in evidence
        ):
            reasons.append("source_stale")

        if question_type in {
            "unit_conversion",
            "measurement_result",
            "experimental_comparison",
            "constant_value",
            "quantity_definition",
        }:
            self._validate_units(data, reasons, checks)
        if question_type in {"measurement_result", "calibration", "experimental_comparison"}:
            self._validate_measurement(data, reasons, checks)
        if question_type == "constant_value":
            self._validate_constant(data, reasons, checks)
        if question_type in {"chemical_term", "mechanism_under_conditions"}:
            self._validate_chemistry(data, reasons, checks)
        if question_type in {"experimental_comparison", "mechanism_under_conditions"}:
            self._validate_safety(data, reasons, checks)

        unique_reasons = _unique(reasons)
        if "evidence_conflict" in unique_reasons:
            status = "conflicted"
        elif reasons:
            status = "blocked"
        else:
            status = "verified"

        claim_id = _claim_id(data)
        evidence_ids = _evidence_ids(evidence)
        citation_ids = _citation_ids(data, evidence)
        report_id = _stable_id(
            "physics-chemistry-validation",
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
        details = {
            "claim": {
                **data,
                "claim_id": claim_id,
                "claim_type": question_type,
                "text": data.get("text", data.get("description")),
                "definition_version": data.get("definition_version"),
                "conditions": data.get("conditions", {}),
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
                "measurement_checked": status == "verified",
                "generated_claims_are_not_measurement": True,
                "provenance": {
                    "pack_id": self.manifest.id,
                    "pack_version": self.manifest.version,
                    "validator_ids": validator_ids,
                    "si_version": data.get("si_version"),
                    "codata_year": data.get("codata_year"),
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
                "measurement_checked": status == "verified",
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
                "model_generation_is_not_measurement": True,
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
            "quantity_definition": ("claim_id", "quantity", "definition_version"),
            "unit_conversion": (
                "claim_id",
                "value",
                "from_unit",
                "to_unit",
                "expected_value",
                "definition_version",
            ),
            "measurement_result": (
                "claim_id",
                "quantity",
                "value",
                "unit",
                "conditions",
                "sample",
                "instrument",
                "significant_figures",
                "definition_version",
            ),
            "constant_value": (
                "claim_id",
                "constant_name",
                "value",
                "unit",
                "codata_year",
                "definition_version",
            ),
            "calibration": ("claim_id", "instrument", "calibration_date", "definition_version"),
            "experimental_comparison": (
                "claim_id",
                "description",
                "definition_version",
            ),
            "chemical_term": ("claim_id", "chemical_term", "definition_version"),
            "mechanism_under_conditions": (
                "claim_id",
                "equation",
                "conditions",
                "definition_version",
            ),
        }
        missing = [field for field in required[question_type] if field not in claim]
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
            checks.append(_check("claim_schema", True, "物理/化学 Claim 必填字段齐全。"))

    def _validate_units(
        self,
        claim: Mapping[str, Any],
        reasons: list[str],
        checks: list[dict[str, Any]],
    ) -> None:
        from_unit = _normalize_unit(str(claim.get("from_unit", claim.get("unit", ""))))
        to_unit = _normalize_unit(str(claim.get("to_unit", "")))
        operation = str(claim.get("operation", "")).lower()

        if from_unit and to_unit and _dimension(from_unit) != _dimension(to_unit):
            reasons.append("unit_dimension_mismatch")
            checks.append(
                _check("unit_dimension", False, _REASON_MESSAGES["unit_dimension_mismatch"])
            )
        elif from_unit and to_unit:
            checks.append(_check("unit_dimension", True, "单位量纲一致。"))
        else:
            checks.append(_check("unit_dimension", True, "单位检查无冲突。"))

        if "celsius" in from_unit or "°c" in from_unit or "celsius" in to_unit or "°c" in to_unit:
            if operation in {"multiply", "divide", "square", "root"} or _implies_celsius_multiply(
                claim
            ):
                reasons.append("temperature_scale_misuse")
                checks.append(
                    _check("temperature_scale", False, _REASON_MESSAGES["temperature_scale_misuse"])
                )
            else:
                checks.append(_check("temperature_scale", True, "温度换算符合仿射温标规则。"))

        unit = _normalize_unit(str(claim.get("unit", "")))
        if from_unit == "ppm" or to_unit == "ppm" or unit == "ppm":
            conditions = _as_dict(claim.get("conditions", {}))
            if not any(key in conditions for key in ("basis", "基准", "w/w", "v/v", "mg/kg")):
                reasons.append("ppm_basis_missing")
                checks.append(
                    _check("ppm_basis", False, _REASON_MESSAGES["ppm_basis_missing"])
                )

    def _validate_measurement(
        self,
        claim: Mapping[str, Any],
        reasons: list[str],
        checks: list[dict[str, Any]],
    ) -> None:
        uncertainty = claim.get("uncertainty")
        coverage_factor = claim.get("coverage_factor")
        sig_figs = claim.get("significant_figures")
        instrument = str(claim.get("instrument", ""))

        if uncertainty is None and coverage_factor is None:
            reasons.append("measurement_uncertainty_missing")
            checks.append(
                _check(
                    "measurement_uncertainty",
                    False,
                    _REASON_MESSAGES["measurement_uncertainty_missing"],
                )
            )
        else:
            checks.append(_check("measurement_uncertainty", True, "测量不确定度已声明。"))

        text = str(claim.get("description", ""))
        if _mass_weight_confused(claim, text):
            reasons.append("mass_weight_confusion")
            checks.append(_check("mass_weight", False, _REASON_MESSAGES["mass_weight_confusion"]))
        else:
            checks.append(_check("mass_weight", True, "质量与重量概念未混用。"))

        if _mol_molecule_confused(claim, text):
            reasons.append("mol_molecule_confusion")
            checks.append(_check("mol_molecule", False, _REASON_MESSAGES["mol_molecule_confusion"]))
        else:
            checks.append(_check("mol_molecule", True, "物质的量与粒子数未混用。"))

        if _error_uncertainty_confused(claim, text):
            reasons.append("error_uncertainty_confused")
            checks.append(
                _check(
                    "error_uncertainty",
                    False,
                    _REASON_MESSAGES["error_uncertainty_confused"],
                )
            )
        else:
            checks.append(_check("error_uncertainty", True, "误差与不确定度概念未混用。"))

        if sig_figs is not None and instrument and not _sigfigs_plausible(sig_figs, instrument):
            reasons.append("significant_figures_inconsistent")
            checks.append(
                _check(
                    "significant_figures",
                    False,
                    _REASON_MESSAGES["significant_figures_inconsistent"],
                )
            )
        else:
            checks.append(_check("significant_figures", True, "有效数字与声明一致。"))

    def _validate_constant(
        self,
        claim: Mapping[str, Any],
        reasons: list[str],
        checks: list[dict[str, Any]],
    ) -> None:
        year = claim.get("codata_year")
        try:
            year_int = int(year) if year is not None else 0
        except (TypeError, ValueError):
            year_int = 0
        if year_int < 2010:
            reasons.append("codata_year_outdated")
            checks.append(
                _check("codata_year", False, _REASON_MESSAGES["codata_year_outdated"])
            )
        else:
            checks.append(_check("codata_year", True, "CODATA 年份在可接受范围内。"))

    def _validate_chemistry(
        self,
        claim: Mapping[str, Any],
        reasons: list[str],
        checks: list[dict[str, Any]],
    ) -> None:
        equation = str(claim.get("equation", ""))
        if equation:
            balanced, atom_diff = _chemical_equation_balanced(equation)
            if not balanced:
                reasons.append("chemical_equation_unbalanced")
                checks.append(
                    _check(
                        "chemical_equation",
                        False,
                        f"{_REASON_MESSAGES['chemical_equation_unbalanced']} 差异：{atom_diff}",
                    )
                )
            else:
                checks.append(_check("chemical_equation", True, "化学方程式原子/电荷守恒。"))
        conditions = _as_dict(claim.get("conditions", {}))
        if claim.get("equation") and not conditions:
            reasons.append("reaction_conditions_missing")
            checks.append(
                _check(
                    "reaction_conditions",
                    False,
                    _REASON_MESSAGES["reaction_conditions_missing"],
                )
            )
        else:
            checks.append(_check("reaction_conditions", True, "化学反应条件已声明。"))

    def _validate_safety(
        self,
        claim: Mapping[str, Any],
        reasons: list[str],
        checks: list[dict[str, Any]],
    ) -> None:
        hazard_flags = set(_list_value(claim.get("hazard_flags", [])))
        description = str(claim.get("description", ""))
        if hazard_flags & _DANGEROUS_HAZARDS or _description_implies_danger(description):
            reasons.append("dangerous_experiment")
            checks.append(
                _check("dangerous_experiment", False, _REASON_MESSAGES["dangerous_experiment"])
            )
        else:
            checks.append(_check("dangerous_experiment", True, "未触发危险实验安全门。"))


def create_physics_chemistry_pack() -> PhysicsChemistryDomainPack:
    """创建物理与化学实验测量领域包。"""
    return PhysicsChemistryDomainPack()


def _build_manifest() -> DomainPackManifest:
    fixture_ids = [
        "physics.measurement.correct",
        "physics.unit.conversion.correct",
        "physics.quantity.definition.correct",
        "physics.calibration.correct",
        "chemistry.term.correct",
        "physics.unit.celsius-multiply",
        "physics.mass-weight.confusion",
        "chemistry.mol-molecule.confusion",
        "physics.constant.old-codata",
        "chemistry.equation.imbalanced",
        "physics.safety.dangerous-experiment",
        "chemistry.mechanism.dangerous",
        "physics.conflict.evidence",
        "chemistry.units.ppm-basis-missing",
        "physics.measurement.error-uncertainty.confusion",
        "physics.sigfig.overstated",
        "physics.uncertainty.missing",
    ]
    source_policy = DomainSourcePolicy(
        policy_id="physics-chemistry.authoritative-sources",
        applies_to=list(_QUESTION_TYPES),
        evidence_requirements=[
            "BIPM SI Brochure 明确版本",
            "NIST CODATA 发布集与调整年份",
            "IUPAC Gold Book 条目标识、定义和版本快照",
            "校准证书、标准方法、实验原始数据或同行评审结果",
        ],
        allowed_source_roles=[
            "si_brochure",
            "codata_constants",
            "iupac_gold_book",
            "calibration_certificate",
            "standard_method",
            "experimental_raw_data",
            "peer_reviewed_result",
            "textbook",
        ],
        on_failure="block",
    )
    claim_schema = DomainClaimSchema(
        schema_id="physics-chemistry.claim.v1",
        applies_to=list(_QUESTION_TYPES),
        required_fields=[
            "claim_id",
            "claim_type",
            "definition_version",
            "quantity_or_term",
            "value_or_description",
            "unit_or_conditions",
        ],
        evidence_requirements=[
            "definition_version",
            "source_version",
            "conditions",
            "uncertainty",
        ],
        wording_policy_ids=["physics-chemistry.wording.v1"],
        unknown_behavior="block",
    )
    wording = DomainWordingPolicy(
        policy_id="physics-chemistry.wording.v1",
        applies_to=list(_QUESTION_TYPES),
        allowed_statuses=[
            "verified",
            "qualified",
            "needs_human",
            "conflicted",
            "blocked",
        ],
        forbidden_strengths=[
            "exact_equality",
            "theory_proves_instrument",
            "universal_without_conditions",
        ],
        required_disclosures=[
            "实验条件",
            "仪器与样品",
            "不确定度或覆盖因子",
            "CODATA 年份或来源版本",
        ],
        requires_human_gate=True,
    )
    rules = [
        DomainRule(
            rule_id="physics.unit.dimension",
            applies_to=[
                "unit_conversion",
                "measurement_result",
                "experimental_comparison",
                "calibration",
                "constant_value",
                "quantity_definition",
            ],
            explanation="单位必须可归一化并保持量纲一致；温度换算遵循仿射温标规则。",
            fixture_ids=[
                "physics.unit.conversion.correct",
                "physics.unit.celsius-multiply",
                "physics.measurement.correct",
                "physics.calibration.correct",
                "physics.quantity.definition.correct",
                "physics.constant.old-codata",
            ],
        ),
        DomainRule(
            rule_id="physics.units.ppm_basis",
            applies_to=["unit_conversion", "measurement_result"],
            explanation="ppm 单位必须声明基准（w/w、v/v、mg/kg），否则浓度不可解释。",
            fixture_ids=[
                "chemistry.units.ppm-basis-missing",
            ],
        ),
        DomainRule(
            rule_id="physics.measurement.error_vs_uncertainty",
            applies_to=["measurement_result"],
            explanation="误差与测量不确定度必须区分：误差不能替代标准不确定度和覆盖因子。",
            fixture_ids=[
                "physics.measurement.error-uncertainty.confusion",
            ],
        ),
        DomainRule(
            rule_id="physics.measurement.uncertainty",
            applies_to=["measurement_result", "experimental_comparison", "calibration"],
            explanation="测量结果和校准必须声明不确定度、覆盖因子和实验条件。",
            fixture_ids=[
                "physics.measurement.correct",
                "physics.uncertainty.missing",
                "physics.calibration.correct",
            ],
        ),
        DomainRule(
            rule_id="physics.sigfig.figures",
            applies_to=["measurement_result"],
            explanation="有效数字必须与仪器分辨率和不确定度声明一致。",
            fixture_ids=[
                "physics.measurement.correct",
                "physics.sigfig.overstated",
            ],
        ),
        DomainRule(
            rule_id="physics.constant.codata",
            applies_to=["constant_value"],
            explanation="基本物理常数必须声明 CODATA 调整年份，旧值必须标记为 stale。",
            fixture_ids=[
                "physics.constant.old-codata",
            ],
        ),
        DomainRule(
            rule_id="chemistry.equation.balance",
            applies_to=["mechanism_under_conditions"],
            explanation="化学方程式必须满足原子和电荷守恒。",
            fixture_ids=[
                "chemistry.equation.imbalanced",
            ],
        ),
        DomainRule(
            rule_id="chemistry.term.conditions",
            applies_to=["chemical_term", "mechanism_under_conditions"],
            explanation="化学术语和反应机理必须声明温度、压力、浓度等适用条件。",
            fixture_ids=[
                "chemistry.equation.imbalanced",
            ],
        ),
        DomainRule(
            rule_id="physics.safety.dangerous_experiment",
            applies_to=["experimental_comparison", "mechanism_under_conditions"],
            explanation="涉及危险实验、高能、高压、强毒、爆炸性、放射性或人体暴露的操作必须进入安全门。",
            fixture_ids=[
                "physics.safety.dangerous-experiment",
                "chemistry.mechanism.dangerous",
            ],
            human_gate="H3",
        ),
        DomainRule(
            rule_id="physics.concept.mass_weight",
            applies_to=["measurement_result"],
            explanation="质量与重量（力）必须使用正确概念和单位。",
            fixture_ids=[
                "physics.mass-weight.confusion",
            ],
        ),
        DomainRule(
            rule_id="chemistry.concept.mol_molecule",
            applies_to=["measurement_result"],
            explanation="物质的量（mol）与粒子数（molecule）不得混用。",
            fixture_ids=[
                "chemistry.mol-molecule.confusion",
            ],
        ),
    ]
    validators = [
        ValidatorRequirement(
            validator_id="validator.physics.unit",
            capability_name="unit_dimension_check",
            capability_version="1.0.0",
            input_schema_version="unit-dimension/v1",
            output_schema_version="physics-chemistry-validation/v1",
            fixture_ids=[
                "physics.measurement.correct",
                "physics.unit.conversion.correct",
                "physics.calibration.correct",
                "physics.unit.celsius-multiply",
                "physics.mass-weight.confusion",
            ],
        ),
        ValidatorRequirement(
            validator_id="validator.physics.uncertainty",
            capability_name="uncertainty_propagation_check",
            capability_version="1.0.0",
            input_schema_version="uncertainty/v1",
            output_schema_version="physics-chemistry-validation/v1",
            fixture_ids=[
                "physics.measurement.correct",
                "physics.uncertainty.missing",
                "physics.calibration.correct",
            ],
        ),
        ValidatorRequirement(
            validator_id="validator.physics.sigfig",
            capability_name="significant_figures_check",
            capability_version="1.0.0",
            input_schema_version="sigfig/v1",
            output_schema_version="physics-chemistry-validation/v1",
            fixture_ids=[
                "physics.measurement.correct",
                "physics.sigfig.overstated",
            ],
        ),
        ValidatorRequirement(
            validator_id="validator.chemistry.equation",
            capability_name="chemical_equation_balance_check",
            capability_version="1.0.0",
            input_schema_version="chemical-equation/v1",
            output_schema_version="physics-chemistry-validation/v1",
            fixture_ids=[
                "chemistry.equation.imbalanced",
            ],
        ),
    ]
    fixtures = _build_fixtures()
    manifest = DomainPackManifest(
        id="physics-chemistry.experiment",
        version="1.0.0",
        platform_api=">=1.0,<2.0",
        pack_api="domain-pack/v1",
        scope=[
            "物理量与测量",
            "SI 单位与换算",
            "实验条件与仪器校准",
            "测量不确定度",
            "基本物理常数",
            "化学术语与物性",
            "化学反应计量与条件",
            "可复现实验解释",
        ],
        exclusions=[
            "危险实验的无防护操作指引",
            "仅凭理论公式推断实际测量精度",
            "把化学数据库条目自动解释为适用于任意温压/纯度条件",
            "将模型生成步骤直接视为已验证实验结论",
        ],
        languages=["zh-CN", "en"],
        disciplines=["physics", "chemistry"],
        risk_tiers=["general_education", "research_support"],
        question_types=list(_QUESTION_TYPES),
        source_policies=[source_policy],
        source_adapters=[
            {
                "adapter_id": "physics-chemistry.reference.snapshot",
                "authority": "publisher_or_standard_body",
                "status_fields": [
                    "edition",
                    "release_year",
                    "entry_id",
                    "lifecycle_status",
                    "content_hash",
                ],
                "failure_semantics": "preserve_unknown_and_block_high_risk",
            }
        ],
        identifier_rules=[
            {
                "rule_id": "physics-chemistry.si-constant-id",
                "required": ["constant_name", "codata_year"],
            },
            {
                "rule_id": "physics-chemistry.iupac-term-id",
                "required": ["chemical_term", "entry_id"],
            },
        ],
        evidence_dimensions=[
            {"id": "metrological_traceability", "values": ["traceable", "partial", "unknown"]},
            {"id": "uncertainty_declared", "values": ["complete", "partial", "missing"]},
            {"id": "conditions_specified", "values": ["complete", "partial", "missing"]},
            {"id": "reproducibility", "values": ["replayable", "partial", "unavailable"]},
        ],
        certainty_mappings=[
            {
                "when": "traceable_and_uncertainty_complete_and_conditions_complete",
                "status": "verified",
            },
            {"when": "uncertainty_missing_or_conditions_missing", "status": "blocked"},
            {"when": "dangerous_experiment_or_hazard_unresolved", "status": "blocked"},
        ],
        wording_policy=[wording],
        claim_schemas=[claim_schema],
        unit_and_formula_rules=[
            {
                "rule_id": "physics-chemistry.unit.dimension",
                "temperature_scales": ["kelvin", "celsius", "fahrenheit"],
                "affine_operations": "addition_subtraction_only",
            },
            {
                "rule_id": "physics-chemistry.sigfig",
                "derive_from_instrument": True,
            },
        ],
        ontologies=[
            {
                "ontology_id": "physics-chemistry.quantity.v1",
                "terms": ["physical_quantity", "unit", "uncertainty", "condition"],
            },
            {
                "ontology_id": "physics-chemistry.state.v1",
                "states": ["verified", "blocked", "needs_human", "conflicted"],
            },
        ],
        tools=[
            {"capability_name": "unit_dimension_check", "sandbox": True, "network": False},
            {"capability_name": "uncertainty_propagation_check", "sandbox": True, "network": False},
            {"capability_name": "significant_figures_check", "sandbox": True, "network": False},
            {
                "capability_name": "chemical_equation_balance_check",
                "sandbox": True,
                "network": False,
            },
        ],
        rules=rules,
        validators=validators,
        conflict_rules=[
            {
                "rule_id": "physics-chemistry.conflict.evidence",
                "when": "supports_and_refutes",
                "status": "conflicted",
            },
        ],
        fixtures=fixtures,
        evaluation_sets=[
            {
                "set_id": "physics-chemistry.t042.core",
                "fixture_ids": fixture_ids,
                "requires_trace": True,
            }
        ],
        compatibility=DomainCompatibility(
            compatible_with=["1.0.0"],
            preserves_runtime_contract=True,
            requires_revalidation=True,
        ),
        content_files={"rules/physics-chemistry-v1.json": "embedded"},
        build_provenance={"builder": "science-companion", "source": "T042", "reproducible": True},
        platform_floor=PlatformSafetyFloor(),
    )
    manifest.content_digest = DomainPackLoader.manifest_digest(manifest)
    return manifest


def _build_fixtures() -> list[FixtureCase]:
    return [
        _fixture(
            "physics.measurement.correct",
            "measurement_result",
            {
                "claim_id": "claim.physics.measurement.correct",
                "claim_type": "measurement_result",
                "quantity": "长度",
                "value": 1.234,
                "unit": "m",
                "uncertainty": 0.002,
                "coverage_factor": 2,
                "conditions": {"temperature": "20 °C", "pressure": "101.325 kPa"},
                "sample": "钢棒 A",
                "instrument": "游标卡尺",
                "significant_figures": 4,
                "definition_version": "si-length-v1",
            },
            "verified",
            ["physics.unit.dimension", "physics.measurement.uncertainty", "physics.sigfig.figures"],
            ["validator.physics.unit", "validator.physics.uncertainty", "validator.physics.sigfig"],
        ),
        _fixture(
            "physics.unit.conversion.correct",
            "unit_conversion",
            {
                "claim_id": "claim.physics.unit.conversion.correct",
                "claim_type": "unit_conversion",
                "value": 1.0,
                "from_unit": "m",
                "to_unit": "cm",
                "expected_value": 100.0,
                "definition_version": "si-units-v1",
            },
            "verified",
            ["physics.unit.dimension"],
            ["validator.physics.unit"],
        ),
        _fixture(
            "physics.quantity.definition.correct",
            "quantity_definition",
            {
                "claim_id": "claim.physics.quantity.definition.correct",
                "claim_type": "quantity_definition",
                "quantity": "速度",
                "definition": "位移对时间的变化率，单位 m/s。",
                "definition_version": "si-kinematics-v1",
            },
            "verified",
            ["physics.unit.dimension"],
            ["validator.physics.unit"],
        ),
        _fixture(
            "physics.calibration.correct",
            "calibration",
            {
                "claim_id": "claim.physics.calibration.correct",
                "claim_type": "calibration",
                "instrument": "电子天平",
                "calibration_date": "2026-01-15",
                "calibration_certificate": "CC-2026-001",
                "uncertainty": 0.001,
                "coverage_factor": 2,
                "definition_version": "calibration-v1",
            },
            "verified",
            ["physics.unit.dimension", "physics.measurement.uncertainty"],
            ["validator.physics.unit", "validator.physics.uncertainty"],
        ),
        _fixture(
            "chemistry.term.correct",
            "chemical_term",
            {
                "claim_id": "claim.chemistry.term.correct",
                "claim_type": "chemical_term",
                "chemical_term": "摩尔",
                "definition": "物质的量的 SI 基本单位，符号 mol。",
                "entry_id": "goldbook:M03980",
                "definition_version": "iupac-goldbook-v1",
            },
            "verified",
            ["chemistry.term.conditions"],
            ["validator.chemistry.equation"],
        ),
        _fixture(
            "physics.unit.celsius-multiply",
            "unit_conversion",
            {
                "claim_id": "claim.physics.unit.celsius-multiply",
                "claim_type": "unit_conversion",
                "value": 20.0,
                "from_unit": "°C",
                "to_unit": "°C",
                "operation": "multiply",
                "expected_value": 40.0,
                "definition_version": "temperature-scales-v1",
            },
            "blocked",
            ["physics.unit.dimension"],
            ["validator.physics.unit"],
            expected_reason_codes=["temperature_scale_misuse"],
        ),
        _fixture(
            "physics.mass-weight.confusion",
            "measurement_result",
            {
                "claim_id": "claim.physics.mass-weight.confusion",
                "claim_type": "measurement_result",
                "quantity": "重量",
                "value": 9.8,
                "unit": "kg",
                "uncertainty": 0.1,
                "coverage_factor": 1,
                "conditions": {"temperature": "20 °C"},
                "sample": "物体 B",
                "instrument": "弹簧秤",
                "significant_figures": 2,
                "definition_version": "si-mass-v1",
                "description": "重量为 9.8 kg",
            },
            "blocked",
            [
                "physics.concept.mass_weight",
                "physics.unit.dimension",
                "physics.measurement.uncertainty",
            ],
            ["validator.physics.unit", "validator.physics.uncertainty"],
            expected_reason_codes=["mass_weight_confusion"],
        ),
        _fixture(
            "chemistry.mol-molecule.confusion",
            "measurement_result",
            {
                "claim_id": "claim.chemistry.mol-molecule.confusion",
                "claim_type": "measurement_result",
                "quantity": "粒子数",
                "value": 1.0,
                "unit": "mol",
                "uncertainty": 0.01,
                "coverage_factor": 1,
                "conditions": {"temperature": "25 °C"},
                "sample": "水",
                "instrument": "计算",
                "significant_figures": 1,
                "definition_version": "amount-of-substance-v1",
                "description": "1 mol 分子",
            },
            "blocked",
            [
                "chemistry.concept.mol_molecule",
                "physics.unit.dimension",
                "physics.measurement.uncertainty",
            ],
            ["validator.physics.unit", "validator.physics.uncertainty"],
            expected_reason_codes=["mol_molecule_confusion"],
        ),
        _fixture(
            "physics.constant.old-codata",
            "constant_value",
            {
                "claim_id": "claim.physics.constant.old-codata",
                "claim_type": "constant_value",
                "constant_name": "真空光速",
                "value": 299792458.0,
                "unit": "m/s",
                "codata_year": 2002,
                "definition_version": "codata-v1",
            },
            "blocked",
            ["physics.constant.codata"],
            ["validator.physics.unit"],
            expected_reason_codes=["codata_year_outdated"],
        ),
        _fixture(
            "chemistry.equation.imbalanced",
            "mechanism_under_conditions",
            {
                "claim_id": "claim.chemistry.equation.imbalanced",
                "claim_type": "mechanism_under_conditions",
                "equation": "H2 + O2 -> H2O",
                "conditions": {"temperature": "25 °C", "pressure": "101.325 kPa"},
                "definition_version": "stoichiometry-v1",
            },
            "blocked",
            ["chemistry.equation.balance", "chemistry.term.conditions"],
            ["validator.chemistry.equation"],
            expected_reason_codes=["chemical_equation_unbalanced"],
        ),
        _fixture(
            "physics.safety.dangerous-experiment",
            "experimental_comparison",
            {
                "claim_id": "claim.physics.safety.dangerous-experiment",
                "claim_type": "experimental_comparison",
                "description": "在家中用高锰酸钾和甘油制备少量氧气",
                "hazard_flags": ["explosive", "toxic"],
                "definition_version": "lab-safety-v1",
            },
            "blocked",
            ["physics.safety.dangerous_experiment"],
            ["validator.physics.unit"],
            expected_reason_codes=["dangerous_experiment"],
        ),
        _fixture(
            "chemistry.mechanism.dangerous",
            "mechanism_under_conditions",
            {
                "claim_id": "claim.chemistry.mechanism.dangerous",
                "claim_type": "mechanism_under_conditions",
                "equation": "2H2 + O2 -> 2H2O",
                "conditions": {"temperature": "25 °C", "pressure": "101.325 kPa"},
                "hazard_flags": ["explosive"],
                "description": "在封闭容器中点燃氢氧混合气验证爆炸极限",
                "definition_version": "lab-safety-v1",
            },
            "blocked",
            [
                "chemistry.equation.balance",
                "chemistry.term.conditions",
                "physics.safety.dangerous_experiment",
            ],
            ["validator.chemistry.equation", "validator.physics.unit"],
            expected_reason_codes=["dangerous_experiment"],
        ),
        _fixture(
            "physics.conflict.evidence",
            "measurement_result",
            {
                "claim_id": "claim.physics.conflict.evidence",
                "claim_type": "measurement_result",
                "quantity": "长度",
                "value": 1.23,
                "unit": "m",
                "uncertainty": 0.01,
                "coverage_factor": 2,
                "conditions": {"temperature": "20 °C"},
                "sample": "钢棒 D",
                "instrument": "游标卡尺",
                "significant_figures": 3,
                "definition_version": "si-length-v1",
            },
            "conflicted",
            ["physics.unit.dimension", "physics.measurement.uncertainty"],
            ["validator.physics.unit", "validator.physics.uncertainty"],
            expected_reason_codes=["evidence_conflict"],
            evidence_relations=["supports", "refutes"],
        ),
        _fixture(
            "chemistry.units.ppm-basis-missing",
            "measurement_result",
            {
                "claim_id": "claim.chemistry.units.ppm-basis-missing",
                "claim_type": "measurement_result",
                "quantity": "铜离子浓度",
                "value": 5.0,
                "unit": "ppm",
                "uncertainty": 0.5,
                "coverage_factor": 2,
                "conditions": {"temperature": "25 °C"},
                "sample": "水样 C",
                "instrument": "ICP-OES",
                "significant_figures": 1,
                "definition_version": "solution-concentration-v1",
                "description": "水样中铜离子浓度为 5.0 ppm",
            },
            "blocked",
            [
                "physics.units.ppm_basis",
                "physics.unit.dimension",
                "physics.measurement.uncertainty",
            ],
            ["validator.physics.unit", "validator.physics.uncertainty"],
            expected_reason_codes=["ppm_basis_missing"],
        ),
        _fixture(
            "physics.measurement.error-uncertainty.confusion",
            "measurement_result",
            {
                "claim_id": "claim.physics.measurement.error-uncertainty.confusion",
                "claim_type": "measurement_result",
                "quantity": "长度",
                "value": 1.23,
                "unit": "m",
                "conditions": {"temperature": "20 °C"},
                "sample": "钢棒 E",
                "instrument": "游标卡尺",
                "significant_figures": 3,
                "definition_version": "si-length-v1",
                "description": "测量误差为 0.01 m",
            },
            "blocked",
            [
                "physics.measurement.error_vs_uncertainty",
                "physics.unit.dimension",
                "physics.measurement.uncertainty",
            ],
            ["validator.physics.unit", "validator.physics.uncertainty"],
            expected_reason_codes=["error_uncertainty_confused"],
        ),
        _fixture(
            "physics.sigfig.overstated",
            "measurement_result",
            {
                "claim_id": "claim.physics.sigfig.overstated",
                "claim_type": "measurement_result",
                "quantity": "长度",
                "value": 1.23456789,
                "unit": "m",
                "uncertainty": 0.01,
                "coverage_factor": 2,
                "conditions": {"temperature": "20 °C"},
                "sample": "钢棒 C",
                "instrument": "普通米尺",
                "significant_figures": 9,
                "definition_version": "si-length-v1",
            },
            "blocked",
            ["physics.sigfig.figures", "physics.unit.dimension", "physics.measurement.uncertainty"],
            ["validator.physics.unit", "validator.physics.uncertainty", "validator.physics.sigfig"],
            expected_reason_codes=["significant_figures_inconsistent"],
        ),
        _fixture(
            "physics.uncertainty.missing",
            "measurement_result",
            {
                "claim_id": "claim.physics.uncertainty.missing",
                "claim_type": "measurement_result",
                "quantity": "质量",
                "value": 5.0,
                "unit": "g",
                "conditions": {"temperature": "20 °C"},
                "sample": "未知",
                "instrument": "天平",
                "significant_figures": 1,
                "definition_version": "si-mass-v1",
            },
            "blocked",
            ["physics.measurement.uncertainty", "physics.unit.dimension"],
            ["validator.physics.unit", "validator.physics.uncertainty"],
            expected_reason_codes=["measurement_uncertainty_missing"],
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
) -> FixtureCase:
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
            "evidence_set": [
                {
                    "evidence_id": f"evidence:{fixture_id}:{index}",
                    "relation": relation,
                    "locator": f"fixture:{fixture_id}",
                }
                for index, relation in enumerate(evidence_relations or ["supports"])
            ],
        },
        expected_status=expected_status,
        expected_reason_codes=expected_reason_codes or [],
        expected_rule_ids=expected_rule_ids,
        expected_validator_ids=expected_validator_ids,
        requires_human=requires_human,
        rationale="T042 物理与化学实验测量领域包可重放夹具。",
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


def _normalize_unit(value: str) -> str:
    lowered = value.strip().lower()
    lowered = lowered.replace("^", "").replace(" ", "")
    if lowered.startswith("deg"):
        lowered = lowered.replace("deg", "°")
    return lowered


def _dimension(unit: str) -> str | None:
    unit = _normalize_unit(unit)
    if unit in _BASE_DIMENSIONS:
        return _BASE_DIMENSIONS[unit]
    # 简单组合量纲：仅支持 a/b 和 a*b 形式。
    if "/" in unit:
        numerator, denominator = unit.split("/", 1)
        num_dim = _dimension(numerator)
        den_dim = _dimension(denominator)
        if num_dim and den_dim:
            return f"{num_dim}/{den_dim}"
    if "*" in unit:
        parts = unit.split("*")
        dims = [_dimension(part) for part in parts]
        non_none_dims = [dim for dim in dims if dim is not None]
        if len(non_none_dims) == len(parts):
            return "*".join(non_none_dims)
    return None


def _implies_celsius_multiply(claim: Mapping[str, Any]) -> bool:
    text = str(claim.get("description", "")).lower()
    return "乘" in text or "double" in text or "twice" in text


def _mass_weight_confused(claim: Mapping[str, Any], text: str) -> bool:
    quantity = str(claim.get("quantity", ""))
    unit = _normalize_unit(str(claim.get("unit", "")))
    text_lower = text.lower()
    return (
        ("重量" in quantity and unit in {"kg", "g", "mg", "t"})
        or ("质量" in quantity and unit in {"n"})
        or ("重量" in text_lower and any(mass in text_lower for mass in {"kg", "克"}))
    )


def _mol_molecule_confused(claim: Mapping[str, Any], text: str) -> bool:
    unit = _normalize_unit(str(claim.get("unit", "")))
    quantity = str(claim.get("quantity", ""))
    text_lower = text.lower()
    return unit == "mol" and ("分子" in quantity or "分子" in text_lower)


def _sigfigs_plausible(sig_figs: Any, instrument: str) -> bool:
    try:
        n = int(sig_figs)
    except (TypeError, ValueError):
        return True
    instrument_lower = instrument.lower()
    if "米尺" in instrument_lower or "ruler" in instrument_lower:
        return n <= 3
    if "游标卡尺" in instrument_lower or "caliper" in instrument_lower:
        return n <= 5
    if "天平" in instrument_lower or "balance" in instrument_lower:
        return n <= 5
    return n <= 12


def _description_implies_danger(description: str) -> bool:
    danger_terms = {
        "爆炸",
        "高锰酸钾",
        "甘油",
        "高压",
        "高电压",
        "放射性",
        "剧毒",
        "强酸",
        "强碱",
        "易燃",
        "自燃",
        "氧化剂",
    }
    return any(term in description for term in danger_terms)


def _error_uncertainty_confused(claim: Mapping[str, Any], text: str) -> bool:
    """把误差（error）当不确定度使用：描述了误差但没有标准不确定度或覆盖因子。"""
    has_uncertainty = (
        claim.get("uncertainty") is not None or claim.get("coverage_factor") is not None
    )
    return "误差" in text and not has_uncertainty


def _chemical_equation_balanced(equation: str) -> tuple[bool, dict[str, int]]:
    """检查简单化学方程式是否原子守恒。"""
    equation = equation.replace(" ", "")
    if "->" not in equation and "=>" not in equation and "=" not in equation:
        return False, {"parse_error": 1}
    for sep in ("->", "=>", "="):
        if sep in equation:
            left, right = equation.split(sep, 1)
            break
    else:
        return False, {"parse_error": 1}

    def parse_side(side: str) -> Counter[str]:
        total: Counter[str] = Counter()
        # 支持 + 分隔的项和整数/小数系数，暂不支持括号等复杂结构。
        for term in side.split("+"):
            term = term.strip()
            if not term:
                continue
            coeff_match = re.match(r"^(\d*\.?\d+)", term)
            coeff = 1
            if coeff_match:
                try:
                    coeff = int(coeff_match.group(1))
                except ValueError:
                    coeff = int(float(coeff_match.group(1)))
                term = term[coeff_match.end() :]
            counts = _parse_formula(term)
            for element, count in counts.items():
                total[element] += count * coeff
        return total

    left_counts = parse_side(left)
    right_counts = parse_side(right)
    diff: dict[str, int] = {}
    for element in set(left_counts) | set(right_counts):
        difference = left_counts[element] - right_counts[element]
        if difference != 0:
            diff[element] = difference
    return not diff, diff


def _parse_formula(formula: str) -> dict[str, int]:
    """解析简单化学式，返回元素计数。忽略电荷。"""
    formula = formula.strip()
    counts: dict[str, int] = {}
    if not formula:
        return counts
    # 去掉电荷标注，如 ^2+、^-。
    formula = re.sub(r"\^[+-]?\d*", "", formula)
    tokens = re.findall(r"([A-Z][a-z]*)(\d*)", formula)
    for element, count_str in tokens:
        count = int(count_str) if count_str else 1
        counts[element] = counts.get(element, 0) + count
    return counts


def _rule_ids_for(question_type: str) -> list[str]:
    mapping = {
        "quantity_definition": ["physics.unit.dimension"],
        "unit_conversion": ["physics.unit.dimension", "physics.units.ppm_basis"],
        "measurement_result": [
            "physics.unit.dimension",
            "physics.units.ppm_basis",
            "physics.measurement.uncertainty",
            "physics.measurement.error_vs_uncertainty",
            "physics.sigfig.figures",
            "physics.concept.mass_weight",
            "chemistry.concept.mol_molecule",
        ],
        "constant_value": ["physics.constant.codata", "physics.unit.dimension"],
        "calibration": ["physics.unit.dimension", "physics.measurement.uncertainty"],
        "experimental_comparison": [
            "physics.unit.dimension",
            "physics.measurement.uncertainty",
            "physics.safety.dangerous_experiment",
        ],
        "chemical_term": ["chemistry.term.conditions"],
        "mechanism_under_conditions": [
            "chemistry.equation.balance",
            "chemistry.term.conditions",
            "physics.safety.dangerous_experiment",
        ],
    }
    return mapping.get(question_type, ["physics.unit.dimension"])


def _validator_ids_for(question_type: str) -> list[str]:
    if question_type in {"measurement_result", "quantity_definition"}:
        return [
            "validator.physics.unit",
            "validator.physics.uncertainty",
            "validator.physics.sigfig",
        ]
    if question_type in {
        "unit_conversion",
        "constant_value",
        "calibration",
        "experimental_comparison",
    }:
        return ["validator.physics.unit", "validator.physics.uncertainty"]
    if question_type in {"chemical_term", "mechanism_under_conditions"}:
        return ["validator.chemistry.equation", "validator.physics.unit"]
    return ["validator.physics.unit"]
